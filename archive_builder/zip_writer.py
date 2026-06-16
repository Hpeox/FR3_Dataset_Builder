"""ZIP packaging helpers for archive bundles."""

from __future__ import annotations

import stat
import zipfile
from pathlib import Path


STORE_SUFFIXES = {".zst", ".mcap", ".npz"}
DEFLATE_SUFFIXES = {".json", ".yaml", ".yml", ".md", ".txt", ".log"}


def create_zip_from_bundle(bundle_dir: Path, output_zip: Path) -> None:
    output_zip.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output_zip, "w", allowZip64=True) as zf:
        for path in sorted(bundle_dir.rglob("*")):
            arcname = path.relative_to(bundle_dir.parent).as_posix()
            mode = path.lstat().st_mode
            if stat.S_ISLNK(mode):
                raise RuntimeError(f"refusing to archive symlink: {path}")
            if stat.S_ISDIR(mode):
                continue
            if not stat.S_ISREG(mode):
                raise RuntimeError(f"refusing to archive special file: {path}")
            compression = compression_for_path(path)
            zf.write(path, arcname, compress_type=compression)
    if not output_zip.exists() or output_zip.stat().st_size <= 0:
        raise RuntimeError(f"ZIP output is missing or empty: {output_zip}")


def compression_for_path(path: Path) -> int:
    suffix = path.suffix.lower()
    if suffix in STORE_SUFFIXES:
        return zipfile.ZIP_STORED
    if suffix in DEFLATE_SUFFIXES:
        return zipfile.ZIP_DEFLATED
    return zipfile.ZIP_STORED

