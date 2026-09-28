from __future__ import annotations

from pathlib import Path

from ..storage_jsonl import JsonlStore, now_iso, read_jsonl
from ..graphdb.duckdb_store import DuckDBStore


def enrich_metadata(data_dir: Path, duckdb_path: Path, confidence_default: float = 0.7) -> dict[str, int]:
    store = JsonlStore(data_dir)
    db = DuckDBStore(duckdb_path)

    authors_added = 0
    venues_added = 0

    for p in read_jsonl(data_dir / "papers.jsonl"):
        pid = p.get("paper_id")
        paper_prov = p.get("provenance", {})
        source = paper_prov.get("source") or p.get("source")
        confidence = paper_prov.get("confidence", confidence_default)

        for a in p.get("authors") or []:
            name = (a.get("name") or "").strip()
            if not name:
                continue
            key = f"author:{name}:{pid}"
            if db.is_processed(key):
                continue
            author_record = {
                "paper_id": pid,
                "name": name,
                "provenance": {
                    "source": source,
                    "fetched_at": now_iso(),
                    "confidence": confidence,
                },
            }
            store.append("authors.jsonl", author_record)
            db.mark_processed(key)
            authors_added += 1

        venue = (p.get("venue") or "").strip()
        if venue:
            key = f"venue:{venue}:{pid}"
            if not db.is_processed(key):
                venue_record = {
                    "paper_id": pid,
                    "venue": venue,
                    "provenance": {
                        "source": source,
                        "fetched_at": now_iso(),
                        "confidence": confidence,
                    },
                }
                store.append("venues.jsonl", venue_record)
                db.mark_processed(key)
                venues_added += 1

    store.log_run("enrich_metadata", {"authors_added": authors_added, "venues_added": venues_added})
    db.close()
    return {"authors_added": authors_added, "venues_added": venues_added}
