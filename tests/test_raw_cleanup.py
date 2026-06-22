from __future__ import annotations

from pathlib import Path

import pytest

import cleanup_raw_demos
import raw_cleanup
from conftest import write_json
from raw_cleanup import CleanupConfig, CleanupFailure, build_plan, delete_plan, discover


def make_cleanup_demo(
    repo: Path,
    *,
    demo_id: str,
    status: str,
    tac_ts: str = "20260605_165503",
    config_ts: str = "20260605_163106",
    archive_config_ts: str | None = None,
    with_sensor_paths: bool = True,
) -> Path:
    demo_dir = repo / "runtime_sessions" / "demos" / demo_id
    demo_dir.mkdir(parents=True)
    frames_root = repo / "runtime_frames"
    config_dir = frames_root / config_ts
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "runtime_OG000544").write_bytes(b"left")
    (config_dir / "runtime_OG001009").write_bytes(b"right")

    sensor_paths = {}
    if with_sensor_paths:
        sensor_paths = {
            "ft300": f"runtime_frames/data_FT_{tac_ts}.npy",
            "xense": f"runtime_frames/data_TAC_{tac_ts}.npy",
        }
        (frames_root / f"data_FT_{tac_ts}.npy").write_bytes(b"ft")
        (frames_root / f"data_TAC_{tac_ts}.npy").write_bytes(b"tac")

    manifest = {
        "status": status,
        "task_name": "16mm-peg-in-hole",
        "language_instruction": "Pick up the test object",
        "sensor_paths": sensor_paths,
        "npz": {},
        "rosbag_uri": "rosbag",
    }
    write_json(demo_dir / "manifest.json", manifest)
    (demo_dir / "payload.txt").write_text("payload\n", encoding="utf-8")
    if status == "done":
        aligned = demo_dir / "aligned"
        aligned.mkdir()
        write_json(aligned / "aligned_manifest.json", {"status": "done"})
    if archive_config_ts is not None:
        archive_dir = repo / "archives"
        hdf5_dir = repo / "hdf5"
        archive_dir.mkdir(exist_ok=True)
        hdf5_dir.mkdir(exist_ok=True)
        zip_path = archive_dir / f"{demo_id}.zip"
        zip_path.write_bytes(b"zip-data")
        write_json(
            archive_dir / f"{demo_id}.archive.json",
            {
                "demo_id": demo_id,
                "zip_size": zip_path.stat().st_size,
                "source_paths": {
                    "tac_runtime_config_dir": (frames_root / archive_config_ts).as_posix(),
                },
            },
        )
        (hdf5_dir / f"{demo_id}.h5").write_bytes(b"h5-data")
    return demo_dir / "manifest.json"


def update_manifest(path: Path, **values: str) -> None:
    payload = raw_cleanup.read_json(path)
    payload.update(values)
    write_json(path, payload)


def cleanup_config(repo: Path, mode: str, *, dry_run: bool = False) -> CleanupConfig:
    kwargs = {}
    if mode == "completed":
        kwargs = {"archives_root": repo / "archives", "hdf5_root": repo / "hdf5"}
    return CleanupConfig(
        demos_root=repo / "runtime_sessions" / "demos",
        runtime_frames_root=repo / "runtime_frames",
        mode=mode,
        dry_run=dry_run,
        **kwargs,
    )


def test_completed_dry_run_keeps_files_and_uses_archive_runtime_config(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    make_cleanup_demo(
        repo,
        demo_id="demo_20260605_165503",
        status="done",
        archive_config_ts="20260605_163106",
    )
    config = cleanup_config(repo, "completed", dry_run=True)
    discovery = discover(config)

    assert len(discovery.candidates) == 1
    plan = build_plan(discovery.candidates[0], config, discovery.runtime_config_refs)
    result = delete_plan(plan, config)

    assert result.status == "dry_run_would_delete"
    assert (repo / "runtime_sessions" / "demos" / "demo_20260605_165503").exists()
    assert (repo / "runtime_frames" / "data_TAC_20260605_165503.npy").exists()
    assert plan.runtime_config.path == (repo / "runtime_frames" / "20260605_163106").resolve()
    assert plan.runtime_config.action == "delete"


def test_completed_rejects_archive_zip_size_mismatch(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    make_cleanup_demo(
        repo,
        demo_id="demo_20260605_165503",
        status="done",
        archive_config_ts="20260605_163106",
    )
    archive_json = repo / "archives" / "demo_20260605_165503.archive.json"
    payload = raw_cleanup.read_json(archive_json)
    payload["zip_size"] += 1
    write_json(archive_json, payload)

    discovery = discover(cleanup_config(repo, "completed"))

    assert discovery.candidates == []
    assert "archive ZIP size mismatch" in discovery.skipped[0].reason


def test_failed_runtime_config_is_derived_and_shared_with_completed(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    make_cleanup_demo(
        repo,
        demo_id="demo_20260605_165503",
        status="done",
        archive_config_ts="20260605_163106",
    )
    make_cleanup_demo(
        repo,
        demo_id="demo_20260605_165900",
        status="failed",
        tac_ts="20260605_165900",
        config_ts="20260605_163106",
    )
    config = cleanup_config(repo, "completed")
    discovery = discover(config)

    plan = build_plan(discovery.candidates[0], config, discovery.runtime_config_refs)

    assert plan.runtime_config.action == "keep_shared"
    assert plan.runtime_config.shared_with == ("demo_20260605_165900",)


def test_failed_last_runtime_config_reference_is_deleted(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    make_cleanup_demo(
        repo,
        demo_id="demo_20260605_165900",
        status="failed",
        tac_ts="20260605_165900",
        config_ts="20260605_163106",
    )
    config = cleanup_config(repo, "failed")
    discovery = discover(config)
    plan = build_plan(discovery.candidates[0], config, discovery.runtime_config_refs)

    assert plan.runtime_config.action == "delete"
    assert plan.runtime_config.path == (repo / "runtime_frames" / "20260605_163106").resolve()


def test_discarded_does_not_derive_runtime_config(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    make_cleanup_demo(
        repo,
        demo_id="demo_20260605_165503",
        status="discarded",
        with_sensor_paths=False,
    )
    config = cleanup_config(repo, "discarded")
    discovery = discover(config)
    plan = build_plan(discovery.candidates[0], config, discovery.runtime_config_refs)

    assert plan.external_files == []
    assert plan.runtime_config.action == "not_applicable"


def test_print_plan_shows_discard_reason(tmp_path: Path, capsys) -> None:
    repo = tmp_path / "repo"
    manifest_path = make_cleanup_demo(
        repo,
        demo_id="demo_20260605_165503",
        status="discarded",
        with_sensor_paths=False,
    )
    update_manifest(manifest_path, discard_reason="operator rejected sample")
    config = cleanup_config(repo, "discarded")
    discovery = discover(config)
    plan = build_plan(discovery.candidates[0], config, discovery.runtime_config_refs)

    cleanup_raw_demos.print_plan(plan, 1, 1, dry_run=True)

    out = capsys.readouterr().out
    assert "discard_reason: operator rejected sample" in out


def test_print_plan_shows_failed_stage_and_reason(tmp_path: Path, capsys) -> None:
    repo = tmp_path / "repo"
    manifest_path = make_cleanup_demo(
        repo,
        demo_id="demo_20260605_165900",
        status="failed",
        tac_ts="20260605_165900",
        config_ts="20260605_163106",
    )
    update_manifest(
        manifest_path,
        failure_stage="FINALIZING",
        failure_reason="sensor stop timeout",
    )
    config = cleanup_config(repo, "failed")
    discovery = discover(config)
    plan = build_plan(discovery.candidates[0], config, discovery.runtime_config_refs)

    cleanup_raw_demos.print_plan(plan, 1, 1, dry_run=True)

    out = capsys.readouterr().out
    assert "failure_stage: FINALIZING" in out
    assert "failure_reason: sensor stop timeout" in out


def test_deletion_order_with_flag_external_config_manifest_and_dir(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path / "repo"
    make_cleanup_demo(
        repo,
        demo_id="demo_20260605_165900",
        status="failed",
        tac_ts="20260605_165900",
        config_ts="20260605_163106",
    )
    config = cleanup_config(repo, "failed")
    discovery = discover(config)
    plan = build_plan(discovery.candidates[0], config, discovery.runtime_config_refs)
    real_delete_path = raw_cleanup.delete_path
    order: list[str] = []

    def recording_delete(path: Path) -> None:
        if path.name != "demo_20260605_165900":
            assert (plan.candidate.demo_dir / raw_cleanup.FLAG_NAME).exists()
        order.append(path.name)
        real_delete_path(path)

    monkeypatch.setattr(raw_cleanup, "delete_path", recording_delete)

    result = delete_plan(plan, config)

    assert result.status == "succeeded"
    assert order[:3] == [
        "data_FT_20260605_165900.npy",
        "data_TAC_20260605_165900.npy",
        "20260605_163106",
    ]
    assert "manifest.json" in order
    assert raw_cleanup.FLAG_NAME in order
    assert order[-1] == "demo_20260605_165900"


def test_partial_failure_is_fail_fast_and_leaves_flag(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path / "repo"
    make_cleanup_demo(
        repo,
        demo_id="demo_20260605_165900",
        status="failed",
        tac_ts="20260605_165900",
        config_ts="20260605_163106",
    )
    config = cleanup_config(repo, "failed")
    discovery = discover(config)
    plan = build_plan(discovery.candidates[0], config, discovery.runtime_config_refs)
    real_delete_path = raw_cleanup.delete_path

    def failing_delete(path: Path) -> None:
        if path.name.startswith("data_FT_"):
            raise RuntimeError(f"boom: {path}")
        real_delete_path(path)

    monkeypatch.setattr(raw_cleanup, "delete_path", failing_delete)

    with pytest.raises(CleanupFailure) as excinfo:
        delete_plan(plan, config)

    assert excinfo.value.result.status == "partial_failed"
    assert (plan.candidate.demo_dir / raw_cleanup.FLAG_NAME).exists()
    assert (plan.candidate.demo_dir / "manifest.json").exists()


def test_failure_after_flag_removal_restores_flag(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path / "repo"
    make_cleanup_demo(
        repo,
        demo_id="demo_20260605_165900",
        status="failed",
        tac_ts="20260605_165900",
        config_ts="20260605_163106",
    )
    config = cleanup_config(repo, "failed")
    discovery = discover(config)
    plan = build_plan(discovery.candidates[0], config, discovery.runtime_config_refs)
    real_delete_path = raw_cleanup.delete_path

    def failing_final_delete(path: Path) -> None:
        if path == plan.candidate.demo_dir:
            raise RuntimeError(f"final boom: {path}")
        real_delete_path(path)

    monkeypatch.setattr(raw_cleanup, "delete_path", failing_final_delete)

    with pytest.raises(CleanupFailure):
        delete_plan(plan, config)

    assert (plan.candidate.demo_dir / raw_cleanup.FLAG_NAME).exists()
    assert not (plan.candidate.demo_dir / "manifest.json").exists()


def test_path_safety_rejects_sensor_path_outside_runtime_root(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    manifest_path = make_cleanup_demo(
        repo,
        demo_id="demo_20260605_165900",
        status="failed",
        tac_ts="20260605_165900",
        config_ts="20260605_163106",
    )
    payload = raw_cleanup.read_json(manifest_path)
    outside = tmp_path / "outside" / "data_TAC_20260605_165900.npy"
    outside.parent.mkdir()
    outside.write_bytes(b"tac")
    payload["sensor_paths"]["xense"] = outside.as_posix()
    write_json(manifest_path, payload)

    discovery = discover(cleanup_config(repo, "failed"))

    assert discovery.candidates == []
    assert "outside configured root" in discovery.skipped[0].reason


def test_path_safety_rejects_symlink_in_demo_tree(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    make_cleanup_demo(
        repo,
        demo_id="demo_20260605_165900",
        status="failed",
        tac_ts="20260605_165900",
        config_ts="20260605_163106",
    )
    link = repo / "runtime_sessions" / "demos" / "demo_20260605_165900" / "link"
    link.symlink_to(repo / "runtime_frames" / "data_TAC_20260605_165900.npy")
    config = cleanup_config(repo, "failed")
    discovery = discover(config)

    with pytest.raises(RuntimeError, match="symlink"):
        build_plan(discovery.candidates[0], config, discovery.runtime_config_refs)
