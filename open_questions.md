# Open questions before implementing the HDF5 builder

## Row policy

Should HDF5 `T` be:

- `aligned_manifest["sample_count"]` / `len(aligned_index["t_ns"])`, keeping invalid rows, or
- `aligned_manifest["valid_count"]` / `aligned_index["sample_valid"] == True`, filtering invalid rows?

The sampled first three demos have `sample_count == valid_count`, but `demo_20260605_165503` has `sample_count == 961` and `valid_count == 960`.

The first builder should probably export only `sample_valid` rows unless a sentinel policy is explicitly required.

## Success label

What should `/attrs/success` mean?

Current `manifest.status == "done"` means the capture transaction completed and required checks passed. It does not necessarily mean the task demonstration succeeded semantically.

No separate task-success label was found in manifests or sampled files.

## Language instruction

Where should `/attrs/language_instruction` come from?

No manifest field, sidecar annotation file, or code path was found that stores this value.

## Camera semantic names

Repository RealSense docs identify:

- `cam1`: wrist
- `cam2`: wrist
- `cam3`: global
- `cam4`: global

The HDF5 schema wants:

- `rgb/top`
- `rgb/side`
- `rgb/wrist[..., camera_names=["wrist1", "wrist2"]]`
- `depth/top`
- `depth/side`
- `depth/wrist[..., camera_names=["wrist1", "wrist2"]]`

Open mapping:

- Which of `cam3` and `cam4` is `top`?
- Which of `cam3` and `cam4` is `side`?
- Should `wrist1 == cam1` and `wrist2 == cam2`, or is there a physical left/right/front/back naming convention?

The builder should not guess this mapping silently.

## Tactile semantic names

Current Xense code uses sensor IDs:

- `sensor_id_0 = "OG000544"`
- `sensor_id_1 = "OG001009"`

The HDF5 schema wants:

```text
sensor_names = ["left", "right"]
```

Open mapping:

- Which serial corresponds to `left`?
- Which serial corresponds to `right`?
- Should the builder store serial IDs as additional attrs even if HDF5 uses `left/right`?

The builder should not hardcode `left/right` from the current sensor order without approval.

## Xense file loading strategy

Xense external `.npy` files are scalar object arrays with GB-scale pickled contents.

Open implementation choice:

- Load the whole object with `np.load(..., allow_pickle=True).item()` and stream rows from memory.
- Convert or pre-index Xense external files into a safer intermediate format before HDF5 materialization.
- Implement a dedicated reader for the current pickle/object format if partial loading is possible.

This audit did not fully load a TAC sample to avoid expensive terminal-side work.

## Path anchor contract

Current raw data requires:

- demo-local paths for `manifest["npz"]` and `manifest["rosbag_uri"]`
- repo-root-relative paths for `manifest["sensor_paths"]`

The future builder CLI should make the repo root explicit and should fail if the manifest path contract is violated.

Open question:

- Should `aligned_manifest["sources"]` be treated as the authority, or should the builder always start from `manifest.json` and only use `aligned_manifest` for cross-checks?

Recommended conservative rule:

1. Start from `manifest.json`.
2. Cross-check the same paths in `aligned_manifest["sources"]`.
3. Fail on mismatch rather than guessing.

## O_T_EE matrix order

The ZMQ payload defines `robot[28:44]` as `O_T_EE`.

Open question:

- Should the HDF5 builder reshape this as C-order `[4, 4]`, or does the upstream Franka convention require a transpose for downstream consumers?

The schema says only "reshaped from 16 floats"; it does not state memory order.
