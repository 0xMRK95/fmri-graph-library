"""Build a versioned citation graph containing exactly the public catalog papers."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from field_cartography.api import Client
from field_cartography.public_graph import build_public_graph


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", required=True, type=Path)
    parser.add_argument("--edge-file", type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--version", default="v1")
    args = parser.parse_args()
    edge_file = args.edge_file or args.data_dir / "network" / "edges.jsonl"
    client = Client(data_root=args.data_dir, slim=True)
    stats = build_public_graph(
        client,
        edge_file,
        args.output_dir,
        version=args.version,
    )
    print(json.dumps(stats, indent=2))


if __name__ == "__main__":
    main()

