"""E2E-wiring — Vibe Engineering P-1: CEL brief on live turns (ADR-0275).

Proves the consolidated `build_brief` is REACHED from the real `stream_turn`
path behind the `vibe_engineering` flag, and its rendered brief lands in the
turn's system-prompt file (e2e-wiring-proof — a unit test on build_brief alone
would not show that anything calls it). Both flag states:
  - flag ON  → build_brief called; the brief marker is in the system prompt.
  - flag OFF → build_brief NOT called; system prompt unchanged (no marker).
  - CEL raises → turn still runs (fail-safe), no marker.
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

_THIS = Path(__file__).resolve()
_REPO = _THIS.parents[3]
sys.path.insert(0, str(_REPO / "core" / "console"))
sys.path.insert(0, str(_REPO / "operator" / "bridges" / "shared"))
sys.path.insert(0, str(_REPO / "operator" / "forge"))

_MARKER = "TEST-CEL-BRIEF-MARKER-42"


def _drain(agen) -> list[dict]:
    async def _collect():
        return [ev async for ev in agen]
    return asyncio.run(_collect())


class VibeEngineeringP1Test(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["CORVIN_HOME"] = self.tmp.name
        os.environ["CORVIN_TENANT_ID"] = "_default"
        os.environ.pop("VOICE_AUDIT_PATH", None)
        import importlib
        from corvin_console import chat_runtime
        importlib.reload(chat_runtime)
        try:
            import forge.paths as fp
            importlib.reload(fp)
            importlib.reload(chat_runtime)
        except ImportError:
            pass
        self.cr = chat_runtime
        self.sess = self.cr.create_session("_default")

    def tearDown(self) -> None:
        self.tmp.cleanup()
        for k in ("CORVIN_HOME", "CORVIN_TENANT_ID"):
            os.environ.pop(k, None)

    def _pin_house_rules_allowed(self):
        import house_rules as _hr  # type: ignore
        _hr._house_rules_classifier = (  # type: ignore[assignment]
            lambda task, rules, auth, **kw: ("", 0.0, "test clear"))

    def _no_spawn_guard(self):
        async def _fake_spawn(*a, **k):
            proc = MagicMock()
            proc.pid = 99999
            proc.returncode = 0
            proc.communicate = AsyncMock(return_value=(b"ok", b""))
            proc.wait = AsyncMock(return_value=0)

            async def _stdout():
                return
                yield
            proc.stdout = MagicMock()
            proc.stdout.__aiter__ = lambda s: _stdout()
            proc.stdin = MagicMock()
            proc.stdin.write = MagicMock()
            proc.stdin.drain = AsyncMock()
            proc.stdin.close = MagicMock()
            proc.stderr = MagicMock()
            proc.stderr.read = AsyncMock(return_value=b"")
            return proc
        self.cr.asyncio.create_subprocess_exec = _fake_spawn  # type: ignore[attr-defined]

    def _system_prompt_text(self) -> str:
        p = self.sess.workdir / ".corvin-system-prompt.txt"
        return p.read_text(encoding="utf-8") if p.exists() else ""

    def _run(self, *, flag_on: bool, build_brief_spy, cel_available=True):
        self._pin_house_rules_allowed()
        self._no_spawn_guard()

        def _flag(fid, tid="_default"):
            return flag_on if fid == "vibe_engineering" else False

        with (
            patch.object(self.cr._feature_flags, "worker_engine_mode",
                         return_value="native"),
            patch.object(self.cr._feature_flags, "is_enabled", side_effect=_flag),
            patch.object(self.cr, "_CEL_AVAILABLE", cel_available),
            patch.object(self.cr, "_cel_build_brief", build_brief_spy),
            patch.object(self.cr, "_cel_render",
                         return_value=f"## Context brief\n{_MARKER}"),
        ):
            return _drain(self.cr.stream_turn(self.sess, "erklär mir postgres indexes"))

    def test_flag_on_reaches_build_brief_and_injects(self):
        spy = MagicMock(return_value=(MagicMock(), {"stages": []}))
        events = self._run(flag_on=True, build_brief_spy=spy)
        self.assertEqual([e.get("type") for e in events][-1], "done")
        self.assertTrue(spy.called, "build_brief must be reached from stream_turn (flag on)")
        # the prompt passed as first arg
        self.assertIn("postgres", spy.call_args[0][0])
        self.assertIn(_MARKER, self._system_prompt_text(),
                      "the rendered CEL brief must land in the system prompt")

    def test_flag_off_does_not_call_and_no_marker(self):
        spy = MagicMock(return_value=(MagicMock(), {"stages": []}))
        events = self._run(flag_on=False, build_brief_spy=spy)
        self.assertEqual([e.get("type") for e in events][-1], "done")
        self.assertFalse(spy.called, "flag off: build_brief must NOT be called")
        self.assertNotIn(_MARKER, self._system_prompt_text(),
                         "flag off: system prompt unchanged, no brief")

    def test_cel_error_is_fail_safe(self):
        def _boom(*a, **k):
            raise RuntimeError("CEL exploded")
        events = self._run(flag_on=True, build_brief_spy=_boom)
        self.assertEqual([e.get("type") for e in events][-1], "done",
                         "a CEL error must not break the turn (fail-safe)")
        self.assertNotIn(_MARKER, self._system_prompt_text())


if __name__ == "__main__":
    unittest.main()
