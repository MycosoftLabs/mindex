from __future__ import annotations

import json
from typing import Optional

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..dependencies import (
    PaginationParams,
    get_db_session,
    pagination_params,
    require_api_key,
)
from ..schemas.telemetry import DeviceLatestSamplesResponse, DeviceListResponse

telemetry_router = APIRouter(
    prefix="/telemetry",
    tags=["telemetry"],
    dependencies=[Depends(require_api_key)],
)

devices_router = APIRouter(
    prefix="/devices",
    tags=["devices"],
    dependencies=[Depends(require_api_key)],
)


@telemetry_router.get("/devices/latest", response_model=DeviceLatestSamplesResponse)
async def get_device_latest_samples(
    pagination: PaginationParams = Depends(pagination_params),
    db: AsyncSession = Depends(get_db_session),
) -> DeviceLatestSamplesResponse:
    stmt = text(
        """
        SELECT
            device_id,
            device_name,
            device_slug,
            stream_id,
            stream_key,
            stream_unit,
            sample_id,
            recorded_at,
            value_numeric,
            value_text,
            value_json,
            value_unit,
            sample_metadata,
            ST_AsGeoJSON(sample_location::geometry) AS sample_location_geojson,
            ST_AsGeoJSON(device_location::geometry) AS device_location_geojson
        FROM app.v_device_latest_samples
        ORDER BY recorded_at DESC NULLS LAST
        LIMIT :limit OFFSET :offset
        """
    )
    result = await db.execute(
        stmt,
        {
            "limit": pagination.limit,
            "offset": pagination.offset,
        },
    )
    rows = []
    for row in result.mappings().all():
        data = dict(row)
        sample_loc = data.pop("sample_location_geojson", None)
        device_loc = data.pop("device_location_geojson", None)
        if sample_loc:
            data["sample_location"] = json.loads(sample_loc)
        else:
            data["sample_location"] = None
        if device_loc:
            data["device_location"] = json.loads(device_loc)
        else:
            data["device_location"] = None
        rows.append(data)

    return DeviceLatestSamplesResponse(
        data=rows,
        pagination={
            "limit": pagination.limit,
            "offset": pagination.offset,
            "total": None,
        },
    )


@devices_router.get("", response_model=DeviceListResponse)
async def list_devices(
    pagination: PaginationParams = Depends(pagination_params),
    db: AsyncSession = Depends(get_db_session),
    status_filter: Optional[str] = None,
) -> DeviceListResponse:
    stmt = text(
        """
        SELECT
            id,
            name,
            slug,
            status,
            taxon_id,
            metadata,
            created_at,
            updated_at,
            ST_AsGeoJSON(location::geometry) AS location_geojson
        FROM telemetry.device
        WHERE (:status IS NULL OR status = :status)
        ORDER BY created_at DESC
        LIMIT :limit OFFSET :offset
        """
    )
    count_stmt = text(
        """
        SELECT count(*)
        FROM telemetry.device
        WHERE (:status IS NULL OR status = :status)
        """
    )

    params = {
        "status": status_filter,
        "limit": pagination.limit,
        "offset": pagination.offset,
    }

    result = await db.execute(stmt, params)
    count_result = await db.execute(count_stmt, params)
    total = count_result.scalar_one()

    devices = []
    for row in result.mappings().all():
        data = dict(row)
        loc = data.pop("location_geojson", None)
        data["location"] = json.loads(loc) if loc else None
        devices.append(data)

    return DeviceListResponse(
        data=devices,
        pagination={
            "limit": pagination.limit,
            "offset": pagination.offset,
            "total": total,
        },
    )
