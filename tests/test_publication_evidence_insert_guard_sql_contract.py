"""Static SQL contract checks only; no PostgreSQL execution or acceptance claim.

Run with bundled Python and -B. The tests inspect the repair overlay, never the
mutable owner checkout. The UPDATE guard retains the pinned immutable-source
and terminal-transition rules except for the approved blank-reviewer hardening
and primary-key identity additions to the immutable-source ROW comparisons.
"""
from __future__ import annotations

import hashlib
import re
import unittest
from pathlib import Path


MIGRATION = (
    Path(__file__).resolve().parents[1]
    / "migrations"
    / "20261003_publication_taxon_evidence_OCT03_2026.sql"
)
FROZEN_UPDATE_GUARD_SHA256 = (
    "7780cf0863fe3a288e644aacb211a65f3e22c88919b1b590add62e88d97edce3"
)
HARDENED_REVIEWER_CHECK = (
    "IF NEW.evidence_state <> OLD.evidence_state\n"
    "       AND (NEW.reviewed_by IS NULL OR NEW.reviewed_by !~ '[^[:space:]]') THEN"
)
FROZEN_REVIEWER_CHECK = (
    "IF NEW.evidence_state <> OLD.evidence_state AND "
    "NULLIF(BTRIM(NEW.reviewed_by), '') IS NULL THEN"
)


def function_statement(sql: str, name: str) -> str:
    match = re.search(
        rf"CREATE OR REPLACE FUNCTION bio\.{re.escape(name)}\(\).*?\$\$;",
        sql,
        flags=re.DOTALL,
    )
    if match is None:
        raise AssertionError(f"missing SQL function: {name}")
    return match.group(0)


class PublicationEvidenceInsertGuardSqlContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.sql = MIGRATION.read_text(encoding="utf-8")
        cls.insert_guard = function_statement(
            cls.sql, "guard_publication_taxon_evidence_insert_review"
        )

    def test_terminal_insert_guard_covers_both_dispositions_and_blank_reviewers(self):
        # The POSIX nonspace expression handles spaces, tabs, and line breaks.
        # Requiring this expression inside the terminal-state IF catches the
        # original UPDATE-only gap and a guard attached to unrelated states.
        self.assertRegex(
            self.insert_guard,
            r"IF\s+NEW\.evidence_state\s+IN\s*"
            r"\('accepted_source_attested',\s*'rejected'\)\s+"
            r"AND\s+\(NEW\.reviewed_by\s+IS\s+NULL\s+OR\s+"
            r"NEW\.reviewed_by\s+!~\s+'\[\^\[:space:\]\]'\)\s+"
            r"THEN\s+RAISE\s+EXCEPTION\s+"
            r"'a reviewer identity is required for evidence disposition';",
        )
        self.assertNotIn("OLD.", self.insert_guard)
        self.assertIn("RETURN NEW;", self.insert_guard)

    def test_guard_is_attached_to_insert_on_the_evidence_table(self):
        self.assertRegex(
            self.sql,
            r"CREATE TRIGGER trg_publication_taxon_evidence_insert_review\s+"
            r"BEFORE INSERT ON bio\.publication_taxon_evidence\s+"
            r"FOR EACH ROW EXECUTE FUNCTION "
            r"bio\.guard_publication_taxon_evidence_insert_review\(\);",
        )
        # Reapplication replaces this trigger rather than stacking instances.
        self.assertIn(
            "DROP TRIGGER IF EXISTS trg_publication_taxon_evidence_insert_review "
            "ON bio.publication_taxon_evidence;",
            self.sql,
        )
        self.assertLess(
            self.sql.index("CREATE TRIGGER trg_publication_taxon_evidence_insert_review"),
            self.sql.rindex("COMMIT;"),
        )

    def test_update_guard_retains_pinned_rules_except_approved_identity_and_reviewer_changes(self):
        update_guard = function_statement(
            self.sql, "guard_publication_taxon_evidence_immutability"
        )
        self.assertEqual(update_guard.count(HARDENED_REVIEWER_CHECK), 1)
        self.assertEqual(update_guard.count("NEW.evidence_id"), 1)
        self.assertEqual(update_guard.count("OLD.evidence_id"), 1)
        self.assertIn("NEW.evidence_id, NEW.publication_id", update_guard)
        self.assertIn("OLD.evidence_id, OLD.publication_id", update_guard)
        normalized_to_frozen = update_guard.replace(
            HARDENED_REVIEWER_CHECK, FROZEN_REVIEWER_CHECK
        )
        normalized_to_frozen = normalized_to_frozen.replace(
            "NEW.evidence_id, NEW.publication_id", "NEW.publication_id"
        ).replace("OLD.evidence_id, OLD.publication_id", "OLD.publication_id")
        self.assertEqual(
            hashlib.sha256(normalized_to_frozen.encode("utf-8")).hexdigest(),
            FROZEN_UPDATE_GUARD_SHA256,
        )
        self.assertRegex(
            self.sql,
            r"CREATE TRIGGER trg_publication_taxon_evidence_immutable\s+"
            r"BEFORE UPDATE ON bio\.publication_taxon_evidence\s+"
            r"FOR EACH ROW EXECUTE FUNCTION "
            r"bio\.guard_publication_taxon_evidence_immutability\(\);",
        )

    def test_contract_checks_reject_six_faulty_guard_mutations(self):
        # Exercise the contract checks against intentional regressions without
        # modifying the migration artifact or executing SQL.
        update_method = (
            "test_update_guard_retains_pinned_rules_except_approved_identity_and_reviewer_changes"
        )
        insert_method = (
            "test_terminal_insert_guard_covers_both_dispositions_and_blank_reviewers"
        )
        controls = [
            (
                "missing INSERT event",
                self.sql.replace("BEFORE INSERT ON", "BEFORE UPDATE ON"),
                "test_guard_is_attached_to_insert_on_the_evidence_table",
            ),
            (
                "missing rejected-state check",
                self.sql.replace(
                    "('accepted_source_attested', 'rejected')",
                    "('accepted_source_attested')",
                ),
                insert_method,
            ),
            (
                "missing INSERT whitespace handling",
                self.sql.replace(
                    "NEW.reviewed_by !~ '[^[:space:]]'", "NEW.reviewed_by = ''", 1
                ),
                insert_method,
            ),
            (
                "altered UPDATE source guard",
                self.sql.replace(
                    "NEW.attribution, NEW.recorded_at, NEW.metadata",
                    "NEW.attribution, NEW.recorded_at",
                ),
                update_method,
            ),
            (
                "regressed UPDATE reviewer predicate",
                self.sql.replace(HARDENED_REVIEWER_CHECK, FROZEN_REVIEWER_CHECK),
                update_method,
            ),
            (
                "missing evidence primary-key comparisons",
                self.sql.replace("NEW.evidence_id, ", "").replace(
                    "OLD.evidence_id, ", ""
                ),
                update_method,
            ),
        ]
        self.assertEqual(len(controls), 6)
        for label, mutated_sql, method_name in controls:
            with self.subTest(mutation=label):
                case = type(self)(methodName=method_name)
                case.sql = mutated_sql
                case.insert_guard = function_statement(
                    mutated_sql, "guard_publication_taxon_evidence_insert_review"
                )
                with self.assertRaises(AssertionError):
                    getattr(case, method_name)()


if __name__ == "__main__":
    unittest.main(verbosity=2)
