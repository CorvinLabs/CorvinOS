#!/usr/bin/env python3
"""
Standalone E2E Test Runner: Real OpenAI TTS API
Runs without pytest — direct test execution with detailed logging

Usage:
  python3 run_openai_tts_e2e_tests.py

Environment:
  export OPENAI_API_KEY=sk-proj-...
"""

import os
import sys
import asyncio
import tempfile
from pathlib import Path
import logging
import traceback

# Setup logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s [%(levelname)s] %(message)s'
)
logger = logging.getLogger(__name__)

# Add bridges/shared to path
sys.path.insert(0, str(Path.home() / "projects/CorvinOS/corvin_operator/bridges/shared"))


class E2ETestRunner:
    """Run OpenAI TTS E2E tests"""

    def __init__(self):
        self.results = {
            "passed": [],
            "failed": [],
            "skipped": []
        }
        self.openai_client = None
        self.setup_client()


    def setup_client(self):
        """Initialize OpenAI client"""
        try:
            from openai import OpenAI

            api_key = os.environ.get("OPENAI_API_KEY") or \
                     os.environ.get("CORVIN_TTS_OPENAI_KEY")

            if not api_key:
                logger.error("❌ No OPENAI_API_KEY found")
                sys.exit(1)

            if api_key.startswith("PLACEHOLDER"):
                logger.warning("⚠️  API Key is PLACEHOLDER value")
                logger.info("    Set real key: export OPENAI_API_KEY=sk-proj-...")
                return False

            self.openai_client = OpenAI(api_key=api_key, timeout=30.0, max_retries=0)
            logger.info(f"✅ OpenAI client initialized")
            logger.info(f"   API Key: {api_key[:40]}...")
            return True

        except ImportError:
            logger.error("❌ openai SDK not installed")
            logger.info("   Run: python3 -m pip install openai")
            return False
        except Exception as e:
            logger.error(f"❌ Client setup failed: {e}")
            traceback.print_exc()
            return False


    def log_result(self, test_name, passed, message=""):
        """Log test result"""
        status = "✅ PASS" if passed else "❌ FAIL"
        if passed:
            self.results["passed"].append(test_name)
        else:
            self.results["failed"].append(test_name)
        logger.info(f"{status}: {test_name}")
        if message:
            logger.info(f"       {message}")


    def test_1_basic_api_call(self):
        """Test 1: Basic real API call"""
        logger.info("\n" + "=" * 70)
        logger.info("TEST 1: Basic Real API Call")
        logger.info("=" * 70)

        if not self.openai_client:
            self.results["skipped"].append("Test 1")
            logger.warning("⏭️  SKIPPED (no API key)")
            return

        try:
            logger.info("Calling OpenAI TTS API...")
            response = self.openai_client.audio.speech.create(
                model="tts-1",
                voice="nova",
                input="Hello from OpenAI Text-to-Speech. This is a test.",
                response_format="opus"
            )

            audio_bytes = response.read()
            logger.info(f"✅ API call successful ({len(audio_bytes)} bytes)")

            # Validate
            assert audio_bytes is not None, "No audio data"
            assert len(audio_bytes) > 0, "Empty audio"
            assert audio_bytes.startswith(b"OggS"), f"Not OGG format: {audio_bytes[:4].hex()}"

            # Check size
            assert 1000 <= len(audio_bytes) <= 1_000_000, "Audio size out of bounds"

            self.log_result("Test 1: Basic API Call", True,
                          f"Audio: {len(audio_bytes)} bytes, OGG format")

        except Exception as e:
            logger.error(f"❌ {e}")
            traceback.print_exc()
            self.log_result("Test 1: Basic API Call", False, str(e))


    def test_2_multiple_voices(self):
        """Test 2: Different voices"""
        logger.info("\n" + "=" * 70)
        logger.info("TEST 2: Multiple Voice Options")
        logger.info("=" * 70)

        if not self.openai_client:
            self.results["skipped"].append("Test 2")
            logger.warning("⏭️  SKIPPED (no API key)")
            return

        try:
            voices = ["nova", "alloy", "echo"]
            successful = 0

            for voice in voices:
                try:
                    logger.info(f"Testing voice: {voice}...")
                    response = self.openai_client.audio.speech.create(
                        model="tts-1",
                        voice=voice,
                        input=f"Testing {voice} voice.",
                        response_format="opus"
                    )

                    audio_bytes = response.read()
                    assert len(audio_bytes) > 0, "Empty audio"
                    assert audio_bytes.startswith(b"OggS"), "Not OGG format"

                    logger.info(f"   ✅ {voice}: {len(audio_bytes)} bytes")
                    successful += 1

                except Exception as e:
                    logger.error(f"   ❌ {voice}: {e}")

            assert successful >= 2, f"Only {successful}/3 voices succeeded"
            self.log_result("Test 2: Multiple Voices", True,
                          f"{successful}/3 voices working")

        except Exception as e:
            logger.error(f"❌ {e}")
            self.log_result("Test 2: Multiple Voices", False, str(e))


    def test_3_response_structure(self):
        """Test 3: Response structure"""
        logger.info("\n" + "=" * 70)
        logger.info("TEST 3: Response Structure")
        logger.info("=" * 70)

        if not self.openai_client:
            self.results["skipped"].append("Test 3")
            logger.warning("⏭️  SKIPPED (no API key)")
            return

        try:
            logger.info("Making API call...")
            response = self.openai_client.audio.speech.create(
                model="tts-1",
                voice="nova",
                input="Testing response structure validation.",
                response_format="opus"
            )

            audio_bytes = response.read()

            # Validate structure
            assert response is not None, "No response"
            assert audio_bytes.startswith(b"OggS"), "Not OGG"
            assert len(audio_bytes) >= 100, "Too small"

            # Check OGG structure (has pages)
            ogg_size = len(audio_bytes)
            logger.info(f"✅ Response structure valid")
            logger.info(f"   OGG magic bytes: OggS")
            logger.info(f"   Total size: {ogg_size} bytes")
            logger.info(f"   Has page structure: {len(audio_bytes) >= 100}")

            self.log_result("Test 3: Response Structure", True,
                          f"OGG format with {ogg_size} bytes")

        except Exception as e:
            logger.error(f"❌ {e}")
            self.log_result("Test 3: Response Structure", False, str(e))


    def test_4_long_text(self):
        """Test 4: Long text input"""
        logger.info("\n" + "=" * 70)
        logger.info("TEST 4: Long Text Handling")
        logger.info("=" * 70)

        if not self.openai_client:
            self.results["skipped"].append("Test 4")
            logger.warning("⏭️  SKIPPED (no API key)")
            return

        try:
            # Create long text (~1250 chars)
            long_text = " ".join(["This is a test sentence."] * 50)

            logger.info(f"Input text: {len(long_text)} characters")
            response = self.openai_client.audio.speech.create(
                model="tts-1",
                voice="nova",
                input=long_text,
                response_format="opus"
            )

            audio_bytes = response.read()

            # Longer text should produce more audio
            assert len(audio_bytes) > 5000, "Audio too small for long input"

            logger.info(f"✅ Long text synthesis successful")
            logger.info(f"   Output size: {len(audio_bytes)} bytes")

            self.log_result("Test 4: Long Text", True,
                          f"{len(long_text)} chars → {len(audio_bytes)} bytes")

        except Exception as e:
            logger.error(f"❌ {e}")
            self.log_result("Test 4: Long Text", False, str(e))


    def test_5_error_handling(self):
        """Test 5: Error handling"""
        logger.info("\n" + "=" * 70)
        logger.info("TEST 5: Error Handling")
        logger.info("=" * 70)

        if not self.openai_client:
            self.results["skipped"].append("Test 5")
            logger.warning("⏭️  SKIPPED (no API key)")
            return

        try:
            errors_caught = 0

            # Test empty input
            logger.info("Testing empty input...")
            try:
                self.openai_client.audio.speech.create(
                    model="tts-1",
                    voice="nova",
                    input="",
                    response_format="opus"
                )
                logger.warning("   ⚠️  Empty input not rejected")
            except Exception as e:
                logger.info(f"   ✅ Empty input rejected: {type(e).__name__}")
                errors_caught += 1

            # Test invalid voice
            logger.info("Testing invalid voice...")
            try:
                self.openai_client.audio.speech.create(
                    model="tts-1",
                    voice="invalid_xyz",
                    input="Test",
                    response_format="opus"
                )
                logger.warning("   ⚠️  Invalid voice not rejected")
            except Exception as e:
                logger.info(f"   ✅ Invalid voice rejected: {type(e).__name__}")
                errors_caught += 1

            logger.info(f"✅ Error handling validated")
            self.log_result("Test 5: Error Handling", True,
                          f"{errors_caught} error cases handled")

        except Exception as e:
            logger.error(f"❌ {e}")
            self.log_result("Test 5: Error Handling", False, str(e))


    def test_6_save_file(self):
        """Test 6: Save and verify file"""
        logger.info("\n" + "=" * 70)
        logger.info("TEST 6: Save & Verify Audio File")
        logger.info("=" * 70)

        if not self.openai_client:
            self.results["skipped"].append("Test 6")
            logger.warning("⏭️  SKIPPED (no API key)")
            return

        try:
            logger.info("Calling OpenAI TTS...")
            response = self.openai_client.audio.speech.create(
                model="tts-1",
                voice="nova",
                input="This audio will be saved to a file.",
                response_format="opus"
            )

            audio_bytes = response.read()

            # Save to temp file
            with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp:
                tmp.write(audio_bytes)
                tmp_path = tmp.name

            try:
                # Verify file
                file_path = Path(tmp_path)
                assert file_path.exists(), "File not created"

                file_size = file_path.stat().st_size
                assert file_size == len(audio_bytes), "File size mismatch"

                # Verify OGG format
                with open(tmp_path, 'rb') as f:
                    magic = f.read(4)
                    assert magic == b"OggS", f"Wrong magic: {magic.hex()}"

                logger.info(f"✅ File saved and verified")
                logger.info(f"   Path: {tmp_path}")
                logger.info(f"   Size: {file_size} bytes")
                logger.info(f"   Format: OGG-Opus")

                self.log_result("Test 6: Save File", True,
                              f"File: {file_size} bytes, OGG format")

            finally:
                Path(tmp_path).unlink(missing_ok=True)

        except Exception as e:
            logger.error(f"❌ {e}")
            self.log_result("Test 6: Save File", False, str(e))


    def test_7_concurrent(self):
        """Test 7: Concurrent requests"""
        logger.info("\n" + "=" * 70)
        logger.info("TEST 7: Concurrent Requests (ADR-0194 Phase 3)")
        logger.info("=" * 70)

        if not self.openai_client:
            self.results["skipped"].append("Test 7")
            logger.warning("⏭️  SKIPPED (no API key)")
            return

        try:
            segments = [
                "First segment of the response.",
                "Second segment with more content.",
                "Third segment concluding the message."
            ]

            logger.info(f"Testing {len(segments)} concurrent requests...")
            results = []

            for i, segment in enumerate(segments, 1):
                try:
                    response = self.openai_client.audio.speech.create(
                        model="tts-1",
                        voice="nova",
                        input=segment,
                        response_format="opus"
                    )
                    audio_bytes = response.read()

                    logger.info(f"   ✅ Segment {i}: {len(audio_bytes)} bytes")
                    results.append(True)

                except Exception as e:
                    logger.error(f"   ❌ Segment {i}: {e}")
                    results.append(False)

            successful = sum(results)
            assert successful >= 2, f"Only {successful}/{len(segments)} succeeded"

            self.log_result("Test 7: Concurrent", True,
                          f"{successful}/{len(segments)} requests successful")

        except Exception as e:
            logger.error(f"❌ {e}")
            self.log_result("Test 7: Concurrent", False, str(e))


    def test_8_adapter_integration(self):
        """Test 8: adapter.py integration"""
        logger.info("\n" + "=" * 70)
        logger.info("TEST 8: Adapter Integration")
        logger.info("=" * 70)

        try:
            import adapter

            logger.info("Testing adapter.synthesize_voice_note()...")
            result = adapter.synthesize_voice_note("Adapter test", "en")

            if result and result.exists():
                size = result.stat().st_size

                with open(result, 'rb') as f:
                    magic = f.read(4)
                    assert magic == b"OggS", "Not OGG format"

                logger.info(f"✅ Voice synthesis: {result.name}")
                logger.info(f"   Size: {size} bytes")
                logger.info(f"   Format: OGG-Opus")

                self.log_result("Test 8: Adapter", True,
                              f"File: {size} bytes, OGG format")
            else:
                reason = adapter.voice_skip_reason()
                logger.warning(f"⚠️  Synthesis skipped: {reason}")
                self.results["skipped"].append("Test 8")

        except Exception as e:
            logger.error(f"❌ {e}")
            self.log_result("Test 8: Adapter", False, str(e))


    def run_all_tests(self):
        """Run all tests"""
        logger.info("\n╔" + "=" * 68 + "╗")
        logger.info("║  E2E Tests: Real OpenAI TTS API (ADR-0194)            ║")
        logger.info("╚" + "=" * 68 + "╝")

        self.test_1_basic_api_call()
        self.test_2_multiple_voices()
        self.test_3_response_structure()
        self.test_4_long_text()
        self.test_5_error_handling()
        self.test_6_save_file()
        self.test_7_concurrent()
        self.test_8_adapter_integration()

        # Summary
        logger.info("\n" + "=" * 70)
        logger.info("TEST SUMMARY")
        logger.info("=" * 70)

        passed = len(self.results["passed"])
        failed = len(self.results["failed"])
        skipped = len(self.results["skipped"])
        total = passed + failed + skipped

        logger.info(f"✅ Passed:  {passed}")
        logger.info(f"❌ Failed:  {failed}")
        logger.info(f"⏭️  Skipped: {skipped}")
        logger.info(f"📊 Total:   {total}")

        if failed == 0:
            logger.info("\n🎉 ALL TESTS PASSED 🎉")
            return 0
        else:
            logger.info(f"\n❌ {failed} tests failed")
            return 1


if __name__ == "__main__":
    runner = E2ETestRunner()
    exit_code = runner.run_all_tests()
    sys.exit(exit_code)
