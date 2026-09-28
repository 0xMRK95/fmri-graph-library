"""field_cartography.api.Client — read-only, in-memory query layer.

See src/field_cartography/api/README.md for the contract, latency targets,
stale-data caveats, and deviations from the v31 spec.

Design:
  * Pure sync. v31 wraps calls in asyncio.to_thread (CPU-bound dict lookups).
  * Lazy per-index load; cached in memory; warm() to preload.
  * Never raises on missing data (returns None / []). Raises ValueError only
    on malformed input.
  * No shared mutable state past the cached indexes -> safe under to_thread.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable, Optional

from .models import ExternalIds, PaperRecord
from .reducer import (
    ALL_CATEGORIES,
    CORE_CATEGORIES,
    REDUCER_SPEC,
    reduce_group,
    tier_min_to_int,
)

log = logging.getLogger("field_cartography.api")

_DEFAULT_CORPUS_ROOT = Path("/root/Workspace/PhD/cartography/corpus")

# Index names accepted by warm().
INDEX_NAMES = (
    "aliases",
    "id_map",
    "papers",
    "citations",
    "classifications",
    "authors",
    "venues",
    "corpus",
    "network",
    "titles",
)

_STOPWORDS = frozenset(
    """a an the of for and or to in on with via using based toward towards from
    is are be we our using use new novel approach method methods model models
    network networks via study analysis""".split()
)

_HEX40 = re.compile(r"^[0-9a-f]{40}$")
_ARXIV = re.compile(r"^\d{4}\.\d{4,5}(v\d+)?$")
_OPENALEX = re.compile(r"^W\d+$", re.IGNORECASE)


def _norm_doi(doi: str) -> str:
    d = doi.strip().lower()
    for p in ("https://doi.org/", "http://doi.org/", "doi:"):
        if d.startswith(p):
            d = d[len(p):]
    return d


def _norm_authors(authors: Any) -> list[dict]:
    """Normalize an authors field to a uniform list[{name, authorId?}].

    Source records are inconsistent: some store [{'name': ...}], some store
    bare strings ['Jane Doe', ...].
    """
    out: list[dict] = []
    if not isinstance(authors, list):
        return out
    for a in authors:
        if isinstance(a, str):
            if a.strip():
                out.append({"name": a.strip()})
        elif isinstance(a, dict):
            nm = a.get("name")
            if nm:
                entry = {"name": nm}
                if a.get("authorId"):
                    entry["authorId"] = a["authorId"]
                out.append(entry)
    return out


def _tokenize_title(title: str) -> set[str]:
    toks = re.split(r"[^a-z0-9]+", title.lower())
    return {t for t in toks if len(t) >= 3 and t not in _STOPWORDS}


class Client:
    """Stateful, read-only query client over the cartography data warehouse."""

    def __init__(
        self,
        data_root: Path | None = None,
        corpus_root: Path | None = None,
        slim: bool = False,
    ) -> None:
        if data_root is None:
            # src/field_cartography/api/client.py -> repo root is parents[3]
            data_root = Path(__file__).resolve().parents[3] / "data"
        self.data_root = Path(data_root)
        if corpus_root is None:
            corpus_root = _DEFAULT_CORPUS_ROOT
            if not corpus_root.exists():
                alt = self.data_root.parent.parent / "corpus"
                if alt.exists():
                    corpus_root = alt
        self.corpus_root = Path(corpus_root)
        self.slim = slim

        self._lock = threading.RLock()
        self._loaded: set[str] = set()

        # indexes (populated lazily)
        self._alias: dict[str, str] = {}
        self._idmap: dict[str, str] = {}      # any external id token -> canonical
        self._papers: dict[str, dict] = {}
        self._cls: dict[str, dict] = {}       # canonical -> reduced label dict
        self._out: dict[str, list[str]] = {}  # cites (refs)
        self._in: dict[str, list[str]] = {}   # cited_by (citers)
        self._authors: dict[str, list[str]] = {}
        self._venues: dict[str, list[str]] = {}
        self._corpus: dict[str, dict] = {}
        self._node_type: dict[str, str] = {}
        self._cluster: dict[str, str] = {}
        self._confirmed: set[str] = set()
        self._title_tokens: dict[str, set[str]] = {}
        self._token_index: dict[str, set[str]] = {}
        self._cocite_cache: dict[str, list[tuple[str, int]]] = {}

    # ----------------------------------------------------------------- io
    def _iter_jsonl(self, name: str) -> Iterable[dict]:
        path = self.data_root / name
        if not path.exists():
            log.warning("data file missing: %s", path)
            return
        with path.open() as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    # torn final line during concurrent append -> skip
                    continue

    def canon(self, pid: str) -> str:
        """Resolve an id through the alias chain to its canonical form."""
        self._ensure("aliases")
        seen = 0
        while pid in self._alias and seen < 16:
            pid = self._alias[pid]
            seen += 1
        return pid

    # ------------------------------------------------------------- loaders
    def warm(self, *indexes: str) -> None:
        """Pre-load named indexes (or all if none given)."""
        names = indexes or INDEX_NAMES
        for n in names:
            if n not in INDEX_NAMES:
                raise ValueError(f"unknown index {n!r}; choices: {INDEX_NAMES}")
            self._ensure(n)

    def _ensure(self, name: str) -> None:
        if name in self._loaded:
            return
        with self._lock:
            if name in self._loaded:
                return
            loader = getattr(self, f"_load_{name}")
            log.info("loading index: %s", name)
            loader()
            self._loaded.add(name)

    def _load_aliases(self) -> None:
        for d in self._iter_jsonl("id_aliases.jsonl"):
            a, c = d.get("alias"), d.get("canonical")
            if a and c:
                self._alias[a] = c

    def _load_id_map(self) -> None:
        self._ensure("aliases")
        for d in self._iter_jsonl("id_map.jsonl"):
            canon = self.canon(d.get("canonical_id") or "")
            if not canon:
                continue
            if d.get("doi"):
                self._idmap["doi:" + _norm_doi(d["doi"])] = canon
            if d.get("s2_id"):
                self._idmap["s2:" + str(d["s2_id"])] = canon
            if d.get("arxiv_id"):
                self._idmap["arxiv:" + str(d["arxiv_id"]).lower()] = canon
            if d.get("pubmed_id"):
                self._idmap["pubmed:" + str(d["pubmed_id"])] = canon
            if d.get("mag_id"):
                self._idmap["mag:" + str(d["mag_id"])] = canon
            if d.get("dblp_id"):
                self._idmap["dblp:" + str(d["dblp_id"])] = canon

    def _load_papers(self) -> None:
        self._ensure("aliases")
        slim = self.slim
        # Seeds and citation-expanded papers use different source files and
        # identifier shapes. Neither is a complete metadata table by itself.
        for source in ("discovered_papers.jsonl", "papers.jsonl"):
            for d in self._iter_jsonl(source):
                pid = d.get("paper_id")
                if not pid:
                    continue
                canon = self.canon(pid)
                ext = d.get("external_ids") or d.get("externalIds") or {}
                if not isinstance(ext, dict):
                    ext = {}
                corpus_id = ext.get("CorpusId") or d.get("corpusId")
                rec = {
                    "title": d.get("title"),
                    "authors": _norm_authors(d.get("authors")),
                    "year": d.get("year"),
                    "venue": d.get("venue"),
                    "abstract": None if slim else d.get("abstract"),
                    "citation_count": d.get("citation_count"),
                    "doi": ext.get("DOI") or d.get("doi"),
                    "arxiv": ext.get("ArXiv") or d.get("arxiv_id"),
                    "s2": str(corpus_id) if corpus_id is not None else None,
                    "pmc": ext.get("PubMedCentral"),
                    "pubmed": ext.get("PubMed"),
                    "s2hash": d.get("s2_paper_id") or d.get("paperId"),
                }
                oa = d.get("open_access_pdf") or d.get("openAccessPdf")
                rec["oa_pdf_url"] = oa.get("url") if isinstance(oa, dict) else None

                prev = self._papers.get(canon)
                if prev is None:
                    self._papers[canon] = rec
                else:
                    # Preserve existing metadata; fill gaps from the other source.
                    if not prev.get("abstract") and rec.get("abstract"):
                        prev["abstract"] = rec["abstract"]
                    for key, value in rec.items():
                        if prev.get(key) in (None, "", []) and value not in (None, "", []):
                            prev[key] = value
                    rec = prev

                # Reverse lookup IDs also occur only in one of the two files.
                if rec.get("pmc"):
                    self._idmap["pmc:" + str(rec["pmc"])] = canon
                if rec.get("s2hash"):
                    self._idmap["s2hash:" + str(rec["s2hash"]).lower()] = canon
                if rec.get("s2"):
                    self._idmap.setdefault("s2:" + str(rec["s2"]), canon)
                if rec.get("doi"):
                    self._idmap.setdefault("doi:" + _norm_doi(rec["doi"]), canon)
                if rec.get("arxiv"):
                    self._idmap.setdefault("arxiv:" + str(rec["arxiv"]).lower(), canon)
                if rec.get("pubmed"):
                    self._idmap.setdefault("pubmed:" + str(rec["pubmed"]), canon)

    def _load_classifications(self) -> None:
        self._ensure("aliases")
        groups: dict[str, list[dict]] = defaultdict(list)
        for d in self._iter_jsonl("classifications_7cat.jsonl"):
            pid = d.get("paper_id")
            if pid:
                groups[self.canon(pid)].append(d)
        for canon, rows in groups.items():
            self._cls[canon] = reduce_group(rows)

    def _load_citations(self) -> None:
        self._ensure("aliases")
        out: dict[str, set] = defaultdict(set)
        inc: dict[str, set] = defaultdict(set)
        for d in self._iter_jsonl("citations.jsonl"):
            src, tgt = d.get("from"), d.get("to")
            if not src or not tgt:
                continue
            src, tgt = self.canon(src), self.canon(tgt)
            if src == tgt:
                continue
            out[src].add(tgt)
            inc[tgt].add(src)
        self._out = {k: list(v) for k, v in out.items()}
        self._in = {k: list(v) for k, v in inc.items()}

    def _load_authors(self) -> None:
        # Merge the side-file (authors.jsonl, ~35K rows) with the inline
        # authors on the full papers index for complete coverage.
        self._ensure("aliases")
        self._ensure("papers")
        m: dict[str, set] = defaultdict(set)
        for d in self._iter_jsonl("authors.jsonl"):
            pid, name = d.get("paper_id"), d.get("name")
            if pid and name:
                m[name.strip().lower()].add(self.canon(pid))
        for canon, rec in self._papers.items():
            for a in rec.get("authors") or []:
                name = (a or {}).get("name")
                if name:
                    m[name.strip().lower()].add(canon)
        self._authors = {k: list(v) for k, v in m.items()}

    def _load_venues(self) -> None:
        self._ensure("aliases")
        self._ensure("papers")
        m: dict[str, set] = defaultdict(set)
        for d in self._iter_jsonl("venues.jsonl"):
            pid, ven = d.get("paper_id"), d.get("venue")
            if pid and ven:
                m[ven.strip().lower()].add(self.canon(pid))
        for canon, rec in self._papers.items():
            ven = rec.get("venue")
            if ven:
                m[str(ven).strip().lower()].add(canon)
        self._venues = {k: list(v) for k, v in m.items()}

    def _load_corpus(self) -> None:
        self._ensure("aliases")
        idx = self.corpus_root / "INDEX.jsonl"
        if not idx.exists():
            log.warning("corpus INDEX missing: %s", idx)
            return
        with idx.open() as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                except json.JSONDecodeError:
                    continue
                pid = d.get("paper_id")
                if pid:
                    self._corpus[self.canon(pid)] = d

    def _load_network(self) -> None:
        self._ensure("aliases")
        for d in self._iter_jsonl("network/nodes.jsonl"):
            pid = d.get("paper_id")
            if pid and d.get("node_type"):
                self._node_type[self.canon(pid)] = d["node_type"]
        for d in self._iter_jsonl("confirmed_corpus.jsonl"):
            pid = d.get("paper_id")
            if pid:
                self._confirmed.add(self.canon(pid))
        # clusters.yaml (graph/clusters.yaml)
        clusters = self.data_root.parent / "graph" / "clusters.yaml"
        if clusters.exists():
            try:
                import yaml

                with clusters.open() as f:
                    doc = yaml.safe_load(f) or {}
                for cl in doc.get("clusters", []):
                    cid = cl.get("cluster_id")
                    for node in cl.get("nodes", []) or []:
                        self._cluster[self.canon(node)] = cid
            except Exception as exc:  # noqa: BLE001
                log.warning("clusters.yaml parse failed: %s", exc)

    def _load_titles(self) -> None:
        self._ensure("papers")
        for canon, rec in self._papers.items():
            title = rec.get("title")
            if not title:
                continue
            toks = _tokenize_title(title)
            if not toks:
                continue
            self._title_tokens[canon] = toks
            for t in toks:
                self._token_index.setdefault(t, set()).add(canon)

    def _candidates(self, q: set[str], max_seed_tokens: int = 4) -> set[str]:
        """Gather candidate papers from the RAREST query tokens only.

        Scoring still uses the full query, but seeding from the most
        discriminative tokens keeps high-frequency words ("graph",
        "connectivity") from ballooning the candidate set. A true match
        shares the rare tokens, so recall on real matches is preserved.
        """
        toks = sorted(q, key=lambda t: len(self._token_index.get(t, ())))
        cand: set[str] = set()
        for t in toks[:max_seed_tokens]:
            cand |= self._token_index.get(t, set())
        return cand

    # ------------------------------------------------------------ 5.1 resolve
    def resolve_id(self, raw: str) -> Optional[str]:
        """Resolve any supported identifier to its canonical id, or None.

        Supported: DOI, arXiv, S2 (CorpusId numeric or 40-hex paperId),
        PMC, PubMed. OpenAlex (W...) is recognized but UNRESOLVABLE (no
        OpenAlex ids exist in the corpus) -> returns None.
        """
        if not isinstance(raw, str) or not raw.strip():
            raise ValueError("resolve_id requires a non-empty string")
        s = raw.strip()
        self._ensure("id_map")

        # explicit prefixes
        low = s.lower()
        if low.startswith("doi:"):
            return self._idmap.get("doi:" + _norm_doi(s[4:]))
        if low.startswith("arxiv:"):
            return (self._idmap.get("arxiv:" + low[6:])
                    or self._idmap.get("doi:10.48550/arxiv." + low[6:]))
        if low.startswith("pmc:") or low.startswith("pmcid:"):
            num = low.split(":", 1)[1].lstrip("pmc")
            self._ensure("papers")
            return self._idmap.get("pmc:" + num) or self._idmap.get("pmc:PMC" + num)
        if low.startswith("pmc") and low[3:].isdigit():
            self._ensure("papers")
            return self._idmap.get("pmc:" + s[3:]) or self._idmap.get("pmc:" + s)
        if low.startswith("pubmed:") or low.startswith("pmid:"):
            return self._idmap.get("pubmed:" + low.split(":", 1)[1])
        if low.startswith("s2:"):
            tok = s[3:]
            self._ensure("papers")
            return (self._idmap.get("s2:" + tok)
                    or self._idmap.get("s2hash:" + tok.lower()))

        # bare DOI
        if low.startswith("10.") and "/" in s:
            return self._idmap.get("doi:" + _norm_doi(s))
        # OpenAlex — recognized but unresolvable
        if _OPENALEX.match(s):
            log.info("OpenAlex id %s recognized but no OpenAlex ids exist in corpus", s)
            return None
        # arXiv bare
        if _ARXIV.match(s):
            return (self._idmap.get("arxiv:" + low)
                    or self._idmap.get("doi:10.48550/arxiv." + low))
        # 40-hex S2 paperId
        if _HEX40.match(low):
            self._ensure("papers")
            return self._idmap.get("s2hash:" + low)
        # bare digits: ambiguous corpusId vs pubmed -> try S2 first
        if s.isdigit():
            hit = self._idmap.get("s2:" + s)
            if hit:
                return hit
            self._ensure("papers")
            return self._idmap.get("s2:" + s) or self._idmap.get("pubmed:" + s)

        raise ValueError(f"unrecognized id format: {raw!r}")

    def resolve_by_title(
        self,
        title: str,
        year: int | None = None,
        authors: list[str] | None = None,
        min_score: float = 0.85,
    ) -> Optional[tuple[str, float]]:
        """Fuzzy title match. Score = Jaccard over normalized title tokens
        (set intersection / union, lowercased, alnum tokens len>=3, minus a
        small stopword set). year, if given, must match exactly when the
        candidate has a year; authors nudge ties via surname overlap.
        Returns (canonical_id, score) of the best match >= min_score, else None.
        """
        if not isinstance(title, str) or not title.strip():
            raise ValueError("resolve_by_title requires a non-empty title")
        self._ensure("titles")
        q = _tokenize_title(title)
        if not q:
            return None
        cand = self._candidates(q)
        best: tuple[str, float] | None = None
        surn = {a.split()[-1].lower() for a in (authors or []) if a.split()}
        for c in cand:
            tt = self._title_tokens.get(c)
            if not tt:
                continue
            j = len(q & tt) / len(q | tt)
            if year is not None:
                cy = self._papers.get(c, {}).get("year")
                if cy is not None and int(cy) != int(year):
                    continue
            if surn:
                rec_auth = {
                    (a.get("name", "").split()[-1].lower())
                    for a in self._papers.get(c, {}).get("authors", [])
                    if a.get("name", "").split()
                }
                if surn & rec_auth:
                    j = min(1.0, j + 0.02)
            if best is None or j > best[1]:
                best = (c, j)
        if best and best[1] >= min_score:
            return best
        return None

    # ----------------------------------------------------------- 5.2 metadata
    def _build_record(self, canon: str) -> Optional[PaperRecord]:
        rec = self._papers.get(canon)
        cls = self._cls.get(canon)
        cor = self._corpus.get(canon)
        if rec is None and cls is None and cor is None:
            return None
        rec = rec or {}
        ft_path = None
        has_ft = False
        if cor and cor.get("fulltext_file"):
            p = self.corpus_root / cor["fulltext_file"]
            has_ft = p.exists()
            ft_path = str(p) if has_ft else None
        return PaperRecord(
            canonical_id=canon,
            title=rec.get("title") or (cor or {}).get("title"),
            authors=rec.get("authors") or [],
            year=rec.get("year") or (cor or {}).get("year"),
            venue=rec.get("venue") or (cor or {}).get("venue"),
            abstract=rec.get("abstract"),
            external_ids=ExternalIds(
                doi=rec.get("doi"),
                arxiv=rec.get("arxiv"),
                s2=rec.get("s2"),
                openalex=None,
                pmc=rec.get("pmc"),
                pubmed=rec.get("pubmed"),
            ),
            category=(cls or {}).get("category"),
            category_tier=(cls or {}).get("tier"),
            category_confidence=(cls or {}).get("confidence"),
            node_type=self._node_type.get(canon),
            citation_count=rec.get("citation_count"),
            open_access_pdf_url=rec.get("oa_pdf_url"),
            has_fulltext=has_ft,
            fulltext_path=ft_path,
        )

    def metadata(self, canonical_id: str) -> Optional[PaperRecord]:
        self._ensure("papers")
        self._ensure("classifications")
        self._ensure("corpus")
        return self._build_record(self.canon(canonical_id))

    def metadata_batch(self, ids: list[str]) -> dict[str, PaperRecord]:
        self._ensure("papers")
        self._ensure("classifications")
        self._ensure("corpus")
        out: dict[str, PaperRecord] = {}
        for raw in ids:
            canon = self.canon(raw)
            rec = self._build_record(canon)
            if rec is not None:
                out[raw] = rec
        return out

    # ---------------------------------------------------------- 5.3 citations
    def cites(self, canonical_id: str) -> list[str]:
        self._ensure("citations")
        return list(self._out.get(self.canon(canonical_id), []))

    def cited_by(self, canonical_id: str) -> list[str]:
        self._ensure("citations")
        return list(self._in.get(self.canon(canonical_id), []))

    def co_cited_with(
        self, canonical_id: str, top_k: int = 20, min_overlap: int = 2
    ) -> list[tuple[str, int]]:
        """Papers sharing >= min_overlap citing-papers with the target.
        For each paper P that cites X, every other paper Y that P also cites
        gets +1; the tally is |{P : P cites both X and Y}|. Cached per id.
        """
        self._ensure("citations")
        canon = self.canon(canonical_id)
        if canon in self._cocite_cache:
            ranked = self._cocite_cache[canon]
        else:
            counter: Counter = Counter()
            for p in self._in.get(canon, []):
                for y in self._out.get(p, []):
                    if y != canon:
                        counter[y] += 1
            ranked = sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))
            self._cocite_cache[canon] = ranked
        return [(y, n) for y, n in ranked if n >= min_overlap][:top_k]

    # ----------------------------------------------------------- 5.4 category
    def papers_in_category(
        self,
        category: str,
        year_min: int | None = None,
        year_max: int | None = None,
        node_types: list[str] | None = None,
        tier_min: "int | str | None" = None,
    ) -> list[str]:
        if category not in ALL_CATEGORIES:
            raise ValueError(
                f"unknown category {category!r}; choices: {sorted(ALL_CATEGORIES)}"
            )
        self._ensure("classifications")
        tmin = tier_min_to_int(tier_min)
        need_year = year_min is not None or year_max is not None
        if need_year:
            self._ensure("papers")
        if node_types:
            self._ensure("network")
        out: list[str] = []
        for canon, lab in self._cls.items():
            if lab.get("category") != category:
                continue
            t = lab.get("tier")
            if (t if isinstance(t, int) else -1) < tmin:
                continue
            if need_year:
                y = self._papers.get(canon, {}).get("year")
                if y is None:
                    continue
                if year_min is not None and int(y) < year_min:
                    continue
                if year_max is not None and int(y) > year_max:
                    continue
            if node_types and self._node_type.get(canon) not in node_types:
                continue
            out.append(canon)
        return out

    def category_counts(self, year_min: int | None = None) -> dict[str, int]:
        self._ensure("classifications")
        if year_min is not None:
            self._ensure("papers")
        c: Counter = Counter()
        for canon, lab in self._cls.items():
            cat = lab.get("category")
            if not cat:
                continue
            if year_min is not None:
                y = self._papers.get(canon, {}).get("year")
                if y is None or int(y) < year_min:
                    continue
            c[cat] += 1
        return dict(c)

    # ------------------------------------------------------------- 5.5 search
    def search_titles(
        self, query: str, top_k: int = 20, filters: dict | None = None
    ) -> list[tuple[str, float]]:
        """Lexical title search. Ranking = Jaccard token overlap blended with
        query-coverage (NOT BM25). Optional filters: category, year_min,
        year_max, node_type, has_fulltext.
        """
        if not isinstance(query, str) or not query.strip():
            raise ValueError("search_titles requires a non-empty query")
        self._ensure("titles")
        q = _tokenize_title(query)
        if not q:
            return []
        filters = filters or {}
        if "category" in filters:
            self._ensure("classifications")
        if filters.get("node_type"):
            self._ensure("network")
        if filters.get("has_fulltext") is not None:
            self._ensure("corpus")
        scored: list[tuple[str, float]] = []
        for c in self._candidates(q):
            tt = self._title_tokens.get(c)
            if not tt:
                continue
            shared = len(q & tt)
            jacc = shared / len(q | tt)
            coverage = shared / len(q)
            score = 0.5 * jacc + 0.5 * coverage
            if not self._passes_filters(c, filters):
                continue
            scored.append((c, round(score, 4)))
        scored.sort(key=lambda kv: (-kv[1], kv[0]))
        return scored[:top_k]

    def _passes_filters(self, canon: str, filters: dict) -> bool:
        if "category" in filters:
            if self._cls.get(canon, {}).get("category") != filters["category"]:
                return False
        if filters.get("node_type"):
            if self._node_type.get(canon) != filters["node_type"]:
                return False
        if filters.get("has_fulltext") is not None:
            has = canon in self._corpus and bool(self._corpus[canon].get("fulltext_file"))
            if has != bool(filters["has_fulltext"]):
                return False
        ymin, ymax = filters.get("year_min"), filters.get("year_max")
        if ymin is not None or ymax is not None:
            y = self._papers.get(canon, {}).get("year")
            if y is None:
                return False
            if ymin is not None and int(y) < ymin:
                return False
            if ymax is not None and int(y) > ymax:
                return False
        return True

    # ---------------------------------------------------- 5.6 authors / venues
    def papers_by_author(self, author_id_or_name: str) -> list[str]:
        """Name-based lookup. NOTE: the corpus has NO author disambiguation
        ids; authorId matching is unavailable, so this is exact normalized
        (lowercased/stripped) name match only.
        """
        if not isinstance(author_id_or_name, str) or not author_id_or_name.strip():
            raise ValueError("papers_by_author requires a non-empty string")
        self._ensure("authors")
        return list(self._authors.get(author_id_or_name.strip().lower(), []))

    def papers_in_venue(self, venue: str, year_min: int | None = None) -> list[str]:
        if not isinstance(venue, str) or not venue.strip():
            raise ValueError("papers_in_venue requires a non-empty string")
        self._ensure("venues")
        hits = list(self._venues.get(venue.strip().lower(), []))
        if year_min is not None:
            self._ensure("papers")
            hits = [
                c for c in hits
                if (self._papers.get(c, {}).get("year") is not None
                    and int(self._papers[c]["year"]) >= year_min)
            ]
        return hits

    # ------------------------------------------------------------ 5.7 network
    def node_stats(self, canonical_id: str) -> Optional[dict]:
        self._ensure("citations")
        self._ensure("classifications")
        self._ensure("network")
        canon = self.canon(canonical_id)
        known = (
            canon in self._out or canon in self._in or canon in self._cls
            or canon in self._node_type or canon in self._papers
        )
        if not known:
            return None
        lab = self._cls.get(canon, {})
        cat = lab.get("category")
        cc = self._papers.get(canon, {}).get("citation_count")
        return {
            "in_degree": len(self._in.get(canon, [])),
            "out_degree": len(self._out.get(canon, [])),
            "node_type": self._node_type.get(canon),
            "cluster_id": self._cluster.get(canon),
            "is_core": bool(lab.get("is_core")) or (cat in CORE_CATEGORIES),
            "is_confirmed": canon in self._confirmed,
            "citation_count": cc,
        }

    # ---------------------------------------------------------- 5.8 full-text
    def fulltext_path(self, canonical_id: str) -> Optional[Path]:
        self._ensure("corpus")
        entry = self._corpus.get(self.canon(canonical_id))
        if not entry or not entry.get("fulltext_file"):
            return None
        p = self.corpus_root / entry["fulltext_file"]
        return p if p.exists() else None

    def has_fulltext_index(self) -> dict[str, dict]:
        """Return the full corpus INDEX.jsonl keyed by canonical id."""
        self._ensure("corpus")
        return dict(self._corpus)

    # --------------------------------------------------------- 5.9 diagnostics
    def health(self) -> dict:
        self._ensure("classifications")
        # totals from cheap indexes; papers/citations only if already loaded
        audit = self._read_audit_metric()
        totals = {
            "classifications": len(self._cls),
            "papers": len(self._papers) or self._linecount("discovered_papers.jsonl"),
            "citations": (sum(len(v) for v in self._out.values())
                          if self._out else self._linecount("citations.jsonl")),
            "authors": len(self._authors) or self._linecount("authors.jsonl"),
            "corpus_papers": (len(self._corpus)
                              or self._corpus_linecount()),
        }
        return {
            "totals": totals,
            "last_data_write": self._last_data_write(),
            "audit_metric": audit,
            "layer_version": __import__(
                "field_cartography.api", fromlist=["__version__"]
            ).__version__,
            "data_root": str(self.data_root),
            "corpus_root": str(self.corpus_root),
            "slim": self.slim,
        }

    def reducer_spec(self) -> dict:
        return dict(REDUCER_SPEC)

    # ------------------------------------------------------------- internals
    def _read_audit_metric(self) -> Optional[str]:
        p = self.data_root / "api_audit_metric.json"
        if not p.exists():
            return None
        try:
            return json.loads(p.read_text()).get("audit_metric")
        except Exception:  # noqa: BLE001
            return None

    def _last_data_write(self) -> Optional[str]:
        import datetime

        latest = 0.0
        for n in ("discovered_papers.jsonl", "citations.jsonl",
                  "classifications_7cat.jsonl", "id_aliases.jsonl"):
            p = self.data_root / n
            if p.exists():
                latest = max(latest, p.stat().st_mtime)
        if not latest:
            return None
        return datetime.datetime.utcfromtimestamp(latest).date().isoformat()

    def _linecount(self, name: str) -> int:
        p = self.data_root / name
        if not p.exists():
            return 0
        with p.open("rb") as f:
            return sum(1 for _ in f)

    def _corpus_linecount(self) -> int:
        p = self.corpus_root / "INDEX.jsonl"
        if not p.exists():
            return 0
        with p.open("rb") as f:
            return sum(1 for _ in f)
