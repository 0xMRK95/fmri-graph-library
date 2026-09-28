"""Recursive citation expansion with 6-category AI classification.

BFS-expands citation networks of confirmed core papers, using the 6-category
taxonomy to classify newly discovered papers. Only core papers (categories 1-3)
get further expansion. All discovered papers are stored with full metadata,
cross-ID deduplication, and auditable classification reasoning.

Citation edges are stored in citations.jsonl for network construction.

Caching layers (no redundant work):
  1. S2 API cache          — built-in, prevents re-fetching same endpoint
  2. expansion_state.jsonl  — prevents re-expanding same paper
  3. classifications_7cat   — 6-category classifications
  4. id_map.jsonl           — prevents classifying same paper under different ID
  5. Legacy files           — haiku_classifications.jsonl, classifications.jsonl
"""

from __future__ import annotations

import json
import logging
from collections import deque
from pathlib import Path
from typing import Any

from ..config import AppConfig
from ..ids import canonical_id, normalize_doi
from ..ingest.semantic_scholar import SemanticScholarClient
from ..models import Category, CORE_CATEGORIES
from ..storage_jsonl import JsonlStore, now_iso, read_jsonl
from .classify_7cat import prepare_expand_batches

logger = logging.getLogger("field-cartography")

# Extended S2 fields — abstract + PDF URL + publication venue
EXTENDED_FIELDS = (
    "title,year,venue,authors,externalIds,corpusId,"
    "citationCount,referenceCount,abstract,openAccessPdf,url"
)


# ---------------------------------------------------------------------------
# Cache loaders (layers 2-5)
# ---------------------------------------------------------------------------

def _load_expansion_state(data_dir: Path) -> set[str]:
    """Layer 2: set of already-expanded paper_ids."""
    return {rec["paper_id"] for rec in read_jsonl(data_dir / "expansion_state.jsonl")}


def _load_evaluated(data_dir: Path) -> set[str]:
    """Layers 3+5+6: union of all classification sources."""
    evaluated: set[str] = set()
    for rec in read_jsonl(data_dir / "classifications.jsonl"):
        evaluated.add(rec["paper_id"])
    for rec in read_jsonl(data_dir / "haiku_classifications.jsonl"):
        evaluated.add(rec["paper_id"])
    for rec in read_jsonl(data_dir / "classifications_7cat.jsonl"):
        evaluated.add(rec["paper_id"])
    return evaluated


def _load_id_map(data_dir: Path) -> dict[str, str]:
    """Layer 4: any_id → canonical_id from persisted id_map.jsonl."""
    id_map: dict[str, str] = {}
    for rec in read_jsonl(data_dir / "id_map.jsonl"):
        canonical = rec["canonical_id"]
        id_map[canonical] = canonical
        for key in ("doi", "s2_id", "arxiv_id", "pubmed_id", "mag_id", "dblp_id"):
            val = rec.get(key)
            if val:
                id_map[_norm_id_key(key, val)] = canonical
    return id_map


def _build_id_map_from_papers(data_dir: Path) -> dict[str, str]:
    """Pre-populate id_map from papers.jsonl externalIds (warm start)."""
    id_map: dict[str, str] = {}
    for rec in read_jsonl(data_dir / "papers.jsonl"):
        pid = rec.get("paper_id")
        if not pid:
            continue
        id_map[pid] = pid
        ext = rec.get("externalIds") or {}
        if ext.get("DOI"):
            id_map[f"doi:{normalize_doi(ext['DOI'])}"] = pid
        corpus = ext.get("CorpusId") or rec.get("corpusId")
        if corpus:
            id_map[f"s2:{corpus}"] = pid
        if ext.get("ArXiv"):
            id_map[f"arxiv:{ext['ArXiv']}"] = pid
        if ext.get("PubMed"):
            id_map[f"pubmed:{ext['PubMed']}"] = pid
    return id_map


def _norm_id_key(key: str, val: str) -> str:
    """Normalize an id_map key for lookup."""
    prefix_map = {
        "doi": "doi",
        "s2_id": "s2",
        "arxiv_id": "arxiv",
        "pubmed_id": "pubmed",
        "mag_id": "mag",
        "dblp_id": "dblp",
    }
    prefix = prefix_map.get(key, key)
    if prefix == "doi":
        return f"doi:{normalize_doi(val)}"
    return f"{prefix}:{val}"


# ---------------------------------------------------------------------------
# Canonical resolution via id_map
# ---------------------------------------------------------------------------

def _resolve_canonical(paper: dict[str, Any], id_map: dict[str, str]) -> str:
    """Resolve an S2 paper dict to its canonical ID, using id_map for dedup."""
    ext = paper.get("externalIds") or {}
    # Check DOI first
    doi = ext.get("DOI")
    if doi:
        doi_key = f"doi:{normalize_doi(doi)}"
        if doi_key in id_map:
            return id_map[doi_key]
    # Check CorpusId
    corpus = ext.get("CorpusId") or paper.get("corpusId")
    if corpus:
        s2_key = f"s2:{corpus}"
        if s2_key in id_map:
            return id_map[s2_key]
    # Check ArXiv
    if ext.get("ArXiv"):
        arxiv_key = f"arxiv:{ext['ArXiv']}"
        if arxiv_key in id_map:
            return id_map[arxiv_key]
    # Fall through to canonical_id()
    return canonical_id(paper)


def _resolve_s2_lookup_id(paper_id: str, paper_index: dict[str, Any]) -> str | None:
    """Convert canonical paper_id to S2-API-compatible lookup string."""
    if paper_id.startswith("doi:"):
        return paper_id[4:]
    if paper_id.startswith("s2:"):
        return f"CorpusId:{paper_id[3:]}"
    rec = paper_index.get(paper_id)
    if rec:
        if rec.get("doi"):
            return rec["doi"]
        if rec.get("corpusId"):
            return f"CorpusId:{rec['corpusId']}"
        ext = rec.get("externalIds") or {}
        if ext.get("DOI"):
            return ext["DOI"]
        if ext.get("CorpusId"):
            return f"CorpusId:{ext['CorpusId']}"
    return None


# ---------------------------------------------------------------------------
# Storage helpers (append-only JSONL)
# ---------------------------------------------------------------------------

def _store_discovered(
    store: JsonlStore,
    paper: dict[str, Any],
    canonical: str,
    depth: int,
    parent: str,
) -> None:
    """Append full S2 metadata → discovered_papers.jsonl."""
    record = {
        "paper_id": canonical,
        "s2_paper_id": paper.get("paperId"),
        "external_ids": paper.get("externalIds") or {},
        "title": paper.get("title"),
        "year": paper.get("year"),
        "venue": paper.get("venue"),
        "authors": [
            {"name": a.get("name", "")} for a in (paper.get("authors") or [])
        ],
        "abstract": paper.get("abstract"),
        "citation_count": paper.get("citationCount"),
        "reference_count": paper.get("referenceCount"),
        "open_access_pdf": paper.get("openAccessPdf"),
        "s2_url": paper.get("url"),
        "provenance": {
            "source": "s2_recursive_expand",
            "fetched_at": now_iso(),
            "depth": depth,
            "parent": parent,
        },
    }
    store.append("discovered_papers.jsonl", record)


def _store_id_mapping(
    store: JsonlStore,
    canonical: str,
    paper: dict[str, Any],
    id_map: dict[str, str],
) -> None:
    """Append cross-ID mapping → id_map.jsonl; update in-memory map."""
    ext = paper.get("externalIds") or {}
    entry = {
        "canonical_id": canonical,
        "doi": ext.get("DOI"),
        "s2_id": str(ext.get("CorpusId")) if ext.get("CorpusId") else None,
        "arxiv_id": ext.get("ArXiv"),
        "pubmed_id": ext.get("PubMed"),
        "mag_id": str(ext.get("MAG")) if ext.get("MAG") else None,
        "dblp_id": ext.get("DBLP"),
    }
    store.append("id_map.jsonl", entry)
    # Update in-memory id_map
    id_map[canonical] = canonical
    if ext.get("DOI"):
        id_map[f"doi:{normalize_doi(ext['DOI'])}"] = canonical
    if ext.get("CorpusId"):
        id_map[f"s2:{ext['CorpusId']}"] = canonical
    if ext.get("ArXiv"):
        id_map[f"arxiv:{ext['ArXiv']}"] = canonical
    if ext.get("PubMed"):
        id_map[f"pubmed:{ext['PubMed']}"] = canonical


def _store_citation_edge(
    store: JsonlStore,
    from_id: str,
    to_id: str,
    relation: str,
    source: str = "s2_recursive_expand",
) -> None:
    """Append a citation edge → citations.jsonl."""
    store.append("citations.jsonl", {
        "from": from_id,
        "to": to_id,
        "relation": relation,
        "source": source,
        "fetched_at": now_iso(),
    })


def _mark_expanded(
    store: JsonlStore,
    paper_id: str,
    depth: int,
    n_refs: int,
    n_cites: int,
    expanded: set[str],
) -> None:
    """Append expansion record → expansion_state.jsonl; update in-memory set."""
    store.append("expansion_state.jsonl", {
        "paper_id": paper_id,
        "expanded_at": now_iso(),
        "n_refs": n_refs,
        "n_cites": n_cites,
        "depth": depth,
    })
    expanded.add(paper_id)


# ---------------------------------------------------------------------------
# Load abstracts for seed papers (from abstracts.jsonl)
# ---------------------------------------------------------------------------

def _load_abstracts(data_dir: Path) -> dict[str, str]:
    """Load paper_id → abstract from abstracts.jsonl."""
    abstracts: dict[str, str] = {}
    for rec in read_jsonl(data_dir / "abstracts.jsonl"):
        pid = rec.get("paper_id")
        abstract = rec.get("abstract")
        if pid and abstract:
            abstracts[pid] = abstract
    return abstracts


# ---------------------------------------------------------------------------
# Main BFS loop
# ---------------------------------------------------------------------------

def recursive_expand(
    cfg: AppConfig,
    max_depth: int = 10,
    batch_size: int = 50,
) -> dict[str, int]:
    """Recursively expand citation network and prepare classification batches.

    Two-phase workflow:
      Phase A (this function): Expand core papers, store edges + discovered papers,
        write classification batch files for unclassified neighbors.
      Phase B (external agents): Classify batches → classifications_7cat.jsonl.
      Re-run this function to pick up newly classified core papers and continue.

    Args:
        cfg:        Application configuration.
        max_depth:  Maximum BFS depth (0=seeds only).
        batch_size: Classification batch size.

    Returns:
        Statistics dict with counts of actions taken.
    """
    data_dir = cfg.storage.data_dir
    store = JsonlStore(data_dir)

    core_cat_values = {c.value for c in CORE_CATEGORIES}

    # Ensure output files exist
    for fname in (
        "discovered_papers.jsonl",
        "id_map.jsonl",
        "haiku_classifications.jsonl",
        "classifications_7cat.jsonl",
        "expansion_state.jsonl",
        "citations.jsonl",
    ):
        (data_dir / fname).touch(exist_ok=True)

    # Load all caches
    logger.info("Loading caches...")
    expanded = _load_expansion_state(data_dir)
    evaluated = _load_evaluated(data_dir)
    id_map = _build_id_map_from_papers(data_dir)
    id_map.update(_load_id_map(data_dir))

    # Load core paper IDs from 6-cat classifications (authoritative source)
    core_ids: set[str] = set()
    for rec in read_jsonl(data_dir / "classifications_7cat.jsonl"):
        if rec.get("category") in core_cat_values:
            core_ids.add(rec["paper_id"])

    # Build paper index for S2 ID resolution (use discovered_papers as cache)
    paper_index: dict[str, dict[str, Any]] = {}
    for rec in read_jsonl(data_dir / "papers.jsonl"):
        pid = rec.get("paper_id")
        if pid:
            paper_index[pid] = rec
    for rec in read_jsonl(data_dir / "discovered_papers.jsonl"):
        pid = rec.get("paper_id")
        if pid and pid not in paper_index:
            paper_index[pid] = rec

    # Initialize BFS queue with unexpanded core papers
    queue: deque[tuple[str, int]] = deque()
    for pid in sorted(core_ids):
        if pid not in expanded:
            queue.append((pid, 0))

    logger.info(
        "Starting recursive expansion: %d core seeds, %d already expanded, "
        "%d already evaluated, %d in id_map",
        len(core_ids), len(expanded), len(evaluated), len(id_map),
    )
    logger.info("Queue size: %d papers to expand", len(queue))

    # Initialize S2 client with extended fields
    s2 = SemanticScholarClient(
        cfg.semantic_scholar.base_url,
        cfg.semantic_scholar.rate_limit_per_min,
        cfg.storage.cache_dir,
    )

    stats = {
        "seeds": len(core_ids),
        "expanded": 0,
        "discovered": 0,
        "edges_stored": 0,
        "pending_classification": 0,
        "skipped_expanded": 0,
        "skipped_evaluated": 0,
        "s2_failures": 0,
    }

    # Collect ALL unclassified papers across the full expansion
    all_pending: list[tuple[str, str, str | None]] = []

    while queue:
        paper_id, depth = queue.popleft()

        if paper_id in expanded:
            stats["skipped_expanded"] += 1
            continue
        if depth > max_depth:
            continue

        # Resolve S2 lookup ID
        s2_id = _resolve_s2_lookup_id(paper_id, paper_index)
        if not s2_id:
            logger.warning("No S2 lookup ID for %s — skipping", paper_id)
            _mark_expanded(store, paper_id, depth, 0, 0, expanded)
            stats["s2_failures"] += 1
            continue

        # Fetch references + citations with extended fields
        refs = s2.get_references(s2_id, limit=500, fields=EXTENDED_FIELDS)
        cites = s2.get_citations(s2_id, limit=500, fields=EXTENDED_FIELDS)

        if not refs and not cites:
            logger.info("No refs/cites for %s (S2 ID: %s)", paper_id, s2_id)
            _mark_expanded(store, paper_id, depth, 0, 0, expanded)
            stats["expanded"] += 1
            continue

        # Store citation edges for ALL refs/cites
        for ref in refs:
            if not ref or not ref.get("title"):
                continue
            ref_canonical = _resolve_canonical(ref, id_map)
            _store_citation_edge(store, paper_id, ref_canonical, "cites")
            stats["edges_stored"] += 1

        for cite in cites:
            if not cite or not cite.get("title"):
                continue
            cite_canonical = _resolve_canonical(cite, id_map)
            _store_citation_edge(store, cite_canonical, paper_id, "cites")
            stats["edges_stored"] += 1

        # Discover + collect unclassified neighbors
        for neighbor in refs + cites:
            if not neighbor or not neighbor.get("title"):
                continue

            canonical = _resolve_canonical(neighbor, id_map)

            if canonical in evaluated:
                stats["skipped_evaluated"] += 1
                continue

            # Store full metadata
            if canonical not in paper_index:
                _store_discovered(store, neighbor, canonical, depth + 1, paper_id)
                _store_id_mapping(store, canonical, neighbor, id_map)
                paper_index[canonical] = neighbor
                stats["discovered"] += 1

            # Collect for classification (will be written as batch files)
            all_pending.append((
                canonical,
                neighbor.get("title", ""),
                neighbor.get("abstract"),
            ))
            evaluated.add(canonical)

        # Mark expanded
        _mark_expanded(store, paper_id, depth, len(refs), len(cites), expanded)
        stats["expanded"] += 1

        logger.info(
            "[depth=%d] Expanded %s: %d refs, %d cites, %d edges | queue=%d",
            depth, paper_id, len(refs), len(cites),
            stats["edges_stored"], len(queue),
        )

    # Write classification batch files for agents
    if all_pending:
        batch_paths = prepare_expand_batches(data_dir, all_pending, batch_size)
        stats["pending_classification"] = len(all_pending)
        stats["classification_batches"] = len(batch_paths)
        logger.info(
            "Wrote %d classification batches (%d papers) to data/expand_classify_batches/",
            len(batch_paths), len(all_pending),
        )
    else:
        stats["classification_batches"] = 0

    # Log run
    store.log_run("recursive_expand", {**stats, "max_depth": max_depth})

    logger.info("Recursive expansion complete: %s", json.dumps(stats, indent=2))
    return stats
