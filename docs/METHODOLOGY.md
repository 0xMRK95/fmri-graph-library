# Methodology: Agentic Citation Network Construction

## Overview

This document describes the systematic literature gathering methodology for the survey "Agentic Survey of Graph Learning in fMRI." The approach combines traditional citation snowballing with AI-assisted classification to map the intersection of graph-based methods and functional magnetic resonance imaging (fMRI) analysis.

The key innovation is an **agentic citation network construction** pipeline: an AI-assisted systematic approach that uses large language models for bulk paper triage, enabling comprehensive coverage of a rapidly growing field that would be infeasible to survey manually.

## Seed Selection

The initial paper corpus was assembled from **15 seed queries** designed to cover the intersection of fMRI and graph-based methods across multiple sub-areas:

- Graph Neural Networks + fMRI / functional connectivity
- Geometric deep learning + neuroimaging
- Brain network analysis + graph theory
- Dynamic functional connectivity + graph methods
- Connectome-based predictive modeling
- Topological data analysis + brain networks
- Graph transformers + neuroscience
- Spectral graph methods + fMRI
- Community detection + brain networks
- Manifold learning + functional connectivity

Queries were executed against the **Semantic Scholar Graph API** and **OpenAlex API**, with results deduplicated by canonical ID resolution. This yielded approximately 5,200 initial papers.

## Snowball Expansion Protocol

Citation expansion follows a **breadth-first search (BFS)** strategy from confirmed in-scope papers:

1. **Initialization**: All papers classified as core (categories 1-3 in the taxonomy below) form the initial expansion frontier.

2. **Edge fetching**: For each paper in the frontier, both references (papers it cites) and citations (papers that cite it) are retrieved from the Semantic Scholar Graph API with extended fields (title, abstract, year, venue, authors, external IDs, citation counts, open access PDF URLs).

3. **Edge storage**: Every citation relationship is recorded as a directed edge in `citations.jsonl`, regardless of whether the neighbor is in-scope.

4. **Classification**: Newly discovered papers are classified using the 6-category taxonomy (see below).

5. **Scope-aware frontier**: Only papers classified as **core** (categories 1-3) are added to the BFS queue for further expansion. Secondary and out-of-scope papers are retained as leaf nodes in the citation network.

6. **Depth limiting**: Expansion is limited to 2 hops from seed papers to maintain focus while capturing the essential citation neighborhood.

7. **Deduplication**: Cross-ID resolution prevents re-processing the same paper discovered under different identifiers (DOI, Semantic Scholar CorpusId, ArXiv ID, PubMed ID).

## AI-Assisted Classification

Classification follows a **3-tier strategy** balancing coverage, accuracy, and cost:

### Tier 1: Haiku Bulk Triage

All newly discovered papers are classified in batches of 50 using Claude Haiku (claude-haiku-4-5), reading title and abstract. The model assigns each paper to one of 6 categories with a confidence level (high/medium/low).

### Tier 2: Sonnet PDF Review

Papers classified with low confidence that have an open-access PDF are re-evaluated using Claude Sonnet (claude-sonnet-4-5) with the full PDF document. This resolves cases where the abstract is ambiguous but the methods section is clear.

### Tier 3: Manual Review

Papers that remain uncertain after Tier 2 (no PDF available, or conflicting signals) are flagged for manual review by the survey authors.

### Inter-Model Agreement Audit

A random sample of 100 Haiku-classified papers is independently classified by Sonnet to assess inter-model agreement. The confusion matrix is recorded in `data/audit_sonnet_7cat.json`.

## 6-Category Taxonomy

| # | Category | Scope | Description | Inclusion Criteria |
|---|----------|-------|-------------|-------------------|
| 1 | `fmri_gnn` | Core | GNN/graph-transformers/geometric-DL on fMRI | Applies graph neural networks, graph attention networks, graph transformers, or geometric deep learning to fMRI or functional connectivity data |
| 2 | `fmri_graph_classical` | Core | Classical graph theory on fMRI | Applies graph-theoretic metrics (degree, betweenness, clustering coefficient), community detection, spectral methods, or small-world analysis to fMRI-derived networks |
| 3 | `fmri_geometric_manifold` | Core | Geometric/manifold/TDA on fMRI | Applies manifold learning, Riemannian geometry, topological data analysis, or persistent homology to fMRI data |
| 4 | `fmri_no_graph` | Secondary | fMRI without graph methods | Analyzes fMRI data without any graph or network methods (GLM, univariate analysis, CNN on raw voxels, ICA, seed-based correlation) |
| 5 | `graph_methods_no_fmri` | Secondary | Graph methods, not fMRI | Applies graph/GNN methods to non-brain data, or to non-fMRI brain data (structural MRI, diffusion imaging, EEG-only) |
| 6 | `out_of_scope` | Excluded | Unrelated | Not related to fMRI or graph methods; pure computer vision, NLP, non-neuroscience, EEG/MEG-only without fMRI |

### Key Distinctions

- Categories 1-3 (**core**) represent the survey's primary scope: graph-based analysis of fMRI data
- Categories 4-5 (**secondary**) provide important citation context but are not primary subjects of the review
- If a paper uses BOTH fMRI and EEG, it is classified based on its fMRI component
- "Functional connectivity" with correlation matrices + GNN/GCN → category 1
- "Functional connectivity" with correlation matrices + graph metrics (degree, modularity) → category 2
- "Functional connectivity" with correlation matrices + SVM/RF/CNN (no graph methods) → category 4
- "Network" or "connectivity" in a title does NOT imply graph methods — only classify as core if explicit graph-theoretic or GNN methods are used

## Scope Definition

### Core Papers
Papers applying any graph-based method (GNN, classical graph theory, or geometric/manifold methods) to fMRI data. These are the primary subjects of the survey and are fully expanded in the citation network.

### Secondary Papers
Papers that are either (a) graph methods applied to non-fMRI data, or (b) fMRI analysis without graph methods. These appear in the citation network as context — they are cited by or cite core papers — but are not themselves expanded.

### Excluded Papers
Papers unrelated to both fMRI and graph methods, or EEG/MEG-only studies without an fMRI component.

### Boundary Nodes
Out-of-scope papers that are direct citation neighbors of core/secondary papers. They appear in the network graph as "boundary" nodes to preserve citation topology.

## Citation Network Construction

The final citation network comprises:

- **Nodes**: All core papers + secondary papers + 1-hop boundary neighbors
- **Edges**: Directed citation relationships (A cites B) from Semantic Scholar API
- **Deduplication**: Canonical ID resolution (DOI > S2 CorpusId > OpenAlex ID > title hash) prevents duplicate nodes
- **Node attributes**: Category, node type (core/secondary/boundary), year, venue, citation count, abstract
- **Edge attributes**: Relation type (cites), data source, fetch timestamp

## Data Sources

| Source | Purpose | Fields Used |
|--------|---------|-------------|
| Semantic Scholar Graph API | Primary paper search, citation retrieval, metadata | Title, abstract, year, venue, authors, external IDs, citation counts, references, citations, open access PDF URLs |
| OpenAlex API | Secondary search, metadata enrichment | Title, year, venue, authors, DOI, cited-by count |
| Europe PMC | Abstract retrieval for PubMed-indexed papers | Abstract text |
| Crossref | DOI resolution, metadata verification | Title, year, venue |

## Reproducibility

All pipeline steps are implemented as CLI commands via the `fc` tool:

```
fc init → fc ingest-seeds → fc consolidate-seeds → fc classify →
fc recursive-expand → fc build-network → fc export → fc qc → fc audit
```

### Data Integrity

- All data stored as **append-only JSONL** with provenance metadata (source, fetch timestamp, confidence)
- **API responses are cached** to prevent redundant network requests and ensure reproducibility
- **Cross-ID deduplication** via canonical ID resolution prevents processing the same paper multiple times
- **Run logs** in `data/runs.jsonl` record every pipeline invocation with timestamps and statistics

## Statistics

*To be filled after pipeline completion:*

- Total papers screened: ~50,000
- Papers classified as core (categories 1-3): TBD
- Papers classified as secondary (categories 4-5): TBD
- Total citation edges: TBD
- Network nodes: TBD
- Connected components: TBD
- Haiku-Sonnet agreement rate: TBD

---

## [2026-05-29] Multi-Wave Classification Refinement

The original methodology described a 3-tier classification strategy (Haiku → Sonnet → manual). In practice, the taxonomy and prompt template were **iterated through 9 versions** across multiple waves (Feb-May 2026) as quality issues were discovered. See `docs/PROMPT_HISTORY.md` for the full prompt history.

Key prompt evolutions:
- **V1-V3 (Feb 2026)**: Title-only, binary YES/NO/UNCERTAIN classification. Found to over-classify "network/connectivity" titles as core.
- **V4-V5 (Feb 24, 2026)**: Multi-chunk JSONL output, explicit exclusion of MEG/EEG.
- **V6 (Feb 25, 2026)**: 7-category taxonomy introduced (with `fmri_network_analysis` as a separate intermediate category). Main classification wave used this.
- **V7 (Feb 26, 2026)**: `fmri_network_analysis` retired and redistributed (376 rescued to core, 1,048 demoted to `fmri_no_graph`). Final taxonomy: 6 categories.
- **V8-V9 (Feb 27, 2026)**: Strict 2-condition test introduced (must have BOTH fMRI mention AND graph/geometric method). Explicit exclusion lists for EEG-only, DTI-only, animal studies, reviews.

The taxonomy as described in this document (6 categories) reflects V9.

## [2026-05-29] Two-Stage Title-Only / Title+Abstract Strategy

For papers discovered during citation expansion (post-Feb 2026), classification was split into two stages to manage cost:

**Stage 1 — Title-only screening** (batches of 300, Claude Haiku):
- Coarse-grained classification using titles alone
- Target ~25-35% `uncertain` rate; papers labeled `uncertain` are pushed to Stage 2
- Used for high-volume initial triage

**Stage 2 — Title + abstract** (batches of 50, Claude Haiku or Sonnet):
- Reserved for: (a) high-confidence-needed papers (high network connectivity), (b) Stage 1 `uncertain` papers
- More accurate per-paper but ~6× more tokens

## [2026-05-29] Network-Heuristic Filtering (Core-Connection Score)

After the first round of citation expansion, **176,726 new papers** were discovered through the ~5,466-paper "core" set, dwarfing the original 85K classified corpus. Naive classification of all of these would have been infeasible and largely wasted.

We introduce a **core-connection score** for each unclassified paper, defined as the number of *latest-classified core papers* it is connected to (via inbound or outbound citation). Filtering rule:

| Score | Action |
|---|---|
| 0 true-core links | Skip (likely irrelevant noise) |
| 1 true-core link | Skip or downstream-only |
| ≥2 true-core links | Classify in Stage 1 |
| ≥6 true-core links | Promote directly to Stage 2 (title+abstract) |

Filter effect on 180,379 unclassified papers:
- 117,008 (65%) had **0 true-core links** → skipped
- 49,199 (27%) had **1 link** → skipped
- 14,172 (8%) had **≥2 links** → classified

This reduced LLM classification volume by ~92% while retaining the papers most likely to be in-scope.

## [2026-05-29] False-Positive Core Detection via Duplicate Records

JSONL is append-only by design (see "Important design constraints"). A side benefit: re-classifying a paper produces a new record with a new timestamp. Reading `classifications_7cat.jsonl` with "last record wins" semantics yields the most recent classification.

A quality audit on 2026-05-28 found:
- **5,464 papers** had 2-3 conflicting classification records (~6% of corpus)
- A second pass identified the top "connector" papers in expansion fanout (highest number of new-paper introductions) — these were largely papers with old false-core labels (`fmri_geometric_manifold` for "Symplectic Geometry", `fmri_graph_classical` for "Subgraph centrality in complex networks", insect connectome papers, etc.)
- Applying last-record-wins reduced the apparent core set from 5,466 → **2,918** (-47%)

This pattern is reproducible: any paper whose set of citation neighbors is dominated by domain-wide diversity (rather than survey-domain papers) is likely mislabeled and warrants manual review.

## [2026-05-29] Final Statistics

Pipeline-end counts (as of 2026-05-29, prior to network rebuild):

| Quantity | Count |
|---|---|
| Papers in system (after seed + expansion) | 266,196 |
| Total classification records (append-only) | 91,790 |
| Unique classified papers | 85,846 |
| Papers with multiple classifications | 5,464 |
| **True core (latest classes)** | **2,918** |
| `fmri_gnn` | 1,035 |
| `fmri_graph_classical` | 1,734 |
| `fmri_geometric_manifold` | 149 |
| `fmri_no_graph` (secondary) | 11,063 |
| `graph_methods_no_fmri` (secondary) | 6,010 |
| `out_of_scope` | 65,855 |
| Citation edges | 481,293 |
| Stage 1 batches dispatched | 179 / 492 (final: 180 with stragglers) |
| Network nodes (pre-rebuild) | 212,404 |
| Largest connected component | 211,207 (99.4%) |

Numbers will be updated after the post-Stage 1 network rebuild.
