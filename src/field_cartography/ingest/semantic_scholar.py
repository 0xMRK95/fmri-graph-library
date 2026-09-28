from __future__ import annotations

from pathlib import Path
import hashlib
import json
import httpx
from typing import Any
from datetime import datetime, timezone

from ..rate_limit import TokenBucket, backoff_sleep
from ..config import env

FIELDS = "paperId,title,year,venue,authors,externalIds,corpusId,citationCount,referenceCount"


class SemanticScholarClient:
    def __init__(self, base_url: str, rate_limit_per_min: int, cache_dir: Path) -> None:
        self.base_url = base_url.rstrip("/")
        self.bucket = TokenBucket(rate_limit_per_min)
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.api_key = env("SEMANTIC_SCHOLAR_API_KEY")
        # Persistent HTTP client for connection reuse
        headers = {}
        if self.api_key:
            headers["x-api-key"] = self.api_key
        self._client = httpx.Client(
            timeout=30.0,
            headers=headers,
            limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "SemanticScholarClient":
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    def _cache_path(self, key: str) -> Path:
        digest = hashlib.md5(key.encode()).hexdigest()
        return self.cache_dir / f"s2_{digest}.json"

    def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        key = path + "_" + json.dumps(params, sort_keys=True)
        cache_path = self._cache_path(key)
        if cache_path.exists():
            return json.loads(cache_path.read_text(encoding="utf-8"))

        for attempt in range(5):
            self.bucket.take(1)
            try:
                resp = self._client.get(f"{self.base_url}{path}", params=params)
                if resp.status_code == 429 or resp.status_code >= 500:
                    backoff_sleep(attempt)
                    continue
                resp.raise_for_status()
                data = resp.json()
                cache_path.write_text(json.dumps(data), encoding="utf-8")
                return data
            except httpx.HTTPError:
                backoff_sleep(attempt)
        return {}

    def _post(self, path: str, body: dict[str, Any], params: dict[str, Any] | None = None) -> Any:
        key = path + "_" + json.dumps(body, sort_keys=True)
        cache_path = self._cache_path(key)
        if cache_path.exists():
            return json.loads(cache_path.read_text(encoding="utf-8"))

        for attempt in range(5):
            self.bucket.take(1)
            try:
                resp = self._client.post(
                    f"{self.base_url}{path}",
                    json=body,
                    params=params or {},
                    headers={"Content-Type": "application/json"},
                )
                if resp.status_code == 429 or resp.status_code >= 500:
                    backoff_sleep(attempt)
                    continue
                resp.raise_for_status()
                data = resp.json()
                cache_path.write_text(json.dumps(data), encoding="utf-8")
                return data
            except httpx.HTTPError:
                backoff_sleep(attempt)
        return {}

    def search(
        self, query: str, limit: int = 1, fields: str | None = None
    ) -> list[dict[str, Any]]:
        data = self._get(
            "/paper/search",
            {"query": query, "limit": limit, "fields": fields or FIELDS},
        )
        return data.get("data", [])

    def get_paper(self, paper_id: str, fields: str | None = None) -> dict[str, Any]:
        return self._get(
            f"/paper/{paper_id}", {"fields": fields or FIELDS + ",references,citations"}
        )

    def get_references(self, paper_id: str, limit: int = 100, fields: str | None = None) -> list[dict[str, Any]]:
        data = self._get(
            f"/paper/{paper_id}/references",
            {"limit": limit, "fields": fields or FIELDS},
        )
        refs = data.get("data") or []
        return [r.get("citedPaper") for r in refs if r and r.get("citedPaper")]

    def get_citations(self, paper_id: str, limit: int = 100, fields: str | None = None) -> list[dict[str, Any]]:
        data = self._get(
            f"/paper/{paper_id}/citations",
            {"limit": limit, "fields": fields or FIELDS},
        )
        cites = data.get("data") or []
        return [c.get("citingPaper") for c in cites if c and c.get("citingPaper")]

    @staticmethod
    def provenance() -> dict[str, Any]:
        return {
            "source": "semantic_scholar",
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        }
