"""Headless proof that simple_scene.blend is what build_fixture_scene.py claims.

Run via a real Blender subprocess (never import this file directly -- it needs
`bpy`, which only exists inside a running Blender process):

    blender --background --factory-startup --python verify_fixture_scene.py

Exits 0 with "FIXTURE VERIFICATION PASSED" on success, exits 1 with a list of
failures otherwise. This is the reachability/proof boundary for Phase 1 of the
video_producer Blender plan: it opens the .blend file fresh in a separate
process and inspects real bpy state, not the build script's in-memory objects.
"""

import os
import sys

import bpy

EXPECTED_FPS = 25
EXPECTED_FRAME_START = 1
EXPECTED_FRAME_END = 50
REQUIRED_OBJECTS = {"FixtureCube", "FixtureCamera", "FixtureSun"}


def verify() -> int:
    blend_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "simple_scene.blend")
    bpy.ops.wm.open_mainfile(filepath=blend_path)

    scene = bpy.context.scene
    errors = []

    if scene.frame_start != EXPECTED_FRAME_START:
        errors.append(f"expected frame_start={EXPECTED_FRAME_START}, got {scene.frame_start}")
    if scene.frame_end != EXPECTED_FRAME_END:
        errors.append(f"expected frame_end={EXPECTED_FRAME_END}, got {scene.frame_end}")
    if scene.render.fps != EXPECTED_FPS:
        errors.append(f"expected fps={EXPECTED_FPS}, got {scene.render.fps}")
    if scene.render.engine != 'CYCLES':
        errors.append(f"expected render.engine=CYCLES, got {scene.render.engine}")
    if scene.cycles.device != 'CPU':
        errors.append(f"expected cycles.device=CPU, got {scene.cycles.device}")

    object_names = {obj.name for obj in bpy.data.objects}
    missing = REQUIRED_OBJECTS - object_names
    if missing:
        errors.append(f"missing objects: {sorted(missing)}")

    if scene.camera is None or scene.camera.name != "FixtureCamera":
        errors.append("scene.camera is not set to FixtureCamera")

    seq_editor = scene.sequence_editor
    if seq_editor is None or "FixtureNarration" not in seq_editor.sequences_all:
        errors.append("no FixtureNarration sound strip in the sequence editor")
    else:
        strip = seq_editor.sequences_all["FixtureNarration"]
        if strip.sound is None:
            errors.append("FixtureNarration strip has no sound datablock")

    cube = bpy.data.objects.get("FixtureCube")
    if cube is None or not cube.animation_data or not cube.animation_data.action:
        errors.append("FixtureCube has no animation data (not animated)")
    else:
        frames = {
            kp.co[0]
            for fcurve in cube.animation_data.action.fcurves
            for kp in fcurve.keyframe_points
        }
        if len(frames) < 2:
            errors.append(f"expected >=2 distinct keyframe frames on FixtureCube, got {sorted(frames)}")

    if errors:
        print("FIXTURE VERIFICATION FAILED:")
        for e in errors:
            print(f"  - {e}")
        return 1

    print("FIXTURE VERIFICATION PASSED")
    print(f"  objects={sorted(object_names)}")
    print(f"  frame_start={scene.frame_start} frame_end={scene.frame_end} fps={scene.render.fps}")
    print(f"  engine={scene.render.engine} device={scene.cycles.device}")
    return 0


if __name__ == "__main__":
    sys.exit(verify())
