"""HTTP-level contract for POST /discord/validate-token, /discord/save-token,
/telegram/validate-token, /telegram/save-token (2026-07-31).

Root cause this replaces: the file previously here never actually called the
route at all -- every "test" patched subprocess.run and then asserted
properties of its OWN mock response dict (`assert mock_response["valid"] is
True`), never invoking TestClient or the real handler. It also authenticated
with a bare `unittest.mock.MagicMock()`, which silently accepts ANY attribute
access -- so `mock_rec.user_id` "worked" in the test even though
`SessionRecord` (auth.py) has no such field.

That combination let a real bug ship across two releases: all four routes
(`validate_discord_token`, `save_discord_token`,
`validate_telegram_token`, `save_telegram_token`) logged
`f"... (user: {rec.user_id})"` -- SessionRecord has no per-user identity
(single-tenant-owner model, no credential auth path; see auth.py), so every
real request hit `AttributeError: 'SessionRecord' object has no attribute
'user_id'` and 500'd before validation could even run. Live-reported via the
Discord bot-token setup dialog. The same bug was already found and fixed
once in rag_hub.py (2026-07-2x, using `.tier` instead) -- it drifted back in
here because nothing exercised the real route through the real auth
dependency.

These tests build a REAL `SessionRecord` via dataclasses.fields
introspection (same technique as test_bridge_disconnect_http.py) and drive
the routes through a real ASGI TestClient, with only the Node.js subprocess
call mocked -- so a re-introduced `rec.user_id` (or any other nonexistent
attribute access anywhere in the handler) fails loudly with a 500, not
silently with a green checkmark.

Run: python3 -m pytest core/console/tests/test_discord_setup.py
"""
from __future__ import annotations

import dataclasses
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

_REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO / "core" / "console"))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from corvin_console import auth as session_auth  # noqa: E402
from corvin_console import deps as console_deps  # noqa: E402
from corvin_console.routes import bridges as br  # noqa: E402


def _fake_record() -> session_auth.SessionRecord:
    """Build a real SessionRecord from its actual field list.

    Deliberately NOT a MagicMock: a Mock accepts any attribute access
    silently, which is exactly how `rec.user_id` shipped undetected. A real
    dataclass instance raises AttributeError on a field that doesn't exist,
    same as production.
    """
    now = 1_000_000.0
    values: dict[str, object] = {}
    for f in dataclasses.fields(session_auth.SessionRecord):
        if f.default is not dataclasses.MISSING:
            continue
        ann = str(f.type)
        if "float" in ann:
            values[f.name] = now + (3600 if f.name == "expires_at" else 0)
        elif "bool" in ann:
            values[f.name] = False
        elif f.name == "tier":
            tier = getattr(session_auth, "Tier", None)
            values[f.name] = next(iter(tier)) if tier else "owner"
        elif f.name == "tenant_id":
            values[f.name] = "_default"
        else:
            values[f.name] = f"test-{f.name}"
    return session_auth.SessionRecord(**values)  # type: ignore[arg-type]


_DISCORD_TOKEN = "d" * 30  # >= 20 chars, the model's min_length
_TELEGRAM_TOKEN = "123456:" + "t" * 20  # >= 10 chars


class _Base(unittest.TestCase):
    def setUp(self) -> None:
        self.td = tempfile.TemporaryDirectory()
        self.addCleanup(self.td.cleanup)
        self.home = Path(self.td.name) / "home"

        self.env = patch.dict(os.environ, {"CORVIN_HOME": str(self.home)})
        self.env.start(); self.addCleanup(self.env.stop)

        self.audit = patch.object(br, "console_audit")
        self.audit.start(); self.addCleanup(self.audit.stop)

        app = FastAPI()
        app.include_router(br.router)
        rec = _fake_record()
        app.dependency_overrides[console_deps.require_csrf] = lambda: rec
        app.dependency_overrides[console_deps.require_session] = lambda: rec
        self.client = TestClient(app, raise_server_exceptions=False)

    def _mock_node(self, response_data: dict, returncode: int = 0):
        mock_process = MagicMock()
        mock_process.returncode = returncode
        mock_process.stdout = json.dumps(response_data)
        mock_process.stderr = ""
        return patch.object(br.subprocess, "run", return_value=mock_process)


class DiscordTokenRouteTests(_Base):
    def test_validate_token_valid(self):
        """The exact regression: a valid-shaped request through the real
        auth dependency must reach the Node.js call and return 200 -- not
        500 on a bogus rec.user_id access before validation even runs."""
        response_data = {
            "valid": True, "appId": "123", "appName": "CorvinOS Bot",
            "url": "https://discord.com/api/oauth2/authorize?client_id=123",
            "permissionsHuman": ["Send Messages"],
        }
        with self._mock_node(response_data):
            resp = self.client.post(
                "/discord/validate-token", json={"token": _DISCORD_TOKEN},
            )
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertTrue(body["valid"])
        self.assertEqual(body["appId"], "123")

    def test_validate_token_invalid(self):
        with self._mock_node({"valid": False, "error": "Invalid token (401 Unauthorized)"}):
            resp = self.client.post(
                "/discord/validate-token", json={"token": _DISCORD_TOKEN},
            )
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertFalse(body["valid"])
        self.assertIn("Invalid token", body["error"])

    def test_save_token_success(self):
        with self._mock_node({"valid": True, "appId": "123", "appName": "Bot"}):
            resp = self.client.post(
                "/discord/save-token", json={"token": _DISCORD_TOKEN},
            )
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertTrue(resp.json()["success"])

    def test_save_token_invalid(self):
        with self._mock_node({"valid": False, "error": "Invalid token"}):
            resp = self.client.post(
                "/discord/save-token", json={"token": _DISCORD_TOKEN},
            )
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertFalse(resp.json()["success"])


class TelegramTokenRouteTests(_Base):
    def test_validate_token_valid(self):
        response_data = {
            "valid": True, "botId": "999", "botUsername": "corvin_bot",
            "botName": "Corvin",
        }
        with self._mock_node(response_data):
            resp = self.client.post(
                "/telegram/validate-token", json={"token": _TELEGRAM_TOKEN},
            )
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertTrue(resp.json()["valid"])

    def test_save_token_success(self):
        with self._mock_node({"valid": True, "botId": "999", "botUsername": "corvin_bot"}):
            resp = self.client.post(
                "/telegram/save-token", json={"token": _TELEGRAM_TOKEN},
            )
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertTrue(resp.json()["success"])


if __name__ == "__main__":
    unittest.main()
