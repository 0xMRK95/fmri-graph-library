from __future__ import annotations

import hashlib
import re
from typing import Any


def normalize_doi(doi: str) -> str:
    doi = doi.strip().lower()
    doi = re.sub(r"^https?://(dx\.)?doi\.org/", "", doi)
    doi = doi.replace("doi:", "").strip()
    return doi


def normalize_title(title: str) -> str:
    title = title.lower().strip()
    title = re.sub(r"[^a-z0-9\s]", "", title)
    title = re.sub(r"\s+", " ", title)
    return title


def canonical_id(record: dict[str, Any]) -> str:
    doi = record.get("doi") or (record.get("externalIds") or {}).get("DOI")
    if doi:
        return f"doi:{normalize_doi(doi)}"
    if record.get("corpusId"):
        return f"s2:{record['corpusId']}"
    if record.get("openalex_id") or record.get("openalexId"):
        return f"oa:{record.get('openalex_id') or record.get('openalexId')}"

    title = normalize_title(record.get("title", ""))
    year = str(record.get("year", ""))
    authors = record.get("authors") or []
    first = ""
    if authors:
        first = (authors[0].get("name") or authors[0].get("family") or "").lower()
        first = re.sub(r"[^a-z]", "", first)
    raw = f"{title}|{year}|{first}"
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]
    return f"hash:{digest}"
