#!/usr/bin/env python3
"""Interactively delete raw demos after archive/HDF5 publication or rejection."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from raw_cleanup import (
    CleanupConfig,
    CleanupFailure,
    CleanupPlan,
    build_plan,
    delete_plan,
    discover,
    format_size,
    update_runtime_config_refs,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--demos-root", type=Path, required=True)
    parser.add_argument("--runtime-frames-root", type=Path, required=True)
    parser.add_argument("--demo", type=Path, default=None, help="single demo directory for force mode")
    parser.add_argument("--archives-root", type=Path, default=None)
    parser.add_argument("--hdf5-root", type=Path, default=None)
    parser.add_argument(
        "--mode",
        choices=("completed", "tactile_warning", "discarded", "failed", "force"),
        required=True,
    )
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config = CleanupConfig(
        demos_root=args.demos_root,
        runtime_frames_root=args.runtime_frames_root,
        archives_root=args.archives_root,
        hdf5_root=args.hdf5_root,
        demo_path=args.demo,
        mode=args.mode,
        dry_run=args.dry_run,
    ).resolved()
    discovery = discover(config)
    summary = {
        "mode": config.mode,
        "dry_run": config.dry_run,
        "candidate_count": len(discovery.candidates),
        "skipped": len(discovery.skipped),
        "deleted": 0,
        "would_delete": 0,
        "user_skipped": 0,
        "failed": 0,
        "quit": False,
        "warnings": discovery.warnings,
        "results": [],
    }
    print(
        json.dumps(
            {
                "mode": config.mode,
                "dry_run": config.dry_run,
                "candidate_count": len(discovery.candidates),
                "skipped": len(discovery.skipped),
                "warnings": len(discovery.warnings),
            },
            ensure_ascii=True,
        ),
        flush=True,
    )

    for index, candidate in enumerate(discovery.candidates, start=1):
        try:
            plan = build_plan(candidate, config, discovery.runtime_config_refs)
        except Exception as exc:
            summary["failed"] += 1
            summary["results"].append(
                {"demo_id": candidate.demo_id, "status": "failed", "error": str(exc)}
            )
            break
        print_plan(plan, index, len(discovery.candidates), config.dry_run)
        answer = input("Delete this demo? [y/N/q]: ").strip().lower()
        if answer == "q":
            summary["quit"] = True
            break
        if answer != "y":
            summary["user_skipped"] += 1
            summary["results"].append({"demo_id": candidate.demo_id, "status": "user_skipped"})
            continue
        try:
            result = delete_plan(plan, config)
        except CleanupFailure as exc:
            result = exc.result
            remaining = len(discovery.candidates) - index
            summary["failed"] += 1
            summary["results"].append(
                {
                    "demo_id": result.demo_id,
                    "status": result.status,
                    "deleted_paths": result.deleted_paths,
                    "failed_path": result.failed_path,
                    "error": result.error,
                    "remaining_candidates": remaining,
                }
            )
            print_summary(summary)
            return 1
        update_runtime_config_refs(discovery.runtime_config_refs, candidate)
        if config.dry_run:
            summary["would_delete"] += 1
        else:
            summary["deleted"] += 1
        summary["results"].append(
            {
                "demo_id": result.demo_id,
                "status": result.status,
                "deleted_paths": result.deleted_paths,
            }
        )

    print_summary(summary)
    return 1 if summary["failed"] else 0


def print_plan(plan: CleanupPlan, index: int, total: int, dry_run: bool) -> None:
    candidate = plan.candidate
    prefix = "DRY-RUN " if dry_run else ""
    print(f"\n[{index}/{total}] {prefix}{candidate.demo_id}", flush=True)
    print(f"mode_status: {candidate.status}", flush=True)
    print_manifest_reason_fields(candidate.manifest, candidate.status)
    if candidate.completion is not None:
        print(
            "completed_outputs: "
            f"zip={candidate.completion.archive_zip} "
            f"zip_size={candidate.completion.zip_size} "
            f"archive_json={candidate.completion.archive_json} "
            f"hdf5={candidate.completion.hdf5} "
            f"hdf5_size={candidate.completion.hdf5_size}",
            flush=True,
        )
    print(f"demo: delete {plan.demo_item.path} size={format_size(plan.demo_item.size)}", flush=True)
    for item in plan.external_files:
        state = "delete" if item.exists else "missing"
        print(f"{item.label}: {state} {item.path} size={format_size(item.size)}", flush=True)
    runtime = plan.runtime_config
    if runtime.path is None:
        print(f"runtime_config: {runtime.action} {runtime.reason}", flush=True)
    elif runtime.action == "keep_shared":
        print(
            f"runtime_config: keep_shared {runtime.path} shared_with={','.join(runtime.shared_with)}",
            flush=True,
        )
    else:
        print(f"runtime_config: {runtime.action} {runtime.path} {runtime.reason}".rstrip(), flush=True)
    print(f"estimated_total: {format_size(plan.total_size)}", flush=True)


def print_manifest_reason_fields(manifest: dict, status: str) -> None:
    postcheck = manifest.get("xense_tactile_postcheck")
    if isinstance(postcheck, dict) and postcheck.get("has_warning") is True:
        print(
            "xense_tactile_warnings: "
            f"{json.dumps(postcheck.get('warnings', []), ensure_ascii=True)}",
            flush=True,
        )
    if status == "discarded":
        print(f"discard_reason: {manifest.get('discard_reason', '')}", flush=True)
    elif status == "failed":
        print(f"failure_stage: {manifest.get('failure_stage', '')}", flush=True)
        print(f"failure_reason: {manifest.get('failure_reason', '')}", flush=True)


def print_summary(summary: dict) -> None:
    compact = {
        "mode": summary["mode"],
        "dry_run": summary["dry_run"],
        "candidate_count": summary["candidate_count"],
        "deleted": summary["deleted"],
        "would_delete": summary["would_delete"],
        "user_skipped": summary["user_skipped"],
        "failed": summary["failed"],
        "quit": summary["quit"],
        "warnings": len(summary["warnings"]),
    }
    print(json.dumps(compact, ensure_ascii=True), flush=True)
    if summary["failed"]:
        tail = summary["results"][-1] if summary["results"] else {}
        print(json.dumps(tail, ensure_ascii=True), flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
