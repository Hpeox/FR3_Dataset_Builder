#!/usr/bin/env python3
"""Build one HDF5 dataset file from one done/aligned raw demo."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from hdf5_builder.context import DATASET_BUILDER_ROOT, DEFAULT_REPO_ROOT, DemoBuildContext
from hdf5_builder.manifest_update import mark_h5_generated
from hdf5_builder.writer import write_hdf5


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True, help="path to demo manifest.json")
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="output .h5 path; defaults to DatasetBuilder/outputs/<demo_id>.h5",
    )
    parser.add_argument(
        "--runtime-root",
        "--repo-root",
        dest="runtime_root",
        type=Path,
        default=DEFAULT_REPO_ROOT,
        help="runtime data root containing runtime_sessions and runtime_frames",
    )
    parser.add_argument("--report", type=Path, default=None, help="optional JSON sidecar report path")
    parser.add_argument("--overwrite", action="store_true", help="replace an existing output .h5")
    parser.add_argument("--min-free-gb", type=float, default=20.0, help="minimum free space required on output filesystem")
    parser.add_argument("--no-update-manifest", action="store_true", help="do not set manifest h5_generated=true after success")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    output_path = args.output or default_output_path(args.manifest)
    ctx = DemoBuildContext(
        manifest_path=args.manifest,
        output_path=output_path,
        repo_root=args.runtime_root,
        report_path=args.report,
        min_free_gb=args.min_free_gb,
    )
    write_hdf5(ctx, overwrite=args.overwrite)
    manifest_updated = False
    if not args.no_update_manifest:
        try:
            mark_h5_generated(ctx.manifest_path)
            manifest_updated = True
        except Exception as exc:
            raise RuntimeError(
                f"HDF5 was generated at {ctx.output_path}, but manifest h5_generated update failed: {exc}"
            ) from exc
    print(
        json.dumps(
            {
                "status": "done",
                "demo_id": ctx.demo_id,
                "output_path": ctx.output_path.as_posix(),
                "report_path": ctx.report_path.as_posix(),
                "total_aligned_rows": ctx.report.total_aligned_rows,
                "exported_rows": ctx.report.exported_rows,
                "warning_count": len(ctx.report.warnings),
                "manifest_updated": manifest_updated,
            },
            indent=2,
            ensure_ascii=True,
        )
    )
    return 0


def default_output_path(manifest_path: Path) -> Path:
    demo_id = manifest_path.resolve().parent.name
    return DATASET_BUILDER_ROOT / "outputs" / f"{demo_id}.h5"


if __name__ == "__main__":
    raise SystemExit(main())
