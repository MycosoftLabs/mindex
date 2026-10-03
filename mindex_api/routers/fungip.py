"""Collection reads only. Register with the existing API prefix in Cursor integration."""
from uuid import UUID
from datetime import datetime, timezone
import re
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import PlainTextResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from ..dependencies import get_db_session, require_api_key
from mindex_etl.fungip.detail import detail, fasta
from mindex_etl.fungip.first40 import FIRST40_IDS
from mindex_etl.fungip.first40_detail import public_first40_launch

router = APIRouter(prefix="/fungip", tags=["fungip"], dependencies=[Depends(require_api_key)])


@router.get("/species")
async def collection(confirmed_only: bool = False, limit: int = Query(300, ge=1, le=300),
                     db: AsyncSession = Depends(get_db_session)):
    rows = (await db.execute(text("""SELECT s.species_id,s.taxon_id,s.accepted_name,
      s.resolution_status,s.missing_data_flags,s.validation_errors,
      EXISTS (SELECT 1 FROM fungip.token_attempt t WHERE t.species_id=s.species_id AND t.status='confirmed' AND t.network='solana-mainnet-beta') AS token_confirmed,
      v.canonical_url, v.record_sha256 AS verified_sha,s.record_sha256,v.evidence,
      s.record->>'group' AS fungal_group,s.record->>'ticker' AS ticker,s.record->>'common_name' AS common_name,
      CASE WHEN s.image_valid THEN s.record->'image' ELSE NULL END AS image,s.record->'dna'->>'accession_version' AS accession_version,
      s.sequence_valid AS reference_dna_available,
      (s.sequence_valid AND s.verified_its_sequence IS NOT NULL) AS complete_its_available
      FROM fungip.species s LEFT JOIN fungip.page_verification v ON v.species_id=s.species_id AND v.taxon_id=s.taxon_id
      WHERE (:confirmed_only = false OR EXISTS(SELECT 1 FROM fungip.token_attempt t WHERE t.species_id=s.species_id AND t.status='confirmed' AND t.network='solana-mainnet-beta'))
      ORDER BY s.species_id LIMIT :limit"""), {"confirmed_only":confirmed_only,"limit":limit})).mappings().all()
    result = []
    for row in rows:
        item = dict(row)
        item["mindex_uuid"] = str(item.pop("taxon_id")) if item["taxon_id"] else None
        evidence = item.pop("evidence") or {}
        if item.pop("verified_sha") != item["record_sha256"] or not all(evidence.get(k) is True for k in
            ("name_checked","taxonomy_checked","dna_checked","download_checked","attribution_checked")):
            item["canonical_url"] = None
        item["collection"] = "FungiP 300"
        if any("image" in str(error) for error in item["validation_errors"]):
            item["image"] = None
        item["feature_label"] = "Confirmed token receipt; biological claims not chain-validated" if item["token_confirmed"] else "Research collection member"
        result.append(item)
    return {"collection":"FungiP 300","data":result,"returned":len(result)}


async def load_detail(db: AsyncSession, field: str, identifier):
    # Field is selected by the two authored handlers, never request text.
    if field not in {"taxon_id", "species_id"}:
        raise ValueError("Invalid lookup field")
    row = (await db.execute(text(f"SELECT * FROM fungip.species WHERE {field}=:id"), {"id":identifier})).mappings().first()
    if row is None:
        raise HTTPException(404, "No FungiP collection record")
    verification = (await db.execute(text("SELECT * FROM fungip.page_verification WHERE species_id=:id"),
                                   {"id":row["species_id"]})).mappings().first()
    tokens = (await db.execute(text("""SELECT network,mint_address,transaction_signature,transaction_url,
      metadata_url,usepaid_url,status
      FROM fungip.token_attempt WHERE species_id=:id ORDER BY created_at"""), {"id":row["species_id"]})).mappings().all()
    parent = dict(row)
    view = detail(parent, dict(verification) if verification else None, [dict(t) for t in tokens])
    launch = None
    if row["species_id"] in FIRST40_IDS:
        stored = (await db.execute(text("""SELECT a.*, b.source_reported_as_of_pt,
          b.source_reported_as_of_utc,b.correction_recorded_at_utc
          FROM fungip.first40_launch_association a
          JOIN fungip.first40_source_batch b ON b.snapshot_sha256=a.snapshot_sha256
          WHERE a.species_id=:id"""), {"id":row["species_id"]})).mappings().first()
        if stored:
            launch = dict(stored)
            for key in ("launched_at","source_reported_as_of_utc","correction_recorded_at_utc"):
                value = launch[key]
                if isinstance(value,datetime):
                    if value.tzinfo is None: raise ValueError("Naive first40 database timestamp")
                    launch[key] = value.astimezone(timezone.utc).isoformat().replace("+00:00","Z")
    try:
        view["first40_launch"] = public_first40_launch(launch,parent)
    except ValueError:
        # Recuration can invalidate a formerly matching association. Keep the
        # original research detail available while withholding that association.
        view["first40_launch"] = None
        view["missing_data_flags"] = sorted(set(view["missing_data_flags"]) | {"first40_source_association_review_required"})
    return view


@router.get("/species/{species_id}")
async def collection_species(species_id: str, db: AsyncSession = Depends(get_db_session)):
    if not re.fullmatch(r"FG\d{3}",species_id):
        raise HTTPException(400,"Invalid permanent collection species ID")
    return await load_detail(db,"species_id",species_id)


@router.get("/taxa/{taxon_id}")
async def species(taxon_id: UUID, db: AsyncSession = Depends(get_db_session)):
    return await load_detail(db,"taxon_id",taxon_id)


@router.get("/taxa/{taxon_id}/sequence")
async def sequence(taxon_id: UUID, kind: str = Query("reference", pattern="^(reference|its)$"),
                   db: AsyncSession = Depends(get_db_session)):
    view = await species(taxon_id, db)
    try:
        content = fasta(view, kind)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    return PlainTextResponse(content, media_type="text/plain",
                             headers={"Content-Disposition":f'attachment; filename="{view["species_id"]}_{kind}.fasta"'})
