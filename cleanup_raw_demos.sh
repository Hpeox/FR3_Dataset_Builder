#!/usr/bin/env bash
set -euo pipefail

usage() {
    echo "Usage: $0 [--dry-run] [--manifest-only]" >&2
}

extra_args=()
manifest_only=false
for arg in "$@"; do
    case "$arg" in
        --dry-run) extra_args+=("$arg") ;;
        --manifest-only) manifest_only=true ;;
        *) usage; exit 2 ;;
    esac
done

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd -- "${script_dir}/.." && pwd)"

if [[ $manifest_only == true ]]; then
    exec python3 "${script_dir}/cleanup_raw_demos.py" \
        --mode manifest_only \
        --demos-root "${repo_root}/runtime_sessions/demos" \
        --runtime-frames-root "${repo_root}/runtime_frames" \
        "${extra_args[@]}"
fi

python3 "${script_dir}/cleanup_raw_demos.py" \
    --mode tactile_warning \
    --demos-root "${repo_root}/runtime_sessions/demos" \
    --runtime-frames-root "${repo_root}/runtime_frames" \
    "${extra_args[@]}"

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
