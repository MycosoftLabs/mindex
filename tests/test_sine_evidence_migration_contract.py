from __future__ import annotations

from pathlib import Path


def test_sine_analysis_evidence_migration_defines_required_tables_without_fake_rows() -> None:
    sql = Path("migrations/20260606_sine_analysis_evidence_jun06_2026.sql").read_text(
        encoding="utf-8"
    ).lower()

    for table in (
        "sine.model_output",
        "sine.prototype_match",
        "sine.fusion_evidence",
        "sine.sound_transcript",
    ):
        assert f"create table if not exists {table}" in sql
        assert f"grant select, insert, update, delete on {table} to mindex" in sql

    assert "references library.analysis_run" in sql
    assert "references library.blob" in sql
    assert "references sine.model_artifact" in sql
    assert "references sine.prototype" in sql
    assert "references library.detection_event" in sql
    assert "insert into" not in sql
