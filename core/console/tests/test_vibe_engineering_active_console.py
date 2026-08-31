"""E2E-wiring — the ACTIVE Context Brain on the CONSOLE turn (ADR-0282/0283).

Review R6 found the console — the surface that OWNS the Vibe Engineering page and
the Context Pipeline editor — could not run the pipeline its own editor authors:
`chat_runtime` only ever called the deterministic `build_brief`, so an operator who
dragged `llm_synthesis` / `toolforge` into their pipeline got those stages recorded
`deferred` on every console turn and never executed. `run_full_pipeline_async` (the
event-loop twin of the bridge's entry point) existed with ZERO callers.

This is the reachability proof for that new entry point, through the real
`stream_turn` boundary — not a unit test on the pipeline function:

  * active flag ON  → `run_full_pipeline_async` is reached; the synthesised prompt
    AND the bound skill bodies land in the turn's system-prompt file; the
    deterministic `build_brief` is NOT used.
  * active flag OFF → dark by default: only `build_brief` runs, byte-for-byte the
    pre-R6 path.
  * a Gate denial injects NOTHING — not even the deterministic fallback, which
    Gate-2 never inspected (parity with the bridge, review R2 A2).
  * an error in the active path is fail-safe: the turn still completes.
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

_THIS = Path(__file__).resolve()
_REPO = _THIS.parents[3]
sys.path.insert(0, str(_REPO / "core" / "console"))
sys.path.insert(0, str(_REPO / "operator" / "bridges" / "shared"))
sys.path.insert(0, str(_REPO / "operator" / "forge"))

_SYNTH = "TEST-SYNTHESISED-PROMPT-77"
_SKILL = "TEST-FORGED-SKILL-BODY-77"
_DETERMINISTIC = "TEST-DETERMINISTIC-BRIEF-77"


def _drain(agen) -> list[dict]:
    async def _collect():
        return [ev async for ev in agen]
    return asyncio.run(_collect())


class ActiveBrainConsoleWiring(unittest.TestCase):
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

    # ── harness (mirrors test_vibe_engineering_p1.py) ────────────────────
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

    @staticmethod
    def _bundle(*, synth=_SYNTH, skills=True):
        return SimpleNamespace(
            brief=MagicMock(), synthesised_prompt=synth,
            tools_to_bind=[], scratch={},
            skills_to_bind=([SimpleNamespace(skill_id="cel_pg_index", body=_SKILL)]
                            if skills else []))

    def _run(self, *, active: bool, run_full=None, build_brief=None, trace=None):
        self._pin_house_rules_allowed()
        self._no_spawn_guard()

        def _flag(fid, tid="_default"):
            if fid == "vibe_engineering":
                return True
            if fid == "vibe_engineering_active":
                return active
            return False

        run_full = run_full or AsyncMock(
            return_value=(self._bundle(), trace if trace is not None else {"stages": []}))
        build_brief = build_brief or MagicMock(
            return_value=(MagicMock(), {"stages": []}))
        self.spy_run_full, self.spy_build_brief = run_full, build_brief
        with (
            patch.object(self.cr._feature_flags, "worker_engine_mode",
                         return_value="native"),
            patch.object(self.cr._feature_flags, "is_enabled", side_effect=_flag),
            patch.object(self.cr, "_CEL_AVAILABLE", True),
            patch.object(self.cr, "_cel_run_full_async", run_full),
            patch.object(self.cr, "_cel_build_brief", build_brief),
            patch.object(self.cr, "_cel_render", return_value=_DETERMINISTIC),
        ):
            return _drain(self.cr.stream_turn(
                self.sess, "erklär mir postgres indexes"))

    # ── the proofs ───────────────────────────────────────────────────────
    def test_active_flag_reaches_the_full_pipeline_from_stream_turn(self):
        events = self._run(active=True)
        self.assertEqual([e.get("type") for e in events][-1], "done")
        self.assertTrue(self.spy_run_full.called,
                        "run_full_pipeline_async must be REACHED from stream_turn")
        self.assertFalse(self.spy_build_brief.called,
                         "the active path replaces the deterministic one, not both")
        # the real task text reaches the pipeline, and the enforcer gets what it needs
        self.assertIn("postgres", self.spy_run_full.call_args[0][0])
        kw = self.spy_run_full.call_args.kwargs
        self.assertTrue(callable(kw.get("gate_fn")), "Gate-1/Gate-2 callback passed")
        self.assertIsInstance(kw.get("persona_patterns"), list)
        self.assertIn("forge_enabled", kw.get("persona_caps") or {},
                      "capability CLASS passed, not just the globs (ADR-0281 R2)")
        sysprompt = self._system_prompt_text()
        self.assertIn(_SYNTH, sysprompt, "the synthesised prompt reaches the worker")
        self.assertIn(_SKILL, sysprompt,
                      "a forged SKILL body reaches the worker via the injection "
                      "channel (it previously reached nobody)")

    def test_gate_denial_injects_nothing(self):
        denied = SimpleNamespace(brief=MagicMock(), synthesised_prompt=None,
                                 tools_to_bind=[], skills_to_bind=[], scratch={})
        run_full = AsyncMock(return_value=(denied, {"stages": [],
                                                    "gate2_denied": "house_rules"}))
        events = self._run(active=True, run_full=run_full)
        self.assertEqual([e.get("type") for e in events][-1], "done")
        txt = self._system_prompt_text()
        self.assertNotIn(_SYNTH, txt)
        self.assertNotIn(_DETERMINISTIC, txt,
                         "a denial must NOT silently fall back to the un-gated "
                         "deterministic brief assembled from the same retrieval")

    def test_active_flag_off_is_the_unchanged_deterministic_path(self):
        events = self._run(active=False)
        self.assertEqual([e.get("type") for e in events][-1], "done")
        self.assertFalse(self.spy_run_full.called,
                         "ships dark: no active pipeline without the second flag")
        self.assertTrue(self.spy_build_brief.called)
        self.assertIn(_DETERMINISTIC, self._system_prompt_text())

    def test_active_path_error_is_fail_safe(self):
        boom = AsyncMock(side_effect=RuntimeError("synthesis subprocess died"))
        events = self._run(active=True, run_full=boom)
        self.assertEqual([e.get("type") for e in events][-1], "done",
                         "an active-brain error must not break the turn")
        self.assertNotIn(_SYNTH, self._system_prompt_text())


if __name__ == "__main__":
    unittest.main()
