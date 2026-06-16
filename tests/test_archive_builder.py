from __future__ import annotations

import json
import os
import zipfile
from pathlib import Path

import build_archive as build_archive_cli
import build_archive_batch as build_archive_batch_cli
from archive_builder.builder import build_archive
from archive_builder.context import ArchiveContext, select_tac_runtime_config
from archive_builder.restore import restore_archive

from conftest import make_demo, make_fake_commands


def test_select_tac_runtime_config_uses_latest_earlier_directory(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    frames = repo / "runtime_frames"
    for name in ("20260605_150000", "20260605_163106", "20260605_165503"):
        (frames / name).mkdir(parents=True)
    tac = frames / "data_TAC_20260605_165503.npy"
    tac.write_bytes(b"tac")

    selected = select_tac_runtime_config(repo, tac)

    assert selected == (frames / "20260605_163106").resolve()


def test_build_archive_layout_and_default_manifest_policy(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path / "repo"
    manifest = make_demo(repo)
    log_path = tmp_path / "commands.log"
    bin_dir = tmp_path / "bin"
    make_fake_commands(bin_dir, log_path)
    monkeypatch.setenv("ARCHIVE_TEST_LOG", log_path.as_posix())
    monkeypatch.setenv("PATH", bin_dir.as_posix() + os.pathsep + os.environ["PATH"])

    ctx = ArchiveContext(manifest_path=manifest, archive_dir=tmp_path / "archives", repo_root=repo)
    payload = build_archive(ctx)

    assert payload["zip_sha256"]
    assert payload["manifest_updated"] is False
    assert "raw_released" not in payload
    assert json.loads(manifest.read_text(encoding="utf-8")).get("archieved") is None
    assert ctx.archive_path.exists()
    assert ctx.archive_json_path.exists()

    with zipfile.ZipFile(ctx.archive_path, "r") as zf:
        names = set(zf.namelist())
        root = f"{ctx.demo_id}_bundle"
        assert f"{root}/demo/manifest.json" in names
        assert f"{root}/demo/aligned/aligned_index.npz" in names
        assert f"{root}/demo/aligned/aligned_index.npz.zst" not in names
        assert f"{root}/demo/ft300_timestamps.npz.zst" in names
        assert f"{root}/demo/ft300_timestamps.npz" not in names
        assert f"{root}/runtime_frames/data_FT_20260605_165503.npy.zst" in names
        assert f"{root}/runtime_frames/data_FT_20260605_165503.npy" not in names
        assert f"{root}/runtime_frames/runtime_config/runtime_OG000544" in names
        assert f"{root}/demo/rosbag/rosbag_0.mcap" in names
        assert zf.getinfo(f"{root}/demo/aligned/aligned_index.npz").compress_type == zipfile.ZIP_STORED
        assert zf.getinfo(f"{root}/demo/manifest.json").compress_type == zipfile.ZIP_DEFLATED

    commands = log_path.read_text(encoding="utf-8")
    assert "TASKSET -c 10,11 ros2 bag convert -i" in commands
    assert "ZSTD -T0 -19 -f -o" in commands
    assert "aligned_index.npz" not in "\n".join(line for line in commands.splitlines() if line.startswith("ZSTD"))


def test_build_archive_update_manifest_is_explicit(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path / "repo"
    manifest = make_demo(repo)
    log_path = tmp_path / "commands.log"
    bin_dir = tmp_path / "bin"
    make_fake_commands(bin_dir, log_path)
    monkeypatch.setenv("ARCHIVE_TEST_LOG", log_path.as_posix())
    monkeypatch.setenv("PATH", bin_dir.as_posix() + os.pathsep + os.environ["PATH"])

    ctx = ArchiveContext(manifest_path=manifest, archive_dir=tmp_path / "archives", repo_root=repo)
    payload = build_archive(ctx, update_manifest=True)

    assert payload["manifest_updated"] is True
    assert json.loads(manifest.read_text(encoding="utf-8"))["archieved"] is True


def test_restore_archive_restores_zst_and_refuses_overwrite(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path / "repo"
    manifest = make_demo(repo)
    log_path = tmp_path / "commands.log"
    bin_dir = tmp_path / "bin"
    make_fake_commands(bin_dir, log_path)
    monkeypatch.setenv("ARCHIVE_TEST_LOG", log_path.as_posix())
    monkeypatch.setenv("PATH", bin_dir.as_posix() + os.pathsep + os.environ["PATH"])
    archive_dir = tmp_path / "archives"
    ctx = ArchiveContext(manifest_path=manifest, archive_dir=archive_dir, repo_root=repo)
    build_archive(ctx)

    restore_root = tmp_path / "restore"
    result = restore_archive(ctx.archive_json_path, None, restore_root, force=False)
    assert result["demo_id"] == ctx.demo_id
    restored_demo = restore_root / "runtime_sessions" / "demos" / ctx.demo_id
    assert (restored_demo / "ft300_timestamps.npz").exists()
    assert (restored_demo / "aligned" / "aligned_index.npz").exists()
    assert (restore_root / "runtime_frames" / "data_TAC_20260605_165503.npy").exists()
    assert (restore_root / "runtime_frames" / "20260605_163106" / "runtime_OG000544").exists()

    try:
        restore_archive(ctx.archive_json_path, None, restore_root, force=False)
    except RuntimeError as exc:
        assert "refusing to overwrite" in str(exc)
    else:
        raise AssertionError("restore should refuse overwrite without force")


def test_cli_defaults_match_single_and_batch_manifest_policy(monkeypatch) -> None:
    monkeypatch.setattr("sys.argv", ["build_archive.py", "--manifest", "demo/manifest.json"])
    single = build_archive_cli.parse_args()
    assert not hasattr(single, "update_manifest")
    assert single.keep_staging is False
    assert single.archive_dir.as_posix().endswith("/DatasetBuilder/outputs")

    monkeypatch.setattr("sys.argv", ["build_archive_batch.py"])
    batch = build_archive_batch_cli.parse_args()
    assert batch.update_manifest is True
    assert batch.archive_dir.as_posix() == "/data/external/DATASET/Archived"

    monkeypatch.setattr("sys.argv", ["build_archive_batch.py", "--no-update-manifest"])
    batch_no_update = build_archive_batch_cli.parse_args()
    assert batch_no_update.update_manifest is False


def test_single_cli_prints_status_line_not_payload_json(tmp_path: Path, monkeypatch, capsys) -> None:
    repo = tmp_path / "repo"
    manifest = make_demo(repo)
    log_path = tmp_path / "commands.log"
    bin_dir = tmp_path / "bin"
    archive_dir = tmp_path / "outputs"
    make_fake_commands(bin_dir, log_path)
    monkeypatch.setenv("ARCHIVE_TEST_LOG", log_path.as_posix())
    monkeypatch.setenv("PATH", bin_dir.as_posix() + os.pathsep + os.environ["PATH"])
    monkeypatch.setattr(
        "sys.argv",
        [
            "build_archive.py",
            "--manifest",
            manifest.as_posix(),
            "--repo-root",
            repo.as_posix(),
            "--archive-dir",
            archive_dir.as_posix(),
        ],
    )

    assert build_archive_cli.main() == 0

    out = capsys.readouterr().out.strip()
    assert out.startswith("demo_20260605_165503: succeeded archive=")
    assert "\n" not in out
    assert not out.startswith("{")


def test_batch_cli_output_matches_hdf5_style(tmp_path: Path, monkeypatch, capsys) -> None:
    repo = tmp_path / "repo"
    manifest = make_demo(repo)
    log_path = tmp_path / "commands.log"
    bin_dir = tmp_path / "bin"
    archive_dir = tmp_path / "Archived"
    make_fake_commands(bin_dir, log_path)
    monkeypatch.setenv("ARCHIVE_TEST_LOG", log_path.as_posix())
    monkeypatch.setenv("PATH", bin_dir.as_posix() + os.pathsep + os.environ["PATH"])
    monkeypatch.setattr(
        "sys.argv",
        [
            "build_archive_batch.py",
            "--demos-root",
            manifest.parent.parent.as_posix(),
            "--repo-root",
            repo.as_posix(),
            "--archive-dir",
            archive_dir.as_posix(),
            "--no-update-manifest",
        ],
    )

    assert build_archive_batch_cli.main() == 0

    lines = capsys.readouterr().out.strip().splitlines()
    assert json.loads(lines[0]) == {
        "candidate_count": 1,
        "archive_dir": archive_dir.as_posix(),
        "update_manifest": False,
    }
    assert lines[1].startswith("demo_20260605_165503: succeeded archive=")
    assert json.loads(lines[-1]) == {"processed": 1, "succeeded": 1, "failed": 0, "skipped": 0}
