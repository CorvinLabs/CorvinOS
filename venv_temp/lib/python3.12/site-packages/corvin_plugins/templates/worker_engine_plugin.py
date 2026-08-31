"""worker_engine_plugin.py — Template for a worker_engine CorvinPlugin (ADR-0030).

Copy this file, rename the class, and fill in the sections marked
"── IMPLEMENT ME ──".

A worker_engine plugin implements:
  CorvinPlugin  (ADR-0030): on_load, on_unload, health_check
  WorkerEngine   (ADR-0022): spawn, cancel

The WorkerEngine protocol is defined informally here (no Protocol import needed
— duck-typing is sufficient for the adapter's engine_factory dispatch):

    spawn(prompt, *, system=None, model=None, working_dir=None,
          timeout=120.0, permission_mode=None, extra_args=None, env=None)
        -> Iterator[StreamEvent]

    cancel() -> None

StreamEvent is a plain dict with at least a "type" key.  Known types:
    {"type": "session_started", "session_id": "...", "model": "..."}
    {"type": "text_delta",      "text": "..."}
    {"type": "tool_use",        "tool": "...", "input": {...}}
    {"type": "tool_result",     "tool": "...", "output": "..."}
    {"type": "turn_completed",  "text": "<full accumulated text>"}
    {"type": "error",           "message": "..."}

Quick-start checklist
---------------------
1.  Pick a unique ``plugin_id`` and ``name`` (engine name used by engine_factory).
2.  Implement ``_do_spawn``: the actual subprocess / HTTP / SDK call.
3.  Implement ``_do_cancel``: interrupt any in-flight call.
4.  Register in tenant config under ``spec.plugins.installed``.

Run inline tests:
    python3 core/plugins/templates/worker_engine_plugin.py
"""
from __future__ import annotations

import logging
import sys
import threading
from pathlib import Path
from typing import Any, Iterator

# ── Adjust path for standalone runs ──────────────────────────────────────────
_HERE = Path(__file__).resolve()
_REPO = _HERE.parents[3]
if str(_REPO / "plugins" / "core" / "plugins") not in sys.path:
    sys.path.insert(0, str(_REPO / "plugins" / "core" / "plugins"))

from corvin_plugins.protocol import CorvinPlugin, HealthStatus, PluginContext  # noqa: E402

log = logging.getLogger(__name__)

# Type alias — StreamEvent is a plain dict at runtime.
StreamEvent = dict[str, Any]


class WorkerEnginePluginTemplate:
    """Template: implements CorvinPlugin lifecycle + WorkerEngine capability.

    on_load() self-registers with ctx.engine_factory.  The adapter then
    dispatches to this engine when a chat profile specifies
    ``engine: <name>`` matching ``self.name``.
    """

    # ── CorvinPlugin identity ────────────────────────────────────────────────
    plugin_id    = "my-worker-engine"         # ── IMPLEMENT ME ── globally unique
    plugin_type  = "worker_engine"
    version      = "0.1.0"                   # ── IMPLEMENT ME ── semver
    display_name = "My Worker Engine"         # ── IMPLEMENT ME ──

    # ── WorkerEngine identity ─────────────────────────────────────────────────
    name = "myengine"                         # ── IMPLEMENT ME ── used in engine_factory lookup

    capabilities: dict[str, Any] = {
        "mid_stream_inject": False,           # set True if you implement inject()
        "hooks":             False,
        "skills_tool":       False,
        "mcp":               False,
        "stream_json":       True,
    }

    def __init__(self) -> None:
        self._ctx: PluginContext | None = None
        self._lock = threading.Lock()
        self._cancelled = False
        self._active = False

    # ── CorvinPlugin: lifecycle ──────────────────────────────────────────────

    def on_load(self, ctx: PluginContext) -> None:
        """Store context and self-register with engine_factory."""
        self._ctx = ctx

        # ── NOTE 2026-07-27: engine_factory has no register() to call ─────────
        # `operator/bridges/shared/engine_registry.py` builds engines from a
        # hard-coded `_ENGINE_BUILDERS` dict with three entries. There is no
        # registration API, so a plugin cannot enter itself — populating this
        # handle would not help (see surface_map.py's worker_engine row, and
        # ADR-0245's deferred design question about an L22 registration
        # surface).
        #
        # The `is not None` guard is kept so the template still runs, but the
        # else-branch says what is actually true. A warning that reads "handle
        # is None" invites the reader to go and pass the handle, which is the
        # one action that changes nothing.
        if ctx.engine_factory is not None:
            ctx.engine_factory.register(self)
            log.info("plugin %r registered engine %r with engine_factory", self.plugin_id, self.name)
        else:
            log.error(
                "plugin %r: engine %r is NOT reachable and cannot be made "
                "reachable by configuration — L22's engine_registry has no "
                "register(); engines come from a hard-coded builder dict. This "
                "template targets an API that does not exist yet; see ADR-0245. "
                "A chat profile naming engine=%r will not find it.",
                self.plugin_id, self.name, self.name,
            )

    def on_unload(self) -> None:
        """Cancel any in-flight call and deregister."""
        self.cancel()
        if self._ctx is not None and self._ctx.engine_factory is not None:
            try:
                self._ctx.engine_factory.unregister(self.name)
            except Exception:
                log.exception("plugin %r: error during engine_factory.unregister", self.plugin_id)

    def health_check(self) -> HealthStatus:
        with self._lock:
            active = self._active
        return HealthStatus(
            ok=True,
            message=f"engine {self.name} healthy",
            details={"active_call": active},
        )

    # ── WorkerEngine: spawn ───────────────────────────────────────────────────

    def spawn(
        self,
        prompt: str,
        *,
        system: str | None = None,
        model: str | None = None,
        working_dir: Path | str | None = None,
        timeout: float = 120.0,
        permission_mode: str | None = None,
        extra_args: list[str] | None = None,
        env: dict[str, str] | None = None,
    ) -> Iterator[StreamEvent]:
        """Spawn the engine and yield StreamEvents.

        The adapter calls this method and streams the events to the bridge.
        Must be a generator (use ``yield``).  May raise on hard errors.
        """
        with self._lock:
            self._cancelled = False
            self._active = True

        try:
            # ── IMPLEMENT ME ── Replace this block with your real engine call ─
            #
            # Common patterns:
            #   subprocess:   yield events from a JSON-stream subprocess
            #   HTTP/SSE:     yield events from a streaming HTTP response
            #   SDK:          yield events from an async SDK, run via asyncio.run
            #
            # Step 1: signal session start
            yield {
                "type": "session_started",
                "session_id": "stub-session-000",
                "model": model or "stub-model",
            }

            # Step 2: stream text deltas
            # ── IMPLEMENT ME ── replace with real output ──────────────────────
            response_text = f"[{self.name}] response to: {prompt[:50]}"
            for chunk in _split_chunks(response_text, size=20):
                if self._is_cancelled():
                    return
                yield {"type": "text_delta", "text": chunk}

            # Step 3: signal turn completed
            yield {
                "type": "turn_completed",
                "text": response_text,
            }
            # ── IMPLEMENT ME ── end ───────────────────────────────────────────

        finally:
            with self._lock:
                self._active = False

    # ── WorkerEngine: cancel ──────────────────────────────────────────────────

    def cancel(self) -> None:
        """Signal the active spawn() to stop at the next yield point."""
        with self._lock:
            self._cancelled = True
        # ── IMPLEMENT ME ── send SIGTERM / close socket / set asyncio cancel ──

    # ── Optional: mid-stream inject ───────────────────────────────────────────

    def inject(self, text: str) -> None:
        """Inject a /btw note into the active stream (optional).

        Only implement if capabilities["mid_stream_inject"] = True.
        """
        # ── IMPLEMENT ME ── write to stdin pipe / side-channel ────────────────
        raise NotImplementedError("mid_stream_inject not supported by this engine")

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _is_cancelled(self) -> bool:
        with self._lock:
            return self._cancelled


def _split_chunks(text: str, size: int) -> list[str]:
    return [text[i:i + size] for i in range(0, len(text), size)]


# ──────────────────────────────────────────────────────────────────────────────
# Tenant configuration reference
#
#   spec:
#     plugins:
#       installed:
#         - id: my-worker-engine
#           config: {}
# ──────────────────────────────────────────────────────────────────────────────


# ──────────────────────────────────────────────────────────────────────────────
# Inline tests
#   python3 core/plugins/templates/worker_engine_plugin.py
# ──────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import unittest

    class WorkerEnginePluginTests(unittest.TestCase):

        def setUp(self) -> None:
            self.plugin = WorkerEnginePluginTemplate()

        def _make_ctx(self, engine_factory: Any = None) -> PluginContext:
            return PluginContext(
                plugin_id=self.plugin.plugin_id,
                tenant_id="test",
                corvin_home=Path("/tmp"),
                config={},
                audit_emit=lambda *_: None,
                engine_factory=engine_factory,
            )

        def test_implements_corvin_plugin(self) -> None:
            self.assertIsInstance(self.plugin, CorvinPlugin)

        def test_on_load_without_factory(self) -> None:
            ctx = self._make_ctx(engine_factory=None)
            self.plugin.on_load(ctx)  # must not raise

        def test_on_load_registers_with_factory(self) -> None:
            registered: list[Any] = []

            class FakeFactory:
                def register(self, engine: Any) -> None:
                    registered.append(engine)

            ctx = self._make_ctx(engine_factory=FakeFactory())
            self.plugin.on_load(ctx)
            self.assertEqual(len(registered), 1)

        def test_on_unload(self) -> None:
            ctx = self._make_ctx()
            self.plugin.on_load(ctx)
            self.plugin.on_unload()  # must not raise

        def test_health_check_ok(self) -> None:
            ctx = self._make_ctx()
            self.plugin.on_load(ctx)
            hs = self.plugin.health_check()
            self.assertTrue(hs.ok)

        def test_spawn_yields_session_started(self) -> None:
            ctx = self._make_ctx()
            self.plugin.on_load(ctx)
            events = list(self.plugin.spawn("hello world"))
            types = [e["type"] for e in events]
            self.assertIn("session_started", types)
            self.assertIn("turn_completed", types)

        def test_cancel_stops_stream(self) -> None:
            ctx = self._make_ctx()
            self.plugin.on_load(ctx)
            events: list[StreamEvent] = []
            for ev in self.plugin.spawn("hello " * 200):
                events.append(ev)
                if ev["type"] == "session_started":
                    self.plugin.cancel()
                    break
            # After cancel we should have stopped before turn_completed
            types = [e["type"] for e in events]
            self.assertNotIn("turn_completed", types)

    suite = unittest.TestLoader().loadTestsFromTestCase(WorkerEnginePluginTests)
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
