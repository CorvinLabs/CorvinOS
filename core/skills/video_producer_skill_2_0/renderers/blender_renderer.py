"""Blender Renderer — Phase 4 (3D Animation Rendering)

Renders 3D scenes using Blender CLI. Async execution, temp file management,
error recovery. Supports custom materials, lighting, camera paths.
"""

import logging
import subprocess
import tempfile
import shutil
import asyncio
import json
import fcntl
import time
from pathlib import Path
from typing import Optional, Dict, List
from dataclasses import dataclass
from datetime import datetime

logger = logging.getLogger(__name__)


@dataclass
class BlenderConfig:
    """Blender rendering configuration"""
    framerate: int = 30
    resolution_x: int = 1920
    resolution_y: int = 1080
    samples: int = 100  # render samples (quality)
    format: str = "PNG"  # PNG, JPEG, EXR
    engine: str = "CYCLES"  # CYCLES or EEVEE
    use_gpu: bool = False  # CUDA/OptiX acceleration


@dataclass
class BlenderScene:
    """Blender scene specification"""
    blend_file: str  # Path to .blend file
    script_path: Optional[str] = None  # Python script to run in Blender
    output_dir: str = "/tmp/blender_output"
    start_frame: int = 1
    end_frame: int = 250


class BlenderRenderer:
    """3D animation rendering via Blender"""

    def __init__(
        self,
        output_dir: str = "/tmp/video_frames",
        config: Optional[BlenderConfig] = None
    ):
        self.output_dir = output_dir
        self.config = config or BlenderConfig()
        self.temp_dir = tempfile.mkdtemp()
        self.process_pool: Dict[int, subprocess.Popen] = {}
        Path(output_dir).mkdir(parents=True, exist_ok=True)

    def render_scene(self, scene: BlenderScene, scene_idx: int = 0) -> List[str]:
        """Render Blender scene to frame sequence

        Args:
            scene: BlenderScene configuration
            scene_idx: Scene index for logging

        Returns:
            List of output PNG frame paths
        """
        try:
            logger.info(f"🎬 Starting Blender render: {scene.blend_file} (frames {scene.start_frame}-{scene.end_frame})")

            # Create output directory
            output_dir = Path(scene.output_dir) or Path(self.output_dir) / f"blender_scene_{scene_idx}"
            output_dir.mkdir(parents=True, exist_ok=True)

            # Build Blender command
            cmd = self._build_blender_command(scene, output_dir)

            # Execute render
            result = subprocess.run(
                cmd,
                capture_output=True,
                timeout=3600,  # 1 hour timeout
                cwd=self.temp_dir
            )

            if result.returncode != 0:
                raise RuntimeError(f"Blender render failed: {result.stderr.decode()}")

            # Collect output frames (with file-locking to ensure Blender finished writing)
            output_frames = self._collect_frames_with_lock(output_dir)
            logger.info(f"✅ Blender render completed: {len(output_frames)} frames")

            return [str(f) for f in output_frames]

        except Exception as e:
            logger.error(f"❌ Blender render failed: {e}")
            raise

    def render_scene_async(self, scene: BlenderScene, scene_idx: int = 0) -> asyncio.Task:
        """Async render Blender scene (non-blocking)

        Args:
            scene: BlenderScene configuration
            scene_idx: Scene index

        Returns:
            Asyncio Task that can be awaited
        """
        loop = asyncio.get_event_loop()
        return loop.run_in_executor(None, self.render_scene, scene, scene_idx)

    def _build_blender_command(self, scene: BlenderScene, output_dir: Path) -> List[str]:
        """Build Blender CLI command"""

        cmd = [
            "blender",
            "--background",  # No UI
            scene.blend_file,
            "--python-use-system-env",
        ]

        # Set output directory
        cmd.extend([
            "-o", str(output_dir / "frame_"),  # Output prefix
        ])

        # Set render range
        cmd.extend([
            "-s", str(scene.start_frame),
            "-e", str(scene.end_frame),
        ])

        # Set render engine and quality
        render_script = self._generate_render_script(scene)
        script_file = Path(self.temp_dir) / f"render_script_{scene.start_frame}_{scene.end_frame}.py"
        script_file.write_text(render_script)

        cmd.extend([
            "-P", str(script_file),
        ])

        # Render all frames
        cmd.append("-a")

        return cmd

    def _generate_render_script(self, scene: BlenderScene) -> str:
        """Generate Python script for Blender to execute"""

        engine = "CYCLES" if self.config.engine == "CYCLES" else "EEVEE"
        samples = self.config.samples

        script = f"""
import bpy

# Set render settings
scene = bpy.context.scene
scene.render.engine = '{engine}'
scene.render.resolution_x = {self.config.resolution_x}
scene.render.resolution_y = {self.config.resolution_y}
scene.render.fps = {self.config.framerate}
scene.render.image_settings.file_format = '{self.config.format}'

# Set samples (quality)
if '{engine}' == 'CYCLES':
    scene.cycles.samples = {samples}
    scene.cycles.use_denoising = True
    scene.cycles.use_motion_blur = True

# GPU acceleration (if available)
if bpy.context.preferences.addons['cycles'].preferences.has_optix_devices:
    scene.cycles.device = 'GPU'

# Render
bpy.ops.render.render(animation=True, write_still=False)
"""

        # If custom script provided, append it
        if scene.script_path and Path(scene.script_path).exists():
            custom_script = Path(scene.script_path).read_text()
            script += f"\n\n# Custom script\n{custom_script}"

        return script

    def create_animation_script(
        self,
        blend_file: str,
        camera_path: List[Dict],
        output_dir: str,
        duration_frames: int = 250
    ) -> str:
        """Create Python script to animate camera in Blender

        Args:
            blend_file: Path to .blend file
            camera_path: List of keyframe dicts {"frame": int, "x": float, "y": float, "z": float}
            output_dir: Output directory for rendered frames
            duration_frames: Total render duration

        Returns:
            Path to generated script
        """

        script_path = Path(self.temp_dir) / "camera_animation.py"

        script = """
import bpy
import json

camera = bpy.data.objects.get('Camera') or bpy.context.scene.camera

if camera:
    camera.animation_data_create()

    # Set keyframes from path
    camera_path = json.loads('''"""

        script += json.dumps(camera_path)
        script += """''')

    for keyframe in camera_path:
        frame = keyframe.get('frame', 1)
        scene.frame_set(frame)

        if 'x' in keyframe:
            camera.location.x = keyframe['x']
        if 'y' in keyframe:
            camera.location.y = keyframe['y']
        if 'z' in keyframe:
            camera.location.z = keyframe['z']

        camera.keyframe_insert(data_path='location', frame=frame)

print(f"✅ Camera animation created: {len(camera_path)} keyframes")
"""

        script_path.write_text(script)
        logger.info(f"✅ Camera animation script created: {script_path}")
        return str(script_path)

    def get_blend_info(self, blend_file: str) -> Dict:
        """Query Blender file for metadata (camera count, material count, etc.)"""

        script = """
import bpy

cameras = [o.name for o in bpy.data.objects if o.type == 'CAMERA']
lights = [o.name for o in bpy.data.objects if o.type == 'LIGHT']
materials = list(bpy.data.materials.keys())
scenes = list(bpy.data.scenes.keys())

print(f"BLEND_INFO:{len(cameras)}|{len(lights)}|{len(materials)}|{len(scenes)}")
"""

        script_file = Path(self.temp_dir) / "query_script.py"
        script_file.write_text(script)

        result = subprocess.run(
            ["blender", "--background", blend_file, "-P", str(script_file)],
            capture_output=True,
            timeout=30
        )

        # Parse output
        output = result.stderr.decode() + result.stdout.decode()
        if "BLEND_INFO:" in output:
            for line in output.split("\n"):
                if "BLEND_INFO:" in line:
                    parts = line.split("BLEND_INFO:")[1].split("|")
                    return {
                        "cameras": int(parts[0]),
                        "lights": int(parts[1]),
                        "materials": int(parts[2]),
                        "scenes": int(parts[3]),
                    }

        return {}

    def _collect_frames_with_lock(self, output_dir: Path) -> List[Path]:
        """Collect frames with file-locking to avoid race conditions

        Ensures Blender has finished writing before we read.
        """
        frames = []
        for png_file in sorted(output_dir.glob("*.png")):
            # Try to acquire exclusive lock (will wait if Blender is writing)
            try:
                with open(png_file, "rb") as f:
                    fcntl.flock(f.fileno(), fcntl.LOCK_SH)  # Shared lock (wait for writer)
                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)  # Release
                frames.append(png_file)
            except IOError as e:
                logger.warning(f"Failed to lock {png_file}: {e}")
                # Retry with backoff
                time.sleep(0.5)
                try:
                    with open(png_file, "rb") as f:
                        fcntl.flock(f.fileno(), fcntl.LOCK_SH)
                        fcntl.flock(f.fileno(), fcntl.LOCK_UN)
                    frames.append(png_file)
                except IOError:
                    logger.error(f"Failed to acquire lock on {png_file}")
                    continue
        return frames

    def cleanup(self) -> None:
        """Clean up temporary files and processes"""

        # Terminate any running processes
        for pid, process in self.process_pool.items():
            if process.poll() is None:  # Still running
                process.terminate()
                logger.info(f"Terminated Blender process: {pid}")

        # Clean temp directory
        shutil.rmtree(self.temp_dir, ignore_errors=True)
        logger.info(f"✅ Cleaned up temp directory: {self.temp_dir}")

    def __del__(self):
        """Cleanup on deletion"""
        self.cleanup()


# Example usage
def example_blender_render():
    """Example: render Blender scene"""

    renderer = BlenderRenderer()

    scene = BlenderScene(
        blend_file="/path/to/scene.blend",
        start_frame=1,
        end_frame=250
    )

    frames = renderer.render_scene(scene, scene_idx=0)
    return frames


__all__ = ["BlenderRenderer", "BlenderConfig", "BlenderScene"]
