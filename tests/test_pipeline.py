from pathlib import Path
from unittest.mock import MagicMock, patch
import json

import pytest
import yaml


@pytest.fixture
def mock_config(tmp_path: Path):
    """Create a mock config for testing."""
    from field_cartography.config import AppConfig, ApiConfig, StorageConfig, ExpansionConfig, ProvenanceConfig, RelevanceConfig

    data_dir = tmp_path / "data"
    graph_dir = tmp_path / "graph"
    cache_dir = tmp_path / "cache"
    duckdb_path = tmp_path / "db" / "test.duckdb"

    data_dir.mkdir(parents=True)
    graph_dir.mkdir(parents=True)
    cache_dir.mkdir(parents=True)
    duckdb_path.parent.mkdir(parents=True)

    (data_dir / "papers.jsonl").touch()
    (data_dir / "citations.jsonl").touch()
    (data_dir / "authors.jsonl").touch()
    (data_dir / "venues.jsonl").touch()
    (data_dir / "runs.jsonl").touch()
    (data_dir / "queries.jsonl").touch()

    return AppConfig(
        project="test",
        semantic_scholar=ApiConfig(base_url="https://api.semanticscholar.org/graph/v1", rate_limit_per_min=100),
        openalex=ApiConfig(base_url="https://api.openalex.org", rate_limit_per_min=60),
        crossref=ApiConfig(base_url="https://api.crossref.org", rate_limit_per_min=50),
        storage=StorageConfig(
            data_dir=data_dir,
            graph_dir=graph_dir,
            duckdb_path=duckdb_path,
            cache_dir=cache_dir,
        ),
        expansion=ExpansionConfig(default_depth=1, default_limit_per_paper=10, default_global_limit=100),
        provenance=ProvenanceConfig(confidence_default=0.7),
        relevance=RelevanceConfig(min_score=3, include_terms=[], exclude_terms=[]),
    )


class TestIngestSeeds:
    def test_ingest_seeds_with_s2_results(self, mock_config: "AppConfig", tmp_path: Path):
        """Test seed ingestion with Semantic Scholar results."""
        data_dir = mock_config.storage.data_dir
        seeds_yaml = data_dir / "seeds.yaml"
        seeds_yaml.write_text(yaml.dump({"seeds": [{"query": "test query", "tag": "test_tag"}]}))

        mock_s2_result = {
            "title": "Test Paper",
            "year": 2023,
            "venue": "Test Venue",
            "authors": [{"name": "Author One"}],
            "corpusId": "123456",
            "externalIds": {"DOI": "10.1000/test"},
        }

        with patch("field_cartography.pipeline.seed_ingest.SemanticScholarClient") as mock_s2_class:
            mock_s2 = MagicMock()
            mock_s2.search.return_value = [mock_s2_result]
            mock_s2_class.return_value = mock_s2

            with patch("field_cartography.pipeline.seed_ingest.OpenAlexClient") as mock_oa_class:
                mock_oa = MagicMock()
                mock_oa_class.return_value = mock_oa

                from field_cartography.pipeline.seed_ingest import ingest_seeds
                count = ingest_seeds(mock_config)

        assert count == 1
        papers = list(_read_jsonl(data_dir / "papers.jsonl"))
        assert len(papers) == 1
        assert papers[0]["title"] == "Test Paper"
        assert papers[0]["seed_tag"] == "test_tag"
        assert papers[0]["provenance"]["source"] == "semantic_scholar"
        assert papers[0]["provenance"]["confidence"] == 0.7

    def test_ingest_seeds_fallback_to_openalex(self, mock_config: "AppConfig"):
        """Test seed ingestion falls back to OpenAlex when S2 returns nothing."""
        data_dir = mock_config.storage.data_dir
        seeds_yaml = data_dir / "seeds.yaml"
        seeds_yaml.write_text(yaml.dump({"seeds": [{"query": "test query", "tag": "oa_tag"}]}))

        mock_oa_result = {
            "title": "OpenAlex Paper",
            "publication_year": 2022,
            "host_venue": {"display_name": "OA Venue"},
            "authorships": [{"author": {"display_name": "OA Author"}}],
            "id": "https://openalex.org/W123",
            "doi": "https://doi.org/10.2000/oa",
        }

        with patch("field_cartography.pipeline.seed_ingest.SemanticScholarClient") as mock_s2_class:
            mock_s2 = MagicMock()
            mock_s2.search.return_value = []
            mock_s2_class.return_value = mock_s2

            with patch("field_cartography.pipeline.seed_ingest.OpenAlexClient") as mock_oa_class:
                mock_oa = MagicMock()
                mock_oa.search.return_value = [mock_oa_result]
                mock_oa_class.return_value = mock_oa

                from field_cartography.pipeline.seed_ingest import ingest_seeds
                count = ingest_seeds(mock_config)

        assert count == 1
        papers = list(_read_jsonl(data_dir / "papers.jsonl"))
        assert len(papers) == 1
        assert papers[0]["title"] == "OpenAlex Paper"
        assert papers[0]["provenance"]["source"] == "openalex"

    def test_ingest_seeds_skips_duplicates(self, mock_config: "AppConfig"):
        """Test that duplicate papers are skipped."""
        data_dir = mock_config.storage.data_dir
        seeds_yaml = data_dir / "seeds.yaml"
        seeds_yaml.write_text(yaml.dump({"seeds": [
            {"query": "query 1", "tag": "tag1"},
            {"query": "query 2", "tag": "tag2"},
        ]}))

        mock_s2_result = {
            "title": "Same Paper",
            "year": 2023,
            "venue": "Venue",
            "authors": [{"name": "Author"}],
            "corpusId": "123456",
            "externalIds": {"DOI": "10.1000/same"},
        }

        with patch("field_cartography.pipeline.seed_ingest.SemanticScholarClient") as mock_s2_class:
            mock_s2 = MagicMock()
            mock_s2.search.return_value = [mock_s2_result]
            mock_s2_class.return_value = mock_s2

            with patch("field_cartography.pipeline.seed_ingest.OpenAlexClient") as mock_oa_class:
                mock_oa = MagicMock()
                mock_oa_class.return_value = mock_oa

                from field_cartography.pipeline.seed_ingest import ingest_seeds
                count = ingest_seeds(mock_config)

        assert count == 1  # Second one should be skipped as duplicate


class TestExpandCitations:
    def test_expand_backward_citations(self, mock_config: "AppConfig"):
        """Test backward citation expansion."""
        data_dir = mock_config.storage.data_dir

        seed_paper = {
            "paper_id": "doi:10.1000/seed",
            "title": "Seed Paper",
            "year": 2023,
            "venue": "Seed Venue",
            "authors": [{"name": "Seed Author"}],
            "doi": "10.1000/seed",
            "corpusId": "111",
            "provenance": {"source": "semantic_scholar", "fetched_at": "2024-01-01T00:00:00+00:00", "confidence": 0.7},
        }
        with (data_dir / "papers.jsonl").open("w") as f:
            f.write(json.dumps(seed_paper) + "\n")

        mock_ref = {
            "title": "Reference Paper",
            "year": 2020,
            "venue": "Ref Venue",
            "authors": [{"name": "Ref Author"}],
            "corpusId": "222",
            "externalIds": {"DOI": "10.1000/ref"},
        }

        with patch("field_cartography.pipeline.expand_citations.SemanticScholarClient") as mock_s2_class:
            mock_s2 = MagicMock()
            mock_s2.get_references.return_value = [mock_ref]
            mock_s2_class.return_value = mock_s2

            with patch("field_cartography.pipeline.expand_citations.OpenAlexClient") as mock_oa_class:
                mock_oa = MagicMock()
                mock_oa_class.return_value = mock_oa

                from field_cartography.pipeline.expand_citations import expand_citations
                added = expand_citations(mock_config, "backward", depth=1, limit_per_paper=10, global_limit=100)

        assert added == 1
        papers = list(_read_jsonl(data_dir / "papers.jsonl"))
        assert len(papers) == 2

        citations = list(_read_jsonl(data_dir / "citations.jsonl"))
        assert len(citations) == 1
        assert citations[0]["relation"] == "backward"

    def test_expand_forward_citations(self, mock_config: "AppConfig"):
        """Test forward citation expansion."""
        data_dir = mock_config.storage.data_dir

        seed_paper = {
            "paper_id": "doi:10.1000/seed",
            "title": "Seed Paper",
            "year": 2020,
            "venue": "Seed Venue",
            "authors": [{"name": "Seed Author"}],
            "doi": "10.1000/seed",
            "corpusId": "111",
            "provenance": {"source": "semantic_scholar", "fetched_at": "2024-01-01T00:00:00+00:00", "confidence": 0.7},
        }
        with (data_dir / "papers.jsonl").open("w") as f:
            f.write(json.dumps(seed_paper) + "\n")

        mock_cite = {
            "title": "Citing Paper",
            "year": 2023,
            "venue": "Cite Venue",
            "authors": [{"name": "Cite Author"}],
            "corpusId": "333",
            "externalIds": {"DOI": "10.1000/cite"},
        }

        with patch("field_cartography.pipeline.expand_citations.SemanticScholarClient") as mock_s2_class:
            mock_s2 = MagicMock()
            mock_s2.get_citations.return_value = [mock_cite]
            mock_s2_class.return_value = mock_s2

            with patch("field_cartography.pipeline.expand_citations.OpenAlexClient") as mock_oa_class:
                mock_oa = MagicMock()
                mock_oa_class.return_value = mock_oa

                from field_cartography.pipeline.expand_citations import expand_citations
                added = expand_citations(mock_config, "forward", depth=1, limit_per_paper=10, global_limit=100)

        assert added == 1
        citations = list(_read_jsonl(data_dir / "citations.jsonl"))
        assert len(citations) == 1
        assert citations[0]["relation"] == "forward"

    def test_expand_respects_global_limit(self, mock_config: "AppConfig"):
        """Test that expansion respects global_limit."""
        data_dir = mock_config.storage.data_dir

        seed_paper = {
            "paper_id": "doi:10.1000/seed",
            "title": "Seed Paper",
            "year": 2023,
            "venue": "Seed Venue",
            "authors": [{"name": "Seed Author"}],
            "doi": "10.1000/seed",
            "corpusId": "111",
            "provenance": {"source": "semantic_scholar", "fetched_at": "2024-01-01T00:00:00+00:00", "confidence": 0.7},
        }
        with (data_dir / "papers.jsonl").open("w") as f:
            f.write(json.dumps(seed_paper) + "\n")

        mock_refs = [
            {"title": f"Ref {i}", "year": 2020, "venue": "V", "authors": [], "corpusId": str(i), "externalIds": {}}
            for i in range(10)
        ]

        with patch("field_cartography.pipeline.expand_citations.SemanticScholarClient") as mock_s2_class:
            mock_s2 = MagicMock()
            mock_s2.get_references.return_value = mock_refs
            mock_s2_class.return_value = mock_s2

            with patch("field_cartography.pipeline.expand_citations.OpenAlexClient") as mock_oa_class:
                mock_oa = MagicMock()
                mock_oa_class.return_value = mock_oa

                from field_cartography.pipeline.expand_citations import expand_citations
                added = expand_citations(mock_config, "backward", depth=1, limit_per_paper=10, global_limit=3)

        assert added == 3

    def test_expand_is_resumable(self, mock_config: "AppConfig"):
        """Test that expansion skips already processed papers at same depth."""
        data_dir = mock_config.storage.data_dir

        seed_paper = {
            "paper_id": "doi:10.1000/seed",
            "title": "Seed Paper",
            "year": 2023,
            "venue": "Seed Venue",
            "authors": [{"name": "Seed Author"}],
            "doi": "10.1000/seed",
            "corpusId": "111",
            "provenance": {"source": "semantic_scholar", "fetched_at": "2024-01-01T00:00:00+00:00", "confidence": 0.7},
        }
        with (data_dir / "papers.jsonl").open("w") as f:
            f.write(json.dumps(seed_paper) + "\n")

        mock_ref = {
            "title": "Ref",
            "year": 2020,
            "venue": "V",
            "authors": [],
            "corpusId": "222",
            "externalIds": {},
        }

        with patch("field_cartography.pipeline.expand_citations.SemanticScholarClient") as mock_s2_class:
            mock_s2 = MagicMock()
            mock_s2.get_references.return_value = [mock_ref]
            mock_s2_class.return_value = mock_s2

            with patch("field_cartography.pipeline.expand_citations.OpenAlexClient") as mock_oa_class:
                mock_oa = MagicMock()
                mock_oa_class.return_value = mock_oa

                from field_cartography.pipeline.expand_citations import expand_citations
                added1 = expand_citations(mock_config, "backward", depth=1, limit_per_paper=10, global_limit=100)

        assert added1 == 1

        # Now run again with NO new refs returned - verify the original seed paper isn't re-processed
        with patch("field_cartography.pipeline.expand_citations.SemanticScholarClient") as mock_s2_class:
            mock_s2 = MagicMock()
            mock_s2.get_references.return_value = []  # No new refs
            mock_s2_class.return_value = mock_s2

            with patch("field_cartography.pipeline.expand_citations.OpenAlexClient") as mock_oa_class:
                mock_oa = MagicMock()
                mock_oa_class.return_value = mock_oa

                from field_cartography.pipeline.expand_citations import expand_citations
                added2 = expand_citations(mock_config, "backward", depth=1, limit_per_paper=10, global_limit=100)

        # The seed paper was already processed at depth 0, so it won't call get_references again
        # The ref paper is at depth 0 on second run (since frontier starts all papers at 0),
        # but it hasn't been processed at depth 0 yet, so it will be processed
        # However, with empty results, no new edges are added
        assert added2 == 0


class TestEnrichMetadata:
    def test_enrich_adds_provenance(self, mock_config: "AppConfig"):
        """Test that enrichment adds proper provenance to authors and venues."""
        data_dir = mock_config.storage.data_dir

        paper = {
            "paper_id": "doi:10.1000/test",
            "title": "Test Paper",
            "year": 2023,
            "venue": "Test Venue",
            "authors": [{"name": "Author One"}, {"name": "Author Two"}],
            "provenance": {"source": "semantic_scholar", "fetched_at": "2024-01-01T00:00:00+00:00", "confidence": 0.8},
        }
        with (data_dir / "papers.jsonl").open("w") as f:
            f.write(json.dumps(paper) + "\n")

        from field_cartography.pipeline.enrich_metadata import enrich_metadata
        result = enrich_metadata(data_dir, mock_config.storage.duckdb_path, confidence_default=0.7)

        assert result["authors_added"] == 2
        assert result["venues_added"] == 1

        authors = list(_read_jsonl(data_dir / "authors.jsonl"))
        assert len(authors) == 2
        assert all("provenance" in a for a in authors)
        assert authors[0]["provenance"]["source"] == "semantic_scholar"
        assert authors[0]["provenance"]["confidence"] == 0.8

        venues = list(_read_jsonl(data_dir / "venues.jsonl"))
        assert len(venues) == 1
        assert venues[0]["provenance"]["source"] == "semantic_scholar"


def _read_jsonl(path: Path):
    """Helper to read JSONL files."""
    if not path.exists():
        return
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            yield json.loads(line)
