"""Maritime Sensor Data Ingestion — TAC-O Maritime Integration.

Ingests acoustic and magnetic sensor data from contractor-agnostic
maritime sensor packages relayed through surface relay -> MycoBrain -> MDP.
"""

import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class MaritimeSensorIngestor:
    """Ingest sensor data from maritime sensor relay networks."""

    def __init__(self, db_pool=None):
        self.db_pool = db_pool

    async def ingest_acoustic(self, mdp_payload: Dict[str, Any]) -> Optional[str]:
        """Process acoustic data from an underwater acoustic sensor."""
        sensor_id = mdp_payload.get("sensor_id", "unknown")
        fingerprint = mdp_payload.get("fingerprint", {})
        location = mdp_payload.get("location", {})
        merkle_hash = self._compute_merkle_hash(mdp_payload)

        if not self.db_pool:
            return None
        try:
            async with self.db_pool.acquire() as conn:
                row = await conn.fetchrow(
                    """INSERT INTO taco_observations
                        (sensor_id, sensor_type, location, depth_m,
                         raw_data, processed_fingerprint, observed_at, merkle_hash)
                    VALUES ($1, 'acoustic', ST_SetSRID(ST_MakePoint($2, $3), 4326),
                            $4, $5, $6, $7, $8)
                    RETURNING observation_id""",
                    sensor_id, location.get("longitude", 0), location.get("latitude", 0),
                    mdp_payload.get("depth_m"),
                    json.dumps(mdp_payload.get("raw_data", {})),
                    json.dumps(fingerprint),
                    mdp_payload.get("observed_at", datetime.now(timezone.utc).isoformat()),
                    merkle_hash,
                )
                return str(row["observation_id"]) if row else None
        except Exception as e:
            logger.error("Acoustic ingest failed for sensor %s: %s", sensor_id, e)
            return None

    async def ingest_magnetic(self, mdp_payload: Dict[str, Any]) -> Optional[str]:
        """Process magnetic data from an underwater magnetic sensor."""
        sensor_id = mdp_payload.get("sensor_id", "unknown")
        fingerprint = mdp_payload.get("fingerprint", {})
        location = mdp_payload.get("location", {})
        merkle_hash = self._compute_merkle_hash(mdp_payload)

        if not self.db_pool:
            return None
        try:
            async with self.db_pool.acquire() as conn:
                row = await conn.fetchrow(
                    """INSERT INTO taco_observations
                        (sensor_id, sensor_type, location, depth_m,
                         raw_data, processed_fingerprint, observed_at, merkle_hash)
                    VALUES ($1, 'magnetic', ST_SetSRID(ST_MakePoint($2, $3), 4326),
                            $4, $5, $6, $7, $8)
                    RETURNING observation_id""",
                    sensor_id, location.get("longitude", 0), location.get("latitude", 0),
                    mdp_payload.get("depth_m"),
                    json.dumps(mdp_payload.get("raw_data", {})),
                    json.dumps(fingerprint),
                    mdp_payload.get("observed_at", datetime.now(timezone.utc).isoformat()),
                    merkle_hash,
                )
                return str(row["observation_id"]) if row else None
        except Exception as e:
            logger.error("Magnetic ingest failed for sensor %s: %s", sensor_id, e)
            return None

    @staticmethod
    def _compute_merkle_hash(payload: Dict[str, Any]) -> str:
        canonical = json.dumps(payload, sort_keys=True, default=str)
        return hashlib.sha256(canonical.encode()).hexdigest()


# Backward-compatible alias for older imports.
ZeetachecIngestor = MaritimeSensorIngestor
