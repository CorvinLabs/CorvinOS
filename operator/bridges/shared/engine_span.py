"""ADR-0171 M1 — Universal Engine-Span audit record.

ONE canonical, engine-agnostic record per engine invocation (OS or worker, any
engine type, any path). Emitting a span at every engine boundary is what makes
"every engine fully auditable" hold independently of the ACS/compute path
(ADR-0114) — a span is one cheap metadata event, so auditability is decoupled
from billing.

This module owns the SCHEMA (event names, field set, span-id minting, the
positive allowlist + severity registration). Each spawn site emits via its own
L16 audit writer so the span lands on the SAME hash-chain as that site's other
events (ACS → acs_runtime._write_audit; OS → audit.audit_event). The console
later projects all three views (OS graph / Worker graph / chain) from these.

METADATA ONLY (L16/L34): never prompt/output/transcript text; IDs are
pseudonymous (L36-erasable), never raw uid/email.
"""
from __future__ import annotations

import secrets
import time
from typing import Any

ENGINE_SPAN_START = "engine.span.start"
ENGINE_SPAN_END = "engine.span.end"

ROLES = frozenset({"os", "manager", "worker"})

# Positive allowlist (ADR-0129 M2) — exactly the metadata fields a span carries.
START_FIELDS = frozenset({
    "span_id", "parent_span_id", "role", "engine_id", "model_id",
    "run_id", "turn_id", "started_at",
})
END_FIELDS = frozenset({
    "span_id", "parent_span_id", "role", "engine_id", "model_id",
    "run_id", "turn_id", "status", "duration_ms", "tokens_used", "tool_call_count",
    "trace_available",  # ADR-0172 M1: True when a worker-trace.jsonl was written
})


def new_span_id() -> str:
    """A fresh pseudonymous span id (no PII; L36-erasable by correlation)."""
    return "spn_" + secrets.token_hex(8)


def _register_allowlists() -> None:
    """Best-effort positive-allowlist registration so the audit floor keeps the
    span fields (and would drop any future non-allowlisted key). Harmless if
    forge isn't importable yet — the denylist floor already passes these
    non-PII fields."""
    try:
        from forge import security_events as _sec  # type: ignore  # noqa: PLC0415
        _sec.register_event_allowlist(ENGINE_SPAN_START, START_FIELDS)
        _sec.register_event_allowlist(ENGINE_SPAN_END, END_FIELDS)
    except Exception:  # noqa: BLE001
        pass


_register_allowlists()


def start_details(*, span_id: str, role: str, engine_id: str, model_id: str = "",
                  parent_span_id: str = "", run_id: str = "",
                  turn_id: str = "") -> dict[str, Any]:
    """Build the metadata dict for an engine.span.start event."""
    return {
        "span_id": span_id, "parent_span_id": parent_span_id, "role": role,
        "engine_id": engine_id, "model_id": model_id or "",
        "run_id": run_id, "turn_id": turn_id, "started_at": time.time(),
    }


def end_details(*, span_id: str, role: str, engine_id: str, model_id: str = "",
                parent_span_id: str = "", run_id: str = "", turn_id: str = "",
                status: str = "ok", duration_ms: int = 0, tokens_used: int = 0,
                tool_call_count: int = 0,
                trace_available: bool = False) -> dict[str, Any]:
    """Build the metadata dict for an engine.span.end event."""
    return {
        "span_id": span_id, "parent_span_id": parent_span_id, "role": role,
        "engine_id": engine_id, "model_id": model_id or "",
        "run_id": run_id, "turn_id": turn_id, "status": status,
        "duration_ms": int(duration_ms), "tokens_used": int(tokens_used),
        "tool_call_count": int(tool_call_count),
        "trace_available": bool(trace_available),
    }


def emit_start(audit_event_fn, **kw: Any) -> None:
    """Emit engine.span.start via a caller-supplied writer with the signature of
    ``audit.audit_event`` (event_type, *, details=, severity=, tenant_id=,
    channel=, chat_key=). Used by the OS paths (M2). The ACS path emits via its
    own _write_audit to stay on its chain."""
    audit_event_fn(ENGINE_SPAN_START, details=start_details(**_split(kw)[0]),
                   severity="INFO", **_split(kw)[1])


def emit_end(audit_event_fn, **kw: Any) -> None:
    """Emit engine.span.end (see emit_start)."""
    audit_event_fn(ENGINE_SPAN_END, details=end_details(**_split(kw)[0]),
                   severity="INFO", **_split(kw)[1])


_WRITER_KEYS = frozenset({"tenant_id", "channel", "chat_key"})


def _split(kw: dict[str, Any]) -> "tuple[dict[str, Any], dict[str, Any]]":
    """Split kwargs into (detail fields, writer-routing fields)."""
    detail = {k: v for k, v in kw.items() if k not in _WRITER_KEYS}
    routing = {k: v for k, v in kw.items() if k in _WRITER_KEYS}
    return detail, routing
