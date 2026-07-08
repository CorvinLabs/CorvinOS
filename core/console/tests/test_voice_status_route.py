"""Per-subtask E2E for the Voice provider-status endpoint (ADR-0185 M4).

Covers:
  * ``GET /v1/console/voice/status`` — 200, per-provider STT+TTS status
    shape reflecting real states: all-ready, package-missing,
    model-missing, key-missing (mocked/monkeypatched — no real model
    download or network call).
  * 401 without a session.
  * ``routes.voice._stt_unavailable_message()`` — the translated,
    actionable message that replaced the raw resolver exception text
    surfaced into the chat transcript (ADR-0185 Decision 4 / Must-NOT):
    it must never contain the resolver's internal
    ``"chain=...; failures=..."`` phrasing.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

_REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO / "core" / "console"))
sys.path.insert(0, str(_REPO / "core" / "gateway"))
sys.path.insert(0, str(_REPO / "operator" / "forge"))
sys.path.insert(0, str(_REPO / "operator" / "bridges" / "shared"))
sys.path.insert(0, str(_REPO / "operator" / "voice" / "scripts"))


# Each test re-imports the console modules so ENV-derived paths reflect the
# active sandbox. Deliberately NOT including "stt"/"say" here: this test
# suite only ever mocks their provider_status() function via mock.patch
# (which self-restores on __exit__), never relies on their env-derived
# module-level state — forcing them out of sys.modules on every sandbox
# teardown isn't needed for this file's own correctness, and it broke
# operator/voice/scripts/test_say_provider_status.py's importlib.reload()
# when both suites ran in the same pytest session (that file keeps a
# module-level reference to the "say" singleton and expects it to stay
# registered in sys.modules — a real cross-file test-isolation regression,
# fixed here rather than by loosening that file's own reload logic).
_REIMPORT_MODULES = (
    "corvin_console.auth",
    "corvin_console.audit",
    "corvin_console.deps",
    "corvin_console.routes.voice",
    "corvin_console.routes.auth_routes",
    "corvin_console.routes.dashboard",
    "corvin_console.routes.sessions",
    "corvin_console.app",
    "corvin_console",
)


def _reset_modules():
    for name in list(sys.modules):
        if name in _REIMPORT_MODULES or name.startswith("corvin_console."):
            sys.modules.pop(name, None)


@contextmanager
def _sandbox(tenant_id: str = "_default"):
    """Hermetic CORVIN_HOME + XDG_CONFIG_HOME + minimal tenant tree, a
    live console session, and the FastAPI app mounted at /v1/console —
    same shape as test_profile_routes.py's ``_sandbox``.
    """
    with tempfile.TemporaryDirectory(prefix="console-voice-status-test-") as td:
        home = Path(td) / "corvin"
        xdg = Path(td) / "xdg"
        (home / "tenants" / tenant_id / "global" / "auth").mkdir(parents=True)
        (home / "tenants" / tenant_id / "global" / "forge").mkdir(parents=True)
        (home / "tenants" / tenant_id / "global" / "console" / "sessions").mkdir(parents=True)
        (xdg / "corvin-voice").mkdir(parents=True)

        prev = {k: os.environ.get(k) for k in ("CORVIN_HOME", "XDG_CONFIG_HOME")}
        os.environ["CORVIN_HOME"] = str(home)
        os.environ["XDG_CONFIG_HOME"] = str(xdg)
        try:
            _reset_modules()
            from corvin_console import auth as console_session_auth
            from corvin_console.app import router
            from fastapi import FastAPI
            from fastapi.testclient import TestClient

            rec = console_session_auth.create_session(tenant_id=tenant_id)

            app = FastAPI()
            app.include_router(router, prefix="/v1/console")
            client = TestClient(app)
            client.cookies.set("corvin_console_sid", rec.sid)

            yield {"client": client, "rec": rec}
        finally:
            for k, v in prev.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
            _reset_modules()


_ALL_READY_STT = {
    "local": {
        "ready": True, "package_installed": True, "model_present": True,
        "key_configured": None, "detail": "ready (model 'tiny-q5_1')",
    },
    "openai": {
        "ready": True, "package_installed": True, "model_present": None,
        "key_configured": True, "detail": "ready",
    },
}
_ALL_READY_TTS = {
    "openai": {
        "ready": True, "package_installed": True, "model_present": None,
        "key_configured": True, "detail": "ready",
    },
    "edge": {
        "ready": True, "package_installed": True, "model_present": None,
        "key_configured": None, "detail": "ready (needs internet at synth time)",
    },
    "piper": {
        "ready": True, "package_installed": True, "model_present": True,
        "key_configured": None, "detail": "ready",
    },
}


class VoiceStatusRouteTests(unittest.TestCase):
    def test_all_ready(self):
        with _sandbox() as ctx:
            with mock.patch("stt.provider_status", return_value=_ALL_READY_STT), \
                 mock.patch("say.provider_status", return_value=_ALL_READY_TTS):
                r = ctx["client"].get("/v1/console/voice/status")
            self.assertEqual(r.status_code, 200, r.text)
            body = r.json()
            self.assertTrue(body["stt"]["local"]["ready"])
            self.assertTrue(body["stt"]["openai"]["ready"])
            self.assertTrue(body["tts"]["piper"]["ready"])
            self.assertTrue(body["tts"]["edge"]["ready"])

    def test_package_missing_state(self):
        fake_stt = {
            "local": {
                "ready": False, "package_installed": False, "model_present": None,
                "key_configured": None, "detail": "pywhispercpp not installed",
            },
            "openai": {
                "ready": False, "package_installed": False, "model_present": None,
                "key_configured": False, "detail": "no API key configured",
            },
        }
        with _sandbox() as ctx:
            with mock.patch("stt.provider_status", return_value=fake_stt), \
                 mock.patch("say.provider_status", return_value={}):
                r = ctx["client"].get("/v1/console/voice/status")
            self.assertEqual(r.status_code, 200, r.text)
            body = r.json()
            self.assertFalse(body["stt"]["local"]["ready"])
            self.assertFalse(body["stt"]["local"]["package_installed"])
            self.assertIn("not installed", body["stt"]["local"]["detail"])

    def test_model_missing_state(self):
        fake_stt = {
            "local": {
                "ready": False, "package_installed": True, "model_present": False,
                "key_configured": None, "detail": "model 'tiny-q5_1' not downloaded yet",
            },
        }
        with _sandbox() as ctx:
            with mock.patch("stt.provider_status", return_value=fake_stt), \
                 mock.patch("say.provider_status", return_value={}):
                r = ctx["client"].get("/v1/console/voice/status")
            body = r.json()
            self.assertTrue(body["stt"]["local"]["package_installed"])
            self.assertFalse(body["stt"]["local"]["model_present"])
            self.assertIn("not downloaded", body["stt"]["local"]["detail"])

    def test_key_missing_state(self):
        fake_stt = {
            "openai": {
                "ready": False, "package_installed": True, "model_present": None,
                "key_configured": False, "detail": "no API key configured",
            },
        }
        with _sandbox() as ctx:
            with mock.patch("stt.provider_status", return_value=fake_stt), \
                 mock.patch("say.provider_status", return_value={}):
                r = ctx["client"].get("/v1/console/voice/status")
            body = r.json()
            self.assertFalse(body["stt"]["openai"]["ready"])
            self.assertFalse(body["stt"]["openai"]["key_configured"])

    def test_status_probe_failure_degrades_to_empty_map_not_500(self):
        """A crash inside provider_status() must not break the endpoint —
        the panel should show 'unavailable', not a 500."""
        with _sandbox() as ctx:
            with mock.patch("stt.provider_status", side_effect=RuntimeError("boom")), \
                 mock.patch("say.provider_status", side_effect=RuntimeError("boom")):
                r = ctx["client"].get("/v1/console/voice/status")
            self.assertEqual(r.status_code, 200, r.text)
            body = r.json()
            self.assertEqual(body["stt"], {})
            self.assertEqual(body["tts"], {})

    def test_requires_session(self):
        with _sandbox() as ctx:
            ctx["client"].cookies.clear()
            r = ctx["client"].get("/v1/console/voice/status")
            self.assertEqual(r.status_code, 401)


class SttUnavailableMessageTests(unittest.TestCase):
    """The chat-facing STT error must never leak the resolver's raw
    'no STT provider available; chain=...; failures=...' text
    (ADR-0185 Decision 4 / Must-NOT)."""

    def test_message_never_leaks_raw_chain_or_failures_string(self):
        fake_status = {
            "local": {
                "ready": False, "package_installed": False, "model_present": None,
                "key_configured": None, "detail": "pywhispercpp not installed",
            },
            "openai": {
                "ready": False, "package_installed": True, "model_present": None,
                "key_configured": False, "detail": "no API key configured",
            },
        }
        with _sandbox():
            from corvin_console.routes import voice as voice_route
            with mock.patch("stt.provider_status", return_value=fake_status):
                msg = voice_route._stt_unavailable_message()
        self.assertNotIn("chain=", msg)
        self.assertNotIn("failures=", msg)
        self.assertNotIn("STTProviderUnavailable", msg)
        self.assertIn("Voice", msg)  # points the user at Settings → Voice
        self.assertGreater(len(msg), 10)

    def test_message_mentions_model_download_when_model_missing(self):
        fake_status = {
            "local": {
                "ready": False, "package_installed": True, "model_present": False,
                "key_configured": None, "detail": "model not downloaded yet",
            },
            "openai": {
                "ready": False, "package_installed": True, "model_present": None,
                "key_configured": False, "detail": "no API key configured",
            },
        }
        with _sandbox():
            from corvin_console.routes import voice as voice_route
            with mock.patch("stt.provider_status", return_value=fake_status):
                msg = voice_route._stt_unavailable_message()
        self.assertNotIn("chain=", msg)
        self.assertIn("download", msg.lower())

    def test_message_probe_failure_still_returns_safe_generic_copy(self):
        with _sandbox():
            from corvin_console.routes import voice as voice_route
            with mock.patch("stt.provider_status", side_effect=RuntimeError("boom")):
                msg = voice_route._stt_unavailable_message()
        self.assertNotIn("chain=", msg)
        self.assertNotIn("boom", msg)
        self.assertGreater(len(msg), 10)


if __name__ == "__main__":
    unittest.main()
