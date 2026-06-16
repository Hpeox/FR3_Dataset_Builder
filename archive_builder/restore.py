"""Restore archive bundles into raw dataset layout."""

from __future__ import annotations

import json
import shutil
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from .compression import decompress_zstd


def restore_archive(archive_json: Path | None, zip_path: Path | None, restore_root: Path | None, force: bool) -> dict[str, Any]:
    metadata = _load_metadata(archive_json, zip_path)
    zip_file = (zip_path or Path(metadata["archive_path"])).resolve()
    root = (restore_root or Path(metadata["restore_root"])).resolve()
    if not zip_file.exists():
        raise RuntimeError(f"archive ZIP does not exist: {zip_file}")
    with TemporaryDirectory(prefix="datasetbuilder-restore-") as tmp:
        extract_dir = Path(tmp)
        with zipfile.ZipFile(zip_file, "r") as zf:
            zf.extractall(extract_dir)
        bundle_dir = extract_dir / f"{metadata['demo_id']}_bundle"
        if not bundle_dir.is_dir():
            raise RuntimeError(f"bundle root missing in archive: {bundle_dir.name}")
        restored = []
        restored.extend(_restore_demo(bundle_dir, root, metadata["demo_id"], force))
        restored.extend(_restore_runtime_frames(bundle_dir, root, metadata, force))
    return {"demo_id": metadata["demo_id"], "restored": restored}


def _load_metadata(path: Path | None, zip_path: Path | None) -> dict[str, Any]:
    if path is not None:
        return json.loads(path.read_text(encoding="utf-8"))
    if zip_path is None:
        raise RuntimeError("--archive-json or --zip is required")
    with zipfile.ZipFile(zip_path, "r") as zf:
        candidates = [name for name in zf.namelist() if name.endswith("/archive_manifest.json")]
        if len(candidates) != 1:
            raise RuntimeError(f"expected exactly one archive_manifest.json in ZIP, found {len(candidates)}")
        with zf.open(candidates[0]) as fp:
            return json.loads(fp.read().decode("utf-8"))


def _restore_demo(bundle_dir: Path, root: Path, demo_id: str, force: bool) -> list[str]:
    source_demo = bundle_dir / "demo"
    target_demo = root / "runtime_sessions" / "demos" / demo_id
    restored: list[str] = []
    for path in sorted(source_demo.rglob("*")):
        if path.is_dir():
            continue
        rel = path.relative_to(source_demo)
        target = target_demo / rel
        if path.name.endswith(".npz.zst"):
            target = target.with_name(target.name[:-4])
            _ensure_writable(target, force)
            decompress_zstd(path, target)
        else:
            _ensure_writable(target, force)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
        restored.append(target.as_posix())
    return restored


def _restore_runtime_frames(bundle_dir: Path, root: Path, metadata: dict[str, Any], force: bool) -> list[str]:
    restored: list[str] = []
    frames_dir = bundle_dir / "runtime_frames"
    for path in sorted(frames_dir.glob("data_*.npy.zst")):
        target = root / "runtime_frames" / path.name[:-4]
        _ensure_writable(target, force)
        decompress_zstd(path, target)
        restored.append(target.as_posix())
    source_config = frames_dir / "runtime_config"
    selected_config = Path(metadata["selected_tac_runtime_config_timestamp_dir"])
    target_config = root / "runtime_frames" / selected_config.name
    for path in sorted(source_config.iterdir()):
        if path.is_dir():
            continue
        target = target_config / path.name
        _ensure_writable(target, force)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        restored.append(target.as_posix())
    return restored


def _ensure_writable(path: Path, force: bool) -> None:
    if path.exists() and not force:
        raise RuntimeError(f"refusing to overwrite existing path without --force: {path}")
