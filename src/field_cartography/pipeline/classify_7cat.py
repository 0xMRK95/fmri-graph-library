"""6-category paper classification for the agentic survey pipeline.

Classification is performed by Claude Code agents (Task tool, model=haiku/sonnet),
NOT by direct Anthropic API calls. This module handles everything around that:

  1. PREPARE  — Scan unclassified papers, write batch JSON files for agents
  2. INGEST   — Parse agent output (JSONL or JSON array), validate, deduplicate,
                append to classifications_7cat.jsonl, mark batches done
  3. STATUS   — Report classification progress, pending batches, category distribution

Taxonomy (6 categories):
  Core (expand citations):
    1. fmri_gnn             — GNN/graph-transformers/geometric-DL on fMRI
    2. fmri_graph_classical — Classical graph theory on fMRI (metrics, community detection, spectral)
    3. fmri_geometric_manifold — Geometric/manifold/TDA methods on fMRI
  Secondary (leaf nodes, no expansion):
    4. fmri_no_graph         — fMRI analysis without graph methods (incl. standard FC, ICA, seed-based)
    5. graph_methods_no_fmri — Graph/GNN methods not on fMRI data
  Excluded:
    6. out_of_scope          — Not related, EEG/MEG only, etc.

Note: The former "fmri_network_analysis" category was eliminated (2026-02-27).
Papers using standard FC/ICA/seed-based connectivity WITHOUT graph-theoretic methods
belong in fmri_no_graph. Papers using graph metrics belong in fmri_graph_classical.
Papers using GNNs/graph DL belong in fmri_gnn.

Workflow:
  $ uv run fc classify prepare          # writes batch files
  $ # ... spawn Claude Code agents ...  # agents classify papers
  $ uv run fc classify ingest <path>    # parse + append results
  $ uv run fc classify status           # check progress
"""

from __future__ import annotations

import json
import logging
import random
import re
from pathlib import Path
from typing import Any

from ..config import AppConfig
from ..models import Category, CORE_CATEGORIES, SECONDARY_CATEGORIES
from ..storage_jsonl import JsonlStore, now_iso, read_jsonl

logger = logging.getLogger("field-cartography")

# Valid category values (for validation)
VALID_CATEGORIES = {c.value for c in Category}
CORE_CAT_VALUES = {c.value for c in CORE_CATEGORIES}

# ---------------------------------------------------------------------------
# Prompt templates (for reference — agents are spawned by Claude Code)
# ---------------------------------------------------------------------------

CLASSIFY_SYSTEM = (
    "You are an expert classifier for a systematic survey on graph-based methods "
    "applied to fMRI brain analysis. You classify papers into exactly one of 6 "
    "categories. Respond ONLY with a valid JSON array."
)

CLASSIFY_PROMPT = """\
Classify each paper into exactly ONE of these 6 categories:

1. fmri_gnn — Uses graph neural networks (GNN, GCN, GAT, graph transformer), \
graph attention, hypergraph neural networks, or any deep learning that operates \
on graph-structured brain connectivity data from fMRI.
2. fmri_graph_classical — Uses classical graph theory on fMRI data: computes \
graph metrics (degree, betweenness, clustering coefficient, efficiency, \
small-world, modularity), community detection, spectral graph analysis, or \
network topology analysis. The paper must EXPLICITLY compute graph-theoretic \
properties on brain connectivity.
3. fmri_geometric_manifold — Uses geometric deep learning, manifold learning, \
topological data analysis (TDA), persistent homology, or Riemannian geometry \
on fMRI brain connectivity.
4. fmri_no_graph — fMRI connectivity or brain network study that does NOT use \
graph-theoretic, GNN, or geometric methods. This includes: seed-based \
correlation, ICA, standard functional connectivity matrices used as features \
(SVM/RF/CNN on correlation matrices), clinical group comparisons of FC, \
ROI-to-ROI correlation, dynamic FC with sliding windows (without graph \
metrics), and connectome-based predictive modeling (CPM) treating connectomes \
as flat feature vectors.
5. graph_methods_no_fmri — Graph/GNN/geometric methods applied to non-fMRI \
data (e.g., structural MRI, diffusion MRI, EEG-only, non-brain data).
6. out_of_scope — Not related to fMRI or graph methods (e.g., pure computer \
vision, NLP, non-neuroscience, EEG/MEG-only without fMRI).

CRITICAL disambiguation rules:
- A paper that builds a correlation matrix and feeds it into a GNN/GCN/GAT → fmri_gnn (NOT fmri_no_graph)
- A paper that builds a correlation matrix and computes graph metrics (degree, \
  efficiency, modularity, etc.) → fmri_graph_classical (NOT fmri_no_graph)
- A paper that builds a correlation matrix and uses it as a flat feature vector \
  for SVM/RF/CNN classification → fmri_no_graph
- A paper with "network" or "connectivity" in the title is NOT automatically \
  graph-related. Only classify as core (1-3) if the paper explicitly uses \
  graph-theoretic or GNN methods.
- If a paper uses BOTH fMRI and EEG, classify based on the fMRI component.
- If unclear or no abstract, classify as fmri_no_graph.

Categories 1-3 are CORE. Categories 4-5 are SECONDARY. Category 6 is EXCLUDED.

Papers:
{papers}

Return ONLY a JSON array (one object per paper):
[{{"id":"<paper_id>","category":"<category_name>","confidence":"high"|"medium"|"low","reasoning":"<1 sentence>"}}]"""

AGENT_INSTRUCTION = """\
You are classifying papers for a systematic survey on graph learning in fMRI.

Read the batch file at: {batch_path}

For each paper, classify it into exactly ONE of these 6 categories:

1. fmri_gnn — Uses GNN, GCN, GAT, graph transformer, hypergraph neural network, \
or any deep learning on graph-structured fMRI brain data.
2. fmri_graph_classical — Uses classical graph theory on fMRI: explicitly computes \
graph metrics (degree, betweenness, clustering coefficient, efficiency, modularity, \
small-world), community detection, or spectral graph analysis.
3. fmri_geometric_manifold — Uses geometric deep learning, manifold learning, TDA, \
persistent homology, or Riemannian geometry on fMRI data.
4. fmri_no_graph — fMRI study WITHOUT graph/GNN/geometric methods. Includes: \
seed-based correlation, ICA, standard FC matrices as features (SVM/RF/CNN on \
correlation values), clinical FC comparisons, dynamic FC without graph metrics, \
connectome-based predictive modeling as flat features.
5. graph_methods_no_fmri — Graph/GNN methods on non-fMRI data (structural MRI, \
diffusion, EEG-only, non-brain).
6. out_of_scope — Unrelated to fMRI or graph methods, EEG/MEG-only, etc.

CRITICAL rules to avoid misclassification:
- "Network" or "connectivity" in title does NOT mean graph methods. Only classify \
  as core (1-3) if graph-theoretic or GNN methods are explicitly used.
- Correlation matrix + GNN/GCN/GAT → fmri_gnn
- Correlation matrix + graph metrics (degree, efficiency, modularity) → fmri_graph_classical
- Correlation matrix + SVM/RF/CNN (flat features) → fmri_no_graph
- If unclear or no abstract → fmri_no_graph

Output ONLY valid JSONL lines, one per paper:
{{"paper_id":"<id>","category":"<category>"}}

Categories 1-3 are CORE (is_core=true). Categories 4-6 are NOT core.

Process ALL papers in the batch."""


# ---------------------------------------------------------------------------
# PREPARE — Create batch files for agent classification
# ---------------------------------------------------------------------------

def prepare_classify_batches(
    cfg: AppConfig,
    batch_size: int = 50,
    source: str = "all",
) -> dict[str, Any]:
    """Prepare batch files for agent-based classification.

    Scans all known papers (seeds + expansion-discovered), skips those
    already classified, and writes batch JSON files.

    Args:
        cfg: Application configuration.
        batch_size: Papers per batch file.
        source: "seeds" for confirmed_seeds only, "expansion" for
                expand_classify_batches only, "all" for both.

    Returns:
        Statistics dict with batch file paths and counts.
    """
    data_dir = cfg.storage.data_dir

    # Load already-classified paper IDs (last-record-wins for dedup)
    already_classified: set[str] = set()
    for rec in read_jsonl(data_dir / "classifications_7cat.jsonl"):
        already_classified.add(rec["paper_id"])

    # Collect all papers that need classification
    to_classify: list[dict[str, Any]] = []
    seen_ids: set[str] = set(already_classified)

    # Source 1: confirmed seeds
    if source in ("all", "seeds"):
        for rec in read_jsonl(data_dir / "confirmed_seeds.jsonl"):
            pid = rec.get("paper_id", "")
            if pid and pid not in seen_ids:
                to_classify.append({
                    "paper_id": pid,
                    "title": rec.get("title") or "",
                    "abstract": (rec.get("abstract") or "")[:600],
                })
                seen_ids.add(pid)

    # Source 2: expansion-discovered papers (from existing batch files)
    if source in ("all", "expansion"):
        expand_dir = data_dir / "expand_classify_batches"
        if expand_dir.exists():
            for batch_file in sorted(expand_dir.glob("batch_*.json")):
                papers = json.loads(batch_file.read_text(encoding="utf-8"))
                for p in papers:
                    pid = p.get("paper_id", "")
                    if pid and pid not in seen_ids:
                        to_classify.append({
                            "paper_id": pid,
                            "title": p.get("title") or "",
                            "abstract": (p.get("abstract") or "")[:600],
                        })
                        seen_ids.add(pid)

    logger.info(
        "%d papers to classify (%d already done)",
        len(to_classify), len(already_classified),
    )

    if not to_classify:
        return {
            "batches": 0,
            "papers": 0,
            "already_classified": len(already_classified),
        }

    # Write unified batch files
    batch_dir = data_dir / "classify_batches"
    batch_dir.mkdir(parents=True, exist_ok=True)

    # Clear old batch files to avoid stale data
    for old in batch_dir.glob("batch_*.json"):
        old.unlink()

    batch_paths: list[str] = []
    for i in range(0, len(to_classify), batch_size):
        chunk = to_classify[i : i + batch_size]
        batch_num = i // batch_size
        batch_path = batch_dir / f"batch_{batch_num:04d}.json"
        batch_path.write_text(
            json.dumps(chunk, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        batch_paths.append(str(batch_path))

    # Ensure output file exists
    (data_dir / "classifications_7cat.jsonl").touch(exist_ok=True)

    stats = {
        "batches": len(batch_paths),
        "papers": len(to_classify),
        "already_classified": len(already_classified),
        "batch_size": batch_size,
        "batch_dir": str(batch_dir),
        "output_file": str(data_dir / "classifications_7cat.jsonl"),
    }

    logger.info("Prepared %d batches in %s", len(batch_paths), batch_dir)
    return stats


def prepare_expand_batches(
    data_dir: Path,
    papers: list[tuple[str, str, str | None]],
    batch_size: int = 50,
) -> list[str]:
    """Prepare batch files for classifying newly discovered papers during expansion.

    Called by recursive_expand when new papers are discovered.

    Args:
        data_dir: Data directory path.
        papers: list of (paper_id, title, abstract_or_None).
        batch_size: Papers per batch.

    Returns:
        List of batch file paths.
    """
    if not papers:
        return []

    batch_dir = data_dir / "expand_classify_batches"
    batch_dir.mkdir(parents=True, exist_ok=True)

    # Find next batch number
    existing = list(batch_dir.glob("batch_*.json"))
    next_num = len(existing)

    batch_paths: list[str] = []
    for i in range(0, len(papers), batch_size):
        chunk = papers[i : i + batch_size]
        batch_data = [
            {
                "paper_id": pid,
                "title": title,
                "abstract": (abstract or "")[:600],
            }
            for pid, title, abstract in chunk
        ]
        batch_num = next_num + (i // batch_size)
        batch_path = batch_dir / f"batch_{batch_num:04d}.json"
        batch_path.write_text(
            json.dumps(batch_data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        batch_paths.append(str(batch_path))

    return batch_paths


# ---------------------------------------------------------------------------
# INGEST — Parse agent results and append to classifications_7cat.jsonl
# ---------------------------------------------------------------------------

_NUM_TO_CAT: dict[str, str] = {
    "1": "fmri_gnn",
    "2": "fmri_graph_classical",
    "3": "fmri_geometric_manifold",
    "4": "fmri_no_graph",
    "5": "graph_methods_no_fmri",
    "6": "out_of_scope",
}

# Legacy mapping: old 7-cat numeric codes still accepted for ingesting old results
_LEGACY_NUM_TO_CAT: dict[str, str] = {
    "7": "out_of_scope",
}

# Legacy category name: remap to fmri_no_graph
_LEGACY_CAT_REMAP: dict[str, str] = {
    "fmri_network_analysis": "fmri_no_graph",
}


def _normalize_record(rec: dict[str, Any]) -> dict[str, Any] | None:
    """Validate and normalize a single classification record."""
    pid = rec.get("paper_id") or rec.get("id") or ""
    cat = str(rec.get("category", "")).lower().strip()

    if not pid:
        return None
    # Handle numeric categories (agents sometimes output "1"-"6")
    if cat in _NUM_TO_CAT:
        cat = _NUM_TO_CAT[cat]
    elif cat in _LEGACY_NUM_TO_CAT:
        cat = _LEGACY_NUM_TO_CAT[cat]
    # Remap legacy category names
    if cat in _LEGACY_CAT_REMAP:
        cat = _LEGACY_CAT_REMAP[cat]
    if cat not in VALID_CATEGORIES:
        cat = "out_of_scope"

    return {
        "paper_id": pid,
        "category": cat,
        "is_core": cat in CORE_CAT_VALUES,
        "confidence": rec.get("confidence", "medium"),
        "reasoning": rec.get("reasoning", ""),
        "classified_at": rec.get("classified_at", now_iso()),
        "model": rec.get("model", "agent-haiku"),
        "tier": rec.get("tier", 1),
    }


def ingest_classification_file(
    data_dir: Path,
    input_path: Path,
) -> dict[str, Any]:
    """Ingest a JSONL file of classification results.

    Reads the file, validates each record, skips duplicates (papers already
    in classifications_7cat.jsonl), and appends new classifications.

    Args:
        data_dir: Data directory (contains classifications_7cat.jsonl).
        input_path: Path to JSONL file with classification results.

    Returns:
        Statistics dict with ingested/skipped/invalid counts.
    """
    if not input_path.exists():
        return {"error": f"File not found: {input_path}"}

    # Load already-classified (last record wins)
    already: set[str] = set()
    for rec in read_jsonl(data_dir / "classifications_7cat.jsonl"):
        already.add(rec["paper_id"])

    store = JsonlStore(data_dir)
    ingested = 0
    skipped_dup = 0
    invalid = 0

    for rec in read_jsonl(input_path, strict=False):
        normalized = _normalize_record(rec)
        if normalized is None:
            invalid += 1
            continue
        if normalized["paper_id"] in already:
            skipped_dup += 1
            continue
        store.append("classifications_7cat.jsonl", normalized)
        already.add(normalized["paper_id"])
        ingested += 1

    return {
        "ingested": ingested,
        "skipped_duplicate": skipped_dup,
        "invalid": invalid,
        "total_classified": len(already),
    }


def ingest_classification_text(
    data_dir: Path,
    text: str,
) -> dict[str, Any]:
    """Parse classification results from agent output text.

    Handles both JSONL (one JSON object per line) and JSON array formats.
    Skips duplicates, appends new classifications.

    Args:
        data_dir: Data directory.
        text: Raw text from agent output containing classification results.

    Returns:
        Statistics dict with ingested/skipped/invalid counts.
    """
    already: set[str] = set()
    for rec in read_jsonl(data_dir / "classifications_7cat.jsonl"):
        already.add(rec["paper_id"])

    store = JsonlStore(data_dir)
    ingested = 0
    skipped_dup = 0
    invalid = 0

    # Try to find a JSON array in the entire text first
    array_match = re.search(r"\[[\s\S]*?\]", text)
    if array_match:
        try:
            results = json.loads(array_match.group())
            if isinstance(results, list) and len(results) > 0:
                for r in results:
                    normalized = _normalize_record(r)
                    if normalized is None:
                        invalid += 1
                        continue
                    if normalized["paper_id"] in already:
                        skipped_dup += 1
                        continue
                    store.append("classifications_7cat.jsonl", normalized)
                    already.add(normalized["paper_id"])
                    ingested += 1
                return {
                    "ingested": ingested,
                    "skipped_duplicate": skipped_dup,
                    "invalid": invalid,
                    "total_classified": len(already),
                }
        except (json.JSONDecodeError, TypeError):
            pass

    # Fall back to JSONL (one JSON object per line)
    for line in text.strip().split("\n"):
        line = line.strip()
        if not line or not line.startswith("{"):
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            invalid += 1
            continue

        normalized = _normalize_record(rec)
        if normalized is None:
            invalid += 1
            continue
        if normalized["paper_id"] in already:
            skipped_dup += 1
            continue
        store.append("classifications_7cat.jsonl", normalized)
        already.add(normalized["paper_id"])
        ingested += 1

    return {
        "ingested": ingested,
        "skipped_duplicate": skipped_dup,
        "invalid": invalid,
        "total_classified": len(already),
    }


# Legacy alias
parse_classification_results = ingest_classification_text


def ingest_classification_dir(
    data_dir: Path,
    results_dir: Path,
) -> dict[str, Any]:
    """Ingest all JSONL result files from a directory.

    Scans for *.jsonl files, ingests each one, returns aggregate stats.
    """
    if not results_dir.exists():
        return {"error": f"Directory not found: {results_dir}"}

    already: set[str] = set()
    for rec in read_jsonl(data_dir / "classifications_7cat.jsonl"):
        already.add(rec["paper_id"])

    store = JsonlStore(data_dir)
    total_ingested = 0
    total_skipped = 0
    total_invalid = 0
    files_processed = 0

    for result_file in sorted(results_dir.glob("*.jsonl")):
        for rec in read_jsonl(result_file, strict=False):
            normalized = _normalize_record(rec)
            if normalized is None:
                total_invalid += 1
                continue
            if normalized["paper_id"] in already:
                total_skipped += 1
                continue
            store.append("classifications_7cat.jsonl", normalized)
            already.add(normalized["paper_id"])
            total_ingested += 1
        files_processed += 1

    return {
        "files_processed": files_processed,
        "ingested": total_ingested,
        "skipped_duplicate": total_skipped,
        "invalid": total_invalid,
        "total_classified": len(already),
    }


def extract_from_agent_logs(
    data_dir: Path,
    logs_dir: Path,
) -> dict[str, Any]:
    """Extract classification JSONL from agent background output logs.

    Fallback for when agents couldn't write to disk files directly.
    Scans .output files for lines matching {"paper_id":..., "category":...}.
    Writes extracted results to data/classify_results/extracted.jsonl,
    then ingests them.
    """
    if not logs_dir.exists():
        return {"error": f"Directory not found: {logs_dir}"}

    results_dir = data_dir / "classify_results"
    results_dir.mkdir(parents=True, exist_ok=True)
    extracted_path = results_dir / "extracted.jsonl"

    extracted = 0
    with extracted_path.open("w", encoding="utf-8") as out:
        for log_file in sorted(logs_dir.glob("*.output")):
            try:
                text = log_file.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            for line in text.split("\n"):
                line = line.strip()
                if not line or not line.startswith("{"):
                    continue
                try:
                    rec = json.loads(line)
                    if "paper_id" in rec and "category" in rec:
                        out.write(line + "\n")
                        extracted += 1
                except json.JSONDecodeError:
                    # Try to extract from nested JSON (agent conversation logs)
                    pass

            # Also try to find JSONL embedded in agent conversation JSON
            for line in text.split("\n"):
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                    # Agent logs are conversation JSONL — look for content with classifications
                    content = ""
                    if isinstance(obj, dict):
                        msg = obj.get("message", {})
                        if isinstance(msg, dict):
                            content = str(msg.get("content", ""))
                    for subline in content.split("\\n"):
                        subline = subline.strip()
                        if subline.startswith("{") and "paper_id" in subline and "category" in subline:
                            try:
                                rec = json.loads(subline)
                                if "paper_id" in rec and "category" in rec:
                                    out.write(json.dumps(rec, ensure_ascii=False) + "\n")
                                    extracted += 1
                            except json.JSONDecodeError:
                                pass
                except (json.JSONDecodeError, TypeError):
                    pass

    if extracted == 0:
        return {"extracted": 0, "message": "No classifications found in agent logs"}

    # Now ingest the extracted file
    result = ingest_classification_file(data_dir, extracted_path)
    result["extracted_from_logs"] = extracted
    return result


# ---------------------------------------------------------------------------
# STATUS — Classification progress report
# ---------------------------------------------------------------------------

def collect_classification_stats(data_dir: Path) -> dict[str, Any]:
    """Comprehensive classification status report.

    Reports:
      - Category distribution from classifications_7cat.jsonl
      - Number of pending papers (in batch files but not yet classified)
      - Batch file inventory
    """
    # Category distribution
    classified: dict[str, str] = {}  # paper_id → category (last wins)
    cat_counts: dict[str, int] = {c.value: 0 for c in Category}

    for rec in read_jsonl(data_dir / "classifications_7cat.jsonl"):
        pid = rec.get("paper_id", "")
        cat = rec.get("category", "out_of_scope")
        classified[pid] = cat

    for cat in classified.values():
        cat_counts[cat] = cat_counts.get(cat, 0) + 1

    total = len(classified)

    # Count pending papers from batch directories
    pending_ids: set[str] = set()
    for batch_dir_name in ("classify_batches", "expand_classify_batches"):
        batch_dir = data_dir / batch_dir_name
        if batch_dir.exists():
            for batch_file in sorted(batch_dir.glob("batch_*.json")):
                papers = json.loads(batch_file.read_text(encoding="utf-8"))
                for p in papers:
                    pid = p.get("paper_id", "")
                    if pid and pid not in classified:
                        pending_ids.add(pid)

    # Count batch files
    classify_batches = len(list((data_dir / "classify_batches").glob("batch_*.json"))) if (data_dir / "classify_batches").exists() else 0
    expand_batches = len(list((data_dir / "expand_classify_batches").glob("batch_*.json"))) if (data_dir / "expand_classify_batches").exists() else 0

    # Core / secondary / excluded breakdown
    core_count = sum(cat_counts.get(c.value, 0) for c in CORE_CATEGORIES)
    secondary_count = sum(cat_counts.get(c.value, 0) for c in SECONDARY_CATEGORIES)
    excluded_count = cat_counts.get("out_of_scope", 0)

    stats: dict[str, Any] = {
        **cat_counts,
        "total": total,
        "core": core_count,
        "secondary": secondary_count,
        "excluded": excluded_count,
        "pending": len(pending_ids),
        "classify_batches": classify_batches,
        "expand_classify_batches": expand_batches,
    }

    return stats


# ---------------------------------------------------------------------------
# Sonnet audit
# ---------------------------------------------------------------------------

def prepare_audit_batches(
    data_dir: Path,
    sample_size: int = 100,
    batch_size: int = 50,
) -> dict[str, Any]:
    """Prepare batch files for Sonnet audit of classifications."""
    haiku_cls: dict[str, str] = {}
    for rec in read_jsonl(data_dir / "classifications_7cat.jsonl"):
        haiku_cls[rec["paper_id"]] = rec["category"]

    if not haiku_cls:
        return {"error": "No classifications to audit"}

    meta_index: dict[str, dict[str, Any]] = {}
    for rec in read_jsonl(data_dir / "confirmed_seeds.jsonl"):
        pid = rec.get("paper_id")
        if pid:
            meta_index[pid] = rec
    for rec in read_jsonl(data_dir / "discovered_papers.jsonl"):
        pid = rec.get("paper_id")
        if pid and pid not in meta_index:
            meta_index[pid] = rec

    all_ids = list(haiku_cls.keys())
    sample_ids = random.sample(all_ids, min(sample_size, len(all_ids)))

    audit_dir = data_dir / "audit_batches"
    audit_dir.mkdir(parents=True, exist_ok=True)

    papers_data: list[dict[str, Any]] = []
    for pid in sample_ids:
        meta = meta_index.get(pid, {})
        papers_data.append({
            "paper_id": pid,
            "title": meta.get("title", ""),
            "abstract": (meta.get("abstract") or "")[:600],
            "haiku_category": haiku_cls.get(pid, "unknown"),
        })

    batch_paths: list[str] = []
    for i in range(0, len(papers_data), batch_size):
        chunk = papers_data[i : i + batch_size]
        batch_num = i // batch_size
        batch_path = audit_dir / f"audit_batch_{batch_num:04d}.json"
        batch_path.write_text(
            json.dumps(chunk, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        batch_paths.append(str(batch_path))

    sample_path = audit_dir / "audit_sample.json"
    sample_path.write_text(
        json.dumps({"sample_ids": sample_ids, "haiku_categories": haiku_cls}, ensure_ascii=False),
        encoding="utf-8",
    )

    return {
        "sample_size": len(sample_ids),
        "batches": len(batch_paths),
        "batch_dir": str(audit_dir),
        "batch_paths": batch_paths,
    }


def compute_audit_agreement(data_dir: Path) -> dict[str, Any]:
    """Compute Haiku vs Sonnet agreement from audit results."""
    audit_dir = data_dir / "audit_batches"
    sample_path = audit_dir / "audit_sample.json"
    if not sample_path.exists():
        return {"error": "No audit sample found — run prepare-audit first"}

    sample_data = json.loads(sample_path.read_text(encoding="utf-8"))
    haiku_cls = sample_data["haiku_categories"]
    sample_ids = sample_data["sample_ids"]

    sonnet_cls: dict[str, str] = {}
    for rec in read_jsonl(data_dir / "audit_classifications.jsonl"):
        sonnet_cls[rec["paper_id"]] = rec["category"]

    agree = 0
    disagree = 0
    confusion: dict[str, dict[str, int]] = {}
    for pid in sample_ids:
        h_cat = haiku_cls.get(pid, "unknown")
        s_cat = sonnet_cls.get(pid, "unknown")
        if h_cat == s_cat:
            agree += 1
        else:
            disagree += 1
        confusion.setdefault(h_cat, {})
        confusion[h_cat][s_cat] = confusion[h_cat].get(s_cat, 0) + 1

    total = agree + disagree
    rate = agree / total if total > 0 else 0.0

    result = {
        "sample_size": len(sample_ids),
        "sonnet_responses": len(sonnet_cls),
        "agreement": agree,
        "disagreement": disagree,
        "agreement_rate": round(rate, 4),
        "confusion_matrix": confusion,
    }

    audit_path = data_dir / "audit_sonnet_7cat.json"
    audit_path.write_text(json.dumps(result, indent=2), encoding="utf-8")

    logger.info("Audit: %d/%d agree (%.1f%%)", agree, total, rate * 100)
    return result
