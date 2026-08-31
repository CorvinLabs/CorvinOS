"""Per-subtask E2E for Layer-29.5 helper_model resolver.

Covers resolution order, opt-out semantics, argv composition, once-
per-process announce log, and the no-SDK structural invariant.
"""

from __future__ import annotations

import ast
import io
import os
import sys
import tempfile
import unittest
import unittest.mock as mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import helper_model  # noqa: E402


class ResolveOrderTests(unittest.TestCase):

    def setUp(self) -> None:
        # Snapshot env, clear all helper vars for the test
        self._snapshot = {k: os.environ.pop(k) for k in list(os.environ)
                          if k.startswith("CORVIN_HELPER_MODEL")}

    def tearDown(self) -> None:
        for k in list(os.environ):
            if k.startswith("CORVIN_HELPER_MODEL"):
                del os.environ[k]
        os.environ.update(self._snapshot)

    def test_default_is_haiku_4_5(self) -> None:
        self.assertEqual(
            helper_model.resolve_helper_model(helper_model.SITE_VOICE_SUMMARY),
            "claude-haiku-4-5-20251001",
        )

    def test_global_env_overrides_default(self) -> None:
        os.environ["CORVIN_HELPER_MODEL"] = "claude-sonnet-4-6"
        self.assertEqual(
            helper_model.resolve_helper_model(helper_model.SITE_VOICE_SUMMARY),
            "claude-sonnet-4-6",
        )

    def test_per_site_overrides_global(self) -> None:
        os.environ["CORVIN_HELPER_MODEL"] = "claude-sonnet-4-6"
        os.environ["CORVIN_HELPER_MODEL_VOICE_SUMMARY"] = "claude-opus-4-7"
        self.assertEqual(
            helper_model.resolve_helper_model(helper_model.SITE_VOICE_SUMMARY),
            "claude-opus-4-7",
        )

    def test_per_site_does_not_leak_across_sites(self) -> None:
        os.environ["CORVIN_HELPER_MODEL_VOICE_SUMMARY"] = "claude-opus-4-7"
        # Different site → still falls through to default
        self.assertEqual(
            helper_model.resolve_helper_model(helper_model.SITE_DIALECTIC_CLI),
            "claude-haiku-4-5-20251001",
        )


class OptOutTests(unittest.TestCase):

    def setUp(self) -> None:
        self._snapshot = {k: os.environ.pop(k) for k in list(os.environ)
                          if k.startswith("CORVIN_HELPER_MODEL")}

    def tearDown(self) -> None:
        for k in list(os.environ):
            if k.startswith("CORVIN_HELPER_MODEL"):
                del os.environ[k]
        os.environ.update(self._snapshot)

    def test_global_opt_out_empty_string(self) -> None:
        os.environ["CORVIN_HELPER_MODEL"] = ""
        self.assertIsNone(
            helper_model.resolve_helper_model(helper_model.SITE_VOICE_SUMMARY)
        )

    def test_global_opt_out_keyword_none(self) -> None:
        os.environ["CORVIN_HELPER_MODEL"] = "none"
        self.assertIsNone(
            helper_model.resolve_helper_model(helper_model.SITE_VOICE_SUMMARY)
        )

    def test_global_opt_out_keyword_default(self) -> None:
        os.environ["CORVIN_HELPER_MODEL"] = "default"
        self.assertIsNone(
            helper_model.resolve_helper_model(helper_model.SITE_VOICE_SUMMARY)
        )

    def test_per_site_opt_out_overrides_global_pin(self) -> None:
        os.environ["CORVIN_HELPER_MODEL"] = "claude-sonnet-4-6"
        os.environ["CORVIN_HELPER_MODEL_VOICE_SUMMARY"] = ""
        self.assertIsNone(
            helper_model.resolve_helper_model(helper_model.SITE_VOICE_SUMMARY)
        )

    def test_per_site_opt_out_does_not_leak(self) -> None:
        # Opt-out for ONE site, other sites still resolve to default
        os.environ["CORVIN_HELPER_MODEL_VOICE_SUMMARY"] = "none"
        self.assertIsNone(
            helper_model.resolve_helper_model(helper_model.SITE_VOICE_SUMMARY)
        )
        self.assertEqual(
            helper_model.resolve_helper_model(helper_model.SITE_DIALECTIC_CLI),
            "claude-haiku-4-5-20251001",
        )


class ClaudeArgsTests(unittest.TestCase):

    def setUp(self) -> None:
        self._snapshot = {k: os.environ.pop(k) for k in list(os.environ)
                          if k.startswith("CORVIN_HELPER_MODEL")}

    def tearDown(self) -> None:
        for k in list(os.environ):
            if k.startswith("CORVIN_HELPER_MODEL"):
                del os.environ[k]
        os.environ.update(self._snapshot)

    def test_args_carries_haiku_by_default(self) -> None:
        self.assertEqual(
            helper_model.claude_args(helper_model.SITE_DIALECTIC_CLI),
            ["--model", "claude-haiku-4-5-20251001"],
        )

    def test_args_empty_on_opt_out(self) -> None:
        os.environ["CORVIN_HELPER_MODEL"] = "none"
        self.assertEqual(
            helper_model.claude_args(helper_model.SITE_DIALECTIC_CLI),
            [],
        )

    def test_args_uses_per_site_override(self) -> None:
        os.environ["CORVIN_HELPER_MODEL_USER_STYLE_JUDGE"] = "claude-haiku-4-5-20251001"
        self.assertEqual(
            helper_model.claude_args(helper_model.SITE_USER_STYLE_JUDGE),
            ["--model", "claude-haiku-4-5-20251001"],
        )


class AnnounceTests(unittest.TestCase):

    def setUp(self) -> None:
        helper_model._reset_for_tests()
        self._snapshot = {k: os.environ.pop(k) for k in list(os.environ)
                          if k.startswith("CORVIN_HELPER_MODEL")}

    def tearDown(self) -> None:
        for k in list(os.environ):
            if k.startswith("CORVIN_HELPER_MODEL"):
                del os.environ[k]
        os.environ.update(self._snapshot)
        helper_model._reset_for_tests()

    def test_announce_emits_once_per_site(self) -> None:
        buf = io.StringIO()
        helper_model.announce(helper_model.SITE_VOICE_SUMMARY, stream=buf)
        helper_model.announce(helper_model.SITE_VOICE_SUMMARY, stream=buf)
        # Second call is silent
        lines = [l for l in buf.getvalue().splitlines() if l.strip()]
        self.assertEqual(len(lines), 1)
        self.assertIn("voice_summary", lines[0])
        self.assertIn("claude-haiku-4-5-20251001", lines[0])

    def test_announce_emits_per_site(self) -> None:
        buf = io.StringIO()
        helper_model.announce(helper_model.SITE_VOICE_SUMMARY, stream=buf)
        helper_model.announce(helper_model.SITE_DIALECTIC_CLI, stream=buf)
        lines = [l for l in buf.getvalue().splitlines() if l.strip()]
        self.assertEqual(len(lines), 2)

    def test_announce_reflects_opt_out(self) -> None:
        os.environ["CORVIN_HELPER_MODEL_VOICE_SUMMARY"] = "none"
        buf = io.StringIO()
        helper_model.announce(helper_model.SITE_VOICE_SUMMARY, stream=buf)
        out = buf.getvalue()
        self.assertIn("voice_summary", out)
        self.assertIn("<cli-default>", out)


class NoSdkImportContractTests(unittest.TestCase):
    """Layer-29.5 cost-contract gate. The helper-model module MUST
    NOT import anthropic — any LLM round-trip goes through the
    operator's Max subscription via the ``claude -p`` subprocess
    pattern, never through the SDK."""

    def test_no_anthropic_or_openai_import(self) -> None:
        src = (Path(__file__).resolve().parent / "helper_model.py").read_text()
        tree = ast.parse(src)
        forbidden = {"anthropic", "openai"}
        offenders: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.split(".", 1)[0] in forbidden:
                        offenders.append(alias.name)
            elif isinstance(node, ast.ImportFrom):
                if node.module and node.module.split(".", 1)[0] in forbidden:
                    offenders.append(node.module)
        self.assertEqual(
            offenders, [],
            "helper_model.py imports forbidden SDK(s): " + ", ".join(offenders),
        )


class ResolveClaudeBinTests(unittest.TestCase):
    """Resolver for helper ``claude -p`` spawns — guards the L44 fail-closed
    regression where a stripped systemd PATH made the bare name unspawnable."""

    def setUp(self) -> None:
        self._snap = {k: os.environ.get(k) for k in
                      ("CORVIN_CLAUDE_BIN", "CORVIN_CLAUDE_BIN_FALLBACKS", "PATH")}

    def tearDown(self) -> None:
        for k, v in self._snap.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_absolute_pin_honoured_as_is(self) -> None:
        os.environ["CORVIN_CLAUDE_BIN"] = "/opt/custom/claude"
        os.environ["PATH"] = "/usr/bin:/bin"
        self.assertEqual(helper_model.resolve_claude_bin(), "/opt/custom/claude")

    def test_stripped_path_falls_back_to_known_location(self) -> None:
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            fake = Path(d) / "claude"
            fake.write_text("#!/bin/sh\nexit 0\n")
            fake.chmod(0o755)
            os.environ.pop("CORVIN_CLAUDE_BIN", None)
            os.environ["PATH"] = "/nonexistent-dir-xyz"
            os.environ["CORVIN_CLAUDE_BIN_FALLBACKS"] = str(fake)
            self.assertEqual(helper_model.resolve_claude_bin(), str(fake))

    def test_no_pin_resolves_via_path(self) -> None:
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            fake = Path(d) / "claude"
            fake.write_text("#!/bin/sh\nexit 0\n")
            fake.chmod(0o755)
            os.environ.pop("CORVIN_CLAUDE_BIN", None)
            os.environ.pop("CORVIN_CLAUDE_BIN_FALLBACKS", None)
            os.environ["PATH"] = d
            self.assertEqual(helper_model.resolve_claude_bin(), str(fake))


class WindowsBinFallbacksTests(unittest.TestCase):
    """helper_model.py's own copy of the Windows npm-shim fallback logic
    (ADR-0265 P1) — must mirror agents.claude_code._windows_bin_fallbacks
    since this module is deliberately dependency-free and can't import
    the engine package to share the implementation."""

    def setUp(self) -> None:
        self._snap = {k: os.environ.get(k) for k in
                      ("APPDATA", "USERPROFILE", "CORVIN_CLAUDE_BIN",
                       "CORVIN_CLAUDE_BIN_FALLBACKS", "PATH")}

    def tearDown(self) -> None:
        for k, v in self._snap.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_windows_bin_fallbacks_reads_appdata_and_userprofile(self) -> None:
        os.environ["APPDATA"] = r"C:\Users\op\AppData\Roaming"
        os.environ["USERPROFILE"] = r"C:\Users\op"
        cands = helper_model._windows_bin_fallbacks()
        self.assertIn(os.path.join(r"C:\Users\op\AppData\Roaming", "npm", "claude.cmd"), cands)
        self.assertIn(os.path.join(r"C:\Users\op", ".local", "bin", "claude.exe"), cands)

    def test_resolve_claude_bin_finds_windows_npm_shim_when_platform_is_windows(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            appdata = Path(d) / "AppData" / "Roaming"
            npm_dir = appdata / "npm"
            npm_dir.mkdir(parents=True)
            shim = npm_dir / "claude.cmd"
            shim.write_text("@echo off\n")
            os.environ["APPDATA"] = str(appdata)
            os.environ.pop("USERPROFILE", None)
            os.environ.pop("CORVIN_CLAUDE_BIN", None)
            os.environ.pop("CORVIN_CLAUDE_BIN_FALLBACKS", None)
            os.environ["PATH"] = "/nonexistent-dir-xyz"
            with mock.patch.object(helper_model.sys, "platform", "win32"), \
                 mock.patch.object(helper_model.shutil, "which", return_value=None), \
                 mock.patch.object(helper_model.os, "access", return_value=True):
                resolved = helper_model.resolve_claude_bin()
            self.assertEqual(resolved, str(shim))

    def test_resolve_claude_bin_ignores_windows_fallbacks_on_posix(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            appdata = Path(d) / "AppData" / "Roaming"
            npm_dir = appdata / "npm"
            npm_dir.mkdir(parents=True)
            shim = npm_dir / "claude.cmd"
            shim.write_text("@echo off\n")
            os.environ["APPDATA"] = str(appdata)
            os.environ.pop("CORVIN_CLAUDE_BIN", None)
            os.environ.pop("CORVIN_CLAUDE_BIN_FALLBACKS", None)
            os.environ["PATH"] = "/nonexistent-dir-xyz"
            with mock.patch.object(helper_model.sys, "platform", "linux"), \
                 mock.patch.object(helper_model.shutil, "which", return_value=None):
                resolved = helper_model.resolve_claude_bin()
            self.assertNotEqual(resolved, str(shim))


class AllSitesCoverageTests(unittest.TestCase):
    """Catches missed sites — if a new helper-site constant is added
    to helper_model.py, ALL_SITES must include it (so dashboard panels
    + operator env-var docs stay in sync)."""

    def test_all_sites_contains_every_site_constant(self) -> None:
        site_attrs = [
            getattr(helper_model, name)
            for name in dir(helper_model)
            if name.startswith("SITE_")
        ]
        for s in site_attrs:
            self.assertIn(s, helper_model.ALL_SITES,
                          f"SITE_* constant {s!r} missing from ALL_SITES")


if __name__ == "__main__":
    unittest.main()
