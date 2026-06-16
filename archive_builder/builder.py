"""High-level archive build orchestration."""

from __future__ import annotations

import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .compression import compress_zstd
from .context import ArchiveContext
from .manifest import mark_archieved, sha256_file, write_archive_json, write_json
from .rosbag_convert import MCAP_CONFIG, convert_rosbag_to_mcap
from .zip_writer import create_zip_from_bundle


DEMO_NPZ_ORDER = ("ft300", "realsense", "xense", "zmq")


def build_archive(
    ctx: ArchiveContext,
    *,
    overwrite: bool = False,
    keep_staging: bool = False,
    update_manifest: bool = False,
) -> dict[str, Any]:
    if ctx.archive_path.exists() and not overwrite:
        raise RuntimeError(f"archive already exists: {ctx.archive_path}")
    if ctx.archive_json_path.exists() and not overwrite:
        raise RuntimeError(f"archive metadata already exists: {ctx.archive_json_path}")
    ctx.archive_dir.mkdir(parents=True, exist_ok=True)
    staging_dir = ctx.archive_dir / ".staging" / f"{ctx.demo_id}.{os.getpid()}"
    if staging_dir.exists():
        shutil.rmtree(staging_dir)
    bundle_dir = staging_dir / ctx.bundle_name
    logs_dir = staging_dir / "logs"
    validations: dict[str, Any] = {}
    try:
        _stage_small_files(ctx, bundle_dir)
        validations["rosbag"] = convert_rosbag_to_mcap(
            ctx.rosbag_dir,
            bundle_dir / "demo" / "rosbag",
            staging_dir / "rosbag_work",
        )
        validations["demo_npz"] = _compress_demo_npz(ctx, bundle_dir, logs_dir)
        validations["external_runtime_frames"] = _compress_external_frames(ctx, bundle_dir, logs_dir)
        _stage_runtime_config(ctx, bundle_dir)

        internal_manifest = _archive_payload(
            ctx,
            archive_path=ctx.archive_path,
            zip_size=0,
            zip_sha256="computed-in-external-sidecar",
            validations=validations,
            manifest_updated=False,
        )
        write_json(bundle_dir / "archive_manifest.json", internal_manifest)

        tmp_zip = ctx.archive_path.with_name(ctx.archive_path.name + ".tmp")
        if tmp_zip.exists():
            tmp_zip.unlink()
        create_zip_from_bundle(bundle_dir, tmp_zip)
        zip_hash = sha256_file(tmp_zip)
        zip_size = tmp_zip.stat().st_size
        archive_manifest = _archive_payload(
            ctx,
            archive_path=ctx.archive_path,
            zip_size=zip_size,
            zip_sha256=zip_hash,
            validations=validations,
            manifest_updated=update_manifest,
        )
        os.replace(tmp_zip, ctx.archive_path)
        write_archive_json(ctx.archive_json_path, archive_manifest)
        if update_manifest:
            mark_archieved(ctx.manifest_path)
        return archive_manifest
    finally:
        if not keep_staging and staging_dir.exists():
            shutil.rmtree(staging_dir)


def _stage_small_files(ctx: ArchiveContext, bundle_dir: Path) -> None:
    demo_dir = bundle_dir / "demo"
    aligned_dir = demo_dir / "aligned"
    aligned_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ctx.manifest_path, demo_dir / "manifest.json")
    shutil.copy2(ctx.aligned_dir / "aligned_manifest.json", aligned_dir / "aligned_manifest.json")
    shutil.copy2(ctx.aligned_dir / "alignment_config.json", aligned_dir / "alignment_config.json")
    shutil.copy2(ctx.aligned_dir / "aligned_index.npz", aligned_dir / "aligned_index.npz")
    report = ctx.aligned_dir / "alignment_report.md"
    if report.exists():
        shutil.copy2(report, aligned_dir / "alignment_report.md")


def _compress_demo_npz(ctx: ArchiveContext, bundle_dir: Path, logs_dir: Path) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key in DEMO_NPZ_ORDER:
        source = ctx.npz_paths[key]
        output = bundle_dir / "demo" / f"{source.name}.zst"
        result[key] = compress_zstd(source, output, logs_dir / "zstd_demo_npz.log")
    return result


def _compress_external_frames(ctx: ArchiveContext, bundle_dir: Path, logs_dir: Path) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key in ("ft300", "xense"):
        source = ctx.sensor_paths[key]
        output = bundle_dir / "runtime_frames" / f"{source.name}.zst"
        result[key] = compress_zstd(source, output, logs_dir / "zstd_runtime_frames.log")
    return result


def _stage_runtime_config(ctx: ArchiveContext, bundle_dir: Path) -> None:
    config_dir = bundle_dir / "runtime_frames" / "runtime_config"
    config_dir.mkdir(parents=True, exist_ok=True)
    for name, source in ctx.selected_tac_config_files.items():
        shutil.copy2(source, config_dir / name)


def _archive_payload(
    ctx: ArchiveContext,
    *,
    archive_path: Path,
    zip_size: int,
    zip_sha256: str,
    validations: dict[str, Any],
    manifest_updated: bool,
) -> dict[str, Any]:
    return {
        "demo_id": ctx.demo_id,
        "archive_format": "datasetbuilder.demo_archive.v1",
        "archive_path": archive_path.as_posix(),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_root": ctx.repo_root.as_posix(),
        "restore_root": ctx.repo_root.as_posix(),
        "zip_size": zip_size,
        "zip_sha256": zip_sha256,
        "compression": {
            "npy": {"command": "zstd -T0 -19", "validation": "zstd -t"},
            "npz": {"command": "zstd -T0 -19", "validation": "zstd -t"},
            "rosbag": {"storage_id": "mcap", "storage_config": MCAP_CONFIG},
            "zip": {"zip64": True, "store_suffixes": [".zst", ".mcap", ".npz"]},
        },
        "validation_results": validations,
        "source_paths": ctx.source_path_report(),
        "selected_tac_runtime_config_timestamp_dir": ctx.selected_tac_config_dir.as_posix(),
        "manifest_updated": manifest_updated,
    }
