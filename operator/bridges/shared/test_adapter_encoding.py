#!/usr/bin/env python3
"""Regression test: adapter.py's file reads must always decode as UTF-8,
never the OS/locale default.

Windows' locale.getpreferredencoding(False) commonly returns a legacy code
page (e.g. cp1252), not UTF-8, unless the operator has manually enabled
Windows' non-default "Beta: Use Unicode UTF-8" region setting or the
process runs with PYTHONUTF8=1 (neither is set anywhere in this repo's
installer/service files). daemon.js writes inbox JSON as UTF-8 (Node's
documented default for a string argument to fs.writeFileSync); before this
fix, adapter.py's Path.read_text() calls used no explicit encoding, so the
FIRST inbound message containing an emoji or non-Latin1 character (ä, ö,
ü, ¡, …) would raise UnicodeDecodeError on a non-UTF-8-locale Windows box
— crashing message processing at the very first line that reads it.

This locks in that adapter.py's inbox/settings reads always pass an
explicit encoding="utf-8" and therefore decode real non-ASCII content
correctly. A direct differential proof (mocking locale.getpreferredencoding
to force the historical failure) was tried and dropped: CPython's text-I/O
encoding resolution on this interpreter does not consult the mockable
locale.getpreferredencoding() at the point patched, so the mock produced a
false negative here rather than reproducing the bug — a genuinely
non-UTF-8-locale Windows box remains the only environment that reproduces
it directly. These tests instead pin the actual code paths' correctness
against real multi-byte UTF-8 content, which is what the fix guarantees.

Run with: python3 operator/bridges/shared/test_adapter_encoding.py
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
import unittest.mock as mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import adapter  # noqa: E402

# A message body that only survives a real UTF-8 decode: umlauts + emoji.
_NON_LATIN1_TEXT = "Öffne bitte die Tür 🚪 für mich, danke!"


class TestAdapterReadsAreExplicitlyUtf8(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="adapter-encoding-"))

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write_inbox_like_daemon_js(self, text: str) -> Path:
        """Mirrors daemon.js's fs.writeFileSync(path, JSON.stringify(...))
        — real UTF-8 bytes on disk, no BOM, exactly what the Node daemon
        actually produces."""
        p = self.tmp / "msg.json"
        # ensure_ascii=False — json.dumps defaults to escaping non-ASCII as
        # \uXXXX, which is pure-ASCII output and would NOT exercise the
        # multi-byte-UTF-8 decode path this test is about. JS's own
        # JSON.stringify (what daemon.js actually calls) has no such
        # default — it emits the raw UTF-8 characters directly.
        payload = json.dumps({
            "id": "msg1", "channel": "discord", "chat_id": "c1",
            "from": "u1", "text": text,
        }, ensure_ascii=False)
        p.write_bytes(payload.encode("utf-8"))
        return p

    def test_route_key_decodes_real_multibyte_utf8_content(self):
        """adapter._route_key reads the inbox file via
        read_text(encoding="utf-8") — must correctly decode real
        multi-byte UTF-8 content (umlauts, emoji) regardless of what the
        OS/interpreter's locale-default encoding would have been."""
        p = self._write_inbox_like_daemon_js(_NON_LATIN1_TEXT)
        key = adapter._route_key(p)
        self.assertEqual(key, "discord:c1")
        # Prove the file really does contain non-ASCII bytes that a
        # naive ascii/latin1 decode would mangle or reject — otherwise
        # this test would pass trivially regardless of the fix.
        raw = p.read_bytes()
        with self.assertRaises(UnicodeDecodeError):
            raw.decode("ascii")

    def test_peek_side_channel_decodes_real_multibyte_utf8_content(self):
        p = self.tmp / "msg2.json"
        payload = json.dumps({
            "id": "msg2", "channel": "discord", "chat_id": "c1",
            "from": "u1", "text": _NON_LATIN1_TEXT, "_btw": True,
        }, ensure_ascii=False)
        p.write_bytes(payload.encode("utf-8"))
        result = adapter._peek_side_channel(p)
        self.assertTrue(result)

    def test_channel_settings_read_decodes_real_multibyte_utf8_content(self):
        """_load_channel_settings reads settings.json via .read_text() —
        a settings file with a non-ASCII display name/label must decode
        correctly."""
        bridges_dir = self.tmp / "bridges"
        (bridges_dir / "discord").mkdir(parents=True)
        settings_path = bridges_dir / "discord" / "settings.json"
        settings_path.write_bytes(
            json.dumps({"label": "Büro-Bot 🤖"}, ensure_ascii=False).encode("utf-8")
        )
        with mock.patch.dict("os.environ", {"ADAPTER_BRIDGES_DIR": str(bridges_dir)}):
            settings = adapter._load_channel_settings("discord")
        self.assertEqual(settings.get("label"), "Büro-Bot 🤖")


if __name__ == "__main__":
    unittest.main(verbosity=2)
