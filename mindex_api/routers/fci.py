"""
FCI (Fungal Computer Interface) API Router

Provides endpoints for storing and querying bioelectric signal data,
pattern detections, and GFST pattern library.

Integrates with the Mycorrhizae Protocol for semantic translation.

(c) 2026 Mycosoft Labs
"""

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, Query, Depends
from pydantic import BaseModel, Field
import asyncpg

from ..db import get_db_pool

router = APIRouter(prefix="/api/fci", tags=["FCI - Fungal Computer Interface"])


# ============================================================================
# PYDANTIC MODELS
# ============================================================================

class GeoLocation(BaseModel):
    latitude: float
    longitude: float
    altitude_m: Optional[float] = None


class FCIDeviceCreate(BaseModel):
    device_id: str = Field(..., description="Unique device identifier")
    device_serial: Optional[str] = None
    device_name: Optional[str] = None
    probe_type: str = Field(default="type_a", description="Probe type: type_a, type_b, type_c, type_d, custom")
    electrode_materials: List[str] = Field(default_factory=list)
    firmware_version: Optional[str] = None
    location: Optional[GeoLocation] = None
    sample_rate_hz: int = 128
    channels_count: int = 2


class FCIDeviceResponse(BaseModel):
    id: UUID
    device_id: str
    device_name: Optional[str]
    probe_type: str
    status: str
    last_seen: Optional[datetime]
    total_readings: int
    created_at: datetime


class BioelectricChannel(BaseModel):
    channel_id: str
    amplitude_uv: float
    rms_uv: Optional[float] = None
    mean_uv: Optional[float] = None
    std_uv: Optional[float] = None
    dominant_freq_hz: Optional[float] = None
    spectral_centroid_hz: Optional[float] = None
    total_power: Optional[float] = None
    band_powers: Optional[Dict[str, float]] = None
    snr_db: Optional[float] = None
    quality_score: Optional[float] = None


class PatternInfo(BaseModel):
    name: str
    category: str
    confidence: float


class EnvironmentData(BaseModel):
    temperature_c: Optional[float] = None
    humidity_pct: Optional[float] = None
    pressure_hpa: Optional[float] = None
    voc_index: Optional[int] = None
    co2_ppm: Optional[int] = None
    light_lux: Optional[float] = None


class FCITelemetrySubmit(BaseModel):
    device_id: str = Field(..., description="Device identifier")
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    channels: List[BioelectricChannel]
    pattern: Optional[PatternInfo] = None
    environment: Optional[EnvironmentData] = None
    spike_count: int = 0
    spike_rate_hz: float = 0.0
    envelope_id: Optional[UUID] = None


class FCIPatternCreate(BaseModel):
    device_id: str
    channel_id: Optional[str] = None
    pattern_name: str
    pattern_category: str
    start_time: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    confidence_score: float
    confidence_level: str = "moderate"
    amplitude_uv: Optional[float] = None
    dominant_freq_hz: Optional[float] = None
    spike_count: Optional[int] = None
    spike_rate_hz: Optional[float] = None
    feature_scores: Dict[str, float] = Field(default_factory=dict)
    phase: str = "onset"
    environment: Optional[EnvironmentData] = None
    interpretation_meaning: Optional[str] = None
    interpretation_implications: List[str] = Field(default_factory=list)
    interpretation_actions: List[str] = Field(default_factory=list)


class FCIPatternResponse(BaseModel):
    id: UUID
    device_id: UUID
    pattern_name: str
    pattern_category: str
    start_time: datetime
    end_time: Optional[datetime]
    confidence_score: float
    confidence_level: Optional[str]
    phase: str
    interpretation_meaning: Optional[str]
    created_at: datetime


class GFSTPatternResponse(BaseModel):
    id: UUID
    name: str
    category: str
    amplitude_min_uv: float
    amplitude_max_uv: float
    frequency_min_hz: float
    frequency_max_hz: float
    meaning: str
    implications: List[str]
    recommended_actions: List[str]
    is_experimental: bool


class StimulusCommandCreate(BaseModel):
    device_id: str
    waveform: str = Field(default="pulse", description="pulse, sine, square, custom")
    amplitude_uv: float = Field(..., ge=0, le=100, description="Amplitude in microvolts (max 100)")
    frequency_hz: float = Field(default=1.0, ge=0.1, le=50)
    duration_ms: int = Field(..., ge=1, le=60000, description="Duration in milliseconds")
    requested_by: str = "api"


class DevicePatternSummary(BaseModel):
    pattern_name: str
    pattern_category: str
    occurrence_count: int
    avg_confidence: float
    first_seen: datetime
    last_seen: datetime


# ============================================================================
# DEVICE ENDPOINTS
# ============================================================================

@router.post("/devices", response_model=FCIDeviceResponse)
async def register_device(device: FCIDeviceCreate, pool = Depends(get_db_pool)):
    """Register a new FCI device."""
    async with pool.acquire() as conn:
        # Check if device already exists
        existing = await conn.fetchrow(
            "SELECT id FROM fci_devices WHERE device_id = $1",
            device.device_id
        )
        
        if existing:
            raise HTTPException(status_code=409, detail="Device already registered")
        
        device_uuid = uuid4()
        
        await conn.execute("""
            INSERT INTO fci_devices (
                id, device_id, device_serial, device_name, probe_type,
                electrode_materials, firmware_version, latitude, longitude, altitude_m,
                sample_rate_hz, channels_count
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
        """,
            device_uuid,
            device.device_id,
            device.device_serial,
            device.device_name,
            device.probe_type,
            device.electrode_materials,
            device.firmware_version,
            device.location.latitude if device.location else None,
            device.location.longitude if device.location else None,
            device.location.altitude_m if device.location else None,
            device.sample_rate_hz,
            device.channels_count,
        )
        
        return FCIDeviceResponse(
            id=device_uuid,
            device_id=device.device_id,
            device_name=device.device_name,
            probe_type=device.probe_type,
            status="active",
            last_seen=None,
            total_readings=0,
            created_at=datetime.now(timezone.utc),
        )


@router.get("/devices", response_model=List[FCIDeviceResponse])
async def list_devices(
    status: Optional[str] = Query(None, description="Filter by status"),
    limit: int = Query(100, ge=1, le=1000),
    pool = Depends(get_db_pool),
):
    """List all registered FCI devices."""
    async with pool.acquire() as conn:
        if status:
            rows = await conn.fetch(
                """SELECT id, device_id, device_name, probe_type, status, 
                          last_seen, total_readings, created_at 
                   FROM fci_devices WHERE status = $1 
                   ORDER BY created_at DESC LIMIT $2""",
                status, limit
            )
        else:
            rows = await conn.fetch(
                """SELECT id, device_id, device_name, probe_type, status, 
                          last_seen, total_readings, created_at 
                   FROM fci_devices ORDER BY created_at DESC LIMIT $1""",
                limit
            )
        
        return [FCIDeviceResponse(**dict(row)) for row in rows]


@router.get("/devices/{device_id}", response_model=FCIDeviceResponse)
async def get_device(device_id: str, pool = Depends(get_db_pool)):
    """Get a specific FCI device by device_id."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """SELECT id, device_id, device_name, probe_type, status, 
                      last_seen, total_readings, created_at 
               FROM fci_devices WHERE device_id = $1""",
            device_id
        )
        
        if not row:
            raise HTTPException(status_code=404, detail="Device not found")
        
        return FCIDeviceResponse(**dict(row))


# ============================================================================
# TELEMETRY ENDPOINTS
# ============================================================================

@router.post("/telemetry", status_code=201)
async def submit_telemetry(telemetry: FCITelemetrySubmit, pool = Depends(get_db_pool)):
    """
    Submit FCI telemetry data.
    
    This is the primary endpoint for ingesting bioelectric signal data
    from MycoBrain FCI devices.
    """
    async with pool.acquire() as conn:
        # Get device UUID
        device_row = await conn.fetchrow(
            "SELECT id FROM fci_devices WHERE device_id = $1",
            telemetry.device_id
        )
        
        if not device_row:
            raise HTTPException(status_code=404, detail="Device not registered")
        
        device_uuid = device_row["id"]
        
        # Insert readings for each channel
        reading_ids = []
        for channel in telemetry.channels:
            reading_id = uuid4()
            
            await conn.execute("""
                INSERT INTO fci_readings (
                    id, device_id, timestamp, amplitude_uv, rms_uv, mean_uv, std_uv,
                    dominant_freq_hz, spectral_centroid_hz, total_power,
                    band_power_ultra_low, band_power_low, band_power_mid, band_power_high,
                    snr_db, quality_score, spike_count, spike_rate_hz, envelope_id
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17, $18, $19)
            """,
                reading_id,
                device_uuid,
                telemetry.timestamp,
                channel.amplitude_uv,
                channel.rms_uv,
                channel.mean_uv,
                channel.std_uv,
                channel.dominant_freq_hz,
                channel.spectral_centroid_hz,
                channel.total_power,
                channel.band_powers.get("ultra_low") if channel.band_powers else None,
                channel.band_powers.get("low") if channel.band_powers else None,
                channel.band_powers.get("mid") if channel.band_powers else None,
                channel.band_powers.get("high") if channel.band_powers else None,
                channel.snr_db,
                channel.quality_score,
                telemetry.spike_count,
                telemetry.spike_rate_hz,
                telemetry.envelope_id,
            )
            reading_ids.append(reading_id)
        
        # Update device last_seen and total_readings
        await conn.execute("""
            UPDATE fci_devices 
            SET last_seen = $1, total_readings = total_readings + $2
            WHERE id = $3
        """, telemetry.timestamp, len(telemetry.channels), device_uuid)
        
        # If pattern was detected, store it
        pattern_id = None
        if telemetry.pattern and telemetry.pattern.confidence > 0.3:
            pattern_id = uuid4()
            
            await conn.execute("""
                INSERT INTO fci_patterns (
                    id, device_id, pattern_name, pattern_category,
                    start_time, confidence_score, confidence_level,
                    amplitude_uv, dominant_freq_hz, spike_count, spike_rate_hz,
                    temperature_c, humidity_pct, pressure_hpa, voc_index
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15)
            """,
                pattern_id,
                device_uuid,
                telemetry.pattern.name,
                telemetry.pattern.category,
                telemetry.timestamp,
                telemetry.pattern.confidence,
                "high" if telemetry.pattern.confidence > 0.8 else ("moderate" if telemetry.pattern.confidence > 0.6 else "low"),
                telemetry.channels[0].amplitude_uv if telemetry.channels else None,
                telemetry.channels[0].dominant_freq_hz if telemetry.channels else None,
                telemetry.spike_count,
                telemetry.spike_rate_hz,
                telemetry.environment.temperature_c if telemetry.environment else None,
                telemetry.environment.humidity_pct if telemetry.environment else None,
                telemetry.environment.pressure_hpa if telemetry.environment else None,
                telemetry.environment.voc_index if telemetry.environment else None,
            )
        
        return {
            "status": "accepted",
            "reading_ids": [str(rid) for rid in reading_ids],
            "pattern_id": str(pattern_id) if pattern_id else None,
            "device_total_readings": await conn.fetchval(
                "SELECT total_readings FROM fci_devices WHERE id = $1",
                device_uuid
            ),
        }


@router.get("/telemetry/{device_id}")
async def get_telemetry(
    device_id: str,
    hours: int = Query(24, ge=1, le=168, description="Hours of data to retrieve"),
    limit: int = Query(1000, ge=1, le=10000),
    pool = Depends(get_db_pool),
):
    """Get recent telemetry data for a device."""
    async with pool.acquire() as conn:
        device_row = await conn.fetchrow(
            "SELECT id FROM fci_devices WHERE device_id = $1",
            device_id
        )
        
        if not device_row:
            raise HTTPException(status_code=404, detail="Device not found")
        
        rows = await conn.fetch("""
            SELECT id, timestamp, amplitude_uv, rms_uv, dominant_freq_hz,
                   snr_db, quality_score, spike_count, spike_rate_hz
            FROM fci_readings
            WHERE device_id = $1 AND timestamp > NOW() - ($2 || ' hours')::INTERVAL
            ORDER BY timestamp DESC
            LIMIT $3
        """, device_row["id"], str(hours), limit)
        
        return {
            "device_id": device_id,
            "hours": hours,
            "count": len(rows),
            "readings": [dict(row) for row in rows],
        }


# ============================================================================
# PATTERN ENDPOINTS
# ============================================================================

@router.post("/patterns", response_model=FCIPatternResponse, status_code=201)
async def create_pattern(pattern: FCIPatternCreate, pool = Depends(get_db_pool)):
    """Record a detected pattern with semantic interpretation."""
    async with pool.acquire() as conn:
        device_row = await conn.fetchrow(
            "SELECT id FROM fci_devices WHERE device_id = $1",
            pattern.device_id
        )
        
        if not device_row:
            raise HTTPException(status_code=404, detail="Device not found")
        
        pattern_id = uuid4()
        
        await conn.execute("""
            INSERT INTO fci_patterns (
                id, device_id, pattern_name, pattern_category,
                start_time, confidence_score, confidence_level,
                amplitude_uv, dominant_freq_hz, spike_count, spike_rate_hz,
                feature_scores, phase,
                temperature_c, humidity_pct, pressure_hpa, voc_index,
                interpretation_meaning, interpretation_implications, interpretation_actions
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17, $18, $19, $20)
        """,
            pattern_id,
            device_row["id"],
            pattern.pattern_name,
            pattern.pattern_category,
            pattern.start_time,
            pattern.confidence_score,
            pattern.confidence_level,
            pattern.amplitude_uv,
            pattern.dominant_freq_hz,
            pattern.spike_count,
            pattern.spike_rate_hz,
            pattern.feature_scores,
            pattern.phase,
            pattern.environment.temperature_c if pattern.environment else None,
            pattern.environment.humidity_pct if pattern.environment else None,
            pattern.environment.pressure_hpa if pattern.environment else None,
            pattern.environment.voc_index if pattern.environment else None,
            pattern.interpretation_meaning,
            pattern.interpretation_implications,
            pattern.interpretation_actions,
        )
        
        return FCIPatternResponse(
            id=pattern_id,
            device_id=device_row["id"],
            pattern_name=pattern.pattern_name,
            pattern_category=pattern.pattern_category,
            start_time=pattern.start_time,
            end_time=None,
            confidence_score=pattern.confidence_score,
            confidence_level=pattern.confidence_level,
            phase=pattern.phase,
            interpretation_meaning=pattern.interpretation_meaning,
            created_at=datetime.now(timezone.utc),
        )


@router.get("/patterns/{device_id}", response_model=List[FCIPatternResponse])
async def get_device_patterns(
    device_id: str,
    hours: int = Query(24, ge=1, le=720),
    min_confidence: float = Query(0.5, ge=0, le=1),
    category: Optional[str] = None,
    pool = Depends(get_db_pool),
):
    """Get detected patterns for a device."""
    async with pool.acquire() as conn:
        device_row = await conn.fetchrow(
            "SELECT id FROM fci_devices WHERE device_id = $1",
            device_id
        )
        
        if not device_row:
            raise HTTPException(status_code=404, detail="Device not found")
        
        query = """
            SELECT id, device_id, pattern_name, pattern_category,
                   start_time, end_time, confidence_score, confidence_level,
                   phase, interpretation_meaning, created_at
            FROM fci_patterns
            WHERE device_id = $1 
              AND start_time > NOW() - ($2 || ' hours')::INTERVAL
              AND confidence_score >= $3
        """
        params = [device_row["id"], str(hours), min_confidence]
        
        if category:
            query += " AND pattern_category = $4"
            params.append(category)
        
        query += " ORDER BY start_time DESC LIMIT 500"
        
        rows = await conn.fetch(query, *params)
        
        return [FCIPatternResponse(**dict(row)) for row in rows]


@router.get("/patterns/{device_id}/summary", response_model=List[DevicePatternSummary])
async def get_pattern_summary(
    device_id: str,
    hours: int = Query(24, ge=1, le=168),
    pool = Depends(get_db_pool),
):
    """Get pattern occurrence summary for a device."""
    async with pool.acquire() as conn:
        device_row = await conn.fetchrow(
            "SELECT id FROM fci_devices WHERE device_id = $1",
            device_id
        )
        
        if not device_row:
            raise HTTPException(status_code=404, detail="Device not found")
        
        rows = await conn.fetch("""
            SELECT pattern_name, pattern_category,
                   COUNT(*) as occurrence_count,
                   AVG(confidence_score) as avg_confidence,
                   MIN(start_time) as first_seen,
                   MAX(start_time) as last_seen
            FROM fci_patterns
            WHERE device_id = $1 AND start_time > NOW() - ($2 || ' hours')::INTERVAL
            GROUP BY pattern_name, pattern_category
            ORDER BY occurrence_count DESC
        """, device_row["id"], str(hours))
        
        return [DevicePatternSummary(**dict(row)) for row in rows]


# ============================================================================
# GFST PATTERN LIBRARY ENDPOINTS
# ============================================================================

@router.get("/gfst/patterns", response_model=List[GFSTPatternResponse])
async def list_gfst_patterns(
    category: Optional[str] = None,
    include_experimental: bool = False,
    pool = Depends(get_db_pool),
):
    """Get the GFST pattern library."""
    async with pool.acquire() as conn:
        query = """
            SELECT id, name, category, amplitude_min_uv, amplitude_max_uv,
                   frequency_min_hz, frequency_max_hz, meaning, 
                   implications, recommended_actions, is_experimental
            FROM gfst_pattern_library
            WHERE is_active = true
        """
        params = []
        
        if category:
            query += " AND category = $1"
            params.append(category)
            if not include_experimental:
                query += " AND is_experimental = false"
        elif not include_experimental:
            query += " AND is_experimental = false"
        
        query += " ORDER BY name"
        
        rows = await conn.fetch(query, *params) if params else await conn.fetch(query)
        
        return [GFSTPatternResponse(**dict(row)) for row in rows]


@router.get("/gfst/patterns/{name}", response_model=GFSTPatternResponse)
async def get_gfst_pattern(name: str, pool = Depends(get_db_pool)):
    """Get a specific GFST pattern definition."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow("""
            SELECT id, name, category, amplitude_min_uv, amplitude_max_uv,
                   frequency_min_hz, frequency_max_hz, meaning, 
                   implications, recommended_actions, is_experimental
            FROM gfst_pattern_library
            WHERE name = $1 AND is_active = true
        """, name)
        
        if not row:
            raise HTTPException(status_code=404, detail="Pattern not found")
        
        return GFSTPatternResponse(**dict(row))


# ============================================================================
# STIMULATION ENDPOINTS
# ============================================================================

@router.post("/stimulate", status_code=202)
async def send_stimulus_command(command: StimulusCommandCreate, pool = Depends(get_db_pool)):
    """
    Send a stimulation command to an FCI device.
    
    This enables bi-directional communication with mycelium.
    Commands are queued and executed by the device.
    """
    async with pool.acquire() as conn:
        device_row = await conn.fetchrow(
            "SELECT id FROM fci_devices WHERE device_id = $1 AND status = 'active'",
            command.device_id
        )
        
        if not device_row:
            raise HTTPException(status_code=404, detail="Device not found or offline")
        
        stim_id = uuid4()
        
        await conn.execute("""
            INSERT INTO fci_stimulations (
                id, device_id, command_type, waveform,
                amplitude_uv, frequency_hz, duration_ms,
                max_amplitude_uv, max_duration_ms, requested_by
            ) VALUES ($1, $2, 'start_stimulus', $3, $4, $5, $6, 100, 60000, $7)
        """,
            stim_id,
            device_row["id"],
            command.waveform,
            command.amplitude_uv,
            command.frequency_hz,
            command.duration_ms,
            command.requested_by,
        )
        
        return {
            "status": "queued",
            "stimulation_id": str(stim_id),
            "message": "Stimulus command queued for device",
        }


@router.get("/stimulate/{device_id}/history")
async def get_stimulation_history(
    device_id: str,
    limit: int = Query(50, ge=1, le=500),
    pool = Depends(get_db_pool),
):
    """Get stimulation history for a device."""
    async with pool.acquire() as conn:
        device_row = await conn.fetchrow(
            "SELECT id FROM fci_devices WHERE device_id = $1",
            device_id
        )
        
        if not device_row:
            raise HTTPException(status_code=404, detail="Device not found")
        
        rows = await conn.fetch("""
            SELECT id, waveform, amplitude_uv, frequency_hz, duration_ms,
                   status, requested_at, executed_at, completed_at,
                   response_pattern_detected
            FROM fci_stimulations
            WHERE device_id = $1
            ORDER BY requested_at DESC
            LIMIT $2
        """, device_row["id"], limit)
        
        return {"device_id": device_id, "stimulations": [dict(row) for row in rows]}


# ============================================================================
# HEALTH AND STATS
# ============================================================================

@router.get("/health")
async def fci_health(pool = Depends(get_db_pool)):
    """Check FCI subsystem health."""
    async with pool.acquire() as conn:
        device_count = await conn.fetchval("SELECT COUNT(*) FROM fci_devices")
        active_devices = await conn.fetchval(
            "SELECT COUNT(*) FROM fci_devices WHERE status = 'active' AND last_seen > NOW() - INTERVAL '1 hour'"
        )
        pattern_count_24h = await conn.fetchval(
            "SELECT COUNT(*) FROM fci_patterns WHERE start_time > NOW() - INTERVAL '24 hours'"
        )
        
        return {
            "status": "healthy",
            "total_devices": device_count,
            "active_devices_1h": active_devices,
            "patterns_detected_24h": pattern_count_24h,
            "gfst_patterns_loaded": await conn.fetchval(
                "SELECT COUNT(*) FROM gfst_pattern_library WHERE is_active = true"
            ),
        }
