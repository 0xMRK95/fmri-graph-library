from __future__ import annotations

from pathlib import Path
import yaml

from ..config import AppConfig
from ..storage_jsonl import JsonlStore, now_iso
from ..graphdb.duckdb_store import DuckDBStore
from ..ingest.semantic_scholar import SemanticScholarClient
from ..ingest.openalex import OpenAlexClient
from ..ingest.resolver import standardize_s2, standardize_openalex


def ingest_seeds(cfg: AppConfig, use_neo4j: bool = False) -> int:
    data_dir = cfg.storage.data_dir
    seeds_path = data_dir / "seeds.yaml"
    if not seeds_path.exists():
        raise FileNotFoundError(str(seeds_path))

    seeds = yaml.safe_load(seeds_path.read_text(encoding="utf-8")).get("seeds", [])
    store = JsonlStore(data_dir)
    db = DuckDBStore(cfg.storage.duckdb_path)

    s2 = SemanticScholarClient(cfg.semantic_scholar.base_url, cfg.semantic_scholar.rate_limit_per_min, cfg.storage.cache_dir)
    oa = OpenAlexClient(cfg.openalex.base_url, cfg.openalex.rate_limit_per_min, cfg.storage.cache_dir)

    neo4j_client = None
    if use_neo4j:
        try:
            from ..graphdb.neo4j_client import Neo4jClient
            neo4j_client = Neo4jClient()
        except RuntimeError:
            pass  # Neo4j not configured, continue without it

    count = 0
    for seed in seeds:
        query = seed.get("query")
        tag = seed.get("tag")
        if not query:
            continue

        store.log_query("semantic_scholar", query, {"seed_tag": tag})
        results = s2.search(query, limit=1)
        paper = None
        if results:
            paper = standardize_s2(results[0])
        else:
            store.log_query("openalex", query, {"seed_tag": tag})
            oa_results = oa.search(query, limit=1)
            if oa_results:
                paper = standardize_openalex(oa_results[0])

        if not paper:
            continue

        paper["seed_tag"] = tag
        paper["provenance"] = {"source": paper.get("source"), "fetched_at": now_iso(), "confidence": cfg.provenance.confidence_default}

        if db.paper_exists(paper["paper_id"]):
            continue

        store.append("papers.jsonl", paper)
        db.add_paper(
            paper["paper_id"],
            paper.get("doi"),
            paper.get("corpusId"),
            paper.get("openalex_id"),
            paper.get("title"),
            paper.get("year"),
        )
        if neo4j_client:
            neo4j_client.upsert_paper(paper["paper_id"], paper.get("title"), paper.get("year"))
        count += 1

    store.log_run("ingest_seeds", {"count": count, "use_neo4j": use_neo4j})
    if neo4j_client:
        neo4j_client.close()
    db.close()
    return count
