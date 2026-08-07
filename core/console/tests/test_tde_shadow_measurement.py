"""E2E — TDE shadow measurement hook (TDE_ROBUST_USABLE_PLAN Step 3).

Drives a REAL native turn through stream_turn and asserts the shadow-measurement
hook at the end of the native path fires correctly (e2e-wiring-proof):

  - flag ON  + sampling ON  → _spawn_shadow_measurement called with the right
    ctx (task_text, session_key, tenant_id, user_model). This also proves
    `_os_model_used` / `_task_text` are in scope at the hook (a NameError there
    would crash the turn before the spy is hit).
  - flag OFF                → hook does NOT fire (ships-dark; native unchanged).
  - sampling OFF            → hook does NOT fire even with the flag on.

The spawn is spied (not executed) so no real TDE fan-out / compute-pool charge
happens in the test — the wiring is what's under test, orchestrate_shadow has
its own coverage.
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


def _drain(agen) -> list[dict]:
    async def _collect():
        return [ev async for ev in agen]
    return asyncio.run(_collect())


class TdeShadowMeasurementTest(unittest.TestCase):
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
            lambda task, rules, auth, **kw: ("", 0.0, "test clear")
        )

    def _no_spawn_guard(self):
        async def _fake_spawn(*args, **kwargs):
            proc = MagicMock()
            proc.pid = 99999
            proc.returncode = 0
            proc.communicate = AsyncMock(return_value=(b"native answer", b""))
            proc.wait = AsyncMock(return_value=0)

            async def _fake_stdout():
                return
                yield

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

    def _run_native(self, *, flag_on: bool, sampling_on: bool, spy):
        self._pin_house_rules_allowed()
        self._no_spawn_guard()
        prompt = "what is the capital of france"  # short, prose → native path

        def _flag(flag_id, tenant_id=None):
            return flag_on if flag_id == "tde_shadow_measurement" else False

        with (
            patch.object(self.cr._feature_flags, "worker_engine_mode",
                         return_value="native"),
            patch.object(self.cr._feature_flags, "is_enabled", side_effect=_flag),
            patch.object(self.cr, "_measurement_should_sample",
                         return_value=sampling_on),
            patch.object(self.cr, "_spawn_shadow_measurement", spy),
        ):
            return _drain(self.cr.stream_turn(self.sess, prompt))

    def test_flag_on_sampling_on_fires_hook(self) -> None:
        spy = MagicMock()
        events = self._run_native(flag_on=True, sampling_on=True, spy=spy)
        self.assertEqual([e.get("type") for e in events][-1], "done")
        self.assertTrue(spy.called,
                        "shadow hook must fire on a native turn with flag+sampling on")
        ctx = spy.call_args[0][0]
        # Proves _task_text / _os_model_used were in scope at the hook.
        self.assertIn("task_text", ctx)
        self.assertIn("capital of france", ctx["task_text"])
        self.assertEqual(ctx["tenant_id"], "_default")
        self.assertIn("session_key", ctx)
        self.assertIn("user_model", ctx)

    def test_flag_off_does_not_fire(self) -> None:
        spy = MagicMock()
        events = self._run_native(flag_on=False, sampling_on=True, spy=spy)
        self.assertEqual([e.get("type") for e in events][-1], "done")
        self.assertFalse(spy.called, "ships-dark: flag off must not fire the hook")

    def test_sampling_off_does_not_fire(self) -> None:
        spy = MagicMock()
        events = self._run_native(flag_on=True, sampling_on=False, spy=spy)
        self.assertEqual([e.get("type") for e in events][-1], "done")
        self.assertFalse(spy.called,
                         "no sampling → no shadow even with the flag on")


if __name__ == "__main__":
    unittest.main()
