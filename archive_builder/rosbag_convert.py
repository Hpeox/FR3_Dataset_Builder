"""Rosbag conversion helpers for archive builds."""

from __future__ import annotations

import subprocess
from pathlib import Path


MCAP_CONFIG = {
    "noChunking": False,
    "compression": "Zstd",
    "compressionLevel": "Slow",
    "chunkSize": 67108864,
    "noChunkCRC": False,
    "forceCompression": False,
}


def convert_rosbag_to_mcap(source_rosbag_dir: Path, output_rosbag_dir: Path, work_dir: Path) -> dict[str, object]:
    output_rosbag_dir.parent.mkdir(parents=True, exist_ok=True)
    work_dir.mkdir(parents=True, exist_ok=True)
    storage_config = work_dir / "mcap_zstd_slow_64m.yaml"
    convert_config = work_dir / "convert_zstd_slow_64m.yaml"
    log_path = work_dir / "rosbag_convert.log"
    _write_storage_config(storage_config)
    _write_convert_config(convert_config, output_rosbag_dir, storage_config)
    command = [
        "taskset",
        "-c",
        "10,11",
        "ros2",
        "bag",
        "convert",
        "-i",
        source_rosbag_dir.as_posix(),
        "-o",
        convert_config.as_posix(),
    ]
    result = subprocess.run(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    log_path.write_text(result.stdout or "", encoding="utf-8")
    if result.returncode != 0:
        tail = "\n".join((result.stdout or "").splitlines()[-20:])
        raise RuntimeError(f"ros2 bag convert failed ({result.returncode}) for {source_rosbag_dir}\n{tail}")
    metadata = output_rosbag_dir / "metadata.yaml"
    mcap = output_rosbag_dir / "rosbag_0.mcap"
    if not metadata.exists():
        raise RuntimeError(f"converted rosbag metadata.yaml is missing: {metadata}")
    if not mcap.exists() or mcap.stat().st_size <= 0:
        raise RuntimeError(f"converted rosbag_0.mcap is missing or empty: {mcap}")
    return {
        "source": source_rosbag_dir.as_posix(),
        "output": output_rosbag_dir.as_posix(),
        "command": command,
        "return_code": result.returncode,
        "metadata": metadata.as_posix(),
        "mcap": mcap.as_posix(),
        "log": log_path.as_posix(),
        "storage_config": MCAP_CONFIG,
    }


def _write_storage_config(path: Path) -> None:
    path.write_text(
        "\n".join(
            [
                "noChunking: false",
                'compression: "Zstd"',
                'compressionLevel: "Slow"',
                "chunkSize: 67108864",
                "noChunkCRC: false",
                "forceCompression: false",
                "",
            ]
        ),
        encoding="utf-8",
    )


def _write_convert_config(path: Path, output_rosbag_dir: Path, storage_config: Path) -> None:
    path.write_text(
        "\n".join(
            [
                "output_bags:",
                f'  - uri: "{output_rosbag_dir.as_posix()}"',
                "    storage_id: mcap",
                f'    storage_config_uri: "{storage_config.as_posix()}"',
                "    all_topics: true",
                "    all_services: true",
                "    all_actions: true",
                "",
            ]
        ),
        encoding="utf-8",
    )

