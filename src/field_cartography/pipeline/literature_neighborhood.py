from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from html.parser import HTMLParser
import json
import re
from pathlib import Path
from typing import Any

import httpx

from ..config import AppConfig
from ..ingest.semantic_scholar import SemanticScholarClient


S2_NEIGHBOR_FIELDS = (
    "paperId,title,year,venue,authors,externalIds,corpusId,citationCount,"
    "referenceCount,abstract,url,openAccessPdf,fieldsOfStudy,s2FieldsOfStudy"
)


def _slugify(value: str, fallback: str = "literature") -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug[:80] or fallback


def _paper_key(paper: dict[str, Any]) -> str:
    external = paper.get("externalIds") or {}
    if paper.get("paperId"):
        return f"s2:{paper['paperId']}"
    if paper.get("corpusId"):
        return f"corpus:{paper['corpusId']}"
    if external.get("DOI"):
        return f"doi:{str(external['DOI']).lower()}"
    if external.get("ArXiv"):
        return f"arxiv:{external['ArXiv']}"
    return f"title:{_slugify(str(paper.get('title') or 'untitled'))}"


def _compact_paper(paper: dict[str, Any]) -> dict[str, Any]:
    external = paper.get("externalIds") or {}
    authors = [
        str(author.get("name"))
        for author in paper.get("authors") or []
        if author.get("name")
    ]
    open_access = paper.get("openAccessPdf") or {}
    return {
        "paper_id": _paper_key(paper),
        "s2_paper_id": paper.get("paperId"),
        "corpus_id": paper.get("corpusId"),
        "title": paper.get("title"),
        "year": paper.get("year"),
        "venue": paper.get("venue"),
        "authors": authors,
        "external_ids": external,
        "citation_count": paper.get("citationCount"),
        "reference_count": paper.get("referenceCount"),
        "url": paper.get("url"),
        "open_access_pdf": open_access.get("url"),
        "abstract_available": bool(paper.get("abstract")),
        "fields_of_study": paper.get("fieldsOfStudy") or [],
        "s2_fields_of_study": paper.get("s2FieldsOfStudy") or [],
    }


def _upsert_node(
    nodes: dict[str, dict[str, Any]],
    paper: dict[str, Any],
    role: str,
    seen_context: dict[str, Any],
) -> str:
    compact = _compact_paper(paper)
    key = compact["paper_id"]
    existing = nodes.get(key)
    if existing is None:
        compact["roles"] = [role]
        compact["seen_in"] = [seen_context]
        nodes[key] = compact
    else:
        if role not in existing["roles"]:
            existing["roles"].append(role)
        existing["seen_in"].append(seen_context)
    return key


def _top_neighbors(
    edges: list[dict[str, Any]],
    nodes: dict[str, dict[str, Any]],
    relation: str,
) -> list[dict[str, Any]]:
    paper_keys = [
        edge["target"] if relation == "references" else edge["source"]
        for edge in edges
        if edge["relation"] == relation
    ]
    papers = [nodes[key] for key in paper_keys if key in nodes]
    return sorted(
        papers,
        key=lambda paper: (
            int(paper.get("citation_count") or 0),
            int(paper.get("year") or 0),
        ),
        reverse=True,
    )


def _format_link(paper: dict[str, Any]) -> str:
    url = paper.get("open_access_pdf") or paper.get("url") or ""
    title = str(paper.get("title") or "Untitled").replace("|", "\\|")
    if url:
        return f"[{title}]({url})"
    return title


def _write_markdown_summary(
    path: Path,
    nodes: dict[str, dict[str, Any]],
    edges: list[dict[str, Any]],
    seeds: list[dict[str, Any]],
    queries: list[str],
    paper_ids: list[str],
) -> None:
    lines = [
        "# Literature Neighborhood",
        "",
        f"Generated: {datetime.now(timezone.utc).isoformat()}",
        "",
        "## Inputs",
        "",
        f"- Queries: {', '.join(queries) if queries else 'none'}",
        f"- Paper IDs: {', '.join(paper_ids) if paper_ids else 'none'}",
        "",
        "## Seeds",
        "",
        "| Year | Citations | Title | Venue |",
        "|---|---:|---|---|",
    ]
    for seed in seeds:
        lines.append(
            f"| {seed.get('year') or ''} | {seed.get('citation_count') or 0} | "
            f"{_format_link(seed)} | {seed.get('venue') or ''} |"
        )

    for relation, heading in (
        ("references", "Top References"),
        ("citations", "Top Citing Papers"),
    ):
        lines.extend(
            [
                "",
                f"## {heading}",
                "",
                "| Year | Citations | Title | Venue | OA PDF |",
                "|---|---:|---|---|---|",
            ]
        )
        for paper in _top_neighbors(edges, nodes, relation)[:25]:
            lines.append(
                f"| {paper.get('year') or ''} | {paper.get('citation_count') or 0} | "
                f"{_format_link(paper)} | {paper.get('venue') or ''} | "
                f"{paper.get('open_access_pdf') or ''} |"
            )

    lines.extend(
        [
            "",
            "## Files",
            "",
            f"- Nodes: `{path.with_suffix('.nodes.jsonl').name}`",
            f"- Edges: `{path.with_suffix('.edges.jsonl').name}`",
            "",
            "Use this as a focused discovery artifact. Read or convert primary papers before treating a novelty claim as established.",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def build_literature_neighborhood(
    cfg: AppConfig,
    queries: list[str],
    paper_ids: list[str],
    seed_limit: int = 1,
    neighbor_limit: int = 25,
    include_references: bool = True,
    include_citations: bool = True,
    output_name: str | None = None,
    client_factory: Callable[..., SemanticScholarClient] = SemanticScholarClient,
) -> dict[str, Any]:
    if not queries and not paper_ids:
        raise ValueError("at least one query or paper ID is required")

    out_dir = cfg.storage.data_dir / "literature_neighborhoods"
    out_dir.mkdir(parents=True, exist_ok=True)
    base_name = output_name or _slugify(queries[0] if queries else paper_ids[0])
    markdown_path = out_dir / f"{base_name}.md"
    nodes_path = markdown_path.with_suffix(".nodes.jsonl")
    edges_path = markdown_path.with_suffix(".edges.jsonl")

    nodes: dict[str, dict[str, Any]] = {}
    edges: list[dict[str, Any]] = []
    seed_keys: set[str] = set()

    with client_factory(
        cfg.semantic_scholar.base_url,
        cfg.semantic_scholar.rate_limit_per_min,
        cfg.storage.cache_dir,
    ) as s2:
        for query in queries:
            for paper in s2.search(query, limit=seed_limit, fields=S2_NEIGHBOR_FIELDS):
                key = _upsert_node(nodes, paper, "seed", {"query": query})
                seed_keys.add(key)
        for paper_id in paper_ids:
            paper = s2.get_paper(paper_id, fields=S2_NEIGHBOR_FIELDS)
            if paper:
                key = _upsert_node(nodes, paper, "seed", {"paper_id": paper_id})
                seed_keys.add(key)

        for seed_key in sorted(seed_keys):
            seed = nodes[seed_key]
            s2_id = seed.get("s2_paper_id") or seed.get("corpus_id")
            if not s2_id:
                continue
            if include_references:
                for ref in s2.get_references(
                    str(s2_id), limit=neighbor_limit, fields=S2_NEIGHBOR_FIELDS
                ):
                    target = _upsert_node(
                        nodes, ref, "reference", {"seed": seed_key}
                    )
                    edges.append(
                        {"source": seed_key, "target": target, "relation": "references"}
                    )
            if include_citations:
                for cite in s2.get_citations(
                    str(s2_id), limit=neighbor_limit, fields=S2_NEIGHBOR_FIELDS
                ):
                    source = _upsert_node(
                        nodes, cite, "citation", {"seed": seed_key}
                    )
                    edges.append(
                        {"source": source, "target": seed_key, "relation": "citations"}
                    )

    nodes_path.write_text(
        "\n".join(json.dumps(node, sort_keys=True) for node in nodes.values()) + "\n",
        encoding="utf-8",
    )
    edges_path.write_text(
        "\n".join(json.dumps(edge, sort_keys=True) for edge in edges) + "\n",
        encoding="utf-8",
    )
    seeds = [nodes[key] for key in sorted(seed_keys)]
    _write_markdown_summary(markdown_path, nodes, edges, seeds, queries, paper_ids)

    return {
        "nodes": len(nodes),
        "edges": len(edges),
        "seeds": len(seed_keys),
        "markdown": str(markdown_path),
        "nodes_file": str(nodes_path),
        "edges_file": str(edges_path),
    }


class _ReadableHTML(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self.skip_depth = 0
        self.heading: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "svg", "math"}:
            self.skip_depth += 1
            return
        if tag in {"h1", "h2", "h3"}:
            self.heading = "#" * int(tag[1])
            self.parts.append("\n\n")
        elif tag in {"p", "div", "section", "article", "li", "tr"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "svg", "math"} and self.skip_depth:
            self.skip_depth -= 1
            return
        if tag in {"h1", "h2", "h3", "p", "li"}:
            self.heading = None
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self.skip_depth:
            return
        text = re.sub(r"\s+", " ", data).strip()
        if not text:
            return
        if self.heading:
            self.parts.append(f"{self.heading} {text}")
        else:
            self.parts.append(text + " ")

    def markdown(self) -> str:
        text = "".join(self.parts)
        text = re.sub(r"[ \t]+\n", "\n", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()


def _arxiv_base(arxiv_id: str) -> str:
    clean = arxiv_id.strip().removeprefix("arXiv:").removeprefix("arxiv:")
    return f"doi_10.48550_arxiv.{clean}"


def _upsert_yearly_index(index_path: Path, record: dict[str, Any]) -> None:
    records: list[dict[str, Any]] = []
    if index_path.exists():
        for line in index_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            current = json.loads(line)
            if (
                str(current.get("paper_id", "")).lower()
                == str(record.get("paper_id", "")).lower()
            ):
                continue
            records.append(current)
    records.append(record)
    index_path.write_text(
        "\n".join(json.dumps(item, sort_keys=True) for item in records) + "\n",
        encoding="utf-8",
    )


def fetch_arxiv_markdown(
    cfg: AppConfig,
    arxiv_id: str,
    title: str | None = None,
    year: int | None = None,
    topic: str = "graph_methods_no_fmri",
    source_url: str | None = None,
) -> dict[str, Any]:
    clean_id = arxiv_id.strip().removeprefix("arXiv:").removeprefix("arxiv:")
    urls = [
        source_url,
        f"https://arxiv.org/html/{clean_id}",
        f"https://ar5iv.labs.arxiv.org/html/{clean_id}",
    ]
    html = ""
    used_url = ""
    with httpx.Client(timeout=45.0, follow_redirects=True) as client:
        for url in [item for item in urls if item]:
            response = client.get(url)
            if response.status_code < 400 and len(response.text) > 1000:
                html = response.text
                used_url = url
                break
    if not html:
        raise RuntimeError(f"could not fetch arXiv HTML for {clean_id}")

    parser = _ReadableHTML()
    parser.feed(html)
    body = parser.markdown()
    base = _arxiv_base(clean_id)
    paper_id = f"doi:10.48550/arxiv.{clean_id.lower()}"
    paper_title = title or f"arXiv:{clean_id}"
    front_matter = (
        f"# {paper_title}\n\n"
        f"Source: {used_url}\n\n"
        f"Paper ID: {paper_id}\n\n"
    )
    text = front_matter + body + "\n"

    yearly_dir = cfg.storage.data_dir / "markdown_corpus" / "yearly"
    fulltext_dir = yearly_dir / "fulltext"
    fulltext_dir.mkdir(parents=True, exist_ok=True)
    path = fulltext_dir / f"{base}.manual.md"
    path.write_text(text, encoding="utf-8")

    record = {
        "paper_id": paper_id,
        "base": f"{base}.manual",
        "title": paper_title,
        "year": year,
        "venue": "arXiv",
        "authors": [],
        "topic": topic,
        "is_core": topic == "fmri_gnn",
        "cluster": None,
        "seed_tag": None,
        "format": "markdown",
        "source_tag": "manual",
        "fulltext_file": path.relative_to(yearly_dir).as_posix(),
        "has_metadata": bool(title or year),
        "n_chars": len(text),
        "url": f"https://arxiv.org/abs/{clean_id}",
    }
    _upsert_yearly_index(yearly_dir / "INDEX.jsonl", record)
    return {
        "paper_id": paper_id,
        "path": str(path),
        "index": str(yearly_dir / "INDEX.jsonl"),
        "n_chars": len(text),
        "source_url": used_url,
    }
