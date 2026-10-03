"""
Merge normalized taxonomy files (from `taxonomy_sources`) into core.taxon, core.taxon_external_id,
core.taxon_synonym and meta.data_source.

Same safety properties as `bulk_taxonomy_ingest`:
- Idempotent: core.taxon_external_id (source, external_id) is the upsert key; synonyms are unique per
  (taxon_id, lower(synonym)).
- Fill-only: existing rows are only enriched; nothing is deleted, capped or sampled. A stored kingdom
  other than Undesignated is never changed, so fungal rows stay fungal.
- Dedupe across sources: crosswalk hit, then name + kingdom, then a name that is unique across all
  kingdoms when one side is Protista/Undesignated (kingdom placement differs between sources for
  algae, slime moulds, protists and incertae sedis names).
- Staging is a session TEMP table: not WAL-logged and not in the aws_mig publication.
- Throttled on replication slot lag and free disk; checkpointed after every batch.

Usage (inside the mindex-api image):
    python -m mindex_etl.jobs.all_species_load --source gbif --norm-dir /w/norm --state-dir /w/state
"""
from __future__ import annotations

import argparse
import csv
import gzip
import json
import sys
import time
from pathlib import Path

import psycopg

from ..config import settings
from .bulk_taxonomy_ingest import load_checkpoint, save_checkpoint, sync_database_url, wait_for_headroom

PROVENANCE = {
    "gbif": ("GBIF Backbone Taxonomy", "CC BY 4.0",
             "https://hosted-datasets.gbif.org/datasets/backbone/current/backbone.zip", "irregular (last build 2023-08-28)"),
    "col": ("Catalogue of Life (ChecklistBank release)", "CC BY 4.0",
            "https://download.checklistbank.org/col/latest_dwca.zip", "monthly"),
    "ncbi": ("NCBI Taxonomy (new_taxdump)", "Public domain (US Government work, NCBI)",
             "https://ftp.ncbi.nlm.nih.gov/pub/taxonomy/new_taxdump/new_taxdump.tar.gz", "daily"),
    "itis": ("Integrated Taxonomic Information System (ITIS)", "Public domain (US Government work)",
             "https://www.itis.gov/downloads/itisSqlite.zip", "monthly"),
    "gtdb": ("Genome Taxonomy Database (GTDB)", "CC BY-SA 4.0",
             "https://data.gtdb.ecogenomic.org/releases/latest/", "per release"),
    "ictv": ("ICTV Master Species List", "CC BY-SA 4.0",
             "https://ictv.global/msl", "annual"),
    "inat": ("iNaturalist taxonomy DwC-A", "CC0 (taxonomy export)",
             "https://www.inaturalist.org/taxa/inaturalist-taxonomy.dwca.zip", "monthly"),
}

STAGE_DDL = """
CREATE TEMP TABLE IF NOT EXISTS stage_taxon (
    seq bigint PRIMARY KEY,
    source_key text NOT NULL UNIQUE,
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
CREATE TEMP TABLE IF NOT EXISTS stage_synonym (
    seq bigint PRIMARY KEY,
    synonym_key text NOT NULL,
    accepted_key text NOT NULL,
    name text NOT NULL
);
"""

RANGE = "s.seq > %(lo)s AND s.seq <= %(hi)s"

BATCH_SQL = [
    # 1. Crosswalk hit.
    f"""
    UPDATE stage_taxon s SET taxon_id = x.taxon_id, match_kind = 'external_id'
    FROM core.taxon_external_id x
    WHERE {RANGE} AND s.taxon_id IS NULL AND x.source = %(source)s AND x.external_id = s.source_key
    """,
    # 2. Name + kingdom (or existing Undesignated) among species-rank taxa not yet claimed by this source.
    f"""
    UPDATE stage_taxon s SET taxon_id = m.id, match_kind = 'name'
    FROM (
        SELECT DISTINCT ON (s2.seq) s2.seq, t.id
        FROM stage_taxon s2
        JOIN core.taxon t
          ON lower(t.canonical_name) = lower(s2.canonical_name)
         AND t.kingdom = ANY (ARRAY[s2.kingdom, 'Undesignated'])
         AND t.rank IN ('species', 'sp.')
        WHERE s2.seq > %(lo)s AND s2.seq <= %(hi)s AND s2.taxon_id IS NULL
          AND (NOT s2.homonym OR t.kingdom = s2.kingdom)
          AND NOT EXISTS (SELECT 1 FROM core.taxon_external_id x WHERE x.taxon_id = t.id AND x.source = %(source)s)
        ORDER BY s2.seq, (t.kingdom = s2.kingdom) DESC, (t.rank = 'species') DESC, t.created_at
    ) m
    WHERE s.seq = m.seq
    """,
    # 3. Cross-kingdom: the name is unique across all kingdoms and one side is Protista/Undesignated.
    f"""
    UPDATE stage_taxon s SET taxon_id = m.id, match_kind = 'name_cross_kingdom'
    FROM (
        SELECT s2.seq, (array_agg(t.id))[1] AS id, (array_agg(t.kingdom))[1] AS kingdom
        FROM stage_taxon s2
        JOIN core.taxon t ON lower(t.canonical_name) = lower(s2.canonical_name) AND t.rank IN ('species', 'sp.')
        WHERE s2.seq > %(lo)s AND s2.seq <= %(hi)s AND s2.taxon_id IS NULL AND NOT s2.homonym
        GROUP BY s2.seq, s2.kingdom
        HAVING count(*) = 1
           AND (s2.kingdom IN ('Protista', 'Undesignated')
                OR bool_or(coalesce(t.kingdom, 'Undesignated') IN ('Protista', 'Undesignated')))
    ) m
    WHERE s.seq = m.seq
      AND NOT EXISTS (SELECT 1 FROM core.taxon_external_id x WHERE x.taxon_id = m.id AND x.source = %(source)s)
    """,
    # 4. Fill-only enrichment; rows with nothing to add are skipped to limit WAL.
    f"""
    UPDATE core.taxon t SET
        kingdom = CASE WHEN t.kingdom IS NULL OR t.kingdom = 'Undesignated' THEN s.kingdom ELSE t.kingdom END,
        lineage = CASE WHEN coalesce(cardinality(t.lineage), 0) < 2 AND s.lineage IS NOT NULL
                       THEN s.lineage ELSE t.lineage END,
        author = coalesce(nullif(t.author, ''), s.author),
        common_name = coalesce(nullif(t.common_name, ''), s.common_name),
        external_ids = CASE WHEN t.external_ids ? %(source)s THEN t.external_ids
                            ELSE t.external_ids || jsonb_build_object(%(source)s::text, s.source_key) END,
        metadata = CASE
            WHEN t.metadata ? %(meta_key)s THEN t.metadata
            ELSE t.metadata
                 || jsonb_build_object(%(meta_key)s::text, s.metadata)
                 || CASE WHEN coalesce(cardinality(t.lineage), 0) = 1 AND s.lineage IS NOT NULL
                         THEN jsonb_build_object('legacy_lineage', to_jsonb(t.lineage)) ELSE '{{}}'::jsonb END
                 || CASE WHEN NOT t.metadata ? 'family' AND s.metadata ? 'family'
                         THEN jsonb_build_object('family', s.metadata->'family') ELSE '{{}}'::jsonb END
                 || CASE WHEN (t.kingdom IS NULL OR t.kingdom = 'Undesignated') AND NOT t.metadata ? 'kingdom'
                              AND s.metadata ? 'kingdom_raw'
                         THEN jsonb_build_object('kingdom', s.metadata->'kingdom_raw') ELSE '{{}}'::jsonb END
            END,
        updated_at = now()
    FROM stage_taxon s
    WHERE {RANGE} AND t.id = s.taxon_id
      AND (
        ((t.kingdom IS NULL OR t.kingdom = 'Undesignated') AND s.kingdom <> 'Undesignated')
        OR (coalesce(cardinality(t.lineage), 0) < 2 AND s.lineage IS NOT NULL)
        OR (coalesce(t.author, '') = '' AND s.author IS NOT NULL)
        OR (coalesce(t.common_name, '') = '' AND s.common_name IS NOT NULL)
        OR NOT t.external_ids ? %(source)s
        OR NOT t.metadata ? %(meta_key)s
      )
    """,
    # 5. Insert genuinely new species. metadata.kingdom keeps the source's own kingdom (e.g. Chromista).
    f"""
    WITH fresh AS (
        UPDATE stage_taxon s SET taxon_id = gen_random_uuid(), match_kind = 'inserted'
        WHERE {RANGE} AND s.taxon_id IS NULL
        RETURNING s.*
    )
    INSERT INTO core.taxon (id, canonical_name, rank, author, common_name, source, kingdom, lineage,
                            metadata, external_ids)
    SELECT f.taxon_id, f.canonical_name, 'species', f.author, f.common_name, %(source)s, f.kingdom, f.lineage,
           f.metadata || CASE WHEN f.metadata ? 'kingdom_raw'
                              THEN jsonb_build_object('kingdom', f.metadata->'kingdom_raw') ELSE '{{}}'::jsonb END,
           jsonb_build_object(%(source)s::text, f.source_key)
    FROM fresh f
    """,
    # 6. Crosswalk rows for the batch.
    f"""
    INSERT INTO core.taxon_external_id (taxon_id, source, external_id, metadata)
    SELECT s.taxon_id, %(source)s, s.source_key, jsonb_build_object('dataset', %(dataset)s::text, 'match', s.match_kind)
    FROM stage_taxon s
    WHERE {RANGE} AND s.taxon_id IS NOT NULL
    ON CONFLICT (source, external_id) DO NOTHING
    """,
]

SYNONYM_SQL = f"""
INSERT INTO core.taxon_synonym (taxon_id, synonym, source)
SELECT DISTINCT ON (x.taxon_id, lower(s.name)) x.taxon_id, s.name, %(source)s || ':' || s.synonym_key
FROM stage_synonym s
JOIN core.taxon_external_id x ON x.source = %(source)s AND x.external_id = s.accepted_key
JOIN core.taxon t ON t.id = x.taxon_id
WHERE {RANGE} AND lower(s.name) <> lower(t.canonical_name)
ORDER BY x.taxon_id, lower(s.name), s.seq
ON CONFLICT (taxon_id, lower(synonym)) DO NOTHING
"""


def log(message: str) -> None:
    print(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} {message}", flush=True)


def _rows(path: Path):
    with gzip.open(path, "rt", encoding="utf-8", newline="") as fh:
        yield from csv.reader(fh, delimiter="\t")


def stage(conn: psycopg.Connection, source: str, norm_dir: Path) -> tuple[int, int]:
    with conn.cursor() as cur:
        cur.execute(STAGE_DDL)
        cur.execute("TRUNCATE stage_taxon, stage_synonym")
        n = 0
        with cur.copy("COPY stage_taxon (seq, source_key, canonical_name, kingdom, lineage, author, common_name, "
                      "metadata) FROM STDIN") as copy:
            for key, name, kingdom, lineage, author, common, metadata in _rows(norm_dir / f"{source}_taxa.tsv.gz"):
                n += 1
                copy.write_row((n, key, name, kingdom, lineage.split("|") if lineage else None, author or None,
                                common or None, metadata))
        m = 0
        with cur.copy("COPY stage_synonym (seq, synonym_key, accepted_key, name) FROM STDIN") as copy:
            for syn_key, accepted_key, name in _rows(norm_dir / f"{source}_synonyms.tsv.gz"):
                m += 1
                copy.write_row((m, syn_key, accepted_key, name))
        cur.execute(
            """
            UPDATE stage_taxon s SET homonym = true
            FROM (SELECT lower(canonical_name) AS lname FROM stage_taxon
                  GROUP BY 1 HAVING count(DISTINCT kingdom) > 1) h
            WHERE lower(s.canonical_name) = h.lname
            """
        )
        log(f"[{source}] staged {n} species, {m} synonyms; cross-kingdom homonyms flagged {cur.rowcount}")
        cur.execute("ANALYZE stage_taxon")
        cur.execute("ANALYZE stage_synonym")
    conn.commit()
    return n, m


def run_batches(conn, args, label: str, total: int, statements: list[str], params: dict, state: dict,
                state_path: Path, totals: dict, count_sql: str | None) -> None:
    last = int(state.get(f"{label}_last_seq", 0))
    with conn.cursor() as cur:
        while last < total:
            wait_for_headroom(conn, args)
            hi = min(last + args.batch_size, total)
            batch = {**params, "lo": last, "hi": hi}
            started = time.time()
            rowcounts = []
            for statement in statements:
                cur.execute(statement, batch)
                rowcounts.append(cur.rowcount)
            if count_sql:
                cur.execute(count_sql, batch)
                for kind, count in cur.fetchall():
                    totals[kind] = totals.get(kind, 0) + count
                totals["enriched"] = totals.get("enriched", 0) + max(rowcounts[3], 0)
            else:
                totals["synonyms_inserted"] = totals.get("synonyms_inserted", 0) + max(rowcounts[0], 0)
            conn.commit()
            last = hi
            state.update({f"{label}_last_seq": last, "totals": totals, "updated_at": time.time()})
            save_checkpoint(state_path, state)
            if (hi // args.batch_size) % 20 == 0 or hi == total:
                log(f"[{params['source']}] {label} {hi}/{total} ({time.time() - started:.1f}s) totals={totals}")
            time.sleep(args.sleep)


def record_provenance(conn: psycopg.Connection, source: str, dataset: str, totals: dict) -> None:
    display, licence, url, cadence = PROVENANCE[source]
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM core.taxon_external_id WHERE source = %s", (source,))
        linked = cur.fetchone()[0]
        cur.execute(
            """
            INSERT INTO meta.data_source (source_key, display_name, schema_name, table_name, license, source_url,
                                          update_cadence, last_run_at, last_row_count, notes)
            VALUES (%s, %s, 'core', 'taxon', %s, %s, %s, now(), %s, %s)
            ON CONFLICT (source_key) DO UPDATE SET display_name = EXCLUDED.display_name, license = EXCLUDED.license,
                source_url = EXCLUDED.source_url, update_cadence = EXCLUDED.update_cadence,
                last_run_at = EXCLUDED.last_run_at, last_row_count = EXCLUDED.last_row_count, notes = EXCLUDED.notes
            """,
            (f"taxonomy_{source}", display, licence, url, cadence, linked,
             json.dumps({"dataset": dataset, "last_load_totals": totals,
                         "scope": "accepted species + species-level synonyms; crosswalk in core.taxon_external_id"})),
        )
    conn.commit()


def run(args: argparse.Namespace) -> None:
    source = args.source
    norm_dir, state_dir = Path(args.norm_dir), Path(args.state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    state_path = state_dir / f"load_{source}.json"
    state = load_checkpoint(state_path)
    if state.get("status") == "complete" and not args.force:
        log(f"[{source}] already complete; use --force to re-run (idempotent)")
        return
    dataset = args.dataset or source
    if args.provenance_only:
        with psycopg.connect(sync_database_url(settings.database_url), application_name=f"all_species_load_{source}") as conn:
            record_provenance(conn, source, dataset, state.get("totals", {}))
        log(f"[{source}] provenance recorded")
        return
    with psycopg.connect(sync_database_url(settings.database_url), application_name=f"all_species_load_{source}") as conn:
        n_taxa, n_syn = stage(conn, source, norm_dir)
        totals = state.get("totals", {})
        state.update({"source": source, "dataset": dataset, "staged": n_taxa, "staged_synonyms": n_syn,
                      "status": "running"})
        save_checkpoint(state_path, state)
        params = {"source": source, "dataset": dataset, "meta_key": f"{source}_taxonomy"}
        count_sql = (f"SELECT match_kind, count(*) FROM stage_taxon s WHERE {RANGE} GROUP BY 1")
        run_batches(conn, args, "taxa", n_taxa, BATCH_SQL, params, state, state_path, totals, count_sql)
        run_batches(conn, args, "synonyms", n_syn, [SYNONYM_SQL], params, state, state_path, totals, None)
        with conn.cursor() as cur:
            cur.execute("ANALYZE core.taxon")
            cur.execute("ANALYZE core.taxon_external_id")
            cur.execute("ANALYZE core.taxon_synonym")
        conn.commit()
        record_provenance(conn, source, dataset, totals)
    state["status"] = "complete"
    save_checkpoint(state_path, state)
    log(f"[{source}] complete totals={totals}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", choices=sorted(PROVENANCE), required=True)
    parser.add_argument("--norm-dir", default="/w/norm")
    parser.add_argument("--state-dir", default="/w/state")
    parser.add_argument("--data-dir", default="/w", help="Filesystem checked for free space while loading.")
    parser.add_argument("--dataset", default="")
    parser.add_argument("--batch-size", type=int, default=5000)
    parser.add_argument("--sleep", type=float, default=0.1)
    parser.add_argument("--max-slot-lag-mb", type=int, default=1024)
    parser.add_argument("--min-free-gb", type=float, default=20.0)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--provenance-only", action="store_true",
                        help="Only upsert the meta.data_source row (e.g. for a source loaded by another job).")
    csv.field_size_limit(sys.maxsize)
    run(parser.parse_args())


if __name__ == "__main__":
    main()
