from __future__ import annotations

from pathlib import Path

from ..storage_jsonl import read_jsonl


def find_duplicate_paper_ids(data_dir: Path) -> list[str]:
    seen = set()
    dups = set()
    for p in read_jsonl(data_dir / "papers.jsonl"):
        pid = p.get("paper_id")
        if not pid:
            continue
        if pid in seen:
            dups.add(pid)
        else:
            seen.add(pid)
    return sorted(dups)
