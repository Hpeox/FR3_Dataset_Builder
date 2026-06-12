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
- `force`: `[35, 20, 3]`, `float64`
- `force_norm`: `[35, 20, 3]`, `float64`
- `force_resultant`: `[6]`, `float64`

Tactile semantic mapping after review:

- `OG000544`: `left`
- `OG001009`: `right`

The selected builder strategy is to load the whole Xense object with `np.load(..., allow_pickle=True).item()` and stream rows from memory.

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
- `/attrs/language_instruction` should currently use the literal placeholder string `"a placeholder string"`; future builder code should leave a `# TODO` for reading this from a specific manifest key.
- RealSense `top`/`side`/`wrist1`/`wrist2` mapping is serial-number based.
- Xense `left`/`right` mapping is serial-number based.
- Xense external `.npy` should be loaded as a whole object.
- `manifest.json` and `aligned_manifest["sources"]` path mismatches should fail the demo.
- `O_T_EE` should have last row `[0, 0, 0, 1]`.

No unresolved row-policy decisions remain from this audit pass.
