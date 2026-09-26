"""Build the CorvinOS demo Blender scene.

Run once, headless, AFTER generate_narration.py (reads its
narration_duration.json to calibrate frame_end to the REAL measured audio
duration, not a guess):

    blender --background --factory-startup --python build_scene.py

Produces corvinos_demo.blend: extruded 3D "CorvinOS" title text (amber, the
Corvin brand color per CLAUDE.md's dataviz section), a ring of small
orbiting modules symbolizing skills/plugins, a slow orbiting camera, and the
real narration.mp3 baked in as a VSE sound strip -- same technique as
tests/fixtures/video_producer/build_fixture_scene.py.
"""

import json
import math
import os

import bpy

HERE = os.path.dirname(os.path.abspath(__file__))

with open(os.path.join(HERE, "narration_duration.json")) as f:
    AUDIO_CONFIG = json.load(f)

FPS = AUDIO_CONFIG["fps"]
FRAME_END = AUDIO_CONFIG["frame_end"]
AUDIO_FILE = AUDIO_CONFIG["audio_file"]

# Corvin brand amber (CLAUDE.md "Charts in the Console" section: Corvin's
# amber, never aliased from a UI accent token -- same principle applies to
# a literal brand color here: pick the real value, don't approximate).
AMBER = (0.90, 0.55, 0.10, 1.0)
DARK_BG = (0.04, 0.04, 0.06, 1.0)


def make_emission_material(name: str, color, strength: float = 2.0):
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


def build() -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene

    scene.render.engine = 'CYCLES'
    scene.cycles.device = 'CPU'
    scene.cycles.samples = 16  # more geometry than the plain cube fixture; still fast

    scene.render.fps = FPS
    scene.frame_start = 1
    scene.frame_end = FRAME_END

    scene.render.resolution_x = 960
    scene.render.resolution_y = 540
    scene.render.resolution_percentage = 100

    scene.world = bpy.data.worlds.new("World")
    scene.world.use_nodes = True
    scene.world.node_tree.nodes["Background"].inputs[0].default_value = DARK_BG

    # ── 3D title text: "CorvinOS" ────────────────────────────────────────
    bpy.ops.object.text_add(location=(0, 0, 0))
    title = bpy.context.active_object
    title.name = "TitleText"
    title.data.body = "CorvinOS"
    title.data.align_x = 'CENTER'
    title.data.align_y = 'CENTER'
    title.data.extrude = 0.08
    title.data.bevel_depth = 0.01
    title.data.size = 1.6
    title.rotation_euler = (math.radians(90), 0, 0)  # face the camera on X-forward
    title.data.materials.append(make_emission_material("AmberEmission", AMBER, 2.5))

    # Gentle continuous rotation over the whole runtime.
    title.rotation_euler = (math.radians(90), 0, 0)
    title.keyframe_insert(data_path="rotation_euler", frame=scene.frame_start)
    title.rotation_euler = (math.radians(90), 0, math.radians(360))
    title.keyframe_insert(data_path="rotation_euler", frame=scene.frame_end)
    for fcurve in title.animation_data.action.fcurves:
        for kp in fcurve.keyframe_points:
            kp.interpolation = 'LINEAR'

    # ── Orbiting modules: skills/plugins ─────────────────────────────────
    module_count = 8
    module_mat = make_emission_material("ModuleEmission", (0.2, 0.6, 0.9, 1.0), 1.5)
    for i in range(module_count):
        angle = (2 * math.pi / module_count) * i
        radius = 4.0
        x = radius * math.cos(angle)
        y = radius * math.sin(angle)
        bpy.ops.mesh.primitive_cube_add(size=0.4, location=(x, y, 0))
        module = bpy.context.active_object
        module.name = f"Module_{i:02d}"
        module.data.materials.append(module_mat)

        empty_name = f"Pivot_{i:02d}"
        bpy.ops.object.empty_add(type='PLAIN_AXES', location=(0, 0, 0))
        pivot = bpy.context.active_object
        pivot.name = empty_name
        module.parent = pivot
        module.location = (x, y, 0)

        pivot.rotation_euler = (0, 0, angle)
        pivot.keyframe_insert(data_path="rotation_euler", frame=scene.frame_start)
        pivot.rotation_euler = (0, 0, angle + math.radians(360))
        pivot.keyframe_insert(data_path="rotation_euler", frame=scene.frame_end)
        for fcurve in pivot.animation_data.action.fcurves:
            for kp in fcurve.keyframe_points:
                kp.interpolation = 'LINEAR'

    # ── Camera: slow orbit + dolly ────────────────────────────────────────
    bpy.ops.object.camera_add(location=(0, -10, 2))
    camera = bpy.context.active_object
    camera.name = "DemoCamera"
    scene.camera = camera

    cam_empty_name = "CameraPivot"
    bpy.ops.object.empty_add(type='PLAIN_AXES', location=(0, 0, 0))
    cam_pivot = bpy.context.active_object
    cam_pivot.name = cam_empty_name
    camera.parent = cam_pivot
    camera.location = (0, -10, 2)

    track = camera.constraints.new(type='TRACK_TO')
    track.target = title
    track.track_axis = 'TRACK_NEGATIVE_Z'
    track.up_axis = 'UP_Y'

    cam_pivot.rotation_euler = (0, 0, 0)
    cam_pivot.keyframe_insert(data_path="rotation_euler", frame=scene.frame_start)
    cam_pivot.rotation_euler = (0, 0, math.radians(140))
    cam_pivot.keyframe_insert(data_path="rotation_euler", frame=scene.frame_end)
    for fcurve in cam_pivot.animation_data.action.fcurves:
        for kp in fcurve.keyframe_points:
            kp.interpolation = 'LINEAR'

    # ── Lighting ──────────────────────────────────────────────────────────
    bpy.ops.object.light_add(type='SUN', location=(4, -4, 8))
    sun = bpy.context.active_object
    sun.name = "KeySun"
    sun.data.energy = 2.0

    bpy.ops.object.light_add(type='POINT', location=(-4, 3, 3))
    fill = bpy.context.active_object
    fill.name = "FillLight"
    fill.data.energy = 200.0
    fill.data.color = (0.9, 0.7, 1.0)

    # ── Real narration audio, baked as a VSE sound strip ─────────────────
    audio_path = os.path.join(HERE, AUDIO_FILE)
    scene.sequence_editor_create()
    scene.sequence_editor.sequences.new_sound(
        name="Narration",
        filepath=audio_path,
        channel=1,
        frame_start=scene.frame_start,
    )
    scene.sequence_editor.sequences_all["Narration"].sound.filepath = f"//{AUDIO_FILE}"

    out_path = os.path.join(HERE, "corvinos_demo.blend")
    bpy.ops.wm.save_as_mainfile(filepath=out_path)
    print(f"CorvinOS demo scene saved to {out_path}")
    print(f"  frame_end={scene.frame_end} fps={scene.render.fps} "
          f"({scene.frame_end / scene.render.fps:.2f}s)")


if __name__ == "__main__":
    build()
