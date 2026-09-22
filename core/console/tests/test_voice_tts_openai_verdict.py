"""A tier that CANNOT work must not be reported as ``ready`` — and must not be
asked twice per call.

Measured on this install 2026-09-22, with a valid key configured and OpenAI as
tier 1 of the auto-chain:

* every request to ``api.openai.com/v1/audio/speech`` returns ``403`` from the
  corporate TLS proxy (``Server: Zscaler/6.2``, category "Generative AI and ML
  Applications") — the request never reaches OpenAI, so the key is never even
  validated;
* ``GET /v1/console/voice/status`` nevertheless reported
  ``openai: {"ready": true, "detail": "ready"}``, because ``say.py``'s
  ``provider_status()`` is documented cheap introspection (key present + package
  importable) and deliberately never synthesizes. So the panel asserted a paid
  tier was usable while the console silently spoke with edge
  (``x-corvin-tts-provider: say.py:edge``);
* the blocked round-trip was paid TWICE per TTS call — once in-process, then
  again inside the ``say.py`` subprocess, which re-derives its own configuration
  and had no way to learn what the console had just measured.

The fix records the OUTCOME the console actually observed and lets both surfaces
read it. Recording an observation is not the same as a policy switch, which is
why it expires: a granted proxy exception must take effect without a restart.
"""
from __future__ import annotations

import subprocess
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

_THIS = Path(__file__).resolve()
_REPO = _THIS.parents[3]
for p in ("core/console", "corvin_operator/bridges/shared", "corvin_operator/forge",
          "corvin_operator/voice/scripts"):
    sys.path.insert(0, str(_REPO / p))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import corvin_console.routes.voice as voice  # noqa: E402


def _app() -> FastAPI:
    app = FastAPI()
    app.include_router(voice.router, prefix="/v1/console")
    return app


class _VerdictTestBase(unittest.TestCase):
    """Every test starts with no observation on record."""

    def setUp(self) -> None:
        voice._openai_tts_forget_verdict()
        self.addCleanup(voice._openai_tts_forget_verdict)

    def _client(self) -> TestClient:
        rec = MagicMock()
        rec.tenant_id = "_default"
        rec.sid_fingerprint = "fp"
        p = patch.object(voice.session_auth, "load_session", return_value=rec)
        p.start()
        self.addCleanup(p.stop)
        c = TestClient(_app())
        c.cookies.set("corvin_console_sid", "valid-sid")
        return c

    def _status_openai(self) -> dict:
        """The tts.openai row as the Voice settings page receives it."""
        # say.py's real provider_status() is cheap introspection; stub it to the
        # exact shape this install returns so the test is about the OVERRIDE.
        raw = {
            "openai": {"ready": True, "package_installed": True,
                       "model_present": None, "key_configured": True,
                       "detail": "ready"},
            "edge": {"ready": True, "package_installed": True,
                     "model_present": None, "key_configured": None,
                     "detail": "ready (needs internet at synth time)"},
        }
        fake_say = MagicMock()
        fake_say.provider_status.return_value = raw
        with (
            patch.object(voice, "_SAY_STATUS_OK", True),
            patch.object(voice, "_say_module", fake_say),
        ):
            r = self._client().get("/v1/console/voice/status")
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()["tts"]["openai"]


class TestTheStatusPanelStopsClaimingReady(_VerdictTestBase):
    def test_without_an_observation_the_row_is_passed_through_unchanged(self) -> None:
        """No evidence either way — do not invent a failure."""
        row = self._status_openai()
        self.assertTrue(row["ready"])
        self.assertEqual(row["detail"], "ready")

    def test_a_definitive_failure_makes_the_row_honest(self) -> None:
        """The reported defect: 403 measured, panel still said "ready"."""
        voice._openai_tts_record_failure(
            exc_type="PermissionDeniedError", status="403",
            host="api.openai.com", model="tts-1")
        row = self._status_openai()
        self.assertFalse(
            row["ready"],
            "a tier that just returned 403 is not 'usable right now' — this is "
            "the claim that sent the operator looking for a Corvin bug",
        )
        self.assertIn("403", row["detail"])
        self.assertIn("api.openai.com", row["detail"])

    def test_the_detail_line_stays_content_free(self) -> None:
        """Same compliance rule as the log line it replaces: no key, no spoken
        text, no ``str(e)`` (which can embed the request payload)."""
        voice._openai_tts_record_failure(
            exc_type="PermissionDeniedError", status="403",
            host="api.openai.com", model="tts-1")
        detail = self._status_openai()["detail"]
        for forbidden in ("sk-", "Bearer", "Kurzer Test", "input="):
            self.assertNotIn(forbidden, detail, detail)

    def test_a_success_clears_the_verdict(self) -> None:
        """An approved endpoint going live must flip the panel back with no
        restart — otherwise the honesty fix becomes its own stale claim."""
        voice._openai_tts_record_failure(
            exc_type="PermissionDeniedError", status="403",
            host="api.openai.com", model="tts-1")
        self.assertFalse(self._status_openai()["ready"])
        voice._openai_tts_record_success()
        row = self._status_openai()
        self.assertTrue(row["ready"])
        self.assertEqual(row["detail"], "ready")

    def test_a_stale_verdict_is_ignored(self) -> None:
        """The observation expires. It is evidence, not configuration."""
        voice._openai_tts_record_failure(
            exc_type="PermissionDeniedError", status="403",
            host="api.openai.com", model="tts-1")
        with patch.object(voice, "_OPENAI_TTS_VERDICT_TTL_S", 0.0):
            self.assertTrue(
                self._status_openai()["ready"],
                "an expired observation must not keep a tier marked dead",
            )


class TestTheSubprocessIsNotAskedTwice(_VerdictTestBase):
    def test_a_fresh_definitive_verdict_is_handed_to_say_py(self) -> None:
        voice._openai_tts_record_failure(
            exc_type="PermissionDeniedError", status="403",
            host="api.openai.com", model="tts-1")
        self.assertEqual(voice._say_env().get("CORVIN_TTS_OPENAI_UNREACHABLE"), "1")

    def test_no_verdict_means_no_hint(self) -> None:
        """Default behaviour is untouched: say.py still tries OpenAI first."""
        self.assertIsNone(voice._say_env().get("CORVIN_TTS_OPENAI_UNREACHABLE"))

    def test_a_transient_failure_does_not_suppress_the_tier(self) -> None:
        """``APIConnectionError`` (no HTTP status) is a blip, not a verdict —
        suppressing OpenAI on one dropped connection would silently downgrade a
        working paid tier."""
        voice._openai_tts_record_failure(
            exc_type="APIConnectionError", status="",
            host="api.openai.com", model="tts-1")
        self.assertIsNone(voice._say_env().get("CORVIN_TTS_OPENAI_UNREACHABLE"))
        self.assertTrue(
            self._status_openai()["ready"],
            "a transient connection error is not proof the tier is unusable",
        )

    def test_a_stale_verdict_lets_the_subprocess_retry(self) -> None:
        voice._openai_tts_record_failure(
            exc_type="PermissionDeniedError", status="403",
            host="api.openai.com", model="tts-1")
        with patch.object(voice, "_OPENAI_TTS_VERDICT_TTL_S", 0.0):
            self.assertIsNone(voice._say_env().get("CORVIN_TTS_OPENAI_UNREACHABLE"))

    def test_the_ttl_is_bounded(self) -> None:
        """A hint that never expires is a permanent pin wearing a hint's name."""
        self.assertGreater(voice._OPENAI_TTS_VERDICT_TTL_S, 0)
        self.assertLessEqual(
            voice._OPENAI_TTS_VERDICT_TTL_S, 3600,
            "an observation older than an hour must not still gate a tier",
        )

    def test_an_explicit_operator_export_is_never_clobbered(self) -> None:
        """Same contract as every other name _say_env fills."""
        with patch.dict("os.environ", {"CORVIN_TTS_OPENAI_UNREACHABLE": "0"}):
            voice._openai_tts_record_failure(
                exc_type="PermissionDeniedError", status="403",
                host="api.openai.com", model="tts-1")
            self.assertEqual(
                voice._say_env()["CORVIN_TTS_OPENAI_UNREACHABLE"], "0")


class TestSayPyHonoursTheHint(unittest.TestCase):
    """The other half, driven as a real subprocess — the console's hint is
    worthless if the child ignores it.

    Hermetic by construction, and deliberately so: ``conftest``'s autouse
    ``CORVIN_TTS_LOCAL_ONLY=1`` exists because a route test once made a LIVE
    billable OpenAI call out of pytest, and the openai tier cannot be exercised
    at all while it is set. So instead of dropping that guard and hoping, these
    runs remove every path to a real key — ``VOICE_CONFIG_DIR`` points at an
    empty directory (the host's ``service.env`` holds a real key on this box)
    and all three candidate env vars are cleared — and PIN the openai tier with
    ``CORVIN_SAY_NO_FALLBACK``, so exactly one tier is evaluated and neither edge
    nor piper is contacted either. With no key resolvable there is no request to
    make: the observable difference between the two cases is *how far into the
    tier say.py got*, which is precisely what the hint changes.
    """

    _SAY = _REPO / "corvin_operator" / "voice" / "scripts" / "say.py"
    _NO_KEY = ("CORVIN_TTS_OPENAI_KEY", "OPENAI_API_KEY", "OPENAI_APIKEY")

    def _run(self, env_extra: dict[str, str]) -> tuple[int, str]:
        import os
        import tempfile

        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "utf-8"
        env.pop("CORVIN_TTS_LOCAL_ONLY", None)   # or openai never runs at all
        env["CORVIN_TTS_PROVIDER"] = "openai"    # one tier, not the chain
        env["CORVIN_SAY_NO_FALLBACK"] = "1"      # and no fall-through to edge
        for name in self._NO_KEY:
            env.pop(name, None)
        with tempfile.TemporaryDirectory() as td:
            env["VOICE_CONFIG_DIR"] = td         # no service.env → no real key
            env.update(env_extra)
            out = Path(td) / "out.bin"
            proc = subprocess.run(
                [sys.executable, str(self._SAY), str(out), "Kurzer Test.", "de", ""],
                capture_output=True, text=True, encoding="utf-8",
                errors="replace", env=env, timeout=120,
            )
        return proc.returncode, (proc.stderr or "")

    def test_the_openai_tier_is_skipped_and_says_why(self) -> None:
        """With a key configured and the hint set, the tier is not entered."""
        rc, err = self._run({
            "CORVIN_TTS_OPENAI_UNREACHABLE": "1",
            "CORVIN_TTS_OPENAI_KEY": "sk-test-not-a-real-key",
        })
        self.assertEqual(rc, 0, err)
        self.assertIn("openai", err.lower(), err)
        self.assertIn("unreachable", err.lower(), err)
        self.assertNotIn(
            "OpenAI TTS failed", err,
            "the tier must not be attempted at all — the point is to stop paying "
            f"a blocked round-trip the console already measured. stderr={err!r}",
        )
        self.assertNotIn(
            "no OPENAI_API_KEY", err,
            "the skip must come BEFORE key resolution; reaching the key check "
            f"means the hint was read too late to save anything. stderr={err!r}",
        )

    def test_without_the_hint_the_tier_is_entered_as_before(self) -> None:
        """Guards the default path: the hint is opt-in, per call.

        Same run with no hint and no key gets all the way to key resolution —
        the step immediately after the skip — which is what proves the tier is
        still reached rather than short-circuited by something ambient.
        """
        rc, err = self._run({})
        self.assertEqual(rc, 0, err)
        self.assertIn(
            "no OPENAI_API_KEY", err,
            f"the openai tier was not even entered; stderr={err!r}",
        )
        self.assertNotIn("unreachable", err.lower(), err)


if __name__ == "__main__":
    unittest.main()
