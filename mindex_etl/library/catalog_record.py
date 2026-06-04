from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from .nlm_source_registry import get_source


def _slug(text: str, max_len: int = 80) -> str:
    s = re.sub(r"[^a-zA-Z0-9._-]+", "_", text.strip().lower())
    return s[:max_len].strip("_") or "unknown"


@dataclass
class CatalogRecord:
    """Per-file training catalog entry for library.blob."""

    origin_dataset_id: str
    filename: str
    label_primary: str
    title: str
    description: str
    acoustic_environment: str
    sensor_type: str
    source_name: str
    source_url: str
    license: str
    nlm_subsystem: str
    nlm_priority: str
    label_secondary: Optional[str] = None
    fold_id: Optional[str] = None
    training_split: Optional[str] = None
    locale: Optional[str] = None
    capture_time_utc: Optional[datetime] = None
    extra: dict[str, Any] = field(default_factory=dict)

    def organized_relpath(self) -> str:
        """NAS path under Library/: acoustic/{source}/{env}/{label}/{file}"""
        env = _slug(self.acoustic_environment)
        label = _slug(self.label_primary)
        return f"acoustic/{self.origin_dataset_id}/{env}/{label}/{self.filename}"

    def to_db_kwargs(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "description": self.description,
            "label_primary": self.label_primary,
            "label_secondary": self.label_secondary,
            "acoustic_environment": self.acoustic_environment,
            "source_name": self.source_name,
            "source_url": self.source_url,
            "origin_dataset_id": self.origin_dataset_id,
            "nlm_subsystem": self.nlm_subsystem,
            "nlm_priority": self.nlm_priority,
            "fold_id": self.fold_id,
            "training_split": self.training_split,
            "locale": self.locale,
            "capture_time_utc": self.capture_time_utc,
            "sensor_type": self.sensor_type,
            "license_name": self.license,
            "metadata": {
                "catalog_version": "2026-06-04",
                "display_title": self.title,
                **self.extra,
            },
        }


def catalog_from_esc50(
    inner_path: str,
    meta_row: dict[str, Any],
) -> CatalogRecord:
    src = get_source("esc50")
    fname = Path(inner_path).name
    category = str(meta_row.get("category") or meta_row.get("target") or "unknown")
    fold = str(meta_row.get("fold", ""))
    esc10 = meta_row.get("esc10")
    env = "coastal_air" if category in {"sea_waves", "crickets"} else "air"
    title = f"ESC-50: {category.replace('_', ' ').title()} — clip {fname}"
    desc = (
        f"Environmental sound from ESC-50 (50-class benchmark). "
        f"Class '{category}' (fold {fold}). "
        f"Five-second field recording for NLM environmental/transfer learning. "
        f"Source: Karol Piczak ESC-50 / {src['source_url']}."
    )
    return CatalogRecord(
        origin_dataset_id="esc50",
        filename=fname if fname.lower().endswith(".wav") else f"{Path(fname).stem}.wav",
        label_primary=category,
        label_secondary=str(esc10) if esc10 is not None else None,
        title=title,
        description=desc,
        acoustic_environment=env,
        sensor_type=src["sensor_type"],
        source_name=src["name"],
        source_url=src["source_url"],
        license=src["license"],
        nlm_subsystem=src["nlm_subsystem"],
        nlm_priority=src["nlm_priority"],
        fold_id=fold or None,
        training_split=f"fold_{fold}" if fold else None,
        locale="field_recording",
        extra={"esc50_filename": fname, "esc10": esc10, "src_file": meta_row.get("src_file")},
    )


def catalog_from_mbari(s3_key: str, bucket: str) -> CatalogRecord:
    src = get_source("mbari_pacific_sound")
    fname = Path(s3_key).name
    # MARS_20180101_092406.wav or MARS-20200101T000000Z-2kHz.wav
    capture: Optional[datetime] = None
    m = re.search(r"(\d{8})", fname)
    if m:
        try:
            capture = datetime.strptime(m.group(1), "%Y%m%d").replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    year = m.group(1)[:4] if m else "unknown"
    label = "ambient_ocean"
    if "2khz" in bucket.lower() or "2kHz" in fname:
        label = "ambient_ocean_2khz_decimated"
    title = f"MBARI Pacific Sound — MARS hydrophone {year}"
    if capture:
        title += f" ({capture.date().isoformat()} UTC)"
    desc = (
        f"Open passive acoustic recording from MBARI MARS hydrophone off central California. "
        f"S3 object {bucket}/{s3_key}. Continuous ocean soundscape for ambient baseline, "
        f"biological and anthropogenic anomaly detection. Registry: {src['source_url']}."
    )
    return CatalogRecord(
        origin_dataset_id="mbari_pacific_sound",
        filename=fname if fname.lower().endswith(".wav") else f"{Path(fname).stem}.wav",
        label_primary=label,
        title=title,
        description=desc,
        acoustic_environment="underwater",
        sensor_type=src["sensor_type"],
        source_name=src["name"],
        source_url=f"https://{bucket}.s3.us-west-2.amazonaws.com/{s3_key}",
        license=src["license"],
        nlm_subsystem=src["nlm_subsystem"],
        nlm_priority=src["nlm_priority"],
        capture_time_utc=capture,
        locale="pacific_ocean_mars",
        extra={"s3_bucket": bucket, "s3_key": s3_key},
    )


def catalog_from_fsd50k(
    fname: str,
    labels: list[str],
    split: str,
) -> CatalogRecord:
    src = get_source("fsd50k")
    primary = labels[0] if labels else "unlabeled_event"
    secondary = labels[1] if len(labels) > 1 else None
    title = f"FSD50K: {primary.replace('_', ' ')} — {fname}"
    desc = (
        f"Human-verified FSD50K clip ({split} split). "
        f"Labels: {', '.join(labels) or 'none'}. "
        f"Fine-grained audio event for NLM pretrain. Source: {src['source_url']}."
    )
    return CatalogRecord(
        origin_dataset_id="fsd50k",
        filename=fname,
        label_primary=primary,
        label_secondary=secondary,
        title=title,
        description=desc,
        acoustic_environment="air",
        sensor_type=src["sensor_type"],
        source_name=src["name"],
        source_url=src["source_url"],
        license=src["license"],
        nlm_subsystem=src["nlm_subsystem"],
        nlm_priority=src["nlm_priority"],
        training_split=split,
        extra={"all_labels": labels},
    )
