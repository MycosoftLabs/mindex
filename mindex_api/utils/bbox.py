"""WGS84 bounds for existing nonwrapping, positive-area bbox route contracts."""
from __future__ import annotations

import math
from typing import Optional

from fastapi import HTTPException, status


def parse_wgs84_bbox(bbox: Optional[str]) -> Optional[dict[str, float]]:
    """Preserve min/max mapping and HTTP 400 errors; never normalize or wrap."""
    if not bbox:
        return None
    try:
        parts = [float(value.strip()) for value in bbox.split(",")]
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid bbox format") from exc
    if len(parts) != 4:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Expected bbox=minLon,minLat,maxLon,maxLat")
    min_lon, min_lat, max_lon, max_lat = parts
    if (not all(math.isfinite(value) for value in parts)
            or not (-180 <= min_lon <= 180 and -180 <= max_lon <= 180)
            or not (-90 <= min_lat <= 90 and -90 <= max_lat <= 90)
            or min_lon >= max_lon or min_lat >= max_lat):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid bbox coordinates")
    return {"min_lon": min_lon, "min_lat": min_lat, "max_lon": max_lon, "max_lat": max_lat}
