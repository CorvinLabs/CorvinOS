"""
Phase 2: Manim Architecture Diagrams
Generates animated architecture and four-pillars diagrams
"""

import os
from pathlib import Path
from datetime import datetime
from typing import Dict, Any
import logging

from base_utils import BasePhase, PhaseOutput

logger = logging.getLogger(__name__)


class ManimDiagramsPhase(BasePhase):
    """Generate Manim animations for architecture diagrams"""

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config, "Phase2_Manim")
        self.phase_config = config.get("phases", {}).get("phase2_manim", {})
        self.manim_available = self.check_dependency("manim")

    def create_four_pillars_script(self) -> str:
        """Create Manim script for Four Pillars"""

        script = '''from manim import *

class FourPillars(Scene):
    def construct(self):
        # Set background
        self.camera.background_color = "#0f1320"

        # Center CorvinOS circle
        center_circle = Circle(radius=0.5, color="#FFD700", fill_opacity=0.3)
        center_text = Text("CORVINOS", font_size=20, color="#FFD700")

        self.play(FadeIn(center_circle), FadeIn(center_text), run_time=1)

        # Define pillars
        pillars = [
            {"emoji": "🎙️", "label": "Voice", "pos": np.array([-2, 1, 0]), "color": "#00D9FF"},
            {"emoji": "🔒", "label": "Encryption", "pos": np.array([2, 1, 0]), "color": "#FF006E"},
            {"emoji": "♻️", "label": "Deduplication", "pos": np.array([-2, -1, 0]), "color": "#00F077"},
            {"emoji": "🔗", "label": "A2A Bridge", "pos": np.array([2, -1, 0]), "color": "#FF6B35"},
        ]

        for pillar in pillars:
            # Emoji icon
            icon = Text(pillar["emoji"], font_size=80, color=pillar["color"])
            icon.move_to(pillar["pos"])

            # Label
            label_text = Text(pillar["label"], font_size=24, color=pillar["color"])
            label_text.next_to(icon, DOWN, buff=0.3)

            # Connection line
            line = Line(center_circle.get_edge_center(pillar["pos"] / np.linalg.norm(pillar["pos"])),
                       icon.get_center())

            self.play(
                FadeIn(icon),
                FadeIn(label_text),
                ShowCreation(line),
                run_time=0.8
            )

            # Pulse animation
            self.play(icon.animate.scale(1.2), run_time=0.3)
            self.play(icon.animate.scale(1.0), run_time=0.3)

        self.wait(2)
'''
        return script

    def create_architecture_script(self) -> str:
        """Create Manim script for Architecture stack"""

        script = '''from manim import *

class ArchitectureStack(Scene):
    def construct(self):
        # Set background
        self.camera.background_color = "#0f1320"

        # Layer definitions
        layers = [
            {"label": "API Gateway", "color": "#FF006E", "order": 0},
            {"label": "Orchestration Layer", "color": "#FF6B35", "order": 1},
            {"label": "Worker Engine", "color": "#00D9FF", "order": 2},
            {"label": "Encryption & Security", "color": "#00F077", "order": 3},
            {"label": "Audit Chain", "color": "#FFD700", "order": 4},
        ]

        stack = VGroup()

        for layer in layers:
            # Layer rectangle
            rect = Rectangle(width=8, height=0.8, color=layer["color"], fill_opacity=0.7)

            # Layer text
            text = Text(layer["label"], font_size=20, color="#0f1320")
            text.move_to(rect.get_center())

            group = VGroup(rect, text)
            group.shift(UP * (4 - layer["order"] * 1.2))

            stack.add(group)

        # Animate layers
        for group in stack:
            self.play(FadeIn(group), run_time=0.6)

        # Add data flow particles
        for _ in range(5):
            particle = Circle(radius=0.15, color="#FFD700", fill_opacity=1.0)
            particle.move_to([0, 4, 0])
            self.play(FadeIn(particle), run_time=0.3)
            self.play(particle.animate.shift(DOWN * 6), run_time=2)
            self.play(FadeOut(particle), run_time=0.3)

        self.wait(1)
'''
        return script

    def create_static_fallback(self) -> bool:
        """Create static image fallback using FFmpeg"""

        self.log("Using static fallback for Manim diagrams")

        # Create Four Pillars static image
        four_pillars_output = self.phase_config.get("output_four_pillars", "./output/phase2_four_pillars.mp4")
        cmd_four_pillars = [
            "ffmpeg",
            "-f", "lavfi",
            "-i", "color=c=#0f1320:s=1920x1080:d=6",
            "-vf",
            "drawtext=text='Four Pillars of CorvinOS':fontfile=/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf:"
            "fontsize=80:fontcolor=FFD700:x=(w-text_w)/2:y=100,"
            "drawtext=text='Voice Encryption Deduplication A2A Bridge':fontfile=/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf:"
            "fontsize=40:fontcolor=00D9FF:x=(w-text_w)/2:y=400",
            "-pix_fmt", "yuv420p",
            "-y",
            four_pillars_output
        ]

        success, _ = self.run_command(cmd_four_pillars)
        if not success:
            return False

        # Create Architecture static image
        architecture_output = self.phase_config.get("output_architecture", "./output/phase2_architecture.mp4")
        cmd_architecture = [
            "ffmpeg",
            "-f", "lavfi",
            "-i", "color=c=#0f1320:s=1920x1080:d=6",
            "-vf",
            "drawtext=text='Architecture Stack':fontfile=/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf:"
            "fontsize=80:fontcolor=FFD700:x=(w-text_w)/2:y=100,"
            "drawtext=text='API Gateway | Orchestration | Worker | Encryption | Audit':fontfile=/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf:"
            "fontsize=40:fontcolor=00D9FF:x=(w-text_w)/2:y=400",
            "-pix_fmt", "yuv420p",
            "-y",
            architecture_output
        ]

        success, _ = self.run_command(cmd_architecture)
        return success

    def execute(self) -> PhaseOutput:
        """Execute Phase 2: Manim Diagrams"""

        start_time = datetime.now()

        try:
            self.log("Starting Phase 2: Manim Architecture Diagrams")

            if self.manim_available:
                self.log("Manim found, attempting native rendering...")
                # Would execute manim scripts here
                # For now, fall back to static version
                success = self.create_static_fallback()
            else:
                self.log("Manim not found, using static fallback", "warning")
                success = self.create_static_fallback()

            if not success:
                return PhaseOutput(
                    phase_name="Phase 2: Manim",
                    video_file="",
                    frame_count=0,
                    duration_seconds=0,
                    status="error",
                    error_message="Manim diagram generation failed"
                )

            duration_actual = (datetime.now() - start_time).total_seconds()

            # Verify outputs
            four_pillars = self.phase_config.get("output_four_pillars", "./output/phase2_four_pillars.mp4")
            architecture = self.phase_config.get("output_architecture", "./output/phase2_architecture.mp4")

            if not (os.path.exists(four_pillars) and os.path.exists(architecture)):
                return PhaseOutput(
                    phase_name="Phase 2: Manim",
                    video_file="",
                    frame_count=0,
                    duration_seconds=0,
                    status="error",
                    error_message="Output files not created"
                )

            self.log(f"Phase 2 complete ({duration_actual:.1f}s)")

            return PhaseOutput(
                phase_name="Phase 2: Manim",
                video_file=f"{four_pillars},{architecture}",
                frame_count=300,
                duration_seconds=12,
                status="success",
                metadata={"duration": duration_actual}
            )

        except Exception as e:
            self.log(f"Phase 2 execution failed: {str(e)}", "error")
            return PhaseOutput(
                phase_name="Phase 2: Manim",
                video_file="",
                frame_count=0,
                duration_seconds=0,
                status="error",
                error_message=str(e)
            )
