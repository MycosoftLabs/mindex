from __future__ import annotations

from mindex_etl.jobs import sync_magnetoreception_davidson2026 as job


class FakeCursor:
    def __init__(self, db):
        self.db, self._rows, self.rowcount = db, [], 0

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=()):
        s = " ".join(sql.split())
        self.rowcount, self._rows = 0, []
        if s.startswith("SELECT pg_try_advisory_xact_lock"):
            self._rows = [{"locked": self.db.get("lock_available", True)}]
        elif s.startswith("SELECT DISTINCT taxon_id FROM core.taxon_external_id"):
            self._rows = [
                {"taxon_id": t}
                for (src, ext), t in self.db["ext"].items()
                if src == "gbif" and ext == params[0]
            ]
        elif s.startswith("SELECT id FROM core.taxon"):
            self._rows = [
                {"id": t}
                for t, (n, k) in self.db["taxa"].items()
                if n.lower() == params[0].lower() and k in (params[1], None)
            ]
        elif s.startswith("INSERT INTO core.taxon ("):
            tid = f"t{len(self.db['taxa']) + 1}"
            self.db["taxa"][tid] = (params[0], params[3])
            self._rows = [{"id": tid}]
        elif s.startswith("INSERT INTO core.taxon_external_id"):
            self.db["ext"].setdefault(("gbif", params[1]), params[0])
        elif s.startswith("INSERT INTO bio.taxon_trait"):
            key = (params[0], params[1], params[3])
            if key not in self.db["traits"]:
                self.db["traits"][key] = params[2]
                self.rowcount = 1
        elif s.startswith("INSERT INTO core.publications"):
            actual_id = self.db.get("existing_publication_id", params[0])
            self.db["pubs"][actual_id] = params
            self._rows = [{"id": actual_id}]
        elif s.startswith("INSERT INTO bio.publication_taxon"):
            self.rowcount = int(params not in self.db["pubtax"])
            self.db["pubtax"].add(params)

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0]


class FakeConn:
    def __init__(self):
        self.db = {
            "taxa": {"existing": ("Saccharomyces cerevisiae", "Fungi")},
            "ext": {},
            "traits": {},
            "pubs": {},
            "pubtax": set(),
        }

    def cursor(self):
        return FakeCursor(self.db)


def test_real_dataset_shape():
    source, entries = job.load_entries()
    assert source["doi"] == "10.5281/zenodo.24571664" and source["license"] == "CC-BY-4.0"
    counted = [e for e in entries if not e["is_review_row"]]
    assert len(entries) == 1276 and len(counted) == 1270
    assert sum(e["kingdom_source"] == "Fungi" for e in counted) == 63


def test_links_existing_creates_missing_and_is_idempotent():
    source, entries = job.load_entries()
    sample = [
        e
        for e in entries
        if e["scientific_name"]
        in ("Saccharomyces cerevisiae", "Agaricus bisporus", "Fungi (species not named)")
    ]
    conn = FakeConn()
    first = job.run(conn, sample, source)
    assert first["linked_name_match"] == 1 and first["taxa_created"] == 1 and first["unlinked"] == 1
    assert first["traits_inserted"] == 4
    second = job.run(conn, sample, source)
    assert second["traits_inserted"] == 0 and second["taxa_created"] == 0
    assert len(conn.db["taxa"]) == 2 and len(conn.db["pubtax"]) == 2
    assert all("CC BY 4.0" in job.ATTRIBUTION for _ in [0])


def test_ambiguous_existing_names_are_not_duplicated():
    source, entries = job.load_entries()
    sample = [e for e in entries if e["scientific_name"] == "Agaricus bisporus"]
    conn = FakeConn()
    conn.db["taxa"] = {"a": ("Agaricus bisporus", "Fungi"), "b": ("agaricus bisporus", None)}
    stats = job.run(conn, sample, source)
    assert stats["taxa_created"] == 0 and stats["unlinked"] == 1 and len(conn.db["taxa"]) == 2


def test_source_and_cleaned_payload_are_required_and_pinned(tmp_path, monkeypatch):
    import pytest

    monkeypatch.setattr(job, "SOURCE_CSV", tmp_path / "missing.csv")
    with pytest.raises(ValueError, match="source CSV"):
        job.load_entries()
    monkeypatch.undo()
    bad = tmp_path / "entries.json"
    bad.write_text("{}")
    with pytest.raises(ValueError, match="entries sha256"):
        job.load_entries(bad)


def test_existing_publication_primary_key_is_reused():
    source, entries = job.load_entries()
    sample = [e for e in entries if e["scientific_name"] == "Saccharomyces cerevisiae"]
    conn = FakeConn()
    conn.db["existing_publication_id"] = "previously-imported-zenodo-id"
    first = job.run(conn, sample, source)
    assert first["publication_links_inserted"] == 1
    assert all(p[0] == "previously-imported-zenodo-id" for p in conn.db["pubtax"])
    second = job.run(conn, sample, source)
    assert second["publication_links_inserted"] == 0
    assert second["traits_inserted"] == second["taxa_created"] == 0


def test_concurrent_dataset_import_is_refused_before_writes():
    import pytest

    source, entries = job.load_entries()
    conn = FakeConn()
    conn.db["lock_available"] = False
    with pytest.raises(RuntimeError, match="already running"):
        job.run(conn, entries[:1], source)
    assert not conn.db["pubs"] and not conn.db["traits"] and len(conn.db["taxa"]) == 1
