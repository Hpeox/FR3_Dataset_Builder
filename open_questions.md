# Open questions and resolved decisions before implementing the HDF5 builder

## Row policy

Resolved decision:

- HDF5 `T = aligned_manifest["sample_count"] = len(aligned_index["t_ns"])`.
- Export all aligned rows, including rows where `aligned_index["sample_valid"] == False`.
- Record warnings with invalid HDF5 row indices and invalid stream names.
- `invalid` does not necessarily mean the source index is missing. If a required stream has a valid non-negative source index, use that indexed source value and record the warning.
- If a required stream index is `-1`, use the previous frame information for that stream.
- If the first row for a required stream has index `-1` and no previous frame exists, remove that leading row from the HDF5 output.
- Warnings must be printed to the terminal and written to a JSON sidecar report.

The sampled first three demos have `sample_count == valid_count`, but `demo_20260605_165503` has `sample_count == 961` and `valid_count == 960`.

The builder must not accidentally use `-1` as a valid Python index.

## Success label

Resolved decision:

- For current processable demos, write `/attrs/success = True`.
- Failed-demo labels can be added later when that workflow is implemented.

Current `manifest.status == "done"` still remains the processability gate together with `aligned_manifest.status == "done"`.

## Language instruction

Resolved decision:

- Use a placeholder string for now.
- The placeholder should be a builder CLI/config value, because no raw manifest field, sidecar annotation file, or code path currently stores this value.
- (from user) just use "a placeholder string" as the placeholder string
- (from user) also leave a #TODO tag somewhere in the code, in the furture the string will be determined by specific key in manifest

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

Resolved mapping:

- `335122271402`: `wrist1`
- `335122272872`: `wrist2`
- `050222071619`: `top`
- `337322074345`: `side`

The builder should apply this serial-number mapping for the current dataset.

## Tactile semantic names

Current Xense code uses sensor IDs:

- `sensor_id_0 = "OG000544"`
- `sensor_id_1 = "OG001009"`

The HDF5 schema wants:

```text
sensor_names = ["left", "right"]
```

Resolved mapping:

- `OG000544`: `left`
- `OG001009`: `right`

The builder should stack tactile sensor dimension as `[left, right] == [OG000544, OG001009]`.

## Xense file loading strategy

Xense external `.npy` files are scalar object arrays with GB-scale pickled contents.

Resolved decision:

- Load the whole object with `np.load(..., allow_pickle=True).item()` and stream rows from memory.

This audit did not fully load a TAC sample to avoid expensive terminal-side work.

## Path anchor contract

Current raw data requires:

- demo-local paths for `manifest["npz"]` and `manifest["rosbag_uri"]`
- repo-root-relative paths for `manifest["sensor_paths"]`

The future builder CLI should make the repo root explicit and should fail if the manifest path contract is violated.

Resolved decision:

1. Start from `manifest.json`.
2. Cross-check the same paths in `aligned_manifest["sources"]`.
3. Fail on mismatch rather than guessing.

## O_T_EE matrix order

The ZMQ payload defines `robot[28:44]` as `O_T_EE`.

Resolved decision:

- The `T` matrix should always have the same last row `[0, 0, 0, 1]`.
- Reshape the 16 stored floats to `[4, 4]` and validate that last row.

If this validation fails, the builder should fail or flag that row rather than silently accepting a malformed transform.

# Resolved answers

## Row policy
Use all rows, but probably leave a warning with row index at somewhere

(from user) invalid does not necessarily mean no valid index. If index is `-1`, use the previous frame information. Warnings should be printed to terminal and written to a JSON sidecar report.

(from user) If the first row has `-1` and no previous frame exists, remove that first row directly.

## Success label
currently, all the label should be success, fail demo maybe will be implemented in the furture

## Language instruction

currently, use a placeholder instead

## Camera semantic names

a serial number to name map:

- `335122271402`: wrist1
- `335122272872`: wrist2
- `050222071619`: top
- `337322074345`: side

## Tactile semantic names

- `OG000544`: left
- `OG001009`: right

## Xense file loading strategy

- Load the whole object with `np.load(..., allow_pickle=True).item()` and stream rows from memory.

## Path anchor contract

throw out an error if any mismatch between `manifest.json` and `aligned_manifest`

just as the Recommended conservative rule:

1. Start from `manifest.json`.
2. Cross-check the same paths in `aligned_manifest["sources"]`.
3. Fail on mismatch rather than guessing.

## O_T_EE matrix order

the T matrix should always have a same last row `[0, 0, 0, 1]`
