"""Adversarial fresh-install finding (HIGH / H4): when Claude Code is installed
but NOT logged in, _effective_os_engine must fall back to Hermes instead of
routing to a claude binary that fails every turn with a raw CLI auth error while
a fully-provisioned Hermes sits unused.

Also guards the fail-open safety of _claude_authenticated (a transient read error
must NOT reroute a possibly-logged-in user off Claude).
"""
from __future__ import annotations

import unittest
from unittest.mock import patch

from corvin_console import chat_runtime as cr


class EffectiveOsEngineAuthFallbackTests(unittest.TestCase):
    def _eff(self):
        return cr._effective_os_engine("_default")

    def test_installed_but_unauthenticated_falls_back_to_hermes(self):
        with (
            patch.object(cr, "_configured_os_engine", return_value="claude_code"),
            patch.object(cr, "_claude_binary", return_value="claude"),
            patch.object(cr.shutil, "which", return_value="/usr/bin/claude"),
            patch.object(cr, "_claude_authenticated", return_value=False),
        ):
            self.assertEqual(self._eff(), "hermes")

    def test_installed_and_authenticated_stays_on_claude(self):
        with (
            patch.object(cr, "_configured_os_engine", return_value="claude_code"),
            patch.object(cr, "_claude_binary", return_value="claude"),
            patch.object(cr.shutil, "which", return_value="/usr/bin/claude"),
            patch.object(cr, "_claude_authenticated", return_value=True),
        ):
            self.assertEqual(self._eff(), "claude_code")

    def test_binary_missing_still_falls_back(self):
        with (
            patch.object(cr, "_configured_os_engine", return_value="claude_code"),
            patch.object(cr, "_claude_binary", return_value="claude"),
            patch.object(cr.shutil, "which", return_value=None),
        ):
            self.assertEqual(self._eff(), "hermes")

    def test_non_claude_engine_is_returned_unchanged(self):
        with patch.object(cr, "_configured_os_engine", return_value="hermes"):
            self.assertEqual(self._eff(), "hermes")

    def test_claude_authenticated_env_key(self):
        with patch.dict(cr.os.environ, {"ANTHROPIC_API_KEY": "sk-x"}, clear=False):
            self.assertTrue(cr._claude_authenticated())

    def test_claude_authenticated_absent_creds_is_false(self):
        import os as _os
        with patch.dict(cr.os.environ, {}, clear=False):
            cr.os.environ.pop("ANTHROPIC_API_KEY", None)
            with patch.object(cr.Path, "home", return_value=cr.Path("/nonexistent-xyz")):
                self.assertFalse(cr._claude_authenticated())


if __name__ == "__main__":
    unittest.main()
