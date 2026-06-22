# HDF5 raw data inspection report

This report summarizes the read-only inspection performed before implementing an HDF5 builder.

## Files read

- `DatasetBuilder/HDF5_schema.md`
- `runtime_sessions/demos/*/manifest.json`
- `runtime_sessions/demos/*/aligned/aligned_manifest.json`
- `runtime_sessions/demos/*/aligned/alignment_config.json`
- `runtime_sessions/demos/*/aligned/aligned_index.npz`
- sampled demo NPZ files listed in manifest
- sampled rosbag metadata and first image messages
- service/code files for FT300S, XenseTacSensor, RealSense, ZMQ, and alignment

## Helper added

Read-only helper:

```text
DatasetBuilder/tools/inspect_raw_samples.py
```

The helper inspects JSON, NPZ keys, `.npy` headers, and external file existence. It does not modify raw demos and does not fully load large TAC object files.

The full helper output from this run was written outside the repository to:

```text
/tmp/hdf5_raw_sample_inspection.json
```

## Demo inventory

Under `runtime_sessions/demos`:

- total demo directories: `131`
- `manifest.status == "done"`: `107`
- `manifest.status == "discarded"`: `22`
- `manifest.status == "failed"`: `2`
- processable by this audit rule: `107`

First three processable demos were selected as samples:

| demo | manifest status | aligned status | sample_count | valid_count | base | mode |
| --- | --- | --- | ---: | ---: | --- | --- |
| `demo_20260601_194946` | `done` | `done` | 754 | 754 | `realsense:bundle` | `causal` |
| `demo_20260602_094621` | `done` | `done` | 686 | 686 | `realsense:bundle` | `causal` |
| `demo_20260602_094713` | `done` | `done` | 929 | 929 | `realsense:bundle` | `causal` |

The user-provided example `demo_20260605_165503` was also spot-checked manually:

- `sample_count`: `961`
- `valid_count`: `960`
- This confirms that done/aligned demos may still have invalid aligned rows. After review, the HDF5 builder should still export all aligned rows and record warnings for invalid row indices.

## Sample file presence

For the sampled demos, manifest paths resolved to existing files:

- `manifest["npz"]["ft300"]`
- `manifest["npz"]["xense"]`
- `manifest["npz"]["realsense"]`
- `manifest["npz"]["zmq"]`
- `manifest["rosbag_uri"] / metadata.yaml`
- `manifest["rosbag_uri"] / rosbag_0.mcap`
- `manifest["sensor_paths"]["ft300"]`
- `manifest["sensor_paths"]["xense"]`

The external sensor files are scalar object `.npy` files:

| sensor | observed `.npy` shape | observed `.npy` dtype | note |
| --- | --- | --- | --- |
| FT300S | scalar | `object` | small enough to load one sample safely |
| XenseTacSensor | scalar | `object` | GB-scale files; audited by header and save code |

## NPZ key summary

Observed key sets were consistent across sampled demos:

| file | keys |
| --- | --- |
| `ft300_timestamps.npz` | `frame_id`, `timestamp_ns`, `recv_time_ns`, `recv_monotonic_ns` |
| `xense_timestamps.npz` | `frame_id`, `timestamp_ns_0`, `timestamp_ns_1`, `recv_time_ns`, `recv_monotonic_ns` |
| `realsense_metadata.npz` | `topic`, `frame_number`, `header_stamp_ns`, `frame_timestamp_ns`, `hw_timestamp_ns`, `clock_domain`, `recv_time_ns`, `recv_monotonic_ns` |
| `zmq_telemetry.npz` | `source`, `seq`, `stamp_s`, `valid_mask`, `floats_58`, `gripper_gPO`, `gripper_gCU`, `recv_time_ns`, `recv_monotonic_ns` |

## Aligned index summary

For `demo_20260601_194946`, `aligned_index.npz` had 94 arrays. Important groups:

- global: `t_ns`, `segment_id`, `sample_valid`
- RealSense bundle: `realsense_bundle_*`
- RealSense per-topic streams: `realsense_cam{1..4}_{color,aligned_depth}_*`
- Xense pair: `xense_pair_*`, `xense_0_*`, `xense_1_*`
- FT300S: `ft300s_*`
- ZMQ: `zmq_source_1_*`, `zmq_source_2_*`, `zmq_source_3_*`

All arrays in this file had length `754`.

## RealSense image verification

One message per required image topic was decoded from `demo_20260601_194946`.

Observed:

- RGB topics: `encoding == "rgb8"`, shape `[480, 640, 3]`, dtype `uint8`
- aligned depth topics: `encoding == "16UC1"`, shape `[480, 640]`, dtype `uint16`

Required topics came from `manifest["realsense_image_readiness"]["required_topics"]` and matched rosbag metadata.

Repository camera role mapping:

- `cam1` / serial `335122271402`: `wrist1`
- `cam2` / serial `335122272872`: `wrist2`
- `cam3` / serial `337322074345`: `side`
- `cam4` / serial `050222071619`: `top`

## FT300S external data verification

Loaded `runtime_frames/data_FT_20260601_194946.npy` with `allow_pickle=True`.

Top-level keys:

- `events`
- `frames_data`

First frame key:

- `00000`

First frame fields:

- `ft300_wrench`: shape `(6,)`, dtype `float64`
- `ft300_fx`, `ft300_fy`, `ft300_fz`, `ft300_tx`, `ft300_ty`, `ft300_tz`
- `ft300_timestamp_ns`
- `ft300_source`
- `ft300_crc_ok`
- `ft300_error_reason`

The schema requests `float32` for HDF5 wrench, so this is a cast during materialization.

## Xense external data verification

The Xense `.npy` files are scalar object files and large. This audit did not deserialize the full GB-scale object in terminal.

Code inspection confirms the saved structure:

- top-level: `events`, `frames_data`
- per-frame keys include:
  - `<sensor_id_0>_rec`
  - `<sensor_id_1>_rec`
  - `<sensor_id_0>_force`
  - `<sensor_id_1>_force`
  - `<sensor_id_0>_force_norm`
  - `<sensor_id_1>_force_norm`
  - `<sensor_id_0>_force_resultant`
  - `<sensor_id_1>_force_resultant`
  - `<sensor_id_0>_timestamp_ns`
  - `<sensor_id_1>_timestamp_ns`

Code and README comments confirm shapes:

- `rec`: `[700, 400, 3]`, `uint8`
- `force`: `[35, 20, 3]`, raw dtype may be `float32` or `float64`
- `force_norm`: `[35, 20, 3]`, raw dtype may be `float32` or `float64`
- `force_resultant`: `[6]`, raw dtype may be `float32` or `float64`

The HDF5 schema now normalizes `/observations/tactile/force`,
`/observations/tactile/force_norm`, and `/observations/tactile/force_resultant`
to `float32` to support Xense SDK 2.0 output while remaining compatible with older
`float64` saved files.

Tactile semantic mapping after review:

- `OG000544`: `left`
- `OG001009`: `right`

The selected builder strategy is to load the whole Xense object with `np.load(..., allow_pickle=True).item()` and stream rows from memory.

## Cold-storage/archive inspection

This pass inspected archive feasibility only. It did not implement archive, compression, validation, restore, or raw-release code.

Representative completed demos inspected for archive planning:

| demo | reason selected | manifest status | aligned status | FT external file | TAC external file | selected TAC runtime config |
| --- | --- | --- | --- | --- | --- | --- |
| `demo_20260601_194946` | early processable sample from the HDF5 audit | `done` | `done` | `runtime_frames/data_FT_20260601_194946.npy` | `runtime_frames/data_TAC_20260601_194946.npy` | `runtime_frames/20260601_194926` |
| `demo_20260602_094855` | active IDE-context demo | `done` | `done` | `runtime_frames/data_FT_20260602_094855.npy` | `runtime_frames/data_TAC_20260602_094855.npy` | `runtime_frames/20260602_094544` |
| `demo_20260605_165503` | task-provided archive layout example | `done` | `done` | `runtime_frames/data_FT_20260605_165503.npy` | `runtime_frames/data_TAC_20260605_165503.npy` | `runtime_frames/20260605_163106` |

### Archive resource discovery

For the sampled demos, all archive resources can be located from existing metadata plus the TAC runtime config timestamp rule:

- demo-owned files come from the complete `runtime_sessions/demos/demo_xxx/` directory
- NPZ files are listed in `manifest["npz"]`
- rosbag directory is listed in `manifest["rosbag_uri"]`
- external FT and TAC `.npy` files are listed in `manifest["sensor_paths"]`
- `aligned_manifest["sources"]` matches the manifest paths for external sensor files and rosbag URI
- TAC runtime config is selected by parsing the timestamp in `data_TAC_YYYYMMDD_HHMMSS.npy` and choosing the latest timestamped `runtime_frames/YYYYMMDD_HHMMSS/` directory earlier than the TAC file timestamp

Across all 107 currently processable demos:

- `manifest["sensor_paths"]` matched `aligned_manifest["sources"]` for FT and Xense paths.
- No duplicate external FT/TAC paths were observed.
- Every TAC file had a selectable earlier runtime config directory containing both `runtime_OG000544` and `runtime_OG001009`.

### Self-contained bundle feasibility

The sampled demos can form self-contained bundles with this layout:

```text
demo_xxx_bundle/
  archive_manifest.json
  demo/
  runtime_frames/
    data_FT_*.npy.zst
    data_TAC_*.npy.zst
    runtime_config/
      runtime_OG000544
      runtime_OG001009
```

The bundle must record the original selected runtime config timestamp directory in archive metadata. The ZIP layout normalizes the files to `runtime_frames/runtime_config/`, but restore must write them back to the selected original directory, for example `runtime_frames/20260605_163106/`.

### File types and special filesystem objects

Observed file type summary:

- `runtime_sessions/demos/demo_20260601_194946`: 11 regular files, 2 directories, 0 symlinks, 0 other special files
- `runtime_sessions/demos/demo_20260602_094855`: 11 regular files, 2 directories, 0 symlinks, 0 other special files
- `runtime_sessions/demos/demo_20260605_165503`: 11 regular files, 2 directories, 0 symlinks, 0 other special files
- `runtime_frames/`: 276 regular files, 29 directories, 0 symlinks, 0 other special files
- sampled late TAC runtime config directories: regular config files only, no symlinks or special files

This supports using ZIP for single-file packaging and path preservation for the currently observed dataset. Archive code should still reject or explicitly report future symlinks, device files, sockets, FIFOs, or permission/owner semantics that cannot be represented by the chosen ZIP policy.

### Naming collisions and shared resources

No duplicate external FT/TAC paths were observed across the 107 processable demos.

The normalized bundle path `runtime_frames/runtime_config/` would collide if more than one TAC runtime config directory were included in the same demo bundle. Current design includes exactly one selected runtime config directory per demo, so no collision was observed. The external archive metadata must preserve the original selected timestamp directory to make restore deterministic.

### Archive validation sufficiency

The established validation policy is sufficient for the observed data:

- `.npy.zst`: `zstd -t` validates the compressed external FT/TAC files.
- rosbag/MCAP: successful `ros2 bag convert`, non-empty `rosbag_0.mcap`, and existing `metadata.yaml` are sufficient for archive construction. Full message deserialization and reverse conversion are intentionally out of scope.
- ZIP: successful creation, non-empty ZIP, and successful external `demo_xxx.archive.json` publication are sufficient for archive state `archived`. Optional ZIP SHA-256 can be computed before long-term storage or transfer.

No raw demo files need to be modified for archive state.

## ZMQ verification

Sample `zmq_telemetry.npz` rows showed:

| source | valid_mask | finite payload columns | interpretation |
| --- | ---: | --- | --- |
| `1` | `1` | `floats_58[0:8]` | GELLO |
| `2` | `2` | `floats_58[8:58]` | robot |
| `3` | `4` | none in `floats_58` | gripper byte fields |

This matches `Zmq_Ref/Readme.md` and `MainController` protocol constants.

## Result

Most numeric, tactile, and image arrays can be obtained from current raw data plus existing alignment outputs.

Resolved by review answers:

- HDF5 should use all aligned rows, with warnings for invalid row indices and invalid stream names.
- `invalid` does not necessarily mean no usable source index exists; non-negative indices should still be used with a warning.
- If a required stream index is `-1`, use the previous frame information for that stream.
- If the first row for a required stream has index `-1` and no previous frame exists, remove that leading row from the HDF5 output.
- Warnings should be printed to the terminal and written to a JSON sidecar report.
- `/attrs/success` should currently be `True` for all processable demos.
- `/attrs/task_name` and `/attrs/language_instruction` are copied verbatim from required top-level manifest strings. Invalid or missing values fail HDF5 build and batch dry-run; there is no fallback.
- RealSense `top`/`side`/`wrist1`/`wrist2` mapping is serial-number based.
- Xense `left`/`right` mapping is serial-number based.
- Xense external `.npy` should be loaded as a whole object.
- `manifest.json` and `aligned_manifest["sources"]` path mismatches should fail the demo.
- `O_T_EE` should have last row `[0, 0, 0, 1]`.

No unresolved row-policy decisions remain from this audit pass.
