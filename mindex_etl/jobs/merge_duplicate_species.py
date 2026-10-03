"""
Non-destructive merge of duplicate species rows in core.taxon.

The same name can exist twice, e.g. a GBIF/iNat row (rank 'species') next to the MycoBank row
(rank 'sp.') for *Abortiporus roseus*. This job folds such rows into one surviving taxon:

- Nothing is deleted. Many tables cascade on core.taxon deletes, so merged rows stay in place with
  their own name, rank, kingdom and data, and gain metadata.merged_into (+ merged_at, merge_reason).
- The survivor only gains data (fill-only): kingdom when Undesignated, lineage, author, common
  name, description, external IDs, and metadata.merged_from.
- Crosswalk rows (core.taxon_external_id) and child rows are repointed to the survivor. A child row
  that would violate a unique constraint stays on the merged row, which still exists. FUNGIP tables
  are never repointed; a row referenced by FUNGIP is always the survivor.
- Only compatible rows merge: same effective kingdom, Undesignated with the one defined kingdom
  in the group, or Fungi with Protista (MycoBank covers fungus-like protists; the Fungi row
  survives). Other cross-kingdom pairs are homonyms under different codes and are left alone.

Default is a dry run that writes the plan to --plan-out. Use --apply to execute in batches with the
same replication-slot and disk headroom checks as the bulk ingest.
"""

from __future__ import annotations

import argparse
import csv
import json
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

import psycopg

from ..config import settings
from .bulk_taxonomy_ingest import load_checkpoint, save_checkpoint, sync_database_url, wait_for_headroom

SPECIES_RANKS = ("species", "sp.")
UNDESIGNATED = "Undesignated"
FUNGAL_SIDE = frozenset({"Fungi", "Protista"})

# Child tables repointed to the survivor (table, column). FUNGIP tables are deliberately absent.
CHILD_REFS = (
    ("species.organisms", "mindex_taxon_id"),
    ("bio.taxon_trait", "taxon_id"),
    ("bio.genome", "taxon_id"),
    ("bio.genetic_sequence", "taxon_id"),
    ("bio.taxon_compound", "taxon_id"),
    ("bio.taxon_guild", "taxon_id"),
    ("bio.taxon_characteristic", "taxon_id"),
    ("bio.publication_taxon", "taxon_id"),
    ("bio.taxon_interaction", "source_taxon_id"),
    ("bio.taxon_interaction", "target_taxon_id"),
    ("obs.observation", "taxon_id"),
    ("media.image", "taxon_id"),
    ("media.video", "taxon_id"),
    ("media.audio", "taxon_id"),
    ("telemetry.device", "taxon_id"),
    ("ip.ip_asset", "taxon_id"),
    ("core.taxon_synonym", "taxon_id"),
    ("core.taxon", "parent_id"),
)

CANDIDATES_SQL = """
WITH names AS (
    SELECT lower(canonical_name) AS lname
    FROM core.taxon
    WHERE rank = ANY(%(ranks)s) AND NOT coalesce(metadata ? 'merged_into', false)
    GROUP BY 1 HAVING count(*) > 1
)
SELECT t.id::text, lower(t.canonical_name), t.canonical_name, t.rank, coalesce(t.source, ''),
       coalesce(nullif(t.kingdom, ''), 'Undesignated'),
       CASE WHEN coalesce(nullif(t.kingdom, ''), 'Undesignated') = 'Undesignated' THEN
            CASE WHEN nullif(t.metadata->>'kingdom', '') IS NOT NULL THEN t.metadata->>'kingdom'
                 WHEN t.metadata->>'iconic_taxon_name' IN ('Animalia', 'Insecta', 'Aves', 'Mammalia', 'Reptilia',
                      'Amphibia', 'Actinopterygii', 'Mollusca', 'Arachnida') THEN 'Animalia'
                 WHEN t.metadata->>'iconic_taxon_name' IN ('Fungi', 'Plantae', 'Protozoa', 'Chromista')
                      THEN t.metadata->>'iconic_taxon_name'
            END
       END,
       coalesce(cardinality(t.lineage), 0),
       (SELECT count(*) FROM core.taxon_external_id x WHERE x.taxon_id = t.id),
       EXISTS (SELECT 1 FROM fungip.species f WHERE f.taxon_id = t.id)
           OR EXISTS (SELECT 1 FROM fungip.page_verification f WHERE f.taxon_id = t.id),
       t.created_at
FROM core.taxon t JOIN names n ON lower(t.canonical_name) = n.lname
WHERE t.rank = ANY(%(ranks)s) AND NOT coalesce(t.metadata ? 'merged_into', false)
ORDER BY 2, t.created_at, t.id
"""

KINGDOM_ALIASES = {"Protozoa": "Protista", "Chromista": "Protista", "Protoctista": "Protista"}


@dataclass
class Row:
    id: str
    lname: str
    name: str
    rank: str
    source: str
    kingdom: str
    effective: str
    lineage_len: int
    crosswalks: int
    fungip: bool
    created_at: datetime


def effective_kingdom(stored: str, from_metadata: Optional[str]) -> str:
    if stored != UNDESIGNATED:
        return stored
    if from_metadata:
        return KINGDOM_ALIASES.get(from_metadata, from_metadata)
    return UNDESIGNATED


def survivor_key(row: Row) -> tuple:
    return (not row.fungip, row.source != "mycobank", row.kingdom == UNDESIGNATED, row.lineage_len < 2,
            -row.crosswalks, row.created_at, row.id)


def plan_group(rows: list[Row]) -> list[tuple[str, str, str]]:
    """Return (loser_id, survivor_id, reason) for one name group."""
    by_kingdom: dict[str, list[Row]] = {}
    unresolved: list[Row] = []
    for row in rows:
        (unresolved if row.effective == UNDESIGNATED else by_kingdom.setdefault(row.effective, [])).append(row)
    if set(by_kingdom) == {"Fungi", "Protista"}:
        by_kingdom = {"Fungi": by_kingdom["Fungi"] + by_kingdom["Protista"]}
        reason = "same_name_fungi_protista"
    else:
        reason = "same_name_same_kingdom"
    if unresolved:
        if len(by_kingdom) == 1:
            next(iter(by_kingdom.values())).extend(unresolved)
        elif not by_kingdom:
            by_kingdom[UNDESIGNATED] = unresolved
    plan = []
    for cluster in by_kingdom.values():
        if len(cluster) < 2 or sum(r.fungip for r in cluster) > 1:
            continue
        survivor = min(cluster, key=survivor_key)
        plan.extend((r.id, survivor.id, reason) for r in cluster if r.id != survivor.id)
    return plan


def build_plan(conn: psycopg.Connection) -> list[tuple[str, str, str, int]]:
    with conn.cursor() as cur:
        cur.execute("SET LOCAL statement_timeout = '900s'")
        cur.execute(CANDIDATES_SQL, {"ranks": list(SPECIES_RANKS)})
        rows = [Row(r[0], r[1], r[2], r[3], r[4], r[5], effective_kingdom(r[5], r[6]), r[7], r[8], r[9], r[10])
                for r in cur.fetchall()]
    conn.commit()
    plan: list[tuple[str, str, str, int]] = []
    group: list[Row] = []
    for row in rows + [None]:
        if group and (row is None or row.lname != group[0].lname):
            ordinal: dict[str, int] = {}
            for loser, survivor, reason in plan_group(group):
                ordinal[survivor] = ordinal.get(survivor, 0) + 1
                plan.append((loser, survivor, reason, ordinal[survivor]))
            group = []
        if row is not None:
            group.append(row)
    log(f"candidate rows {len(rows)}; merges planned {len(plan)}")
    return plan


PLAN_DDL = """
CREATE TEMP TABLE IF NOT EXISTS merge_plan (
    seq integer PRIMARY KEY, loser uuid NOT NULL UNIQUE, survivor uuid NOT NULL, reason text, ord integer
) ON COMMIT PRESERVE ROWS
"""

RANGE = "p.seq > %(lo)s AND p.seq <= %(hi)s"

ENRICH_SQL = f"""
UPDATE core.taxon s SET
    kingdom = CASE WHEN coalesce(nullif(s.kingdom, ''), 'Undesignated') = 'Undesignated'
                    AND coalesce(nullif(l.kingdom, ''), 'Undesignated') <> 'Undesignated'
                   THEN l.kingdom ELSE s.kingdom END,
    lineage = CASE WHEN coalesce(cardinality(s.lineage), 0) < 2 AND coalesce(cardinality(l.lineage), 0) >= 2
                   THEN l.lineage ELSE s.lineage END,
    author = coalesce(nullif(s.author, ''), l.author),
    common_name = coalesce(nullif(s.common_name, ''), l.common_name),
    description = coalesce(nullif(s.description, ''), l.description),
    external_ids = coalesce(l.external_ids, '{{}}'::jsonb) || coalesce(s.external_ids, '{{}}'::jsonb),
    metadata = coalesce(s.metadata, '{{}}'::jsonb) || jsonb_build_object(
        'merged_from', coalesce(s.metadata->'merged_from', '[]'::jsonb) || jsonb_build_array(jsonb_build_object(
            'id', l.id, 'source', l.source, 'rank', l.rank, 'kingdom', l.kingdom, 'reason', p.reason))),
    updated_at = now()
FROM merge_plan p JOIN core.taxon l ON l.id = p.loser
WHERE {RANGE} AND p.ord = %(ord)s AND s.id = p.survivor
"""

CROSSWALK_SQL = f"""
UPDATE core.taxon_external_id x SET taxon_id = p.survivor
FROM merge_plan p WHERE {RANGE} AND x.taxon_id = p.loser
"""

MARK_SQL = f"""
UPDATE core.taxon l SET
    metadata = coalesce(l.metadata, '{{}}'::jsonb) || jsonb_build_object(
        'merged_into', p.survivor::text, 'merged_at', now()::text, 'merge_reason', p.reason,
        'merged_by', 'merge_duplicate_species'),
    updated_at = now()
FROM merge_plan p WHERE {RANGE} AND l.id = p.loser
"""


def log(message: str) -> None:
    print(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} {message}", flush=True)


def write_plan(path: Path, plan: list[tuple[str, str, str, int]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh, delimiter="\t", lineterminator="\n")
        writer.writerow(["loser_id", "survivor_id", "reason", "ord"])
        writer.writerows(plan)


def repoint_children(cur, batch: dict) -> dict[str, int]:
    moved: dict[str, int] = {}
    for table, column in CHILD_REFS:
        cur.execute("SAVEPOINT child")
        try:
            cur.execute(
                f"UPDATE {table} c SET {column} = p.survivor FROM merge_plan p "
                f"WHERE {RANGE} AND c.{column} = p.loser",
                batch,
            )
            moved[f"{table}.{column}"] = cur.rowcount
            cur.execute("RELEASE SAVEPOINT child")
        except psycopg.errors.UniqueViolation:
            cur.execute("ROLLBACK TO SAVEPOINT child")
            moved[f"{table}.{column}:kept_on_merged_row"] = -1
    return moved


def apply_plan(conn: psycopg.Connection, args, plan: list[tuple[str, str, str, int]], state: dict,
               state_path: Path) -> None:
    with conn.cursor() as cur:
        cur.execute(PLAN_DDL)
        cur.execute("TRUNCATE merge_plan")
        with cur.copy("COPY merge_plan (seq, loser, survivor, reason, ord) FROM STDIN") as copy:
            for seq, (loser, survivor, reason, ordinal) in enumerate(plan, start=1):
                copy.write_row((seq, loser, survivor, reason, ordinal))
        cur.execute("ANALYZE merge_plan")
        cur.execute("SELECT coalesce(max(ord), 0) FROM merge_plan")
        max_ord = cur.fetchone()[0]
    conn.commit()
    totals = state.setdefault("totals", {})
    last = int(state.get("last_seq", 0))
    total = len(plan)
    with conn.cursor() as cur:
        while last < total:
            wait_for_headroom(conn, args)
            batch = {"lo": last, "hi": min(last + args.batch_size, total)}
            for ordinal in range(1, max_ord + 1):
                cur.execute(ENRICH_SQL, {**batch, "ord": ordinal})
                totals["survivors_enriched"] = totals.get("survivors_enriched", 0) + max(cur.rowcount, 0)
            cur.execute(CROSSWALK_SQL, batch)
            totals["crosswalks_repointed"] = totals.get("crosswalks_repointed", 0) + max(cur.rowcount, 0)
            for key, count in repoint_children(cur, batch).items():
                totals[key] = totals.get(key, 0) + count
            cur.execute(MARK_SQL, batch)
            totals["rows_marked_merged"] = totals.get("rows_marked_merged", 0) + max(cur.rowcount, 0)
            conn.commit()
            last = batch["hi"]
            state.update({"last_seq": last, "totals": totals, "updated_at": time.time()})
            save_checkpoint(state_path, state)
            log(f"merged {last}/{total} totals={json.dumps(totals)}")
            time.sleep(args.sleep)


def run(args: argparse.Namespace) -> None:
    state_path = Path(args.state_dir) / "merge_duplicate_species.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state = load_checkpoint(state_path) if args.apply else {}
    if args.apply and state.get("status") == "complete" and not args.force:
        log("already complete; use --force to plan and merge any new duplicates")
        return
    with psycopg.connect(sync_database_url(settings.database_url),
                         application_name="merge_duplicate_species") as conn:
        plan = build_plan(conn)
        write_plan(Path(args.plan_out), plan)
        reasons: dict[str, int] = {}
        for _, _, reason, _ in plan:
            reasons[reason] = reasons.get(reason, 0) + 1
        log(f"plan written to {args.plan_out}: {reasons}")
        if not args.apply:
            log("dry run: no changes made")
            return
        # The plan excludes rows already merged, so a resumed run starts over on the remaining rows.
        state.update({"plan_size": len(plan), "last_seq": 0, "status": "running"})
        apply_plan(conn, args, plan, state, state_path)
    state["status"] = "complete"
    save_checkpoint(state_path, state)
    log(f"complete totals={json.dumps(state.get('totals', {}))}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="Execute the plan (default is a dry run).")
    parser.add_argument("--plan-out", default="/w/state/merge_plan.tsv")
    parser.add_argument("--state-dir", default="/w/state")
    parser.add_argument("--data-dir", default="/w", help="Filesystem checked for free space while merging.")
    parser.add_argument("--batch-size", type=int, default=2000)
    parser.add_argument("--sleep", type=float, default=0.1)
    parser.add_argument("--max-slot-lag-mb", type=int, default=1024)
    parser.add_argument("--min-free-gb", type=float, default=20.0)
    parser.add_argument("--force", action="store_true")
    run(parser.parse_args())


if __name__ == "__main__":
    main()
