from __future__ import annotations

from typing import Any, Dict, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..dependencies import (
    PaginationParams,
    get_db_session,
    pagination_params,
    require_api_key,
)
from ..ledger import (
    hash_dataset,
    record_bitcoin_ordinal,
    record_hypergraph_anchor,
    record_solana_binding,
)
from ..contracts.v1.ip_assets import (
    BitcoinOrdinal,
    HypergraphAnchor,
    HypergraphAnchorRequest,
    IPAsset,
    IPAssetListResponse,
    OrdinalAnchorRequest,
    SolanaBinding,
    SolanaBindingRequest,
)

router = APIRouter(
    prefix="/ip/assets",
    tags=["ip-assets"],
    dependencies=[Depends(require_api_key)],
)

ASSET_QUERY_TEMPLATE = """
SELECT
    ia.id,
    ia.name,
    ia.description,
    ia.taxon_id,
    ia.created_by,
    encode(ia.content_hash, 'hex') AS content_hash,
    ia.content_uri,
    ia.metadata,
    ia.created_at,
    ia.updated_at,
    COALESCE(h.hypergraph_anchors, '[]'::jsonb) AS hypergraph_anchors,
    COALESCE(b.bitcoin_ordinals, '[]'::jsonb) AS bitcoin_ordinals,
    COALESCE(s.solana_bindings, '[]'::jsonb) AS solana_bindings
FROM ip.ip_asset ia
LEFT JOIN LATERAL (
    SELECT jsonb_agg(
        jsonb_build_object(
            'id', ha.id,
            'sample_id', ha.sample_id,
            'anchor_hash', encode(ha.anchor_hash, 'hex'),
            'metadata', ha.metadata,
            'anchored_at', ha.anchored_at
        )
        ORDER BY ha.anchored_at DESC
    ) AS hypergraph_anchors
    FROM ledger.hypergraph_anchor ha
    WHERE ha.ip_asset_id = ia.id
) h ON TRUE
LEFT JOIN LATERAL (
    SELECT jsonb_agg(
        jsonb_build_object(
            'id', bo.id,
            'content_hash', encode(bo.content_hash, 'hex'),
            'inscription_id', bo.inscription_id,
            'inscription_address', bo.inscription_address,
            'metadata', bo.metadata,
            'inscribed_at', bo.inscribed_at
        )
        ORDER BY bo.inscribed_at DESC
    ) AS bitcoin_ordinals
    FROM ledger.bitcoin_ordinal bo
    WHERE bo.ip_asset_id = ia.id
) b ON TRUE
LEFT JOIN LATERAL (
    SELECT jsonb_agg(
        jsonb_build_object(
            'id', sb.id,
            'mint_address', sb.mint_address,
            'token_account', sb.token_account,
            'metadata', sb.metadata,
            'bound_at', sb.bound_at
        )
        ORDER BY sb.bound_at DESC
    ) AS solana_bindings
    FROM ledger.solana_binding sb
    WHERE sb.ip_asset_id = ia.id
) s ON TRUE
{where_clause}
{order_clause}
{limit_clause}
"""


def _build_asset_query(
    *,
    where_clause: Optional[str] = None,
    order_clause: Optional[str] = None,
    limit_clause: Optional[str] = None,
) -> str:
    return ASSET_QUERY_TEMPLATE.format(
        where_clause=f"WHERE {where_clause}" if where_clause else "",
        order_clause=order_clause or "",
        limit_clause=limit_clause or "",
    )


def _serialize_asset(row: Dict[str, Any]) -> Dict[str, Any]:
    data = dict(row)
    data["content_hash"] = data.get("content_hash")
    for key in ("hypergraph_anchors", "bitcoin_ordinals", "solana_bindings"):
        data[key] = data.get(key) or []
    return data


async def _ensure_asset_exists(db: AsyncSession, asset_id: UUID) -> None:
    stmt = text("SELECT 1 FROM ip.ip_asset WHERE id = :id")
    result = await db.execute(stmt, {"id": str(asset_id)})
    if result.scalar_one_or_none() is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="IP asset not found")


@router.get("", response_model=IPAssetListResponse)
async def list_ip_assets(
    pagination: PaginationParams = Depends(pagination_params),
    db: AsyncSession = Depends(get_db_session),
    q: Optional[str] = Query(None, description="Search by name or description."),
) -> IPAssetListResponse:
    where = []
    params: Dict[str, Any] = {
        "limit": pagination.limit,
        "offset": pagination.offset,
    }
    if q:
        where.append("(ia.name ILIKE :q OR ia.description ILIKE :q)")
        params["q"] = f"%{q}%"

    query = _build_asset_query(
        where_clause=" AND ".join(where) if where else None,
        order_clause="ORDER BY ia.created_at DESC",
        limit_clause="LIMIT :limit OFFSET :offset",
    )
    count_query = f"SELECT count(*) FROM ip.ip_asset ia {'WHERE ' + ' AND '.join(where) if where else ''}"

    result = await db.execute(text(query), params)
    rows = [_serialize_asset(row) for row in result.mappings().all()]
    total = (await db.execute(text(count_query), params)).scalar_one()

    assets = [IPAsset(**row) for row in rows]
    return IPAssetListResponse(
        data=assets,
        pagination={
            "limit": pagination.limit,
            "offset": pagination.offset,
            "total": total,
        },
    )


@router.get("/{ip_asset_id}", response_model=IPAsset)
async def get_ip_asset(
    ip_asset_id: UUID,
    db: AsyncSession = Depends(get_db_session),
) -> IPAsset:
    query = _build_asset_query(
        where_clause="ia.id = :asset_id",
        limit_clause="LIMIT 1",
    )
    result = await db.execute(text(query), {"asset_id": str(ip_asset_id)})
    row = result.mappings().one_or_none()
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="IP asset not found")
    return IPAsset(**_serialize_asset(row))


@router.post("/{ip_asset_id}/anchor/hypergraph", response_model=HypergraphAnchor, status_code=status.HTTP_201_CREATED)
async def anchor_hypergraph(
    ip_asset_id: UUID,
    payload: HypergraphAnchorRequest,
    db: AsyncSession = Depends(get_db_session),
) -> HypergraphAnchor:
    await _ensure_asset_exists(db, ip_asset_id)
    digest = hash_dataset(bytes(payload.payload_b64))
    record = await record_hypergraph_anchor(
        db,
        ip_asset_id=ip_asset_id,
        anchor_hash=digest,
        metadata=payload.metadata,
        sample_id=payload.sample_id,
    )
    return HypergraphAnchor(**record)


@router.post(
    "/{ip_asset_id}/anchor/ordinal",
    response_model=BitcoinOrdinal,
    status_code=status.HTTP_201_CREATED,
)
async def anchor_bitcoin_ordinal(
    ip_asset_id: UUID,
    payload: OrdinalAnchorRequest,
    db: AsyncSession = Depends(get_db_session),
) -> BitcoinOrdinal:
    await _ensure_asset_exists(db, ip_asset_id)
    record = await record_bitcoin_ordinal(
        db,
        ip_asset_id=ip_asset_id,
        payload=bytes(payload.payload_b64),
        inscription_id=payload.inscription_id,
        inscription_address=payload.inscription_address,
        metadata=payload.metadata,
    )
    return BitcoinOrdinal(**record)


@router.post(
    "/{ip_asset_id}/bind/solana",
    response_model=SolanaBinding,
    status_code=status.HTTP_201_CREATED,
)
async def bind_solana(
    ip_asset_id: UUID,
    payload: SolanaBindingRequest,
    db: AsyncSession = Depends(get_db_session),
) -> SolanaBinding:
    await _ensure_asset_exists(db, ip_asset_id)
    record = await record_solana_binding(
        db,
        ip_asset_id=ip_asset_id,
        mint_address=payload.mint_address,
        token_account=payload.token_account,
        metadata=payload.metadata,
    )
    return SolanaBinding(**record)
