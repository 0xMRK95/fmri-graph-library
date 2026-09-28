"""Pydantic models for the read-only query API.

These are the *contract* types returned by `field_cartography.api.Client`.
They are deliberately decoupled from the build-pipeline models in
`field_cartography.models` so the query contract can evolve independently.
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class ExternalIds(BaseModel):
    """External identifiers for a paper.

    `openalex` is ALWAYS None: OpenAlex IDs are not present anywhere in the
    cartography corpus (0 records). Kept in the schema for forward-compat.
    """

    doi: Optional[str] = None
    arxiv: Optional[str] = None
    s2: Optional[str] = None          # Semantic Scholar CorpusId (numeric, as str)
    openalex: Optional[str] = None    # never populated — see note above
    pmc: Optional[str] = None         # PubMedCentral id
    pubmed: Optional[str] = None


class PaperRecord(BaseModel):
    """Canonical metadata record for one paper.

    `node_type` is only populated when the 'network' index is loaded
    (warm('network') or any node_stats() call). Otherwise it is None even
    when known — see README "Deviations".
    """

    canonical_id: str
    title: Optional[str] = None
    authors: list[dict] = Field(default_factory=list)  # [{name, authorId?}]
    year: Optional[int] = None
    venue: Optional[str] = None
    abstract: Optional[str] = None
    external_ids: ExternalIds = Field(default_factory=ExternalIds)
    category: Optional[str] = None
    category_tier: Optional[int] = None
    category_confidence: Optional[str] = None   # 'high' | 'medium' | 'low'
    node_type: Optional[str] = None             # 'core' | 'secondary' | 'boundary'
    citation_count: Optional[int] = None        # S2 global count (not in-corpus degree)
    open_access_pdf_url: Optional[str] = None
    has_fulltext: bool = False
    fulltext_path: Optional[str] = None
