from __future__ import annotations

import json
import re
import time
import unicodedata
from pathlib import Path
from typing import Any

import httpx

from ..config import AppConfig
from ..storage_jsonl import JsonlStore, now_iso, read_jsonl
from ..graphdb.duckdb_store import DuckDBStore
from ..ingest.semantic_scholar import SemanticScholarClient
from ..ingest.openalex import OpenAlexClient


def _s2_lookup_id(record: dict[str, Any]) -> str | None:
    if record.get("corpusId"):
        return str(record["corpusId"])
    if record.get("doi"):
        return record["doi"]
    if record.get("paper_id", "").startswith("s2:"):
        return record["paper_id"].split(":", 1)[1]
    if record.get("paper_id", "").startswith("doi:"):
        return record["paper_id"].split(":", 1)[1]
    return None


def _oa_lookup_id(record: dict[str, Any]) -> str | None:
    if record.get("openalex_id"):
        oa_id = record["openalex_id"]
        return oa_id.split("/")[-1] if "/" in oa_id else oa_id
    if record.get("doi"):
        return f"https://doi.org/{record['doi']}"
    return None


def _reconstruct_oa_abstract(inverted_index: dict[str, list[int]] | None) -> str | None:
    if not inverted_index or not isinstance(inverted_index, dict):
        return None
    word_positions = []
    for word, positions in inverted_index.items():
        for pos in positions:
            word_positions.append((pos, word))
    if not word_positions:
        return None
    word_positions.sort()
    return " ".join(w for _, w in word_positions)


def _mine_cached_abstracts(cache_dir: Path, papers: list[dict]) -> dict[str, tuple[str, str]]:
    """Extract abstracts from already-cached API responses without new API calls."""
    doi_to_pid = {}
    corpus_to_pid = {}
    oa_to_pid = {}
    for p in papers:
        pid = p.get("paper_id")
        if p.get("doi"):
            doi_to_pid[p["doi"].lower()] = pid
        if p.get("corpusId"):
            corpus_to_pid[str(p["corpusId"])] = pid
        if p.get("openalex_id"):
            oa_to_pid[p["openalex_id"]] = pid

    found: dict[str, tuple[str, str]] = {}

    for cache_file in cache_dir.glob("*.json"):
        try:
            data = json.loads(cache_file.read_text())
        except Exception:
            continue

        # OpenAlex cached works have abstract_inverted_index
        if data.get("abstract_inverted_index"):
            oa_id = data.get("id", "")
            pid = oa_to_pid.get(oa_id)
            doi = (data.get("doi") or "").replace("https://doi.org/", "").lower()
            if not pid and doi:
                pid = doi_to_pid.get(doi)
            if pid and pid not in found:
                text = _reconstruct_oa_abstract(data["abstract_inverted_index"])
                if text:
                    found[pid] = (text, "openalex")

        # S2 cached responses may have abstract field
        if data.get("abstract") and isinstance(data.get("abstract"), str):
            pid = None
            ext = data.get("externalIds") or {}
            doi = (ext.get("DOI") or "").lower()
            corpus = str(data.get("corpusId", ""))
            if doi and doi in doi_to_pid:
                pid = doi_to_pid[doi]
            elif corpus in corpus_to_pid:
                pid = corpus_to_pid[corpus]
            if pid and pid not in found:
                found[pid] = (data["abstract"], "semantic_scholar")

    return found


def fetch_abstracts(cfg: AppConfig, batch_size: int = 0) -> dict[str, int]:
    data_dir = cfg.storage.data_dir
    store = JsonlStore(data_dir)
    db = DuckDBStore(cfg.storage.duckdb_path)

    papers = list(read_jsonl(data_dir / "papers.jsonl"))

    # Phase 1: mine cached data (free, no API calls)
    print("Phase 1: Mining cached API responses...")
    cached = _mine_cached_abstracts(cfg.storage.cache_dir, papers)

    fetched = 0
    skipped = 0

    for p in papers:
        pid = p.get("paper_id")
        if not pid:
            continue
        key = f"abstract:{pid}"
        if db.is_processed(key):
            skipped += 1
            continue
        if pid in cached:
            abstract, source = cached[pid]
            record = {
                "paper_id": pid,
                "title": p.get("title"),
                "abstract": abstract,
                "provenance": {"source": source, "fetched_at": now_iso()},
            }
            store.append("abstracts.jsonl", record)
            db.mark_processed(key)
            fetched += 1

    print(f"  cached: {fetched} abstracts, {skipped} already done")

    # Phase 2: batch fetch via OpenAlex filter API (up to 50 DOIs per request)
    print("Phase 2: Batch fetching via OpenAlex filter API...")
    oa = OpenAlexClient(
        cfg.openalex.base_url,
        cfg.openalex.rate_limit_per_min,
        cfg.storage.cache_dir,
    )

    remaining = []
    for p in papers:
        pid = p.get("paper_id")
        if not pid:
            continue
        key = f"abstract:{pid}"
        if db.is_processed(key):
            continue
        remaining.append(p)

    print(f"  remaining papers to fetch: {len(remaining)}")

    api_fetched = 0
    failed = 0
    BATCH = 50

    # Group remaining papers into DOI batches
    doi_batches: list[list[dict]] = []
    no_doi: list[dict] = []
    current_batch: list[dict] = []

    for p in remaining:
        if p.get("doi"):
            current_batch.append(p)
            if len(current_batch) >= BATCH:
                doi_batches.append(current_batch)
                current_batch = []
        else:
            no_doi.append(p)
    if current_batch:
        doi_batches.append(current_batch)

    print(f"  DOI batches: {len(doi_batches)} ({sum(len(b) for b in doi_batches)} papers), no-DOI: {len(no_doi)}")

    ts = now_iso()
    for batch_idx, batch_papers in enumerate(doi_batches):
        dois = [p["doi"] for p in batch_papers]
        doi_filter = "|".join(dois)
        data = oa._get("/works", {
            "filter": f"doi:{doi_filter}",
            "per-page": BATCH,
            "select": "id,doi,abstract_inverted_index",
        })

        results = data.get("results") or []
        doi_to_abstract: dict[str, str] = {}
        for r in results:
            rdoi = (r.get("doi") or "").replace("https://doi.org/", "").lower()
            if rdoi:
                text = _reconstruct_oa_abstract(r.get("abstract_inverted_index"))
                if text:
                    doi_to_abstract[rdoi] = text

        for p in batch_papers:
            pid = p["paper_id"]
            key = f"abstract:{pid}"
            doi = p["doi"].lower()
            abstract = doi_to_abstract.get(doi)
            if abstract:
                record = {
                    "paper_id": pid,
                    "title": p.get("title"),
                    "abstract": abstract,
                    "provenance": {"source": "openalex", "fetched_at": ts},
                }
                store.append("abstracts.jsonl", record)
                api_fetched += 1
            else:
                failed += 1
            db.mark_processed(key)

        if (batch_idx + 1) % 5 == 0:
            print(f"  batch {batch_idx + 1}/{len(doi_batches)}: {api_fetched} fetched, {failed} no abstract")

        if batch_size and (fetched + api_fetched) >= batch_size:
            break

    # Handle no-DOI papers individually via OpenAlex or S2
    s2 = SemanticScholarClient(
        cfg.semantic_scholar.base_url,
        cfg.semantic_scholar.rate_limit_per_min,
        cfg.storage.cache_dir,
    )
    for p in no_doi:
        pid = p.get("paper_id")
        if not pid:
            continue
        key = f"abstract:{pid}"
        if db.is_processed(key):
            continue

        abstract = None
        source = None

        oa_id = _oa_lookup_id(p)
        if oa_id:
            data = oa.get_work(oa_id)
            if data:
                text = _reconstruct_oa_abstract(data.get("abstract_inverted_index"))
                if text:
                    abstract = text
                    source = "openalex"

        if not abstract:
            s2_id = _s2_lookup_id(p)
            if s2_id:
                data = s2._get(f"/paper/{s2_id}", {"fields": "abstract"})
                if data and data.get("abstract"):
                    abstract = data["abstract"]
                    source = "semantic_scholar"

        if abstract:
            record = {
                "paper_id": pid,
                "title": p.get("title"),
                "abstract": abstract,
                "provenance": {"source": source, "fetched_at": ts},
            }
            store.append("abstracts.jsonl", record)
            api_fetched += 1
        else:
            failed += 1
        db.mark_processed(key)

    fetched += api_fetched
    store.log_run("fetch_abstracts", {"fetched": fetched, "failed": failed, "skipped": skipped})
    db.close()
    return {"fetched": fetched, "failed": failed, "skipped": skipped}


# ---------------------------------------------------------------------------
# Extended fetching: Europe PMC, DOI content negotiation, title search
# ---------------------------------------------------------------------------

def _normalize_title(title: str) -> str:
    """Normalize title for fuzzy comparison."""
    title = title.lower().strip()
    title = unicodedata.normalize("NFKD", title)
    title = re.sub(r"[^\w\s]", "", title)
    title = re.sub(r"\s+", " ", title).strip()
    return title


def _titles_match(a: str, b: str) -> bool:
    na, nb = _normalize_title(a), _normalize_title(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    # Accept if the shorter is contained in the longer (handles subtitles)
    shorter = na if len(na) < len(nb) else nb
    longer = nb if len(na) < len(nb) else na
    if len(shorter) > 20 and shorter in longer:
        return True
    return False


def _strip_html(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text).strip()


def _get_existing_abstract_pids(data_dir: Path) -> set[str]:
    """Return set of paper_ids that already have abstracts."""
    pids: set[str] = set()
    for rec in read_jsonl(data_dir / "abstracts.jsonl"):
        pid = rec.get("paper_id")
        if pid:
            pids.add(pid)
    return pids


def _phase_europe_pmc_doi(
    papers: list[dict], found_pids: set[str], db: DuckDBStore,
    store: JsonlStore, ts: str,
) -> int:
    """Phase 1: Batch fetch from Europe PMC by DOI."""
    doi_papers = [
        p for p in papers
        if p.get("doi") and p["paper_id"] not in found_pids
        and not db.is_processed(f"abs_epmc:{p['paper_id']}")
    ]
    print(f"  {len(doi_papers)} papers with DOIs to try")
    if not doi_papers:
        return 0

    fetched = 0
    BATCH = 20  # keep query string short

    with httpx.Client(timeout=30.0) as client:
        for i in range(0, len(doi_papers), BATCH):
            batch = doi_papers[i : i + BATCH]
            doi_map = {p["doi"].lower(): p for p in batch}

            query = " OR ".join(f'DOI:"{p["doi"]}"' for p in batch)
            try:
                resp = client.get(
                    "https://www.ebi.ac.uk/europepmc/webservices/rest/search",
                    params={
                        "query": query,
                        "format": "json",
                        "resultType": "core",
                        "pageSize": str(BATCH),
                    },
                )
                if resp.status_code == 429:
                    time.sleep(3)
                    continue
                resp.raise_for_status()
                data = resp.json()
            except Exception as e:
                print(f"  Europe PMC error: {e}")
                time.sleep(1)
                continue

            results = data.get("resultList", {}).get("result", [])
            for r in results:
                abstract = r.get("abstractText")
                rdoi = (r.get("doi") or "").lower()
                if abstract and rdoi in doi_map:
                    pid = doi_map[rdoi]["paper_id"]
                    if pid not in found_pids:
                        abstract = _strip_html(abstract)
                        record = {
                            "paper_id": pid,
                            "title": doi_map[rdoi].get("title"),
                            "abstract": abstract,
                            "provenance": {"source": "europe_pmc", "fetched_at": ts},
                        }
                        store.append("abstracts.jsonl", record)
                        found_pids.add(pid)
                        fetched += 1

            # Mark all batch papers as tried
            for p in batch:
                db.mark_processed(f"abs_epmc:{p['paper_id']}")

            batch_num = i // BATCH + 1
            total_batches = (len(doi_papers) + BATCH - 1) // BATCH
            if batch_num % 10 == 0 or batch_num == total_batches:
                print(f"  batch {batch_num}/{total_batches}: {fetched} fetched")

            time.sleep(0.2)

    print(f"  Europe PMC DOI: {fetched} abstracts")
    return fetched


def _phase_doi_negotiation(
    papers: list[dict], found_pids: set[str], db: DuckDBStore,
    store: JsonlStore, ts: str,
) -> int:
    """Phase 2: DOI content negotiation via CSL-JSON."""
    doi_papers = [
        p for p in papers
        if p.get("doi") and p["paper_id"] not in found_pids
        and not db.is_processed(f"abs_csl:{p['paper_id']}")
    ]
    print(f"  {len(doi_papers)} papers with DOIs to try")
    if not doi_papers:
        return 0

    fetched = 0

    with httpx.Client(timeout=15.0, follow_redirects=True) as client:
        for idx, p in enumerate(doi_papers):
            pid = p["paper_id"]
            if pid in found_pids:
                continue

            doi = p["doi"]
            try:
                resp = client.get(
                    f"https://doi.org/{doi}",
                    headers={"Accept": "application/vnd.citationstyles.csl+json"},
                )
                if resp.status_code == 429:
                    time.sleep(5)
                    db.mark_processed(f"abs_csl:{pid}")
                    continue
                if resp.status_code != 200:
                    db.mark_processed(f"abs_csl:{pid}")
                    continue
                data = resp.json()
                abstract = data.get("abstract")
                if abstract:
                    abstract = _strip_html(abstract)
                    record = {
                        "paper_id": pid,
                        "title": p.get("title"),
                        "abstract": abstract,
                        "provenance": {"source": "doi_csl", "fetched_at": ts},
                    }
                    store.append("abstracts.jsonl", record)
                    found_pids.add(pid)
                    fetched += 1
            except Exception:
                pass

            db.mark_processed(f"abs_csl:{pid}")

            if (idx + 1) % 100 == 0:
                print(f"  {idx + 1}/{len(doi_papers)}: {fetched} fetched")

            time.sleep(0.15)

    print(f"  DOI negotiation: {fetched} abstracts")
    return fetched


def _phase_title_search(
    papers: list[dict], found_pids: set[str], db: DuckDBStore,
    store: JsonlStore, ts: str,
) -> int:
    """Phase 3: Europe PMC title search for remaining papers."""
    remaining = [
        p for p in papers
        if p["paper_id"] not in found_pids and p.get("title")
        and len(p.get("title", "")) > 10
        and not db.is_processed(f"abs_title:{p['paper_id']}")
    ]
    print(f"  {len(remaining)} papers to search by title")
    if not remaining:
        return 0

    fetched = 0

    with httpx.Client(timeout=15.0) as client:
        for idx, p in enumerate(remaining):
            pid = p["paper_id"]
            title = p["title"]

            try:
                resp = client.get(
                    "https://www.ebi.ac.uk/europepmc/webservices/rest/search",
                    params={
                        "query": f'TITLE:"{title}"',
                        "format": "json",
                        "resultType": "core",
                        "pageSize": "3",
                    },
                )
                if resp.status_code == 429:
                    time.sleep(3)
                    db.mark_processed(f"abs_title:{pid}")
                    continue
                if resp.status_code != 200:
                    db.mark_processed(f"abs_title:{pid}")
                    continue
                data = resp.json()
            except Exception:
                db.mark_processed(f"abs_title:{pid}")
                continue

            results = data.get("resultList", {}).get("result", [])
            for r in results:
                abstract = r.get("abstractText")
                r_title = r.get("title", "")
                if abstract and _titles_match(title, r_title):
                    abstract = _strip_html(abstract)
                    record = {
                        "paper_id": pid,
                        "title": p.get("title"),
                        "abstract": abstract,
                        "provenance": {"source": "europe_pmc_title", "fetched_at": ts},
                    }
                    store.append("abstracts.jsonl", record)
                    found_pids.add(pid)
                    fetched += 1
                    break

            db.mark_processed(f"abs_title:{pid}")

            if (idx + 1) % 100 == 0:
                print(f"  {idx + 1}/{len(remaining)}: {fetched} fetched")

            time.sleep(0.15)

    print(f"  Title search: {fetched} abstracts")
    return fetched


def fetch_abstracts_extended(cfg: AppConfig) -> dict[str, Any]:
    """Extended abstract fetching: Europe PMC, DOI negotiation, title search."""
    data_dir = cfg.storage.data_dir
    store = JsonlStore(data_dir)
    db = DuckDBStore(cfg.storage.duckdb_path)

    papers = list(read_jsonl(data_dir / "papers.jsonl"))
    total = len(papers)

    found_pids = _get_existing_abstract_pids(data_dir)
    missing_before = total - len(found_pids)
    print(f"Total papers: {total}, with abstracts: {len(found_pids)}, missing: {missing_before}")

    missing = [p for p in papers if p.get("paper_id") and p["paper_id"] not in found_pids]
    ts = now_iso()

    # Phase 1: Europe PMC by DOI
    print("\nPhase 1: Europe PMC batch DOI lookup...")
    epmc = _phase_europe_pmc_doi(missing, found_pids, db, store, ts)

    # Phase 2: DOI content negotiation
    doi_remaining = [p for p in missing if p.get("doi") and p["paper_id"] not in found_pids]
    print(f"\nPhase 2: DOI content negotiation ({len(doi_remaining)} remaining with DOIs)...")
    csl = _phase_doi_negotiation(missing, found_pids, db, store, ts)

    # Phase 3: Title search
    title_remaining = [p for p in missing if p["paper_id"] not in found_pids]
    print(f"\nPhase 3: Europe PMC title search ({len(title_remaining)} remaining)...")
    title = _phase_title_search(missing, found_pids, db, store, ts)

    fetched = epmc + csl + title
    coverage = len(found_pids)

    store.log_run("fetch_abstracts_extended", {
        "europe_pmc": epmc, "doi_csl": csl, "title_search": title,
        "total_new": fetched, "coverage": f"{coverage}/{total}",
    })
    db.close()

    print(f"\n=== Extended fetch complete ===")
    print(f"  Europe PMC:      {epmc}")
    print(f"  DOI negotiation: {csl}")
    print(f"  Title search:    {title}")
    print(f"  Total new:       {fetched}")
    print(f"  Coverage:        {coverage}/{total} ({100 * coverage / total:.1f}%)")

    return {"fetched": fetched, "europe_pmc": epmc, "doi_csl": csl,
            "title_search": title, "coverage": f"{coverage}/{total}"}
