from pathlib import Path

import pytest

from field_cartography.api import Client
from field_cartography.api.models import ExternalIds, PaperRecord
from field_cartography.public_catalog import build_catalog, render_category, source_links


def paper(
    paper_id: str,
    title: str,
    category: str = "fmri_gnn",
    year: int | None = 2025,
    venue: str | None = None,
    identifiers: ExternalIds | None = None,
) -> PaperRecord:
    return PaperRecord(
        canonical_id=paper_id,
        title=title,
        category=category,
        year=year,
        venue=venue,
        external_ids=identifiers or ExternalIds(),
        fulltext_path="/private/paper.pdf",
        abstract="Private abstract",
    )


class FakeClient:
    def __init__(self, papers: list[PaperRecord]) -> None:
        self.papers = {p.canonical_id: p for p in papers}

    def papers_in_category(self, category: str) -> list[str]:
        return [pid for pid, p in self.papers.items() if p.category == category]

    def metadata_batch(self, ids: list[str]) -> dict[str, PaperRecord]:
        return {pid: self.papers[pid] for pid in ids}


def test_source_links_use_only_stable_identifiers() -> None:
    record = paper(
        "doi:10.48550/arXiv.2509.01426",
        "Example",
        identifiers=ExternalIds(pmc="PMC1234", pubmed="12345"),
    )
    assert source_links(record) == [
        "[DOI](https://doi.org/10.48550/arXiv.2509.01426)",
        "[arXiv](https://arxiv.org/abs/2509.01426)",
        "[PMC](https://pmc.ncbi.nlm.nih.gov/articles/PMC1234/)",
        "[PubMed](https://pubmed.ncbi.nlm.nih.gov/12345/)",
    ]


def test_source_link_accepts_open_copy_but_rejects_signed_url() -> None:
    record = paper("s2:123", "Example")
    record.open_access_pdf_url = "https://example.org/paper.pdf"
    assert source_links(record) == ["[Source: example.org](https://example.org/paper.pdf)"]
    record.open_access_pdf_url = "https://example.org/paper.pdf?token=secret"
    assert source_links(record) == []


def test_source_link_does_not_repeat_doi_resolver() -> None:
    record = paper("doi:10.1000/test", "Example")
    record.open_access_pdf_url = "https://doi.org/10.1000/test"
    assert source_links(record) == ["[DOI](https://doi.org/10.1000/test)"]


def test_category_page_is_sorted_and_never_contains_fulltext() -> None:
    records = [paper("doi:10.1/old", "Old", year=2020), paper("doi:10.1/new", "<scp>[New]</scp>", year=2025)]
    output = render_category("fmri_gnn", records)
    assert output.index("## 2025") < output.index("## 2020")
    assert "\\[New\\]" in output
    assert "&lt;scp&gt;" not in output
    assert "/private/paper.pdf" not in output
    assert "Private abstract" not in output


def test_build_catalog_writes_categories_and_preserves_hand_edited_files(tmp_path: Path) -> None:
    client = FakeClient([paper("doi:10.1/test", "A graph paper")])
    counts = build_catalog(client, tmp_path / "catalog")
    assert counts["fmri_gnn"] == 1
    assert (tmp_path / "catalog" / "fmri_gnn.md").exists()
    assert (tmp_path / "catalog" / "by_year" / "2025.md").exists()
    assert (tmp_path / "catalog" / "by_venue" / "unknown.md").exists()
    assert (tmp_path / "catalog" / "by_source" / "pending.md").exists()
    assert "1" in (tmp_path / "catalog" / "README.md").read_text()
    (tmp_path / "catalog" / "README.md").write_text("Human note")
    with pytest.raises(FileExistsError):
        build_catalog(client, tmp_path / "catalog")
    assert (tmp_path / "catalog" / "README.md").read_text() == "Human note"


def test_catalog_builds_year_venue_and_source_views(tmp_path: Path) -> None:
    records = [
        paper(
            "doi:10.1/recent",
            "Recent graph paper",
            year=2025,
            venue="NeuroImage",
            identifiers=ExternalIds(doi="10.1/recent"),
        ),
        paper(
            "arxiv:2401.12345",
            "Earlier geometric paper",
            category="fmri_geometric_manifold",
            year=2024,
            venue="Medical Image Analysis",
            identifiers=ExternalIds(arxiv="2401.12345"),
        ),
    ]
    output = tmp_path / "catalog"
    build_catalog(FakeClient(records), output)

    index = (output / "README.md").read_text()
    assert "by_year/README.md" in index
    assert "by_venue/README.md" in index
    assert "by_source/README.md" in index

    year_index = (output / "by_year" / "README.md").read_text()
    assert "[2025](2025.md)" in year_index
    assert "[2024](2024.md)" in year_index
    assert "Recent graph paper" in (output / "by_year" / "2025.md").read_text()

    venue_index = (output / "by_venue" / "README.md").read_text()
    assert "[N](n.md)" in venue_index
    assert "NeuroImage" in (output / "by_venue" / "n.md").read_text()

    assert "Recent graph paper" in (output / "by_source" / "doi.md").read_text()
    assert "Earlier geometric paper" in (output / "by_source" / "arxiv.md").read_text()


def test_seed_metadata_is_included_even_without_citation_discovery(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    corpus_dir = tmp_path / "corpus"
    data_dir.mkdir()
    corpus_dir.mkdir()
    (data_dir / "papers.jsonl").write_text(
        '{"paper_id":"doi:10.1000/seed","title":"Seed-only paper",'
        '"year":2024,"doi":"10.1000/seed"}\n',
        encoding="utf-8",
    )
    (data_dir / "classifications_7cat.jsonl").write_text(
        '{"paper_id":"doi:10.1000/seed","category":"fmri_gnn","tier":3}\n',
        encoding="utf-8",
    )
    client = Client(data_root=data_dir, corpus_root=corpus_dir, slim=True)
    counts = build_catalog(client, tmp_path / "catalog")
    assert counts["fmri_gnn"] == 1
    page = (tmp_path / "catalog" / "fmri_gnn.md").read_text()
    assert "Seed-only paper" in page
    assert "[DOI](https://doi.org/10.1000/seed)" in page
