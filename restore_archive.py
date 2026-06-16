#!/usr/bin/env python3
"""Restore a DatasetBuilder cold-storage archive."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from archive_builder.restore import restore_archive


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive-json", type=Path, default=None)
    parser.add_argument("--zip", dest="zip_path", type=Path, default=None)
    parser.add_argument("--restore-root", type=Path, default=None)
    parser.add_argument("--force", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    result = restore_archive(args.archive_json, args.zip_path, args.restore_root, args.force)
    print(json.dumps(result, indent=2, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

