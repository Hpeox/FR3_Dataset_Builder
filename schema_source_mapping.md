# HDF5 schema source mapping audit

This is an audit document only. It does not define or implement `build_hdf5.py`.

## Scope

Audited schema: `DatasetBuilder/HDF5_schema.md`.

Sampled processable demos:

- `runtime_sessions/demos/demo_20260601_194946`
- `runtime_sessions/demos/demo_20260602_094621`
- `runtime_sessions/demos/demo_20260602_094713`

Processability rule used in this audit:

1. `manifest.json` exists and has `status == "done"`.
2. `aligned/aligned_manifest.json` exists and has `status == "done"`.

The sampled demos all use `aligned/aligned_index.npz` with `schema_version: 3`, `base: realsense:bundle`, and `mode: causal`.

## Common indexing rules

Let:

- `idx = np.load(demo_dir / "aligned" / "aligned_index.npz")`
- `T = len(idx["t_ns"])`
- `rows = np.arange(T)`

Decision after review:

- Export all aligned rows, including rows where `idx["sample_valid"] == False`.
- `invalid` does not necessarily mean a missing source index. If a required stream is invalid but its source index is non-negative, use that indexed source value and record the warning.
- If a required stream index is `-1`, reuse the previous frame information for that stream.
- If the first row for a required stream has index `-1` and no previous frame exists, remove that leading row from the HDF5 output.
- Print warnings to the terminal and write the same warning records to a JSON sidecar report.
- Do not silently use `-1` as a normal Python/NumPy index. In particular, the builder must not let Python indexing select the last raw row.

After leading-row removal, all remaining `-1` entries should have a previous stream frame available for reuse.

## Mapping table

| HDF5 path | Source | Access method | Shape | Dtype | Indexing rule | Status | Notes |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `/attrs/demo_id` | demo directory | `demo_dir.name` | scalar | string | one value per HDF5 file | confirmed | Computed from the selected demo directory name. |
| `/attrs/success` | builder policy / `manifest.json` | For current demos, write `True` for every processable demo. Future failed-demo labels can extend this later. | scalar | bool | one value per HDF5 file | confirmed | Processable demos still require `manifest.status == "done"` and `aligned_manifest.status == "done"`. |
| `/attrs/total_steps` | `aligned_manifest.json` / `aligned_index.npz` | `len(idx["t_ns"])`; cross-check with `aligned_manifest["sample_count"]`. | scalar | int | one value per HDF5 file | confirmed | Current decision is to export all aligned rows, not only `sample_valid` rows. |
| `/attrs/schema_version` | builder policy | Constant `"v0.1"` from `HDF5_schema.md`. | scalar | string | one value per HDF5 file | not_implemented_yet | Not stored in raw demo. |
| `/attrs/nominal_hz` | `aligned_manifest.json` / builder policy | `aligned_manifest["hz"]` when present; sampled demos show `30.0`. | scalar | int/float | one value per HDF5 file | confirmed | HDF5 schema wants `30`; alignment artifacts store `30.0`. |
| `/attrs/language_instruction` | builder policy | Use the literal placeholder string `"a placeholder string"` for now. | scalar | string | one value per HDF5 file | confirmed | No raw annotation source exists yet. When implementing the builder, leave a `# TODO` near this assignment: future versions should read the string from a specific manifest key. |
| `/attrs/spatial_chunk_t` | builder policy | Constant `8` from schema. | scalar | int | one value per HDF5 file | not_implemented_yet | Compression/chunk metadata is not raw data. |
| `/attrs/lowdim_chunk_t` | builder policy | `min(T, 512)` from schema. | scalar | int | one value per HDF5 file | not_implemented_yet | Depends on chosen `T`. |
| `/attrs/compression` | builder policy | Constant `"zstd"` from schema. | scalar | string | one value per HDF5 file | not_implemented_yet | HDF5 writer policy. |
| `/attrs/compression_level` | builder policy | Constant `12` from schema. | scalar | int | one value per HDF5 file | not_implemented_yet | HDF5 writer policy. |
| `/actions/gello_q` | `zmq_telemetry.npz` | `z = np.load(demo_dir / manifest["npz"]["zmq"]); src_rows = idx["zmq_source_1_index"][rows]; z["floats_58"][src_rows, 0:7]` | `[T, 7]` | float64 | `zmq_source_1_index` is an absolute row index into the mixed ZMQ table. If valid flag is false but index is non-negative, use that index and warn; if index is `-1`, reuse previous stream frame. | confirmed | Directly stored in ZMQ payload, then copied by aligned index. `source=1` is GELLO. |
| `/actions/gello_gripper_cmd` | `zmq_telemetry.npz` | `z["floats_58"][idx["zmq_source_1_index"][rows], 7]` | `[T]` | float64 | Same as GELLO rows. | confirmed | Directly stored in ZMQ payload. |
| `/observations/robot_state/q` | `zmq_telemetry.npz` | `z["floats_58"][idx["zmq_source_2_index"][rows], 8:15]` | `[T, 7]` | float64 | `zmq_source_2_index` is an absolute row index into the mixed ZMQ table. Apply the common invalid-index policy: warn on invalid, use non-negative indices, and reuse previous stream frame for `-1`. | confirmed | `source=2` is robot; robot payload begins at `floats_58[8]`. |
| `/observations/robot_state/dq` | `zmq_telemetry.npz` | `z["floats_58"][idx["zmq_source_2_index"][rows], 15:22]` | `[T, 7]` | float64 | Same as robot rows. | confirmed | Directly stored, then copied. |
| `/observations/robot_state/tau_J` | `zmq_telemetry.npz` | `z["floats_58"][idx["zmq_source_2_index"][rows], 22:29]` | `[T, 7]` | float64 | Same as robot rows. | confirmed | Directly stored, then copied. |
| `/observations/robot_state/tau_J_d` | `zmq_telemetry.npz` | `z["floats_58"][idx["zmq_source_2_index"][rows], 29:36]` | `[T, 7]` | float64 | Same as robot rows. | confirmed | Directly stored, then copied. |
| `/observations/robot_state/O_T_EE` | `zmq_telemetry.npz` | `z["floats_58"][idx["zmq_source_2_index"][rows], 36:52].reshape(T, 4, 4)` | `[T, 4, 4]` | float64 | Same as robot rows. | confirmed | Computed by reshaping 16 stored floats. Validate that the last row is `[0, 0, 0, 1]`; otherwise fail or flag the row. |
| `/observations/robot_state/O_dP_EE` | `zmq_telemetry.npz` | `z["floats_58"][idx["zmq_source_2_index"][rows], 52:58]` | `[T, 6]` | float64 | Same as robot rows. | confirmed | Directly stored, then copied. |
| `/observations/gripper/gPO` | `zmq_telemetry.npz` | `z["gripper_gPO"][idx["zmq_source_3_index"][rows]].astype(np.uint8)` | `[T]` | uint8 | `zmq_source_3_index` is an absolute row index into the mixed ZMQ table. Apply the common invalid-index policy. | confirmed | Protocol stores raw Robotiq feedback bytes; NPZ array dtype is `int64`, so HDF5 writer casts to `uint8`. |
| `/observations/gripper/gCU` | `zmq_telemetry.npz` | `z["gripper_gCU"][idx["zmq_source_3_index"][rows]].astype(np.uint8)` | `[T]` | uint8 | Same as gripper rows. | confirmed | Stored as protocol byte, persisted as `int64`, copied with cast. |
| `/observations/ft300s/wrench` | external `runtime_frames` + `ft300_timestamps.npz` | Resolve `manifest["sensor_paths"]["ft300"]` as repo-root-relative path. Load object dict with `np.load(path, allow_pickle=True).item()`. For each row: `i = idx["ft300s_index"][row]`; `frame_id = ft_npz["frame_id"][i]`; `frame = obj["frames_data"][f"{frame_id:05d}"]`; read `frame["ft300_wrench"]`. | `[T, 6]` | float32 in HDF5; raw sampled value is float64 | `ft300s_index` is a row index into `ft300_timestamps.npz`; use `frame_id` to access external `frames_data`. Apply the common invalid-index policy. | confirmed | Directly stored in FT external file as float64 wrench. Schema requests float32, so builder must cast. Do not use filename guessing; use manifest path. |
| `/observations/rgb/top` | rosbag image topic | Decode `/cam4/camera/color/image_raw` from `rosbag_uri`; serial `050222071619` maps to `top`. | `[T, 480, 640, 3]` | uint8 | Use `realsense_cam4_color_index`. | confirmed | Per-stream RealSense indices are per-topic message indices, not mixed rosbag row numbers. |
| `/observations/rgb/side` | rosbag image topic | Decode `/cam3/camera/color/image_raw` from `rosbag_uri`; serial `337322074345` maps to `side`. | `[T, 480, 640, 3]` | uint8 | Use `realsense_cam3_color_index`. | confirmed | Per-stream RealSense indices are per-topic message indices. |
| `/observations/rgb/wrist` | rosbag image topics | Decode `/cam1/camera/color/image_raw` and `/cam2/camera/color/image_raw`; serials `335122271402` and `335122272872` map to `wrist1` and `wrist2`. | `[T, 2, 480, 640, 3]` | uint8 | Use `realsense_cam1_color_index` and `realsense_cam2_color_index`; stack as `[wrist1, wrist2]`. | confirmed | Sample rosbag messages confirm `encoding == "rgb8"`, `height == 480`, `width == 640`, `step == 1920`. |
| `/observations/depth/top` | rosbag image topic | Decode `/cam4/camera/aligned_depth_to_color/image_raw`; serial `050222071619` maps to `top`. | `[T, 480, 640]` | uint16 | Use `realsense_cam4_aligned_depth_index`. | confirmed | Decode `16UC1` to `uint16`. |
| `/observations/depth/side` | rosbag image topic | Decode `/cam3/camera/aligned_depth_to_color/image_raw`; serial `337322074345` maps to `side`. | `[T, 480, 640]` | uint16 | Use `realsense_cam3_aligned_depth_index`. | confirmed | Decode `16UC1` to `uint16`. |
| `/observations/depth/wrist` | rosbag image topics | Decode `/cam1/camera/aligned_depth_to_color/image_raw` and `/cam2/camera/aligned_depth_to_color/image_raw`; stack as `[wrist1, wrist2]`. | `[T, 2, 480, 640]` | uint16 | Use `realsense_cam1_aligned_depth_index` and `realsense_cam2_aligned_depth_index`. | confirmed | Sample rosbag messages confirm `encoding == "16UC1"`, `height == 480`, `width == 640`, `step == 1280`. |
| `/observations/tactile_images/rgb` | external Xense `runtime_frames` | Resolve `manifest["sensor_paths"]["xense"]` as repo-root-relative path. Load the whole object dict with `np.load(..., allow_pickle=True).item()`. For each row: `i = idx["xense_pair_source_index"][row]`; `frame_id = xense_npz["frame_id"][i]`; `frame = obj["frames_data"][f"{frame_id:05d}"]`; read `OG000544_rec` as left and `OG001009_rec` as right. | `[T, 2, 700, 400, 3]` | uint8 | Xense pair uses one same-row index for both sensors. Apply the common invalid-index policy. | confirmed | Sensor dimension order is `[left, right] == [OG000544, OG001009]`. |
| `/observations/tactile/force` | external Xense `runtime_frames` | Same Xense row access; read `OG000544_force` as left and `OG001009_force` as right; cast raw `float32`/`float64` to HDF5 `float32`. | `[T, 2, 35, 20, 3]` | float32 | Same-row Xense pair index. | confirmed | SDK 2.0 stores force arrays as `float32`; older saved files may be `float64`. HDF5 normalizes to `float32`. |
| `/observations/tactile/force_norm` | external Xense `runtime_frames` | Same Xense row access; read `OG000544_force_norm` as left and `OG001009_force_norm` as right; cast raw `float32`/`float64` to HDF5 `float32`. | `[T, 2, 35, 20, 3]` | float32 | Same-row Xense pair index. | confirmed | SDK 2.0 stores force arrays as `float32`; older saved files may be `float64`. HDF5 normalizes to `float32`. |
| `/observations/tactile/force_resultant` | external Xense `runtime_frames` | Same Xense row access; read `OG000544_force_resultant` as left and `OG001009_force_resultant` as right; cast raw `float32`/`float64` to HDF5 `float32`. | `[T, 2, 6]` | float32 | Same-row Xense pair index. | confirmed | SDK 2.0 stores force resultant arrays as `float32`; older saved files may be `float64`. HDF5 normalizes to `float32`. |

## Cold-storage/archive mapping

The archive bundle is a cold-storage packaging layer. It must preserve the raw data needed to rebuild HDF5, but it is not itself an HDF5 source-mapping contract.

Bundle root:

```text
demo_xxx_bundle/
```

| Bundle path | Raw source | Discovery method | Compression | Validation | Restore target | Status | Notes |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `archive_manifest.json` | archive writer metadata | Generated during archive publication from the selected demo, source paths, compression settings, validation results, and lifecycle state. | ZIP deflate as small JSON. | Require valid JSON inside ZIP and matching external `demo_xxx.archive.json`. | Not restored into raw demo by default. | design_confirmed | Do not write archive state into raw `demo/manifest.json`. |
| `demo/manifest.json` | `runtime_sessions/demos/demo_xxx/manifest.json` | User-selected or batch-selected demo entry point. | ZIP deflate as small JSON. | Require file to exist and `manifest.status == "done"` for the current archiveable set. | `runtime_sessions/demos/demo_xxx/manifest.json` | confirmed | This remains the authoritative entry point for demo-owned paths. |
| `demo/aligned/aligned_index.npz` | `runtime_sessions/demos/demo_xxx/aligned/aligned_index.npz` | Fixed demo-relative path under `aligned/`. | ZIP store because `.npz` is already compressed. | Require file to exist and be non-empty. | `runtime_sessions/demos/demo_xxx/aligned/aligned_index.npz` | confirmed | Do not rerun timestamp alignment for archive creation. |
| `demo/aligned/aligned_manifest.json` | `runtime_sessions/demos/demo_xxx/aligned/aligned_manifest.json` | Fixed demo-relative path under `aligned/`. | ZIP deflate as small JSON. | Require file to exist and `aligned_manifest.status == "done"`. | `runtime_sessions/demos/demo_xxx/aligned/aligned_manifest.json` | confirmed | Use `aligned_manifest["sources"]` only as a consistency check against `manifest.json`. |
| `demo/aligned/alignment_config.json` | `runtime_sessions/demos/demo_xxx/aligned/alignment_config.json` | Fixed demo-relative path under `aligned/`. | ZIP deflate as small JSON. | Require file to exist and parse as JSON. | `runtime_sessions/demos/demo_xxx/aligned/alignment_config.json` | confirmed | Preserves the alignment configuration used to create `aligned_index.npz`. |
| `demo/aligned/alignment_report.md` | `runtime_sessions/demos/demo_xxx/aligned/alignment_report.md` | Include if present under `aligned/`. | ZIP deflate as small text. | Require readable regular file when present. | `runtime_sessions/demos/demo_xxx/aligned/alignment_report.md` | confirmed | Documentation artifact; absence should not invalidate old demos if the required alignment files exist. |
| `demo/ft300_timestamps.npz` | `demo_dir / manifest["npz"]["ft300"]` | Resolve relative to the demo directory. | ZIP store because `.npz` is already compressed. | Require file to exist and be non-empty. | `runtime_sessions/demos/demo_xxx/ft300_timestamps.npz` | confirmed | Required for FT external frame-id lookup. |
| `demo/xense_timestamps.npz` | `demo_dir / manifest["npz"]["xense"]` | Resolve relative to the demo directory. | ZIP store because `.npz` is already compressed. | Require file to exist and be non-empty. | `runtime_sessions/demos/demo_xxx/xense_timestamps.npz` | confirmed | Required for TAC external frame-id lookup. |
| `demo/realsense_metadata.npz` | `demo_dir / manifest["npz"]["realsense"]` | Resolve relative to the demo directory. | ZIP store because `.npz` is already compressed. | Require file to exist and be non-empty. | `runtime_sessions/demos/demo_xxx/realsense_metadata.npz` | confirmed | Required for RealSense metadata inspection and HDF5 rebuilds. |
| `demo/zmq_telemetry.npz` | `demo_dir / manifest["npz"]["zmq"]` | Resolve relative to the demo directory. | ZIP store because `.npz` is already compressed. | Require file to exist and be non-empty. | `runtime_sessions/demos/demo_xxx/zmq_telemetry.npz` | confirmed | Required for robot, GELLO, and gripper streams. |
| `demo/rosbag/metadata.yaml` | `demo_dir / manifest["rosbag_uri"] / metadata.yaml` | Resolve `manifest["rosbag_uri"]` relative to the demo directory. | ZIP deflate as small YAML. | Require `metadata.yaml` to exist after rosbag conversion. | `runtime_sessions/demos/demo_xxx/rosbag/metadata.yaml` | confirmed | Restore should keep the same demo-local `rosbag/` layout. |
| `demo/rosbag/rosbag_0.mcap` | `demo_dir / manifest["rosbag_uri"]` | Resolve `manifest["rosbag_uri"]`; convert the rosbag into the bundle rosbag directory. | `ros2 bag convert` to MCAP with internal zstd compression, preset `slow`, chunk size `64 MB`; ZIP store the resulting `.mcap`. | Require successful convert return code, non-empty `rosbag_0.mcap`, and `metadata.yaml`; do not deserialize every message and do not require reverse conversion. | `runtime_sessions/demos/demo_xxx/rosbag/rosbag_0.mcap` | design_confirmed | The current samples already contain MCAP, but archive creation should still follow the established rosbag conversion policy. |
| `runtime_frames/data_FT_YYYYMMDD_HHMMSS.npy.zst` | `repo_root / manifest["sensor_paths"]["ft300"]` | Resolve `manifest["sensor_paths"]["ft300"]` relative to repo root and cross-check `aligned_manifest["sources"]["ft300s_saved_file"]`. | `zstd -T0 -19`; ZIP store the `.npy.zst`. | Run `zstd -t` on the compressed file. | `runtime_frames/data_FT_YYYYMMDD_HHMMSS.npy` after decompression. | confirmed | Use manifest paths; do not infer the FT file by demo name. |
| `runtime_frames/data_TAC_YYYYMMDD_HHMMSS.npy.zst` | `repo_root / manifest["sensor_paths"]["xense"]` | Resolve `manifest["sensor_paths"]["xense"]` relative to repo root and cross-check `aligned_manifest["sources"]["xense_saved_file"]`. | `zstd -T0 -19`; ZIP store the `.npy.zst`. | Run `zstd -t` on the compressed file. | `runtime_frames/data_TAC_YYYYMMDD_HHMMSS.npy` after decompression. | confirmed | Use manifest paths; do not infer the TAC file by demo name. |
| `runtime_frames/runtime_config/runtime_OG000544` | `runtime_frames/YYYYMMDD_HHMMSS/runtime_OG000544` | For the TAC `.npy`, parse `YYYYMMDD_HHMMSS` from `data_TAC_YYYYMMDD_HHMMSS.npy`; choose the latest runtime config directory whose timestamp is earlier than the TAC timestamp. | ZIP store or deflate according to file type; sampled files are regular non-extension files. | Require readable regular file when the selected TAC config exists. | `runtime_frames/<selected_config_timestamp>/runtime_OG000544` | confirmed | The bundle path intentionally normalizes the selected timestamp directory to `runtime_config/`; restore must write back to the original selected timestamp directory. |
| `runtime_frames/runtime_config/runtime_OG001009` | `runtime_frames/YYYYMMDD_HHMMSS/runtime_OG001009` | Same selected runtime config directory as `runtime_OG000544`. | ZIP store or deflate according to file type; sampled files are regular non-extension files. | Require readable regular file when the selected TAC config exists. | `runtime_frames/<selected_config_timestamp>/runtime_OG001009` | confirmed | Sampled directories contain both tactile sensor config files. |

## RealSense topic access details

Use the topic list from:

1. `manifest["realsense_rosbag_postcheck"]["required_topics"]`, if present.
2. Otherwise `manifest["realsense_image_readiness"]["required_topics"]`.

Do not hardcode the topic list except as a validation expectation for the current formal demos.

Access method:

1. Resolve `manifest["rosbag_uri"]` against the demo directory.
2. Open with `rosbag2_py.SequentialReader`.
3. Filter to required topics.
4. Deserialize each message with `rclpy.serialization.deserialize_message` and `rosidl_runtime_py.utilities.get_message`.
5. Build a per-topic list in read order.
6. For each HDF5 row, use the matching `aligned_index.npz` per-stream index to select the decoded per-topic message.

Sample verification decoded all eight required image topics from `demo_20260601_194946`; color topics were `rgb8` with `[480, 640, 3]` `uint8`, aligned depth topics were `16UC1` with `[480, 640]` `uint16`.

Camera semantic mapping after review:

- `335122271402` / `cam1`: `wrist1`
- `335122272872` / `cam2`: `wrist2`
- `337322074345` / `cam3`: `side`
- `050222071619` / `cam4`: `top`

## ZMQ payload split

The upstream binary frame layout and sample data agree on:

- `source == 1`: GELLO, `valid_mask == 1`, finite `floats_58[:, 0:8]`.
- `source == 2`: robot, `valid_mask == 2`, finite `floats_58[:, 8:58]`.
- `source == 3`: gripper, `valid_mask == 4`, finite gripper byte fields and all `floats_58` NaN.

The ZMQ aligned indices are absolute row indices into the mixed `zmq_telemetry.npz`, not per-source row indices.
