from __future__ import annotations

from pathlib import Path
import json
from collections import deque
from typing import Any

from ..config import AppConfig
from ..storage_jsonl import JsonlStore, now_iso, read_jsonl
from ..graphdb.duckdb_store import DuckDBStore
from ..ingest.semantic_scholar import SemanticScholarClient
from ..ingest.openalex import OpenAlexClient
from ..ingest.resolver import standardize_s2, standardize_openalex


def _s2_id(record: dict[str, Any]) -> str | None:
    if record.get("corpusId"):
        return str(record["corpusId"])
    if record.get("doi"):
        return record["doi"]
    if record.get("paper_id", "").startswith("s2:"):
        return record["paper_id"].split(":", 1)[1]
    if record.get("paper_id", "").startswith("doi:"):
        return record["paper_id"].split(":", 1)[1]
    return None


def _openalex_id(record: dict[str, Any]) -> str | None:
    if record.get("openalex_id"):
        return record["openalex_id"]
    if record.get("paper_id", "").startswith("oa:"):
        return record["paper_id"].split(":", 1)[1]
    return None


def expand_citations(cfg: AppConfig, direction: str, depth: int, limit_per_paper: int, global_limit: int, use_neo4j: bool = False) -> int:
    data_dir = cfg.storage.data_dir
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

    paper_index: dict[str, dict[str, Any]] = {}
    for p in read_jsonl(data_dir / "papers.jsonl"):
        paper_index[p.get("paper_id")] = p

    frontier = deque([(pid, 0) for pid in paper_index.keys()])
    visited = set(paper_index.keys())
    added = 0

    while frontier and added < global_limit:
        paper_id, d = frontier.popleft()
        if d >= depth:
            continue
        if db.is_processed(f"{direction}:{paper_id}:{d}"):
            continue

        record = paper_index.get(paper_id)
        if not record:
            continue

        neighbors = []
        source_used = "semantic_scholar"

        # Try Semantic Scholar first
        s2_paper_id = _s2_id(record)
        if s2_paper_id:
            if direction == "backward":
                neighbors = s2.get_references(s2_paper_id, limit=limit_per_paper)
            else:
                neighbors = s2.get_citations(s2_paper_id, limit=limit_per_paper)

        # Fallback to OpenAlex if S2 returns nothing
        if not neighbors:
            oa_id = _openalex_id(record)
            doi = record.get("doi")
            work = None

            if oa_id:
                work = oa.get_work(oa_id.split("/")[-1] if "/" in oa_id else oa_id)
            elif doi:
                work = oa.get_work(f"https://doi.org/{doi}")

            if work:
                if direction == "backward":
                    ref_ids = work.get("referenced_works") or []
                    for ref_id in ref_ids[:limit_per_paper]:
                        if ref_id:
                            ref_work = oa.get_work(ref_id.split("/")[-1] if "/" in str(ref_id) else str(ref_id))
                            if ref_work:
                                neighbors.append(ref_work)
                    source_used = "openalex"
                else:
                    cited_url = work.get("cited_by_api_url")
                    if cited_url:
                        path = cited_url.replace(oa.base_url, "")
                        cited_data = oa._get(path, {"per-page": limit_per_paper})
                        for ref_work in cited_data.get("results", []):
                            if ref_work:
                                neighbors.append(ref_work)
                        source_used = "openalex"

        if not neighbors:
            db.mark_processed(f"{direction}:{paper_id}:{d}")
            continue

        for n in neighbors:
            if not n:
                continue

            if source_used == "semantic_scholar":
                paper = standardize_s2(n)
            else:
                paper = standardize_openalex(n)

            paper["provenance"] = {"source": source_used, "fetched_at": now_iso(), "confidence": cfg.provenance.confidence_default}

            if not db.paper_exists(paper["paper_id"]):
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

            edge = {
                "from": paper_id if direction == "backward" else paper["paper_id"],
                "to": paper["paper_id"] if direction == "backward" else paper_id,
                "relation": direction,
                "source": source_used,
                "fetched_at": now_iso(),
            }
            if not db.citation_exists(edge["from"], edge["to"]):
                store.append("citations.jsonl", edge)
                db.add_citation(edge["from"], edge["to"])
                if neo4j_client:
                    neo4j_client.upsert_citation(edge["from"], edge["to"])
                added += 1

            if paper["paper_id"] not in visited:
                visited.add(paper["paper_id"])
                paper_index[paper["paper_id"]] = paper
                frontier.append((paper["paper_id"], d + 1))

            if added >= global_limit:
                break

        db.mark_processed(f"{direction}:{paper_id}:{d}")

    store.log_run("expand_citations", {"direction": direction, "depth": depth, "limit_per_paper": limit_per_paper, "global_limit": global_limit, "edges_added": added, "use_neo4j": use_neo4j})
    if neo4j_client:
        neo4j_client.close()
    db.close()
    return added
