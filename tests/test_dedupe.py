from pathlib import Path
from field_cartography.pipeline.dedupe import find_duplicate_paper_ids


def test_find_duplicate_paper_ids(tmp_path: Path):
    data_dir = tmp_path
    (data_dir / "papers.jsonl").write_text(
        '{"paper_id":"a"}\n{"paper_id":"b"}\n{"paper_id":"a"}\n',
        encoding="utf-8",
    )
    dups = find_duplicate_paper_ids(data_dir)
    assert dups == ["a"]
