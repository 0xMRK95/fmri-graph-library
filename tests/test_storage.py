from pathlib import Path
from field_cartography.storage_jsonl import JsonlStore


def test_jsonl_append(tmp_path: Path):
    store = JsonlStore(tmp_path)
    store.append("papers.jsonl", {"paper_id": "x"})
    store.append("papers.jsonl", {"paper_id": "y"})
    text = (tmp_path / "papers.jsonl").read_text(encoding="utf-8")
    assert "\n" in text
    assert "x" in text and "y" in text
