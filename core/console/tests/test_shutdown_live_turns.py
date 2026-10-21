"""Host shutdown finalises the running console turns (chat_runtime.shutdown_live_turns, 2026-10-21).

uvicorn re-raises the captured SIGTERM after the lifespan shutdown, so a turn still
pending then dies without its finaliser. Both shipped hosts call
``shutdown_live_turns`` from their lifespan shutdown; this pins the function and
the wiring (the call sites are read from the source of both lifespans).
"""
from __future__ import annotations

import asyncio
import inspect
import sys
import unittest
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
for p in ("core/console", "core/gateway", "corvin_operator/bridges/shared", "corvin_operator/forge"):
    sys.path.insert(0, str(_REPO / p))

import corvin_console.chat_runtime as cr  # noqa: E402


class ShutdownLiveTurnsTests(unittest.TestCase):
    def test_every_live_turn_is_cancelled_with_reason_shutdown_and_awaited(self) -> None:
        seen: list[str] = []

        async def turn(tenant_id: str, sid: str) -> None:
            loop = asyncio.get_running_loop()
            task = asyncio.current_task()
            cr.register_live_turn(tenant_id, sid, loop, task)
            try:
                await asyncio.sleep(100)
            except asyncio.CancelledError:
                seen.append(cr._take_cancel_reason(tenant_id, sid) or "none")
                raise
            finally:
                cr.unregister_live_turn(tenant_id, sid, loop, task)

        async def go() -> int:
            tasks = [asyncio.create_task(turn("_default", "a")), asyncio.create_task(turn("_default", "b"))]
            await asyncio.sleep(0)
            n = await cr.shutdown_live_turns(timeout_s=5)
            self.assertTrue(all(t.done() for t in tasks))
            return n

        self.assertEqual(asyncio.run(go()), 2)
        self.assertEqual(sorted(seen), ["shutdown", "shutdown"])
        self.assertEqual(cr._live_turns, {})

    def test_nothing_running_is_a_noop(self) -> None:
        self.assertEqual(asyncio.run(cr.shutdown_live_turns()), 0)

    def test_both_hosts_call_it_before_the_shutdown_note(self) -> None:
        import corvin_console.app as console_app
        import corvin_gateway.app as gateway_app
        for mod in (console_app, gateway_app):
            src = inspect.getsource(mod)
            call = src.index("shutdown_live_turns()")
            note = src.index("note_shutdown()")
            self.assertLess(call, note, mod.__name__)


if __name__ == "__main__":
    unittest.main()
