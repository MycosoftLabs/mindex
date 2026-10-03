"""Offline list/detail availability contracts; no router, ASGI or database startup."""
import ast
import re
import unittest
from pathlib import Path
from types import SimpleNamespace

from mindex_etl.fungip.detail import detail, fasta


def list_availability(row, alias):
    """Evaluate the authored SQL's limited boolean/null predicate without a database.

    These fields have non-null boolean validity flags. The supported expression
    subset has the same boolean/null behavior in Python and PostgreSQL.
    """
    path = Path(__file__).parents[1] / "mindex_api/routers/fungip.py"
    router = ast.parse(path.read_text(encoding="utf-8"))
    collection = next(node for node in router.body
                      if isinstance(node, ast.AsyncFunctionDef) and node.name == "collection")
    statements = [node.args[0].value for node in ast.walk(collection)
                  if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                  and node.func.id == "text" and node.args
                  and isinstance(node.args[0], ast.Constant)
                  and isinstance(node.args[0].value, str)]
    if len(statements) != 1:
        raise AssertionError("Expected one authored collection query")
    match = re.search(rf"(?m)^\s*(.*?)\s+AS\s+{re.escape(alias)}\s*,?\s*$", statements[0])
    if not match:
        raise AssertionError(f"Missing collection availability projection: {alias}")
    expression = re.sub(r"\bIS\s+NOT\s+NULL\b", "is not None", match[1])
    expression = re.sub(r"\bAND\b", "and", expression)
    predicate = ast.parse(expression, mode="eval")
    supported = (ast.Expression, ast.BoolOp, ast.And, ast.Compare, ast.IsNot,
                 ast.Attribute, ast.Name, ast.Load, ast.Constant)
    for node in ast.walk(predicate):
        if not isinstance(node, supported):
            raise AssertionError("Availability predicate exceeds reviewed pure subset")
        if isinstance(node, ast.Name) and node.id != "s":
            raise AssertionError("Unexpected availability identifier")
        if isinstance(node, ast.Attribute) and (
                not isinstance(node.value, ast.Name) or node.value.id != "s"
                or node.attr not in {"sequence_valid", "verified_its_sequence"}):
            raise AssertionError("Unexpected availability column")
        if isinstance(node, ast.Constant) and node.value is not None:
            raise AssertionError("Unexpected availability constant")
    return bool(eval(compile(predicate, str(path), "eval"), {"__builtins__": {}},
                     {"s": SimpleNamespace(**row)}))


def species_row(sequence_valid, its_sequence):
    return {
        "species_id": "FG001", "taxon_id": None,
        "record": {
            "accepted_name": "Pleurotus ostreatus", "requested_name": "Pleurotus ostreatus",
            "dna": {"sequence": "ACGTACGT", "accession_version": "NR_123.1",
                    "its_span_1based_inclusive": [2, 7]},
        },
        "external_ids": [], "missing_data_flags": [],
        "validation_errors": [] if sequence_valid else ["sequence_hash_or_length_mismatch"],
        "record_sha256": "a" * 64, "sequence_valid": sequence_valid,
        "verified_its_sequence": its_sequence, "image_valid": False,
    }


class AvailabilityTests(unittest.TestCase):
    def test_invalid_reference_with_retained_its_is_unavailable_in_list_and_detail(self):
        # Extraction can survive a later reference/accession qualification failure.
        row = species_row(False, "CGTACG")
        self.assertIsNotNone(row["verified_its_sequence"])
        self.assertFalse(list_availability(row, "reference_dna_available"))
        self.assertFalse(list_availability(row, "complete_its_available"))
        view = detail(row)
        self.assertIsNone(view["dna"]["sequence"])
        self.assertIsNone(view["dna"]["its_sequence"])
        for kind in ("reference", "its"):
            with self.subTest(kind=kind), self.assertRaisesRegex(ValueError, "Validated sequence unavailable"):
                fasta(view, kind)

    def test_valid_reference_and_supported_its_remain_available(self):
        row = species_row(True, "CGTACG")
        self.assertTrue(list_availability(row, "reference_dna_available"))
        self.assertTrue(list_availability(row, "complete_its_available"))
        view = detail(row)
        self.assertEqual(view["dna"]["sequence"], "ACGTACGT")
        self.assertEqual(view["dna"]["its_sequence"], "CGTACG")
        self.assertIn("\nACGTACGT\n", fasta(view, "reference"))
        self.assertIn("\nCGTACG\n", fasta(view, "its"))

    def test_missing_its_stays_unavailable_for_valid_and_invalid_references(self):
        for valid in (False, True):
            with self.subTest(sequence_valid=valid):
                row = species_row(valid, None)
                self.assertEqual(list_availability(row, "reference_dna_available"), valid)
                self.assertFalse(list_availability(row, "complete_its_available"))
                view = detail(row)
                self.assertIsNone(view["dna"]["its_sequence"])
                with self.assertRaisesRegex(ValueError, "Validated sequence unavailable"):
                    fasta(view, "its")


if __name__ == "__main__":
    unittest.main()
