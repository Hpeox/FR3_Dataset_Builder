#!/usr/bin/env python3
"""Migrate legacy external HDF5 and ZIP artifacts to task metadata schema v0.2."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import random
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import h5py


DATASET_ROOT = Path("/data/external/DATASET")
TASK_NAME = "16mm-peg-in-hole"
TARGET_SCHEMA_VERSION = "v0.2"
LEGACY_SCHEMA_VERSION = "v0.1"
LEGACY_INSTRUCTION = "a placeholder string"
STATE_NAME = "task_metadata_migration_state.json"
REPORT_NAME = "task_metadata_migration_report.json"
WEIGHT_SUM_ABS_TOL = 1e-9


@dataclass(frozen=True)
class ArtifactSet:
    demo_id: str
    h5_path: Path
    zip_path: Path
    sidecar_path: Path


@dataclass(frozen=True)
class TaskInstructions:
    texts: tuple[str, ...]
    weights: tuple[float, ...]
    source_sha256: str


def require_system_python() -> None:
    executable = Path(sys.executable).resolve()
    expected = Path("/usr/bin/python3").resolve()
    if executable != expected:
        raise RuntimeError(
            f"this migration must run with /usr/bin/python3, got {sys.executable}"
        )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(
            json.dumps(payload, indent=2, ensure_ascii=True) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"cannot read valid JSON from {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"JSON root must be an object: {path}")
    return payload


def load_task_instructions(path: Path) -> TaskInstructions:
    payload = read_json(path)
    if set(payload) != {"instructions"}:
        raise RuntimeError(
            f'{path}: root must contain only the "instructions" field'
        )
    items = payload["instructions"]
    if not isinstance(items, list) or not items:
        raise RuntimeError(f'{path}: "instructions" must be a non-empty array')

    texts: list[str] = []
    raw_weights: list[float] = []
    for index, item in enumerate(items):
        if not isinstance(item, dict) or set(item) != {"text", "weight"}:
            raise RuntimeError(
                f'{path}: instructions[{index}] must contain only "text" and "weight"'
            )
        text = item["text"]
        if not isinstance(text, str) or not text.strip() or "\x00" in text:
            raise RuntimeError(
                f"{path}: instructions[{index}].text must be a non-empty string "
                "without NUL characters"
            )
        weight = item["weight"]
        if isinstance(weight, bool) or not isinstance(weight, (int, float)):
            raise RuntimeError(
                f"{path}: instructions[{index}].weight must be a number"
            )
        weight = float(weight)
        if not math.isfinite(weight):
            raise RuntimeError(
                f"{path}: instructions[{index}].weight must be finite"
            )
        if weight != -1.0 and not 0.0 < weight < 1.0:
            raise RuntimeError(
                f"{path}: instructions[{index}].weight must be -1 or satisfy "
                "0 < weight < 1"
            )
        texts.append(text.strip())
        raw_weights.append(weight)

    explicit_sum = sum(weight for weight in raw_weights if weight != -1.0)
    automatic_count = sum(weight == -1.0 for weight in raw_weights)
    if automatic_count:
        if explicit_sum >= 1.0:
            raise RuntimeError(
                f"{path}: explicit weights must sum to less than 1 when automatic "
                "entries exist"
            )
        automatic_weight = (1.0 - explicit_sum) / automatic_count
        weights = tuple(
            automatic_weight if weight == -1.0 else weight
            for weight in raw_weights
        )
    else:
        if not math.isclose(
            explicit_sum,
            1.0,
            rel_tol=0.0,
            abs_tol=WEIGHT_SUM_ABS_TOL,
        ):
            raise RuntimeError(f"{path}: explicit weights must sum to 1")
        weights = tuple(raw_weights)
    return TaskInstructions(tuple(texts), weights, sha256_file(path))


def discover_artifacts(dataset_root: Path) -> tuple[ArtifactSet, ...]:
    archive_root = dataset_root / "Archived"
    h5_by_id = {path.stem: path for path in dataset_root.glob("demo_*.h5")}
    zip_by_id = {path.stem: path for path in archive_root.glob("demo_*.zip")}
    sidecar_by_id = {
        path.name.removesuffix(".archive.json"): path
        for path in archive_root.glob("demo_*.archive.json")
    }
    id_sets = {
        "HDF5": set(h5_by_id),
        "ZIP": set(zip_by_id),
        "sidecar": set(sidecar_by_id),
    }
    all_ids = set().union(*id_sets.values())
    errors = []
    for label, ids in id_sets.items():
        missing = sorted(all_ids - ids)
        if missing:
            errors.append(f"{label} missing for: {', '.join(missing)}")
    if errors:
        raise RuntimeError("artifact correspondence check failed:\n" + "\n".join(errors))
    if not all_ids:
        raise RuntimeError(f"no demo artifacts found under {dataset_root}")
    return tuple(
        ArtifactSet(
            demo_id=demo_id,
            h5_path=h5_by_id[demo_id],
            zip_path=zip_by_id[demo_id],
            sidecar_path=sidecar_by_id[demo_id],
        )
        for demo_id in sorted(all_ids)
    )


def decode_attr(value: Any) -> Any:
    return value.decode("utf-8") if isinstance(value, bytes) else value


def inspect_h5(artifact: ArtifactSet, allowed: set[str]) -> str | None:
    with h5py.File(artifact.h5_path, "r") as h5:
        demo_id = decode_attr(h5.attrs.get("demo_id"))
        if demo_id != artifact.demo_id:
            raise RuntimeError(
                f"{artifact.h5_path}: demo_id {demo_id!r} does not match filename"
            )
        schema = decode_attr(h5.attrs.get("schema_version"))
        task = decode_attr(h5.attrs.get("task_name"))
        instruction = decode_attr(h5.attrs.get("language_instruction"))
    if (
        schema == LEGACY_SCHEMA_VERSION
        and task is None
        and instruction == LEGACY_INSTRUCTION
    ):
        return None
    if (
        schema == TARGET_SCHEMA_VERSION
        and task == TASK_NAME
        and isinstance(instruction, str)
        and instruction in allowed
    ):
        return instruction
    raise RuntimeError(
        f"{artifact.h5_path}: unsupported or conflicting task metadata "
        f"schema={schema!r}, task_name={task!r}, "
        f"language_instruction={instruction!r}"
    )


def zip_manifest_member(archive: zipfile.ZipFile) -> str:
    candidates = [
        name
        for name in archive.namelist()
        if name.endswith("/demo/manifest.json")
    ]
    if len(candidates) != 1:
        raise RuntimeError(
            f"expected exactly one */demo/manifest.json, found {len(candidates)}"
        )
    return candidates[0]


def inspect_zip(artifact: ArtifactSet, allowed: set[str]) -> tuple[str, str | None]:
    try:
        with zipfile.ZipFile(artifact.zip_path) as archive:
            member = zip_manifest_member(archive)
            payload = json.loads(archive.read(member).decode("utf-8"))
            duplicates = [
                info.filename
                for info in archive.infolist()
                if info.filename == member
            ]
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, zipfile.BadZipFile) as exc:
        raise RuntimeError(f"cannot inspect ZIP {artifact.zip_path}: {exc}") from exc
    if len(duplicates) != 1:
        raise RuntimeError(f"{artifact.zip_path}: duplicate manifest member {member}")
    if not isinstance(payload, dict):
        raise RuntimeError(f"{artifact.zip_path}: demo manifest root must be an object")
    task = payload.get("task_name")
    instruction = payload.get("language_instruction")
    if task is None and instruction is None:
        return member, None
    if task == TASK_NAME and isinstance(instruction, str) and instruction in allowed:
        return member, instruction
    raise RuntimeError(
        f"{artifact.zip_path}: conflicting manifest task metadata "
        f"task_name={task!r}, language_instruction={instruction!r}"
    )


def inspect_sidecar(artifact: ArtifactSet) -> dict[str, Any]:
    payload = read_json(artifact.sidecar_path)
    if payload.get("demo_id") != artifact.demo_id:
        raise RuntimeError(
            f"{artifact.sidecar_path}: demo_id does not match filename"
        )
    if not isinstance(payload.get("zip_size"), int):
        raise RuntimeError(f"{artifact.sidecar_path}: zip_size must be an integer")
    zip_hash = payload.get("zip_sha256")
    if not isinstance(zip_hash, str) or len(zip_hash) != 64:
        raise RuntimeError(
            f"{artifact.sidecar_path}: zip_sha256 must be a SHA-256 hex string"
        )
    return payload


def infer_existing_instruction(
    h5_instruction: str | None,
    zip_instruction: str | None,
    demo_id: str,
) -> str | None:
    existing = {
        value for value in (h5_instruction, zip_instruction) if value is not None
    }
    if len(existing) > 1:
        raise RuntimeError(
            f"{demo_id}: HDF5 and ZIP contain different language instructions"
        )
    return next(iter(existing), None)


def new_state(
    dataset_root: Path,
    artifacts: tuple[ArtifactSet, ...],
    instructions: TaskInstructions,
    inferred: dict[str, str | None],
) -> dict[str, Any]:
    chooser = random.SystemRandom()
    assignments = {
        artifact.demo_id: (
            inferred[artifact.demo_id]
            or chooser.choices(
                instructions.texts,
                weights=instructions.weights,
                k=1,
            )[0]
        )
        for artifact in artifacts
    }
    return {
        "state_schema_version": 1,
        "dataset_root": dataset_root.as_posix(),
        "task_name": TASK_NAME,
        "target_hdf5_schema_version": TARGET_SCHEMA_VERSION,
        "instruction_source_sha256": instructions.source_sha256,
        "assignments": assignments,
        "completed": [],
    }


def validate_state(
    state: dict[str, Any],
    dataset_root: Path,
    artifacts: tuple[ArtifactSet, ...],
    instructions: TaskInstructions,
) -> None:
    expected_ids = {artifact.demo_id for artifact in artifacts}
    assignments = state.get("assignments")
    if (
        state.get("state_schema_version") != 1
        or state.get("dataset_root") != dataset_root.as_posix()
        or state.get("task_name") != TASK_NAME
        or state.get("target_hdf5_schema_version") != TARGET_SCHEMA_VERSION
        or state.get("instruction_source_sha256") != instructions.source_sha256
        or not isinstance(assignments, dict)
        or set(assignments) != expected_ids
    ):
        raise RuntimeError("migration state does not match the current dataset/config")
    allowed = set(instructions.texts)
    invalid = {
        demo_id: value
        for demo_id, value in assignments.items()
        if not isinstance(value, str) or value not in allowed
    }
    if invalid:
        raise RuntimeError(f"migration state contains invalid assignments: {invalid}")
    completed = state.get("completed")
    if not isinstance(completed, list) or not set(completed).issubset(expected_ids):
        raise RuntimeError("migration state contains an invalid completed list")


def manifest_with_metadata(
    manifest: dict[str, Any],
    instruction: str,
) -> dict[str, Any]:
    if "task_name" in manifest or "language_instruction" in manifest:
        if (
            manifest.get("task_name") == TASK_NAME
            and manifest.get("language_instruction") == instruction
        ):
            return manifest
        raise RuntimeError("manifest already contains conflicting task metadata")
    anchor = "xense_sdk_version" if "xense_sdk_version" in manifest else "run_id"
    if anchor not in manifest:
        raise RuntimeError("manifest has neither xense_sdk_version nor run_id")
    updated: dict[str, Any] = {}
    for key, value in manifest.items():
        updated[key] = value
        if key == anchor:
            updated["task_name"] = TASK_NAME
            updated["language_instruction"] = instruction
    return updated


def update_h5(artifact: ArtifactSet, instruction: str) -> None:
    with h5py.File(artifact.h5_path, "r+") as h5:
        h5.attrs["schema_version"] = TARGET_SCHEMA_VERSION
        h5.attrs["task_name"] = TASK_NAME
        h5.attrs["language_instruction"] = instruction
        h5.flush()
    with h5py.File(artifact.h5_path, "r") as h5:
        actual = (
            decode_attr(h5.attrs.get("schema_version")),
            decode_attr(h5.attrs.get("task_name")),
            decode_attr(h5.attrs.get("language_instruction")),
        )
    expected = (TARGET_SCHEMA_VERSION, TASK_NAME, instruction)
    if actual != expected:
        raise RuntimeError(
            f"{artifact.h5_path}: HDF5 metadata verification failed: {actual!r}"
        )


def update_zip(
    artifact: ArtifactSet,
    member: str,
    instruction: str,
    zip_command: str,
) -> None:
    with zipfile.ZipFile(artifact.zip_path) as archive:
        manifest = json.loads(archive.read(member).decode("utf-8"))
    updated = manifest_with_metadata(manifest, instruction)
    if updated == manifest:
        return

    with tempfile.TemporaryDirectory(
        prefix=f".{artifact.demo_id}.manifest.",
        dir=artifact.zip_path.parent,
    ) as temporary:
        staging_root = Path(temporary)
        staged_manifest = staging_root / member
        staged_manifest.parent.mkdir(parents=True)
        staged_manifest.write_text(
            json.dumps(updated, indent=2, ensure_ascii=True) + "\n",
            encoding="utf-8",
        )
        result = subprocess.run(
            [
                zip_command,
                "-q",
                "-T",
                artifact.zip_path.as_posix(),
                member,
            ],
            cwd=staging_root,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
        if result.returncode != 0:
            tail = "\n".join(result.stdout.splitlines()[-20:])
            raise RuntimeError(
                f"zip update failed for {artifact.zip_path} "
                f"with exit code {result.returncode}:\n{tail}"
            )


def update_sidecar(
    artifact: ArtifactSet,
    payload: dict[str, Any],
    zip_size: int,
    zip_hash: str,
) -> None:
    payload["zip_size"] = zip_size
    payload["zip_sha256"] = zip_hash
    atomic_write_json(artifact.sidecar_path, payload)


def update_batch_report(
    batch_report_path: Path,
    updates: dict[str, tuple[int, str]],
) -> int:
    payload = read_json(batch_report_path)
    results = payload.get("results")
    if not isinstance(results, list):
        raise RuntimeError(f"{batch_report_path}: results must be an array")
    changed = 0
    for record in results:
        if not isinstance(record, dict) or record.get("status") != "succeeded":
            continue
        demo_id = record.get("demo_id")
        if demo_id not in updates:
            continue
        zip_size, zip_hash = updates[demo_id]
        if record.get("zip_size") != zip_size or record.get("zip_sha256") != zip_hash:
            record["zip_size"] = zip_size
            record["zip_sha256"] = zip_hash
            changed += 1
    if changed:
        atomic_write_json(batch_report_path, payload)
    return changed


def inspect_all(
    artifacts: tuple[ArtifactSet, ...],
    instructions: TaskInstructions,
) -> tuple[
    dict[str, str | None],
    dict[str, str],
    dict[str, dict[str, Any]],
]:
    allowed = set(instructions.texts)
    inferred: dict[str, str | None] = {}
    members: dict[str, str] = {}
    sidecars: dict[str, dict[str, Any]] = {}
    errors: list[str] = []
    for artifact in artifacts:
        try:
            h5_instruction = inspect_h5(artifact, allowed)
            member, zip_instruction = inspect_zip(artifact, allowed)
            inferred[artifact.demo_id] = infer_existing_instruction(
                h5_instruction,
                zip_instruction,
                artifact.demo_id,
            )
            members[artifact.demo_id] = member
            sidecars[artifact.demo_id] = inspect_sidecar(artifact)
        except RuntimeError as exc:
            errors.append(str(exc))
    if errors:
        raise RuntimeError("migration preflight failed:\n" + "\n".join(errors))
    return inferred, members, sidecars


def verify_demo(
    artifact: ArtifactSet,
    instruction: str,
    expected_member: str,
) -> tuple[int, str]:
    allowed = {instruction}
    if inspect_h5(artifact, allowed) != instruction:
        raise RuntimeError(f"{artifact.demo_id}: HDF5 verification failed")
    member, zip_instruction = inspect_zip(artifact, allowed)
    if member != expected_member or zip_instruction != instruction:
        raise RuntimeError(f"{artifact.demo_id}: ZIP manifest verification failed")
    with zipfile.ZipFile(artifact.zip_path) as archive:
        bad_member = archive.testzip()
    if bad_member is not None:
        raise RuntimeError(
            f"{artifact.zip_path}: CRC verification failed at {bad_member}"
        )
    zip_size = artifact.zip_path.stat().st_size
    zip_hash = sha256_file(artifact.zip_path)
    return zip_size, zip_hash


def migration_summary(
    artifacts: tuple[ArtifactSet, ...],
    inferred: dict[str, str | None],
    state_exists: bool,
) -> dict[str, Any]:
    already_migrated = sum(value is not None for value in inferred.values())
    return {
        "artifact_count": len(artifacts),
        "already_migrated_or_partial": already_migrated,
        "not_started": len(artifacts) - already_migrated,
        "state_exists": state_exists,
        "task_name": TASK_NAME,
        "target_hdf5_schema_version": TARGET_SCHEMA_VERSION,
    }


def apply_migration(
    dataset_root: Path,
    artifacts: tuple[ArtifactSet, ...],
    instructions: TaskInstructions,
    inferred: dict[str, str | None],
    members: dict[str, str],
    sidecars: dict[str, dict[str, Any]],
    state_path: Path,
    report_path: Path,
    zip_command: str,
) -> dict[str, Any]:
    if state_path.exists():
        state = read_json(state_path)
        validate_state(state, dataset_root, artifacts, instructions)
    else:
        state = new_state(dataset_root, artifacts, instructions, inferred)
        atomic_write_json(state_path, state)

    assignments: dict[str, str] = state["assignments"]
    completed = set(state["completed"])
    batch_updates: dict[str, tuple[int, str]] = {}
    started = time.monotonic()
    last_heartbeat = started
    heartbeat_step = max(1, math.ceil(len(artifacts) * 0.05))
    processed_since_heartbeat = 0

    for index, artifact in enumerate(artifacts, start=1):
        instruction = assignments[artifact.demo_id]
        update_h5(artifact, instruction)
        update_zip(
            artifact,
            members[artifact.demo_id],
            instruction,
            zip_command,
        )
        zip_size, zip_hash = verify_demo(
            artifact,
            instruction,
            members[artifact.demo_id],
        )
        update_sidecar(
            artifact,
            sidecars[artifact.demo_id],
            zip_size,
            zip_hash,
        )
        batch_updates[artifact.demo_id] = (zip_size, zip_hash)
        if artifact.demo_id not in completed:
            completed.add(artifact.demo_id)
            state["completed"] = sorted(completed)
            atomic_write_json(state_path, state)

        processed_since_heartbeat += 1
        now = time.monotonic()
        if (
            index < len(artifacts)
            and processed_since_heartbeat >= heartbeat_step
            and now - last_heartbeat >= 120.0
        ):
            print(
                json.dumps(
                    {
                        "progress": f"{index}/{len(artifacts)}",
                        "completed_percent": round(index * 100 / len(artifacts), 1),
                        "elapsed_seconds": round(now - started, 1),
                    }
                ),
                flush=True,
            )
            last_heartbeat = now
            processed_since_heartbeat = 0

    batch_report = dataset_root / "Archived" / "batch_archive_report.json"
    batch_records_updated = update_batch_report(batch_report, batch_updates)
    report = {
        "status": "completed",
        "artifact_count": len(artifacts),
        "task_name": TASK_NAME,
        "target_hdf5_schema_version": TARGET_SCHEMA_VERSION,
        "state_path": state_path.as_posix(),
        "batch_archive_records_updated": batch_records_updated,
        "elapsed_seconds": round(time.monotonic() - started, 1),
    }
    atomic_write_json(report_path, report)
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-root", type=Path, default=DATASET_ROOT)
    parser.add_argument(
        "--instruction-file",
        type=Path,
        default=(
            Path(__file__).resolve().parents[2]
            / "TaskInstruction"
            / f"{TASK_NAME}.json"
        ),
    )
    parser.add_argument("--state", type=Path, default=None)
    parser.add_argument("--report", type=Path, default=None)
    parser.add_argument("--zip-command", default="/usr/bin/zip")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="perform migration; without this flag only preflight checks run",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        require_system_python()
        dataset_root = args.dataset_root.expanduser().resolve()
        instruction_path = args.instruction_file.expanduser().resolve()
        state_path = (
            args.state.expanduser().resolve()
            if args.state
            else dataset_root / STATE_NAME
        )
        report_path = (
            args.report.expanduser().resolve()
            if args.report
            else dataset_root / REPORT_NAME
        )
        if shutil.which(args.zip_command) is None:
            raise RuntimeError(f"zip command is unavailable: {args.zip_command}")
        instructions = load_task_instructions(instruction_path)
        artifacts = discover_artifacts(dataset_root)
        inferred, members, sidecars = inspect_all(artifacts, instructions)
        if state_path.exists():
            validate_state(
                read_json(state_path),
                dataset_root,
                artifacts,
                instructions,
            )
        print(
            json.dumps(
                {
                    "mode": "apply" if args.apply else "dry-run",
                    **migration_summary(
                        artifacts,
                        inferred,
                        state_path.exists(),
                    ),
                },
                indent=2,
            ),
            flush=True,
        )
        if not args.apply:
            return 0
        report = apply_migration(
            dataset_root,
            artifacts,
            instructions,
            inferred,
            members,
            sidecars,
            state_path,
            report_path,
            args.zip_command,
        )
        print(json.dumps(report, indent=2), flush=True)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
