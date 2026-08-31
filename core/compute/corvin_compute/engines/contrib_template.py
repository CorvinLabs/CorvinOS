"""contrib_template.py — Starter template for a custom Corvin Compute Engine.

Copy this file, rename the class, and fill in the three sections marked
"── IMPLEMENT ME ──". Everything else (state machine, thread safety, gate
dispatch, registration helper) is wired up for you.

Quick-start checklist
---------------------
1.  Pick a unique ``engine_id`` and ``job_id_prefix``  (no collisions with
    "flat", "pipeline", "hac" or each other).
2.  Define your engine-specific config in ``ContribEngineSpec`` (TypedDict).
3.  Implement ``_run_job``: the core compute loop.
4.  Decide whether your engine supports gates (``supports_gates``); if so,
    implement ``_handle_gate``.
5.  Call ``register_contrib_engine(...)`` before ``WorkerServer.serve_forever()``.
6.  Add ``"your_engine_id"`` to the tenant's ``engines_allowed`` list in
    ``tenant.corvin.yaml``.
7.  Write tests — see the inline test harness at the bottom of this file.

Protocol reference
------------------
The ``ComputeEngine`` Protocol (ADR-0029) is defined in ``engine_protocol.py``:

    submit(spec)           → job_id            (non-blocking)
    status(job_id)         → ComputeStatus
    result(job_id, wait_s) → ComputeResult      (blocks up to wait_s)
    gate_action(job_id, a) → None               (no-op if supports_gates=False)
    abort(job_id)          → None

State machine (see docs/assets/plugin-state-machine.svg):
----------------------------------------------------------
#   (none) → queued → running → [gate_open ⇄] → terminal / failed

Thread-safety contract
----------------------
``_jobs`` and ``_lock`` guard all shared state.  The background thread
writes via ``_set_state`` / ``_set_result``. The MCP thread reads via
``status()`` / ``result()`` and delivers gate actions via ``gate_action()``.
``threading.Event`` objects mediate blocking in ``result()`` and
``gate_action()`` (gate wait).
"""
from __future__ import annotations

import logging
import secrets
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, TypedDict

from ..engine_protocol import (
    ComputeEngine,          # Protocol (for isinstance checks)
    ComputeResult,
    ComputeSpec,
    ComputeStatus,
    EngineDoesNotSupportGates,
    GateAction,
    UnknownJobId,
)

log = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────────────
# 1.  Engine-specific configuration  (read from ComputeSpec.extra)
#
#     Define every custom key your engine reads from spec.extra here.
#     TypedDict gives you IDE completion and clear contributor docs.
# ──────────────────────────────────────────────────────────────────────────────

class ContribEngineSpec(TypedDict, total=False):
    """Keys recognised inside ``ComputeSpec.extra`` for this engine.

    All keys are optional (``total=False``); the engine supplies defaults.
    """
    # ── IMPLEMENT ME ── add your custom fields here ──────────────────────────
    max_iterations: int       # hard cap on compute iterations (default: 100)
    checkpoint_every: int     # emit a gate every N iterations (default: 0=off)
    custom_param: str         # example: algorithm variant selector
    # ─────────────────────────────────────────────────────────────────────────


# ──────────────────────────────────────────────────────────────────────────────
# 2.  Internal per-job state
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class _JobState:
    job_id: str
    spec: ComputeSpec
    state: str = "queued"           # queued | running | gate_open | terminal | failed
    iterations_done: int = 0
    best_loss: float = float("inf")
    best_params: dict = field(default_factory=dict)
    error: str | None = None
    result: dict = field(default_factory=dict)

    # Synchronisation primitives
    done_event: threading.Event = field(default_factory=threading.Event)
    gate_event: threading.Event = field(default_factory=threading.Event)
    gate_action: GateAction | None = None           # set by gate_action()


# ──────────────────────────────────────────────────────────────────────────────
# 3.  Engine implementation
# ──────────────────────────────────────────────────────────────────────────────

class ContribEngine:
    """Template for a custom Corvin Compute Engine (ADR-0029).

    Rename this class (e.g. ``SimulatedAnnealingEngine``) and fill in the
    "IMPLEMENT ME" sections.  The protocol surface, thread model, and gate
    dispatch are already wired up.
    """

    # ── Identity ──────────────────────────────────────────────────────────────
    # These three strings MUST be globally unique across all registered engines.
    # job_id_prefix must end with "_" and must not clash with:
    #   "compute_"  (FlatEngine)
    #   "pipeline_" (PipelineEngine)
    #   "hac_"      (HACEngine)
    engine_id     = "contrib"           # ── IMPLEMENT ME ── rename
    display_name  = "Contrib Template Engine (ADR-0029)"
    job_id_prefix = "contrib_"         # ── IMPLEMENT ME ── rename
    supports_gates = False             # set True if your engine has gates

    def __init__(
        self,
        *,
        corvin_home: Path,
        audit_emit: Callable[[str, dict], None] | None = None,
    ) -> None:
        """
        Args:
            corvin_home: Resolved tenant corvin home (for disk persistence).
            audit_emit:   Optional callable ``(event_name, metadata) → None``
                          wired to the tenant's hash-chained audit log.
                          Pass ``None`` to skip auditing (tests / offline use).

        Prefer keyword-only arguments so callers are explicit.
        """
        self._corvin_home = corvin_home
        self._audit_emit = audit_emit or (lambda *_: None)

        self._jobs: dict[str, _JobState] = {}
        self._lock = threading.Lock()

    # ── Protocol: submit ──────────────────────────────────────────────────────

    def submit(self, spec: ComputeSpec) -> str:
        """Start a new job and return its job_id.  Non-blocking."""
        job_id = self.job_id_prefix + secrets.token_urlsafe(22)
        state = _JobState(job_id=job_id, spec=spec)

        with self._lock:
            self._jobs[job_id] = state

        # Emit audit event BEFORE starting work (fail-safe: if thread fails,
        # the audit still records that a job was requested).
        self._audit_emit("compute.contrib_started", {
            "job_id": job_id,
            "engine_id": self.engine_id,
            "tenant_id": spec.tenant_id,
            "strategy": spec.strategy,
        })

        t = threading.Thread(
            target=self._run_job,
            args=(job_id,),
            daemon=True,
            name=f"contrib-{job_id[:12]}",
        )
        t.start()
        return job_id

    # ── Protocol: status ──────────────────────────────────────────────────────

    def status(self, job_id: str) -> ComputeStatus:
        """Return current state.  Safe to poll frequently."""
        js = self._require(job_id)
        with self._lock:
            return ComputeStatus(
                job_id=job_id,
                engine_id=self.engine_id,
                state=js.state,
                progress={
                    "iterations_done": js.iterations_done,
                    "best_loss": js.best_loss,
                },
                detail={
                    "best_params": js.best_params,
                },
            )

    # ── Protocol: result ──────────────────────────────────────────────────────

    def result(self, job_id: str, wait_s: float = 30.0) -> ComputeResult:
        """Block up to *wait_s* seconds and return the terminal result."""
        js = self._require(job_id)
        js.done_event.wait(timeout=wait_s)

        with self._lock:
            state = js.state
            result_dict = dict(js.result)
            error = js.error

        if state not in ("terminal", "failed"):
            # Still running after timeout — return partial progress.
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

    # ── Protocol: gate_action ─────────────────────────────────────────────────

    def gate_action(self, job_id: str, action: GateAction) -> None:
        """Deliver a gate action to a waiting job.

        If ``supports_gates=False``, raises ``EngineDoesNotSupportGates``.
        Called from the MCP thread; must be thread-safe.
        """
        if not self.supports_gates:
            raise EngineDoesNotSupportGates(
                f"{self.display_name} has no gates — set supports_gates=True"
            )
        js = self._require(job_id)
        with self._lock:
            if js.state != "gate_open":
                log.warning(
                    "gate_action on job %s in state %s (expected gate_open)",
                    job_id, js.state,
                )
                return
            js.gate_action = action
        # Unblock the background thread.
        js.gate_event.set()

    # ── Protocol: abort ───────────────────────────────────────────────────────

    def abort(self, job_id: str) -> None:
        """Request graceful termination.

        Sets the job to a terminal state and unblocks any gate wait so
        the background thread exits cleanly.
        """
        js = self._require(job_id)
        with self._lock:
            if js.state in ("terminal", "failed"):
                return
            js.state = "terminal"
            js.result = {"converge_reason": "aborted"}
            js.gate_action = GateAction(action_type="abort")
        js.gate_event.set()     # unblock gate wait (if any)
        js.done_event.set()     # unblock result() callers
        self._audit_emit("compute.contrib_aborted", {"job_id": job_id})

    # ── Internal: background worker ───────────────────────────────────────────

    def _run_job(self, job_id: str) -> None:
        """Background thread: runs the actual computation.

        Transitions:  queued → running → [gate_open → running …] → terminal
        """
        js = self._require(job_id)
        self._set_state(js, "running")

        # Read engine-specific config from spec.extra
        extra: ContribEngineSpec = js.spec.extra  # type: ignore[assignment]
        max_iter       = int(extra.get("max_iterations", 100))
        checkpoint_n   = int(extra.get("checkpoint_every", 0))

        try:
            # ── IMPLEMENT ME ── Replace this block with real compute logic ────
            #
            # Conventions:
            #   • Call a forge tool via spec.tool_name / spec.param_grid.
            #   • Update js.best_loss / js.best_params on improvement.
            #   • Increment js.iterations_done each iteration.
            #   • Open a gate (if supports_gates=True) with _open_gate().
            #   • Check for abort between iterations.
            #   • Raise on unrecoverable errors; _run_job catches them.
            #
            # Example skeleton:
            for i in range(max_iter):
                with self._lock:
                    if js.state == "terminal":
                        return          # aborted externally

                # --- your per-iteration compute here ---
                fake_loss = 1.0 / (i + 1)          # placeholder
                fake_params = {"iter": i}           # placeholder

                with self._lock:
                    if fake_loss < js.best_loss:
                        js.best_loss = fake_loss
                        js.best_params = fake_params
                    js.iterations_done = i + 1

                # Open a checkpoint gate every checkpoint_n iterations
                if checkpoint_n > 0 and (i + 1) % checkpoint_n == 0:
                    action = self._open_gate(js, reason="checkpoint")
                    if action and action.action_type == "abort":
                        return

            # ── IMPLEMENT ME ── end of your compute block ─────────────────────

            self._set_result(js, {
                "best_loss":   js.best_loss,
                "best_params": js.best_params,
                "iterations":  js.iterations_done,
                "converge_reason": "budget",
            })

        except Exception as exc:  # noqa: BLE001
            log.exception("ContribEngine job %s failed", job_id)
            with self._lock:
                js.state = "failed"
                js.error = str(exc)
            js.done_event.set()
            self._audit_emit("compute.contrib_failed", {
                "job_id": job_id,
                "error": type(exc).__name__,    # type name only, not message — no PII
            })

    # ── Internal: gate helper ─────────────────────────────────────────────────

    def _open_gate(self, js: _JobState, *, reason: str = "") -> GateAction | None:
        """Transition to gate_open, block until gate_action() delivers a decision.

        Returns the GateAction the caller delivered, or None if the job was
        aborted externally before the gate resolved.

        Only call this from the background worker thread.
        """
        if not self.supports_gates:
            return None

        timeout_s: float = float(js.spec.extra.get("gate_timeout_s", 3600))

        with self._lock:
            js.state = "gate_open"
            js.gate_event.clear()
            js.gate_action = None

        self._audit_emit("compute.contrib_gate_opened", {
            "job_id": js.job_id,
            "reason": reason,
            "iterations_done": js.iterations_done,
        })

        js.gate_event.wait(timeout=timeout_s)

        with self._lock:
            action = js.gate_action
            if action is None:
                # Timeout — auto-resume
                action = GateAction(action_type="resume")
            if action.action_type != "abort":
                js.state = "running"
            return action

    # ── Internal: handle gate payload ─────────────────────────────────────────

    def _handle_gate(self, js: _JobState, action: GateAction) -> None:
        """Apply gate-specific side effects (called AFTER _open_gate returns).

        ── IMPLEMENT ME ── if supports_gates=True, add your custom gate
        action types here.  "resume" and "abort" are handled by _run_job.
        """
        if action.action_type == "resume":
            return                          # nothing extra to do
        if action.action_type == "abort":
            return                          # _run_job will return
        # ── IMPLEMENT ME ── custom action types ───────────────────────────────
        # Example:
        #   if action.action_type == "adjust_lr":
        #       self._lr = float(action.payload.get("lr", self._lr))
        # ─────────────────────────────────────────────────────────────────────
        log.warning("unknown gate action_type %r for job %s", action.action_type, js.job_id)

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
        self._audit_emit("compute.contrib_terminal", {
            "job_id":     js.job_id,
            "iterations": js.iterations_done,
            "converge":   result.get("converge_reason", ""),
        })


# ──────────────────────────────────────────────────────────────────────────────
# 4.  Registration helper
#
#     Call this once before WorkerServer.serve_forever().
#     The server wires the engine into prefix routing and the MCP bridge.
# ──────────────────────────────────────────────────────────────────────────────

def register_contrib_engine(
    corvin_home: Path,
    *,
    audit_emit: Callable[[str, dict], None] | None = None,
) -> ContribEngine:
    """Create and register a ContribEngine instance.

    Typical usage (in the server entrypoint)::

        from corvin_compute.engines.contrib_template import register_contrib_engine

        engine = register_contrib_engine(corvin_home, audit_emit=emit)
        server = WorkerServer(..., extra_engines=[engine])
        server.serve_forever()

    Returns the engine so callers can hold a reference for testing or
    runtime introspection.
    """
    from .. import engine_registry as reg  # late import — avoids circular at module load

    engine = ContribEngine(corvin_home=corvin_home, audit_emit=audit_emit)
    reg.register(engine)
    log.info("registered engine %r (prefix=%r)", engine.engine_id, engine.job_id_prefix)
    return engine


# ──────────────────────────────────────────────────────────────────────────────
# 5.  Tenant configuration reference
#
#     Add this block to your tenant.corvin.yaml to enable the engine:
#
#     spec:
#       compute:
#         enabled: true
#         engines_allowed: [flat, pipeline, hac, contrib]   # add "contrib"
#         contrib:                                           # engine-specific config
#           max_iterations: 200
#           checkpoint_every: 50
#           custom_param: "variant_a"
# ──────────────────────────────────────────────────────────────────────────────


# ──────────────────────────────────────────────────────────────────────────────
# 6.  Inline test harness
#     python3 core/compute/corvin_compute/engines/contrib_template.py
# ──────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    import tempfile
    import unittest

    from ..engine_protocol import ComputeEngine  # noqa: F811

    class ContribEngineTests(unittest.TestCase):

        def setUp(self) -> None:
            self.tmp = tempfile.mkdtemp()
            self.engine = ContribEngine(corvin_home=Path(self.tmp))

        # ── Protocol conformance ──────────────────────────────────────────────

        def test_implements_protocol(self) -> None:
            self.assertIsInstance(self.engine, ComputeEngine)

        def test_engine_id_and_prefix(self) -> None:
            self.assertTrue(self.engine.job_id_prefix.endswith("_"))
            self.assertNotIn(self.engine.engine_id, ("flat", "pipeline", "hac"))

        # ── submit / status / result lifecycle ───────────────────────────────

        def _make_spec(self, **extra: Any) -> ComputeSpec:
            return ComputeSpec(
                engine=self.engine.engine_id,
                tenant_id="test",
                budget={"max_iterations": 10},
                tool_name=None,
                extra=extra,
            )

        def test_submit_returns_prefixed_job_id(self) -> None:
            job_id = self.engine.submit(self._make_spec())
            self.assertTrue(job_id.startswith(self.engine.job_id_prefix), job_id)

        def test_status_returns_compute_status(self) -> None:
            job_id = self.engine.submit(self._make_spec())
            st = self.engine.status(job_id)
            self.assertEqual(st.engine_id, self.engine.engine_id)
            self.assertIn(st.state, ("queued", "running", "terminal"))

        def test_result_reaches_terminal(self) -> None:
            job_id = self.engine.submit(self._make_spec(max_iterations=5))
            res = self.engine.result(job_id, wait_s=10.0)
            self.assertIn(res.state, ("converged", "failed"))

        def test_unknown_job_id_raises(self) -> None:
            with self.assertRaises(UnknownJobId):
                self.engine.status("contrib_doesnotexist")

        # ── abort ─────────────────────────────────────────────────────────────

        def test_abort_before_completion(self) -> None:
            job_id = self.engine.submit(self._make_spec(max_iterations=10_000))
            time.sleep(0.05)                    # let it start
            self.engine.abort(job_id)
            res = self.engine.result(job_id, wait_s=5.0)
            self.assertIn(res.state, ("converged", "terminal", "failed"))

        # ── gate_action guard ─────────────────────────────────────────────────

        def test_gate_action_raises_when_no_gates(self) -> None:
            if self.engine.supports_gates:
                self.skipTest("engine supports gates — skipping guard test")
            job_id = self.engine.submit(self._make_spec())
            with self.assertRaises(EngineDoesNotSupportGates):
                self.engine.gate_action(job_id, GateAction(action_type="resume"))

    # Run the tests
    suite = unittest.TestLoader().loadTestsFromTestCase(ContribEngineTests)
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
