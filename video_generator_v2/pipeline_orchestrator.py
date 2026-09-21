#!/usr/bin/env python3
"""
Pipeline Orchestrator
Master orchestration for complete E2E video generation
"""

import os
import sys
import json
import yaml
import logging
import tempfile
import subprocess
from pathlib import Path
from typing import Dict, List, Any, Optional
from datetime import datetime

# Import our generators
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'audio_generator'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'asset_generators'))

from openai_tts import OpenAITTSEngine, generate_german_narration
from powerpoint_generator import PowerPointGenerator
from svg_generator import SVGDiagramGenerator
from quality_validator import (
    validate_video_asset,
    validate_audio_audible,
    probe_duration_seconds as _probe_duration_seconds,
    QualityValidationError,
)

logger = logging.getLogger(__name__)


class RenderPipeline:
    """Master render pipeline orchestrator"""

    def __init__(self, config_path: Optional[str] = None):
        """
        Initialize render pipeline

        Args:
            config_path: Path to pipeline config YAML
        """
        self.config_path = config_path or os.path.join(
            os.path.dirname(__file__),
            'config',
            'pipeline_config.yaml'
        )
        self.config = self._load_config()

        # Set output_dir BEFORE logging setup
        self.output_dir = self.config.get("output", {}).get("directory", "/tmp/corvinos_video_v2")
        Path(self.output_dir).mkdir(parents=True, exist_ok=True)

        self._setup_logging()

        self.api_key = os.environ.get("OPENAI_API_KEY")
        logger.info(f"Pipeline initialized: {self.output_dir}")

    def _load_config(self) -> Dict[str, Any]:
        """Load configuration from YAML"""
        if not os.path.exists(self.config_path):
            logger.warning(f"Config not found: {self.config_path}, using defaults")
            return self._get_default_config()

        try:
            with open(self.config_path, 'r') as f:
                config = yaml.safe_load(f)
            logger.info(f"✓ Loaded config: {self.config_path}")
            return config
        except Exception as e:
            logger.warning(f"Failed to load config: {e}, using defaults")
            return self._get_default_config()

    def _get_default_config(self) -> Dict[str, Any]:
        """Get default configuration"""
        return {
            "output": {
                "directory": "/tmp/corvinos_video_v2",
                "final_video": "corvinos_final.mp4"
            },
            "video": {
                "width": 1920,
                "height": 1080,
                "fps": 25,
                "bitrate": "50M",
                "crf": 18
            },
            "audio": {
                "bitrate": "192k",
                "sample_rate": 24000
            },
            "segments": [
                {
                    "name": "intro",
                    "text": "Willkommen zu CorvinOS. Die nächste Generation der KI-Infrastruktur.",
                    "duration_seconds": 5
                },
                {
                    "name": "features",
                    "text": "Mit Sprachsteuerung, Verschlüsselung und vollständiger Audit-Chain.",
                    "duration_seconds": 4
                }
            ]
        }

    def _setup_logging(self):
        """Configure logging"""
        log_dir = os.path.join(self.output_dir, "logs")
        Path(log_dir).mkdir(parents=True, exist_ok=True)

        log_file = os.path.join(log_dir, "pipeline.log")

        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
            handlers=[
                logging.FileHandler(log_file),
                logging.StreamHandler(sys.stdout)
            ]
        )

    def print_banner(self):
        """Print welcome banner"""
        banner = """
╔══════════════════════════════════════════════════════════════════════╗
║              CorvinOS Premium Video Generator v2                    ║
║           Full E2E Pipeline: PowerPoint + SVG + Voice + Video       ║
║                                                                      ║
║  Pipeline Stages:                                                   ║
║  1. PowerPoint Slides → Video                                       ║
║  2. SVG Diagrams → Animated Videos                                  ║
║  3. OpenAI TTS → German Narration                                   ║
║  4. Composition & Final Assembly                                    ║
║                                                                      ║
║  Output: Broadcast-Ready MP4 (1920×1080, H.264, 50 Mbps)           ║
╚══════════════════════════════════════════════════════════════════════╝
        """
        print(banner)

    def phase1_powerpoint(self) -> str:
        """Phase 1: Generate PowerPoint slides"""
        logger.info("="*70)
        logger.info("PHASE 1: PowerPoint Slides")
        logger.info("="*70)

        try:
            pptx_file = None  # Will create default
            config = {
                "output_dir": os.path.join(self.output_dir, "phase1_pptx"),
                "fps": self.config.get("video", {}).get("fps", 25),
                "resolution": (
                    self.config.get("video", {}).get("width", 1920),
                    self.config.get("video", {}).get("height", 1080)
                ),
                # Topic content is optional and config-driven — lets this
                # pipeline generate a video about any subject, not just the
                # hardcoded CorvinOS overview.
                "slides": self.config.get("slides"),
                "footer_text": self.config.get("footer_text"),
                "slide_durations_seconds": self.config.get("slide_durations_seconds"),
            }
            config = {k: v for k, v in config.items() if v is not None}

            generator = PowerPointGenerator(pptx_file=pptx_file, config=config)
            video = generator.execute()

            # Defense in depth: re-validate at the orchestrator boundary too,
            # so a future generator change can't silently skip this check.
            validate_video_asset(video, require_audio=False)

            logger.info(f"✓ Phase 1 complete: {video}")
            return video

        except (Exception, QualityValidationError) as e:
            logger.error(f"Phase 1 failed: {e}")
            raise

    def phase2_svg_diagrams(self) -> Dict[str, str]:
        """Phase 2: Generate SVG diagrams"""
        logger.info("="*70)
        logger.info("PHASE 2: SVG Diagrams")
        logger.info("="*70)

        try:
            config = {
                "output_dir": os.path.join(self.output_dir, "phase2_svg"),
                "fps": self.config.get("video", {}).get("fps", 25),
                "resolution": (
                    self.config.get("video", {}).get("width", 1920),
                    self.config.get("video", {}).get("height", 1080)
                ),
                "diagrams": self.config.get("diagrams"),
            }
            config = {k: v for k, v in config.items() if v is not None}

            generator = SVGDiagramGenerator(config=config)
            diagrams = generator.execute()

            # Defense in depth: re-validate every diagram at the orchestrator
            # boundary, same reasoning as Phase 1.
            for diagram_name, diagram_path in diagrams.items():
                validate_video_asset(diagram_path, require_audio=False)

            logger.info(f"✓ Phase 2 complete: {len(diagrams)} diagrams (all content-verified)")
            return diagrams

        except (Exception, QualityValidationError) as e:
            logger.error(f"Phase 2 failed: {e}")
            raise

    def phase3_openai_voice(self) -> str:
        """Phase 3: Generate OpenAI TTS narration"""
        logger.info("="*70)
        logger.info("PHASE 3: OpenAI TTS Narration")
        logger.info("="*70)

        if not self.api_key:
            # Legitimate "no voice requested" state — not an error.
            logger.warning("OPENAI_API_KEY not set, skipping narration")
            return ""

        segments = self.config.get("segments", [])
        if not segments:
            logger.warning("No segments configured for narration")
            return ""

        # From here on an API key WAS provided, so voice is expected —
        # fail closed instead of silently shipping a voiceless video.
        # (Root cause of the earlier "es gibt keinen content"/no-audio
        # reports: this used to swallow the exception and return "",
        # and phase4 happily composed a silent video and called it done.)
        output_dir = os.path.join(self.output_dir, "phase3_voice")
        narration = generate_german_narration(
            segments=segments,
            output_dir=output_dir,
            api_key=self.api_key
        )

        if not narration or not os.path.exists(narration):
            raise QualityValidationError(
                "OPENAI_API_KEY is set but narration generation produced no file — "
                "refusing to continue with a silent video"
            )

        validate_audio_audible(narration)

        logger.info(f"✓ Phase 3 complete: {narration}")
        return narration

    def phase4_composition(
        self,
        pptx_video: str,
        svg_diagrams: Dict[str, str],
        narration_audio: str
    ) -> str:
        """Phase 4: Compose all elements into final video"""
        logger.info("="*70)
        logger.info("PHASE 4: Composition & Assembly")
        logger.info("="*70)

        try:
            # Build video segment list (order matters). Diagram order follows
            # svg_diagrams' insertion order, so a config-driven diagram list
            # (any names/topic) composes correctly, not just the four
            # hardcoded CorvinOS diagram keys.
            segments = [{"name": "PowerPoint Slides", "video": pptx_video}]
            segments += [
                {"name": name, "video": path} for name, path in svg_diagrams.items()
            ]

            # Filter out missing files
            valid_segments = [s for s in segments if s["video"] and os.path.exists(s["video"])]

            logger.info(f"Composing {len(valid_segments)} video segments...")

            # Create concat demuxer file
            concat_file = os.path.join(self.output_dir, "concat_segments.txt")
            with open(concat_file, 'w') as f:
                for seg in valid_segments:
                    f.write(f"file '{seg['video']}'\n")
                    logger.info(f"  Adding: {seg['name']} → {seg['video']}")

            # Concatenate all videos
            master_video = os.path.join(self.output_dir, "master_video.mp4")

            # Force a constant target framerate on the concat output.
            # Root cause of a real bug the quality gate caught here: the
            # PowerPoint phase writes its clip as VFR (`-fps_mode vfr`,
            # one frame held per slide duration via the concat demuxer's
            # `duration` directive) at ~1/12 fps, while the SVG diagram
            # clips are CFR at 25fps. Concatenating those without pinning
            # an explicit output framerate produced a master video whose
            # measured bitrate collapsed to ~21k bps — each asset was
            # individually verified as real content, but the merge itself
            # diluted it. `-r <fps> -fps_mode cfr` normalizes every input
            # to one consistent frame rate before muxing.
            target_fps = self.config.get("video", {}).get("fps", 25)
            cmd = [
                "ffmpeg",
                "-f", "concat",
                "-safe", "0",
                "-i", concat_file,
                "-r", str(target_fps),
                "-fps_mode", "cfr",
                "-c:v", "libx264",
                "-crf", "18",
                "-pix_fmt", "yuv420p",
                "-y",
                master_video
            ]

            logger.info("Concatenating video segments...")
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)

            if result.returncode != 0:
                logger.error(f"Concatenation failed: {result.stderr}")
                raise RuntimeError("Video concatenation failed")

            logger.info(f"✓ Master video created: {master_video}")

            # Fail-closed defense in depth: the concatenated slide/diagram
            # video must itself still carry real content at this point.
            validate_video_asset(master_video, require_audio=False)

            # Add audio if available. If narration was generated (Phase 3
            # succeeded with an API key present), mixing it in is REQUIRED —
            # falling back to a silent video here is exactly the "content
            # fehlt"/"kein Ton" bug this pipeline kept shipping.
            if narration_audio and os.path.exists(narration_audio):
                final_video = os.path.join(self.output_dir, "corvinos_final.mp4")

                # Root cause fixed here: `-c:v copy ... -shortest` does not
                # reliably truncate a stream-copied video track to the
                # audio track's length (measured directly: a 108.0s master
                # video + 76.7s narration produced video=97.9s/audio=78.9s
                # — the last ~19s played silently, and require_audio's
                # volumedetect check didn't catch it because it only
                # measures the audio stream's own loudness, not how far
                # past it the video runs). Compute the shorter duration
                # explicitly and re-encode the video to hit it exactly —
                # `-t` on a copied stream only cuts at the next keyframe,
                # which is not precise enough here.
                video_duration = _probe_duration_seconds(master_video)
                audio_duration = _probe_duration_seconds(narration_audio)
                target_duration = min(video_duration, audio_duration)
                logger.info(
                    f"Trimming to shorter of video={video_duration:.1f}s / "
                    f"audio={audio_duration:.1f}s → target={target_duration:.1f}s"
                )

                cmd = [
                    "ffmpeg",
                    "-i", master_video,
                    "-i", narration_audio,
                    "-t", str(target_duration),
                    "-c:v", "libx264",
                    "-crf", "18",
                    "-pix_fmt", "yuv420p",
                    "-c:a", "aac",
                    "-y",
                    final_video
                ]

                logger.info("Adding audio narration...")
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)

                if result.returncode != 0 or not os.path.exists(final_video):
                    raise RuntimeError(
                        f"Audio mixing failed (narration was generated, so a silent "
                        f"fallback is not acceptable): {result.stderr[:300]}"
                    )

                # Fail-closed: the final deliverable must have BOTH real
                # video content and audible audio, not just "a stream exists".
                validate_video_asset(final_video, require_audio=True)
                logger.info(f"✓ Final video with verified audio+content: {final_video}")
                return final_video

            # No narration was configured/available at all (no API key) —
            # a video-only output is a legitimate outcome in that case.
            return master_video

        except Exception as e:
            logger.error(f"Phase 4 failed: {e}")
            raise

    def validate_output(self, video_file: str, require_audio: bool = False) -> bool:
        """Fail-closed validation of the final video output.

        Replaces the previous implementation, which returned True on any
        ffprobe exception and only ever failed on a missing video stream —
        it could not detect a flat-color/empty video or a silent audio
        track, which is exactly what this pipeline kept shipping. This
        version re-runs the same real content checks the phases already
        apply, as a last line of defense on the actual deliverable.
        """
        logger.info("Validating final video (fail-closed)...")

        try:
            validate_video_asset(video_file, require_audio=require_audio)
        except QualityValidationError as e:
            logger.error(f"Output validation FAILED: {e}")
            return False

        file_size = os.path.getsize(video_file)
        logger.info(f"✓ File size: {file_size / (1024*1024):.1f} MB")
        logger.info("✓ Output validation passed (streams, bitrate"
                     f"{', audio loudness' if require_audio else ''})")
        return True

    def run(self) -> str:
        """Execute complete pipeline"""
        self.print_banner()

        start_time = datetime.now()

        try:
            # Phase 1: PowerPoint
            pptx_video = self.phase1_powerpoint()

            # Phase 2: SVG Diagrams
            svg_diagrams = self.phase2_svg_diagrams()

            # Phase 3: OpenAI Voice
            narration_audio = self.phase3_openai_voice()

            # Phase 4: Composition
            final_video = self.phase4_composition(pptx_video, svg_diagrams, narration_audio)

            # Validate — if narration was generated, the deliverable MUST
            # carry audible audio; a silent fallback is not acceptable.
            narration_was_expected = bool(narration_audio)
            if self.validate_output(final_video, require_audio=narration_was_expected):
                duration = (datetime.now() - start_time).total_seconds()

                logger.info("="*70)
                logger.info("✅ PIPELINE COMPLETE")
                logger.info("="*70)
                logger.info(f"Output: {final_video}")
                logger.info(f"Duration: {duration:.1f} seconds")
                logger.info("="*70)

                return final_video
            else:
                raise RuntimeError("Output validation failed")

        except Exception as e:
            logger.error(f"Pipeline execution failed: {e}")
            raise


def main():
    """Main entry point"""
    logging.basicConfig(level=logging.INFO)

    try:
        pipeline = RenderPipeline()
        output = pipeline.run()
        print(f"\n✅ Generated: {output}\n")
        return 0
    except Exception as e:
        print(f"\n❌ Pipeline failed: {e}\n", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
