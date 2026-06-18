"""Demo build context, path resolution, and aligned-index policy."""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from .report import BuildReport


DATASET_BUILDER_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPO_ROOT = DATASET_BUILDER_ROOT.parent
EXTERNAL_DATASET_ROOT = Path("/data/external/DATASET")
REQUIRED_NPZ = ("ft300", "xense", "realsense", "zmq")
REQUIRED_SENSOR_PATHS = ("ft300", "xense")
REQUIRED_STREAMS = (
    "zmq_source_1",
    "zmq_source_2",
    "zmq_source_3",
    "ft300s",
    "xense_pair",
    "realsense_cam1_color",
    "realsense_cam1_aligned_depth",
    "realsense_cam2_color",
    "realsense_cam2_aligned_depth",
    "realsense_cam3_color",
    "realsense_cam3_aligned_depth",
    "realsense_cam4_color",
    "realsense_cam4_aligned_depth",
)


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise RuntimeError(f"required JSON file is missing: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def resolve_demo_path(demo_dir: Path, value: str | None, label: str) -> Path:
    if not value:
        raise RuntimeError(f"missing required demo-relative path: {label}")
    path = Path(value)
    resolved = path if path.is_absolute() else demo_dir / path
    if not resolved.exists():
        raise RuntimeError(f"missing required {label}: raw={value!r}, resolved={resolved}")
    return resolved


def resolve_repo_path(repo_root: Path, value: str | None, label: str) -> Path:
    if not value:
        raise RuntimeError(f"missing required runtime-root-relative path: {label}")
    path = Path(value)
    resolved = path if path.is_absolute() else repo_root / path
    if not resolved.exists():
        raise RuntimeError(f"missing required {label}: raw={value!r}, resolved={resolved}")
    return resolved


def required_image_topics(manifest: dict[str, Any]) -> list[str]:
    postcheck = manifest.get("realsense_rosbag_postcheck") or {}
    readiness = manifest.get("realsense_image_readiness") or {}
    topics = postcheck.get("required_topics") or readiness.get("required_topics") or []
    return [str(topic) for topic in topics]


def detect_storage_id(bag_dir: Path) -> str:
    metadata_file = bag_dir / "metadata.yaml"
    if metadata_file.exists():
        import re

        match = re.search(
            r"storage_identifier:\s*([A-Za-z0-9_\-]+)",
            metadata_file.read_text(encoding="utf-8", errors="ignore"),
        )
        if match:
            return match.group(1)
    if list(bag_dir.glob("*.mcap")):
        return "mcap"
    return "sqlite3"


@dataclass
class DemoBuildContext:
    manifest_path: Path
    output_path: Path
    repo_root: Path = DEFAULT_REPO_ROOT
    report_path: Path | None = None
    min_free_gb: float = 20.0
    emit_warnings: bool = True
    manifest: dict[str, Any] = field(init=False)
    aligned_manifest: dict[str, Any] = field(init=False)
    alignment_config: dict[str, Any] = field(init=False)
    aligned_index: dict[str, np.ndarray] = field(init=False)
    demo_dir: Path = field(init=False)
    aligned_dir: Path = field(init=False)
    tmp_output_path: Path = field(init=False)
    npz_paths: dict[str, Path] = field(init=False)
    sensor_paths: dict[str, Path] = field(init=False)
    rosbag_uri: Path = field(init=False)
    image_topics: list[str] = field(init=False)
    aligned_rows: np.ndarray = field(init=False)
    resolved_indices: dict[str, np.ndarray] = field(init=False)
    report: BuildReport = field(init=False)

    def __post_init__(self) -> None:
        self.manifest_path = self.manifest_path.resolve()
        self.output_path = self.output_path.resolve()
        self.repo_root = self.repo_root.resolve()
        if self.report_path is None:
            self.report_path = self.output_path.with_suffix(".build_report.json")
        else:
            self.report_path = self.report_path.resolve()
        self.tmp_output_path = self.output_path.with_suffix(self.output_path.suffix + ".tmp")
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        self.report_path.parent.mkdir(parents=True, exist_ok=True)
        self._check_free_space()

        self.demo_dir = self.manifest_path.parent
        self.aligned_dir = self.demo_dir / "aligned"
        self.manifest = read_json(self.manifest_path)
        self.aligned_manifest = read_json(self.aligned_dir / "aligned_manifest.json")
        self.alignment_config = read_json(self.aligned_dir / "alignment_config.json")
        self._check_processable()
        self._check_source_consistency()
        self.npz_paths = self._resolve_npz_paths()
        self.sensor_paths = self._resolve_sensor_paths()
        self.rosbag_uri = resolve_demo_path(self.demo_dir, self.manifest.get("rosbag_uri"), "rosbag_uri")
        self.image_topics = required_image_topics(self.manifest)
        if not self.image_topics:
            raise RuntimeError("manifest does not define required RealSense image topics")
        self.aligned_index = self._load_aligned_index()
        self.report = BuildReport(
            demo_id=self.demo_id,
            manifest_path=self.manifest_path,
            output_path=self.output_path,
            report_path=self.report_path,
            total_aligned_rows=len(self.aligned_index["t_ns"]),
            source_paths=self.source_path_report(),
            emit_warnings=self.emit_warnings,
        )
        self.aligned_rows, self.resolved_indices = self._resolve_export_rows()
        self.report.exported_rows = len(self.aligned_rows)

    @property
    def demo_id(self) -> str:
        return self.demo_dir.name

    @property
    def total_steps(self) -> int:
        return int(len(self.aligned_rows))

    def source_path_report(self) -> dict[str, str]:
        return {
            "manifest": self.manifest_path.as_posix(),
            "aligned_manifest": (self.aligned_dir / "aligned_manifest.json").as_posix(),
            "aligned_index": (self.aligned_dir / "aligned_index.npz").as_posix(),
            "alignment_config": (self.aligned_dir / "alignment_config.json").as_posix(),
            "rosbag_uri": self.rosbag_uri.as_posix(),
            "ft300": self.sensor_paths["ft300"].as_posix(),
            "xense": self.sensor_paths["xense"].as_posix(),
            **{f"npz_{key}": value.as_posix() for key, value in self.npz_paths.items()},
        }

    def indices(self, stream: str) -> np.ndarray:
        return self.resolved_indices[stream]

    def valid_mask(self, stream: str) -> np.ndarray:
        key = f"{stream}_valid"
        if key not in self.aligned_index:
            raise RuntimeError(f"aligned_index missing required validity array: {key}")
        return self.aligned_index[key][self.aligned_rows]

    def _check_free_space(self) -> None:
        free = shutil.disk_usage(self.output_path.parent).free
        required = int(float(self.min_free_gb) * 1024**3)
        if free < required:
            raise RuntimeError(
                f"not enough free space under {self.output_path.parent}: "
                f"free={free / 1024**3:.2f} GiB, required={self.min_free_gb:.2f} GiB"
            )

    def _check_processable(self) -> None:
        if self.manifest.get("status") != "done":
            raise RuntimeError(f"manifest.status must be 'done', got {self.manifest.get('status')!r}")
        if self.aligned_manifest.get("status") != "done":
            raise RuntimeError(
                f"aligned_manifest.status must be 'done', got {self.aligned_manifest.get('status')!r}"
            )
        for path in (
            self.aligned_dir / "aligned_index.npz",
            self.aligned_dir / "alignment_config.json",
        ):
            if not path.exists():
                raise RuntimeError(f"missing required alignment artifact: {path}")

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
        result: dict[str, Path] = {}
        for key in REQUIRED_NPZ:
            result[key] = resolve_demo_path(self.demo_dir, manifest_npz.get(key), f"npz.{key}")
        return result

    def _resolve_sensor_paths(self) -> dict[str, Path]:
        manifest_sensor_paths = self.manifest.get("sensor_paths") or {}
        result: dict[str, Path] = {}
        for key in REQUIRED_SENSOR_PATHS:
            result[key] = resolve_repo_path(
                self.repo_root,
                manifest_sensor_paths.get(key),
                f"sensor_paths.{key}",
            )
        return result

    def _load_aligned_index(self) -> dict[str, np.ndarray]:
        path = self.aligned_dir / "aligned_index.npz"
        with np.load(path, allow_pickle=False) as data:
            arrays = {key: data[key] for key in data.files}
        if "t_ns" not in arrays:
            raise RuntimeError("aligned_index missing required array: t_ns")
        expected = int(self.aligned_manifest.get("sample_count"))
        actual = len(arrays["t_ns"])
        if expected != actual:
            raise RuntimeError(f"aligned sample_count mismatch: manifest={expected}, aligned_index={actual}")
        if actual <= 0:
            raise RuntimeError("aligned_index has no timesteps")
        return arrays

    def _resolve_export_rows(self) -> tuple[np.ndarray, dict[str, np.ndarray]]:
        total = len(self.aligned_index["t_ns"])
        full_rows = np.arange(total, dtype=np.int64)
        drop_prefix = 0
        for stream in REQUIRED_STREAMS:
            key = f"{stream}_index"
            if stream == "xense_pair" and "xense_pair_source_index" in self.aligned_index:
                key = "xense_pair_source_index"
            if key not in self.aligned_index:
                raise RuntimeError(f"aligned_index missing required index array: {key}")
            indices = self.aligned_index[key]
            nonnegative = np.flatnonzero(indices >= 0)
            if len(nonnegative) == 0:
                raise RuntimeError(f"required stream has no usable source index: {stream}")
            drop_prefix = max(drop_prefix, int(nonnegative[0]))

        rows = full_rows[drop_prefix:]
        if drop_prefix:
            self.report.dropped_leading_rows = full_rows[:drop_prefix].astype(int).tolist()
            self.report.warn(
                reason="dropped_leading_rows_without_previous_frame",
                action="removed_from_hdf5_output",
                rows=self.report.dropped_leading_rows,
            )

        if "sample_valid" in self.aligned_index:
            sample_valid = self.aligned_index["sample_valid"]
            for hdf5_row, aligned_row in enumerate(rows):
                if not bool(sample_valid[aligned_row]):
                    self.report.warn(
                        hdf5_row=int(hdf5_row),
                        aligned_row=int(aligned_row),
                        stream="sample_valid",
                        reason="global_sample_invalid",
                        action="export_row_with_stream_level_policy",
                    )

        resolved: dict[str, np.ndarray] = {}
        for stream in REQUIRED_STREAMS:
            key = f"{stream}_index"
            if stream == "xense_pair" and "xense_pair_source_index" in self.aligned_index:
                key = "xense_pair_source_index"
            valid_key = f"{stream}_valid"
            resolved[stream] = self._resolve_stream_indices(stream, key, valid_key, rows)
        return rows, resolved

    def _resolve_stream_indices(
        self,
        stream: str,
        index_key: str,
        valid_key: str,
        rows: np.ndarray,
    ) -> np.ndarray:
        raw = self.aligned_index[index_key]
        valid = self.aligned_index.get(valid_key)
        result = np.empty(len(rows), dtype=np.int64)
        previous: int | None = None
        for aligned_row in range(0, int(rows[0]) + 1 if len(rows) else 0):
            value = int(raw[aligned_row])
            if value >= 0:
                previous = value
        for hdf5_row, aligned_row in enumerate(rows):
            value = int(raw[aligned_row])
            if valid is not None and not bool(valid[aligned_row]):
                self.report.warn(
                    hdf5_row=int(hdf5_row),
                    aligned_row=int(aligned_row),
                    stream=stream,
                    reason="stream_invalid",
                    action="use_nonnegative_index" if value >= 0 else "reuse_previous_frame",
                    source_index=value,
                )
            if value >= 0:
                previous = value
                result[hdf5_row] = value
                continue
            if previous is None:
                raise RuntimeError(
                    f"stream {stream} has index -1 at aligned row {aligned_row} without previous frame"
                )
            self.report.warn(
                hdf5_row=int(hdf5_row),
                aligned_row=int(aligned_row),
                stream=stream,
                reason="missing_source_index",
                action="reuse_previous_frame",
                source_index=int(previous),
            )
            result[hdf5_row] = previous
        if np.any(result < 0):
            raise RuntimeError(f"internal error: resolved negative index for stream {stream}")
        return result
