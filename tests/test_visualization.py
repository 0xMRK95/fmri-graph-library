import csv
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from field_cartography.visualization import build_gexf


def test_gexf_has_positions_colors_and_sizes(tmp_path: Path) -> None:
    pytest.importorskip("igraph")
    nodes = tmp_path / "nodes.csv"
    edges = tmp_path / "edges.csv"
    with nodes.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["paper_id", "title", "year", "venue", "category", "doi", "arxiv", "pubmed", "pmc", "citation_count"])
        writer.writerow(["a", "Paper A", 2024, "Venue", "fmri_gnn", "", "", "", "", 10])
        writer.writerow(["b", "Paper B", 2023, "Venue", "fmri_graph_classical", "", "", "", "", 4])
        writer.writerow(["c", "Isolate", 2022, "Venue", "fmri_geometric_manifold", "", "", "", "", 0])
    with edges.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["source", "target", "relation"])
        writer.writerow(["a", "b", "cites"])

    output = tmp_path / "network.gexf"
    stats = build_gexf(nodes, edges, output)
    root = ET.parse(output).getroot()
    namespace = {
        "g": "http://www.gexf.net/1.2draft",
        "viz": "http://www.gexf.net/1.2draft/viz",
    }
    assert stats == {"nodes": 3, "edges": 1, "positioned_nodes": 3, "isolated_nodes": 1}
    assert len(root.findall(".//g:node", namespace)) == 3
    assert len(root.findall(".//viz:position", namespace)) == 3
    assert len(root.findall(".//viz:color", namespace)) == 4
    assert len(root.findall(".//viz:size", namespace)) == 3
