"""
ETL Checkpoint System
=====================
Saves and restores sync progress to allow resuming after interruptions.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional

CHECKPOINT_DIR = Path("/tmp/mindex_etl_checkpoints")


class CheckpointManager:
    """Manages ETL sync checkpoints for resumable syncs."""

    def __init__(self, job_name: str):
        self.job_name = job_name
        self.checkpoint_file = CHECKPOINT_DIR / f"{job_name}.json"
        CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)

    def save(self, page: int, **metadata) -> None:
        """Write legacy diagnostic state, deliberately not resumable.

        Commit-aware jobs use save_committed only after conn.commit returns.
        """
        checkpoint = {
            "job_name": self.job_name,
            "page": page,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "metadata": metadata,
        }
        self._replace(checkpoint)

    def _replace(self, checkpoint: Dict) -> None:
        """Same-directory atomic replacement after file fsync; one writer required.

        This is not a distributed lock or a whole-filesystem power-loss guarantee.
        """
        data = json.dumps(checkpoint, indent=2, allow_nan=False)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.checkpoint_file.parent,
                                             prefix=self.checkpoint_file.name + ".", suffix=".tmp",
                                             delete=False) as handle:
                temporary = Path(handle.name)
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.checkpoint_file)
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink()

    @staticmethod
    def _query_fingerprint(query: Dict) -> str:
        encoded = json.dumps(query, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def save_committed(self, page: int, *, query: Dict, **metadata) -> None:
        """Publish an actual fetched page only after its database commit succeeds."""
        if type(page) is not int or page < 1:
            raise ValueError("Committed checkpoint page must be a positive integer")
        self._replace({"schema_version": 2, "committed": True, "job_name": self.job_name,
                       "page": page, "query_fingerprint": self._query_fingerprint(query),
                       "timestamp": datetime.now(timezone.utc).isoformat(), "metadata": metadata})

    def load(self) -> Optional[Dict]:
        """Load validated committed state; never silently discard corrupt state."""
        if not self.checkpoint_file.exists():
            return None
        try:
            with open(self.checkpoint_file, "r", encoding="utf-8") as f:
                checkpoint = json.load(f)
        except (ValueError, UnicodeError) as exc:
            raise ValueError("Unreadable checkpoint; inspect it and explicitly restart from page 1") from exc
        if (not isinstance(checkpoint, dict) or type(checkpoint.get("schema_version")) is not int
                or checkpoint.get("schema_version") != 2 or checkpoint.get("committed") is not True
                or checkpoint.get("job_name") != self.job_name
                or type(checkpoint.get("page")) is not int or checkpoint["page"] < 1
                or not isinstance(checkpoint.get("query_fingerprint"), str)
                or len(checkpoint["query_fingerprint"]) != 64
                or any(c not in "0123456789abcdef" for c in checkpoint["query_fingerprint"])):
            raise ValueError("Untrusted or legacy checkpoint; inspect it and explicitly restart from page 1")
        return checkpoint

    def resume_page(self, *, query: Dict, start_page: int = 1) -> int:
        """Bind resume to the effective query; max_pages may be extended."""
        if type(start_page) is not int or start_page < 1:
            raise ValueError("start_page must be a positive integer")
        checkpoint = self.load()
        if checkpoint is None:
            return start_page
        if checkpoint["query_fingerprint"] != self._query_fingerprint(query):
            raise ValueError("Checkpoint query differs; use matching arguments or explicitly restart")
        next_page = checkpoint["page"] + 1
        if start_page not in (1, next_page):
            raise ValueError("start_page conflicts with the last committed checkpoint")
        return next_page

    def get_last_page(self) -> Optional[int]:
        """Get the last successfully processed page."""
        checkpoint = self.load()
        return checkpoint.get("page") if checkpoint else None

    def clear(self) -> None:
        """Clear the checkpoint."""
        if self.checkpoint_file.exists():
            self.checkpoint_file.unlink()

    def exists(self) -> bool:
        """Check if checkpoint exists."""
        return self.checkpoint_file.exists()


def resume_from_checkpoint(
    job_name: str,
    sync_func,
    checkpoint_manager: Optional[CheckpointManager] = None,
    **sync_kwargs,
) -> int:
    """
    Resume a sync job from the last checkpoint.
    
    Args:
        job_name: Name of the job
        sync_func: Function that takes start_page and max_pages
        checkpoint_manager: Optional checkpoint manager (creates one if not provided)
    
    Returns:
        Total records processed
    """
    if checkpoint_manager is None:
        checkpoint_manager = CheckpointManager(job_name)

    # The selected job knows the effective query and validates the saved cursor.
    return sync_func(checkpoint_manager=checkpoint_manager, **sync_kwargs)
