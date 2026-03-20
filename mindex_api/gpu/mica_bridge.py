"""
MICA Bridge — Merkle-Verified GPU Computation
================================================
First Python code to write to mica.* tables in MINDEX.

Provides cryptographic provenance for all GPU operations:
    - cuVS index builds → root_record (type=spatial)
    - cuVS index updates → chained root_records
    - ETL transforms → event_object (kind=gpu.etl_transform)
    - Vector searches → event_object (kind=gpu.vector_search)
    - STATIC constraint sets → event_object (kind=gpu.constraint_set)

Each GPU computation result is content-addressed via BLAKE3-256,
stored as a ca_object, and linked into the Merkle tree so that
downstream consumers (MAS, CREP, NatureOS) can verify provenance.

Interfaces with:
    - mica.ca_object — content-addressed storage
    - mica.event_object — immutable event stream
    - mica.root_record — Merkle tree roots
    - mica.root_member — Merkle tree children
    - mica.mutable_head — latest root pointers
    - cuVS index manager — index state hashing
    - cuDF engine — bulk BLAKE3 hashing
"""

from __future__ import annotations

import hashlib
import json
import logging
import struct
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from uuid import uuid4

from .config import gpu_config

logger = logging.getLogger(__name__)


def _blake3_hash(data: bytes) -> bytes:
    """Compute BLAKE3-256 hash, falling back to SHA-256 if blake3 not installed."""
    try:
        import blake3

        return blake3.blake3(data).digest()
    except ImportError:
        return hashlib.sha256(data).digest()


def _deterministic_cbor(obj: Any) -> bytes:
    """Deterministic CBOR serialization, falling back to sorted JSON."""
    try:
        import cbor2

        return cbor2.dumps(obj, canonical=True)
    except ImportError:
        return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()


class MICABridge:
    """Writes GPU computation results into the MICA Merkle ledger.

    All writes are idempotent — content-addressed objects with the same
    hash will not be duplicated (INSERT ON CONFLICT DO NOTHING).
    """

    DEVICE_ID = "mindex.gpu"
    PRODUCER = "mindex-gpu-accelerator"

    # ------------------------------------------------------------------
    # Core: content-addressed object storage
    # ------------------------------------------------------------------

    async def store_object(
        self,
        db_session,
        body: Any,
        object_type: str,
        codec: str = "cbor",
        labels: Optional[Dict[str, str]] = None,
    ) -> bytes:
        """Hash and store an object in mica.ca_object.

        Returns the 32-byte object hash (BLAKE3 or SHA-256).
        Idempotent: duplicate hashes are silently ignored.
        """
        from sqlalchemy import text

        body_bytes = _deterministic_cbor(body) if codec == "cbor" else json.dumps(body).encode()
        object_hash = _blake3_hash(body_bytes)
        labels = labels or {}

        await db_session.execute(
            text("""
                INSERT INTO mica.ca_object (
                    object_hash, codec, object_type, size_bytes,
                    body_cbor, created_by, labels
                ) VALUES (
                    :hash, :codec, :type, :size,
                    :body, :created_by, :labels::jsonb
                )
                ON CONFLICT (object_hash) DO NOTHING
            """),
            {
                "hash": object_hash,
                "codec": codec,
                "type": object_type,
                "size": len(body_bytes),
                "body": body_bytes,
                "created_by": self.PRODUCER,
                "labels": json.dumps(labels),
            },
        )
        await db_session.commit()
        return object_hash

    # ------------------------------------------------------------------
    # Events: immutable computation records
    # ------------------------------------------------------------------

    async def record_event(
        self,
        db_session,
        kind: str,
        payload: Dict[str, Any],
        event_id: Optional[str] = None,
        parent_hashes: Optional[List[bytes]] = None,
        lat: Optional[float] = None,
        lon: Optional[float] = None,
    ) -> bytes:
        """Record a GPU computation event in mica.event_object.

        Event types:
            - gpu.index_build: cuVS index was built
            - gpu.index_update: cuVS index was updated
            - gpu.vector_search: vector search was performed
            - gpu.etl_transform: ETL data was GPU-transformed
            - gpu.constraint_set: STATIC constraints were extracted
            - gpu.merkle_verify: Merkle proof was verified

        Returns the event hash.
        """
        from sqlalchemy import text

        now = datetime.now(timezone.utc)
        now_ns = int(now.timestamp() * 1e9)
        event_id = event_id or str(uuid4())
        tick_id = int(now.timestamp())  # 1-second ticks

        # Store the payload as a ca_object first
        object_hash = await self.store_object(
            db_session, payload, object_type=f"event.{kind}",
            labels={"kind": kind},
        )

        # Build geometry if coordinates provided
        geom_clause = "NULL"
        params: Dict[str, Any] = {
            "event_hash": object_hash,
            "event_id": event_id,
            "kind": kind,
            "device_id": self.DEVICE_ID,
            "producer": self.PRODUCER,
            "event_time": now,
            "ingest_time": now,
            "event_time_ns": now_ns,
            "ingest_time_ns": now_ns,
            "tick_width_ns": 1_000_000_000,  # 1-second ticks
            "tick_id": tick_id,
            "parent_hashes": parent_hashes or [],
            "fields": json.dumps(payload),
        }

        if lat is not None and lon is not None:
            geom_clause = "ST_SetSRID(ST_MakePoint(:lon, :lat), 4326)::geography"
            params["lat"] = lat
            params["lon"] = lon

        try:
            await db_session.execute(
                text(f"""
                    INSERT INTO mica.event_object (
                        event_hash, event_id, kind, device_id, producer,
                        event_time, ingest_time, event_time_ns, ingest_time_ns,
                        tick_width_ns, tick_id,
                        lat, lon, geom,
                        provenance_parent_hashes, fields
                    ) VALUES (
                        :event_hash, :event_id, :kind, :device_id, :producer,
                        :event_time, :ingest_time, :event_time_ns, :ingest_time_ns,
                        :tick_width_ns, :tick_id,
                        {":lat" if lat is not None else "NULL"},
                        {":lon" if lon is not None else "NULL"},
                        {geom_clause},
                        :parent_hashes, :fields::jsonb
                    )
                    ON CONFLICT (device_id, event_id) DO NOTHING
                """),
                params,
            )
            await db_session.commit()
        except Exception as e:
            logger.warning("Failed to record event: %s", e)
            await db_session.rollback()

        return object_hash

    # ------------------------------------------------------------------
    # Merkle roots: index state snapshots
    # ------------------------------------------------------------------

    async def create_root(
        self,
        db_session,
        root_type: str,
        member_hashes: List[bytes],
        previous_root_hash: Optional[bytes] = None,
        labels: Optional[Dict[str, str]] = None,
    ) -> bytes:
        """Build a Merkle root from member hashes and store in mica.root_record.

        The root hash is computed as BLAKE3(sorted(member_hashes)).
        Each member is recorded in mica.root_member with its ordinal.

        Returns the root hash.
        """
        from sqlalchemy import text

        now = datetime.now(timezone.utc)
        tick_id = int(now.timestamp())
        labels = labels or {}

        # Compute root hash deterministically
        root_body = {
            "type": root_type,
            "members": [h.hex() for h in sorted(member_hashes)],
            "tick_id": tick_id,
        }
        root_hash = await self.store_object(
            db_session, root_body, object_type=f"root.{root_type}",
            labels=labels,
        )

        # Insert root record
        try:
            await db_session.execute(
                text("""
                    INSERT INTO mica.root_record (
                        root_hash, root_type, tick_id,
                        tick_start_ns, tick_width_ns,
                        status, child_count,
                        previous_root_hash, labels
                    ) VALUES (
                        :root_hash, :root_type, :tick_id,
                        :tick_start_ns, :tick_width_ns,
                        'final', :child_count,
                        :prev_hash, :labels::jsonb
                    )
                    ON CONFLICT (root_hash) DO NOTHING
                """),
                {
                    "root_hash": root_hash,
                    "root_type": root_type,
                    "tick_id": tick_id,
                    "tick_start_ns": int(now.timestamp() * 1e9),
                    "tick_width_ns": 1_000_000_000,
                    "child_count": len(member_hashes),
                    "prev_hash": previous_root_hash,
                    "labels": json.dumps(labels),
                },
            )

            # Insert members
            for ordinal, member_hash in enumerate(member_hashes):
                await db_session.execute(
                    text("""
                        INSERT INTO mica.root_member (
                            root_hash, ordinal, member_hash, member_type
                        ) VALUES (
                            :root_hash, :ordinal, :member_hash, :member_type
                        )
                        ON CONFLICT (root_hash, ordinal) DO NOTHING
                    """),
                    {
                        "root_hash": root_hash,
                        "ordinal": ordinal,
                        "member_hash": member_hash,
                        "member_type": root_type,
                    },
                )

            await db_session.commit()
        except Exception as e:
            logger.warning("Failed to create root: %s", e)
            await db_session.rollback()

        return root_hash

    # ------------------------------------------------------------------
    # Specialized: index snapshot → MICA
    # ------------------------------------------------------------------

    async def record_index_snapshot(
        self,
        db_session,
        index_name: str,
        index_hash: bytes,
        vector_count: int,
        previous_root_hash: Optional[bytes] = None,
    ) -> bytes:
        """Record a cuVS index state as a MICA Merkle root.

        Creates:
            1. ca_object for the index metadata
            2. root_record linking to the index hash
            3. Event recording the build operation
        """
        # Record the build event
        event_hash = await self.record_event(
            db_session,
            kind="gpu.index_build",
            payload={
                "index_name": index_name,
                "index_hash": index_hash.hex(),
                "vector_count": vector_count,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        )

        # Create Merkle root for this index version
        root_hash = await self.create_root(
            db_session,
            root_type="spatial",
            member_hashes=[index_hash, event_hash],
            previous_root_hash=previous_root_hash,
            labels={
                "index_name": index_name,
                "vector_count": str(vector_count),
            },
        )

        # Update mutable head pointer
        from sqlalchemy import text

        await db_session.execute(
            text("""
                INSERT INTO mica.mutable_head (head_name, object_hash, labels)
                VALUES (:head, :hash, :labels::jsonb)
                ON CONFLICT (head_name) DO UPDATE SET
                    object_hash = :hash,
                    updated_at = NOW(),
                    labels = :labels::jsonb
            """),
            {
                "head": f"cuvs.{index_name}",
                "hash": root_hash,
                "labels": json.dumps({"index_name": index_name}),
            },
        )
        await db_session.commit()

        logger.info(
            "MICA: recorded index snapshot for '%s' (root=%s, vectors=%d)",
            index_name, root_hash.hex()[:16], vector_count,
        )
        return root_hash

    # ------------------------------------------------------------------
    # Verification
    # ------------------------------------------------------------------

    async def verify_membership(
        self,
        db_session,
        root_hash: bytes,
        member_hash: bytes,
    ) -> bool:
        """Verify that a member hash exists in a Merkle root."""
        from sqlalchemy import text

        result = await db_session.execute(
            text("""
                SELECT 1 FROM mica.root_member
                WHERE root_hash = :root_hash AND member_hash = :member_hash
                LIMIT 1
            """),
            {"root_hash": root_hash, "member_hash": member_hash},
        )
        return result.scalar_one_or_none() is not None

    async def get_root_chain(
        self,
        db_session,
        head_name: str,
        max_depth: int = 10,
    ) -> List[Dict[str, Any]]:
        """Walk the root chain for a mutable head (e.g., 'cuvs.fci_signals').

        Returns a list of root records from newest to oldest.
        """
        from sqlalchemy import text

        # Get current head
        result = await db_session.execute(
            text("SELECT object_hash FROM mica.mutable_head WHERE head_name = :head"),
            {"head": head_name},
        )
        current = result.scalar_one_or_none()
        if current is None:
            return []

        chain = []
        for _ in range(max_depth):
            result = await db_session.execute(
                text("""
                    SELECT root_hash, root_type, tick_id, status,
                           child_count, previous_root_hash, labels, created_at
                    FROM mica.root_record
                    WHERE root_hash = :hash
                """),
                {"hash": current},
            )
            row = result.fetchone()
            if row is None:
                break

            chain.append({
                "root_hash": row[0].hex() if isinstance(row[0], (bytes, memoryview)) else str(row[0]),
                "root_type": row[1],
                "tick_id": row[2],
                "status": row[3],
                "child_count": row[4],
                "previous_root_hash": row[5].hex() if row[5] else None,
                "labels": row[6],
                "created_at": row[7].isoformat() if row[7] else None,
            })

            if row[5] is None:  # No previous root
                break
            current = row[5]

        return chain


# ---------------------------------------------------------------------------
# Singleton accessor
# ---------------------------------------------------------------------------

_mica: Optional[MICABridge] = None


def get_mica_bridge() -> MICABridge:
    """Get the shared MICABridge singleton."""
    global _mica
    if _mica is None:
        _mica = MICABridge()
    return _mica
