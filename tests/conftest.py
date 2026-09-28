"""Shared test fixtures and utilities."""
from pathlib import Path
import json
from typing import Iterable

import pytest


@pytest.fixture
def mock_config(tmp_path: Path):
    """Create a mock config for testing with ALL required AppConfig fields."""
    from field_cartography.config import (
        AppConfig,
        ApiConfig,
        StorageConfig,
        ExpansionConfig,
        ProvenanceConfig,
        RelevanceConfig,
    )

    data_dir = tmp_path / "data"
    graph_dir = tmp_path / "graph"
    cache_dir = tmp_path / "cache"
    duckdb_path = tmp_path / "db" / "test.duckdb"

    data_dir.mkdir(parents=True)
    graph_dir.mkdir(parents=True)
    cache_dir.mkdir(parents=True)
    duckdb_path.parent.mkdir(parents=True)

    # Create all required JSONL files
    for fname in [
        "papers.jsonl",
        "citations.jsonl",
        "authors.jsonl",
        "venues.jsonl",
        "runs.jsonl",
        "queries.jsonl",
    ]:
        (data_dir / fname).touch()

    return AppConfig(
        project="test",
        semantic_scholar=ApiConfig(
            base_url="https://api.semanticscholar.org/graph/v1", rate_limit_per_min=100
        ),
        openalex=ApiConfig(
            base_url="https://api.openalex.org", rate_limit_per_min=60
        ),
        crossref=ApiConfig(base_url="https://api.crossref.org", rate_limit_per_min=50),
        storage=StorageConfig(
            data_dir=data_dir,
            graph_dir=graph_dir,
            duckdb_path=duckdb_path,
            cache_dir=cache_dir,
        ),
        expansion=ExpansionConfig(
            default_depth=1, default_limit_per_paper=10, default_global_limit=100
        ),
        provenance=ProvenanceConfig(confidence_default=0.7),
        relevance=RelevanceConfig(
            min_score=3, include_terms=[], exclude_terms=[]
        ),
    )


def _read_jsonl(path: Path) -> Iterable[dict]:
    """Helper to read JSONL files."""
    if not path.exists():
        return
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            yield json.loads(line)
