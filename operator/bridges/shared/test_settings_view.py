"""test_settings_view.py — per-subtask E2E for the /settings aggregator.

Covers:
  - render_paths_block(): exact path layout, persona add_dirs surfacing,
    JID-normalised fallback for chat_key lookups.
  - render_session_block(): persona / permission / role / quota / consent
    fields against a sandboxed corvin home with seeded settings.json +
    persona file + roles / quota entries.
  - render_system_block(): tenant config rendering, bridges
    check across canonical + legacy locations, audit-chain probe,
    autoupdate marker detection.
  - render_settings(): full round-trip — every block appears in the
    expected order, contains the title, and is byte-stable across two
    invocations (no env / mtime leaks).
  - CLI: `python settings_view.py render <channel> <chat_key>`
    returns 0 + prints the same output to stdout.
  - Empty-tenant case: a fresh tenant tree with no chat profile, no
    persona, no quota entries still renders without crashing (every
    section degrades to "—").
  - Language switch: --lang en flips headings + label strings.
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
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import settings_view  # noqa: E402
import paths  # noqa: E402


CHANNEL = "telegram"
CHAT = "150210"
OWNER_UID = "owner-sv"


class _SettingsTestBase(unittest.TestCase):
    """Per-test sandbox: CORVIN_HOME → tempdir, channel settings file
    seeded in the canonical (ADR-0008) location. Patches the persona
    file lookup at the module-private helper so the test-bundle dir
    can be redirected."""

    def setUp(self) -> None:
        self._tmp = tempfile.mkdtemp(prefix="settings-view-")
        self._orig_corvin_home = os.environ.get("CORVIN_HOME")
        self._orig_tenant_id = os.environ.get("CORVIN_TENANT_ID")
        os.environ["CORVIN_HOME"] = self._tmp
        os.environ.pop("CORVIN_TENANT_ID", None)
        # Seed the canonical bridge settings location.
        self._chan_dir = Path(self._tmp) / "bridges" / CHANNEL
        self._chan_dir.mkdir(parents=True)
        self._settings_path = self._chan_dir / "settings.json"
        # Write a minimal non-empty settings file so _read_channel_settings
        # returns a truthy dict and the bridge shows as ✓ in _bridges_summary.
        self._settings_path.write_text(json.dumps({"enabled": True}))
        # Seed persona dir + minimal coder + research persona.
        self._persona_dir = Path(self._tmp) / "personas-test"
        self._persona_dir.mkdir()
        (self._persona_dir / "coder.json").write_text(json.dumps({
            "name": "coder",
            "description": "default engineering persona",
            "permission_mode": "bypassPermissions",
            "add_dirs": ["/home/test/projects/foo"],
        }))
        (self._persona_dir / "research.json").write_text(json.dumps({
            "name": "research",
            "permission_mode": "default",
            "working_dir": "/home/test/research",
            "add_dirs": ["/home/test/research/a", "/home/test/research/b",
                         "/home/test/research/c", "/home/test/research/d"],
        }))
        self._orig_persona_files = settings_view._persona_files
        settings_view._persona_files = lambda: [self._persona_dir]

    def tearDown(self) -> None:
        settings_view._persona_files = self._orig_persona_files
        if self._orig_corvin_home is not None:
            os.environ["CORVIN_HOME"] = self._orig_corvin_home
        else:
            os.environ.pop("CORVIN_HOME", None)
        if self._orig_tenant_id is not None:
            os.environ["CORVIN_TENANT_ID"] = self._orig_tenant_id
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _write_settings(self, body: dict) -> None:
        self._settings_path.write_text(json.dumps(body), encoding="utf-8")


# ── PATHS block ────────────────────────────────────────────────────────────

class PathsBlockTests(_SettingsTestBase):
    def test_corvin_home_visible(self):
        lines = settings_view.render_paths_block(CHANNEL, CHAT, lang="de")
        joined = "\n".join(lines)
        self.assertIn("Corvin home", joined)
        # Must contain the sandbox dir somewhere (under ~ or absolute).
        self.assertTrue(any(self._tmp in ln or "~" in ln for ln in lines))

    def test_default_tenant_id(self):
        lines = settings_view.render_paths_block(CHANNEL, CHAT, lang="de")
        self.assertIn("• Tenant: _default", "\n".join(lines))

    def test_session_dir_path_shape(self):
        lines = settings_view.render_paths_block(CHANNEL, CHAT, lang="de")
        joined = "\n".join(lines)
        self.assertIn(f"{CHANNEL}:{CHAT}", joined)
        self.assertIn("sessions/telegram:150210", joined)

    def test_voice_state_path_shape(self):
        lines = settings_view.render_paths_block(CHANNEL, CHAT, lang="de")
        joined = "\n".join(lines)
        self.assertIn(f"voice/sessions/{CHANNEL}/{CHAT}", joined)

    def test_persona_dirs_from_chat_profile(self):
        self._write_settings({"chat_profiles": {CHAT: {"persona": "research"}}})
        lines = settings_view.render_paths_block(CHANNEL, CHAT, lang="de")
        joined = "\n".join(lines)
        # research persona has working_dir + 4 add_dirs (truncated to 3 + counter)
        self.assertIn("working_dir=", joined)
        self.assertIn("add_dirs=", joined)
        self.assertIn("(+1)", joined)  # 4 dirs, show 3, suffix says +1

    def test_no_cwd_line(self):
        # cwd was removed in favour of voice state + session dir; the
        # field should not appear at all.
        lines = settings_view.render_paths_block(CHANNEL, CHAT, lang="de")
        joined = "\n".join(lines)
        self.assertNotIn("• cwd:", joined)
        self.assertNotIn("• cwd ", joined)

    def test_jid_normalised_chat_key_fallback(self):
        """A chat_key like 'foo@s.whatsapp.net' should match a profile
        entry keyed by the bare 'foo' part."""
        self._write_settings({"chat_profiles": {"foo": {"persona": "research"}}})
        lines = settings_view.render_paths_block(
            CHANNEL, "foo@s.whatsapp.net", lang="de")
        joined = "\n".join(lines)
        self.assertIn("working_dir=", joined)


# ── SESSION block ──────────────────────────────────────────────────────────

class SessionBlockTests(_SettingsTestBase):
    def test_persona_default_coder_when_no_profile(self):
        lines = settings_view.render_session_block(CHANNEL, CHAT, lang="de")
        joined = "\n".join(lines)
        self.assertIn("Persona:", joined)
        self.assertIn("coder", joined)

    def test_persona_from_chat_profile(self):
        self._write_settings({"chat_profiles": {CHAT: {"persona": "research"}}})
        lines = settings_view.render_session_block(CHANNEL, CHAT, lang="de")
        joined = "\n".join(lines)
        self.assertIn("research", joined)

    def test_permission_falls_back_to_persona(self):
        self._write_settings({"chat_profiles": {CHAT: {"persona": "research"}}})
        lines = settings_view.render_session_block(CHANNEL, CHAT, lang="de")
        joined = "\n".join(lines)
        # research persona declares "default"
        self.assertIn("default", joined)

    def test_role_and_quota_dashes_without_uid(self):
        lines = settings_view.render_session_block(CHANNEL, CHAT, lang="de")
        joined = "\n".join(lines)
        # Without uid, both fields read "—"
        self.assertIn("Role:        —", joined)
        self.assertIn("Quota:       —", joined)

    def test_consent_default_observer_off(self):
        lines = settings_view.render_session_block(CHANNEL, CHAT, lang="de")
        joined = "\n".join(lines)
        self.assertIn("Observer-Modus: off", joined)

    def test_consent_observer_transcript_when_enabled(self):
        self._write_settings({
            "chat_profiles": {CHAT: {"observer_visibility": "transcript"}},
        })
        lines = settings_view.render_session_block(CHANNEL, CHAT, lang="de")
        joined = "\n".join(lines)
        self.assertIn("Observer-Modus: transcript", joined)


# ── SYSTEM block ───────────────────────────────────────────────────────────

class SystemBlockTests(_SettingsTestBase):
    def test_tenant_id_default(self):
        lines = settings_view.render_system_block(lang="de")
        joined = "\n".join(lines)
        self.assertIn("• Tenant:       _default", joined)

    def test_bridges_check_picks_up_seeded_channel(self):
        # CHANNEL was seeded in setUp → must read as ✓
        lines = settings_view.render_system_block(lang="de")
        joined = "\n".join(lines)
        self.assertIn("TG ✓", joined)

    def test_bridges_check_legacy_location(self):
        """settings.json under the legacy in-repo path is also detected."""
        # Seed the legacy whatsapp path: <repo>/operator/bridges/whatsapp/settings.json
        legacy = paths.legacy_bridge_runtime_dir("whatsapp", "root")
        self.assertIsNotNone(legacy, "legacy path resolver returned None — repo root not found")
        legacy_settings = legacy / "settings.json"
        cleanup_legacy = not legacy_settings.is_file()
        if cleanup_legacy:
            legacy.mkdir(parents=True, exist_ok=True)
            # Write a non-empty dict — _read_channel_settings returns {} for
            # empty/falsy dicts, which would mark the bridge as ✗.
            legacy_settings.write_text(json.dumps({"enabled": True}))
        try:
            lines = settings_view.render_system_block(lang="de")
            self.assertIn("WA ✓", "\n".join(lines))
        finally:
            if cleanup_legacy and legacy_settings.is_file():
                legacy_settings.unlink()

    def test_engine_default_is_layer22(self):
        lines = settings_view.render_system_block(lang="de")
        self.assertIn("claude_code (Layer 22)", "\n".join(lines))

    def test_engine_legacy_when_opt_out(self):
        os.environ["CORVIN_USE_ENGINE_LAYER"] = "0"
        try:
            lines = settings_view.render_system_block(lang="de")
            self.assertIn("legacy direct-spawn", "\n".join(lines))
        finally:
            os.environ.pop("CORVIN_USE_ENGINE_LAYER", None)

    def test_stt_default_chain(self):
        lines = settings_view.render_system_block(lang="de")
        self.assertIn("openai → local", "\n".join(lines))

    def test_stt_pinned_override(self):
        os.environ["CORVIN_STT_PROVIDER"] = "local"
        try:
            lines = settings_view.render_system_block(lang="de")
            self.assertIn("pinned=local", "\n".join(lines))
        finally:
            os.environ.pop("CORVIN_STT_PROVIDER", None)

    def test_audit_chain_present(self):
        chain = paths.tenant_global_dir() / "forge"
        chain.mkdir(parents=True, exist_ok=True)
        (chain / "audit.jsonl").write_text('{"a":1}\n')
        lines = settings_view.render_system_block(lang="de")
        self.assertIn("present", "\n".join(lines))

    def test_audit_chain_missing(self):
        lines = settings_view.render_system_block(lang="de")
        self.assertIn("Audit chain:  —", "\n".join(lines))


# ── full render + CLI ──────────────────────────────────────────────────────

class FullRenderTests(_SettingsTestBase):
    def test_three_block_order(self):
        out = settings_view.render_settings(CHANNEL, CHAT, lang="de")
        # All three section headers in the expected order.
        ip = out.index("━━ WORKING / PFADE ━━")
        is_ = out.index("━━ SESSION ━━")
        iy = out.index("━━ SYSTEM ━━")
        self.assertLess(ip, is_)
        self.assertLess(is_, iy)
        self.assertIn("🔧 Corvin — Settings", out)

    def test_byte_stable_across_two_calls(self):
        a = settings_view.render_settings(CHANNEL, CHAT, lang="de")
        b = settings_view.render_settings(CHANNEL, CHAT, lang="de")
        self.assertEqual(a, b)

    def test_english_headers(self):
        out = settings_view.render_settings(CHANNEL, CHAT, lang="en")
        self.assertIn("━━ WORKING / PATHS ━━", out)
        self.assertIn("Observer mode:", out)

    def test_unknown_lang_falls_back_to_de(self):
        out = settings_view.render_settings(CHANNEL, CHAT, lang="zz")
        self.assertIn("━━ WORKING / PFADE ━━", out)

    def test_empty_tenant_no_crash(self):
        # Fresh sandbox already has no chat profile, no audit, no quota.
        out = settings_view.render_settings(CHANNEL, "fresh-chat", lang="de")
        # Must include all three blocks even on a near-empty tree.
        self.assertIn("━━ WORKING / PFADE ━━", out)
        self.assertIn("━━ SESSION ━━", out)
        self.assertIn("━━ SYSTEM ━━", out)


class CliTests(_SettingsTestBase):
    def test_cli_render_roundtrip(self):
        script = Path(settings_view.__file__).resolve()
        env = os.environ.copy()
        env["CORVIN_HOME"] = self._tmp
        r = subprocess.run(
            [sys.executable, str(script), "render", CHANNEL, CHAT,
             "--lang", "de"],
            env=env, capture_output=True, text=True, timeout=20,
        )
        self.assertEqual(r.returncode, 0,
                         f"CLI failed: {r.stderr}")
        self.assertIn("Corvin — Settings", r.stdout)
        self.assertIn("━━ SESSION ━━", r.stdout)

    def test_cli_help_exits_zero(self):
        script = Path(settings_view.__file__).resolve()
        r = subprocess.run(
            [sys.executable, str(script), "--help"],
            capture_output=True, text=True, timeout=10,
        )
        self.assertEqual(r.returncode, 0)
        self.assertIn("usage:", r.stdout)

    def test_cli_missing_args_exits_nonzero(self):
        script = Path(settings_view.__file__).resolve()
        r = subprocess.run(
            [sys.executable, str(script), "render"],
            capture_output=True, text=True, timeout=10,
        )
        self.assertNotEqual(r.returncode, 0)

    def test_cli_unknown_lang_falls_back(self):
        script = Path(settings_view.__file__).resolve()
        env = os.environ.copy()
        env["CORVIN_HOME"] = self._tmp
        r = subprocess.run(
            [sys.executable, str(script), "render", CHANNEL, CHAT,
             "--lang", "klingon"],
            env=env, capture_output=True, text=True, timeout=20,
        )
        self.assertEqual(r.returncode, 0)
        self.assertIn("━━ WORKING / PFADE ━━", r.stdout)


if __name__ == "__main__":
    unittest.main()
