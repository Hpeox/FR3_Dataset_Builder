#!/usr/bin/env python3
"""Build HDF5 files for all done/aligned demos in a demos directory."""

from __future__ import annotations

import argparse
import json
import numpy as np
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from hdf5_builder.context import (
    DATASET_ROOT,
    DATASET_BUILDER_ROOT,
    DEFAULT_REPO_ROOT,
    DemoBuildContext,
    read_json,
    required_image_topics,
    resolve_demo_path,
    resolve_repo_path,
    task_metadata_from_manifest,
)
from hdf5_builder.manifest_update import mark_h5_generated
from hdf5_builder.writer import write_hdf5


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--demos-root", type=Path, default=Path("runtime_sessions/demos"))
    parser.add_argument("--output-dir", type=Path, default=DATASET_ROOT)
    parser.add_argument(
        "--runtime-root",
        "--repo-root",
        dest="runtime_root",
        type=Path,
        default=DEFAULT_REPO_ROOT,
        help="runtime data root containing runtime_sessions and runtime_frames",
    )
    parser.add_argument("--overwrite", action="store_true", help="replace existing per-demo HDF5 files")
    parser.add_argument(
        "--skip-existing",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="skip demos whose output exists and manifest has h5_generated=true",
    )
    parser.add_argument("--limit", type=int, default=None, help="maximum number of processable demos to build")
    parser.add_argument("--min-free-gb", type=float, default=20.0)
    parser.add_argument("--no-update-manifest", action="store_true")
    parser.add_argument("--batch-report", type=Path, default=None)
    parser.add_argument("--dry-run", action="store_true", help="validate inputs and paths without writing outputs or manifests")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    repo_root = args.runtime_root.resolve()
    demos_root = resolve_demos_root(args.demos_root, repo_root)
    output_dir = resolve_output_dir(args.output_dir)
    batch_report_path = (args.batch_report.resolve() if args.batch_report else output_dir / "batch_build_report.json")
    if not args.dry_run:
        output_dir.mkdir(parents=True, exist_ok=True)
        batch_report_path.parent.mkdir(parents=True, exist_ok=True)

    summary = {
        "dry_run": bool(args.dry_run),
        "demos_root": demos_root.as_posix(),
        "output_dir": output_dir.as_posix(),
        "processed": 0,
        "succeeded": 0,
        "failed": 0,
        "skipped": 0,
        "checked": 0,
        "results": [],
    }

    processable_seen = 0
    for demo_dir in sorted(demos_root.glob("demo_*")):
        if not demo_dir.is_dir():
            continue
        if skip_reason_for_demo(demo_dir / "manifest.json") is None:
            processable_seen += 1
            if args.limit is not None and processable_seen > args.limit:
                summary["limit_reached"] = True
                break
        result = process_demo(
            demo_dir,
            BatchBuildOptions(
                repo_root=repo_root,
                output_dir=output_dir,
                overwrite=args.overwrite,
                skip_existing=args.skip_existing,
                min_free_gb=args.min_free_gb,
                update_manifest=not args.no_update_manifest,
                dry_run=args.dry_run,
            ),
        )
        record = result.record
        summary["processed"] += int(result.processed)
        if record["status"] == "succeeded":
            summary["succeeded"] += 1
        elif record["status"] == "dry_run_ok":
            summary["checked"] += 1
        elif record["status"] == "failed":
            summary["failed"] += 1
        else:
            summary["skipped"] += 1
        summary["results"].append(record)
        print_status(record)
        if not args.dry_run:
            write_batch_report(batch_report_path, summary)

    if not args.dry_run:
        write_batch_report(batch_report_path, summary)
    print(
        json.dumps(
            {key: summary[key] for key in ("dry_run", "checked", "processed", "succeeded", "failed", "skipped")},
            ensure_ascii=True,
        )
    )
    return 1 if summary["failed"] else 0


@dataclass(frozen=True)
class BatchBuildOptions:
    repo_root: Path
    output_dir: Path
    overwrite: bool = False
    skip_existing: bool = True
    min_free_gb: float = 20.0
    update_manifest: bool = True
    dry_run: bool = False


@dataclass(frozen=True)
class DemoProcessResult:
    record: dict[str, Any]
    processed: bool


def process_demo(demo_dir: Path, options: BatchBuildOptions) -> DemoProcessResult:
    manifest_path = demo_dir / "manifest.json"
    output_path = options.output_dir / f"{demo_dir.name}.h5"
    report_path = options.output_dir / f"{demo_dir.name}.build_report.json"
    base_record = {
        "demo_id": demo_dir.name,
        "manifest_path": manifest_path.as_posix(),
        "output_path": output_path.as_posix(),
        "report_path": report_path.as_posix(),
    }
    skip_reason = skip_reason_for_demo(manifest_path)
    if skip_reason:
        return DemoProcessResult(
            {**base_record, "status": "skipped", "reason": skip_reason},
            processed=False,
        )

    manifest = read_json(manifest_path)
    if options.dry_run:
        try:
            payload = dry_run_check_demo(
                manifest_path,
                output_path,
                report_path,
                options.repo_root,
            )
            record = {
                **base_record,
                "status": "dry_run_ok",
                "would_skip_existing": bool(
                    options.skip_existing
                    and output_path.exists()
                    and bool(manifest.get("h5_generated"))
                ),
                **payload,
            }
        except Exception as exc:
            record = {**base_record, "status": "failed", "error": str(exc)}
        return DemoProcessResult(record, processed=False)

    if (
        options.skip_existing
        and output_path.exists()
        and bool(manifest.get("h5_generated"))
    ):
        return DemoProcessResult(
            {
                **base_record,
                "status": "skipped",
                "reason": "existing_h5_and_manifest_marked",
            },
            processed=False,
        )

    try:
        ctx = DemoBuildContext(
            manifest_path=manifest_path,
            output_path=output_path,
            repo_root=options.repo_root,
            report_path=report_path,
            min_free_gb=options.min_free_gb,
            emit_warnings=False,
        )
        write_hdf5(ctx, overwrite=options.overwrite)
        manifest_updated = False
        if options.update_manifest:
            mark_h5_generated(ctx.manifest_path)
            manifest_updated = True
        record = {
            **base_record,
            "status": "succeeded",
            "exported_rows": ctx.report.exported_rows,
            "warning_count": len(ctx.report.warnings),
            "manifest_updated": manifest_updated,
        }
    except Exception as exc:
        record = {**base_record, "status": "failed", "error": str(exc)}
    return DemoProcessResult(record, processed=True)


def resolve_demos_root(path: Path, repo_root: Path) -> Path:
    resolved = path if path.is_absolute() else repo_root / path
    if not resolved.exists():
        raise RuntimeError(f"--demos-root does not exist: {resolved}")
    if not resolved.is_dir():
        raise RuntimeError(f"--demos-root is not a directory: {resolved}")
    return resolved.resolve()


def resolve_output_dir(path: Path) -> Path:
    if path.is_absolute():
        return path.resolve()
    return (DATASET_BUILDER_ROOT / path).resolve()


def skip_reason_for_demo(manifest_path: Path) -> str | None:
    if not manifest_path.exists():
        return "missing_manifest"
    try:
        manifest = read_json(manifest_path)
    except Exception as exc:
        return f"manifest_read_error: {exc}"
    if manifest.get("status") != "done":
        return f"manifest_status_{manifest.get('status')!r}"
    aligned_manifest_path = manifest_path.parent / "aligned" / "aligned_manifest.json"
    if not aligned_manifest_path.exists():
        return "missing_aligned_manifest"
    try:
        aligned_manifest = read_json(aligned_manifest_path)
    except Exception as exc:
        return f"aligned_manifest_read_error: {exc}"
    if aligned_manifest.get("status") != "done":
        return f"aligned_manifest_status_{aligned_manifest.get('status')!r}"
    return None


def dry_run_check_demo(manifest_path: Path, output_path: Path, report_path: Path, repo_root: Path) -> dict[str, Any]:
    demo_dir = manifest_path.parent
    aligned_dir = demo_dir / "aligned"
    manifest = read_json(manifest_path)
    task_metadata_from_manifest(manifest)
    aligned_manifest = read_json(aligned_dir / "aligned_manifest.json")
    read_json(aligned_dir / "alignment_config.json")

    sources = aligned_manifest.get("sources") or {}
    if dict(manifest.get("npz") or {}) != dict(sources.get("npz") or {}):
        raise RuntimeError("manifest npz paths do not match aligned_manifest.sources.npz")
    sensor_paths = manifest.get("sensor_paths") or {}
    if sensor_paths.get("ft300") != sources.get("ft300s_saved_file"):
        raise RuntimeError("manifest sensor_paths.ft300 does not match aligned_manifest source")
    if sensor_paths.get("xense") != sources.get("xense_saved_file"):
        raise RuntimeError("manifest sensor_paths.xense does not match aligned_manifest source")
    if manifest.get("rosbag_uri") != sources.get("rosbag_uri"):
        raise RuntimeError("manifest rosbag_uri does not match aligned_manifest source")

    manifest_npz = manifest.get("npz") or {}
    npz_paths = {
        key: resolve_demo_path(demo_dir, manifest_npz.get(key), f"npz.{key}")
        for key in ("ft300", "xense", "realsense", "zmq")
    }
    sensor_resolved = {
        key: resolve_repo_path(repo_root, sensor_paths.get(key), f"sensor_paths.{key}")
        for key in ("ft300", "xense")
    }
    rosbag_dir = resolve_demo_path(demo_dir, manifest.get("rosbag_uri"), "rosbag_uri")
    for path in (rosbag_dir / "metadata.yaml", rosbag_dir / "rosbag_0.mcap"):
        if not path.exists():
            raise RuntimeError(f"missing rosbag file: {path}")
    topics = required_image_topics(manifest)
    if not topics:
        raise RuntimeError("manifest does not define required RealSense image topics")

    aligned_index_path = aligned_dir / "aligned_index.npz"
    if not aligned_index_path.exists():
        raise RuntimeError(f"missing aligned_index.npz: {aligned_index_path}")
    with np.load(aligned_index_path, allow_pickle=False) as aligned_index:
        if "t_ns" not in aligned_index:
            raise RuntimeError("aligned_index.npz missing t_ns")
        sample_count = int(len(aligned_index["t_ns"]))
    if sample_count != int(aligned_manifest.get("sample_count")):
        raise RuntimeError(
            f"sample_count mismatch: aligned_manifest={aligned_manifest.get('sample_count')}, "
            f"aligned_index={sample_count}"
        )

    return {
        "sample_count": sample_count,
        "npz_paths": {key: path.as_posix() for key, path in npz_paths.items()},
        "sensor_paths": {key: path.as_posix() for key, path in sensor_resolved.items()},
        "rosbag_uri": rosbag_dir.as_posix(),
        "required_topic_count": len(topics),
    }


def write_batch_report(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")


def print_status(record: dict[str, Any]) -> None:
    if record["status"] == "succeeded":
        print(
            f"{record['demo_id']}: succeeded rows={record.get('exported_rows')} "
            f"warnings={record.get('warning_count')} output={record['output_path']}",
            flush=True,
        )
    elif record["status"] == "dry_run_ok":
        print(
            f"{record['demo_id']}: dry-run ok samples={record.get('sample_count')} "
            f"would_skip_existing={record.get('would_skip_existing')}",
            flush=True,
        )
    elif record["status"] == "failed":
        print(f"{record['demo_id']}: failed error={record.get('error')}", flush=True)
    else:
        print(f"{record['demo_id']}: skipped reason={record.get('reason')}", flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
