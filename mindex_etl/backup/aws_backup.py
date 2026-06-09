"""
AWS backup + snapshot for MINDEX.
=================================
Durable, off-site copies of the canonical database and the NAS library to S3,
with lifecycle into Glacier for the petabyte cold tier.

Three operations (each idempotent, each recorded in ``etl.backup_log``):

1. ``pg_dump_to_s3``  — ``pg_dump -Fc`` of the canonical DB, uploaded to
   ``s3://$AWS_S3_MINDEX_BUCKET/backups/pg/`` as Standard-IA. A bucket lifecycle
   rule transitions these to Glacier after N days.
2. ``sync_nas_manifest_to_s3`` — a compact JSON manifest of the NAS library
   (path, size, mtime) uploaded to S3. Manifest-only by default so it scales to
   a petabyte of files without copying them inline; opt-in Deep-Archive offload
   of flagged cold directories via ``MINDEX_NAS_OFFLOAD_DIRS``.
3. ``create_snapshot`` — convenience: pg_dump + NAS manifest in one call.

Everything degrades gracefully: no ``boto3``, no credentials, or no
``AWS_S3_MINDEX_BUCKET`` => the op is recorded as ``skipped`` and returns
cleanly. Backups must never crash the runtime.
"""
from __future__ import annotations

import gzip
import json
import logging
import os
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("mindex.backup.aws")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _stamp() -> str:
    return _utcnow().strftime("%Y%m%d_%H%M%S")


def _state_store():
    """Lazy AgentStateStore for backup_log writes (optional)."""
    try:
        from ..agents.state import AgentStateStore

        store = AgentStateStore()
        store.ensure_schema()
        return store
    except Exception:
        return None


def _s3_client():
    """Return (client, bucket) or (None, None) when not configured."""
    bucket = os.getenv("AWS_S3_MINDEX_BUCKET", "").strip()
    if not bucket:
        logger.info("AWS_S3_MINDEX_BUCKET not set — backups skipped.")
        return None, None
    try:
        import boto3  # type: ignore
    except Exception as exc:  # pragma: no cover
        logger.warning("boto3 not installed — backups skipped (%s). `pip install boto3`.", exc)
        return None, None

    kwargs: Dict[str, Any] = {}
    region = os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION")
    if region:
        kwargs["region_name"] = region
    endpoint = os.getenv("S3_ENDPOINT_URL")  # S3-compatible (MinIO/Wasabi/etc.)
    if endpoint:
        kwargs["endpoint_url"] = endpoint
    try:
        return boto3.client("s3", **kwargs), bucket
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not create S3 client: %s", exc)
        return None, None


def _db_env() -> Dict[str, str]:
    return {
        "host": os.getenv("MINDEX_DB_HOST", "localhost"),
        "port": os.getenv("MINDEX_DB_PORT", "5432"),
        "user": os.getenv("MINDEX_DB_USER", "mindex"),
        "password": os.getenv("MINDEX_DB_PASSWORD", "mindex"),
        "name": os.getenv("MINDEX_DB_NAME", "mindex"),
    }


# ---------------------------------------------------------------------------
# 1) Postgres dump -> S3
# ---------------------------------------------------------------------------
def pg_dump_to_s3(
    prefix: str = "backups/pg",
    storage_class: str = "STANDARD_IA",
) -> Dict[str, Any]:
    store = _state_store()
    client, bucket = _s3_client()
    if client is None:
        if store:
            store.record_backup("pg_dump", status="skipped", finished=True,
                                metadata={"reason": "s3_not_configured"})
        return {"status": "skipped", "reason": "s3_not_configured"}

    db = _db_env()
    key = f"{prefix}/{db['name']}_{_stamp()}.dump"
    target = f"s3://{bucket}/{key}"
    if store:
        store.record_backup("pg_dump", target=target, status="running",
                            storage_class=storage_class)

    tmp_path: Optional[str] = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".dump", delete=False) as tmp:
            tmp_path = tmp.name

        env = dict(os.environ, PGPASSWORD=db["password"])
        cmd = [
            "pg_dump",
            "-h", db["host"], "-p", db["port"], "-U", db["user"],
            "-d", db["name"],
            "-Fc",  # custom format = compressed + restorable via pg_restore
            "-f", tmp_path,
        ]
        logger.info("Running pg_dump for %s -> %s", db["name"], target)
        proc = subprocess.run(env=env, args=cmd, capture_output=True, text=True, timeout=6 * 3600)
        if proc.returncode != 0:
            raise RuntimeError(f"pg_dump failed: {(proc.stderr or '')[-500:]}")

        size = os.path.getsize(tmp_path)
        client.upload_file(
            tmp_path, bucket, key,
            ExtraArgs={"StorageClass": storage_class, "ContentType": "application/octet-stream"},
        )
        logger.info("Uploaded pg backup: %s (%.1f MB)", target, size / (1024 * 1024))
        report = {"status": "success", "target": target, "size_bytes": size,
                  "storage_class": storage_class}
        if store:
            store.record_backup("pg_dump", target=target, status="success",
                                size_bytes=size, storage_class=storage_class,
                                finished=True, metadata={"format": "custom"})
        return report
    except Exception as exc:  # noqa: BLE001
        logger.error("pg_dump_to_s3 failed: %s", exc)
        if store:
            store.record_backup("pg_dump", target=target, status="error",
                                error=str(exc)[:500], finished=True)
        return {"status": "error", "error": str(exc), "target": target}
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


# ---------------------------------------------------------------------------
# 2) NAS manifest -> S3  (+ optional Deep-Archive offload of flagged dirs)
# ---------------------------------------------------------------------------
def sync_nas_manifest_to_s3(
    prefix: str = "backups/nas-manifest",
    max_entries: int = 2_000_000,
) -> Dict[str, Any]:
    store = _state_store()
    client, bucket = _s3_client()
    nas_root = Path(os.getenv("NAS_MOUNT_PATH", "/mnt/nas/mindex"))

    if client is None:
        if store:
            store.record_backup("nas_manifest", status="skipped", finished=True,
                                metadata={"reason": "s3_not_configured"})
        return {"status": "skipped", "reason": "s3_not_configured"}
    if not nas_root.exists():
        logger.info("NAS not mounted at %s — manifest skipped.", nas_root)
        if store:
            store.record_backup("nas_manifest", status="skipped", finished=True,
                                metadata={"reason": "nas_not_mounted", "path": str(nas_root)})
        return {"status": "skipped", "reason": "nas_not_mounted"}

    key = f"{prefix}/manifest_{_stamp()}.json.gz"
    target = f"s3://{bucket}/{key}"
    if store:
        store.record_backup("nas_manifest", target=target, status="running")

    try:
        entries: List[Dict[str, Any]] = []
        total_bytes = 0
        count = 0
        for path in nas_root.rglob("*"):
            if not path.is_file():
                continue
            try:
                stat = path.stat()
            except OSError:
                continue
            total_bytes += stat.st_size
            count += 1
            if count <= max_entries:
                entries.append({
                    "path": str(path.relative_to(nas_root)),
                    "size": stat.st_size,
                    "mtime": int(stat.st_mtime),
                })

        manifest = {
            "generated_at": _utcnow().isoformat(),
            "nas_root": str(nas_root),
            "file_count": count,
            "total_bytes": total_bytes,
            "truncated": count > max_entries,
            "entries": entries,
        }
        body = gzip.compress(json.dumps(manifest, default=str).encode("utf-8"))
        client.put_object(
            Bucket=bucket, Key=key, Body=body,
            ContentType="application/gzip", ContentEncoding="gzip",
            StorageClass="STANDARD_IA",
        )
        logger.info(
            "NAS manifest uploaded: %s (%d files, %.1f GB tracked)",
            target, count, total_bytes / (1024 ** 3),
        )

        offload = _offload_cold_dirs(client, bucket, nas_root, store)

        report = {
            "status": "success",
            "target": target,
            "object_count": count,
            "size_bytes": total_bytes,
            "manifest_bytes": len(body),
            "offload": offload,
        }
        if store:
            store.record_backup(
                "nas_manifest", target=target, status="success",
                object_count=count, size_bytes=total_bytes, finished=True,
                metadata={"truncated": manifest["truncated"], "offload": offload},
            )
        return report
    except Exception as exc:  # noqa: BLE001
        logger.error("sync_nas_manifest_to_s3 failed: %s", exc)
        if store:
            store.record_backup("nas_manifest", target=target, status="error",
                                error=str(exc)[:500], finished=True)
        return {"status": "error", "error": str(exc)}


def _offload_cold_dirs(client, bucket: str, nas_root: Path, store) -> Dict[str, Any]:
    """Opt-in: copy flagged cold directories to S3 Deep Archive (Glacier).

    Controlled by ``MINDEX_NAS_OFFLOAD_DIRS`` (comma list of NAS-relative dirs).
    Bounded by ``MINDEX_NAS_OFFLOAD_MAX_GB`` so a single run can't blow the
    egress budget. Default: disabled (no dirs flagged)."""
    dirs = [d.strip() for d in os.getenv("MINDEX_NAS_OFFLOAD_DIRS", "").split(",") if d.strip()]
    if not dirs:
        return {"enabled": False}

    max_gb = float(os.getenv("MINDEX_NAS_OFFLOAD_MAX_GB", "50"))
    budget = int(max_gb * (1024 ** 3))
    uploaded = 0
    moved_bytes = 0
    for rel in dirs:
        base = nas_root / rel
        if not base.exists():
            continue
        for path in base.rglob("*"):
            if not path.is_file() or moved_bytes >= budget:
                continue
            try:
                size = path.stat().st_size
            except OSError:
                continue
            key = f"nas-cold/{path.relative_to(nas_root)}"
            try:
                client.upload_file(
                    str(path), bucket, key,
                    ExtraArgs={"StorageClass": "DEEP_ARCHIVE"},
                )
                uploaded += 1
                moved_bytes += size
            except Exception as exc:  # noqa: BLE001
                logger.debug("Glacier offload failed for %s: %s", path, exc)
    if store and uploaded:
        store.record_backup(
            "nas_offload", status="success", object_count=uploaded,
            size_bytes=moved_bytes, storage_class="DEEP_ARCHIVE", finished=True,
            metadata={"dirs": dirs},
        )
    return {"enabled": True, "objects": uploaded, "bytes": moved_bytes}


# ---------------------------------------------------------------------------
# 3) Snapshot = pg_dump + NAS manifest
# ---------------------------------------------------------------------------
def create_snapshot() -> Dict[str, Any]:
    return {
        "pg": pg_dump_to_s3(),
        "nas": sync_nas_manifest_to_s3(),
        "at": _utcnow().isoformat(),
    }


def main() -> int:
    import argparse

    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser(description="MINDEX AWS backup")
    parser.add_argument("op", choices=["pg", "nas", "snapshot"], help="Backup operation")
    args = parser.parse_args()

    if args.op == "pg":
        report = pg_dump_to_s3()
    elif args.op == "nas":
        report = sync_nas_manifest_to_s3()
    else:
        report = create_snapshot()
    print(json.dumps(report, indent=2, default=str))
    return 0 if report.get("status") != "error" else 1


if __name__ == "__main__":
    raise SystemExit(main())
