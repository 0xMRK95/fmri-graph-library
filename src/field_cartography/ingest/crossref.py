from __future__ import annotations

from pathlib import Path
import json
import httpx
from typing import Any
from datetime import datetime, timezone

from ..rate_limit import TokenBucket, backoff_sleep


class CrossrefClient:
    def __init__(self, base_url: str, rate_limit_per_min: int, cache_dir: Path) -> None:
        self.base_url = base_url.rstrip("/")
        self.bucket = TokenBucket(rate_limit_per_min)
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _cache_path(self, key: str) -> Path:
        safe = key.replace("/", "_")
        return self.cache_dir / f"cr_{safe}.json"

    def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        key = path + "_" + json.dumps(params, sort_keys=True)
        cache_path = self._cache_path(str(abs(hash(key))))
        if cache_path.exists():
            return json.loads(cache_path.read_text(encoding="utf-8"))

        for attempt in range(5):
            self.bucket.take(1)
            try:
                with httpx.Client(timeout=30.0) as client:
                    resp = client.get(f"{self.base_url}{path}", params=params)
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

    def resolve_doi(self, doi: str) -> dict[str, Any]:
        return self._get(f"/works/{doi}", {})

    @staticmethod
    def provenance() -> dict[str, Any]:
        return {
            "source": "crossref",
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        }
