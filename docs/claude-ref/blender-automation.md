# Blender automation — 3D learning videos (ADR-2033)

`tools/blender_3d_scene_generator.py` turns a learning-concept YAML into Blender
scenes, EXR frames and a `.blend` file. Loop A (3D PoC) task #1.

## Run

```bash
# validate only (host Python + PyYAML)
python3 tools/blender_3d_scene_generator.py validate learning_concepts/audit_chain.yaml
# build the .blend (no render)
python3 tools/blender_3d_scene_generator.py build learning_concepts/audit_chain.yaml
# quick preview: 2 frames per scene at 10 % resolution
python3 tools/blender_3d_scene_generator.py build learning_concepts/audit_chain.yaml \
    --render --preview --frames 1-2 --resolution 10
```

Output (default): `$CORVIN_HOME/tenants/<tid>/video_library/<concept>/` —
`<concept>.blend`, `frames/<scene>/frame_####.exr`, `report.json`,
`blender_attempt_N.log`. Exit codes: 0 ok · 1 build/render/validation failed
(see `report.json`) · 2 the concept YAML is invalid (Blender never started).

## Concept YAML

```yaml
name: audit_chain
fps: 30
render_settings: {device: AUTO, samples: 256, preview_samples: 64,
                  use_denoiser: true, adaptive_sampling: true, resolution: [1920, 1080]}
scenes:
  - name: chain
    template: linked_chain            # linked_chain | hash_transformation | checkmark_sequence
    template_params: {num_items: 4, spacing: 3.0}
    duration_s: 6                     # → frame range 1..duration_s*fps
    camera_angle: 90                  # camera orbits this many degrees over the scene
    narration: "…"                    # kept for the TTS step (task #2); not rendered
```

| Template | Params | Builds |
|---|---|---|
| `linked_chain` | `num_items` (1–50), `spacing` | event cubes joined by arrows |
| `hash_transformation` | `distance`, `pulse_period_s` | input cube → pulsating hash sphere → output cube |
| `checkmark_sequence` | `num_marks` (1–50), `interval_frames` | checkmarks appearing one after another |

## How it works

Host side validates the YAML (`ConceptError` → exit 2), writes `concept.json`,
runs `blender --background --factory-startup --python <this file> -- inner …`.
Blender side (`CorvinOS3DSceneGenerator`, `SceneTemplate`, `RenderOrchestrator`,
`QualityValidator`):

- geometry via **bmesh**, not `bpy.ops` — headless Blender has no window, and
  operators would add objects to whichever scene the context holds;
- one Blender scene per concept scene, sun light, dark world, camera on an
  orbiting pivot with a Track-To constraint;
- **device**: GPU (OptiX → CUDA → HIP → oneAPI → Metal) when Blender finds one,
  otherwise CPU with `fallback: true` in the report — never silent;
- **denoiser**: OptiX on OptiX GPUs, else OpenImageDenoise, else none —
  whatever this Blender build actually has, recorded as `device.denoiser`;
- **pre-render checks**: every mesh/curve has a material, a camera with focal
  length > 0, light or world present, sane bounds, non-empty frame range;
- **post-render checks**: every expected frame exists, is OpenEXR (magic bytes),
  has the expected size and non-empty pixel data;
- a failed render is retried up to twice; a pre-render problem is not retried
  (a YAML problem will not fix itself).

## This host (2026-09-23)

Blender 4.0.2 (Ubuntu package): **no GPU device, no denoiser** in the build —
renders run on 16 CPU cores without denoising. A full 17 s concept at 1080p /
256 samples is hours of CPU time; production renders belong on the GPU cluster
(or an upstream Blender build with OpenImageDenoise).

## Not built yet (rest of ADR-2033)

FFmpeg composition with narration audio (task #4), TTS (task #2), frame-hash
cache, batch/parallel rendering, and the `plugins/video_producer/generators/`
wrapper.

Tests: `tests/video_producer/test_blender_automation.py` (real CLI + real
Blender; skipped only when Blender is absent).
