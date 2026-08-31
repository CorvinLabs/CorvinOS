"""Channel disconnect / delete (2026-07-22).

Until now a channel could be created (save-token / OAuth exchange / QR pairing)
and toggled off, but never *un*-connected: the credential stayed on disk and
re-running the wizard was the only way to replace it. There was no way to move a
bridge to a different bot/account or to revoke a leaked token from the console.

The traps this covers, all of which would make a "delete" silently not delete:

  1. Credentials live in TWO places — the zero-config endpoints write via
     _resolve_bridges_dir() (source/_vendor) while _settings_path() is the
     runtime path, and _read_settings() falls back runtime → legacy. Clearing
     one leaves a working credential behind.
  2. ``pin`` matches _SECRET_KEY_HINTS but is the operator's access PIN, i.e. a
     preference. A generic secret sweep would delete it and would also count it
     as proof that a channel is connected.
  3. ``configured`` used to mean "settings.json exists", so a file holding only
     preferences read as connected — true on this install for telegram — and
     after a disconnect every channel would still claim to be configured.
  4. WhatsApp's linked-device credentials live in <channel>/auth/, not in
     settings.json; leaving them means the daemon re-attaches to the old
     account after a "disconnect".

Run: python3 core/console/tests/test_bridge_disconnect.py
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

from corvin_console.routes import bridges as br  # noqa: E402


def _write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


class CredentialKeyTests(unittest.TestCase):
    def test_token_is_a_credential(self):
        self.assertTrue(br._is_credential_key("discord_token"))
        self.assertTrue(br._is_credential_key("imap_password"))
        self.assertTrue(br._is_credential_key("client_secret"))

    def test_pin_is_a_preference_not_a_credential(self):
        """The bridge access PIN matches the secret hints but must survive."""
        self.assertFalse(br._is_credential_key("pin"))
        self.assertFalse(br._is_credential_key("PIN"))

    def test_plain_preferences_are_not_credentials(self):
        for key in ("whitelist", "rate_limit_per_hour", "lang", "chat_profiles"):
            self.assertFalse(br._is_credential_key(key), key)

    def test_drop_list_keeps_preferences(self):
        settings = {
            "discord_token": "abc", "pin": "1234", "whitelist": ["x"],
            "rate_limit_per_hour": 10, "_token_validated_at": "via-console",
        }
        drop = br._credential_keys_for("discord", settings)
        self.assertIn("discord_token", drop)
        self.assertIn("_token_validated_at", drop)
        self.assertNotIn("pin", drop)
        self.assertNotIn("whitelist", drop)
        self.assertNotIn("rate_limit_per_hour", drop)

    def test_slack_team_id_is_connection_identity(self):
        drop = br._credential_keys_for("slack", {"slack_token": "x", "team_id": "T1"})
        self.assertIn("team_id", drop)


class ConnectedTests(unittest.TestCase):
    """``configured`` must reflect a usable credential, not a file's existence."""

    def _isolate(self, td: str):
        home = Path(td) / "home"
        legacy = Path(td) / "legacy"
        return (
            patch.dict(os.environ, {"CORVIN_HOME": str(home)}),
            patch.object(br, "_BRIDGES_DIR", legacy),
            home,
            legacy,
        )

    def test_preferences_only_file_is_not_connected(self):
        with tempfile.TemporaryDirectory() as td:
            env, leg, home, _ = self._isolate(td)
            with env, leg:
                _write(home / "bridges" / "telegram" / "settings.json",
                       {"whitelist": ["a"], "pin": "1234", "read_only": True})
                self.assertFalse(br._channel_connected("telegram"))

    def test_token_makes_it_connected(self):
        with tempfile.TemporaryDirectory() as td:
            env, leg, home, _ = self._isolate(td)
            with env, leg:
                _write(home / "bridges" / "telegram" / "settings.json",
                       {"telegram_token": "123:abc"})
                self.assertTrue(br._channel_connected("telegram"))

    def test_empty_token_is_not_connected(self):
        with tempfile.TemporaryDirectory() as td:
            env, leg, home, _ = self._isolate(td)
            with env, leg:
                _write(home / "bridges" / "discord" / "settings.json",
                       {"discord_token": "   "})
                self.assertFalse(br._channel_connected("discord"))

    def test_link_flag_counts_without_any_secret(self):
        """Signal/WhatsApp pair by device; there is no token in the file."""
        with tempfile.TemporaryDirectory() as td:
            env, leg, home, _ = self._isolate(td)
            with env, leg:
                _write(home / "bridges" / "signal" / "settings.json",
                       {"signal_linked": True})
                self.assertTrue(br._channel_connected("signal"))

    def test_credential_only_in_legacy_still_counts(self):
        """_read_settings falls back to legacy — connected must see that too,
        otherwise the UI offers 'Connect' for an already-working bridge."""
        with tempfile.TemporaryDirectory() as td:
            env, leg, _, legacy = self._isolate(td)
            with env, leg:
                _write(legacy / "discord" / "settings.json", {"discord_token": "abc"})
                self.assertTrue(br._channel_connected("discord"))


class DisconnectFlowTests(unittest.TestCase):
    """The route body's disk work, exercised through the same helpers it uses."""

    def _clean(self, channel: str, mode: str) -> list[str]:
        """Mirror of the route's step 3 — kept in sync by the tests below."""
        cleared: list[str] = []
        for path in (br._settings_path(channel), br._legacy_settings_path(channel)):
            if not path.exists():
                continue
            if mode == "delete":
                path.unlink()
                continue
            current = br._read_json(path)
            drop = br._credential_keys_for(channel, current)
            for key in drop:
                current.pop(key, None)
            br._write_atomic(path, current)
            cleared.extend(k for k in drop if k not in cleared)
        return cleared

    def test_disconnect_clears_both_locations(self):
        """The core trap: a credential surviving in the legacy path means the
        channel silently stays connected after a 'disconnect'."""
        with tempfile.TemporaryDirectory() as td:
            home, legacy = Path(td) / "home", Path(td) / "legacy"
            with patch.dict(os.environ, {"CORVIN_HOME": str(home)}), \
                 patch.object(br, "_BRIDGES_DIR", legacy):
                _write(home / "bridges" / "discord" / "settings.json",
                       {"discord_token": "runtime", "pin": "1111"})
                _write(legacy / "discord" / "settings.json",
                       {"discord_token": "legacy", "pin": "1111"})

                self._clean("discord", "disconnect")

                self.assertFalse(br._channel_connected("discord"))
                for p in (home / "bridges" / "discord" / "settings.json",
                          legacy / "discord" / "settings.json"):
                    data = json.loads(p.read_text())
                    self.assertNotIn("discord_token", data, p)
                    self.assertEqual(data.get("pin"), "1111", "PIN must survive")

    def test_disconnect_preserves_preferences(self):
        with tempfile.TemporaryDirectory() as td:
            home, legacy = Path(td) / "home", Path(td) / "legacy"
            with patch.dict(os.environ, {"CORVIN_HOME": str(home)}), \
                 patch.object(br, "_BRIDGES_DIR", legacy):
                p = home / "bridges" / "discord" / "settings.json"
                _write(p, {
                    "discord_token": "abc", "pin": "1234",
                    "whitelist": ["u1", "u2"], "rate_limit_per_hour": 20,
                    "lang": "de", "chat_profiles": {"a": 1},
                })
                self._clean("discord", "disconnect")
                data = json.loads(p.read_text())
                self.assertEqual(data["whitelist"], ["u1", "u2"])
                self.assertEqual(data["rate_limit_per_hour"], 20)
                self.assertEqual(data["lang"], "de")
                self.assertEqual(data["chat_profiles"], {"a": 1})
                self.assertNotIn("discord_token", data)

    def test_delete_removes_the_files(self):
        with tempfile.TemporaryDirectory() as td:
            home, legacy = Path(td) / "home", Path(td) / "legacy"
            with patch.dict(os.environ, {"CORVIN_HOME": str(home)}), \
                 patch.object(br, "_BRIDGES_DIR", legacy):
                a = home / "bridges" / "discord" / "settings.json"
                b = legacy / "discord" / "settings.json"
                _write(a, {"discord_token": "abc", "whitelist": ["x"]})
                _write(b, {"discord_token": "abc"})
                self._clean("discord", "delete")
                self.assertFalse(a.exists())
                self.assertFalse(b.exists())
                self.assertFalse(br._channel_connected("discord"))

    def test_disconnect_then_reconnect_round_trip(self):
        """The whole point: after disconnecting, saving a NEW token must give a
        working connection with the old preferences intact."""
        with tempfile.TemporaryDirectory() as td:
            home, legacy = Path(td) / "home", Path(td) / "legacy"
            with patch.dict(os.environ, {"CORVIN_HOME": str(home)}), \
                 patch.object(br, "_BRIDGES_DIR", legacy):
                p = home / "bridges" / "discord" / "settings.json"
                _write(p, {"discord_token": "old", "whitelist": ["keep"]})

                self._clean("discord", "disconnect")
                self.assertFalse(br._channel_connected("discord"))

                settings = br._read_settings("discord")
                settings["discord_token"] = "new"
                br._write_atomic(p, settings)

                self.assertTrue(br._channel_connected("discord"))
                data = json.loads(p.read_text())
                self.assertEqual(data["discord_token"], "new")
                self.assertEqual(data["whitelist"], ["keep"])


class LinkStateTests(unittest.TestCase):
    def test_whatsapp_auth_dir_is_archived_not_left_behind(self):
        with tempfile.TemporaryDirectory() as td:
            legacy = Path(td) / "legacy"
            auth = legacy / "whatsapp" / "auth"
            auth.mkdir(parents=True)
            (auth / "creds.json").write_text("{}", encoding="utf-8")
            with patch.dict(os.environ, {"CORVIN_HOME": str(Path(td) / "home")}), \
                 patch.object(br, "_BRIDGES_DIR", legacy):
                dirs = br._link_state_dirs("whatsapp")
                self.assertIn(auth, dirs)
                archived = br._archive_dir(auth)
                self.assertIsNotNone(archived)
                self.assertFalse(auth.exists(), "old linked device must not remain")
                self.assertTrue(Path(archived).is_dir(), "must stay recoverable")

    def test_non_qr_channels_have_no_link_dirs(self):
        for ch in ("discord", "telegram", "slack", "email", "teams"):
            self.assertEqual(br._link_state_dirs(ch), [], ch)


class ContractTests(unittest.TestCase):
    def test_mode_is_constrained(self):
        from pydantic import ValidationError
        br.BridgeDisconnectRequest(mode="disconnect")
        br.BridgeDisconnectRequest(mode="delete")
        with self.assertRaises(ValidationError):
            br.BridgeDisconnectRequest(mode="wipe-everything")

    def test_default_mode_is_the_gentler_one(self):
        self.assertEqual(br.BridgeDisconnectRequest().mode, "disconnect")

    def test_route_is_registered(self):
        paths = {r.path for r in br.router.routes}
        self.assertIn("/bridges/{channel}/disconnect", paths)


if __name__ == "__main__":
    unittest.main(verbosity=2)
