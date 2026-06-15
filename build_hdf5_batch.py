#!/usr/bin/env python3
"""Build HDF5 files for all done/aligned demos in a demos directory."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from hdf5_builder.context import (
    DATASET_BUILDER_ROOT,
    DEFAULT_REPO_ROOT,
    EXTERNAL_DATASET_ROOT,
    DemoBuildContext,
    read_json,
    require_allowed_output_path,
)
from hdf5_builder.manifest_update import mark_h5_generated
from hdf5_builder.writer import write_hdf5


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--demos-root", type=Path, default=Path("runtime_sessions/demos"))
    parser.add_argument("--output-dir", type=Path, default=EXTERNAL_DATASET_ROOT)
    parser.add_argument("--repo-root", type=Path, default=DEFAULT_REPO_ROOT)
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
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    repo_root = args.repo_root.resolve()
    demos_root = resolve_demos_root(args.demos_root, repo_root)
    output_dir = resolve_output_dir(args.output_dir)
    batch_report_path = (args.batch_report.resolve() if args.batch_report else output_dir / "batch_build_report.json")
    require_allowed_output_path(output_dir, "--output-dir")
    require_allowed_output_path(batch_report_path, "--batch-report")
    output_dir.mkdir(parents=True, exist_ok=True)
    batch_report_path.parent.mkdir(parents=True, exist_ok=True)

    summary = {
        "demos_root": demos_root.as_posix(),
        "output_dir": output_dir.as_posix(),
        "processed": 0,
        "succeeded": 0,
        "failed": 0,
        "skipped": 0,
        "results": [],
    }

    processable_seen = 0
    for demo_dir in sorted(demos_root.glob("demo_*")):
        manifest_path = demo_dir / "manifest.json"
        output_path = output_dir / f"{demo_dir.name}.h5"
        report_path = output_dir / f"{demo_dir.name}.build_report.json"
        base_record = {
            "demo_id": demo_dir.name,
            "manifest_path": manifest_path.as_posix(),
            "output_path": output_path.as_posix(),
            "report_path": report_path.as_posix(),
        }
        skip_reason = skip_reason_for_demo(manifest_path)
        if skip_reason:
            record = {**base_record, "status": "skipped", "reason": skip_reason}
            summary["skipped"] += 1
            summary["results"].append(record)
            print_status(record)
            write_batch_report(batch_report_path, summary)
            continue

        processable_seen += 1
        if args.limit is not None and processable_seen > args.limit:
            summary["limit_reached"] = True
            break

        manifest = read_json(manifest_path)
        if args.skip_existing and output_path.exists() and bool(manifest.get("h5_generated")):
            record = {**base_record, "status": "skipped", "reason": "existing_h5_and_manifest_marked"}
            summary["skipped"] += 1
            summary["results"].append(record)
            print_status(record)
            write_batch_report(batch_report_path, summary)
            continue

        summary["processed"] += 1
        try:
            ctx = DemoBuildContext(
                manifest_path=manifest_path,
                output_path=output_path,
                repo_root=repo_root,
                report_path=report_path,
                min_free_gb=args.min_free_gb,
                emit_warnings=False,
            )
            write_hdf5(ctx, overwrite=args.overwrite)
            manifest_updated = False
            if not args.no_update_manifest:
                mark_h5_generated(ctx.manifest_path)
                manifest_updated = True
            record = {
                **base_record,
                "status": "succeeded",
                "exported_rows": ctx.report.exported_rows,
                "warning_count": len(ctx.report.warnings),
                "manifest_updated": manifest_updated,
            }
            summary["succeeded"] += 1
        except Exception as exc:
            record = {**base_record, "status": "failed", "error": str(exc)}
            summary["failed"] += 1
        summary["results"].append(record)
        print_status(record)
        write_batch_report(batch_report_path, summary)

    write_batch_report(batch_report_path, summary)
    print(json.dumps({key: summary[key] for key in ("processed", "succeeded", "failed", "skipped")}, ensure_ascii=True))
    return 1 if summary["failed"] else 0


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


def write_batch_report(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")


def print_status(record: dict[str, Any]) -> None:
    if record["status"] == "succeeded":
        print(
            f"{record['demo_id']}: succeeded rows={record.get('exported_rows')} "
            f"warnings={record.get('warning_count')} output={record['output_path']}",
            flush=True,
        )
    elif record["status"] == "failed":
        print(f"{record['demo_id']}: failed error={record.get('error')}", flush=True)
    else:
        print(f"{record['demo_id']}: skipped reason={record.get('reason')}", flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
