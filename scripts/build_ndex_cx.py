"""Convert a public graph release to NDEx-compatible CX and optionally upload it."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
from pathlib import Path


def _value(row: dict[str, str], key: str) -> str | int | None:
    value = row.get(key, "").strip()
    if not value:
        return None
    if key in {"year", "citation_count"}:
        return int(value)
    return value


def build_cx(nodes_path: Path, edges_path: Path, output_path: Path, version: str) -> object:
    try:
        from ndex2.nice_cx_network import NiceCXNetwork
    except ImportError as error:
        raise SystemExit("Install the NDEx export extra: uv sync --extra ndex") from error

    network = NiceCXNetwork()
    network.set_network_attribute("name", f"fMRI Graph Library — public graph {version}")
    network.set_network_attribute(
        "description",
        "Directed citation graph induced by the public fMRI Graph Library catalog.",
    )
    network.set_network_attribute("version", version)
    network.set_network_attribute("scope", "catalog papers only")

    node_ids: dict[str, int] = {}
    with nodes_path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            paper_id = row["paper_id"]
            node_id = network.create_node(
                node_name=row["title"] or paper_id,
                node_represents=paper_id,
            )
            node_ids[paper_id] = node_id
            network.set_node_attribute(node_id, "paper_id", paper_id)
            for key in (
                "year",
                "venue",
                "category",
                "doi",
                "arxiv",
                "pubmed",
                "pmc",
                "citation_count",
            ):
                value = _value(row, key)
                if value is not None:
                    data_type = "integer" if isinstance(value, int) else "string"
                    network.set_node_attribute(node_id, key, value, type=data_type)

    with edges_path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            network.create_edge(
                edge_source=node_ids[row["source"]],
                edge_target=node_ids[row["target"]],
                edge_interaction=row.get("relation") or "cites",
            )

    cx = network.to_cx(log_to_stdout=False)
    output_path.write_text(json.dumps(cx, ensure_ascii=False), encoding="utf-8")
    return network


def update_checksums(release_dir: Path) -> None:
    files = [
        "nodes.csv",
        "edges.csv",
        "network.graphml",
        "network.gexf",
        "network.json",
        "network.cx",
        "stats.json",
        "README.md",
    ]
    lines = []
    for filename in files:
        digest = hashlib.sha256((release_dir / filename).read_bytes()).hexdigest()
        lines.append(f"{digest}  {filename}")
    (release_dir / "SHA256SUMS").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-dir", type=Path, default=Path("public_graph/v1"))
    parser.add_argument("--version", default="v1")
    parser.add_argument("--upload", action="store_true")
    parser.add_argument("--server", default="https://public.ndexbio.org")
    args = parser.parse_args()

    output = args.release_dir / "network.cx"
    network = build_cx(
        args.release_dir / "nodes.csv",
        args.release_dir / "edges.csv",
        output,
        args.version,
    )
    update_checksums(args.release_dir)
    print(f"Wrote {output}")

    if args.upload:
        username = os.getenv("NDEX_USERNAME")
        password = os.getenv("NDEX_PASSWORD")
        if not username or not password:
            raise SystemExit("Set NDEX_USERNAME and NDEX_PASSWORD before using --upload")
        uuid = network.upload_to(args.server, username, password)
        print(f"NDEx network: {args.server}/viewer/networks/{uuid}")


if __name__ == "__main__":
    main()
