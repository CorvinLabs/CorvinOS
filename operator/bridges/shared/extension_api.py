"""ADR-0142 M1 — Layer Extension API (public hook contract).

This module defines the three public types an extension author imports to
write a CorvinOS layer extension hook:

    ExtensionHook   — abstract base class; subclass and implement ``handle``
    HookResult      — allow/deny verdict returned by a hook
    HookContext     — per-call context (tenant/session/persona + audit/metrics)

Public import path
==================
The ADR text uses ``from corvin.extension import ...`` as the conceptual
name. CorvinOS has **no** importable top-level ``corvin`` package today
(``operator/`` is deliberately not packaged because it shadows the stdlib
``operator`` module), so the real, supported import path mirrors the existing
``engine_trust`` convention: callers insert ``operator/bridges/shared`` on
``sys.path`` and ``import extension_api`` (and ``import extension_registry``).
See ``docs/claude-ref`` for the documented seam.

Constraints (load-bearing — see CLAUDE.md / ADR-0142):
  * NO ``import anthropic`` anywhere in this module (CI AST lint enforces).
  * Extensions NEVER write to ``audit.jsonl`` directly — only via
    ``ctx.audit_write()`` which routes through the L16 hash chain via
    ``forge.security_events.write_event``.
  * The ext.* audit allow-list is enforced here: only ``name``, ``version``,
    ``scope``, ``event_type``, ``hook``, ``reason`` survive — never hook
    input/output content.
"""
from __future__ import annotations

import os
import sys
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

# ── ext.* audit allow-list (ADR-0142) ───────────────────────────────────────
# These are the ONLY field names an ext.* audit event may carry in `details`.
# Everything else is dropped before the write reaches the L16 chain. Hook
# input / output content must NEVER appear here.
EXT_AUDIT_ALLOWED_FIELDS: frozenset[str] = frozenset({
    "name", "version", "scope", "event_type", "hook", "reason",
})

# Canonical ext.* lifecycle + runtime audit events (severity registered in
# forge.security_events.EVENT_SEVERITY by extension_registry import side-effect,
# but listed here as the single source of truth for the event names).
EXT_AUDIT_EVENTS: frozenset[str] = frozenset({
    "ext.installed",
    "ext.removed",
    "ext.enabled",
    "ext.disabled",
    "ext.hook_denied",
    "ext.load_failed",
    "ext.core_namespace_rejected",
})


def _resolve_audit_writer():
    """Return ``(write_event, audit_path_fn)`` from the forge plugin, or
    ``(None, None)`` when forge is not importable (standalone mode).

    Mirrors operator/bridges/shared/audit.py exactly: insert operator/forge
    on sys.path then import forge.security_events. The audit-chain path is the
    unified, scope-independent chain at ``<corvin_home>/global/forge/audit.jsonl``
    (env override ``VOICE_AUDIT_PATH`` / ``FORGE_ROOT`` honoured by audit.py).
    """
    try:
        # audit.py owns the canonical path-resolution + forge bootstrap; reuse
        # it so the ext.* events land on the SAME unified chain as everything
        # else (single voice-audit verify covers them).
        try:
            from . import audit as _audit  # type: ignore
        except ImportError:
            sys.path.insert(0, str(Path(__file__).resolve().parent))
            import audit as _audit  # type: ignore
        if _audit._se is None:  # forge missing → audit disabled
            return None, None
        return _audit._se.write_event, _audit.audit_path
    except Exception:
        return None, None


class HookResult:
    """Verdict returned by an extension hook.

    Deny-wins model (ADR-0142): a hook may ``deny(reason)`` to BLOCK an
    action or ``allow()`` to pass. A hook can NEVER un-deny a prior deny —
    the registry pipeline combines results so the final verdict is deny if
    ANY hook denied.
    """

    __slots__ = ("_is_deny", "_reason")

    def __init__(self, is_deny: bool, reason: str = "") -> None:
        self._is_deny = bool(is_deny)
        self._reason = str(reason or "")

    @classmethod
    def allow(cls) -> "HookResult":
        """Pass — this hook raises no objection."""
        return cls(False, "")

    @classmethod
    def deny(cls, reason: str) -> "HookResult":
        """Block the action. ``reason`` is a short reason code/string; it is
        surfaced to the audit chain (ext.hook_denied) and must NOT carry hook
        input/output content."""
        return cls(True, reason or "denied")

    @property
    def is_deny(self) -> bool:
        return self._is_deny

    @property
    def is_allow(self) -> bool:
        return not self._is_deny

    @property
    def reason(self) -> str:
        return self._reason

    def __repr__(self) -> str:  # pragma: no cover — diagnostic only
        return f"HookResult(deny={self._is_deny}, reason={self._reason!r})"


class _NoopMetrics:
    """No-op metrics placeholder (ADR-0142 ``ctx.metrics``).

    Extensions may emit Prometheus metrics via ``ctx.metrics`` in a future
    milestone. Until the real registry is wired, every call is a safe no-op so
    extension code that uses ``ctx.metrics.inc(...)`` never crashes whether or
    not a metrics backend is present.
    """

    __slots__ = ()

    def inc(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    def observe(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    def set(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    def gauge(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    def __getattr__(self, _name: str):
        # Any unknown metric verb resolves to a no-op callable, so extensions
        # cannot break by calling a metric method we don't model yet.
        def _noop(*_a: Any, **_k: Any) -> None:
            return None
        return _noop


class HookContext:
    """Per-call context handed to every extension hook.

    Carries the ADR-0007 scope identity (tenant_id, session_id, channel,
    chat_key, persona) plus the extension's own merged ``config`` dict. Provides
    ``audit_write(event, details)`` — the ONLY sanctioned path for an extension
    to reach the L16 audit chain — and a no-op-safe ``metrics`` placeholder.
    """

    def __init__(
        self,
        *,
        tenant_id: str = "_default",
        session_id: str = "",
        channel: str = "",
        chat_key: str = "",
        persona: str = "",
        config: dict[str, Any] | None = None,
        ext_name: str = "",
        ext_version: str = "",
        ext_scope: str = "",
        audit_writer=None,
        audit_path_fn=None,
    ) -> None:
        self.tenant_id = tenant_id or "_default"
        self.session_id = session_id or ""
        self.channel = channel or ""
        self.chat_key = chat_key or ""
        self.persona = persona or ""
        self.config: dict[str, Any] = dict(config or {})
        # Identity of the owning extension — folded into every ext.* audit
        # event so the chain attributes the event to the right layer.
        self.ext_name = ext_name or ""
        self.ext_version = ext_version or ""
        self.ext_scope = ext_scope or ""
        self.metrics = _NoopMetrics()
        # Audit seam: injectable for tests; defaults to the forge-backed writer.
        if audit_writer is None or audit_path_fn is None:
            _w, _p = _resolve_audit_writer()
            self._audit_writer = audit_writer or _w
            self._audit_path_fn = audit_path_fn or _p
        else:
            self._audit_writer = audit_writer
            self._audit_path_fn = audit_path_fn

    # ── audit ───────────────────────────────────────────────────────────────
    def audit_write(self, event: str, details: dict[str, Any] | None = None) -> bool:
        """Emit an ext.* audit event to the L16 hash chain.

        The ext.* allow-list (``name``, ``version``, ``scope``, ``event_type``,
        ``hook``, ``reason``) is enforced HERE before the write — any other
        field is silently dropped so a buggy or hostile extension can never
        smuggle hook input/output content into the permanent chain. The
        extension's own identity (name/version/scope) is always injected.

        Returns True on a best-effort successful write, False when the audit
        backend is unavailable (standalone mode) or the write failed. Never
        raises — audit is best-effort and must not crash a hook.
        """
        if self._audit_writer is None or self._audit_path_fn is None:
            return False
        body = _filter_ext_details(details, event_type=event)
        # Always attribute to the owning extension (identity, not content).
        if self.ext_name and "name" not in body:
            body["name"] = self.ext_name
        if self.ext_version and "version" not in body:
            body["version"] = self.ext_version
        if self.ext_scope and "scope" not in body:
            body["scope"] = self.ext_scope
        try:
            path = self._audit_path_fn()
            try:
                Path(path).parent.mkdir(parents=True, exist_ok=True)
            except OSError:
                return False
            self._audit_writer(
                path, event,
                severity=None, tool="", run_id="",
                details=body, hash_chain=True,
            )
            return True
        except Exception:
            # Best-effort: a write-protected fs / serialization edge must never
            # crash the hook pipeline.
            return False


def _filter_ext_details(
    details: dict[str, Any] | None, *, event_type: str = "",
) -> dict[str, Any]:
    """Strip everything outside the ext.* audit allow-list.

    Defence-in-depth: this is the FIRST gate (before the forge.security_events
    M1/M2 floor). It guarantees the ext.* chain never carries hook payload
    content even if the registry-side positive allow-list registration is
    missing or bypassed.
    """
    if not details:
        return {}
    out: dict[str, Any] = {}
    for k, v in details.items():
        if k in EXT_AUDIT_ALLOWED_FIELDS:
            out[str(k)] = v
    return out


class ExtensionHook(ABC):
    """Abstract base class for an extension hook.

    Subclass and implement :meth:`handle`. The registry instantiates the hook
    once and calls ``handle`` for every matching event. ``handle`` must return
    a :class:`HookResult` — ``HookResult.allow()`` to pass or
    ``HookResult.deny(reason)`` to block.

    Optional class attribute ``priority`` (int, default 0) sets ordering within
    the extension band: higher runs earlier; core hooks are always priority 0
    and always run first. The manifest hook entry's ``priority`` overrides the
    class attribute.
    """

    #: Default ordering hint; manifest entry priority overrides this.
    priority: int = 0

    @abstractmethod
    def handle(
        self, tool_name: str, tool_input: dict, ctx: HookContext,
    ) -> HookResult:
        """Decide whether the action may proceed.

        Args:
            tool_name: the tool being invoked (PreToolUse semantics).
            tool_input: the tool's input dict.
            ctx: per-call :class:`HookContext`.

        Returns:
            :class:`HookResult` — allow() to pass, deny(reason) to block.
        """
        raise NotImplementedError
