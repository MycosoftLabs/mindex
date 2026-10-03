import csv
import gzip
import json

from datetime import datetime

from mindex_etl.jobs.merge_duplicate_species import Row, effective_kingdom, plan_group
from mindex_etl.jobs.taxonomy_sources import BINOMIAL, Writer, ncbi_kingdom, rewrite_common_names


def make_row(row_id, source, kingdom, effective=None, rank="species", fungip=False, lineage_len=6, day=1):
    return Row(row_id, "abortiporus roseus", "Abortiporus roseus", rank, source, kingdom,
               effective or kingdom, lineage_len, 1, fungip, datetime(2026, 6, day))


def read_rows(path):
    with gzip.open(path, "rt", encoding="utf-8", newline="") as fh:
        return list(csv.reader(fh, delimiter="\t"))


def test_binomial_filter_keeps_formal_names_and_drops_placeholders():
    assert BINOMIAL.match("Amanita muscaria")
    assert BINOMIAL.match("Candidatus Pelagibacter ubique")
    assert not BINOMIAL.match("Bacillus sp. 123")
    assert not BINOMIAL.match("uncultured bacterium")
    assert not BINOMIAL.match("Escherichia coli K-12")


def test_ncbi_kingdom_mapping():
    assert ncbi_kingdom("Bacteria", "", "0") == "Bacteria"
    assert ncbi_kingdom("Archaea", "", "0") == "Archaea"
    assert ncbi_kingdom("", "", "9") == "Viruses"
    assert ncbi_kingdom("Eukaryota", "Fungi", "4") == "Fungi"
    assert ncbi_kingdom("Eukaryota", "Metazoa", "2") == "Animalia"
    assert ncbi_kingdom("Eukaryota", "Viridiplantae", "4") == "Plantae"
    assert ncbi_kingdom("Eukaryota", "", "4") == "Protista"


def test_writer_dedupes_and_patches_common_names(tmp_path):
    writer = Writer(tmp_path, "col")
    writer.taxon("1", "Amanita muscaria", "Fungi", ["Fungi", "Basidiomycota"], "(L.) Lam.", None,
                 {"col_id": "1", "empty": ""})
    writer.taxon("1", "Amanita muscaria", "Fungi", [], None, None, {})
    writer.synonym("2", "1", "Agaricus muscarius")
    rewrite_common_names(writer, {"1": "Fly agaric"})

    taxa = read_rows(tmp_path / "col_taxa.tsv.gz")
    assert len(taxa) == 1
    key, canonical, kingdom, lineage, author, common, metadata = taxa[0]
    assert (key, canonical, kingdom, common) == ("1", "Amanita muscaria", "Fungi", "Fly agaric")
    assert lineage == "Fungi|Basidiomycota"
    assert json.loads(metadata) == {"col_id": "1"}
    assert read_rows(tmp_path / "col_synonyms.tsv.gz") == [["2", "1", "Agaricus muscarius"]]


def test_merge_folds_gbif_undesignated_into_mycobank_fungus():
    gbif = make_row("g", "gbif", "Undesignated", effective="Undesignated", lineage_len=1, day=1)
    myco = make_row("m", "mycobank", "Fungi", rank="sp.", day=2)
    assert plan_group([gbif, myco]) == [("g", "m", "same_name_same_kingdom")]


def test_merge_keeps_fungus_as_survivor_over_protist_and_skips_true_homonyms():
    inat = make_row("i", "inat", "Protista")
    myco = make_row("m", "mycobank", "Fungi", rank="sp.")
    assert plan_group([inat, myco]) == [("i", "m", "same_name_fungi_protista")]
    animal = make_row("a", "inat", "Animalia")
    assert plan_group([animal, myco]) == []


def test_merge_prefers_fungip_row_and_never_merges_two_fungip_rows():
    myco = make_row("m", "mycobank", "Fungi", rank="sp.")
    curated = make_row("c", "inat", "Fungi", fungip=True)
    assert plan_group([myco, curated]) == [("m", "c", "same_name_same_kingdom")]
    assert plan_group([make_row("x", "inat", "Fungi", fungip=True), curated]) == []


def test_effective_kingdom_maps_protozoa_and_chromista():
    assert effective_kingdom("Undesignated", "Chromista") == "Protista"
    assert effective_kingdom("Fungi", "Plantae") == "Fungi"
    assert effective_kingdom("Undesignated", None) == "Undesignated"
