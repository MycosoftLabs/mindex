"""
Bulk all-kingdom species ingest from Darwin Core Archives (GBIF Backbone, iNaturalist taxonomy).

Taxonomy only (names, rank, authority, lineage, common names, source IDs) - no occurrences.

Safety properties:
- Idempotent: the crosswalk core.taxon_external_id (source, external_id) is the upsert key.
- Fill-only: existing rows are only enriched (null/'Undesignated' kingdom, empty lineage, missing
  author/common name/external id). Nothing is deleted, capped or sampled.
- Cross-source dedupe: an unmatched record links to an existing species-rank taxon with the same
  name when the kingdom agrees (or the existing row is 'Undesignated' and the name is not a
  cross-kingdom homonym in the source).
- Staging lives in session TEMP tables (not WAL-logged, not part of any publication).
- Resumable: per-source checkpoint of the last committed source ID.
- Throttled: pauses while the logical replication slot lag or free disk crosses a threshold.

Usage (inside the mindex-api image, see docs/MINDEX_ALL_SPECIES_INGEST_AND_ANCESTRY_DATABASE_OCT03_2026.md):
    python -m mindex_etl.jobs.bulk_taxonomy_ingest --source gbif --data-dir /data
    python -m mindex_etl.jobs.bulk_taxonomy_ingest --source inat --data-dir /data
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import re
import shutil
import sys
import time
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Optional

import httpx
import psycopg

from ..config import settings
from ..taxon_canonicalizer import normalize_kingdom

USER_AGENT = "MINDEX-taxonomy-bulk-ingest/1.0 (+https://mycosoft.com)"
DWC = "http://rs.tdwg.org/dwc/terms/"


@dataclass(frozen=True)
class SourceSpec:
    name: str
    url: str
    filename: str
    dataset_label: str
    statuses: Optional[frozenset[str]]


SOURCES = {
    "gbif": SourceSpec(
        name="gbif",
        url="https://hosted-datasets.gbif.org/datasets/backbone/current/backbone.zip",
        filename="gbif_backbone.zip",
        dataset_label="gbif_backbone_dwca",
        statuses=frozenset({"accepted"}),
    ),
    "inat": SourceSpec(
        name="inat",
        url="https://www.inaturalist.org/taxa/inaturalist-taxonomy.dwca.zip",
        filename="inaturalist_taxonomy_dwca.zip",
        dataset_label="inaturalist_taxonomy_dwca",
        statuses=None,
    ),
}

LINEAGE_TERMS = ("kingdom", "phylum", "class", "order", "family", "genus")


def log(message: str) -> None:
    print(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} {message}", flush=True)


def download(spec: SourceSpec, data_dir: Path) -> Path:
    """Single resumable download of the public bulk file (HTTP Range)."""
    target = data_dir / spec.filename
    partial = target.with_suffix(target.suffix + ".part")
    headers = {"User-Agent": USER_AGENT}
    with httpx.Client(follow_redirects=True, timeout=60, headers=headers) as client:
        head = client.head(spec.url)
        head.raise_for_status()
        expected = int(head.headers.get("content-length") or 0)
        if target.exists() and expected and target.stat().st_size == expected:
            log(f"[{spec.name}] archive present ({expected} bytes), skipping download")
            return target
        start = partial.stat().st_size if partial.exists() else 0
        request_headers = {"Range": f"bytes={start}-"} if start else {}
        with client.stream("GET", spec.url, headers=request_headers, timeout=None) as response:
            response.raise_for_status()
            mode = "ab" if start and response.status_code == 206 else "wb"
            written = start if mode == "ab" else 0
            next_report = written + 100 * 1024 * 1024
            with partial.open(mode) as handle:
                for chunk in response.iter_bytes(1024 * 1024):
                    handle.write(chunk)
                    written += len(chunk)
                    if written >= next_report:
                        log(f"[{spec.name}] downloaded {written // (1024 * 1024)} MB / {expected // (1024 * 1024)} MB")
                        next_report += 100 * 1024 * 1024
        if expected and partial.stat().st_size != expected:
            raise RuntimeError(f"Incomplete download: {partial.stat().st_size} of {expected} bytes")
        partial.replace(target)
    log(f"[{spec.name}] download complete -> {target}")
    return target


@dataclass(frozen=True)
class DwcaFile:
    locations: tuple[str, ...]
    row_type: str
    delimiter: str
    quote: str
    header_lines: int
    id_index: int
    fields: dict[str, int]


def _parse_file_node(node: ET.Element, ns: str) -> DwcaFile:
    def unescape(value: str) -> str:
        return value.replace("\\t", "\t").replace("\\n", "\n")

    locations = tuple(loc.text.strip() for loc in node.findall(f"{ns}files/{ns}location") if loc.text)
    id_node = node.find(f"{ns}id")
    if id_node is None:
        id_node = node.find(f"{ns}coreid")
    fields = {}
    for field in node.findall(f"{ns}field"):
        if field.get("index") is None:
            continue
        term = field.get("term", "").rsplit("/", 1)[-1]
        fields[term] = int(field.get("index"))
    return DwcaFile(
        locations=locations,
        row_type=node.get("rowType", ""),
        delimiter=unescape(node.get("fieldsTerminatedBy", ",")),
        quote=unescape(node.get("fieldsEnclosedBy", "")),
        header_lines=int(node.get("ignoreHeaderLines", "0") or 0),
        id_index=int(id_node.get("index")) if id_node is not None else 0,
        fields=fields,
    )


def read_meta(archive: zipfile.ZipFile) -> tuple[DwcaFile, list[DwcaFile]]:
    root = ET.fromstring(archive.read("meta.xml"))
    ns = root.tag[: root.tag.index("}") + 1] if root.tag.startswith("{") else ""
    core = _parse_file_node(root.find(f"{ns}core"), ns)
    extensions = [_parse_file_node(node, ns) for node in root.findall(f"{ns}extension")]
    return core, extensions


def iter_rows(archive: zipfile.ZipFile, spec: DwcaFile) -> Iterator[list[str]]:
    csv.field_size_limit(sys.maxsize)
    for location in spec.locations:
        yield from _iter_file_rows(archive, spec, location)


def _iter_file_rows(archive: zipfile.ZipFile, spec: DwcaFile, location: str) -> Iterator[list[str]]:
    with archive.open(location) as raw:
        text = io.TextIOWrapper(raw, encoding="utf-8", errors="replace", newline="")
        if spec.quote:
            reader = csv.reader(text, delimiter=spec.delimiter, quotechar=spec.quote)
        else:
            reader = csv.reader(text, delimiter=spec.delimiter, quoting=csv.QUOTE_NONE)
        for line_number, row in enumerate(reader):
            if line_number < spec.header_lines:
                continue
            yield row


def _cell(row: list[str], spec: DwcaFile, term: str) -> Optional[str]:
    index = spec.fields.get(term)
    if index is None or index >= len(row):
        return None
    value = row[index].strip()
    return value or None


def _source_id(raw: Optional[str]) -> Optional[int]:
    if not raw:
        return None
    tail = raw.rstrip("/").rsplit("/", 1)[-1]
    return int(tail) if tail.isdigit() else None


STAGE_DDL = """
CREATE TEMP TABLE IF NOT EXISTS stage_taxon (
    source_id bigint PRIMARY KEY,
    canonical_name text NOT NULL,
    kingdom text NOT NULL,
    lineage text[],
    author text,
    common_name text,
    metadata jsonb NOT NULL,
    homonym boolean NOT NULL DEFAULT false,
    taxon_id uuid,
    match_kind text
);
CREATE TEMP TABLE IF NOT EXISTS stage_vernacular (source_id bigint, name text);
"""


def stage_source(conn: psycopg.Connection, spec: SourceSpec, archive_path: Path) -> int:
    with zipfile.ZipFile(archive_path) as archive:
        core, extensions = read_meta(archive)
        log(f"[{spec.name}] core={core.locations} fields={sorted(core.fields)}")
        staged = 0
        skipped_rank = 0
        with conn.cursor() as cur:
            cur.execute(STAGE_DDL)
            cur.execute("TRUNCATE stage_taxon, stage_vernacular")
            with cur.copy(
                "COPY stage_taxon (source_id, canonical_name, kingdom, lineage, author, metadata) FROM STDIN"
            ) as copy:
                seen: set[int] = set()
                for row in iter_rows(archive, core):
                    rank = (_cell(row, core, "taxonRank") or "").lower()
                    if rank != "species":
                        skipped_rank += 1
                        continue
                    status = (_cell(row, core, "taxonomicStatus") or "").lower()
                    if spec.statuses is not None and status not in spec.statuses:
                        continue
                    source_id = _source_id(row[core.id_index] if core.id_index < len(row) else None)
                    if source_id is None:
                        source_id = _source_id(_cell(row, core, "taxonID"))
                    if source_id is None or source_id in seen:
                        continue
                    scientific_name = _cell(row, core, "scientificName")
                    canonical = _cell(row, core, "canonicalName")
                    genus = _cell(row, core, "genus") or _cell(row, core, "genericName")
                    epithet = _cell(row, core, "specificEpithet")
                    if not canonical and genus and epithet:
                        canonical = f"{genus} {epithet}"
                    canonical = canonical or scientific_name
                    if not canonical:
                        continue
                    canonical = " ".join(canonical.split())
                    raw_kingdom = _cell(row, core, "kingdom")
                    kingdom = normalize_kingdom(raw_kingdom, default="Undesignated")
                    lineage = [v for v in (_cell(row, core, t) for t in LINEAGE_TERMS) if v]
                    author = _cell(row, core, "scientificNameAuthorship")
                    if not author and scientific_name and scientific_name.startswith(canonical + " "):
                        author = scientific_name[len(canonical) + 1:].strip() or None
                    metadata = {
                        f"{spec.name}_id": source_id,
                        "scientific_name": scientific_name or canonical,
                        "taxonomic_status": status or None,
                        "kingdom_raw": raw_kingdom,
                        "phylum": _cell(row, core, "phylum"),
                        "class": _cell(row, core, "class"),
                        "order": _cell(row, core, "order"),
                        "family": _cell(row, core, "family"),
                        "genus": genus,
                        "source_dataset": spec.dataset_label,
                    }
                    if spec.name == "inat":
                        metadata["inat_id"] = source_id
                    metadata = {k: v for k, v in metadata.items() if v is not None}
                    seen.add(source_id)
                    copy.write_row((source_id, canonical, kingdom, lineage or None, author, json.dumps(metadata)))
                    staged += 1
                    if staged % 250_000 == 0:
                        log(f"[{spec.name}] staged {staged} species")
            log(f"[{spec.name}] staged {staged} species (non-species rows skipped: {skipped_rank})")

            vernacular_files = [e for e in extensions if e.row_type.endswith("VernacularName")]
            with cur.copy("COPY stage_vernacular (source_id, name) FROM STDIN") as copy:
                kept = 0
                for ext in vernacular_files:
                    for row in iter_rows(archive, ext):
                        language = (_cell(row, ext, "language") or "").lower()
                        if language not in ("en", "eng", "english"):
                            continue
                        source_id = _source_id(row[ext.id_index] if ext.id_index < len(row) else None)
                        name = _cell(row, ext, "vernacularName")
                        if source_id is not None and name:
                            copy.write_row((source_id, name[:300]))
                            kept += 1
            log(f"[{spec.name}] staged {kept} English vernacular names from {sum(len(e.locations) for e in vernacular_files)} file(s)")
            cur.execute(
                """
                UPDATE stage_taxon s SET common_name = v.name
                FROM (SELECT DISTINCT ON (source_id) source_id, name FROM stage_vernacular
                      ORDER BY source_id, length(name)) v
                WHERE v.source_id = s.source_id
                """
            )
            cur.execute(
                """
                UPDATE stage_taxon s SET homonym = true
                FROM (SELECT lower(canonical_name) AS lname FROM stage_taxon
                      GROUP BY 1 HAVING count(DISTINCT kingdom) > 1) h
                WHERE lower(s.canonical_name) = h.lname
                """
            )
            log(f"[{spec.name}] cross-kingdom homonyms flagged: {cur.rowcount}")
            cur.execute("CREATE INDEX IF NOT EXISTS stage_taxon_lname ON stage_taxon (lower(canonical_name))")
            cur.execute("ANALYZE stage_taxon")
    conn.commit()
    return staged


BATCH_SQL = [
    # 1. Crosswalk hit: this source ID is already linked.
    """
    UPDATE stage_taxon s SET taxon_id = x.taxon_id, match_kind = 'external_id'
    FROM core.taxon_external_id x
    WHERE s.source_id > %(lo)s AND s.source_id <= %(hi)s AND s.taxon_id IS NULL
      AND x.source = %(source)s AND x.external_id = s.source_id::text
    """,
    # 2. Name + kingdom match against species-rank taxa not yet claimed by this source.
    """
    UPDATE stage_taxon s SET taxon_id = m.id, match_kind = 'name'
    FROM (
        SELECT DISTINCT ON (s2.source_id) s2.source_id, t.id
        FROM stage_taxon s2
        JOIN core.taxon t
          ON lower(t.canonical_name) = lower(s2.canonical_name)
         AND t.kingdom = ANY (ARRAY[s2.kingdom, 'Undesignated'])
         AND t.rank IN ('species', 'sp.')
        WHERE s2.source_id > %(lo)s AND s2.source_id <= %(hi)s AND s2.taxon_id IS NULL
          AND (NOT s2.homonym OR t.kingdom = s2.kingdom)
          AND NOT EXISTS (SELECT 1 FROM core.taxon_external_id x
                          WHERE x.taxon_id = t.id AND x.source = %(source)s)
        ORDER BY s2.source_id, (t.kingdom = s2.kingdom) DESC, (t.rank = 'species') DESC, t.created_at
    ) m
    WHERE s.source_id = m.source_id
    """,
    # 3. Fill-only enrichment of matched rows; skip rows with nothing to add (limits WAL).
    """
    UPDATE core.taxon t SET
        kingdom = CASE WHEN t.kingdom IS NULL OR t.kingdom = 'Undesignated' THEN s.kingdom ELSE t.kingdom END,
        lineage = CASE WHEN coalesce(cardinality(t.lineage), 0) < 2 AND s.lineage IS NOT NULL
                       THEN s.lineage ELSE t.lineage END,
        author = coalesce(nullif(t.author, ''), s.author),
        common_name = coalesce(nullif(t.common_name, ''), s.common_name),
        external_ids = CASE WHEN coalesce(t.external_ids, '{}'::jsonb) ? %(source)s THEN t.external_ids
                            ELSE coalesce(t.external_ids, '{}'::jsonb) || jsonb_build_object(%(source)s::text, s.source_id::text) END,
        metadata = CASE
            WHEN coalesce(t.metadata, '{}'::jsonb) ? %(meta_key)s THEN t.metadata
            ELSE coalesce(t.metadata, '{}'::jsonb)
                 || jsonb_build_object(%(meta_key)s::text, s.metadata)
                 || CASE WHEN coalesce(cardinality(t.lineage), 0) = 1 AND s.lineage IS NOT NULL
                         THEN jsonb_build_object('legacy_lineage', to_jsonb(t.lineage)) ELSE '{}'::jsonb END
                 || CASE WHEN NOT coalesce(t.metadata, '{}'::jsonb) ? 'family' AND s.metadata ? 'family'
                         THEN jsonb_build_object('family', s.metadata->'family') ELSE '{}'::jsonb END
            END,
        updated_at = now()
    FROM stage_taxon s
    WHERE s.source_id > %(lo)s AND s.source_id <= %(hi)s AND t.id = s.taxon_id
      AND (
        ((t.kingdom IS NULL OR t.kingdom = 'Undesignated') AND s.kingdom <> 'Undesignated')
        OR (coalesce(cardinality(t.lineage), 0) < 2 AND s.lineage IS NOT NULL)
        OR (coalesce(t.author, '') = '' AND s.author IS NOT NULL)
        OR (coalesce(t.common_name, '') = '' AND s.common_name IS NOT NULL)
        OR NOT coalesce(t.external_ids, '{}'::jsonb) ? %(source)s
        OR NOT coalesce(t.metadata, '{}'::jsonb) ? %(meta_key)s
      )
    """,
    # 4. Insert genuinely new species.
    """
    WITH fresh AS (
        UPDATE stage_taxon s SET taxon_id = gen_random_uuid(), match_kind = 'inserted'
        WHERE s.source_id > %(lo)s AND s.source_id <= %(hi)s AND s.taxon_id IS NULL
        RETURNING s.*
    )
    INSERT INTO core.taxon (id, canonical_name, rank, author, common_name, source, kingdom, lineage,
                            metadata, external_ids)
    SELECT f.taxon_id, f.canonical_name, 'species', f.author, f.common_name, %(source)s, f.kingdom,
           f.lineage, f.metadata, jsonb_build_object(%(source)s::text, f.source_id::text)
    FROM fresh f
    """,
    # 5. Crosswalk rows for everything in the batch.
    """
    INSERT INTO core.taxon_external_id (taxon_id, source, external_id, metadata)
    SELECT s.taxon_id, %(source)s, s.source_id::text,
           jsonb_build_object('dataset', %(dataset)s::text, 'match', s.match_kind)
    FROM stage_taxon s
    WHERE s.source_id > %(lo)s AND s.source_id <= %(hi)s AND s.taxon_id IS NOT NULL
    ON CONFLICT (source, external_id) DO NOTHING
    """,
]


def slot_lag_bytes(conn: psycopg.Connection) -> Optional[int]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT max(pg_wal_lsn_diff(pg_current_wal_lsn(), coalesce(confirmed_flush_lsn, restart_lsn)))::bigint
            FROM pg_replication_slots
            """
        )
        row = cur.fetchone()
    conn.commit()
    return int(row[0]) if row and row[0] is not None else None


def wait_for_headroom(conn: psycopg.Connection, args: argparse.Namespace) -> None:
    while True:
        lag = slot_lag_bytes(conn) or 0
        free_gb = shutil.disk_usage(args.data_dir).free / 1024**3
        if lag <= args.max_slot_lag_mb * 1024**2 and free_gb >= args.min_free_gb:
            return
        log(f"throttle: slot lag {lag // 1024**2} MB (max {args.max_slot_lag_mb}), "
            f"free disk {free_gb:.1f} GB (min {args.min_free_gb}); sleeping 30s")
        time.sleep(30)


def sync_database_url(url: str) -> str:
    """The API's DATABASE_URL may carry a SQLAlchemy driver suffix (postgresql+asyncpg://)."""
    return re.sub(r"^postgres(ql)?\+\w+://", "postgresql://", url)


def load_checkpoint(path: Path) -> dict:
    return json.loads(path.read_text()) if path.exists() else {}


def save_checkpoint(path: Path, state: dict) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2))
    tmp.replace(path)


def run(args: argparse.Namespace) -> None:
    spec = SOURCES[args.source]
    data_dir = Path(args.data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    checkpoint_path = data_dir / f"checkpoint_{spec.name}.json"
    state = load_checkpoint(checkpoint_path)
    archive = download(spec, data_dir)

    with psycopg.connect(sync_database_url(settings.database_url), application_name=f"bulk_taxonomy_ingest_{spec.name}") as conn:
        staged = stage_source(conn, spec, archive)
        with conn.cursor() as cur:
            cur.execute("SELECT coalesce(max(source_id), 0) FROM stage_taxon")
            max_id = int(cur.fetchone()[0])
        conn.commit()
        last = int(state.get("last_source_id", 0))
        totals = state.get("totals", {"external_id": 0, "name": 0, "inserted": 0, "enriched": 0})
        state.update({"source": spec.name, "staged": staged, "max_source_id": max_id, "status": "running"})
        save_checkpoint(checkpoint_path, state)
        log(f"[{spec.name}] resuming after source_id {last}; max {max_id}")

        params = {"source": spec.name, "dataset": spec.dataset_label, "meta_key": f"{spec.name}_taxonomy"}
        with conn.cursor() as cur:
            batches = 0
            while last < max_id and (not args.max_batches or batches < args.max_batches):
                batches += 1
                wait_for_headroom(conn, args)
                cur.execute(
                    "SELECT max(source_id) FROM (SELECT source_id FROM stage_taxon WHERE source_id > %s "
                    "ORDER BY source_id LIMIT %s) b",
                    (last, args.batch_size),
                )
                hi = int(cur.fetchone()[0])
                batch = {**params, "lo": last, "hi": hi}
                started = time.time()
                counts = []
                for statement in BATCH_SQL:
                    cur.execute(statement, batch)
                    counts.append(cur.rowcount)
                cur.execute(
                    "SELECT match_kind, count(*) FROM stage_taxon WHERE source_id > %s AND source_id <= %s "
                    "GROUP BY 1",
                    (last, hi),
                )
                for kind, count in cur.fetchall():
                    totals[kind] = totals.get(kind, 0) + count
                totals["enriched"] = totals.get("enriched", 0) + max(counts[2], 0)
                conn.commit()
                last = hi
                state.update({"last_source_id": last, "totals": totals, "updated_at": time.time()})
                save_checkpoint(checkpoint_path, state)
                log(f"[{spec.name}] batch -> {hi} ({time.time() - started:.1f}s) totals={totals}")
                time.sleep(args.sleep)
        if last < max_id:
            state["status"] = "paused"
            save_checkpoint(checkpoint_path, state)
            log(f"[{spec.name}] stopped after {args.max_batches} batch(es) at source_id {last}")
            return
        with conn.cursor() as cur:
            cur.execute("ANALYZE core.taxon")
            cur.execute("ANALYZE core.taxon_external_id")
        conn.commit()
        state["status"] = "complete"
        save_checkpoint(checkpoint_path, state)
        log(f"[{spec.name}] complete totals={totals}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", choices=sorted(SOURCES), required=True)
    parser.add_argument("--data-dir", default="/data")
    parser.add_argument("--batch-size", type=int, default=5000)
    parser.add_argument("--sleep", type=float, default=0.25)
    parser.add_argument("--max-slot-lag-mb", type=int, default=1024)
    parser.add_argument("--min-free-gb", type=float, default=20.0)
    parser.add_argument("--max-batches", type=int, default=0, help="Stop after N batches (smoke test); 0 = all.")
    run(parser.parse_args())


if __name__ == "__main__":
    main()
