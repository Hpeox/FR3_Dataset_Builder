"""HDF5 writer for the single-demo offline builder."""

from __future__ import annotations

import os
from typing import Any

import h5py
import hdf5plugin
import numpy as np

from .context import DemoBuildContext
from .sources import Ft300Source, RealSenseSource, XenseSource, ZmqSource, topic_for


SCHEMA_VERSION = "v0.1"
SPATIAL_CHUNK_T = 8
LOWDIM_CHUNK_T_MAX = 512
COMPRESSION = "zstd"
COMPRESSION_LEVEL = 12


def zstd_kwargs(dtype: np.dtype[Any]) -> dict[str, Any]:
    kwargs = dict(hdf5plugin.Zstd(clevel=COMPRESSION_LEVEL))
    if np.dtype(dtype).kind in {"f", "u"} and np.dtype(dtype) != np.dtype("uint8"):
        kwargs["shuffle"] = True
    return kwargs


def assert_dataset_spec(path: str, data: np.ndarray, shape: tuple[int, ...], dtype: np.dtype[Any]) -> None:
    if data.shape != shape:
        raise RuntimeError(f"{path} shape mismatch before write: expected {shape}, got {data.shape}")
    if data.dtype != dtype:
        raise RuntimeError(f"{path} dtype mismatch before write: expected {dtype}, got {data.dtype}")


def create_dataset(
    h5: h5py.File,
    path: str,
    data: np.ndarray,
    shape: tuple[int, ...],
    dtype: np.dtype[Any],
    chunks: tuple[int, ...],
) -> None:
    assert_dataset_spec(path, data, shape, dtype)
    h5.create_dataset(path, data=data, shape=shape, dtype=dtype, chunks=chunks, **zstd_kwargs(dtype))


def create_empty_dataset(
    h5: h5py.File,
    path: str,
    shape: tuple[int, ...],
    dtype: np.dtype[Any],
    chunks: tuple[int, ...],
) -> h5py.Dataset:
    return h5.create_dataset(path, shape=shape, dtype=dtype, chunks=chunks, **zstd_kwargs(dtype))


def write_hdf5(ctx: DemoBuildContext, overwrite: bool = False) -> None:
    if ctx.output_path.exists() and not overwrite:
        raise RuntimeError(f"output already exists; pass --overwrite to replace: {ctx.output_path}")
    if ctx.report_path.exists() and not overwrite:
        raise RuntimeError(f"report already exists; pass --overwrite to replace: {ctx.report_path}")
    if ctx.tmp_output_path.exists():
        raise RuntimeError(f"temporary output already exists; remove it first: {ctx.tmp_output_path}")

    created_tmp = False
    try:
        with h5py.File(ctx.tmp_output_path, "w") as h5:
            created_tmp = True
            write_attrs(h5, ctx)
            write_lowdim(h5, ctx)
            write_realsense(h5, ctx)
            write_tactile(h5, ctx)
        os.replace(ctx.tmp_output_path, ctx.output_path)
        ctx.report.write()
    except Exception:
        if created_tmp and ctx.tmp_output_path.exists():
            ctx.tmp_output_path.unlink()
        raise


def write_attrs(h5: h5py.File, ctx: DemoBuildContext) -> None:
    lowdim_chunk_t = min(ctx.total_steps, LOWDIM_CHUNK_T_MAX)
    h5.attrs["demo_id"] = ctx.demo_id
    h5.attrs["success"] = True
    h5.attrs["total_steps"] = ctx.total_steps
    h5.attrs["schema_version"] = SCHEMA_VERSION
    h5.attrs["nominal_hz"] = ctx.aligned_manifest.get("hz", 30.0)
    # TODO: Read language_instruction from a specific manifest key when annotations exist.
    h5.attrs["language_instruction"] = "a placeholder string"
    h5.attrs["spatial_chunk_t"] = SPATIAL_CHUNK_T
    h5.attrs["lowdim_chunk_t"] = lowdim_chunk_t
    h5.attrs["compression"] = COMPRESSION
    h5.attrs["compression_level"] = COMPRESSION_LEVEL


def write_lowdim(h5: h5py.File, ctx: DemoBuildContext) -> None:
    t = ctx.total_steps
    k = min(t, LOWDIM_CHUNK_T_MAX)
    zmq = ZmqSource(ctx)
    ft = Ft300Source(ctx)
    create_dataset(h5, "/actions/gello_q", zmq.gello_q(), (t, 7), np.dtype("float64"), (k, 7))
    create_dataset(
        h5,
        "/actions/gello_gripper_cmd",
        zmq.gello_gripper_cmd(),
        (t,),
        np.dtype("float64"),
        (k,),
    )
    create_dataset(h5, "/observations/robot_state/q", zmq.robot_slice("q", 8, 15), (t, 7), np.dtype("float64"), (k, 7))
    create_dataset(h5, "/observations/robot_state/dq", zmq.robot_slice("dq", 15, 22), (t, 7), np.dtype("float64"), (k, 7))
    create_dataset(h5, "/observations/robot_state/tau_J", zmq.robot_slice("tau_J", 22, 29), (t, 7), np.dtype("float64"), (k, 7))
    create_dataset(h5, "/observations/robot_state/tau_J_d", zmq.robot_slice("tau_J_d", 29, 36), (t, 7), np.dtype("float64"), (k, 7))
    create_dataset(h5, "/observations/robot_state/O_T_EE", zmq.o_t_ee(), (t, 4, 4), np.dtype("float64"), (k, 4, 4))
    create_dataset(h5, "/observations/robot_state/O_dP_EE", zmq.robot_slice("O_dP_EE", 52, 58), (t, 6), np.dtype("float64"), (k, 6))
    create_dataset(h5, "/observations/gripper/gPO", zmq.gripper("gripper_gPO"), (t,), np.dtype("uint8"), (k,))
    create_dataset(h5, "/observations/gripper/gCU", zmq.gripper("gripper_gCU"), (t,), np.dtype("uint8"), (k,))
    create_dataset(h5, "/observations/ft300s/wrench", ft.wrench(), (t, 6), np.dtype("float32"), (k, 6))


def write_realsense(h5: h5py.File, ctx: DemoBuildContext) -> None:
    t = ctx.total_steps
    rs = RealSenseSource(ctx)
    rgb_top = create_empty_dataset(h5, "/observations/rgb/top", (t, 480, 640, 3), np.dtype("uint8"), (min(t, SPATIAL_CHUNK_T), 480, 640, 3))
    rgb_side = create_empty_dataset(h5, "/observations/rgb/side", (t, 480, 640, 3), np.dtype("uint8"), (min(t, SPATIAL_CHUNK_T), 480, 640, 3))
    rgb_wrist = create_empty_dataset(h5, "/observations/rgb/wrist", (t, 2, 480, 640, 3), np.dtype("uint8"), (min(t, SPATIAL_CHUNK_T), 2, 480, 640, 3))
    rgb_wrist.attrs["camera_names"] = np.asarray(["wrist1", "wrist2"], dtype=h5py.string_dtype("utf-8"))

    depth_top = create_empty_dataset(h5, "/observations/depth/top", (t, 480, 640), np.dtype("uint16"), (min(t, SPATIAL_CHUNK_T), 480, 640))
    depth_side = create_empty_dataset(h5, "/observations/depth/side", (t, 480, 640), np.dtype("uint16"), (min(t, SPATIAL_CHUNK_T), 480, 640))
    depth_wrist = create_empty_dataset(h5, "/observations/depth/wrist", (t, 2, 480, 640), np.dtype("uint16"), (min(t, SPATIAL_CHUNK_T), 2, 480, 640))
    depth_wrist.attrs["camera_names"] = np.asarray(["wrist1", "wrist2"], dtype=h5py.string_dtype("utf-8"))
    for dataset in (depth_top, depth_side, depth_wrist):
        dataset.attrs["unit"] = "millimeter"
        dataset.attrs["encoding"] = "aligned_depth_to_color_uint16"

    fill_topic_dataset(rs, rgb_top, topic_for("cam4", "color"), "realsense_cam4_color")
    fill_topic_dataset(rs, rgb_side, topic_for("cam3", "color"), "realsense_cam3_color")
    fill_topic_dataset(rs, rgb_wrist, topic_for("cam1", "color"), "realsense_cam1_color", sensor_axis=0)
    fill_topic_dataset(rs, rgb_wrist, topic_for("cam2", "color"), "realsense_cam2_color", sensor_axis=1)
    fill_topic_dataset(rs, depth_top, topic_for("cam4", "aligned_depth"), "realsense_cam4_aligned_depth")
    fill_topic_dataset(rs, depth_side, topic_for("cam3", "aligned_depth"), "realsense_cam3_aligned_depth")
    fill_topic_dataset(rs, depth_wrist, topic_for("cam1", "aligned_depth"), "realsense_cam1_aligned_depth", sensor_axis=0)
    fill_topic_dataset(rs, depth_wrist, topic_for("cam2", "aligned_depth"), "realsense_cam2_aligned_depth", sensor_axis=1)


def fill_topic_dataset(
    rs: RealSenseSource,
    dataset: h5py.Dataset,
    topic: str,
    stream: str,
    sensor_axis: int | None = None,
) -> None:
    images = rs.read_topic_images(topic)
    indices = rs.ctx.indices(stream)
    max_index = int(indices.max()) if len(indices) else -1
    if max_index >= len(images):
        raise RuntimeError(f"{stream} source index {max_index} exceeds decoded topic length {len(images)}")
    for out_row, source_index in enumerate(indices):
        image = images[int(source_index)]
        if image.dtype != dataset.dtype:
            raise RuntimeError(f"{dataset.name} row dtype mismatch: expected {dataset.dtype}, got {image.dtype}")
        if sensor_axis is None:
            if image.shape != dataset.shape[1:]:
                raise RuntimeError(f"{dataset.name} row shape mismatch: expected {dataset.shape[1:]}, got {image.shape}")
            dataset[out_row] = image
        else:
            if image.shape != dataset.shape[2:]:
                raise RuntimeError(f"{dataset.name} row shape mismatch: expected {dataset.shape[2:]}, got {image.shape}")
            dataset[out_row, sensor_axis] = image


def write_tactile(h5: h5py.File, ctx: DemoBuildContext) -> None:
    t = ctx.total_steps
    k = min(t, LOWDIM_CHUNK_T_MAX)
    c = min(t, SPATIAL_CHUNK_T)
    xense = XenseSource(ctx)
    tactile_rgb = xense.tactile_rgb()
    tactile_images = create_empty_dataset(
        h5,
        "/observations/tactile_images/rgb",
        (t, 2, 700, 400, 3),
        np.dtype("uint8"),
        (c, 2, 700, 400, 3),
    )
    assert_dataset_spec("/observations/tactile_images/rgb", tactile_rgb, (t, 2, 700, 400, 3), np.dtype("uint8"))
    tactile_images.attrs["sensor_names"] = np.asarray(["left", "right"], dtype=h5py.string_dtype("utf-8"))
    tactile_images[...] = tactile_rgb
    del tactile_rgb

    force = xense.force()
    create_dataset(h5, "/observations/tactile/force", force, (t, 2, 35, 20, 3), np.dtype("float32"), (c, 2, 35, 20, 3))
    h5["/observations/tactile/force"].attrs["sensor_names"] = np.asarray(["left", "right"], dtype=h5py.string_dtype("utf-8"))
    del force
    force_norm = xense.force_norm()
    create_dataset(h5, "/observations/tactile/force_norm", force_norm, (t, 2, 35, 20, 3), np.dtype("float32"), (c, 2, 35, 20, 3))
    h5["/observations/tactile/force_norm"].attrs["sensor_names"] = np.asarray(["left", "right"], dtype=h5py.string_dtype("utf-8"))
    del force_norm
    force_resultant = xense.force_resultant()
    create_dataset(h5, "/observations/tactile/force_resultant", force_resultant, (t, 2, 6), np.dtype("float32"), (k, 2, 6))
    h5["/observations/tactile/force_resultant"].attrs["sensor_names"] = np.asarray(["left", "right"], dtype=h5py.string_dtype("utf-8"))
