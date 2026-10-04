"""Opt-in integration test for a disposable MINDEX PostgreSQL fixture.

Set MINDEX_RESEARCH_IDENTITY_EXPORT_TEST_DSN only to a task-owned private test
database whose name begins with `mindex_identity_export_test_` and whose server
is loopback. This test inserts and removes one synthetic row pair.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
from uuid import uuid4

import pytest

from mindex_etl.research_identity_export import export_identity_snapshot


DSN = os.environ.get("MINDEX_RESEARCH_IDENTITY_EXPORT_TEST_DSN")
SOURCE_ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(not DSN, reason="private PostgreSQL DSN was not supplied")


def test_exact_declared_postgres_rows_export_read_only_stored_identity():
    import psycopg
    from psycopg.conninfo import conninfo_to_dict

    conninfo = conninfo_to_dict(DSN)
    host = conninfo.get("host")
    port = int(conninfo.get("port", "5432"))
    database = conninfo.get("dbname", "")
    assert host in {"127.0.0.1", "localhost", "::1"}
    assert 55400 <= port <= 55500
    assert database.startswith("mindex_identity_export_test_")
    connection = psycopg.connect(DSN)

    taxon_id = uuid4()
    accession = f"ZZTEST{taxon_id.hex[:10].upper()}"
    version = f"{accession}.1"
    sequence = "acgt\nACGT "
    sequence_hash = hashlib.sha256(sequence.encode("utf-8")).hexdigest()
    metadata = {"taxon_linkage": {"state": "linked_unique_exact_external_id"},
                "source_taxon_ids": ["999999999"]}
    sequence_row_id = None
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """INSERT INTO core.taxon (id, canonical_name, rank, source, metadata)
                   VALUES (%s, %s, 'species', 'test_fixture', '{}'::jsonb)""",
                (taxon_id, f"Fixture fungus {taxon_id.hex[:8]}")
            )
            cursor.execute(
                """INSERT INTO bio.genetic_sequence (
                       accession, taxon_id, species_name, gene, region, sequence,
                       sequence_length, sequence_type, source, source_url, version, metadata
                   ) VALUES (%s, %s, 'fixture fungus', 'ITS', 'ITS1', %s,
                             %s, 'dna', 'genbank', %s, %s, %s::jsonb)
                   RETURNING id""",
                (accession, taxon_id, sequence, len(sequence),
                 f"https://www.ncbi.nlm.nih.gov/nuccore/{version}", version,
                 json.dumps(metadata))
            )
            sequence_row_id = cursor.fetchone()[0]
        connection.commit()
    finally:
        connection.close()

    try:
        with psycopg.connect(DSN) as readonly_connection:
            export = export_identity_snapshot(
                readonly_connection,
                producer_commit=subprocess.check_output(
                    ["git", "rev-parse", "HEAD"], cwd=SOURCE_ROOT, text=True,
                ).strip(),
                accessions=[accession],
            )
        assert export["status"] == "available"
        assert export["authority"]["transaction_read_only"] is True
        assert export["authority"]["transaction_isolation"] == "repeatable read"
        assert export["records"] == [{
            "resource": "its",
            "sequence_row_id": sequence_row_id,
            "accession": accession,
            "version": version,
            "provider": "genbank",
            "molecule": "dna",
            "gene": "ITS",
            "region": "ITS1",
            "marker_mapping": "its",
            "source_url": f"https://www.ncbi.nlm.nih.gov/nuccore/{version}",
            "sequence_sha256": sequence_hash,
            "sequence_hash_scope": "exact_stored_sequence_utf8_bytes",
            "sequence_utf8_bytes": len(sequence.encode("utf-8")),
            "declared_sequence_sha256": [],
            "sequence_hash_state": "stored_digest_only",
            "canonical_taxon_id": str(taxon_id),
            "source_taxon_ids": ["999999999"],
            "taxon_association_state": "stored_fk",
            "taxon_linkage_provenance_state": "linked_unique_exact_external_id",
            "eligible_for_exact_identity_join": True,
            "diagnostic_codes": [],
        }]
    finally:
        with psycopg.connect(DSN) as cleanup_connection:
            with cleanup_connection.cursor() as cursor:
                cursor.execute("DELETE FROM bio.genetic_sequence WHERE id = %s", (sequence_row_id,))
                cursor.execute("DELETE FROM core.taxon WHERE id = %s", (taxon_id,))
