"""
ETL Checkpoint System
=====================
Saves and restores sync progress to allow resuming after interruptions.
"""
from __future__ import annotations

import json
import os
from datetime import datetime
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
        """Save checkpoint with current page and metadata."""
        checkpoint = {
            "job_name": self.job_name,
            "page": page,
            "timestamp": datetime.utcnow().isoformat(),
            "metadata": metadata,
        }
        with open(self.checkpoint_file, "w") as f:
            json.dump(checkpoint, f, indent=2)

    def load(self) -> Optional[Dict]:
        """Load checkpoint if it exists."""
        if not self.checkpoint_file.exists():
            return None
        try:
            with open(self.checkpoint_file, "r") as f:
                return json.load(f)
        except Exception:
            return None

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

    checkpoint = checkpoint_manager.load()
    start_page = checkpoint.get("page", 1) + 1 if checkpoint else 1

    if checkpoint:
        print(f"Resuming {job_name} from page {start_page} (last checkpoint: page {checkpoint['page']})")
    else:
        print(f"Starting {job_name} from page 1")

    return sync_func(start_page=start_page, checkpoint_manager=checkpoint_manager)
