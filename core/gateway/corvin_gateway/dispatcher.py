"""Run dispatcher — drives accepted runs through the engine layer.

ADR-0007 Phase 2.3.

The dispatcher is the bridge between the Gateway's HTTP surface
(Phase 2.2, ``app.py``) and the existing Corvin engine layer
(``bridges/shared/agents/claude_code.py``). It owns:

* Asynchronous fire-and-forget scheduling — the HTTP handler returns
  202 immediately; the actual engine work happens in a background
  task driven by the FastAPI lifespan.
* Status state-machine transitions from ``accepted`` to
  ``running`` to one of ``completed`` / ``failed`` / ``budget_exceeded``.
* Tenant-id propagation — every engine subprocess is spawned with
  ``CORVIN_TENANT_ID=<tid>`` in env so any downstream forge /
  skill-forge / state-store write lands in the right tenant tree.
* Wall-clock budget enforcement via :func:`asyncio.wait_for`.

What this module does NOT do
----------------------------

* It does not own the engine itself — engines are pluggable through
  ``engine_factory``. The default is ``ClaudeCodeEngine``; tests
  inject a stub.
* It does not stream events back to the client — Phase 2.5 (SSE)
  will subscribe to a future event-buffer the dispatcher will gain
  alongside the worker. Phase 2.3 ships the offline lifecycle only.
* It does not fire webhooks — that's Phase 2.4. Once dispatched
  state lands as ``completed`` / ``failed`` / ``budget_exceeded``,
  Phase 2.4's webhook worker picks the record up out-of-band.
* It does not enqueue work onto a persistent queue. Phase 7 will add
  a durable queue when rate-limiting + multi-process workers land;
  Phase 2 keeps the simpler in-process model. A crash of the gateway
  process between ``accepted`` and ``running`` leaves the run in
  ``accepted`` — the operator (or a future janitor) sweeps stale
  entries.
"""
from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path
from typing import Any, Callable, Iterator, Protocol, runtime_checkable

# Engine layer lives in voice/bridges/shared/agents/; add it to sys.path
# so the import below is straightforward. Same pattern auth.py / runs.py
# use for the forge package.
_THIS_DIR = Path(__file__).resolve().parent
_REPO = _THIS_DIR.parents[3]
_BRIDGES_SHARED = _REPO / "operator" / "bridges" / "shared"
if str(_BRIDGES_SHARED) not in sys.path:
    sys.path.insert(0, str(_BRIDGES_SHARED))

from .runs import (
    RunNotFound,
    RunRecord,
    RunRegistry,
    RunStoreMalformed,
)
from .sse import EventBufferRegistry, RunEventBuffer, stream_event_to_dict
from .webhooks import WebhookDispatcher
from . import tenant_config as _tenant_config
from . import durable_queue as _durable_queue

# Engine/zone-policy audit events live in the unified hash chain.
# forge.security_events is already on sys.path via runs.py / auth.py;
# registering keeps EVENT_SEVERITY authoritative.
from forge import security_events as _security_events  # noqa: E402

# L44 acceptable-use (house-rules) pre-spawn gate — ADR-0143, MANDATORY,
# fail-CLOSED. ``spawn_gates`` lives in operator/bridges/shared, already on
# sys.path above. The module itself audits the deny/escalate decision into the
# per-tenant L16 forge chain before returning, so the dispatcher only needs to
# fail the run on a non-None refusal string.
from spawn_gates import check_l44 as _check_l44  # noqa: E402

# L34 data-classification + L35 network-egress pre-spawn gates — ADR-0042 /
# ADR-0043. Same shared ``spawn_gates`` SSOT the console, bridge adapter and
# a2a_worker use. Unlike L44 (fail-CLOSED on module-absence), L34/L35 fail-OPEN
# only when the module itself is absent (adapter/a2a parity); an EVALUATION
# error inside the gate fails CLOSED (the gate returns a refusal string). If the
# import fails entirely we degrade to None → the gate is skipped (module-absence
# = fail-open), matching how the other surfaces treat a missing gate module.
try:
    from spawn_gates import (  # noqa: E402
        check_l34 as _check_l34,
        check_l35 as _check_l35,
    )
except Exception:  # noqa: BLE001 — L34/L35 module-absence fails OPEN (parity)
    _check_l34 = None  # type: ignore[assignment]
    _check_l35 = None  # type: ignore[assignment]

# ADR-0171 — universal engine span. operator/bridges/shared is on sys.path
# (spawn_gates above). Guarded: a missing module degrades to gateway.* events only.
try:
    import engine_span as _espan  # type: ignore  # noqa: E402
except Exception:  # noqa: BLE001
    _espan = None  # type: ignore[assignment]

_POLICY_EVENTS = {
    "gateway.engine_denied":   "WARNING",
    "gateway.zone_denied":     "WARNING",
}
for _evt, _sev in _POLICY_EVENTS.items():
    _security_events.EVENT_SEVERITY.setdefault(_evt, _sev)


# ── Engine protocol (minimal — for stub-in-test) ─────────────────────


@runtime_checkable
class _EngineLike(Protocol):
    """Minimal slice of the engine surface the dispatcher consumes.

    The real :class:`ClaudeCodeEngine` satisfies this naturally; tests
    pass a synthetic stub with the same shape.
    """

    name: str

    def spawn(
        self,
        prompt: str,
        *,
        env: dict[str, str] | None = None,
        timeout: float = 120.0,
    ) -> Iterator[Any]:
        ...


# ── Defaults ─────────────────────────────────────────────────────────


DEFAULT_BUDGET_S = 60
"""Wall-clock cap when ``budget_override.max_wall_clock_s`` is absent."""


def _default_engine_factory() -> _EngineLike:
    """Lazy import — keeps the dispatcher importable in environments
    without the bridges/ subtree (e.g. unit-test-only sandboxes)."""
    from agents.claude_code import ClaudeCodeEngine  # type: ignore[import]
    return ClaudeCodeEngine()


# ── Result + dispatcher ──────────────────────────────────────────────


class RunDispatcher:
    """Drive an accepted run through the engine layer.

    Construction is cheap; the dispatcher is normally instantiated
    once per gateway process via the FastAPI ``lifespan`` handler and
    reused across requests. The engine factory is called per-run so
    crashes in one engine instance don't poison subsequent runs.
    """

    def __init__(
        self,
        *,
        registry: RunRegistry | None = None,
        engine_factory: Callable[[], _EngineLike] | None = None,
        default_budget_s: int = DEFAULT_BUDGET_S,
        webhook_dispatcher: WebhookDispatcher | None = None,
        event_registry: EventBufferRegistry | None = None,
    ) -> None:
        self._registry = registry or RunRegistry()
        self._engine_factory = engine_factory or _default_engine_factory
        self._default_budget_s = default_budget_s
        self._webhook_dispatcher = webhook_dispatcher or WebhookDispatcher()
        self._event_registry = event_registry or EventBufferRegistry()
        self._in_flight: set[asyncio.Task] = set()
        self._webhook_tasks: set[asyncio.Task] = set()

    @property
    def events(self) -> EventBufferRegistry:
        """Expose the event-buffer registry so the SSE endpoint can
        subscribe by (tenant_id, run_id)."""
        return self._event_registry

    # ------------------------------------------------------------------
    # Audit helper (policy decisions)
    # ------------------------------------------------------------------

    def _audit_policy(
        self,
        event_type: str,
        *,
        tenant_id: str,
        details: dict[str, Any] | None = None,
        severity: str | None = None,
    ) -> None:
        """Best-effort audit write into the tenant's unified chain.

        Mirror of ``runs._audit`` — failures never wedge the dispatch
        path. Used by Phase 3.2 / 3.3 policy denials.
        """
        try:
            from forge import paths as _fp  # local import: same lazy guard
            chain_path = (
                _fp.tenant_global_dir(tenant_id) / "forge" / "audit.jsonl"
            )
            _security_events.write_event(
                chain_path, event_type,
                severity=severity, details=dict(details or {}),
                hash_chain=True,
            )
        except Exception:
            pass

    def _emit_engine_span(self, kind: str, *, tenant_id: str, run_id: str,
                          engine_id: str, status: str = "ok",
                          duration_ms: int = 0) -> None:
        """ADR-0171 — engine.span.start/end (role=worker) for the gateway run on
        the tenant's unified chain. Best-effort; never wedges dispatch."""
        if _espan is None:
            return
        span_id = f"spn-{run_id}-w0"
        if kind == "start":
            self._audit_policy(
                _espan.ENGINE_SPAN_START, tenant_id=tenant_id, severity="INFO",
                details=_espan.start_details(span_id=span_id, role="worker",
                                             engine_id=engine_id, run_id=run_id))
        else:
            self._audit_policy(
                _espan.ENGINE_SPAN_END, tenant_id=tenant_id, severity="INFO",
                details=_espan.end_details(span_id=span_id, role="worker",
                                           engine_id=engine_id, run_id=run_id,
                                           status=status, duration_ms=int(duration_ms)))

    # ------------------------------------------------------------------
    # Public surface
    # ------------------------------------------------------------------

    def submit(self, tenant_id: str, run_id: str) -> asyncio.Task:
        """Fire-and-forget. Returns the asyncio.Task for tests / drain.

        Production callers ignore the return value; the
        FastAPI ``lifespan`` shutdown handler awaits :meth:`drain` to
        finish in-flight work cleanly.

        Phase 7.1: also enqueue into the durable queue so a gateway
        crash between this point and ``_set_terminal`` doesn't strand
        the run. Recovery sweep at startup re-dispatches everything
        still ``pending`` in the queue.
        """
        try:
            _durable_queue.enqueue(tenant_id, run_id)
        except Exception:
            # Queue write is best-effort — a transient SQLite hiccup
            # must never block the in-memory dispatch path.
            pass
        task = asyncio.create_task(
            self._run_one(tenant_id, run_id),
            name=f"gateway-run:{run_id}",
        )
        self._in_flight.add(task)
        task.add_done_callback(self._in_flight.discard)
        return task

    async def recover_pending(self) -> int:
        """Re-dispatch every run still pending in the durable queue.

        Called by the FastAPI lifespan handler on startup. Returns
        the number of runs re-dispatched.
        """
        try:
            pending = _durable_queue.recover_pending()
        except Exception:
            return 0
        recovered = 0
        for tid, rid in pending:
            try:
                # Use submit() so the task lands in _in_flight + we
                # get the re-enqueue idempotency.
                self.submit(tid, rid)
                recovered += 1
            except Exception:
                continue
        return recovered

    async def drain(self, *, timeout: float | None = None) -> None:
        """Wait for every in-flight task to finish.

        Called from the FastAPI ``lifespan`` shutdown path. Idempotent
        and re-entrant; a second call on an empty set is a no-op.
        Run tasks finish first, then webhook tasks — a webhook for a
        completed run must not race the drain into oblivion.
        """
        pending_runs = list(self._in_flight)
        if pending_runs:
            if timeout is None:
                await asyncio.gather(*pending_runs, return_exceptions=True)
            else:
                _done, still = await asyncio.wait(pending_runs, timeout=timeout)
                for t in still:
                    t.cancel()
        pending_webhooks = list(self._webhook_tasks)
        if pending_webhooks:
            if timeout is None:
                await asyncio.gather(*pending_webhooks, return_exceptions=True)
            else:
                _done, still = await asyncio.wait(pending_webhooks, timeout=timeout)
                for t in still:
                    t.cancel()

    @property
    def in_flight(self) -> int:
        return len(self._in_flight)

    @property
    def webhook_in_flight(self) -> int:
        return len(self._webhook_tasks)

    # ------------------------------------------------------------------
    # Webhook fan-out
    # ------------------------------------------------------------------

    def _maybe_dispatch_webhook(self, tenant_id: str, run_id: str) -> None:
        """Best-effort: read the final record and fire a webhook if
        ``spec.webhook`` is set. Failures are caught — webhook
        delivery never breaks the run lifecycle.
        """
        try:
            record = self._registry.get(tenant_id, run_id)
        except (RunNotFound, RunStoreMalformed):
            return
        if record.status not in ("completed", "failed", "budget_exceeded"):
            return
        webhook = (record.request or {}).get("spec", {}).get("webhook")
        if not webhook or not isinstance(webhook, dict):
            return
        url = webhook.get("url")
        secret_ref = webhook.get("secret_ref")
        if not isinstance(url, str) or not isinstance(secret_ref, str):
            return
        task = asyncio.create_task(
            self._webhook_dispatcher.dispatch_for_record(
                record, url=url, secret_ref=secret_ref,
            ),
            name=f"gateway-webhook:{run_id}",
        )
        self._webhook_tasks.add(task)
        task.add_done_callback(self._webhook_tasks.discard)

    # ------------------------------------------------------------------
    # Per-run driver
    # ------------------------------------------------------------------

    async def _run_one(self, tenant_id: str, run_id: str) -> None:
        """Drive one run through the engine."""
        try:
            record = self._registry.get(tenant_id, run_id)
        except (RunNotFound, RunStoreMalformed):
            # Race window: the file was deleted between accept and
            # dispatch. Nothing to do; the absence is its own audit.
            return

        # Idempotent: if a prior dispatch already moved the run, skip.
        if record.status != "accepted":
            return

        try:
            self._registry.set_status(tenant_id, run_id, "running")
        except (RunNotFound, RunStoreMalformed, ValueError):
            return

        spec = (record.request or {}).get("spec", {}) or {}
        prompt = str(spec.get("input", ""))
        persona = str(spec.get("persona", ""))
        budget_override = spec.get("budget_override") or {}
        budget_s = int(
            budget_override.get("max_wall_clock_s")
            or self._default_budget_s
        )

        # SSE event buffer — one per run. The dispatcher worker writes
        # engine events into it; the GET /events endpoint subscribes.
        loop = asyncio.get_running_loop()
        event_buffer = self._event_registry.get_or_create(
            tenant_id, run_id, loop,
        )

        # Per-run env: tenant + persona + caller binding. The engine
        # inherits os.environ + this overlay; CORVIN_TENANT_ID makes
        # downstream forge/skill-forge writes land in the right tree.
        spawn_env = {
            "CORVIN_TENANT_ID":      tenant_id,
            "CORVIN_CALLER_PERSONA": persona,
            "CORVIN_CHANNEL_ID":     f"gateway:{run_id}",
        }

        # Construct the engine HERE (not inside the worker thread) so
        # the budget-timeout path can call engine.cancel() to free the
        # subprocess. Without this, asyncio.wait_for cancels its own
        # coroutine but leaves the worker thread + any spawned engine
        # subprocess running to completion.
        try:
            engine = self._engine_factory()
        except Exception as exc:
            self._set_terminal(
                tenant_id, run_id, "failed",
                error=f"engine-factory: {type(exc).__name__}: {exc}",
            )
            return

        # Phase 3.2: gate the engine against the tenant's policy
        # (tenant.corvin.yaml). Missing config → permissive default
        # (every engine allowed); defective config → fail-closed.
        try:
            tcfg = _tenant_config.load_or_default(tenant_id)
        except _tenant_config.TenantConfigMalformed as exc:
            self._audit_policy(
                "gateway.engine_denied", tenant_id=tenant_id,
                details={
                    "run_id":  run_id,
                    "engine":  getattr(engine, "name", "<unknown>"),
                    "reason":  "tenant-config-malformed",
                    # ADR-0129: "message" is a denylisted (content) key →
                    # always dropped. Use the exception CLASS name — a useful
                    # diagnostic that carries no content and survives the floor.
                    "error_class": type(exc).__name__,
                },
            )
            self._set_terminal(
                tenant_id, run_id, "failed",
                error=f"tenant-config-malformed: {exc}",
            )
            return
        engine_name = getattr(engine, "name", "")
        if not tcfg.is_engine_allowed(engine_name):
            self._audit_policy(
                "gateway.engine_denied", tenant_id=tenant_id,
                details={
                    "run_id": run_id,
                    "engine": engine_name,
                    "allowed_engines": list(
                        tcfg.spec.data_residency.allowed_engines
                    ),
                    "forbid_engines": list(
                        tcfg.spec.data_residency.forbid_engines
                    ),
                },
            )
            self._set_terminal(
                tenant_id, run_id, "failed",
                error=f"engine-not-allowed: {engine_name}",
            )
            return

        # Phase 3.3: data-residency zone gate. Tenants with a pinned
        # zone (spec.data_residency.zone) require the engine to
        # advertise the same zone — or "global", which means the
        # engine serves every zone. Engines without a `zone`
        # attribute default to "global", so the gate is a no-op for
        # backwards-compat with engines that predate Phase 3.3.
        tenant_zone = tcfg.spec.data_residency.zone
        if tenant_zone is not None:
            engine_zone = getattr(engine, "zone", "global") or "global"
            if engine_zone != "global" and engine_zone != tenant_zone:
                self._audit_policy(
                    "gateway.zone_denied", tenant_id=tenant_id,
                    details={
                        "run_id":      run_id,
                        "engine":      engine_name,
                        "engine_zone": engine_zone,
                        "tenant_zone": tenant_zone,
                    },
                )
                self._set_terminal(
                    tenant_id, run_id, "failed",
                    error=(
                        f"zone-mismatch: engine={engine_name!r} "
                        f"zone={engine_zone!r} != "
                        f"tenant_zone={tenant_zone!r}"
                    ),
                )
                return

        # L34 data-classification + L35 network-egress gates — ADR-0042 / ADR-0043,
        # fail-CLOSED on evaluation error. The gateway EXECUTE path (REST POST
        # /v1/tenants/{tid}/runs AND gRPC SubmitRun funnel through _run_one) is a
        # peer of the console (check_console_spawn_or_refusal), the bridge adapter
        # and a2a_worker — all of which run L34 + L35 + L44 before a spawn. Without
        # these two the gateway would spawn a cloud engine on a CONFIDENTIAL/SECRET
        # prompt without consulting the tenant residency matrix (L34) or the egress
        # policy (L35). check_l34/check_l35 are the shared spawn_gates SSOT: they
        # fail-OPEN only on genuine no-policy / module-absence and fail-CLOSED
        # (refusal string) on any evaluation error, and each emits its own
        # data_flow.{approved,blocked} / egress L16 audit event before returning.
        # Run BEFORE the compute meter so a blocked request is never charged.
        _gw_engine_id = engine_name or getattr(engine, "name", "claude_code")
        if _check_l34 is not None:
            try:
                _l34_refusal = _check_l34(
                    _gw_engine_id, tenant_id,
                    prompt=prompt, persona=persona,
                    channel="gateway", chat_key=f"gateway:{run_id}",
                )
            except Exception as exc:  # noqa: BLE001 — eval error → FAIL-CLOSED
                self._set_terminal(
                    tenant_id, run_id, "failed",
                    error=f"data-classification-gate-error: {type(exc).__name__}",
                )
                return
            if _l34_refusal is not None:
                # DataFlowGuard.validate already emitted data_flow.blocked.
                self._set_terminal(
                    tenant_id, run_id, "failed", error=_l34_refusal[:2000],
                )
                return
        if _check_l35 is not None:
            try:
                _l35_refusal = _check_l35(
                    _gw_engine_id, tenant_id,
                    persona=persona,
                    channel="gateway", chat_key=f"gateway:{run_id}",
                )
            except Exception as exc:  # noqa: BLE001 — eval error → FAIL-CLOSED
                self._set_terminal(
                    tenant_id, run_id, "failed",
                    error=f"egress-gate-error: {type(exc).__name__}",
                )
                return
            if _l35_refusal is not None:
                self._set_terminal(
                    tenant_id, run_id, "failed", error=_l35_refusal[:2000],
                )
                return

        # L44 acceptable-use (house-rules) gate — ADR-0143, MANDATORY, fail-CLOSED.
        # The gateway run-dispatch (REST POST /v1/tenants/{tid}/runs AND gRPC
        # SubmitRun both funnel through _run_one) spawns a claude OS-turn on a
        # user-controlled prompt; without this gate it is a second EXECUTE path
        # bypassing the acceptable-use guarantee the console + adapter enforce.
        # check_l44 is fail-closed (a missing module / tampered policy / classifier
        # error all REFUSE) and audit-first (it emits the house_rules.{denied,
        # escalated} L16 event on the tenant's forge chain BEFORE returning the
        # refusal). Runs BEFORE the compute meter so a blocked request is never
        # charged or spawned.
        try:
            _l44_refusal = _check_l44(
                prompt,
                tenant_id=tenant_id,
                persona=persona,
                channel="gateway",
                chat_key=f"gateway:{run_id}",
                engine_id=engine_name or getattr(engine, "name", "claude_code"),
            )
        except Exception as exc:  # noqa: BLE001 — the gate is fail-closed; an
            # unexpected raise from check_l44 itself must still REFUSE, never
            # fall through to a spawn. (check_l44 swallows its own errors into a
            # refusal string; this is belt-and-suspenders for the import path.)
            self._set_terminal(
                tenant_id, run_id, "failed",
                error=f"house-rules-gate-error: {type(exc).__name__}",
            )
            return
        if _l44_refusal is not None:
            # Audit already emitted inside check_l44 (house_rules.{denied,
            # escalated}) — do NOT re-emit here. Fail the run with the refusal
            # string; no engine spawn happens.
            self._set_terminal(
                tenant_id, run_id, "failed",
                error=_l44_refusal[:2000],
            )
            return

        # ADR-0149 LIC-GW-CQ-01: the gateway run-dispatch spawns a billable engine
        # run (REST POST /v1/tenants/{tid}/runs AND gRPC SubmitRun both funnel
        # through _run_one) but charged no compute_units_per_day — a second EXECUTE
        # path bypassing the meter the console routes + ACS chokepoint enforce.
        # Charge fail-CLOSED here, before the spawn. License-module-absent is
        # fail-open (boot self_test B1 covers genuine absence).
        try:
            _gw_op = Path(__file__).resolve().parents[3] / "operator"
            for _gw_p in (str(_gw_op / "forge"), str(_gw_op)):
                if _gw_p not in sys.path:
                    sys.path.insert(0, _gw_p)
            from license.compute_quota import increment_and_check as _gw_cq  # type: ignore
            from license.limits import LicenseLimitError as _GwCQErr  # type: ignore
            from forge import paths as _gw_paths  # type: ignore
        except ImportError:
            _gw_cq = None  # type: ignore[assignment]
            _GwCQErr = None  # type: ignore[assignment]
        if _gw_cq is not None:
            try:
                _gw_cq(_gw_paths.corvin_home(), channel="gateway",
                       chat_key=f"gateway:{tenant_id}:{run_id}")
            except _GwCQErr as _gw_exc:  # type: ignore[misc]
                self._set_terminal(
                    tenant_id, run_id, "failed",
                    error=f"compute_units_per_day exceeded: {_gw_exc}"[:300],
                )
                return
            except Exception:  # noqa: BLE001 — operational error already swallowed
                pass

        start = time.time()
        # ADR-0171 — engine.span.start (role=worker): every gateway engine run is
        # auditable as a span regardless of outcome (paired at all 4 exits below).
        self._emit_engine_span("start", tenant_id=tenant_id, run_id=run_id,
                               engine_id=engine_name)
        try:
            outcome = await asyncio.wait_for(
                asyncio.to_thread(
                    self._spawn_collect,
                    engine=engine,
                    prompt=prompt,
                    env=spawn_env,
                    event_buffer=event_buffer,
                ),
                timeout=float(budget_s),
            )
        except asyncio.TimeoutError:
            duration = time.time() - start
            # Best-effort: tell the engine to abandon work. The worker
            # thread may still complete, but its result is discarded
            # because the asyncio task already moved on.
            try:
                engine.cancel()
            except Exception:
                pass
            self._emit_engine_span("end", tenant_id=tenant_id, run_id=run_id,
                                   engine_id=engine_name, status="error",
                                   duration_ms=int(duration * 1000))
            self._set_terminal(
                tenant_id, run_id, "budget_exceeded",
                error=(
                    f"wall_clock_timeout after {duration:.1f}s "
                    f"(budget {budget_s}s)"
                ),
            )
            return
        except asyncio.CancelledError:
            # ADR-0171 — dispatcher drain/shutdown cancels the in-flight run task
            # (drain() → t.cancel()). CancelledError is BaseException so it
            # bypasses the `except Exception` below. Mirror the TimeoutError path
            # above: abandon the engine subprocess (engine.cancel()) AND move the
            # run to a terminal state BEFORE re-raising. Without both, the run is
            # stranded at status="running" forever (recover_pending re-dispatches
            # it but _run_one skips any non-"accepted" record → never finalized,
            # clients hang) and the worker thread + spawned `claude -p`
            # subprocess keep running (leak).
            duration = time.time() - start
            try:
                engine.cancel()
            except Exception:
                pass
            self._emit_engine_span("end", tenant_id=tenant_id, run_id=run_id,
                                   engine_id=engine_name, status="error",
                                   duration_ms=int(duration * 1000))
            # No "cancelled" state exists in the run state-machine
            # (runs.ALL_STATES = accepted/running/completed/failed/
            # budget_exceeded); set_status would reject it → ValueError →
            # _set_terminal no-ops and the run would stay "running". Use "failed"
            # with a cancellation diagnostic so the terminal transition sticks.
            # (_set_terminal is idempotent per terminal state; the span is emitted
            # exactly once here, not inside _set_terminal.)
            self._set_terminal(
                tenant_id, run_id, "failed",
                error="cancelled: dispatcher drain/shutdown",
            )
            # CancelledError MUST propagate for cooperative cancellation.
            raise
        except Exception as exc:  # engine crash, import error, etc.
            self._emit_engine_span("end", tenant_id=tenant_id, run_id=run_id,
                                   engine_id=engine_name, status="error",
                                   duration_ms=int((time.time() - start) * 1000))
            self._set_terminal(
                tenant_id, run_id, "failed",
                error=f"{type(exc).__name__}: {exc}",
            )
            return

        # Outcome shape from _spawn_collect: {"final_text", "usage",
        # "duration_ms", "error"}. An engine-level error message
        # (not an exception) still surfaces here and routes to
        # "failed" with the engine's diagnostic intact.
        if outcome.get("error"):
            self._emit_engine_span("end", tenant_id=tenant_id, run_id=run_id,
                                   engine_id=engine_name, status="error",
                                   duration_ms=int(outcome.get("duration_ms", 0)))
            self._set_terminal(
                tenant_id, run_id, "failed",
                result={"usage": outcome.get("usage") or {}},
                error=str(outcome["error"]),
            )
            return

        self._emit_engine_span("end", tenant_id=tenant_id, run_id=run_id,
                               engine_id=engine_name, status="ok",
                               duration_ms=int(outcome.get("duration_ms", 0)))
        self._set_terminal(
            tenant_id, run_id, "completed",
            result={
                "final_text":  outcome.get("final_text", ""),
                "usage":       outcome.get("usage") or {},
                "duration_ms": outcome.get("duration_ms", 0),
            },
        )

    def _set_terminal(
        self,
        tenant_id: str,
        run_id: str,
        status: str,
        *,
        result: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> None:
        """Set terminal status, close the SSE buffer, fan out the
        webhook if configured.

        Single funnel for every terminal transition — keeps the
        webhook trigger guaranteed to fire exactly once per terminal
        state and never on a re-write-into-the-same-terminal path.
        """
        try:
            self._registry.set_status(
                tenant_id, run_id, status,  # type: ignore[arg-type]
                result=result, error=error,
            )
        except (RunNotFound, RunStoreMalformed, ValueError):
            return
        # Close the SSE buffer with a terminal event so any
        # subscribed clients see the final status before the stream
        # ends. The buffer stays in the registry so late subscribers
        # (connecting after termination) still get the full replay.
        buf = self._event_registry.get(tenant_id, run_id)
        if buf is not None:
            buf.close({
                "type":   f"run.{status}",
                "status": status,
                "result": result,
                "error":  error,
            })
        # Phase 7.1: dequeue the run from the durable queue. Best-
        # effort; a transient DB hiccup must not block webhook
        # dispatch.
        try:
            _durable_queue.mark_terminal(tenant_id, run_id)
        except Exception:
            pass
        self._maybe_dispatch_webhook(tenant_id, run_id)

    # ------------------------------------------------------------------
    # Engine bridge (sync — runs in worker thread)
    # ------------------------------------------------------------------

    def _spawn_collect(
        self,
        *,
        engine: _EngineLike,
        prompt: str,
        env: dict[str, str],
        event_buffer: RunEventBuffer | None = None,
    ) -> dict[str, Any]:
        """Sync wrapper: drain the engine stream, project to dict.

        Runs inside an asyncio worker thread via :func:`asyncio.to_thread`.
        The engine instance is constructed in the parent coroutine so
        ``engine.cancel()`` is reachable from the budget-timeout path.

        Every engine event is also forwarded to ``event_buffer`` (when
        provided) for the SSE endpoint to pick up in real time.
        """
        start = time.time()
        text_chunks: list[str] = []
        usage: dict[str, Any] = {}
        error: str | None = None

        try:
            for event in engine.spawn(prompt, env=env):
                ev_type = getattr(event, "type", None)
                ev_text = getattr(event, "text", "") or ""
                ev_usage = getattr(event, "usage", None) or {}
                ev_error = getattr(event, "error", None)

                # Tap: forward every engine event to the SSE buffer
                # before collection logic mutates state. Best-effort
                # — a buffer-side exception must not break collection.
                if event_buffer is not None:
                    try:
                        event_buffer.append(stream_event_to_dict(event))
                    except Exception:
                        pass

                if ev_type == "text_delta" and ev_text:
                    text_chunks.append(ev_text)
                elif ev_type == "turn_completed":
                    if ev_usage:
                        usage = ev_usage
                    if ev_text and not text_chunks:
                        # Engines that only deliver final text on
                        # completion (e.g. Codex) land here.
                        text_chunks.append(ev_text)
                elif ev_type == "error":
                    error = ev_error or "unknown engine error"
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"

        return {
            "final_text":  "".join(text_chunks).strip(),
            "usage":       usage,
            "duration_ms": int((time.time() - start) * 1000),
            "error":       error,
        }
