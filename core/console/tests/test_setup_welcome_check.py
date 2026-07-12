"""Tests for POST /setup/welcome-check + GET /setup/welcome-check/status.

Concept: docs/first-run-language-and-voice-onboarding.md §2 — the first-boot
spoken onboarding self-check. Runs house_rules_boot_health_check + (optional)
Hermes warm-up + voice_doctor's STT/TTS round-trip + the existing engine
connectivity probe as a background job, then builds a localized greeting
that reflects the ACTUAL check outcome and always includes the
capabilities/actions clause the user gets to hear.

Harness follows the FastAPI TestClient + isolated-CORVIN_HOME + auth-bypass
pattern established by test_engine_detect_routes_adr0125.py. house_rules and
voice_doctor are bare top-level modules resolved via a sys.path insert at
call time (no package __init__ chain to patch through) — tests fake them out
by pre-seeding sys.modules, exactly what a real import would find cached.
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
import types
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch

_REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO / "core" / "console"))
sys.path.insert(0, str(_REPO / "operator" / "bridges" / "shared"))


def _reset_modules():
    for key in list(sys.modules):
        if key == "corvin_console" or key.startswith("corvin_console."):
            del sys.modules[key]


@contextmanager
def _sandbox(tmp_path: Path):
    """FastAPI TestClient with an isolated CORVIN_HOME and bypassed auth."""
    home = tmp_path / "corvin_home"
    tenant_id = "_default"
    (home / "tenants" / tenant_id / "global" / "forge").mkdir(parents=True)
    (home / "tenants" / tenant_id / "global" / "console" / "sessions").mkdir(parents=True)

    prev_home = os.environ.get("CORVIN_HOME")
    prev_tid = os.environ.get("CORVIN_TENANT_ID")
    os.environ["CORVIN_HOME"] = str(home)
    os.environ["CORVIN_TENANT_ID"] = tenant_id

    try:
        _reset_modules()
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from corvin_console.app import router as console_router
        from corvin_console.deps import require_csrf, require_session

        app = FastAPI()
        app.include_router(console_router, prefix="/v1/console")

        mock_session = MagicMock()
        mock_session.username = "test_admin"
        mock_session.tenant_id = "_default"
        mock_session.role = "admin"
        mock_session.sid_fingerprint = "test_fp_0123456789ab"

        app.dependency_overrides[require_session] = lambda: mock_session
        app.dependency_overrides[require_csrf] = lambda: mock_session

        with TestClient(app, raise_server_exceptions=False) as tc:
            yield tc
    finally:
        for k, v in [("CORVIN_HOME", prev_home), ("CORVIN_TENANT_ID", prev_tid)]:
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        _reset_modules()


def _poll_welcome_check(tc, timeout: float = 5.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        data = tc.get("/v1/console/setup/welcome-check/status").json()
        if data.get("state") == "done":
            return data
        time.sleep(0.02)
    raise AssertionError(f"welcome-check did not finish within {timeout}s")


def _install_fake_house_rules(*, warn: str | None = None) -> None:
    mod = types.ModuleType("house_rules")

    def _boot_check(log_fn=None):
        if warn and callable(log_fn):
            log_fn(warn)

    mod.house_rules_boot_health_check = _boot_check
    sys.modules["house_rules"] = mod


def _install_fake_voice_doctor(*, stt_ok: bool = True, tts_ok: bool = True) -> None:
    mod = types.ModuleType("voice_doctor")
    mod._DOCTOR_TTS_TEXT = "test"
    mod._check_stt = lambda timeout_s: (stt_ok, "ok" if stt_ok else "stt broken")
    mod._check_tts = lambda text: (tts_ok, "ok" if tts_ok else "tts broken", None)
    sys.modules["voice_doctor"] = mod


class TestWelcomeCheckEndpoint(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._tmp_path = Path(self._tmp)
        self._orig_house_rules = sys.modules.get("house_rules")
        self._orig_voice_doctor = sys.modules.get("voice_doctor")

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmp, ignore_errors=True)
        for name, orig in (
            ("house_rules", self._orig_house_rules),
            ("voice_doctor", self._orig_voice_doctor),
        ):
            if orig is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = orig

    def test_healthy_pipeline_produces_full_greeting_and_ok_components(self):
        _install_fake_house_rules(warn=None)
        _install_fake_voice_doctor(stt_ok=True, tts_ok=True)

        with _sandbox(self._tmp_path) as tc:
            with (
                patch("corvin_console.routes.setup._default_engine", return_value="claude_code"),
                patch("corvin_console.routes.setup._welcome_check_lang", return_value="de"),
                patch(
                    "corvin_console.routes.setup.test_engine",
                    return_value={"ok": True, "detail": "claude --version 1.2.3"},
                ),
            ):
                start = tc.post("/v1/console/setup/welcome-check")
                self.assertEqual(start.status_code, 200)
                self.assertIn(start.json()["state"], ("running", "done"))

                data = _poll_welcome_check(tc)

        self.assertEqual(data["state"], "done")
        self.assertEqual(data["components"]["stt"]["status"], "ok")
        self.assertEqual(data["components"]["tts"]["status"], "ok")
        self.assertEqual(data["components"]["engine"]["status"], "ok")
        self.assertIn("Corvin", data["greeting"])
        # Capabilities/actions clause must be present — the user explicitly
        # asked for "what Corvin can do and what the user can do with it",
        # not just a health report.
        self.assertIn("programmieren", data["greeting"])
        # "Voice to action" framing clause — a follow-up ask: explain that
        # voice controls the whole digital life (computer/browser/internet
        # access), not just capabilities as a bullet list.
        self.assertIn("Voice to Action", data["greeting"])

    def test_degraded_tts_reflects_in_greeting_but_never_blocks(self):
        _install_fake_house_rules(warn="ollama down")
        _install_fake_voice_doctor(stt_ok=True, tts_ok=False)

        with _sandbox(self._tmp_path) as tc:
            with (
                patch("corvin_console.routes.setup._default_engine", return_value="claude_code"),
                patch("corvin_console.routes.setup._welcome_check_lang", return_value="de"),
                patch(
                    "corvin_console.routes.setup.test_engine",
                    return_value={"ok": True, "detail": "claude --version 1.2.3"},
                ),
            ):
                resp = tc.post("/v1/console/setup/welcome-check")
                data = _poll_welcome_check(tc)

        # The check itself never blocks/fails the request — only the wording changes.
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(data["components"]["tts"]["status"], "degraded")
        self.assertEqual(data["components"]["house_rules"]["status"], "degraded")
        self.assertIn("eingeschränkt", data["greeting"])

    def test_engine_probe_exception_never_crashes_the_job(self):
        _install_fake_house_rules(warn=None)
        _install_fake_voice_doctor(stt_ok=True, tts_ok=True)

        with _sandbox(self._tmp_path) as tc:
            with (
                patch("corvin_console.routes.setup._default_engine", return_value="claude_code"),
                patch(
                    "corvin_console.routes.setup.test_engine",
                    side_effect=RuntimeError("boom"),
                ),
            ):
                tc.post("/v1/console/setup/welcome-check")
                data = _poll_welcome_check(tc)

        self.assertEqual(data["state"], "done")
        self.assertEqual(data["components"]["engine"]["status"], "unavailable")
        # Even with the engine probe raising, a full greeting is still built.
        self.assertTrue(data["greeting"])

    def test_second_call_while_running_returns_in_flight_state_not_duplicate(self):
        _install_fake_house_rules(warn=None)
        _install_fake_voice_doctor(stt_ok=True, tts_ok=True)

        with _sandbox(self._tmp_path) as tc:
            with (
                patch("corvin_console.routes.setup._default_engine", return_value="claude_code"),
                patch(
                    "corvin_console.routes.setup.test_engine",
                    return_value={"ok": True, "detail": ""},
                ),
            ):
                r1 = tc.post("/v1/console/setup/welcome-check")
                r2 = tc.post("/v1/console/setup/welcome-check")
                _poll_welcome_check(tc)

        self.assertEqual(r1.status_code, 200)
        self.assertEqual(r2.status_code, 200)

    def test_non_hermes_engine_passed_through_not_collapsed_to_claude_code(self):
        """Regression: engine_id used to be forced to "claude_code" for any
        non-hermes engine, so an opencode-only install got probed for the
        `claude` CLI and could be mislabeled as broken. It must now reach
        test_engine() with the tenant's actual configured engine id."""
        _install_fake_house_rules(warn=None)
        _install_fake_voice_doctor(stt_ok=True, tts_ok=True)

        seen_engine_ids = []

        def _fake_test_engine(body, rec):
            seen_engine_ids.append(body.engine_id)
            return {"ok": True, "detail": "no probe available"}

        with _sandbox(self._tmp_path) as tc:
            with (
                patch("corvin_console.routes.setup._default_engine", return_value="opencode"),
                patch("corvin_console.routes.setup.test_engine", side_effect=_fake_test_engine),
            ):
                tc.post("/v1/console/setup/welcome-check")
                data = _poll_welcome_check(tc)

        self.assertEqual(seen_engine_ids, ["opencode"])
        self.assertNotIn("hermes", data["components"])  # hermes warm-up only runs for engine_id == "hermes"

    def test_two_tenants_do_not_clobber_each_others_welcome_check_state(self):
        """Regression: _WELCOME_CHECK_STATE used to be one shared process-wide
        dict, so tenant B's poll could read tenant A's in-flight/finished
        check (engine id, STT/TTS status). Must be tenant_id-scoped."""
        _install_fake_house_rules(warn=None)
        _install_fake_voice_doctor(stt_ok=True, tts_ok=True)

        with _sandbox(self._tmp_path) as tc:
            from corvin_console.routes import setup as setup_module

            with (
                patch("corvin_console.routes.setup._default_engine", return_value="claude_code"),
                patch(
                    "corvin_console.routes.setup.test_engine",
                    return_value={"ok": True, "detail": ""},
                ),
            ):
                tc.post("/v1/console/setup/welcome-check")
                _poll_welcome_check(tc)

                # A second tenant polling status must NOT see tenant A's
                # finished check — it must still be "idle" for a tenant that
                # never started one.
                other_state = setup_module._welcome_state_for("other_tenant")
                self.assertEqual(other_state.get("state"), "idle")
                # And tenant A's own state must still be independently "done".
                self.assertEqual(setup_module._welcome_state_for("_default").get("state"), "done")


if __name__ == "__main__":
    unittest.main()
