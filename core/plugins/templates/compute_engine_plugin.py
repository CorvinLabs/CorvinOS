"""compute_engine_plugin.py — Template for a compute_engine CorvinPlugin (ADR-0030).

Copy this file, rename the class, and fill in the sections marked
"── IMPLEMENT ME ──".  Everything else (state machine, thread safety, gate
dispatch, lifecycle, health check) is wired up for you.

Quick-start checklist
---------------------
1.  Pick a unique ``plugin_id``, ``engine_id``, and ``job_id_prefix``.
2.  Set ``display_name`` and ``version``.
3.  Implement ``_run_job`` (the core compute loop).
4.  Decide whether your engine supports gates (``supports_gates``).
5.  Call ``register_compute_plugin(...)`` before WorkerServer.serve_forever().
6.  Add your plugin to the tenant's ``spec.plugins.installed`` list.
7.  Run the inline tests:
        python3 core/plugins/templates/compute_engine_plugin.py

Protocol contracts
------------------
CorvinPlugin  (ADR-0030): on_load, on_unload, health_check
ComputeEngine  (ADR-0029): submit, status, result, gate_action, abort
"""
from __future__ import annotations

import logging
import secrets
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

# ── Adjust path for standalone runs ──────────────────────────────────────────
_HERE = Path(__file__).resolve()
_REPO = _HERE.parents[3]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))
if str(_REPO / "plugins" / "core" / "plugins") not in sys.path:
    sys.path.insert(0, str(_REPO / "plugins" / "core" / "plugins"))
if str(_REPO / "plugins" / "core" / "compute") not in sys.path:
    sys.path.insert(0, str(_REPO / "plugins" / "core" / "compute"))

from corvin_compute.engine_protocol import (  # noqa: E402
    ComputeEngine,
    ComputeResult,
    ComputeSpec,
    ComputeStatus,
    EngineDoesNotSupportGates,
    GateAction,
    UnknownJobId,
)
from corvin_plugins.protocol import CorvinPlugin, HealthStatus, PluginContext  # noqa: E402

log = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────────────
# 1.  Internal per-job state  (same pattern as contrib_template.py)
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class _JobState:
    job_id: str
    spec: ComputeSpec
    state: str = "queued"            # queued | running | gate_open | terminal | failed
    iterations_done: int = 0
    best_loss: float = float("inf")
    best_params: dict = field(default_factory=dict)
    error: str | None = None
    result: dict = field(default_factory=dict)

    done_event: threading.Event = field(default_factory=threading.Event)
    gate_event: threading.Event = field(default_factory=threading.Event)
    gate_action: GateAction | None = None


# ──────────────────────────────────────────────────────────────────────────────
# 2.  Plugin class — implements BOTH CorvinPlugin AND ComputeEngine
# ──────────────────────────────────────────────────────────────────────────────

class ComputeEnginePluginTemplate:
    """Template: implements CorvinPlugin lifecycle + ComputeEngine capability.

    Implements ADR-0030 (plugin lifecycle) and ADR-0029 (compute engine protocol).
    on_load() self-registers with ctx.compute_registry so the MCP bridge picks
    up the engine automatically.
    """

    # ── CorvinPlugin identity ────────────────────────────────────────────────
    plugin_id    = "my-compute-engine"   # ── IMPLEMENT ME ── globally unique
    plugin_type  = "compute_engine"
    version      = "0.1.0"              # ── IMPLEMENT ME ── semver
    display_name = "My Compute Engine"  # ── IMPLEMENT ME ──

    # ── ComputeEngine identity ────────────────────────────────────────────────
    engine_id     = "myengine"          # ── IMPLEMENT ME ── no clashes with flat/pipeline/hac
    job_id_prefix = "myengine_"         # ── IMPLEMENT ME ── must end with "_"
    supports_gates = False              # set True if your engine has checkpoint gates

    def __init__(self) -> None:
        self._jobs: dict[str, _JobState] = {}
        self._lock = threading.Lock()
        self._ctx: PluginContext | None = None
        self._audit_emit: Callable[[str, dict], None] = lambda *_: None

    # ── CorvinPlugin: lifecycle ──────────────────────────────────────────────

    def on_load(self, ctx: PluginContext) -> None:
        """Store context, wire audit, and self-register with compute registry."""
        self._ctx = ctx
        self._audit_emit = ctx.audit_emit

        # Read engine config from ctx.config (tenant's spec.plugins.installed[*].config)
        cfg = ctx.config  # noqa: F841 - used by the example line below
        # ── IMPLEMENT ME ── read your engine-specific config here, e.g.:
        # self._max_iter = int(cfg.get("max_iterations", 100))

        if ctx.compute_registry is not None:
            ctx.compute_registry.register(self)
            log.info("plugin %r registered engine %r with compute_registry", self.plugin_id, self.engine_id)
        else:
            log.warning(
                "plugin %r: ctx.compute_registry is None — engine %r not reachable via MCP bridge",
                self.plugin_id, self.engine_id,
            )

    def on_unload(self) -> None:
        """Deregister from compute registry on shutdown / hot-reload."""
        if self._ctx is not None and self._ctx.compute_registry is not None:
            try:
                self._ctx.compute_registry.unregister(self.engine_id)
            except Exception:
                log.exception("plugin %r: error during compute_registry.unregister", self.plugin_id)

    def health_check(self) -> HealthStatus:
        with self._lock:
            active = sum(
                1 for js in self._jobs.values()
                if js.state in ("running", "queued", "gate_open")
            )
        return HealthStatus(
            ok=True,
            message=f"engine {self.engine_id} healthy",
            details={"jobs_active": active},
        )

    # ── ComputeEngine: submit ─────────────────────────────────────────────────

    def submit(self, spec: ComputeSpec) -> str:
        job_id = self.job_id_prefix + secrets.token_urlsafe(22)
        js = _JobState(job_id=job_id, spec=spec)

        with self._lock:
            self._jobs[job_id] = js

        self._audit_emit("compute.myengine_started", {
            "job_id": job_id,
            "engine_id": self.engine_id,
            "tenant_id": spec.tenant_id,
            "strategy": spec.strategy,
        })

        t = threading.Thread(
            target=self._run_job,
            args=(job_id,),
            daemon=True,
            name=f"myengine-{job_id[:12]}",
        )
        t.start()
        return job_id

    # ── ComputeEngine: status ─────────────────────────────────────────────────

    def status(self, job_id: str) -> ComputeStatus:
        js = self._require(job_id)
        with self._lock:
            return ComputeStatus(
                job_id=job_id,
                engine_id=self.engine_id,
                state=js.state,
                progress={"iterations_done": js.iterations_done, "best_loss": js.best_loss},
                detail={"best_params": js.best_params},
            )

    # ── ComputeEngine: result ─────────────────────────────────────────────────

    def result(self, job_id: str, wait_s: float = 30.0) -> ComputeResult:
        js = self._require(job_id)
        js.done_event.wait(timeout=wait_s)

        with self._lock:
            state = js.state
            result_dict = dict(js.result)
            error = js.error

        if state not in ("terminal", "failed"):
            return ComputeResult(
                job_id=job_id,
                engine_id=self.engine_id,
                state="running",
                result={"iterations_done": js.iterations_done},
            )
        if state == "failed":
            return ComputeResult(
                job_id=job_id,
                engine_id=self.engine_id,
                state="failed",
                result={"error": error or "unknown"},
            )
        return ComputeResult(
            job_id=job_id,
            engine_id=self.engine_id,
            state="converged",
            result=result_dict,
        )

    # ── ComputeEngine: gate_action ────────────────────────────────────────────

    def gate_action(self, job_id: str, action: GateAction) -> None:
        if not self.supports_gates:
            raise EngineDoesNotSupportGates(
                f"{self.display_name} has no gates — set supports_gates=True"
            )
        js = self._require(job_id)
        with self._lock:
            if js.state != "gate_open":
                log.warning("gate_action on job %s in state %s (expected gate_open)", job_id, js.state)
                return
            js.gate_action = action
        js.gate_event.set()

    # ── ComputeEngine: abort ──────────────────────────────────────────────────

    def abort(self, job_id: str) -> None:
        js = self._require(job_id)
        with self._lock:
            if js.state in ("terminal", "failed"):
                return
            js.state = "terminal"
            js.result = {"converge_reason": "aborted"}
            js.gate_action = GateAction(action_type="abort")
        js.gate_event.set()
        js.done_event.set()
        self._audit_emit("compute.myengine_aborted", {"job_id": job_id})

    # ── Internal: background worker ───────────────────────────────────────────

    def _run_job(self, job_id: str) -> None:
        js = self._require(job_id)
        self._set_state(js, "running")

        extra = js.spec.extra
        max_iter = int(extra.get("max_iterations", 100))

        try:
            # ── IMPLEMENT ME ── Replace this block with real compute logic ────
            #
            # Conventions (same as contrib_template.py):
            #   • Call forge tool via spec.tool_name / spec.param_grid.
            #   • Update js.best_loss / js.best_params on improvement.
            #   • Increment js.iterations_done each iteration.
            #   • Open a gate (if supports_gates=True) with _open_gate().
            #   • Check for abort between iterations.
            #
            for i in range(max_iter):
                with self._lock:
                    if js.state == "terminal":
                        return  # aborted externally

                # --- your per-iteration compute here ---
                fake_loss = 1.0 / (i + 1)
                fake_params: dict[str, Any] = {"iter": i}

                with self._lock:
                    if fake_loss < js.best_loss:
                        js.best_loss = fake_loss
                        js.best_params = fake_params
                    js.iterations_done = i + 1

            # ── IMPLEMENT ME ── end of your compute block ─────────────────────

            self._set_result(js, {
                "best_loss": js.best_loss,
                "best_params": js.best_params,
                "iterations": js.iterations_done,
                "converge_reason": "budget",
            })

        except Exception as exc:  # noqa: BLE001
            log.exception("ComputeEnginePluginTemplate job %s failed", job_id)
            with self._lock:
                js.state = "failed"
                js.error = str(exc)
            js.done_event.set()
            self._audit_emit("compute.myengine_failed", {
                "job_id": job_id,
                "error": type(exc).__name__,  # class name only — no PII
            })

    # ── Internal: gate helpers ────────────────────────────────────────────────

    def _open_gate(self, js: _JobState, *, reason: str = "") -> GateAction | None:
        if not self.supports_gates:
            return None

        timeout_s = float(js.spec.extra.get("gate_timeout_s", 3600))
        with self._lock:
            js.state = "gate_open"
            js.gate_event.clear()
            js.gate_action = None

        self._audit_emit("compute.myengine_gate_opened", {
            "job_id": js.job_id,
            "reason": reason,
            "iterations_done": js.iterations_done,
        })

        js.gate_event.wait(timeout=timeout_s)

        with self._lock:
            action = js.gate_action
            if action is None:
                action = GateAction(action_type="resume")
            if action.action_type != "abort":
                js.state = "running"
            return action

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _require(self, job_id: str) -> _JobState:
        with self._lock:
            js = self._jobs.get(job_id)
        if js is None:
            raise UnknownJobId(job_id)
        return js

    def _set_state(self, js: _JobState, state: str) -> None:
        with self._lock:
            js.state = state

    def _set_result(self, js: _JobState, result: dict) -> None:
        with self._lock:
            js.state = "terminal"
            js.result = result
        js.done_event.set()
        self._audit_emit("compute.myengine_terminal", {
            "job_id": js.job_id,
            "iterations": js.iterations_done,
            "converge": result.get("converge_reason", ""),
        })


# ──────────────────────────────────────────────────────────────────────────────
# 3.  Registration helper
# ──────────────────────────────────────────────────────────────────────────────

def register_compute_plugin(
    corvin_home: Path,
    config: dict,
    *,
    compute_registry: Any,
    audit_emit: Callable[[str, dict], None] | None = None,
) -> ComputeEnginePluginTemplate:
    """Create a ComputeEnginePluginTemplate, call on_load(), and return it.

    Typical usage (worker entrypoint)::

        from templates.compute_engine_plugin import register_compute_plugin

        plugin = register_compute_plugin(
            corvin_home,
            config={"max_iterations": 200},
            compute_registry=registry,
            audit_emit=emit,
        )
        server = WorkerServer(..., extra_engines=[plugin])
        server.serve_forever()
    """
    from corvin_plugins.protocol import PluginContext
    from corvin_plugins.registry import PluginRegistry

    _audit = audit_emit or (lambda *_: None)

    ctx = PluginContext(
        plugin_id=ComputeEnginePluginTemplate.plugin_id,
        tenant_id="_default",
        corvin_home=corvin_home,
        config=config,
        audit_emit=_audit,
        compute_registry=compute_registry,
    )

    local_registry = PluginRegistry()
    plugin = ComputeEnginePluginTemplate()
    local_registry.register(plugin, ctx)
    return plugin


# ──────────────────────────────────────────────────────────────────────────────
# 4.  Tenant configuration reference
#
#     spec:
#       plugins:
#         installed:
#           - id: my-compute-engine
#             config:
#               max_iterations: 200
# ──────────────────────────────────────────────────────────────────────────────


# ──────────────────────────────────────────────────────────────────────────────
# 5.  Inline tests
#     python3 core/plugins/templates/compute_engine_plugin.py
# ──────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import tempfile
    import unittest

    class ComputeEnginePluginTests(unittest.TestCase):

        def setUp(self) -> None:
            self.tmp = Path(tempfile.mkdtemp())
            self.plugin = ComputeEnginePluginTemplate()

        def _make_ctx(self, compute_registry: Any = None) -> PluginContext:
            from corvin_plugins.protocol import PluginContext
            return PluginContext(
                plugin_id=self.plugin.plugin_id,
                tenant_id="test",
                corvin_home=self.tmp,
                config={},
                audit_emit=lambda *_: None,
                compute_registry=compute_registry,
            )

        def _make_spec(self, **extra: Any) -> ComputeSpec:
            return ComputeSpec(
                engine=self.plugin.engine_id,
                tenant_id="test",
                budget={"max_iterations": 5},
                extra=extra,
            )

        # ── Protocol conformance ──────────────────────────────────────────────

        def test_implements_corvin_plugin(self) -> None:
            self.assertIsInstance(self.plugin, CorvinPlugin)

        def test_implements_compute_engine(self) -> None:
            self.assertIsInstance(self.plugin, ComputeEngine)

        def test_engine_id_and_prefix(self) -> None:
            self.assertTrue(self.plugin.job_id_prefix.endswith("_"))
            self.assertNotIn(self.plugin.engine_id, ("flat", "pipeline", "hac"))

        # ── Lifecycle ─────────────────────────────────────────────────────────

        def test_on_load_without_registry(self) -> None:
            ctx = self._make_ctx(compute_registry=None)
            self.plugin.on_load(ctx)  # must not raise even without registry

        def test_on_load_with_registry(self) -> None:
            registered: list[Any] = []

            class FakeRegistry:
                def register(self, engine: Any) -> None:
                    registered.append(engine)

            ctx = self._make_ctx(compute_registry=FakeRegistry())
            self.plugin.on_load(ctx)
            self.assertEqual(len(registered), 1)
            self.assertIs(registered[0], self.plugin)

        def test_on_unload(self) -> None:
            ctx = self._make_ctx(compute_registry=None)
            self.plugin.on_load(ctx)
            self.plugin.on_unload()  # must not raise

        # ── health_check ──────────────────────────────────────────────────────

        def test_health_check_ok(self) -> None:
            ctx = self._make_ctx()
            self.plugin.on_load(ctx)
            hs = self.plugin.health_check()
            self.assertTrue(hs.ok)
            self.assertIn("jobs_active", hs.details)

        # ── submit / status / result ──────────────────────────────────────────

        def test_submit_returns_prefixed_job_id(self) -> None:
            ctx = self._make_ctx()
            self.plugin.on_load(ctx)
            job_id = self.plugin.submit(self._make_spec())
            self.assertTrue(job_id.startswith(self.plugin.job_id_prefix), job_id)

        def test_status_returns_compute_status(self) -> None:
            ctx = self._make_ctx()
            self.plugin.on_load(ctx)
            job_id = self.plugin.submit(self._make_spec())
            st = self.plugin.status(job_id)
            self.assertEqual(st.engine_id, self.plugin.engine_id)
            self.assertIn(st.state, ("queued", "running", "terminal"))

        def test_result_reaches_terminal(self) -> None:
            ctx = self._make_ctx()
            self.plugin.on_load(ctx)
            job_id = self.plugin.submit(self._make_spec(max_iterations=5))
            res = self.plugin.result(job_id, wait_s=10.0)
            self.assertIn(res.state, ("converged", "failed"))

        def test_unknown_job_id_raises(self) -> None:
            ctx = self._make_ctx()
            self.plugin.on_load(ctx)
            with self.assertRaises(UnknownJobId):
                self.plugin.status("myengine_doesnotexist")

        # ── abort ─────────────────────────────────────────────────────────────

        def test_abort_before_completion(self) -> None:
            ctx = self._make_ctx()
            self.plugin.on_load(ctx)
            job_id = self.plugin.submit(self._make_spec(max_iterations=10_000))
            time.sleep(0.05)
            self.plugin.abort(job_id)
            res = self.plugin.result(job_id, wait_s=5.0)
            self.assertIn(res.state, ("converged", "terminal", "failed"))

        # ── gate guard ────────────────────────────────────────────────────────

        def test_gate_action_raises_when_no_gates(self) -> None:
            if self.plugin.supports_gates:
                self.skipTest("engine supports gates — skipping guard test")
            ctx = self._make_ctx()
            self.plugin.on_load(ctx)
            job_id = self.plugin.submit(self._make_spec())
            with self.assertRaises(EngineDoesNotSupportGates):
                self.plugin.gate_action(job_id, GateAction(action_type="resume"))

    suite = unittest.TestLoader().loadTestsFromTestCase(ComputeEnginePluginTests)
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
