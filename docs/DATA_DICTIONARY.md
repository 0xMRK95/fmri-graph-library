# Data dictionary

## Nodes (`public_graph/v1/nodes.csv`)

Each row is one paper in the public catalog. The node set is fixed to the
catalog; papers cited by the catalog but not themselves included are absent.

| Field | Type | Meaning |
|---|---|---|
| `paper_id` | string | Canonical internal identifier; unique and stable within this release. |
| `title` | string | Paper title. |
| `year` | integer | Publication year when available. |
| `venue` | string | Journal, conference, or repository label from upstream metadata. |
| `category` | string | Machine-screened method category. |
| `doi` | string | DOI without URL prefix, when available. |
| `arxiv` | string | arXiv identifier, when available. |
| `pubmed` | string | PubMed identifier, when available. |
| `pmc` | string | PubMed Central identifier, when available. |
| `citation_count` | integer | Global citation count reported by the upstream index at collection time. |

The three category values are `fmri_gnn`, `fmri_geometric_manifold`, and
`fmri_graph_classical`. They are machine-generated labels, not a human-verified
systematic-review decision.

## Edges (`public_graph/v1/edges.csv`)

| Field | Type | Meaning |
|---|---|---|
| `source` | string | Citing paper's `paper_id`. |
| `target` | string | Cited paper's `paper_id`. |
| `relation` | string | Always `cites` in version 1. |

All endpoints occur in `nodes.csv`. Edges are directed and unique; self-loops
introduced by identifier reconciliation are removed.

## Exchange formats

- `network.graphml` contains the same node and edge data for Gephi, Gephi Lite,
  Cytoscape, and NetworkX.
- `network.gexf` is the presentation-ready view: it adds a deterministic layout,
  method-family colors, degree-scaled node sizes, and subdued edge styling.
- `network.json` supplies the same styled view to the interactive project map.
- `network.cx` contains the same graph in NDEx CX format.
- `stats.json` records release counts, component statistics, scope, and build
  timestamp.
- `SHA256SUMS` provides file-integrity hashes.
