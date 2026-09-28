"""Build a styled, pre-positioned GEXF view of a public citation graph."""

from __future__ import annotations

import csv
import math
import random
from pathlib import Path

import networkx as nx


CATEGORY_COLORS = {
    "fmri_gnn": {"r": 17, "g": 148, "b": 137, "a": 0.92},
    "fmri_geometric_manifold": {"r": 218, "g": 145, "b": 55, "a": 0.92},
    "fmri_graph_classical": {"r": 73, "g": 103, "b": 161, "a": 0.88},
}


def _read_graph(nodes_path: Path, edges_path: Path) -> nx.DiGraph:
    graph = nx.DiGraph(name="fMRI Graph Library — catalog citation map v1")
    with nodes_path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            attributes = {key: value for key, value in row.items() if value}
            for key in ("year", "citation_count"):
                if attributes.get(key):
                    attributes[key] = int(attributes[key])
            graph.add_node(row["paper_id"], **attributes)
    with edges_path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            graph.add_edge(row["source"], row["target"], relation="cites")
    return graph


def _layout(graph: nx.DiGraph, seed: int) -> dict[str, tuple[float, float]]:
    try:
        import igraph as ig
    except ImportError as error:
        raise RuntimeError("Install the visualization extra: uv sync --extra visualization") from error

    node_ids = list(graph.nodes)
    index = {node_id: offset for offset, node_id in enumerate(node_ids)}
    edges = [(index[source], index[target]) for source, target in graph.edges]
    layout_graph = ig.Graph(n=len(node_ids), edges=edges, directed=False)
    ig.set_random_number_generator(random.Random(seed))
    coordinates = layout_graph.layout_drl()

    connected = [index[node_id] for node_id in node_ids if graph.degree(node_id) > 0]
    xs = [coordinates[offset][0] for offset in connected]
    ys = [coordinates[offset][1] for offset in connected]
    center_x = (min(xs) + max(xs)) / 2
    center_y = (min(ys) + max(ys)) / 2
    span = max(max(xs) - min(xs), max(ys) - min(ys), 1)
    scale = 1800 / span

    positions = {
        node_id: (
            (coordinates[offset][0] - center_x) * scale,
            (coordinates[offset][1] - center_y) * scale,
        )
        for offset, node_id in enumerate(node_ids)
        if graph.degree(node_id) > 0
    }
    isolates = sorted(node_id for node_id in node_ids if graph.degree(node_id) == 0)
    for offset, node_id in enumerate(isolates):
        angle = (2 * math.pi * offset) / max(len(isolates), 1)
        positions[node_id] = (1050 * math.cos(angle), 1050 * math.sin(angle))
    return positions


def build_gexf(
    nodes_path: Path,
    edges_path: Path,
    output_path: Path,
    *,
    seed: int = 42,
) -> dict[str, int]:
    """Write a deterministic, styled GEXF intended for immediate web exploration."""
    graph = _read_graph(nodes_path, edges_path)
    positions = _layout(graph, seed)
    degrees = dict(graph.degree())
    max_degree = max(degrees.values(), default=1)
    denominator = math.log1p(max_degree) or 1

    for node_id, attributes in graph.nodes(data=True):
        x, y = positions[node_id]
        degree = degrees[node_id]
        attributes["label"] = attributes.get("title", node_id)
        attributes["degree"] = degree
        attributes["in_degree"] = graph.in_degree(node_id)
        attributes["out_degree"] = graph.out_degree(node_id)
        attributes["viz"] = {
            "position": {"x": float(x), "y": float(y), "z": 0.0},
            "size": 2.0 + 12.0 * math.sqrt(math.log1p(degree) / denominator),
            "color": CATEGORY_COLORS.get(
                attributes.get("category"),
                {"r": 120, "g": 130, "b": 128, "a": 0.85},
            ),
        }

    for _, _, attributes in graph.edges(data=True):
        attributes["label"] = "cites"
        attributes["viz"] = {
            "color": {"r": 105, "g": 117, "b": 115, "a": 0.07},
            "thickness": 0.15,
        }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    nx.write_gexf(graph, output_path, version="1.2draft", prettyprint=False)
    return {
        "nodes": graph.number_of_nodes(),
        "edges": graph.number_of_edges(),
        "positioned_nodes": len(positions),
        "isolated_nodes": sum(degree == 0 for degree in degrees.values()),
    }
