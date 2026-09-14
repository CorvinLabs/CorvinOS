"""Manim Animator Worker — Tier 2 (Rich Math Animation)

Generates animated diagrams using Manim (Mathematical Animation Engine).
Used for didactic explanation of concepts (Learning Loop, ADR Graph, etc.)

ADR-0741: 3-Tier Animation Architecture (Tier 2 Implementation)
ADR-0740: Director Mode Advanced (Weeks 1–3)
"""

import subprocess
import time
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional
from datetime import datetime


@dataclass
class AnimationRequest:
    """Request to generate animated diagram"""
    animation_id: str              # "learning-loop"
    concept_id: str                # "learning-loop"
    didactic_level: str            # "beginner", "technical"
    duration_seconds: int          # 30
    assets: List[str]              # ["learning-loop.svg"]
    output_format: str             # "mp4"
    tier: int = 2                  # Tier 2 (Manim)
    preferred_tier: int = 2        # Default to Manim

    def __post_init__(self):
        if self.didactic_level not in ["beginner", "technical"]:
            raise ValueError(f"Invalid didactic_level: {self.didactic_level}")
        if self.output_format not in ["mp4", "webm", "gif"]:
            raise ValueError(f"Invalid output_format: {self.output_format}")


@dataclass
class AnimationResult:
    """Result of animation generation"""
    animation_id: str
    output_path: Optional[Path] = None
    duration_seconds: float = 0.0
    render_time_ms: int = 0
    output_hash: str = ""
    success: bool = False
    error: Optional[str] = None
    tier_used: int = 2


class ManimAnimatorWorker:
    """Manim-based animation generator (Tier 2)

    Renders mathematical animations using Manim framework.
    Features:
    - Scene spec loading from asset library
    - Manim scene script generation
    - Subprocess rendering with timeout (60s hard limit)
    - Output verification (duration + hash)
    - Error handling + fallback support
    """

    def __init__(self, timeout_seconds: int = 60, cache_enabled: bool = True):
        """Initialize ManimAnimatorWorker

        Args:
            timeout_seconds: Hard timeout for manim subprocess (default: 60)
            cache_enabled: Cache rendered animations by ID + hash
        """
        self.name = "manim_animator_worker"
        self.version = "5.1.0"
        self.timeout = timeout_seconds
        self.cache_enabled = cache_enabled

        # Setup directories
        self.cache_dir = Path("/tmp/manim_cache")
        self.output_dir = Path("/home/shumway/projects/Corvin-Videos/manim_output")
        self.scenes_dir = Path("/home/shumway/projects/Corvin-Videos/scenes")

        for d in [self.cache_dir, self.output_dir, self.scenes_dir]:
            d.mkdir(parents=True, exist_ok=True)

    def execute(self, request: AnimationRequest) -> AnimationResult:
        """Execute animation generation

        Args:
            request: AnimationRequest with animation specs

        Returns:
            AnimationResult with success flag, output path, hash, render time
        """
        start_time = time.time()

        try:
            # Step 1: Check cache
            if self.cache_enabled:
                cached = self._get_cached(request.animation_id)
                if cached:
                    render_time_ms = int((time.time() - start_time) * 1000)
                    return AnimationResult(
                        animation_id=request.animation_id,
                        output_path=cached["path"],
                        duration_seconds=cached["duration"],
                        render_time_ms=render_time_ms,
                        output_hash=cached["hash"],
                        success=True,
                        tier_used=2
                    )

            # Step 2: Load scene spec
            scene_spec = self._load_scene_spec(request.animation_id)
            if not scene_spec:
                return AnimationResult(
                    animation_id=request.animation_id,
                    success=False,
                    error=f"Scene spec not found: {request.animation_id}",
                    render_time_ms=int((time.time() - start_time) * 1000)
                )

            # Step 3: Generate Manim scene script
            scene_script = self._generate_scene_script(
                animation_id=request.animation_id,
                scene_spec=scene_spec,
                didactic_level=request.didactic_level
            )

            # Step 4: Render with manim (subprocess)
            output_path = self._render_manim(
                animation_id=request.animation_id,
                scene_script=scene_script,
                duration_seconds=request.duration_seconds
            )

            if not output_path:
                render_time_ms = int((time.time() - start_time) * 1000)
                return AnimationResult(
                    animation_id=request.animation_id,
                    success=False,
                    error="Manim render failed (timeout or error)",
                    render_time_ms=render_time_ms
                )

            # Step 5: Verify output
            duration = self._get_duration(output_path)
            output_hash = self._get_hash(output_path)
            render_time_ms = int((time.time() - start_time) * 1000)

            # Cache result
            if self.cache_enabled:
                self._cache_result(
                    animation_id=request.animation_id,
                    output_path=output_path,
                    output_hash=output_hash,
                    duration=duration,
                    render_time_ms=render_time_ms
                )

            return AnimationResult(
                animation_id=request.animation_id,
                output_path=output_path,
                duration_seconds=duration,
                render_time_ms=render_time_ms,
                output_hash=output_hash,
                success=True,
                tier_used=2
            )

        except Exception as e:
            render_time_ms = int((time.time() - start_time) * 1000)
            return AnimationResult(
                animation_id=request.animation_id,
                success=False,
                error=f"Exception: {str(e)}",
                render_time_ms=render_time_ms
            )

    def _load_scene_spec(self, animation_id: str) -> Optional[dict]:
        """Load scene specification from asset library

        Returns scene spec dict or None if not found
        """
        # Hardcoded specs for Phase 5.1
        specs = {
            "learning-loop": {
                "title": "Learning Loop",
                "description": "5-step feedback loop animation",
                "elements": ["feedback-box", "optimize-box", "measure-box"],
                "flow": "circular"
            },
            "maestro-workers": {
                "title": "Maestro with 5 Workers",
                "description": "Hierarchical diagram of Maestro + Workers",
                "elements": ["maestro-center", "worker-1", "worker-2", "worker-3", "worker-4", "worker-5"],
                "flow": "hierarchical"
            },
            "audit-chain": {
                "title": "Audit Chain",
                "description": "Hash-chained audit events",
                "elements": ["event-1", "event-2", "event-3", "chain-link"],
                "flow": "linear"
            }
        }
        return specs.get(animation_id)

    def _generate_scene_script(self, animation_id: str, scene_spec: dict, didactic_level: str) -> str:
        """Generate Manim Python scene script

        Returns Python code as a string that defines a Manim scene
        """

        if animation_id == "learning-loop":
            return self._scene_learning_loop(didactic_level)
        elif animation_id == "maestro-workers":
            return self._scene_maestro_workers(didactic_level)
        elif animation_id == "audit-chain":
            return self._scene_audit_chain(didactic_level)
        else:
            return self._scene_generic(animation_id, scene_spec, didactic_level)

    def _scene_learning_loop(self, didactic_level: str) -> str:
        """Generate Learning Loop scene (Manim code)"""
        return '''
from manim import *

class LearningLoopScene(Scene):
    def construct(self):
        # Title
        title = Text("Learning Loop", font_size=60, color=CYAN)
        self.play(Write(title))
        self.wait(1)

        # Create 5-step cycle (Measure → Feedback → Analyze → Optimize → Deploy)
        step_labels = ["Measure", "Feedback", "Analyze", "Optimize", "Deploy"]
        radius = 2.5
        boxes = []

        for i, label in enumerate(step_labels):
            angle = i * 2 * 3.14159 / 5 - 3.14159 / 2
            x = radius * __import__("math").cos(angle)
            y = radius * __import__("math").sin(angle)

            box = Rectangle(width=1.5, height=0.8, color=BLUE)
            box.move_to([x, y, 0])
            text = Text(label, font_size=16)
            text.move_to([x, y, 0])

            boxes.append(VGroup(box, text))

        # Animate boxes appearing
        self.play(*[GrowFromCenter(b) for b in boxes], run_time=2)
        self.wait(1)

        # Draw arrows connecting boxes (cycle)
        for i in range(len(boxes)):
            j = (i + 1) % len(boxes)
            # Get approximate endpoints
            start_angle = i * 2 * 3.14159 / 5 - 3.14159 / 2
            end_angle = j * 2 * 3.14159 / 5 - 3.14159 / 2

            start_x = radius * __import__("math").cos(start_angle)
            start_y = radius * __import__("math").sin(start_angle)
            end_x = radius * __import__("math").cos(end_angle)
            end_y = radius * __import__("math").sin(end_angle)

            arrow = Arrow([start_x, start_y, 0], [end_x, end_y, 0], color=YELLOW, buff=0.7)
            self.play(GrowArrow(arrow), run_time=0.5)

        self.wait(3)

        # Highlight each step with description
        descriptions = [
            "Collect data",
            "Receive feedback",
            "Determine issues",
            "Adjust strategy",
            "Try again"
        ]

        for i, (box, desc) in enumerate(zip(boxes, descriptions)):
            desc_text = Text(desc, font_size=14, color=GREEN)
            desc_text.move_to([0, -3.5, 0])
            self.play(Write(desc_text), run_time=0.5)
            self.wait(1)
            self.play(Unwrite(desc_text), run_time=0.3)

        self.wait(2)
        self.play(FadeOut(title), *[FadeOut(b) for b in boxes], run_time=1)
        self.wait(1)
'''

    def _scene_maestro_workers(self, didactic_level: str) -> str:
        """Generate Maestro + Workers scene (Manim code)"""
        return '''
from manim import *

class MaestroWorkersScene(Scene):
    def construct(self):
        # Title
        title = Text("Maestro & Workers", font_size=60, color=PURPLE)
        self.play(Write(title))
        self.wait(1)

        # Maestro (center)
        maestro = Circle(radius=0.5, color=RED)
        maestro_text = Text("M", font_size=24, color=WHITE)
        maestro_text.move_to(maestro.get_center())
        maestro_group = VGroup(maestro, maestro_text)

        # 5 Workers (arranged in circle)
        workers = []
        for i in range(5):
            angle = i * 2 * 3.14159 / 5
            x = 2.5 * __import__("math").cos(angle)
            y = 2.5 * __import__("math").sin(angle)

            worker = Circle(radius=0.4, color=BLUE)
            worker.move_to([x, y, 0])
            worker_text = Text(f"W{i+1}", font_size=14, color=WHITE)
            worker_text.move_to([x, y, 0])
            workers.append(VGroup(worker, worker_text))

        # Animate maestro
        self.play(GrowFromCenter(maestro_group), run_time=1)
        self.wait(1)

        # Animate workers appearing
        self.play(*[GrowFromCenter(w) for w in workers], run_time=2)
        self.wait(1)

        # Draw connections (Maestro → Workers)
        for worker in workers:
            connection = Line(maestro.get_center(), worker[0].get_center(), color=YELLOW)
            self.play(Create(connection), run_time=0.3)

        self.wait(2)

        # Highlight communication (pulse effect)
        for _ in range(2):
            for worker in workers:
                pulse = Circle(radius=0.4, color=GREEN)
                pulse.move_to(worker[0].get_center())
                self.play(ScaleInPlace(pulse, 1.5), run_time=0.3)
                self.play(FadeOut(pulse), run_time=0.2)

        self.wait(2)
'''

    def _scene_audit_chain(self, didactic_level: str) -> str:
        """Generate Audit Chain scene (Manim code)"""
        return '''
from manim import *

class AuditChainScene(Scene):
    def construct(self):
        # Title
        title = Text("Audit Chain (Hash-Linked)", font_size=55, color=CYAN)
        self.play(Write(title))
        self.wait(1)

        # Create 4 event blocks
        events = []
        for i in range(4):
            x = -3 + i * 2.2
            event_box = Rectangle(width=1.8, height=1.0, color=BLUE)
            event_box.move_to([x, 0, 0])

            event_text = Text(f"Event {i+1}", font_size=12)
            event_text.move_to([x, 0.3, 0])

            hash_text = Text(f"h{i+1}", font_size=10, color=GREEN)
            hash_text.move_to([x, -0.3, 0])

            events.append(VGroup(event_box, event_text, hash_text))

        # Animate events
        self.play(*[GrowFromCenter(e) for e in events], run_time=2)
        self.wait(1)

        # Draw hash chain (links)
        for i in range(len(events) - 1):
            link = Line(
                events[i].get_right(),
                events[i+1].get_left(),
                color=YELLOW,
                stroke_width=3
            )
            self.play(Create(link), run_time=0.5)

        self.wait(1)

        # Highlight chain integrity
        integrity_text = Text("Chain Verified ✓", font_size=24, color=GREEN)
        integrity_text.move_to([0, -2.5, 0])
        self.play(Write(integrity_text), run_time=1)
        self.wait(2)
'''

    def _scene_generic(self, animation_id: str, scene_spec: dict, didactic_level: str) -> str:
        """Generate generic scene (placeholder)"""
        return f'''
from manim import *

class GenericScene(Scene):
    def construct(self):
        title = Text("{animation_id.replace('-', ' ').title()}", font_size=60, color=CYAN)
        self.play(Write(title))
        self.wait(3)
'''

    def _render_manim(self, animation_id: str, scene_script: str, duration_seconds: int) -> Optional[Path]:
        """Call manim subprocess to render

        Args:
            animation_id: Unique animation ID
            scene_script: Manim Python scene code
            duration_seconds: Target duration (for reference)

        Returns:
            Path to generated MP4 or None on failure
        """

        # Write scene script to temp file
        script_path = self.cache_dir / f"{animation_id}_scene.py"
        try:
            script_path.write_text(scene_script)
        except Exception as e:
            print(f"Failed to write scene script: {e}")
            return None

        # Output file
        output_file = self.output_dir / f"{animation_id}.mp4"

        try:
            # Determine scene class name (extract from script)
            scene_class_name = "LearningLoopScene"  # Default
            if "MaestroWorkersScene" in scene_script:
                scene_class_name = "MaestroWorkersScene"
            elif "AuditChainScene" in scene_script:
                scene_class_name = "AuditChainScene"
            elif "GenericScene" in scene_script:
                scene_class_name = "GenericScene"

            # Call manim via subprocess
            # -ql = low quality (faster rendering, good enough for demo)
            # --disable_caching = ensure reproducible output
            # --format=mp4 = output format
            cmd = [
                "manim",
                "-ql",
                "--disable_caching",
                "--format=mp4",
                f"--output_file={output_file.stem}",
                "-o", output_file.name,
                str(script_path),
                scene_class_name
            ]

            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                cwd=str(self.output_dir)
            )

            # Check if manim succeeded
            if result.returncode == 0:
                # Look for actual output file in manim's typical output structure
                # Manim creates: media/videos/1080p60/<scene_class>.mp4
                media_dir = self.output_dir / "media" / "videos" / "1080p60"
                if media_dir.exists():
                    manim_output = media_dir / f"{scene_class_name}.mp4"
                    if manim_output.exists():
                        # Copy to our output directory
                        import shutil
                        final_output = self.output_dir / f"{animation_id}.mp4"
                        shutil.copy(manim_output, final_output)
                        return final_output

                # If file exists in output_dir directly
                if output_file.exists():
                    return output_file
            else:
                print(f"Manim error: {result.stderr[:500]}")
                return None

        except subprocess.TimeoutExpired:
            print(f"Manim timeout ({self.timeout}s) for {animation_id}")
            return None
        except FileNotFoundError:
            print("manim command not found. Install with: pip install manim")
            return None
        except Exception as e:
            print(f"Manim subprocess error: {e}")
            return None

        return None

    def _get_duration(self, path: Path) -> float:
        """Get video duration via ffprobe"""
        try:
            result = subprocess.run(
                [
                    "ffprobe", "-v", "error",
                    "-show_entries", "format=duration",
                    "-of", "default=noprint_wrappers=1:nokey=1",
                    str(path)
                ],
                capture_output=True,
                text=True,
                timeout=5
            )
            if result.stdout.strip():
                return float(result.stdout.strip())
        except Exception as e:
            print(f"ffprobe error: {e}")

        return 0.0

    def _get_hash(self, path: Path) -> str:
        """Get SHA256 hash of file"""
        try:
            sha = hashlib.sha256()
            with open(path, "rb") as f:
                sha.update(f.read())
            return sha.hexdigest()
        except Exception as e:
            print(f"Hash error: {e}")
            return ""

    def _get_cached(self, animation_id: str) -> Optional[dict]:
        """Get cached animation result"""
        cache_file = self.cache_dir / f"{animation_id}_cache.json"
        if cache_file.exists():
            try:
                with open(cache_file) as f:
                    return json.load(f)
            except:
                pass
        return None

    def _cache_result(self, animation_id: str, output_path: Path, output_hash: str,
                     duration: float, render_time_ms: int):
        """Cache animation result"""
        cache_file = self.cache_dir / f"{animation_id}_cache.json"
        cache_data = {
            "animation_id": animation_id,
            "path": str(output_path),
            "hash": output_hash,
            "duration": duration,
            "render_time_ms": render_time_ms,
            "cached_at": datetime.now().isoformat()
        }
        try:
            with open(cache_file, "w") as f:
                json.dump(cache_data, f)
        except Exception as e:
            print(f"Cache write error: {e}")
