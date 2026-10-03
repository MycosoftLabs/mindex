"""
Parse public all-kingdom taxonomy dumps into one normalized file format (no database access).

Each source writes two gzip TSV files into --out-dir:
  <source>_taxa.tsv.gz      accepted species: source_key, canonical_name, kingdom, lineage, author,
                            common_name, metadata (JSON)
  <source>_synonyms.tsv.gz  species-level synonyms: synonym_key, accepted_key, synonym_name

`all_species_load` stages these files and merges them into core.taxon / core.taxon_synonym.

Sources and the files they read (downloaded beforehand, see docs/MINDEX_ALL_SPECIES_ON_EARTH_INGEST_OCT03_2026.md):
  gbif   GBIF Backbone Taxonomy DwC-A            backbone.zip
  col    Catalogue of Life release DwC-A          latest_dwca.zip
  ncbi   NCBI Taxonomy new_taxdump               rankedlineage.dmp, names.dmp, nodes.dmp
  itis   ITIS SQLite                             itisSqlite.zip
  gtdb   GTDB bac120 + ar53 taxonomy             *_taxonomy.tsv.gz
  ictv   ICTV Master Species List (xlsx)         ICTV_Master_Species_List_*.xlsx

Usage:
    python -m mindex_etl.jobs.taxonomy_sources --source gbif --raw-dir /w/raw --out-dir /w/norm
"""
from __future__ import annotations

import argparse
import csv
import gzip
import json
import re
import sqlite3
import sys
import tempfile
import time
import zipfile
from pathlib import Path
from typing import Iterator, Optional

import openpyxl

from ..taxon_canonicalizer import normalize_kingdom
from .bulk_taxonomy_ingest import _cell, iter_rows, read_meta

ENGLISH = frozenset({"en", "eng", "english"})
SPECIES_RANKS = frozenset({"species"})
INFRA_RANKS = frozenset({"subspecies", "variety", "form", "infraspecific name", "forma", "subvariety", "subform"})
LINEAGE_KEYS = ("kingdom", "phylum", "class", "order", "family", "genus")

# Formal binomial (optionally "Candidatus "), used to keep NCBI/GTDB placeholder names out.
BINOMIAL = re.compile(r"^(Candidatus )?[A-Z][a-z]+(-[a-z]+)? [a-z][a-z-]+$")
NCBI_VIRUS_JUNK = re.compile(r"uncultured|unidentified|environmental|metagenome|\bsp\.|unclassified", re.I)


def log(message: str) -> None:
    print(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} {message}", flush=True)


class Writer:
    """Writes the normalized taxa + synonym files for one source."""

    def __init__(self, out_dir: Path, source: str) -> None:
        out_dir.mkdir(parents=True, exist_ok=True)
        self.taxa_path = out_dir / f"{source}_taxa.tsv.gz"
        self.syn_path = out_dir / f"{source}_synonyms.tsv.gz"
        self._taxa_fh = gzip.open(self.taxa_path.with_suffix(".part"), "wt", encoding="utf-8", newline="")
        self._syn_fh = gzip.open(self.syn_path.with_suffix(".part"), "wt", encoding="utf-8", newline="")
        self.taxa = csv.writer(self._taxa_fh, delimiter="\t", lineterminator="\n")
        self.syn = csv.writer(self._syn_fh, delimiter="\t", lineterminator="\n")
        self.keys: set[str] = set()
        self.n_taxa = 0
        self.n_syn = 0

    def taxon(self, key: str, canonical: str, kingdom: str, lineage: list[str], author: Optional[str],
              common: Optional[str], metadata: dict) -> None:
        if key in self.keys:
            return
        self.keys.add(key)
        metadata = {k: v for k, v in metadata.items() if v not in (None, "")}
        self.taxa.writerow([key, canonical, kingdom, "|".join(lineage), author or "", common or "",
                            json.dumps(metadata, ensure_ascii=False)])
        self.n_taxa += 1

    def synonym(self, key: str, accepted_key: str, name: str) -> None:
        self.syn.writerow([key, accepted_key, name])
        self.n_syn += 1

    def close(self) -> None:
        self._taxa_fh.close()
        self._syn_fh.close()
        self.taxa_path.with_suffix(".part").replace(self.taxa_path)
        self.syn_path.with_suffix(".part").replace(self.syn_path)
        log(f"wrote {self.n_taxa} accepted species -> {self.taxa_path.name}, {self.n_syn} synonyms -> {self.syn_path.name}")


def clean(name: Optional[str]) -> Optional[str]:
    return " ".join(name.split()) if name else None


def strip_author(scientific: Optional[str], author: Optional[str]) -> Optional[str]:
    if not scientific:
        return None
    if author and scientific.endswith(author):
        return clean(scientific[: -len(author)])
    return clean(scientific)


# --------------------------------------------------------------------------- DwC-A (GBIF, COL)

def parse_dwca(source: str, archive_path: Path, writer: Writer, accepted: frozenset[str], dataset: str) -> None:
    with zipfile.ZipFile(archive_path) as archive:
        core, extensions = read_meta(archive)
        species_keys: set[str] = set()
        skipped_status: dict[str, int] = {}
        synonyms: list[tuple[str, str, str]] = []
        rows = 0
        for row in iter_rows(archive, core):
            rows += 1
            key = (row[core.id_index] if core.id_index < len(row) else "").strip() or _cell(row, core, "taxonID")
            if not key:
                continue
            rank = (_cell(row, core, "taxonRank") or "").lower()
            status = (_cell(row, core, "taxonomicStatus") or "").lower()
            author = _cell(row, core, "scientificNameAuthorship")
            scientific = _cell(row, core, "scientificName")
            genus = _cell(row, core, "genericName") or _cell(row, core, "genus")
            epithet = _cell(row, core, "specificEpithet")
            canonical = clean(_cell(row, core, "canonicalName"))
            if not canonical and rank in SPECIES_RANKS and genus and epithet:
                canonical = f"{genus} {epithet}"
            canonical = canonical or strip_author(scientific, author)
            if not canonical:
                continue
            if status in accepted:
                if rank not in SPECIES_RANKS:
                    continue
                raw_kingdom = _cell(row, core, "kingdom")
                lineage = [v for v in (_cell(row, core, t) for t in LINEAGE_KEYS) if v]
                metadata = {
                    f"{source}_id": key,
                    "scientific_name": scientific or canonical,
                    "taxonomic_status": status,
                    "kingdom_raw": raw_kingdom,
                    "phylum": _cell(row, core, "phylum"),
                    "class": _cell(row, core, "class"),
                    "order": _cell(row, core, "order"),
                    "family": _cell(row, core, "family"),
                    "genus": _cell(row, core, "genus") or genus,
                    "nomenclatural_code": _cell(row, core, "nomenclaturalCode"),
                    "name_published_in": (_cell(row, core, "namePublishedIn") or "")[:500],
                    "source_dataset": dataset,
                    "source_dataset_id": _cell(row, core, "datasetID"),
                }
                writer.taxon(key, canonical, normalize_kingdom(raw_kingdom), lineage, author, None, metadata)
                species_keys.add(key)
            elif "synonym" in status and (rank in SPECIES_RANKS or rank in INFRA_RANKS):
                accepted_key = _cell(row, core, "acceptedNameUsageID")
                if accepted_key:
                    synonyms.append((key, accepted_key, canonical))
            else:
                skipped_status[status or "-"] = skipped_status.get(status or "-", 0) + 1
            if rows % 1_000_000 == 0:
                log(f"[{source}] read {rows} rows, {writer.n_taxa} accepted species")
        log(f"[{source}] read {rows} rows; accepted species {writer.n_taxa}; other statuses {skipped_status}")
        for key, accepted_key, name in synonyms:
            if accepted_key in species_keys:
                writer.synonym(key, accepted_key, name)
        commons = read_vernaculars(archive, extensions, species_keys)
    rewrite_common_names(writer, commons)


def read_vernaculars(archive: zipfile.ZipFile, extensions, keys: set[str]) -> dict[str, str]:
    best: dict[str, str] = {}
    for ext in (e for e in extensions if e.row_type.endswith("VernacularName")):
        for row in iter_rows(archive, ext):
            if (_cell(row, ext, "language") or "").lower() not in ENGLISH:
                continue
            key = (row[ext.id_index] if ext.id_index < len(row) else "").strip()
            name = clean(_cell(row, ext, "vernacularName"))
            if key in keys and name and (key not in best or len(name) < len(best[key])):
                best[key] = name[:300]
    log(f"English common names for {len(best)} species")
    return best


def rewrite_common_names(writer: Writer, commons: dict[str, str]) -> None:
    """Common names come from an extension read after the core, so patch them into the taxa file."""
    writer.close()
    if not commons:
        return
    tmp = writer.taxa_path.with_suffix(".tmp")
    with gzip.open(writer.taxa_path, "rt", encoding="utf-8", newline="") as src, \
            gzip.open(tmp, "wt", encoding="utf-8", newline="") as dst:
        out = csv.writer(dst, delimiter="\t", lineterminator="\n")
        for row in csv.reader(src, delimiter="\t"):
            if not row[5] and row[0] in commons:
                row[5] = commons[row[0]]
            out.writerow(row)
    tmp.replace(writer.taxa_path)


# --------------------------------------------------------------------------- NCBI

def _dmp(path: Path) -> Iterator[list[str]]:
    with path.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            yield [c.strip() for c in line.rstrip("\n").rstrip("|").split("\t|")]


def ncbi_kingdom(domain: str, kingdom: str, division: str) -> str:
    if domain in ("Bacteria", "Archaea"):
        return domain
    if division in ("3", "9"):
        return "Viruses"
    if domain == "Eukaryota":
        return {"Metazoa": "Animalia", "Viridiplantae": "Plantae", "Fungi": "Fungi"}.get(kingdom, "Protista")
    return "Undesignated"


def parse_ncbi(raw_dir: Path, writer: Writer) -> None:
    base = raw_dir / "ncbi"
    nodes: dict[str, tuple[str, str]] = {}
    for c in _dmp(base / "nodes.dmp"):
        nodes[c[0]] = (c[2], c[4])  # rank, division_id
    log(f"[ncbi] nodes {len(nodes)}")
    species_names: dict[str, str] = {}
    authority: dict[str, str] = {}
    common: dict[str, str] = {}
    synonyms: list[tuple[str, str]] = []
    for c in _dmp(base / "names.dmp"):
        tax_id, name, name_class = c[0], c[1], c[3]
        if nodes.get(tax_id, ("", ""))[0] != "species":
            continue
        if name_class == "scientific name":
            species_names[tax_id] = name
        elif name_class == "authority":
            authority.setdefault(tax_id, name)
        elif name_class in ("genbank common name", "common name"):
            if name_class == "genbank common name" or tax_id not in common:
                common[tax_id] = name
        elif name_class == "synonym":
            synonyms.append((tax_id, name))
    kept, dropped = 0, 0
    for c in _dmp(base / "rankedlineage.dmp"):
        tax_id = c[0]
        if tax_id not in species_names:
            continue
        name = clean(species_names[tax_id])
        _, _, _, genus, family, order, klass, phylum, nk, domain = (c + [""] * 10)[:10]
        kingdom = ncbi_kingdom(domain, nk, nodes[tax_id][1])
        if kingdom == "Viruses":
            ok = not NCBI_VIRUS_JUNK.search(name)
        else:
            ok = bool(BINOMIAL.match(name))
        if not ok:
            dropped += 1
            continue
        auth = authority.get(tax_id)
        author = auth[len(name):].strip(" ,") if auth and auth.startswith(name) else None
        lineage = [v for v in (nk if kingdom != "Viruses" else domain, phylum, klass, order, family, genus) if v]
        writer.taxon(tax_id, name, kingdom, lineage, author, common.get(tax_id), {
            "ncbi_id": int(tax_id), "scientific_name": name, "taxonomic_status": "accepted",
            "kingdom_raw": nk or domain, "domain": domain, "phylum": phylum, "class": klass, "order": order,
            "family": family, "genus": genus, "source_dataset": "ncbi_taxonomy_new_taxdump",
        })
        kept += 1
    log(f"[ncbi] formal species kept {kept}; placeholder/environmental names dropped {dropped}")
    for i, (tax_id, name) in enumerate(synonyms):
        name = clean(name)
        if tax_id in writer.keys and name and name != species_names.get(tax_id):
            writer.synonym(f"{tax_id}:{i}", tax_id, name)
    writer.close()


# --------------------------------------------------------------------------- ITIS

def parse_itis(raw_dir: Path, writer: Writer) -> None:
    with zipfile.ZipFile(raw_dir / "itis_sqlite.zip") as archive, tempfile.TemporaryDirectory(dir=raw_dir) as tmp:
        member = next(n for n in archive.namelist() if n.endswith(".sqlite"))
        db_path = Path(archive.extract(member, tmp))
        db = sqlite3.connect(db_path)
        cur = db.cursor()
        cur.execute("SELECT kingdom_id, kingdom_name FROM kingdoms")
        kingdoms = dict(cur.fetchall())
        cur.execute("SELECT rank_id, kingdom_id, rank_name FROM taxon_unit_types")
        rank_names = {(r, k): n.lower() for r, k, n in cur.fetchall()}
        cur.execute("SELECT taxon_author_id, taxon_author FROM taxon_authors_lkp")
        authors = dict(cur.fetchall())
        cur.execute("SELECT tsn, vernacular_name FROM vernaculars WHERE lower(language) = 'english'")
        common: dict[int, str] = {}
        for tsn, name in cur.fetchall():
            if tsn not in common or len(name) < len(common[tsn]):
                common[tsn] = name
        cur.execute("SELECT tsn, parent_tsn, complete_name, rank_id, kingdom_id FROM taxonomic_units")
        parents = {tsn: (parent, name, rank_names.get((rank, k), "")) for tsn, parent, name, rank, k in cur.fetchall()}

        def lineage_of(tsn: int) -> dict[str, str]:
            out: dict[str, str] = {}
            seen = 0
            while tsn and tsn in parents and seen < 60:
                parent, name, rank = parents[tsn]
                if rank in LINEAGE_KEYS and rank not in out:
                    out[rank] = name
                tsn, seen = parent, seen + 1
            return out

        cur.execute(
            "SELECT tsn, complete_name, taxon_author_id, kingdom_id, rank_id, name_usage FROM taxonomic_units "
            "WHERE name_usage IN ('valid', 'accepted')"
        )
        for tsn, name, author_id, kingdom_id, rank_id, usage in cur.fetchall():
            if rank_names.get((rank_id, kingdom_id)) != "species":
                continue
            name = clean(name)
            if not name:
                continue
            line = lineage_of(tsn)
            raw_kingdom = kingdoms.get(kingdom_id)
            writer.taxon(str(tsn), name, normalize_kingdom(raw_kingdom),
                         [line[k] for k in LINEAGE_KEYS if k in line], authors.get(author_id), common.get(tsn), {
                             "itis_tsn": tsn, "scientific_name": name, "taxonomic_status": usage,
                             "kingdom_raw": raw_kingdom, **{k: line.get(k) for k in LINEAGE_KEYS[1:]},
                             "source_dataset": "itis_sqlite",
                         })
        cur.execute(
            "SELECT s.tsn, s.tsn_accepted, t.complete_name FROM synonym_links s "
            "JOIN taxonomic_units t ON t.tsn = s.tsn"
        )
        for tsn, accepted, name in cur.fetchall():
            if str(accepted) in writer.keys and name:
                writer.synonym(str(tsn), str(accepted), clean(name))
        db.close()
    writer.close()


# --------------------------------------------------------------------------- GTDB

def parse_gtdb(raw_dir: Path, writer: Writer, version: str) -> None:
    placeholder = 0
    for fname in ("gtdb_bac120_taxonomy.tsv.gz", "gtdb_ar53_taxonomy.tsv.gz"):
        with gzip.open(raw_dir / fname, "rt", encoding="utf-8") as fh:
            for line in fh:
                accession, lineage = line.rstrip("\n").split("\t")
                parts = dict(p.split("__", 1) for p in lineage.split(";"))
                name = parts.get("s", "")
                if not name or name in writer.keys:
                    continue
                if "_" in name or not BINOMIAL.match(name):
                    placeholder += 1
                    writer.keys.add(name)
                    continue
                domain = parts.get("d", "")
                ranks = [parts.get(r, "") for r in ("p", "c", "o", "f", "g")]
                writer.taxon(name, name, domain if domain in ("Bacteria", "Archaea") else "Undesignated",
                             [v for v in [domain] + ranks if v], None, None, {
                                 "gtdb_species": name, "scientific_name": name, "taxonomic_status": "accepted",
                                 "kingdom_raw": domain, "phylum": ranks[0], "class": ranks[1], "order": ranks[2],
                                 "family": ranks[3], "genus": ranks[4], "gtdb_representative": accession,
                                 "source_dataset": f"gtdb_{version}",
                             })
    log(f"[gtdb] placeholder species skipped (e.g. 'Genus sp000123'): {placeholder}")
    writer.close()


# --------------------------------------------------------------------------- ICTV

def parse_ictv(raw_dir: Path, writer: Writer) -> None:
    path = sorted(raw_dir.glob("ictv_msl*.xlsx"))[-1]
    book = openpyxl.load_workbook(path, read_only=True, data_only=True)
    sheet = next(ws for ws in book.worksheets if "msl" in ws.title.lower() or "species" in ws.title.lower())
    rows = sheet.iter_rows(values_only=True)
    header: list[str] = []
    for row in rows:
        cells = [str(c).strip() if c is not None else "" for c in row]
        if "Species" in cells and "Genus" in cells:
            header = cells
            break
    idx = {name: i for i, name in enumerate(header)}
    for row in rows:
        cells = [str(c).strip() if c is not None else "" for c in row]
        name = clean(cells[idx["Species"]]) if len(cells) > idx["Species"] else None
        if not name:
            continue
        get = lambda col: cells[idx[col]] if col in idx and idx[col] < len(cells) else ""  # noqa: E731
        lineage = [v for v in (get("Realm"), get("Kingdom"), get("Phylum"), get("Class"), get("Order"),
                               get("Family"), get("Genus")) if v]
        writer.taxon(name, name, "Viruses", lineage, None, None, {
            "ictv_species": name, "scientific_name": name, "taxonomic_status": "accepted",
            "kingdom_raw": get("Kingdom") or get("Realm"), "realm": get("Realm"), "phylum": get("Phylum"),
            "class": get("Class"), "order": get("Order"), "family": get("Family"), "genus": get("Genus"),
            "genome_composition": get("Genome Composition") or get("Genome composition"),
            "source_dataset": path.stem,
        })
    writer.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", choices=["gbif", "col", "ncbi", "itis", "gtdb", "ictv"], required=True)
    parser.add_argument("--raw-dir", default="/w/raw")
    parser.add_argument("--out-dir", default="/w/norm")
    parser.add_argument("--dataset-version", default="")
    args = parser.parse_args()
    raw, out = Path(args.raw_dir), Path(args.out_dir)
    csv.field_size_limit(sys.maxsize)
    writer = Writer(out, args.source)
    if args.source == "gbif":
        parse_dwca("gbif", raw / "gbif_backbone.zip", writer, frozenset({"accepted"}),
                   f"gbif_backbone_dwca_{args.dataset_version}".rstrip("_"))
    elif args.source == "col":
        parse_dwca("col", raw / "col_latest_dwca.zip", writer, frozenset({"accepted", "provisionally accepted"}),
                   f"catalogue_of_life_{args.dataset_version}".rstrip("_"))
    elif args.source == "ncbi":
        parse_ncbi(raw, writer)
    elif args.source == "itis":
        parse_itis(raw, writer)
    elif args.source == "gtdb":
        parse_gtdb(raw, writer, args.dataset_version or "latest")
    elif args.source == "ictv":
        parse_ictv(raw, writer)


if __name__ == "__main__":
    main()
