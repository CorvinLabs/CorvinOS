#!/usr/bin/env python3
"""Regression tests for lang_cli.py against a malformed profile.json.

Confirmed blind spot (adversarially verified): profile.load() used to only
substitute `{}` when the parsed JSON was falsy (`return _cache or {}`). If
profile.json held valid JSON that was not an object -- e.g. `[1,2,3]`,
`"x"`, `null`, `42` -- load() returned that value unchanged, and any
caller doing `profile.load().get(...)` crashed with an uncaught
`AttributeError`. lang_cli.py's `cmd_show()`/`cmd_set()` call
`profile.load().get(...)` with no try/except (unlike adapter.py's
`_resolve_voice_output_language` and settings_view.py's `_profile_line`,
which both wrap the call defensively), so `/lang show` and `/lang set`
would crash outright instead of degrading cleanly.

This exact non-dict-but-valid-JSON shape is a plausible outcome of a torn
/ partial write to profile.json (see the profile.py write-race blind
spot), so it is not a purely synthetic edge case.

profile.py has since been hardened to coerce any non-dict parse to `{}`
(see `load()`'s `isinstance(_cache, dict)` guard). These tests pin that
fix at both layers:

  * profile.load() itself always returns a dict, whatever shape of valid
    JSON is on disk.
  * lang_cli.py's `show`/`set` sub-commands, run as real subprocesses
    against a profile.json in each of these malformed shapes, still emit
    a clean JSON response with exit code 0 instead of an uncaught
    traceback.

Uses the same subprocess-through-XDG_CONFIG_HOME sandboxing convention as
`operator/bridges/shared/test_i18n.py::LangCliTests`.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SHARED = HERE.parent.parent / "bridges" / "shared"
LANG_CLI = HERE / "lang_cli.py"

sys.path.insert(0, str(SHARED))
import profile as prof  # noqa: E402

# Valid JSON payloads that are NOT objects -- the class of blind spot.
_NON_DICT_PAYLOADS = {
    "list": "[1,2,3]",
    "string": '"x"',
    "null": "null",
    "number": "42",
}


class ProfileLoadNonDictCoercionTests(unittest.TestCase):
    """profile.load() must always hand back a dict, even when the file on
    disk holds valid-but-wrong-shaped JSON."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="lang-cli-profile-test-")
        self._prev_xdg = os.environ.get("XDG_CONFIG_HOME")
        os.environ["XDG_CONFIG_HOME"] = self._tmp
        for m in ("profile",):
            sys.modules.pop(m, None)
        import profile as p  # noqa: PLC0415
        self.prof = p
        self.prof.reset()

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)
        if self._prev_xdg is None:
            os.environ.pop("XDG_CONFIG_HOME", None)
        else:
            os.environ["XDG_CONFIG_HOME"] = self._prev_xdg

    def _write_raw(self, text: str) -> None:
        self.prof.PROFILE_FILE.parent.mkdir(parents=True, exist_ok=True)
        self.prof.PROFILE_FILE.write_text(text)
        # Force a fresh disk read regardless of mtime granularity.
        self.prof._cache = None
        self.prof._cache_mtime = 0.0

    def test_list_payload_coerced_to_dict(self):
        self._write_raw(_NON_DICT_PAYLOADS["list"])
        d = self.prof.load(force=True)
        self.assertIsInstance(d, dict)
        # Must not raise AttributeError -- this is the exact reported crash.
        self.assertIsNone(d.get("display_language"))

    def test_string_payload_coerced_to_dict(self):
        self._write_raw(_NON_DICT_PAYLOADS["string"])
        d = self.prof.load(force=True)
        self.assertIsInstance(d, dict)
        self.assertIsNone(d.get("display_language"))

    def test_null_payload_coerced_to_dict(self):
        self._write_raw(_NON_DICT_PAYLOADS["null"])
        d = self.prof.load(force=True)
        self.assertIsInstance(d, dict)
        self.assertIsNone(d.get("display_language"))

    def test_number_payload_coerced_to_dict(self):
        self._write_raw(_NON_DICT_PAYLOADS["number"])
        d = self.prof.load(force=True)
        self.assertIsInstance(d, dict)
        self.assertIsNone(d.get("display_language"))


class LangCliMalformedProfileTests(unittest.TestCase):
    """`/lang show` and `/lang set` run as real subprocesses against a
    malformed (valid-JSON, non-dict) profile.json must degrade cleanly
    instead of crashing with an uncaught traceback."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="lang-cli-malformed-test-"))
        self.env = os.environ.copy()
        self.env["XDG_CONFIG_HOME"] = str(self.tmp)
        (self.tmp / "corvin-voice").mkdir(parents=True, exist_ok=True)
        self.profile_file = self.tmp / "corvin-voice" / "profile.json"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, *args):
        r = subprocess.run(
            ["python3", str(LANG_CLI), *args],
            capture_output=True, text=True, env=self.env,
        )
        return r

    def _write_raw(self, text: str) -> None:
        self.profile_file.write_text(text)

    def _assert_clean_json_ok(self, r: subprocess.CompletedProcess) -> dict:
        self.assertEqual(
            r.returncode, 0,
            f"expected a clean exit, got rc={r.returncode}; "
            f"stderr={r.stderr!r}",
        )
        self.assertNotIn(
            "Traceback", r.stderr,
            f"CLI crashed with an uncaught exception instead of degrading: "
            f"{r.stderr!r}",
        )
        try:
            payload = json.loads(r.stdout)
        except json.JSONDecodeError:
            self.fail(f"stdout was not valid JSON: {r.stdout!r} (stderr={r.stderr!r})")
        self.assertTrue(payload.get("ok"))
        return payload

    def test_show_against_list_profile_does_not_crash(self):
        self._write_raw(_NON_DICT_PAYLOADS["list"])
        r = self._run("show")
        payload = self._assert_clean_json_ok(r)
        # Falls back to the "unset" default, same as an empty profile.
        self.assertFalse(payload["set"])
        self.assertEqual(payload["code"], "en")

    def test_show_against_string_profile_does_not_crash(self):
        self._write_raw(_NON_DICT_PAYLOADS["string"])
        r = self._run("show")
        payload = self._assert_clean_json_ok(r)
        self.assertFalse(payload["set"])

    def test_show_against_null_profile_does_not_crash(self):
        self._write_raw(_NON_DICT_PAYLOADS["null"])
        r = self._run("show")
        payload = self._assert_clean_json_ok(r)
        self.assertFalse(payload["set"])

    def test_show_against_number_profile_does_not_crash(self):
        self._write_raw(_NON_DICT_PAYLOADS["number"])
        r = self._run("show")
        payload = self._assert_clean_json_ok(r)
        self.assertFalse(payload["set"])

    def test_set_against_list_profile_does_not_crash_and_persists(self):
        self._write_raw(_NON_DICT_PAYLOADS["list"])
        r = self._run("set", "de")
        payload = self._assert_clean_json_ok(r)
        self.assertEqual(payload["code"], "de")
        # The malformed file must have been replaced by a proper object,
        # not left as-is or merged incorrectly.
        on_disk = json.loads(self.profile_file.read_text())
        self.assertIsInstance(on_disk, dict)
        self.assertEqual(on_disk.get("display_language"), "de")

        # A follow-up `show` must now see the value that was just set.
        r2 = self._run("show")
        payload2 = self._assert_clean_json_ok(r2)
        self.assertTrue(payload2["set"])
        self.assertEqual(payload2["code"], "de")


if __name__ == "__main__":
    unittest.main(verbosity=2)
