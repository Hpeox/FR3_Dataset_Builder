"""External compression helpers for archive builds."""

from __future__ import annotations

import subprocess
from pathlib import Path


def compress_zstd(source: Path, output: Path, log_path: Path | None = None) -> dict[str, object]:
    output.parent.mkdir(parents=True, exist_ok=True)
    command = ["zstd", "-T0", "-19", "-f", "-o", output.as_posix(), source.as_posix()]
    compress_result = _run(command, log_path)
    test_command = ["zstd", "-t", output.as_posix()]
    test_result = _run(test_command, log_path)
    if not output.exists() or output.stat().st_size <= 0:
        raise RuntimeError(f"zstd output is missing or empty: {output}")
    return {
        "source": source.as_posix(),
        "output": output.as_posix(),
        "command": command,
        "return_code": compress_result.returncode,
        "test_command": test_command,
        "test_return_code": test_result.returncode,
        "size": output.stat().st_size,
    }


def decompress_zstd(source: Path, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["zstd", "-d", "-f", "-o", output.as_posix(), source.as_posix()],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def _run(command: list[str], log_path: Path | None) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if log_path is not None:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as fp:
            fp.write("$ " + " ".join(command) + "\n")
            fp.write(result.stdout or "")
            if result.stdout and not result.stdout.endswith("\n"):
                fp.write("\n")
    if result.returncode != 0:
        tail = "\n".join((result.stdout or "").splitlines()[-20:])
        raise RuntimeError(f"command failed ({result.returncode}): {' '.join(command)}\n{tail}")
    return result

