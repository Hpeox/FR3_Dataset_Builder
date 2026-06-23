from __future__ import annotations

import argparse
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

import build_archive_batch
import build_hdf5_batch
import build_hdf5_parallel
from build_hdf5_batch import DemoProcessResult
from hdf5_builder.context import DATASET_ROOT
from tools import migrate_external_task_metadata


def topology_rows() -> dict[int, tuple[int, int, bool]]:
    return {
        cpu: (cpu // 2, 0, True)
        for cpu in range(16)
    }


def prepare_args(tmp_path: Path, demo_count: int = 9) -> argparse.Namespace:
    repo_root = tmp_path / "repo"
    demos_root = repo_root / "runtime_sessions" / "demos"
    for index in range(demo_count):
        (demos_root / f"demo_{index:02d}").mkdir(parents=True)
    return argparse.Namespace(
        run_dir=tmp_path / "run",
        demos_root=Path("runtime_sessions/demos"),
        output_dir=tmp_path / "output",
        runtime_root=repo_root,
        overwrite=False,
        skip_existing=True,
        min_free_gb=0.0,
        no_update_manifest=False,
        batch_report=None,
        dry_run=True,
    )


def test_cpu_pairs_use_adjacent_pcores_and_skip_10_11() -> None:
    rows = topology_rows()
    assert build_hdf5_parallel.validate_cpu_pairs(4, rows) == [
        "0,1",
        "2,3",
        "4,5",
        "6,7",
    ]
    assert build_hdf5_parallel.validate_cpu_pairs(7, rows) == [
        "0,1",
        "2,3",
        "4,5",
        "6,7",
        "8,9",
        "12,13",
        "14,15",
    ]


def test_cpu_pair_validation_rejects_invalid_worker_count_and_topology() -> None:
    rows = topology_rows()
    with pytest.raises(RuntimeError, match="between 1 and 7"):
        build_hdf5_parallel.validate_cpu_pairs(8, rows)

    rows[3] = (99, 0, True)
    with pytest.raises(RuntimeError, match="does not share one physical core"):
        build_hdf5_parallel.validate_cpu_pairs(2, rows)

    rows = topology_rows()
    rows[1] = (0, 0, False)
    with pytest.raises(RuntimeError, match="offline"):
        build_hdf5_parallel.validate_cpu_pairs(1, rows)


def test_dynamic_workers_cover_snapshot_exactly_once(tmp_path: Path, monkeypatch) -> None:
    args = prepare_args(tmp_path)
    assert build_hdf5_parallel.prepare_run(args) == 0

    def fake_process(demo_dir: Path, _options) -> DemoProcessResult:
        return DemoProcessResult(
            {
                "demo_id": demo_dir.name,
                "manifest_path": (demo_dir / "manifest.json").as_posix(),
                "output_path": (tmp_path / "output" / f"{demo_dir.name}.h5").as_posix(),
                "report_path": (
                    tmp_path / "output" / f"{demo_dir.name}.build_report.json"
                ).as_posix(),
                "status": "dry_run_ok",
            },
            processed=False,
        )

    monkeypatch.setattr(build_hdf5_parallel, "process_demo", fake_process)
    with ThreadPoolExecutor(max_workers=4) as executor:
        statuses = list(
            executor.map(
                lambda worker_id: build_hdf5_parallel.run_worker(
                    args.run_dir,
                    worker_id,
                ),
                [f"worker_{index}" for index in range(4)],
            )
        )
    assert statuses == [0, 0, 0, 0]
    assert not list((args.run_dir / "pending").glob("*.json"))
    assert not list((args.run_dir / "in_progress").glob("*/*.json"))

    results = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted((args.run_dir / "results").glob("*.json"))
    ]
    assert len(results) == 9
    assert len({result["demo_id"] for result in results}) == 9
    assert build_hdf5_parallel.finalize_run(args.run_dir) == 0
    summary = json.loads((args.run_dir / "summary.json").read_text(encoding="utf-8"))
    assert summary["checked"] == 9
    assert len(summary["results"]) == 9


def test_worker_claim_is_retried_once_then_becomes_failure(tmp_path: Path) -> None:
    args = prepare_args(tmp_path, demo_count=1)
    build_hdf5_parallel.prepare_run(args)
    pending = next((args.run_dir / "pending").glob("*.json"))
    worker_dir = args.run_dir / "in_progress" / "worker_0"
    worker_dir.mkdir()
    claim = worker_dir / pending.name
    pending.replace(claim)

    task = json.loads(claim.read_text(encoding="utf-8"))
    task["attempts"] = 1
    for field in ("output_path", "report_path", "tmp_output_path"):
        path = Path(task[field])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"created by crashed worker")
    claim.write_text(json.dumps(task), encoding="utf-8")
    build_hdf5_parallel.recover_worker(args.run_dir, "worker_0", 9)
    pending = args.run_dir / "pending" / claim.name
    assert pending.exists()
    for field in ("output_path", "report_path", "tmp_output_path"):
        assert not Path(task[field]).exists()

    pending.replace(claim)
    task["attempts"] = 2
    claim.write_text(json.dumps(task), encoding="utf-8")
    build_hdf5_parallel.recover_worker(args.run_dir, "worker_0", 9)
    result = json.loads(
        (args.run_dir / "results" / "00000000.json").read_text(encoding="utf-8")
    )
    assert result["status"] == "failed"
    assert result["failure_kind"] == "worker_crash"
    assert result["attempt"] == 2


def test_finalize_rejects_missing_results(tmp_path: Path) -> None:
    args = prepare_args(tmp_path, demo_count=1)
    build_hdf5_parallel.prepare_run(args)
    with pytest.raises(RuntimeError, match="result coverage mismatch"):
        build_hdf5_parallel.finalize_run(args.run_dir)
    assert (args.run_dir / "finalize_error.json").exists()


def test_dataset_root_defaults_moved_to_internal(monkeypatch) -> None:
    assert DATASET_ROOT == Path("/data/internal/DATASET")
    monkeypatch.setattr("sys.argv", ["build_hdf5_batch.py"])
    assert build_hdf5_batch.parse_args().output_dir == DATASET_ROOT
    monkeypatch.setattr("sys.argv", ["build_archive_batch.py"])
    assert build_archive_batch.parse_args().archive_dir == (
        DATASET_ROOT / "Archived"
    )
    monkeypatch.setattr("sys.argv", ["migrate_external_task_metadata.py"])
    assert migrate_external_task_metadata.parse_args().dataset_root == DATASET_ROOT
