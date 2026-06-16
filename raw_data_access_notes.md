# Raw data access notes

This document records the raw access contracts observed during the HDF5 schema audit.

## Demo entry point

The entry point for a raw demo is:

```text
runtime_sessions/demos/demo_xxx/manifest.json
```

A demo is processable only when both are true:

- `manifest.json` has `status == "done"`.
- `aligned/aligned_manifest.json` exists and has `status == "done"`.

For processable demos, the builder should consume existing alignment artifacts under `aligned/` and must not rerun timestamp alignment.

## Relative path rules observed in current data

Current sampled manifests use two different relative path contracts:

- `manifest["npz"][...]` and `manifest["rosbag_uri"]` are demo-directory relative.
- `manifest["sensor_paths"]["ft300"]` and `manifest["sensor_paths"]["xense"]` are repo-root relative, for example `runtime_frames/data_FT_20260601_194946.npy`.

This is not bash-cwd relative. The builder should resolve paths from explicit anchors:

- manifest-owned demo files: `demo_dir / relative_path`
- external `runtime_frames` files: `repo_root / relative_path`

If any required path is missing for a done/aligned demo, the builder should fail that demo with a clear runtime error. It should not invent fallback paths from filenames.

The builder should start from `manifest.json`, cross-check the same paths in `aligned_manifest["sources"]`, and fail the demo if any path mismatches. `aligned_manifest` is a consistency check, not a license to guess alternate locations.

## Alignment artifacts

The builder should read:

```text
aligned/aligned_index.npz
aligned/aligned_manifest.json
aligned/alignment_config.json
```

Important arrays in `aligned_index.npz`:

- `t_ns`: aligned target timeline.
- `sample_valid`: global validity across aligned streams.
- `<stream>_index`: source row index.
- `<stream>_time_ns`: selected source timestamp.
- `<stream>_delta_ns`: `source_time_ns - t_ns`.
- `<stream>_valid`: stream validity at that aligned timestep.

For ZMQ streams, `<stream>_index` is an absolute row index into the mixed `zmq_telemetry.npz`.

For FT300S and Xense, `<stream>_index` is a row index into the corresponding timestamp NPZ. Use that timestamp NPZ row's `frame_id` to access the external object `.npy` `frames_data` key.

For RealSense, `<stream>_index` is a per-topic message index into the decoded list for that topic.

Current row policy after review:

- Export all aligned rows: `T = len(idx["t_ns"])`.
- If `sample_valid` or a required `<stream>_valid` entry is false, record a warning with the HDF5 row index and invalid stream name.
- `invalid` does not necessarily mean no usable source index exists. If the required stream index is non-negative, use the indexed source value and still record the warning.
- If the required stream index is `-1`, use the previous frame information for that stream.
- If the first row for a required stream has index `-1` and no previous frame exists, remove that leading row from the HDF5 output.
- Print warnings to the terminal and write them to a JSON sidecar report.
- Never use `-1` as a normal NumPy/Python index. The builder must not accidentally treat `-1` as "last row".
- After leading-row removal, all remaining `-1` entries should have a previous stream frame available for reuse.

## NPZ files

The sampled processable demos have these NPZ files listed under `manifest["npz"]`:

```text
ft300_timestamps.npz
xense_timestamps.npz
realsense_metadata.npz
zmq_telemetry.npz
```

Observed key sets:

- `ft300_timestamps.npz`: `frame_id`, `timestamp_ns`, `recv_time_ns`, `recv_monotonic_ns`
- `xense_timestamps.npz`: `frame_id`, `timestamp_ns_0`, `timestamp_ns_1`, `recv_time_ns`, `recv_monotonic_ns`
- `realsense_metadata.npz`: `topic`, `frame_number`, `header_stamp_ns`, `frame_timestamp_ns`, `hw_timestamp_ns`, `clock_domain`, `recv_time_ns`, `recv_monotonic_ns`
- `zmq_telemetry.npz`: `source`, `seq`, `stamp_s`, `valid_mask`, `floats_58`, `gripper_gPO`, `gripper_gCU`, `recv_time_ns`, `recv_monotonic_ns`

The HDF5 builder should use `aligned_index.npz` for temporal selection and use these NPZ files for source data and row-to-frame-id lookup. It should not recompute alignment.

## External FT300S file

Path source:

```python
manifest["sensor_paths"]["ft300"]
```

The current data points to scalar object `.npy` files under `runtime_frames/`.

Access pattern:

```python
obj = np.load(ft_path, allow_pickle=True).item()
frame_id = ft_npz["frame_id"][ft300s_index]
frame = obj["frames_data"][f"{int(frame_id):05d}"]
wrench = frame["ft300_wrench"]
```

Observed frame fields from a sample FT file:

- `ft300_wrench`: shape `(6,)`, dtype `float64`
- `ft300_fx`, `ft300_fy`, `ft300_fz`, `ft300_tx`, `ft300_ty`, `ft300_tz`
- `ft300_timestamp_ns`
- `ft300_source`
- `ft300_crc_ok`
- `ft300_error_reason`

The schema wants `/observations/ft300s/wrench` as `float32`, so the builder must cast from raw `float64`.

## External Xense file

Path source:

```python
manifest["sensor_paths"]["xense"]
```

The current data points to scalar object `.npy` files under `runtime_frames/`. The selected builder strategy is to load the whole object with `np.load(..., allow_pickle=True).item()` and stream rows from memory.

Service-side save logic writes:

```python
obj = {
    "events": ...,
    "frames_data": {
        "00000": {
            "<sensor_id_0>_rec": ...,
            "<sensor_id_1>_rec": ...,
            "<sensor_id_0>_force": ...,
            "<sensor_id_1>_force": ...,
            "<sensor_id_0>_force_norm": ...,
            "<sensor_id_1>_force_norm": ...,
            "<sensor_id_0>_force_resultant": ...,
            "<sensor_id_1>_force_resultant": ...,
            "<sensor_id_0>_timestamp_ns": ...,
            "<sensor_id_1>_timestamp_ns": ...,
        }
    }
}
```

Sensor semantic mapping after review:

- `OG000544`: `left`
- `OG001009`: `right`

Access pattern:

```python
obj = np.load(xense_path, allow_pickle=True).item()
frame_id = xense_npz["frame_id"][xense_pair_source_index]
frame = obj["frames_data"][f"{int(frame_id):05d}"]
rec0 = frame[f"{sensor_id_0}_rec"]
rec1 = frame[f"{sensor_id_1}_rec"]
```

For the current dataset, stack tactile sensor dimension as `[left, right] == [OG000544, OG001009]`.

Xense SDK 2.0 stores `force`, `force_norm`, and `force_resultant` arrays as `float32`.
Older saved files may contain `float64` arrays. The HDF5 builder should accept either
raw dtype for these tactile force fields and normalize the HDF5 datasets under
`/observations/tactile/*` to `float32`.

## Archive resource discovery

Cold-storage archive creation should reuse the same authoritative resource discovery rules as the HDF5 builder:

1. Start from `runtime_sessions/demos/demo_xxx/manifest.json`.
2. Require `manifest.status == "done"` and `aligned/aligned_manifest.json` with `status == "done"` for the current archiveable set.
3. Resolve `manifest["npz"][...]` and `manifest["rosbag_uri"]` relative to the demo directory.
4. Resolve `manifest["sensor_paths"]["ft300"]` and `manifest["sensor_paths"]["xense"]` relative to the repository root.
5. Cross-check `manifest["sensor_paths"]["ft300"]` against `aligned_manifest["sources"]["ft300s_saved_file"]`.
6. Cross-check `manifest["sensor_paths"]["xense"]` against `aligned_manifest["sources"]["xense_saved_file"]`.
7. Cross-check `manifest["rosbag_uri"]` against `aligned_manifest["sources"]["rosbag_uri"]`.

If any required path is missing or any cross-check mismatches, archive creation should fail that demo. It should not infer replacement paths from file names.

## Archive runtime config discovery

TAC runtime config files are not listed directly in the sampled demo manifests. They live in timestamped directories under `runtime_frames/`, for example:

```text
runtime_frames/20260605_163106/runtime_OG000544
runtime_frames/20260605_163106/runtime_OG001009
```

For each archived TAC external file:

1. Resolve the TAC `.npy` from `manifest["sensor_paths"]["xense"]`.
2. Parse the timestamp from the TAC file name, for example `data_TAC_20260605_165503.npy` -> `20260605_165503`.
3. List timestamp-named `runtime_frames/YYYYMMDD_HHMMSS/` directories.
4. Select the latest directory whose timestamp is strictly earlier than the TAC `.npy` timestamp.
5. Include the selected directory's `runtime_OG000544` and `runtime_OG001009` files.

The bundle should store these files under:

```text
runtime_frames/runtime_config/runtime_OG000544
runtime_frames/runtime_config/runtime_OG001009
```

The external archive metadata should record the original selected timestamp directory so restore can write the files back to:

```text
runtime_frames/<selected_config_timestamp>/runtime_OG000544
runtime_frames/<selected_config_timestamp>/runtime_OG001009
```

## Archive bundle path construction

Each demo archive should publish:

```text
archives/demo_xxx.zip
archives/demo_xxx.archive.json
```

The ZIP should contain one logical bundle root:

```text
demo_xxx_bundle/
```

Inside that root:

- `archive_manifest.json` is generated archive metadata.
- `demo/` is the complete raw `runtime_sessions/demos/demo_xxx/` directory.
- `runtime_frames/data_FT_*.npy.zst` is the compressed external FT file referenced by the manifest.
- `runtime_frames/data_TAC_*.npy.zst` is the compressed external TAC file referenced by the manifest.
- `runtime_frames/runtime_config/` contains the selected TAC runtime config files.

Bundle paths should be deterministic and should not depend on the shell working directory.

## Archive compression selection

Use the established compression policy:

- External `.npy` files: compress with `zstd -T0 -19`, output `.npy.zst`.
- Rosbags: use `ros2 bag convert` to MCAP with internal zstd compression, preset `slow`, and chunk size `64 MB`.
- Outer ZIP: one ZIP per demo, ZIP64 enabled.

ZIP entry policy:

- Store `.zst`, `.mcap`, `.npz`, and other already-compressed files.
- Deflate small text files such as `.json`, `.yaml`, `.md`, and other metadata.
- ZIP is for single-file packaging and path preservation, not the main compression layer.

ZIP remains acceptable only while inspection confirms the archived data has no required Unix owner, permission, symlink, or special-file semantics.

## Archive metadata and lifecycle

Do not write archive state into the raw demo's `manifest.json`.

Each published archive must have an external sidecar:

```text
archives/demo_xxx.archive.json
```

It should contain at least:

- `demo_id`
- `archive_format`
- `archive_path`
- `created_at`
- `source_root`
- `restore_root`
- `zip_size`
- optional `zip_sha256`
- compression parameters
- validation results
- selected external source paths
- selected TAC runtime config timestamp directory
- `raw_released`

The persistent lifecycle is:

- `not_archived`: no successfully published ZIP plus external archive JSON.
- `archived`: successfully published and validated ZIP plus external archive JSON.
- `raw_released`: archive JSON records that the original raw resources were released.

If archive construction fails or is interrupted before both final artifacts are successfully published, treat the demo as `not_archived` and rebuild it on the next run.

## Archive validation

For `.npy.zst`:

```text
zstd -t
```

For converted rosbag/MCAP:

- require successful `ros2 bag convert` return code
- require `rosbag_0.mcap` to exist and be non-empty
- require `metadata.yaml` to exist
- do not deserialize every message
- do not perform reverse conversion
- do not require checksum equivalence with the original bag

For the final archive:

- require successful ZIP creation
- require the ZIP to exist and be non-empty
- require the external `demo_xxx.archive.json` to be written successfully
- optionally compute ZIP SHA-256 before long-term storage or transfer

## Archive restore destinations

Restore behavior should be fixed and deterministic:

- Restore bundle `demo/` to `runtime_sessions/demos/demo_xxx/`.
- Decompress and restore `runtime_frames/data_FT_*.npy.zst` to the original `runtime_frames/data_FT_*.npy`.
- Decompress and restore `runtime_frames/data_TAC_*.npy.zst` to the original `runtime_frames/data_TAC_*.npy`.
- Restore `runtime_frames/runtime_config/runtime_OG000544` and `runtime_frames/runtime_config/runtime_OG001009` to the selected original timestamp directory recorded in archive metadata.
- Do not overwrite existing files by default.
- Allow overwriting only with an explicit force option.

Do not design arbitrary restore roots or a restore planner.

## RealSense rosbag

Path source:

```python
manifest["rosbag_uri"]
```

Access pattern:

1. Resolve against the demo directory.
2. Open with `rosbag2_py.SequentialReader`.
3. Filter to required image topics from manifest readiness/postcheck.
4. Deserialize `sensor_msgs/msg/Image` messages.
5. Decode `rgb8` as `uint8` `[height, width, 3]`.
6. Decode `16UC1` as `uint16` `[height, width]`.

The sampled demos record eight image topics:

- `/cam1/camera/color/image_raw`
- `/cam1/camera/aligned_depth_to_color/image_raw`
- `/cam2/camera/color/image_raw`
- `/cam2/camera/aligned_depth_to_color/image_raw`
- `/cam3/camera/color/image_raw`
- `/cam3/camera/aligned_depth_to_color/image_raw`
- `/cam4/camera/color/image_raw`
- `/cam4/camera/aligned_depth_to_color/image_raw`

Camera semantic mapping after review:

- `335122271402` / `cam1`: `wrist1`
- `335122272872` / `cam2`: `wrist2`
- `337322074345` / `cam3`: `side`
- `050222071619` / `cam4`: `top`

## ZMQ telemetry

Access pattern:

```python
z = np.load(demo_dir / manifest["npz"]["zmq"], allow_pickle=True)
src_rows = idx["zmq_source_2_index"][rows]
robot_q = z["floats_58"][src_rows, 8:15]
```

Payload split:

```text
source=1 gello:
  floats_58[0:7]   GELLO joint positions
  floats_58[7]     GELLO gripper command

source=2 robot:
  floats_58[8:15]  q
  floats_58[15:22] dq
  floats_58[22:29] tau_J
  floats_58[29:36] tau_J_d
  floats_58[36:52] O_T_EE
  floats_58[52:58] O_dP_EE

source=3 gripper:
  gripper_gPO
  gripper_gCU
```

For `O_T_EE`, reshape `floats_58[36:52]` to `[4, 4]` and validate that the last row is `[0, 0, 0, 1]`.

Sample data confirmed:

- `source=1` rows have finite `floats_58[0:8]` and NaN robot slots.
- `source=2` rows have finite `floats_58[8:58]` and NaN GELLO slots.
- `source=3` rows have all `floats_58` NaN and gripper byte fields.
