# DatasetBuilder

DatasetBuilder turns completed, aligned raw demos into portable training and
storage artifacts.

It currently supports two workflows:

- HDF5 export for model training and downstream dataset inspection.
- Cold-storage archive build and restore for preserving raw demo inputs.

The detailed HDF5 schema, raw source mapping, and inspection notes live in the
linked documents below. This README is the operational entry point.

## Repository Layout

- `build_hdf5.py`: build one HDF5 file from one raw demo `manifest.json`.
- `build_hdf5_batch.py`: scan a demos directory and build HDF5 files for all
  processable demos.
- `hdf5_builder/`: HDF5 path resolution, source readers, writer, report, and
  manifest update helpers.
- `build_archive.py`: build one cold-storage ZIP archive from one raw demo.
- `build_archive_batch.py`: scan a demos directory and build archives in batch.
- `restore_archive.py`: restore a cold-storage archive into a raw dataset tree.
- `cleanup_raw_demos.py`: interactively delete raw demos and owned
  `runtime_frames` resources after archive/HDF5 publication or rejection.
- `archive_builder/`: archive discovery, compression, rosbag conversion,
  archive metadata, ZIP writing, dry-run checks, and restore logic.
- `tools/`: ad hoc inspection and validation tools.
- `tests/`: focused tests for DatasetBuilder behavior.
- `HDF5_schema.md`, `schema_source_mapping.md`, `raw_data_access_notes.md`,
  `inspection_report.md`, and `open_questions.md`: deeper reference material.

## Inputs and Processable Demos

The entry point for both HDF5 and archive workflows is a raw demo
`manifest.json`, usually under:

```text
runtime_sessions/demos/<demo_id>/manifest.json
```

A demo is processable only when both conditions are true:

- `manifest.status == "done"`
- `aligned/aligned_manifest.json.status == "done"`

The builders also cross-check raw inputs recorded in `manifest.json` against
`aligned/aligned_manifest.json.sources`. Demo-owned NPZ and rosbag paths are
resolved relative to the demo directory. External `sensor_paths` are resolved
relative to `--runtime-root`, which is the runtime data root containing
`runtime_sessions/` and `runtime_frames/`. It defaults to the parent of
`DatasetBuilder` for the repository-local MainController layout. The previous
name `--repo-root` remains available as a compatibility alias.

## HDF5 Workflows

### Single Demo

From the repository root:

```bash
python3 DatasetBuilder/build_hdf5.py \
  --manifest runtime_sessions/demos/<demo_id>/manifest.json
```

Default outputs:

- HDF5: `DatasetBuilder/outputs/<demo_id>.h5`
- sidecar report: `DatasetBuilder/outputs/<demo_id>.build_report.json`

By default, a successful single-demo HDF5 build sets
`h5_generated: true` in the raw demo `manifest.json`. Use
`--no-update-manifest` to leave the manifest unchanged:

```bash
python3 DatasetBuilder/build_hdf5.py \
  --manifest runtime_sessions/demos/<demo_id>/manifest.json \
  --no-update-manifest
```

Useful options:

- `--output <path>`: choose a specific `.h5` output path.
- `--report <path>`: choose a specific JSON sidecar report path.
- `--overwrite`: replace an existing HDF5 output and report.
- `--min-free-gb <gb>`: require free space on the output filesystem before
  writing.
- `--runtime-root <path>`: set the runtime data root used for `sensor_paths`;
  `--repo-root` is a compatibility alias.

### Batch

```bash
python3 DatasetBuilder/build_hdf5_batch.py \
  --demos-root runtime_sessions/demos
```

Default outputs:

- output directory: `/data/external/DATASET/`
- HDF5 per demo: `/data/external/DATASET/<demo_id>.h5`
- report per demo: `/data/external/DATASET/<demo_id>.build_report.json`
- batch report: `/data/external/DATASET/batch_build_report.json`

Batch HDF5 mode scans one level of `demo_*` directories under `--demos-root`.
`--skip-existing` is enabled by default and skips a demo only when the output
HDF5 already exists and the manifest already has `h5_generated` set. Use
`--no-skip-existing` to disable that behavior.

Useful examples:

```bash
# Validate processable demos and paths without writing HDF5 files or manifests.
python3 DatasetBuilder/build_hdf5_batch.py --dry-run

# Build at most 10 processable demos.
python3 DatasetBuilder/build_hdf5_batch.py --limit 10

# Keep raw manifests unchanged during batch HDF5 generation.
python3 DatasetBuilder/build_hdf5_batch.py --no-update-manifest

# Write batch output under an explicit output directory.
python3 DatasetBuilder/build_hdf5_batch.py \
  --output-dir /data/external/DATASET

# Read MainController data stored under an external runtime root.
python3 DatasetBuilder/build_hdf5_batch.py \
  --demos-root /data/external/runtime/runtime_sessions/demos \
  --runtime-root /data/external/runtime
```

HDF5 output and report paths may be any writable path. The defaults remain
`DatasetBuilder/outputs/<demo_id>.h5` for single-demo runs and
`/data/external/DATASET/` for batch runs, but custom `--output`, `--report`,
`--output-dir`, and `--batch-report` values are not restricted by the tool.

## Archive Workflows

Archives preserve the raw demo inputs needed to restore a demo later. The
archive builder includes the demo metadata and aligned artifacts, compresses
demo NPZ and external NPY inputs with `zstd`, converts rosbag data to compressed
MCAP, and stores selected TAC runtime config files.

### Single Archive

```bash
python3 DatasetBuilder/build_archive.py \
  --manifest runtime_sessions/demos/<demo_id>/manifest.json
```

Default outputs:

- archive ZIP: `DatasetBuilder/outputs/<demo_id>.zip`
- external archive metadata: `DatasetBuilder/outputs/<demo_id>.archive.json`

Single-archive mode does not update the raw demo manifest.

Useful options:

- `--archive-dir <path>`: choose the output directory.
- `--runtime-root <path>`: set the runtime data root used for external
  `sensor_paths` and runtime config discovery; `--repo-root` is a compatibility
  alias.
- `--overwrite`: replace existing archive artifacts.
- `--keep-staging`: keep the per-demo staging directory after completion.
- `--dry-run`: validate inputs and target paths without writing artifacts.

### Batch Archive

```bash
python3 DatasetBuilder/build_archive_batch.py \
  --demos-root runtime_sessions/demos
```

Default outputs:

- archive directory: `/data/external/DATASET/Archived`
- archive ZIP per demo: `/data/external/DATASET/Archived/<demo_id>.zip`
- archive metadata per demo:
  `/data/external/DATASET/Archived/<demo_id>.archive.json`
- batch report: `/data/external/DATASET/Archived/batch_archive_report.json`

Batch archive mode defaults to `--update-manifest`, which sets
`archieved: true` in the raw demo manifest after successful publication. Use
`--no-update-manifest` to opt out.

Useful examples:

```bash
# Validate archive inputs without writing archive artifacts.
python3 DatasetBuilder/build_archive_batch.py --dry-run

# Archive at most 5 processable demos.
python3 DatasetBuilder/build_archive_batch.py --limit 5

# Archive only selected demos.
python3 DatasetBuilder/build_archive_batch.py \
  --only demo_20260601_194946 \
  --only demo_20260601_200000

# Leave raw manifests unchanged during batch archive publication.
python3 DatasetBuilder/build_archive_batch.py --no-update-manifest

# Read MainController data stored under an external runtime root.
python3 DatasetBuilder/build_archive_batch.py \
  --demos-root /data/external/runtime/runtime_sessions/demos \
  --runtime-root /data/external/runtime
```

### Restore Archive

Restore using the external archive metadata:

```bash
python3 DatasetBuilder/restore_archive.py \
  --archive-json /data/external/DATASET/Archived/<demo_id>.archive.json
```

Restore using only a ZIP file:

```bash
python3 DatasetBuilder/restore_archive.py \
  --zip /data/external/DATASET/Archived/<demo_id>.zip
```

Use `--restore-root <path>` to override the restore destination. Use `--force`
only when existing restored files may be overwritten.

## Raw Demo Cleanup

`cleanup_raw_demos.py` is an interactive cleanup tool for removing raw demo
directories and their owned external `runtime_frames` files. It never edits raw
manifests or archive metadata.

Completed demos require explicit archive and HDF5 roots:

```bash
python3 DatasetBuilder/cleanup_raw_demos.py \
  --mode completed \
  --demos-root runtime_sessions/demos \
  --runtime-frames-root runtime_frames \
  --archives-root /data/external/DATASET/Archived \
  --hdf5-root /data/external/DATASET
```

Rejected demos do not require archive or HDF5 roots:

```bash
python3 DatasetBuilder/cleanup_raw_demos.py \
  --mode discarded \
  --demos-root runtime_sessions/demos \
  --runtime-frames-root runtime_frames

python3 DatasetBuilder/cleanup_raw_demos.py \
  --mode failed \
  --demos-root runtime_sessions/demos \
  --runtime-frames-root runtime_frames
```

Use `--dry-run` to show the same per-demo deletion plans without creating the
cleanup flag or deleting files.

Cleanup modes:

- `completed`: selects demos with `manifest.status == "done"` and
  `aligned/aligned_manifest.json.status == "done"`, then requires non-empty
  `<demo_id>.zip`, `<demo_id>.archive.json`, and `<demo_id>.h5`. The archive
  metadata `zip_size` must match the actual ZIP file size. This is a lightweight
  completion check and does not open ZIPs, compute SHA-256, read HDF5 payloads,
  or repeat builder validation.
- `discarded`: selects demos with `manifest.status == "discarded"`.
- `failed`: selects demos with `manifest.status == "failed"`.

External resource policy:

- FT/TAC `.npy` files come only from `manifest["sensor_paths"]` and are treated
  as one-demo-owned resources.
- Runtime config directories are shared resources. For completed demos, the
  candidate cleanup target is read from archive metadata
  `source_paths.tac_runtime_config_dir`. For failed demos, the tool derives the
  config directory from the TAC file timestamp using the archive builder's
  existing timestamp rule. Discarded demos do not reference runtime config.
- At startup, the tool scans existing manifests and builds a reverse mapping
  from runtime config directories to referencing demos. A runtime config
  directory is deleted only when the current candidate is the last remaining
  referencing demo.

Safety and failure behavior:

- All paths are resolved and must stay inside the configured demo or
  `runtime_frames` roots. The tool refuses to delete either root itself,
  symlinks, and special files.
- Each candidate is shown with exact paths, runtime config action, estimated
  size, and a `y/N/q` prompt.
- On confirmed deletion, the tool creates
  `.raw_cleanup_in_progress` in the demo directory, deletes external resources
  first, then deletes the demo contents, manifest, flag, and empty demo
  directory.
- Any deletion failure stops the run immediately and prints a partial-failure
  summary. The cleanup flag is intentionally left in place for manual
  inspection.

## Outputs and Reports

HDF5 generation writes:

- `<demo_id>.h5`
- `<demo_id>.build_report.json`
- `batch_build_report.json` for batch runs

Archive publication writes:

- `<demo_id>.zip`
- `<demo_id>.archive.json`
- `batch_archive_report.json` for batch runs

The authoritative archive SHA-256 is stored in the external
`<demo_id>.archive.json` as `zip_sha256`. The ZIP-internal archive manifest
cannot contain the final ZIP SHA-256 without making the hash circular.

## Operational Notes

- Batch commands print compact startup/status/final summaries. Detailed warning
  and per-demo information belongs in sidecar reports.
- Avoid streaming one line per internal item during long batch runs. Keep stdout
  bounded and inspect report files for details.
- Operators and external agents may apply their own output-path policy, but the
  HDF5 CLI itself only requires the target location to be writable.
- `--limit` counts processable demos, not skipped invalid demo directories.
- Existing outputs are not overwritten unless `--overwrite` is passed.
- `build_hdf5.py` and `build_hdf5_batch.py` require enough free space on the
  output filesystem, controlled by `--min-free-gb`.
- Archive conversion depends on local `zstd` and `ros2 bag convert`
  availability.

## Further Reading

- [HDF5_schema.md](HDF5_schema.md): exported HDF5 groups, datasets, shapes,
  dtypes, chunking, and compression.
- [schema_source_mapping.md](schema_source_mapping.md): source-to-HDF5 field
  mapping.
- [raw_data_access_notes.md](raw_data_access_notes.md): raw data access and
  decoding notes.
- [inspection_report.md](inspection_report.md): inspection findings for current
  generated artifacts.
- [open_questions.md](open_questions.md): unresolved schema and workflow
  questions.
