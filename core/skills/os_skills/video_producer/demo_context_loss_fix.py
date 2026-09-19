#!/usr/bin/env python3
"""
Demo: Context-Loss Deep-Fix in Video Producer

This script demonstrates the FAIL-CLOSED pattern where missing content is
LOUDLY rejected instead of silently falling back to placeholder output.

Execution:
  python3 demo_context_loss_fix.py
"""

import sys
import logging
from pathlib import Path

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)-8s | %(message)s"
)
logger = logging.getLogger(__name__)

# Avoid circular import by adding parent to path FIRST
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))

# Import maestro (this avoids the local types.py circular import)
try:
    from core.skills.os_skills.video_producer.maestro import VideoProducerMaestro
except ImportError:
    # Fallback: try direct import if already in the right directory
    sys.path.insert(0, str(Path(__file__).parent))
    from maestro import VideoProducerMaestro


def test_case_1_valid_job():
    """Test Case 1: Valid job with FULL narration content."""
    logger.info("\n" + "="*70)
    logger.info("TEST 1: Valid job with FULL narration content")
    logger.info("="*70)

    maestro = VideoProducerMaestro("/tmp/test_maestro_1")

    job = {
        "job_id": "corvinos_showcase",
        "narration": [
            {"scene_index": 0, "text": "What if your operating system could think?", "duration_seconds": 6},
            {"scene_index": 1, "text": "Meet CorvinOS — the first agentic operating system.", "duration_seconds": 10},
            {"scene_index": 2, "text": "Powered by Claude and optimized for autonomous workflows.", "duration_seconds": 8},
        ],
        "components": {
            "blender": {
                "scenes": [
                    {"name": "intro", "duration": 6, "scene_data": {"camera": {}, "objects": []}},
                ]
            }
        },
    }

    try:
        maestro.validate_job_content(job)
        narration = maestro._extract_narration_content(job)
        logger.info(f"✓ PASS: Valid job accepted")
        logger.info(f"  - Narration scenes: {len(narration)}")
        logger.info(f"  - Scene 1: {narration[0][:50]}...")
        return True
    except ValueError as e:
        logger.error(f"✗ FAIL: {e}")
        return False


def test_case_2_empty_narration():
    """Test Case 2: Job with NO narration — should LOUDLY REJECT."""
    logger.info("\n" + "="*70)
    logger.info("TEST 2: Job with NO narration — FAIL-CLOSED")
    logger.info("="*70)

    maestro = VideoProducerMaestro("/tmp/test_maestro_2")

    job = {"narration": []}  # Empty narration

    try:
        maestro.validate_job_content(job)
        logger.error("✗ FAIL: Should have rejected empty narration")
        return False
    except ValueError as e:
        logger.info(f"✓ PASS: Correctly rejected empty narration")
        logger.info(f"  - Error: {e}")
        return True


def test_case_3_missing_narration_text():
    """Test Case 3: Narration scene with NO TEXT — should LOUDLY REJECT."""
    logger.info("\n" + "="*70)
    logger.info("TEST 3: Narration scene with NO TEXT — FAIL-CLOSED")
    logger.info("="*70)

    maestro = VideoProducerMaestro("/tmp/test_maestro_3")

    job = {
        "narration": [
            {"scene_index": 0, "text": ""},  # Empty text
        ]
    }

    try:
        maestro.validate_job_content(job)
        logger.error("✗ FAIL: Should have rejected empty text")
        return False
    except ValueError as e:
        logger.info(f"✓ PASS: Correctly rejected empty narration text")
        logger.info(f"  - Error: {e}")
        return True


def test_case_4_short_narration():
    """Test Case 4: Narration that's too short (stub) — should LOUDLY REJECT."""
    logger.info("\n" + "="*70)
    logger.info("TEST 4: Narration text too short (stub) — FAIL-CLOSED")
    logger.info("="*70)

    maestro = VideoProducerMaestro("/tmp/test_maestro_4")

    job = {
        "narration": [
            {"scene_index": 0, "text": "Hi"},  # Too short
        ]
    }

    try:
        maestro.validate_job_content(job)
        logger.error("✗ FAIL: Should have rejected short text")
        return False
    except ValueError as e:
        logger.info(f"✓ PASS: Correctly rejected stub narration")
        logger.info(f"  - Error: {e}")
        return True


def test_case_5_missing_blender_content():
    """Test Case 5: Blender config with NO SCENES — should LOUDLY REJECT."""
    logger.info("\n" + "="*70)
    logger.info("TEST 5: Blender config with NO SCENES — FAIL-CLOSED")
    logger.info("="*70)

    maestro = VideoProducerMaestro("/tmp/test_maestro_5")

    job = {
        "narration": [
            {"scene_index": 0, "text": "Valid narration"},
        ],
        "components": {
            "blender": {
                "scenes": []  # No scenes
            }
        },
    }

    try:
        maestro.validate_job_content(job)
        logger.error("✗ FAIL: Should have rejected empty blender scenes")
        return False
    except ValueError as e:
        logger.info(f"✓ PASS: Correctly rejected missing blender scenes")
        logger.info(f"  - Error: {e}")
        return True


def test_case_6_blender_scene_no_data():
    """Test Case 6: Blender scene without file or data — should LOUDLY REJECT."""
    logger.info("\n" + "="*70)
    logger.info("TEST 6: Blender scene without file or data — FAIL-CLOSED")
    logger.info("="*70)

    maestro = VideoProducerMaestro("/tmp/test_maestro_6")

    job = {
        "narration": [
            {"scene_index": 0, "text": "Valid narration"},
        ],
        "components": {
            "blender": {
                "scenes": [
                    {
                        "name": "incomplete_scene",
                        # No scene_file or scene_data
                    }
                ]
            }
        },
    }

    try:
        maestro.validate_job_content(job)
        logger.error("✗ FAIL: Should have rejected incomplete scene")
        return False
    except ValueError as e:
        logger.info(f"✓ PASS: Correctly rejected incomplete blender scene")
        logger.info(f"  - Error: {e}")
        return True


def test_case_7_audio_whistle_detection():
    """Test Case 7: Audio validation detects whistle tone (tiny file)."""
    logger.info("\n" + "="*70)
    logger.info("TEST 7: Audio validation detects whistle tone")
    logger.info("="*70)

    import tempfile

    maestro = VideoProducerMaestro("/tmp/test_maestro_7")

    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
        # Write tiny audio (whistle = < 5KB)
        f.write(b"fake audio")
        tiny_audio = Path(f.name)

    try:
        maestro._validate_audio_output(tiny_audio)
        logger.error("✗ FAIL: Should have rejected tiny audio file")
        return False
    except ValueError as e:
        logger.info(f"✓ PASS: Correctly detected whistle tone (tiny file)")
        logger.info(f"  - Error: {e}")
        tiny_audio.unlink()
        return True


def test_case_8_audio_real_content():
    """Test Case 8: Audio validation passes for real audio."""
    logger.info("\n" + "="*70)
    logger.info("TEST 8: Audio validation passes for real audio content")
    logger.info("="*70)

    import tempfile

    maestro = VideoProducerMaestro("/tmp/test_maestro_8")

    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
        # Write realistic audio (real = > 5KB)
        f.write(b"x" * 50_000)
        real_audio = Path(f.name)

    try:
        maestro._validate_audio_output(real_audio)
        logger.info(f"✓ PASS: Real audio accepted")
        logger.info(f"  - File size: 50 KB")
        real_audio.unlink()
        return True
    except ValueError as e:
        logger.error(f"✗ FAIL: {e}")
        real_audio.unlink()
        return False


def test_case_9_video_placeholder_detection():
    """Test Case 9: Video validation detects placeholder (tiny file)."""
    logger.info("\n" + "="*70)
    logger.info("TEST 9: Video validation detects placeholder (solid color)")
    logger.info("="*70)

    import tempfile

    maestro = VideoProducerMaestro("/tmp/test_maestro_9")

    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as f:
        # Write tiny video (placeholder = < 100KB)
        f.write(b"solid color frame")
        tiny_video = Path(f.name)

    try:
        maestro._validate_video_output(tiny_video)
        logger.error("✗ FAIL: Should have rejected tiny video file")
        return False
    except ValueError as e:
        logger.info(f"✓ PASS: Correctly detected placeholder video")
        logger.info(f"  - Error: {e}")
        tiny_video.unlink()
        return True


def test_case_10_video_real_content():
    """Test Case 10: Video validation passes for real video."""
    logger.info("\n" + "="*70)
    logger.info("TEST 10: Video validation passes for real video content")
    logger.info("="*70)

    import tempfile

    maestro = VideoProducerMaestro("/tmp/test_maestro_10")

    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as f:
        # Write realistic video (real = > 100KB)
        f.write(b"x" * 500_000)
        real_video = Path(f.name)

    try:
        maestro._validate_video_output(real_video)
        logger.info(f"✓ PASS: Real video accepted")
        logger.info(f"  - File size: 500 KB")
        real_video.unlink()
        return True
    except ValueError as e:
        logger.error(f"✗ FAIL: {e}")
        real_video.unlink()
        return False


def main():
    """Run all test cases."""
    logger.info("\n\n")
    logger.info("╔" + "═"*68 + "╗")
    logger.info("║  CORVINOS CONTEXT-LOSS DEEP-FIX DEMONSTRATION                 ║")
    logger.info("║  Fail-Closed Pattern: LOUD rejection vs Silent Fallback        ║")
    logger.info("╚" + "═"*68 + "╝")

    test_cases = [
        ("Valid job with full content", test_case_1_valid_job),
        ("Empty narration rejection", test_case_2_empty_narration),
        ("Missing narration text", test_case_3_missing_narration_text),
        ("Short narration (stub)", test_case_4_short_narration),
        ("Missing blender scenes", test_case_5_missing_blender_content),
        ("Incomplete blender scene", test_case_6_blender_scene_no_data),
        ("Whistle tone detection", test_case_7_audio_whistle_detection),
        ("Real audio validation", test_case_8_audio_real_content),
        ("Placeholder video detection", test_case_9_video_placeholder_detection),
        ("Real video validation", test_case_10_video_real_content),
    ]

    results = []
    for name, test_fn in test_cases:
        try:
            passed = test_fn()
            results.append((name, passed))
        except Exception as e:
            logger.error(f"✗ EXCEPTION: {e}", exc_info=True)
            results.append((name, False))

    # Summary
    logger.info("\n" + "="*70)
    logger.info("SUMMARY")
    logger.info("="*70)

    passed = sum(1 for _, p in results if p)
    total = len(results)

    for name, passed_flag in results:
        status = "✓ PASS" if passed_flag else "✗ FAIL"
        logger.info(f"  {status}: {name}")

    logger.info(f"\nTotal: {passed}/{total} tests passed")
    logger.info("="*70 + "\n")

    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
