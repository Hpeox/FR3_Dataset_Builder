#!/usr/bin/env bash
set -euo pipefail

usage() {
    echo "Usage: $0 [--dry-run]" >&2
}

extra_args=()
if [[ $# -gt 1 ]]; then
    usage
    exit 2
fi
if [[ $# -eq 1 ]]; then
    if [[ $1 != "--dry-run" ]]; then
        usage
        exit 2
    fi
    extra_args+=("$1")
fi

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd -- "${script_dir}/.." && pwd)"

python3 "${script_dir}/cleanup_raw_demos.py" \
    --mode completed \
    --demos-root "${repo_root}/runtime_sessions/demos" \
    --runtime-frames-root "${repo_root}/runtime_frames" \
    --archives-root /data/internal/DATASET/Archived \
    --hdf5-root /data/internal/DATASET \
    "${extra_args[@]}"

python3 "${script_dir}/cleanup_raw_demos.py" \
    --mode discarded \
    --demos-root "${repo_root}/runtime_sessions/demos" \
    --runtime-frames-root "${repo_root}/runtime_frames" \
    "${extra_args[@]}"

python3 "${script_dir}/cleanup_raw_demos.py" \
    --mode failed \
    --demos-root "${repo_root}/runtime_sessions/demos" \
    --runtime-frames-root "${repo_root}/runtime_frames" \
    "${extra_args[@]}"
