import csv
import json
import xml.etree.ElementTree as ET
from pathlib import Path

from field_cartography.api.models import ExternalIds, PaperRecord
from field_cartography.public_graph import build_public_graph


class FakeClient:
    def __init__(self, papers: list[PaperRecord]) -> None:
        self.papers = {paper.canonical_id: paper for paper in papers}

    def papers_in_category(self, category: str) -> list[str]:
        return [
            paper_id
            for paper_id, paper in self.papers.items()
            if paper.category == category
        ]

    def metadata_batch(self, ids: list[str]) -> dict[str, PaperRecord]:
        return {paper_id: self.papers[paper_id] for paper_id in ids}


def test_public_graph_is_induced_by_catalog_and_keeps_isolates(tmp_path: Path) -> None:
    papers = [
        PaperRecord(
            canonical_id="doi:10.1/a",
            title="Paper A",
            year=2025,
            category="fmri_gnn",
            external_ids=ExternalIds(doi="10.1/a"),
        ),
        PaperRecord(
            canonical_id="doi:10.1/b",
            title="Paper B",
            year=2024,
            category="fmri_graph_classical",
        ),
        PaperRecord(
            canonical_id="doi:10.1/c",
            title="Isolated paper",
            category="fmri_geometric_manifold",
        ),
    ]
    edge_path = tmp_path / "edges.jsonl"
    records = [
        {"from": "doi:10.1/a", "to": "doi:10.1/b", "relation": "backward"},
        {"from": "doi:10.1/a", "to": "outside", "relation": "cites"},
        {"from": "outside", "to": "doi:10.1/b", "relation": "cites"},
        {"from": "doi:10.1/a", "to": "doi:10.1/a", "relation": "forward"},
    ]
    edge_path.write_text(
        "".join(json.dumps(record) + "\n" for record in records),
        encoding="utf-8",
    )
    output = tmp_path / "v1"
    stats = build_public_graph(FakeClient(papers), edge_path, output)

    assert stats["nodes"] == 3
    assert stats["edges"] == 1
    assert stats["isolated_nodes"] == 1
    assert stats["self_loops_removed"] == 1

    with (output / "nodes.csv").open(newline="", encoding="utf-8") as handle:
        nodes = list(csv.DictReader(handle))
    assert {node["paper_id"] for node in nodes} == {
        "doi:10.1/a",
        "doi:10.1/b",
        "doi:10.1/c",
    }
    with (output / "edges.csv").open(newline="", encoding="utf-8") as handle:
        edges = list(csv.DictReader(handle))
    assert edges == [{"source": "doi:10.1/a", "target": "doi:10.1/b", "relation": "cites"}]

    namespace = {"g": "http://graphml.graphstruct.org/xmlns"}
    graphml = ET.parse(output / "network.graphml")
    assert len(graphml.findall(".//g:node", namespace)) == 3
    assert len(graphml.findall(".//g:edge", namespace)) == 1
    assert len((output / "SHA256SUMS").read_text().splitlines()) == 5

