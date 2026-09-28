"""Tests for OpenAlex fallback and cycle detection in citation expansion."""
from pathlib import Path
from unittest.mock import MagicMock, patch
import json

import pytest


class TestOpenAlexFallback:
    """Test OpenAlex fallback when Semantic Scholar returns empty results."""

    def test_expand_citations_fallback_to_openalex_backward(
        self, mock_config: "AppConfig"
    ):
        """Test backward expansion falls back to OpenAlex when S2 returns nothing."""
        data_dir = mock_config.storage.data_dir

        seed_paper = {
            "paper_id": "doi:10.1000/seed",
            "title": "Seed Paper",
            "year": 2023,
            "venue": "Seed Venue",
            "authors": [{"name": "Seed Author"}],
            "doi": "10.1000/seed",
            "openalex_id": "W123456",
            "provenance": {
                "source": "semantic_scholar",
                "fetched_at": "2024-01-01T00:00:00+00:00",
                "confidence": 0.7,
            },
        }
        with (data_dir / "papers.jsonl").open("w") as f:
            f.write(json.dumps(seed_paper) + "\n")

        mock_oa_work = {
            "id": "https://openalex.org/W789",
            "title": "OpenAlex Reference",
            "publication_year": 2020,
            "doi": "https://doi.org/10.2000/oa_ref",
            "host_venue": {"display_name": "OA Venue"},
            "authorships": [{"author": {"display_name": "OA Author"}}],
            "referenced_works": [],
        }

        with patch(
            "field_cartography.pipeline.expand_citations.SemanticScholarClient"
        ) as mock_s2_class:
            mock_s2 = MagicMock()
            mock_s2.get_references.return_value = []  # S2 returns nothing
            mock_s2_class.return_value = mock_s2

            with patch(
                "field_cartography.pipeline.expand_citations.OpenAlexClient"
            ) as mock_oa_class:
                mock_oa = MagicMock()
                mock_oa.get_work.return_value = mock_oa_work
                mock_oa_class.return_value = mock_oa

                from field_cartography.pipeline.expand_citations import expand_citations

                # Should not add anything since mock_oa_work has no referenced_works
                added = expand_citations(
                    mock_config,
                    "backward",
                    depth=1,
                    limit_per_paper=10,
                    global_limit=100,
                )

        assert added == 0
        # Verify that OpenAlex was called since S2 returned empty
        mock_oa.get_work.assert_called()

    def test_expand_citations_fallback_to_openalex_with_refs(
        self, mock_config: "AppConfig"
    ):
        """Test backward expansion retrieves references from OpenAlex."""
        data_dir = mock_config.storage.data_dir

        seed_paper = {
            "paper_id": "doi:10.1000/seed",
            "title": "Seed Paper",
            "year": 2023,
            "venue": "Seed Venue",
            "authors": [{"name": "Seed Author"}],
            "doi": "10.1000/seed",
            "openalex_id": "W123456",
            "provenance": {
                "source": "semantic_scholar",
                "fetched_at": "2024-01-01T00:00:00+00:00",
                "confidence": 0.7,
            },
        }
        with (data_dir / "papers.jsonl").open("w") as f:
            f.write(json.dumps(seed_paper) + "\n")

        # Main work with references
        mock_oa_work = {
            "id": "https://openalex.org/W123456",
            "title": "Seed Paper",
            "publication_year": 2023,
            "doi": "https://doi.org/10.1000/seed",
            "host_venue": {"display_name": "Seed Venue"},
            "authorships": [{"author": {"display_name": "Seed Author"}}],
            "referenced_works": ["https://openalex.org/W789"],
        }

        # Referenced work
        mock_ref_work = {
            "id": "https://openalex.org/W789",
            "title": "Referenced Paper",
            "publication_year": 2020,
            "doi": "https://doi.org/10.2000/ref",
            "host_venue": {"display_name": "Ref Venue"},
            "authorships": [{"author": {"display_name": "Ref Author"}}],
            "referenced_works": [],
        }

        with patch(
            "field_cartography.pipeline.expand_citations.SemanticScholarClient"
        ) as mock_s2_class:
            mock_s2 = MagicMock()
            mock_s2.get_references.return_value = []  # S2 returns nothing
            mock_s2_class.return_value = mock_s2

            with patch(
                "field_cartography.pipeline.expand_citations.OpenAlexClient"
            ) as mock_oa_class:
                mock_oa = MagicMock()

                def get_work_side_effect(work_id):
                    if "123456" in str(work_id):
                        return mock_oa_work
                    elif "789" in str(work_id):
                        return mock_ref_work
                    return None

                mock_oa.get_work.side_effect = get_work_side_effect
                mock_oa_class.return_value = mock_oa

                from field_cartography.pipeline.expand_citations import expand_citations

                added = expand_citations(
                    mock_config,
                    "backward",
                    depth=1,
                    limit_per_paper=10,
                    global_limit=100,
                )

        assert added == 1
        papers = list(_read_jsonl(data_dir / "papers.jsonl"))
        assert len(papers) == 2
        assert papers[1]["source"] == "openalex"

        citations = list(_read_jsonl(data_dir / "citations.jsonl"))
        assert len(citations) == 1
        assert citations[0]["relation"] == "backward"
        assert citations[0]["source"] == "openalex"

    def test_expand_citations_fallback_to_openalex_forward(
        self, mock_config: "AppConfig"
    ):
        """Test forward expansion uses OpenAlex when S2 returns nothing."""
        data_dir = mock_config.storage.data_dir

        seed_paper = {
            "paper_id": "doi:10.1000/seed",
            "title": "Seed Paper",
            "year": 2020,
            "venue": "Seed Venue",
            "authors": [{"name": "Seed Author"}],
            "doi": "10.1000/seed",
            "openalex_id": "W123456",
            "provenance": {
                "source": "semantic_scholar",
                "fetched_at": "2024-01-01T00:00:00+00:00",
                "confidence": 0.7,
            },
        }
        with (data_dir / "papers.jsonl").open("w") as f:
            f.write(json.dumps(seed_paper) + "\n")

        mock_oa_work = {
            "id": "https://openalex.org/W123456",
            "title": "Seed Paper",
            "publication_year": 2020,
            "doi": "https://doi.org/10.1000/seed",
            "host_venue": {"display_name": "Seed Venue"},
            "authorships": [{"author": {"display_name": "Seed Author"}}],
            "cited_by_api_url": "https://api.openalex.org/works?filter=cites:W123456&per-page=10",
        }

        mock_citing_work = {
            "id": "https://openalex.org/W999",
            "title": "Citing Paper",
            "publication_year": 2023,
            "doi": "https://doi.org/10.3000/cite",
            "host_venue": {"display_name": "Cite Venue"},
            "authorships": [{"author": {"display_name": "Cite Author"}}],
        }

        with patch(
            "field_cartography.pipeline.expand_citations.SemanticScholarClient"
        ) as mock_s2_class:
            mock_s2 = MagicMock()
            mock_s2.get_citations.return_value = []  # S2 returns nothing
            mock_s2_class.return_value = mock_s2

            with patch(
                "field_cartography.pipeline.expand_citations.OpenAlexClient"
            ) as mock_oa_class:
                mock_oa = MagicMock()
                mock_oa.base_url = "https://api.openalex.org"
                mock_oa.get_work.return_value = mock_oa_work
                mock_oa._get.return_value = {"results": [mock_citing_work]}
                mock_oa_class.return_value = mock_oa

                from field_cartography.pipeline.expand_citations import expand_citations

                added = expand_citations(
                    mock_config,
                    "forward",
                    depth=1,
                    limit_per_paper=10,
                    global_limit=100,
                )

        assert added == 1
        papers = list(_read_jsonl(data_dir / "papers.jsonl"))
        assert len(papers) == 2

        citations = list(_read_jsonl(data_dir / "citations.jsonl"))
        assert len(citations) == 1
        assert citations[0]["relation"] == "forward"
        assert citations[0]["source"] == "openalex"


class TestCycleDetection:
    """Test that circular citations don't cause infinite loops."""

    def test_cycle_detection_backward_same_depth(self, mock_config: "AppConfig"):
        """Test that papers citing each other at same depth don't loop."""
        data_dir = mock_config.storage.data_dir

        # Paper A and B that reference each other
        paper_a = {
            "paper_id": "doi:10.1000/a",
            "title": "Paper A",
            "year": 2020,
            "venue": "Venue A",
            "authors": [{"name": "Author A"}],
            "doi": "10.1000/a",
            "corpusId": "111",
            "provenance": {
                "source": "semantic_scholar",
                "fetched_at": "2024-01-01T00:00:00+00:00",
                "confidence": 0.7,
            },
        }
        paper_b = {
            "paper_id": "doi:10.1000/b",
            "title": "Paper B",
            "year": 2019,
            "venue": "Venue B",
            "authors": [{"name": "Author B"}],
            "doi": "10.1000/b",
            "corpusId": "222",
            "provenance": {
                "source": "semantic_scholar",
                "fetched_at": "2024-01-01T00:00:00+00:00",
                "confidence": 0.7,
            },
        }

        with (data_dir / "papers.jsonl").open("w") as f:
            f.write(json.dumps(paper_a) + "\n")
            f.write(json.dumps(paper_b) + "\n")

        # A references B, B references A
        mock_ref_b = {
            "title": "Paper B",
            "year": 2019,
            "venue": "Venue B",
            "authors": [{"name": "Author B"}],
            "corpusId": "222",
            "externalIds": {"DOI": "10.1000/b"},
        }
        mock_ref_a = {
            "title": "Paper A",
            "year": 2020,
            "venue": "Venue A",
            "authors": [{"name": "Author A"}],
            "corpusId": "111",
            "externalIds": {"DOI": "10.1000/a"},
        }

        with patch(
            "field_cartography.pipeline.expand_citations.SemanticScholarClient"
        ) as mock_s2_class:
            mock_s2 = MagicMock()

            def get_refs_side_effect(corpus_id, **kwargs):
                if corpus_id == "111":
                    return [mock_ref_b]
                elif corpus_id == "222":
                    return [mock_ref_a]
                return []

            mock_s2.get_references.side_effect = get_refs_side_effect
            mock_s2_class.return_value = mock_s2

            with patch(
                "field_cartography.pipeline.expand_citations.OpenAlexClient"
            ) as mock_oa_class:
                mock_oa = MagicMock()
                mock_oa_class.return_value = mock_oa

                from field_cartography.pipeline.expand_citations import expand_citations

                # Depth 1 means each paper processed once, no infinite loops
                added = expand_citations(
                    mock_config,
                    "backward",
                    depth=1,
                    limit_per_paper=10,
                    global_limit=100,
                )

        # Both A and B already exist in graph, so papers aren't new.
        # But citation edges A->B and B->A are new and will be added.
        # The key test is that it terminates (no infinite loop) with bounded edges.
        assert added <= 2  # at most 2 edges (A->B, B->A), no runaway

    def test_cycle_detection_with_new_papers(self, mock_config: "AppConfig"):
        """Test cycle involving new papers added during expansion."""
        data_dir = mock_config.storage.data_dir

        # Start with only paper A
        paper_a = {
            "paper_id": "doi:10.1000/a",
            "title": "Paper A",
            "year": 2021,
            "venue": "Venue A",
            "authors": [{"name": "Author A"}],
            "doi": "10.1000/a",
            "corpusId": "111",
            "provenance": {
                "source": "semantic_scholar",
                "fetched_at": "2024-01-01T00:00:00+00:00",
                "confidence": 0.7,
            },
        }

        with (data_dir / "papers.jsonl").open("w") as f:
            f.write(json.dumps(paper_a) + "\n")

        # Also add to DuckDB (as the real pipeline does)
        from field_cartography.graphdb.duckdb_store import DuckDBStore
        db = DuckDBStore(mock_config.storage.duckdb_path)
        db.add_paper("doi:10.1000/a", "10.1000/a", "111", None, "Paper A", 2021)
        db.close()

        # A -> B -> A (cycle)
        mock_ref_b = {
            "title": "Paper B",
            "year": 2020,
            "venue": "Venue B",
            "authors": [{"name": "Author B"}],
            "corpusId": "222",
            "externalIds": {"DOI": "10.1000/b"},
        }

        mock_ref_a = {
            "title": "Paper A",
            "year": 2021,
            "venue": "Venue A",
            "authors": [{"name": "Author A"}],
            "corpusId": "111",
            "externalIds": {"DOI": "10.1000/a"},
        }

        with patch(
            "field_cartography.pipeline.expand_citations.SemanticScholarClient"
        ) as mock_s2_class:
            mock_s2 = MagicMock()

            def get_refs_side_effect(corpus_id, **kwargs):
                if corpus_id == "111":  # A references B
                    return [mock_ref_b]
                elif corpus_id == "222":  # B references A
                    return [mock_ref_a]
                return []

            mock_s2.get_references.side_effect = get_refs_side_effect
            mock_s2_class.return_value = mock_s2

            with patch(
                "field_cartography.pipeline.expand_citations.OpenAlexClient"
            ) as mock_oa_class:
                mock_oa = MagicMock()
                mock_oa_class.return_value = mock_oa

                from field_cartography.pipeline.expand_citations import expand_citations

                # Depth 2 allows exploring A -> B -> A, but shouldn't infinite loop
                added = expand_citations(
                    mock_config,
                    "backward",
                    depth=2,
                    limit_per_paper=10,
                    global_limit=100,
                )

        # Key assertion: terminates, doesn't loop. B is new so A->B edge added.
        # B->A edge: A already exists so no new paper, but citation edge is new.
        # visited set prevents A from being re-queued to frontier.
        assert added >= 1  # At least A->B
        assert added <= 2  # At most A->B + B->A
        papers = list(_read_jsonl(data_dir / "papers.jsonl"))
        assert len(papers) == 2  # A and B only, no duplicates


class TestNeo4jIntegration:
    """Test Neo4j client integration in citation expansion."""

    def test_expand_citations_upserts_to_neo4j(self, mock_config: "AppConfig"):
        """Test that papers and citations are upserted to Neo4j when enabled."""
        data_dir = mock_config.storage.data_dir

        seed_paper = {
            "paper_id": "doi:10.1000/seed",
            "title": "Seed Paper",
            "year": 2023,
            "venue": "Seed Venue",
            "authors": [{"name": "Seed Author"}],
            "doi": "10.1000/seed",
            "corpusId": "111",
            "provenance": {
                "source": "semantic_Scholar",
                "fetched_at": "2024-01-01T00:00:00+00:00",
                "confidence": 0.7,
            },
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

        with patch(
            "field_cartography.pipeline.expand_citations.SemanticScholarClient"
        ) as mock_s2_class:
            mock_s2 = MagicMock()
            mock_s2.get_references.return_value = [mock_ref]
            mock_s2_class.return_value = mock_s2

            with patch(
                "field_cartography.pipeline.expand_citations.OpenAlexClient"
            ) as mock_oa_class:
                mock_oa = MagicMock()
                mock_oa_class.return_value = mock_oa

                with patch(
                    "field_cartography.graphdb.neo4j_client.Neo4jClient"
                ) as mock_neo4j_class:
                    mock_neo4j = MagicMock()
                    mock_neo4j_class.return_value = mock_neo4j

                    from field_cartography.pipeline.expand_citations import (
                        expand_citations,
                    )

                    added = expand_citations(
                        mock_config,
                        "backward",
                        depth=1,
                        limit_per_paper=10,
                        global_limit=100,
                        use_neo4j=True,
                    )

        assert added == 1

        # Verify Neo4j upsert methods were called
        mock_neo4j.upsert_paper.assert_called()
        mock_neo4j.upsert_citation.assert_called()
        mock_neo4j.close.assert_called_once()


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
