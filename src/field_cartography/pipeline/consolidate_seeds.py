"""Phase 1: Consolidate all confirmed fMRI×GNN seed papers.

Collects papers classified as fmri_gnn from both haiku_classifications.jsonl
and classifications.jsonl, joins full metadata, filters EEG-only papers,
and outputs a unified confirmed_seeds.jsonl.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from ..config import AppConfig
from ..storage_jsonl import JsonlStore, now_iso, read_jsonl

logger = logging.getLogger("field-cartography")

# Patterns for EEG/MEG detection vs fMRI confirmation
_EEG_PATTERNS = re.compile(
    r"\b(eeg|electroencephalogra\w+|meg\b|magnetoencephalogra\w+)",
    re.IGNORECASE,
)
_FMRI_PATTERNS = re.compile(
    r"\b(fmri|functional\s+mri|functional\s+magnetic|"
    r"functional\s+connectiv\w+|bold|resting[- ]state|rs-fmri|rsfmri)",
    re.IGNORECASE,
)


def _is_eeg_only(title: str | None, abstract: str | None) -> bool:
    """Return True if the paper mentions EEG/MEG but NOT fMRI."""
    text = f"{title or ''} {abstract or ''}"
    if not _EEG_PATTERNS.search(text):
        return False
    if _FMRI_PATTERNS.search(text):
        return False  # mentions both — keep it
    return True


def consolidate_seeds(cfg: AppConfig) -> dict[str, int]:
    """Collect, join, filter, and output confirmed seed papers.

    Returns:
        Statistics dict.
    """
    data_dir = cfg.storage.data_dir
    store = JsonlStore(data_dir)

    # Step 1: Collect fmri_gnn paper IDs (last-record-wins for each source)
    fmri_gnn_ids: set[str] = set()

    for rec in read_jsonl(data_dir / "haiku_classifications.jsonl"):
        if rec.get("is_fmri_gnn") is True:
            fmri_gnn_ids.add(rec["paper_id"])

    for rec in read_jsonl(data_dir / "classifications.jsonl"):
        if rec.get("category") == "fmri_gnn":
            fmri_gnn_ids.add(rec["paper_id"])

    logger.info("Found %d fmri_gnn paper IDs from classifications", len(fmri_gnn_ids))

    # Step 2: Build metadata index (discovered_papers > papers > abstracts)
    # discovered_papers.jsonl is richest (has abstract, citation_count, etc.)
    meta_index: dict[str, dict[str, Any]] = {}

    # Base layer: papers.jsonl
    for rec in read_jsonl(data_dir / "papers.jsonl"):
        pid = rec.get("paper_id")
        if pid and pid in fmri_gnn_ids:
            meta_index[pid] = rec

    # Overlay: discovered_papers.jsonl (richer metadata)
    for rec in read_jsonl(data_dir / "discovered_papers.jsonl"):
        pid = rec.get("paper_id")
        if pid and pid in fmri_gnn_ids:
            existing = meta_index.get(pid, {})
            # Merge — prefer discovered_papers fields when present
            merged = {**existing, **{k: v for k, v in rec.items() if v is not None}}
            meta_index[pid] = merged

    # Overlay abstracts from abstracts.jsonl (if not already present)
    abstracts_path = data_dir / "abstracts.jsonl"
    if abstracts_path.exists():
        for rec in read_jsonl(abstracts_path):
            pid = rec.get("paper_id")
            if pid and pid in meta_index and not meta_index[pid].get("abstract"):
                meta_index[pid]["abstract"] = rec.get("abstract")

    # Step 3: Filter EEG-only papers
    eeg_filtered = 0
    confirmed: list[dict[str, Any]] = []

    for pid in sorted(fmri_gnn_ids):
        meta = meta_index.get(pid)
        if not meta:
            # No metadata found — still include with minimal record
            meta = {"paper_id": pid}

        title = meta.get("title")
        abstract = meta.get("abstract")

        if _is_eeg_only(title, abstract):
            eeg_filtered += 1
            logger.debug("Filtered EEG-only paper: %s — %s", pid, title)
            continue

        # Build unified output record
        seed = {
            "paper_id": pid,
            "title": meta.get("title"),
            "year": meta.get("year"),
            "venue": meta.get("venue"),
            "authors": meta.get("authors", []),
            "abstract": meta.get("abstract"),
            "doi": meta.get("doi"),
            "external_ids": meta.get("external_ids") or meta.get("externalIds") or {},
            "citation_count": meta.get("citation_count") or meta.get("citationCount"),
            "reference_count": meta.get("reference_count") or meta.get("referenceCount"),
            "open_access_pdf": meta.get("open_access_pdf") or meta.get("openAccessPdf"),
            "s2_url": meta.get("s2_url") or meta.get("url"),
            "seed_tag": meta.get("seed_tag"),
            "source": meta.get("source"),
            "provenance": {
                "source": "consolidate_seeds",
                "consolidated_at": now_iso(),
                "original_source": (meta.get("provenance") or {}).get("source", "unknown"),
            },
        }
        confirmed.append(seed)

    # Step 4: Write confirmed_seeds.jsonl (overwrite)
    output_path = data_dir / "confirmed_seeds.jsonl"
    with output_path.open("w", encoding="utf-8") as f:
        for seed in confirmed:
            f.write(json.dumps(seed, ensure_ascii=False) + "\n")

    stats = {
        "total_fmri_gnn_ids": len(fmri_gnn_ids),
        "metadata_found": len(meta_index),
        "eeg_filtered": eeg_filtered,
        "confirmed_seeds": len(confirmed),
    }
    store.log_run("consolidate_seeds", stats)
    logger.info("Consolidation complete: %s", json.dumps(stats, indent=2))
    return stats
