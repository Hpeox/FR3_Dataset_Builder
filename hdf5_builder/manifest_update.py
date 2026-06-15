"""Atomic updates for raw demo manifests after HDF5 generation."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def mark_h5_generated(manifest_path: Path) -> None:
    manifest_path = manifest_path.resolve()
    if not manifest_path.exists():
        raise RuntimeError(f"manifest does not exist: {manifest_path}")
    payload: dict[str, Any] = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload["h5_generated"] = True
    tmp_path = manifest_path.with_name(manifest_path.name + ".tmp")
    tmp_path.write_text(json.dumps(payload, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    os.replace(tmp_path, manifest_path)
