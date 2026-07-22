from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path


DATASET_BUILDER_ROOT = Path(__file__).resolve().parents[1]
if DATASET_BUILDER_ROOT.as_posix() not in sys.path:
    sys.path.insert(0, DATASET_BUILDER_ROOT.as_posix())


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")


def make_demo(repo_root: Path, demo_id: str = "demo_20260605_165503") -> Path:
    demo_dir = repo_root / "runtime_sessions" / "demos" / demo_id
    aligned_dir = demo_dir / "aligned"
    rosbag_dir = demo_dir / "rosbag"
    frames_dir = repo_root / "runtime_frames"
    config_dir = frames_dir / "20260605_163106"
    for path in (aligned_dir, rosbag_dir, config_dir):
        path.mkdir(parents=True, exist_ok=True)

    npz = {
        "ft300": "ft300_timestamps.npz",
        "xense": "xense_timestamps.npz",
        "realsense": "realsense_metadata.npz",
        "zmq": "zmq_telemetry.npz",
    }
    sensor_paths = {
        "ft300": "runtime_frames/data_FT_20260605_165503.npy",
        "xense": "runtime_frames/data_TAC_20260605_165503.npy",
    }
    write_json(
        demo_dir / "manifest.json",
        {
            "status": "done",
            "task_name": "16mm-peg-in-hole",
            "language_instruction": "Pick up the test object",
            "npz": npz,
            "sensor_paths": sensor_paths,
            "rosbag_uri": "rosbag",
        },
    )
    write_json(
        aligned_dir / "aligned_manifest.json",
        {
            "status": "done",
            "sources": {
                "npz": npz,
                "ft300s_saved_file": sensor_paths["ft300"],
                "xense_saved_file": sensor_paths["xense"],
                "rosbag_uri": "rosbag",
            },
        },
    )
    write_json(aligned_dir / "alignment_config.json", {"mode": "causal"})
    (aligned_dir / "alignment_report.md").write_text("# report\n", encoding="utf-8")
    (aligned_dir / "aligned_index.npz").write_bytes(b"aligned-index")
    for name in npz.values():
        (demo_dir / name).write_bytes(f"{name}\n".encode("ascii"))
    (rosbag_dir / "metadata.yaml").write_text("storage_identifier: mcap\n", encoding="utf-8")
    (rosbag_dir / "rosbag_0.mcap").write_bytes(b"source mcap")
    (frames_dir / "data_FT_20260605_165503.npy").write_bytes(b"ft")
    (frames_dir / "data_TAC_20260605_165503.npy").write_bytes(b"tac")
    (config_dir / "runtime_OG001622").write_bytes(b"left config")
    (config_dir / "runtime_OG001623").write_bytes(b"right config")
    return demo_dir / "manifest.json"


def make_fake_commands(bin_dir: Path, log_path: Path) -> None:
    bin_dir.mkdir(parents=True, exist_ok=True)
    zstd = bin_dir / "zstd"
    zstd.write_text(
        """#!/usr/bin/env python3
import os, shutil, sys
from pathlib import Path
log = Path(os.environ["ARCHIVE_TEST_LOG"])
with log.open("a", encoding="utf-8") as fp:
    fp.write("ZSTD " + " ".join(sys.argv[1:]) + "\\n")
if "-t" in sys.argv:
    raise SystemExit(0)
out = sys.argv[sys.argv.index("-o") + 1]
src = sys.argv[-1]
Path(out).parent.mkdir(parents=True, exist_ok=True)
shutil.copyfile(src, out)
""",
        encoding="utf-8",
    )
    taskset = bin_dir / "taskset"
    taskset.write_text(
        """#!/usr/bin/env python3
import os, sys
from pathlib import Path
log = Path(os.environ["ARCHIVE_TEST_LOG"])
args = sys.argv[1:]
with log.open("a", encoding="utf-8") as fp:
    fp.write("TASKSET " + " ".join(args) + "\\n")
yaml_path = Path(args[args.index("-o") + 1])
text = yaml_path.read_text(encoding="utf-8")
uri_line = [line for line in text.splitlines() if "uri:" in line][0]
uri = uri_line.split(":", 1)[1].strip().strip('"')
out = Path(uri)
out.mkdir(parents=True, exist_ok=True)
(out / "metadata.yaml").write_text("storage_identifier: mcap\\n", encoding="utf-8")
(out / "rosbag_0.mcap").write_bytes(b"converted mcap")
""",
        encoding="utf-8",
    )
    for path in (zstd, taskset):
        path.chmod(path.stat().st_mode | stat.S_IXUSR)
