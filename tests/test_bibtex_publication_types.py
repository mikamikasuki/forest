import json

import httpx
import pytest
from pybtex.database import parse_string

from research.literature import sources


def entry(text):
    return next(iter(parse_string(text, "bibtex").entries.values()))


def crossref_book(publication_type="edited-book"):
    return {
        "type": publication_type,
        "title": ["A Research Handbook"],
        "editor": [{"given": "Casey", "family": "Editor"}],
        "publisher": "Example Press",
        "published": {"date-parts": [[2024]]},
        "DOI": "10.1234/handbook",
        "URL": "https://doi.org/10.1234/handbook",
    }


def crossref_proceedings():
    return {
        "type": "proceedings-article",
        "title": ["A Conference Paper"],
        "author": [{"given": "Alex", "family": "Researcher"}],
        "container-title": ["Proceedings of the Example Conference"],
        "published": {"date-parts": [[2024]]},
        "DOI": "10.1234/conference-paper",
        "URL": "https://doi.org/10.1234/conference-paper",
        "volume": "7",
        "issue": "2",
        "page": "10-18",
    }


@pytest.mark.parametrize("publication_type", [
    "book", "edited-book", "monograph", "reference-book",
])
def test_crossref_books_keep_their_publication_type_and_editors(publication_type):
    record = sources._crossref(crossref_book(publication_type))

    assert record["type"] == publication_type
    assert record["editors"] == ["Casey Editor"]
    exported = entry(record["bibtex"])
    assert exported.type == "book"
    assert exported.fields["publisher"] == "Example Press"
    assert exported.fields["doi"] == "10.1234/handbook"
    assert exported.persons["editor"][0].last_names == ["Editor"]
    assert "author" not in exported.persons
    assert "journal" not in exported.fields


def test_authored_book_keeps_its_author_and_publisher():
    exported = entry(sources.bibtex([{
        "type": "book", "title": "An Authored Book", "authors": ["Alex Author"],
        "year": 2024, "publisher": "Example Press",
    }]))

    assert exported.type == "book"
    assert exported.persons["author"][0].last_names == ["Author"]
    assert exported.fields["publisher"] == "Example Press"


def test_crossref_proceedings_articles_export_as_inproceedings_with_booktitle():
    record = sources._crossref(crossref_proceedings())

    assert record["type"] == "proceedings-article"
    assert record["journal"] == "Proceedings of the Example Conference"
    exported = entry(record["bibtex"])

    assert exported.type == "inproceedings"
    assert exported.fields["booktitle"] == "Proceedings of the Example Conference"
    assert "journal" not in exported.fields
    assert exported.persons["author"][0].last_names == ["Researcher"]
    assert exported.fields["year"] == "2024"
    assert exported.fields["doi"] == "10.1234/conference-paper"
    assert exported.fields["url"] == "https://doi.org/10.1234/conference-paper"
    assert exported.fields["volume"] == "7"
    assert exported.fields["number"] == "2"
    assert exported.fields["pages"] == "10-18"


@pytest.mark.parametrize("publication_type", [None, "journal-article", "unrecognized"])
def test_articles_and_legacy_records_keep_existing_export_fields(publication_type):
    record = {
        "title": "An Article", "authors": ["Alex Author"], "year": 2024,
        "journal": "Example Journal", "volume": "12", "number": "3", "pages": "4-9",
    }
    if publication_type is not None:
        record["type"] = publication_type
    exported = entry(sources.bibtex([record]))

    assert exported.type == "article"
    assert exported.fields["journal"] == "Example Journal"
    assert exported.fields["volume"] == "12"
    assert exported.fields["number"] == "3"
    assert exported.fields["pages"] == "4-9"


def test_bibtex_export_keeps_the_braces_of_an_escaped_backslash():
    exported = sources.bibtex([{
        "title": r"Stable laws: the \alpha-stable case",
        "authors": [r"Ann \Bee"],
        "year": 2024,
    }])

    assert r"title = {Stable laws: the \textbackslash{}alpha-stable case}" in exported
    assert r"author = {Ann \textbackslash{}Bee}" in exported


def test_bibtex_export_still_drops_braces_and_escapes_special_characters():
    exported = sources.bibtex([{
        "title": "{Braced} a_b & 50% #1", "authors": ["Alex Author"], "year": 2024,
    }])

    assert r"title = {Braced a\_b \& 50\% \#1}" in exported
    assert "textbackslash" not in exported


@pytest.mark.parametrize(("text", "format"), [
    (
        "@book{handbook, title={A Handbook}, editor={Editor, Casey}, "
        "publisher={Example Press}, year={2024}}",
        "bibtex",
    ),
    (
        "TY  - BOOK\nTI  - A Handbook\nED  - Editor, Casey\n"
        "PB  - Example Press\nPY  - 2024\nER  -",
        "ris",
    ),
    (
        "TY  - EBOOK\nTI  - A Handbook\nED  - Editor, Casey\n"
        "PB  - Example Press\nPY  - 2024\nER  -",
        "ris",
    ),
    (
        "TY  - EDBOOK\nTI  - A Handbook\nED  - Editor, Casey\n"
        "PB  - Example Press\nPY  - 2024\nER  -",
        "ris",
    ),
])
def test_imported_book_bibliographies_keep_their_type(text, format):
    records = sources.import_bibliography(text, format)
    exported = entry(sources.bibtex(records))

    assert exported.type == "book"
    assert exported.fields["publisher"] == "Example Press"
    assert exported.persons["editor"][0].last_names == ["Editor"]
    assert records[0]["verification"] == "unverified"
    if format == "bibtex":
        assert records[0]["citation_key"] == "handbook"


def test_doi_import_writes_book_metadata_and_bibtex(tmp_path, monkeypatch):
    requested = []

    def get(url, *args, **kwargs):
        requested.append(url)
        return httpx.Response(200, json={"message": crossref_book()})

    monkeypatch.setattr(sources, "_get", get)
    record = sources.import_identifier("10.1234/handbook", tmp_path)

    assert requested == ["https://api.crossref.org/works/10.1234%2Fhandbook"]
    assert record["type"] == "edited-book"
    assert json.loads((tmp_path / "metadata.json").read_text())["type"] == "edited-book"
    assert entry((tmp_path / "references.bib").read_text()).type == "book"


def test_doi_import_writes_proceedings_metadata_and_bibtex(tmp_path, monkeypatch):
    requested = []

    def get(url, *args, **kwargs):
        requested.append(url)
        return httpx.Response(200, json={"message": crossref_proceedings()})

    monkeypatch.setattr(sources, "_get", get)
    record = sources.import_identifier("10.1234/conference-paper", tmp_path)

    assert requested == ["https://api.crossref.org/works/10.1234%2Fconference-paper"]
    metadata = json.loads((tmp_path / "metadata.json").read_text())
    assert metadata["type"] == "proceedings-article"
    assert metadata["journal"] == "Proceedings of the Example Conference"
    exported = entry((tmp_path / "references.bib").read_text())
    assert exported.type == "inproceedings"
    assert exported.fields["booktitle"] == "Proceedings of the Example Conference"
    assert "journal" not in exported.fields
