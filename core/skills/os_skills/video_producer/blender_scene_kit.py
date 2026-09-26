"""Reusable Blender scene-building helpers for video_producer's Blender
render path (BlenderHeadlessOrchestrator / blender_cli.py).

ONLY IMPORTABLE FROM WITHIN A RUNNING BLENDER PROCESS -- it needs ``bpy``,
which does not exist in the regular CorvinOS/pytest Python environment (no
``bpy`` pip package is installed; it only exists inside Blender's own
embedded interpreter). Scene-building scripts import this via
``blender --background --python <script>``, exactly like
``tests/fixtures/video_producer/build_fixture_scene.py`` and
``/home/shumway/projects/Corvin-Videos/*/build_blender_scene.py`` do. Never
import this from ``core/skills/os_skills/video_producer/__init__.py`` or any
other module regular CorvinOS code imports at process start -- that would
break every non-Blender caller of the video_producer package.

Consolidates lessons from building two real rendered videos this session
(see CorvinOS commit history + /home/shumway/projects/Corvin-Videos/
audit_chain_learn_video/): a scene assembled from Emission-only materials,
an unrotated Sun lamp, and BEZIER camera keyframes renders fast but looks
flat, shadowless, and produces a "stop-start" camera judder. Every helper
here exists because a specific version of that mistake was made and fixed:

- Emission-only materials ignore every scene light entirely (uniform flat
  color, no shading gradient, no real self-shadowing) -- ``make_lit_material``
  uses Principled BSDF instead, so blocks/objects actually respond to light.
- A Sun lamp's default rotation (0,0,0) points straight down, casting
  shadows almost directly under objects -- invisible from a frontal camera.
  ``add_key_fill_lights`` angles it for a real, visible raking shadow.
- BEZIER interpolation with AUTO_CLAMPED handles eases to a near-zero
  velocity AT every keyframe -- fine for 2-3 keyframes, but a "dolly out as
  N things reveal" camera rig with many keyframes turns into a repeated
  slow-stop-restart judder, which reads as "the video stutters" even though
  every frame renders correctly. ``animate_camera_stages`` uses LINEAR
  interpolation instead (constant velocity within each segment; a single
  slope change at each keyframe reads as far smoother than N micro-stops).
- Blender's default Filmic view transform desaturates/clips bright colors
  under-descriptively for a UI/brand-color scene (see
  ``configure_render_basics``'s own note).
- A Principled BSDF material on a LARGE surface (a whole ground plane) with
  real roughness/glossiness made one render run over 5x slower and blow
  through its timeout entirely -- ``make_lit_material`` is meant for small
  objects (props, not scene-spanning planes); keep large surfaces on
  ``make_diffuse_material`` (a plain Diffuse BSDF, no glossy lobe).
- No motion blur at 25fps makes camera movement look stroboscopic/juddery
  frame-to-frame even with perfectly smooth keyframe interpolation --
  ``configure_render_basics(motion_blur=True)`` (the default) turns it on.
"""
from __future__ import annotations

import math
from typing import Optional

import bpy


def configure_render_basics(
    scene,
    resolution=(960, 540),
    fps: int = 25,
    frame_end: Optional[int] = None,
    samples: int = 16,
    motion_blur: bool = True,
    background_color=(0.03, 0.03, 0.05, 1.0),
) -> None:
    """Engine, resolution, fps, color management, world background, motion
    blur -- the handful of scene-level settings every one of this session's
    Blender scenes needed and that are easy to silently omit one of.
    """
    scene.render.engine = 'CYCLES'
    scene.cycles.device = 'CPU'
    scene.cycles.samples = samples

    scene.render.fps = fps
    scene.frame_start = 1
    if frame_end is not None:
        scene.frame_end = frame_end

    scene.render.resolution_x = resolution[0]
    scene.render.resolution_y = resolution[1]
    scene.render.resolution_percentage = 100

    # Filmic (Blender's default) desaturates/compresses bright colors for a
    # realistic highlight rolloff -- it turns deliberately-chosen brand
    # colors (e.g. a UI's amber) into a washed-out beige. Standard renders
    # colors much closer to their literal RGB values, matching any PNG
    # stills (PowerPoint/SVG segments) that never go through Blender's color
    # pipeline at all, so a multi-segment video's segments match visually.
    scene.view_settings.view_transform = 'Standard'

    scene.world = bpy.data.worlds.new("World")
    scene.world.use_nodes = True
    scene.world.node_tree.nodes["Background"].inputs[0].default_value = background_color

    scene.render.use_motion_blur = motion_blur
    if motion_blur:
        scene.cycles.motion_blur_position = 'CENTER'

    # Every scene in this session needed OpenImageDenoiser disabled --
    # confirmed absent from this host's Blender build ("Build without
    # OpenImageDenoiser" is a hard render failure, not a quality knob).
    for view_layer in scene.view_layers:
        if hasattr(view_layer, "cycles"):
            view_layer.cycles.use_denoising = False


def make_lit_material(
    name: str,
    base_color,
    emission_strength: float = 0.35,
    roughness: float = 0.35,
    metallic: float = 0.25,
    texture_scale: float = 6.0,
):
    """Principled BSDF with a modest emission accent + a cheap procedural
    roughness texture -- for PROPS (small objects), not scene-spanning
    planes (see this module's docstring for why that distinction matters
    for render time). Real light/shadow response, not a flat unlit color.

    Emission strength is deliberately kept <= ~0.5: with the Standard view
    transform (see configure_render_basics), a channel driven past 1.0
    clips independently of the others -- amber (0.9, 0.55, 0.1) at strength
    2.2 clipped red+green to white while blue didn't, turning "amber" into
    "yellow" on screen. The Principled BSDF's own base_color already carries
    the visible color under real lighting; emission only needs to add a
    slight glow on top, not define the color by itself.
    """
    mat = bpy.data.materials.new(name=name)
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    nodes.clear()

    principled = nodes.new("ShaderNodeBsdfPrincipled")
    principled.inputs["Base Color"].default_value = base_color
    principled.inputs["Metallic"].default_value = metallic
    principled.inputs["Emission Color"].default_value = base_color
    principled.inputs["Emission Strength"].default_value = emission_strength

    noise = nodes.new("ShaderNodeTexNoise")
    noise.inputs["Scale"].default_value = texture_scale
    map_range = nodes.new("ShaderNodeMapRange")
    map_range.inputs["To Min"].default_value = max(0.0, roughness - 0.12)
    map_range.inputs["To Max"].default_value = min(1.0, roughness + 0.15)
    links.new(noise.outputs["Fac"], map_range.inputs["Value"])
    links.new(map_range.outputs["Result"], principled.inputs["Roughness"])

    output = nodes.new("ShaderNodeOutputMaterial")
    links.new(principled.outputs["BSDF"], output.inputs["Surface"])
    return mat


def make_diffuse_material(name: str, color):
    """Plain Diffuse BSDF -- for large surfaces (ground planes, backdrops)
    where a Principled BSDF's glossy lobe cost (see module docstring)
    isn't worth paying for a surface meant to read as flat-matte anyway."""
    mat = bpy.data.materials.new(name=name)
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    nodes.clear()
    diffuse = nodes.new("ShaderNodeBsdfDiffuse")
    diffuse.inputs["Color"].default_value = color
    output = nodes.new("ShaderNodeOutputMaterial")
    mat.node_tree.links.new(diffuse.outputs["BSDF"], output.inputs["Surface"])
    return mat


def make_flat_label_material(name: str, color, strength: float = 1.0):
    """Flat, unlit Emission color -- for text labels, where crisp
    readability regardless of scene lighting matters more than realistic
    shading (a shaded/shadowed label can become unreadable from some
    angles)."""
    mat = bpy.data.materials.new(name=name)
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    nodes.clear()
    emission = nodes.new("ShaderNodeEmission")
    emission.inputs["Color"].default_value = color
    emission.inputs["Strength"].default_value = strength
    output = nodes.new("ShaderNodeOutputMaterial")
    mat.node_tree.links.new(emission.outputs["Emission"], output.inputs["Surface"])
    return mat


def add_key_fill_lights(
    key_location=(0, 0, 10),
    key_energy: float = 3.0,
    key_angle_deg=(55.0, 0.0, -35.0),
    key_sun_size_deg: float = 2.0,
    fill_location=(-4, 3, 4),
    fill_energy: float = 180.0,
    fill_color=(0.75, 0.8, 1.0, 1.0),
):
    """A Sun key light (angled, NOT the default straight-down rotation --
    see module docstring) + a Point fill light. Returns (sun, fill).

    Sun lamps are directional: their POSITION doesn't affect the lighting,
    only rotation does. Leaving rotation at the default (0,0,0) -- which
    happens implicitly if you never set it -- points the sun straight down,
    casting shadows almost directly under objects and barely visible from a
    frontal camera. This helper always sets an angled rotation explicitly.
    """
    bpy.ops.object.light_add(type='SUN', location=key_location)
    sun = bpy.context.active_object
    sun.name = "KeySun"
    sun.data.energy = key_energy
    sun.data.angle = math.radians(key_sun_size_deg)
    sun.rotation_euler = tuple(math.radians(a) for a in key_angle_deg)

    bpy.ops.object.light_add(type='POINT', location=fill_location)
    fill = bpy.context.active_object
    fill.name = "FillLight"
    fill.data.energy = fill_energy
    fill.data.color = fill_color[:3]

    return sun, fill


def add_3d_label(
    location,
    text: str,
    material,
    size: float = 0.45,
    extrude: float = 0.02,
    rotation_euler=(math.pi / 2, 0, 0),
    name: Optional[str] = None,
):
    """A small extruded 3D text object, centered, facing -Y by default (the
    convention this session's scenes use for a camera parked on the -Y
    axis). Caller is responsible for animating it (e.g. pop-in scale
    keyframes matching a related object's reveal timing)."""
    bpy.ops.object.text_add(location=location)
    label = bpy.context.active_object
    if name:
        label.name = name
    label.data.body = text
    label.data.align_x = 'CENTER'
    label.data.align_y = 'CENTER'
    label.data.extrude = extrude
    label.data.size = size
    label.rotation_euler = rotation_euler
    label.data.materials.append(material)
    return label


def camera_distance_for_half_width(
    half_width: float,
    lens_mm: float = 35.0,
    sensor_width_mm: float = 36.0,
    margin_factor: float = 1.2,
) -> float:
    """Distance a camera at ``lens_mm`` needs to be from a subject so that
    ``half_width`` (subject half-extent + your own margin) fits horizontally
    in frame, with ``margin_factor`` extra headroom on top of the bare
    minimum. Use this instead of guessing a camera distance by eye -- a
    guessed distance is exactly how a previous version of this scene cut
    objects off at the frame edge."""
    half_fov_rad = math.atan(sensor_width_mm / (2 * lens_mm))
    min_distance = half_width / math.tan(half_fov_rad)
    return min_distance * margin_factor


def set_linear_interpolation(*objects) -> None:
    """LINEAR interpolation on every f-curve of the given objects' current
    animation data.

    Why not BEZIER/AUTO_CLAMPED: it eases velocity to near-zero AT every
    keyframe. Across 2-3 keyframes that's a nice cinematic ease; across the
    many keyframes a "reveal as content grows" camera rig needs, it becomes
    a repeated slow-stop-restart judder that reads as "the video stutters"
    even though every individual frame renders correctly. LINEAR keeps
    constant velocity within each segment -- a slope change at each
    keyframe, never a full stop.
    """
    for obj in objects:
        if obj.animation_data is None or obj.animation_data.action is None:
            continue
        for fcurve in obj.animation_data.action.fcurves:
            for kp in fcurve.keyframe_points:
                kp.interpolation = 'LINEAR'


def set_pop_in_scale(obj, appear_frame: int, settle_offset: int = 8, frame_start: int = 1) -> None:
    """A 'pop in' reveal: scale 0 -> 1 across ``settle_offset`` frames,
    starting at ``appear_frame``. Uses BACK interpolation deliberately (a
    brief overshoot-then-settle bounce reads as an intentional "appear"
    beat, not stutter, precisely because it's a single short event per
    object rather than a many-keyframe continuous motion -- the judder
    concern set_linear_interpolation's docstring describes doesn't apply
    to a one-shot pop-in the way it does to camera movement)."""
    obj.scale = (0.001, 0.001, 0.001)
    obj.keyframe_insert(data_path="scale", frame=max(frame_start, appear_frame - 1))
    obj.scale = (1.0, 1.0, 1.0)
    obj.keyframe_insert(data_path="scale", frame=appear_frame + settle_offset)
    for fcurve in obj.animation_data.action.fcurves:
        for kp in fcurve.keyframe_points:
            kp.interpolation = 'BACK' if kp.co[0] > frame_start else 'CONSTANT'
