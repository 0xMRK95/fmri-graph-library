# Workflow Guide: Reproducing the Pipeline

Step-by-step guide to reproduce the full field-cartography pipeline from scratch.

## Prerequisites

```bash
# Python 3.11+ required
uv venv .venv --python 3.11
uv pip install -e .

# Optional: set environment variables
export SEMANTIC_SCHOLAR_API_KEY=...   # faster rate limits
```

## Step 1: Initialize

```bash
uv run fc init
```

Creates directory structure, empty JSONL files, default `seeds.yaml`.

**Output**: `data/`, `graph/`, `db/`, `notes/` directories; `data/seeds.yaml`

## Step 2: Edit Seeds

Edit `data/seeds.yaml` with your seed queries. The default includes queries covering fMRI × graph methods.

## Step 3: Ingest Seeds

```bash
uv run fc ingest-seeds
```

Searches Semantic Scholar (+ OpenAlex fallback) for each seed query and stores results in `data/papers.jsonl`.

**Output**: `data/papers.jsonl`

## Step 4: Consolidate Seeds

```bash
uv run fc consolidate-seeds
```

Collects all papers previously classified as fmri_gnn, joins full metadata from all sources, filters out EEG-only papers, and produces a unified seed list.

**Output**: `data/confirmed_seeds.jsonl`

**Verification**: `wc -l data/confirmed_seeds.jsonl`

## Step 5: Classify with 7-Category Taxonomy

Classification uses a 3-command workflow: **prepare → [agents] → ingest**.

```bash
# 1. Prepare batch files for agent classification
uv run fc classify prepare                    # all unclassified papers
uv run fc classify prepare --source seeds     # seeds only
uv run fc classify prepare --source expansion # expansion-discovered only

# 2. Spawn Claude Code agents (model=haiku) to classify batches
#    Agents read batch files, output JSONL with {paper_id, category} per paper.
#    This step happens interactively in Claude Code (not in Python).

# 3. Ingest agent results
uv run fc classify ingest <results.jsonl>     # append validated results

# 4. Check progress
uv run fc classify status                     # category breakdown + pending count
```

The `prepare` command scans all known papers (seeds + expansion-discovered), skips those already classified, and writes batch JSON files to `data/classify_batches/`.

The `ingest` command reads agent output (JSONL), validates categories, deduplicates against existing classifications, and appends to `classifications_7cat.jsonl`.

Already-classified papers are automatically skipped (resumable).

**Output**: `data/classifications_7cat.jsonl`

## Step 6: Recursive Expansion

```bash
uv run fc recursive-expand --max-depth 2 --batch-size 50
```

BFS-expands the citation network from all core papers (categories 1-4). Fetches refs+cites from Semantic Scholar, stores citation edges, and writes classification batch files for newly discovered papers.

**Output**:
- `data/citations.jsonl` — citation edges
- `data/discovered_papers.jsonl` — grows with new papers
- `data/expand_classify_batches/` — batches for agent classification
- `data/expansion_state.jsonl` — tracks expanded papers

After agents classify the batches, re-run `recursive-expand` to continue with newly discovered core papers.

## Step 7: Build Network

```bash
uv run fc build-network
```

Constructs the final citation network from classified papers and edges. Includes core + secondary papers as nodes, plus 1-hop boundary neighbors.

**Output**:
- `data/network/nodes.jsonl` — all network nodes with metadata + category + node_type
- `data/network/edges.jsonl` — deduplicated citation edges
- `data/network/network_stats.json` — summary statistics

**Verification**: `cat data/network/network_stats.json`

## Step 8: Export

```bash
uv run fc export
```

Exports the network to CSV (Gephi/Cytoscape compatible) and GraphML format.

**Output**:
- `graph/nodes.csv` — node table with Id, Label, Year, Venue, DOI, Category, Node_Type
- `graph/edges.csv` — edge table with Source, Target, Type, Relation
- `graph/network.graphml` — GraphML for NetworkX/Gephi

## Step 9: Quality Check

```bash
uv run fc qc
```

Runs basic quality checks: duplicate IDs, missing metadata, broken edges, category distribution.

## Step 10: Audit

```bash
# Run non-agent checks (EEG scan, category stats, network checks)
uv run fc audit

# Prepare Sonnet audit batches
uv run fc audit --prepare --sample-size 100

# Compute agreement after Sonnet agents classify
uv run fc audit --compute
```

Comprehensive audit:
1. EEG contamination scan on core/secondary papers
2. Category distribution analysis
3. Network connectivity check
4. Duplicate edge detection
5. Sonnet spot-check (inter-model agreement)

**Output**: `data/audit_sonnet_7cat.json` — confusion matrix

**Verification**: Agreement rate >90%

## Full Pipeline

```bash
uv run fc init
uv run fc ingest-seeds
uv run fc consolidate-seeds
uv run fc classify prepare          # → batch files
# ... spawn Claude Code agents ...  # → results.jsonl
uv run fc classify ingest results.jsonl
uv run fc classify status           # verify progress
uv run fc recursive-expand --max-depth 2
# ... classify expansion batches ...
uv run fc classify ingest expansion_results.jsonl
uv run fc build-network
uv run fc export
uv run fc qc
uv run fc audit
```

Note: `classify prepare` and `recursive-expand` produce batch files for external AI agents. Run agents between these steps, ingest results, then continue.

## Resumability

Most commands are resumable:
- `consolidate-seeds`: Idempotent (overwrites output)
- `classify`: Skips already-classified papers
- `recursive-expand`: Skips already-expanded papers (via expansion_state.jsonl)
- `build-network`: Idempotent (overwrites output)

If expansion is interrupted, simply re-run `fc recursive-expand` — it will resume from where it left off.
