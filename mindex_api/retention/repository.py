"""Canonical private retention transactions; no object bytes or claims in logs.

The sessions callable must yield a fresh SQLAlchemy AsyncSession. The service role
must be unable to provision membership/access grants. Every principal operation
rechecks membership inside its transaction; worker methods are internal only.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import text

from .contracts import RetentionError, admission_metadata, public_receipt


_SELECT = """SELECT a.*, j.job_id, j.attempt_count, j.lease_token,
    j.lease_expires_at, j.last_error_code FROM retention.artifact a
    JOIN retention.job j USING (artifact_id)"""
_VISIBLE = """a.tenant_id=:tenant_id AND a.project_id=:project_id
    AND a.state NOT IN ('deleted','cancelled') AND a.retention_until > now()
    AND ((a.owner_issuer=:issuer AND a.owner_subject=:subject) OR EXISTS (
      SELECT 1 FROM retention.access_grant g WHERE g.artifact_id=a.artifact_id
      AND g.grantee_issuer=:issuer AND g.grantee_subject=:subject AND g.active))"""


def _identity(principal):
    return {key: getattr(principal, key) for key in
            ('issuer', 'subject', 'tenant_id', 'project_id')}


def _id():
    return str(uuid4())


def receipt(row):
    """Safe typed metadata only; callers never serialize a raw database row."""
    fields = ('artifact_id', 'job_id', 'kind', 'media_type', 'sha256', 'byte_length',
              'tenant_id', 'project_id', 'classification', 'source_event_at',
              'received_at', 'available_at', 'retention_until', 'state',
              'attempt_count', 'deletion_requested_at', 'physical_deleted_at')
    result = public_receipt(dict(row))
    result.update({key: row.get(key) for key in fields})
    result.update(contract_version='retention.v1', task_id=row.get('job_id'),
                  retained=row['state'] == 'verified',
                  coverage_watermark=row.get('available_at') if row['state'] == 'verified' else None,
                  physical_deletion_pending=bool(row.get('deletion_requested_at')
                                                 and row.get('object_version')
                                                 and not row.get('physical_deleted_at')))
    return result


class RetentionRepository:
    def __init__(self, sessions, config):
        self.sessions = sessions
        self.config = config

    async def require_membership(self, principal):
        """Authorization seam for app-owned MINDEX operations; never grants membership."""
        async with self.sessions() as session, session.begin():
            await self._membership(session, principal)

    async def authorize_in_session(self, session, principal):
        """Recheck and share-lock authoritative membership in a caller-owned transaction."""
        if not session.in_transaction():
            raise RetentionError('authorization_transaction_required', 503)
        await self._membership(session, principal)

    async def _membership(self, session, principal):
        params = _identity(principal)
        result = await session.execute(text("""SELECT 1 FROM retention.membership
            WHERE issuer=:issuer AND subject=:subject AND tenant_id=:tenant_id
            AND project_id=:project_id AND active FOR SHARE"""), params)
        if result.scalar_one_or_none() is None:
            raise RetentionError('membership_required', 403)
        return params

    async def _read(self, session, params, artifact_id, *, owner=False, lock=False):
        params = dict(params, artifact_id=artifact_id)
        query = _SELECT + ' WHERE a.artifact_id=:artifact_id AND ' + _VISIBLE
        if owner:
            query += ' AND a.owner_issuer=:issuer AND a.owner_subject=:subject'
        if lock:
            query += ' FOR UPDATE OF a, j'
        row = (await session.execute(text(query), params)).mappings().first()
        if row is None:
            raise RetentionError('artifact_not_found', 404)
        return dict(row)

    async def admit(self, principal, metadata, payload):
        self.config.admission()
        if not isinstance(payload, bytes) or not payload or len(payload) > self.config.max_payload_bytes:
            raise RetentionError('payload_too_large', 413)
        kind, media_type = metadata.get('kind'), metadata.get('media_type', '')
        key, meta_hash = metadata.get('idempotency_key', ''), metadata.get('metadata_sha256', '')
        if kind not in ('dataset', 'chart', 'artifact') or not 1 <= len(media_type) <= 200:
            raise RetentionError('invalid_metadata', 422)
        if not isinstance(key, str) or not 1 <= len(key) <= 200:
            raise RetentionError('invalid_idempotency_key', 422)
        if not isinstance(meta_hash, str) or len(meta_hash) != 64 or any(
                c not in '0123456789abcdef' for c in meta_hash):
            raise RetentionError('invalid_metadata_digest', 422)
        source_at = metadata.get('source_event_at')
        if source_at is not None:
            if not isinstance(source_at, datetime) or source_at.tzinfo is None:
                raise RetentionError('invalid_source_event_time', 422)
            source_at = source_at.astimezone(timezone.utc)
        canonical = admission_metadata(kind, key, media_type,
                                       source_at.isoformat() if source_at else None, self.config)
        if canonical['metadata_sha256'] != meta_hash:
            raise RetentionError('invalid_metadata_digest', 422)
        media_type = canonical['media_type']
        digest = hashlib.sha256(payload).hexdigest()
        fingerprint = hashlib.sha256(json.dumps({
            'kind': kind, 'media_type': media_type, 'metadata_sha256': meta_hash,
            'source_event_at': source_at.isoformat() if source_at else None,
            'sha256': digest, 'byte_length': len(payload),
        }, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        async with self.sessions() as session, session.begin():
            params = await self._membership(session, principal)
            # Project-wide admission lock serializes quota and same-key checks.
            # It is transaction-scoped and survives no crashed client session.
            lock_scope = json.dumps([params['tenant_id'], params['project_id']]).encode()
            lock_id = int.from_bytes(hashlib.sha256(lock_scope).digest()[:8], 'big', signed=True)
            await session.execute(text('SELECT pg_advisory_xact_lock(:lock_id)'), {'lock_id': lock_id})
            existing = (await session.execute(text(_SELECT + """ WHERE
                a.owner_issuer=:issuer AND a.owner_subject=:subject AND a.tenant_id=:tenant_id
                AND a.project_id=:project_id AND a.idempotency_key=:key"""),
                dict(params, key=key))).mappings().first()
            if existing:
                if existing['fingerprint'] != fingerprint:
                    raise RetentionError('idempotency_conflict', 409)
                # Tombstones preserve ID uniqueness without returning deleted bytes.
                return receipt(existing), False
            usage = (await session.execute(text("""SELECT count(*) AS count,
                COALESCE(sum(byte_length),0) AS bytes FROM retention.artifact
                WHERE tenant_id=:tenant_id AND project_id=:project_id AND payload IS NOT NULL"""),
                params)).mappings().one()
            if usage['count'] >= self.config.max_pending_count or (
                    usage['bytes'] + len(payload) > self.config.max_pending_bytes):
                raise RetentionError('pending_quota_exceeded', 429)
            params.update(artifact_id=_id(), job_id=_id(), key=key, fingerprint=fingerprint,
                          metadata_sha256=meta_hash, sha256=digest, byte_length=len(payload),
                          payload=payload, kind=kind, media_type=media_type, source_event_at=source_at,
                          retention_days=self.config.retention_days)
            await session.execute(text("""INSERT INTO retention.artifact
                (artifact_id,owner_issuer,owner_subject,tenant_id,project_id,kind,media_type,
                 idempotency_key,fingerprint,metadata_sha256,sha256,byte_length,payload,
                 source_event_at,retention_until)
                VALUES (:artifact_id,:issuer,:subject,:tenant_id,:project_id,:kind,:media_type,
                 :key,:fingerprint,:metadata_sha256,:sha256,:byte_length,:payload,
                 :source_event_at,now()+make_interval(days=>:retention_days))"""), params)
            await session.execute(text("""INSERT INTO retention.job(job_id,artifact_id)
                VALUES (:job_id,:artifact_id)"""), params)
            await session.execute(text("""INSERT INTO retention.outbox(artifact_id,job_id,event)
                VALUES (:artifact_id,:job_id,'archive')"""), params)
            row = await self._read(session, params, params['artifact_id'])
            return receipt(row), True

    async def get(self, principal, artifact_id):
        async with self.sessions() as session, session.begin():
            params = await self._membership(session, principal)
            return await self._read(session, params, artifact_id)

    async def get_task(self, principal, job_id):
        async with self.sessions() as session, session.begin():
            params = await self._membership(session, principal)
            row = (await session.execute(text(_SELECT + ' WHERE j.job_id=:job_id AND ' + _VISIBLE),
                                         dict(params, job_id=job_id))).mappings().first()
            if not row:
                raise RetentionError('task_not_found', 404)
            return receipt(row)

    async def get_job(self, principal, job_id):
        return await self.get_task(principal, job_id)

    async def list(self, principal, query='', limit=50):
        if not isinstance(query, str) or len(query) > 200 or not 1 <= limit <= 100:
            raise RetentionError('invalid_query', 422)
        async with self.sessions() as session, session.begin():
            params = await self._membership(session, principal)
            # Literal substring only; no caller-controlled wildcard or SQL expression.
            params.update(query=query, limit=limit)
            rows = (await session.execute(text(_SELECT + ' WHERE ' + _VISIBLE + """
                AND (:query='' OR strpos(a.artifact_id,:query)>0 OR strpos(a.kind,:query)>0
                    OR strpos(a.media_type,:query)>0)
                ORDER BY a.received_at DESC,a.artifact_id LIMIT :limit"""), params)).mappings()
            return [receipt(row) for row in rows]

    async def cancel(self, principal, job_id):
        async with self.sessions() as session, session.begin():
            params = await self._membership(session, principal)
            row = (await session.execute(text(_SELECT + ' WHERE j.job_id=:job_id AND ' + _VISIBLE
                + ' AND a.owner_issuer=:issuer AND a.owner_subject=:subject FOR UPDATE OF a,j'),
                dict(params, job_id=job_id))).mappings().first()
            if not row:
                raise RetentionError('task_not_found', 404)
            if row['state'] == 'verified':
                raise RetentionError('task_already_completed', 409)
            await self._terminal(session, row, 'cancelled')
            return receipt(dict(row, state='cancelled', available_at=None))

    async def _terminal(self, session, row, state):
        params = {'artifact_id': row['artifact_id'], 'state': state}
        await session.execute(text("""UPDATE retention.artifact SET state=:state,payload=NULL,
            available_at=NULL,deletion_requested_at=now() WHERE artifact_id=:artifact_id"""), params)
        await session.execute(text("""UPDATE retention.job SET state=:state,lease_token=NULL,
            lease_expires_at=NULL,completed_at=now() WHERE artifact_id=:artifact_id"""), params)
        await session.execute(text("""UPDATE retention.outbox SET state='cancelled',completed_at=now(),
            lease_token=NULL,lease_expires_at=NULL WHERE artifact_id=:artifact_id AND event='archive'"""), params)
        await session.execute(text("""UPDATE retention.memory_reference SET revoked_at=now(),summary=''
            WHERE artifact_id=:artifact_id AND revoked_at IS NULL"""), params)
        if row.get('object_version'):
            await session.execute(text("""INSERT INTO retention.outbox(artifact_id,job_id,event)
                VALUES (:artifact_id,:job_id,'purge_object') ON CONFLICT (artifact_id,event) DO NOTHING"""),
                dict(params, job_id=row['job_id']))

    async def delete(self, principal, artifact_id):
        async with self.sessions() as session, session.begin():
            params = await self._membership(session, principal)
            row = await self._read(session, params, artifact_id, owner=True, lock=True)
            await self._terminal(session, row, 'deleted')
            result = (await session.execute(text(_SELECT + ' WHERE a.artifact_id=:artifact_id'),
                                           {'artifact_id': artifact_id})).mappings().one()
            return receipt(result)

    async def claim(self):
        """Internal archive worker lease. Revoked/expired owners never gain availability."""
        if not self.config.enabled:
            return None
        async with self.sessions() as session, session.begin():
            row = (await session.execute(text(_SELECT + """
                JOIN retention.outbox o USING (artifact_id,job_id)
                JOIN retention.membership m ON m.issuer=a.owner_issuer AND m.subject=a.owner_subject
                    AND m.tenant_id=a.tenant_id AND m.project_id=a.project_id
                WHERE m.active AND a.retention_until>now() AND a.payload IS NOT NULL
                    AND o.event='archive' AND o.state IN ('pending','leased')
                    AND j.state IN ('pending','archiving') AND j.next_attempt_at<=now()
                    AND (j.lease_expires_at IS NULL OR j.lease_expires_at<=now())
                ORDER BY o.outbox_id FOR UPDATE OF a,j,o SKIP LOCKED LIMIT 1"""))).mappings().first()
            if not row:
                return None
            params = {'artifact_id': row['artifact_id'], 'token': _id(),
                      'seconds': self.config.lease_seconds}
            await session.execute(text("""UPDATE retention.job SET state='archiving',lease_token=:token,
                lease_expires_at=now()+make_interval(secs=>:seconds),attempt_count=attempt_count+1
                WHERE artifact_id=:artifact_id"""), params)
            await session.execute(text("""UPDATE retention.artifact SET state='archiving'
                WHERE artifact_id=:artifact_id"""), params)
            await session.execute(text("""UPDATE retention.outbox SET state='leased' WHERE
                artifact_id=:artifact_id AND event='archive'"""), params)
            return dict(row, state='archiving', lease_token=params['token'],
                        attempt_count=row['attempt_count'] + 1)

    async def _fenced(self, session, row):
        return (await session.execute(text(_SELECT + """
            JOIN retention.membership m ON m.issuer=a.owner_issuer AND m.subject=a.owner_subject
                AND m.tenant_id=a.tenant_id AND m.project_id=a.project_id
            WHERE a.artifact_id=:artifact_id AND j.job_id=:job_id AND j.lease_token=:lease_token
                AND j.state='archiving' AND j.lease_expires_at>now()
                AND a.state='archiving' AND m.active AND a.retention_until>now()
            FOR UPDATE OF a,j FOR SHARE OF m"""), row)).mappings().first()

    async def complete(self, row, reference):
        """Accept proof only from the server worker after exact S3 version readback."""
        if not self._archive_proof(row, reference):
            await self.retry(row, 'archive_integrity_mismatch', integrity=True)
            return False
        async with self.sessions() as session, session.begin():
            locked = await self._fenced(session, row)
            if not locked:
                return False
            # Recheck authoritative content, not merely the worker's row snapshot.
            if locked['sha256'] != reference['sha256'] or locked['byte_length'] != reference['byte_length']:
                return False
            params = dict(row, bucket=reference['bucket'], key=reference['key'], version=reference['version'])
            await session.execute(text("""UPDATE retention.artifact SET state='verified',available_at=now(),
                object_bucket=:bucket,object_key=:key,object_version=:version,payload=NULL
                WHERE artifact_id=:artifact_id"""), params)
            await session.execute(text("""UPDATE retention.job SET state='verified',completed_at=now(),
                lease_token=NULL,lease_expires_at=NULL,last_error_code=NULL WHERE artifact_id=:artifact_id"""), params)
            await session.execute(text("""UPDATE retention.outbox SET state='done',completed_at=now()
                WHERE artifact_id=:artifact_id AND event='archive'"""), params)
            return True

    async def retry(self, row, code, integrity=False):
        # Errors must be fixed public codes, never provider exceptions/private source data.
        allowed = {'archive_integrity_mismatch', 'archive_unavailable', 'archive_timeout',
                   'archive_configuration', 'worker_interrupted', 'archive_failed'}
        code = code if code in allowed else 'archive_failed'
        async with self.sessions() as session, session.begin():
            locked = await self._fenced(session, row)
            if not locked:
                return False
            quarantine = integrity or locked['attempt_count'] >= getattr(self.config, 'max_attempts', 8)
            params = dict(row, state='quarantined' if quarantine else 'pending', code=code,
                          outbox_state='quarantined' if quarantine else 'pending',
                          delay=min(300, 2 ** min(locked['attempt_count'], 8)))
            await session.execute(text("""UPDATE retention.job SET state=:state,last_error_code=:code,
                lease_token=NULL,lease_expires_at=NULL,next_attempt_at=now()+make_interval(secs=>:delay)
                WHERE artifact_id=:artifact_id"""), params)
            await session.execute(text("""UPDATE retention.artifact SET state=:state,available_at=NULL
                WHERE artifact_id=:artifact_id"""), params)
            await session.execute(text("""UPDATE retention.outbox SET state=:outbox_state
                WHERE artifact_id=:artifact_id AND event='archive'"""), params)
            return True

    async def memory_link(self, principal, artifact_id, summary, *, proof_sha256=None):
        if not isinstance(summary, str) or len(summary) > 2000:
            raise RetentionError('invalid_memory_summary', 422)
        async with self.sessions() as session, session.begin():
            params = await self._membership(session, principal)
            row = await self._read(session, params, artifact_id, lock=True)
            if row['state'] != 'verified' or proof_sha256 != row['sha256']:
                raise RetentionError('artifact_readback_required', 409)
            params.update(memory_id=_id(), artifact_id=artifact_id, summary=summary, proof=proof_sha256)
            memory = (await session.execute(text("""INSERT INTO retention.memory_reference
                (memory_id,artifact_id,owner_issuer,owner_subject,tenant_id,project_id,summary,proof_sha256)
                VALUES (:memory_id,:artifact_id,:issuer,:subject,:tenant_id,:project_id,:summary,:proof)
                ON CONFLICT (artifact_id,owner_issuer,owner_subject) DO UPDATE SET summary=EXCLUDED.summary
                RETURNING *"""), params)).mappings().one()
            return self._memory_receipt(memory, row)

    @staticmethod
    def _memory_receipt(memory, row):
        return {'contract_version': 'retention.v1', 'memory_id': memory['memory_id'],
                'artifact_id': row['artifact_id'], 'task_id': row['job_id'],
                'summary': memory['summary'], 'sha256': memory['proof_sha256'],
                'created_at': memory['created_at'], 'state': 'referenced',
                'learned_model_state': False, 'artifact': receipt(row)}

    async def memory_get(self, principal, memory_id):
        async with self.sessions() as session, session.begin():
            params = await self._membership(session, principal)
            memory = (await session.execute(text("""SELECT * FROM retention.memory_reference
                WHERE memory_id=:memory_id AND owner_issuer=:issuer AND owner_subject=:subject
                AND tenant_id=:tenant_id AND project_id=:project_id AND revoked_at IS NULL"""),
                dict(params, memory_id=memory_id))).mappings().first()
            if not memory:
                raise RetentionError('memory_not_found', 404)
            row = await self._read(session, params, memory['artifact_id'])
            if row['state'] != 'verified':
                raise RetentionError('artifact_readback_required', 409)
            return self._memory_receipt(memory, row)

    async def memory_list(self, principal, query='', limit=50):
        if not isinstance(query, str) or len(query) > 200 or not 1 <= limit <= 100:
            raise RetentionError('invalid_query', 422)
        async with self.sessions() as session, session.begin():
            params = await self._membership(session, principal)
            params.update(query=query, limit=limit)
            memories = (await session.execute(text("""SELECT r.* FROM retention.memory_reference r
                JOIN retention.artifact a USING (artifact_id) WHERE r.owner_issuer=:issuer
                AND r.owner_subject=:subject AND r.tenant_id=:tenant_id AND r.project_id=:project_id
                AND r.revoked_at IS NULL AND a.state='verified' AND """ + _VISIBLE + """
                AND (:query='' OR strpos(r.summary,:query)>0)
                ORDER BY r.created_at DESC,r.memory_id LIMIT :limit"""), params)).mappings().all()
            result = []
            for memory in memories:
                row = await self._read(session, params, memory['artifact_id'])
                result.append(self._memory_receipt(memory, row))
            return result

    async def expire(self, limit=50):
        """Internal bounded sweep: expiry immediately removes payloads and memory summaries."""
        async with self.sessions() as session, session.begin():
            rows = (await session.execute(text(_SELECT + """ WHERE a.retention_until<=now()
                AND a.state NOT IN ('deleted','cancelled') ORDER BY a.retention_until
                FOR UPDATE OF a,j SKIP LOCKED LIMIT :limit"""), {'limit': max(1, min(limit, 100))})).mappings().all()
            for row in rows:
                await self._terminal(session, row, 'deleted')
            return len(rows)

    async def claim_purge(self):
        """Internal cleanup, including revoked identities. Object Lock must have expired."""
        async with self.sessions() as session, session.begin():
            row = (await session.execute(text(_SELECT + """
                JOIN retention.outbox o USING (artifact_id,job_id)
                WHERE o.event='purge_object' AND o.state IN ('pending','leased')
                AND (o.lease_expires_at IS NULL OR o.lease_expires_at<=now())
                AND a.state IN ('deleted','cancelled') AND a.retention_until<=now()
                AND a.object_version IS NOT NULL AND a.physical_deleted_at IS NULL
                ORDER BY o.outbox_id FOR UPDATE OF a,o SKIP LOCKED LIMIT 1"""))).mappings().first()
            if not row:
                return None
            token = _id()
            await session.execute(text("""UPDATE retention.outbox SET state='leased',lease_token=:token,
                lease_expires_at=now()+make_interval(secs=>:seconds),attempt_count=attempt_count+1
                WHERE artifact_id=:artifact_id AND event='purge_object'"""),
                {'artifact_id': row['artifact_id'], 'token': token, 'seconds': self.config.lease_seconds})
            return dict(row, purge_lease_token=token)

    @staticmethod
    def _deletion_proof(row, proof):
        return (isinstance(proof, dict) and proof.get('deleted') is True
                and all(proof.get(k) == row.get('object_' + k)
                        for k in ('bucket', 'key', 'version')))

    async def complete_purge(self, row, proof):
        """Call only after the object adapter confirms exact-version absence."""
        if not self._deletion_proof(row, proof):
            return False
        async with self.sessions() as session, session.begin():
            result = await session.execute(text("""UPDATE retention.outbox SET state='done',completed_at=now(),
                lease_token=NULL,lease_expires_at=NULL WHERE artifact_id=:artifact_id AND event='purge_object'
                AND lease_token=:purge_lease_token AND lease_expires_at>now() RETURNING artifact_id"""), row)
            if result.scalar_one_or_none() is None:
                return False
            await session.execute(text("""UPDATE retention.artifact SET physical_deleted_at=now()
                WHERE artifact_id=:artifact_id"""), row)
            return True

    def _archive_proof(self, row, reference):
        expected_key = '/'.join((self.config.prefix, row['tenant_id'], row['project_id'], row['artifact_id']))
        return (isinstance(reference, dict) and reference.get('verified') is True
                and reference.get('sha256') == row['sha256']
                and type(reference.get('byte_length')) is int
                and reference['byte_length'] == row['byte_length']
                and bool(self.config.bucket) and reference.get('bucket') == self.config.bucket
                and reference.get('key') == expected_key
                and isinstance(reference.get('version'), str)
                and 1 <= len(reference['version']) <= 1024 and reference['version'] != 'null')

    async def register_orphan(self, row, reference):
        """Internal evidence for an upload rejected by the availability fence.

        Must be called immediately after a rejected complete. A crash before this
        transaction still needs version inventory reconciliation; no fabricated
        physical-deletion success is permitted for that gap.
        """
        if not self._archive_proof(row, reference) or not row.get('lease_token'):
            raise RetentionError('archive_integrity_mismatch', 409)
        async with self.sessions() as session, session.begin():
            current = (await session.execute(text(_SELECT + ' WHERE a.artifact_id=:artifact_id FOR UPDATE OF a'),
                                             row)).mappings().first()
            if not current or current['sha256'] != row['sha256'] or current['byte_length'] != row['byte_length']:
                raise RetentionError('archive_integrity_mismatch', 409)
            if (current['state'] == 'verified' and current['object_bucket'] == reference['bucket']
                    and current['object_key'] == reference['key'] and current['object_version'] == reference['version']):
                return False
            params = dict(row, orphan_id=_id(), bucket=reference['bucket'], key=reference['key'], version=reference['version'])
            await session.execute(text("""INSERT INTO retention.orphan_archive
                (orphan_id,artifact_id,archive_lease_token,object_bucket,object_key,object_version)
                VALUES (:orphan_id,:artifact_id,:lease_token,:bucket,:key,:version)
                ON CONFLICT (artifact_id,object_bucket,object_key,object_version) DO NOTHING"""), params)
            return True

    async def claim_orphan_purge(self):
        async with self.sessions() as session, session.begin():
            row = (await session.execute(text("""SELECT a.*,o.orphan_id,
                o.object_bucket AS orphan_bucket,o.object_key AS orphan_key,o.object_version AS orphan_version
                FROM retention.orphan_archive o JOIN retention.artifact a USING (artifact_id)
                WHERE o.state IN ('pending','leased') AND a.retention_until<=now()
                AND (o.lease_expires_at IS NULL OR o.lease_expires_at<=now())
                AND NOT (a.state='verified' AND a.object_bucket=o.object_bucket
                    AND a.object_key=o.object_key AND a.object_version=o.object_version)
                ORDER BY o.recorded_at FOR UPDATE OF a,o SKIP LOCKED LIMIT 1"""))).mappings().first()
            if not row:
                return None
            token = _id()
            await session.execute(text("""UPDATE retention.orphan_archive SET state='leased',lease_token=:token,
                lease_expires_at=now()+make_interval(secs=>:seconds) WHERE orphan_id=:orphan_id"""),
                {'token': token, 'seconds': self.config.lease_seconds, 'orphan_id': row['orphan_id']})
            return dict(row, object_bucket=row['orphan_bucket'], object_key=row['orphan_key'],
                        object_version=row['orphan_version'], orphan_lease_token=token)

    async def complete_orphan_purge(self, row, proof):
        if not self._deletion_proof(row, proof):
            return False
        async with self.sessions() as session, session.begin():
            result = await session.execute(text("""UPDATE retention.orphan_archive SET state='done',
                physical_deleted_at=now(),lease_token=NULL,lease_expires_at=NULL
                WHERE orphan_id=:orphan_id AND lease_token=:orphan_lease_token
                AND lease_expires_at>now() AND object_bucket=:object_bucket
                AND object_key=:object_key AND object_version=:object_version RETURNING orphan_id"""), row)
            return result.scalar_one_or_none() is not None
