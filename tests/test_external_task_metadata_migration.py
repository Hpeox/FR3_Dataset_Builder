from __future__ import annotations

import hashlib
import json
import subprocess
import zipfile
from pathlib import Path

import h5py

from tools.migrate_external_task_metadata import (
    ArtifactSet,
    TASK_NAME,
    TARGET_SCHEMA_VERSION,
    TaskInstructions,
    apply_migration,
    discover_artifacts,
    inspect_all,
    manifest_with_metadata,
)


INSTRUCTION = "Insert the largest peg into the matching hole."


def make_fixture(tmp_path: Path, *, with_xense_version: bool) -> tuple[Path, str]:
    root = tmp_path / "DATASET"
    archived = root / "Archived"
    archived.mkdir(parents=True)
    demo_id = "demo_20260601_120000"
    h5_path = root / f"{demo_id}.h5"
    with h5py.File(h5_path, "w") as h5:
        h5.attrs["demo_id"] = demo_id
        h5.attrs["schema_version"] = "v0.1"
        h5.attrs["language_instruction"] = "a placeholder string"
        h5.create_dataset("data", data=[1, 2, 3])

    bundle = tmp_path / "bundle"
    member = f"{demo_id}_bundle/demo/manifest.json"
    manifest_path = bundle / member
    manifest_path.parent.mkdir(parents=True)
    manifest = {
        "status": "done",
        "run_id": "run_test",
    }
    if with_xense_version:
        manifest["xense_sdk_version"] = "2.0"
    manifest["rosbag_uri"] = "rosbag"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    extra_path = bundle / f"{demo_id}_bundle/payload.bin"
    extra_path.parent.mkdir(parents=True, exist_ok=True)
    extra_path.write_bytes(b"unchanged payload")
    zip_path = archived / f"{demo_id}.zip"
    subprocess.run(
        ["/usr/bin/zip", "-q", zip_path.as_posix(), member, extra_path.relative_to(bundle).as_posix()],
        cwd=bundle,
        check=True,
    )
    zip_hash = hashlib.sha256(zip_path.read_bytes()).hexdigest()
    sidecar = {
        "demo_id": demo_id,
        "zip_size": zip_path.stat().st_size,
        "zip_sha256": zip_hash,
        "created_at": "unchanged",
    }
    (archived / f"{demo_id}.archive.json").write_text(
        json.dumps(sidecar),
        encoding="utf-8",
    )
    batch = {
        "results": [
            {
                "demo_id": demo_id,
                "status": "succeeded",
                "zip_size": sidecar["zip_size"],
                "zip_sha256": sidecar["zip_sha256"],
            }
        ]
    }
    (archived / "batch_archive_report.json").write_text(
        json.dumps(batch),
        encoding="utf-8",
    )
    return root, member


def instructions() -> TaskInstructions:
    return TaskInstructions((INSTRUCTION,), (1.0,), "source-hash")


def test_manifest_inserts_after_xense_sdk_version():
    manifest = {
        "run_id": "run",
        "xense_sdk_version": "2.0",
        "rosbag_uri": "rosbag",
    }
    updated = manifest_with_metadata(manifest, INSTRUCTION)
    assert list(updated) == [
        "run_id",
        "xense_sdk_version",
        "task_name",
        "language_instruction",
        "rosbag_uri",
    ]


def test_manifest_falls_back_to_run_id():
    manifest = {"status": "done", "run_id": "run", "rosbag_uri": "rosbag"}
    updated = manifest_with_metadata(manifest, INSTRUCTION)
    assert list(updated) == [
        "status",
        "run_id",
        "task_name",
        "language_instruction",
        "rosbag_uri",
    ]


def test_migration_updates_h5_zip_sidecar_and_batch_report(tmp_path):
    root, member = make_fixture(tmp_path, with_xense_version=True)
    artifacts = discover_artifacts(root)
    inferred, members, sidecars = inspect_all(artifacts, instructions())
    state_path = root / "state.json"
    report_path = root / "report.json"

    report = apply_migration(
        root,
        artifacts,
        instructions(),
        inferred,
        members,
        sidecars,
        state_path,
        report_path,
        "/usr/bin/zip",
    )

    assert report["status"] == "completed"
    artifact = artifacts[0]
    with h5py.File(artifact.h5_path, "r") as h5:
        assert h5.attrs["schema_version"] == TARGET_SCHEMA_VERSION
        assert h5.attrs["task_name"] == TASK_NAME
        assert h5.attrs["language_instruction"] == INSTRUCTION
        assert h5["data"][:].tolist() == [1, 2, 3]
    with zipfile.ZipFile(artifact.zip_path) as archive:
        manifest = json.loads(archive.read(member))
        assert manifest["task_name"] == TASK_NAME
        assert manifest["language_instruction"] == INSTRUCTION
        assert archive.read(f"{artifact.demo_id}_bundle/payload.bin") == b"unchanged payload"
        assert archive.testzip() is None
        assert sum(info.filename == member for info in archive.infolist()) == 1
    zip_hash = hashlib.sha256(artifact.zip_path.read_bytes()).hexdigest()
    sidecar = json.loads(artifact.sidecar_path.read_text())
    assert sidecar["created_at"] == "unchanged"
    assert sidecar["zip_size"] == artifact.zip_path.stat().st_size
    assert sidecar["zip_sha256"] == zip_hash
    batch = json.loads(
        (root / "Archived" / "batch_archive_report.json").read_text()
    )
    assert batch["results"][0]["zip_size"] == sidecar["zip_size"]
    assert batch["results"][0]["zip_sha256"] == zip_hash


def test_migration_is_idempotent_and_reuses_state(tmp_path):
    root, _ = make_fixture(tmp_path, with_xense_version=False)
    artifacts = discover_artifacts(root)
    inferred, members, sidecars = inspect_all(artifacts, instructions())
    state_path = root / "state.json"
    report_path = root / "report.json"
    apply_migration(
        root,
        artifacts,
        instructions(),
        inferred,
        members,
        sidecars,
        state_path,
        report_path,
        "/usr/bin/zip",
    )
    state_before = state_path.read_bytes()

    inferred, members, sidecars = inspect_all(artifacts, instructions())
    apply_migration(
        root,
        artifacts,
        instructions(),
        inferred,
        members,
        sidecars,
        state_path,
        report_path,
        "/usr/bin/zip",
    )

    assert json.loads(state_before)["assignments"] == json.loads(
        state_path.read_bytes()
    )["assignments"]
