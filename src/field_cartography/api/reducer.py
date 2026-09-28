"""The classification reducer — the single source of truth for 'what category
is this paper'.

`classifications_7cat.jsonl` is an APPEND LOG, not a table: a paper can have
many rows across re-classification waves (Haiku first-pass, Sonnet re-pass,
Opus tiebreak, administrative prune rows). v31 must NOT count raw lines.

This module encodes the reduction so callers never reimplement it. The exact
rule is also returned by `Client.reducer_spec()` for transcript logging.
"""
from __future__ import annotations

from typing import Any, Callable

# Tier ladder (the `tier` int field in classification rows):
#   3  -> Sonnet / Opus SDK + agent classifiers (most trusted)
#   2  -> Haiku medium-confidence re-pass
#   1  -> Haiku first-pass
#   0  -> administrative rows (prune-unreachable, *_audit) — low trust
#   None -> legacy / unknown (treated as -1 for ordering)
TIER_LADDER: dict[int, str] = {
    3: "sonnet/opus",
    2: "haiku-repass",
    1: "haiku-firstpass",
    0: "administrative",
}

# Human-facing names accepted by `tier_min` (in addition to raw ints).
TIER_NAME_TO_INT: dict[str, int] = {
    "any": 0,
    "administrative": 0,
    "haiku": 1,
    "haiku-firstpass": 1,
    "haiku-repass": 2,
    "sonnet": 3,
    "opus": 3,
    "sonnet/opus": 3,
    "trusted": 3,
}

CORE_CATEGORIES = frozenset(
    {"fmri_gnn", "fmri_graph_classical", "fmri_geometric_manifold"}
)

ALL_CATEGORIES = frozenset(
    {
        "fmri_gnn",
        "fmri_graph_classical",
        "fmri_geometric_manifold",
        "fmri_no_graph",
        "graph_methods_no_fmri",
        "out_of_scope",
        "uncertain",
    }
)

REDUCER_SPEC: dict[str, Any] = {
    "version": "1.0",
    "rule": (
        "For each paper, collapse all IDs to their canonical id via "
        "id_aliases.jsonl (transitive), group every classification row under "
        "that canonical id, then pick the WINNING row as the one with the "
        "highest `tier`, breaking ties by latest `classified_at`. The winning "
        "row's `category`/`tier`/`confidence` is the authoritative label. "
        "Administrative tier-0 rows (prune-unreachable, *_audit) only win if a "
        "paper has no higher-tier row."
    ),
    "tier_ladder": TIER_LADDER,
    "alias_collapse": "transitive via data/id_aliases.jsonl (alias -> canonical)",
    "notes": (
        "Validated 2026-06-16: yields 144,824 unique canonical papers and "
        "fmri_gnn=1,205. The frozen snapshot data/classifications_7cat."
        "canonicalized.jsonl reports 1,206 (a 1-paper alias-edge drift). "
        "Counting raw log lines instead gives fmri_gnn=1,502 (~20% inflation) "
        "— do not do that."
    ),
}


def _sort_key(row: dict) -> tuple[int, str]:
    tier = row.get("tier")
    tier = tier if isinstance(tier, int) else -1
    return (tier, row.get("classified_at") or "")


def winning_row(rows: list[dict]) -> dict:
    """Return the authoritative classification row for one paper's rows."""
    return max(rows, key=_sort_key)


def reduce_group(rows: list[dict]) -> dict:
    """Reduce a paper's rows to a compact label dict."""
    win = winning_row(rows)
    cat = win.get("category")
    return {
        "category": cat,
        "tier": win.get("tier"),
        "confidence": win.get("confidence"),
        "is_core": bool(win.get("is_core")) or (cat in CORE_CATEGORIES),
        "model": win.get("model"),
        "classified_at": win.get("classified_at"),
    }


def tier_min_to_int(tier_min: "int | str | None") -> int:
    """Normalize a tier_min argument (int or human name) to an int floor."""
    if tier_min is None:
        return -1
    if isinstance(tier_min, int):
        return tier_min
    key = str(tier_min).strip().lower()
    if key not in TIER_NAME_TO_INT:
        raise ValueError(
            f"unrecognized tier_min {tier_min!r}; use an int 0-3 or one of "
            f"{sorted(TIER_NAME_TO_INT)}"
        )
    return TIER_NAME_TO_INT[key]
