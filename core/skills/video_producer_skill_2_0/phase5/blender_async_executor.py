"""Blender Async Executor — Tier 3 Premium Rendering

Manages async Blender render jobs (background process).
Auto-downgrade to Tier 2 if Blender unavailable >48h.
Non-blocking: Returns job ID immediately; progress polled via job_id.

ADR-0741: 3-Tier Animation Architecture (Phase 5.4)
"""

import subprocess
import json
from pathlib import Path
from datetime import datetime, timedelta
import time
from typing import Optional, Dict
import os
import signal

try:
    import psutil
except ImportError:
    psutil = None


class BlenderAsyncExecutor:
    """Async Blender renderer (Premium Tier 3, non-blocking)"""

    def __init__(self, blender_timeout_minutes: int = 30):
        self.name = "blender_async_executor"
        self.version = "5.4.0"
        self.blender_timeout = blender_timeout_minutes * 60
        self.output_dir = Path("/home/shumway/projects/Corvin-Videos/blender_output")
        self.output_dir.mkdir(exist_ok=True, parents=True)

        self.active_jobs: Dict[str, dict] = {}  # job_id → {pid, start_time, animation_id, status}
        self.last_blender_success = datetime.now()
        self.consecutive_failures = 0

    def submit_async_job(self, animation_id: str, blender_file: Optional[Path] = None) -> dict:
        """Submit Blender render job (non-blocking)"""

        # Check if Blender unavailable >48h (auto-downgrade policy)
        if self._should_auto_downgrade():
            return {
                "success": False,
                "error": "Blender unavailable >48h, auto-downgrade to Tier 2",
                "auto_downgrade": True,
                "job_id": None
            }

        # Check if Blender is installed
        if not self._check_blender_available():
            self.consecutive_failures += 1
            return {
                "success": False,
                "error": "Blender not found (install Blender 3.6+)",
                "auto_downgrade": True,
                "job_id": None
            }

        try:
            # Create default Blender scene if no file provided
            if blender_file is None:
                blender_file = self._create_default_scene(animation_id)

            # Submit background process
            output_file = self.output_dir / f"{animation_id}_render_%04d.exr"

            # Blender command: render to EXR frames in background
            process = subprocess.Popen(
                [
                    "blender", "-b", str(blender_file),
                    "--engine", "CYCLES",
                    "--render-animation",
                    "-o", str(output_file),
                    "-s", "1", "-e", str(int(30 * 30)),  # 30 seconds @ 30fps
                    "-f", "0"  # Don't start immediately; just queue
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                preexec_fn=None  # Safe on Linux
            )

            job_id = f"blender_{animation_id}_{process.pid}_{int(time.time())}"

            self.active_jobs[job_id] = {
                "pid": process.pid,
                "start_time": datetime.now(),
                "animation_id": animation_id,
                "status": "rendering",
                "output_file": str(output_file),
                "process": process
            }

            self.last_blender_success = datetime.now()
            self.consecutive_failures = 0

            return {
                "success": True,
                "job_id": job_id,
                "pid": process.pid,
                "status": "queued",
                "animation_id": animation_id,
                "estimated_duration_seconds": 30
            }

        except Exception as e:
            self.consecutive_failures += 1
            return {
                "success": False,
                "error": str(e),
                "auto_downgrade": True
            }

    def get_job_status(self, job_id: str) -> dict:
        """Get Blender job status (progress polling)"""

        if job_id not in self.active_jobs:
            return {
                "job_id": job_id,
                "status": "not_found",
                "progress_percent": 0
            }

        job = self.active_jobs[job_id]
        process = job["process"]
        elapsed = datetime.now() - job["start_time"]

        # Check if process is still running
        if process.poll() is None:
            # Still rendering
            progress = min(95, int((elapsed.total_seconds() / 30) * 100))
            return {
                "job_id": job_id,
                "status": "rendering",
                "progress_percent": progress,
                "elapsed_seconds": int(elapsed.total_seconds()),
                "pid": job["pid"]
            }
        elif process.returncode == 0:
            # Render completed successfully
            output_path = self._convert_exr_to_mp4(job["output_file"], job["animation_id"])

            return {
                "job_id": job_id,
                "status": "completed",
                "progress_percent": 100,
                "elapsed_seconds": int(elapsed.total_seconds()),
                "output_path": str(output_path) if output_path else None
            }
        else:
            # Render failed
            return {
                "job_id": job_id,
                "status": "failed",
                "progress_percent": 0,
                "error": f"Blender exited with code {process.returncode}"
            }

    def cancel_job(self, job_id: str) -> dict:
        """Cancel a Blender render job"""

        if job_id not in self.active_jobs:
            return {
                "success": False,
                "error": "Job not found"
            }

        job = self.active_jobs[job_id]
        process = job["process"]

        try:
            # Kill process tree (Blender may have subprocesses)
            if psutil is not None:
                try:
                    parent = psutil.Process(process.pid)
                    for child in parent.children(recursive=True):
                        child.kill()
                    parent.kill()
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
            else:
                # Fallback: use os.kill with signal
                os.kill(process.pid, signal.SIGTERM)

            job["status"] = "cancelled"

            return {
                "success": True,
                "job_id": job_id,
                "status": "cancelled"
            }
        except Exception as e:
            return {
                "success": False,
                "error": str(e)
            }

    def wait_for_completion(self, job_id: str, timeout_seconds: int = 1800) -> dict:
        """Blocking wait for job completion (useful for E2E tests)"""

        start = time.time()

        while True:
            status = self.get_job_status(job_id)

            if status["status"] in ["completed", "failed"]:
                return status

            if time.time() - start > timeout_seconds:
                self.cancel_job(job_id)
                return {
                    "job_id": job_id,
                    "status": "timeout",
                    "error": f"Exceeded {timeout_seconds}s timeout"
                }

            time.sleep(1)

    def _check_blender_available(self) -> bool:
        """Check if Blender is installed"""
        try:
            result = subprocess.run(
                ["blender", "--version"],
                capture_output=True,
                timeout=5,
                text=True
            )
            return result.returncode == 0
        except Exception:
            return False

    def _should_auto_downgrade(self) -> bool:
        """Auto-downgrade if Blender unavailable >48h"""

        time_since_success = datetime.now() - self.last_blender_success

        return time_since_success > timedelta(hours=48) and self.consecutive_failures >= 3

    def _create_default_scene(self, animation_id: str) -> Path:
        """Create default Blender scene (cube + lighting)"""

        blender_file = self.output_dir / f"{animation_id}_default.blend"

        # TODO: Use Blender Python API to create scene programmatically
        # For now, return a placeholder path (assumes user has default.blend)

        default_template = self.output_dir / "default_template.blend"

        if not default_template.exists():
            print(f"[BLENDER] Creating default template at {default_template}")
            # Blender Python script to create minimal scene
            blend_script = """
import bpy
bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete()
bpy.ops.mesh.primitive_cube_add(size=2, location=(0, 0, 0))
bpy.ops.object.light_add(type='POINT', location=(5, 5, 5))
bpy.context.view_layer.objects.active = bpy.context.selected_objects[0]
bpy.ops.object.shade_smooth()
bpy.ops.wm.save_as_mainfile(filepath=r'""" + str(default_template) + """')
"""
            # Create via subprocess
            try:
                subprocess.run(
                    ["blender", "-b", "--python-expr", blend_script],
                    timeout=10,
                    capture_output=True
                )
            except Exception as e:
                print(f"[BLENDER] Failed to create template: {e}")

        return default_template if default_template.exists() else blender_file

    def _convert_exr_to_mp4(self, exr_pattern: str, animation_id: str) -> Optional[Path]:
        """Convert EXR frames to MP4 via FFmpeg"""

        output_mp4 = self.output_dir / f"{animation_id}_final.mp4"

        try:
            # FFmpeg: Convert EXR sequence to MP4
            subprocess.run([
                "ffmpeg", "-y",
                "-framerate", "30",
                "-i", exr_pattern,  # %04d in filename
                "-c:v", "libx264",
                "-pix_fmt", "yuv420p",
                "-crf", "23",
                str(output_mp4)
            ], capture_output=True, timeout=60, check=False)

            if output_mp4.exists():
                return output_mp4
            else:
                return None

        except Exception as e:
            print(f"[BLENDER] FFmpeg conversion error: {e}")
            return None

    def cleanup_old_jobs(self, max_age_minutes: int = 1440):
        """Clean up completed/failed jobs older than max_age"""

        cutoff_time = datetime.now() - timedelta(minutes=max_age_minutes)

        to_remove = [
            job_id for job_id, job in self.active_jobs.items()
            if job["start_time"] < cutoff_time and job["status"] in ["completed", "failed", "cancelled"]
        ]

        for job_id in to_remove:
            del self.active_jobs[job_id]

        return {"cleaned_jobs": len(to_remove)}

    def get_all_jobs(self) -> dict:
        """Get status of all active jobs"""

        jobs_summary = {
            "total": len(self.active_jobs),
            "rendering": 0,
            "completed": 0,
            "failed": 0,
            "jobs": []
        }

        for job_id, job in self.active_jobs.items():
            status = self.get_job_status(job_id)

            if status["status"] == "rendering":
                jobs_summary["rendering"] += 1
            elif status["status"] == "completed":
                jobs_summary["completed"] += 1
            elif status["status"] == "failed":
                jobs_summary["failed"] += 1

            jobs_summary["jobs"].append({
                "job_id": job_id,
                "animation_id": job["animation_id"],
                "status": status["status"],
                "progress": status.get("progress_percent", 0)
            })

        return jobs_summary
