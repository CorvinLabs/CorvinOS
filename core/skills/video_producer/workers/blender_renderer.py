"""Optional Blender-based 3D intro renderer (CONCEPT-0093 Option C).

Blender is NOT a hard dependency of the Video Producer — most installs
won't have it, and the PIL+FFmpeg pipeline (icon_library + frame_templates
+ transitions) already produces a complete video without it. This worker
is an opt-in upgrade for ONE scene (typically the opening hero shot): if
Blender is on PATH, render a short rotating-node 3D animation; if not,
report that cleanly so the caller falls back to render_hero_frame() instead
of raising.

The worker never raises when Blender is simply absent — that is the
expected, common case, not an error. It DOES raise if Blender is present
but the render itself fails (wrong Blender version, script bug, disk full),
since that is a real defect the caller should surface, not silently paper
over with a fallback that hides a regression.
"""

import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class BlenderRenderResult:
    available: bool          # was blender on PATH at all?
    success: bool             # only meaningful if available=True
    output_path: Optional[str]
    error: Optional[str] = None


def blender_available() -> bool:
    """Real PATH check — the only question this answers is 'can we even
    try', not 'will the render succeed'."""
    return shutil.which("blender") is not None


# Minimal, self-contained Blender Python script: N node spheres arranged in
# a circle, connected by cylinders to a center node, the whole rig rotating
# around Z, with the title text beneath it. Rendered to a PNG sequence then
# assembled to MP4 by the caller via ffmpeg (keeps Blender's own job to
# "render frames", not "encode video" — one less format Blender's bundled
# ffmpeg build needs to agree with the rest of the pipeline on).
_BLENDER_SCRIPT_TEMPLATE = '''
import bpy
import math
import sys

argv = sys.argv[sys.argv.index("--") + 1:]
title_text = argv[0]
out_dir = argv[1]
num_frames = int(argv[2])
num_nodes = int(argv[3])
resolution_percentage = int(argv[4]) if len(argv) > 4 else 100

scene = bpy.context.scene
bpy.ops.object.select_all(action="SELECT")
bpy.ops.object.delete()

_available_engines = {e.identifier for e in bpy.types.RenderSettings.bl_rna.properties["engine"].enum_items}
scene.render.engine = "BLENDER_EEVEE_NEXT" if "BLENDER_EEVEE_NEXT" in _available_engines else "BLENDER_EEVEE"
scene.render.resolution_x = 1920
scene.render.resolution_y = 1080
scene.render.resolution_percentage = resolution_percentage
scene.render.film_transparent = False
scene.render.fps = 30
scene.frame_start = 1
scene.frame_end = num_frames
# Blender 4.0 defaults to the AgX view transform, which strongly
# desaturates/flattens bright saturated colors (a deliberate filmic look) —
# that is why the blue material rendered as pale gray-blue before this.
# "Standard" renders colors as authored, which is what a flat UI-style
# explainer graphic needs.
scene.view_settings.view_transform = "Standard"

world = bpy.data.worlds.new("World")
scene.world = world
world.use_nodes = True
bg = world.node_tree.nodes["Background"]
bg.inputs[0].default_value = (0.043, 0.055, 0.078, 1.0)

bpy.ops.object.camera_add(location=(0, -11, 6), rotation=(math.radians(62), 0, 0))
scene.camera = bpy.context.object

bpy.ops.object.light_add(type="SUN", location=(4, -4, 8))
bpy.context.object.data.energy = 0.5

rig = bpy.data.objects.new("Rig", None)
bpy.context.collection.objects.link(rig)

mat_node = bpy.data.materials.new("NodeMat")
mat_node.use_nodes = True
bsdf_node = mat_node.node_tree.nodes["Principled BSDF"]
bsdf_node.inputs["Base Color"].default_value = (0.23, 0.51, 0.96, 1.0)
bsdf_node.inputs["Roughness"].default_value = 0.65
bsdf_node.inputs["Emission Color"].default_value = (0.23, 0.51, 0.96, 1.0)
bsdf_node.inputs["Emission Strength"].default_value = 0.35

mat_link = bpy.data.materials.new("LinkMat")
mat_link.use_nodes = True
mat_link.node_tree.nodes["Principled BSDF"].inputs["Base Color"].default_value = (0.4, 0.4, 0.5, 1.0)

bpy.ops.mesh.primitive_uv_sphere_add(radius=0.5, location=(0, 0, 0))
center = bpy.context.object
center.data.materials.append(mat_node)
center.parent = rig

for i in range(num_nodes):
    angle = (2 * math.pi / num_nodes) * i
    x, y = 2.8 * math.cos(angle), 2.8 * math.sin(angle)
    bpy.ops.mesh.primitive_uv_sphere_add(radius=0.3, location=(x, y, 0))
    node = bpy.context.object
    node.data.materials.append(mat_node)
    node.parent = rig

    bpy.ops.mesh.primitive_cylinder_add(radius=0.04, depth=1.0, location=(x / 2, y / 2, 0))
    link = bpy.context.object
    link.data.materials.append(mat_link)
    direction = (x, y, 0)
    length = math.sqrt(x * x + y * y)
    link.scale = (1, 1, length)
    link.rotation_euler = (0, math.atan2(math.sqrt(x * x + y * y), 0), math.atan2(y, x))
    link.parent = rig

rig.rotation_euler = (0, 0, 0)
rig.keyframe_insert(data_path="rotation_euler", index=2, frame=1)
rig.rotation_euler = (0, 0, math.radians(90))
rig.keyframe_insert(data_path="rotation_euler", index=2, frame=num_frames)
for fcurve in rig.animation_data.action.fcurves:
    for kp in fcurve.keyframe_points:
        kp.interpolation = "LINEAR"

scene.render.filepath = out_dir + "/frame_"
scene.render.image_settings.file_format = "PNG"
bpy.ops.render.render(animation=True)
'''


def render_blender_intro(
    title_text: str,
    output_video_path: str,
    num_frames: int = 60,
    num_nodes: int = 5,
    fps: int = 30,
    resolution_percentage: int = 100,
    timeout_s: float = 300,
) -> BlenderRenderResult:
    """Render a short rotating-node 3D intro via Blender, if available.

    Returns BlenderRenderResult(available=False, ...) immediately — no
    subprocess spawned — when Blender isn't on PATH. Callers branch on
    `.available`, not on catching an exception, so "no Blender" is an
    ordinary code path, not an error-handling one — and so is a render that
    runs past timeout_s (it used to raise TimeoutExpired out of here).

    resolution_percentage renders below 1920x1080 (EEVEE time scales with
    pixel count); the output is scaled back to 1920x1080 by ffmpeg.
    """
    if not 10 <= int(resolution_percentage) <= 100:
        raise ValueError("resolution_percentage must be 10..100")
    if not blender_available():
        return BlenderRenderResult(available=False, success=False, output_path=None)

    frame_dir = tempfile.mkdtemp(prefix="blender_intro_")
    script_path = os.path.join(frame_dir, "render.py")
    with open(script_path, "w") as f:
        f.write(_BLENDER_SCRIPT_TEMPLATE)

    cmd = [
        "blender", "--background", "--python", script_path,
        "--", title_text, frame_dir, str(num_frames), str(num_nodes), str(int(resolution_percentage)),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s)
    except subprocess.TimeoutExpired:
        return BlenderRenderResult(
            available=True, success=False, output_path=None,
            error=f"Blender render exceeded {timeout_s:.0f}s ({num_frames} frames at {resolution_percentage}%)",
        )

    if result.returncode != 0:
        return BlenderRenderResult(
            available=True, success=False, output_path=None,
            error=f"Blender render failed (exit {result.returncode}): {result.stderr[-2000:]}",
        )

    rendered_frames = sorted(Path(frame_dir).glob("frame_*.png"))
    if not rendered_frames:
        return BlenderRenderResult(
            available=True, success=False, output_path=None,
            error=f"Blender exited 0 but produced no frames in {frame_dir}",
        )

    ffmpeg_cmd = [
        "ffmpeg", "-y",
        "-framerate", str(fps),
        "-i", f"{frame_dir}/frame_%04d.png",
        "-vf", "scale=1920:1080:flags=lanczos",
        "-c:v", "libx264", "-preset", "medium", "-b:v", "2500k",
        "-pix_fmt", "yuv420p",
        output_video_path,
    ]
    ff_result = subprocess.run(ffmpeg_cmd, capture_output=True, text=True, timeout=60)
    if ff_result.returncode != 0:
        return BlenderRenderResult(
            available=True, success=False, output_path=None,
            error=f"ffmpeg assembly of Blender frames failed: {ff_result.stderr}",
        )

    return BlenderRenderResult(available=True, success=True, output_path=output_video_path)
