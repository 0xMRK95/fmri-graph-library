from __future__ import annotations

from dataclasses import dataclass
import os

try:
    from neo4j import GraphDatabase  # type: ignore
except Exception:  # pragma: no cover
    GraphDatabase = None


@dataclass
class Neo4jConfig:
    uri: str
    user: str
    password: str


class Neo4jClient:
    def __init__(self) -> None:
        uri = os.getenv("NEO4J_URI")
        user = os.getenv("NEO4J_USER")
        password = os.getenv("NEO4J_PASSWORD")
        if not (uri and user and password):
            raise RuntimeError("Neo4j environment variables not configured")
        if GraphDatabase is None:
            raise RuntimeError("neo4j driver not installed")
        self.driver = GraphDatabase.driver(uri, auth=(user, password))

    def close(self) -> None:
        self.driver.close()

    def upsert_paper(self, paper_id: str, title: str | None, year: int | None) -> None:
        with self.driver.session() as session:
            session.run(
                "MERGE (p:Paper {id: $id}) SET p.title = $title, p.year = $year",
                id=paper_id,
                title=title,
                year=year,
            )

    def upsert_citation(self, source_id: str, target_id: str) -> None:
        with self.driver.session() as session:
            session.run(
                "MERGE (a:Paper {id: $a}) MERGE (b:Paper {id: $b}) MERGE (a)-[:CITES]->(b)",
                a=source_id,
                b=target_id,
            )
