## context
T = total_steps，通常约 900–1000

K = min(T, 512)          # 低维数据 chunk_t

C = 8                    # 图像 / depth / 空间触觉数据 chunk_t

H = 480, W = 640         # RealSense 图像尺寸

TH = 700, TW = 400       # 触觉图像尺寸


compression = zstd(level=12)

shuffle = true for float64 / float32 / uint16

shuffle = false or omitted for uint8

## schema
``` text
demo_xxx.h5
/
├── attrs
│   ├── demo_id: string
│   ├── success: bool
│   ├── total_steps: int
│   ├── schema_version: "v0.3"
│   ├── nominal_hz: 30
│   ├── task_name: "<manifest.task_name>"
│   ├── language_instruction: "<manifest.language_instruction>"
│   ├── spatial_chunk_t: 8
│   ├── lowdim_chunk_t: 512
│   ├── compression: "zstd"
│   └── compression_level: 12
│
├── actions
│   ├── gello_q
│   │   shape: [T, 7]
│   │   dtype: float64
│   │   chunks: [K, 7]
│   │   compression: zstd(level=12) + shuffle
│   │   source: GELLO input[0:7]
│   │
│   └── gello_gripper_cmd
│       shape: [T]
│       dtype: float64
│       chunks: [K]
│       compression: zstd(level=12) + shuffle
│       source: GELLO input[7]
│
└── observations
    │
    ├── robot_state
    │   ├── q
    │   │   shape: [T, 7]
    │   │   dtype: float64
    │   │   chunks: [K, 7]
    │   │   compression: zstd(level=12) + shuffle
    │   │   source: robot[0:7]
    │   │
    │   ├── dq
    │   │   shape: [T, 7]
    │   │   dtype: float64
    │   │   chunks: [K, 7]
    │   │   compression: zstd(level=12) + shuffle
    │   │   source: robot[7:14]
    │   │
    │   ├── tau_J
    │   │   shape: [T, 7]
    │   │   dtype: float64
    │   │   chunks: [K, 7]
    │   │   compression: zstd(level=12) + shuffle
    │   │   source: robot[14:21]
    │   │
    │   ├── tau_J_d
    │   │   shape: [T, 7]
    │   │   dtype: float64
    │   │   chunks: [K, 7]
    │   │   compression: zstd(level=12) + shuffle
    │   │   source: robot[21:28]
    │   │
    │   ├── O_T_EE
    │   │   shape: [T, 4, 4]
    │   │   dtype: float64
    │   │   chunks: [K, 4, 4]
    │   │   compression: zstd(level=12) + shuffle
    │   │   source: robot[28:44], reshaped from 16 floats
    │   │
    │   └── O_dP_EE
    │       shape: [T, 6]
    │       dtype: float64
    │       chunks: [K, 6]
    │       compression: zstd(level=12) + shuffle
    │       source: robot[44:50]
    │
    ├── gripper
    │   ├── gPO
    │   │   shape: [T]
    │   │   dtype: uint8
    │   │   chunks: [K]
    │   │   compression: zstd(level=12)
    │   │   source: gripper_gPO
    │   │
    │   └── gCU
    │       shape: [T]
    │       dtype: uint8
    │       chunks: [K]
    │       compression: zstd(level=12)
    │       source: gripper_gCU
    │
    ├── ft300s
    │   └── wrench
    │       shape: [T, 6]
    │       dtype: float32
    │       chunks: [K, 6]
    │       compression: zstd(level=12) + shuffle
    │       source: FT300S wrench, original precision ≈ int16 / 1000
    │
    ├── rgb
    │   ├── top
    │   │   shape: [T, 480, 640, 3]
    │   │   dtype: uint8
    │   │   chunks: [8, 480, 640, 3]
    │   │   compression: zstd(level=12)
    │   │
    │   ├── side
    │   │   shape: [T, 480, 640, 3]
    │   │   dtype: uint8
    │   │   chunks: [8, 480, 640, 3]
    │   │   compression: zstd(level=12)
    │   │
    │   └── wrist
    │       shape: [T, 2, 480, 640, 3]
    │       dtype: uint8
    │       chunks: [8, 2, 480, 640, 3]
    │       compression: zstd(level=12)
    │       attrs:
    │         camera_names = ["wrist1", "wrist2"]
    │
    ├── depth
    │   ├── top
    │   │   shape: [T, 480, 640]
    │   │   dtype: uint16
    │   │   chunks: [8, 480, 640]
    │   │   compression: zstd(level=12) + shuffle
    │   │   attrs:
    │   │     unit = "millimeter"
    │   │     encoding = "aligned_depth_to_color_uint16"
    │   │
    │   ├── side
    │   │   shape: [T, 480, 640]
    │   │   dtype: uint16
    │   │   chunks: [8, 480, 640]
    │   │   compression: zstd(level=12) + shuffle
    │   │   attrs:
    │   │     unit = "millimeter"
    │   │     encoding = "aligned_depth_to_color_uint16"
    │   │
    │   └── wrist
    │       shape: [T, 2, 480, 640]
    │       dtype: uint16
    │       chunks: [8, 2, 480, 640]
    │       compression: zstd(level=12) + shuffle
    │       attrs:
    │         camera_names = ["wrist1", "wrist2"]
    │         unit = "millimeter"
    │         encoding = "aligned_depth_to_color_uint16"
    │
    ├── tactile_images
    │   └── bgr
    │       shape: [T, 2, 700, 400, 3]
    │       dtype: uint8
    │       chunks: [8, 2, 700, 400, 3]
    │       compression: zstd(level=12)
    │       channel order: BGR
    │       attrs:
    │         sensor_names = ["left", "right"]
    │
    └── tactile
        ├── force
        │   shape: [T, 2, 35, 20, 3]
        │   dtype: float32
        │   chunks: [8, 2, 35, 20, 3]
        │   compression: zstd(level=12) + shuffle
        │   attrs:
        │     sensor_names = ["left", "right"]
        │
        ├── force_norm
        │   shape: [T, 2, 35, 20, 3]
        │   dtype: float32
        │   chunks: [8, 2, 35, 20, 3]
        │   compression: zstd(level=12) + shuffle
        │   attrs:
        │     sensor_names = ["left", "right"]
        │
        └── force_resultant
            shape: [T, 2, 6]
            dtype: float32
            chunks: [K, 2, 6]
            compression: zstd(level=12) + shuffle
            attrs:
              sensor_names = ["left", "right"]
```

`task_name` and `language_instruction` are copied verbatim from the required
top-level `manifest.json` string fields. `task_name` must satisfy the safe task
slug rule. DatasetBuilder does not synthesize values or accept a CLI fallback.
