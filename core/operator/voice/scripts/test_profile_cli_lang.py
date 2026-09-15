#!/usr/bin/env python3
"""Regression tests for profile_cli.py's `set display_language=...` path.

Bug (confirmed 2026-07-12, traced via a live deployment): unlike `/lang
set <code>` (lang_cli.py), which validates through i18n.normalise() before
storing, the generic `/profile set key=value` command stored
`display_language` completely unvalidated. A bare "zh" (not the canonical
"zh-Hans") persisted verbatim, and every downstream i18n.t() lookup
(welcome greeting, voice-summary language pin) then silently fell through
its own fallback chain to English -- not the configured language, not the
user's actual language. See docs/troubleshooting.md #34.

Fix: `cmd_set` now special-cases `display_language`, routing it through the
same i18n.normalise() call `/lang set` already uses, and refuses to store an
unrecognisable code instead of accepting it verbatim.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(_SCRIPTS))
_SHARED = _SCRIPTS.parent.parent / "bridges" / "shared"
sys.path.insert(0, str(_SHARED))


class ProfileCliDisplayLanguageTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._prev_xdg = os.environ.get("XDG_CONFIG_HOME")
        os.environ["XDG_CONFIG_HOME"] = self._tmp
        for m in ("profile", "i18n", "profile_cli"):
            sys.modules.pop(m, None)
        import profile_cli  # noqa: PLC0415
        import profile as prof  # noqa: PLC0415
        self.profile_cli = profile_cli
        self.prof = prof

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmp, ignore_errors=True)
        if self._prev_xdg is None:
            os.environ.pop("XDG_CONFIG_HOME", None)
        else:
            os.environ["XDG_CONFIG_HOME"] = self._prev_xdg

    def test_bare_zh_is_normalised_to_zh_hans_not_stored_verbatim(self):
        """The exact reported bug: a bare 'zh' must never be persisted
        unnormalised -- that's the value that silently broke the welcome
        greeting's i18n lookups."""
        rc = self.profile_cli.cmd_set(["display_language=zh"])
        self.assertEqual(rc, 0)
        self.assertEqual(self.prof.get("display_language"), "zh-Hans")

    def test_already_normalised_code_round_trips_unchanged(self):
        rc = self.profile_cli.cmd_set(["display_language=de"])
        self.assertEqual(rc, 0)
        self.assertEqual(self.prof.get("display_language"), "de")

    def test_case_and_underscore_variants_normalise(self):
        rc = self.profile_cli.cmd_set(["display_language=DE"])
        self.assertEqual(rc, 0)
        self.assertEqual(self.prof.get("display_language"), "de")

    def test_unrecognisable_code_is_refused_not_stored(self):
        rc = self.profile_cli.cmd_set(["display_language=not-a-real-lang!!!"])
        self.assertEqual(rc, 2, "an unrecognisable code must be refused (non-zero exit)")
        self.assertIsNone(
            self.prof.get("display_language"),
            "a refused value must never be persisted",
        )

    def test_other_keys_are_unaffected_by_the_language_special_case(self):
        """The special-case must only apply to display_language -- every
        other /profile set key keeps its existing (unvalidated) behavior."""
        rc = self.profile_cli.cmd_set(["name=Silvio"])
        self.assertEqual(rc, 0)
        self.assertEqual(self.prof.get("name"), "Silvio")


if __name__ == "__main__":
    unittest.main(verbosity=2)
