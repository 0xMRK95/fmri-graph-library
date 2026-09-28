from __future__ import annotations

from typing import Any

from ..ids import canonical_id, normalize_doi


def _authors_list(authors: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    if not authors:
        return []
    out = []
    for a in authors:
        name = a.get("name") or a.get("display_name") or a.get("author") or ""
        out.append({"name": name})
    return out


def standardize_s2(paper: dict[str, Any]) -> dict[str, Any]:
    doi = None
    ext = paper.get("externalIds") or {}
    if ext.get("DOI"):
        doi = normalize_doi(ext.get("DOI"))
    out = {
        "title": paper.get("title"),
        "year": paper.get("year"),
        "venue": paper.get("venue"),
        "authors": _authors_list(paper.get("authors")),
        "doi": doi,
        "corpusId": paper.get("corpusId"),
        "externalIds": ext,
        "source": "semantic_scholar",
    }
    out["paper_id"] = canonical_id(out)
    return out


def standardize_openalex(work: dict[str, Any]) -> dict[str, Any]:
    doi = None
    if work.get("doi"):
        doi = normalize_doi(work.get("doi"))
    authors = []
    for a in work.get("authorships", []):
        name = a.get("author", {}).get("display_name", "")
        authors.append({"name": name})
    out = {
        "title": work.get("title"),
        "year": work.get("publication_year"),
        "venue": (work.get("host_venue", {}) or {}).get("display_name")
        or ((work.get("primary_location") or {}).get("source") or {}).get(
            "display_name"
        ),
        "authors": authors,
        "doi": doi,
        "openalex_id": work.get("id"),
        "source": "openalex",
    }
    out["paper_id"] = canonical_id(out)
    return out
