"""Tests for quality checks."""
from pathlib import Path
import json

import pytest


class TestQualityChecksDuplicates:
    """Test quality checks for duplicate paper_ids."""

    def test_qc_detects_duplicate_paper_ids(self, mock_config: "AppConfig"):
        """Test that QC detects duplicate paper_id entries."""
        data_dir = mock_config.storage.data_dir

        # Write papers with a duplicate
        papers = [
            {
                "paper_id": "doi:10.1000/unique1",
                "title": "Paper 1",
                "year": 2023,
                "venue": "Venue 1",
                "authors": [{"name": "Author 1"}],
                "provenance": {
                    "source": "semantic_scholar",
                    "fetched_at": "2024-01-01T00:00:00+00:00",
                    "confidence": 0.7,
                },
            },
            {
                "paper_id": "doi:10.1000/dup",
                "title": "Paper 2",
                "year": 2022,
                "venue": "Venue 2",
                "authors": [{"name": "Author 2"}],
                "provenance": {
                    "source": "semantic_scholar",
                    "fetched_at": "2024-01-01T00:00:00+00:00",
                    "confidence": 0.7,
                },
            },
            {
                "paper_id": "doi:10.1000/dup",  # Duplicate!
                "title": "Paper 2 Updated",
                "year": 2022,
                "venue": "Venue 2",
                "authors": [{"name": "Author 2"}],
                "provenance": {
                    "source": "semantic_scholar",
                    "fetched_at": "2024-01-02T00:00:00+00:00",
                    "confidence": 0.8,
                },
            },
        ]

        with (data_dir / "papers.jsonl").open("w") as f:
            for p in papers:
                f.write(json.dumps(p) + "\n")

        from field_cartography.pipeline.dedupe import find_duplicate_paper_ids

        dups = find_duplicate_paper_ids(data_dir)
        assert len(dups) == 1
        assert "doi:10.1000/dup" in dups

    def test_qc_no_duplicates_clean_data(self, mock_config: "AppConfig"):
        """Test QC passes when no duplicates exist."""
        data_dir = mock_config.storage.data_dir

        papers = [
            {
                "paper_id": "doi:10.1000/a",
                "title": "Paper A",
                "year": 2023,
                "venue": "Venue A",
                "authors": [{"name": "Author A"}],
                "provenance": {
                    "source": "semantic_scholar",
                    "fetched_at": "2024-01-01T00:00:00+00:00",
                    "confidence": 0.7,
                },
            },
            {
                "paper_id": "doi:10.1000/b",
                "title": "Paper B",
                "year": 2022,
                "venue": "Venue B",
                "authors": [{"name": "Author B"}],
                "provenance": {
                    "source": "semantic_scholar",
                    "fetched_at": "2024-01-01T00:00:00+00:00",
                    "confidence": 0.7,
                },
            },
        ]

        with (data_dir / "papers.jsonl").open("w") as f:
            for p in papers:
                f.write(json.dumps(p) + "\n")

        from field_cartography.pipeline.dedupe import find_duplicate_paper_ids

        dups = find_duplicate_paper_ids(data_dir)
        assert len(dups) == 0


class TestQualityChecksBrokenEdges:
    """Test quality checks for broken citation edges."""

    def test_qc_detects_broken_edges(self, mock_config: "AppConfig"):
        """Test that QC detects citations referencing non-existent papers."""
        data_dir = mock_config.storage.data_dir

        # Write one paper
        papers = [
            {
                "paper_id": "doi:10.1000/exists",
                "title": "Existing Paper",
                "year": 2023,
                "venue": "Venue",
                "authors": [{"name": "Author"}],
                "provenance": {
                    "source": "semantic_scholar",
                    "fetched_at": "2024-01-01T00:00:00+00:00",
                    "confidence": 0.7,
                },
            }
        ]

        with (data_dir / "papers.jsonl").open("w") as f:
            for p in papers:
                f.write(json.dumps(p) + "\n")

        # Write citations where one references a non-existent paper
        citations = [
            {
                "from": "doi:10.1000/exists",
                "to": "doi:10.1000/exists_too",
                "relation": "backward",
                "source": "semantic_scholar",
                "fetched_at": "2024-01-01T00:00:00+00:00",
            },
            {
                "from": "doi:10.1000/missing",  # This paper doesn't exist!
                "to": "doi:10.1000/exists",
                "relation": "forward",
                "source": "semantic_scholar",
                "fetched_at": "2024-01-01T00:00:00+00:00",
            },
        ]

        with (data_dir / "citations.jsonl").open("w") as f:
            for c in citations:
                f.write(json.dumps(c) + "\n")

        # Check manually (simulating what the QC command does)
        paper_index = {}
        with (data_dir / "papers.jsonl").open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                p = json.loads(line)
                pid = p.get("paper_id")
                if pid:
                    paper_index[pid] = p

        nodes = set(paper_index.keys())
        broken = 0
        with (data_dir / "citations.jsonl").open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                c = json.loads(line)
                if c.get("from") not in nodes or c.get("to") not in nodes:
                    broken += 1

        assert broken == 2  # Both citations are broken

    def test_qc_no_broken_edges_clean_data(self, mock_config: "AppConfig"):
        """Test QC passes with valid citation edges."""
        data_dir = mock_config.storage.data_dir

        papers = [
            {
                "paper_id": "doi:10.1000/a",
                "title": "Paper A",
                "year": 2023,
                "venue": "Venue A",
                "authors": [{"name": "Author A"}],
                "provenance": {
                    "source": "semantic_scholar",
                    "fetched_at": "2024-01-01T00:00:00+00:00",
                    "confidence": 0.7,
                },
            },
            {
                "paper_id": "doi:10.1000/b",
                "title": "Paper B",
                "year": 2022,
                "venue": "Venue B",
                "authors": [{"name": "Author B"}],
                "provenance": {
                    "source": "semantic_scholar",
                    "fetched_at": "2024-01-01T00:00:00+00:00",
                    "confidence": 0.7,
                },
            },
        ]

        citations = [
            {
                "from": "doi:10.1000/a",
                "to": "doi:10.1000/b",
                "relation": "backward",
                "source": "semantic_scholar",
                "fetched_at": "2024-01-01T00:00:00+00:00",
            },
            {
                "from": "doi:10.1000/b",
                "to": "doi:10.1000/a",
                "relation": "forward",
                "source": "semantic_scholar",
                "fetched_at": "2024-01-01T00:00:00+00:00",
            },
        ]

        with (data_dir / "papers.jsonl").open("w") as f:
            for p in papers:
                f.write(json.dumps(p) + "\n")

        with (data_dir / "citations.jsonl").open("w") as f:
            for c in citations:
                f.write(json.dumps(c) + "\n")

        # Check manually
        paper_index = {}
        with (data_dir / "papers.jsonl").open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                p = json.loads(line)
                pid = p.get("paper_id")
                if pid:
                    paper_index[pid] = p

        nodes = set(paper_index.keys())
        broken = 0
        with (data_dir / "citations.jsonl").open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                c = json.loads(line)
                if c.get("from") not in nodes or c.get("to") not in nodes:
                    broken += 1

        assert broken == 0


class TestQualityChecksProvenance:
    """Test quality checks for missing provenance."""

    def test_qc_detects_missing_provenance(self, mock_config: "AppConfig"):
        """Test that QC detects papers missing provenance fields."""
        data_dir = mock_config.storage.data_dir

        papers = [
            {
                "paper_id": "doi:10.1000/complete",
                "title": "Complete Paper",
                "year": 2023,
                "venue": "Venue",
                "authors": [{"name": "Author"}],
                "provenance": {
                    "source": "semantic_scholar",
                    "fetched_at": "2024-01-01T00:00:00+00:00",
                    "confidence": 0.7,
                },
            },
            {
                "paper_id": "doi:10.1000/no_provenance",
                "title": "Missing Provenance Paper",
                "year": 2023,
                "venue": "Venue",
                "authors": [{"name": "Author"}],
                # No provenance field!
            },
            {
                "paper_id": "doi:10.1000/incomplete_provenance",
                "title": "Incomplete Provenance",
                "year": 2023,
                "venue": "Venue",
                "authors": [{"name": "Author"}],
                "provenance": {
                    "source": "semantic_scholar",
                    # Missing fetched_at!
                },
            },
        ]

        with (data_dir / "papers.jsonl").open("w") as f:
            for p in papers:
                f.write(json.dumps(p) + "\n")

        # Simulate QC check for provenance
        found_issues = False
        with (data_dir / "papers.jsonl").open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                p = json.loads(line)
                prov = p.get("provenance")
                if not prov or not prov.get("source") or not prov.get("fetched_at"):
                    found_issues = True
                    break

        assert found_issues is True

    def test_qc_no_provenance_issues_clean_data(self, mock_config: "AppConfig"):
        """Test QC passes when all papers have complete provenance."""
        data_dir = mock_config.storage.data_dir

        papers = [
            {
                "paper_id": "doi:10.1000/a",
                "title": "Paper A",
                "year": 2023,
                "venue": "Venue A",
                "authors": [{"name": "Author A"}],
                "provenance": {
                    "source": "semantic_scholar",
                    "fetched_at": "2024-01-01T00:00:00+00:00",
                    "confidence": 0.7,
                },
            },
            {
                "paper_id": "doi:10.1000/b",
                "title": "Paper B",
                "year": 2022,
                "venue": "Venue B",
                "authors": [{"name": "Author B"}],
                "provenance": {
                    "source": "openalex",
                    "fetched_at": "2024-01-02T00:00:00+00:00",
                    "confidence": 0.8,
                },
            },
        ]

        with (data_dir / "papers.jsonl").open("w") as f:
            for p in papers:
                f.write(json.dumps(p) + "\n")

        # Simulate QC check
        found_issues = False
        with (data_dir / "papers.jsonl").open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                p = json.loads(line)
                prov = p.get("provenance")
                if not prov or not prov.get("source") or not prov.get("fetched_at"):
                    found_issues = True
                    break

        assert found_issues is False


class TestQualityChecksMissingMetadata:
    """Test quality checks for missing metadata (title, year, venue)."""

    def test_qc_detects_missing_metadata(self, mock_config: "AppConfig"):
        """Test that QC detects papers with missing title/year/venue."""
        data_dir = mock_config.storage.data_dir

        papers = [
            {
                "paper_id": "doi:10.1000/complete",
                "title": "Complete Paper",
                "year": 2023,
                "venue": "Venue",
                "authors": [{"name": "Author"}],
                "provenance": {
                    "source": "semantic_scholar",
                    "fetched_at": "2024-01-01T00:00:00+00:00",
                    "confidence": 0.7,
                },
            },
            {
                "paper_id": "doi:10.1000/no_year",
                "title": "Missing Year",
                # No year!
                "venue": "Venue",
                "authors": [{"name": "Author"}],
                "provenance": {
                    "source": "semantic_scholar",
                    "fetched_at": "2024-01-01T00:00:00+00:00",
                    "confidence": 0.7,
                },
            },
            {
                "paper_id": "doi:10.1000/no_venue",
                "title": "Missing Venue",
                "year": 2023,
                # No venue!
                "authors": [{"name": "Author"}],
                "provenance": {
                    "source": "semantic_scholar",
                    "fetched_at": "2024-01-01T00:00:00+00:00",
                    "confidence": 0.7,
                },
            },
        ]

        with (data_dir / "papers.jsonl").open("w") as f:
            for p in papers:
                f.write(json.dumps(p) + "\n")

        # Simulate QC check for metadata
        missing_count = 0
        with (data_dir / "papers.jsonl").open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                p = json.loads(line)
                if not p.get("title") or not p.get("year") or not p.get("venue"):
                    missing_count += 1

        assert missing_count == 2

    def test_qc_no_metadata_issues_clean_data(self, mock_config: "AppConfig"):
        """Test QC passes when all papers have required metadata."""
        data_dir = mock_config.storage.data_dir

        papers = [
            {
                "paper_id": "doi:10.1000/a",
                "title": "Paper A",
                "year": 2023,
                "venue": "Venue A",
                "authors": [{"name": "Author A"}],
                "provenance": {
                    "source": "semantic_scholar",
                    "fetched_at": "2024-01-01T00:00:00+00:00",
                    "confidence": 0.7,
                },
            },
            {
                "paper_id": "doi:10.1000/b",
                "title": "Paper B",
                "year": 2022,
                "venue": "Venue B",
                "authors": [{"name": "Author B"}],
                "provenance": {
                    "source": "openalex",
                    "fetched_at": "2024-01-02T00:00:00+00:00",
                    "confidence": 0.8,
                },
            },
        ]

        with (data_dir / "papers.jsonl").open("w") as f:
            for p in papers:
                f.write(json.dumps(p) + "\n")

        # Simulate QC check
        missing_count = 0
        with (data_dir / "papers.jsonl").open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                p = json.loads(line)
                if not p.get("title") or not p.get("year") or not p.get("venue"):
                    missing_count += 1

        assert missing_count == 0


class TestQualityChecksIntegration:
    """Integration tests for the full QC check suite."""

    def test_qc_full_suite_passes_on_clean_data(self, mock_config: "AppConfig"):
        """Test that all QC checks pass on clean data."""
        data_dir = mock_config.storage.data_dir

        papers = [
            {
                "paper_id": "doi:10.1000/a",
                "title": "Paper A",
                "year": 2023,
                "venue": "Venue A",
                "authors": [{"name": "Author A"}],
                "provenance": {
                    "source": "semantic_scholar",
                    "fetched_at": "2024-01-01T00:00:00+00:00",
                    "confidence": 0.7,
                },
            },
            {
                "paper_id": "doi:10.1000/b",
                "title": "Paper B",
                "year": 2022,
                "venue": "Venue B",
                "authors": [{"name": "Author B"}],
                "provenance": {
                    "source": "openalex",
                    "fetched_at": "2024-01-02T00:00:00+00:00",
                    "confidence": 0.8,
                },
            },
        ]

        citations = [
            {
                "from": "doi:10.1000/a",
                "to": "doi:10.1000/b",
                "relation": "backward",
                "source": "semantic_scholar",
                "fetched_at": "2024-01-01T00:00:00+00:00",
            }
        ]

        with (data_dir / "papers.jsonl").open("w") as f:
            for p in papers:
                f.write(json.dumps(p) + "\n")

        with (data_dir / "citations.jsonl").open("w") as f:
            for c in citations:
                f.write(json.dumps(c) + "\n")

        # Simulate full QC checks
        from field_cartography.pipeline.dedupe import find_duplicate_paper_ids

        issues = []

        # Check duplicates
        dups = find_duplicate_paper_ids(data_dir)
        if dups:
            issues.append(f"duplicate paper_id: {len(dups)}")

        # Check metadata and provenance
        paper_index = {}
        with (data_dir / "papers.jsonl").open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                p = json.loads(line)
                pid = p.get("paper_id")
                if pid:
                    paper_index[pid] = p

        missing = 0
        for p in paper_index.values():
            if not p.get("title") or not p.get("year") or not p.get("venue"):
                missing += 1
            prov = p.get("provenance")
            if not prov or not prov.get("source") or not prov.get("fetched_at"):
                issues.append("missing provenance")
                break

        if missing:
            issues.append(f"missing year/title/venue: {missing}")

        # Check broken edges
        nodes = set(paper_index.keys())
        broken = 0
        with (data_dir / "citations.jsonl").open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                c = json.loads(line)
                if c.get("from") not in nodes or c.get("to") not in nodes:
                    broken += 1
        if broken:
            issues.append(f"broken edges: {broken}")

        assert len(issues) == 0

    def test_qc_full_suite_reports_all_issues(self, mock_config: "AppConfig"):
        """Test that QC reports all issues found."""
        data_dir = mock_config.storage.data_dir

        papers = [
            {
                "paper_id": "doi:10.1000/a",
                "title": "Paper A",
                "year": 2023,
                "venue": "Venue A",
                "authors": [{"name": "Author A"}],
                # Missing provenance!
            },
            {
                "paper_id": "doi:10.1000/b",
                "title": "Paper B",
                # Missing year!
                "venue": "Venue B",
                "authors": [{"name": "Author B"}],
                "provenance": {
                    "source": "semantic_scholar",
                    "fetched_at": "2024-01-01T00:00:00+00:00",
                    "confidence": 0.7,
                },
            },
            {
                "paper_id": "doi:10.1000/dup",
                "title": "Duplicate Paper",
                "year": 2022,
                "venue": "Venue C",
                "authors": [{"name": "Author C"}],
                "provenance": {
                    "source": "semantic_scholar",
                    "fetched_at": "2024-01-01T00:00:00+00:00",
                    "confidence": 0.7,
                },
            },
            {
                "paper_id": "doi:10.1000/dup",  # Duplicate!
                "title": "Duplicate Paper",
                "year": 2022,
                "venue": "Venue C",
                "authors": [{"name": "Author C"}],
                "provenance": {
                    "source": "semantic_scholar",
                    "fetched_at": "2024-01-01T00:00:00+00:00",
                    "confidence": 0.7,
                },
            },
        ]

        citations = [
            {
                "from": "doi:10.1000/missing",  # Paper doesn't exist!
                "to": "doi:10.1000/a",
                "relation": "backward",
                "source": "semantic_scholar",
                "fetched_at": "2024-01-01T00:00:00+00:00",
            }
        ]

        with (data_dir / "papers.jsonl").open("w") as f:
            for p in papers:
                f.write(json.dumps(p) + "\n")

        with (data_dir / "citations.jsonl").open("w") as f:
            for c in citations:
                f.write(json.dumps(c) + "\n")

        # Simulate full QC checks
        from field_cartography.pipeline.dedupe import find_duplicate_paper_ids

        issues = []

        # Check duplicates
        dups = find_duplicate_paper_ids(data_dir)
        if dups:
            issues.append(f"duplicate paper_id: {len(dups)}")

        # Check metadata and provenance
        paper_index = {}
        with (data_dir / "papers.jsonl").open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                p = json.loads(line)
                pid = p.get("paper_id")
                if pid:
                    paper_index[pid] = p

        missing = 0
        for p in paper_index.values():
            if not p.get("title") or not p.get("year") or not p.get("venue"):
                missing += 1
            prov = p.get("provenance")
            if not prov or not prov.get("source") or not prov.get("fetched_at"):
                issues.append("missing provenance")
                break

        if missing:
            issues.append(f"missing year/title/venue: {missing}")

        # Check broken edges
        nodes = set(paper_index.keys())
        broken = 0
        with (data_dir / "citations.jsonl").open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                c = json.loads(line)
                if c.get("from") not in nodes or c.get("to") not in nodes:
                    broken += 1
        if broken:
            issues.append(f"broken edges: {broken}")

        # Should have detected multiple issues
        assert len(issues) >= 3
        assert any("duplicate" in i for i in issues)
        assert any("missing" in i for i in issues)
        assert any("broken" in i for i in issues)
