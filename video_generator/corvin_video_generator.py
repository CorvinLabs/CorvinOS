#!/usr/bin/env python3
"""
CorvinOS Premium Video Generator
Orchestrator for the complete 5-phase video generation pipeline

Usage:
    python3 corvin_video_generator.py [--config config/video_settings.yaml] [--phase N]
"""

import os
import sys
import argparse
import yaml
import logging
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, List

# Add phases directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'phases'))

from base_utils import (
    setup_logging, PhaseOutput, ProgressTracker, BasePhase
)
from phase1_blender_intro import BlenderIntroPhase
from phase2_manim_diagrams import ManimDiagramsPhase
from phase3_svg_flows import SVGFlowsPhase
from phase4_particle_effects import ParticleEffectsPhase
from phase5_compositor import CompositorPhase


class VideoGeneratorOrchestrator:
    """Main orchestrator for video generation pipeline"""

    def __init__(self, config_path: str):
        """Initialize with config file"""
        self.config_path = config_path
        self.config = self._load_config()
        self.logger = setup_logging(self.config)
        self.logger = logging.getLogger(__name__)
        self.progress = ProgressTracker(5)

        # Verify output directories
        self._setup_directories()

    def _load_config(self) -> Dict[str, Any]:
        """Load and validate configuration"""
        try:
            with open(self.config_path, 'r') as f:
                config = yaml.safe_load(f)
            print(f"✓ Loaded config: {self.config_path}")
            return config
        except FileNotFoundError:
            print(f"✗ Config file not found: {self.config_path}")
            sys.exit(1)
        except yaml.YAMLError as e:
            print(f"✗ Invalid YAML: {e}")
            sys.exit(1)

    def _setup_directories(self):
        """Create necessary directories"""
        dirs_to_create = [
            self.config.get("output", {}).get("temp_dir", "/tmp/corvinos_video_gen"),
            self.config.get("output", {}).get("assets_dir", "./output/assets"),
            self.config.get("output", {}).get("frames_dir", "./output/frames"),
            Path(self.config.get("logging", {}).get("log_file", "./logs/video_generation.log")).parent,
        ]

        for dir_path in dirs_to_create:
            Path(dir_path).mkdir(parents=True, exist_ok=True)

    def print_banner(self):
        """Print welcome banner"""
        banner = """
╔══════════════════════════════════════════════════════════════════════════╗
║                   CorvinOS Premium Video Generator                       ║
║                         Broadcast Quality 1920×1080                      ║
║                                                                          ║
║  5-Phase Pipeline:                                                       ║
║  ✓ Phase 1: Blender 3D Logo (5s)                                        ║
║  ✓ Phase 2: Manim Diagrams (12s)                                        ║
║  ✓ Phase 3: SVG Data Flows (24s)                                        ║
║  ✓ Phase 4: Particle Effects (51s overlay)                              ║
║  ✓ Phase 5: Composition & Grading (final assembly)                      ║
║                                                                          ║
║  Output: ~51 seconds @ 25fps, H.264, 50 Mbps, Broadcast Quality         ║
╚══════════════════════════════════════════════════════════════════════════╝
        """
        print(banner)

    def run_phase(self, phase_num: int) -> PhaseOutput:
        """Run a specific phase"""

        phases = [
            BlenderIntroPhase,
            ManimDiagramsPhase,
            SVGFlowsPhase,
            ParticleEffectsPhase,
            CompositorPhase,
        ]

        if phase_num < 1 or phase_num > len(phases):
            self.logger.error(f"Invalid phase number: {phase_num}")
            return None

        phase_class = phases[phase_num - 1]
        phase = phase_class(self.config)

        self.logger.info(f"\n{'='*70}")
        self.logger.info(f"Phase {phase_num}: Starting execution")
        self.logger.info(f"{'='*70}")

        result = phase.execute()
        self.progress.add_phase_result(result)

        # Print result
        status_emoji = "✓" if result.status == "success" else ("⊘" if result.status == "skipped" else "✗")
        self.logger.info(f"{status_emoji} {result.phase_name}: {result.status}")

        if result.error_message:
            self.logger.error(f"  Error: {result.error_message}")
        else:
            self.logger.info(f"  Duration: {result.duration_seconds}s, Frames: {result.frame_count}")
            self.logger.info(f"  Output: {result.video_file}")

        return result

    def run_all_phases(self) -> bool:
        """Run all phases sequentially"""

        self.print_banner()

        start_time = datetime.now()

        self.logger.info(f"Starting complete video generation pipeline")
        self.logger.info(f"Config: {self.config_path}")
        self.logger.info(f"Output: {self.config.get('output', {}).get('file')}")

        # Execute phases sequentially
        for phase_num in range(1, 6):
            result = self.run_phase(phase_num)

            if result.status == "error":
                self.logger.error(f"Phase {phase_num} failed, aborting pipeline")
                return False

        # Print summary
        total_duration = (datetime.now() - start_time).total_seconds()
        summary = self.progress.get_summary()

        self.logger.info(f"\n{'='*70}")
        self.logger.info(f"Pipeline Complete")
        self.logger.info(f"{'='*70}")
        self.logger.info(f"Total Duration: {total_duration:.1f}s")
        self.logger.info(f"Completed: {summary['completed']}/{summary['total_phases']}")
        self.logger.info(f"Failed: {summary['failed']}, Skipped: {summary['skipped']}")

        # Check if final file exists
        final_file = self.config.get("output", {}).get("file")
        if final_file and os.path.exists(final_file):
            file_size_mb = os.path.getsize(final_file) / (1024 * 1024)
            self.logger.info(f"\n✓ SUCCESS: {final_file}")
            self.logger.info(f"  File Size: {file_size_mb:.1f} MB")
            self.logger.info(f"  Duration: 51 seconds @ 25fps")
            self.logger.info(f"  Quality: H.264, 50 Mbps, 1920×1080 (Broadcast)")
            return True
        else:
            self.logger.error(f"✗ FAILED: Final output not found")
            return False

    def run_single_phase(self, phase_num: int) -> bool:
        """Run a single phase"""

        self.logger.info(f"Running Phase {phase_num} only")
        result = self.run_phase(phase_num)

        return result.status == "success"

    def check_dependencies(self) -> Dict[str, bool]:
        """Check required and optional dependencies"""

        self.logger.info("Checking dependencies...")

        # Create a dummy phase to use check_dependency
        dummy_phase = BasePhase(self.config, "DependencyCheck")

        required = self.config.get("dependencies", {}).get("required", [])
        optional = self.config.get("dependencies", {}).get("optional", [])

        results = {}

        for tool in required:
            available = dummy_phase.check_dependency(tool)
            results[tool] = available
            status = "✓" if available else "✗"
            self.logger.info(f"  {status} {tool} (REQUIRED)")

            if not available:
                self.logger.error(f"    → Please install: sudo apt-get install {tool}")

        for tool in optional:
            available = dummy_phase.check_dependency(tool)
            results[tool] = available
            status = "✓" if available else "⊘"
            self.logger.info(f"  {status} {tool} (optional, fallback available)")

        return results


def main():
    """Main entry point"""

    parser = argparse.ArgumentParser(
        description="CorvinOS Premium Video Generator",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Generate complete video
  python3 corvin_video_generator.py

  # Generate with custom config
  python3 corvin_video_generator.py --config my_config.yaml

  # Run only Phase 2 (Manim diagrams)
  python3 corvin_video_generator.py --phase 2

  # Check dependencies
  python3 corvin_video_generator.py --check-deps
        """
    )

    parser.add_argument(
        '--config',
        default='config/video_settings.yaml',
        help='Path to configuration file (default: config/video_settings.yaml)'
    )
    parser.add_argument(
        '--phase',
        type=int,
        choices=[1, 2, 3, 4, 5],
        help='Run only a specific phase (1-5)'
    )
    parser.add_argument(
        '--check-deps',
        action='store_true',
        help='Check dependencies and exit'
    )
    parser.add_argument(
        '--verbose',
        action='store_true',
        help='Enable verbose logging'
    )

    args = parser.parse_args()

    # Create orchestrator
    orchestrator = VideoGeneratorOrchestrator(args.config)

    # Check dependencies if requested
    if args.check_deps:
        deps = orchestrator.check_dependencies()
        all_required_ok = all(deps.get(tool) for tool in orchestrator.config.get("dependencies", {}).get("required", []))
        sys.exit(0 if all_required_ok else 1)

    # Run pipeline
    if args.phase:
        success = orchestrator.run_single_phase(args.phase)
    else:
        success = orchestrator.run_all_phases()

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
