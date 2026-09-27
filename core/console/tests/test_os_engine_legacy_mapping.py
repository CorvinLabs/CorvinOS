"""ADR-2087 — Hermes / local Ollama removed from the console OS-turn path.

Drives the real ``chat_runtime.stream_turn`` resolver (and the WebSocket
pre-turn guard ``get_engine_unavailable_message``) against a real
``tenant.corvin.yaml`` under a temp CORVIN_HOME:

  (a) a stored legacy ``spec.default_engine: hermes`` (or ``hermes-*``,
      ``ollama``, ``claude_code_local`` …) is MAPPED on read — the turn runs on
      claude_code and spawns the claude CLI, never a Hermes engine;
  (b) claude missing → the turn stays on claude_code and yields an actionable
      "not found" error pointing at the Engines setup — no Hermes fallback;
  (c) claude present but not signed in → actionable "auth login" error, no
      spawn, no Hermes fallback;
  (d) ``os_turn.engine_substituted`` is no longer emitted (no target left).
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

_THIS = Path(__file__).resolve()
_REPO = _THIS.parents[3]
sys.path.insert(0, str(_REPO / "core" / "console"))
sys.path.insert(0, str(_REPO / "corvin_operator" / "bridges" / "shared"))
sys.path.insert(0, str(_REPO / "corvin_operator" / "forge"))


def _drain(agen):
    async def _collect():
        out = []
        async for ev in agen:
            out.append(ev)
        return out
    return asyncio.run(_collect())


class LegacyEngineMappingE2E(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self._env_prev = {k: os.environ.get(k) for k in
                          ("CORVIN_HOME", "CORVIN_TENANT_ID", "CORVIN_CLAUDE_BIN")}
        os.environ["CORVIN_HOME"] = self.tmp.name
        os.environ["CORVIN_TENANT_ID"] = "_default"
        os.environ.pop("VOICE_AUDIT_PATH", None)

        import importlib
        from corvin_console import chat_runtime
        importlib.reload(chat_runtime)
        try:
            import forge.paths as fp  # type: ignore[import]
            importlib.reload(fp)
            importlib.reload(chat_runtime)
        except ImportError:
            pass
        self.cr = chat_runtime
        self._patches: list[tuple] = []

        # A real executable standing in for the claude CLI.
        self.fake_claude = Path(self.tmp.name) / "bin" / "claude"
        self.fake_claude.parent.mkdir(parents=True)
        self.fake_claude.write_text("#!/bin/sh\nexit 0\n")
        self.fake_claude.chmod(0o755)

        # No live classifier: pin house_rules to allow (same technique as
        # test_chat_house_rules_gate.py).
        import house_rules as _hr  # type: ignore
        self._patch(_hr, "_house_rules_classifier",
                    lambda task, rules, auth, **kw: ("", 0.99, "forced allow for test"))

    def _patch(self, obj, attr: str, value) -> None:
        self._patches.append((obj, attr, getattr(obj, attr)))
        setattr(obj, attr, value)

    def tearDown(self) -> None:
        for obj, attr, original in reversed(self._patches):
            setattr(obj, attr, original)
        for k, v in self._env_prev.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.tmp.cleanup()

    # ── helpers ──────────────────────────────────────────────────────────────
    def _write_tenant_yaml(self, default_engine: str) -> None:
        cfg = (Path(self.tmp.name) / "tenants" / "_default" / "global"
               / "tenant.corvin.yaml")
        cfg.parent.mkdir(parents=True, exist_ok=True)
        cfg.write_text(
            f"spec:\n  default_engine: {default_engine}\n  hermes_model: qwen3:8b\n",
            encoding="utf-8",
        )

    def _use_claude_bin(self, path: str) -> None:
        os.environ["CORVIN_CLAUDE_BIN"] = path
        self._patch(self.cr, "_claude_binary", lambda: path)

    def _record_spawns(self) -> dict:
        spawned: dict = {"hit": False, "argv": None}

        async def _fake_spawn(*args, **kwargs):
            spawned["hit"] = True
            spawned["argv"] = args
            # Stop right after proving the spawn target; no stream-json needed.
            raise FileNotFoundError("stubbed: spawn reached")

        self.addCleanup(setattr, self.cr.asyncio, "create_subprocess_exec",
                        self.cr.asyncio.create_subprocess_exec)
        self.cr.asyncio.create_subprocess_exec = _fake_spawn  # type: ignore[attr-defined]
        return spawned

    def _audit_events(self) -> list[dict]:
        p = (Path(self.tmp.name) / "tenants" / "_default" / "global"
             / "forge" / "audit.jsonl")
        if not p.is_file():
            return []
        return [json.loads(ln) for ln in p.read_text("utf-8").splitlines() if ln.strip()]

    def _text(self, events: list[dict]) -> str:
        return "\n".join(str(e.get("text") or e.get("message") or "") for e in events)

    # ── (a) stored legacy engine → turn runs on claude_code ─────────────────
    def test_stored_hermes_default_engine_runs_on_claude_code(self) -> None:
        for legacy in ("hermes", "hermes-fast", "ollama", "claude_code_local"):
            with self.subTest(legacy=legacy):
                self._write_tenant_yaml(legacy)
                self._use_claude_bin(str(self.fake_claude))
                self._patch(self.cr, "_claude_authenticated", lambda: True)
                spawned = self._record_spawns()

                self.assertEqual(self.cr._configured_os_engine("_default"), "claude_code")
                self.assertIsNone(self.cr.get_engine_unavailable_message("_default"))

                sess = self.cr.create_session("_default")
                events = _drain(self.cr.stream_turn(sess, "What is the capital of France?"))

                self.assertTrue(spawned["hit"], f"{legacy}: the claude CLI must be spawned")
                self.assertEqual(spawned["argv"][0], str(self.fake_claude))
                engines = [e.get("engine") for e in events if e.get("type") == "engine"]
                self.assertNotIn("hermes", engines)
                self.assertNotIn("Hermes", self._text(events))

    # ── (b) claude missing → actionable error, no fallback ──────────────────
    def test_claude_missing_is_actionable_error_not_hermes(self) -> None:
        self._write_tenant_yaml("hermes")
        self._use_claude_bin("/nonexistent/claude-bin")
        spawned = self._record_spawns()

        guard = self.cr.get_engine_unavailable_message("_default")
        self.assertIsNotNone(guard)
        self.assertIn("was not found", guard)
        self.assertIn("Settings → Engines", guard)
        self.assertNotIn("Hermes", guard)

        sess = self.cr.create_session("_default")
        events = _drain(self.cr.stream_turn(sess, "What is the capital of France?"))
        self.assertFalse(spawned["hit"])
        text = self._text(events)
        self.assertIn("was not found", text)
        self.assertNotIn("Hermes", text)
        self.assertNotIn("ollama", text.lower())
        self.assertEqual(events[-1].get("type"), "done")
        # No substitution is audited any more — there is nothing to substitute.
        subs = [e for e in self._audit_events()
                if (e.get("event_type") or e.get("event")) == "os_turn.engine_substituted"]
        self.assertEqual(subs, [])

    # ── (c) claude not signed in → actionable error, no spawn ───────────────
    def test_claude_not_authenticated_is_actionable_error(self) -> None:
        self._write_tenant_yaml("claude_code")
        self._use_claude_bin(str(self.fake_claude))
        self._patch(self.cr, "_claude_authenticated", lambda: False)
        spawned = self._record_spawns()

        sess = self.cr.create_session("_default")
        events = _drain(self.cr.stream_turn(sess, "What is the capital of France?"))
        self.assertFalse(spawned["hit"])
        text = self._text(events)
        self.assertIn("not signed in", text)
        self.assertIn("auth login", text)
        self.assertNotIn("Hermes", text)
        self.assertEqual(self.cr._effective_os_engine("_default"), "claude_code")


if __name__ == "__main__":
    unittest.main()
