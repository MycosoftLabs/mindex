"""Versioned BFF/MYCA HTTP client; carries verified user token, never stores it."""
from __future__ import annotations

import hashlib
from urllib.parse import urlsplit

import httpx

from .contracts import RetentionError, canonical_uuid


class RetentionClient:
    def __init__(self, origin: str, *, access_token: str, tenant_id: str, project_id: str,
                 transport: httpx.AsyncBaseTransport | None = None):
        url = urlsplit(origin)
        if (url.username or url.password or url.query or url.fragment or url.path not in {'', '/'}
                or url.scheme != 'https' and not (url.scheme == 'http' and url.hostname in {'localhost', '127.0.0.1', '::1'})):
            raise RetentionError('invalid_retention_origin')
        if not access_token or len(access_token) > 16384 or '\r' in access_token or '\n' in access_token:
            raise RetentionError('authentication_required', 401)
        self.scope = {'tenant_id': canonical_uuid(tenant_id), 'project_id': canonical_uuid(project_id)}
        self._http = httpx.AsyncClient(base_url=origin.rstrip('/') + '/api/mindex/retention/v1/',
            headers={'Authorization': 'Bearer ' + access_token,
                     'X-Tenant-Id': tenant_id, 'X-Project-Id': project_id},
            transport=transport, timeout=httpx.Timeout(35, connect=5), follow_redirects=False)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        await self._http.aclose()

    async def _request(self, method, path, *, maximum=256*1024, **kwargs):
        try:
            async with self._http.stream(method, path, **kwargs) as response:
                if response.status_code not in {200, 201, 202}:
                    raise RetentionError('retention_request_failed', response.status_code)
                chunks, size = [], 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > maximum:
                        raise RetentionError('response_too_large', 502)
                    chunks.append(chunk)
                payload = b''.join(chunks)
                return httpx.Response(response.status_code, headers=response.headers, content=payload)
        except RetentionError:
            raise
        except httpx.HTTPError as exc:
            raise RetentionError('retention_unavailable') from exc

    def _receipt(self, response):
        try:
            row = response.json()
            if (row['contract_version'] != 'retention.v1' or row['classification'] != 'private'
                    or row['tenant_id'] != self.scope['tenant_id'] or row['project_id'] != self.scope['project_id']
                    or row['state'] not in {'pending','archiving','verified','quarantined','cancelled','deleted'}
                    or row['archive_verified'] != (row['state'] == 'verified')
                    or row['durability'] != 'postgres_committed' or row['learned'] is not False
                    or not isinstance(row['byte_length'], int) or not 0 < row['byte_length'] <= 16*1024*1024
                    or len(row['sha256']) != 64 or any(c not in '0123456789abcdef' for c in row['sha256'])):
                raise ValueError('invalid receipt')
            canonical_uuid(row['artifact_id']); canonical_uuid(row['job_id'])
            return row
        except (ValueError, TypeError, KeyError) as exc:
            raise RetentionError('invalid_receipt', 502) from exc

    async def principal(self):
        value = (await self._request('GET', 'principal')).json()
        if (value.get('contract_version') != 'retention.v1'
                or any(value.get(key) != val for key, val in self.scope.items())):
            raise RetentionError('invalid_principal', 502)
        canonical_uuid(value.get('subject'))
        return value

    async def admit(self, payload: bytes, *, idempotency_key: str, kind='artifact', media_type='application/octet-stream', source_event_at=None):
        if not 0 < len(payload) <= 16*1024*1024:
            raise RetentionError('payload_size_invalid', 413)
        headers = {'Idempotency-Key': idempotency_key, 'X-Artifact-Kind': kind, 'Content-Type': media_type}
        if source_event_at:
            headers['X-Source-Event-At'] = source_event_at
        return self._receipt(await self._request('POST', 'artifacts', content=payload, headers=headers))

    async def get_artifact(self, artifact_id):
        row = self._receipt(await self._request('GET', 'artifacts/' + canonical_uuid(artifact_id)))
        if row['artifact_id'] != artifact_id:
            raise RetentionError('receipt_identifier_mismatch', 502)
        return row

    async def content(self, artifact_id):
        row = await self.get_artifact(artifact_id)
        if not row['archive_verified']:
            raise RetentionError('artifact_not_verified', 409)
        response = await self._request('GET', f'artifacts/{artifact_id}/content', maximum=row['byte_length'])
        payload = response.content
        if (response.headers.get('x-artifact-id') != artifact_id
                or response.headers.get('x-artifact-sha256') != row['sha256']
                or response.headers.get('x-retention-contract') != 'retention.v1'
                or len(payload) != row['byte_length'] or hashlib.sha256(payload).hexdigest() != row['sha256']):
            raise RetentionError('artifact_integrity_failed', 502)
        current = await self.get_artifact(artifact_id)
        if not current['archive_verified'] or current['sha256'] != row['sha256']:
            raise RetentionError('artifact_unavailable', 409)
        return current, payload

    async def remember(self, artifact_id, summary):
        value = (await self._request('POST', 'memories', json={'artifact_id': canonical_uuid(artifact_id), 'summary': summary})).json()
        if (value.get('contract_version') != 'retention.v1' or value.get('artifact_id') != artifact_id
                or value.get('reference_verified') is not True or value.get('learned') is not False):
            raise RetentionError('invalid_memory_receipt', 502)
        return value

    async def recall(self, memory_id):
        value = (await self._request('GET', 'memories/' + canonical_uuid(memory_id))).json()
        if (value.get('contract_version') != 'retention.v1' or value.get('memory_id') != memory_id
                or value.get('reference_verified') is not True or value.get('learned') is not False):
            raise RetentionError('invalid_memory_receipt', 502)
        return value


class MycaMemoryAdapter:
    """MYCA reference bridge: canonical MINDEX remains the only evidence store.

    A vector result may propose a memory ID. Recall always authorizes exact ID and
    bytes through MINDEX; vector similarity is never an access grant.
    """
    def __init__(self, client: RetentionClient):
        self.client = client

    async def remember(self, artifact_id: str, summary: str):
        receipt, _ = await self.client.content(artifact_id)
        link = await self.client.remember(artifact_id, summary)
        exact = await self.client.recall(link['memory_id'])
        if (exact.get('artifact_id') != artifact_id or exact.get('artifact_sha256') != receipt['sha256']
                or exact.get('summary') != summary or exact.get('memory_id') != link.get('memory_id')):
            raise RetentionError('memory_readback_failed', 502)
        return exact

    async def recall(self, memory_id: str):
        memory = await self.client.recall(memory_id)
        receipt, payload = await self.client.content(memory['artifact_id'])
        if receipt['sha256'] != memory.get('artifact_sha256'):
            raise RetentionError('memory_readback_failed', 502)
        return memory, payload
