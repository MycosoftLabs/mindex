"""
NLM P0 acoustic ingest — download, normalize (16 kHz mono WAV), organized NAS paths,
full per-file catalog labels, library.blob + library.source registry.

Usage:
  python -m mindex_etl.jobs.ingest_nlm_audio_p0 --sources esc50,mbari_pacific_sound --max-files-per-source 2000 --max-gb 100
"""
from __future__ import annotations

import argparse
import logging
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Any, Optional

from ..db import db_session
from ..library.audio_normalize import normalize_to_wav, sha256_file, write_sidecar_manifest
from ..library.catalog_record import (
    CatalogRecord,
    catalog_from_esc50,
    catalog_from_mbari,
)
from ..library.db_registry import finish_manifest, register_blob, start_manifest
from ..library.nas_mount import require_nas_mount
from ..library.nas_paths import ensure_category_dirs, library_acoustic_root
from ..library.nlm_source_registry import get_source, upsert_sources
from ..library.sources_esc50 import iter_esc50_audio
from ..library.sources_hf import iter_hf_audio_dataset
from ..library.sources_hf_hub import iter_hf_hub_parquet_audio
from ..library.sources_mbari import iter_mbari_s3

logger = logging.getLogger(__name__)

HF_AUDIO_SOURCES = {
    "ds3500": ("peng7554/DS3500", "audio"),
}


def _bytes_budget(max_gb: float) -> int:
    return int(max_gb * (1024**3))


def _library_root_parent() -> Path:
    return library_acoustic_root().parent


def _dest_for_catalog(catalog: CatalogRecord) -> Path:
    out = _library_root_parent() / catalog.organized_relpath()
    out.parent.mkdir(parents=True, exist_ok=True)
    return out


def ingest_source(
    conn,
    source_id: str,
    dest_root: Path,
    max_files: int,
    bytes_remaining: list[int],
) -> tuple[int, int]:
    manifest_id = start_manifest(conn, source_id)
    registered = 0
    bytes_total = 0
    src_defaults = get_source(source_id)

    def process_file(
        src_path: Path,
        catalog: CatalogRecord,
        extra: Optional[dict[str, Any]] = None,
    ) -> None:
        nonlocal registered, bytes_total
        if bytes_remaining[0] <= 0 or registered >= max_files:
            return
        out_path = _dest_for_catalog(catalog)
        if out_path.is_file():
            content_hash = sha256_file(out_path)
            probe: dict[str, Any] = {}
        else:
            probe = normalize_to_wav(src_path, out_path)
            content_hash = sha256_file(out_path)
            sidecar = {
                **catalog.to_db_kwargs(),
                "probe": probe,
                **(extra or {}),
            }
            write_sidecar_manifest(out_path, sidecar)
        size = out_path.stat().st_size
        bytes_remaining[0] -= size
        bytes_total += size
        lib_parent = _library_root_parent()
        rel = str(out_path.relative_to(lib_parent)).replace("\\", "/")
        db_kw = catalog.to_db_kwargs()
        if register_blob(
            conn,
            source_id=source_id,
            rel_path=rel,
            abs_path=str(out_path),
            filename=catalog.filename,
            content_hash=content_hash,
            size_bytes=size,
            manifest_id=manifest_id,
            sensor_type=catalog.sensor_type,
            license_name=catalog.license,
            metadata=db_kw.get("metadata"),
            title=db_kw.get("title"),
            description=db_kw.get("description"),
            label_primary=db_kw.get("label_primary"),
            label_secondary=db_kw.get("label_secondary"),
            acoustic_environment=db_kw.get("acoustic_environment"),
            source_name=db_kw.get("source_name"),
            source_url=db_kw.get("source_url"),
            origin_dataset_id=db_kw.get("origin_dataset_id"),
            nlm_subsystem=db_kw.get("nlm_subsystem"),
            nlm_priority=db_kw.get("nlm_priority"),
            fold_id=db_kw.get("fold_id"),
            training_split=db_kw.get("training_split"),
            locale=db_kw.get("locale"),
            capture_time_utc=db_kw.get("capture_time_utc"),
        ):
            registered += 1

    try:
        if source_id == "esc50":
            for blob, inner, meta_row in iter_esc50_audio(max_files):
                if bytes_remaining[0] <= 0:
                    break
                catalog = catalog_from_esc50(inner, meta_row)
                with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
                    tmp.write(blob)
                    tmp.flush()
                    process_file(Path(tmp.name), catalog, {"archive_path": inner})
                Path(tmp.name).unlink(missing_ok=True)

        elif source_id == "mbari_pacific_sound":
            for path, bucket, key in iter_mbari_s3(max_files):
                if bytes_remaining[0] <= 0:
                    break
                catalog = catalog_from_mbari(key, bucket)
                process_file(path, catalog, {"s3_bucket": bucket, "s3_key": key})
                path.unlink(missing_ok=True)

        elif source_id in HF_AUDIO_SOURCES:
            ds_id, col = HF_AUDIO_SOURCES[source_id]
            src = get_source(source_id)
            used = 0

            def _hf_catalog(fname: str, row_meta: dict) -> CatalogRecord:
                label = str(row_meta.get("label") or row_meta.get("class") or "vessel_signature")
                return CatalogRecord(
                    origin_dataset_id=source_id,
                    filename=fname if fname.lower().endswith(".wav") else f"{Path(fname).stem}.wav",
                    label_primary=label,
                    title=f"{src['name']}: {label} — {fname}",
                    description=(
                        f"Underwater acoustic clip from HuggingFace {ds_id}. "
                        f"For NLM vessel/target classification. {src['description']}"
                    ),
                    acoustic_environment=src["acoustic_environment"],
                    sensor_type=src["sensor_type"],
                    source_name=src["name"],
                    source_url=src["source_url"],
                    license=src["license"],
                    nlm_subsystem=src["nlm_subsystem"],
                    nlm_priority=src["nlm_priority"],
                    extra=row_meta,
                )

            for path, row_meta in iter_hf_hub_parquet_audio(ds_id, max_files):
                if bytes_remaining[0] <= 0:
                    break
                process_file(path, _hf_catalog(path.name, row_meta), row_meta)
                used += 1
            if used == 0:
                for path, row_meta in iter_hf_audio_dataset(ds_id, col, max_files):
                    if bytes_remaining[0] <= 0:
                        break
                    process_file(path, _hf_catalog(path.name, row_meta), row_meta)
                    if str(path).startswith(tempfile.gettempdir()):
                        path.unlink(missing_ok=True)

        else:
            logger.warning("Unknown source_id %s — skip", source_id)

        finish_manifest(
            conn,
            manifest_id,
            registered,
            bytes_total,
            "complete",
            {"max_files": max_files, "catalog_version": "2026-06-04"},
        )
    except Exception as exc:
        logger.exception("Source %s failed: %s", source_id, exc)
        finish_manifest(conn, manifest_id, registered, bytes_total, "failed", {"error": str(exc)})
    return registered, bytes_total


def run_ingest(
    sources: list[str],
    max_files_per_source: int,
    max_gb: float,
) -> int:
    require_nas_mount()
    ensure_category_dirs()
    root = library_acoustic_root()
    logger.info("NAS acoustic root (remote mount): %s", root)

    total_registered = 0
    budget = [_bytes_budget(max_gb)]

    with db_session() as conn:
        upsert_sources(conn)
        conn.commit()
        for source_id in sources:
            if budget[0] <= 0:
                logger.info("Byte budget exhausted; stopping.")
                break
            n, b = ingest_source(conn, source_id, root, max_files_per_source, budget)
            conn.commit()
            total_registered += n
            logger.info("Source %s: registered=%s bytes=%s", source_id, n, b)

    return total_registered


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="NLM P0 acoustic ingest to NAS + library.blob")
    parser.add_argument(
        "--sources",
        default="esc50,mbari_pacific_sound",
        help="Comma-separated source ids",
    )
    parser.add_argument("--max-files-per-source", type=int, default=5000)
    parser.add_argument("--max-gb", type=float, default=200.0)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    sources = [s.strip() for s in args.sources.split(",") if s.strip()]
    count = run_ingest(sources, args.max_files_per_source, args.max_gb)
    logger.info("Total registered: %s", count)
    return 0 if count >= 0 else 1


if __name__ == "__main__":
    sys.exit(main())
