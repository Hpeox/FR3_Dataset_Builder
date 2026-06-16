"""Archive metadata and raw manifest update helpers."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .context import read_json, write_json_atomic


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fp:
        for chunk in iter(lambda: fp.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def mark_archieved(manifest_path: Path) -> None:
    payload = read_json(manifest_path)
    payload["archieved"] = True
    write_json_atomic(manifest_path, payload)


def write_archive_json(path: Path, payload: dict[str, Any]) -> None:
    if "raw_released" in payload:
        raise RuntimeError("archive metadata must not include raw_released in this phase")
    if not payload.get("zip_sha256"):
        raise RuntimeError("archive metadata requires zip_sha256")
    write_json_atomic(path, payload)


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")

