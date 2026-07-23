"""ADR-0214: TDE audit shim — hash-chained, CONTENT-FREE events.

Bridges the TDE package to the canonical audit chain
(operator/bridges/shared/audit.py → forge.security_events, hash_chain=True).

Compliance contract (CLAUDE.md baseline):
- Every emitted event is CONTENT-FREE: only allowlisted scalar metadata
  (engine names, confidences, counts, reason codes). Task text, statement
  values, and snapshots are NEVER passed through — _scrub() drops any
  non-allowlisted key and truncates strings defensively.
- Best-effort: if the audit backend is unavailable (e.g. unit tests without
  forge), events are dropped silently. TDE decisions themselves are never
  blocked by audit availability (audit_event upstream is best-effort too).

Event namespace: ``tde.*``
- tde.engine_selected      {engine, confidence, override, trivial, task_type, complexity, tde_run_id}
- tde.l34_blocked          {scope, reason_code, variable_class, tde_run_id, step_num?}
- tde.delegation_decision  {step_action, delegate, reason_code, tde_run_id, step_num}
- tde.step_delegated       {step_action, success, duration_ms, ipc, tde_run_id, step_num}
- tde.step_executed_local  {step_action, success, duration_ms, tde_run_id, step_num}
- tde.loss_recorded        {task_type, engine, loss_pct, measured, tde_run_id, step_num}
- tde.plan_executed        {step_count, batch_count, delegated_count, local_count, tde_run_id}

``tde_run_id`` (ADR-0214 audit-graph endpoint) is the per-turn correlation ID
chat_runtime._stream_tde_turn generates (``tde-<epoch>-<hex>``) and threads
down through SendIntegration -> TieredDelegationEngine -> AdaptiveDelegation-
Executor; ``step_num`` is the plan's 1-based step number. Together they let
core/console/corvin_console/routes/compute.py::_build_tde_audit_graph
reconstruct one turn's real delegation tree from the hash-chained events.
Both are optional (default "") for callers that predate this correlation —
an event missing them just can't be placed on a per-turn graph.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any, Callable, Optional

_logger = logging.getLogger(__name__)

# Allowlisted detail keys (closed set — everything else is dropped).
_ALLOWED_KEYS = {
    "engine", "confidence", "override", "trivial", "task_type", "complexity",
    "scope", "reason_code", "variable_class", "step_action", "delegate",
    "success", "duration_ms", "ipc", "loss_pct", "measured", "step_count",
    "batch_count", "delegated_count", "local_count",
    # ADR-0214 audit-graph endpoint (turn/step correlation — see compute.py
    # _build_tde_audit_graph): tde_run_id is the same identifier chat_runtime
    # already tags its own web.turn.* events with; step_num lets the graph
    # builder distinguish two steps sharing the same step_action.
    "tde_run_id", "step_num",
    # NOTE: no free-text-capable keys. "signals_digest" was removed (round-3):
    # it was the only unguarded string slot and is not emitted anywhere.
}
_MAX_STR = 120

# Keys whose values are LM-influenced identifiers (step actions, task types).
# The LM can emit FREE TEXT here ("email john@example.com the report") — a
# raw pass-through would put PII into the unerasable hash chain (round-2
# refutation finding). Enforce a closed identifier shape; anything else is
# replaced by a neutral token.
_IDENTIFIER_KEYS = {"step_action", "task_type", "engine", "complexity",
                    "reason_code", "scope", "variable_class", "ipc",
                    "tde_run_id"}
_IDENTIFIER_RE = __import__("re").compile(r"^[A-Za-z0-9_.:-]{1,40}$")

_audit_fn: Optional[Callable[..., None]] = None
_resolved = False


def _resolve_audit_fn() -> Optional[Callable[..., None]]:
    """Locate bridges/shared audit.audit_event (lazy, cached)."""
    global _audit_fn, _resolved
    if _resolved:
        return _audit_fn
    _resolved = True

    try:
        import audit as _audit_mod  # already on sys.path (bridge context)
        if hasattr(_audit_mod, "audit_event"):
            _audit_fn = _audit_mod.audit_event
            return _audit_fn
    except Exception:
        pass

    try:
        shared = Path(__file__).resolve().parents[2] / "bridges" / "shared"
        if shared.is_dir() and str(shared) not in sys.path:
            sys.path.insert(0, str(shared))
        import audit as _audit_mod  # type: ignore[no-redef]
        if hasattr(_audit_mod, "audit_event"):
            _audit_fn = _audit_mod.audit_event
    except Exception as exc:  # pragma: no cover - environment-dependent
        _logger.debug("TDE audit backend unavailable: %s", exc)
        _audit_fn = None
    return _audit_fn


def _scrub(details: dict[str, Any]) -> dict[str, Any]:
    """CONTENT-FREE enforcement: allowlisted scalar keys only."""
    out: dict[str, Any] = {}
    for key, value in details.items():
        if key not in _ALLOWED_KEYS:
            continue
        if isinstance(value, bool) or value is None:
            out[key] = value
        elif isinstance(value, (int, float)):
            out[key] = round(value, 4) if isinstance(value, float) else value
        elif isinstance(value, str):
            if key in _IDENTIFIER_KEYS:
                # Closed identifier vocabulary — free text never enters the chain.
                out[key] = value if _IDENTIFIER_RE.match(value) else "nonstandard"
            else:
                out[key] = value[:_MAX_STR]
        # dicts/lists/objects: dropped — they could smuggle content
    return out


def emit(event_type: str, **details: Any) -> None:
    """Emit one hash-chained tde.* audit event (best-effort, content-free)."""
    if not event_type.startswith("tde."):
        event_type = f"tde.{event_type}"
    fn = _resolve_audit_fn()
    if fn is None:
        return
    try:
        fn(event_type, details=_scrub(details))
    except Exception as exc:  # pragma: no cover - backend hiccups must not break TDE
        _logger.debug("TDE audit emit failed (%s): %s", event_type, exc)


def reset_for_tests() -> None:
    """Reset cached resolution (test hook)."""
    global _audit_fn, _resolved
    _audit_fn = None
    _resolved = False
