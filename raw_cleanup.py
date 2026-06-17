"""Interactive raw-demo cleanup planning and deletion helpers."""

from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal


CleanupMode = Literal["completed", "discarded", "failed"]
RuntimeConfigAction = Literal["delete", "keep_shared", "not_applicable", "missing_unowned"]
FLAG_NAME = ".raw_cleanup_in_progress"


@dataclass
class CleanupConfig:
    demos_root: Path
    runtime_frames_root: Path
    mode: CleanupMode
    dry_run: bool = False
    archives_root: Path | None = None
    hdf5_root: Path | None = None

    def resolved(self) -> "CleanupConfig":
        demos_root = self.demos_root.resolve()
        runtime_frames_root = self.runtime_frames_root.resolve()
        if not demos_root.is_dir():
            raise RuntimeError(f"--demos-root is not a directory: {demos_root}")
        if not runtime_frames_root.is_dir():
            raise RuntimeError(f"--runtime-frames-root is not a directory: {runtime_frames_root}")
        archives_root = self.archives_root.resolve() if self.archives_root else None
        hdf5_root = self.hdf5_root.resolve() if self.hdf5_root else None
        if self.mode == "completed":
            if archives_root is None:
                raise RuntimeError("--archives-root is required in completed mode")
            if hdf5_root is None:
                raise RuntimeError("--hdf5-root is required in completed mode")
            if not archives_root.is_dir():
                raise RuntimeError(f"--archives-root is not a directory: {archives_root}")
            if not hdf5_root.is_dir():
                raise RuntimeError(f"--hdf5-root is not a directory: {hdf5_root}")
        return CleanupConfig(
            demos_root=demos_root,
            runtime_frames_root=runtime_frames_root,
            mode=self.mode,
            dry_run=self.dry_run,
            archives_root=archives_root,
            hdf5_root=hdf5_root,
        )


@dataclass
class CompletionCheck:
    archive_zip: Path
    archive_json: Path
    hdf5: Path
    zip_size: int
    hdf5_size: int


@dataclass
class RuntimeConfigDecision:
    action: RuntimeConfigAction
    path: Path | None = None
    shared_with: tuple[str, ...] = ()
    reason: str = ""


@dataclass
class Candidate:
    demo_id: str
    demo_dir: Path
    manifest_path: Path
    manifest: dict[str, Any]
    status: str
    completion: CompletionCheck | None = None
    runtime_config_path: Path | None = None


@dataclass
class DeleteItem:
    label: str
    path: Path
    exists: bool
    size: int
    required: bool = True


@dataclass
class CleanupPlan:
    candidate: Candidate
    external_files: list[DeleteItem]
    runtime_config: RuntimeConfigDecision
    demo_item: DeleteItem

    @property
    def total_size(self) -> int:
        total = self.demo_item.size
        total += sum(item.size for item in self.external_files)
        if self.runtime_config.action == "delete" and self.runtime_config.path is not None:
            total += path_size(self.runtime_config.path)
        return total


@dataclass
class SkippedDemo:
    demo_id: str
    reason: str


@dataclass
class Discovery:
    candidates: list[Candidate]
    skipped: list[SkippedDemo]
    runtime_config_refs: dict[Path, set[str]]
    warnings: list[str] = field(default_factory=list)


@dataclass
class CleanupResult:
    demo_id: str
    status: str
    deleted_paths: list[str] = field(default_factory=list)
    failed_path: str | None = None
    error: str | None = None


class CleanupFailure(RuntimeError):
    def __init__(self, result: CleanupResult) -> None:
        self.result = result
        message = result.error or "cleanup failed"
        if result.failed_path:
            message = f"{message}: {result.failed_path}"
        super().__init__(message)


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def require_under_root(path: Path, root: Path, label: str) -> Path:
    resolved = path.resolve()
    root = root.resolve()
    if resolved == root:
        raise RuntimeError(f"{label} must not be the root directory itself: {resolved}")
    if not is_relative_to(resolved, root):
        raise RuntimeError(f"{label} is outside configured root: {resolved} not under {root}")
    return resolved


def resolve_demo_dir(path: Path, demos_root: Path) -> Path:
    resolved = require_under_root(path, demos_root, "demo directory")
    if not resolved.name.startswith("demo_"):
        raise RuntimeError(f"demo directory name must start with demo_: {resolved}")
    return resolved


def resolve_runtime_manifest_path(value: str | None, runtime_frames_root: Path, label: str) -> Path | None:
    if not value:
        return None
    raw = Path(value)
    if raw.is_absolute():
        candidate = raw
    else:
        parts = raw.parts
        if not parts or parts[0] != "runtime_frames":
            raise RuntimeError(f"{label} must be absolute or runtime_frames-relative: {value!r}")
        candidate = runtime_frames_root.joinpath(*parts[1:])
    resolved = candidate.resolve()
    return require_under_root(resolved, runtime_frames_root, label)


def resolve_runtime_metadata_path(value: str | None, runtime_frames_root: Path, label: str) -> Path | None:
    if not value:
        return None
    raw = Path(value)
    if raw.is_absolute():
        candidate = raw
    else:
        parts = raw.parts
        if parts and parts[0] == "runtime_frames":
            candidate = runtime_frames_root.joinpath(*parts[1:])
        else:
            candidate = runtime_frames_root / raw
    resolved = candidate.resolve()
    return require_under_root(resolved, runtime_frames_root, label)


def select_tac_runtime_config(runtime_frames_root: Path, tac_npy_path: Path) -> Path:
    match = re.fullmatch(r"data_TAC_(\d{8}_\d{6})\.npy", tac_npy_path.name)
    if not match:
        raise RuntimeError(f"TAC file name does not contain expected timestamp: {tac_npy_path.name}")
    tac_timestamp = match.group(1)
    candidates = [
        path
        for path in runtime_frames_root.iterdir()
        if path.is_dir()
        and re.fullmatch(r"\d{8}_\d{6}", path.name)
        and path.name < tac_timestamp
    ]
    if not candidates:
        raise RuntimeError(f"no runtime config directory earlier than TAC timestamp {tac_timestamp}")
    return sorted(candidates, key=lambda path: path.name)[-1].resolve()


def check_completed_outputs(demo_id: str, archives_root: Path, hdf5_root: Path) -> CompletionCheck:
    archive_zip = archives_root / f"{demo_id}.zip"
    archive_json = archives_root / f"{demo_id}.archive.json"
    hdf5 = hdf5_root / f"{demo_id}.h5"
    archive_zip_size = require_nonempty_regular_file(archive_zip, "archive ZIP")
    archive_json_size = require_nonempty_regular_file(archive_json, "archive JSON")
    if archive_json_size <= 0:
        raise RuntimeError(f"archive JSON must be non-empty: {archive_json}")
    payload = read_json(archive_json)
    if payload.get("demo_id") != demo_id:
        raise RuntimeError(f"archive JSON demo_id mismatch for {demo_id}: {payload.get('demo_id')!r}")
    expected_zip_size = int(payload.get("zip_size"))
    if expected_zip_size != archive_zip_size:
        raise RuntimeError(
            f"archive ZIP size mismatch for {demo_id}: metadata={expected_zip_size}, actual={archive_zip_size}"
        )
    hdf5_size = require_nonempty_regular_file(hdf5, "HDF5")
    return CompletionCheck(
        archive_zip=archive_zip.resolve(),
        archive_json=archive_json.resolve(),
        hdf5=hdf5.resolve(),
        zip_size=archive_zip_size,
        hdf5_size=hdf5_size,
    )


def require_nonempty_regular_file(path: Path, label: str) -> int:
    if path.is_symlink():
        raise RuntimeError(f"{label} must not be a symlink: {path}")
    if not path.is_file():
        raise RuntimeError(f"{label} must be a regular file: {path}")
    size = path.stat().st_size
    if size <= 0:
        raise RuntimeError(f"{label} must be non-empty: {path}")
    return size


def completed_runtime_config_from_archive(archive_json: Path, runtime_frames_root: Path) -> Path | None:
    payload = read_json(archive_json)
    source_paths = payload.get("source_paths") or {}
    return resolve_runtime_metadata_path(
        source_paths.get("tac_runtime_config_dir"),
        runtime_frames_root,
        "source_paths.tac_runtime_config_dir",
    )


def derive_failed_runtime_config(manifest: dict[str, Any], runtime_frames_root: Path) -> Path | None:
    sensor_paths = manifest.get("sensor_paths") or {}
    tac_path = resolve_runtime_manifest_path(sensor_paths.get("xense"), runtime_frames_root, "sensor_paths.xense")
    if tac_path is None:
        return None
    return select_tac_runtime_config(runtime_frames_root, tac_path)


def discover(config: CleanupConfig) -> Discovery:
    config = config.resolved()
    refs: dict[Path, set[str]] = {}
    warnings: list[str] = []
    candidates: list[Candidate] = []
    skipped: list[SkippedDemo] = []

    manifests = sorted(config.demos_root.glob("demo_*/manifest.json"))
    for manifest_path in manifests:
        demo_dir = resolve_demo_dir(manifest_path.parent, config.demos_root)
        demo_id = demo_dir.name
        try:
            manifest = read_json(manifest_path)
        except Exception as exc:
            skipped.append(SkippedDemo(demo_id=demo_id, reason=f"manifest_read_error: {exc}"))
            continue
        status = str(manifest.get("status"))
        runtime_config_path = runtime_config_for_refs(
            demo_id,
            status,
            manifest,
            config,
            warnings,
        )
        if runtime_config_path is not None:
            refs.setdefault(runtime_config_path, set()).add(demo_id)

        try:
            candidate = candidate_from_manifest(demo_id, demo_dir, manifest_path, manifest, status, config)
        except Exception as exc:
            skipped.append(SkippedDemo(demo_id=demo_id, reason=str(exc)))
            continue
        if candidate is None:
            continue
        candidates.append(candidate)

    return Discovery(candidates=candidates, skipped=skipped, runtime_config_refs=refs, warnings=warnings)


def runtime_config_for_refs(
    demo_id: str,
    status: str,
    manifest: dict[str, Any],
    config: CleanupConfig,
    warnings: list[str],
) -> Path | None:
    try:
        if status == "done":
            archive_json = config.archives_root / f"{demo_id}.archive.json" if config.archives_root else None
            if archive_json is not None and archive_json.exists():
                return completed_runtime_config_from_archive(archive_json, config.runtime_frames_root)
            return derive_failed_runtime_config(manifest, config.runtime_frames_root)
        if status == "failed":
            return derive_failed_runtime_config(manifest, config.runtime_frames_root)
    except Exception as exc:
        warnings.append(f"{demo_id}: runtime_config_ref_skipped: {exc}")
    return None


def candidate_from_manifest(
    demo_id: str,
    demo_dir: Path,
    manifest_path: Path,
    manifest: dict[str, Any],
    status: str,
    config: CleanupConfig,
) -> Candidate | None:
    if config.mode == "discarded":
        if status != "discarded":
            return None
        return Candidate(demo_id, demo_dir, manifest_path, manifest, status)
    if config.mode == "failed":
        if status != "failed":
            return None
        runtime_config_path = derive_failed_runtime_config(manifest, config.runtime_frames_root)
        return Candidate(demo_id, demo_dir, manifest_path, manifest, status, None, runtime_config_path)

    if status != "done":
        return None
    aligned_manifest_path = demo_dir / "aligned" / "aligned_manifest.json"
    if not aligned_manifest_path.exists():
        raise RuntimeError("missing aligned_manifest")
    aligned_manifest = read_json(aligned_manifest_path)
    if aligned_manifest.get("status") != "done":
        return None
    assert config.archives_root is not None
    assert config.hdf5_root is not None
    completion = check_completed_outputs(demo_id, config.archives_root, config.hdf5_root)
    runtime_config_path = completed_runtime_config_from_archive(completion.archive_json, config.runtime_frames_root)
    return Candidate(demo_id, demo_dir, manifest_path, manifest, status, completion, runtime_config_path)


def build_plan(candidate: Candidate, config: CleanupConfig, runtime_config_refs: dict[Path, set[str]]) -> CleanupPlan:
    config = config.resolved()
    demo_dir = resolve_demo_dir(candidate.demo_dir, config.demos_root)
    validate_deletable_path(demo_dir, config.demos_root, "demo directory")
    external_files = []
    sensor_paths = candidate.manifest.get("sensor_paths") or {}
    for key, label in (("ft300", "ft300 external file"), ("xense", "TAC external file")):
        path = resolve_runtime_manifest_path(sensor_paths.get(key), config.runtime_frames_root, f"sensor_paths.{key}")
        if path is not None:
            validate_deletable_path(path, config.runtime_frames_root, label)
            external_files.append(
                DeleteItem(label=label, path=path, exists=path.exists(), size=path_size(path) if path.exists() else 0)
            )

    runtime_decision = decide_runtime_config(candidate, runtime_config_refs, config.runtime_frames_root)
    if runtime_decision.action == "delete" and runtime_decision.path is not None:
        validate_deletable_path(runtime_decision.path, config.runtime_frames_root, "runtime config directory")
    demo_item = DeleteItem(label="demo directory", path=demo_dir, exists=demo_dir.exists(), size=path_size(demo_dir))
    return CleanupPlan(
        candidate=candidate,
        external_files=external_files,
        runtime_config=runtime_decision,
        demo_item=demo_item,
    )


def decide_runtime_config(
    candidate: Candidate,
    runtime_config_refs: dict[Path, set[str]],
    runtime_frames_root: Path,
) -> RuntimeConfigDecision:
    path = candidate.runtime_config_path
    if path is None:
        return RuntimeConfigDecision(action="not_applicable", reason="candidate has no runtime config")
    path = require_under_root(path, runtime_frames_root, "runtime config directory")
    if not path.exists():
        return RuntimeConfigDecision(action="missing_unowned", path=path, reason="runtime config path does not exist")
    remaining = set(runtime_config_refs.get(path, set()))
    remaining.discard(candidate.demo_id)
    if remaining:
        return RuntimeConfigDecision(action="keep_shared", path=path, shared_with=tuple(sorted(remaining)))
    return RuntimeConfigDecision(action="delete", path=path)


def validate_deletable_path(path: Path, root: Path, label: str) -> None:
    path = require_under_root(path, root, label)
    if path.is_symlink():
        raise RuntimeError(f"{label} must not be a symlink: {path}")
    if path.exists() and not (path.is_file() or path.is_dir()):
        raise RuntimeError(f"{label} must be a regular file or directory: {path}")
    if path.is_dir():
        for child in path.rglob("*"):
            if child.is_symlink():
                raise RuntimeError(f"{label} tree must not contain symlink: {child}")
            if child.exists() and not (child.is_file() or child.is_dir()):
                raise RuntimeError(f"{label} tree contains special file: {child}")


def path_size(path: Path) -> int:
    if not path.exists():
        return 0
    if path.is_symlink():
        raise RuntimeError(f"refusing to size symlink: {path}")
    if path.is_file():
        return path.stat().st_size
    if not path.is_dir():
        raise RuntimeError(f"refusing to size special file: {path}")
    total = 0
    for child in path.rglob("*"):
        if child.is_symlink():
            raise RuntimeError(f"refusing to size symlink: {child}")
        if child.is_file():
            total += child.stat().st_size
        elif not child.is_dir():
            raise RuntimeError(f"refusing to size special file: {child}")
    return total


def delete_plan(plan: CleanupPlan, config: CleanupConfig) -> CleanupResult:
    config = config.resolved()
    if config.dry_run:
        return CleanupResult(demo_id=plan.candidate.demo_id, status="dry_run_would_delete")

    result = CleanupResult(demo_id=plan.candidate.demo_id, status="partial_failed")
    flag_path = plan.candidate.demo_dir / FLAG_NAME
    try:
        flag_path.write_bytes(b"")
        for item in plan.external_files:
            if item.exists:
                delete_path(item.path)
                result.deleted_paths.append(item.path.as_posix())
        if plan.runtime_config.action == "delete" and plan.runtime_config.path is not None:
            delete_path(plan.runtime_config.path)
            result.deleted_paths.append(plan.runtime_config.path.as_posix())
        delete_demo_contents(plan.candidate.demo_dir, plan.candidate.manifest_path, flag_path, result)
        delete_path(plan.candidate.manifest_path)
        result.deleted_paths.append(plan.candidate.manifest_path.as_posix())
        delete_path(flag_path)
        result.deleted_paths.append(flag_path.as_posix())
        delete_path(plan.candidate.demo_dir)
        result.deleted_paths.append(plan.candidate.demo_dir.as_posix())
    except Exception as exc:
        if plan.candidate.demo_dir.exists() and not flag_path.exists():
            try:
                flag_path.write_bytes(b"")
            except Exception:
                pass
        result.failed_path = path_from_exception_context(exc)
        result.error = str(exc)
        raise CleanupFailure(result) from exc

    result.status = "succeeded"
    return result


def delete_demo_contents(demo_dir: Path, manifest_path: Path, flag_path: Path, result: CleanupResult) -> None:
    for child in sorted(demo_dir.iterdir(), key=lambda path: path.name):
        if child == manifest_path or child == flag_path:
            continue
        delete_path(child)
        result.deleted_paths.append(child.as_posix())


def delete_path(path: Path) -> None:
    if not path.exists():
        return
    if path.is_symlink():
        raise RuntimeError(f"refusing to delete symlink: {path}")
    if path.is_dir():
        shutil.rmtree(path)
    elif path.is_file():
        path.unlink()
    else:
        raise RuntimeError(f"refusing to delete special file: {path}")


def path_from_exception_context(exc: Exception) -> str | None:
    text = str(exc)
    match = re.search(r": (/[^:]+)$", text)
    return match.group(1) if match else None


def update_runtime_config_refs(runtime_config_refs: dict[Path, set[str]], candidate: Candidate) -> None:
    for demos in runtime_config_refs.values():
        demos.discard(candidate.demo_id)


def format_size(num_bytes: int) -> str:
    value = float(num_bytes)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if value < 1024.0 or unit == "TiB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024.0
