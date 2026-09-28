from __future__ import annotations

from pathlib import Path
import duckdb
from typing import Iterable


class DuckDBStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.con = duckdb.connect(str(self.path))
        self._init()

    def _init(self) -> None:
        self.con.execute(
            """
            CREATE TABLE IF NOT EXISTS papers (
                paper_id TEXT PRIMARY KEY,
                doi TEXT,
                corpus_id TEXT,
                openalex_id TEXT,
                title TEXT,
                year INTEGER
            );
            """
        )
        self.con.execute(
            """
            CREATE TABLE IF NOT EXISTS citations (
                source_id TEXT,
                target_id TEXT,
                UNIQUE(source_id, target_id)
            );
            """
        )
        self.con.execute(
            """
            CREATE TABLE IF NOT EXISTS processed (
                paper_id TEXT PRIMARY KEY
            );
            """
        )

    def paper_exists(self, paper_id: str) -> bool:
        return self.con.execute(
            "SELECT 1 FROM papers WHERE paper_id = ? LIMIT 1", [paper_id]
        ).fetchone() is not None

    def citation_exists(self, source_id: str, target_id: str) -> bool:
        return self.con.execute(
            "SELECT 1 FROM citations WHERE source_id = ? AND target_id = ? LIMIT 1",
            [source_id, target_id],
        ).fetchone() is not None

    def mark_processed(self, paper_id: str) -> None:
        self.con.execute(
            "INSERT OR IGNORE INTO processed(paper_id) VALUES (?)", [paper_id]
        )

    def is_processed(self, paper_id: str) -> bool:
        return self.con.execute(
            "SELECT 1 FROM processed WHERE paper_id = ? LIMIT 1", [paper_id]
        ).fetchone() is not None

    def add_paper(self, paper_id: str, doi: str | None, corpus_id: str | None, openalex_id: str | None, title: str | None, year: int | None) -> None:
        self.con.execute(
            """
            INSERT OR IGNORE INTO papers(paper_id, doi, corpus_id, openalex_id, title, year)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [paper_id, doi, corpus_id, openalex_id, title, year],
        )

    def add_citation(self, source_id: str, target_id: str) -> None:
        self.con.execute(
            "INSERT OR IGNORE INTO citations(source_id, target_id) VALUES (?, ?)",
            [source_id, target_id],
        )

    def iter_paper_ids(self) -> Iterable[str]:
        rows = self.con.execute("SELECT paper_id FROM papers").fetchall()
        for (pid,) in rows:
            yield pid

    def close(self) -> None:
        self.con.close()
