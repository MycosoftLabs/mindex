from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..dependencies import get_db_session
from ..storage import get_storage
from ..utils.fusarium_training_doc_parser import parse_training_sources

router = APIRouter(prefix="/fusarium/catalog", tags=["fusarium-catalog"])


FUSARIUM_SILOS: List[Dict[str, Any]] = [
    {"silo_id": "underwater_pam", "name": "Underwater PAM", "description": "Continuous passive acoustic monitoring and ambient ocean sound baselines"},
    {"silo_id": "vessel_uatr", "name": "Vessel UATR", "description": "Underwater acoustic target recognition for vessels and submarines"},
    {"silo_id": "marine_bio", "name": "Marine Bio", "description": "Marine mammal and biological sound classification datasets"},
    {"silo_id": "aerial_bio_uav", "name": "Aerial Bio/UAV", "description": "Bird and drone audio discrimination datasets"},
    {"silo_id": "threat_munitions", "name": "Threat Munitions", "description": "Explosion, UXO, and military threat acoustics"},
    {"silo_id": "env_transfer_audio", "name": "Environmental Transfer Audio", "description": "General environmental audio corpora and audio transfer-learning sets"},
    {"silo_id": "oceanographic_grid", "name": "Oceanographic Grid", "description": "Temperature, salinity, sound speed, currents, and sea-state grids"},
    {"silo_id": "bathymetry", "name": "Bathymetry", "description": "Seafloor and terrain grids for underwater propagation context"},
    {"silo_id": "magnetic", "name": "Magnetic", "description": "World magnetic model, anomaly grids, and MAD references"},
    {"silo_id": "ais_maritime", "name": "AIS Maritime", "description": "AIS and maritime traffic ground truth and trajectory data"},
    {"silo_id": "sonar_imagery", "name": "Sonar Imagery", "description": "Forward-looking sonar images and detection corpora"},
    {"silo_id": "gas_chemistry", "name": "Gas Chemistry", "description": "Gas/VOC-related training corpora and fingerprints"},
    {"silo_id": "electromagnetics", "name": "Electromagnetics", "description": "Electromagnetic and field-based fingerprints"},
    {"silo_id": "vibration_touch", "name": "Vibration & Touch", "description": "Vibration, haptics, and mechanical signal datasets"},
    {"silo_id": "bioelectric_fci", "name": "Bioelectric FCI", "description": "Fungal bioelectric and FCI-aligned sensing sets"},
    {"silo_id": "model_registry", "name": "Model Registry", "description": "Pretrained model weights, checkpoints, and model references"},
]

FUSARIUM_ENVIRONMENTS: List[Dict[str, Any]] = [
    {"env_id": "land", "axis_type": "classical_domain", "name": "land", "description": "Classical terrestrial domain", "parent_id": None},
    {"env_id": "sea", "axis_type": "classical_domain", "name": "sea", "description": "Classical maritime domain", "parent_id": None},
    {"env_id": "air", "axis_type": "classical_domain", "name": "air", "description": "Classical air domain", "parent_id": None},
    {"env_id": "space", "axis_type": "classical_domain", "name": "space", "description": "Classical space domain", "parent_id": None},
    {"env_id": "cyber", "axis_type": "classical_domain", "name": "cyber", "description": "Classical cyber domain", "parent_id": None},
    {"env_id": "above_canopy", "axis_type": "vertical_layer", "name": "above_canopy", "description": "Above canopy layer", "parent_id": None},
    {"env_id": "below_canopy", "axis_type": "vertical_layer", "name": "below_canopy", "description": "Below canopy layer", "parent_id": None},
    {"env_id": "above_ground", "axis_type": "vertical_layer", "name": "above_ground", "description": "Above ground layer", "parent_id": None},
    {"env_id": "below_ground", "axis_type": "vertical_layer", "name": "below_ground", "description": "Below ground layer", "parent_id": None},
    {"env_id": "subsurface_water", "axis_type": "vertical_layer", "name": "subsurface_water", "description": "Below-surface water column", "parent_id": None},
    {"env_id": "seafloor", "axis_type": "vertical_layer", "name": "seafloor", "description": "Seafloor or bottom interaction layer", "parent_id": None},
    {"env_id": "cave", "axis_type": "vertical_layer", "name": "cave", "description": "Cave and subterranean chamber environments", "parent_id": "below_ground"},
    {"env_id": "jungle", "axis_type": "environment", "name": "jungle", "description": "Jungle environment", "parent_id": "land"},
    {"env_id": "forest", "axis_type": "environment", "name": "forest", "description": "Forest environment", "parent_id": "land"},
    {"env_id": "desert", "axis_type": "environment", "name": "desert", "description": "Desert environment", "parent_id": "land"},
    {"env_id": "mountain", "axis_type": "environment", "name": "mountain", "description": "Mountain environment", "parent_id": "land"},
    {"env_id": "river", "axis_type": "environment", "name": "river", "description": "Generic river environment", "parent_id": "land"},
    {"env_id": "dry_river", "axis_type": "environment", "name": "dry_river", "description": "Dry riverbed environment", "parent_id": "river"},
    {"env_id": "wet_river", "axis_type": "environment", "name": "wet_river", "description": "Flowing river environment", "parent_id": "river"},
    {"env_id": "coast", "axis_type": "environment", "name": "coast", "description": "Coastal environment", "parent_id": "sea"},
    {"env_id": "littoral", "axis_type": "environment", "name": "littoral", "description": "Littoral interface", "parent_id": "sea"},
    {"env_id": "reef", "axis_type": "environment", "name": "reef", "description": "Reef environment", "parent_id": "sea"},
    {"env_id": "estuary", "axis_type": "environment", "name": "estuary", "description": "Estuary environment", "parent_id": "sea"},
    {"env_id": "urban", "axis_type": "environment", "name": "urban", "description": "Urban environment", "parent_id": "land"},
    {"env_id": "agricultural", "axis_type": "environment", "name": "agricultural", "description": "Agricultural environment", "parent_id": "land"},
    {"env_id": "polar", "axis_type": "environment", "name": "polar", "description": "Polar environment", "parent_id": "land"},
    {"env_id": "volcanic", "axis_type": "environment", "name": "volcanic", "description": "Volcanic environment", "parent_id": "land"},
    {"env_id": "season", "axis_type": "temporal_context", "name": "season", "description": "Seasonal label", "parent_id": None},
    {"env_id": "local_time_band", "axis_type": "temporal_context", "name": "local_time_band", "description": "Local day/night or time band", "parent_id": None},
    {"env_id": "tide_state", "axis_type": "temporal_context", "name": "tide_state", "description": "Tidal context", "parent_id": None},
    {"env_id": "event_window", "axis_type": "temporal_context", "name": "event_window", "description": "Pre/post event window context", "parent_id": None},
    {"env_id": "mission_phase", "axis_type": "temporal_context", "name": "mission_phase", "description": "Mission phase context", "parent_id": None},
]

SILO_TO_ENV_TAGS: Dict[str, List[str]] = {
    "underwater_pam": ["sea", "subsurface_water"],
    "vessel_uatr": ["sea", "subsurface_water"],
    "marine_bio": ["sea", "subsurface_water"],
    "aerial_bio_uav": ["air", "above_ground", "above_canopy"],
    "threat_munitions": ["sea", "subsurface_water", "littoral"],
    "env_transfer_audio": ["land", "sea", "air"],
    "oceanographic_grid": ["sea", "subsurface_water", "littoral"],
    "bathymetry": ["sea", "seafloor"],
    "magnetic": ["sea", "seafloor", "below_ground"],
    "ais_maritime": ["sea", "coast", "littoral"],
    "sonar_imagery": ["sea", "subsurface_water"],
    "gas_chemistry": ["land", "urban", "agricultural"],
    "electromagnetics": ["land", "sea", "air", "cyber"],
    "vibration_touch": ["land", "below_ground"],
    "bioelectric_fci": ["below_ground", "forest", "agricultural"],
    "model_registry": [],
}


class ImportTrainingSourcesRequest(BaseModel):
    doc_path: str


class DatasetManifestCreate(BaseModel):
    dataset_id: str
    split_name: str
    record_count: int
    storage_uri: str
    checksum: Optional[str] = None
    manifest_metadata: Dict[str, Any] = {}


class TrainingRunCreate(BaseModel):
    external_run_ref: Optional[str] = None
    model_name: str
    model_version: str
    status: str = "registered"
    source_mix: List[Dict[str, Any]] = []
    environment_scope: List[str] = []
    metrics: Dict[str, Any] = {}


class ModelRegistryCreate(BaseModel):
    model_name: str
    model_version: str
    role: str
    source_uri: Optional[str] = None
    source_datasets: List[str] = []
    metrics: Dict[str, Any] = {}


P0_DATASET_IDS = {
    "panns",
    "beats",
    "panns_deepship",
    "uwtrl_meg",
    "ds3500",
    "shipsear",
    "deepship",
    "watkins_whoi",
    "esc50",
    "audioset",
    "mbari_pacific_sound",
    "sanctsound",
    "noaa_nrs",
    "woa_soundspeed",
    "gebco_2025",
    "wmm2025",
    "emag2v3",
    "noaa_ais",
    "ndbc",
    "speechbrain",
    "librosa",
}


async def _seed_reference_tables(db: AsyncSession) -> None:
    for silo in FUSARIUM_SILOS:
        await db.execute(
            text(
                """
                INSERT INTO fusarium_catalog.modality_silo (silo_id, name, description, source_categories)
                VALUES (:silo_id, :name, :description, CAST(:source_categories AS jsonb))
                ON CONFLICT (silo_id) DO UPDATE SET
                    name = EXCLUDED.name,
                    description = EXCLUDED.description,
                    source_categories = EXCLUDED.source_categories
                """
            ),
            {**silo, "source_categories": json.dumps([])},
        )

    for env in FUSARIUM_ENVIRONMENTS:
        await db.execute(
            text(
                """
                INSERT INTO fusarium_env.environment_domain (env_id, axis_type, name, description, parent_id)
                VALUES (:env_id, :axis_type, :name, :description, :parent_id)
                ON CONFLICT (env_id) DO UPDATE SET
                    axis_type = EXCLUDED.axis_type,
                    name = EXCLUDED.name,
                    description = EXCLUDED.description,
                    parent_id = EXCLUDED.parent_id
                """
            ),
            env,
        )


def _infer_environment_tags(source: Dict[str, Any]) -> List[str]:
    tags = list(SILO_TO_ENV_TAGS.get(source.get("modality_silo", ""), []))
    section = str(source.get("section_category", "")).lower()
    if "arctic" in section:
        tags.append("polar")
    if "coastal" in section or "maritime" in section:
        tags.extend(["coast", "littoral"])
    return sorted({tag for tag in tags if tag})


@router.post("/import-training-sources")
async def import_training_sources(
    request: ImportTrainingSourcesRequest,
    db: AsyncSession = Depends(get_db_session),
):
    await _seed_reference_tables(db)
    sources = parse_training_sources(request.doc_path)
    for source in sources:
        env_tags = _infer_environment_tags(source)
        await db.execute(
            text(
                """
                INSERT INTO fusarium_catalog.dataset_source (
                    dataset_id, name, section_category, source_url, source_urls, dataset_type,
                    file_format, size_estimate, access_level, nlm_target, repo_targets,
                    priority, modality_silo, license, description, environment_domain_tags,
                    storage_uri, checksum, ingest_status, raw_metadata, updated_at
                ) VALUES (
                    :dataset_id, :name, :section_category, :source_url, CAST(:source_urls AS jsonb), :dataset_type,
                    :file_format, :size_estimate, :access_level, :nlm_target, CAST(:repo_targets AS jsonb),
                    :priority, :modality_silo, :license, :description, CAST(:environment_domain_tags AS jsonb),
                    :storage_uri, :checksum, :ingest_status, CAST(:raw_metadata AS jsonb), NOW()
                )
                ON CONFLICT (dataset_id) DO UPDATE SET
                    name = EXCLUDED.name,
                    section_category = EXCLUDED.section_category,
                    source_url = EXCLUDED.source_url,
                    source_urls = EXCLUDED.source_urls,
                    dataset_type = EXCLUDED.dataset_type,
                    file_format = EXCLUDED.file_format,
                    size_estimate = EXCLUDED.size_estimate,
                    access_level = EXCLUDED.access_level,
                    nlm_target = EXCLUDED.nlm_target,
                    repo_targets = EXCLUDED.repo_targets,
                    priority = EXCLUDED.priority,
                    modality_silo = EXCLUDED.modality_silo,
                    description = EXCLUDED.description,
                    environment_domain_tags = EXCLUDED.environment_domain_tags,
                    storage_uri = EXCLUDED.storage_uri,
                    checksum = EXCLUDED.checksum,
                    ingest_status = EXCLUDED.ingest_status,
                    raw_metadata = EXCLUDED.raw_metadata,
                    updated_at = NOW()
                """
            ),
            {
                "dataset_id": source.get("dataset_id"),
                "name": source.get("name"),
                "section_category": source.get("section_category"),
                "source_url": source.get("source_url"),
                "dataset_type": source.get("dataset_type"),
                "file_format": source.get("file_format"),
                "size_estimate": source.get("size_estimate"),
                "access_level": source.get("access_level"),
                "nlm_target": source.get("nlm_target"),
                "priority": source.get("priority"),
                "modality_silo": source.get("modality_silo"),
                "source_urls": json.dumps(source.get("source_urls", [])),
                "repo_targets": json.dumps(source.get("repo_targets", [])),
                "environment_domain_tags": json.dumps(env_tags),
                "raw_metadata": json.dumps(source.get("raw_metadata", {})),
                "license": source.get("raw_metadata", {}).get("license"),
                "description": source.get("dataset_type"),
                "storage_uri": source.get("storage_uri"),
                "checksum": source.get("checksum"),
                "ingest_status": source.get("ingest_status", "cataloged"),
            },
        )
        for tag in env_tags:
            await db.execute(
                text(
                    """
                    INSERT INTO fusarium_catalog.dataset_environment_tag (dataset_id, env_id)
                    VALUES (:dataset_id, :env_id)
                    ON CONFLICT (dataset_id, env_id) DO NOTHING
                    """
                ),
                {"dataset_id": source["dataset_id"], "env_id": tag},
            )
    await db.commit()
    return {"status": "imported", "count": len(sources)}


@router.get("/datasets")
async def list_datasets(
    priority: Optional[str] = None,
    modality_silo: Optional[str] = None,
    access_level: Optional[str] = None,
    limit: int = Query(default=500, ge=1, le=5000),
    db: AsyncSession = Depends(get_db_session),
):
    result = await db.execute(
        text(
            """
            SELECT dataset_id, name, section_category, source_url, dataset_type, file_format,
                   size_estimate, access_level, nlm_target, repo_targets, priority,
                   modality_silo, ingest_status, storage_uri, last_verified_at
            FROM fusarium_catalog.dataset_source
            WHERE (:priority IS NULL OR priority = :priority)
              AND (:modality_silo IS NULL OR modality_silo = :modality_silo)
              AND (:access_level IS NULL OR access_level = :access_level)
            ORDER BY priority NULLS LAST, dataset_id
            LIMIT :limit
            """
        ),
        {"priority": priority, "modality_silo": modality_silo, "access_level": access_level, "limit": limit},
    )
    rows = result.mappings().all()
    return {"datasets": [dict(row) for row in rows], "total": len(rows)}


@router.get("/datasets/p0")
async def list_p0_datasets(db: AsyncSession = Depends(get_db_session)):
    result = await db.execute(
        text(
            """
            SELECT dataset_id, name, modality_silo, ingest_status, storage_uri, access_level
            FROM fusarium_catalog.dataset_source
            WHERE priority = 'P0'
            ORDER BY dataset_id
            """
        )
    )
    rows = result.mappings().all()
    return {"datasets": [dict(row) for row in rows], "total": len(rows)}


@router.get("/datasets/{dataset_id}")
async def get_dataset(dataset_id: str, db: AsyncSession = Depends(get_db_session)):
    result = await db.execute(
        text(
            """
            SELECT *
            FROM fusarium_catalog.dataset_source
            WHERE dataset_id = :dataset_id
            LIMIT 1
            """
        ),
        {"dataset_id": dataset_id},
    )
    row = result.mappings().first()
    return {"dataset": dict(row)} if row else {"dataset_id": dataset_id, "status": "not_found"}


@router.get("/modalities")
async def list_modalities(db: AsyncSession = Depends(get_db_session)):
    result = await db.execute(text("SELECT silo_id, name, description FROM fusarium_catalog.modality_silo ORDER BY silo_id"))
    rows = result.mappings().all()
    return {"modalities": [dict(row) for row in rows], "total": len(rows)}


@router.get("/environments")
async def list_environments(db: AsyncSession = Depends(get_db_session)):
    result = await db.execute(
        text("SELECT env_id, axis_type, name, description, parent_id FROM fusarium_env.environment_domain ORDER BY axis_type, env_id")
    )
    rows = result.mappings().all()
    return {"environments": [dict(row) for row in rows], "total": len(rows)}


@router.get("/domains")
async def list_domains():
    return {
        "domains": [
            "land",
            "sea",
            "air",
            "space",
            "cyber",
            "above_canopy",
            "below_canopy",
            "above_ground",
            "below_ground",
            "subsurface_water",
            "seafloor",
            "cave",
            "jungle",
            "forest",
            "desert",
            "mountain",
            "river",
            "dry_river",
            "wet_river",
            "coast",
            "littoral",
            "reef",
            "estuary",
            "urban",
            "agricultural",
            "polar",
            "volcanic",
        ]
    }


@router.post("/training/manifests")
async def create_manifest(manifest: DatasetManifestCreate, db: AsyncSession = Depends(get_db_session)):
    result = await db.execute(
        text(
            """
            INSERT INTO fusarium_training.dataset_manifest (
                dataset_id, split_name, record_count, storage_uri, checksum, manifest_metadata
            ) VALUES (
                :dataset_id, :split_name, :record_count, :storage_uri, :checksum, CAST(:manifest_metadata AS jsonb)
            )
            RETURNING manifest_id
            """
        ),
        {**manifest.model_dump(), "manifest_metadata": json.dumps(manifest.manifest_metadata)},
    )
    await db.commit()
    return {"status": "created", "manifest_id": str(result.scalar_one())}


@router.get("/training/manifests")
async def list_manifests(dataset_id: Optional[str] = None, db: AsyncSession = Depends(get_db_session)):
    result = await db.execute(
        text(
            """
            SELECT manifest_id, dataset_id, split_name, record_count, storage_uri, checksum, manifest_metadata, created_at
            FROM fusarium_training.dataset_manifest
            WHERE (:dataset_id IS NULL OR dataset_id = :dataset_id)
            ORDER BY created_at DESC
            """
        ),
        {"dataset_id": dataset_id},
    )
    rows = result.mappings().all()
    return {"manifests": [dict(row) for row in rows], "total": len(rows)}


@router.post("/training/bootstrap-p0-manifests")
async def bootstrap_p0_manifests(db: AsyncSession = Depends(get_db_session)):
    storage = get_storage()
    result = await db.execute(
        text(
            """
            SELECT dataset_id, modality_silo
            FROM fusarium_catalog.dataset_source
            WHERE priority = 'P0'
            ORDER BY dataset_id
            """
        )
    )
    rows = result.mappings().all()
    created = []
    for row in rows:
        storage_uri = str(storage.nas_training / "fusarium" / row["modality_silo"] / row["dataset_id"])
        manifest_result = await db.execute(
            text(
                """
                INSERT INTO fusarium_training.dataset_manifest (
                    dataset_id, split_name, record_count, storage_uri, checksum, manifest_metadata
                ) VALUES (
                    :dataset_id, 'full_corpus', 0, :storage_uri, NULL, CAST(:manifest_metadata AS jsonb)
                )
                RETURNING manifest_id
                """
            ),
            {
                "dataset_id": row["dataset_id"],
                "storage_uri": storage_uri,
                "manifest_metadata": json.dumps(
                    {
                        "bootstrap": True,
                        "priority": "P0",
                        "ingest_mode": "full_bulk_expected",
                    }
                ),
            },
        )
        created.append({"dataset_id": row["dataset_id"], "manifest_id": str(manifest_result.scalar_one()), "storage_uri": storage_uri})
    await db.commit()
    return {"status": "bootstrapped", "created": created, "total": len(created)}


@router.post("/training/runs")
async def register_training_run(run: TrainingRunCreate, db: AsyncSession = Depends(get_db_session)):
    result = await db.execute(
        text(
            """
            INSERT INTO fusarium_training.training_run (
                external_run_ref, model_name, model_version, status, source_mix, environment_scope, metrics
            ) VALUES (
                :external_run_ref, :model_name, :model_version, :status, CAST(:source_mix AS jsonb), CAST(:environment_scope AS jsonb), CAST(:metrics AS jsonb)
            )
            RETURNING training_run_id
            """
        ),
        {
            **run.model_dump(),
            "source_mix": json.dumps(run.source_mix),
            "environment_scope": json.dumps(run.environment_scope),
            "metrics": json.dumps(run.metrics),
        },
    )
    await db.commit()
    return {"status": "created", "training_run_id": str(result.scalar_one())}


@router.post("/models")
async def register_model(model: ModelRegistryCreate, db: AsyncSession = Depends(get_db_session)):
    await db.execute(
        text(
            """
            INSERT INTO fusarium_training.model_registry (
                model_name, model_version, role, source_uri, source_datasets, metrics, updated_at
            ) VALUES (
                :model_name, :model_version, :role, :source_uri, CAST(:source_datasets AS jsonb), CAST(:metrics AS jsonb), NOW()
            )
            ON CONFLICT (model_name, model_version) DO UPDATE SET
                role = EXCLUDED.role,
                source_uri = EXCLUDED.source_uri,
                source_datasets = EXCLUDED.source_datasets,
                metrics = EXCLUDED.metrics,
                updated_at = NOW()
            """
        ),
        {
            **model.model_dump(),
            "source_datasets": json.dumps(model.source_datasets),
            "metrics": json.dumps(model.metrics),
        },
    )
    await db.commit()
    return {"status": "registered", "model_name": model.model_name, "model_version": model.model_version}


@router.get("/models")
async def list_models(db: AsyncSession = Depends(get_db_session)):
    result = await db.execute(
        text(
            """
            SELECT model_name, model_version, role, source_uri, source_datasets, metrics, created_at, updated_at
            FROM fusarium_training.model_registry
            ORDER BY updated_at DESC
            """
        )
    )
    rows = result.mappings().all()
    return {"models": [dict(row) for row in rows], "total": len(rows)}


@router.get("/training/runs")
async def list_training_runs(db: AsyncSession = Depends(get_db_session)):
    result = await db.execute(
        text(
            """
            SELECT training_run_id, external_run_ref, model_name, model_version, status,
                   source_mix, environment_scope, metrics, created_at, updated_at
            FROM fusarium_training.training_run
            ORDER BY updated_at DESC
            """
        )
    )
    rows = result.mappings().all()
    return {"training_runs": [dict(row) for row in rows], "total": len(rows)}


@router.get("/storage")
async def storage_summary():
    storage = get_storage()
    return {
        "nas_available": storage.nas_available(),
        "nas_usage": storage.nas_usage(),
        "fusarium_paths": {
            "registry": str(storage.nas_training / "fusarium" / "registry"),
            "underwater_pam": str(storage.nas_training / "fusarium" / "underwater_pam"),
            "vessel_uatr": str(storage.nas_training / "fusarium" / "vessel_uatr"),
            "model_registry": str(storage.nas_training / "fusarium" / "model_registry"),
        },
    }


@router.get("/readiness")
async def readiness_summary(db: AsyncSession = Depends(get_db_session)):
    dataset_result = await db.execute(text("SELECT COUNT(*)::int AS count FROM fusarium_catalog.dataset_source"))
    manifest_result = await db.execute(text("SELECT COUNT(*)::int AS count FROM fusarium_training.dataset_manifest"))
    model_result = await db.execute(text("SELECT COUNT(*)::int AS count FROM fusarium_training.model_registry"))
    run_result = await db.execute(text("SELECT COUNT(*)::int AS count FROM fusarium_training.training_run"))
    p0_result = await db.execute(
        text(
            """
            SELECT COUNT(*)::int AS count,
                   COUNT(*) FILTER (WHERE storage_uri IS NOT NULL)::int AS with_storage,
                   COUNT(*) FILTER (WHERE ingest_status IN ('cataloged', 'imported', 'ready', 'ingesting'))::int AS tracked
            FROM fusarium_catalog.dataset_source
            WHERE priority = 'P0'
            """
        )
    )
    p0_row = p0_result.mappings().first() or {"count": 0, "with_storage": 0, "tracked": 0}
    return {
        "dataset_count": dataset_result.scalar() or 0,
        "manifest_count": manifest_result.scalar() or 0,
        "model_count": model_result.scalar() or 0,
        "training_run_count": run_result.scalar() or 0,
        "p0_total": p0_row["count"],
        "p0_with_storage_uri": p0_row["with_storage"],
        "p0_tracked": p0_row["tracked"],
    }
