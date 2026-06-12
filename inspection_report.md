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
- This confirms that done/aligned demos may still have at least one invalid aligned row.

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

- `cam1`: wrist
- `cam2`: wrist
- `cam3`: global
- `cam4`: global

No repo source was found that maps `cam3`/`cam4` to schema names `top`/`side`.

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

Default sensor IDs in code are `OG000544` and `OG001009`. The schema names are `left` and `right`, which were not found as persisted raw metadata.

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

Items not fully resolved from repository/sample data:

- HDF5 `success` semantic source
- HDF5 `language_instruction` source
- whether HDF5 `T` should be `sample_count` or `valid_count`
- camera semantic mapping for `top` and `side`
- tactile semantic mapping from `OG000544`/`OG001009` to `left`/`right`
- whether to hard-fail or filter when `sample_valid` contains false rows
