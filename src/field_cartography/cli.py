from __future__ import annotations

import typer
from pathlib import Path
import json
import yaml

from .config import load_config
from .logging import setup_logging
from .storage_jsonl import JsonlStore
from .pipeline.seed_ingest import ingest_seeds
from .pipeline.expand_citations import expand_citations
from .pipeline.enrich_metadata import enrich_metadata
from .pipeline.cluster import cluster_graph
from .pipeline.dedupe import find_duplicate_paper_ids
from .pipeline.fetch_abstracts import fetch_abstracts, fetch_abstracts_extended
from .pipeline.backfill_metadata import backfill_metadata
from .pipeline.recursive_expand import recursive_expand
from .pipeline.consolidate_seeds import consolidate_seeds
from .pipeline.classify_7cat import (
    prepare_classify_batches,
    collect_classification_stats,
    ingest_classification_file,
    ingest_classification_text,
    ingest_classification_dir,
    extract_from_agent_logs,
    prepare_audit_batches,
    compute_audit_agreement,
)
from .pipeline.build_network import build_network
from .pipeline.literature_neighborhood import (
    build_literature_neighborhood,
    fetch_arxiv_markdown,
)
from .export_csv import export_graph
from .markdown_corpus import (
    build_index,
    corpus_stats,
    hit_as_dict,
    list_documents,
    read_document_lines,
    resolve_document,
    search_index,
)

app = typer.Typer()

corpus_app = typer.Typer(help="Search the converted Markdown paper corpus.")
app.add_typer(corpus_app, name="corpus")

literature_app = typer.Typer(help="Focused external literature discovery and corpus-gap tools.")
app.add_typer(literature_app, name="literature")


def _corpus_paths() -> tuple[Path, Path, Path, Path]:
    cfg = load_config()
    root = cfg.storage.data_dir / "markdown_corpus"
    return (
        root / "cache",
        root / "yearly",
        root / "index" / "corpus.sqlite",
        cfg.storage.data_dir,
    )


@corpus_app.command("build")
def corpus_build() -> None:
    """Build the section-level SQLite FTS index from the local Markdown mirror."""
    cache_dir, yearly_dir, database_path, data_dir = _corpus_paths()
    result = build_index(cache_dir, database_path, data_dir, yearly_dir)
    typer.echo(json.dumps(result, indent=2))


@corpus_app.command("search")
def corpus_search(
    query: str = typer.Argument(..., help="SQLite FTS5 query"),
    limit: int = typer.Option(10, min=1, max=100),
    category: str | None = typer.Option(None, help="Exact 7-category label"),
    year_min: int | None = typer.Option(None),
    year_max: int | None = typer.Option(None),
    all_versions: bool = typer.Option(False, help="Include duplicate converted versions"),
    include_low_quality: bool = typer.Option(
        False, help="Include stubs, thin records, and suspected title mismatches"
    ),
    include_back_matter: bool = typer.Option(
        False, help="Include references, acknowledgments, and other back matter"
    ),
    per_paper: int = typer.Option(2, min=1, max=10, help="Maximum passages per paper"),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON"),
) -> None:
    """Find line-addressable evidence passages, preferring one version per paper."""
    _, _, database_path, _ = _corpus_paths()
    hits = search_index(
        database_path,
        query,
        limit=limit,
        category=category,
        year_min=year_min,
        year_max=year_max,
        all_versions=all_versions,
        include_low_quality=include_low_quality,
        include_back_matter=include_back_matter,
        per_document=per_paper,
    )
    if json_output:
        typer.echo(json.dumps([hit_as_dict(hit) for hit in hits], indent=2))
        return
    for hit in hits:
        year = hit.year or "?"
        typer.echo(f"[{hit.document_id}] {hit.title} ({year})")
        typer.echo(
            f"  {hit.source_path}:{hit.line_start}-{hit.line_end} | {hit.heading}"
        )
        typer.echo(f"  {hit.snippet}\n")


@corpus_app.command("stats")
def corpus_statistics() -> None:
    """Report corpus/index coverage."""
    _, _, database_path, _ = _corpus_paths()
    typer.echo(json.dumps(corpus_stats(database_path), indent=2))


@corpus_app.command("latest")
def corpus_latest(
    limit: int = typer.Option(20, min=1, max=100),
    category: str | None = typer.Option(None, help="Exact 7-category label"),
    include_unknown_year: bool = typer.Option(False),
) -> None:
    """List the newest preferred conversions using joined publication metadata."""
    _, _, database_path, _ = _corpus_paths()
    for document in list_documents(
        database_path,
        limit=limit,
        category=category,
        include_unknown_year=include_unknown_year,
    ):
        typer.echo(
            f"[{document['id']}] {document['year'] or '?'} | {document['title']}\n"
            f"  {document['origin']}:{document['converter_source']} | "
            f"{document['source_path']}"
        )


@corpus_app.command("read")
def corpus_read(
    selector: str = typer.Argument(..., help="Document ID, paper ID, or exact source path"),
    start: int = typer.Option(1, min=1),
    end: int | None = typer.Option(None, min=1),
) -> None:
    """Read a conversion with stable source line numbers for evidence citation."""
    cache_dir, _, database_path, _ = _corpus_paths()
    try:
        document = resolve_document(database_path, selector)
    except KeyError as error:
        raise typer.BadParameter(str(error)) from error
    typer.echo(
        f"# [{document['id']}] {document['title']} ({document['year'] or '?'})\n"
        f"# {document['source_path']}\n"
    )
    for line in read_document_lines(cache_dir, document, start=start, end=end):
        typer.echo(line)


@literature_app.command("neighborhood")
def literature_neighborhood(
    query: list[str] | None = typer.Option(
        None, "--query", "-q", help="Semantic Scholar query to resolve as a seed"
    ),
    paper_id: list[str] | None = typer.Option(
        None,
        "--paper-id",
        "-p",
        help="Semantic Scholar paper ID, CorpusId:<id>, DOI:<doi>, or ARXIV:<id>",
    ),
    seed_limit: int = typer.Option(1, min=1, max=10),
    neighbor_limit: int = typer.Option(25, min=1, max=100),
    references: bool = typer.Option(True, "--references/--no-references"),
    citations: bool = typer.Option(True, "--citations/--no-citations"),
    output_name: str | None = typer.Option(None, help="Base filename under data/literature_neighborhoods"),
) -> None:
    """Follow a small Semantic Scholar citation/reference neighborhood."""
    setup_logging()
    cfg = load_config()
    try:
        result = build_literature_neighborhood(
            cfg,
            queries=query or [],
            paper_ids=paper_id or [],
            seed_limit=seed_limit,
            neighbor_limit=neighbor_limit,
            include_references=references,
            include_citations=citations,
            output_name=output_name,
        )
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error
    typer.echo(json.dumps(result, indent=2))


@literature_app.command("fetch-arxiv")
def literature_fetch_arxiv(
    arxiv_id: str = typer.Argument(..., help="arXiv identifier, e.g. 2509.21489"),
    title: str | None = typer.Option(None, help="Known title to store in the yearly index"),
    year: int | None = typer.Option(None, help="Publication/preprint year"),
    topic: str = typer.Option(
        "graph_methods_no_fmri", help="Corpus topic/category label for indexing"
    ),
    source_url: str | None = typer.Option(
        None, help="Override HTML URL; otherwise arxiv.org/html then ar5iv are tried"
    ),
) -> None:
    """Fetch an open arXiv HTML paper into the local Markdown corpus layout."""
    setup_logging()
    cfg = load_config()
    result = fetch_arxiv_markdown(
        cfg,
        arxiv_id=arxiv_id,
        title=title,
        year=year,
        topic=topic,
        source_url=source_url,
    )
    typer.echo(json.dumps(result, indent=2))

# ---------------------------------------------------------------------------
# classify subcommand group
# ---------------------------------------------------------------------------

classify_app = typer.Typer(help="6-category paper classification (prepare/ingest/status).")
app.add_typer(classify_app, name="classify")


@classify_app.callback(invoke_without_command=True)
def classify_default(ctx: typer.Context) -> None:
    """Show classification status (default when no subcommand given)."""
    if ctx.invoked_subcommand is None:
        setup_logging()
        cfg = load_config()
        stats = collect_classification_stats(cfg.storage.data_dir)
        typer.echo(json.dumps(stats, indent=2))


@classify_app.command("prepare")
def classify_prepare(
    batch_size: int = typer.Option(50, help="Papers per batch file"),
    source: str = typer.Option("all", help="Source: all, seeds, or expansion"),
) -> None:
    """Prepare batch files for agent classification.

    Scans unclassified papers from seeds + expansion, writes batch JSON files
    to data/classify_batches/ for Claude Code agents to process.
    """
    setup_logging()
    cfg = load_config()
    result = prepare_classify_batches(cfg, batch_size=batch_size, source=source)
    typer.echo(json.dumps(result, indent=2))

    if result.get("batches", 0) > 0:
        typer.echo(
            f"\nNext: spawn Claude Code agents (model=haiku) to classify "
            f"{result['batches']} batch files in {result['batch_dir']}/"
        )
        typer.echo(
            "Then run: uv run fc classify ingest <results.jsonl>"
        )


@classify_app.command("ingest")
def classify_ingest(
    path: Path = typer.Argument(..., help="JSONL file or directory with classification results"),
) -> None:
    """Ingest classification results from a JSONL file or directory.

    If path is a file: reads it, validates, deduplicates, appends.
    If path is a directory: scans all *.jsonl files and ingests them all.
    """
    setup_logging()
    cfg = load_config()
    if path.is_dir():
        result = ingest_classification_dir(cfg.storage.data_dir, path)
    else:
        result = ingest_classification_file(cfg.storage.data_dir, path)
    typer.echo(json.dumps(result, indent=2))


@classify_app.command("extract-logs")
def classify_extract_logs(
    logs_dir: Path = typer.Argument(
        ..., help="Directory with agent .output log files"
    ),
) -> None:
    """Extract classifications from agent background output logs.

    Fallback when agents couldn't write result files directly.
    Scans agent .output files for JSONL classification lines,
    extracts them, and ingests into classifications_7cat.jsonl.
    """
    setup_logging()
    cfg = load_config()
    result = extract_from_agent_logs(cfg.storage.data_dir, logs_dir)
    typer.echo(json.dumps(result, indent=2))


@classify_app.command("status")
def classify_status() -> None:
    """Show classification progress and category distribution."""
    setup_logging()
    cfg = load_config()
    stats = collect_classification_stats(cfg.storage.data_dir)

    typer.echo(f"Classified: {stats['total']}  |  Pending: {stats['pending']}")
    typer.echo(f"  Core:      {stats['core']}")
    typer.echo(f"  Secondary: {stats['secondary']}")
    typer.echo(f"  Excluded:  {stats['excluded']}")
    typer.echo("")
    typer.echo("Category breakdown:")
    for cat_name in [
        "fmri_gnn", "fmri_graph_classical", "fmri_geometric_manifold",
        "fmri_no_graph", "graph_methods_no_fmri",
        "out_of_scope",
    ]:
        count = stats.get(cat_name, 0)
        typer.echo(f"  {cat_name}: {count}")
    typer.echo("")
    typer.echo(f"Batch files: {stats['classify_batches']} (classify_batches/) + "
               f"{stats['expand_classify_batches']} (expand_classify_batches/)")


# ---------------------------------------------------------------------------
# Other commands
# ---------------------------------------------------------------------------

@app.command()
def init() -> None:
    """Initialize directory structure and base files."""
    cfg = load_config()
    cfg.storage.data_dir.mkdir(parents=True, exist_ok=True)
    cfg.storage.graph_dir.mkdir(parents=True, exist_ok=True)
    cfg.storage.cache_dir.mkdir(parents=True, exist_ok=True)
    Path(cfg.storage.duckdb_path).parent.mkdir(parents=True, exist_ok=True)
    Path("notes").mkdir(parents=True, exist_ok=True)
    for fname in ["papers.jsonl", "citations.jsonl", "authors.jsonl", "venues.jsonl", "queries.jsonl", "runs.jsonl"]:
        (cfg.storage.data_dir / fname).touch(exist_ok=True)
    (cfg.storage.graph_dir / "nodes.csv").touch(exist_ok=True)
    (cfg.storage.graph_dir / "edges.csv").touch(exist_ok=True)
    (cfg.storage.graph_dir / "clusters.yaml").touch(exist_ok=True)

    seeds_file = cfg.storage.data_dir / "seeds.yaml"
    if not seeds_file.exists():
        default_seeds = {
            "seeds": [
                {"query": "Graph Neural Networks for functional connectivity fMRI", "tag": "gnn_fmri"},
                {"query": "geometric deep learning neuroimaging", "tag": "geom_dl"},
            ]
        }
        with seeds_file.open("w", encoding="utf-8") as f:
            yaml.dump(default_seeds, f, default_flow_style=False)

    env_example_file = Path(".env.example")
    if not env_example_file.exists():
        env_example_content = """# Optional API keys
SEMANTIC_SCHOLAR_API_KEY=
OPENALEX_EMAIL=
# Optional Neo4j
NEO4J_URI=
NEO4J_USER=
NEO4J_PASSWORD=
"""
        env_example_file.write_text(env_example_content, encoding="utf-8")


@app.command("ingest-seeds")
def ingest_seeds_cmd(
    graphdb: str = typer.Option("duckdb", help="duckdb or neo4j"),
) -> None:
    """Ingest seed queries from data/seeds.yaml."""
    setup_logging()
    cfg = load_config()
    use_neo4j = graphdb == "neo4j"
    count = ingest_seeds(cfg, use_neo4j=use_neo4j)
    typer.echo(f"Seeds ingested: {count}")
    if use_neo4j:
        typer.echo("Neo4j mirroring enabled")


@app.command()
def expand(
    direction: str = typer.Option("backward", help="backward or forward"),
    depth: int = typer.Option(None, help="Depth of expansion"),
    limit: int = typer.Option(None, help="Limit per paper"),
    global_limit: int = typer.Option(None, help="Global limit for edges added"),
    graphdb: str = typer.Option("duckdb", help="duckdb or neo4j"),
) -> None:
    """Expand citations."""
    setup_logging()
    cfg = load_config()
    depth = depth or cfg.expansion.default_depth
    limit = limit or cfg.expansion.default_limit_per_paper
    global_limit = global_limit or cfg.expansion.default_global_limit

    use_neo4j = graphdb == "neo4j"
    added = expand_citations(cfg, direction, depth, limit, global_limit, use_neo4j=use_neo4j)
    typer.echo(f"Edges added: {added}")
    if use_neo4j:
        typer.echo("Neo4j mirroring enabled")


@app.command()
def enrich() -> None:
    """Enrich authors/venues index."""
    setup_logging()
    cfg = load_config()
    result = enrich_metadata(cfg.storage.data_dir, cfg.storage.duckdb_path, cfg.provenance.confidence_default)
    typer.echo(json.dumps(result))


@app.command()
def abstracts(
    batch_size: int = typer.Option(0, help="Max abstracts per run (0 = all)"),
    extended: bool = typer.Option(False, help="Use extended sources (Europe PMC, DOI negotiation, title search)"),
) -> None:
    """Fetch abstracts for all papers."""
    setup_logging()
    cfg = load_config()
    (cfg.storage.data_dir / "abstracts.jsonl").touch(exist_ok=True)
    if extended:
        result = fetch_abstracts_extended(cfg)
    else:
        result = fetch_abstracts(cfg, batch_size=batch_size)
    typer.echo(json.dumps(result))


@app.command()
def export() -> None:
    """Export nodes/edges CSV + GraphML."""
    setup_logging()
    cfg = load_config()
    export_graph(cfg.storage.data_dir, cfg.storage.graph_dir)
    typer.echo("Exported graph (CSV + GraphML)")


@app.command()
def cluster() -> None:
    """Cluster graph."""
    setup_logging()
    cfg = load_config()
    cluster_graph(cfg.storage.data_dir, cfg.storage.graph_dir, Path("notes"))
    typer.echo("Clustered graph")


@app.command("backfill-metadata")
def backfill_metadata_cmd(
    dry_run: bool = typer.Option(False, help="Only scan and report counts, no changes"),
) -> None:
    """Backfill missing title/year/venue metadata from cache and APIs."""
    setup_logging()
    cfg = load_config()
    result = backfill_metadata(cfg, dry_run=dry_run)
    typer.echo(json.dumps(result, indent=2))


@app.command("recursive-expand")
def recursive_expand_cmd(
    max_depth: int = typer.Option(10, help="Maximum BFS depth"),
    batch_size: int = typer.Option(50, help="Classification batch size"),
) -> None:
    """Recursively expand citation network with 6-category AI classification."""
    setup_logging()
    cfg = load_config()
    stats = recursive_expand(cfg, max_depth=max_depth, batch_size=batch_size)
    typer.echo(json.dumps(stats, indent=2))


@app.command("consolidate-seeds")
def consolidate_seeds_cmd() -> None:
    """Consolidate all fMRI×GNN seeds into confirmed_seeds.jsonl."""
    setup_logging()
    cfg = load_config()
    stats = consolidate_seeds(cfg)
    typer.echo(json.dumps(stats, indent=2))


@app.command("build-network")
def build_network_cmd() -> None:
    """Build citation network from classified papers and edges."""
    setup_logging()
    cfg = load_config()
    stats = build_network(cfg)
    typer.echo(json.dumps(stats, indent=2))


@app.command()
def audit(
    prepare: bool = typer.Option(False, help="Prepare Sonnet audit batches"),
    compute: bool = typer.Option(False, help="Compute agreement from Sonnet results"),
    sample_size: int = typer.Option(100, help="Number of papers for Sonnet audit"),
    batch_size: int = typer.Option(50, help="Papers per audit batch"),
) -> None:
    """Quality audit: EEG scan, category stats, network checks, Sonnet agreement.

    Use --prepare to write audit batch files for Sonnet agents.
    Use --compute after agents classify to compute agreement.
    Without flags, runs EEG scan + category stats + network checks.
    """
    setup_logging()
    cfg = load_config()
    data_dir = cfg.storage.data_dir

    from .models import Category, CORE_CATEGORIES, SECONDARY_CATEGORIES
    from .storage_jsonl import read_jsonl

    if prepare:
        result = prepare_audit_batches(data_dir, sample_size, batch_size)
        typer.echo(json.dumps(result, indent=2))
        if result.get("batches", 0) > 0:
            typer.echo(
                f"\nNext: spawn Claude Code agents (model=sonnet) to classify "
                f"{result['batches']} batches in {result['batch_dir']}/"
            )
            typer.echo(
                "Results should be appended to data/audit_classifications.jsonl"
            )
        return

    if compute:
        result = compute_audit_agreement(data_dir)
        typer.echo(json.dumps(result, indent=2))
        return

    # Default: run all non-agent checks
    issues: list[str] = []

    import re
    eeg_pat = re.compile(
        r"\b(eeg|electroencephalogra\w+|meg\b|magnetoencephalogra\w+)", re.IGNORECASE,
    )
    fmri_pat = re.compile(
        r"\b(fmri|functional\s+mri|functional\s+magnetic|"
        r"functional\s+connectiv\w+|bold|resting[- ]state)", re.IGNORECASE,
    )

    core_cats = {c.value for c in CORE_CATEGORIES}
    secondary_cats = {c.value for c in SECONDARY_CATEGORIES}

    eeg_contaminated: list[str] = []
    cat_dist: dict[str, int] = {}
    total_classified = 0

    meta_index: dict[str, dict] = {}
    for rec in read_jsonl(data_dir / "confirmed_seeds.jsonl"):
        meta_index[rec.get("paper_id", "")] = rec
    for rec in read_jsonl(data_dir / "discovered_papers.jsonl"):
        pid = rec.get("paper_id")
        if pid and pid not in meta_index:
            meta_index[pid] = rec

    for rec in read_jsonl(data_dir / "classifications_7cat.jsonl"):
        pid = rec.get("paper_id", "")
        cat = rec.get("category", "out_of_scope")
        cat_dist[cat] = cat_dist.get(cat, 0) + 1
        total_classified += 1

        if cat in core_cats or cat in secondary_cats:
            meta = meta_index.get(pid, {})
            text = f"{meta.get('title', '')} {meta.get('abstract', '')}"
            if eeg_pat.search(text) and not fmri_pat.search(text):
                eeg_contaminated.append(pid)

    if eeg_contaminated:
        issues.append(f"EEG-only papers in core/secondary: {len(eeg_contaminated)}")
        for pid in eeg_contaminated[:5]:
            title = meta_index.get(pid, {}).get("title", "?")
            issues.append(f"  - {pid}: {title[:80]}")

    if total_classified:
        typer.echo("\nCategory distribution:")
        for cat, count in sorted(cat_dist.items(), key=lambda x: -x[1]):
            pct = count / total_classified * 100
            typer.echo(f"  {cat}: {count} ({pct:.1f}%)")
    else:
        typer.echo("\nNo 6-cat classifications found yet.")

    network_stats_path = data_dir / "network" / "network_stats.json"
    if network_stats_path.exists():
        ns = json.loads(network_stats_path.read_text(encoding="utf-8"))
        typer.echo(f"\nNetwork: {ns['nodes']['total']} nodes, {ns['edges']['total']} edges")
        typer.echo(f"  Components: {ns['components']['total']} (largest: {ns['components']['largest']})")
        typer.echo(f"  Isolated: {ns['components']['isolated']}")
        if ns["components"]["isolated"] > ns["nodes"]["total"] * 0.1:
            issues.append(f"High isolated node ratio: {ns['components']['isolated']}/{ns['nodes']['total']}")

    citations_path = data_dir / "citations.jsonl"
    if citations_path.exists() and citations_path.stat().st_size > 0:
        edge_count = 0
        edge_set: set[tuple[str, str]] = set()
        dup_edges = 0
        for rec in read_jsonl(citations_path):
            edge_count += 1
            key = (rec.get("from", ""), rec.get("to", ""))
            if key in edge_set:
                dup_edges += 1
            edge_set.add(key)
        typer.echo(f"\nEdges: {edge_count} total, {len(edge_set)} unique")
        if dup_edges:
            issues.append(f"Duplicate citation edges: {dup_edges}/{edge_count}")

    audit_path = data_dir / "audit_sonnet_7cat.json"
    if audit_path.exists():
        ar = json.loads(audit_path.read_text(encoding="utf-8"))
        typer.echo(f"\nSonnet audit: {ar['agreement']}/{ar['sample_size']} agree "
                   f"({ar['agreement_rate'] * 100:.1f}%)")
    else:
        typer.echo("\nNo Sonnet audit results yet. Run: fc audit --prepare")

    if issues:
        typer.echo("\nAudit issues:")
        for issue in issues:
            typer.echo(f"  - {issue}")
    else:
        typer.echo("\nAudit: all checks passed")


@app.command()
def qc() -> None:
    """Quality checks."""
    setup_logging()
    cfg = load_config()
    data_dir = cfg.storage.data_dir

    issues = []

    dups = find_duplicate_paper_ids(data_dir)
    if dups:
        issues.append(f"duplicate paper_id: {len(dups)}")

    paper_index: dict[str, dict] = {}
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

    new_cls_path = data_dir / "classifications_7cat.jsonl"
    if new_cls_path.exists():
        cat_counts: dict[str, int] = {}
        for line in new_cls_path.open("r", encoding="utf-8"):
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            cat = rec.get("category", "unknown")
            cat_counts[cat] = cat_counts.get(cat, 0) + 1
        if cat_counts:
            typer.echo("6-cat distribution:")
            for cat, count in sorted(cat_counts.items(), key=lambda x: -x[1]):
                typer.echo(f"  {cat}: {count}")

    if issues:
        typer.echo("QC issues:")
        for i in issues:
            typer.echo(f"- {i}")
    else:
        typer.echo("QC OK")
