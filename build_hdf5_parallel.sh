#!/usr/bin/env bash
set -uo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_BIN="${PYTHON:-/usr/bin/python3}"
WORKERS=4
RUN_DIR=""
FORWARD_ARGS=()
PIDS=()
WORKER_IDS=()
INTERRUPTED=0

usage() {
  cat <<'EOF'
Usage:
  DatasetBuilder/build_hdf5_parallel.sh [options]

Parallel HDF5 batch export using a fixed startup snapshot and file-backed
dynamic work queue.

Parallel options:
  --workers N            Worker count, 1 through 7. Default: 4.
  --python PATH          Python executable. Default: ${PYTHON:-/usr/bin/python3}.
  --run-dir PATH         Queue, result, and log directory.

HDF5 options passed through:
  --demos-root PATH
  --output-dir PATH
  --runtime-root PATH
  --repo-root PATH
  --overwrite
  --skip-existing
  --no-skip-existing
  --min-free-gb GB
  --no-update-manifest
  --batch-report PATH
  --dry-run
EOF
}

die() {
  echo "[ERROR] $*" >&2
  exit 2
}

cleanup_workers() {
  local pid
  for pid in "${PIDS[@]}"; do
    if kill -0 "$pid" 2>/dev/null; then
      kill -TERM -- "-$pid" 2>/dev/null || kill -TERM "$pid" 2>/dev/null || true
    fi
  done
  for pid in "${PIDS[@]}"; do
    wait "$pid" 2>/dev/null || true
  done
}

handle_signal() {
  INTERRUPTED=1
  cleanup_workers
}

trap handle_signal INT TERM

while [[ $# -gt 0 ]]; do
  case "$1" in
    --workers)
      [[ $# -ge 2 ]] || die "--workers requires a value"
      WORKERS="$2"
      shift 2
      ;;
    --python)
      [[ $# -ge 2 ]] || die "--python requires a value"
      PYTHON_BIN="$2"
      shift 2
      ;;
    --run-dir)
      [[ $# -ge 2 ]] || die "--run-dir requires a value"
      RUN_DIR="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      FORWARD_ARGS+=("$1")
      shift
      ;;
  esac
done

[[ "$WORKERS" =~ ^[0-9]+$ ]] || die "--workers must be an integer"
((WORKERS >= 1 && WORKERS <= 7)) || die "--workers must be between 1 and 7"
[[ -x "$PYTHON_BIN" ]] || die "python executable not found or not executable: $PYTHON_BIN"
command -v taskset >/dev/null 2>&1 || die "taskset is not available"
command -v setsid >/dev/null 2>&1 || die "setsid is not available"

if [[ -z "$RUN_DIR" ]]; then
  RUN_DIR="${SCRIPT_DIR}/parallel_runs/run_$(date +%Y%m%d_%H%M%S)_$$"
elif [[ "$RUN_DIR" != /* ]]; then
  RUN_DIR="${PWD}/${RUN_DIR}"
fi

mapfile -t CPU_PAIRS < <(
  "$PYTHON_BIN" "${SCRIPT_DIR}/build_hdf5_parallel.py" \
    validate-cpus --workers "$WORKERS"
)
[[ "${#CPU_PAIRS[@]}" -eq "$WORKERS" ]] || die "CPU topology validation failed"

"$PYTHON_BIN" "${SCRIPT_DIR}/build_hdf5_parallel.py" \
  prepare --run-dir "$RUN_DIR" "${FORWARD_ARGS[@]}" || exit $?

echo "[INFO] run_dir=$RUN_DIR workers=$WORKERS cpu_pairs=${CPU_PAIRS[*]}"

for ((index = 0; index < WORKERS; index++)); do
  worker_id="worker_${index}"
  log_path="${RUN_DIR}/logs/${worker_id}.log"
  setsid taskset -c "${CPU_PAIRS[$index]}" \
    "$PYTHON_BIN" "${SCRIPT_DIR}/build_hdf5_parallel.py" \
    worker --run-dir "$RUN_DIR" --worker-id "$worker_id" \
    >"$log_path" 2>&1 &
  PIDS+=("$!")
  WORKER_IDS+=("$worker_id")
done

for ((index = 0; index < WORKERS; index++)); do
  pid="${PIDS[$index]}"
  worker_id="${WORKER_IDS[$index]}"
  if wait "$pid"; then
    status=0
  else
    status=$?
  fi
  if [[ "$INTERRUPTED" == "1" ]]; then
    exit 130
  fi
  if [[ "$status" -ne 0 ]]; then
    echo "[WARN] ${worker_id} exited with code ${status}; recovering its claim" >&2
    "$PYTHON_BIN" "${SCRIPT_DIR}/build_hdf5_parallel.py" \
      recover --run-dir "$RUN_DIR" --worker-id "$worker_id" --exit-code "$status"
  fi
done

# A crashed worker may have returned one task to pending after the other workers
# had already drained the queue. Run bounded recovery waves until no task is left.
for ((wave = 1; wave <= 2; wave++)); do
  if ! compgen -G "${RUN_DIR}/pending/*.json" >/dev/null; then
    break
  fi
  echo "[WARN] starting recovery wave ${wave}" >&2
  PIDS=()
  WORKER_IDS=()
  for ((index = 0; index < WORKERS; index++)); do
    worker_id="recovery_${wave}_${index}"
    log_path="${RUN_DIR}/logs/${worker_id}.log"
    setsid taskset -c "${CPU_PAIRS[$index]}" \
      "$PYTHON_BIN" "${SCRIPT_DIR}/build_hdf5_parallel.py" \
      worker --run-dir "$RUN_DIR" --worker-id "$worker_id" \
      >"$log_path" 2>&1 &
    PIDS+=("$!")
    WORKER_IDS+=("$worker_id")
  done
  for ((index = 0; index < WORKERS; index++)); do
    pid="${PIDS[$index]}"
    worker_id="${WORKER_IDS[$index]}"
    if wait "$pid"; then
      status=0
    else
      status=$?
    fi
    if [[ "$INTERRUPTED" == "1" ]]; then
      exit 130
    fi
    if [[ "$status" -ne 0 ]]; then
      "$PYTHON_BIN" "${SCRIPT_DIR}/build_hdf5_parallel.py" \
        recover --run-dir "$RUN_DIR" --worker-id "$worker_id" --exit-code "$status"
    fi
  done
done

"$PYTHON_BIN" "${SCRIPT_DIR}/build_hdf5_parallel.py" finalize --run-dir "$RUN_DIR"
