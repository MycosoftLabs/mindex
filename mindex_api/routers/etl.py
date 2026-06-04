import asyncio
import json
import os
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncIterator, Dict, List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..db import async_session_scope
from ..dependencies import get_db_session
from ..storage import get_storage
from ..utils.deep_agent_events import schedule_domain_event

router = APIRouter(tags=["ETL & Sync"])

# In-process ETL run tracking (API host)
_active_runs: Dict[str, Dict[str, Any]] = {}

SOURCE_TO_JOB: Dict[str, str] = {
    "iNaturalist": "inat_taxa",
    "inat": "inat_taxa",
    "GBIF": "gbif",
    "MycoBank": "mycobank",
    "GenBank": "genetics",
    "FungiDB": "fungidb",
    "PubChem": "pubchem",
    "ChemSpider": "chemspider",
}


def _discover_etl_job_modules() -> list[str]:
    """List `mindex_etl.jobs.*` modules from the repo (filesystem scan)."""
    root = Path(__file__).resolve().parent.parent.parent / "mindex_etl" / "jobs"
    if not root.is_dir():
        return []
    out: list[str] = []
    for p in sorted(root.glob("*.py")):
        if p.name.startswith("_"):
            continue
        out.append(f"mindex_etl.jobs.{p.stem}")
    return out


def _docker_etl_running() -> bool:
    try:
        result = subprocess.run(
            [
                "docker",
                "ps",
                "--filter",
                "name=mindex",
                "--format",
                "{{.Names}} {{.Status}}",
            ],
            capture_output=True,
            text=True,
            timeout=3,
            env=os.environ,
        )
        if result.returncode != 0:
            return False
        for line in result.stdout.splitlines():
            low = line.lower()
            if "etl" in low or "sync" in low or "scheduler" in low:
                if "up" in low or "running" in low:
                    return True
        return False
    except Exception:
        return False


def _run_etl_subprocess(
    jobs: Optional[List[str]] = None,
    *,
    full_sync: bool = False,
    max_pages: Optional[int] = None,
    domain_mode: Optional[str] = None,
    run_id: Optional[str] = None,
) -> None:
    """Run master ETL in a detached subprocess (blocking worker for BackgroundTasks)."""
    rid = run_id or str(uuid.uuid4())
    cmd = [sys.executable, "-m", "mindex_etl.jobs.run_all"]
    if full_sync:
        cmd.append("--full")
    else:
        cmd.append("--incremental")
    if jobs:
        cmd.extend(["--jobs", *jobs])
    if max_pages is not None:
        cmd.extend(["--max-pages", str(max_pages)])
    if domain_mode:
        cmd.extend(["--domain-mode", domain_mode])

    _active_runs[rid] = {
        "run_id": rid,
        "status": "running",
        "jobs": jobs or ["all"],
        "started_at": datetime.now(timezone.utc).isoformat(),
        "command": " ".join(cmd),
    }
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=60 * 60 * 6,
            env=os.environ,
        )
        _active_runs[rid].update(
            {
                "status": "completed" if proc.returncode == 0 else "failed",
                "exit_code": proc.returncode,
                "completed_at": datetime.now(timezone.utc).isoformat(),
                "stdout_tail": (proc.stdout or "")[-4000:],
                "stderr_tail": (proc.stderr or "")[-2000:],
            }
        )
    except subprocess.TimeoutExpired:
        _active_runs[rid].update(
            {
                "status": "timeout",
                "completed_at": datetime.now(timezone.utc).isoformat(),
            }
        )
    except Exception as exc:
        _active_runs[rid].update(
            {
                "status": "failed",
                "error": str(exc)[:500],
                "completed_at": datetime.now(timezone.utc).isoformat(),
            }
        )


class SyncRequest(BaseModel):
    sources: Optional[List[str]] = ["iNaturalist", "GBIF", "MycoBank", "GenBank"]
    limit: Optional[int] = 1000
    full_sync: bool = False


class EtlRunRequest(BaseModel):
    job: str = Field(..., description="Registry job name, e.g. inat_taxa, gbif, genetics")
    full_sync: bool = False
    max_pages: Optional[int] = Field(default=None, ge=1, le=5000)
    domain_mode: Optional[str] = None


@router.get("/etl/sources")
async def list_etl_sources():
    """Registered ETL job entrypoints (repo scan; empty if jobs dir missing in deployment image)."""
    return {
        "items": _discover_etl_job_modules(),
        "scanned_path": str(
            Path(__file__).resolve().parent.parent.parent / "mindex_etl" / "jobs"
        ),
    }


@router.post("/sync")
async def trigger_sync(request: SyncRequest, background_tasks: BackgroundTasks):
    """Queue real ETL jobs for the requested sources (runs `mindex_etl.jobs.run_all`)."""
    from mindex_etl.jobs.run_all import create_job_registry

    registry = create_job_registry()
    jobs: List[str] = []
    for src in request.sources or []:
        key = SOURCE_TO_JOB.get(src) or src.strip()
        if key in registry:
            jobs.append(key)
        elif key.replace("-", "_") in registry:
            jobs.append(key.replace("-", "_"))

    if not jobs:
        raise HTTPException(
            status_code=400,
            detail={
                "message": "No matching ETL jobs for sources",
                "sources": request.sources,
                "known_jobs": sorted(registry.keys()),
            },
        )

    max_pages = None
    if request.limit and request.limit < 50000:
        max_pages = max(1, min(500, request.limit // 100 or 10))

    run_id = str(uuid.uuid4())
    background_tasks.add_task(
        _run_etl_subprocess,
        jobs,
        full_sync=request.full_sync,
        max_pages=max_pages,
        run_id=run_id,
    )

    response = {
        "success": True,
        "message": "ETL jobs queued on MINDEX host",
        "job_id": run_id,
        "jobs_queued": jobs,
        "sources_queued": request.sources,
        "limit": request.limit,
        "full_sync": request.full_sync,
    }
    schedule_domain_event(
        domain="search",
        task="MINDEX ETL sync triggered",
        context={"route": "/sync", **response},
        preferred_agent="myca-research",
    )
    return response


@router.post("/etl/run")
async def run_etl_job(request: EtlRunRequest, background_tasks: BackgroundTasks):
    """Run a single registered ETL job in the background."""
    from mindex_etl.jobs.run_all import create_job_registry

    registry = create_job_registry()
    job_key = request.job.strip()
    if job_key not in registry:
        raise HTTPException(
            status_code=404,
            detail={
                "message": f"Unknown job: {job_key}",
                "known_jobs": sorted(registry.keys()),
            },
        )
    if request.domain_mode and request.domain_mode not in ("all", "fungi"):
        raise HTTPException(status_code=400, detail="domain_mode must be 'all' or 'fungi'")

    run_id = str(uuid.uuid4())
    background_tasks.add_task(
        _run_etl_subprocess,
        [job_key],
        full_sync=request.full_sync,
        max_pages=request.max_pages,
        domain_mode=request.domain_mode,
        run_id=run_id,
    )
    job = registry[job_key]
    return {
        "success": True,
        "run_id": run_id,
        "job": job_key,
        "source": job.source,
        "description": job.description,
        "status": "queued",
    }


@router.get("/etl/runs")
async def list_etl_runs():
    """Recent in-process ETL runs started via /sync or /etl/run on this API instance."""
    return {"runs": list(_active_runs.values())[-20:]}


async def _scalar_count(db: AsyncSession, sql: str) -> int:
    try:
        return int((await db.execute(text(sql))).scalar() or 0)
    except Exception:
        await db.rollback()
        return 0


async def _taxonomy_diagnostics(db: AsyncSession) -> dict[str, Any]:
    """All-life taxonomy health for Encyclopedia / console (Request 002)."""
    taxon_full_ok = False
    try:
        await db.execute(text("SELECT 1 FROM bio.taxon_full LIMIT 1"))
        taxon_full_ok = True
    except Exception:
        await db.rollback()

    return {
        "bio_taxon_full_view": taxon_full_ok,
        "list_source": "bio.taxon_full" if taxon_full_ok else "core.taxon_fallback",
        "taxa_with_kingdom": await _scalar_count(
            db, "SELECT COUNT(*) FROM core.taxon WHERE kingdom IS NOT NULL"
        ),
        "observations_with_taxon_id": await _scalar_count(
            db,
            "SELECT COUNT(*) FROM obs.observation WHERE taxon_id IS NOT NULL",
        ),
        "taxa_with_default_photo": await _scalar_count(
            db,
            "SELECT COUNT(*) FROM core.taxon "
            "WHERE metadata->>'default_photo' IS NOT NULL "
            "OR metadata->>'image_url' IS NOT NULL",
        ),
    }


async def _build_full_etl_status_payload(db: AsyncSession) -> dict[str, Any]:
    """Full pipeline status: biodiversity registry + maritime taco tables."""
    from mindex_etl.jobs.run_all import create_job_registry
    from mindex_etl.scheduler import ETLScheduler

    registry = create_job_registry()
    scheduler = ETLScheduler()

    jobs_payload = []
    for name, job in sorted(registry.items(), key=lambda x: x[1].priority):
        interval = scheduler.schedule.get(name, 24)
        jobs_payload.append(
            {
                "name": name,
                "source": job.source,
                "description": job.description,
                "priority": job.priority,
                "interval_hours": interval,
            }
        )

    core_counts = {
        "taxon": await _scalar_count(db, "SELECT COUNT(*) FROM core.taxon"),
        "observation": await _scalar_count(db, "SELECT COUNT(*) FROM obs.observation"),
        "taxon_external_id": await _scalar_count(
            db, "SELECT COUNT(*) FROM core.taxon_external_id"
        ),
        "genome": await _scalar_count(db, "SELECT COUNT(*) FROM bio.genome"),
        "genetic_sequence": await _scalar_count(
            db, "SELECT COUNT(*) FROM bio.genetic_sequence"
        ),
        "taxon_compound": await _scalar_count(
            db, "SELECT COUNT(*) FROM bio.taxon_compound"
        ),
    }

    taco: dict[str, Any] = {}
    taco_queries = [
        ("taco_observations", "taco_observations", "observed_at"),
        ("ocean_environments", "ocean_environments", "observed_at"),
        ("taco_assessments", "taco_assessments", "assessed_at"),
    ]
    for table, key, ts_col in taco_queries:
        try:
            row = (
                await db.execute(
                    text(
                        f"SELECT COUNT(*)::int AS count, MAX({ts_col}) AS last_seen "
                        f"FROM {table}"
                    )
                )
            ).mappings().first()
            taco[key] = {
                "count": row["count"] if row else 0,
                "last_seen": row["last_seen"] if row else None,
            }
        except Exception:
            await db.rollback()
            taco[key] = {"count": 0, "last_seen": None, "missing_table": True}

    docker_running = _docker_etl_running()
    active = [r for r in _active_runs.values() if r.get("status") == "running"]
    etl_state = "running" if docker_running or active else "idle"
    if core_counts["taxon"] == 0 and core_counts["observation"] == 0 and not docker_running:
        etl_state = "idle"

    return {
        "pipeline": "mindex-master-etl",
        "status": etl_state,
        "docker_etl_containers": docker_running,
        "active_runs": active,
        "jobs": jobs_payload,
        "job_count": len(jobs_payload),
        "scheduler_note": "Long-running schedule: `python -m mindex_etl.scheduler` on MINDEX VM / ETL container",
        "core_counts": core_counts,
        "taxonomy_diagnostics": await _taxonomy_diagnostics(db),
        "maritime": taco,
        "available_sources": sorted({j["source"] for j in jobs_payload}),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


async def _build_etl_status_payload(db: AsyncSession) -> dict[str, Any]:
    """Backward-compatible alias: full ETL status (replaces taco-only payload)."""
    return await _build_full_etl_status_payload(db)


@router.get("/etl-status")
async def get_etl_status(db: AsyncSession = Depends(get_db_session)):
    """Current ETL pipeline status (registry jobs, DB counts, active runs)."""
    schedule_domain_event(
        domain="search",
        task="MINDEX ETL status requested",
        context={"route": "/etl-status"},
        preferred_agent="myca-research",
    )
    return await _build_full_etl_status_payload(db)


@router.get("/console")
async def mindex_console(db: AsyncSession = Depends(get_db_session)):
    """
    Admin console payload for NatureOS MINDEX app: stats, earth domains, NAS, images, ETL jobs.
    """
    from .earth import earth_stats, infrastructure_status

    storage = get_storage()
    etl_payload = await _build_full_etl_status_payload(db)

    # Database stats (same queries as stats router)
    stats: Dict[str, Any] = {
        "total_taxa": await _scalar_count(db, "SELECT COUNT(*) FROM core.taxon"),
        "total_observations": await _scalar_count(db, "SELECT COUNT(*) FROM obs.observation"),
        "total_external_ids": await _scalar_count(
            db, "SELECT COUNT(*) FROM core.taxon_external_id"
        ),
        "observations_with_location": 0,
        "observations_with_images": 0,
        "genome_records": await _scalar_count(db, "SELECT COUNT(*) FROM bio.genome"),
        "trait_records": 0,
        "synonym_records": await _scalar_count(
            db, "SELECT COUNT(*) FROM core.taxon_synonym"
        ),
        "etl_status": etl_payload.get("status", "unknown"),
    }
    try:
        stats["observations_with_location"] = await _scalar_count(
            db,
            "SELECT COUNT(*) FROM obs.observation WHERE location IS NOT NULL",
        )
    except Exception:
        await db.rollback()
        stats["observations_with_location"] = await _scalar_count(
            db,
            "SELECT COUNT(*) FROM obs.observation "
            "WHERE latitude IS NOT NULL AND longitude IS NOT NULL",
        )
    stats["observations_with_images"] = await _scalar_count(
        db,
        "SELECT COUNT(*) FROM obs.observation "
        "WHERE media IS NOT NULL AND media::text != '[]'",
    )
    try:
        stats["trait_records"] = await _scalar_count(
            db, "SELECT COUNT(*) FROM bio.taxon_trait"
        )
    except Exception:
        await db.rollback()

    taxa_by_source: Dict[str, int] = {}
    try:
        rows = await db.execute(
            text("SELECT source, COUNT(*) FROM core.taxon GROUP BY source ORDER BY 2 DESC")
        )
        taxa_by_source = {str(r[0]): int(r[1]) for r in rows.fetchall()}
    except Exception:
        await db.rollback()
    stats["taxa_by_source"] = taxa_by_source

    obs_by_source: Dict[str, int] = {}
    try:
        rows = await db.execute(
            text("SELECT source, COUNT(*) FROM obs.observation GROUP BY source ORDER BY 2 DESC")
        )
        obs_by_source = {str(r[0]): int(r[1]) for r in rows.fetchall()}
    except Exception:
        await db.rollback()
    stats["observations_by_source"] = obs_by_source

    image_stats: Dict[str, Any] = {}
    try:
        total_taxa = stats["total_taxa"]
        with_img = await _scalar_count(
            db,
            "SELECT COUNT(*) FROM core.taxon "
            "WHERE metadata->>'default_photo' IS NOT NULL "
            "OR metadata->>'image_url' IS NOT NULL",
        )
        image_stats = {
            "total_taxa": total_taxa,
            "taxa_with_images": with_img,
            "taxa_without_images": max(0, total_taxa - with_img),
            "coverage_percent": round((with_img / total_taxa * 100), 2)
            if total_taxa
            else 0.0,
        }
    except Exception:
        await db.rollback()

    earth = await earth_stats(session=db)
    infrastructure = await infrastructure_status(session=db)

    return {
        "service": "mindex",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "data_source": "mindex",
        "stats": stats,
        "etl": etl_payload,
        "earth": {
            "domains": earth.domains,
            "total_entities": earth.total_entities,
        },
        "storage": {
            "nas_mount_path": settings.nas_mount_path,
            "nas_host": os.environ.get("NAS_HOST", "192.168.0.105"),
            "local_staging_path": settings.local_staging_path,
            "nas": storage.nas_usage(),
            "nas_writable": storage.nas_available(),
        },
        "infrastructure": infrastructure,
        "images": image_stats,
        "taxonomy_diagnostics": await _taxonomy_diagnostics(db),
        "etl_modules": _discover_etl_job_modules(),
        "recent_runs": list(_active_runs.values())[-10:],
    }


async def _pipeline_stream() -> AsyncIterator[bytes]:
    """SSE: periodic full pipeline snapshot."""
    while True:
        try:
            async with async_session_scope() as session:
                payload = await _build_full_etl_status_payload(session)
            payload["stream"] = "mindex.pipeline"
            yield f"data: {json.dumps(payload, default=str)}\n\n".encode("utf-8")
        except Exception as exc:
            yield f"data: {json.dumps({'error': str(exc)[:200]})}\n\n".encode("utf-8")
        await asyncio.sleep(8)


@router.get("/pipeline/stream")
async def pipeline_stream():
    return StreamingResponse(_pipeline_stream(), media_type="text/event-stream")
