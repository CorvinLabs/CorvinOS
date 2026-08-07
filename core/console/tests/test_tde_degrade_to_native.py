"""E2E — TDE in-flight degrade to native (TDE_ROBUST_USABLE_PLAN Step 1).

Robustness invariant: once a turn enters the Tiered Delegation Engine, an
in-flight failure (analysis timeout, worker IPC error, malformed plan) or a
mid-run shared-pool exhaustion must NOT surface an error to the user. The turn
must degrade to the native Claude Code OS-turn — the degrade ladder ends at
native (never ACS, per CLAUDE.md § Worker Engine Selection).

These drive the REAL `stream_turn` streaming path (not `_stream_tde_turn` in
isolation) so the sentinel-swallow + fall-through-to-native wiring is exercised
end to end (e2e-wiring-proof). TDE is made to look available (so the turn
actually enters TDE, not the pre-dispatch native fallback), then the real
in-flight run is forced to fail.

Audit invariant checked indirectly via the event stream: the `_tde_degraded`
sentinel must NEVER leak to the client, and the turn ends with exactly one
`done` — the native path owns the single os-span close + web.turn.completed.
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

_THIS = Path(__file__).resolve()
_REPO = _THIS.parents[3]
sys.path.insert(0, str(_REPO / "core" / "console"))
sys.path.insert(0, str(_REPO / "operator" / "bridges" / "shared"))
sys.path.insert(0, str(_REPO / "operator" / "forge"))


def _drain(agen) -> list[dict]:
    async def _collect():
        out = []
        async for ev in agen:
            out.append(ev)
        return out
    return asyncio.run(_collect())


class TdeDegradeToNativeTest(unittest.TestCase):
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
        for mod_name in ("tde", "tde.analysis_runner", "tde.engine_registry",
                         "tde.send_integration", "tde.worker_ipc"):
            sys.modules.pop(mod_name, None)

    def _pin_house_rules_allowed(self):
        import house_rules as _hr  # type: ignore
        _hr._house_rules_classifier = (  # type: ignore[assignment]
            lambda task, rules, auth, **kw: ("", 0.0, "test clear")
        )

    def _inject_fake_tde(self, *, analysis_raises: bool = False,
                         execute_result: dict | None = None):
        """Inject fake tde.* modules so the import inside _stream_tde_turn
        succeeds, but the real in-flight run fails (or returns a given result).

        analysis_raises=True → run_initial_analysis_sync raises (degrade path 3,
        the generic in-flight exception). execute_result → SendIntegration
        returns it (used to simulate reason='quota_exhausted', degrade path 2)."""
        tde_pkg = types.ModuleType("tde")

        ar = types.ModuleType("tde.analysis_runner")

        class _Classification:
            task_type = "general"
            complexity = "medium"

        class _Plan:
            steps = [object()]

        class _Analysis:
            classification = _Classification()
            global_plan = _Plan()

        def _run_analysis(task_text, context, proc_holder=None):
            if analysis_raises:
                raise RuntimeError("simulated TDE analysis failure")
            return _Analysis()
        ar.run_initial_analysis_sync = _run_analysis  # type: ignore[attr-defined]

        er = types.ModuleType("tde.engine_registry")

        class _EngineRegistry:
            def __init__(self, **kw):
                pass
        er.EngineRegistry = _EngineRegistry  # type: ignore[attr-defined]

        si = types.ModuleType("tde.send_integration")

        class _SendIntegration:
            def __init__(self, **kw):
                pass

            async def select_engine_and_execute(self, *a, **kw):
                if execute_result is not None:
                    return ("tiered_delegation", execute_result)
                raise RuntimeError("simulated TDE execution failure")
        si.SendIntegration = _SendIntegration  # type: ignore[attr-defined]

        wi = types.ModuleType("tde.worker_ipc")

        class _ProcHolder:
            def kill(self):
                pass
        wi.ProcHolder = _ProcHolder  # type: ignore[attr-defined]

        for name, mod in (("tde", tde_pkg), ("tde.analysis_runner", ar),
                          ("tde.engine_registry", er),
                          ("tde.send_integration", si),
                          ("tde.worker_ipc", wi)):
            sys.modules[name] = mod

    def _no_spawn_guard(self):
        """Fake the native `claude -p` subprocess so the native OS-turn the
        degrade falls through to runs without a real spawn."""
        called = {"hit": False}

        async def _fake_spawn(*args, **kwargs):
            called["hit"] = True
            proc = MagicMock()
            proc.pid = 99999
            proc.returncode = 0
            proc.communicate = AsyncMock(return_value=(b"native answer", b""))
            proc.wait = AsyncMock(return_value=0)

            async def _fake_stdout():
                return
                yield  # make it an async generator

            proc.stdout = MagicMock()
            proc.stdout.__aiter__ = lambda s: _fake_stdout()
            proc.stdin = MagicMock()
            proc.stdin.write = MagicMock()
            proc.stdin.drain = AsyncMock()
            proc.stdin.close = MagicMock()
            proc.stderr = MagicMock()
            proc.stderr.read = AsyncMock(return_value=b"")
            return proc

        self.cr.asyncio.create_subprocess_exec = _fake_spawn  # type: ignore[attr-defined]
        return called

    def _run_tde_turn(self, prompt: str):
        with (
            patch.object(self.cr, "_delegation_enabled", return_value=True),
            patch.object(self.cr, "_should_delegate", return_value=True),
            patch.object(self.cr._feature_flags, "worker_engine_mode",
                         return_value="tde"),
            patch.object(self.cr, "_tde_available", return_value=True),
            patch.object(self.cr, "_tde_quota_peek_ok", return_value=True),
        ):
            return _drain(self.cr.stream_turn(self.sess, prompt))

    # ── Path 3: in-flight runtime failure → degrade to native ───────────────
    def test_runtime_failure_degrades_to_native(self) -> None:
        self._pin_house_rules_allowed()
        self._inject_fake_tde(analysis_raises=True)
        spawn = self._no_spawn_guard()

        events = self._run_tde_turn("summarise the design notes for me please")
        types_seen = [e.get("type") for e in events]
        all_text = " ".join(str(e.get("text") or e.get("message") or "")
                             for e in events)

        # The internal degrade sentinel must NEVER leak to the client.
        self.assertNotIn("_tde_degraded", types_seen,
                         "the _tde_degraded sentinel must be swallowed by the caller")

        # The user must NOT see the old raw TDE failure text.
        self.assertNotIn("TDE-Turn fehlgeschlagen", all_text,
                         "in-flight TDE failure must degrade, not surface an error")

        # The honest TDE→native fallback notice IS present (reason=runtime).
        tde_notices = [e for e in events if e.get("type") == "notice"
                       and e.get("subtype") == "tde_fallback"]
        self.assertTrue(tde_notices,
                        f"expected a tde_fallback notice; got {types_seen}")

        # The native OS-turn actually ran (fell through to the direct path).
        self.assertTrue(spawn["hit"],
                        "the native claude subprocess must run after degrade")

        # Exactly one clean turn close.
        self.assertEqual(types_seen[-1], "done",
                         "turn must end with a single 'done' after degrade")
        self.assertEqual(types_seen.count("done"), 1,
                         "degrade must not produce two turn-completions")

    # ── Path 2: mid-run shared-pool exhaustion → degrade to native ──────────
    def test_quota_exhausted_midrun_degrades_to_native(self) -> None:
        self._pin_house_rules_allowed()
        self._inject_fake_tde(execute_result={"reason": "quota_exhausted"})
        spawn = self._no_spawn_guard()

        events = self._run_tde_turn("summarise the design notes for me please")
        types_seen = [e.get("type") for e in events]
        all_text = " ".join(str(e.get("text") or e.get("message") or "")
                             for e in events)

        self.assertNotIn("_tde_degraded", types_seen)
        # The correct shared-pool notice is shown (reason=quota reuses it), and
        # the turn still runs natively rather than dead-ending on an upgrade msg.
        self.assertIn("Agentic-Compute-Kontingent", all_text,
                      "quota degrade must show the shared-pool notice")
        self.assertTrue(spawn["hit"],
                        "native turn must run even when the pool is exhausted")
        self.assertEqual(types_seen[-1], "done")
        self.assertEqual(types_seen.count("done"), 1)


if __name__ == "__main__":
    unittest.main()
