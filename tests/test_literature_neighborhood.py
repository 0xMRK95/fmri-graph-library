import json
from pathlib import Path
from types import SimpleNamespace

from field_cartography.pipeline.literature_neighborhood import (
    build_literature_neighborhood,
)


def _paper(identifier: str, title: str, citations: int = 0) -> dict:
    return {
        "paperId": identifier,
        "corpusId": int(identifier.removeprefix("S")),
        "title": title,
        "year": 2025,
        "venue": "Test Journal",
        "authors": [{"name": "A. Researcher"}],
        "externalIds": {},
        "citationCount": citations,
        "referenceCount": 1,
        "url": f"https://example.org/{identifier}",
        "openAccessPdf": None,
        "abstract": "An abstract",
        "fieldsOfStudy": ["Computer Science"],
        "s2FieldsOfStudy": [],
    }


class FakeSemanticScholarClient:
    def __init__(self, *_args, **_kwargs) -> None:
        self.seed = _paper("S1", "Seed paper", 10)

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None

    def search(self, query: str, limit: int, fields: str) -> list[dict]:
        assert query == "graph fMRI"
        assert limit == 1
        assert "paperId" in fields
        return [self.seed]

    def get_paper(self, paper_id: str, fields: str) -> dict:
        raise AssertionError(f"unexpected explicit paper lookup: {paper_id} {fields}")

    def get_references(self, paper_id: str, limit: int, fields: str) -> list[dict]:
        assert paper_id == "S1"
        return [_paper("S2", "Reference paper", 100)]

    def get_citations(self, paper_id: str, limit: int, fields: str) -> list[dict]:
        assert paper_id == "S1"
        return [_paper("S3", "Citing paper", 3)]


def test_literature_neighborhood_preserves_edge_direction_and_outputs(tmp_path: Path) -> None:
    cfg = SimpleNamespace(
        storage=SimpleNamespace(data_dir=tmp_path, cache_dir=tmp_path / "cache"),
        semantic_scholar=SimpleNamespace(
            base_url="https://api.example.test",
            rate_limit_per_min=100,
        ),
    )

    result = build_literature_neighborhood(
        cfg,
        queries=["graph fMRI"],
        paper_ids=[],
        output_name="test-neighborhood",
        client_factory=FakeSemanticScholarClient,
    )

    assert result["nodes"] == 3
    assert result["edges"] == 2
    edges = [
        json.loads(line)
        for line in Path(result["edges_file"]).read_text(encoding="utf-8").splitlines()
    ]
    assert edges == [
        {"relation": "references", "source": "s2:S1", "target": "s2:S2"},
        {"relation": "citations", "source": "s2:S3", "target": "s2:S1"},
    ]
    summary = Path(result["markdown"]).read_text(encoding="utf-8")
    assert "Seed paper" in summary
    assert "Reference paper" in summary
    assert "Citing paper" in summary
