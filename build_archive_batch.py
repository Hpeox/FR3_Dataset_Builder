#!/usr/bin/env python3
"""Build cold-storage archives for done/aligned demos in a demos directory."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from archive_builder.builder import build_archive
from archive_builder.context import BATCH_ARCHIVE_ROOT, DEFAULT_REPO_ROOT, ArchiveContext, read_json
from archive_builder.dry_run import dry_run_check_archive


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--demos-root", type=Path, default=Path("runtime_sessions/demos"))
    parser.add_argument("--archive-dir", type=Path, default=BATCH_ARCHIVE_ROOT)
    parser.add_argument(
        "--runtime-root",
        "--repo-root",
        dest="runtime_root",
        type=Path,
        default=DEFAULT_REPO_ROOT,
        help="runtime data root containing runtime_sessions and runtime_frames",
    )
    parser.add_argument("--overwrite", action="store_true", help="replace existing archive artifacts")
    parser.add_argument("--keep-staging", action="store_true", help="keep staging directories after completion")
    parser.add_argument(
        "--update-manifest",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="set raw manifest archieved=true after publication",
    )
    parser.add_argument("--skip-existing", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--only", action="append", default=None, help="archive only the named demo; may be repeated")
    parser.add_argument("--batch-report", type=Path, default=None)
    parser.add_argument("--dry-run", action="store_true", help="validate archive inputs and paths without writing artifacts")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    repo_root = args.runtime_root.resolve()
    demos_root = resolve_demos_root(args.demos_root, repo_root)
    archive_dir = args.archive_dir.resolve() if args.archive_dir.is_absolute() else (Path(__file__).resolve().parent / args.archive_dir).resolve()
    if not args.dry_run:
        archive_dir.mkdir(parents=True, exist_ok=True)
    batch_report = args.batch_report or archive_dir / "batch_archive_report.json"
    batch_report = batch_report.resolve()
    only = set(args.only or [])
    summary: dict[str, Any] = {
        "demos_root": demos_root.as_posix(),
        "archive_dir": archive_dir.as_posix(),
        "processed": 0,
        "succeeded": 0,
        "failed": 0,
        "skipped": 0,
        "checked": 0,
        "dry_run": bool(args.dry_run),
        "results": [],
    }
    candidates = [path for path in sorted(demos_root.glob("demo_*")) if path.is_dir()]
    if only:
        candidates = [path for path in candidates if path.name in only]
    print(
        json.dumps(
            {
                "candidate_count": len(candidates),
                "archive_dir": archive_dir.as_posix(),
                "update_manifest": args.update_manifest,
                "dry_run": bool(args.dry_run),
            },
            ensure_ascii=True,
        ),
        flush=True,
    )
    processable_seen = 0
    for demo_dir in candidates:
        manifest_path = demo_dir / "manifest.json"
        record: dict[str, Any] = {
            "demo_id": demo_dir.name,
            "manifest_path": manifest_path.as_posix(),
            "archive_path": (archive_dir / f"{demo_dir.name}.zip").as_posix(),
            "archive_json_path": (archive_dir / f"{demo_dir.name}.archive.json").as_posix(),
        }
        skip_reason = skip_reason_for_demo(manifest_path)
        if skip_reason:
            _append(summary, {**record, "status": "skipped", "reason": skip_reason}, batch_report)
            print_status(summary["results"][-1])
            continue
        processable_seen += 1
        if args.limit is not None and processable_seen > args.limit:
            summary["limit_reached"] = True
            break
        if args.skip_existing and Path(record["archive_path"]).exists() and Path(record["archive_json_path"]).exists():
            _append(summary, {**record, "status": "skipped", "reason": "existing_archive"}, batch_report)
            print_status(summary["results"][-1])
            continue
        try:
            ctx = ArchiveContext(manifest_path=manifest_path, archive_dir=archive_dir, repo_root=repo_root)
            if args.dry_run:
                dry_run_payload = dry_run_check_archive(ctx, overwrite=args.overwrite)
                _append(
                    summary,
                    {
                        **record,
                        "status": "dry_run_ok",
                        **dry_run_payload,
                    },
                    batch_report,
                )
                print_status(summary["results"][-1])
                continue
            summary["processed"] += 1
            payload = build_archive(
                ctx,
                overwrite=args.overwrite,
                keep_staging=args.keep_staging,
                update_manifest=args.update_manifest,
            )
            _append(
                summary,
                {
                    **record,
                    "status": "succeeded",
                    "zip_size": payload["zip_size"],
                    "zip_sha256": payload["zip_sha256"],
                    "manifest_updated": payload["manifest_updated"],
                },
                batch_report,
            )
        except Exception as exc:
            _append(summary, {**record, "status": "failed", "error": str(exc)}, batch_report)
        print_status(summary["results"][-1])
    if not args.dry_run:
        write_batch_report(batch_report, summary)
    print(
        json.dumps(
            {key: summary[key] for key in ("dry_run", "checked", "processed", "succeeded", "failed", "skipped")},
            ensure_ascii=True,
        )
    )
    return 1 if summary["failed"] else 0


def resolve_demos_root(path: Path, repo_root: Path) -> Path:
    resolved = path if path.is_absolute() else repo_root / path
    if not resolved.is_dir():
        raise RuntimeError(f"--demos-root is not a directory: {resolved}")
    return resolved.resolve()


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


def _append(summary: dict[str, Any], record: dict[str, Any], batch_report: Path) -> None:
    if record["status"] == "succeeded":
        summary["succeeded"] += 1
    elif record["status"] == "dry_run_ok":
        summary["checked"] += 1
    elif record["status"] == "failed":
        summary["failed"] += 1
    else:
        summary["skipped"] += 1
    summary["results"].append(record)
    if not summary.get("dry_run"):
        write_batch_report(batch_report, summary)


def write_batch_report(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")


def print_status(record: dict[str, Any]) -> None:
    if record["status"] == "succeeded":
        print(f"{record['demo_id']}: succeeded archive={record['archive_path']}", flush=True)
    elif record["status"] == "dry_run_ok":
        print(
            f"{record['demo_id']}: dry-run ok target_conflict={record.get('target_conflict')} "
            f"archive={record['archive_path']}",
            flush=True,
        )
    elif record["status"] == "failed":
        error = str(record.get("error", ""))
        tail = "\n".join(error.splitlines()[-8:])
        print(f"{record['demo_id']}: failed error={tail}", flush=True)
    else:
        print(f"{record['demo_id']}: skipped reason={record.get('reason')}", flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
