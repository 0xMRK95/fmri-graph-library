"""
Pydantic validation models for field-cartography.

Defines core data models for papers, citations, authors, venues, and provenance.
"""

from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field, field_validator, ConfigDict


class Category(str, Enum):
    """6-category classification taxonomy for the survey.

    Core (expand citations):
      1. fmri_gnn — GNN / graph transformer / geometric DL on fMRI
      2. fmri_graph_classical — Classical graph theory on fMRI
      3. fmri_geometric_manifold — Geometric / manifold / TDA on fMRI

    Secondary (keep as leaf nodes, no expansion):
      4. fmri_no_graph — fMRI without graph methods
      5. graph_methods_no_fmri — Graph methods on non-fMRI data

    Excluded:
      6. out_of_scope — Unrelated, EEG/MEG-only, etc.
    """

    FMRI_GNN = "fmri_gnn"
    FMRI_GRAPH_CLASSICAL = "fmri_graph_classical"
    FMRI_GEOMETRIC_MANIFOLD = "fmri_geometric_manifold"
    GRAPH_METHODS_NO_FMRI = "graph_methods_no_fmri"
    FMRI_NO_GRAPH = "fmri_no_graph"
    OUT_OF_SCOPE = "out_of_scope"


CORE_CATEGORIES = frozenset({
    Category.FMRI_GNN,
    Category.FMRI_GRAPH_CLASSICAL,
    Category.FMRI_GEOMETRIC_MANIFOLD,
})

SECONDARY_CATEGORIES = frozenset({
    Category.GRAPH_METHODS_NO_FMRI,
    Category.FMRI_NO_GRAPH,
})


class Provenance(BaseModel):
    """Data source and confidence metadata."""

    source: str
    fetched_at: str
    confidence: float = Field(ge=0.0, le=1.0)

    model_config = ConfigDict(frozen=False)


class Author(BaseModel):
    """Author metadata."""

    author_id: Optional[str] = None
    name: str
    affiliations: list[str] = Field(default_factory=list)
    provenance: Optional[Provenance] = None

    model_config = ConfigDict(frozen=False)


class Venue(BaseModel):
    """Publication venue (journal, conference, etc.)."""

    venue_id: Optional[str] = None
    name: str
    provenance: Optional[Provenance] = None

    model_config = ConfigDict(frozen=False)


class Paper(BaseModel):
    """Scholarly paper with metadata from multiple sources."""

    paper_id: str
    title: Optional[str] = None
    year: Optional[int] = None
    venue: Optional[str] = None
    authors: list[dict] = Field(default_factory=list)
    doi: Optional[str] = None
    corpusId: Optional[str] = None
    openalex_id: Optional[str] = None
    provenance: Optional[Provenance] = None
    seed_tag: Optional[str] = None
    abstract: Optional[str] = None

    model_config = ConfigDict(frozen=False)

    @field_validator("paper_id")
    @classmethod
    def validate_paper_id(cls, v: str) -> str:
        """Ensure paper_id starts with one of: doi:, s2:, oa:, hash:"""
        valid_prefixes = ("doi:", "s2:", "oa:", "hash:")
        if not any(v.startswith(prefix) for prefix in valid_prefixes):
            raise ValueError(
                f"paper_id must start with one of {valid_prefixes}, got '{v}'"
            )
        return v


class Citation(BaseModel):
    """Citation edge from one paper to another."""

    from_id: str = Field(alias="from")
    to_id: str = Field(alias="to")
    relation: Optional[str] = None
    source: Optional[str] = None
    fetched_at: Optional[str] = None

    model_config = ConfigDict(populate_by_name=True, frozen=False)
