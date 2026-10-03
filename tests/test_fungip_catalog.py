"""Offline scientific/provenance tests. No network, wallet, server or production data."""
import copy
import json
import unittest
from fungip_local_inputs import local_input
from pathlib import Path
from uuid import uuid4
from mindex_etl.fungip.catalog import canonical, digest, its_span, resolve
from mindex_etl.fungip.detail import detail, fasta
from mindex_etl.fungip.drafts import draft


def entry():
    record = {"species_id":"FG001","accepted_name":"Pleurotus ostreatus","requested_name":"Pleurotus ostreatus",
              "ticker":"OYSTER","taxonomy":{"KINGDOM":"Fungi"},"taxonomy_source":"col_xr",
              "dna":{"sequence":"ACGTACGT","accession_version":"NR_123.1","sha256":digest(b"ACGTACGT")},"image":None}
    return {"species_id":"FG001","record":record,"external_ids":[{"source":"inat","external_id":"123"}],
            "record_sha256":digest(canonical(record).encode()),"errors":[],"missing_data_flags":[],"verified_its_sequence":None,"sequence_valid":True,"image_valid":False}


class CatalogTests(unittest.TestCase):
    def test_namespace_does_not_equate_numeric_ids(self):
        e = entry()
        self.assertEqual(resolve(e,[{"source":"gbif","external_id":"123","taxon_id":str(uuid4()),"rank":"species","canonical_name":"Pleurotus ostreatus"}]),(None,"unresolved"))

    def test_exact_source_rank_name_required(self):
        e = entry(); uid = str(uuid4())
        c = {"source":"inat","external_id":"123","taxon_id":uid,"rank":"species","canonical_name":"Pleurotus ostreatus"}
        self.assertEqual(resolve(e,[c]),(uid,"resolved"))
        self.assertEqual(resolve(e,[{**c,"rank":"genus"}]),(None,"identity_conflict"))
        self.assertEqual(resolve(e,[{**c,"canonical_name":"Pleurotus pulmonarius"}]),(None,"identity_conflict"))
        self.assertEqual(resolve(e,[c,{**c,"taxon_id":str(uuid4())}]),(None,"source_conflict"))

    def test_its_fuzzy_or_note_only_is_not_complete_its(self):
        dna = {"sequence":"ACGTACGT","its_sequence":"CGTACG","its_span_1based_inclusive":[2,7],
               "its_features":[{"location":"2..3","qualifiers":{"product":["internal transcribed spacer 1"]}},
                               {"location":"5..7","qualifiers":{"product":["internal transcribed spacer 2"]}}]}
        self.assertEqual(its_span(dna),"CGTACG")
        for location in ("<2..3","2..>3","complement(2..3)","join(2..3,5..7)"):
            altered = copy.deepcopy(dna); altered["its_features"][0]["location"] = location
            self.assertIsNone(its_span(altered))
        self.assertIsNone(its_span({**dna,"its_features":[{"location":"<1..>8","qualifiers":{"note":["contains ITS1 and ITS2"]}}]}))

    def test_page_verification_is_record_version_and_identity_bound(self):
        e = entry(); uid = str(uuid4()); row = {**e,"taxon_id":uid,"validation_errors":[]}
        v = {"taxon_id":uid,"canonical_url":f"https://mycosoft.com/natureos/ancestry/species/{uid}","record_sha256":e["record_sha256"],
             "evidence":dict.fromkeys(["name_checked","taxonomy_checked","dna_checked","download_checked","attribution_checked"],True)}
        self.assertIsNone(detail(row)["canonical_url"])
        self.assertTrue(detail(row,v)["live_verified"])
        self.assertFalse(detail(row,{**v,"record_sha256":"a"*64})["live_verified"])
        self.assertFalse(detail(row,{**v,"taxon_id":str(uuid4())})["live_verified"])

    def test_fasta_and_full_reference_are_separate(self):
        e = entry(); row = {**e,"taxon_id":None,"validation_errors":[]}
        view = detail(row)
        self.assertIn("ACGTACGT",fasta(view,"reference"))
        with self.assertRaises(ValueError): fasta(view,"its")
        bad = detail({**row,"validation_errors":["sequence_hash_or_length_mismatch"],"sequence_valid":False})
        with self.assertRaises(ValueError): fasta(bad,"reference")

    def test_draft_has_no_invented_link_wallet_or_launch(self):
        d = draft(entry())
        self.assertIsNone(d["canonical_species_url"])
        self.assertIsNone(d["verified_recipient"])
        self.assertEqual(d["initial_buy"],0)
        self.assertEqual(d["status"],"draft")
        self.assertIn("image_visual_and_license_review_pending",d["release_blockers"])

    def test_public_projection_excludes_internal_receipt_recipient_and_wallet(self):
        e = entry()
        tokens = [{"status":"submitted-unknown","recipient_identity":{"private_observation":"fixture"},"recipient_public_wallet":"fixture","finalized_evidence":{"raw":"fixture"}}]
        view = detail({**e,"taxon_id":None,"validation_errors":[]},tokens=tokens)
        self.assertNotIn("recipient_identity",view["tokens"][0])
        self.assertNotIn("finalized_evidence",view["tokens"][0])
        self.assertNotIn("recipient_public_wallet",view["tokens"][0])

    def test_actual_audit_preserves_all_300_and_quarantines_fg056(self):
        report = json.loads(local_input("FUNGIP_CAPTURED_AUDIT").read_text(encoding="utf-8"))
        self.assertEqual(report["counts"],{"staged":300,"production_imported":0,"sequences":300,"images":258,"supported_its":42,"record_errors":0})
        record = next(x for x in report["records"] if x["species_id"] == "FG056")
        self.assertIsNone(record["verified_its_sequence"])
        self.assertTrue(record["record"]["dna"]["sequence"])
        self.assertIn("unsupported_its_extraction_quarantined",record["missing_data_flags"])
        self.assertEqual(report["hash_conflicts"],[])


if __name__ == "__main__": unittest.main()
