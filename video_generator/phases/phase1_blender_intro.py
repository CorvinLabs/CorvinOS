"""
Phase 1: Blender 3D Logo Animation (with CGI Fallback)
Generates 5-second animated CorvinOS logo intro
"""

import os
import subprocess
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, Any
import logging

from base_utils import BasePhase, PhaseOutput, FFmpegHelper

logger = logging.getLogger(__name__)


class BlenderIntroPhase(BasePhase):
    """Generate 3D logo animation using Blender"""

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config, "Phase1_Blender")
        self.phase_config = config.get("phases", {}).get("phase1_blender", {})
        self.ffmpeg = FFmpegHelper(config)
        self.blender_available = self.check_dependency("blender")

    def create_blender_script(self) -> str:
        """Create Python script for Blender"""

        script = '''#!/usr/bin/env python3
import bpy
import math
import os

# Set up scene
bpy.context.scene.render.resolution_x = 1920
bpy.context.scene.render.resolution_y = 1080
bpy.context.scene.render.fps = 25
bpy.context.scene.render.image_settings.file_format = 'PNG'
bpy.context.scene.render.image_settings.color_mode = 'RGBA'

# Set up lighting
bpy.ops.object.light_add(type='SUN', location=(5, 5, 10))
sun = bpy.context.active_object
sun.data.energy = 2.0

# Add camera
bpy.ops.object.camera_add(location=(0, 0, 8))
camera = bpy.context.active_object
bpy.context.scene.camera = camera

# Create material
mat = bpy.data.materials.new("CorvinOS_Gold")
mat.use_nodes = True
bsdf = mat.node_tree.nodes["Principled BSDF"]
bsdf.inputs['Base Color'].default_value = (1.0, 0.84, 0.0, 1.0)  # Gold
bsdf.inputs['Metallic'].default_value = 0.8
bsdf.inputs['Roughness'].default_value = 0.2
bsdf.inputs['Emission'].default_value = (1.0, 0.84, 0.0, 1.0)
bsdf.inputs['Emission Strength'].default_value = 0.3

# Create torus (ring)
bpy.ops.mesh.primitive_torus_add(major_radius=2, minor_radius=0.3, location=(0, 0, 0))
torus = bpy.context.active_object
torus.data.materials.append(mat)

# Animate rotation
for frame in range(0, 126):  # 126 frames = 5.04 seconds at 25fps
    bpy.context.scene.frame_set(frame)
    torus.rotation_z = (frame / 125) * math.pi * 2
    torus.keyframe_insert(data_path="rotation_euler", index=2)

# Add text
bpy.ops.object.text_add(location=(0, -2.5, 0))
text_obj = bpy.context.active_object
text_obj.data.body = "CORVINOS"
text_obj.scale = (0.5, 0.5, 0.5)
text_obj.data.align_x = 'CENTER'
text_mat = bpy.data.materials.new("Text_Material")
text_mat.use_nodes = True
text_bsdf = text_mat.node_tree.nodes["Principled BSDF"]
text_bsdf.inputs['Base Color'].default_value = (1.0, 1.0, 1.0, 1.0)
text_obj.data.materials.append(text_mat)

# Render animation
output_dir = "%(output_frames)s"
os.makedirs(output_dir, exist_ok=True)
bpy.context.scene.render.filepath = output_dir

bpy.ops.render.render(animation=True, write_still=False)

print("Blender render complete!")
'''

        output_dir = self.phase_config.get("output_frames", "./output/frames/phase1_blender_%%04d.png")
        return script % {"output_frames": output_dir}

    def create_cgi_fallback(self) -> bool:
        """Create fallback using pure FFmpeg with color gradients"""

        self.log("Using CGI fallback for Phase 1 (Blender unavailable)")

        # Create gradient frames with animated circle
        frames_dir = self.ensure_directory(self.phase_config.get("output_frames", "./output/frames"))
        frame_count = self.phase_config.get("frame_count", 125)

        # FFmpeg filter to create animated gradient + animated circle
        duration = self.phase_config.get("duration_seconds", 5)

        filter_complex = (
            "color=c=#0f1320:s=1920x1080:d=%(duration)d,"
            "drawtext=text='CORVINOS':fontfile=/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf:"
            "fontsize=120:fontcolor=FFD700:x=(w-text_w)/2:y=h/2:alpha='if(lt(t\\,3)\\,t/3\\,1)',"
            "drawtext=text='Premium Video Engine':fontfile=/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf:"
            "fontsize=60:fontcolor=00D9FF:x=(w-text_w)/2:y=h/2+150:alpha='if(lt(t\\,3)\\,t/3\\,1)'"
        ) % {"duration": duration}

        cmd = [
            "ffmpeg",
            "-f", "lavfi",
            "-i", filter_complex,
            "-vf",
            "fps=25",
            "-pix_fmt", "yuv420p",
            "-y",
            self.phase_config.get("output_video", "./output/phase1_blender_intro.mp4")
        ]

        success, output = self.run_command(cmd)
        return success

    def execute(self) -> PhaseOutput:
        """Execute Phase 1: Blender Logo"""

        start_time = datetime.now()

        try:
            duration = self.phase_config.get("duration_seconds", 5)
            frame_count = self.phase_config.get("frame_count", 125)
            output_file = self.phase_config.get("output_video", "./output/phase1_blender_intro.mp4")

            self.log(f"Starting Phase 1: Blender Logo Animation ({duration}s, {frame_count} frames)")

            # Check if Blender is available
            use_fallback = self.phase_config.get("fallback", True)

            if self.blender_available and not use_fallback:
                self.log("Blender found, using native rendering...")

                # Create Blender script
                script_path = "/tmp/corvinos_blender_render.py"
                with open(script_path, 'w') as f:
                    f.write(self.create_blender_script())

                # Run Blender
                cmd = ["blender", "--background", "--python", script_path]
                success, output = self.run_command(cmd)

                if not success:
                    self.log("Blender rendering failed, falling back to CGI...", "warning")
                    use_fallback = True

            # Use fallback if needed
            if use_fallback:
                success = self.create_cgi_fallback()
                if not success:
                    return PhaseOutput(
                        phase_name="Phase 1: Blender",
                        video_file="",
                        frame_count=0,
                        duration_seconds=0,
                        status="error",
                        error_message="Both Blender and fallback rendering failed"
                    )

            # Verify output
            output_video = self.phase_config.get("output_video", "./output/phase1_blender_intro.mp4")
            if not os.path.exists(output_video):
                return PhaseOutput(
                    phase_name="Phase 1: Blender",
                    video_file="",
                    frame_count=0,
                    duration_seconds=0,
                    status="error",
                    error_message=f"Output file not created: {output_video}"
                )

            duration_actual = (datetime.now() - start_time).total_seconds()

            self.log(f"Phase 1 complete: {output_video} ({duration_actual:.1f}s)")

            return PhaseOutput(
                phase_name="Phase 1: Blender",
                video_file=output_video,
                frame_count=frame_count,
                duration_seconds=duration,
                status="success",
                metadata={
                    "method": "fallback_cgi" if use_fallback else "blender",
                    "duration": duration_actual
                }
            )

        except Exception as e:
            self.log(f"Phase 1 execution failed: {str(e)}", "error")
            return PhaseOutput(
                phase_name="Phase 1: Blender",
                video_file="",
                frame_count=0,
                duration_seconds=0,
                status="error",
                error_message=str(e)
            )
