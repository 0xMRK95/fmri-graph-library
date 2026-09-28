"""Phase 4: Build the citation network from classified papers and edges.

Produces:
  - data/network/nodes.jsonl  — all nodes with metadata + category + node_type
  - data/network/edges.jsonl  — deduplicated directed citation edges
  - data/network/network_stats.json — summary statistics
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from ..config import AppConfig
from ..models import Category, CORE_CATEGORIES, SECONDARY_CATEGORIES
from ..storage_jsonl import now_iso, read_jsonl

logger = logging.getLogger("field-cartography")


def build_network(cfg: AppConfig) -> dict[str, Any]:
    """Build the citation network from classified papers and citation edges.

    Returns:
        Statistics dict with node/edge counts and network metrics.
    """
    data_dir = cfg.storage.data_dir
    network_dir = data_dir / "network"
    network_dir.mkdir(parents=True, exist_ok=True)

    # Step 1: Load all classifications (new 6-cat + legacy)
    # Priority: new_classifications > haiku_classifications > classifications
    paper_categories: dict[str, str] = {}

    # Legacy classifications.jsonl (oldest, lowest priority)
    for rec in read_jsonl(data_dir / "classifications.jsonl"):
        pid = rec.get("paper_id")
        cat = rec.get("category")
        if pid and cat:
            # Map legacy categories to 6-cat where possible
            paper_categories[pid] = _map_legacy_category(cat, rec.get("scope"))

    # haiku_classifications.jsonl (binary, medium priority)
    for rec in read_jsonl(data_dir / "haiku_classifications.jsonl"):
        pid = rec.get("paper_id")
        if not pid:
            continue
        if rec.get("is_fmri_gnn") is True:
            paper_categories[pid] = Category.FMRI_GNN.value
        elif rec.get("is_fmri_gnn") is False:
            # Binary "no" — keep as out_of_scope unless already better classified
            if pid not in paper_categories:
                paper_categories[pid] = Category.OUT_OF_SCOPE.value

    # classifications_7cat.jsonl (6-cat, highest priority)
    for rec in read_jsonl(data_dir / "classifications_7cat.jsonl"):
        pid = rec.get("paper_id")
        cat = rec.get("category")
        if pid and cat:
            paper_categories[pid] = cat

    core_cats = {c.value for c in CORE_CATEGORIES}
    secondary_cats = {c.value for c in SECONDARY_CATEGORIES}

    # Step 2: Load metadata for all papers
    meta_index: dict[str, dict[str, Any]] = {}
    for rec in read_jsonl(data_dir / "papers.jsonl"):
        pid = rec.get("paper_id")
        if pid:
            meta_index[pid] = rec
    for rec in read_jsonl(data_dir / "discovered_papers.jsonl"):
        pid = rec.get("paper_id")
        if pid:
            # Merge — discovered_papers has richer metadata
            existing = meta_index.get(pid, {})
            meta_index[pid] = {**existing, **{k: v for k, v in rec.items() if v is not None}}

    # Step 3: Identify core + secondary paper IDs
    core_ids: set[str] = set()
    secondary_ids: set[str] = set()
    for pid, cat in paper_categories.items():
        if cat in core_cats:
            core_ids.add(pid)
        elif cat in secondary_cats:
            secondary_ids.add(pid)

    in_network_ids = core_ids | secondary_ids

    # Step 4: Load citation edges and identify boundary nodes
    edges: list[dict[str, Any]] = []
    seen_edges: set[tuple[str, str]] = set()
    boundary_ids: set[str] = set()

    for rec in read_jsonl(data_dir / "citations.jsonl"):
        src = rec.get("from")
        tgt = rec.get("to")
        if not src or not tgt:
            continue

        # Include edge if at least one endpoint is core/secondary
        src_in = src in in_network_ids
        tgt_in = tgt in in_network_ids

        if not src_in and not tgt_in:
            continue

        edge_key = (src, tgt)
        if edge_key in seen_edges:
            continue
        seen_edges.add(edge_key)

        edges.append({
            "from": src,
            "to": tgt,
            "relation": rec.get("relation", "cites"),
            "source": rec.get("source"),
        })

        # Track boundary nodes (1-hop out-of-scope neighbors)
        if not src_in:
            boundary_ids.add(src)
        if not tgt_in:
            boundary_ids.add(tgt)

    # Step 5: Build node list
    all_node_ids = core_ids | secondary_ids | boundary_ids
    nodes: list[dict[str, Any]] = []

    for pid in sorted(all_node_ids):
        meta = meta_index.get(pid, {})
        cat = paper_categories.get(pid, Category.OUT_OF_SCOPE.value)

        if pid in core_ids:
            node_type = "core"
        elif pid in secondary_ids:
            node_type = "secondary"
        else:
            node_type = "boundary"

        node = {
            "paper_id": pid,
            "title": meta.get("title"),
            "year": meta.get("year"),
            "venue": meta.get("venue"),
            "authors": meta.get("authors", []),
            "category": cat,
            "node_type": node_type,
            "citation_count": meta.get("citation_count") or meta.get("citationCount"),
            "abstract": meta.get("abstract"),
            "doi": meta.get("doi"),
            "external_ids": meta.get("external_ids") or meta.get("externalIds") or {},
        }
        nodes.append(node)

    # Step 6: Write outputs
    nodes_path = network_dir / "nodes.jsonl"
    with nodes_path.open("w", encoding="utf-8") as f:
        for node in nodes:
            f.write(json.dumps(node, ensure_ascii=False) + "\n")

    edges_path = network_dir / "edges.jsonl"
    with edges_path.open("w", encoding="utf-8") as f:
        for edge in edges:
            f.write(json.dumps(edge, ensure_ascii=False) + "\n")

    # Step 7: Compute network stats
    # Simple connected components via BFS
    adjacency: dict[str, set[str]] = {pid: set() for pid in all_node_ids}
    for edge in edges:
        src, tgt = edge["from"], edge["to"]
        if src in adjacency:
            adjacency[src].add(tgt)
        if tgt in adjacency:
            adjacency[tgt].add(src)

    visited: set[str] = set()
    components: list[int] = []
    for pid in all_node_ids:
        if pid in visited:
            continue
        # BFS
        component_size = 0
        queue = [pid]
        while queue:
            node = queue.pop()
            if node in visited:
                continue
            visited.add(node)
            component_size += 1
            for neighbor in adjacency.get(node, set()):
                if neighbor not in visited:
                    queue.append(neighbor)
        components.append(component_size)

    components.sort(reverse=True)
    isolated = sum(1 for c in components if c == 1)
    n_nodes = len(all_node_ids)
    n_edges = len(edges)
    density = (2 * n_edges) / (n_nodes * (n_nodes - 1)) if n_nodes > 1 else 0.0

    # Category distribution
    cat_dist: dict[str, int] = {}
    for node in nodes:
        cat = node["category"]
        cat_dist[cat] = cat_dist.get(cat, 0) + 1

    stats = {
        "nodes": {
            "total": n_nodes,
            "core": len(core_ids),
            "secondary": len(secondary_ids),
            "boundary": len(boundary_ids),
        },
        "edges": {
            "total": n_edges,
            "deduplicated": True,
        },
        "components": {
            "total": len(components),
            "largest": components[0] if components else 0,
            "isolated": isolated,
        },
        "density": round(density, 8),
        "category_distribution": cat_dist,
        "built_at": now_iso(),
    }

    stats_path = network_dir / "network_stats.json"
    stats_path.write_text(json.dumps(stats, indent=2), encoding="utf-8")

    logger.info("Network built: %d nodes, %d edges, %d components", n_nodes, n_edges, len(components))
    return stats


def _map_legacy_category(category: str, scope: str | None) -> str:
    """Map legacy classifications.jsonl categories to the 6-cat taxonomy."""
    # Core categories from old system
    if category == "fmri_gnn":
        return Category.FMRI_GNN.value
    if category in ("brain_connectivity", "brain_network_analysis"):
        return Category.FMRI_NO_GRAPH.value
    if category in ("graph_theory_brain", "spectral_brain"):
        return Category.FMRI_GRAPH_CLASSICAL.value
    if category in ("geometric_brain", "manifold_brain", "tda_brain"):
        return Category.FMRI_GEOMETRIC_MANIFOLD.value
    # Secondary
    if category in ("foundational_ml_gnn", "graph_no_brain"):
        return Category.GRAPH_METHODS_NO_FMRI.value
    if category in ("fmri_methodology", "neuroimaging_no_graph"):
        return Category.FMRI_NO_GRAPH.value
    # Psychiatric/clinical fMRI with connectivity — often core
    if category in ("psychiatric_disorders", "clinical_neuro"):
        return Category.FMRI_NO_GRAPH.value
    # Everything else
    return Category.OUT_OF_SCOPE.value
