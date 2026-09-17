from __future__ import annotations

import json
import os
import subprocess
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
    (config_dir / "runtime_OG001622").write_bytes(b"left")
    (config_dir / "runtime_OG001623").write_bytes(b"right")

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


def update_manifest(path: Path, **values: object) -> None:
    payload = raw_cleanup.read_json(path)
    payload.update(values)
    write_json(path, payload)


def set_tactile_postcheck(path: Path, has_warning: object, warnings: list[str] | None = None) -> None:
    payload = raw_cleanup.read_json(path)
    payload["xense_tactile_postcheck"] = {
        "has_warning": has_warning,
        "warnings": warnings or [],
    }
    write_json(path, payload)


def cleanup_config(
    repo: Path,
    mode: str,
    *,
    dry_run: bool = False,
    demo_path: Path | None = None,
) -> CleanupConfig:
    kwargs = {}
    if mode == "completed":
        kwargs = {"archives_root": repo / "archives", "hdf5_root": repo / "hdf5"}
    return CleanupConfig(
        demos_root=repo / "runtime_sessions" / "demos",
        runtime_frames_root=repo / "runtime_frames",
        mode=mode,
        dry_run=dry_run,
        demo_path=demo_path,
        **kwargs,
    )


def test_force_requires_demo_path(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    make_cleanup_demo(repo, demo_id="demo_20260605_165503", status="interrupted")

    with pytest.raises(RuntimeError, match="--demo is required"):
        cleanup_config(repo, "force").resolved()


@pytest.mark.parametrize("dry_run", [True, False])
def test_manifest_only_without_outputs_preserves_shared_config(tmp_path: Path, monkeypatch, dry_run) -> None:
    repo = tmp_path / "repo"
    target = make_cleanup_demo(repo, demo_id="demo_20260605_165503", status="done")
    update_manifest(target, archieved=True, h5_generated=True)
    (target.parent / "aligned" / "aligned_manifest.json").unlink()
    other = make_cleanup_demo(
        repo, demo_id="demo_20260605_170000", status="interrupted", tac_ts="20260605_170000"
    )

    def fail_if_called(*args, **kwargs):
        raise AssertionError("published outputs must not be checked")

    monkeypatch.setattr(raw_cleanup, "check_completed_outputs", fail_if_called)
    monkeypatch.setattr(raw_cleanup, "completed_runtime_config_from_archive", fail_if_called)
    config = cleanup_config(repo, "manifest_only", dry_run=dry_run)
    config.archives_root = repo / "missing_archives"
    config.hdf5_root = repo / "missing_hdf5"
    discovery = discover(config)
    assert [candidate.demo_id for candidate in discovery.candidates] == [target.parent.name]
    plan = build_plan(discovery.candidates[0], config, discovery.runtime_config_refs)
    assert plan.runtime_config.action == "keep_shared"
    result = delete_plan(plan, config)
    assert result.status == ("dry_run_would_delete" if dry_run else "succeeded")
    assert target.exists() == dry_run
    assert (repo / "runtime_frames" / "data_TAC_20260605_165503.npy").exists() == dry_run
    assert other.exists()
    assert plan.runtime_config.path.is_dir()


@pytest.mark.parametrize("status,archieved,h5_generated", [
    ("done", True, True),
    ("done", False, True),
    ("done", True, False),
    ("done", "true", True),
    ("done", True, 1),
    ("done", None, True),
    ("done", True, None),
    ("failed", True, True),
    ("interrupted", True, True),
])
def test_manifest_only_requires_publication_flags(tmp_path, status, archieved, h5_generated) -> None:
    repo = tmp_path / "repo"
    manifest_path = make_cleanup_demo(repo, demo_id="demo_20260605_165503", status=status)
    flags = {key: value for key, value in {
        "archieved": archieved, "h5_generated": h5_generated,
    }.items() if value is not None}
    update_manifest(manifest_path, **flags)
    discovery = discover(cleanup_config(repo, "manifest_only"))
    assert bool(discovery.candidates) == (status == "done" and archieved is True and h5_generated is True)


@pytest.mark.parametrize("args,modes", [
    ([], ["tactile_warning", "completed", "discarded", "failed"]),
    (["--manifest-only", "--dry-run"], ["manifest_only"]),
    (["--dry-run", "--manifest-only"], ["manifest_only"]),
    (["--invalid"], []),
])
def test_cleanup_wrapper_routes_modes_without_running_cleanup(tmp_path, args, modes) -> None:
    stub = tmp_path / "python3"
    stub.write_text(
        '#!/usr/bin/python3\nimport json, sys\nprint(json.dumps(sys.argv[1:]))\n',
        encoding="utf-8",
    )
    stub.chmod(0o755)
    wrapper = Path(cleanup_raw_demos.__file__).with_suffix(".sh")
    result = subprocess.run(
        ["bash", str(wrapper), *args],
        env={**os.environ, "PATH": f"{tmp_path}:{os.environ['PATH']}"},
        capture_output=True, text=True,
    )
    calls = [json.loads(line) for line in result.stdout.splitlines()]
    assert result.returncode == (0 if modes else 2)
    assert [call[call.index("--mode") + 1] for call in calls] == modes
    assert all(("--dry-run" in call) == ("--dry-run" in args) for call in calls)


def test_force_rejects_demo_outside_demos_root(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    make_cleanup_demo(repo, demo_id="demo_20260605_165503", status="interrupted")
    outside = tmp_path / "outside" / "demo_20260605_165503"
    outside.mkdir(parents=True)
    write_json(outside / "manifest.json", {"status": "interrupted"})

    with pytest.raises(RuntimeError, match="outside configured root"):
        cleanup_config(repo, "force", demo_path=outside).resolved()


def test_force_selects_single_demo_with_any_status_without_completed_check(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path / "repo"
    manifest_path = make_cleanup_demo(
        repo,
        demo_id="demo_20260605_165503",
        status="interrupted",
        tac_ts="20260605_165503",
        config_ts="20260605_163106",
    )

    def fail_if_called(*args, **kwargs):
        raise AssertionError("completed check must not run in force mode")

    monkeypatch.setattr(raw_cleanup, "check_completed_outputs", fail_if_called)
    config = cleanup_config(repo, "force", dry_run=True, demo_path=manifest_path.parent)
    discovery = discover(config)
    plan = build_plan(discovery.candidates[0], config, discovery.runtime_config_refs)

    assert len(discovery.candidates) == 1
    assert discovery.candidates[0].status == "interrupted"
    assert discovery.candidates[0].completion is None
    assert plan.external_files[0].path.name == "data_FT_20260605_165503.npy"
    assert plan.external_files[1].path.name == "data_TAC_20260605_165503.npy"
    assert plan.runtime_config.action == "delete"


def test_force_runtime_config_keeps_shared_reference(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    target = make_cleanup_demo(
        repo,
        demo_id="demo_20260605_165900",
        status="interrupted",
        tac_ts="20260605_165900",
        config_ts="20260605_163106",
    )
    make_cleanup_demo(
        repo,
        demo_id="demo_20260605_170000",
        status="failed",
        tac_ts="20260605_170000",
        config_ts="20260605_163106",
    )
    config = cleanup_config(repo, "force", demo_path=target.parent)
    discovery = discover(config)
    plan = build_plan(discovery.candidates[0], config, discovery.runtime_config_refs)

    assert len(discovery.candidates) == 1
    assert plan.runtime_config.action == "keep_shared"
    assert plan.runtime_config.shared_with == ("demo_20260605_170000",)


def test_force_argparse_requires_existing_required_roots(monkeypatch) -> None:
    monkeypatch.setattr(
        "sys.argv",
        [
            "cleanup_raw_demos.py",
            "--mode",
            "force",
            "--demo",
            "runtime_sessions/demos/demo_xxx",
        ],
    )

    with pytest.raises(SystemExit):
        cleanup_raw_demos.parse_args()


def test_tactile_warning_selects_done_boolean_true_without_completion_check(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repo = tmp_path / "repo"
    manifest_path = make_cleanup_demo(
        repo,
        demo_id="demo_20260605_165503",
        status="done",
        tac_ts="20260605_165503",
        config_ts="20260605_163106",
    )
    set_tactile_postcheck(manifest_path, True, ["right sensor edge warning"])
    (manifest_path.parent / "aligned" / "aligned_manifest.json").unlink()

    def fail_if_called(*args, **kwargs):
        raise AssertionError("completed check must not run in tactile_warning mode")

    monkeypatch.setattr(raw_cleanup, "check_completed_outputs", fail_if_called)
    config = cleanup_config(repo, "tactile_warning")
    discovery = discover(config)
    plan = build_plan(discovery.candidates[0], config, discovery.runtime_config_refs)

    assert [candidate.demo_id for candidate in discovery.candidates] == [
        "demo_20260605_165503"
    ]
    assert discovery.candidates[0].completion is None
    assert plan.runtime_config.path == (repo / "runtime_frames" / "20260605_163106").resolve()
    assert plan.runtime_config.action == "delete"


@pytest.mark.parametrize("has_warning", [False, "true", 1, None])
def test_tactile_warning_requires_strict_boolean_true(
    tmp_path: Path,
    has_warning: object,
) -> None:
    repo = tmp_path / "repo"
    manifest_path = make_cleanup_demo(
        repo,
        demo_id="demo_20260605_165503",
        status="done",
    )
    set_tactile_postcheck(manifest_path, has_warning)

    discovery = discover(cleanup_config(repo, "tactile_warning"))

    assert discovery.candidates == []


def test_tactile_warning_rejects_missing_postcheck_and_non_done_status(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    make_cleanup_demo(
        repo,
        demo_id="demo_20260605_165503",
        status="done",
        tac_ts="20260605_165503",
    )
    failed_manifest = make_cleanup_demo(
        repo,
        demo_id="demo_20260605_165900",
        status="failed",
        tac_ts="20260605_165900",
    )
    set_tactile_postcheck(failed_manifest, True)

    discovery = discover(cleanup_config(repo, "tactile_warning"))

    assert discovery.candidates == []


def test_tactile_warning_prints_warnings_and_keeps_shared_runtime_config(
    tmp_path: Path,
    capsys,
) -> None:
    repo = tmp_path / "repo"
    manifest_path = make_cleanup_demo(
        repo,
        demo_id="demo_20260605_165503",
        status="done",
        tac_ts="20260605_165503",
        config_ts="20260605_163106",
    )
    set_tactile_postcheck(manifest_path, True, ["right sensor edge warning"])
    make_cleanup_demo(
        repo,
        demo_id="demo_20260605_165900",
        status="failed",
        tac_ts="20260605_165900",
        config_ts="20260605_163106",
    )
    config = cleanup_config(repo, "tactile_warning")
    discovery = discover(config)
    plan = build_plan(discovery.candidates[0], config, discovery.runtime_config_refs)

    cleanup_raw_demos.print_plan(plan, 1, 1, dry_run=True)

    out = capsys.readouterr().out
    assert 'xense_tactile_warnings: ["right sensor edge warning"]' in out
    assert plan.runtime_config.action == "keep_shared"
    assert plan.runtime_config.shared_with == ("demo_20260605_165900",)


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
