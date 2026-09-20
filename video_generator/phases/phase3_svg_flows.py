"""
Phase 3: SVG Data Flow Graphics
Generates animated data flow visualizations for Healthcare, Finance, Government
"""

import os
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, List
import logging

from base_utils import BasePhase, PhaseOutput

logger = logging.getLogger(__name__)


class SVGFlowsPhase(BasePhase):
    """Generate animated SVG data flow graphics"""

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config, "Phase3_SVG")
        self.phase_config = config.get("phases", {}).get("phase3_svg", {})

    def create_flow_animation(self, flow_name: str, flow_type: str) -> bool:
        """Create animated flow using FFmpeg filters"""

        # Define flow-specific colors and labels
        flows = {
            "healthcare": {
                "title": "Healthcare Data Flow",
                "steps": ["Patient Data", "Encryption", "HIPAA Compliance", "Storage"],
                "colors": ["#FF006E", "#00D9FF", "#00F077", "#FFD700"],
                "icon": "⚕️"
            },
            "finance": {
                "title": "Financial Transaction Flow",
                "steps": ["Transaction", "Security Check", "Deduplication", "Settlement"],
                "colors": ["#FF6B35", "#FF006E", "#00D9FF", "#00F077"],
                "icon": "💳"
            },
            "government": {
                "title": "Government Data Sovereignty",
                "steps": ["Data Collection", "Sovereign Cloud", "Audit Trail", "Access Control"],
                "colors": ["#FFD700", "#00F077", "#FF006E", "#00D9FF"],
                "icon": "🏛️"
            }
        }

        if flow_name not in flows:
            self.log(f"Unknown flow: {flow_name}", "error")
            return False

        flow = flows[flow_name]
        duration = 8  # 8 seconds per flow

        # Build FFmpeg filter string with animated text
        filter_str = (
            f"color=c=#0f1320:s=1920x1080:d={duration},"
            f"drawtext=text='{flow['icon']} {flow['title']}':fontfile=/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf:"
            f"fontsize=72:fontcolor=FFD700:x=(w-text_w)/2:y=100,"
            f"drawtext=text='{' → '.join(flow['steps'])}':fontfile=/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf:"
            f"fontsize=48:fontcolor=00D9FF:x=(w-text_w)/2:y=300,"
            f"drawtext=text='Processing Time: <dynamic>':fontfile=/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf:"
            f"fontsize=36:fontcolor=00F077:x=100:y=800,"
            f"drawtext=text='Compliance Status: ✓ Active':fontfile=/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf:"
            f"fontsize=36:fontcolor=00F077:x=1400:y=800"
        )

        # Find output file for this flow
        output_file = None
        for flow_config in self.phase_config.get("flows", []):
            if flow_config.get("name") == flow_name:
                output_file = flow_config.get("output", f"./output/phase3_{flow_name}_flow.mp4")
                break

        if not output_file:
            output_file = f"./output/phase3_{flow_name}_flow.mp4"

        # Ensure output directory
        Path(output_file).parent.mkdir(parents=True, exist_ok=True)

        # Create video with FFmpeg
        cmd = [
            "ffmpeg",
            "-f", "lavfi",
            "-i", filter_str,
            "-c:v", "libx264",
            "-crf", "18",
            "-pix_fmt", "yuv420p",
            "-y",
            output_file
        ]

        success, output = self.run_command(cmd)

        if success:
            self.log(f"Created {flow_name} flow animation: {output_file}")

        return success

    def execute(self) -> PhaseOutput:
        """Execute Phase 3: SVG Data Flows"""

        start_time = datetime.now()

        try:
            self.log("Starting Phase 3: SVG Data Flow Graphics")

            output_files = []
            total_duration = 0

            # Generate each flow
            flows = ["healthcare", "finance", "government"]
            for flow_name in flows:
                self.log(f"Generating {flow_name} flow...")
                success = self.create_flow_animation(flow_name, flow_name)

                if not success:
                    self.log(f"Failed to generate {flow_name} flow", "warning")
                    continue

                # Find output file
                for flow_config in self.phase_config.get("flows", []):
                    if flow_config.get("name") == flow_name:
                        output_files.append(flow_config.get("output", f"./output/phase3_{flow_name}_flow.mp4"))
                        total_duration += flow_config.get("duration", 8)
                        break

            if not output_files:
                return PhaseOutput(
                    phase_name="Phase 3: SVG Flows",
                    video_file="",
                    frame_count=0,
                    duration_seconds=0,
                    status="error",
                    error_message="No SVG flows generated"
                )

            duration_actual = (datetime.now() - start_time).total_seconds()

            self.log(f"Phase 3 complete: {len(output_files)} flows generated ({duration_actual:.1f}s)")

            return PhaseOutput(
                phase_name="Phase 3: SVG Flows",
                video_file=",".join(output_files),
                frame_count=int(total_duration * 25),
                duration_seconds=total_duration,
                status="success",
                metadata={
                    "flows": len(output_files),
                    "duration": duration_actual
                }
            )

        except Exception as e:
            self.log(f"Phase 3 execution failed: {str(e)}", "error")
            return PhaseOutput(
                phase_name="Phase 3: SVG Flows",
                video_file="",
                frame_count=0,
                duration_seconds=0,
                status="error",
                error_message=str(e)
            )
