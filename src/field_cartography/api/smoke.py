"""Smoke test for the query API.

    python -m field_cartography.api.smoke [optional_canonical_id]

Constructs a Client, exercises every public method once, prints PASS/FAIL
per method, and exits non-zero if any method errors or returns an obviously
broken result. v31 runs this before allow-listing the cartography tools.

It auto-discovers a known-good canonical id (a confirmed fmri_gnn paper with
full text) unless one is passed on the command line.
"""
from __future__ import annotations

import sys
import time
import traceback

from . import Client, __version__


def _discover_id(c: Client) -> str:
    """Pick a paper that exists in citations + classifications + corpus."""
    idx = c.has_fulltext_index()
    for canon in idx:
        if c.cited_by(canon) or c.cites(canon):
            if c._cls.get(canon):  # noqa: SLF001 - smoke test introspection
                return canon
    # fallback: any fmri_gnn paper
    gnn = c.papers_in_category("fmri_gnn")
    return gnn[0] if gnn else next(iter(idx), "doi:10.1002/ima.70306")


def main() -> int:
    print(f"field_cartography.api smoke test  (v{__version__})")
    results: list[tuple[str, bool, str]] = []

    def check(name, fn, ok=lambda r: True):
        t0 = time.perf_counter()
        try:
            r = fn()
            dt = (time.perf_counter() - t0) * 1000
            passed = ok(r)
            note = f"{dt:7.1f}ms  {_short(r)}"
            results.append((name, passed, note))
        except Exception:  # noqa: BLE001
            results.append((name, False, "EXC\n" + traceback.format_exc()))

    c = Client()

    check("reducer_spec", c.reducer_spec, lambda r: "rule" in r)
    check("health", c.health, lambda r: r["totals"]["classifications"] > 0)
    check("category_counts", c.category_counts,
          lambda r: r.get("fmri_gnn", 0) > 0)

    pid = sys.argv[1] if len(sys.argv) > 1 else _discover_id(c)
    print(f"  using sample id: {pid}\n")

    check("resolve_id(doi)", lambda: c.resolve_id(pid),
          lambda r: r is not None)
    check("metadata", lambda: c.metadata(pid), lambda r: r is not None)
    check("metadata_batch", lambda: c.metadata_batch([pid]),
          lambda r: pid in r)
    check("cites", lambda: c.cites(pid), lambda r: isinstance(r, list))
    check("cited_by", lambda: c.cited_by(pid), lambda r: isinstance(r, list))
    check("co_cited_with", lambda: c.co_cited_with(pid, top_k=5),
          lambda r: isinstance(r, list))
    check("papers_in_category", lambda: c.papers_in_category("fmri_gnn"),
          lambda r: len(r) > 0)
    check("search_titles", lambda: c.search_titles("graph neural network fmri"),
          lambda r: isinstance(r, list))
    check("node_stats", lambda: c.node_stats(pid), lambda r: r is not None)
    check("fulltext_path", lambda: c.fulltext_path(pid))
    check("has_fulltext_index", c.has_fulltext_index, lambda r: len(r) > 0)

    # title / author / venue (best-effort, derive from the sample)
    rec = c.metadata(pid)
    if rec and rec.title:
        check("resolve_by_title", lambda: c.resolve_by_title(rec.title),
              lambda r: r is not None)
    if rec and rec.authors:
        nm = rec.authors[0].get("name")
        check("papers_by_author", lambda: c.papers_by_author(nm),
              lambda r: isinstance(r, list))
    if rec and rec.venue:
        check("papers_in_venue", lambda: c.papers_in_venue(rec.venue),
              lambda r: isinstance(r, list))

    # error contract
    check("resolve_id(garbage)->ValueError",
          lambda: _expect_valueerror(c.resolve_id, "!!!notanid!!!"),
          lambda r: r is True)
    check("metadata(missing)->None",
          lambda: c.metadata("doi:10.0000/does-not-exist"),
          lambda r: r is None)

    print()
    n_fail = 0
    for name, ok, note in results:
        tag = "PASS" if ok else "FAIL"
        if not ok:
            n_fail += 1
        print(f"  [{tag}] {name:32s} {note}")
    print()
    if n_fail:
        print(f"SMOKE FAILED: {n_fail}/{len(results)} methods failed")
        return 1
    print(f"SMOKE OK: {len(results)}/{len(results)} methods passed")
    return 0


def _expect_valueerror(fn, *a):
    try:
        fn(*a)
        return False
    except ValueError:
        return True


def _short(r) -> str:
    s = repr(r)
    return s if len(s) <= 90 else s[:87] + "..."


if __name__ == "__main__":
    raise SystemExit(main())
