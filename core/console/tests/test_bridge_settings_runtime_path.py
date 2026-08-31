"""Bridge settings writer/reader path unification (2026-07-22).

Root cause: PUT /bridges/{channel}/settings wrote to the source/_vendor
channel dir while every daemon reads <corvin_home>/bridges/<channel>/
settings.json (bridgeSettingsPath, ADR-0008 §8.3) and _materialise_channel
deliberately skips settings.json. On a wheel install a token saved in the UI
never reached the daemon → "FATAL: DISCORD_TOKEN not set" on a fresh install.

Covers (module-level, no HTTP harness — the route bodies delegate to these
helpers directly):
  1. _settings_path resolves to <corvin_home>/bridges/<ch>/settings.json.
  2. _read_settings falls back to the legacy source-tree file.
  3. Runtime file wins over legacy once it exists.
  4. PUT-equivalent flow migrates legacy secrets to the runtime path and
     preserves masked secrets.

Run: python3 core/console/tests/test_bridge_settings_runtime_path.py
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


class SettingsPathTests(unittest.TestCase):
    def test_settings_path_is_runtime(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td) / "home"
            with patch.dict(os.environ, {"CORVIN_HOME": str(home)}):
                p = br._settings_path("discord")
                self.assertEqual(p, home / "bridges" / "discord" / "settings.json")

    def test_unknown_channel_rejected(self):
        with self.assertRaises(Exception):
            br._settings_path("not-a-channel")

    def test_read_falls_back_to_legacy(self):
        with tempfile.TemporaryDirectory() as td:
            tdp = Path(td)
            home = tdp / "home"
            legacy_root = tdp / "src_bridges"
            (legacy_root / "discord").mkdir(parents=True)
            (legacy_root / "discord" / "settings.json").write_text(
                json.dumps({"discord_token": "legacy-secret"}), encoding="utf-8")
            with patch.dict(os.environ, {"CORVIN_HOME": str(home)}), \
                 patch.object(br, "_BRIDGES_DIR", legacy_root):
                data = br._read_settings("discord")
                self.assertEqual(data.get("discord_token"), "legacy-secret")

    def test_runtime_wins_over_legacy(self):
        with tempfile.TemporaryDirectory() as td:
            tdp = Path(td)
            home = tdp / "home"
            legacy_root = tdp / "src_bridges"
            (legacy_root / "discord").mkdir(parents=True)
            (legacy_root / "discord" / "settings.json").write_text(
                json.dumps({"discord_token": "legacy"}), encoding="utf-8")
            runtime = home / "bridges" / "discord"
            runtime.mkdir(parents=True)
            (runtime / "settings.json").write_text(
                json.dumps({"discord_token": "runtime"}), encoding="utf-8")
            with patch.dict(os.environ, {"CORVIN_HOME": str(home)}), \
                 patch.object(br, "_BRIDGES_DIR", legacy_root):
                data = br._read_settings("discord")
                self.assertEqual(data.get("discord_token"), "runtime")

    def test_put_flow_migrates_legacy_secret_to_runtime(self):
        """Mimics put_bridge_settings' body: read (with legacy fallback),
        merge preserving masked secrets, write to the RUNTIME path."""
        with tempfile.TemporaryDirectory() as td:
            tdp = Path(td)
            home = tdp / "home"
            legacy_root = tdp / "src_bridges"
            (legacy_root / "discord").mkdir(parents=True)
            (legacy_root / "discord" / "settings.json").write_text(
                json.dumps({"discord_token": "sekret-123", "operator_name": "Op"}),
                encoding="utf-8")
            with patch.dict(os.environ, {"CORVIN_HOME": str(home)}), \
                 patch.object(br, "_BRIDGES_DIR", legacy_root):
                path = br._settings_path("discord")
                existing = br._read_settings("discord")
                # UI round-trips the masked token + edits another field.
                incoming = {"discord_token": br._MASKED_PREFIX + "-123",
                            "operator_name": "NewOp"}
                merged = br._merge_preserving_secrets(incoming, existing)
                path.parent.mkdir(parents=True, exist_ok=True)
                br._write_atomic(path, merged)

                self.assertTrue(path.exists(), "PUT must write the runtime path")
                stored = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual(stored["discord_token"], "sekret-123",
                                 "masked secret must be restored from legacy file")
                self.assertEqual(stored["operator_name"], "NewOp")


if __name__ == "__main__":
    unittest.main(verbosity=2)
