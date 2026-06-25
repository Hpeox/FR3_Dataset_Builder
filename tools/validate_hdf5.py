#!/usr/bin/env python3
"""Validate the first-phase single-demo HDF5 schema."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

import h5py
import hdf5plugin  # noqa: F401 - registers HDF5 compression plugins.
import numpy as np


DATASETS: dict[str, tuple[str, tuple[Any, ...]]] = {
    "/actions/gello_q": ("float64", ("T", 7)),
    "/actions/gello_gripper_cmd": ("float64", ("T",)),
    "/observations/robot_state/q": ("float64", ("T", 7)),
    "/observations/robot_state/dq": ("float64", ("T", 7)),
    "/observations/robot_state/tau_J": ("float64", ("T", 7)),
    "/observations/robot_state/tau_J_d": ("float64", ("T", 7)),
    "/observations/robot_state/O_T_EE": ("float64", ("T", 4, 4)),
    "/observations/robot_state/O_dP_EE": ("float64", ("T", 6)),
    "/observations/gripper/gPO": ("uint8", ("T",)),
    "/observations/gripper/gCU": ("uint8", ("T",)),
    "/observations/ft300s/wrench": ("float32", ("T", 6)),
    "/observations/rgb/top": ("uint8", ("T", 480, 640, 3)),
    "/observations/rgb/side": ("uint8", ("T", 480, 640, 3)),
    "/observations/rgb/wrist": ("uint8", ("T", 2, 480, 640, 3)),
    "/observations/depth/top": ("uint16", ("T", 480, 640)),
    "/observations/depth/side": ("uint16", ("T", 480, 640)),
    "/observations/depth/wrist": ("uint16", ("T", 2, 480, 640)),
    "/observations/tactile_images/bgr": ("uint8", ("T", 2, 700, 400, 3)),
    "/observations/tactile/force": ("float32", ("T", 2, 35, 20, 3)),
    "/observations/tactile/force_norm": ("float32", ("T", 2, 35, 20, 3)),
    "/observations/tactile/force_resultant": ("float32", ("T", 2, 6)),
}

REQUIRED_ATTRS = (
    "demo_id",
    "success",
    "total_steps",
    "schema_version",
    "nominal_hz",
    "task_name",
    "language_instruction",
    "spatial_chunk_t",
    "lowdim_chunk_t",
    "compression",
    "compression_level",
)
TASK_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True, help="HDF5 file to validate")
    return parser.parse_args()


def attr_strings(value: Any) -> list[str]:
    return [item.decode("utf-8") if isinstance(item, bytes) else str(item) for item in value]


def expected_shape(spec: tuple[Any, ...], t: int) -> tuple[int, ...]:
    return tuple(t if item == "T" else int(item) for item in spec)


def main() -> int:
    args = parse_args()
    errors: list[str] = []
    with h5py.File(args.input, "r") as h5:
        for attr in REQUIRED_ATTRS:
            if attr not in h5.attrs:
                errors.append(f"missing root attr: {attr}")
        for attr in ("task_name", "language_instruction"):
            value = h5.attrs.get(attr)
            if isinstance(value, bytes):
                value = value.decode("utf-8", errors="replace")
            if not isinstance(value, str) or not value.strip():
                errors.append(f"{attr} root attr must be a non-empty string")
        task_name = h5.attrs.get("task_name")
        if isinstance(task_name, bytes):
            task_name = task_name.decode("utf-8", errors="replace")
        if (
            isinstance(task_name, str)
            and (
                not TASK_NAME_PATTERN.fullmatch(task_name)
                or ".." in task_name
            )
        ):
            errors.append("task_name root attr must be a valid task slug")
        if h5.attrs.get("schema_version") != "v0.3":
            errors.append(
                f"schema_version attr mismatch: expected v0.3, "
                f"got {h5.attrs.get('schema_version')!r}"
            )
        t = int(h5.attrs.get("total_steps", -1))
        for path, (dtype, shape_spec) in DATASETS.items():
            if path not in h5:
                errors.append(f"missing dataset: {path}")
                continue
            dataset = h5[path]
            shape = expected_shape(shape_spec, t)
            if tuple(dataset.shape) != shape:
                errors.append(f"{path} shape mismatch: expected {shape}, got {tuple(dataset.shape)}")
            if dataset.dtype != np.dtype(dtype):
                errors.append(f"{path} dtype mismatch: expected {dtype}, got {dataset.dtype}")
        for path in ("/observations/rgb/wrist", "/observations/depth/wrist"):
            if path in h5 and attr_strings(h5[path].attrs.get("camera_names", [])) != ["wrist1", "wrist2"]:
                errors.append(f"{path} camera_names attr mismatch")
        for path in (
            "/observations/tactile_images/bgr",
            "/observations/tactile/force",
            "/observations/tactile/force_norm",
            "/observations/tactile/force_resultant",
        ):
            if path in h5 and attr_strings(h5[path].attrs.get("sensor_names", [])) != ["left", "right"]:
                errors.append(f"{path} sensor_names attr mismatch")
        for path in ("/observations/depth/top", "/observations/depth/side", "/observations/depth/wrist"):
            if path in h5:
                if h5[path].attrs.get("unit") != "millimeter":
                    errors.append(f"{path} unit attr mismatch")
                if h5[path].attrs.get("encoding") != "aligned_depth_to_color_uint16":
                    errors.append(f"{path} encoding attr mismatch")
    print(json.dumps({"status": "failed" if errors else "ok", "errors": errors}, indent=2, ensure_ascii=True))
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
