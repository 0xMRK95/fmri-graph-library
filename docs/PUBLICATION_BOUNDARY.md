# Publication boundary (draft)

The research warehouse and a future public repository have different jobs.
This file records the first, non-destructive cleanup pass; it does **not**
authorize uploading any data or deleting the private archive.

## Keep in the private warehouse

- Downloaded PDF/HTML/Markdown article bodies and extracted figures in the
  sibling `papers-shared/` and `corpus/` directories.
- Raw Semantic Scholar/OpenAlex responses, API caches, classification logs,
  historical JSONL snapshots, and the full citation-edge provenance.
- Any file whose origin, license, or role is not yet verified.

## Candidate for a public release

- Reviewed source code, tests, configuration examples, and a concise workflow.
- A generated, link-only catalog grouped by method category and year. Each
  entry uses stable DOI, arXiv, PMC, or PubMed identifiers when available,
  plus a source-domain link when the warehouse has a non-signed open-copy URL.
- A citation-network export only after checking source terms, provenance,
  canonical IDs, and edge-direction tests. Do not copy raw API payloads by
  default.

Generate the **draft** catalog locally with:

```bash
uv run --frozen python scripts/build_public_catalog.py --output-dir public_catalog
```

The generator reads the canonical classification reducer. It never copies
abstracts or article bodies. Files marked `Source link pending` and papers with
missing titles require metadata repair before publication. The classical graph
analysis category is kept separate from graph *learning*.

## Cleanup order

1. Inventory and checksum the private warehouse; decide what is a source,
   a reproducible derivative, a historical checkpoint, or unknown.
2. Resolve missing catalog metadata and spot-check category assignments.
3. Remove obsolete code artifacts only after proving no active imports or
   documented commands depend on them; keep recovery copies outside the
   public release.
4. Build a fresh, small public repository from a reviewed export rather than
   pushing the multi-gigabyte research Git history.

No upload, history rewrite, or research-data deletion is part of this pass.

The two root-level `test_classify*.py` files are historical batch experiments,
not portable tests; `pytest.ini` now limits automated collection to `tests/`.
The 42-byte `batch_output.txt` console fragment was removed. The many ignored
macOS `._*` files are not part of Git and need no public-release action.
