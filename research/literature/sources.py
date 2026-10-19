"""Actual Crossref/arXiv retrieval, PDF passages, and bibliography import/export."""
from __future__ import annotations

import html
import ipaddress
import json
import os
from pathlib import Path
import re
import socket
import time
from urllib.parse import quote, urlencode, urlparse, urlsplit
from uuid import uuid4
import xml.etree.ElementTree as ET

import httpx

_ARXIV_LAST = 0.0
MAX_DOWNLOAD = 35 * 1024 * 1024


def _get(url: str, params=None, accept=None):
    headers = {"User-Agent": "FOREST-research/1.0"}
    if os.getenv("FOREST_CONTACT_EMAIL"):
        headers["User-Agent"] += f" (mailto:{os.environ['FOREST_CONTACT_EMAIL']})"
    if accept:
        headers["Accept"] = accept
    response = httpx.get(url, params=params, headers=headers, timeout=40, follow_redirects=True)
    response.raise_for_status()
    return response


def _clean(text):
    return html.unescape(re.sub(r"<[^>]+>", "", text or "")).strip()


def _bib_escape(value):
    return str(value or "").replace("\\", "\\textbackslash{}").replace("&", r"\&").replace("%", r"\%").replace("_", r"\_").replace("#", r"\#").replace("{", "").replace("}", "")


def _author_names(value):
    if isinstance(value, (str, dict)):
        value = [value]
    elif not isinstance(value, (list, tuple)):
        value = []
    names = []
    for author in value or []:
        if isinstance(author, str):
            name = author.strip()
        elif isinstance(author, dict):
            name = author.get("name") or " ".join(
                str(part).strip()
                for part in (author.get("given"), author.get("family"))
                if part
            )
        else:
            name = ""
        if name:
            names.append(name)
    return names


def bibtex(records: list[dict]) -> str:
    entries = []
    for i, record in enumerate(records):
        authors = _author_names(record.get("authors"))
        entry_type = "book" if str(record.get("type") or "").lower() in (
            "book", "edited-book", "monograph", "reference-book"
        ) else "article"
        first_author = authors[0] if authors else "source"
        surname = first_author.split(",", 1)[0].split()[-1]
        key = record.get("citation_key") or re.sub(r"[^a-zA-Z0-9]", "", surname) + str(record.get("year") or "nd") + str(i)
        fields = {"title": record.get("title"), "author": " and ".join(authors), "year": record.get("year"), "doi": record.get("doi"), "url": record.get("url"), "journal": record.get("journal"), "volume": record.get("volume"), "number": record.get("number"), "pages": record.get("pages")}
        if entry_type == "book":
            fields.pop("journal", None)
            fields["publisher"] = record.get("publisher")
            if not authors:
                fields["editor"] = " and ".join(_author_names(record.get("editors")))
        entries.append("@" + entry_type + "{" + key + ",\n" + ",\n".join(f"  {k} = {{{_bib_escape(v)}}}" for k, v in fields.items() if v) + "\n}")
    return "\n\n".join(entries)


def _crossref(item):
    dates = item.get("published", item.get("issued", {})).get("date-parts", [[]])[0]
    record = {"title": _clean((item.get("title") or ["Untitled"])[0]), "authors": [" ".join(filter(None, [a.get("given"), a.get("family")])) or a.get("name", "") for a in item.get("author", [])], "year": dates[0] if dates else None, "doi": item.get("DOI"), "arxiv_id": None, "url": item.get("URL"), "abstract": _clean(item.get("abstract")), "journal": (item.get("container-title") or [""])[0], "volume": item.get("volume"), "number": item.get("issue"), "pages": item.get("page"), "publisher": item.get("publisher"), "source": "crossref", "read_scope": "metadata", "links": item.get("link", []), "passages": [], "trusted_instructions": False}
    record["type"] = item.get("type")
    record["editors"] = _author_names(item.get("editor"))
    if record["abstract"]:
        record["read_scope"] = "abstract"
        record["passages"] = [{"id": "abstract", "section": "Abstract", "page": None, "text": record["abstract"], "source_url": record["url"]}]
    record["bibtex"] = bibtex([record])
    return record


def _arxiv(params):
    global _ARXIV_LAST
    time.sleep(max(0, 3.05 - (time.monotonic() - _ARXIV_LAST)))
    # Use RFC3986 spaces rather than form '+' encoding for Atom query syntax.
    response = _get("https://export.arxiv.org/api/query?" + urlencode(params, quote_via=quote, safe=":"), accept="application/atom+xml")
    _ARXIV_LAST = time.monotonic()
    root = ET.fromstring(response.text)
    ns = {"a": "http://www.w3.org/2005/Atom", "x": "http://arxiv.org/schemas/atom"}
    records = []
    for entry in root.findall("a:entry", ns):
        title = " ".join(entry.findtext("a:title", "", ns).split())
        if title.lower() == "error":
            raise ValueError(entry.findtext("a:summary", "arXiv query failed", ns))
        url = entry.findtext("a:id", "", ns)
        abstract = " ".join(entry.findtext("a:summary", "", ns).split())
        record = {"title": title, "authors": [a.findtext("a:name", "", ns) for a in entry.findall("a:author", ns)], "year": int(entry.findtext("a:published", "0000", ns)[:4]), "doi": entry.findtext("x:doi", None, ns), "arxiv_id": url.rsplit("/abs/", 1)[-1], "url": url, "abstract": abstract, "source": "arxiv", "read_scope": "abstract", "passages": [{"id": "abstract", "section": "Abstract", "page": None, "text": abstract, "source_url": url}], "trusted_instructions": False}
        record["pdf_url"] = next((e.attrib["href"].replace("http:", "https:") for e in entry.findall("a:link", ns) if e.attrib.get("title") == "pdf"), None)
        record["bibtex"] = bibtex([record])
        records.append(record)
    return records


def search(query: str, source="crossref", limit=8) -> list[dict]:
    if not query.strip():
        raise ValueError("A nonempty literature query is required")
    limit = max(1, min(int(limit), 30))
    if source == "crossref":
        result = _get("https://api.crossref.org/works", {"query.bibliographic": query, "rows": limit}).json()
        return [_crossref(item) for item in result["message"]["items"]]
    if source == "arxiv":
        words = " ".join(query.replace('"', " ").split()).split()
        if not words:
            raise ValueError("A nonempty literature query is required")
        keyword_expression = " AND ".join("all:" + word for word in words)
        params = {"start": 0, "max_results": limit, "sortBy": "relevance"}
        if len(words) == 1:
            return _arxiv({**params, "search_query": keyword_expression})

        # arXiv supports quoted phrases in field queries. Search titles for the
        # complete phrase first so common words are not independently dropped
        # or over-constrained, then retain the existing all-token search as a
        # broad fallback. Merge by arXiv ID because the two result sets overlap.
        phrase = " ".join(words)
        phrase_expression = f'ti:"{phrase}"'
        phrase_results = _arxiv({**params, "search_query": phrase_expression})
        if len(phrase_results) >= limit:
            return phrase_results[:limit]
        keyword_results = _arxiv({**params, "search_query": keyword_expression})
        results = []
        seen = set()
        for record in [*phrase_results, *keyword_results]:
            identifier = record.get("arxiv_id") or record.get("url")
            if identifier:
                identifier = re.sub(r"v\d+$", "", identifier)
            if identifier and identifier in seen:
                continue
            if identifier:
                seen.add(identifier)
            results.append(record)
            if len(results) == limit:
                break
        return results
    raise ValueError("Supported sources: crossref, arxiv")


def extract_pdf(path: str | Path) -> list[dict]:
    from pypdf import PdfReader
    reader = PdfReader(str(path))
    if reader.is_encrypted:
        raise ValueError("The PDF is encrypted; supply an unlocked copy")
    passages = []
    for page_number, page in enumerate(reader.pages, 1):
        text = (page.extract_text() or "").strip()
        for paragraph_number, paragraph in enumerate(re.split(r"\n\s*\n", text)):
            if paragraph.strip():
                passages.append({"id": f"p{page_number}-{paragraph_number}", "page": page_number, "section": None, "text": paragraph.strip(), "source_file": Path(path).name})
    return passages


def _public_url(url):
    parsed = urlparse(url)
    if parsed.scheme not in {"https", "http"} or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Only public HTTP(S) URLs are supported")
    for _, _, _, _, address in socket.getaddrinfo(parsed.hostname, parsed.port or 443):
        if not ipaddress.ip_address(address[0]).is_global:
            raise ValueError("Private/network-local literature URLs are not allowed")


def download_pdf(url, target):
    """Size-limited public retrieval with redirect targets validated individually."""
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    for _ in range(6):
        _public_url(url)
        with httpx.stream("GET", url, timeout=45, follow_redirects=False, headers={"User-Agent": "FOREST-research/1.0"}) as response:
            if response.is_redirect:
                from urllib.parse import urljoin
                url = urljoin(url, response.headers["location"])
                continue
            response.raise_for_status()
            content = bytearray()
            for chunk in response.iter_bytes():
                content.extend(chunk)
                if len(content) > MAX_DOWNLOAD:
                    raise ValueError("PDF exceeds the 35 MB import limit")
            if not bytes(content).lstrip().startswith(b"%PDF"):
                raise ValueError("The URL did not return a PDF")
            target.write_bytes(content)
            return str(target)
    raise ValueError("Too many redirects")


def import_identifier(identifier: str, output_dir: str | Path | None = None) -> dict:
    value = identifier.strip()
    doi_input = urlsplit(value).path if value.startswith(("https://", "http://")) else value
    local = Path(value)
    if local.is_file() and local.suffix.lower() == ".pdf":
        record = {"title": local.stem, "authors": [], "year": None, "source": "local_pdf", "url": None, "pdf_path": str(local), "passages": extract_pdf(local), "read_scope": "full_text", "trusted_instructions": False}
    elif re.search(r"(?:arxiv\.org/(?:abs|pdf)/|^)(\d{4}\.\d{4,5}(?:v\d+)?)", value):
        arxiv_id = re.search(r"(\d{4}\.\d{4,5}(?:v\d+)?)", value).group(1)
        records = _arxiv({"id_list": arxiv_id, "max_results": 1})
        if not records:
            raise ValueError("arXiv identifier was not found")
        record = records[0]
    elif re.search(r"10\.\d{4,9}/\S+", doi_input):
        doi = re.search(r"10\.\d{4,9}/\S+", doi_input).group(0)
        record = _crossref(_get("https://api.crossref.org/works/" + quote(doi, safe="")).json()["message"])
    elif value.startswith(("https://", "http://")) and urlparse(value).path.lower().endswith(".pdf"):
        if not output_dir:
            raise ValueError("An output directory is needed for PDF import")
        # Allocate per import, not per URL/basename: even a refreshed URL must
        # never replace the bytes referenced by an existing Library record.
        path = download_pdf(value, Path(output_dir) / f"source-{uuid4().hex}.pdf")
        record = {"title": Path(urlparse(value).path).stem, "authors": [], "year": None, "source": "public_pdf", "url": value, "pdf_path": path, "passages": extract_pdf(path), "read_scope": "full_text", "trusted_instructions": False}
    else:
        raise ValueError("Use a DOI, arXiv identifier/URL, public PDF URL, or local PDF path")
    record["bibtex"] = bibtex([record])
    if output_dir:
        output = Path(output_dir)
        output.mkdir(parents=True, exist_ok=True)
        (output / "metadata.json").write_text(json.dumps(record, indent=2, ensure_ascii=False))
        (output / "references.bib").write_text(record["bibtex"])
    return record


def import_bibliography(text: str, format="bibtex") -> list[dict]:
    """Parse user bibliography; local metadata is explicitly unverified."""
    records = []
    if format.lower() == "ris":
        for block in text.split("ER  -"):
            tags = {}
            for line in block.splitlines():
                match = re.match(r"([A-Z0-9]{2})  - (.*)", line)
                if match:
                    tags.setdefault(match.group(1), []).append(match.group(2))
            if tags:
                records.append({"title": (tags.get("TI") or tags.get("T1") or ["Untitled"])[0], "authors": tags.get("AU", []), "doi": (tags.get("DO") or [None])[0], "year": (tags.get("PY") or [None])[0], "url": (tags.get("UR") or [None])[0], "source": "imported_ris", "read_scope": "metadata", "verification": "unverified", "passages": [],
                                "type": (tags.get("TY") or [None])[0],
                                "editors": tags.get("ED", []),
                                "publisher": (tags.get("PB") or [None])[0]})
    elif format.lower() == "bibtex":
        # pybtex handles nested braces, quoted values, and multiline fields.
        from pybtex.database import parse_string
        database = parse_string(text, "bibtex")
        for key, entry in database.entries.items():
            records.append({"citation_key": key, "title": entry.fields.get("title", "Untitled"), "authors": [str(p) for p in entry.persons.get("author", [])], "year": entry.fields.get("year"), "doi": entry.fields.get("doi"), "url": entry.fields.get("url"), "source": "imported_bibtex", "read_scope": "metadata", "verification": "unverified", "passages": [],
                            "type": entry.type,
                            "editors": [str(p) for p in entry.persons.get("editor", [])],
                            "publisher": entry.fields.get("publisher")})
    else:
        raise ValueError("Bibliography format must be bibtex or ris")
    for record in records:
        record["bibtex"] = bibtex([record])
    return records
