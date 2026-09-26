"""Blender render CLI -- the real, non-test entry point for BlenderHeadlessOrchestrator.

Usage:
    python3 -m core.skills.os_skills.video_producer.blender_cli \\
        --blend tests/fixtures/video_producer/simple_scene.blend \\
        --output /tmp/my_video.mp4

fps/frame range/resolution are read directly from the .blend file's own
scene via a real, separate headless Blender subprocess -- never guessed. A
wrong guess here would silently truncate or extend the render relative to
what the scene actually authored.

This is the reachability / E2E-wiring-proof call site for
BlenderHeadlessOrchestrator.render_enhancement(): a real CLI invocation
outside the class's own definition file and outside any test file.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Optional

from core.skills.os_skills.video_producer.blender_orchestrator import (
    BlenderHeadlessOrchestrator,
    RenderConfig,
)

_PROBE_SCRIPT = """
import bpy
import json
scene = bpy.context.scene
print("BLEND_SCENE_PROBE:" + json.dumps({
    "fps": scene.render.fps,
    "frame_start": scene.frame_start,
    "frame_end": scene.frame_end,
    "resolution_x": scene.render.resolution_x,
    "resolution_y": scene.render.resolution_y,
}))
"""


def probe_blend_scene(blend_file: str) -> dict:
    """Read the .blend file's own authored fps/frame range/resolution.

    Runs a real, separate headless Blender subprocess -- the only way to
    read bpy scene state without importing bpy (which only exists inside a
    running Blender process, not in this plain python3 interpreter).
    """
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
        f.write(_PROBE_SCRIPT)
        script_path = f.name

    try:
        result = subprocess.run(
            ["blender", "--background", blend_file, "--python", script_path],
            capture_output=True,
            text=True,
            timeout=60,
        )
    finally:
        Path(script_path).unlink(missing_ok=True)

    if result.returncode != 0:
        raise RuntimeError(
            f"failed to probe {blend_file}: exit {result.returncode}\n{result.stderr}"
        )

    for line in result.stdout.splitlines():
        if line.startswith("BLEND_SCENE_PROBE:"):
            return json.loads(line[len("BLEND_SCENE_PROBE:"):])

    raise RuntimeError(f"probe of {blend_file} produced no BLEND_SCENE_PROBE line")


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--blend", required=True, help="Path to the .blend scene to render")
    parser.add_argument("--output", required=True, help="Durable destination path for the rendered MP4")
    parser.add_argument("--tenant-id", default="_default")
    parser.add_argument("--bitrate-kbps", type=int, default=2000)
    parser.add_argument("--codec", default="h264")
    parser.add_argument("--timeout-sec", type=int, default=600)
    args = parser.parse_args(argv)

    blend_path = Path(args.blend).resolve()
    if not blend_path.exists():
        print(f"error: blend file not found: {blend_path}", file=sys.stderr)
        return 1

    print(f"Probing scene: {blend_path}")
    try:
        scene_info = probe_blend_scene(str(blend_path))
    except RuntimeError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    # BlenderHeadlessOrchestrator's generated script only ever sets
    # scene.frame_end = config.frame_count (frame_start is left as whatever
    # the .blend already has) -- so reusing the scene's OWN frame_end here
    # reproduces exactly the authored range, not frame_end - frame_start + 1
    # frames starting over from frame 1.
    frame_count = scene_info["frame_end"]
    fps = scene_info["fps"]
    resolution = f'{scene_info["resolution_x"]}x{scene_info["resolution_y"]}'
    print(
        f"Scene: fps={fps} frame_start={scene_info['frame_start']} "
        f"frame_end={scene_info['frame_end']} resolution={resolution}"
    )

    orchestrator = BlenderHeadlessOrchestrator(
        blend_file=str(blend_path),
        tenant_id=args.tenant_id,
    )
    config = RenderConfig(
        resolution=resolution,
        fps=fps,
        frame_count=frame_count,
        output_codec=args.codec,
        bitrate_kbps=args.bitrate_kbps,
        timeout_sec=args.timeout_sec,
    )

    result = asyncio.run(
        orchestrator.render_enhancement(
            input_video="",
            config=config,
            output_path=args.output,
        )
    )

    if not result.success:
        print(f"RENDER FAILED: {result.errors}", file=sys.stderr)
        return 1

    print(f"RENDER OK: {result.output_file} ({result.frame_count} frames, {result.duration_sec:.2f}s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
