from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List


SECTION_TO_SILO = {
    "UNDERWATER ACOUSTIC DATABASES": "underwater_pam",
    "VESSEL / SUBMARINE ACOUSTIC SIGNATURES": "vessel_uatr",
    "MARINE MAMMAL & BIOLOGICAL SOUNDS": "marine_bio",
    "AERIAL / DRONE / BIRD ACOUSTIC": "aerial_bio_uav",
    "EXPLOSION / WEAPON / MILITARY ACOUSTIC": "threat_munitions",
    "ENVIRONMENTAL SOUND CLASSIFICATION (TRANSFER LEARNING)": "env_transfer_audio",
    "NOAA / NASA / GOVERNMENT OCEANOGRAPHIC DATA": "oceanographic_grid",
    "BATHYMETRY & SEAFLOOR TERRAIN": "bathymetry",
    "MAGNETOMETER & MAGNETIC ANOMALY DETECTION (MAD)": "magnetic",
    "AIS / SATELLITE / GEOSPATIAL MARITIME": "ais_maritime",
    "PRE-TRAINED ML MODELS & WEIGHTS": "model_registry",
    "SONAR IMAGE & TARGET DETECTION": "sonar_imagery",
    "PAM SOFTWARE & FRAMEWORKS": "env_transfer_audio",
}


def parse_training_sources(doc_path: str | Path) -> List[Dict[str, Any]]:
    path = Path(doc_path)
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()

    current_section = "UNSPECIFIED"
    current_section_target = ""
    current_repo_targets: List[str] = []
    current_dataset: Dict[str, Any] | None = None
    datasets: List[Dict[str, Any]] = []

    repo_pattern = re.compile(r"^\*\*Repo\*\*:\s*(.+)$")
    target_pattern = re.compile(r"^\*\*NLM Target\*\*:\s*(.+)$")
    dataset_header_pattern = re.compile(r"^###\s+(.+)$")
    bullet_pattern = re.compile(r"^- \*\*(.+?)\*\*:\s*(.+)$")
    section_pattern = re.compile(r"^## SECTION \d+:\s+(.+)$")

    def finish_dataset() -> None:
        nonlocal current_dataset
        if current_dataset and current_dataset.get("dataset_id"):
            current_dataset.setdefault("section_category", current_section)
            current_dataset.setdefault("nlm_target", current_section_target)
            current_dataset.setdefault("repo_targets", current_repo_targets)
            current_dataset.setdefault("modality_silo", SECTION_TO_SILO.get(current_section, "unclassified"))
            current_dataset.setdefault("source_urls", [])
            current_dataset.setdefault("raw_metadata", {})
            datasets.append(current_dataset)
        current_dataset = None

    for line in lines:
        section_match = section_pattern.match(line)
        if section_match:
            finish_dataset()
            current_section = section_match.group(1).strip()
            current_section_target = ""
            current_repo_targets = []
            continue

        repo_match = repo_pattern.match(line)
        if repo_match:
            current_repo_targets = [part.strip(" `") for part in repo_match.group(1).split(",")]
            continue

        target_match = target_pattern.match(line)
        if target_match:
            current_section_target = target_match.group(1).strip()
            continue

        header_match = dataset_header_pattern.match(line)
        if header_match:
            finish_dataset()
            current_dataset = {
                "name": header_match.group(1).strip(),
                "section_category": current_section,
                "nlm_target": current_section_target,
                "repo_targets": current_repo_targets,
                "modality_silo": SECTION_TO_SILO.get(current_section, "unclassified"),
                "source_urls": [],
                "raw_metadata": {},
            }
            continue

        if current_dataset is None:
            continue

        bullet_match = bullet_pattern.match(line)
        if not bullet_match:
            continue

        key = bullet_match.group(1).strip().lower().replace(" ", "_")
        value = bullet_match.group(2).strip()
        current_dataset["raw_metadata"][key] = value

        if key == "id":
            current_dataset["dataset_id"] = value
        elif key == "url":
            current_dataset["source_url"] = value
            current_dataset["source_urls"].append(value)
        elif key in {"aws", "github", "doi", "download", "ncei", "noaa"}:
            current_dataset["source_urls"].append(value)
        elif key == "type":
            current_dataset["dataset_type"] = value
        elif key == "format":
            current_dataset["file_format"] = value
        elif key == "size":
            current_dataset["size_estimate"] = value
        elif key == "access":
            current_dataset["access_level"] = value
        elif key == "nlm_target":
            current_dataset["nlm_target"] = value
        elif key == "priority":
            current_dataset["priority"] = value

    finish_dataset()
    return datasets
