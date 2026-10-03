"""Real importer against an in-memory SQL dialect adapter; NOT PostgreSQL qualification."""
import contextlib
import copy
import json
import sqlite3
import unittest
from uuid import uuid4
from mindex_etl.fungip.importer import import_manifest
from mindex_etl.fungip.catalog import canonical, digest


class Result:
    def __init__(self, cursor): self.cursor = cursor
    def fetchone(self):
        row = self.cursor.fetchone()
        return dict(row) if row else None
    def fetchall(self): return [dict(row) for row in self.cursor.fetchall()]


class MemorySQL:
    def __init__(self):
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""CREATE TABLE taxon(id TEXT PRIMARY KEY,canonical_name TEXT,rank TEXT);
          CREATE TABLE taxon_external_id(source TEXT,external_id TEXT,taxon_id TEXT);
          CREATE TABLE import_run(catalog_sha256 TEXT PRIMARY KEY,manifest TEXT);
          CREATE TABLE import_observation(catalog_sha256 TEXT,report TEXT);
          CREATE TABLE species(species_id TEXT PRIMARY KEY,taxon_id TEXT UNIQUE,accepted_name TEXT,record TEXT,
          external_ids TEXT,verified_its_sequence TEXT,missing_data_flags TEXT,validation_errors TEXT,record_sha256 TEXT,
          catalog_sha256 TEXT,resolution_status TEXT,updated_at TEXT,image_valid BOOLEAN,sequence_valid BOOLEAN);
          CREATE TABLE species_revision(species_id TEXT,record_sha256 TEXT,record TEXT,catalog_sha256 TEXT,PRIMARY KEY(species_id,record_sha256));""")
    @contextlib.contextmanager
    def transaction(self):
        with self.db: yield
    def execute(self,sql,params=()):
        if "pg_advisory" in sql: return None
        sql = sql.replace("core.","").replace("fungip.","").replace("::jsonb","").replace(" FOR UPDATE","").replace("%s","?").replace("now()","CURRENT_TIMESTAMP")
        return Result(self.db.execute(sql,params))


def manifest():
    r = {"species_id":"FG001","accepted_name":"Pleurotus ostreatus"}
    e = {"species_id":"FG001","record":r,"record_sha256":digest(canonical(r).encode()),
         "external_ids":[{"source":"inat","external_id":"123"}],"verified_its_sequence":None,
         "missing_data_flags":["canonical_uuid_unresolved"],"errors":[],"image_valid":False,"sequence_valid":False}
    return {"catalog_sha256":"a"*64,"hash_conflicts":[],"duplicates":{},"records":[e]}


class ImportTests(unittest.TestCase):
    def test_import_is_idempotent_and_preserves_revisions(self):
        db = MemorySQL(); m = manifest()
        self.assertEqual(import_manifest(db,m)["inserted"],1)
        self.assertEqual(import_manifest(db,m)["unchanged"],1)
        revised = copy.deepcopy(m); revised["catalog_sha256"] = "b"*64
        revised["records"][0]["record"]["description"] = "New reviewed source fact"
        revised["records"][0]["record_sha256"] = digest(canonical(revised["records"][0]["record"]).encode())
        self.assertEqual(import_manifest(db,revised)["updated"],1)
        self.assertEqual(db.db.execute("SELECT COUNT(*) FROM species").fetchone()[0],1)
        self.assertEqual(db.db.execute("SELECT COUNT(*) FROM species_revision").fetchone()[0],2)

    def test_unresolved_becomes_uuid_without_creating_core_taxon(self):
        db = MemorySQL(); m = manifest(); import_manifest(db,m)
        uid = str(uuid4())
        db.db.execute("INSERT INTO taxon VALUES (?,?,?)",(uid,"Pleurotus ostreatus","species"))
        db.db.execute("INSERT INTO taxon_external_id VALUES (?,?,?)",("inat","123",uid))
        self.assertEqual(import_manifest(db,m)["resolved"],1)
        self.assertEqual(db.db.execute("SELECT taxon_id FROM species").fetchone()[0],uid)
        self.assertEqual(db.db.execute("SELECT COUNT(*) FROM taxon").fetchone()[0],1)

    def test_known_identity_is_not_silently_retargeted(self):
        db = MemorySQL(); m = manifest(); uid = str(uuid4())
        db.db.execute("INSERT INTO taxon VALUES (?,?,?)",(uid,"Pleurotus ostreatus","species"))
        db.db.execute("INSERT INTO taxon_external_id VALUES (?,?,?)",("inat","123",uid))
        import_manifest(db,m)
        db.db.execute("DELETE FROM taxon_external_id")
        report = import_manifest(db,m)
        self.assertEqual(report["conflicts"][0]["reason"],"mapping_changed_requires_curation")
        self.assertEqual(db.db.execute("SELECT taxon_id FROM species").fetchone()[0],uid)

    def test_corrupt_manifest_rolls_back_and_does_not_ingest(self):
        db = MemorySQL(); m = manifest(); m["hash_conflicts"] = ["image.jpg"]
        with self.assertRaises(ValueError): import_manifest(db,m)
        self.assertEqual(db.db.execute("SELECT COUNT(*) FROM species").fetchone()[0],0)

    def test_incomplete_error_record_is_preserved(self):
        db = MemorySQL(); m = manifest(); m["records"][0]["errors"] = ["missing_accession_version"]
        report = import_manifest(db,m)
        self.assertEqual(report["inserted"],1)
        self.assertEqual(json.loads(db.db.execute("SELECT validation_errors FROM species").fetchone()[0]),["missing_accession_version"])

    def test_same_record_hash_requalification_updates_derived_fields_and_history(self):
        db = MemorySQL(); m = manifest(); import_manifest(db,m)
        changed = copy.deepcopy(m)
        changed["records"][0].update({"errors":["invalid_sequence_alphabet"],"verified_its_sequence":None,"missing_data_flags":["qualification_changed"],"image_valid":True})
        self.assertEqual(import_manifest(db,changed)["updated"],1)
        row = db.db.execute("SELECT * FROM species").fetchone()
        self.assertEqual(json.loads(row["validation_errors"]),["invalid_sequence_alphabet"])
        self.assertEqual(json.loads(row["missing_data_flags"]),["qualification_changed"])
        self.assertTrue(row["image_valid"])
        self.assertEqual(db.db.execute("SELECT COUNT(*) FROM import_observation").fetchone()[0],2)


if __name__ == "__main__": unittest.main()
