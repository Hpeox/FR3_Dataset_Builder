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

The current data points to scalar object `.npy` files under `runtime_frames/`. The files are large; this audit read their `.npy` headers and code paths but did not fully load a GB-scale TAC sample.

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

Default service settings are currently:

- `sensor_id_0 = "OG000544"`
- `sensor_id_1 = "OG001009"`

Access pattern:

```python
obj = np.load(xense_path, allow_pickle=True).item()
frame_id = xense_npz["frame_id"][xense_pair_source_index]
frame = obj["frames_data"][f"{int(frame_id):05d}"]
rec0 = frame[f"{sensor_id_0}_rec"]
rec1 = frame[f"{sensor_id_1}_rec"]
```

The builder should discover actual key prefixes from the loaded frame or from a future persisted metadata field, rather than relying only on current defaults.

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

Repository RealSense docs identify `cam1` and `cam2` as wrist cameras, and `cam3` and `cam4` as global cameras. They do not identify which global camera is `top` and which is `side`.

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

Sample data confirmed:

- `source=1` rows have finite `floats_58[0:8]` and NaN robot slots.
- `source=2` rows have finite `floats_58[8:58]` and NaN GELLO slots.
- `source=3` rows have all `floats_58` NaN and gripper byte fields.
