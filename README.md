# fMRI Graph Library

A searchable literature corpus and citation-network toolkit for graph and
geometric learning in functional MRI.

**Project website:** https://0xmrk95.github.io/fmri-graph-library/

## Paper catalog

**[Browse the current paper catalog](public_catalog/README.md)**

Browse directly [by method](public_catalog/README.md#browse-by-method), [by
publication year](public_catalog/by_year/README.md), [by publication
venue](public_catalog/by_venue/README.md), or [by source
link](public_catalog/by_source/README.md).

The catalog contains 5,770 machine-screened, in-scope papers:

| Area | Papers |
|---|---:|
| Graph and geometric deep learning | 1,204 |
| Geometric, manifold, and topological methods | 405 |
| Classical graph analysis | 4,161 |

It is a link-only index: titles, years, venues, and stable source links are
included, while downloaded articles, abstracts, raw API responses, and private
working data remain outside this repository. The much larger citation
neighborhood contains many out-of-scope records and is therefore not presented
as the paper library.

## What is in this repository

- `src/field_cartography/` — ingestion, identity resolution, citation expansion,
  corpus search, and read-only query APIs.
- `public_catalog/` — the generated, reviewable bibliography.
- `public_graph/v1/` — the first catalog-only citation-graph release.
- `scripts/build_public_catalog.py` — regenerates the public catalog from a
  private local warehouse.
- `scripts/build_public_graph.py` — regenerates the catalog-induced graph.
- `scripts/build_ndex_cx.py` — creates the NDEx-compatible CX release file.
- `tests/` — automated tests for the reusable code.
- `docs/` — methodology, workflow, and publication-boundary documentation.

Internal datasets, downloaded full text, caches, database files, classification
snapshots, batch logs, and one-off research scripts are deliberately excluded.

## Install

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/).

```bash
uv sync
uv run fc --help
```

Optional Neo4j support:

```bash
uv sync --extra neo4j
```

## Regenerate the catalog

Point the command at a private local warehouse; no article text is copied into
the generated output.

```bash
uv run python scripts/build_public_catalog.py \
  --data-dir /path/to/private/data \
  --output-dir public_catalog
```

See [the methodology](docs/METHODOLOGY.md), [the reproducible
workflow](docs/WORKFLOW.md), and [the publication
boundary](docs/PUBLICATION_BOUNDARY.md) for details. Field definitions are in
the [data dictionary](docs/DATA_DICTIONARY.md).

## Regenerate the public graph

Version 1 contains exactly the papers in the public catalog and only citations
whose source and target are both catalog papers. Isolated catalog papers are
retained.

```bash
uv run python scripts/build_public_graph.py \
  --data-dir /path/to/private/data \
  --output-dir public_graph/v1 \
  --version v1
```

## Explore and reuse

- Browse all papers in the [public catalog](public_catalog/README.md).
- Download the catalog-only graph from [public graph v1](public_graph/v1/README.md).
- Open the public GraphML file in Gephi Lite from the project website.
- Create the NDEx upload file with `uv run --extra ndex python scripts/build_ndex_cx.py`.
