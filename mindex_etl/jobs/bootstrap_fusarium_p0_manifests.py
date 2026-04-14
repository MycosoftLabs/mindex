from __future__ import annotations

import asyncio
import json

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from mindex_api.config import settings
from mindex_api.storage import get_storage


async def bootstrap_p0_manifests() -> int:
    storage = get_storage()
    storage.ensure_dirs()
    engine = create_async_engine(settings.mindex_db_dsn, future=True, echo=False)
    created = 0
    async with engine.begin() as conn:
        result = await conn.execute(
            text(
                """
                SELECT dataset_id, modality_silo
                FROM fusarium_catalog.dataset_source
                WHERE priority = 'P0'
                ORDER BY dataset_id
                """
            )
        )
        rows = result.mappings().all()
        for row in rows:
            storage_uri = str(storage.nas_training / "fusarium" / row["modality_silo"] / row["dataset_id"])
            await conn.execute(
                text(
                    """
                    INSERT INTO fusarium_training.dataset_manifest (
                        dataset_id, split_name, record_count, storage_uri, checksum, manifest_metadata
                    ) VALUES (
                        :dataset_id, 'full_corpus', 0, :storage_uri, NULL, CAST(:manifest_metadata AS jsonb)
                    )
                    """
                ),
                {
                    "dataset_id": row["dataset_id"],
                    "storage_uri": storage_uri,
                    "manifest_metadata": json.dumps(
                        {
                            "bootstrap": True,
                            "priority": "P0",
                            "ingest_mode": "full_bulk_expected",
                        }
                    ),
                },
            )
            created += 1
    await engine.dispose()
    return created


def main() -> None:
    created = asyncio.run(bootstrap_p0_manifests())
    print(f"Bootstrapped {created} P0 Fusarium manifests")


if __name__ == "__main__":
    main()
