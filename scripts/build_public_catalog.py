"""Generate a link-only catalog without copying downloaded papers.

Example: python scripts/build_public_catalog.py --output-dir public_catalog
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from field_cartography.api import Client
from field_cartography.public_catalog import build_catalog


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--data-dir", type=Path, help="Private research-warehouse data directory")
    parser.add_argument("--corpus-dir", type=Path, help="Private readable corpus directory")
    args = parser.parse_args()
    client = Client(data_root=args.data_dir, corpus_root=args.corpus_dir, slim=True)
    counts = build_catalog(client, args.output_dir)
    print(json.dumps({"output_dir": str(args.output_dir), "counts": counts}, indent=2))


if __name__ == "__main__":
    main()
