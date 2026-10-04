"""Read-only v2 snapshots of stored genetic-sequence identities.

This exports database row identity and stored provenance. It does not identify
species, fetch providers, or infer an ITS marker from the provider name.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
from typing import Any
from uuid import UUID, uuid4


SCHEMA = "research.identity-export.v2"
MAX_LIMIT = 5000
MAX_ACCESSIONS = 500
MAX_TOTAL_SEQUENCE_UTF8_BYTES = 50_000_000
DEFAULT_TTL_SECONDS = 600
MIN_TTL_SECONDS = 30
MAX_TTL_SECONDS = 3600

AUTHORITY_SQL = """
SELECT current_database() AS database_name,
       system_identifier::text AS system_identifier,
       transaction_timestamp() AS captured_at,
       current_setting('transaction_read_only') AS transaction_read_only,
       current_setting('transaction_isolation') AS transaction_isolation
FROM pg_control_system()
"""

SCHEMA_SQL = """
WITH expected(schema_name, table_name, column_name, udt_name) AS (
    VALUES
        ('bio', 'genetic_sequence', 'id', 'int4'),
        ('bio', 'genetic_sequence', 'accession', 'varchar'),
        ('bio', 'genetic_sequence', 'version', 'varchar'),
        ('bio', 'genetic_sequence', 'taxon_id', 'uuid'),
        ('bio', 'genetic_sequence', 'gene', 'varchar'),
        ('bio', 'genetic_sequence', 'region', 'varchar'),
        ('bio', 'genetic_sequence', 'sequence', 'text'),
        ('bio', 'genetic_sequence', 'sequence_type', 'varchar'),
        ('bio', 'genetic_sequence', 'source', 'varchar'),
        ('bio', 'genetic_sequence', 'source_url', 'text'),
        ('bio', 'genetic_sequence', 'metadata', 'jsonb'),
        ('core', 'taxon', 'id', 'uuid')
)
SELECT e.schema_name, e.table_name, e.column_name,
       c.udt_name AS actual_udt_name
FROM expected AS e
LEFT JOIN information_schema.columns AS c
  ON c.table_schema = e.schema_name
 AND c.table_name = e.table_name
 AND c.column_name = e.column_name
ORDER BY e.schema_name, e.table_name, e.column_name
"""

ROWS_SQL = """
WITH requested(accessions) AS (SELECT %s::text[])
 , bounded AS (
    SELECT gs.id, gs.accession, gs.version, gs.taxon_id,
           (t.id IS NOT NULL) AS taxon_exists,
           gs.gene, gs.region, gs.sequence_type, gs.source, gs.source_url,
           gs.sequence,
           octet_length(convert_to(gs.sequence, 'UTF8')) AS sequence_utf8_bytes,
           gs.metadata
    FROM bio.genetic_sequence AS gs
    LEFT JOIN core.taxon AS t ON t.id = gs.taxon_id
    CROSS JOIN requested
    WHERE requested.accessions IS NULL
       OR gs.accession = ANY(requested.accessions)
       OR gs.version = ANY(requested.accessions)
    ORDER BY gs.id
    LIMIT %s
 ), measured AS (
    SELECT *, sum(sequence_utf8_bytes) OVER (ORDER BY id ROWS UNBOUNDED PRECEDING)
                 AS cumulative_sequence_utf8_bytes
    FROM bounded
 )
SELECT id, accession, version, taxon_id, taxon_exists, gene, region, sequence_type,
       source, source_url,
       CASE WHEN cumulative_sequence_utf8_bytes <= %s
            THEN encode(digest(convert_to(sequence, 'UTF8'), 'sha256'), 'hex')
            ELSE NULL END AS sequence_sha256,
       sequence_utf8_bytes, cumulative_sequence_utf8_bytes, metadata
FROM measured
ORDER BY id
"""

_ACCESSION_RE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,79}\Z")
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_COMMIT_RE = re.compile(r"[0-9a-f]{40,64}\Z")
_ITS_MARKERS = {"its", "its1", "its2"}
_DNA_PROVIDERS = {"genbank", "ncbi", "refseq", "bold", "unite"}
_MOLECULES = {"dna", "rna", "protein"}
_REQUIRED_SCHEMA_TYPES = {
    ("bio", "genetic_sequence", "id"): "int4",
    ("bio", "genetic_sequence", "accession"): "varchar",
    ("bio", "genetic_sequence", "version"): "varchar",
    ("bio", "genetic_sequence", "taxon_id"): "uuid",
    ("bio", "genetic_sequence", "gene"): "varchar",
    ("bio", "genetic_sequence", "region"): "varchar",
    ("bio", "genetic_sequence", "sequence"): "text",
    ("bio", "genetic_sequence", "sequence_type"): "varchar",
    ("bio", "genetic_sequence", "source"): "varchar",
    ("bio", "genetic_sequence", "source_url"): "text",
    ("bio", "genetic_sequence", "metadata"): "jsonb",
    ("core", "taxon", "id"): "uuid",
}


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _cursor_mapping(cursor, row: Any) -> dict[str, Any]:
    if isinstance(row, Mapping):
        return dict(row)
    description = cursor.description or ()
    names = [column.name if hasattr(column, "name") else column[0] for column in description]
    if len(names) != len(row):
        raise ValueError("database row does not match the fixed projection")
    return dict(zip(names, row, strict=True))


def _canonical_uuid(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, str):
        parsed = UUID(value)
        if str(parsed) == value:
            return value
    raise ValueError("database UUID has an unexpected representation")


def _version_evidence(
    provider: Any,
    accession: str,
    version: Any,
    sequence_sha256: Any,
    metadata: Any,
) -> tuple[str, str | None, dict[str, Any] | None, str | None]:
    """Validate versions only against evidence in the provider's namespace."""
    if not isinstance(provider, str):
        return "version_namespace_unsupported", None, None, "version_namespace_unsupported"
    provider = provider.casefold()
    if provider not in {"genbank", "ncbi", "refseq", "uniprot"}:
        # Do not apply a different provider's accession syntax, even when a
        # stored value happens to look versioned or equals the accession.
        return "version_namespace_unsupported", None, None, "version_namespace_unsupported"
    if not isinstance(version, str) or not version:
        return "version_missing", None, None, "version_missing"
    if not _ACCESSION_RE.fullmatch(version):
        return "version_invalid", None, None, "version_invalid"

    if provider in {"genbank", "ncbi", "refseq"}:
        base, separator, suffix = version.rpartition(".")
        exact = (
            bool(separator and base and suffix.isdigit())
            and (version == accession or (base == accession and version.startswith(accession + ".")))
        )
        if exact:
            shape = "fully_versioned_accession" if version == accession else "accession_plus_version"
            return "exact", "ncbi.accession_version", {
                "stored_accession": accession, "stored_version": version, "shape": shape,
            }, None
        if version == accession:
            return "version_unversioned", "ncbi.accession_version", {
                "stored_accession": accession, "stored_version": version,
            }, "version_unversioned"
        return "version_conflict", "ncbi.accession_version", {
            "stored_accession": accession, "stored_version": version,
        }, "version_conflict"

    if provider == "uniprot":
        namespace = "uniprot.sequenceVersion"
        if version == accession:
            return "version_unversioned", namespace, {
                "stored_accession": accession, "stored_version": version,
            }, "version_unversioned"
        capture = metadata.get("uniprot_source_record") if isinstance(metadata, Mapping) else None
        if not isinstance(capture, Mapping):
            return "version_source_evidence_unavailable", namespace, None, "version_source_evidence_unavailable"
        audit = capture.get("entryAudit")
        sequence = capture.get("sequence")
        primary_accession = capture.get("primaryAccession")
        sequence_version = audit.get("sequenceVersion") if isinstance(audit, Mapping) else None
        entry_version = audit.get("entryVersion") if isinstance(audit, Mapping) else None
        sequence_value = sequence.get("value") if isinstance(sequence, Mapping) else None
        evidence: dict[str, Any] = {
            "stored_accession": accession,
            "stored_version": version,
            "source_primary_accession": primary_accession,
            "source_sequence_version": sequence_version,
            "source_entry_version": entry_version,
        }
        if not isinstance(primary_accession, str) or primary_accession != accession:
            return "version_source_identity_conflict", namespace, evidence, "version_source_identity_conflict"
        if not isinstance(sequence_value, str) or not isinstance(sequence_sha256, str) or not _SHA256_RE.fullmatch(sequence_sha256):
            return "version_source_evidence_unavailable", namespace, evidence, "version_source_evidence_unavailable"
        source_sequence_sha256 = _sha256(sequence_value.encode("utf-8"))
        evidence["source_sequence_sha256"] = source_sequence_sha256
        if source_sequence_sha256 != sequence_sha256:
            return "source_sequence_hash_conflict", namespace, evidence, "source_sequence_hash_conflict"
        if type(sequence_version) is not int or str(sequence_version) != version:
            return "version_conflict", namespace, evidence, "version_conflict"
        return "exact", namespace, evidence, None

    raise AssertionError("recognized provider version policy was not handled")


def _resource_mapping(row: Mapping[str, Any]) -> tuple[str | None, str | None, str | None]:
    """Return (resource, marker, diagnostic); provider and marker are explicit."""
    source = row.get("source")
    molecule = row.get("sequence_type")
    if not isinstance(source, str) or not source.strip():
        return None, None, "provider_missing"
    if not isinstance(molecule, str) or molecule not in _MOLECULES:
        return None, None, "molecule_unmapped"
    provider = source.casefold()
    if provider == "uniprot" and molecule == "protein":
        return "uniprot", "protein", None
    if provider == "ensembl" and molecule in {"dna", "protein"}:
        return "ensembl", molecule, None
    if provider not in _DNA_PROVIDERS or molecule != "dna":
        return None, None, "provider_molecule_unmapped"

    markers = {
        str(row.get(field)).strip().casefold()
        for field in ("gene", "region")
        if row.get(field) is not None and str(row.get(field)).strip()
    }
    if not markers:
        return None, None, "marker_missing"
    if not markers.issubset(_ITS_MARKERS):
        return None, None, "marker_unmapped_or_conflicting"
    return "its", "its", None


def _declared_sequence_hashes(metadata: Any) -> list[Any]:
    if not isinstance(metadata, Mapping):
        return []
    values: list[Any] = [metadata.get("sequence_sha256")]
    capture = metadata.get("fungip_capture")
    if isinstance(capture, Mapping):
        values.append(capture.get("sequence_sha256"))
        original = capture.get("original_dna")
        if isinstance(original, Mapping):
            values.append(original.get("sha256"))
    unique: list[Any] = []
    for value in values:
        if value is not None and value not in unique:
            unique.append(value)
    return unique


def _metadata_linkage_state(metadata: Any, taxon_id: str | None) -> str | None:
    if not isinstance(metadata, Mapping):
        return None
    linkage = metadata.get("taxon_linkage")
    if isinstance(linkage, Mapping) and isinstance(linkage.get("state"), str):
        return linkage["state"]
    capture = metadata.get("fungip_capture")
    if isinstance(capture, Mapping):
        if capture.get("link_policy") == "strict_current_resolver_and_kingdom":
            return (
                "fungip_strict_current_resolver"
                if capture.get("candidate_taxon_id") == taxon_id
                else "fungip_candidate_taxon_conflict"
            )
        if capture.get("link_policy") == "canonical_pending":
            return "canonical_pending"
    return None


def _source_taxon_ids(metadata: Any) -> list[Any]:
    if not isinstance(metadata, Mapping):
        return []
    linkage = metadata.get("taxon_linkage")
    if isinstance(linkage, Mapping) and isinstance(linkage.get("source_ids"), list):
        return list(linkage["source_ids"])
    values = metadata.get("source_taxon_ids")
    return list(values) if isinstance(values, list) else []


def _make_record(row: Mapping[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    row_id = row.get("id")
    if type(row_id) is not int or not 1 <= row_id <= 2**31 - 1:
        raise ValueError("database sequence id is not an int4 identity")
    accession = row.get("accession")
    if not isinstance(accession, str) or not _ACCESSION_RE.fullmatch(accession):
        raise ValueError("database accession has an unexpected representation")
    taxon_id = _canonical_uuid(row.get("taxon_id"))
    metadata = row.get("metadata")
    sequence_sha256 = row.get("sequence_sha256")
    sequence_utf8_bytes = row.get("sequence_utf8_bytes")
    if type(sequence_utf8_bytes) is not int or sequence_utf8_bytes < 0:
        raise ValueError("database sequence byte count has an unexpected representation")

    resource, marker, mapping_error = _resource_mapping(row)
    diagnostics: list[dict[str, Any]] = []

    def add(code: str) -> None:
        diagnostics.append({"code": code, "sequence_row_id": row_id, "accession": accession})

    version_state, version_namespace, version_evidence, version_diagnostic = _version_evidence(
        row.get("source"), accession, row.get("version"), sequence_sha256, metadata,
    )
    if version_diagnostic:
        add(version_diagnostic)
    if mapping_error:
        add(mapping_error)

    if row.get("taxon_id") is None:
        taxon_state = "unlinked"
        add("taxon_link_absent")
    elif row.get("taxon_exists") is not True:
        taxon_state = "orphaned"
        add("taxon_fk_target_absent")
    else:
        taxon_state = "stored_fk"

    if sequence_utf8_bytes > MAX_TOTAL_SEQUENCE_UTF8_BYTES or sequence_sha256 is None:
        hash_state = "omitted_export_byte_limit"
        add("sequence_export_byte_limit")
        sequence_sha256 = None
    elif not isinstance(sequence_sha256, str) or not _SHA256_RE.fullmatch(sequence_sha256):
        raise ValueError("database sequence digest has an unexpected representation")
    else:
        hash_state = None

    linkage_provenance_state = _metadata_linkage_state(metadata, taxon_id)
    linkage_provenance_supported = linkage_provenance_state in {
        "linked_unique_exact_external_id", "fungip_strict_current_resolver",
    }
    if linkage_provenance_state == "ambiguous_exact_external_id":
        add("taxon_link_ambiguous")
    elif linkage_provenance_state in {"source_name_mismatch", "fungip_candidate_taxon_conflict"}:
        add("taxon_link_conflict")
    elif not linkage_provenance_supported:
        add("taxon_link_provenance_unverified")

    declared_hashes = _declared_sequence_hashes(metadata)
    invalid_declared_hash = any(not isinstance(value, str) or not _SHA256_RE.fullmatch(value) for value in declared_hashes)
    if invalid_declared_hash:
        hash_state = "conflict"
        add("sequence_hash_conflict")
    elif hash_state == "omitted_export_byte_limit":
        pass
    elif any(value != sequence_sha256 for value in declared_hashes):
        hash_state = "conflict"
        add("sequence_hash_conflict")
    elif declared_hashes:
        hash_state = "stored_digest_matches_declared"
    else:
        hash_state = "stored_digest_only"

    record = {
        "resource": resource,
        "sequence_row_id": row_id,
        "accession": accession,
        "version": row.get("version"),
        "version_state": version_state,
        "version_namespace": version_namespace,
        "version_evidence": version_evidence,
        "provider": row.get("source"),
        "molecule": row.get("sequence_type"),
        "gene": row.get("gene"),
        "region": row.get("region"),
        "marker_mapping": marker,
        "source_url": row.get("source_url"),
        "sequence_sha256": sequence_sha256,
        "sequence_hash_scope": "exact_stored_sequence_utf8_bytes",
        "sequence_utf8_bytes": sequence_utf8_bytes,
        "declared_sequence_sha256": declared_hashes,
        "sequence_hash_state": hash_state,
        "canonical_taxon_id": taxon_id,
        "source_taxon_ids": _source_taxon_ids(metadata),
        "taxon_association_state": taxon_state,
        "taxon_linkage_provenance_state": linkage_provenance_state,
        "eligible_for_exact_identity_join": bool(
            resource and version_state == "exact" and taxon_state == "stored_fk"
            and linkage_provenance_supported
            and hash_state not in {"conflict", "omitted_export_byte_limit"} and not mapping_error
        ),
        "diagnostic_codes": [item["code"] for item in diagnostics],
    }
    return record, diagnostics


def _query_hash() -> str:
    return _sha256((AUTHORITY_SQL + "\0" + SCHEMA_SQL + "\0" + ROWS_SQL).encode("utf-8"))


def _connection_is_idle(connection) -> bool:
    info = getattr(connection, "info", None)
    status = getattr(info, "transaction_status", None)
    if status is not None:
        name = getattr(status, "name", None)
        return name.upper() == "IDLE" if isinstance(name, str) else status == 0
    get_status = getattr(connection, "get_transaction_status", None)
    if callable(get_status):
        return get_status() == 0
    return False


def _producer_hash() -> str:
    return _sha256(Path(__file__).read_bytes())


def _base_export(producer_commit: str) -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "id": str(uuid4()),
        "status": "unavailable",
        "authority": None,
        "producer": {
            "name": "MINDEX",
            "source_commit": producer_commit,
            "source_file_sha256": _producer_hash(),
            "query_sha256": _query_hash(),
        },
        "records": [],
        "diagnostics": [],
    }


def _seal(export: dict[str, Any]) -> dict[str, Any]:
    export["export_sha256"] = _sha256(_canonical_json(export))
    return export


def _fetch_all(cursor) -> list[dict[str, Any]]:
    rows = cursor.fetchall()
    return [_cursor_mapping(cursor, row) for row in rows]


def export_identity_snapshot(
    connection,
    *,
    producer_commit: str,
    accessions: Iterable[str] | None = None,
    limit: int = 1000,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
) -> dict[str, Any]:
    """Create a bounded v2 export from a fresh idle psycopg DB-API connection.

    The function opens one repeatable-read, read-only transaction and always
    rolls it back. It never executes caller-supplied SQL or contacts providers.
    """
    if not isinstance(producer_commit, str) or not _COMMIT_RE.fullmatch(producer_commit):
        raise ValueError("producer_commit must be a lowercase source commit hash")
    if type(limit) is not int or not 1 <= limit <= MAX_LIMIT:
        raise ValueError(f"limit must be between 1 and {MAX_LIMIT}")
    if type(ttl_seconds) is not int or not MIN_TTL_SECONDS <= ttl_seconds <= MAX_TTL_SECONDS:
        raise ValueError(f"ttl_seconds must be between {MIN_TTL_SECONDS} and {MAX_TTL_SECONDS}")
    accession_filter: list[str] | None
    if accessions is None:
        accession_filter = None
    else:
        if isinstance(accessions, (str, bytes)):
            raise ValueError("accessions must be an iterable of complete accession values")
        supplied_accessions = list(accessions)
        if not supplied_accessions or len(supplied_accessions) > MAX_ACCESSIONS or any(
            not isinstance(value, str) or not _ACCESSION_RE.fullmatch(value)
            for value in supplied_accessions
        ):
            raise ValueError("accession filter is invalid or exceeds its bound")
        accession_filter = list(dict.fromkeys(supplied_accessions))

    export = _base_export(producer_commit)
    cursor = None
    transaction_started = False
    try:
        if not _connection_is_idle(connection):
            export["diagnostics"].append({"code": "fresh_idle_connection_required"})
            return _seal(export)
        cursor = connection.cursor()
        transaction_started = True
        cursor.execute("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY")
        cursor.execute(AUTHORITY_SQL)
        authority_row = _cursor_mapping(cursor, cursor.fetchone())
        database_name = authority_row["database_name"]
        system_identifier = authority_row["system_identifier"]
        captured_at = authority_row["captured_at"]
        read_only = authority_row["transaction_read_only"]
        isolation = authority_row["transaction_isolation"]
        if read_only != "on" or isolation not in {"repeatable read", "serializable"}:
            export["diagnostics"].append({"code": "readonly_repeatable_snapshot_required"})
            return _seal(export)
        if not isinstance(database_name, str) or not database_name or not str(system_identifier).isdigit():
            export["diagnostics"].append({"code": "authority_instance_identity_unavailable"})
            return _seal(export)
        if not isinstance(captured_at, datetime) or captured_at.tzinfo is None:
            export["diagnostics"].append({"code": "authority_capture_time_unavailable"})
            return _seal(export)

        expires_at = captured_at + timedelta(seconds=ttl_seconds)
        export["authority"] = {
            "kind": "mindex.postgresql.readonly-snapshot.v2",
            "instance": {
                "postgres_system_identifier": str(system_identifier),
                "database": database_name,
            },
            "receipt_id": export["id"],
            "captured_at": captured_at.astimezone(timezone.utc).isoformat(),
            "expires_at": expires_at.astimezone(timezone.utc).isoformat(),
            "transaction_read_only": True,
            "transaction_isolation": isolation,
        }

        cursor.execute(SCHEMA_SQL)
        schema_rows = _fetch_all(cursor)
        schema_issues = []
        for row in schema_rows:
            key = (row["schema_name"], row["table_name"], row["column_name"])
            if row["actual_udt_name"] != _REQUIRED_SCHEMA_TYPES[key]:
                schema_issues.append(".".join(key))
        if schema_issues:
            export["diagnostics"].append({
                "code": "required_schema_incompatible",
                "columns": schema_issues,
            })
            return _seal(export)

        cursor.execute(ROWS_SQL, (accession_filter, limit + 1, MAX_TOTAL_SEQUENCE_UTF8_BYTES))
        rows = _fetch_all(cursor)
        truncated = len(rows) > limit
        if truncated:
            rows = rows[:limit]
        for row in rows:
            try:
                record, diagnostics = _make_record(row)
            except (TypeError, ValueError):
                item = {"code": "sequence_row_projection_invalid"}
                if type(row.get("id")) is int:
                    item["sequence_row_id"] = row["id"]
                if isinstance(row.get("accession"), str):
                    item["accession"] = row["accession"]
                export["diagnostics"].append(item)
                continue
            export["records"].append(record)
            export["diagnostics"].extend(diagnostics)
        identity_rows: dict[tuple[str, str, str, str], list[dict[str, Any]]] = {}
        for record in export["records"]:
            key_values = (
                record.get("resource"), record.get("accession"),
                record.get("version"), record.get("sequence_sha256"),
            )
            if all(isinstance(value, str) and value for value in key_values):
                identity_rows.setdefault(key_values, []).append(record)
        for duplicate_records in identity_rows.values():
            if len(duplicate_records) < 2:
                continue
            for record in duplicate_records:
                record["eligible_for_exact_identity_join"] = False
                if "duplicate_sequence_identity" not in record["diagnostic_codes"]:
                    record["diagnostic_codes"].append("duplicate_sequence_identity")
                export["diagnostics"].append({
                    "code": "duplicate_sequence_identity",
                    "sequence_row_id": record["sequence_row_id"],
                    "accession": record["accession"],
                })
        if truncated:
            export["diagnostics"].append({
                "code": "record_limit_exceeded",
                "limit": limit,
                "returned": limit,
            })
        if not rows and not export["diagnostics"]:
            export["status"] = "empty"
        elif export["diagnostics"]:
            export["status"] = "partial"
        else:
            export["status"] = "available"
    except Exception:
        export["records"] = []
        export["status"] = "unavailable"
        export["diagnostics"].append({"code": "authority_schema_or_query_unavailable"})
    finally:
        if transaction_started:
            try:
                connection.rollback()
            except Exception:
                pass
        if cursor is not None:
            try:
                cursor.close()
            except Exception:
                pass
    return _seal(export)
