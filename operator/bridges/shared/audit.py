"""Voice bridge audit log.

Events from the bridge layer (login attempts, whitelist denials, PIN
failures, channel rate-limit hits, message-received, persona-routed,
tool-use) are appended to the **same** sha256 hash-chained file the
forge plugin writes to, so a single ``voice-audit verify`` covers both
bridge and tool-factory activity.

Audit-chain strategy
====================
The hash chain is **unified** across all bridges, all forge scopes,
all personas — one chain per host, period. We therefore resolve the
default chain location at the user-global ``corvin_home() / "global"
/ "forge" / "audit.jsonl"``, *independently* of the active workspace
scope (task / session / project / user). Splitting the chain per
scope would multiply chains by 4x and defeat the single-verify
guarantee that ``voice-audit verify`` advertises.

Default path resolution (precedence high -> low):
  1. ``VOICE_AUDIT_PATH``  — explicit env override (tests use this)
  2. ``$FORGE_ROOT/audit.jsonl``
  3. ``corvin_home() / "global" / "forge" / "audit.jsonl"``

Backed by ``forge.security_events`` when the forge plugin sits next to
voice in this repo (the normal case). When forge is missing — voice
deployed standalone without forge — the audit functions become no-ops.
That preserves the legacy behaviour: voice never crashes on a missing
optional plugin.

Public API:
    audit_event(event_type, *, channel, chat_key, user, persona, tool,
                details) -> None
        Best-effort append. Silent on failure (filesystem read-only,
        forge missing, etc).

    verify_audit() -> tuple[bool, list[dict]]
        Returns (ok, problems). ``ok`` is True when the chain holds end
        to end. ``problems`` lists tampered/broken entries with line
        numbers — same shape as forge.security_events.verify_chain.

    audit_path() -> Path
        Returns the resolved audit file path (env override or default).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

# ── ADR-0141 Tier 3 — self-register this security capability at import time ──
try:  # pragma: no cover - exercised at adapter boot / self-test
    from security_capabilities import (  # noqa: E402
        register_capability as _reg_cap,
        module_self_hash as _self_hash,
    )

    _reg_cap("audit", version="3.0", file_hash=_self_hash(__file__))
except Exception:  # pragma: no cover - fail-closed: absent capability blocks spawn
    pass


def _forge_workspace_root() -> Path:
    """Resolve the *audit-chain* workspace root.

    NOTE: this intentionally does NOT mirror
    ``operator/forge/forge.py::_default_root`` — the audit chain is
    unified (scope-independent) by design (see module docstring), so
    we skip the scope-detection branch and pin it at
    ``corvin_home()/global/forge``. ``FORGE_ROOT`` still wins as an
    explicit override (used by tests + ops tooling that want to
    sandbox the chain).
    """
    env = os.environ.get("FORGE_ROOT")
    if env:
        return Path(env).expanduser()
    # paths.py sits next to audit.py in operator/bridges/shared/.
    # Tests load audit.py as a top-level module (sys.path injection),
    # so relative import only works in package mode — fall back to
    # absolute when needed.
    try:
        from .paths import corvin_home as corvin_home  # type: ignore
    except ImportError:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from paths import corvin_home as corvin_home  # type: ignore
    # Audit-chain default lives at the user-global root regardless of
    # active workspace scope, so it stays unified across sessions.
    return corvin_home() / "global" / "forge"


def audit_path() -> Path:
    p = os.environ.get("VOICE_AUDIT_PATH")
    if p:
        return Path(p)
    return _forge_workspace_root() / "audit.jsonl"


# Kept for backwards reference in old tests / docs that imported the
# constant directly. The real default is computed by audit_path().
DEFAULT_AUDIT_PATH = _forge_workspace_root() / "audit.jsonl"


# Optional forge dependency — silent fallback when absent.
_se = None
try:
    _voice_plugin_root = Path(__file__).resolve().parents[2]  # operator/bridges/shared/audit.py → operator/
    _forge_root = _voice_plugin_root / "forge"
    if _forge_root.is_dir() and (_forge_root / "forge").is_dir():
        sys.path.insert(0, str(_forge_root))
        from forge import security_events as _se  # type: ignore
except Exception:
    _se = None
    import logging as _audit_log
    _audit_log.getLogger("corvin.audit").warning(
        "forge not importable — audit writes are no-ops; "
        "voice-audit verify will report OK with 0 events (not an error in standalone mode)"
    )

# ── ADR-0233 — optional secondary sink (audit_backend plugin) ────────────────
# Optional exactly like cowork/forge: the only permitted form is the
# `_audit_sink is not None` guard below.  This is a FAN-OUT hook, not a writer:
# it runs AFTER the core hash-chained write has committed, so no plugin can
# suppress, rewrite, reorder or delay a compliance record (ADR-0232, ADR-0233 D4).
_audit_sink = None
try:
    _core_plugins = Path(__file__).resolve().parents[3] / "core" / "plugins"
    if (_core_plugins / "corvin_plugins").is_dir():
        if str(_core_plugins) not in sys.path:
            # append, NOT insert(0): core/plugins also holds tests/ and
            # templates/ without __init__.py. audit.py is imported by every
            # adapter process, so putting that directory first on sys.path would
            # let `import tests` resolve there instead of the caller's own —
            # the operator/ stdlib-shadow trap, one level over.
            sys.path.append(str(_core_plugins))
        from corvin_plugins.providers import audit_backend as _audit_sink  # type: ignore
except Exception:  # noqa: BLE001 - absent plugin package must never break audit
    _audit_sink = None


# Voice-specific severity mapping; forge's mapping is reused for tool/policy
# events, so we don't duplicate those.
_VOICE_EVENT_SEVERITY: dict[str, str] = {
    "bridge.login":               "INFO",
    "bridge.login_failed":        "WARNING",
    "bridge.whitelist_deny":      "WARNING",
    "bridge.read_only_drop":      "WARNING",
    "bridge.observer_appended":   "INFO",
    "bridge.observer_removed":    "INFO",
    "bridge.observer_transcript_consumed": "INFO",
    "bridge.pin_failure":         "WARNING",
    "bridge.rate_limit_exceeded": "WARNING",
    "bridge.message_received":    "INFO",
    "bridge.persona_routed":      "INFO",
    "bridge.tool_use":            "INFO",
    "bridge.cancel":              "INFO",
    "bridge.btw_inject":          "INFO",
    "bridge.inbox_whitelist_drift": "WARNING",
    "bridge.config_reloaded":     "INFO",
    "bridge.error":               "ERROR",
    # Audit-chain integrity (layer-16 hardening) — emitted by the boot
    # health check when verify_chain() finds tampered or broken records
    # from a prior session. Out-of-band: written WITHOUT hash_chain so a
    # broken chain can still record its own gap.
    "audit.chain_gap_detected":   "CRITICAL",
    # ADR-0169 — boot-time gate-pipeline invariant self-test. A mis-ordered
    # security chain (e.g. egress before classification) is a CRITICAL defect,
    # not an INFO note (security review 2026-06-27).
    "gate_pipeline.self_test_failed": "CRITICAL",
    # ADR-0171 — universal engine-span audit (one record per engine invocation,
    # OS or worker, any engine; metadata only). Routine lifecycle → INFO.
    "engine.span.start":          "INFO",
    "engine.span.end":            "INFO",
    # daemon lifecycle (emitted by Node daemons via `voice-audit emit`)
    "daemon.started":             "INFO",
    "daemon.stopped":             "INFO",
    "daemon.error":               "ERROR",
    # ADR-0049 — Layer 22 session-pinned workers
    "worker_session.created":     "INFO",
    "worker_session.resumed":     "INFO",
    "worker_session.stale_evicted": "WARNING",
    "worker_session.purged":      "INFO",
    # ADR-0057 — L39 Incident Tracker (Art. 73)
    "incident.opened":            "CRITICAL",
    "incident.status_changed":    "INFO",
    "incident.closed":            "INFO",
    # ADR-0154 — OTA license-tamper detection (M4 compliant response + validator canary)
    "license.tamper_response":    "CRITICAL",
    "license.tampering_detected": "CRITICAL",
    # SOB key-material permission tamper + license quota-gate fail-open (R1 review)
    "license.sob_mode_error":     "WARNING",
    "compute.quota_gate_degraded": "WARNING",
    # ADR-0057 — Operator Declaration Gate (Art. 28-30)
    "operator.declaration_verified": "INFO",
    # ADR-0057 — Content Marking (Art. 50 §4)
    "content_marking.applied":    "INFO",
    # Bridge events missing from earlier revisions
    "bridge.reset_prewarned":         "WARNING",
    "bridge.budget_rejected":         "WARNING",
    "bridge.engine_policy_denied":    "WARNING",
}


def audit_event(
    event_type: str,
    *,
    channel: str = "",
    chat_key: str = "",
    user: str = "",
    persona: str = "",
    tool: str = "",
    details: dict[str, Any] | None = None,
    severity: str | None = None,
    tenant_id: str = "",
    **extra: Any,
) -> None:
    """Append a voice bridge audit event to the chain. Silent on failure.

    severity, when provided by the caller, overrides the
    _VOICE_EVENT_SEVERITY registry entry; otherwise the registry default
    (or INFO) applies. Accepts upper- or lower-case ("WARNING"/"warning").

    tenant_id is included in the chain record's details block when provided.
    It is never required — omit for events that don't carry tenant context
    (standalone bridge mode, boot events, etc.).

    ``**extra`` domain fields (e.g. ``tier=``, ``jti=``, ``reason=``) are folded
    into ``details`` and pass through the metadata-only floor. This is a
    structural defense against the recurring bad-kwarg-drop class: a caller that
    forwards a domain kwarg directly would otherwise raise TypeError, which the
    caller's surrounding ``except`` swallows — silently DROPPING a security/audit
    event (GDPR Art. 30/32). Folding extras means such a call still records the
    event instead of losing it. Prefer ``details=`` explicitly in new code; pass
    a reason CODE (not ``str(exc)``) to keep paths/PII out of the chain.
    """
    if _se is None:
        return  # forge not installed → audit silently disabled
    path = audit_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        return
    # Reserved keys that must come from positional args, never from details.
    # Strip them from caller-supplied details to prevent silent overwrites or
    # tenant_id injection when the positional arg is falsy.
    _RESERVED = frozenset({"channel", "chat_key", "user", "persona", "tenant_id"})
    # Build audit body: caller-supplied details first (minus reserved keys),
    # then reserved fields so they can never be overwritten by a caller.
    body: dict[str, Any] = {}
    if details:
        body.update({k: v for k, v in details.items() if k not in _RESERVED})
    # Fold caller-forwarded domain kwargs (the bad-kwarg-drop defense). Reserved
    # keys are still stripped; explicit `details` wins over an extra of the same
    # name. The metadata-only floor (write_event) sanitises everything below.
    if extra:
        body.update({k: v for k, v in extra.items()
                     if k not in _RESERVED and k not in body})
    body.update({
        "channel":  channel,
        "chat_key": chat_key,
        "user":     user,
        "persona":  persona,
    })
    # tenant_id enriches the chain for multi-tenant forensics without
    # changing the chain character (metadata only, no PII).
    if tenant_id:
        body["tenant_id"] = tenant_id
    effective_severity = (severity.upper() if severity else None) or _VOICE_EVENT_SEVERITY.get(event_type) or "INFO"
    core_write_committed = False
    try:
        if tenant_id:
            from forge.tenants import current_tenant
            if tenant_id != current_tenant():
                raise PermissionError(f"tenant_id {tenant_id!r} does not match context tenant")
        _se.write_event(
            path, event_type,
            severity=effective_severity,
            tool=tool, run_id="",
            details=body, hash_chain=True,
        )
        core_write_committed = True
    except OSError:
        # I/O resilience contract: a write-protected / full fs must never
        # crash the bridge. Prefer silence + missing entry over a crash-loop.
        pass
    except Exception:  # noqa: BLE001
        # A NON-IO failure here is a logic/serialization bug (or a value that
        # slipped the floor), NOT an fs condition — swallowing it silently
        # would mask a security event being dropped. Surface it best-effort
        # (no PII: type only) so suppressed events are observable, then
        # continue (still never crash the bridge).
        try:
            import logging as _logging
            _logging.getLogger(__name__).error(
                "audit_event(%s): dropped on non-IO error %s",
                event_type, sys.exc_info()[0].__name__,
            )
        except Exception:  # noqa: BLE001
            pass

    # Secondary sink, OUTSIDE the core write's try/except (ADR-0233 D4).
    # Keeping it inside meant a leak from fanout() was reported as
    # "audit_event(...): dropped on non-IO error" even though the core record had
    # already committed — a false compliance alarm on a healthy chain. The sink
    # only ever sees an event whose core write actually succeeded.
    if core_write_committed and _audit_sink is not None:
        try:
            _audit_sink.fanout(
                event_type, body,
                severity=effective_severity,
                tenant_id=tenant_id or "_default",
            )
        except Exception:  # noqa: BLE001 - a sink can never affect this call site
            pass


def verify_audit(path: Path | None = None) -> tuple[bool, list[dict]]:
    """Verify the bridge audit log's hash chain. (True, []) when forge
    is missing — there's nothing to verify, no chain exists."""
    if _se is None:
        return True, []
    target = path if path is not None else audit_path()
    return _se.verify_chain(target)


def audit_health_check(path: Path | None = None) -> tuple[bool, int]:
    """Boot-time integrity check: verify the chain and emit a CRITICAL
    ``audit.chain_gap_detected`` event when verify_chain finds tampered
    or broken records.

    The gap event is written **without** hash_chain so a broken chain
    can still record its own gap — otherwise the very mechanism we use
    to record the corruption would itself depend on the corruption
    being absent.

    Returns ``(ok, problem_count)``. Silent on filesystem errors so the
    bridge never crashes on a read-only fs.
    """
    if _se is None:
        return True, 0
    target = path if path is not None else audit_path()
    try:
        # Acquire a shared read-lock before verify_chain reads the file so that
        # a concurrent write_event() (which holds an exclusive lock) cannot
        # produce a partial-read TOCTOU race that yields a spurious
        # chain-broken verdict. Reuses security_events.py's own
        # _lock_chain/_unlock_chain (real msvcrt-based locking on Windows,
        # not the inert fcntl shim — see that module for why the shim is
        # wrong for this specific cross-process case) rather than a second,
        # POSIX-only implementation here.
        if target.exists():
            with target.open("r") as _lf:
                _se._lock_chain(_lf, shared=True)
                ok, problems = _se.verify_chain(target)
                _se._unlock_chain(_lf)
        else:
            ok, problems = True, []
    except Exception as exc:
        import logging as _logging
        _logging.getLogger(__name__).warning(
            "audit_health_check: verify_chain raised — chain unverifiable: %s", exc
        )
        # R3 finding: a verify_chain CRASH is itself a chain-health failure that
        # must leave a tamper-evident trail, just like a detected gap below.
        # Emit the out-of-band CRITICAL event (hash_chain=False) before returning.
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            _se.write_event(
                target, "audit.chain_gap_detected",
                severity="CRITICAL", tool="", run_id="",
                details={"problem_count": 1, "issue": "verify_errored",
                         "reason": type(exc).__name__},
                hash_chain=False,
            )
        except Exception:  # noqa: BLE001
            pass
        return False, 1
    if ok:
        return True, 0
    # Emit out-of-band gap event. We deliberately bypass hash_chain so
    # the gap record itself doesn't depend on the broken predecessor.
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        problem_summary = [
            {"line": p.get("line"), "issue": p.get("issue")}
            for p in problems[:20]
        ]
        _se.write_event(
            target, "audit.chain_gap_detected",
            severity="CRITICAL",
            tool="", run_id="",
            details={
                "problem_count": len(problems),
                "first_problems": problem_summary,
            },
            hash_chain=False,
        )
    except Exception:
        pass
    return False, len(problems)
