"""Build the video_producer Blender fixture scene.

Run once, headless, to (re)generate simple_scene.blend:

    blender --background --factory-startup --python build_fixture_scene.py

Produces a tiny (2s @ 25fps = 50 frame) animated scene: a rotating cube, one
camera, one sun light. Cycles+CPU is baked into the .blend file itself
(the orchestrator's generated render script only sets resolution/fps/frame_end,
never the render engine or device -- see blender_orchestrator.py's
_auto_generate_bpy_script), so headless CI runs never depend on a GPU/EGL
context being available.

Also bakes a 2s sine-tone WAV (narration.wav, generated deterministically via
ffmpeg -- not TTS) into a VSE sound strip, so the render path can be proven to
carry a real audio stream end-to-end without depending on a TTS worker.
"""

import math
import os
import sys

import bpy

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + "/../../..")
from core.skills.os_skills.video_producer import blender_scene_kit as kit  # noqa: E402

FPS = 25
DURATION_SEC = 2
FRAME_END = FPS * DURATION_SEC  # 50


def build() -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene

    # samples=4, motion_blur=False -- this is a 2s smoke-test fixture proving
    # the render path works end-to-end, not a quality render.
    kit.configure_render_basics(
        scene, resolution=(320, 240), fps=FPS, frame_end=FRAME_END,
        samples=4, motion_blur=False,
    )

    bpy.ops.mesh.primitive_cube_add(location=(0, 0, 0))
    cube = bpy.context.active_object
    cube.name = "FixtureCube"

    cube.rotation_euler = (0, 0, 0)
    cube.keyframe_insert(data_path="rotation_euler", frame=scene.frame_start)
    cube.rotation_euler = (0, 0, math.radians(360))
    cube.keyframe_insert(data_path="rotation_euler", frame=scene.frame_end)
    kit.set_linear_interpolation(cube)

    bpy.ops.object.camera_add(
        location=(4, -4, 3),
        rotation=(math.radians(60), 0, math.radians(45)),
    )
    camera = bpy.context.active_object
    camera.name = "FixtureCamera"
    scene.camera = camera

    bpy.ops.object.light_add(type='SUN', location=(4, -4, 6))
    light = bpy.context.active_object
    light.name = "FixtureSun"
    light.data.energy = 3.0

    fixture_dir = os.path.dirname(os.path.abspath(__file__))
    audio_path = os.path.join(fixture_dir, "narration.wav")
    if os.path.exists(audio_path):
        scene.sequence_editor_create()
        scene.sequence_editor.sequences.new_sound(
            name="FixtureNarration",
            filepath=audio_path,
            channel=1,
            frame_start=scene.frame_start,
        )
        # Relative path so the strip stays valid regardless of where this repo
        # is checked out, as long as narration.wav ships alongside the .blend.
        scene.sequence_editor.sequences_all["FixtureNarration"].sound.filepath = "//narration.wav"

    out_path = os.path.join(fixture_dir, "simple_scene.blend")
    bpy.ops.wm.save_as_mainfile(filepath=out_path)
    print(f"Fixture scene saved to {out_path}")


if __name__ == "__main__":
    build()
