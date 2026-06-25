from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import h5py
import pytest

import build_hdf5_batch
from hdf5_builder.context import DemoBuildContext, task_metadata_from_manifest
from hdf5_builder.writer import SCHEMA_VERSION, write_attrs


TASK_NAME = "16mm-peg-in-hole"
INSTRUCTION = "Move the object's handle; then stop."


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


@pytest.mark.parametrize(
    "manifest",
    [
        {},
        {"task_name": TASK_NAME},
        {"task_name": ".hidden", "language_instruction": INSTRUCTION},
        {"task_name": "task..name", "language_instruction": INSTRUCTION},
        {"task_name": "task/name", "language_instruction": INSTRUCTION},
        {"task_name": TASK_NAME, "language_instruction": None},
        {"task_name": TASK_NAME, "language_instruction": 123},
        {"task_name": TASK_NAME, "language_instruction": " \n"},
        {"task_name": TASK_NAME, "language_instruction": "move\x00object"},
    ],
)
def test_task_metadata_from_manifest_rejects_invalid_values(manifest):
    with pytest.raises(RuntimeError):
        task_metadata_from_manifest(manifest)


def test_task_metadata_from_manifest_preserves_valid_values():
    assert task_metadata_from_manifest(
        {
            "task_name": TASK_NAME,
            "language_instruction": INSTRUCTION,
        }
    ) == (
        TASK_NAME,
        INSTRUCTION,
    )


def test_single_demo_context_rejects_missing_task_metadata(tmp_path):
    manifest_path = tmp_path / "demo_test" / "manifest.json"
    write_json(manifest_path, {"status": "done"})

    with pytest.raises(
        RuntimeError,
        match="manifest.task_name",
    ):
        DemoBuildContext(
            manifest_path=manifest_path,
            output_path=tmp_path / "output.h5",
            repo_root=tmp_path,
            min_free_gb=0,
        )


def test_batch_dry_run_rejects_missing_task_metadata(tmp_path):
    manifest_path = tmp_path / "demo_test" / "manifest.json"
    write_json(manifest_path, {"status": "done"})

    with pytest.raises(
        RuntimeError,
        match="manifest.task_name",
    ):
        build_hdf5_batch.dry_run_check_demo(
            manifest_path,
            tmp_path / "output.h5",
            tmp_path / "output.build_report.json",
            tmp_path,
        )


def test_write_attrs_uses_manifest_task_metadata_and_v03_schema(tmp_path):
    output_path = tmp_path / "attrs.h5"
    ctx = SimpleNamespace(
        demo_id="demo_test",
        total_steps=3,
        aligned_manifest={"hz": 30.0},
        task_name=TASK_NAME,
        language_instruction=INSTRUCTION,
    )

    with h5py.File(output_path, "w") as h5:
        write_attrs(h5, ctx)

    with h5py.File(output_path, "r") as h5:
        assert SCHEMA_VERSION == "v0.3"
        assert h5.attrs["schema_version"] == "v0.3"
        assert h5.attrs["task_name"] == TASK_NAME
        assert h5.attrs["language_instruction"] == INSTRUCTION
