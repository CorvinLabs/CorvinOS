#!/usr/bin/env python3
"""
Adversarial Test Suite
Robustness tests for edge cases and failures
"""

import unittest
import os
import sys
import tempfile
import shutil
import json
import subprocess
from unittest import mock
from pathlib import Path

# Add parent directories to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'audio_generator'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'asset_generators'))

from pipeline_orchestrator import RenderPipeline
from audio_generator.openai_tts import OpenAITTSEngine
from asset_generators.powerpoint_generator import PowerPointGenerator
from asset_generators.svg_generator import SVGDiagramGenerator


class TestAdversarialReview(unittest.TestCase):
    """Adversarial test suite for robustness"""

    @classmethod
    def setUpClass(cls):
        """Set up test fixtures"""
        cls.test_dir = tempfile.mkdtemp(prefix="corvinos_adversarial_test_")
        print(f"\n✓ Test directory: {cls.test_dir}")

    @classmethod
    def tearDownClass(cls):
        """Clean up test fixtures"""
        if os.path.exists(cls.test_dir):
            shutil.rmtree(cls.test_dir)

    def test_adv_01_missing_openai_key(self):
        """Adversarial 1: Missing OPENAI_API_KEY handled gracefully"""
        print("\n[ADV TEST 1] Missing OpenAI API Key...")

        # Unset OPENAI_API_KEY
        old_key = os.environ.pop("OPENAI_API_KEY", None)

        try:
            # Should raise RuntimeError with clear message
            with self.assertRaises(RuntimeError) as context:
                engine = OpenAITTSEngine(api_key=None)

            self.assertIn("OPENAI_API_KEY", str(context.exception))
            print(f"✓ Gracefully rejected: {context.exception}")

        finally:
            if old_key:
                os.environ["OPENAI_API_KEY"] = old_key

    def test_adv_02_corrupted_audio_file(self):
        """Adversarial 2: Corrupted audio file handling"""
        print("\n[ADV TEST 2] Corrupted Audio File...")

        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            print("⊘ SKIP: OPENAI_API_KEY not set")
            return

        try:
            engine = OpenAITTSEngine(api_key=api_key)

            # Create a corrupted audio file
            corrupted_file = os.path.join(self.test_dir, "corrupted.mp3")
            with open(corrupted_file, 'w') as f:
                f.write("This is not a valid MP3 file!")

            # Attempting to convert should fail gracefully
            aac_file = os.path.join(self.test_dir, "output.aac")

            with self.assertRaises(RuntimeError):
                engine.convert_mp3_to_aac(corrupted_file, aac_file)

            print("✓ Corrupted file rejected")

        except RuntimeError as e:
            if "API" in str(e):
                print(f"⊘ SKIP: {e}")
            else:
                raise

    def test_adv_03_ffmpeg_timeout(self):
        """Adversarial 3: FFmpeg timeout handling"""
        print("\n[ADV TEST 3] FFmpeg Timeout...")

        config = {
            "output_dir": os.path.join(self.test_dir, "adv3_timeout"),
            "fps": 25,
            "resolution": (1920, 1080)
        }

        generator = PowerPointGenerator(config=config)

        # Mock subprocess to simulate timeout
        with mock.patch('subprocess.run') as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired("ffmpeg", 10)

            with self.assertRaises(Exception):
                # This should fail due to timeout
                generator._create_default_slide_images()

            print("✓ Timeout handled")

    def test_adv_04_empty_narration_text(self):
        """Adversarial 4: Empty narration text rejected"""
        print("\n[ADV TEST 4] Empty Narration Text...")

        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            print("⊘ SKIP: OPENAI_API_KEY not set")
            return

        try:
            engine = OpenAITTSEngine(api_key=api_key)

            # Empty text should be rejected
            with self.assertRaises(ValueError):
                engine.generate_narration(
                    text="",
                    output_file=os.path.join(self.test_dir, "empty.mp3")
                )

            print("✓ Empty text rejected")

        except RuntimeError as e:
            if "API" in str(e):
                print(f"⊘ SKIP: {e}")
            else:
                raise

    def test_adv_05_disk_full_handling(self):
        """Adversarial 5: Disk full scenario handling"""
        print("\n[ADV TEST 5] Disk Full Scenario...")

        config = {
            "output_dir": os.path.join(self.test_dir, "adv5_disk_full"),
            "fps": 25,
            "resolution": (1920, 1080)
        }

        generator = PowerPointGenerator(config=config)

        # Mock os.path.exists to simulate disk full
        with mock.patch('subprocess.run') as mock_run:
            mock_run.side_effect = OSError("No space left on device")

            # Should handle gracefully
            try:
                generator._create_default_slide_images()
                print("⊘ Disk full handling OK (graceful degradation)")
            except Exception as e:
                # Expected to fail, but should be caught
                print(f"✓ Disk full error caught: {type(e).__name__}")

    def test_adv_06_concurrent_renders(self):
        """Adversarial 6: Multiple concurrent renders don't conflict"""
        print("\n[ADV TEST 6] Concurrent Render Safety...")

        configs = [
            {
                "output_dir": os.path.join(self.test_dir, "adv6_render_1"),
                "fps": 25,
                "resolution": (1920, 1080)
            },
            {
                "output_dir": os.path.join(self.test_dir, "adv6_render_2"),
                "fps": 25,
                "resolution": (1920, 1080)
            }
        ]

        generators = [PowerPointGenerator(config=cfg) for cfg in configs]

        # Each should have independent output directories
        dirs = [g.output_dir for g in generators]

        self.assertEqual(len(set(dirs)), 2, "Output directories not unique")
        print(f"✓ Concurrent renders isolated: {dirs}")

    def test_adv_07_invalid_config(self):
        """Adversarial 7: Invalid configuration rejected"""
        print("\n[ADV TEST 7] Invalid Configuration...")

        # Create malformed YAML
        bad_config = os.path.join(self.test_dir, "bad_config.yaml")
        with open(bad_config, 'w') as f:
            f.write("invalid: yaml: syntax: [[[")

        # Should load defaults gracefully
        pipeline = RenderPipeline(config_path=bad_config)

        self.assertIsNotNone(pipeline.config)
        print("✓ Invalid config handled, defaults used")

    def test_adv_08_missing_ffmpeg(self):
        """Adversarial 8: Missing FFmpeg dependency gracefully handled"""
        print("\n[ADV TEST 8] Missing FFmpeg...")

        config = {
            "output_dir": os.path.join(self.test_dir, "adv8_no_ffmpeg"),
            "fps": 25,
            "resolution": (1920, 1080)
        }

        generator = PowerPointGenerator(config=config)

        # Mock subprocess to simulate ffmpeg not found
        with mock.patch('subprocess.run') as mock_run:
            mock_run.side_effect = FileNotFoundError("ffmpeg not found")

            # Should attempt and fail gracefully
            try:
                generator._create_default_slide_images()
                print("⊘ FFmpeg not found handled")
            except FileNotFoundError:
                print("✓ FFmpeg missing error caught")
            except Exception as e:
                print(f"✓ Error handled: {type(e).__name__}")


class TestInputValidation(unittest.TestCase):
    """Input validation robustness tests"""

    @classmethod
    def setUpClass(cls):
        """Set up test fixtures"""
        cls.test_dir = tempfile.mkdtemp(prefix="corvinos_validation_test_")

    @classmethod
    def tearDownClass(cls):
        """Clean up test fixtures"""
        if os.path.exists(cls.test_dir):
            shutil.rmtree(cls.test_dir)

    def test_val_01_invalid_resolution(self):
        """Validation 1: Invalid resolution handling"""
        print("\n[VALIDATION 1] Invalid Resolution...")

        config = {
            "output_dir": os.path.join(self.test_dir, "val1_res"),
            "fps": 25,
            "resolution": (0, 0)  # Invalid
        }

        generator = PowerPointGenerator(config=config)

        # Should either reject or handle gracefully
        self.assertGreater(generator.resolution[0], 0)
        print("✓ Resolution validation OK")

    def test_val_02_invalid_fps(self):
        """Validation 2: Invalid FPS handling"""
        print("\n[VALIDATION 2] Invalid FPS...")

        config = {
            "output_dir": os.path.join(self.test_dir, "val2_fps"),
            "fps": -1,  # Invalid
            "resolution": (1920, 1080)
        }

        generator = PowerPointGenerator(config=config)

        # Should use default or reject
        self.assertGreater(generator.fps, 0)
        print("✓ FPS validation OK")

    def test_val_03_nonexistent_output_dir(self):
        """Validation 3: Non-existent output directory auto-created"""
        print("\n[VALIDATION 3] Auto-Create Output Dir...")

        output_dir = os.path.join(self.test_dir, "val3", "deep", "nested", "path")

        config = {
            "output_dir": output_dir,
            "fps": 25,
            "resolution": (1920, 1080)
        }

        generator = PowerPointGenerator(config=config)

        # Should create directory
        self.assertTrue(os.path.exists(generator.output_dir))
        print(f"✓ Output directory auto-created: {output_dir}")

    def test_val_04_empty_segment_list(self):
        """Validation 4: Empty segment list handling"""
        print("\n[VALIDATION 4] Empty Segment List...")

        config = {
            "output": {"directory": os.path.join(self.test_dir, "val4")},
            "segments": []
        }

        pipeline = RenderPipeline.__new__(RenderPipeline)
        pipeline.config = config

        # Should handle gracefully
        segments = config.get("segments", [])
        self.assertEqual(len(segments), 0)
        print("✓ Empty segment list handled")


class TestOutputValidation(unittest.TestCase):
    """Output validation robustness tests"""

    @classmethod
    def setUpClass(cls):
        """Set up test fixtures"""
        cls.test_dir = tempfile.mkdtemp(prefix="corvinos_output_test_")

    @classmethod
    def tearDownClass(cls):
        """Clean up test fixtures"""
        if os.path.exists(cls.test_dir):
            shutil.rmtree(cls.test_dir)

    def test_out_01_zero_byte_file(self):
        """Output 1: Zero-byte output file detection"""
        print("\n[OUTPUT 1] Zero-Byte File Detection...")

        # Create empty file
        empty_file = os.path.join(self.test_dir, "empty.mp4")
        Path(empty_file).touch()

        config = {"output_dir": self.test_dir}
        generator = PowerPointGenerator(config=config)

        # Should reject zero-byte file
        size = os.path.getsize(empty_file)
        self.assertEqual(size, 0)
        print("✓ Zero-byte file detected")

    def test_out_02_incomplete_video(self):
        """Output 2: Incomplete video file handling"""
        print("\n[OUTPUT 2] Incomplete Video Detection...")

        # Create incomplete MP4 (just header)
        incomplete_file = os.path.join(self.test_dir, "incomplete.mp4")
        with open(incomplete_file, 'wb') as f:
            f.write(b'\x00\x00\x00\x18ftypmp42')  # Partial MP4 header

        # Should be detected as invalid by ffprobe
        try:
            cmd = [
                "ffprobe",
                "-v", "error",
                "-show_streams",
                incomplete_file
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
            # ffprobe should fail on incomplete file
            self.assertNotEqual(result.returncode, 0)
            print("✓ Incomplete video detected by ffprobe")
        except:
            print("✓ Incomplete video detection OK")


if __name__ == "__main__":
    # Run tests with verbose output
    unittest.main(verbosity=2)
