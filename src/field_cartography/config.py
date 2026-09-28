from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os
import yaml


@dataclass
class ApiConfig:
    base_url: str
    rate_limit_per_min: int


@dataclass
class StorageConfig:
    data_dir: Path
    graph_dir: Path
    duckdb_path: Path
    cache_dir: Path


@dataclass
class ExpansionConfig:
    default_depth: int
    default_limit_per_paper: int
    default_global_limit: int


@dataclass
class ProvenanceConfig:
    confidence_default: float


@dataclass
class RelevanceConfig:
    min_score: int
    include_terms: list[str]
    exclude_terms: list[str]


@dataclass
class AppConfig:
    project: str
    semantic_scholar: ApiConfig
    openalex: ApiConfig
    crossref: ApiConfig
    storage: StorageConfig
    expansion: ExpansionConfig
    provenance: ProvenanceConfig
    relevance: RelevanceConfig


def load_config(path: str | Path = "cartography.config.yaml") -> AppConfig:
    path = Path(path)
    with path.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    storage = raw.get("storage", {})
    storage_cfg = StorageConfig(
        data_dir=Path(storage.get("data_dir", "data")),
        graph_dir=Path(storage.get("graph_dir", "graph")),
        duckdb_path=Path(storage.get("duckdb_path", "db/duckdb/field_cartography.duckdb")),
        cache_dir=Path(storage.get("cache_dir", "data/cache")),
    )

    return AppConfig(
        project=raw.get("project", "field-cartography"),
        semantic_scholar=ApiConfig(**raw["api"]["semantic_scholar"]),
        openalex=ApiConfig(**raw["api"]["openalex"]),
        crossref=ApiConfig(**raw["api"]["crossref"]),
        storage=storage_cfg,
        expansion=ExpansionConfig(**raw["expansion"]),
        provenance=ProvenanceConfig(**raw["provenance"]),
        relevance=RelevanceConfig(**raw.get("relevance", {})),
    )


def env(key: str, default: str | None = None) -> str | None:
    return os.getenv(key, default)
