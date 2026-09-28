"""Build a styled, pre-positioned GEXF view of a public citation graph."""

from __future__ import annotations

import csv
import hashlib
import json
import math
import random
from pathlib import Path

import networkx as nx


CATEGORY_COLORS = {
    "fmri_gnn": {"r": 17, "g": 148, "b": 137, "a": 0.92},
    "fmri_geometric_manifold": {"r": 218, "g": 145, "b": 55, "a": 0.92},
    "fmri_graph_classical": {"r": 73, "g": 103, "b": 161, "a": 0.88},
}

CATEGORY_HEX = {
    "fmri_gnn": "#119489",
    "fmri_geometric_manifold": "#da9137",
    "fmri_graph_classical": "#4967a1",
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


def _timeline_layout(graph: nx.DiGraph) -> dict[str, tuple[float, float]]:
    """Arrange every paper by publication year inside a method-family lane."""
    lane_centers = {
        "fmri_gnn": 620.0,
        "fmri_geometric_manifold": 0.0,
        "fmri_graph_classical": -620.0,
    }
    groups: dict[tuple[str, int], list[str]] = {}
    for node_id, attributes in graph.nodes(data=True):
        category = attributes.get("category", "fmri_graph_classical")
        year = int(attributes.get("year") or 2002)
        groups.setdefault((category, year), []).append(node_id)

    positions: dict[str, tuple[float, float]] = {}
    for (category, year), node_ids in groups.items():
        ordered = sorted(node_ids)
        center = lane_centers.get(category, 0.0)
        count = len(ordered)
        for index, node_id in enumerate(ordered):
            digest = hashlib.sha1(node_id.encode("utf-8")).digest()
            jitter_x = (int.from_bytes(digest[:2], "big") / 65535 - 0.5) * 48
            jitter_y = (int.from_bytes(digest[2:4], "big") / 65535 - 0.5) * 7
            year_x = -960 + ((max(2002, min(year, 2026)) - 2002) / 24) * 1920
            if count == 1:
                lane_y = center
            else:
                lane_y = center - 235 + (index / (count - 1)) * 470
            positions[node_id] = (year_x + jitter_x, lane_y + jitter_y)
    return positions


def _atlas_layout(graph: nx.DiGraph) -> dict[str, tuple[float, float]]:
    """Pack each method family into a distinct, degree-ranked constellation."""
    centers = {
        "fmri_graph_classical": (-430.0, 0.0),
        "fmri_gnn": (610.0, 350.0),
        "fmri_geometric_manifold": (620.0, -470.0),
    }
    groups: dict[str, list[str]] = {}
    for node_id, attributes in graph.nodes(data=True):
        groups.setdefault(attributes.get("category", "fmri_graph_classical"), []).append(node_id)

    golden_angle = math.pi * (3 - math.sqrt(5))
    positions: dict[str, tuple[float, float]] = {}
    for category, node_ids in groups.items():
        center_x, center_y = centers.get(category, (0.0, 0.0))
        ordered = sorted(node_ids, key=lambda node_id: (-graph.degree(node_id), node_id))
        spacing = 7.6
        for index, node_id in enumerate(ordered):
            radius = spacing * math.sqrt(index)
            angle = index * golden_angle
            positions[node_id] = (
                center_x + radius * math.cos(angle),
                center_y + radius * math.sin(angle),
            )
    return positions


def _impact_layout(graph: nx.DiGraph) -> dict[str, tuple[float, float]]:
    """Place papers by publication year and global citation count on a log scale."""
    citation_counts = [int(attributes.get("citation_count") or 0) for _, attributes in graph.nodes(data=True)]
    denominator = math.log1p(max(citation_counts, default=1)) or 1
    positions: dict[str, tuple[float, float]] = {}
    for node_id, attributes in graph.nodes(data=True):
        year = int(attributes.get("year") or 2002)
        citation_count = int(attributes.get("citation_count") or 0)
        digest = hashlib.sha1(node_id.encode("utf-8")).digest()
        jitter_x = (int.from_bytes(digest[:2], "big") / 65535 - 0.5) * 42
        jitter_y = (int.from_bytes(digest[2:4], "big") / 65535 - 0.5) * 15
        year_x = -960 + ((max(2002, min(year, 2026)) - 2002) / 24) * 1920
        impact_y = -690 + (math.log1p(citation_count) / denominator) * 1380
        positions[node_id] = (year_x + jitter_x, impact_y + jitter_y)
    return positions


def build_gexf(
    nodes_path: Path,
    edges_path: Path,
    output_path: Path,
    *,
    json_path: Path | None = None,
    seed: int = 42,
) -> dict[str, int]:
    """Write a deterministic, styled GEXF intended for immediate web exploration."""
    graph = _read_graph(nodes_path, edges_path)
    positions = _layout(graph, seed)
    timeline_positions = _timeline_layout(graph)
    atlas_positions = _atlas_layout(graph)
    impact_positions = _impact_layout(graph)
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
    if json_path is not None:
        export = {
            "attributes": {
                "name": "fMRI Graph Library — catalog citation map v1",
                "type": "directed",
            },
            "nodes": [
                {
                    "key": node_id,
                    "attributes": {
                        key: value
                        for key, value in attributes.items()
                        if key != "viz"
                    }
                    | {
                        "x": float(timeline_positions[node_id][0]),
                        "y": float(timeline_positions[node_id][1]),
                        "network_x": float(positions[node_id][0]),
                        "network_y": float(positions[node_id][1]),
                        "timeline_x": float(timeline_positions[node_id][0]),
                        "timeline_y": float(timeline_positions[node_id][1]),
                        "atlas_x": float(atlas_positions[node_id][0]),
                        "atlas_y": float(atlas_positions[node_id][1]),
                        "impact_x": float(impact_positions[node_id][0]),
                        "impact_y": float(impact_positions[node_id][1]),
                        "size": 1.4
                        + 5.6
                        * math.sqrt(math.log1p(degrees[node_id]) / denominator),
                        "color": CATEGORY_HEX.get(
                            attributes.get("category"), "#788280"
                        ),
                    },
                }
                for node_id, attributes in graph.nodes(data=True)
            ],
            "edges": [
                {
                    "key": f"e{index}",
                    "source": source,
                    "target": target,
                    "attributes": {
                        "relation": "cites",
                        "size": 0.18,
                        "color": "rgba(80, 99, 95, 0.10)",
                    },
                }
                for index, (source, target) in enumerate(graph.edges)
            ],
        }
        json_path.write_text(
            json.dumps(export, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
    return {
        "nodes": graph.number_of_nodes(),
        "edges": graph.number_of_edges(),
        "positioned_nodes": len(positions),
        "isolated_nodes": sum(degree == 0 for degree in degrees.values()),
    }
