"""
Canonical NLM / MINDEX library source registry (aligned with NLM_TRAINING_DATA_SOURCES.md).
Each entry is the parent dataset; every ingested file gets origin_dataset_id = id.
"""
from __future__ import annotations

from typing import Any

SOURCE_REGISTRY: dict[str, dict[str, Any]] = {
    "esc50": {
        "id": "esc50",
        "name": "ESC-50 Environmental Sound Classification",
        "source_url": "https://github.com/karolpiczak/ESC-50",
        "license": "CC BY-NC 3.0",
        "nlm_subsystem": "nlm/pretrain/",
        "nlm_priority": "P0",
        "sensor_type": "environmental_microphone",
        "acoustic_environment": "air",
        "description": "2000 five-second environmental sound clips in 50 balanced classes (rain, sea waves, dog, helicopter, etc.). Transfer-learning baseline for terrestrial and coastal ambience.",
        "access_level": "open",
        "format": "WAV",
    },
    "mbari_pacific_sound": {
        "id": "mbari_pacific_sound",
        "name": "MBARI Pacific Ocean Sound (AWS Open Data)",
        "source_url": "https://registry.opendata.aws/pacific-sound/",
        "license": "Open (MBARI / AWS Open Data Registry)",
        "nlm_subsystem": "nlm/data/ambient/",
        "nlm_priority": "P0",
        "sensor_type": "hydrophone",
        "acoustic_environment": "underwater",
        "description": "Continuous deep-ocean hydrophone recordings from MARS off central California. Includes biological, anthropogenic, and ambient ocean soundscapes.",
        "access_level": "open",
        "format": "WAV",
    },
    "ds3500": {
        "id": "ds3500",
        "name": "DS3500 Underwater Acoustic Target Classification",
        "source_url": "https://huggingface.co/datasets/peng7554/DS3500",
        "license": "See HuggingFace dataset card",
        "nlm_subsystem": "nlm/data/vessel/",
        "nlm_priority": "P0",
        "sensor_type": "hydrophone",
        "acoustic_environment": "underwater",
        "description": "Vessel underwater acoustic signatures for target classification training.",
        "access_level": "open",
        "format": "varies",
    },
    "fsd50k": {
        "id": "fsd50k",
        "name": "FSD50K (Freesound Dataset 50K)",
        "source_url": "https://zenodo.org/records/4060432",
        "license": "CC-BY",
        "nlm_subsystem": "nlm/pretrain/",
        "nlm_priority": "P1",
        "sensor_type": "microphone",
        "acoustic_environment": "air",
        "description": "51,197 human-verified clips across 200 AudioSet-derived classes with JSON ground truth.",
        "access_level": "open",
        "format": "WAV + JSON",
    },
    "urbansound8k": {
        "id": "urbansound8k",
        "name": "UrbanSound8K",
        "source_url": "https://urbansounddataset.weebly.com/urbansound8k.html",
        "license": "UrbanSound dataset license (academic)",
        "nlm_subsystem": "nlm/pretrain/",
        "nlm_priority": "P1",
        "sensor_type": "microphone",
        "acoustic_environment": "urban_air",
        "description": "8732 urban sound excerpts: engines, sirens, drills, dog bark, gun shot, etc.",
        "access_level": "open",
        "format": "WAV",
    },
    "watkins_whoi": {
        "id": "watkins_whoi",
        "name": "Watkins Marine Mammal Sound Database (WHOI)",
        "source_url": "https://www.whoi.edu/project/watkins/",
        "license": "See WHOI / publication terms",
        "nlm_subsystem": "nlm/data/marine_mammal/",
        "nlm_priority": "P0",
        "sensor_type": "hydrophone",
        "acoustic_environment": "underwater",
        "description": "Classic marine mammal call library used for species-level bioacoustic training.",
        "access_level": "academic",
        "format": "WAV",
    },
    "glacier_bay": {
        "id": "glacier_bay",
        "name": "Glacier Bay Underwater Sound",
        "source_url": "https://www.nps.gov/glba/learn/nature/soundscape.htm",
        "license": "NPS / see dataset terms",
        "nlm_subsystem": "nlm/data/marine_mammal/",
        "nlm_priority": "P1",
        "sensor_type": "hydrophone",
        "acoustic_environment": "underwater",
        "description": "Labelled multi-class underwater recordings from Glacier Bay National Park.",
        "access_level": "open",
        "format": "WAV",
    },
    "noaa_nrs": {
        "id": "noaa_nrs",
        "name": "NOAA NRS Passive Acoustic Monitoring",
        "source_url": "https://www.ncei.noaa.gov/maps/passive-acoustic-data/",
        "license": "Public domain (US Government)",
        "nlm_subsystem": "nlm/data/ambient/",
        "nlm_priority": "P0",
        "sensor_type": "hydrophone",
        "acoustic_environment": "underwater",
        "description": "NOAA Ocean Noise Reference Station network continuous hydrophone archives.",
        "access_level": "open",
        "format": "WAV / HDF5",
    },
    "sanctsound": {
        "id": "sanctsound",
        "name": "SanctSound (NOAA NCEI)",
        "source_url": "https://www.ncei.noaa.gov/products/passive-acoustic-data-sanctsound",
        "license": "Public domain (US Government)",
        "nlm_subsystem": "nlm/data/ambient/",
        "nlm_priority": "P0",
        "sensor_type": "hydrophone",
        "acoustic_environment": "underwater",
        "description": "National marine sanctuary passive acoustic monitoring — vessels, biology, weather noise.",
        "access_level": "open",
        "format": "WAV",
    },
}


def get_source(source_id: str) -> dict[str, Any]:
    return SOURCE_REGISTRY.get(source_id, {
        "id": source_id,
        "name": source_id,
        "source_url": "",
        "license": "unknown",
        "nlm_subsystem": "nlm/data/",
        "nlm_priority": "P2",
        "sensor_type": "microphone",
        "acoustic_environment": "unknown",
        "description": f"NLM library source {source_id}",
        "access_level": "unknown",
        "format": "audio",
    })


def upsert_sources(conn) -> None:
    """Seed library.source table from registry."""
    with conn.cursor() as cur:
        for sid, row in SOURCE_REGISTRY.items():
            cur.execute(
                """
                INSERT INTO library.source (
                    id, name, category, source_url, license, nlm_subsystem,
                    nlm_priority, sensor_type, acoustic_environment, description,
                    access_level, format, metadata
                ) VALUES (
                    %s, %s, 'acoustic', %s, %s, %s,
                    %s, %s, %s, %s,
                    %s, %s, '{}'::jsonb
                )
                ON CONFLICT (id) DO UPDATE SET
                    name = EXCLUDED.name,
                    source_url = EXCLUDED.source_url,
                    license = EXCLUDED.license,
                    nlm_subsystem = EXCLUDED.nlm_subsystem,
                    nlm_priority = EXCLUDED.nlm_priority,
                    sensor_type = EXCLUDED.sensor_type,
                    acoustic_environment = EXCLUDED.acoustic_environment,
                    description = EXCLUDED.description,
                    updated_at = NOW()
                """,
                (
                    sid,
                    row["name"],
                    row.get("source_url"),
                    row.get("license"),
                    row.get("nlm_subsystem"),
                    row.get("nlm_priority"),
                    row.get("sensor_type"),
                    row.get("acoustic_environment"),
                    row.get("description"),
                    row.get("access_level"),
                    row.get("format"),
                ),
            )
