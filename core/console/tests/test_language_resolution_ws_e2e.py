"""E2E wiring proof for ADR-0650 language resolution (Phase 4).

Drives the REAL chat WebSocket route (``routes/chat.py::chat_stream``) with a
German prompt and asserts the turn's language decision arrives on the wire —
produced by the real ``chat_runtime.stream_turn`` calling the real
``language_resolution.resolve_turn_language``. Nothing on the path from the
socket to the resolver is stubbed.

Two things ARE substituted, and neither is the component under test:
  * the user's profile file (``load_profile_language``) — a fixture, so the
    test does not depend on whatever display_language this machine happens to
    have configured;
  * ``_drain_compute_inbox``, the step that runs immediately AFTER language
    resolution — raising there truncates the turn before it spawns an engine
    subprocess. The route turns that into an in-band error+done, which is the
    documented robustness contract.

Locks two rules:
  1. No profile set + German input  -> "de", source "input"  (answer in the
     language the user wrote in).
  2. Profile set to "en" + German input -> "en", source "profile" (the profile
     is CANONICAL and detection never overrides it).
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

_THIS = Path(__file__).resolve()
_REPO = _THIS.parents[3]
for p in ("core/console", "operator/bridges/shared", "operator/forge"):
    sys.path.insert(0, str(_REPO / p))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import corvin_console.chat_runtime as chat_runtime  # noqa: E402
import corvin_console.routes.chat as chat_routes  # noqa: E402


def _app() -> FastAPI:
    app = FastAPI()
    app.include_router(chat_routes.router, prefix="/v1/console")
    return app


def _boom(_sess):  # noqa: ANN001
    """Stand-in for the step right after language resolution."""
    raise RuntimeError("turn truncated after language resolution (test)")


class LanguageResolutionWsE2E(unittest.TestCase):
    def setUp(self) -> None:
        import tempfile

        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

        self.rec = MagicMock()
        self.rec.tenant_id = "_default"
        self.rec.sid_fingerprint = "fp"

        self.sess = chat_runtime.WebChatSession(
            sid="lang-e2e",
            tenant_id="_default",
            created_at=0.0,
            last_active_at=0.0,
            title="lang",
            turn_count=0,
            workdir=Path(self._tmp.name),
        )

        for target, repl in (
            ("corvin_console.routes._compute_license_gate.enforce_chat_turns",
             lambda *a, **k: None),
        ):
            _p = patch(target, repl)
            _p.start()
            self.addCleanup(_p.stop)

    def _first_language_event(self, prompt: str, profile_lang: str | None) -> dict:
        with (
            patch.object(chat_routes.session_auth, "load_session", return_value=self.rec),
            patch.object(chat_routes.chat_runtime, "get_session", return_value=self.sess),
            patch.object(chat_runtime, "load_profile_language", lambda _t: profile_lang),
            patch.object(chat_runtime, "_drain_compute_inbox", _boom),
        ):
            c = TestClient(_app())
            c.cookies.set("corvin_console_sid", "valid-sid")
            with c.websocket_connect("/v1/console/chat/sessions/lang-e2e/stream") as ws:
                self.assertEqual(ws.receive_json()["type"], "ready")
                ws.send_json({"type": "user", "text": prompt})
                evt = ws.receive_json()
                # Drain the truncation error so the socket closes cleanly.
                self.assertEqual(ws.receive_json()["type"], "error")
                self.assertEqual(ws.receive_json()["type"], "done")
        return evt

    def test_german_turn_without_profile_resolves_to_german(self) -> None:
        evt = self._first_language_event("Das ist Deutsch", profile_lang=None)
        self.assertEqual(evt["type"], "language")
        self.assertEqual(evt["resolved_lang"], "de")
        self.assertEqual(evt["source"], "input")
        self.assertFalse(evt["profile_set"])

    def test_german_turn_with_english_profile_stays_english(self) -> None:
        """Profile is CANONICAL — input detection must not override it."""
        evt = self._first_language_event("Das ist Deutsch", profile_lang="en")
        self.assertEqual(evt["type"], "language")
        self.assertEqual(evt["resolved_lang"], "en")
        self.assertEqual(evt["source"], "profile")
        self.assertTrue(evt["profile_set"])

    def test_decision_is_stashed_for_persistence(self) -> None:
        """The same decision _append_turn writes onto every turn record."""
        self._first_language_event("Überprüf diesen Code bitte", profile_lang=None)
        self.assertEqual(self.sess.language_context["resolved_lang"], "de")
        self.assertEqual(self.sess.language_context["source"], "input")


if __name__ == "__main__":
    unittest.main()
