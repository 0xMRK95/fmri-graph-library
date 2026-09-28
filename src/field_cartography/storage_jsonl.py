"""Append-only JSONL storage and shared I/O utilities."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


def now_iso() -> str:
    """Return current UTC time as ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


def read_jsonl(path: Path, *, strict: bool = True) -> Iterator[dict[str, Any]]:
    """Iterate over records in a JSONL file, skipping blank lines.

    Yields nothing if the file does not exist.
    When strict=False, silently skips malformed JSON lines.
    """
    if not path.exists():
        return
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                if strict:
                    raise


class JsonlStore:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self.data_dir.mkdir(parents=True, exist_ok=True)

    def append(self, filename: str, obj: dict[str, Any]) -> None:
        path = self.data_dir / filename
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(obj, ensure_ascii=False) + "\n")

    def log_run(self, action: str, meta: dict[str, Any]) -> None:
        payload = {"action": action, "ts": now_iso(), **meta}
        self.append("runs.jsonl", payload)

    def log_query(self, source: str, query: str, meta: dict[str, Any]) -> None:
        payload = {"source": source, "query": query, "ts": now_iso(), **meta}
        self.append("queries.jsonl", payload)
