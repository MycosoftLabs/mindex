"""App jobs and outbox share the canonical MINDEX PostgreSQL transaction boundary."""
from __future__ import annotations

import json
import secrets
from uuid import uuid4

from sqlalchemy import text

from .contracts import (FormSpaceError, LEASE_SECONDS, MAX_ATTEMPTS, MAX_PENDING,
                        MAX_RUNNING, MAX_PROJECT_RUNNING,
                        canonical, digest, idempotency, receipt)

SCOPE = "issuer=:issuer AND subject=:subject AND tenant_id=:tenant_id AND project_id=:project_id"


class FormSpaceRepository:
    def __init__(self, retention_repository, principal_type):
        self.retention = retention_repository
        self.sessions = retention_repository.sessions
        self.principal_type = principal_type

    def owner(self, row):
        return self.principal_type(**{key: str(row[key]) for key in
                                     ("issuer", "subject", "tenant_id", "project_id")})

    async def _authorize(self, session, principal):
        # This public shared API must hold membership's FOR SHARE lock until the
        # caller's transaction ends; no copied identity or membership machinery.
        await self.retention.authorize_in_session(session, principal)
        return {key: str(getattr(principal, key)) for key in
                ("issuer", "subject", "tenant_id", "project_id")}

    async def admit(self, principal, key, request):
        key = idempotency(key)
        encoded = canonical(request)
        params = dict(key=key, request_hash=digest(encoded), request=encoded.decode(),
                      chart_hash=digest(canonical(request["chart_revision"])),
                      dataset_hash=digest(canonical(request["dataset"])),
                      chart_id=request["chart_revision"]["chart_id"],
                      revision=request["chart_revision"]["revision"],
                      chart=canonical(request["chart_revision"]).decode())
        async with self.sessions() as session, session.begin():
            params.update(await self._authorize(session, principal))
            # One project lock makes idempotency, immutable revisions and quotas
            # deterministic even across independent workers/process restarts.
            lock = int.from_bytes(bytes.fromhex(digest(canonical(
                {"formspace": params["project_id"], "tenant": params["tenant_id"]})))[:8],
                                  "big", signed=True)
            await session.execute(text("SELECT pg_advisory_xact_lock(:lock)"), {"lock": lock})
            existing = (await session.execute(text(
                "SELECT * FROM formspace.job WHERE " + SCOPE + " AND idempotency_key=:key"),
                params)).mappings().first()
            if existing:
                if existing["request_hash"] != params["request_hash"]:
                    raise FormSpaceError("idempotency_conflict", 409)
                return receipt(dict(existing)), False
            active = (await session.execute(text("""SELECT count(*) FROM formspace.job
                WHERE tenant_id=:tenant_id AND project_id=:project_id
                AND state IN ('admitted','running','archiving')"""), params)).scalar_one()
            if active >= MAX_PENDING:
                raise FormSpaceError("pending_quota_exceeded", 429)
            previous = (await session.execute(text("SELECT chart_hash FROM formspace.chart_revision WHERE "
                + SCOPE + " AND chart_id=:chart_id AND revision=:revision"), params)).scalar_one_or_none()
            if previous and previous != params["chart_hash"]:
                raise FormSpaceError("chart_revision_conflict", 409)
            await session.execute(text("""INSERT INTO formspace.chart_revision
                (issuer,subject,tenant_id,project_id,chart_id,revision,chart_hash,definition)
                VALUES (:issuer,:subject,:tenant_id,:project_id,:chart_id,:revision,:chart_hash,
                        CAST(:chart AS jsonb)) ON CONFLICT DO NOTHING"""), params)
            params["job_id"] = str(uuid4())
            row = (await session.execute(text("""INSERT INTO formspace.job
                (job_id,issuer,subject,tenant_id,project_id,idempotency_key,request_hash,
                 chart_hash,dataset_hash,request)
                VALUES (:job_id,:issuer,:subject,:tenant_id,:project_id,:key,:request_hash,
                        :chart_hash,:dataset_hash,CAST(:request AS jsonb)) RETURNING *"""),
                params)).mappings().one()
            await session.execute(text("INSERT INTO formspace.outbox(job_id) VALUES (:job_id)"), params)
            return receipt(dict(row)), True

    async def get(self, principal, job_id):
        async with self.sessions() as session, session.begin():
            params = dict(await self._authorize(session, principal), job_id=job_id)
            row = (await session.execute(text("SELECT * FROM formspace.job WHERE " + SCOPE
                + " AND job_id=:job_id"), params)).mappings().first()
            if row is None:
                raise FormSpaceError("job_not_found", 404)
            return dict(row)

    async def list(self, principal, limit=50):
        async with self.sessions() as session, session.begin():
            params = dict(await self._authorize(session, principal), limit=limit)
            rows = (await session.execute(text("SELECT * FROM formspace.job WHERE " + SCOPE
                + " ORDER BY created_at DESC, job_id DESC LIMIT :limit"), params)).mappings().all()
            return [receipt(dict(row)) for row in rows]

    async def cancel(self, principal, job_id):
        async with self.sessions() as session, session.begin():
            params = dict(await self._authorize(session, principal), job_id=job_id)
            row = (await session.execute(text("SELECT * FROM formspace.job WHERE " + SCOPE
                + " AND job_id=:job_id FOR UPDATE"), params)).mappings().first()
            if row is None:
                raise FormSpaceError("job_not_found", 404)
            if row["state"] in ("completed", "failed"):
                raise FormSpaceError("job_terminal", 409)
            row = (await session.execute(text("""UPDATE formspace.job SET state='cancelled',
                output_bytes=NULL,updated_at=now() WHERE job_id=:job_id RETURNING *"""),
                params)).mappings().one()
            await session.execute(text("""UPDATE formspace.outbox SET done=true,fence=fence+1,
                lease_token=NULL,lease_until=NULL WHERE job_id=:job_id"""), params)
            return receipt(dict(row))

    async def claim(self, worker_id):
        async with self.sessions() as session, session.begin():
            # Short dispatch lock bounds concurrent leases atomically; it is held
            # only for claim admission, never during scalar or archive execution.
            await session.execute(text("SELECT pg_advisory_xact_lock(5075400608589832547)"))
            running = (await session.execute(text("""SELECT count(*) FROM formspace.outbox
                WHERE NOT done AND lease_until>clock_timestamp()"""))).scalar_one()
            if running >= MAX_RUNNING:
                return None
            # Shared membership is then locked/rechecked on the selected owner.
            # Inactive owners cannot monopolize the ready queue.
            row = (await session.execute(text("""SELECT j.* FROM formspace.outbox o
                JOIN formspace.job j USING(job_id)
                JOIN retention.membership m ON m.issuer=j.issuer AND m.subject=j.subject
                    AND m.tenant_id=CAST(j.tenant_id AS text)
                    AND m.project_id=CAST(j.project_id AS text) AND m.active
                WHERE NOT o.done AND o.available_at<=now()
                    AND (o.lease_until IS NULL OR o.lease_until<=clock_timestamp())
                    AND j.state IN ('admitted','running','archiving')
                    AND (SELECT count(*) FROM formspace.outbox active_o
                        JOIN formspace.job active_j USING(job_id)
                        WHERE active_j.tenant_id=j.tenant_id AND active_j.project_id=j.project_id
                          AND NOT active_o.done AND active_o.lease_until>clock_timestamp()) < :project_max
                ORDER BY o.available_at,j.created_at LIMIT 1
                FOR UPDATE OF j,o SKIP LOCKED"""), {"project_max": MAX_PROJECT_RUNNING})).mappings().first()
            if row is None:
                return None
            row = dict(row)
            await self._authorize(session, self.owner(row))
            params = dict(job_id=str(row["job_id"]), worker_id=worker_id,
                          token=secrets.token_urlsafe(32), seconds=LEASE_SECONDS)
            outbox = (await session.execute(text("""UPDATE formspace.outbox
                SET lease_token=:token,worker_id=:worker_id,
                    lease_until=clock_timestamp()+make_interval(secs=>:seconds),
                    attempts=attempts+1,fence=fence+1 WHERE job_id=:job_id RETURNING *"""),
                params)).mappings().one()
            if outbox["attempts"] > MAX_ATTEMPTS and row["output_bytes"] is None:
                await session.execute(text("""UPDATE formspace.job SET state='failed',
                    error_code='attempt_limit',updated_at=now() WHERE job_id=:job_id"""), params)
                await session.execute(text("UPDATE formspace.outbox SET done=true WHERE job_id=:job_id"), params)
                return None
            if row["state"] != "archiving":
                await session.execute(text("UPDATE formspace.job SET state='running',updated_at=now() WHERE job_id=:job_id"), params)
                row["state"] = "running"
            request = row["request"]
            return {"job": receipt(row), "request": json.loads(request) if isinstance(request, str) else request,
                    "has_output": row["output_bytes"] is not None,
                    "lease": {"token": params["token"], "fence": outbox["fence"],
                              "expires_at": outbox["lease_until"]}}

    async def _fenced(self, session, job_id, lease):
        params = dict(job_id=job_id, token=lease["lease_token"], fence=lease["fence"])
        row = (await session.execute(text("""SELECT j.* FROM formspace.job j
            JOIN formspace.outbox o USING(job_id) WHERE j.job_id=:job_id
            AND o.lease_token=:token AND o.fence=:fence AND o.lease_until>clock_timestamp()
            AND NOT o.done AND j.state IN ('running','archiving') FOR UPDATE OF j,o"""),
            params)).mappings().first()
        if row is None:
            raise FormSpaceError("lease_lost", 409)
        row = dict(row)
        await self._authorize(session, self.owner(row))
        # Authorization may block behind revocation. PostgreSQL now() is fixed
        # at transaction start, so check live time again after acquiring locks.
        live = (await session.execute(text("""SELECT 1 FROM formspace.outbox
            WHERE job_id=:job_id AND lease_token=:token AND fence=:fence
              AND lease_until>clock_timestamp() AND NOT done"""), params)).scalar_one_or_none()
        if live is None:
            raise FormSpaceError("lease_lost", 409)
        return row, params

    async def leased(self, job_id, lease):
        async with self.sessions() as session, session.begin():
            row, _ = await self._fenced(session, job_id, lease)
            return row

    async def heartbeat(self, job_id, lease):
        async with self.sessions() as session, session.begin():
            row, params = await self._fenced(session, job_id, lease)
            params["seconds"] = LEASE_SECONDS
            await session.execute(text("""UPDATE formspace.outbox SET
                lease_until=clock_timestamp()+make_interval(secs=>:seconds) WHERE job_id=:job_id"""), params)
            return receipt(row)

    async def computed(self, job_id, lease, payload, sha256):
        async with self.sessions() as session, session.begin():
            row, params = await self._fenced(session, job_id, lease)
            if row["output_sha256"] and row["output_sha256"] != sha256:
                raise FormSpaceError("output_conflict", 409)
            params.update(payload=payload, sha256=sha256)
            row = (await session.execute(text("""UPDATE formspace.job SET state='archiving',
                output_bytes=:payload,output_sha256=:sha256,updated_at=now()
                WHERE job_id=:job_id RETURNING *"""), params)).mappings().one()
            return receipt(dict(row))

    async def retained(self, job_id, lease, artifact, *, verified=False):
        async with self.sessions() as session, session.begin():
            row, params = await self._fenced(session, job_id, lease)
            if artifact["sha256"] != row["output_sha256"]:
                raise FormSpaceError("artifact_integrity_failed", 502)
            if row["artifact_id"] and str(row["artifact_id"]) != str(artifact["artifact_id"]):
                raise FormSpaceError("artifact_conflict", 409)
            if verified and artifact["state"] != "verified":
                raise FormSpaceError("artifact_not_verified", 409)
            params.update(artifact_id=str(artifact["artifact_id"]),
                          artifact_state=artifact["state"],
                          state="completed" if verified else "archiving", verified=verified)
            row = (await session.execute(text("""UPDATE formspace.job SET
                artifact_id=:artifact_id,artifact_state=:artifact_state,state=:state,
                output_bytes=CASE WHEN :verified THEN NULL ELSE output_bytes END,
                updated_at=now() WHERE job_id=:job_id RETURNING *"""), params)).mappings().one()
            await session.execute(text("""UPDATE formspace.outbox SET done=:verified,
                lease_token=NULL,lease_until=NULL,available_at=now()+interval '15 seconds'
                WHERE job_id=:job_id"""), params)
            return receipt(dict(row))

    async def fail(self, job_id, lease, error_code):
        async with self.sessions() as session, session.begin():
            row, params = await self._fenced(session, job_id, lease)
            params["error_code"] = error_code
            if error_code == "retention_unavailable" and row["output_bytes"] is not None:
                await session.execute(text("""UPDATE formspace.outbox SET lease_token=NULL,
                    lease_until=NULL,available_at=now()+interval '30 seconds' WHERE job_id=:job_id"""), params)
            else:
                await session.execute(text("UPDATE formspace.job SET state='failed',updated_at=now() WHERE job_id=:job_id"), params)
                await session.execute(text("UPDATE formspace.outbox SET done=true WHERE job_id=:job_id"), params)
            row = (await session.execute(text("""UPDATE formspace.job SET error_code=:error_code,
                updated_at=now() WHERE job_id=:job_id RETURNING *"""), params)).mappings().one()
            return receipt(dict(row))

    async def memory(self, principal, job_id, state, memory_id):
        async with self.sessions() as session, session.begin():
            params = dict(await self._authorize(session, principal), job_id=job_id, state=state,
                          memory_id=memory_id)
            await session.execute(text("UPDATE formspace.job SET memory_state=:state,memory_id=:memory_id WHERE "
                + SCOPE + " AND job_id=:job_id AND state='completed'"), params)
