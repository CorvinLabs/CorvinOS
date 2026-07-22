"""HTTP-level contract for POST /bridges/{channel}/disconnect (2026-07-22).

The module-level suite (test_bridge_disconnect.py) proves the disk semantics.
This one proves the wiring the disk tests cannot see:

  * the destructive route sits behind the ADR-0015 mutation gate — a missing or
    wrong re-auth token must 401 and must NOT touch disk;
  * an unknown channel is rejected before any filesystem work;
  * the daemon is stopped BEFORE the credential is cleaned (a live daemon
    hot-reloads settings.json and several re-persist credentials, so cleaning
    under a running daemon can be silently undone);
  * the channel is left disabled, so a restart cannot bring it back up against
    a half-removed connection;
  * the response never echoes a secret VALUE, only key names.

Run: python3 core/console/tests/test_bridge_disconnect_http.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

_REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO / "core" / "console"))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from corvin_console import auth as session_auth  # noqa: E402
from corvin_console import deps as console_deps  # noqa: E402
from corvin_console.routes import bridges as br  # noqa: E402


_GOOD_REAUTH = "correct-reauth-token"


def _fake_record() -> session_auth.SessionRecord:
    """Build a real SessionRecord from its actual field list.

    Deliberately NOT wrapped in try/except-then-skip: an earlier version of this
    helper guessed the constructor shape and skipped on TypeError, so when the
    guess was wrong all 13 tests skipped and the suite still reported OK — a
    green run that proved nothing. If the dataclass changes, this must fail
    loudly instead.
    """
    import dataclasses
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


class _Base(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.addCleanup(self.td.cleanup)
        root = Path(self.td.name)
        self.home = root / "home"
        self.legacy = root / "legacy"

        self.env = patch.dict(os.environ, {"CORVIN_HOME": str(self.home)})
        self.env.start(); self.addCleanup(self.env.stop)
        self.leg = patch.object(br, "_BRIDGES_DIR", self.legacy)
        self.leg.start(); self.addCleanup(self.leg.stop)

        # Record the order of side effects so the daemon-stop-first ordering
        # can be asserted rather than assumed.
        self.calls: list[str] = []

        def _runtime(channel, *, enabled=None):
            self.calls.append(f"runtime:{channel}:{enabled}")
            return {"applied": True, "via": "test"}

        self.rt = patch.object(br, "_apply_runtime_change", _runtime)
        self.rt.start(); self.addCleanup(self.rt.stop)

        real_write = br._write_atomic

        def _tracked_write(path, payload):
            self.calls.append(f"write:{path.name}")
            return real_write(path, payload)

        self.wr = patch.object(br, "_write_atomic", _tracked_write)
        self.wr.start(); self.addCleanup(self.wr.stop)

        self.audit = patch.object(br, "console_audit")
        self.audit.start(); self.addCleanup(self.audit.stop)

        self.reauth = patch.object(
            br, "verify_reauth", lambda rec, token: token == _GOOD_REAUTH)
        self.reauth.start(); self.addCleanup(self.reauth.stop)

        app = FastAPI()
        app.include_router(br.router)
        rec = _fake_record()
        app.dependency_overrides[console_deps.require_csrf] = lambda: rec
        app.dependency_overrides[console_deps.require_session] = lambda: rec
        self.client = TestClient(app)

    def _seed(self, channel="discord", **extra):
        payload = {"discord_token": "live-secret-value", "pin": "1234",
                   "whitelist": ["u1"], **extra}
        for base in (self.home / "bridges", self.legacy):
            p = base / channel / "settings.json"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(payload), encoding="utf-8")
        return payload

    def _post(self, channel="discord", **body):
        return self.client.post(f"/bridges/{channel}/disconnect", json=body)


class GateTests(_Base):
    def test_missing_reauth_is_rejected(self):
        self._seed()
        r = self._post(mode="disconnect")
        self.assertEqual(r.status_code, 401)

    def test_wrong_reauth_is_rejected(self):
        self._seed()
        r = self._post(mode="delete", re_auth_token="nope")
        self.assertEqual(r.status_code, 401)

    def test_rejected_request_does_not_touch_disk(self):
        """A failed gate must not stop the daemon or strip the credential."""
        self._seed()
        self._post(mode="delete", re_auth_token="nope")
        self.assertEqual(self.calls, [], "no side effects before authorisation")
        data = json.loads(
            (self.home / "bridges" / "discord" / "settings.json").read_text())
        self.assertEqual(data["discord_token"], "live-secret-value")

    def test_unknown_channel_rejected(self):
        r = self._post("not-a-channel", mode="disconnect",
                       re_auth_token=_GOOD_REAUTH)
        self.assertEqual(r.status_code, 400)

    def test_bogus_mode_rejected(self):
        r = self._post(mode="nuke", re_auth_token=_GOOD_REAUTH)
        self.assertEqual(r.status_code, 422)


class OrderingTests(_Base):
    def test_daemon_is_stopped_before_disk_is_touched(self):
        self._seed()
        r = self._post(mode="disconnect", re_auth_token=_GOOD_REAUTH)
        self.assertEqual(r.status_code, 200)
        self.assertTrue(self.calls, "expected side effects")
        self.assertTrue(
            self.calls[0].startswith("runtime:discord:False"),
            f"daemon stop must come first, got {self.calls}",
        )
        self.assertTrue(any(c.startswith("write:") for c in self.calls))

    def test_channel_is_left_disabled(self):
        self._seed()
        self._post(mode="disconnect", re_auth_token=_GOOD_REAUTH)
        state = json.loads(
            (self.home / "bridges" / "state.json").read_text(encoding="utf-8"))
        self.assertIs(state["channels"]["discord"]["enabled"], False)


class ResponseTests(_Base):
    def test_disconnect_reports_key_names_not_values(self):
        self._seed()
        r = self._post(mode="disconnect", re_auth_token=_GOOD_REAUTH)
        body = r.json()
        self.assertEqual(r.status_code, 200)
        self.assertIn("discord_token", body["cleared_keys"])
        self.assertNotIn("live-secret-value", json.dumps(body),
                         "the response must never carry a secret value")

    def test_disconnect_clears_both_paths_and_keeps_prefs(self):
        self._seed()
        self._post(mode="disconnect", re_auth_token=_GOOD_REAUTH)
        for p in (self.home / "bridges" / "discord" / "settings.json",
                  self.legacy / "discord" / "settings.json"):
            data = json.loads(p.read_text())
            self.assertNotIn("discord_token", data, p)
            self.assertEqual(data["pin"], "1234")
            self.assertEqual(data["whitelist"], ["u1"])

    def test_delete_removes_files_and_keeps_a_backup(self):
        self._seed()
        r = self._post(mode="delete", re_auth_token=_GOOD_REAUTH)
        self.assertEqual(r.status_code, 200)
        runtime = self.home / "bridges" / "discord" / "settings.json"
        self.assertFalse(runtime.exists())
        self.assertTrue(runtime.with_suffix(".json.bak").exists(),
                        "a mis-click must stay recoverable")
        self.assertEqual(len(r.json()["removed_files"]), 2)

    def test_disconnect_on_a_never_configured_channel_is_a_no_op(self):
        r = self._post("teams", mode="disconnect", re_auth_token=_GOOD_REAUTH)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["cleared_keys"], [])
        self.assertEqual(r.json()["removed_files"], [])


class ListingTests(_Base):
    def test_configured_flips_to_false_after_disconnect(self):
        """The tile must offer setup again — that is the whole feature."""
        self._seed()
        before = self.client.get("/bridges").json()["bridges"]
        self.assertTrue(next(b for b in before if b["channel"] == "discord")["configured"])

        self._post(mode="disconnect", re_auth_token=_GOOD_REAUTH)

        after = next(b for b in self.client.get("/bridges").json()["bridges"]
                     if b["channel"] == "discord")
        self.assertFalse(after["configured"], "disconnected must not read as configured")
        self.assertTrue(after["has_settings"], "preferences were kept, so the file remains")

    def test_delete_clears_has_settings_too(self):
        self._seed()
        self._post(mode="delete", re_auth_token=_GOOD_REAUTH)
        after = next(b for b in self.client.get("/bridges").json()["bridges"]
                     if b["channel"] == "discord")
        self.assertFalse(after["configured"])
        self.assertFalse(after["has_settings"], "delete means back to never-configured")


if __name__ == "__main__":
    unittest.main(verbosity=2)
