"""ADR-0190 M4/M5/M6 — consolidated orchestration MCP server over stdio.

Transport: line-delimited JSON-RPC 2.0 on stdin/stdout (same shape as the
forge / skill-forge / corvin_delegate MCP servers — this file mirrors
``core/delegate/corvin_delegate/mcp_server.py``'s protocol skeleton).

Six tools across three previously chat-unreachable subsystems:

  - ``workflow_run`` / ``workflow_resume`` / ``workflow_list_paused``
    (AWP DAG-Workflows, ``core/workflows/corvin_workflows``)
  - ``a2a_send`` / ``a2a_list_endpoints``
    (instance-to-instance, ``operator/bridges/shared/remote_trigger_sender``)
  - ``acs_delegate``
    (Autonomous Compute Shell, ``operator/bridges/shared/acs_engine_adapter``)

Each group's external dependency is imported defensively (try/except at
module load) — a missing plugin (e.g. base install without the workflows
package) silently omits that group's tools from ``tools/list`` rather than
crashing the whole server, mirroring ``forge/mcp_server.py``'s established
pattern for the optional compute plugin.

Run via::

    python -m corvin_orchestration.mcp_server
"""
from __future__ import annotations

import json
import os
import re
import secrets
import sys
import threading
import time
import traceback
from pathlib import Path
from typing import Any, Callable

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "corvin-orchestration"
SERVER_VERSION = "0.1.0"

# JSON-RPC error codes (subset — same values as forge/corvin_delegate)
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603

_WID_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")

# ---------------------------------------------------------------------------
# forge (tenant resolution + audit chain) — same package the other MCP
# servers already depend on via PYTHONPATH; optional so a caller with a
# broken PYTHONPATH degrades to "_default" tenant + no audit instead of
# crashing the whole server on import.
# ---------------------------------------------------------------------------
try:
    from forge.paths import corvin_home as _corvin_home  # type: ignore[import]
    from forge.security_events import write_event as _write_event  # type: ignore[import]
    from forge.tenants import current_tenant as _current_tenant  # type: ignore[import]
    from forge.tenants import tenant_home as _tenant_home  # type: ignore[import]
    _FORGE_AVAILABLE = True
except ImportError:
    _FORGE_AVAILABLE = False

    def _current_tenant(tenant_id: str | None = None) -> str:  # type: ignore[misc]
        return tenant_id or "_default"

    def _tenant_home(tenant_id: str | None = None, **_kw: Any) -> Path:  # type: ignore[misc]
        return Path.home() / ".corvin" / "tenants" / (tenant_id or "_default")


def _audit_sink_for(tenant_id: str, prefix: str) -> Callable[[dict], None] | None:
    """Best-effort audit sink writing to the forge hash chain. Returns None
    (not a no-op callable) when forge is unavailable so callers that treat
    ``audit_sink=None`` as "skip" don't pay a wasted call."""
    if not _FORGE_AVAILABLE:
        return None
    try:
        audit_path = _corvin_home() / "tenants" / tenant_id / "audit.jsonl"
    except Exception:  # noqa: BLE001
        return None

    def _sink(event: dict) -> None:
        try:
            ev = event.get("event", "event")
            _write_event(audit_path, f"{prefix}.{ev}", severity="INFO", details=event)
        except Exception:  # noqa: BLE001
            pass  # never let audit shaping abort a live run

    return _sink


def _clamp(value: Any, *, lo: int, hi: int, default: int) -> int:
    try:
        n = int(value) if value is not None else default
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, n))


def _replier_from_channel_id(channel_id: str) -> str:
    """Derive the WF-A3 `replier` identity from a trusted `CORVIN_CHANNEL_ID`
    value ("<bridge>:<chat_key>", set by the bridge adapter at spawn time —
    see operator/bridges/shared/adapter.py::_build_spawn_env). Strips the
    bridge prefix because a workflow checkpoint's recorded `approver` is
    always a bare chat_id (corvin_workflows/node_types.py::_execute_ask_human
    stores `pause.chat_id` verbatim, never bridge-prefixed) — the same split
    already used by operator/bridges/shared/phase3_cli.py's debug-channel
    identity check.

    Always returns a string, never None: an empty/missing channel_id yields
    ``""``, which correctly FAILS to match any real approver in
    resume_workflow()'s WF-A3 check rather than being treated as the
    `replier=None` privileged-owner bypass. Passing None here would silently
    re-open the exact vulnerability this function exists to close.
    """
    if not channel_id:
        return ""
    _, _, chat_key = channel_id.partition(":")
    return chat_key or channel_id


# ---------------------------------------------------------------------------
# License gate — workflows_concurrent (ADR-0190 "a new MCP tool must call the
# exact gate already enforced on the equivalent REST path"). The console's
# POST /workflows/{id}/runs enforces license.validator.get_limit(
# "workflows_concurrent") against its run registry; without this gate a chat
# turn could start unlimited parallel runs the console would refuse
# (adversarial-review finding, 2026-07-12). Same import + fail-closed
# FREE_TIER fallback chain as forge/mcp_server.py's datasource gate — the
# resolver's PYTHONPATH does not carry operator/, so put it on sys.path
# first (the datasource_connect lesson: an unguarded import silently fell
# back to free-tier for every licensed tenant).
# ---------------------------------------------------------------------------
_WF_FREE_TIER_FALLBACK: dict = {"workflows_concurrent": 1}
try:
    _operator_root = Path(__file__).resolve().parents[3] / "operator"
    if not _operator_root.is_dir() and _FORGE_AVAILABLE:
        # Wheel install: this file lives at site-packages/core/orchestration/
        # corvin_orchestration/, where parents[3]/operator does not exist —
        # but the forge package (already imported via the resolver's
        # PYTHONPATH) sits at <operator-root>/forge/forge/, in BOTH layouts.
        try:
            import forge as _forge_pkg  # type: ignore[import]
            _operator_root = Path(_forge_pkg.__file__).resolve().parents[2]
        except Exception:  # noqa: BLE001
            pass
    if _operator_root.is_dir() and str(_operator_root) not in sys.path:
        sys.path.insert(0, str(_operator_root))
    from license.validator import get_limit as _lic_get_limit  # type: ignore[import]
except ImportError:
    try:
        from license.limits import FREE_TIER as _WF_FREE_TIER  # type: ignore[import]
        _lic_get_limit = _WF_FREE_TIER.get  # type: ignore[assignment]
    except ImportError:
        _lic_get_limit = _WF_FREE_TIER_FALLBACK.get  # type: ignore[assignment]

# In-process running-workflow counter (chat-path runs). The console's runs
# are counted separately via their on-disk *.meta.json registry; this
# counter covers the DAGRunner invocations this server itself started,
# which never appear in that registry.
_ACTIVE_WORKFLOW_RUNS = 0
_ACTIVE_WORKFLOW_LOCK = threading.Lock()

# Node types whose executor NEVER calls engine.spawn() (see
# corvin_workflows/node_types.py). A workflow built only from these runs with
# NO LLM engine — so it must not require the `claude` CLI on PATH. This is the
# Hermes-only / no-Claude fresh-install path (and CI, which ships no CLI): a
# pure code/compute/merge workflow has to execute regardless. Any node type
# NOT in this set is treated as engine-requiring and fails fast up front with a
# clean engine_unavailable envelope rather than an opaque mid-run node failure.
_ENGINE_FREE_NODE_TYPES = frozenset(
    {"code", "compute", "merge", "static", "ask_human", "deliver"}
)


class _NullEngine:
    """Placeholder engine for workflows with no engine-requiring node. Its
    spawn() must never be reached; if it is, that is a routing bug and we raise
    loudly rather than silently returning an empty result."""

    name = "null"

    def spawn(self, call):  # noqa: ANN001, ANN201
        raise RuntimeError(
            "internal: engine-free workflow reached engine.spawn — node-type "
            f"classification is wrong for agent={getattr(call, 'agent', '?')!r}"
        )


_NULL_ENGINE = _NullEngine()


class _LazyClaudeEngine:
    """Constructs the real ClaudeCliEngine on the FIRST spawn(). A workflow
    resume whose remaining nodes never reach an agent — or an unknown-run
    lookup that fails before any spawn — then needs no `claude` CLI on PATH.
    Deferring construction here is what keeps `workflow_resume` on a Hermes-only
    / no-Claude install (and in CI) from masking a clean 'no paused run found'
    behind an engine_unavailable envelope."""

    name = "claude"

    def __init__(self) -> None:
        self._engine: Any = None

    def spawn(self, call):  # noqa: ANN001, ANN201
        if self._engine is None:
            self._engine = _ClaudeCliEngine()
        return self._engine.spawn(call)


def _count_console_running(tenant_id: str) -> int:
    """Best-effort count of console-started runs currently 'running'
    (mirrors routes/workflows.py::_count_running_workflows). Fail-closed:
    an unreadable runs TREE raises (caller refuses), a single unreadable
    meta file is a bounded under-count."""
    wf_root = _tenant_home(tenant_id) / "workflows"
    if not wf_root.exists():
        return 0
    count = 0
    for wf_dir in wf_root.iterdir():
        runs_dir = wf_dir / "runs"
        if not runs_dir.is_dir():
            continue
        for meta_file in runs_dir.glob("*.meta.json"):
            try:
                meta = json.loads(meta_file.read_text(encoding="utf-8"))
                if meta.get("status") == "running":
                    count += 1
            except Exception:  # noqa: BLE001
                pass
    return count


def _workflow_concurrency_refusal(tenant_id: str) -> str | None:
    """None if a new run may start; a user-facing refusal string otherwise.
    Fail-closed: if the limit or the current count cannot be determined,
    refuse rather than granting unlimited concurrency."""
    try:
        limit = _lic_get_limit("workflows_concurrent")
    except Exception:  # noqa: BLE001
        limit = _WF_FREE_TIER_FALLBACK["workflows_concurrent"]
    if limit is None:
        return None  # unlimited tier
    try:
        with _ACTIVE_WORKFLOW_LOCK:
            active = _ACTIVE_WORKFLOW_RUNS
        running = active + _count_console_running(tenant_id)
    except Exception:  # noqa: BLE001
        return ("cannot determine current workflow concurrency — refusing "
                "to start a new run (fail-closed)")
    if running >= int(limit):
        return (f"license limit reached: {running} workflow run(s) already "
                f"active, workflows_concurrent={limit}. Wait for a run to "
                "finish or upgrade at https://corvin-labs.com/pricing")
    return None


def _run_with_budget(
    fn: Callable[[], Any],
    *,
    budget_s: int,
    run_id_hint: str | None = None,
    on_timeout: Callable[[], None] | None = None,
    on_finish: Callable[[Any, BaseException | None], None] | None = None,
) -> Any:
    """Run *fn* on a daemon thread, joined with a wall-clock timeout.

    ADR-0029/ADR-0190 watchdog pattern — DAGRunner.run() / resume_workflow()
    / run_acs_workflow() have no built-in wall-clock cutoff and can run
    indefinitely (subprocess spawns). Best-effort, detached: on timeout the
    thread is NOT killed (Python has no safe thread-kill primitive) — it
    keeps running in the background and this call returns a typed timeout
    envelope rather than blocking the MCP server forever.

    ADR-0192 (background-completion contract): a bare timeout envelope with no
    handle at all left a caller unable to ever learn the outcome of a run that
    outlived this call — worse, the calling `claude -p` subprocess (and this
    MCP server, its child) is reliably killed minutes later by the per-turn
    bridge adapter, so "still running in the background" was frequently a
    polite fiction. Two optional hooks close that gap without coupling this
    generic helper to completion_notify directly:

    - ``run_id_hint``: echoed into the timeout envelope's ``run_id`` field —
      a stable identifier the caller can use to correlate a later
      completion-notification or (for AWP workflows) a `workflow_list_paused`
      lookup, even though *fn* has not returned yet.
    - ``on_timeout``: called synchronously, ONCE, from the caller's thread,
      exactly when a timeout is detected (i.e. strictly before *fn* has
      finished) — the natural place to register a completion notification,
      since this is the one moment we know the caller is about to lose its
      only synchronous handle on the result.
    - ``on_finish``: called from the background thread once *fn* actually
      completes (successfully or not), always — including the common case
      where *fn* finished well within budget_s. Deliberately unconditional
      instead of gated on "did we time out": the callback itself is the
      no-op when nothing was registered (mark_done on an unregistered id is
      a safe no-op), which avoids a redundant duplicate notification on top
      of the normal synchronous reply for the (overwhelmingly common) case
      where a run finishes in time.

    Both hooks are best-effort: an exception raised inside either is caught
    and swallowed here so a notification failure can never crash the
    (possibly already-detached) worker thread or mask the real result/error.

    Register/mark_done ordering race (closed below): ``t.is_alive()`` only
    goes False once ``_target`` fully RETURNS from its ``finally`` clause —
    it can still read True while the background thread is midway through
    that ``finally`` (i.e. already inside ``on_finish``, e.g. blocked on the
    file I/O ``completion_notify.mark_done`` does). That means "``on_finish``
    fires before ``on_timeout`` has registered anything" is possible even
    though this function only takes the timeout branch when ``t.is_alive()``
    reads True. When that happens, ``on_finish``'s ``mark_done`` no-ops
    (nothing registered yet), ``on_timeout``'s ``register`` then creates the
    record fresh, and — with no further signal — it would stay ``pending``
    forever even though the run already finished. ``hook_lock`` below makes
    the two sides observe each other deterministically instead of racing:
    whichever of {the background thread's ``finally``, the timeout branch}
    acquires the lock first decides the outcome, and the loser (if it
    already ran) triggers a rescue re-fire of ``on_finish`` — safe because
    ``completion_notify.mark_done`` is idempotent (a second call just
    updates an already-ready record; it never resurrects a delivered one).
    """
    box: dict[str, Any] = {}
    hook_lock = threading.Lock()
    finished = {"done": False}

    def _fire_on_finish() -> None:
        if on_finish is None:
            return
        try:
            on_finish(box.get("value"), box.get("error"))
        except Exception:  # noqa: BLE001 — never let a notification
            # failure crash the (already detached) worker thread, nor the
            # caller thread when this runs as part of the rescue re-fire.
            pass

    def _target() -> None:
        try:
            box["value"] = fn()
        except BaseException as exc:  # noqa: BLE001
            box["error"] = exc
        finally:
            with hook_lock:
                finished["done"] = True
                _fire_on_finish()

    t = threading.Thread(target=_target, daemon=True)
    t.start()
    t.join(timeout=budget_s)
    if t.is_alive():
        hooks_registered = on_timeout is not None
        with hook_lock:
            already_finished = finished["done"]
            if on_timeout is not None:
                try:
                    on_timeout()
                except Exception:  # noqa: BLE001
                    pass
            if already_finished:
                # The background thread's `finally` already ran on_finish
                # BEFORE on_timeout's register() just above — the ordering
                # race described in the docstring. Fire it again now that a
                # record exists: mark_done is idempotent, so this either
                # rescues the orphaned record (the common case this closes)
                # or is a harmless repeat no-op (nothing was registered).
                _fire_on_finish()
        delivery_note = (
            f" It will be delivered to the originating chat when it "
            f"finishes (run_id={run_id_hint})."
            if hooks_registered else
            " No automatic delivery is configured for this caller."
        )
        return {
            "status": "timeout",
            "run_id": run_id_hint,
            "error": (
                f"exceeded budget_s={budget_s}s — the underlying run may "
                "still be executing in the background; it was not killed."
                + delivery_note
            ),
        }
    if "error" in box:
        raise box["error"]
    return box.get("value")


# ---------------------------------------------------------------------------
# Background-completion contract (ADR-0192) — lets a run that outlives
# _run_with_budget's timeout still notify the originating messenger, the same
# way the scheduler / `/task` / console task-worker-pool / L25 compute worker
# already do via operator/bridges/shared/completion_notify.py. This MCP
# server is spawned as a child of the per-turn `claude -p` subprocess
# (docs/personas-and-routing.md) and does not itself know the originating
# channel/chat_id/sender as tool-call arguments — instead it recovers them
# from the SAME env vars the adapter's spawn already injects into every
# child process (`_build_spawn_env`, operator/bridges/shared/adapter.py),
# mirroring core/compute/corvin_compute/worker.py's `notify={channel,
# chat_id, sender}` gate but sourced from env instead of an explicit submit
# param (this MCP server has no such param in its tool schemas).
# ---------------------------------------------------------------------------

# Channels the completion-notify outbox knows how to route to (mirrors
# core/compute/corvin_compute/worker.py::_MESSENGER_CHANNELS).
_MESSENGER_CHANNELS = frozenset(
    {"discord", "telegram", "whatsapp", "slack", "signal", "email", "teams"}
)


def _notify_origin_from_env() -> dict[str, str] | None:
    """Best-effort messenger origin for background completion notification.

    ``CORVIN_CHANNEL_ID`` is ``"<bridge>:<chat_key>"`` and ``CORVIN_ORIGIN_
    SENDER`` is the sender uid — both set by the adapter's `_build_spawn_env`
    for every `claude -p` turn that carried messenger context, and inherited
    by this MCP server as its child process's environment. Returns None (not
    a partial dict) when the origin can't be established — e.g. a console/
    REST-originated call, or a channel completion_notify doesn't route to —
    so callers degrade to today's synchronous-only behavior rather than
    registering a notification nobody can ever deliver. A non-empty sender is
    REQUIRED: completion_notify.purge_user (GDPR Art. 17) matches records on
    sender, so an empty sender would leave an un-erasable record.
    """
    raw_channel_id = os.environ.get("CORVIN_CHANNEL_ID") or ""
    channel, _sep, chat_id = raw_channel_id.partition(":")
    sender = (os.environ.get("CORVIN_ORIGIN_SENDER") or "").strip()
    if channel not in _MESSENGER_CHANNELS or not chat_id or not sender:
        return None
    return {"channel": channel, "chat_id": chat_id, "sender": sender}


def _load_completion_notify() -> Any:
    """Best-effort import of the bridge-side completion_notify backbone.

    Reuses the already-resolved ``_operator_root`` (handles both a source
    checkout and a wheel install layout — see the license-gate import
    above), rather than re-deriving the path a second way.
    """
    try:
        shared = _operator_root / "bridges" / "shared"
        if shared.is_dir() and str(shared) not in sys.path:
            sys.path.insert(0, str(shared))
        import completion_notify as _cn  # type: ignore[import]
        return _cn
    except Exception:  # noqa: BLE001
        return None


def _notify_result_text(label: str, result: Any) -> tuple[str, bool]:
    """Render a finished AWP ``RunResult`` as plain completion-notify text.

    The messenger delivery path renders plain text (see completion_notify.
    _envelope_for), not the JSON-RPC tool envelope this server otherwise
    returns — so this is a separate, human-readable rendering, not a reuse
    of ``_run_result_envelope``.
    """
    state = getattr(result, "state", None)
    if state == "paused":
        prompt = getattr(result, "paused_prompt", "") or ""
        text = (
            f"{label} paused at node {getattr(result, 'paused_at_node', '?')!r} "
            f"awaiting a reply (run_id={getattr(result, 'run_id', '?')}). {prompt}"
        ).strip()
        return text, True
    if state == "failed":
        return f"{label} failed: {getattr(result, 'error', '') or 'unknown error'}", False
    return f"{label} completed.", True


def _completion_notify_hooks(
    run_id: str, tenant_id: str, label: str,
) -> tuple[Callable[[], None] | None, Callable[[Any, BaseException | None], None] | None]:
    """Build the (on_timeout, on_finish) pair `_run_with_budget` wires up for
    an AWP workflow run/resume, per the background-completion contract above.

    Returns (None, None) when no messenger origin is available — the caller
    then passes no hooks at all and behavior is exactly as before this
    contract existed.

    Registration is deliberately deferred to ``on_timeout`` (i.e. the moment
    a run has ACTUALLY outlived its budget) rather than performed eagerly
    before the run starts: the overwhelming common case is a run finishing
    within budget_s, and eagerly registering would mean every such run also
    gets a redundant completion-notify message on top of the normal
    synchronous tools/call reply the caller already received. Deferring to
    the timeout moment means the notification exists ONLY for the case a
    caller has no other way to learn the outcome.
    """
    cn = _load_completion_notify()
    origin = _notify_origin_from_env()
    if cn is None or origin is None:
        return None, None

    def _on_timeout() -> None:
        try:
            cn.register(
                run_id,
                channel=origin["channel"], chat_id=origin["chat_id"],
                sender=origin["sender"], tenant_id=tenant_id, label=label,
            )
            # Stamp THIS process as the record's producer, exactly as a
            # correct producer does (mirrors bg_task_worker.py's cn.claim()
            # right after it picks up a spec). The background thread that
            # will eventually call on_finish/mark_done lives in this SAME
            # process, so this process's pid is the right producer to
            # record. Without this, the record's producer_pid stays None
            # forever, and completion_notify.deliver_ready's dead-producer
            # reap explicitly SKIPS pid=None records (they look identical to
            # a long-running compute worker that legitimately hasn't claimed
            # yet) — so if this MCP server process is killed (e.g. by the
            # per-turn bridge adapter minutes after the turn ends, per the
            # ADR-0192 docstring above) before the background thread finishes,
            # the record would be invisible to the 30-minute dead-producer
            # reap and stay "pending" for the full 7-day CN_PENDING_MAX_AGE
            # instead of being turned into a "worker stopped" notification.
            cn.claim(run_id)
        except Exception:  # noqa: BLE001 — best-effort; must not block the
            # timeout response this runs synchronously ahead of.
            pass

    def _on_finish(value: Any, error: BaseException | None) -> None:
        # mark_done is a documented no-op (returns False) when no record was
        # ever registered under this id — i.e. this fires on EVERY run
        # (including the common in-budget case) but only actually delivers
        # anything for a run _on_timeout already registered.
        try:
            if error is not None:
                cn.mark_done(
                    run_id,
                    text=f"{label} crashed: {type(error).__name__}: {error}",
                    ok=False,
                )
                return
            text, ok = _notify_result_text(label, value)
            cn.mark_done(run_id, text=text, ok=ok)
        except Exception:  # noqa: BLE001
            pass

    return _on_timeout, _on_finish


# ---------------------------------------------------------------------------
# Group 1 — AWP DAG-Workflows (ADR-0190 M5)
# ---------------------------------------------------------------------------
try:
    from corvin_workflows import (  # type: ignore[import]
        DAGRunner as _DAGRunner,
    )
    from corvin_workflows import (
        WorkflowInvalid as _WorkflowInvalid,
    )
    from corvin_workflows import checkpoint as _awp_checkpoint  # type: ignore[import]
    from corvin_workflows import (
        load_workflow as _load_workflow,
    )
    from corvin_workflows import (
        resume_workflow as _resume_workflow,
    )
    from corvin_workflows import (
        validate as _validate_workflow,
    )
    from corvin_workflows.engines_claude import (  # type: ignore[import]
        ClaudeCliEngine as _ClaudeCliEngine,
    )
    from corvin_workflows.engines_claude import (
        ClaudeEngineError as _ClaudeEngineError,
    )
    from corvin_workflows.runner import (
        UnauthorizedReplier as _UnauthorizedReplier,  # type: ignore[import]
    )
    _AWP_AVAILABLE = True
except ImportError:
    _AWP_AVAILABLE = False

_WORKFLOW_RUN_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "workflow_id": {
            "type": "string",
            "description": (
                "The workflow's id (matches [a-z][a-z0-9_-]{0,63}) — same id "
                "space as the console's Workflows editor. Resolves to "
                "<tenant>/workflows/<workflow_id>.awp.yaml."
            ),
        },
        "inputs": {
            "type": "object",
            "description": "Key-value inputs merged into the workflow's initial state.",
        },
        "tenant_id": {"type": ["string", "null"]},
        "budget_s": {
            "type": "integer",
            "minimum": 10, "maximum": 600,
            "description": "Wall-clock budget in seconds. Clamped [10,600], default 120.",
        },
    },
    "required": ["workflow_id"],
    "additionalProperties": False,
}

_WORKFLOW_RESUME_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "run_id": {"type": "string", "description": "The paused run's id (from workflow_list_paused)."},
        "reply": {"type": "string", "description": "The human reply to inject at the paused ask_human node."},
        "tenant_id": {"type": ["string", "null"]},
        "budget_s": {
            "type": "integer", "minimum": 10, "maximum": 600,
            "description": "Wall-clock budget in seconds. Clamped [10,600], default 120.",
        },
    },
    "required": ["run_id", "reply"],
    "additionalProperties": False,
}

_WORKFLOW_LIST_PAUSED_SCHEMA: dict = {
    "type": "object",
    "properties": {"tenant_id": {"type": ["string", "null"]}},
    "additionalProperties": False,
}


# ---------------------------------------------------------------------------
# Group 2 — A2A instance-to-instance send (ADR-0190 M4)
# ---------------------------------------------------------------------------
try:
    from remote_trigger_sender import (  # type: ignore[import]
        EndpointError as _EndpointError,
    )
    from remote_trigger_sender import (
        RemoteEndpointRegistry as _RemoteEndpointRegistry,
    )
    from remote_trigger_sender import (
        RemoteTriggerSender as _RemoteTriggerSender,
    )
    _A2A_AVAILABLE = True
except ImportError:
    _A2A_AVAILABLE = False

_A2A_SEND_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "endpoint_id": {
            "type": "string",
            "description": "Target endpoint id — see a2a_list_endpoints for the configured set.",
        },
        "instruction": {
            "type": "string",
            "description": "The task instruction sent to the remote instance.",
        },
        "ttl_s": {
            "type": ["integer", "null"],
            "description": "Override the endpoint's default_ttl_s. Omit to use the endpoint default.",
        },
        "timeout_s": {
            "type": "integer", "minimum": 5, "maximum": 120,
            "description": "HTTP round-trip timeout. Clamped [5,120], default 30.",
        },
        "purpose_id": {
            "type": ["string", "null"],
            "description": "Optional ADR purpose/consent tag threaded into the signed envelope.",
        },
    },
    "required": ["endpoint_id", "instruction"],
    "additionalProperties": False,
}

_A2A_LIST_ENDPOINTS_SCHEMA: dict = {
    "type": "object",
    "properties": {},
    "additionalProperties": False,
}


# ---------------------------------------------------------------------------
# Group 3 — ACS delegation_loop (ADR-0190 M6)
# ---------------------------------------------------------------------------
try:
    from acs_engine_adapter import run_acs_workflow as _run_acs_workflow  # type: ignore[import]
    _ACS_AVAILABLE = True
except ImportError:
    _ACS_AVAILABLE = False

_ACS_DELEGATE_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "task": {
            "type": "string",
            "description": (
                "Natural-language task for the autonomous delegation_loop "
                "engine (ADR-0104) — a manager sub-agent plans and spawns "
                "worker sub-agents until the task converges or the budget "
                "is exhausted."
            ),
        },
        "tenant_id": {"type": ["string", "null"]},
        "dry_run": {
            "type": "boolean",
            "description": "Validate the spec and return without spending compute quota. Default false.",
        },
        "budget_override": {
            "type": ["object", "null"],
            "description": "Optional override of the delegation_loop budget dict (e.g. max_iterations).",
        },
        "budget_s": {
            "type": "integer", "minimum": 30, "maximum": 600,
            "description": "Wall-clock watchdog in seconds (separate from the compute budget). Clamped [30,600], default 180.",
        },
    },
    "required": ["task"],
    "additionalProperties": False,
}


def _tool_definitions() -> list[dict[str, Any]]:
    tools: list[dict[str, Any]] = []
    if _AWP_AVAILABLE:
        tools.extend([
            {
                "name": "workflow_run",
                "description": (
                    "Run an AWP DAG-workflow (ADR-0090/ADR-0188) end to end via "
                    "the real engine — code/merge/route/ask_human nodes all "
                    "supported, unlike the console's separate chat-mode "
                    "executor. May pause and return paused_at_node/paused_prompt "
                    "if the workflow hits an ask_human node; resume via "
                    "workflow_resume. Enforces the same workflows_concurrent "
                    "license limit as the console (fail-closed); a wall-clock "
                    "watchdog (budget_s) additionally bounds this call."
                ),
                "inputSchema": _WORKFLOW_RUN_SCHEMA,
            },
            {
                "name": "workflow_resume",
                "description": (
                    "Resume a paused AWP workflow run (from workflow_list_paused) "
                    "by injecting a human reply at its ask_human node."
                ),
                "inputSchema": _WORKFLOW_RESUME_SCHEMA,
            },
            {
                "name": "workflow_list_paused",
                "description": "List paused AWP workflow runs awaiting a human reply for this tenant.",
                "inputSchema": _WORKFLOW_LIST_PAUSED_SCHEMA,
            },
        ])
    if _A2A_AVAILABLE:
        tools.extend([
            {
                "name": "a2a_send",
                "description": (
                    "Send a signed task instruction to a paired CorvinOS "
                    "instance (ADR-0103/ADR-0116/ADR-0038 A2A protocol). "
                    "Pairing must already be configured (console-managed); "
                    "this only sends. Never raises on remote/transport "
                    "failure — check the response's ok/status fields."
                ),
                "inputSchema": _A2A_SEND_SCHEMA,
            },
            {
                "name": "a2a_list_endpoints",
                "description": "List configured A2A endpoint ids and labels for this instance.",
                "inputSchema": _A2A_LIST_ENDPOINTS_SCHEMA,
            },
        ])
    if _ACS_AVAILABLE:
        tools.append({
            "name": "acs_delegate",
            "description": (
                "Delegate a task to the Autonomous Compute Shell (ADR-0104 "
                "delegation_loop engine) — an autonomous manager/worker "
                "sub-agent loop, out of this turn's context. Spends compute "
                "quota (charge_quota=true) unless dry_run=true. Distinct "
                "from workflow_run: this is an open-ended autonomous loop, "
                "not a fixed DAG."
            ),
            "inputSchema": _ACS_DELEGATE_SCHEMA,
        })
    return tools


# ---------------------------------------------------------------------------
# Server
# ---------------------------------------------------------------------------


class OrchestrationServer:
    def __init__(self, *, stdin=None, stdout=None, stderr=None) -> None:
        self._stdin = stdin or sys.stdin
        self._stdout = stdout or sys.stdout
        self._stderr = stderr or sys.stderr
        self._stdout_lock = threading.Lock()
        self._initialized = False
        self._shutting_down = False
        self.caller_persona = (os.environ.get("CORVIN_CALLER_PERSONA") or "").strip()
        # WF-A3: the identity of the chat this server process is actually
        # running inside, set by the bridge adapter at spawn time — NOT
        # something a `tools/call` argument can carry, because a chat
        # participant could then simply claim to be whoever they like. See
        # `_replier_from_channel_id()` for how this is turned into a
        # `resume_workflow(replier=...)` value.
        self.caller_channel_id = (os.environ.get("CORVIN_CHANNEL_ID") or "").strip()

    # -- transport -----------------------------------------------------

    def _send(self, msg: dict[str, Any]) -> None:
        line = json.dumps(msg, separators=(",", ":")) + "\n"
        with self._stdout_lock:
            self._stdout.write(line)
            self._stdout.flush()

    def _respond(self, msgid: Any, result: Any) -> None:
        self._send({"jsonrpc": "2.0", "id": msgid, "result": result})

    def _error(self, msgid: Any, code: int, message: str, data: Any = None) -> None:
        err: dict[str, Any] = {"code": code, "message": message}
        if data is not None:
            err["data"] = data
        self._send({"jsonrpc": "2.0", "id": msgid, "error": err})

    def _log(self, *args: Any) -> None:
        print(*args, file=self._stderr, flush=True)

    def _text_result(self, payload: dict, *, is_error: bool = False) -> dict:
        return {
            "content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}],
            "isError": is_error,
        }

    # -- main loop -------------------------------------------------------

    def serve(self) -> int:
        for raw in self._stdin:
            raw = raw.strip()
            if not raw:
                continue
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                self._error(None, PARSE_ERROR, "parse error")
                continue
            try:
                self._dispatch(msg)
            except Exception as e:  # noqa: BLE001
                self._log("server: unhandled", repr(e))
                self._log(traceback.format_exc())
                msgid = msg.get("id") if isinstance(msg, dict) else None
                self._error(msgid, INTERNAL_ERROR, f"internal error: {e}")
            if self._shutting_down:
                break
        return 0

    def _dispatch(self, msg: dict[str, Any]) -> None:
        if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0":
            self._error(msg.get("id") if isinstance(msg, dict) else None,
                        INVALID_REQUEST, "invalid request")
            return
        method = msg.get("method")
        msgid = msg.get("id")
        params = msg.get("params") or {}
        is_notification = "id" not in msg

        if method == "initialize":
            self._handle_initialize(msgid, params)
        elif method == "notifications/initialized":
            self._initialized = True
        elif method == "tools/list":
            self._respond(msgid, {"tools": _tool_definitions()})
        elif method == "tools/call":
            self._handle_tools_call(msgid, params)
        elif method == "shutdown":
            self._respond(msgid, None)
            self._shutting_down = True
        elif method == "ping":
            self._respond(msgid, {})
        elif is_notification:
            return
        else:
            self._error(msgid, METHOD_NOT_FOUND, f"method not found: {method}")

    def _handle_initialize(self, msgid: Any, params: dict) -> None:
        client_info = params.get("clientInfo", {})
        self._log(f"initialize from {client_info.get('name', '?')}")
        self._respond(msgid, {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        })

    def _handle_tools_call(self, msgid: Any, params: dict) -> None:
        name = params.get("name") or ""
        args = params.get("arguments") or {}
        if not isinstance(args, dict):
            self._error(msgid, INVALID_PARAMS, "arguments must be an object")
            return

        handlers: dict[str, Callable[[Any, dict], None]] = {
            "workflow_run": self._call_workflow_run,
            "workflow_resume": self._call_workflow_resume,
            "workflow_list_paused": self._call_workflow_list_paused,
            "a2a_send": self._call_a2a_send,
            "a2a_list_endpoints": self._call_a2a_list_endpoints,
            "acs_delegate": self._call_acs_delegate,
        }
        handler = handlers.get(name)
        if handler is None:
            self._error(msgid, METHOD_NOT_FOUND, f"unknown tool: {name!r}")
            return
        handler(msgid, args)

    # -- Group 1: AWP workflows -----------------------------------------

    def _call_workflow_run(self, msgid: Any, args: dict) -> None:
        if not _AWP_AVAILABLE:
            self._error(msgid, METHOD_NOT_FOUND, "AWP workflow engine not installed (core/workflows/)")
            return
        workflow_id = str(args.get("workflow_id") or "")
        if not _WID_RE.match(workflow_id):
            self._error(msgid, INVALID_PARAMS, "workflow_id must match [a-z][a-z0-9_-]{0,63}")
            return
        tenant_id = _current_tenant(args.get("tenant_id"))
        yaml_path = _tenant_home(tenant_id) / "workflows" / f"{workflow_id}.awp.yaml"
        if not yaml_path.exists():
            self._error(msgid, INVALID_PARAMS, f"workflow {workflow_id!r} not found for tenant {tenant_id!r}")
            return

        try:
            doc = _load_workflow(str(yaml_path))
            _validate_workflow(doc)
        except _WorkflowInvalid as exc:
            self._error(msgid, INVALID_PARAMS, f"[{exc.code}] {exc.message}")
            return
        except Exception as exc:  # noqa: BLE001
            # Deliberately broad, not just (OSError, ValueError): malformed
            # YAML raises yaml.YAMLError (ScannerError/ParserError/...),
            # which is not a subclass of either — a corrupted workflow file
            # must surface as a clean "invalid workflow" INVALID_PARAMS
            # response, not an opaque INTERNAL_ERROR from the top-level
            # unhandled-exception handler.
            self._error(msgid, INVALID_PARAMS, f"invalid workflow: {exc}")
            return

        # Concurrency gate runs BEFORE engine construction: it is a cheap,
        # engine-independent licensing check, and at the limit we must refuse
        # without paying to instantiate an engine. Ordering also keeps the
        # refusal deterministic in environments where the `claude` CLI is
        # absent (CI) — otherwise engine_unavailable would mask the refusal.
        refusal = _workflow_concurrency_refusal(tenant_id)
        if refusal:
            self._respond(msgid, self._text_result(
                {"status": "refused", "error": refusal}, is_error=True,
            ))
            return

        # Construct the LLM engine ONLY when a node actually needs it. A pure
        # code/compute/merge workflow must run without the `claude` CLI on PATH
        # (Hermes-only / no-Claude fresh install, and CI). An engine-requiring
        # workflow with no CLI fails fast here with a clean engine_unavailable
        # envelope instead of an opaque mid-run node failure.
        needs_engine = any(
            str(node.get("type", "agent")) not in _ENGINE_FREE_NODE_TYPES
            for node in doc.graph
        )
        engine: Any = _NULL_ENGINE
        if needs_engine:
            try:
                engine = _ClaudeCliEngine()
            except _ClaudeEngineError as exc:
                self._respond(msgid, self._text_result(
                    {"status": "engine_unavailable", "error": str(exc)}, is_error=True,
                ))
                return

        budget_s = _clamp(args.get("budget_s"), lo=10, hi=600, default=120)
        sink = _audit_sink_for(tenant_id, "workflow")
        runner = _DAGRunner(doc, engine=engine, audit_sink=sink, tenant_id=tenant_id)

        # Pre-assign the run's id (rather than letting DAGRunner.run() pick
        # one internally) so a timeout response — returned before run()
        # itself returns — can still hand back a real, later-pollable
        # run_id, and so a background completion notification (registered
        # only if the run actually outlives budget_s, see
        # _completion_notify_hooks) is keyed under the SAME id.
        run_id_hint = secrets.token_hex(8)
        on_timeout, on_finish = _completion_notify_hooks(
            run_id_hint, tenant_id, f"workflow {workflow_id!r}",
        )

        global _ACTIVE_WORKFLOW_RUNS
        with _ACTIVE_WORKFLOW_LOCK:
            _ACTIVE_WORKFLOW_RUNS += 1
        try:
            result = _run_with_budget(
                lambda: runner.run(inputs=args.get("inputs") or {}, run_id=run_id_hint),
                budget_s=budget_s, run_id_hint=run_id_hint,
                on_timeout=on_timeout, on_finish=on_finish,
            )
        except Exception as exc:  # noqa: BLE001
            self._error(msgid, INTERNAL_ERROR, f"workflow run failed: {exc}")
            return
        finally:
            with _ACTIVE_WORKFLOW_LOCK:
                _ACTIVE_WORKFLOW_RUNS -= 1
        self._respond(msgid, self._run_result_envelope(result))

    def _call_workflow_resume(self, msgid: Any, args: dict) -> None:
        if not _AWP_AVAILABLE:
            self._error(msgid, METHOD_NOT_FOUND, "AWP workflow engine not installed (core/workflows/)")
            return
        run_id = str(args.get("run_id") or "")
        reply = str(args.get("reply") or "")
        if not run_id or not reply:
            self._error(msgid, INVALID_PARAMS, "run_id and reply are required")
            return
        tenant_id = _current_tenant(args.get("tenant_id"))
        budget_s = _clamp(args.get("budget_s"), lo=10, hi=600, default=120)
        sink = _audit_sink_for(tenant_id, "workflow")

        # Unlike workflow_run, the run_id already exists (it's the caller's
        # own argument) — reuse it as-is for the completion-notify hooks, so
        # a resume that outlives its budget notifies under the same id the
        # caller already has, and any subsequent resume attempt on the same
        # run_id shares one notification lineage.
        on_timeout, on_finish = _completion_notify_hooks(
            run_id, tenant_id, f"workflow resume {run_id!r}",
        )

        try:
            result = _run_with_budget(
                lambda: _resume_workflow(
                    run_id, reply,
                    # Lazy: an unknown run_id (or a resume whose remaining nodes
                    # never spawn an agent) must not require the `claude` CLI —
                    # construction is deferred to the first real agent spawn.
                    engine=_LazyClaudeEngine(),
                    tenant_id=tenant_id,
                    # WF-A3: this tool is chat-reachable by design (ADR-0190 —
                    # any persona participant with this MCP server exposed can
                    # call it), so it must NEVER take the `replier=None`
                    # privileged-owner shortcut reserved for genuinely
                    # privileged callers (e.g. the console). The real caller
                    # identity comes from `CORVIN_CHANNEL_ID`, set by the
                    # bridge adapter at process-spawn time and therefore not
                    # spoofable via the tool call's own arguments.
                    # resume_workflow() rejects a mismatch against the
                    # checkpoint's recorded `approver` with
                    # UnauthorizedReplier, handled below.
                    replier=_replier_from_channel_id(self.caller_channel_id),
                    audit_sink=sink,
                ),
                budget_s=budget_s, run_id_hint=run_id,
                on_timeout=on_timeout, on_finish=on_finish,
            )
        except _ClaudeEngineError as exc:
            self._respond(msgid, self._text_result(
                {"status": "engine_unavailable", "error": str(exc)}, is_error=True,
            ))
            return
        except KeyError as exc:
            self._error(msgid, INVALID_PARAMS, f"no paused run found: {exc}")
            return
        except _awp_checkpoint.AlreadyClaimedError as exc:
            self._respond(msgid, self._text_result(
                {"status": "already_resuming", "error": str(exc)}, is_error=True,
            ))
            return
        except _UnauthorizedReplier as exc:
            self._error(msgid, INVALID_PARAMS, f"unauthorized reply: {exc}")
            return
        except RuntimeError as exc:
            self._error(msgid, INTERNAL_ERROR, f"resume failed: {exc}")
            return
        self._respond(msgid, self._run_result_envelope(result))

    def _run_result_envelope(self, result: Any) -> dict:
        if isinstance(result, dict):  # _run_with_budget timeout sentinel
            return self._text_result(result, is_error=True)
        envelope = {
            "status": result.state,  # "complete" | "failed" | "paused"
            "run_id": result.run_id,
            "error": result.error,
            "paused_at_node": result.paused_at_node,
            "paused_prompt": result.paused_prompt,
            "final_state": result.final_state,
            "node_count": len(result.nodes),
        }
        return self._text_result(envelope, is_error=(result.state == "failed"))

    def _call_workflow_list_paused(self, msgid: Any, args: dict) -> None:
        if not _AWP_AVAILABLE:
            self._error(msgid, METHOD_NOT_FOUND, "AWP workflow engine not installed (core/workflows/)")
            return
        tenant_id = _current_tenant(args.get("tenant_id"))
        try:
            paused = _awp_checkpoint.list_paused(tenant_id=tenant_id)
        except Exception as exc:  # noqa: BLE001
            self._error(msgid, INTERNAL_ERROR, f"list_paused failed: {exc}")
            return
        self._respond(msgid, self._text_result({"paused": paused}))

    # -- Group 2: A2A ------------------------------------------------------

    def _call_a2a_send(self, msgid: Any, args: dict) -> None:
        if not _A2A_AVAILABLE:
            self._error(msgid, METHOD_NOT_FOUND, "A2A sender not installed (operator/bridges/shared/)")
            return
        endpoint_id = str(args.get("endpoint_id") or "")
        instruction = str(args.get("instruction") or "")
        if not endpoint_id or not instruction:
            self._error(msgid, INVALID_PARAMS, "endpoint_id and instruction are required")
            return
        timeout_s = _clamp(args.get("timeout_s"), lo=5, hi=120, default=30)
        ttl_s = args.get("ttl_s")
        purpose_id = args.get("purpose_id")

        sender = _RemoteTriggerSender()
        try:
            result = _run_with_budget(
                lambda: sender.send(
                    endpoint_id, instruction,
                    ttl_s=ttl_s, timeout_s=timeout_s, purpose_id=purpose_id,
                ),
                budget_s=timeout_s + 15,
            )
        except Exception as exc:  # noqa: BLE001
            self._error(msgid, INTERNAL_ERROR, f"a2a_send failed: {exc}")
            return
        if isinstance(result, dict):  # _run_with_budget timeout sentinel
            self._respond(msgid, self._text_result(result, is_error=True))
            return
        envelope = {
            "ok": result.ok,
            "status": result.status,
            "task_id": result.task_id,
            "instance_id": result.instance_id,
            "instance_id_match": result.instance_id_match,
            "data": result.data,
            "duration_ms": result.duration_ms,
        }
        self._respond(msgid, self._text_result(envelope, is_error=not result.ok))

    def _call_a2a_list_endpoints(self, msgid: Any, args: dict) -> None:
        if not _A2A_AVAILABLE:
            self._error(msgid, METHOD_NOT_FOUND, "A2A sender not installed (operator/bridges/shared/)")
            return
        registry = _RemoteEndpointRegistry()
        out: list[dict] = []
        for eid in registry.list_ids():
            try:
                cfg = registry.load(eid)
                out.append({"endpoint_id": eid, "label": cfg.get("label", ""), "enabled": True})
            except _EndpointError as exc:
                out.append({"endpoint_id": eid, "enabled": False, "error": str(exc)})
        self._respond(msgid, self._text_result({"endpoints": out}))

    # -- Group 3: ACS --------------------------------------------------

    def _call_acs_delegate(self, msgid: Any, args: dict) -> None:
        if not _ACS_AVAILABLE:
            self._error(msgid, METHOD_NOT_FOUND, "ACS engine not installed (operator/bridges/shared/)")
            return
        task = str(args.get("task") or "")
        if not task:
            self._error(msgid, INVALID_PARAMS, "task is required")
            return
        tenant_id = _current_tenant(args.get("tenant_id"))
        dry_run = bool(args.get("dry_run", False))
        budget_override = args.get("budget_override")
        budget_s = _clamp(args.get("budget_s"), lo=30, hi=600, default=180)

        # R31: delegation_loop.budget, if present at all, must carry max_depth
        # — so an absent/empty budget_override must omit the key entirely
        # (letting the engine apply its own defaults) rather than send an
        # empty {} that trips validation. budget_override is passed to
        # run_acs_workflow() as its own kwarg below (which merges it over
        # spec.orchestration.delegation_loop.budget) — NOT embedded into the
        # spec here too, so there is exactly one source of truth for it.
        spec = {
            "awp": "1.0.0",
            "workflow": {
                "name": f"chat-delegated-{int(time.time())}",
                "description": task,
                "version": "1.0.0",
            },
            "orchestration": {
                "engine": "delegation_loop",
                "delegation_loop": {},
            },
            "state": {"initial": {"task": task}},
        }

        try:
            result = _run_with_budget(
                lambda: _run_acs_workflow(
                    spec, tenant_id=tenant_id, dry_run=dry_run,
                    budget_override=budget_override,
                ),
                budget_s=budget_s,
            )
        except Exception as exc:  # noqa: BLE001
            self._error(msgid, INTERNAL_ERROR, f"acs_delegate failed: {exc}")
            return
        is_error = isinstance(result, dict) and result.get("status") in ("timeout", "failed")
        self._respond(msgid, self._text_result(result, is_error=is_error))


def main(argv: list[str] | None = None) -> int:  # noqa: ARG001
    server = OrchestrationServer()
    return server.serve()


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main(sys.argv[1:]))
