from __future__ import annotations

from pathlib import Path
from collections import Counter
import yaml
import networkx as nx

from ..storage_jsonl import read_jsonl


def cluster_graph(data_dir: Path, graph_dir: Path, notes_dir: Path) -> None:
    papers = {p["paper_id"]: p for p in read_jsonl(data_dir / "papers.jsonl")}
    edges = list(read_jsonl(data_dir / "citations.jsonl"))

    g = nx.Graph()
    for pid in papers.keys():
        g.add_node(pid)
    for e in edges:
        g.add_edge(e.get("from"), e.get("to"))

    clusters = []
    summaries = []

    for idx, comp in enumerate(nx.connected_components(g)):
        sub = g.subgraph(comp)
        try:
            communities = list(nx.algorithms.community.greedy_modularity_communities(sub))
        except Exception:
            communities = [set(sub.nodes())]

        for j, comm in enumerate(communities):
            titles = []
            venues = []
            for pid in comm:
                p = papers.get(pid, {})
                if p.get("title"):
                    titles.append(p["title"])
                if p.get("venue"):
                    venues.append(p["venue"])
            top_venues = [v for v, _ in Counter(venues).most_common(3)]
            keywords = []
            for t in titles:
                for token in t.lower().split():
                    if len(token) > 4:
                        keywords.append(token)
            top_keywords = [k for k, _ in Counter(keywords).most_common(5)]

            clusters.append({
                "cluster_id": f"c{idx}_{j}",
                "size": len(comm),
                "nodes": sorted(list(comm)),
                "top_venues": top_venues,
                "top_keywords": top_keywords,
            })

            summaries.append(
                f"Cluster c{idx}_{j}: size={len(comm)} | venues={', '.join(top_venues)} | keywords={', '.join(top_keywords)}"
            )

    graph_dir.mkdir(parents=True, exist_ok=True)
    notes_dir.mkdir(parents=True, exist_ok=True)

    (graph_dir / "clusters.yaml").write_text(yaml.safe_dump({"clusters": clusters}), encoding="utf-8")
    (notes_dir / "cluster-summaries.md").write_text("\n".join(summaries), encoding="utf-8")
