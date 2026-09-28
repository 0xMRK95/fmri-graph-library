# Read-only query API

`field_cartography.api.Client` provides lazy, read-only access to a local
field-cartography warehouse. It performs no network requests and does not
modify source files.

```python
from pathlib import Path
from field_cartography.api import Client

client = Client(
    data_root=Path("/path/to/warehouse/data"),
    corpus_root=Path("/path/to/fulltext/corpus"),
    slim=True,
)

paper = client.metadata("doi:10.example/article")
matches = client.search_titles("dynamic functional connectivity")
```

Both paths are optional. `data_root` defaults to the repository's untracked
`data/` directory. `corpus_root` defaults to a sibling `corpus/` directory and
can also be set with `FIELD_CARTOGRAPHY_CORPUS_ROOT`.

The API returns `None` or an empty collection when a record is absent. It raises
`ValueError` only for malformed input. Indexes are loaded on first use and then
held in memory; call `warm()` to preload them.

Run the smoke test against a populated local warehouse with:

```bash
uv run python -m field_cartography.api.smoke
```
