#!/usr/bin/env python3
"""Build one cold-storage archive from one done/aligned raw demo."""

from __future__ import annotations

import argparse
from pathlib import Path

from archive_builder.builder import build_archive
from archive_builder.context import DEFAULT_REPO_ROOT, SINGLE_ARCHIVE_ROOT, ArchiveContext
from archive_builder.dry_run import dry_run_check_archive


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True, help="path to demo manifest.json")
    parser.add_argument(
        "--runtime-root",
        "--repo-root",
        dest="runtime_root",
        type=Path,
        default=DEFAULT_REPO_ROOT,
        help="runtime data root containing runtime_sessions and runtime_frames",
    )
    parser.add_argument("--archive-dir", type=Path, default=SINGLE_ARCHIVE_ROOT)
    parser.add_argument("--overwrite", action="store_true", help="replace existing archive artifacts")
    parser.add_argument("--keep-staging", action="store_true", help="keep per-demo staging directory after completion")
    parser.add_argument("--dry-run", action="store_true", help="validate archive inputs and paths without writing artifacts")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    ctx = ArchiveContext(
        manifest_path=args.manifest,
        archive_dir=args.archive_dir,
        repo_root=args.runtime_root,
    )
    if args.dry_run:
        payload = dry_run_check_archive(ctx, overwrite=args.overwrite)
        print(
            f"{ctx.demo_id}: dry-run ok target_conflict={payload['target_conflict']} "
            f"archive={payload['archive_path']}",
            flush=True,
        )
        return 0
    payload = build_archive(
        ctx,
        overwrite=args.overwrite,
        keep_staging=args.keep_staging,
        update_manifest=False,
    )
    print(
        f"{payload['demo_id']}: succeeded archive={payload['archive_path']} "
        f"metadata={ctx.archive_json_path.as_posix()}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
