"""Archive build context and raw resource discovery."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


DATASET_BUILDER_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPO_ROOT = DATASET_BUILDER_ROOT.parent
DATASET_ROOT = Path("/data/internal/DATASET")
DEFAULT_ARCHIVE_ROOT = DATASET_ROOT / "archives"
SINGLE_ARCHIVE_ROOT = DATASET_BUILDER_ROOT / "outputs"
BATCH_ARCHIVE_ROOT = DATASET_ROOT / "Archived"
REQUIRED_NPZ = ("ft300", "xense", "realsense", "zmq")
REQUIRED_SENSOR_PATHS = ("ft300", "xense")
TAC_CONFIG_SENSOR_FILE_CANDIDATES = {
    "left": ("runtime_OG001622", "runtime_OG000544"),
    "right": ("runtime_OG001623", "runtime_OG001009"),
}


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise RuntimeError(f"required JSON file is missing: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(path.name + ".tmp")
    tmp_path.write_text(json.dumps(payload, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    tmp_path.replace(path)


def is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def resolve_demo_path(demo_dir: Path, value: str | None, label: str) -> Path:
    if not value:
        raise RuntimeError(f"missing required demo-relative path: {label}")
    path = Path(value)
    resolved = path if path.is_absolute() else demo_dir / path
    if not resolved.exists():
        raise RuntimeError(f"missing required {label}: raw={value!r}, resolved={resolved}")
    return resolved.resolve()


def resolve_repo_path(repo_root: Path, value: str | None, label: str) -> Path:
    if not value:
        raise RuntimeError(f"missing required runtime-root-relative path: {label}")
    path = Path(value)
    resolved = path if path.is_absolute() else repo_root / path
    if not resolved.exists():
        raise RuntimeError(f"missing required {label}: raw={value!r}, resolved={resolved}")
    return resolved.resolve()


def resolve_archive_root(path: Path) -> Path:
    return path.resolve() if path.is_absolute() else (DATASET_BUILDER_ROOT / path).resolve()


def require_regular_file(path: Path, label: str) -> None:
    if path.is_symlink():
        raise RuntimeError(f"{label} must not be a symlink: {path}")
    if not path.is_file():
        raise RuntimeError(f"{label} must be a regular file: {path}")


def select_existing_regular_file(directory: Path, candidates: tuple[str, ...], label: str) -> tuple[str, Path]:
    for name in candidates:
        path = directory / name
        if path.exists():
            require_regular_file(path, label)
            return name, path.resolve()
    expected = ", ".join(candidates)
    raise RuntimeError(f"missing required {label}; expected one of: {expected} under {directory}")


@dataclass
class ArchiveContext:
    manifest_path: Path
    archive_dir: Path = DEFAULT_ARCHIVE_ROOT
    repo_root: Path = DEFAULT_REPO_ROOT
    manifest: dict[str, Any] = field(init=False)
    aligned_manifest: dict[str, Any] = field(init=False)
    demo_dir: Path = field(init=False)
    demo_id: str = field(init=False)
    aligned_dir: Path = field(init=False)
    archive_path: Path = field(init=False)
    archive_json_path: Path = field(init=False)
    bundle_name: str = field(init=False)
    npz_paths: dict[str, Path] = field(init=False)
    sensor_paths: dict[str, Path] = field(init=False)
    rosbag_dir: Path = field(init=False)
    selected_tac_config_dir: Path = field(init=False)
    selected_tac_config_files: dict[str, Path] = field(init=False)

    def __post_init__(self) -> None:
        self.manifest_path = self.manifest_path.resolve()
        self.repo_root = self.repo_root.resolve()
        self.archive_dir = resolve_archive_root(self.archive_dir)
        self.demo_dir = self.manifest_path.parent
        self.demo_id = self.demo_dir.name
        self.aligned_dir = self.demo_dir / "aligned"
        self.archive_path = self.archive_dir / f"{self.demo_id}.zip"
        self.archive_json_path = self.archive_dir / f"{self.demo_id}.archive.json"
        self.bundle_name = f"{self.demo_id}_bundle"

        self.manifest = read_json(self.manifest_path)
        self.aligned_manifest = read_json(self.aligned_dir / "aligned_manifest.json")
        self._check_processable()
        self._check_source_consistency()
        self.npz_paths = self._resolve_npz_paths()
        self.sensor_paths = self._resolve_sensor_paths()
        self.rosbag_dir = resolve_demo_path(self.demo_dir, self.manifest.get("rosbag_uri"), "rosbag_uri")
        self._check_required_demo_files()
        self.selected_tac_config_dir = select_tac_runtime_config(
            self.repo_root,
            self.sensor_paths["xense"],
        )
        self.selected_tac_config_files = self._resolve_tac_config_files()

    def source_path_report(self) -> dict[str, str]:
        return {
            "manifest": self.manifest_path.as_posix(),
            "aligned_manifest": (self.aligned_dir / "aligned_manifest.json").as_posix(),
            "aligned_index": (self.aligned_dir / "aligned_index.npz").as_posix(),
            "alignment_config": (self.aligned_dir / "alignment_config.json").as_posix(),
            "alignment_report": (self.aligned_dir / "alignment_report.md").as_posix(),
            "rosbag_uri": self.rosbag_dir.as_posix(),
            "ft300": self.sensor_paths["ft300"].as_posix(),
            "xense": self.sensor_paths["xense"].as_posix(),
            "tac_runtime_config_dir": self.selected_tac_config_dir.as_posix(),
            **{f"npz_{key}": value.as_posix() for key, value in self.npz_paths.items()},
        }

    def _check_processable(self) -> None:
        if self.manifest.get("status") != "done":
            raise RuntimeError(f"manifest.status must be 'done', got {self.manifest.get('status')!r}")
        if self.aligned_manifest.get("status") != "done":
            raise RuntimeError(
                f"aligned_manifest.status must be 'done', got {self.aligned_manifest.get('status')!r}"
            )

    def _check_source_consistency(self) -> None:
        sources = self.aligned_manifest.get("sources") or {}
        if dict(self.manifest.get("npz") or {}) != dict(sources.get("npz") or {}):
            raise RuntimeError("manifest npz paths do not match aligned_manifest.sources.npz")
        sensor_paths = self.manifest.get("sensor_paths") or {}
        pairs = {
            "ft300": "ft300s_saved_file",
            "xense": "xense_saved_file",
        }
        for manifest_key, source_key in pairs.items():
            if sensor_paths.get(manifest_key) != sources.get(source_key):
                raise RuntimeError(
                    f"manifest sensor_paths.{manifest_key} does not match "
                    f"aligned_manifest.sources.{source_key}"
                )
        if self.manifest.get("rosbag_uri") != sources.get("rosbag_uri"):
            raise RuntimeError("manifest rosbag_uri does not match aligned_manifest.sources.rosbag_uri")

    def _resolve_npz_paths(self) -> dict[str, Path]:
        manifest_npz = self.manifest.get("npz") or {}
        return {
            key: resolve_demo_path(self.demo_dir, manifest_npz.get(key), f"npz.{key}")
            for key in REQUIRED_NPZ
        }

    def _resolve_sensor_paths(self) -> dict[str, Path]:
        manifest_sensor_paths = self.manifest.get("sensor_paths") or {}
        return {
            key: resolve_repo_path(self.repo_root, manifest_sensor_paths.get(key), f"sensor_paths.{key}")
            for key in REQUIRED_SENSOR_PATHS
        }

    def _check_required_demo_files(self) -> None:
        required = [
            (self.manifest_path, "manifest"),
            (self.aligned_dir / "aligned_index.npz", "aligned_index"),
            (self.aligned_dir / "aligned_manifest.json", "aligned_manifest"),
            (self.aligned_dir / "alignment_config.json", "alignment_config"),
        ]
        for path, label in required:
            require_regular_file(path, label)
        metadata = self.rosbag_dir / "metadata.yaml"
        if not metadata.exists():
            raise RuntimeError(f"missing required rosbag metadata: {metadata}")

    def _resolve_tac_config_files(self) -> dict[str, Path]:
        result = {}
        for _role, candidates in TAC_CONFIG_SENSOR_FILE_CANDIDATES.items():
            name, path = select_existing_regular_file(
                self.selected_tac_config_dir,
                candidates,
                "TAC runtime config",
            )
            result[name] = path
        return result


def select_tac_runtime_config(repo_root: Path, tac_npy_path: Path) -> Path:
    match = re.fullmatch(r"data_TAC_(\d{8}_\d{6})\.npy", tac_npy_path.name)
    if not match:
        raise RuntimeError(f"TAC file name does not contain expected timestamp: {tac_npy_path.name}")
    tac_timestamp = match.group(1)
    runtime_frames = repo_root / "runtime_frames"
    if not runtime_frames.is_dir():
        raise RuntimeError(f"runtime_frames directory is missing: {runtime_frames}")
    candidates = [
        path
        for path in runtime_frames.iterdir()
        if path.is_dir()
        and re.fullmatch(r"\d{8}_\d{6}", path.name)
        and path.name < tac_timestamp
    ]
    if not candidates:
        raise RuntimeError(f"no runtime config directory earlier than TAC timestamp {tac_timestamp}")
    return sorted(candidates, key=lambda path: path.name)[-1].resolve()
