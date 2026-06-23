#!/usr/bin/env python3
"""Coordinate file-backed workers for parallel DatasetBuilder HDF5 export."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from build_hdf5_batch import (
    BatchBuildOptions,
    process_demo,
    resolve_demos_root,
    resolve_output_dir,
)
from hdf5_builder.context import DATASET_ROOT, DEFAULT_REPO_ROOT


CPU_PAIRS = ((0, 1), (2, 3), (4, 5), (6, 7), (8, 9), (12, 13), (14, 15))
MAX_ATTEMPTS = 2


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate = subparsers.add_parser("validate-cpus")
    validate.add_argument("--workers", type=int, required=True)

    prepare = subparsers.add_parser("prepare")
    prepare.add_argument("--run-dir", type=Path, required=True)
    prepare.add_argument("--demos-root", type=Path, default=Path("runtime_sessions/demos"))
    prepare.add_argument("--output-dir", type=Path, default=DATASET_ROOT)
    prepare.add_argument(
        "--runtime-root",
        "--repo-root",
        dest="runtime_root",
        type=Path,
        default=DEFAULT_REPO_ROOT,
    )
    prepare.add_argument("--overwrite", action="store_true")
    prepare.add_argument(
        "--skip-existing",
        action=argparse.BooleanOptionalAction,
        default=True,
    )
    prepare.add_argument("--min-free-gb", type=float, default=20.0)
    prepare.add_argument("--no-update-manifest", action="store_true")
    prepare.add_argument("--batch-report", type=Path, default=None)
    prepare.add_argument("--dry-run", action="store_true")

    worker = subparsers.add_parser("worker")
    worker.add_argument("--run-dir", type=Path, required=True)
    worker.add_argument("--worker-id", required=True)

    recover = subparsers.add_parser("recover")
    recover.add_argument("--run-dir", type=Path, required=True)
    recover.add_argument("--worker-id", required=True)
    recover.add_argument("--exit-code", type=int, required=True)

    finalize = subparsers.add_parser("finalize")
    finalize.add_argument("--run-dir", type=Path, required=True)

    return parser.parse_args()


def atomic_write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(
            json.dumps(payload, indent=2, ensure_ascii=True) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def parse_lscpu_rows(text: str) -> dict[int, tuple[int, int, bool]]:
    rows: dict[int, tuple[int, int, bool]] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        fields = line.split(",")
        if len(fields) != 4:
            raise RuntimeError(f"unexpected lscpu row: {raw_line!r}")
        cpu, core, socket = (int(value) for value in fields[:3])
        rows[cpu] = (core, socket, fields[3].strip().upper() == "Y")
    return rows


def validate_cpu_pairs(workers: int, rows: dict[int, tuple[int, int, bool]]) -> list[str]:
    if workers < 1 or workers > len(CPU_PAIRS):
        raise RuntimeError(
            f"--workers must be between 1 and {len(CPU_PAIRS)}, got {workers}"
        )
    selected: list[str] = []
    for first, second in CPU_PAIRS[:workers]:
        missing = [cpu for cpu in (first, second) if cpu not in rows]
        if missing:
            raise RuntimeError(f"CPU pair {first},{second} is missing CPUs: {missing}")
        first_core, first_socket, first_online = rows[first]
        second_core, second_socket, second_online = rows[second]
        if not first_online or not second_online:
            raise RuntimeError(f"CPU pair {first},{second} contains an offline CPU")
        if (first_core, first_socket) != (second_core, second_socket):
            raise RuntimeError(
                f"CPU pair {first},{second} does not share one physical core: "
                f"{first_core}/{first_socket} != {second_core}/{second_socket}"
            )
        selected.append(f"{first},{second}")
    return selected


def validate_cpus(workers: int) -> int:
    result = subprocess.run(
        ["lscpu", "-p=CPU,CORE,SOCKET,ONLINE"],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    )
    for pair in validate_cpu_pairs(workers, parse_lscpu_rows(result.stdout)):
        print(pair)
    return 0


def require_empty_run_dir(run_dir: Path) -> None:
    if run_dir.exists() and any(run_dir.iterdir()):
        raise RuntimeError(f"--run-dir must be absent or empty: {run_dir}")
    run_dir.mkdir(parents=True, exist_ok=True)


def check_output_writable(output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        prefix=".hdf5_parallel_write_test.",
        dir=output_dir,
    ):
        pass


def prepare_run(args: argparse.Namespace) -> int:
    run_dir = args.run_dir.resolve()
    require_empty_run_dir(run_dir)
    repo_root = args.runtime_root.resolve()
    demos_root = resolve_demos_root(args.demos_root, repo_root)
    output_dir = resolve_output_dir(args.output_dir)
    batch_report = (
        args.batch_report.resolve()
        if args.batch_report
        else output_dir / "batch_build_report.json"
    )
    if not args.dry_run:
        check_output_writable(output_dir)
        batch_report.parent.mkdir(parents=True, exist_ok=True)

    for name in ("pending", "in_progress", "results", "logs"):
        (run_dir / name).mkdir()

    demos = [
        path.resolve()
        for path in sorted(demos_root.glob("demo_*"))
        if path.is_dir()
    ]
    snapshot = []
    for index, demo_dir in enumerate(demos):
        task_id = f"{index:08d}"
        output_path = output_dir / f"{demo_dir.name}.h5"
        report_path = output_dir / f"{demo_dir.name}.build_report.json"
        tmp_output_path = output_path.with_suffix(output_path.suffix + ".tmp")
        task = {
            "task_id": task_id,
            "demo_id": demo_dir.name,
            "demo_dir": demo_dir.as_posix(),
            "attempts": 0,
            "output_path": output_path.as_posix(),
            "report_path": report_path.as_posix(),
            "tmp_output_path": tmp_output_path.as_posix(),
            "output_existed_before_run": output_path.exists(),
            "report_existed_before_run": report_path.exists(),
            "tmp_output_existed_before_run": tmp_output_path.exists(),
        }
        snapshot.append(task)
        atomic_write_json(run_dir / "pending" / f"{task_id}.json", task)

    config = {
        "repo_root": repo_root.as_posix(),
        "demos_root": demos_root.as_posix(),
        "output_dir": output_dir.as_posix(),
        "batch_report": batch_report.as_posix(),
        "overwrite": bool(args.overwrite),
        "skip_existing": bool(args.skip_existing),
        "min_free_gb": float(args.min_free_gb),
        "update_manifest": not args.no_update_manifest,
        "dry_run": bool(args.dry_run),
    }
    atomic_write_json(run_dir / "config.json", config)
    atomic_write_json(run_dir / "snapshot.json", snapshot)
    print(
        json.dumps(
            {
                "run_dir": run_dir.as_posix(),
                "snapshot_count": len(snapshot),
                "output_dir": output_dir.as_posix(),
                "dry_run": bool(args.dry_run),
            },
            ensure_ascii=True,
        )
    )
    return 0


def load_options(run_dir: Path) -> BatchBuildOptions:
    config = read_json(run_dir / "config.json")
    return BatchBuildOptions(
        repo_root=Path(config["repo_root"]),
        output_dir=Path(config["output_dir"]),
        overwrite=bool(config["overwrite"]),
        skip_existing=bool(config["skip_existing"]),
        min_free_gb=float(config["min_free_gb"]),
        update_manifest=bool(config["update_manifest"]),
        dry_run=bool(config["dry_run"]),
    )


def claim_next(run_dir: Path, worker_dir: Path) -> Path | None:
    for pending_path in sorted((run_dir / "pending").glob("*.json")):
        claimed_path = worker_dir / pending_path.name
        try:
            os.replace(pending_path, claimed_path)
        except FileNotFoundError:
            continue
        task = read_json(claimed_path)
        task["attempts"] = int(task.get("attempts", 0)) + 1
        atomic_write_json(claimed_path, task)
        return claimed_path
    return None


def run_worker(run_dir: Path, worker_id: str) -> int:
    run_dir = run_dir.resolve()
    options = load_options(run_dir)
    worker_dir = run_dir / "in_progress" / worker_id
    worker_dir.mkdir(parents=True, exist_ok=True)
    if any(worker_dir.glob("*.json")):
        raise RuntimeError(f"worker has an unrecovered claim: {worker_dir}")

    while True:
        claimed_path = claim_next(run_dir, worker_dir)
        if claimed_path is None:
            return 0
        task = read_json(claimed_path)
        result_path = run_dir / "results" / f"{task['task_id']}.json"
        if result_path.exists():
            raise RuntimeError(f"result already exists before processing: {result_path}")
        result = process_demo(Path(task["demo_dir"]), options)
        record = {
            **result.record,
            "task_id": task["task_id"],
            "worker_id": worker_id,
            "attempt": task["attempts"],
            "_processed": result.processed,
        }
        atomic_write_json(result_path, record)
        claimed_path.unlink()


def worker_crash_record(task: dict[str, Any], config: dict[str, Any], worker_id: str, exit_code: int) -> dict[str, Any]:
    demo_dir = Path(task["demo_dir"])
    output_dir = Path(config["output_dir"])
    return {
        "task_id": task["task_id"],
        "demo_id": task["demo_id"],
        "manifest_path": (demo_dir / "manifest.json").as_posix(),
        "output_path": (output_dir / f"{task['demo_id']}.h5").as_posix(),
        "report_path": (
            output_dir / f"{task['demo_id']}.build_report.json"
        ).as_posix(),
        "status": "failed",
        "failure_kind": "worker_crash",
        "error": (
            f"worker {worker_id} exited with code {exit_code} on attempt "
            f"{task['attempts']}"
        ),
        "worker_id": worker_id,
        "attempt": task["attempts"],
        "_processed": True,
    }


def cleanup_retry_artifacts(task: dict[str, Any]) -> None:
    artifact_fields = (
        ("output_path", "output_existed_before_run"),
        ("report_path", "report_existed_before_run"),
        ("tmp_output_path", "tmp_output_existed_before_run"),
    )
    for path_field, existed_field in artifact_fields:
        if not bool(task.get(existed_field, False)):
            Path(task[path_field]).unlink(missing_ok=True)


def recover_worker(run_dir: Path, worker_id: str, exit_code: int) -> int:
    run_dir = run_dir.resolve()
    worker_dir = run_dir / "in_progress" / worker_id
    config = read_json(run_dir / "config.json")
    claims = sorted(worker_dir.glob("*.json")) if worker_dir.exists() else []
    if len(claims) > 1:
        raise RuntimeError(f"worker {worker_id} has multiple claims: {claims}")
    for claimed_path in claims:
        task = read_json(claimed_path)
        result_path = run_dir / "results" / f"{task['task_id']}.json"
        if result_path.exists():
            claimed_path.unlink()
        elif int(task["attempts"]) < MAX_ATTEMPTS:
            cleanup_retry_artifacts(task)
            os.replace(claimed_path, run_dir / "pending" / claimed_path.name)
        else:
            atomic_write_json(
                result_path,
                worker_crash_record(task, config, worker_id, exit_code),
            )
            claimed_path.unlink()
    return 0


def summarize_results(records: list[dict[str, Any]], config: dict[str, Any]) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "dry_run": bool(config["dry_run"]),
        "demos_root": config["demos_root"],
        "output_dir": config["output_dir"],
        "processed": 0,
        "succeeded": 0,
        "failed": 0,
        "skipped": 0,
        "checked": 0,
        "results": [],
    }
    for raw_record in records:
        record = dict(raw_record)
        summary["processed"] += int(bool(record.pop("_processed", False)))
        status = record["status"]
        if status == "succeeded":
            summary["succeeded"] += 1
        elif status == "dry_run_ok":
            summary["checked"] += 1
        elif status == "failed":
            summary["failed"] += 1
        else:
            summary["skipped"] += 1
        summary["results"].append(record)
    return summary


def finalize_run(run_dir: Path) -> int:
    run_dir = run_dir.resolve()
    config = read_json(run_dir / "config.json")
    snapshot = read_json(run_dir / "snapshot.json")
    expected_ids = [task["task_id"] for task in snapshot]
    result_paths = sorted((run_dir / "results").glob("*.json"))
    actual_ids = [path.stem for path in result_paths]
    pending = sorted((run_dir / "pending").glob("*.json"))
    in_progress = sorted((run_dir / "in_progress").glob("*/*.json"))

    errors = []
    if len(expected_ids) != len(set(expected_ids)):
        errors.append("snapshot contains duplicate task IDs")
    if actual_ids != expected_ids:
        missing = sorted(set(expected_ids) - set(actual_ids))
        unexpected = sorted(set(actual_ids) - set(expected_ids))
        errors.append(f"result coverage mismatch: missing={missing}, unexpected={unexpected}")
    if pending:
        errors.append(f"pending tasks remain: {[path.name for path in pending]}")
    if in_progress:
        errors.append(
            f"in-progress tasks remain: {[path.as_posix() for path in in_progress]}"
        )
    if errors:
        atomic_write_json(run_dir / "finalize_error.json", {"errors": errors})
        raise RuntimeError("; ".join(errors))

    records = [read_json(path) for path in result_paths]
    if [record["task_id"] for record in records] != expected_ids:
        raise RuntimeError("result payload task IDs do not match snapshot order")
    summary = summarize_results(records, config)
    atomic_write_json(run_dir / "summary.json", summary)
    if not config["dry_run"]:
        atomic_write_json(Path(config["batch_report"]), summary)
    print(
        json.dumps(
            {
                key: summary[key]
                for key in (
                    "dry_run",
                    "checked",
                    "processed",
                    "succeeded",
                    "failed",
                    "skipped",
                )
            },
            ensure_ascii=True,
        )
    )
    return 1 if summary["failed"] else 0


def main() -> int:
    args = parse_args()
    if args.command == "validate-cpus":
        return validate_cpus(args.workers)
    if args.command == "prepare":
        return prepare_run(args)
    if args.command == "worker":
        return run_worker(args.run_dir, args.worker_id)
    if args.command == "recover":
        return recover_worker(
            args.run_dir,
            args.worker_id,
            args.exit_code,
        )
    if args.command == "finalize":
        return finalize_run(args.run_dir)
    raise RuntimeError(f"unsupported command: {args.command}")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        raise SystemExit(2)
