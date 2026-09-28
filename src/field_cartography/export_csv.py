from __future__ import annotations

import csv
import xml.etree.ElementTree as ET
from pathlib import Path

from .storage_jsonl import read_jsonl


def export_graph(data_dir: Path, graph_dir: Path) -> None:
    """Export citation network to CSV (Gephi/Cytoscape compatible).

    Prefers network/ subdirectory (Phase 4 output) if available,
    falls back to legacy papers.jsonl + citations.jsonl.
    """
    network_dir = data_dir / "network"
    if (network_dir / "nodes.jsonl").exists():
        nodes = list(read_jsonl(network_dir / "nodes.jsonl"))
        edges = list(read_jsonl(network_dir / "edges.jsonl"))
    else:
        nodes = list(read_jsonl(data_dir / "papers.jsonl"))
        edges = list(read_jsonl(data_dir / "citations.jsonl"))

    graph_dir.mkdir(parents=True, exist_ok=True)

    # CSV export
    _export_csv(nodes, edges, graph_dir)

    # GraphML export
    _export_graphml(nodes, edges, graph_dir / "network.graphml")


def _export_csv(nodes: list[dict], edges: list[dict], graph_dir: Path) -> None:
    nodes_path = graph_dir / "nodes.csv"
    edges_path = graph_dir / "edges.csv"

    with nodes_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "Id", "Label", "Year", "Venue", "DOI", "Category", "Node_Type", "Citation_Count",
        ])
        for p in nodes:
            title = p.get("title") or ""
            label = (title[:60] + "...") if len(title) > 60 else title
            writer.writerow([
                p.get("paper_id"),
                label,
                p.get("year"),
                p.get("venue"),
                p.get("doi"),
                p.get("category", ""),
                p.get("node_type", ""),
                p.get("citation_count", ""),
            ])

    with edges_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["Source", "Target", "Type", "Relation"])
        for c in edges:
            writer.writerow([c.get("from"), c.get("to"), "Directed", c.get("relation")])


def _export_graphml(nodes: list[dict], edges: list[dict], output_path: Path) -> None:
    """Export network as GraphML (compatible with Gephi, Cytoscape, NetworkX)."""
    ns = "http://graphml.graphstruct.org/xmlns"
    ET.register_namespace("", ns)

    graphml = ET.Element("graphml", xmlns=ns)

    # Define node attribute keys
    attrs = [
        ("label", "string"), ("year", "int"), ("venue", "string"),
        ("doi", "string"), ("category", "string"), ("node_type", "string"),
        ("citation_count", "int"),
    ]
    for attr_name, attr_type in attrs:
        ET.SubElement(graphml, "key", {
            "id": attr_name, "for": "node", "attr.name": attr_name, "attr.type": attr_type,
        })

    # Define edge attribute keys
    ET.SubElement(graphml, "key", {
        "id": "relation", "for": "edge", "attr.name": "relation", "attr.type": "string",
    })

    graph = ET.SubElement(graphml, "graph", edgedefault="directed")

    # Add nodes
    for p in nodes:
        pid = p.get("paper_id", "")
        node_el = ET.SubElement(graph, "node", id=pid)
        title = p.get("title") or ""
        label = (title[:60] + "...") if len(title) > 60 else title
        _add_data(node_el, "label", label)
        if p.get("year"):
            _add_data(node_el, "year", str(p["year"]))
        if p.get("venue"):
            _add_data(node_el, "venue", p["venue"])
        if p.get("doi"):
            _add_data(node_el, "doi", p["doi"])
        if p.get("category"):
            _add_data(node_el, "category", p["category"])
        if p.get("node_type"):
            _add_data(node_el, "node_type", p["node_type"])
        if p.get("citation_count"):
            _add_data(node_el, "citation_count", str(p["citation_count"]))

    # Add edges
    for i, c in enumerate(edges):
        edge_el = ET.SubElement(graph, "edge", {
            "id": f"e{i}",
            "source": c.get("from", ""),
            "target": c.get("to", ""),
        })
        _add_data(edge_el, "relation", c.get("relation", "cites"))

    tree = ET.ElementTree(graphml)
    ET.indent(tree, space="  ")
    tree.write(str(output_path), encoding="unicode", xml_declaration=True)


def _add_data(parent: ET.Element, key: str, value: str) -> None:
    d = ET.SubElement(parent, "data", key=key)
    d.text = value
