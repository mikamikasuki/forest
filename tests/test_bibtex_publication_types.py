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
