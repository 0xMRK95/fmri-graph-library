"""Backfill missing title / year / venue on paper records.

Phases (ordered by cost):
  0. Mine existing OpenAlex cache files  (free)
  1. Mine existing S2 cache files        (free)
  2. S2 batch API                        (network)
  3. OpenAlex batch API                  (network)
  4. Crossref individual DOI lookup      (network)
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from ..config import AppConfig
from ..storage_jsonl import JsonlStore, now_iso, read_jsonl
from ..graphdb.duckdb_store import DuckDBStore

log = logging.getLogger("field-cartography")


def _is_missing(paper: dict[str, Any]) -> bool:
    t = paper.get("title")
    y = paper.get("year")
    v = paper.get("venue")
    return (
        (not t or (isinstance(t, str) and not t.strip()))
        or (not y)
        or (not v or (isinstance(v, str) and not v.strip()))
    )


def _merge_metadata(
    original: dict[str, Any],
    new_fields: dict[str, Any],
    source: str,
    confidence: float,
) -> dict[str, Any] | None:
    """Return a merged copy if any missing field was filled, else None."""
    merged = dict(original)
    changed = False
    for field in ("title", "year", "venue"):
        old_val = merged.get(field)
        new_val = new_fields.get(field)
        old_empty = not old_val or (isinstance(old_val, str) and not old_val.strip())
        new_present = new_val and (not isinstance(new_val, str) or new_val.strip())
        if old_empty and new_present:
            merged[field] = new_val
            changed = True
    if not changed:
        return None
    merged["provenance"] = {
        "source": source,
        "fetched_at": now_iso(),
        "confidence": confidence,
        "backfill": True,
    }
    return merged


# ---------------------------------------------------------------------------
# Phase 0 – mine OpenAlex cache
# ---------------------------------------------------------------------------

def _phase_mine_oa_cache(
    papers: list[dict[str, Any]],
    found: set[str],
    cache_dir: Path,
    db: DuckDBStore,
    store: JsonlStore,
    confidence: float,
) -> int:
    doi_map: dict[str, dict] = {}
    oa_map: dict[str, dict] = {}
    for p in papers:
        pid = p["paper_id"]
        if pid in found or db.is_processed(f"backfill:{pid}"):
            found.add(pid)
            continue
        if p.get("doi"):
            doi_map[p["doi"].lower()] = p
        if p.get("openalex_id"):
            oa_map[p["openalex_id"]] = p

    count = 0
    for cf in cache_dir.glob("oa_*.json"):
        try:
            data = json.loads(cf.read_text(encoding="utf-8"))
        except Exception:
            continue

        pl = (data.get("primary_location") or {}).get("source") or {}
        venue = pl.get("display_name")
        if not venue:
            # fallback to host_venue
            venue = ((data.get("host_venue") or {}).get("display_name"))
        year = data.get("publication_year")
        if not venue and not year:
            continue

        oa_id = data.get("id", "")
        rdoi = (data.get("doi") or "").replace("https://doi.org/", "").lower()
        paper = oa_map.get(oa_id) or (doi_map.get(rdoi) if rdoi else None)
        if not paper or paper["paper_id"] in found:
            continue

        new_fields: dict[str, Any] = {}
        if venue:
            new_fields["venue"] = venue
        if year:
            new_fields["year"] = year

        merged = _merge_metadata(paper, new_fields, "openalex_cache", confidence)
        if merged:
            store.append("papers.jsonl", merged)
            db.mark_processed(f"backfill:{paper['paper_id']}")
            found.add(paper["paper_id"])
            count += 1

    return count


# ---------------------------------------------------------------------------
# Phase 1 – mine S2 cache
# ---------------------------------------------------------------------------

def _phase_mine_s2_cache(
    papers: list[dict[str, Any]],
    found: set[str],
    cache_dir: Path,
    db: DuckDBStore,
    store: JsonlStore,
    confidence: float,
) -> int:
    corpus_map: dict[str, dict] = {}
    doi_map: dict[str, dict] = {}
    for p in papers:
        pid = p["paper_id"]
        if pid in found:
            continue
        if p.get("corpusId"):
            corpus_map[str(p["corpusId"])] = p
        if p.get("doi"):
            doi_map[p["doi"].lower()] = p

    count = 0
    for cf in cache_dir.glob("s2_*.json"):
        try:
            data = json.loads(cf.read_text(encoding="utf-8"))
        except Exception:
            continue

        items: list[dict] = []
        if "data" in data and isinstance(data["data"], list):
            for item in data["data"]:
                if not isinstance(item, dict):
                    continue
                for key in ("citedPaper", "citingPaper"):
                    nested = item.get(key)
                    if nested and isinstance(nested, dict):
                        items.append(nested)
                if "citedPaper" not in item and "citingPaper" not in item:
                    items.append(item)
        elif isinstance(data, dict):
            items.append(data)

        for item in items:
            venue = item.get("venue")
            if not venue:
                continue
            year = item.get("year")
            corpus = str(item.get("corpusId", ""))
            ext = item.get("externalIds") or {}
            doi = (ext.get("DOI") or "").lower()

            paper = None
            if corpus and corpus in corpus_map:
                paper = corpus_map[corpus]
            elif doi and doi in doi_map:
                paper = doi_map[doi]
            if not paper or paper["paper_id"] in found:
                continue

            new_fields: dict[str, Any] = {"venue": venue}
            if year:
                new_fields["year"] = year

            merged = _merge_metadata(paper, new_fields, "s2_cache", confidence)
            if merged:
                store.append("papers.jsonl", merged)
                db.mark_processed(f"backfill:{paper['paper_id']}")
                found.add(paper["paper_id"])
                count += 1

    return count


# ---------------------------------------------------------------------------
# Phase 2 – S2 batch API
# ---------------------------------------------------------------------------

def _s2_id(paper: dict[str, Any]) -> str | None:
    if paper.get("corpusId"):
        return f"CorpusId:{paper['corpusId']}"
    pid = paper.get("paper_id", "")
    if pid.startswith("s2:"):
        return f"CorpusId:{pid.split(':', 1)[1]}"
    if paper.get("doi"):
        return paper["doi"]
    if pid.startswith("doi:"):
        return pid.split(":", 1)[1]
    return None


def _phase_s2_batch(
    papers: list[dict[str, Any]],
    found: set[str],
    db: DuckDBStore,
    store: JsonlStore,
    cfg: AppConfig,
    confidence: float,
) -> int:
    from ..ingest.semantic_scholar import SemanticScholarClient

    remaining = []
    for p in papers:
        pid = p["paper_id"]
        if pid in found:
            continue
        sid = _s2_id(p)
        if sid:
            remaining.append((p, sid))

    if not remaining:
        return 0

    s2 = SemanticScholarClient(
        cfg.semantic_scholar.base_url,
        cfg.semantic_scholar.rate_limit_per_min,
        cfg.storage.cache_dir,
    )

    count = 0
    BATCH = 500
    for i in range(0, len(remaining), BATCH):
        batch = remaining[i : i + BATCH]
        ids = [sid for _, sid in batch]
        id_to_paper = {}
        for p, sid in batch:
            id_to_paper[sid] = p

        data = s2._post(
            "/paper/batch",
            {"ids": ids},
            params={"fields": "venue,year,externalIds,corpusId"},
        )
        if not data or not isinstance(data, list):
            continue

        for j, result in enumerate(data):
            if not result:
                # mark as processed even if not found
                if j < len(batch):
                    db.mark_processed(f"backfill:{batch[j][0]['paper_id']}")
                continue

            # match back
            corpus = str(result.get("corpusId", ""))
            ext = result.get("externalIds") or {}
            doi = (ext.get("DOI") or "").lower()

            paper = None
            s2_key = f"CorpusId:{corpus}" if corpus else None
            if s2_key and s2_key in id_to_paper:
                paper = id_to_paper[s2_key]
            elif doi:
                for sid, p in id_to_paper.items():
                    if (p.get("doi") or "").lower() == doi:
                        paper = p
                        break
            # fallback: positional match
            if not paper and j < len(batch):
                paper = batch[j][0]

            if not paper or paper["paper_id"] in found:
                continue

            new_fields: dict[str, Any] = {}
            if result.get("venue"):
                new_fields["venue"] = result["venue"]
            if result.get("year"):
                new_fields["year"] = result["year"]

            if new_fields:
                merged = _merge_metadata(paper, new_fields, "semantic_scholar", confidence)
                if merged:
                    store.append("papers.jsonl", merged)
                    found.add(paper["paper_id"])
                    count += 1

            db.mark_processed(f"backfill:{paper['paper_id']}")

        bn = i // BATCH + 1
        total = (len(remaining) + BATCH - 1) // BATCH
        log.info(f"  S2 batch {bn}/{total}: {count} filled so far")

    return count


# ---------------------------------------------------------------------------
# Phase 3 – OpenAlex batch API
# ---------------------------------------------------------------------------

def _phase_oa_batch(
    papers: list[dict[str, Any]],
    found: set[str],
    db: DuckDBStore,
    store: JsonlStore,
    cfg: AppConfig,
    confidence: float,
) -> int:
    from ..ingest.openalex import OpenAlexClient

    remaining = [p for p in papers if p["paper_id"] not in found and p.get("doi")]
    if not remaining:
        return 0

    oa = OpenAlexClient(
        cfg.openalex.base_url,
        cfg.openalex.rate_limit_per_min,
        cfg.storage.cache_dir,
    )

    count = 0
    BATCH = 50
    for i in range(0, len(remaining), BATCH):
        batch = remaining[i : i + BATCH]
        doi_map = {p["doi"].lower(): p for p in batch}
        doi_filter = "|".join(p["doi"] for p in batch)

        data = oa._get("/works", {
            "filter": f"doi:{doi_filter}",
            "per-page": str(BATCH),
            "select": "id,doi,primary_location,publication_year",
        })

        for r in data.get("results", []):
            rdoi = (r.get("doi") or "").replace("https://doi.org/", "").lower()
            if rdoi not in doi_map:
                continue
            paper = doi_map[rdoi]
            pid = paper["paper_id"]
            if pid in found:
                continue

            pl = (r.get("primary_location") or {}).get("source") or {}
            venue = pl.get("display_name")
            year = r.get("publication_year")

            new_fields: dict[str, Any] = {}
            if venue:
                new_fields["venue"] = venue
            if year:
                new_fields["year"] = year

            if new_fields:
                merged = _merge_metadata(paper, new_fields, "openalex", confidence)
                if merged:
                    store.append("papers.jsonl", merged)
                    found.add(pid)
                    count += 1

            db.mark_processed(f"backfill:{pid}")

        bn = i // BATCH + 1
        total = (len(remaining) + BATCH - 1) // BATCH
        if bn % 10 == 0 or bn == total:
            log.info(f"  OA batch {bn}/{total}: {count} filled so far")

    return count


# ---------------------------------------------------------------------------
# Phase 4 – Crossref individual DOI
# ---------------------------------------------------------------------------

def _phase_crossref(
    papers: list[dict[str, Any]],
    found: set[str],
    db: DuckDBStore,
    store: JsonlStore,
    cfg: AppConfig,
    confidence: float,
) -> int:
    from ..ingest.crossref import CrossrefClient

    remaining = [
        p for p in papers
        if p["paper_id"] not in found
        and p.get("doi")
        and not db.is_processed(f"backfill:{p['paper_id']}")
    ]
    if not remaining:
        return 0

    cr = CrossrefClient(
        cfg.crossref.base_url,
        cfg.crossref.rate_limit_per_min,
        cfg.storage.cache_dir,
    )

    count = 0
    for idx, p in enumerate(remaining):
        pid = p["paper_id"]
        doi = p["doi"]

        try:
            data = cr.resolve_doi(doi)
        except Exception:
            db.mark_processed(f"backfill:{pid}")
            continue

        msg = data.get("message") or data
        new_fields: dict[str, Any] = {}

        ct = msg.get("container-title")
        if ct and isinstance(ct, list) and ct:
            new_fields["venue"] = ct[0]
        elif ct and isinstance(ct, str):
            new_fields["venue"] = ct

        pub_date = msg.get("published-print") or msg.get("published-online") or {}
        date_parts = pub_date.get("date-parts", [[]])
        if date_parts and date_parts[0]:
            new_fields["year"] = date_parts[0][0]

        title_list = msg.get("title")
        if title_list and isinstance(title_list, list) and title_list:
            new_fields["title"] = title_list[0]

        if new_fields:
            merged = _merge_metadata(p, new_fields, "crossref", confidence)
            if merged:
                store.append("papers.jsonl", merged)
                found.add(pid)
                count += 1

        db.mark_processed(f"backfill:{pid}")

        if (idx + 1) % 50 == 0:
            log.info(f"  Crossref {idx + 1}/{len(remaining)}: {count} filled so far")

    return count


# ---------------------------------------------------------------------------
# orchestrator
# ---------------------------------------------------------------------------

def backfill_metadata(cfg: AppConfig, dry_run: bool = False) -> dict[str, Any]:
    """Backfill missing title/year/venue from cache + APIs."""
    data_dir = cfg.storage.data_dir
    store = JsonlStore(data_dir)
    db = DuckDBStore(cfg.storage.duckdb_path)

    # build last-record-wins index
    paper_index: dict[str, dict[str, Any]] = {}
    for p in read_jsonl(data_dir / "papers.jsonl"):
        pid = p.get("paper_id")
        if pid:
            paper_index[pid] = p

    total = len(paper_index)
    missing = [p for p in paper_index.values() if _is_missing(p)]
    n_missing = len(missing)

    log.info(f"Total papers: {total}, missing metadata: {n_missing}")

    if dry_run:
        has_doi = sum(1 for p in missing if p.get("doi"))
        has_corpus = sum(1 for p in missing if p.get("corpusId"))
        has_oa = sum(1 for p in missing if p.get("openalex_id"))
        hash_only = sum(
            1 for p in missing
            if p.get("paper_id", "").startswith("hash:")
            and not p.get("doi") and not p.get("corpusId") and not p.get("openalex_id")
        )
        db.close()
        return {
            "total": total,
            "missing": n_missing,
            "has_doi": has_doi,
            "has_corpusId": has_corpus,
            "has_openalex_id": has_oa,
            "hash_only_unfetchable": hash_only,
        }

    found: set[str] = set()
    conf = cfg.provenance.confidence_default

    log.info("Phase 0: Mining OpenAlex cache...")
    c0 = _phase_mine_oa_cache(missing, found, cfg.storage.cache_dir, db, store, conf)
    log.info(f"  → {c0} papers filled from OA cache")

    log.info("Phase 1: Mining S2 cache...")
    c1 = _phase_mine_s2_cache(missing, found, cfg.storage.cache_dir, db, store, conf)
    log.info(f"  → {c1} papers filled from S2 cache")

    log.info("Phase 2: S2 batch API...")
    c2 = _phase_s2_batch(missing, found, db, store, cfg, conf)
    log.info(f"  → {c2} papers filled from S2 API")

    log.info("Phase 3: OpenAlex batch API...")
    c3 = _phase_oa_batch(missing, found, db, store, cfg, conf)
    log.info(f"  → {c3} papers filled from OA API")

    log.info("Phase 4: Crossref DOI lookup...")
    c4 = _phase_crossref(missing, found, db, store, cfg, conf)
    log.info(f"  → {c4} papers filled from Crossref")

    total_filled = c0 + c1 + c2 + c3 + c4
    still_missing = n_missing - total_filled

    result = {
        "total_papers": total,
        "missing_before": n_missing,
        "filled_oa_cache": c0,
        "filled_s2_cache": c1,
        "filled_s2_api": c2,
        "filled_oa_api": c3,
        "filled_crossref": c4,
        "total_filled": total_filled,
        "still_missing": still_missing,
    }

    store.log_run("backfill_metadata", result)
    db.close()

    log.info(f"\n=== Backfill complete ===")
    log.info(f"  Total filled: {total_filled}/{n_missing}")
    log.info(f"  Still missing: {still_missing}")

    return result
