#!/usr/bin/env python3
"""Tests for `corvin-voice doctor` (ADR-0185 M5).

Two tiers, matching this directory's established convention (see
test_stt.py's ``LocalWhisperPywhispercppTests``):

  * Hermetic unit tests — CLI arg parsing, exit-code contract, cleanup
    behaviour — with the two round-trip checks monkeypatched out so they
    run fast and offline, no network/model required.
  * A REAL, non-mocked end-to-end test that actually runs the STT and TTS
    round-trips (and the full ``run_doctor()`` sequence) exactly as a
    human running `corvin-voice doctor` would. This is the actual point
    of ADR-0185 M5: this subsystem has broken silently before (a missing
    `import asyncio` in adapter.py swallowed every edge-tts failure into
    a log line for a long time), so a mocked test here would defeat the
    milestone's purpose. Skipped only when pywhispercpp truly isn't
    importable in the current environment — never faked.

Run standalone: `python3 test_voice_doctor.py`
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import voice_doctor  # noqa: E402


class ArgParsingTests(unittest.TestCase):
    def test_missing_subcommand_exits_nonzero(self):
        with self.assertRaises(SystemExit):
            voice_doctor.main([])

    def test_doctor_uses_default_timeout(self):
        with mock.patch.object(voice_doctor, "run_doctor", return_value=0) as m:
            rc = voice_doctor.main(["doctor"])
        self.assertEqual(rc, 0)
        m.assert_called_once_with(stt_timeout=voice_doctor._DEFAULT_STT_TIMEOUT_S)

    def test_doctor_timeout_override_is_parsed(self):
        with mock.patch.object(voice_doctor, "run_doctor", return_value=0) as m:
            voice_doctor.main(["doctor", "--stt-timeout", "5"])
        m.assert_called_once_with(stt_timeout=5.0)


class ExitCodeContractTests(unittest.TestCase):
    """run_doctor()'s exit code must reflect BOTH round-trips — hermetic
    (provider tables + round-trip checks are monkeypatched)."""

    def _run_with(self, stt_ok: bool, tts_ok: bool, tts_path=None) -> int:
        with mock.patch.object(voice_doctor, "_stt_provider_rows", return_value=[]), \
             mock.patch.object(voice_doctor, "_tts_provider_rows", return_value=[]), \
             mock.patch.object(voice_doctor, "_check_stt", return_value=(stt_ok, "stub")), \
             mock.patch.object(voice_doctor, "_check_tts", return_value=(tts_ok, "stub", tts_path)):
            return voice_doctor.run_doctor()

    def test_both_pass_is_overall_pass(self):
        self.assertEqual(self._run_with(True, True), 0)

    def test_stt_fail_is_overall_fail(self):
        self.assertEqual(self._run_with(False, True), 1)

    def test_tts_fail_is_overall_fail(self):
        self.assertEqual(self._run_with(True, False), 1)

    def test_both_fail_is_overall_fail(self):
        self.assertEqual(self._run_with(False, False), 1)

    def test_successful_tts_artifact_is_cleaned_up(self):
        fake_path = mock.MagicMock(spec=Path)
        rc = self._run_with(True, True, tts_path=fake_path)
        self.assertEqual(rc, 0)
        fake_path.unlink.assert_called_once()

    def test_cleanup_failure_does_not_change_exit_code(self):
        fake_path = mock.MagicMock(spec=Path)
        fake_path.unlink.side_effect = OSError("gone already")
        rc = self._run_with(True, True, tts_path=fake_path)
        self.assertEqual(rc, 0)


try:
    import pywhispercpp  # noqa: F401
    _PYWHISPERCPP_AVAILABLE = True
except ImportError:
    _PYWHISPERCPP_AVAILABLE = False


@unittest.skipUnless(
    _PYWHISPERCPP_AVAILABLE,
    "pywhispercpp not installed — real round-trip needs it (ADR-0185 M1 default local STT engine)",
)
class RealRoundTripTests(unittest.TestCase):
    """The actual point of ADR-0185 M5: a real, non-mocked STT+TTS round-trip.

    Requires network on first run (Whisper GGML model download + edge-tts
    reachability) — never faked, mirroring
    test_stt.py::LocalWhisperPywhispercppTests.
    """

    def test_stt_round_trip_returns_nonempty_text(self):
        ok, msg = voice_doctor._check_stt(timeout_s=180.0)
        self.assertTrue(ok, msg)

    def test_tts_round_trip_produces_nonzero_file(self):
        ok, msg, path = voice_doctor._check_tts("Automated test of corvin-voice doctor.")
        try:
            self.assertTrue(ok, msg)
            self.assertIsNotNone(path)
            self.assertGreater(path.stat().st_size, 0)
        finally:
            if path is not None:
                try:
                    path.unlink()
                except OSError:
                    pass

    def test_full_doctor_run_exits_zero(self):
        rc = voice_doctor.run_doctor()
        self.assertEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()
