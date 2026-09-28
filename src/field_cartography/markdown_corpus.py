from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterator


HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
TITLE_TOKEN_RE = re.compile(r"[^a-z0-9]+")
SECTION_RE = re.compile(
    r"^#{1,6}\s+(?:abstract|introduction|background|related work|methods?|"
    r"methodology|experiments?|results?|discussion|conclusions?|references)\b",
    re.IGNORECASE | re.MULTILINE,
)
CONVERTER_SUFFIXES = {
    "adni",
    "aut",
    "core",
    "epmc",
    "manual",
    "openalex",
    "rescue",
    "s2alt",
    "unpaywall",
}
BACK_MATTER_HEADINGS = (
    "acknowledgements",
    "acknowledgments",
    "bibliography",
    "competing interest",
    "competing interests",
    "conflict of interest",
    "credit authorship",
    "declaration of interest",
    "declaration of competing interest",
    "recommended article",
    "recommended articles",
    "references",
    "related article",
    "related articles",
)
MATCH_START = "<<FC_MATCH>>"
MATCH_END = "<<FC_END>>"


@dataclass(frozen=True)
class Chunk:
    heading: str
    line_start: int
    line_end: int
    text: str


@dataclass(frozen=True)
class SearchHit:
    document_id: int
    paper_id: str
    title: str
    year: int | None
    category: str
    origin: str
    converter_source: str
    evidence_status: str
    source_path: str
    heading: str
    line_start: int
    line_end: int
    score: float
    snippet: str


def _read_jsonl(path: Path) -> Iterator[dict]:
    if not path.exists():
        return
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def load_metadata(data_dir: Path) -> tuple[dict[str, dict], dict[str, str]]:
    papers: dict[str, dict] = {}
    for filename in (
        "papers.jsonl",
        "confirmed_seeds.jsonl",
        "discovered_papers.jsonl",
        "secondary_list.jsonl",
        "corpus_supplemental.jsonl",
    ):
        for record in _read_jsonl(data_dir / filename):
            paper_id = str(record.get("paper_id") or "").lower()
            if not paper_id:
                continue
            current = papers.setdefault(paper_id, {})
            for key in ("paper_id", "doi", "title", "year", "venue"):
                if record.get(key) not in (None, "") and not current.get(key):
                    current[key] = record[key]

    categories: dict[str, str] = {}
    for filename in ("classifications_7cat.jsonl", "corpus_supplemental.jsonl"):
        for record in _read_jsonl(data_dir / filename):
            paper_id = str(record.get("paper_id") or "").lower()
            if paper_id and record.get("category"):
                categories[paper_id] = str(record["category"])
    return papers, categories


def load_yearly_corpus(yearly_dir: Path) -> list[tuple[Path, dict]]:
    """Load one record per case-insensitive paper identity from yearly corpus."""
    index_path = yearly_dir / "INDEX.jsonl"
    fulltext_dir = yearly_dir / "fulltext"
    if not index_path.exists() or not fulltext_dir.exists():
        return []

    actual_paths = {
        path.relative_to(yearly_dir).as_posix().lower(): path
        for path in fulltext_dir.rglob("*.md")
    }
    by_path: dict[str, tuple[tuple[int, int, int, int, int], Path, dict]] = {}
    for record in _read_jsonl(index_path):
        paper_id = str(record.get("paper_id") or "").lower()
        relative = str(record.get("fulltext_file") or "")
        path = actual_paths.get(relative.lower())
        if not paper_id or path is None:
            continue
        actual_relative = path.relative_to(yearly_dir).as_posix()
        identity_rank = 2 if paper_id.startswith("doi:") else int(
            paper_id.startswith("s2:")
        )
        rank = (
            identity_rank,
            int(relative == actual_relative),
            int(bool(record.get("has_metadata"))),
            int(bool(record.get("title"))),
            int(record.get("n_chars") or 0),
        )
        path_key = actual_relative.lower()
        current = by_path.get(path_key)
        if current is None or rank > current[0]:
            by_path[path_key] = (rank, path, record)

    selected: dict[str, tuple[tuple[int, int, int, int, int], Path, dict]] = {}
    for rank, path, record in by_path.values():
        paper_id = str(record.get("paper_id") or "").lower()
        current = selected.get(paper_id)
        if current is None or rank > current[0]:
            selected[paper_id] = (rank, path, record)
    return [(path, record) for _, path, record in selected.values()]


def identity_from_path(source_path: str) -> tuple[str, str, str]:
    """Return canonical key, paper ID, and converter source from a corpus path."""
    directory = Path(source_path).parts[0]
    stem, separator, possible_source = directory.rpartition(".")
    if separator and possible_source in CONVERTER_SUFFIXES:
        identity = stem
        converter_source = possible_source
    else:
        identity = directory
        converter_source = "unknown"

    if identity.startswith("doi_10."):
        raw = identity.removeprefix("doi_")
        registrant, separator, suffix = raw.partition("_")
        doi = f"{registrant}/{suffix}" if separator else registrant
        paper_id = f"doi:{doi.lower()}"
        return paper_id, paper_id, converter_source
    if identity.startswith("s2_"):
        paper_id = f"s2:{identity.removeprefix('s2_').lower()}"
        return paper_id, paper_id, converter_source
    return identity.lower(), "", converter_source


def markdown_title(text: str, fallback: str) -> str:
    for line in text.splitlines():
        match = HEADING_RE.match(line.strip())
        if match and len(match.group(1)) == 1:
            return match.group(2).strip()
        if line.strip():
            break
    return fallback


def normalized_title(title: str) -> str:
    return TITLE_TOKEN_RE.sub(" ", title.lower()).strip()


def evidence_status(text: str, title: str, origin: str) -> str:
    """Conservatively identify yearly records that are unsuitable as evidence."""
    if origin == "drive":
        return "ready"
    if len(text) < 2_000:
        return "short"

    lowered = text.lower()
    if (
        "references & citations" in lowered
        and "loading..." in lowered
        and len(text) < 15_000
    ) or ("current browse context" in lowered and len(text) < 8_000):
        return "stub"
    if any(
        marker in lowered[:5_000]
        for marker in ("access denied", "just a moment...", "captcha", "403 forbidden")
    ):
        return "blocked"

    title_tokens = {
        token for token in normalized_title(title).split() if len(token) >= 5
    }
    if len(title_tokens) >= 3:
        body_title = markdown_title(text, "")
        if body_title:
            body_title_normalized = normalized_title(body_title)
            heading_overlap = sum(
                token in body_title_normalized for token in title_tokens
            ) / len(title_tokens)
            if heading_overlap < 0.5:
                return "title_mismatch"
        head = normalized_title(text[:12_000])
        overlap = sum(token in head for token in title_tokens) / len(title_tokens)
        if overlap < 0.5:
            return "title_mismatch"

    section_count = len(SECTION_RE.findall(text))
    if section_count < 2 and len(text) < 10_000:
        return "thin"
    return "ready"


def chunk_markdown(text: str, max_chars: int = 4_500) -> list[Chunk]:
    """Split Markdown by heading and size while preserving source line ranges."""
    lines = text.splitlines()
    heading_stack: list[str] = []
    chunks: list[Chunk] = []
    current: list[tuple[int, str]] = []
    current_heading = "Document"

    def flush() -> None:
        nonlocal current
        if not current:
            return
        start = 0
        while start < len(current):
            end = start
            size = 0
            while end < len(current):
                next_size = len(current[end][1]) + 1
                if end > start and size + next_size > max_chars:
                    break
                size += next_size
                end += 1
            selected = current[start:end]
            body = "\n".join(line for _, line in selected).strip()
            if body:
                chunks.append(
                    Chunk(
                        heading=current_heading,
                        line_start=selected[0][0],
                        line_end=selected[-1][0],
                        text=body,
                    )
                )
            start = end
        current = []

    for line_number, line in enumerate(lines, start=1):
        match = HEADING_RE.match(line.strip())
        if match:
            flush()
            level = len(match.group(1))
            heading_stack[level - 1 :] = [match.group(2).strip()]
            current_heading = " > ".join(heading_stack)
        current.append((line_number, line))
    flush()
    return chunks


def _quality_score(text: str, chunks: list[Chunk]) -> float:
    character_score = min(len(text), 100_000) / 1_000
    heading_score = min(len(chunks), 40) * 1.5
    replacement_penalty = text.count("\ufffd") * 0.25
    return round(character_score + heading_score - replacement_penalty, 3)


def _create_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        PRAGMA journal_mode = WAL;
        PRAGMA foreign_keys = ON;

        DROP TABLE IF EXISTS chunks_fts;
        DROP TABLE IF EXISTS chunks;
        DROP TABLE IF EXISTS documents;

        CREATE TABLE documents (
            id INTEGER PRIMARY KEY,
            canonical_key TEXT NOT NULL,
            paper_id TEXT NOT NULL DEFAULT '',
            doi TEXT NOT NULL DEFAULT '',
            title TEXT NOT NULL,
            year INTEGER,
            venue TEXT NOT NULL DEFAULT '',
            category TEXT NOT NULL DEFAULT '',
            origin TEXT NOT NULL DEFAULT 'drive',
            source_format TEXT NOT NULL DEFAULT 'markdown',
            is_core INTEGER,
            converter_source TEXT NOT NULL,
            evidence_status TEXT NOT NULL DEFAULT 'ready',
            evidence_ready INTEGER NOT NULL DEFAULT 1,
            source_path TEXT NOT NULL UNIQUE,
            size_bytes INTEGER NOT NULL,
            line_count INTEGER NOT NULL,
            character_count INTEGER NOT NULL,
            sha256 TEXT NOT NULL,
            quality_score REAL NOT NULL,
            metadata_matched INTEGER NOT NULL DEFAULT 0,
            preferred INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE chunks (
            id INTEGER PRIMARY KEY,
            document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
            heading TEXT NOT NULL,
            line_start INTEGER NOT NULL,
            line_end INTEGER NOT NULL,
            text TEXT NOT NULL
        );

        CREATE VIRTUAL TABLE chunks_fts USING fts5(
            title,
            heading,
            text,
            tokenize = 'porter unicode61'
        );

        CREATE INDEX documents_canonical_key_idx ON documents(canonical_key);
        CREATE INDEX documents_paper_id_idx ON documents(paper_id);
        CREATE INDEX documents_category_year_idx ON documents(category, year);
        CREATE INDEX documents_origin_idx ON documents(origin);
        CREATE INDEX chunks_document_id_idx ON chunks(document_id);
        """
    )


def build_index(
    cache_dir: Path,
    database_path: Path,
    data_dir: Path,
    yearly_corpus_dir: Path | None = None,
) -> dict[str, int]:
    papers, categories = load_metadata(data_dir)
    database_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database_path)
    _create_schema(connection)

    document_count = 0
    chunk_count = 0
    matched_metadata = 0
    digest_canonical_keys: dict[str, str] = {}
    title_canonical_keys: dict[str, str] = {}
    source_records: list[tuple[Path, str, str, str, str, dict]] = []
    for path in sorted(cache_dir.rglob("*.md")):
        relative_path = path.relative_to(cache_dir).as_posix()
        canonical_key, inferred_paper_id, converter_source = identity_from_path(
            relative_path
        )
        source_records.append(
            (
                path,
                relative_path,
                "drive",
                canonical_key,
                inferred_paper_id,
                {"source_tag": converter_source, "format": "markdown"},
            )
        )
    if yearly_corpus_dir is not None:
        for path, record in load_yearly_corpus(yearly_corpus_dir):
            paper_id = str(record.get("paper_id") or "").lower()
            relative_path = path.relative_to(yearly_corpus_dir).as_posix()
            source_records.append(
                (
                    path,
                    f"yearly/{relative_path}",
                    "yearly",
                    paper_id,
                    paper_id,
                    record,
                )
            )

    origin_counts: dict[str, int] = {}
    for (
        path,
        source_path,
        origin,
        canonical_key,
        inferred_paper_id,
        source_record,
    ) in source_records:
        joined_metadata = papers.get(inferred_paper_id, {})
        metadata = {
            **source_record,
            **{
                key: value
                for key, value in joined_metadata.items()
                if value not in (None, "")
            },
        }
        paper_id = str(metadata.get("paper_id") or inferred_paper_id).lower()
        doi = str(metadata.get("doi") or "")
        if not doi and paper_id.startswith("doi:"):
            doi = paper_id.removeprefix("doi:")
        metadata_available = bool(joined_metadata) or bool(metadata.get("has_metadata"))
        if metadata_available:
            matched_metadata += 1

        text = path.read_text(encoding="utf-8", errors="replace")
        chunks = chunk_markdown(text)
        title = str(metadata.get("title") or markdown_title(text, path.stem))
        category = categories.get(paper_id, "") or str(metadata.get("topic") or "")
        converter_source = str(metadata.get("source_tag") or "unknown")
        source_format = str(metadata.get("format") or "markdown")
        status = evidence_status(text, title, origin)
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        title_key = normalized_title(title)
        if digest in digest_canonical_keys:
            canonical_key = digest_canonical_keys[digest]
        elif len(title_key) >= 30 and title_key in title_canonical_keys:
            canonical_key = title_canonical_keys[title_key]
        else:
            digest_canonical_keys[digest] = canonical_key
            if len(title_key) >= 30:
                title_canonical_keys[title_key] = canonical_key
        cursor = connection.execute(
            """
            INSERT INTO documents (
                canonical_key, paper_id, doi, title, year, venue, category,
                origin, source_format, is_core, converter_source,
                evidence_status, evidence_ready, source_path,
                size_bytes, line_count,
                character_count, sha256, quality_score, metadata_matched
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                canonical_key,
                paper_id,
                doi.lower(),
                title,
                metadata.get("year"),
                str(metadata.get("venue") or ""),
                category,
                origin,
                source_format,
                None if metadata.get("is_core") is None else int(bool(metadata["is_core"])),
                converter_source,
                status,
                int(status == "ready"),
                source_path,
                path.stat().st_size,
                len(text.splitlines()),
                len(text),
                digest,
                _quality_score(text, chunks),
                int(metadata_available),
            ),
        )
        origin_counts[origin] = origin_counts.get(origin, 0) + 1
        document_id = int(cursor.lastrowid)
        document_count += 1
        for chunk_index, chunk in enumerate(chunks):
            chunk_cursor = connection.execute(
                """
                INSERT INTO chunks (document_id, heading, line_start, line_end, text)
                VALUES (?, ?, ?, ?, ?)
                """,
                (document_id, chunk.heading, chunk.line_start, chunk.line_end, chunk.text),
            )
            searchable_heading = chunk.heading
            heading_parts = chunk.heading.split(" > ")
            if (
                len(heading_parts) > 1
                and normalized_title(heading_parts[0]) == normalized_title(title)
            ):
                searchable_heading = " > ".join(heading_parts[1:])
            connection.execute(
                "INSERT INTO chunks_fts (rowid, title, heading, text) VALUES (?, ?, ?, ?)",
                (
                    int(chunk_cursor.lastrowid),
                    # Avoid turning a title match into one apparent passage per
                    # section. The first chunk still makes title search possible.
                    title if chunk_index == 0 else "",
                    searchable_heading,
                    chunk.text,
                ),
            )
            chunk_count += 1

    connection.execute(
        """
        UPDATE documents
        SET preferred = 1
        WHERE id IN (
            SELECT id FROM (
                SELECT id, ROW_NUMBER() OVER (
                    PARTITION BY canonical_key
                    ORDER BY
                        CASE
                            WHEN paper_id LIKE 'doi:%' THEN 0
                            WHEN paper_id LIKE 's2:%' THEN 1
                            ELSE 2
                        END,
                        evidence_ready DESC,
                        quality_score DESC,
                        character_count DESC,
                        source_path ASC
                ) AS rank
                FROM documents
            ) WHERE rank = 1
        )
        """
    )
    connection.commit()
    preferred_count = connection.execute(
        "SELECT COUNT(*) FROM documents WHERE preferred = 1"
    ).fetchone()[0]
    connection.close()
    return {
        "documents": document_count,
        "preferred_documents": int(preferred_count),
        "chunks": chunk_count,
        "metadata_matches": matched_metadata,
        "origins": origin_counts,
    }


def search_index(
    database_path: Path,
    query: str,
    *,
    limit: int = 10,
    category: str | None = None,
    year_min: int | None = None,
    year_max: int | None = None,
    all_versions: bool = False,
    include_low_quality: bool = False,
    include_back_matter: bool = False,
    per_document: int = 2,
) -> list[SearchHit]:
    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    conditions = ["chunks_fts MATCH ?"]
    parameters: list[object] = [query]
    if not all_versions:
        conditions.append("d.preferred = 1")
    if not include_low_quality:
        conditions.append("d.evidence_ready = 1")
    if not include_back_matter:
        for heading in BACK_MATTER_HEADINGS:
            conditions.append(
                "(lower(c.heading) != ? AND lower(c.heading) NOT LIKE ? "
                "AND lower(c.heading) NOT LIKE ?)"
            )
            parameters.extend(
                [heading, f"% > {heading}", f"% > {heading} > %"]
            )
    if category:
        conditions.append("d.category = ?")
        parameters.append(category)
    if year_min is not None:
        conditions.append("d.year >= ?")
        parameters.append(year_min)
    if year_max is not None:
        conditions.append("d.year <= ?")
        parameters.append(year_max)
    candidate_limit = max(limit * max(per_document, 1) * 10, 100)
    parameters.append(candidate_limit)
    rows = connection.execute(
        f"""
        SELECT
            d.id AS document_id,
            d.paper_id,
            d.title,
            d.year,
            d.category,
            d.origin,
            d.converter_source,
            d.evidence_status,
            d.source_path,
            c.heading,
            c.line_start,
            c.line_end,
            bm25(chunks_fts, 4.0, 2.0, 1.0) AS score,
            highlight(chunks_fts, 0, '<<FC_MATCH>>', '<<FC_END>>') AS title_highlight,
            highlight(chunks_fts, 1, '<<FC_MATCH>>', '<<FC_END>>') AS heading_highlight,
            snippet(chunks_fts, 2, '<<FC_MATCH>>', '<<FC_END>>', ' ... ', 32) AS snippet
        FROM chunks_fts
        JOIN chunks c ON c.id = chunks_fts.rowid
        JOIN documents d ON d.id = c.document_id
        WHERE {' AND '.join(conditions)}
        ORDER BY score ASC, d.year DESC
        LIMIT ?
        """,
        parameters,
    ).fetchall()
    connection.close()
    diversified: list[SearchHit] = []
    counts: dict[int, int] = {}
    for row in rows:
        values = dict(row)
        document_id = int(values["document_id"])
        if counts.get(document_id, 0) >= per_document:
            continue
        if MATCH_START not in values["snippet"]:
            if MATCH_START in values["heading_highlight"]:
                values["snippet"] = values["heading_highlight"]
            else:
                values["heading"] = "Title match"
                values["line_start"] = 1
                values["line_end"] = 1
                values["snippet"] = values["title_highlight"]
        values["snippet"] = values["snippet"].replace(MATCH_START, "[").replace(
            MATCH_END, "]"
        )
        values.pop("title_highlight")
        values.pop("heading_highlight")
        diversified.append(SearchHit(**values))
        counts[document_id] = counts.get(document_id, 0) + 1
        if len(diversified) >= limit:
            break
    return diversified


def corpus_stats(database_path: Path) -> dict:
    connection = sqlite3.connect(database_path)
    stats = {
        "documents": connection.execute("SELECT COUNT(*) FROM documents").fetchone()[0],
        "preferred_documents": connection.execute(
            "SELECT COUNT(*) FROM documents WHERE preferred = 1"
        ).fetchone()[0],
        "chunks": connection.execute("SELECT COUNT(*) FROM chunks").fetchone()[0],
        "metadata_matches": connection.execute(
            "SELECT COUNT(*) FROM documents WHERE metadata_matched = 1"
        ).fetchone()[0],
        "duplicate_versions": connection.execute(
            "SELECT COUNT(*) FROM documents WHERE preferred = 0"
        ).fetchone()[0],
        "short_conversions_under_2000_chars": connection.execute(
            "SELECT COUNT(*) FROM documents WHERE character_count < 2000"
        ).fetchone()[0],
        "evidence_ready": connection.execute(
            "SELECT COUNT(*) FROM documents WHERE evidence_ready = 1"
        ).fetchone()[0],
        "evidence_status": dict(
            connection.execute(
                "SELECT evidence_status, COUNT(*) FROM documents "
                "GROUP BY evidence_status ORDER BY COUNT(*) DESC"
            ).fetchall()
        ),
        "newest_metadata_year": connection.execute(
            "SELECT MAX(year) FROM documents"
        ).fetchone()[0],
        "origins": dict(
            connection.execute(
                "SELECT origin, COUNT(*) FROM documents GROUP BY origin ORDER BY COUNT(*) DESC"
            ).fetchall()
        ),
        "source_formats": dict(
            connection.execute(
                "SELECT source_format, COUNT(*) FROM documents GROUP BY source_format ORDER BY COUNT(*) DESC"
            ).fetchall()
        ),
        "source_tags": dict(
            connection.execute(
                "SELECT converter_source, COUNT(*) FROM documents GROUP BY converter_source ORDER BY COUNT(*) DESC"
            ).fetchall()
        ),
        "categories": dict(
            connection.execute(
                """
                SELECT COALESCE(NULLIF(category, ''), 'unclassified'), COUNT(*)
                FROM documents WHERE preferred = 1 GROUP BY category ORDER BY COUNT(*) DESC
                """
            ).fetchall()
        ),
    }
    connection.close()
    return stats


def list_documents(
    database_path: Path,
    *,
    limit: int = 20,
    category: str | None = None,
    include_unknown_year: bool = False,
) -> list[dict]:
    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    conditions = ["preferred = 1", "evidence_ready = 1"]
    parameters: list[object] = []
    if category:
        conditions.append("category = ?")
        parameters.append(category)
    if not include_unknown_year:
        conditions.append("year IS NOT NULL")
    parameters.append(limit)
    rows = connection.execute(
        f"""
        SELECT id, paper_id, title, year, category, origin, converter_source,
               evidence_status,
               source_path,
               character_count, quality_score
        FROM documents
        WHERE {' AND '.join(conditions)}
        ORDER BY COALESCE(year, 0) DESC, title ASC
        LIMIT ?
        """,
        parameters,
    ).fetchall()
    connection.close()
    return [dict(row) for row in rows]


def resolve_document(database_path: Path, selector: str) -> dict:
    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    if selector.isdigit():
        row = connection.execute(
            "SELECT * FROM documents WHERE id = ?", (int(selector),)
        ).fetchone()
    else:
        normalized = selector.lower()
        row = connection.execute(
            """
            SELECT * FROM documents
            WHERE lower(paper_id) = ? OR lower(source_path) = ?
            ORDER BY preferred DESC, quality_score DESC
            LIMIT 1
            """,
            (normalized, normalized),
        ).fetchone()
    connection.close()
    if row is None:
        raise KeyError(f"No corpus document matches {selector!r}")
    return dict(row)


def read_document_lines(
    cache_dir: Path,
    document: dict,
    *,
    start: int = 1,
    end: int | None = None,
) -> list[str]:
    source_path = str(document["source_path"])
    if source_path.startswith("yearly/"):
        path = cache_dir.parent / source_path
    else:
        path = cache_dir / source_path
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    line_end = min(end or len(lines), len(lines))
    line_start = max(start, 1)
    return [
        f"{number:>6}  {lines[number - 1]}"
        for number in range(line_start, line_end + 1)
    ]


def hit_as_dict(hit: SearchHit) -> dict:
    return asdict(hit)
