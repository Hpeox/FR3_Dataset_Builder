#!/usr/bin/env python3
"""Read-only raw demo inspection helper for HDF5 schema source mapping."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import numpy as np
from numpy.lib import format as npy_format


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def resolve_demo_path(demo_dir: Path, value: str | None) -> Path | None:
    if not value:
        return None
    path = Path(value)
    return path if path.is_absolute() else demo_dir / path


def resolve_repo_path(repo_root: Path, value: str | None) -> Path | None:
    if not value:
        return None
    path = Path(value)
    return path if path.is_absolute() else repo_root / path


def npz_summary(path: Path) -> dict[str, Any]:
    result: dict[str, Any] = {"path": rel(path), "exists": path.exists()}
    if not path.exists():
        return result
    with np.load(path, allow_pickle=True) as data:
        arrays: dict[str, Any] = {}
        for key in data.files:
            arr = data[key]
            arrays[key] = {
                "shape": list(arr.shape),
                "dtype": str(arr.dtype),
                "sample": sample_value(arr),
            }
        result["arrays"] = arrays
    return result


def npy_header_summary(path: Path) -> dict[str, Any]:
    result: dict[str, Any] = {"path": rel(path), "exists": path.exists()}
    if not path.exists():
        return result
    with path.open("rb") as fp:
        version = npy_format.read_magic(fp)
        shape, fortran_order, dtype = npy_format._read_array_header(fp, version)
    result.update(
        {
            "shape": list(shape),
            "dtype": str(dtype),
            "fortran_order": bool(fortran_order),
            "size_bytes": path.stat().st_size,
        }
    )
    if dtype.hasobject:
        result["sample_note"] = "object dtype; not loaded by this bounded inspection helper"
    else:
        arr = np.load(path, mmap_mode="r", allow_pickle=False)
        result["sample"] = sample_value(arr)
    return result


def sample_value(arr: np.ndarray) -> Any:
    if arr.size == 0:
        return None
    value = arr.reshape(-1)[0]
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return repr(value)


def discover_processable_demos(demos_root: Path) -> list[Path]:
    demos: list[Path] = []
    for demo_dir in sorted(demos_root.glob("demo_*")):
        manifest_path = demo_dir / "manifest.json"
        aligned_manifest_path = demo_dir / "aligned" / "aligned_manifest.json"
        if not manifest_path.exists() or not aligned_manifest_path.exists():
            continue
        try:
            manifest = read_json(manifest_path)
            aligned_manifest = read_json(aligned_manifest_path)
        except Exception:
            continue
        if manifest.get("status") == "done" and aligned_manifest.get("status") == "done":
            demos.append(demo_dir)
    return demos


def inspect_demo(repo_root: Path, demo_dir: Path) -> dict[str, Any]:
    manifest = read_json(demo_dir / "manifest.json")
    aligned_manifest = read_json(demo_dir / "aligned" / "aligned_manifest.json")
    alignment_config = read_json(demo_dir / "aligned" / "alignment_config.json")

    npz = {}
    for key, value in (manifest.get("npz") or {}).items():
        path = resolve_demo_path(demo_dir, value)
        npz[key] = npz_summary(path) if path is not None else {"path": None, "exists": False}

    external = {}
    for key, value in (manifest.get("sensor_paths") or {}).items():
        path = resolve_repo_path(repo_root, value)
        external[key] = npy_header_summary(path) if path is not None else {"path": None, "exists": False}

    aligned_index_path = demo_dir / "aligned" / "aligned_index.npz"
    aligned = npz_summary(aligned_index_path)
    if "arrays" in aligned:
        # Keep aligned_index output compact: samples are not useful for the large mapping table.
        for item in aligned["arrays"].values():
            item.pop("sample", None)

    return {
        "demo_id": demo_dir.name,
        "manifest": {
            "status": manifest.get("status"),
            "run_id": manifest.get("run_id"),
            "rosbag_uri": manifest.get("rosbag_uri"),
            "sensor_paths": manifest.get("sensor_paths"),
            "npz": manifest.get("npz"),
            "frame_counts": manifest.get("frame_counts"),
            "required_topics": (manifest.get("realsense_image_readiness") or {}).get("required_topics"),
        },
        "aligned_manifest": {
            "status": aligned_manifest.get("status"),
            "schema_version": aligned_manifest.get("schema_version"),
            "sample_count": aligned_manifest.get("sample_count"),
            "valid_count": aligned_manifest.get("valid_count"),
            "base": aligned_manifest.get("base"),
            "base_kind": aligned_manifest.get("base_kind"),
            "mode": aligned_manifest.get("mode"),
            "sources": aligned_manifest.get("sources"),
            "stream_names": sorted((aligned_manifest.get("streams") or {}).keys()),
        },
        "alignment_config": {
            "base": alignment_config.get("base"),
            "base_kind": alignment_config.get("base_kind"),
            "mode": alignment_config.get("mode"),
            "hz": alignment_config.get("hz"),
            "sources": alignment_config.get("sources"),
            "stream_names": sorted((alignment_config.get("streams") or {}).keys()),
        },
        "aligned_index": aligned,
        "npz": npz,
        "external_npy": external,
        "rosbag": {
            "path": rel(resolve_demo_path(demo_dir, manifest.get("rosbag_uri")) or demo_dir / "rosbag"),
            "metadata_exists": (resolve_demo_path(demo_dir, manifest.get("rosbag_uri")) or demo_dir / "rosbag").joinpath("metadata.yaml").exists(),
            "mcap_exists": (resolve_demo_path(demo_dir, manifest.get("rosbag_uri")) or demo_dir / "rosbag").joinpath("rosbag_0.mcap").exists(),
        },
    }


def rel(path: Path | None) -> str | None:
    if path is None:
        return None
    try:
        return path.resolve().relative_to(Path.cwd().resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--runtime-root",
        "--repo-root",
        dest="runtime_root",
        type=Path,
        default=Path.cwd(),
        help="runtime data root containing runtime_sessions and runtime_frames",
    )
    parser.add_argument("--limit", type=int, default=3)
    parser.add_argument("--demo", action="append", default=[])
    args = parser.parse_args()

    runtime_root = args.runtime_root.resolve()
    demos_root = runtime_root / "runtime_sessions" / "demos"
    if args.demo:
        demos = [(demos_root / name if not Path(name).is_absolute() else Path(name)) for name in args.demo]
    else:
        demos = discover_processable_demos(demos_root)[: args.limit]

    payload = {
        "runtime_root": runtime_root.as_posix(),
        "processability_rule": "manifest.status == 'done' and aligned/aligned_manifest.json status == 'done'",
        "selected_demos": [demo.name for demo in demos],
        "demos": [inspect_demo(runtime_root, demo) for demo in demos],
    }
    print(json.dumps(payload, indent=2, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
