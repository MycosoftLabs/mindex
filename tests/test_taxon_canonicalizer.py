from __future__ import annotations

import pytest

from mindex_etl.taxon_canonicalizer import normalize_name


def test_normalize_name_trims_whitespace():
    assert normalize_name("  Agaricus   campestris ") == "Agaricus campestris"


def test_normalize_name_raises_for_empty():
    with pytest.raises(ValueError):
        normalize_name(" ")
