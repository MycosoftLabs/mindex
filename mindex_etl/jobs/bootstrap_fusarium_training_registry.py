from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from mindex_api.config import settings
from mindex_api.routers.fusarium_catalog import FUSARIUM_ENVIRONMENTS, FUSARIUM_SILOS
from mindex_api.utils.fusarium_training_doc_parser import parse_training_sources


async def bootstrap_registry(doc_path: str) -> int:
    engine = create_async_engine(settings.mindex_db_dsn, future=True, echo=False)
    async with engine.begin() as conn:
        for silo in FUSARIUM_SILOS:
            await conn.execute(
                text(
                    """
                    INSERT INTO fusarium_catalog.modality_silo (silo_id, name, description, source_categories)
                    VALUES (:silo_id, :name, :description, CAST(:source_categories AS jsonb))
                    ON CONFLICT (silo_id) DO UPDATE SET
                        name = EXCLUDED.name,
                        description = EXCLUDED.description,
                        source_categories = EXCLUDED.source_categories
                    """
                ),
                {**silo, "source_categories": json.dumps([])},
            )

        for env in FUSARIUM_ENVIRONMENTS:
            await conn.execute(
                text(
                    """
                    INSERT INTO fusarium_env.environment_domain (env_id, axis_type, name, description, parent_id)
                    VALUES (:env_id, :axis_type, :name, :description, :parent_id)
                    ON CONFLICT (env_id) DO UPDATE SET
                        axis_type = EXCLUDED.axis_type,
                        name = EXCLUDED.name,
                        description = EXCLUDED.description,
                        parent_id = EXCLUDED.parent_id
                    """
                ),
                env,
            )

        sources = parse_training_sources(Path(doc_path))
        for source in sources:
            await conn.execute(
                text(
                    """
                    INSERT INTO fusarium_catalog.dataset_source (
                        dataset_id, name, section_category, source_url, source_urls, dataset_type,
                        file_format, size_estimate, access_level, nlm_target, repo_targets,
                        priority, modality_silo, description, environment_domain_tags,
                        storage_uri, checksum, ingest_status, raw_metadata
                    ) VALUES (
                        :dataset_id, :name, :section_category, :source_url, CAST(:source_urls AS jsonb), :dataset_type,
                        :file_format, :size_estimate, :access_level, :nlm_target, CAST(:repo_targets AS jsonb),
                        :priority, :modality_silo, :description, CAST(:environment_domain_tags AS jsonb),
                        :storage_uri, :checksum, :ingest_status, CAST(:raw_metadata AS jsonb)
                    )
                    ON CONFLICT (dataset_id) DO UPDATE SET
                        name = EXCLUDED.name,
                        section_category = EXCLUDED.section_category,
                        source_url = EXCLUDED.source_url,
                        source_urls = EXCLUDED.source_urls,
                        dataset_type = EXCLUDED.dataset_type,
                        file_format = EXCLUDED.file_format,
                        size_estimate = EXCLUDED.size_estimate,
                        access_level = EXCLUDED.access_level,
                        nlm_target = EXCLUDED.nlm_target,
                        repo_targets = EXCLUDED.repo_targets,
                        priority = EXCLUDED.priority,
                        modality_silo = EXCLUDED.modality_silo,
                        description = EXCLUDED.description,
                        environment_domain_tags = EXCLUDED.environment_domain_tags,
                        storage_uri = EXCLUDED.storage_uri,
                        checksum = EXCLUDED.checksum,
                        ingest_status = EXCLUDED.ingest_status,
                        raw_metadata = EXCLUDED.raw_metadata,
                        updated_at = NOW()
                    """
                ),
                {
                    "dataset_id": source.get("dataset_id"),
                    "name": source.get("name"),
                    "section_category": source.get("section_category"),
                    "source_url": source.get("source_url"),
                    "dataset_type": source.get("dataset_type"),
                    "file_format": source.get("file_format"),
                    "size_estimate": source.get("size_estimate"),
                    "access_level": source.get("access_level"),
                    "nlm_target": source.get("nlm_target"),
                    "priority": source.get("priority"),
                    "modality_silo": source.get("modality_silo"),
                    "source_urls": json.dumps(source.get("source_urls", [])),
                    "repo_targets": json.dumps(source.get("repo_targets", [])),
                    "environment_domain_tags": json.dumps(source.get("environment_domain_tags", [])),
                    "description": source.get("dataset_type"),
                    "storage_uri": source.get("storage_uri"),
                    "checksum": source.get("checksum"),
                    "ingest_status": source.get("ingest_status", "cataloged"),
                    "raw_metadata": json.dumps(source.get("raw_metadata", {})),
                },
            )

    await engine.dispose()
    return len(sources)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--doc-path",
        default=str(Path(__file__).resolve().parents[2] / "docs" / "NLM_TRAINING_DATA_SOURCES.md"),
    )
    args = parser.parse_args()
    count = asyncio.run(bootstrap_registry(args.doc_path))
    print(f"Bootstrapped {count} Fusarium training sources")


if __name__ == "__main__":
    main()
