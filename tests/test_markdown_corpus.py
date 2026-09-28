import json
from pathlib import Path

from field_cartography.markdown_corpus import (
    build_index,
    corpus_stats,
    identity_from_path,
    read_document_lines,
    resolve_document,
    search_index,
)


def _write_jsonl(path: Path, records: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(record) + "\n" for record in records),
        encoding="utf-8",
    )


def test_build_search_resolve_and_read_local_markdown(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    cache_dir = data_dir / "markdown_corpus" / "cache"
    paper_dir = cache_dir / "doi_10.1000_example.manual"
    paper_dir.mkdir(parents=True)
    source = paper_dir / "paper.md"
    source.write_text(
        "# Graph learning for fMRI\n\n"
        "## Abstract\n\n"
        "Functional connectivity is represented as a graph.\n\n"
        "## Results\n\n"
        "The graph baseline was compared with a linear baseline.\n\n"
        "## Reference atlas sensitivity\n\n"
        "A substantive retained phrase appears here.\n\n"
        "## Acknowledgments\n\n"
        "Recommended articles\n\n"
        "### A decoy citation\n\n"
        "A citation-only decoy phrase appears here.\n",
        encoding="utf-8",
    )
    _write_jsonl(
        data_dir / "papers.jsonl",
        [
            {
                "paper_id": "doi:10.1000/example",
                "title": "Graph learning for fMRI",
                "year": 2025,
                "venue": "Test Journal",
            }
        ],
    )
    _write_jsonl(
        data_dir / "classifications_7cat.jsonl",
        [
            {
                "paper_id": "doi:10.1000/example",
                "category": "fmri_gnn",
            }
        ],
    )

    database = data_dir / "markdown_corpus" / "index" / "corpus.sqlite"
    result = build_index(cache_dir, database, data_dir)

    assert result == {
        "documents": 1,
        "preferred_documents": 1,
        "chunks": 6,
        "metadata_matches": 1,
        "origins": {"drive": 1},
    }
    assert corpus_stats(database)["categories"] == {"fmri_gnn": 1}

    hits = search_index(database, '"functional connectivity"', category="fmri_gnn")
    assert len(hits) == 1
    assert hits[0].paper_id == "doi:10.1000/example"
    assert hits[0].line_start == 3

    title_hits = search_index(database, '"graph learning"', per_document=10)
    assert len(title_hits) == 1
    assert title_hits[0].line_start == 1
    assert "[Graph learning]" in title_hits[0].snippet

    substantive_hits = search_index(database, '"retained phrase"')
    assert len(substantive_hits) == 1
    assert "Reference atlas sensitivity" in substantive_hits[0].heading

    assert search_index(database, '"decoy phrase"') == []
    back_matter_hits = search_index(
        database,
        '"decoy phrase"',
        include_back_matter=True,
    )
    assert len(back_matter_hits) == 1
    assert "Acknowledgments" in back_matter_hits[0].heading

    document = resolve_document(database, "DOI:10.1000/EXAMPLE")
    assert document["preferred"] == 1
    assert read_document_lines(cache_dir, document, start=3, end=5) == [
        "     3  ## Abstract",
        "     4  ",
        "     5  Functional connectivity is represented as a graph.",
    ]


def test_identity_from_path_keeps_converter_provenance() -> None:
    assert identity_from_path("doi_10.1000_example.openalex/paper.md") == (
        "doi:10.1000/example",
        "doi:10.1000/example",
        "openalex",
    )
    assert identity_from_path("s2_ABC123/manual.md") == (
        "s2:abc123",
        "s2:abc123",
        "unknown",
    )
