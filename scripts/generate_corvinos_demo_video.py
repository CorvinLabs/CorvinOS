#!/usr/bin/env python3
"""Generate 90-second CorvinOS Demo Video

This script uses the Video Producer Skill 2.0 (Phase 4) to generate a
professional 90-second video explaining CorvinOS and demonstrating the
Video Producer plugin capabilities.

Execution:
    python3 scripts/generate_corvinos_demo_video.py

Output:
    - /tmp/video_<jobid>_final.mp4 (the actual video)
    - /outputs/demo_video_corvinOS_90sec.mp4 (final output)
    - /tmp/<jobid>_upload_metadata.json (YouTube metadata)
"""

import sys
import os
from pathlib import Path

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from core.skills.video_producer.maestro import MaestroOrchestrator, VideoJobPhase
from core.skills.video_producer.workers.asset_analyzer import AssetAnalyzerWorker
from core.skills.video_producer.workers.voice_synthesizer import VoiceSynthesizerWorker
from core.skills.video_producer.workers.screenshot_capturer import ScreenshotCapturerWorker
from core.skills.video_producer.workers.video_assembler import VideoAssemblerWorker
from core.skills.video_producer.workers.youtube_uploader import YouTubeUploaderWorker
import shutil


def main():
    print("=" * 80)
    print("VIDEO PRODUCER SKILL 2.0 — PHASE 4 DEMO")
    print("Generating 90-second CorvinOS Tutorial Video")
    print("=" * 80)
    print()

    # Create maestro orchestrator
    maestro = MaestroOrchestrator()

    # Define 90-second video with 3 scenes (30s each)
    narration_scenes = [
        # Scene 1 (0-30s): "The Problem"
        """
        Developers today struggle with complex task automation.
        APIs break, workflows get stuck, learning systems go stale.
        Meet CorvinOS — a modern operating system for AI agents
        that learns from experience and improves over time.
        """.strip(),

        # Scene 2 (30-60s): "The Solution"
        """
        CorvinOS powers AI agents with learning loops, real-time audit trails,
        and multi-engine skill composition. Watch as the Video Producer Skill
        orchestrates assets, voice narration, and video assembly — all audited
        and learned from with every run. Skills are self-optimizing programs
        that improve through user feedback.
        """.strip(),

        # Scene 3 (60-90s): "The Payoff"
        """
        Generate professional videos, manage distributed tasks across engines,
        and let your system improve with every run. CorvinOS is the operating
        system that learns. Visit corvin-labs.com to get started with CorvinOS today.
        """.strip(),
    ]

    # Create video job
    print("[1/5] Creating video job...")
    job_id = maestro.create_job(
        topic="What is CorvinOS?",
        duration=90,
        audience="beginners",
        narration=narration_scenes,
    )
    print(f"✓ Job created: {job_id}")
    print()

    # Get the job
    job = maestro.get_job(job_id)

    # Register workers for each phase
    print("[SETUP] Registering Phase 4 workers...")
    maestro.register_worker(VideoJobPhase.ANALYSIS, AssetAnalyzerWorker())
    maestro.register_worker(VideoJobPhase.VOICE, VoiceSynthesizerWorker(tts_provider="edge-tts"))
    maestro.register_worker(VideoJobPhase.SCREENSHOTS, ScreenshotCapturerWorker())
    maestro.register_worker(VideoJobPhase.ASSEMBLY, VideoAssemblerWorker(codec="h264", preset="medium"))
    maestro.register_worker(VideoJobPhase.YOUTUBE, YouTubeUploaderWorker(visibility="unlisted"))
    print("✓ All workers registered")
    print()

    # Execute each phase in order
    phases = [
        ("ANALYSIS", "Asset analysis and validation"),
        ("VOICE", "Voice narration synthesis (TTS)"),
        ("SCREENSHOTS", "Screenshot capture (Playwright)"),
        ("ASSEMBLY", "Video assembly (FFmpeg)"),
        ("YOUTUBE", "YouTube metadata generation"),
    ]

    for idx, (phase_name, description) in enumerate(phases, 1):
        print(f"[{idx + 1}/{len(phases) + 1}] Executing {phase_name}: {description}...")
        try:
            result = maestro.execute_phase(job_id)

            if isinstance(result, dict):
                success = result.get("success", True)
            else:
                success = getattr(result, "success", True)

            if success:
                print(f"✓ {phase_name} completed successfully")
                print(f"  Result: {result}")
            else:
                print(f"⚠ {phase_name} completed with warnings")
                print(f"  Result: {result}")
        except Exception as e:
            print(f"✗ {phase_name} failed: {e}")
            import traceback
            traceback.print_exc()
            return 1

        print()

    # Get final job status
    print("[FINAL] Retrieving job results...")
    final_job = maestro.get_job(job_id)

    print(f"✓ Job completed: {job_id}")
    print()
    print("Final Results:")
    print("-" * 80)

    if final_job.video_result:
        video_path = (
            final_job.video_result.video_path
            if hasattr(final_job.video_result, "video_path")
            else final_job.video_result.get("video_path")
        )
        print(f"Video File: {video_path}")
        if os.path.exists(video_path):
            file_size_mb = os.path.getsize(video_path) / (1024 * 1024)
            print(f"Video Size: {file_size_mb:.2f} MB")

    if final_job.youtube_result:
        video_id = (
            final_job.youtube_result.video_id
            if hasattr(final_job.youtube_result, "video_id")
            else final_job.youtube_result.get("video_id")
        )
        url = (
            final_job.youtube_result.url
            if hasattr(final_job.youtube_result, "url")
            else final_job.youtube_result.get("url")
        )
        print(f"Video ID: {video_id}")
        print(f"URL: {url}")

    print()
    print("Audit Log Events:")
    print("-" * 80)
    audit_log = maestro.get_audit_log()
    for event in audit_log[-5:]:  # Last 5 events
        print(f"  {event['event_type']:20s} → {event['details']}")

    print()
    print("=" * 80)
    print("PHASE 4 DEMO COMPLETE!")
    print("=" * 80)

    # Copy final video to outputs if it exists
    if final_job.video_result:
        video_path = (
            final_job.video_result.video_path
            if hasattr(final_job.video_result, "video_path")
            else final_job.video_result.get("video_path")
        )

        if os.path.exists(video_path):
            output_dir = Path(PROJECT_ROOT) / "outputs"
            output_dir.mkdir(exist_ok=True)
            output_file = output_dir / "demo_video_corvinOS_90sec.mp4"

            try:
                shutil.copy(video_path, str(output_file))
                print(f"\n✓ Video saved to: {output_file}")
            except Exception as e:
                print(f"\n⚠ Could not copy video to outputs: {e}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
