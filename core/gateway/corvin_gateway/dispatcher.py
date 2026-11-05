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
import inspect
import logging
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
_BRIDGES_SHARED = _REPO / "corvin_operator" / "bridges" / "shared"
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

# Phase 2: Metric recorders for engine/workflow/context metrics.
# Best-effort: failures never crash the dispatcher.
try:
    from core.monitoring import (
        EngineMetricsCollector,
        WorkflowMetricsCollector,
    )
except ImportError:
    EngineMetricsCollector = None  # type: ignore[assignment]
    WorkflowMetricsCollector = None  # type: ignore[assignment]

# Engine/zone-policy audit events live in the unified hash chain.
# forge.security_events is already on sys.path via runs.py / auth.py;
# registering keeps EVENT_SEVERITY authoritative.
from forge import security_events as _security_events  # noqa: E402

# L44 acceptable-use (house-rules) pre-spawn gate — ADR-0143, MANDATORY,
# fail-CLOSED. ``spawn_gates`` lives in corvin_operator/bridges/shared, already on
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

# ADR-0171 — universal engine span. corvin_operator/bridges/shared is on sys.path
# (spawn_gates above). Guarded: a missing module degrades to gateway.* events only.
try:
    import engine_span as _espan  # type: ignore  # noqa: E402
except Exception:  # noqa: BLE001
    _espan = None  # type: ignore[assignment]

# Every gateway.* / routing event this module writes is registered in
# forge.security_events (EVENT_SEVERITY + _EVENT_ALLOWLIST). An import-time
# ``EVENT_SEVERITY.setdefault`` used to stand in for that and left the events
# without an allowlist, so their run_id/engine/reason were floored away
# (review 2026-11-05, DLG-16).

_log = logging.getLogger(__name__)


def _audit_gateway(
    event_type: str,
    *,
    tenant_id: str,
    details: dict[str, Any] | None = None,
    severity: str | None = None,
) -> bool:
    """Write one hash-chained record onto ``tenant_id``'s ONE audit chain.

    The path comes from ``forge.paths.tenant_audit_chain`` (ADR-0654) — never
    composed by hand. Best-effort for the caller (a failed write never wedges a
    dispatch), but never silent: a refused write is logged with its class.
    Returns whether the record committed.
    """
    try:
        from forge import paths as _fp  # noqa: PLC0415 — same lazy guard as runs.py

        _security_events.write_event(
            _fp.tenant_audit_chain(tenant_id), event_type,
            severity=severity, details=dict(details or {}),
            hash_chain=True,
        )
        return True
    except Exception as exc:  # noqa: BLE001 — audit must not wedge the run
        _log.warning("gateway audit write %s for tenant %s failed: %s",
                     event_type, tenant_id, type(exc).__name__)
        return False


def _worker_spawn_env(tenant_id: str, *, persona: str, run_id: str,
                      base: "dict[str, str] | None" = None) -> dict[str, str]:
    """The env OVERLAY for a gateway run's engine spawn (review 2026-11-05, DLG-3).

    A gateway run executes a tenant-supplied prompt on a tool-capable engine,
    so it gets exactly the environment an ACS worker gets — built by the SAME
    helpers (``acs_runtime``), never a copy of them:

    1. ``_strip_worker_secrets`` — every operator credential (explicit list +
       name-pattern catch-all; public keys and location pointers kept);
    2. ``_restore_platform_credentials`` — the Bedrock/Vertex/Foundry chain the
       engine signs with, only when the host is in platform mode (ADR-0759);
    3. ``_apply_provider_redirect`` — the tenant's provider egress, so the L35
       gate and the actual egress agree (ADR-0181).

    ``ClaudeCodeEngine.spawn`` merges its ``env`` OVER ``os.environ``, so a
    stripped name is passed as ``""`` — that is how it is removed. The three
    ``CORVIN_*`` keys bind the run's tenant, persona and channel.

    Raises ``RuntimeError`` when the shared helpers cannot be imported: the
    caller fails the run rather than spawn with the operator's secrets.
    """
    try:
        from acs_runtime import (  # type: ignore[import-not-found]  # noqa: PLC0415
            _apply_provider_redirect,
            _restore_platform_credentials,
            _strip_worker_secrets,
        )
    except ImportError as exc:
        raise RuntimeError(
            f"worker env strip unavailable ({type(exc).__name__}) — refusing to spawn "
            "a gateway run with the operator's environment"
        ) from exc
    src = dict(os.environ if base is None else base)
    kept = dict(src)
    _strip_worker_secrets(kept)
    _restore_platform_credentials(kept, dict(src))
    overlay: dict[str, str] = {name: "" for name in src if name not in kept}
    redirect: dict[str, str] = {}
    _apply_provider_redirect(redirect, tenant_id)
    overlay.update(redirect)
    overlay.update({
        "CORVIN_TENANT_ID":      tenant_id,
        "CORVIN_CALLER_PERSONA": persona,
        "CORVIN_CHANNEL_ID":     f"gateway:{run_id}",
    })
    return overlay


# ── Engine protocol (minimal — for stub-in-test) ─────────────────────


def _newest_haiku(tenant_id: str) -> str:
    """Cheapest-model fallback: the RUN tenant's Console choice for the
    ``SIMPLE`` tier, else the newest available Haiku (ADR-2090 lineage).

    The tenant is required: ``console_selection`` falls back to the PROCESS
    tenant (``CORVIN_TENANT_ID``) when given none, which in this multi-tenant
    gateway handed tenant B the boot tenant's model (review 2026-11-05, DLG-6).
    """
    try:
        from console_selection import saved_tier_model  # type: ignore  # noqa: PLC0415
        from model_lineage import latest  # type: ignore  # noqa: PLC0415

        return (saved_tier_model("SIMPLE", tenant_id=tenant_id)
                or latest("haiku") or "claude-haiku-4-5")
    except Exception:  # noqa: BLE001
        return "claude-haiku-4-5"


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
        # OPTIONAL on purpose. The real ClaudeCodeEngine takes it; a minimal
        # engine (and every test stub written before 2026-09-15) does not, and
        # an engine that cannot be steered to a model is a supported engine —
        # not a broken one. _spawn_collect probes for the parameter rather than
        # passing it unconditionally, so adding it here widens the protocol
        # without invalidating any existing implementation.
        model: str | None = None,
        # OPTIONAL, probed the same way as ``model`` (see _engine_accepts_kw).
        extra_args: list[str] | None = None,
    ) -> Iterator[Any]:
        ...


# ── Defaults ─────────────────────────────────────────────────────────


DEFAULT_BUDGET_S = 60
"""Wall-clock cap when ``budget_override.max_wall_clock_s`` is absent."""


def _engine_accepts_kw(engine: Any, keyword: str) -> bool:
    """Does this engine's ``spawn`` take ``keyword``?

    Passing an optional keyword blindly turned every engine WITHOUT the
    parameter into a ``TypeError`` inside the worker thread, which surfaced as
    a generic ``status="failed"`` run — a model preference silently converting
    working dispatches into failures is a far worse outcome than not honouring
    the preference. Probed, not assumed, and the result cached per
    (class, keyword) because ``inspect.signature`` is not free and the engine
    is constructed per run.
    """
    key = (type(engine), keyword)
    cached = _SPAWN_KW_SUPPORT.get(key)
    if cached is not None:
        return cached
    try:
        params = inspect.signature(engine.spawn).parameters
        supported = keyword in params or any(
            prm.kind is inspect.Parameter.VAR_KEYWORD for prm in params.values()
        )
    except (TypeError, ValueError):  # builtin / C-level callable
        supported = False
    _SPAWN_KW_SUPPORT[key] = supported
    return supported


def _engine_accepts_model(engine: Any) -> bool:
    """Does this engine's ``spawn`` take a ``model`` keyword? See
    :func:`_engine_accepts_kw`."""
    return _engine_accepts_kw(engine, "model")


def _model_is_available(model_id: str, engine_id: str) -> bool:
    """Is ``model_id`` a model ``engine_id`` can run, per the engine registry?

    ``False`` only on a DEFINITE miss: the registry describes ``engine_id`` and
    ``engine_models.model_is_registered`` (the check ``resolve_step_model``
    uses) does not find the id. An engine the registry does not describe
    (a stub engine, a third-party engine) cannot be judged here and passes —
    the CLI then validates the id itself.

    Until 2026-11-05 this imported a ``model_catalog`` name ``engine_models``
    never exports, so it returned True for every id and the "unavailable model
    → fall back" branches below were dead (review DLG-19).
    """
    if not model_id:
        return True
    try:
        from engine_models import (  # type: ignore[import]  # noqa: PLC0415
            load_registry, model_is_registered,
        )
    except ImportError:  # bridges subtree absent (unit sandbox): cannot judge
        return True
    if load_registry().get(engine_id) is None:
        return True
    return model_is_registered(model_id, engine_id)


#: (type, keyword) -> does its spawn() accept that keyword. Bounded by the
#: number of engine classes in the process, which is a handful.
_SPAWN_KW_SUPPORT: dict[tuple[type, str], bool] = {}


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
        path. Used by Phase 3.2 / 3.3 policy denials and the engine spans.
        The chain path is ``tenant_audit_chain`` (see :func:`_audit_gateway`).
        """
        _audit_gateway(event_type, tenant_id=tenant_id, details=details,
                       severity=severity)

    @staticmethod
    def _usage_split(usage: "dict[str, Any] | None") -> dict[str, int]:
        """The four billed token counts from an engine's raw usage object.

        Delegates to ``engine_span.usage_split``, the ONE normaliser every span
        emitter shares (ADR-0759). It used to be a private copy here, and a
        private copy is how two paths end up pricing the same run differently
        — the A2A worker path had no split at all and every inbound run read as
        $0.00. Kept as a method so the existing call sites and tests are
        unchanged.
        """
        if _espan is None:  # forge/shared not importable — span emission is off
            return {"input_tokens": 0, "output_tokens": 0,
                    "cache_read_tokens": 0, "cache_write_tokens": 0}
        return _espan.usage_split(usage)

    def _emit_engine_span(self, kind: str, *, tenant_id: str, run_id: str,
                          engine_id: str, model_id: str = "", status: str = "ok",
                          duration_ms: int = 0,
                          usage: "dict[str, Any] | None" = None) -> None:
        """ADR-0171 — engine.span.start/end (role=worker) for the gateway run on
        the tenant's unified chain. Best-effort; never wedges dispatch.

        ``model_id`` is NOT optional in practice even though the parameter is.
        It was omitted entirely until 2026-09-15, and the consequence was not a
        missing field: ``console/model_usage.py`` keys every row on the model id
        and DROPS an observation that has none, so 100% of delegated worker
        spend was invisible in the Engine Configuration panel while the OS turns
        next to it were counted. The panel read as "this install only ever runs
        OS turns", which is exactly the opposite of what a delegating install
        does. Pass the real id at every call site.
        """
        if _espan is None:
            return
        span_id = f"spn-{run_id}-w0"
        if kind == "start":
            self._audit_policy(
                _espan.ENGINE_SPAN_START, tenant_id=tenant_id, severity="INFO",
                details=_espan.start_details(span_id=span_id, role="worker",
                                             engine_id=engine_id,
                                             model_id=model_id, run_id=run_id))
        else:
            end = _espan.end_details(span_id=span_id, role="worker",
                                     engine_id=engine_id,
                                     model_id=model_id, run_id=run_id,
                                     status=status, duration_ms=int(duration_ms),
                                     **self._usage_split(usage))
            self._audit_policy(
                _espan.ENGINE_SPAN_END, tenant_id=tenant_id, severity="INFO",
                details=end)
            # ADR-2286 — live model-usage signal from the same span details.
            _espan.send_live_model_usage(end)

    @staticmethod
    def _pinned_worker_model(tenant_id: str, engine_id: str) -> str:
        """The operator's ``spec.engine_models.<engine>.worker_model``, normalised, else ``""``.

        Read through ``console_selection.pinned_model`` — the ONE reader of the
        Console Models-page choice — with the RUN's tenant. A pin is the
        operator's instruction: like ``model_selector.normalise_pin`` it is
        never dropped because this process cannot verify the id (an alias, a
        snapshot newer than the registry); dropping it would silently
        substitute another model, and a retired id is swapped for its
        successor at ``ClaudeCodeEngine._build_args`` anyway.
        """
        try:
            from console_selection import pinned_model  # type: ignore[import]  # noqa: PLC0415
        except ImportError:  # bridges subtree absent (unit sandbox)
            return ""
        return pinned_model("worker_model", tenant_id=tenant_id, engine_id=engine_id) or ""

    @staticmethod
    def _resolve_worker_model(tenant_id: str, engine_id: str, prompt: str = "",
                              *, run_id: str = "") -> str:
        """The worker model for ``engine_id``: the operator's pin, else the prompt router, else the default.

        Order (decision 2026-10-29 "pin vor router"; before it the router came first and a pin only applied
        when the router was unavailable or there was no prompt, against the operator rule of 2026-10-13 that
        the model chosen on the Console Models page is the one that runs):
        0. The operator's pin (spec.engine_models.<engine>.worker_model), normalised, never dropped
        1. Intelligent routing (if prompt provided AND router available): Haiku/Sonnet/Opus by task
           complexity; the decision is audited on the tenant's chain (``intelligent_routing_decision``,
           closed fields). A pick the engine registry definitely does not hold falls back to the
           RUN tenant's cheap tier (``_newest_haiku(tenant_id)``)
        2. Engine registry default (the run tenant's cheap tier when the registry does not hold it)
        3. Empty string (let engine use its own default)

        Args:
            tenant_id: Tenant scope (the RUN's tenant — never the process tenant)
            engine_id: Engine identifier (``engine.name``, e.g. "claude_code")
            prompt: Optional task/prompt for intelligent routing
            run_id: The gateway run, recorded on the routing audit event

        Returns:
            Model ID string (e.g., "claude-haiku-4-5") or ""
        """
        # An operator pin is an instruction, not a hint: the router never overrides it.
        pinned = RunDispatcher._pinned_worker_model(tenant_id, engine_id)
        if pinned:
            return pinned

        if prompt:
            try:
                from core.skills.os_skills.intelligent_router_integration import (
                    IntelligentRouterBridge,
                )  # noqa: PLC0415

                decision = IntelligentRouterBridge.route_with_intelligent_selection(
                    prompt,
                    engine_mode=engine_id,  # Pass engine mode for cost/latency tuning
                    latency_critical=(engine_id == "native"),
                    tenant_id=tenant_id,
                )
            except Exception as _ir_exc:  # noqa: BLE001 — router unavailable/errored: engine default below
                _log.debug("IntelligentRouter fallback: %s", type(_ir_exc).__name__)
            else:
                selected_model = str(decision.model or "")
                if selected_model and _model_is_available(selected_model, engine_id):
                    chosen = selected_model
                elif selected_model:
                    _log.warning("routed model %s is not registered for %s; "
                                 "falling back to the tenant's cheap tier",
                                 selected_model, engine_id)
                    chosen = _newest_haiku(tenant_id)
                else:
                    chosen = ""
                if chosen:
                    # Closed fields only (review 2026-11-05, DLG-5): the router's free-text
                    # ``reasoning`` paraphrases the tenant's prompt and is never written.
                    # No tenant_id field: the record lands on the run tenant's own chain,
                    # and the chokepoint refuses a tenant-tagged record for any tenant but
                    # the process tenant (AuditTenantMismatch) — fatal in this
                    # multi-tenant process.
                    _audit_gateway(
                        "intelligent_routing_decision", tenant_id=tenant_id, severity="INFO",
                        details={
                            "model": chosen,
                            "tier": str(getattr(decision, "tier", "") or ""),
                            "confidence": float(getattr(decision, "confidence", 0.0) or 0.0),
                            "engine": engine_id,
                            "run_id": run_id,
                        },
                    )
                    return chosen

        try:
            from engine_models import load_registry  # type: ignore[import]  # noqa: PLC0415
        except ImportError:  # bridges subtree absent (unit sandbox)
            return ""
        spec = load_registry().get(engine_id)
        default = (spec.default_worker_model() or "") if spec is not None else ""
        if default and _model_is_available(default, engine_id):
            return default
        return _newest_haiku(tenant_id) if default else ""

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

        # Per-run env OVERLAY (the engine merges it over os.environ): every
        # operator secret blanked, the platform credential chain and the
        # tenant's provider redirect restored, plus tenant + persona + caller
        # binding (CORVIN_TENANT_ID makes downstream forge/skill-forge writes
        # land in the right tree). Same rule as an ACS worker — see
        # _worker_spawn_env (review 2026-11-05, DLG-3). No strip, no spawn.
        try:
            spawn_env = _worker_spawn_env(tenant_id, persona=persona, run_id=run_id)
        except RuntimeError as exc:
            self._set_terminal(tenant_id, run_id, "failed", error=str(exc)[:300])
            return

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
        # ── Why every gate below is awaited via asyncio.to_thread ──────────────
        # These gates are SYNCHRONOUS and one of them is expensive: L44 adjudicates
        # the prompt with an LLM (house_rules_adjudicator → the cloud classifier).
        # Called directly inside this `async def`, that blocks the event loop, and
        # the gateway shares its uvicorn process with the console — so a single
        # in-flight run made the WHOLE process unresponsive. Measured 2026-07-26:
        # /healthz went from a few ms idle to 13 350 ms during one dispatch, and a
        # smoke test's 2 s status poll timed out against a server that was up.
        # to_thread changes nothing about semantics: the call is still awaited, still
        # ordered before the compute meter, still fail-closed on any exception — it
        # only stops one run from freezing every other request. The engine spawn
        # below was already off-loop this way; the gates had been missed.
        if _check_l34 is not None:
            try:
                _l34_refusal = await asyncio.to_thread(
                    _check_l34,
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
                _l35_refusal = await asyncio.to_thread(
                    _check_l35,
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
            # The expensive one: an LLM classification per dispatch. See the
            # to_thread note above the L34 gate.
            _l44_refusal = await asyncio.to_thread(
                _check_l44,
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
            _gw_op = Path(__file__).resolve().parents[3] / "corvin_operator"
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
                # File-locked counter write — short, but off-loop for the same
                # reason as the gates above.
                await asyncio.to_thread(
                    _gw_cq, _gw_paths.corvin_home(), channel="gateway",
                    chat_key=f"gateway:{tenant_id}:{run_id}",
                )
            except _GwCQErr as _gw_exc:  # type: ignore[misc]
                self._set_terminal(
                    tenant_id, run_id, "failed",
                    error=f"compute_units_per_day exceeded: {_gw_exc}"[:300],
                )
                return
            except Exception:  # noqa: BLE001 — operational error already swallowed
                pass

        start = time.time()
        # STEP 0: Strip /delegate prefix if present (must happen BEFORE routing)
        # so routing decision is deterministic regardless of entry point
        from delegation_policy import strip_delegate_prefix
        clean_prompt, _was_delegated = strip_delegate_prefix(prompt)
        # STEP 1 (ADR-0867): Use IntelligentRouter to select model based on task complexity
        # Use the cleaned prompt (without /delegate prefix) for routing decisions
        worker_model = self._resolve_worker_model(tenant_id, _gw_engine_id, prompt=clean_prompt,
                                                  run_id=run_id)
        # ADR-0171 — engine.span.start (role=worker): every gateway engine run is
        # auditable as a span regardless of outcome (paired at all 4 exits below).
        # The START record carries the REQUESTED model; every END record carries
        # only what the engine REPORTED ("" when it reported none — unpriced,
        # honest; ADR-0759, review 2026-11-05 DLG-20).
        self._emit_engine_span("start", tenant_id=tenant_id, run_id=run_id,
                               engine_id=engine_name, model_id=worker_model)
        try:
            outcome = await asyncio.wait_for(
                asyncio.to_thread(
                    self._spawn_collect,
                    engine=engine,
                    # The STRIPPED prompt: delegation_policy.strip_delegate_prefix —
                    # "the raw '/delegate' text must never reach an LLM". L34/L44
                    # above judged the raw prompt, a superset (review DLG-18).
                    prompt=clean_prompt,
                    env=spawn_env,
                    event_buffer=event_buffer,
                    model=worker_model or None,
                    extra_args=(["--strict-mcp-config"]
                                if _gw_engine_id == "claude_code" else None),
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
                                   engine_id=engine_name, model_id="",
                                   status="error",
                                   duration_ms=int(duration * 1000))
            # Record timeout error metric
            if EngineMetricsCollector is not None:
                EngineMetricsCollector.record_error(
                    tenant_id, engine_name or "unknown",
                    error_type="budget_exceeded",
                    latency_ms=int(duration * 1000),
                )
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
                                   engine_id=engine_name, model_id="",
                                   status="error",
                                   duration_ms=int(duration * 1000))
            # Record cancellation metric
            if EngineMetricsCollector is not None:
                EngineMetricsCollector.record_error(
                    tenant_id, engine_name or "unknown",
                    error_type="cancelled",
                    latency_ms=int(duration * 1000),
                )
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
            duration = time.time() - start
            self._emit_engine_span("end", tenant_id=tenant_id, run_id=run_id,
                                   engine_id=engine_name, model_id="",
                                   status="error",
                                   duration_ms=int(duration * 1000))
            # Record error metric
            if EngineMetricsCollector is not None:
                EngineMetricsCollector.record_error(
                    tenant_id, engine_name or "unknown",
                    error_type=type(exc).__name__,
                    latency_ms=int(duration * 1000),
                )
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
            duration_ms = int(outcome.get("duration_ms", 0))
            self._emit_engine_span("end", tenant_id=tenant_id, run_id=run_id,
                                   engine_id=engine_name,
                                   model_id=outcome.get("model") or "",
                                   status="error",
                                   duration_ms=duration_ms,
                                   usage=outcome.get("usage"))
            # Record engine-level error metric
            if EngineMetricsCollector is not None:
                EngineMetricsCollector.record_error(
                    tenant_id, engine_name or "unknown",
                    error_type="engine_error",
                    latency_ms=duration_ms,
                )
            self._set_terminal(
                tenant_id, run_id, "failed",
                result={"usage": outcome.get("usage") or {}},
                error=str(outcome["error"]),
            )
            return

        duration_ms = int(outcome.get("duration_ms", 0))
        tokens_used = outcome.get("usage", {}).get("output_tokens")
        self._emit_engine_span("end", tenant_id=tenant_id, run_id=run_id,
                               engine_id=engine_name,
                               model_id=outcome.get("model") or "",
                               status="ok",
                               usage=outcome.get("usage"),
                               duration_ms=duration_ms)
        # Record successful execution metric
        if EngineMetricsCollector is not None:
            EngineMetricsCollector.record_success(
                tenant_id, engine_name or "unknown",
                latency_ms=duration_ms,
                tokens_used=tokens_used,
            )
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
        model: str | None = None,
        extra_args: list[str] | None = None,
    ) -> dict[str, Any]:
        """Sync wrapper: drain the engine stream, project to dict.

        ``extra_args`` (``--strict-mcp-config`` for claude_code: only the MCP
        servers the spawn names — none — load, never the operator's user or
        project servers) is passed only to an engine whose ``spawn`` takes it,
        probed like ``model``.

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
        # The model the ENGINE reports it is running, from its own
        # session_started/init frame. Preferred over the id we asked for when
        # closing the span: an alias, a Bedrock inference profile or a CLI
        # default resolves to something else, and auditing the request instead
        # of the fact would make the usage panel confidently wrong.
        reported_model: str = ""

        try:
            # ADR-0648 amendment 2 (round 4, 2026-09-07): `prompt` is the raw
            # `spec.input` of a tenant-submitted Run (see `_drive`), i.e.
            # attacker-authored text behind a tenant JWT — and a tenant is not
            # the operator (ADR-0007). It is handed over unmodified because
            # `ClaudeCodeEngine.spawn()` applies `guard_prompt_head()` itself:
            # byte-0 sentinel (`/cmd`, `!cmd` — the latter is LOCAL SHELL
            # EXECUTION on the positional-argv transport this dispatcher uses)
            # plus a U+2060 joiner before every token-start `@` (`@<path>` is
            # a client-side file read that no `--disallowedTools` restrains).
            # Do NOT re-introduce a bespoke argv build here: it would leave the
            # engine's guard behind. Proof:
            # `core/gateway/tests/test_dispatcher_prompt_guard.py`.
            spawn_kwargs: dict[str, Any] = {"env": env}
            if model and _engine_accepts_model(engine):
                spawn_kwargs["model"] = model
            if extra_args and _engine_accepts_kw(engine, "extra_args"):
                spawn_kwargs["extra_args"] = list(extra_args)
            for event in engine.spawn(prompt, **spawn_kwargs):
                ev_type = getattr(event, "type", None)
                if ev_type == "session_started" and not reported_model:
                    raw = getattr(event, "raw", None)
                    if isinstance(raw, dict):
                        reported_model = str(raw.get("model") or "")
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
            # "" when the engine never announced one (a crash before its init
            # frame). The caller closes the span on it as-is: an unreported
            # model stays "" (unpriced, honest — ADR-0759), never the id that
            # was REQUESTED, which would price the run at a guess.
            "model":       reported_model,
        }
