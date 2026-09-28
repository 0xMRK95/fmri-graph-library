# `field_cartography.api` — read-only query layer (v1.0.0)

A stateful, in-memory, **read-only** query client over the field-cartography
warehouse (citation graph + 7-cat classifications + metadata + full-text
pointers). Built for sibling projects (e.g. v31 idea-agent) to `pip install -e`
and import. No writes, no live external API calls, no embeddings.

## Install

```bash
cd /root/Workspace/PhD/cartography/field-cartography
uv pip install -e .        # or: pip install -e .
```

## Quick start

```python
from field_cartography.api import Client

c = Client()                       # cheap; loads nothing yet
c.warm()                           # optional: preload all indexes (~38s, ~1.9 GB RSS)

rec = c.metadata("doi:10.1002/ima.70306")
print(rec.title, rec.category, rec.has_fulltext)

c.resolve_id("10.1002/ima.70306")          # -> "doi:10.1002/ima.70306"
c.cited_by("doi:10.1016/j.media.2018.06.001")   # forward citations
c.papers_in_category("fmri_gnn", year_min=2023, tier_min="sonnet")
c.search_titles("dynamic functional connectivity graph transformer")
p = c.fulltext_path("doi:...")             # Path to corpus/*.md or None — you open() it
```

### Construction

```python
Client(data_root: Path | None = None,    # default: <repo>/data
       corpus_root: Path | None = None,   # default: /root/Workspace/PhD/cartography/corpus
       slim: bool = False)                # slim=True skips on-disk abstracts (~0.4 GB saved)
```

`Client()` with no args is the normal case. With `slim=True`, `PaperRecord.abstract`
is always `None` (read abstracts from the markdown corpus instead).

## Concurrency / async

The API is **pure sync** (recommendation (a) from the spec). All work after warm
is CPU-bound dict lookups. From an asyncio session, wrap calls in
`asyncio.to_thread(c.metadata, pid)`. After indexes are loaded, all state is
read-only, so concurrent `to_thread` calls are safe. (Lazy first-touch loads are
guarded by an internal lock, so a concurrent first call won't double-load — but
to be safe, call `c.warm()` once at startup before fanning out.)

## Loading model

Lazy per-index, cached. Index names for `warm(*names)`:
`aliases, id_map, papers, citations, classifications, authors, venues, corpus,
network, titles`. `warm()` with no args loads everything.

Measured (this host, full warm): **~38s, peak RSS ~1.88 GB**. `slim=True` lowers RSS.

## Error contract

- Missing data → `None` (single) or `[]` (list). Never raises for missing.
- Malformed input (wrong type, unrecognizable id) → `ValueError`.
- Stale-data notes → stdlib `logging` (`field_cartography.api` logger), never raises.

## Latency (after warm, measured on this host)

| method | target | measured (median / p95) |
|---|---|---|
| resolve_id, metadata, cites, cited_by, node_stats, fulltext_path | <1 ms | <0.05 ms ✅ |
| metadata_batch (200) | <50 ms | 7.7 / 21 ms ✅ |
| papers_in_category | <100 ms | 34 / 59 ms ✅ |
| category_counts | <100 ms | 65 / 93 ms ✅ |
| search_titles | <500 ms | 170–350 / ≤470 ms ✅ |
| resolve_by_title | <200 ms | 1.8 / 3 ms ✅ |
| co_cited_with | <500 ms (1st ~1s) | 1st-call ~12 ms, cached 0.3 ms ✅ |

## The reducer (`reducer_spec()`)

`classifications_7cat.jsonl` is an **append log** with many rows per paper across
re-classification waves. The API reduces it so you never reimplement it:

> For each paper, collapse all IDs to canonical via `id_aliases.jsonl`
> (transitive), group every classification row under that canonical id, then pick
> the winning row by **highest `tier`, ties broken by latest `classified_at`**.
> Administrative tier-0 rows (`prune-unreachable`, `*_audit`) only win if nothing
> higher exists.

Tier ladder: `3 = sonnet/opus` (trusted), `2 = haiku-repass`, `1 = haiku-firstpass`,
`0 = administrative`. `tier_min` accepts an int (0–3) or a name
(`"sonnet"`/`"opus"`/`"trusted"` → 3, `"haiku"` → 1, `"any"` → 0).

**Authoritative counts** (`category_counts()`, validated 2026-06-16):

| category | n |
|---|---:|
| fmri_gnn | **1,205** |
| fmri_graph_classical | 4,163 |
| fmri_geometric_manifold | 405 |
| fmri_no_graph | 29,324 |
| graph_methods_no_fmri | 13,989 |
| out_of_scope | 95,009 |
| uncertain | 729 |
| **total canonical papers** | **144,824** |

`category_counts()` is THE number (B3). The frozen snapshot
`classifications_7cat.canonicalized.jsonl` says fmri_gnn=1,206 (a 1-paper
alias-edge drift). Counting raw log lines gives 1,502 (~20% inflation) — don't.

## Audit metric (B5) — `health()["audit_metric"]`

> *final-label vs independent Sonnet strict-audit concordance = **0.669** on
> 4,302 papers (2026-06-16). No human gold standard exists; this is model
> concordance.*

**Honest reading of it.** There is **no human ground truth**. `phase3_groundtruth.jsonl`
holds *Haiku* labels, not gold. The cheap-Haiku vs Sonnet/Opus overlap is only
**47 papers** (the deliberately-escalated hard cases) — selection-biased and
useless as an accuracy estimate. The most defensible signal is concordance
between the final reduced label and the independent 4,302-paper Sonnet "strict
audit" (`strict_audit_results.jsonl`). It **decomposes**, and this is the part
v31 should act on:

| boundary | concordance |
|---|---:|
| out_of_scope (in/out-of-scope gate) | **0.97** |
| graph_methods_no_fmri | 0.92 |
| fmri_no_graph | 0.75 |
| fmri_gnn | 0.65 |
| fmri_geometric_manifold | 0.62 |
| fmri_graph_classical | 0.56 |

**Takeaway for v31:** trust "is this fMRI×graph at all" (~95%); treat the fine
subcategory split (gnn vs classical vs manifold) as **~55–65% reliable**. When
the strict audit disagreed it usually pulled `fmri_no_graph`/`graph_methods_no_fmri`
*into* the graph-on-fMRI buckets (the audit was more inclusive). Raw numbers in
`data/api_audit_metric.json`.

## Known-stale-data caveats (Part C5)

- **Provenance is ~83%, not 12%.** My earlier 12% was a head-of-file sampling
  artifact (early seed records lack it). Full-file: 83.3% of papers carry
  `provenance.source` + `fetched_at`; the missing 17% are early phase-1 seeds.
  `provenance.confidence` is essentially absent (354 records) — the `confidence`
  you want is the *classification* confidence, exposed as
  `category_confidence`. (Answers **P3**.)
- **Network snapshot lag.** `node_type`, `cluster_id`, and the `network/nodes.jsonl`
  view are a **2026-06-03** snapshot (pre-final-dedup). `node_type` covers the
  218K snapshot, not all 262K papers, so it can be `None` for recently-added
  papers. Categories/counts come from the fresher **06-05** classification log,
  not the snapshot.
- **No author disambiguation.** Names are free strings; there are no `authorId`s
  in the data. `papers_by_author` is exact normalized-name match only and will
  conflate distinct people who share a name.
- **Venue is a free string.** No venue normalization; `papers_in_venue` is exact
  normalized-string match.
- **citation_count is the S2 *global* count**, not in-corpus degree. For in-corpus
  degree use `node_stats()` `in_degree`/`out_degree`.
- **~38% of papers have no/stub abstract** on disk (62% inline coverage). Prefer
  the markdown corpus when present.
- **S2 back-link lag ~26%** on 2024–26 papers: some real forward edges are simply
  not in S2 yet, so `cited_by` undercounts very recent citers.
- **DuckDB (`db/duckdb/...`) is NOT used by this layer** — it's the build
  pipeline's stale dedup index. Ignore it.

## Corpus shape (B1)

- Path: `/root/Workspace/PhD/cartography/corpus/` (override via `corpus_root`).
- `INDEX.jsonl`: **1,627 rows** in the current local snapshot, one per readable
  full-text record. Schema per row:
  `paper_id, base, title, year, venue, authors[str], topic (7-cat), is_core,
  cluster, seed_tag, format (html|markdown|pdf_text|pmc_xml), source_tag,
  fulltext_file (relative path), has_metadata, n_chars`.
- Bodies are markdown at `corpus/fulltext/<base>.md`. v31 reads them directly.
- **Full-vs-partial:** all 1,627 indexed paths currently exist, but readable does
  not guarantee complete article text. The generated section-level search index
  contains 1,581 converted documents, marks 1,254 as evidence-ready, and retains
  1,350 preferred documents after version deduplication. The rest are short,
  thin, blocked/stub, or suspected title mismatches. Check `n_chars` and the
  source body before relying on a passage.
- The INDEX-level acquisition tags currently include 1,219 `fmri_gnn`, 395
  `fmri_geometric_manifold`, and 4 `fmri_graph_classical` records. These topic
  tags are not the canonical classification reducer and therefore need not equal
  `category_counts()`.
- `has_fulltext`/`fulltext_path` on `PaperRecord`, plus `c.fulltext_path(id)` and
  `c.has_fulltext_index()`, only return paths that **exist on disk**.

## PMC resolution (P2)

PMC **is** indexed. PMC ids live only in `external_ids.PubMedCentral` (60,496
papers), not in `id_map.jsonl`, so PMC resolution requires the `papers` index —
`resolve_id("PMC11449801")` auto-loads it on first use. **OpenAlex is NOT
resolvable**: there are 0 OpenAlex ids anywhere in the corpus. `resolve_id("W...")`
returns `None` (recognized format, no data), and `external_ids.openalex` is always
`None`.

## Concurrency / freshness (P11)

The pipeline is **paused** (recall-saturated; see `docs/CONVERGENCE.md`). Last data
writes were **2026-06-05**. Because this Client loads everything into memory at
warm/first-touch and never re-reads, you do **not** need to `cp` JSONLs first —
load once at startup and you have a stable snapshot for the session. (Torn final
lines from any future concurrent append are skipped, not raised.)

## Deviations from the spec (honest list)

1. **`node_type` is only populated when the `network` index is loaded.** `metadata()`
   does NOT force-load the 289 MB network snapshot (it would add ~4s + memory to
   the first metadata call). It loads `papers`+`classifications`+`corpus`. To get
   `node_type` inside `PaperRecord`, call `c.warm("network")` first (or use
   `node_stats()`, which loads it). `warm()` with no args includes it.
2. **`tier_min` accepts `int | str`, not just `str`.** The data's `tier` is an int;
   I kept the spec's string names (`"sonnet"`/`"opus"`/…) AND allow raw ints 0–3.
3. **`category_counts()` returns fmri_gnn=1,205**, not the snapshot's 1,206
   (1-paper alias-edge drift). The live reducer is authoritative per your B3.
4. **audit_metric is concordance, not precision/recall vs gold.** No gold set
   exists; I refuse to print a fake precision number. See the audit section.
5. **No `warehouse.duckdb`** built (B4 said skip if latency targets met — they are).

## Versioning

`field_cartography.api.__version__` is semver (currently `1.0.0`). Method
signatures above are the v1 contract; new methods may be added at minor versions,
removals/renames only at a major bump.

## Smoke test

```bash
python -m field_cartography.api.smoke            # auto-picks a known-good id
python -m field_cartography.api.smoke doi:10.1002/ima.70306
```

Exercises every public method once, prints PASS/FAIL per method, exits non-zero on
any failure. Run it before allow-listing the cartography tools.
