"""Dry-run validation for archive builds."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .context import ArchiveContext, require_regular_file


def dry_run_check_archive(ctx: ArchiveContext, *, overwrite: bool = False) -> dict[str, Any]:
    metadata = ctx.rosbag_dir / "metadata.yaml"
    mcap = ctx.rosbag_dir / "rosbag_0.mcap"
    require_regular_file(metadata, "rosbag metadata.yaml")
    require_regular_file(mcap, "rosbag rosbag_0.mcap")
    if mcap.stat().st_size <= 0:
        raise RuntimeError(f"rosbag rosbag_0.mcap must be non-empty: {mcap}")

    target_conflict = (ctx.archive_path.exists() or ctx.archive_json_path.exists()) and not overwrite
    return {
        "archive_path": ctx.archive_path.as_posix(),
        "archive_json_path": ctx.archive_json_path.as_posix(),
        "archive_exists": ctx.archive_path.exists(),
        "archive_json_exists": ctx.archive_json_path.exists(),
        "target_conflict": target_conflict,
        "would_overwrite": bool(overwrite and (ctx.archive_path.exists() or ctx.archive_json_path.exists())),
        "npz_paths": {key: path.as_posix() for key, path in ctx.npz_paths.items()},
        "sensor_paths": {key: path.as_posix() for key, path in ctx.sensor_paths.items()},
        "rosbag_uri": ctx.rosbag_dir.as_posix(),
        "selected_tac_runtime_config_dir": ctx.selected_tac_config_dir.as_posix(),
        "selected_tac_runtime_config_files": {
            key: path.as_posix() for key, path in ctx.selected_tac_config_files.items()
        },
    }

