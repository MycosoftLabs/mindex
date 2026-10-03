import csv
import gzip
import json

from mindex_etl.jobs.taxonomy_sources import BINOMIAL, Writer, ncbi_kingdom, rewrite_common_names


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
