"""Build the styled GEXF used by the public Gephi Lite link."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from field_cartography.visualization import build_gexf


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-dir", type=Path, default=Path("public_graph/v1"))
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    stats = build_gexf(
        args.release_dir / "nodes.csv",
        args.release_dir / "edges.csv",
        args.release_dir / "network.gexf",
        json_path=args.release_dir / "network.json",
        seed=args.seed,
    )
    print(json.dumps(stats, indent=2))


if __name__ == "__main__":
    main()
