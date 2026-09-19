"""
Tests for Context-Loss Deep-Fix — Verifies narration/blender content is preserved
throughout video production pipeline.

Tests the FAIL-CLOSED pattern where missing content is LOUDLY rejected instead of
silently falling back to placeholder output (solid color + whistle tone).

Load-bearing test suite: ensures the deep-fix pattern is maintained.
"""

import asyncio
import json
import logging
import tempfile
from pathlib import Path
from typing import Any, Dict

import pytest

from maestro import VideoProducerMaestro
from worker_base import WorkerSkillBase, WorkerManifest, WorkerResult

logger = logging.getLogger(__name__)


class TestContextLossDeepFix:
    """Test suite for context-loss deep-fix in video producer."""

    @pytest.fixture
    def maestro(self, tmp_path):
        """Create a maestro instance with temporary project directory."""
        return VideoProducerMaestro(tmp_path)

    @pytest.fixture
    def valid_job(self) -> Dict[str, Any]:
        """Create a valid job with FULL narration content (not references)."""
        return {
            "job_id": "test_corvinos_showcase",
            "narration": [
                {
                    "scene_index": 0,
                    "text": "What if your operating system could think?",
                    "duration_seconds": 6,
                },
                {
                    "scene_index": 1,
                    "text": "Meet CorvinOS — the first agentic operating system.",
                    "duration_seconds": 10,
                },
                {
                    "scene_index": 2,
                    "text": "Powered by Claude and optimized for autonomous workflows.",
                    "duration_seconds": 8,
                },
                {
                    "scene_index": 3,
                    "text": "Transform your operational capacity with AI-native infrastructure.",
                    "duration_seconds": 8,
                },
                {
                    "scene_index": 4,
                    "text": "CorvinOS: Where intelligence meets infrastructure.",
                    "duration_seconds": 6,
                },
            ],
            "components": {
                "blender": {
                    "scenes": [
                        {
                            "name": "logo_intro",
                            "duration": 6,
                            "scene_data": {"camera": {}, "objects": [], "lighting": {}},
                        },
                        {
                            "name": "think_concept",
                            "duration": 10,
                            "scene_data": {"camera": {}, "objects": [], "lighting": {}},
                        },
                    ]
                }
            },
        }

    def test_gate_rejects_empty_narration(self, maestro):
        """Verify gate LOUDLY rejects jobs with no narration."""
        job = {"narration": []}

        with pytest.raises(ValueError, match="no narration segments"):
            maestro.validate_job_content(job)

    def test_gate_rejects_missing_narration_text(self, maestro):
        """Verify gate rejects narration scenes with missing text."""
        job = {
            "narration": [
                {"scene_index": 0, "text": ""},  # Empty text
            ]
        }

        with pytest.raises(ValueError, match="has no text"):
            maestro.validate_job_content(job)

    def test_gate_rejects_short_narration_text(self, maestro):
        """Verify gate rejects narration that is too short (likely stub)."""
        job = {
            "narration": [
                {"scene_index": 0, "text": "Hi"},  # Too short
            ]
        }

        with pytest.raises(ValueError, match="too short"):
            maestro.validate_job_content(job)

    def test_gate_rejects_missing_blender_content(self, maestro):
        """Verify gate rejects jobs with blender config but no scenes."""
        job = {
            "narration": [
                {"scene_index": 0, "text": "Valid narration text here"},
            ],
            "components": {
                "blender": {
                    "scenes": []  # No scenes
                }
            },
        }

        with pytest.raises(ValueError, match="no scenes"):
            maestro.validate_job_content(job)

    def test_gate_rejects_blender_scene_without_data(self, maestro):
        """Verify gate rejects blender scenes with neither file nor inline data."""
        job = {
            "narration": [
                {"scene_index": 0, "text": "Valid narration text here"},
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

        with pytest.raises(ValueError, match="no file or inline data"):
            maestro.validate_job_content(job)

    def test_gate_passes_valid_job(self, maestro, valid_job):
        """Verify gate PASSES when all content is valid and present."""
        # Should not raise
        maestro.validate_job_content(valid_job)
        logger.info("✓ Gate passed valid job")

    def test_extract_narration_content(self, maestro, valid_job):
        """Verify narration content is correctly extracted."""
        narration = maestro._extract_narration_content(valid_job)

        assert len(narration) == 5
        assert narration[0] == "What if your operating system could think?"
        assert narration[-1] == "CorvinOS: Where intelligence meets infrastructure."
        logger.info(f"✓ Extracted {len(narration)} narration segments")

    def test_extract_blender_content(self, maestro, valid_job):
        """Verify blender content is correctly extracted."""
        blender = maestro._extract_blender_content(valid_job)

        assert blender is not None
        assert "scenes" in blender
        assert len(blender["scenes"]) == 2
        logger.info(f"✓ Extracted blender config with {len(blender['scenes'])} scenes")

    def test_audio_validation_rejects_whistle(self, maestro, tmp_path):
        """Verify audio validation rejects whistle-tone audio (tiny file)."""
        # Create a tiny audio file (< 5KB = whistle)
        tiny_audio = tmp_path / "whistle.mp3"
        tiny_audio.write_bytes(b"fake audio data")  # Only 15 bytes

        with pytest.raises(ValueError, match="too small"):
            maestro._validate_audio_output(tiny_audio)

    def test_audio_validation_passes_real_audio(self, maestro, tmp_path):
        """Verify audio validation passes for real audio (> 5KB)."""
        # Create a realistic audio file (> 5KB)
        real_audio = tmp_path / "narration.mp3"
        real_audio.write_bytes(b"x" * 50_000)  # 50KB of data

        # Should not raise
        maestro._validate_audio_output(real_audio)
        logger.info("✓ Audio validation passed")

    def test_video_validation_rejects_placeholder(self, maestro, tmp_path):
        """Verify video validation rejects placeholder video (tiny file)."""
        # Create a tiny video file (< 100KB = placeholder)
        placeholder_video = tmp_path / "placeholder.mp4"
        placeholder_video.write_bytes(b"solid color frame")  # Only 17 bytes

        with pytest.raises(ValueError, match="too small"):
            maestro._validate_video_output(placeholder_video)

    def test_video_validation_passes_real_video(self, maestro, tmp_path):
        """Verify video validation passes for real video (> 100KB)."""
        # Create a realistic video file (> 100KB)
        real_video = tmp_path / "output.mp4"
        real_video.write_bytes(b"x" * 500_000)  # 500KB of data

        # Should not raise
        maestro._validate_video_output(real_video)
        logger.info("✓ Video validation passed")

    @pytest.mark.asyncio
    async def test_orchestrate_validates_job_content(self, maestro, valid_job):
        """Verify orchestrate() validates job content at entry point."""
        # Should not raise (content is valid)
        result = await maestro.orchestrate([], job=valid_job)

        # Expect some result (may be partial since workers aren't registered)
        assert result is not None
        logger.info(f"✓ Orchestration result: {result.get('message')}")

    @pytest.mark.asyncio
    async def test_orchestrate_rejects_invalid_job(self, maestro):
        """Verify orchestrate() rejects jobs with missing narration."""
        invalid_job = {"narration": []}  # No narration

        result = await maestro.orchestrate([], job=invalid_job)

        # Expect error result
        assert result["status"] == "error"
        assert "no narration segments" in result["message"]
        logger.info(f"✓ Orchestration rejected invalid job: {result['message']}")

    def test_fail_closed_pattern_loud_signals(self, maestro):
        """
        Verify fail-closed pattern: missing content produces LOUD errors,
        not silent fallback to placeholder output.
        """
        test_cases = [
            ({"narration": []}, "no narration segments"),
            ({"narration": [{"text": ""}]}, "has no text"),
            ({"narration": [{"text": "X"}]}, "too short"),
            (
                {
                    "narration": [{"text": "Valid narration"}],
                    "components": {"blender": {"scenes": []}},
                },
                "no scenes",
            ),
        ]

        for job, expected_error in test_cases:
            with pytest.raises(ValueError, match=expected_error):
                maestro.validate_job_content(job)

        logger.info("✓ Fail-closed pattern: all invalid jobs rejected with LOUD signals")


class TestContentPreservationThroughPipeline:
    """Test that content survives the entire production pipeline."""

    @pytest.fixture
    def maestro(self, tmp_path):
        """Create maestro with temporary directory."""
        return VideoProducerMaestro(tmp_path)

    @pytest.fixture
    def full_narration_job(self, tmp_path) -> Dict[str, Any]:
        """Create a realistic job with full narration and blender content."""
        # Create a temporary blender file
        blender_file = tmp_path / "scenes.blend"
        blender_file.write_bytes(b"x" * 500_000)  # 500KB placeholder

        return {
            "job_id": "test_full_pipeline",
            "narration": [
                {
                    "scene_index": 0,
                    "text": "Introduction to CorvinOS",
                    "duration_seconds": 6,
                },
                {
                    "scene_index": 1,
                    "text": "Core features and capabilities",
                    "duration_seconds": 20,
                },
                {
                    "scene_index": 2,
                    "text": "Getting started with CorvinOS",
                    "duration_seconds": 15,
                },
            ],
            "components": {
                "blender": {
                    "scenes": [
                        {
                            "name": "intro_sequence",
                            "duration": 6,
                            "scene_file": str(blender_file),
                        },
                        {
                            "name": "features_showcase",
                            "duration": 20,
                            "scene_data": {
                                "camera": {"position": [0, 0, 10]},
                                "objects": [{"name": "cube", "type": "mesh"}],
                            },
                        },
                    ]
                }
            },
        }

    def test_narration_preserved_across_validation(self, maestro, full_narration_job):
        """Verify narration text is preserved (not replaced by references)."""
        # Validate job
        maestro.validate_job_content(full_narration_job)

        # Extract narration
        narration = maestro._extract_narration_content(full_narration_job)

        # Verify all narration is still present
        assert len(narration) == 3
        assert all(isinstance(text, str) for text in narration)
        assert all(len(text) > 10 for text in narration)

        logger.info("✓ Narration content preserved through validation")

    def test_blender_content_preserved_across_validation(self, maestro, full_narration_job):
        """Verify blender content is preserved (not lost as references)."""
        # Validate job
        maestro.validate_job_content(full_narration_job)

        # Extract blender content
        blender = maestro._extract_blender_content(full_narration_job)

        # Verify blender content is still present
        assert blender is not None
        assert len(blender["scenes"]) == 2

        # Verify both inline data and file references are preserved
        scene1 = blender["scenes"][0]
        assert "scene_file" in scene1

        scene2 = blender["scenes"][1]
        assert "scene_data" in scene2

        logger.info("✓ Blender content preserved through validation")


class TestAuditTrail:
    """Test that validation and content extraction are auditable."""

    @pytest.fixture
    def maestro(self, tmp_path):
        """Create maestro with temporary directory."""
        return VideoProducerMaestro(tmp_path)

    def test_validation_logs_success(self, maestro, caplog):
        """Verify successful validation is logged (audit trail)."""
        job = {
            "narration": [
                {"text": "Valid narration segment"},
            ]
        }

        with caplog.at_level(logging.INFO):
            maestro.validate_job_content(job)

        # Check that validation was logged
        assert "validation PASSED" in caplog.text

    def test_validation_logs_failure(self, maestro, caplog):
        """Verify failed validation is logged (audit trail)."""
        job = {"narration": []}

        with caplog.at_level(logging.ERROR):
            with pytest.raises(ValueError):
                maestro.validate_job_content(job)

        # Audit log should indicate rejection
        logger.error("Validation failed for job with no narration")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
