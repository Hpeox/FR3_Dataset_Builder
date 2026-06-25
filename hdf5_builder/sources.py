"""Raw source loaders for the offline HDF5 builder."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from .context import DemoBuildContext, detect_storage_id


RGB_SHAPE = (480, 640, 3)
DEPTH_SHAPE = (480, 640)
TACTILE_BGR_SHAPE = (700, 400, 3)
TACTILE_FORCE_SHAPE = (35, 20, 3)
TACTILE_RESULTANT_SHAPE = (6,)
XENSE_LEFT = "OG000544"
XENSE_RIGHT = "OG001009"


def require_array(name: str, value: np.ndarray, shape: tuple[int, ...], dtype: np.dtype[Any]) -> np.ndarray:
    arr = np.asarray(value)
    if arr.shape != shape:
        raise RuntimeError(f"{name} shape mismatch: expected {shape}, got {arr.shape}")
    if arr.dtype != dtype:
        raise RuntimeError(f"{name} dtype mismatch: expected {dtype}, got {arr.dtype}")
    return arr


def require_cast_array(
    name: str,
    value: np.ndarray,
    shape: tuple[int, ...],
    dtype: np.dtype[Any],
) -> np.ndarray:
    arr = np.asarray(value)
    if arr.shape != shape:
        raise RuntimeError(f"{name} shape mismatch: expected {shape}, got {arr.shape}")
    return arr.astype(dtype, copy=False)


@dataclass
class ZmqSource:
    ctx: DemoBuildContext

    def __post_init__(self) -> None:
        self.data = np.load(self.ctx.npz_paths["zmq"], allow_pickle=False)
        required = ("floats_58", "gripper_gPO", "gripper_gCU")
        for key in required:
            if key not in self.data:
                raise RuntimeError(f"zmq_telemetry.npz missing required array: {key}")

    def gello_q(self) -> np.ndarray:
        rows = self.ctx.indices("zmq_source_1")
        return require_array("gello_q", self.data["floats_58"][rows, 0:7], (self.ctx.total_steps, 7), np.dtype("float64"))

    def gello_gripper_cmd(self) -> np.ndarray:
        rows = self.ctx.indices("zmq_source_1")
        return require_array(
            "gello_gripper_cmd",
            self.data["floats_58"][rows, 7],
            (self.ctx.total_steps,),
            np.dtype("float64"),
        )

    def robot_slice(self, name: str, start: int, stop: int) -> np.ndarray:
        rows = self.ctx.indices("zmq_source_2")
        return require_array(
            name,
            self.data["floats_58"][rows, start:stop],
            (self.ctx.total_steps, stop - start),
            np.dtype("float64"),
        )

    def o_t_ee(self) -> np.ndarray:
        flat = self.robot_slice("O_T_EE_flat", 36, 52)
        value = flat.reshape(self.ctx.total_steps, 4, 4, order="F")
        expected = np.array([0.0, 0.0, 0.0, 1.0], dtype=np.float64)
        bad = np.flatnonzero(~np.all(np.isclose(value[:, 3, :], expected), axis=1))
        if len(bad):
            raise RuntimeError(f"O_T_EE last-row validation failed at HDF5 row {int(bad[0])}")
        return require_array("O_T_EE", value, (self.ctx.total_steps, 4, 4), np.dtype("float64"))

    def gripper(self, key: str) -> np.ndarray:
        rows = self.ctx.indices("zmq_source_3")
        return require_cast_array(
            key,
            self.data[key][rows],
            (self.ctx.total_steps,),
            np.dtype("uint8"),
        )


@dataclass
class Ft300Source:
    ctx: DemoBuildContext

    def __post_init__(self) -> None:
        self.timestamps = np.load(self.ctx.npz_paths["ft300"], allow_pickle=False)
        if "frame_id" not in self.timestamps:
            raise RuntimeError("ft300_timestamps.npz missing required array: frame_id")
        self.obj = np.load(self.ctx.sensor_paths["ft300"], allow_pickle=True).item()
        if "frames_data" not in self.obj:
            raise RuntimeError("FT300 external file missing frames_data")

    def wrench(self) -> np.ndarray:
        result = np.empty((self.ctx.total_steps, 6), dtype=np.float32)
        source_rows = self.ctx.indices("ft300s")
        frame_ids = self.timestamps["frame_id"]
        frames = self.obj["frames_data"]
        for out_row, source_row in enumerate(source_rows):
            frame_key = f"{int(frame_ids[source_row]):05d}"
            if frame_key not in frames:
                raise RuntimeError(f"FT300 frames_data missing frame {frame_key}")
            result[out_row] = require_cast_array(
                f"FT300 wrench {frame_key}",
                frames[frame_key]["ft300_wrench"],
                (6,),
                np.dtype("float32"),
            )
        return result


@dataclass
class XenseSource:
    ctx: DemoBuildContext

    def __post_init__(self) -> None:
        self.timestamps = np.load(self.ctx.npz_paths["xense"], allow_pickle=False)
        if "frame_id" not in self.timestamps:
            raise RuntimeError("xense_timestamps.npz missing required array: frame_id")
        self.obj = np.load(self.ctx.sensor_paths["xense"], allow_pickle=True).item()
        if "frames_data" not in self.obj:
            raise RuntimeError("Xense external file missing frames_data")

    def tactile_bgr(self) -> np.ndarray:
        return self._stack_pair("rec", TACTILE_BGR_SHAPE, np.dtype("uint8"))

    def force(self) -> np.ndarray:
        return self._stack_pair("force", TACTILE_FORCE_SHAPE, np.dtype("float32"))

    def force_norm(self) -> np.ndarray:
        return self._stack_pair("force_norm", TACTILE_FORCE_SHAPE, np.dtype("float32"))

    def force_resultant(self) -> np.ndarray:
        return self._stack_pair("force_resultant", TACTILE_RESULTANT_SHAPE, np.dtype("float32"))

    def _stack_pair(self, suffix: str, item_shape: tuple[int, ...], dtype: np.dtype[Any]) -> np.ndarray:
        result = np.empty((self.ctx.total_steps, 2, *item_shape), dtype=dtype)
        source_rows = self.ctx.indices("xense_pair")
        frame_ids = self.timestamps["frame_id"]
        frames = self.obj["frames_data"]
        for out_row, source_row in enumerate(source_rows):
            frame_key = f"{int(frame_ids[source_row]):05d}"
            if frame_key not in frames:
                raise RuntimeError(f"Xense frames_data missing frame {frame_key}")
            frame = frames[frame_key]
            for sensor_axis, sensor_id in enumerate((XENSE_LEFT, XENSE_RIGHT)):
                key = f"{sensor_id}_{suffix}"
                if key not in frame:
                    raise RuntimeError(f"Xense frame {frame_key} missing field {key}")
                name = f"Xense {frame_key} {key}"
                if dtype == np.dtype("uint8"):
                    result[out_row, sensor_axis] = require_array(name, frame[key], item_shape, dtype)
                else:
                    result[out_row, sensor_axis] = require_cast_array(name, frame[key], item_shape, dtype)
        return result


@dataclass
class RealSenseSource:
    ctx: DemoBuildContext

    def read_topic_images(self, topic: str) -> list[np.ndarray]:
        try:
            import rosbag2_py
            from rclpy.serialization import deserialize_message
            from rosidl_runtime_py.utilities import get_message
        except Exception as exc:  # pragma: no cover - depends on ROS environment.
            raise RuntimeError(f"required ROS rosbag modules are unavailable: {exc}") from exc

        reader = rosbag2_py.SequentialReader()
        reader.open(
            rosbag2_py.StorageOptions(uri=str(self.ctx.rosbag_uri), storage_id=detect_storage_id(self.ctx.rosbag_uri)),
            rosbag2_py.ConverterOptions("", ""),
        )
        topic_types = {item.name: item.type for item in reader.get_all_topics_and_types()}
        if topic not in topic_types:
            raise RuntimeError(f"rosbag missing required topic: {topic}")
        msg_cls = get_message(topic_types[topic])
        reader.set_filter(rosbag2_py.StorageFilter(topics=[topic]))
        images: list[np.ndarray] = []
        while reader.has_next():
            read_topic, serialized, _recorded = reader.read_next()
            if read_topic != topic:
                continue
            msg = deserialize_message(serialized, msg_cls)
            images.append(decode_image_msg(topic, msg))
        if not images:
            raise RuntimeError(f"rosbag topic has no messages: {topic}")
        return images


def decode_image_msg(topic: str, msg: Any) -> np.ndarray:
    height = int(msg.height)
    width = int(msg.width)
    step = int(msg.step)
    encoding = str(msg.encoding)
    data = bytes(msg.data)
    if encoding == "rgb8":
        if (height, width) != (480, 640):
            raise RuntimeError(f"{topic} rgb8 dimensions mismatch: {(height, width)}")
        raw = np.frombuffer(data, dtype=np.uint8)
        expected = height * step
        if raw.size != expected:
            raise RuntimeError(f"{topic} rgb8 data size mismatch: expected {expected}, got {raw.size}")
        row_major = raw.reshape(height, step)[:, : width * 3]
        return require_array(topic, row_major.reshape(height, width, 3).copy(), RGB_SHAPE, np.dtype("uint8"))
    if encoding == "16UC1":
        if (height, width) != (480, 640):
            raise RuntimeError(f"{topic} 16UC1 dimensions mismatch: {(height, width)}")
        raw = np.frombuffer(data, dtype=np.uint8)
        expected = height * step
        if raw.size != expected:
            raise RuntimeError(f"{topic} 16UC1 data size mismatch: expected {expected}, got {raw.size}")
        row_bytes = raw.reshape(height, step)[:, : width * 2]
        depth = row_bytes.reshape(height, width, 2).copy().view(np.uint16).reshape(height, width)
        if int(getattr(msg, "is_bigendian", 0)):
            depth = depth.byteswap()
        return require_array(topic, depth, DEPTH_SHAPE, np.dtype("uint16"))
    raise RuntimeError(f"{topic} unsupported image encoding: {encoding!r}")


def topic_for(cam: str, kind: str) -> str:
    if kind == "color":
        return f"/{cam}/camera/color/image_raw"
    if kind == "aligned_depth":
        return f"/{cam}/camera/aligned_depth_to_color/image_raw"
    raise ValueError(kind)
