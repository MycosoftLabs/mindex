from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from psycopg import Connection


def start_manifest(conn: Connection, source_id: str, category: str = "acoustic") -> uuid.UUID:
    manifest_id = uuid.uuid4()
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO library.manifest (id, source_id, category, status, run_started_at)
            VALUES (%s, %s, %s, 'running', %s)
            """,
            (manifest_id, source_id, category, datetime.now(timezone.utc)),
        )
    return manifest_id


def finish_manifest(
    conn: Connection,
    manifest_id: uuid.UUID,
    files_registered: int,
    bytes_total: int,
    status: str,
    metadata: Optional[dict[str, Any]] = None,
) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE library.manifest
            SET run_finished_at = %s, files_registered = %s, bytes_total = %s,
                status = %s, metadata = COALESCE(metadata, '{}'::jsonb) || %s::jsonb
            WHERE id = %s
            """,
            (
                datetime.now(timezone.utc),
                files_registered,
                bytes_total,
                status,
                json.dumps(metadata or {}),
                manifest_id,
            ),
        )


def register_blob(
    conn: Connection,
    *,
    source_id: str,
    rel_path: str,
    abs_path: str,
    filename: str,
    content_hash: str,
    size_bytes: int,
    manifest_id: uuid.UUID,
    category: str = "acoustic",
    sensor_type: Optional[str] = None,
    duration_sec: Optional[float] = None,
    sample_rate_hz: Optional[int] = None,
    channels: Optional[int] = None,
    fmt: Optional[str] = None,
    codec: Optional[str] = None,
    license_name: Optional[str] = None,
    needs_transcode: bool = False,
    unsupported_codec: bool = False,
    metadata: Optional[dict[str, Any]] = None,
    title: Optional[str] = None,
    description: Optional[str] = None,
    label_primary: Optional[str] = None,
    label_secondary: Optional[str] = None,
    acoustic_environment: Optional[str] = None,
    source_name: Optional[str] = None,
    source_url: Optional[str] = None,
    origin_dataset_id: Optional[str] = None,
    nlm_subsystem: Optional[str] = None,
    nlm_priority: Optional[str] = None,
    fold_id: Optional[str] = None,
    training_split: Optional[str] = None,
    locale: Optional[str] = None,
    capture_time_utc: Optional[datetime] = None,
    update_labels_on_conflict: bool = False,
) -> bool:
    """Insert blob row; returns False if duplicate hash (unless label backfill)."""
    cols = [
        "source_id", "category", "sensor_type", "rel_path", "abs_path", "filename",
        "content_hash", "size_bytes", "duration_sec", "sample_rate_hz", "channels",
        "format", "codec", "license", "needs_transcode", "unsupported_codec",
        "manifest_id", "metadata",
        "title", "description", "label_primary", "label_secondary",
        "acoustic_environment", "source_name", "source_url", "origin_dataset_id",
        "nlm_subsystem", "nlm_priority", "fold_id", "training_split", "locale",
        "capture_time_utc",
    ]
    vals = [
        source_id, category, sensor_type, rel_path, abs_path, filename,
        content_hash, size_bytes, duration_sec, sample_rate_hz, channels,
        fmt, codec, license_name, needs_transcode, unsupported_codec,
        manifest_id, json.dumps(metadata or {}),
        title, description, label_primary, label_secondary,
        acoustic_environment, source_name, source_url, origin_dataset_id,
        nlm_subsystem, nlm_priority, fold_id, training_split, locale,
        capture_time_utc,
    ]
    placeholders = ", ".join(["%s"] * len(cols))
    col_list = ", ".join(cols)

    if update_labels_on_conflict:
        conflict = """
            ON CONFLICT (content_hash) DO UPDATE SET
                title = COALESCE(EXCLUDED.title, library.blob.title),
                description = COALESCE(EXCLUDED.description, library.blob.description),
                label_primary = COALESCE(EXCLUDED.label_primary, library.blob.label_primary),
                label_secondary = COALESCE(EXCLUDED.label_secondary, library.blob.label_secondary),
                acoustic_environment = COALESCE(EXCLUDED.acoustic_environment, library.blob.acoustic_environment),
                source_name = COALESCE(EXCLUDED.source_name, library.blob.source_name),
                source_url = COALESCE(EXCLUDED.source_url, library.blob.source_url),
                origin_dataset_id = COALESCE(EXCLUDED.origin_dataset_id, library.blob.origin_dataset_id),
                nlm_subsystem = COALESCE(EXCLUDED.nlm_subsystem, library.blob.nlm_subsystem),
                nlm_priority = COALESCE(EXCLUDED.nlm_priority, library.blob.nlm_priority),
                fold_id = COALESCE(EXCLUDED.fold_id, library.blob.fold_id),
                training_split = COALESCE(EXCLUDED.training_split, library.blob.training_split),
                locale = COALESCE(EXCLUDED.locale, library.blob.locale),
                capture_time_utc = COALESCE(EXCLUDED.capture_time_utc, library.blob.capture_time_utc),
                rel_path = EXCLUDED.rel_path,
                abs_path = EXCLUDED.abs_path,
                metadata = library.blob.metadata || EXCLUDED.metadata::jsonb
            RETURNING id
        """
    else:
        conflict = "ON CONFLICT (content_hash) DO NOTHING RETURNING id"

    with conn.cursor() as cur:
        cur.execute(
            f"""
            INSERT INTO library.blob ({col_list})
            VALUES ({placeholders})
            {conflict}
            """,
            vals,
        )
        row = cur.fetchone()
        return row is not None
