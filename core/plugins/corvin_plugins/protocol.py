"""CorvinPlugin protocol and shared lifecycle types (ADR-0030, ADR-0033)."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Protocol, runtime_checkable

# ── Shared data types ─────────────────────────────────────────────────────────

@dataclass
class HealthStatus:
    ok: bool
    message: str = ""
    details: dict = field(default_factory=dict)


@dataclass
class PluginContext:
    plugin_id: str
    tenant_id: str
    corvin_home: Path
    config: dict
    audit_emit: Callable[[str, dict], None]
    compute_registry: Any | None = None        # corvin_compute.ComputeEngineRegistry
    engine_factory: Any | None = None          # adapter engine_factory
    channel_registry: Any | None = None        # bridge channel_registry
    # ADR-0033 provider registries
    notification_registry: Any | None = None   # providers.notification_backend._registry
    recall_registry: Any | None = None         # providers.recall_backend._registry
    summary_registry: Any | None = None        # providers.summary_provider._registry
    router_registry: Any | None = None         # providers.router_backend._registry
    # ADR-0233 additive-backend registries
    audit_registry: Any | None = None          # providers.audit_backend._registry
    user_registry: Any | None = None           # providers.user_backend._registry
    # Handles for the two types that were in KNOWN_PLUGIN_TYPES with nowhere to
    # register: an stt_provider / data_connector plugin could implement on_load()
    # and had no registry to hand itself to.
    stt_registry: Any | None = None            # providers.stt_provider._registry
    data_connector_registry: Any | None = None  # providers.data_connector._registry
    extra: dict = field(default_factory=dict)


# ── Plugin protocol ───────────────────────────────────────────────────────────

@runtime_checkable
class CorvinPlugin(Protocol):
    """Lifecycle protocol for all Corvin extension points (ADR-0030).

    A plugin implements BOTH CorvinPlugin (lifecycle) AND the capability-specific
    protocol for its ``plugin_type``.  on_load() self-registers with that
    capability registry via ctx handles.
    """

    plugin_id: str      # globally unique; matches tenant config key
    plugin_type: str    # one of KNOWN_PLUGIN_TYPES
    version: str        # semver string, e.g. "1.0.0"
    display_name: str   # shown in health dashboard and logs

    def on_load(self, ctx: PluginContext) -> None:
        """Called once after discovery.  Self-register with the capability
        registry here."""
        ...

    def on_unload(self) -> None:
        """Called on graceful shutdown or tenant hot-reload."""
        ...

    def health_check(self) -> HealthStatus:
        """Called periodically.  Must not block for more than 2 s."""
        ...


# ── Provider protocols (ADR-0033) ─────────────────────────────────────────────

@runtime_checkable
class NotificationBackend(Protocol):
    """Deliver an event notification to an external system (ADR-0033).

    Must be non-blocking (fire-and-forget or async-queued).
    Must NOT block the calling thread for more than 100 ms.
    Must NOT put message content or PII in payload — metadata only.
    """

    def notify(
        self,
        event: str,
        payload: dict,
        *,
        tenant_id: str = "_default",
        severity: str = "info",
    ) -> None: ...


@runtime_checkable
class RecallBackend(Protocol):
    """Store and retrieve PII-redacted conversation turns (ADR-0033).

    Signature mirrors conversation_recall.py so SqliteRecallBackend can
    delegate 1-to-1 and third-party backends get the full context they need.

    index_turn(): user_text + assistant_text MUST already be PII-redacted
    by the caller.  Returns an indexing-metadata dict (never raises).
    recall(): full-text / semantic search; returns list of turn dicts.
    forget(): GDPR Art. 17 erasure; returns row count deleted.
    """

    def index_turn(
        self,
        channel: str,
        chat_key: str,
        *,
        user_text: str,
        assistant_text: str,
        msg_id: str = "",
        persona: str = "",
        ts: float | None = None,
        run_id: str = "",
        tenant_id: str | None = None,
    ) -> dict: ...

    def recall(
        self,
        query: str,
        *,
        channel: str | None = None,
        chat_key: str | None = None,
        since: float | None = None,
        until: float | None = None,
        limit: int = 20,
        caller_persona: str = "",
        tenant_id: str | None = None,
    ) -> list[dict]: ...

    def forget(
        self,
        *,
        channel: str | None = None,
        chat_key: str | None = None,
        before_ts: float | None = None,
        tenant_id: str | None = None,
    ) -> int: ...


@runtime_checkable
class SummaryProvider(Protocol):
    """Summarize a long assistant reply into a TTS-friendly spoken form (ADR-0033).

    Must NOT raise — return a truncated fallback instead.
    Must NOT import the anthropic SDK directly.
    """

    def summarize(
        self,
        text: str,
        *,
        lang: str = "de",
        max_chars: int = 400,
        tenant_id: str = "_default",
    ) -> str: ...


@runtime_checkable
class RouterBackend(Protocol):
    """Select a persona for an incoming user message (ADR-0033).

    Returns {"persona": str, "confidence": float, "why": str} on match,
    None on no-match or any error.  Must NOT raise.
    Signature mirrors router.route() so ChainRouterBackend can delegate 1-to-1.
    """

    def route(
        self,
        text: str,
        personas: list[dict],
        *,
        model: str = "",
        min_confidence: float = 0.5,
        timeout: float = 12.0,
        mode: str = "heuristic",
        tenant_id: str = "_default",
    ) -> dict | None: ...


@runtime_checkable
class AuditBackend(Protocol):
    """Receive a COPY of an audit event for fan-out to an external sink (ADR-0233).

    **This protocol never owns the audit trail.**  Core writes every event to its
    own hash-chained ``audit.jsonl`` first and unconditionally (GDPR Art. 30/32,
    ADR-0232); a backend is a secondary sink that may forward the same event to
    Postgres, S3, a SIEM, whatever.  Consequences of that ordering, all
    load-bearing:

    * A backend CANNOT suppress, rewrite, reorder or delay a core record.  By the
      time ``fanout()`` is called the core write has already committed.
    * ``fanout()`` MUST NOT raise.  A raising or slow backend is logged (exception
      class name only) and dropped — it never fails the caller and never fails
      core.  Do not "improve" this into a retry queue inside the caller's thread.
    * ``fanout()`` receives metadata that already passed the core write_event
      floor.  A backend MUST NOT enrich it with PII, prompts or user content.
    * ``verify_chain()`` reports on the BACKEND's own copy, if it keeps one.  It is
      never consulted to decide whether the core chain is intact — that is
      ``core/compliance/tripwire.py``'s job against the core writer.
    """

    def fanout(
        self,
        event_type: str,
        details: dict,
        *,
        severity: str = "INFO",
        tenant_id: str = "_default",
    ) -> None: ...

    def verify_chain(self) -> HealthStatus: ...

    def enforce_retention(self, max_age_days: int, *, tenant_id: str = "_default") -> dict: ...


@runtime_checkable
class UserBackend(Protocol):
    """Authenticate and describe users from an external directory (ADR-0233).

    **Deny is the only safe failure.**  ``authenticate()`` returns a dict on
    success and ``None`` on failure; an exception, a timeout and ``None`` all mean
    *deny*.  A caller MUST NOT translate any of them into a guest session, an
    anonymous admit, or a cached-credential admit (CLAUDE.md: no auto-admit, no
    trusted-observer allowlist).

    Note the registry deliberately has **no default backend**:
    ``providers.user_backend.get_active()`` returns ``None`` when no plugin is
    installed, meaning "core auth is responsible".  A default that denied
    everything would lock out every install the moment a call site appeared; a
    default that admitted anything would be an auth bypass.  Absence is the
    honest third state.

    Returned dicts carry ``user_id`` and ``roles``; they MUST NOT carry secrets
    (no password hashes, no tokens) — the caller may log the shape.
    """

    async def authenticate(self, credentials: dict) -> dict | None: ...

    async def get_user(self, user_id: str) -> dict | None: ...

    async def list_users(self) -> list[dict]: ...

    async def enforce_quota(self, user_id: str, resource: str) -> None: ...


# ── Exceptions ────────────────────────────────────────────────────────────────

class PluginAlreadyRegistered(ValueError):
    """Raised when a plugin_id collision is detected at register time."""


class PluginNotFound(KeyError):
    """Raised when a plugin_id lookup finds no registered plugin."""


class PluginDisableRefused(PermissionError):
    """Raised when an operator-initiated disable targets a non-disableable plugin.

    ADR-0243: the compliance boot layer is not switchable.  This is deliberately an
    exception and not a ``False`` return — a caller that ignores a boolean would
    report "disabled" in the Console while the plugin kept running, which is the
    worse of the two failure modes for a compliance mechanism.
    """


class PluginReplacementRefused(PermissionError):
    """Raised when a replacement targets something that may not be replaced.

    Only ``boot_layer=core`` reference implementations are replaceable.  Naming a
    compliance plugin, a bundled plugin or an unknown id in ``replaces`` is a
    refusal, never a silent no-op that leaves both plugins loaded.
    """


# ── Known plugin types ────────────────────────────────────────────────────────

KNOWN_PLUGIN_TYPES: frozenset[str] = frozenset({
    # ADR-0030 (original six)
    "compute_engine",        # L25 — ComputeEngine   (ADR-0029)
    "worker_engine",         # L22 — WorkerEngine    (ADR-0022)
    "bridge_channel",        # Bridge — BridgeChannel
    "stt_provider",          # L23 — STTProvider
    "data_connector",        # L24 — DataConnector
    "audit_backend",         # L16 — AuditBackend
    # ADR-0033 (four new provider abstractions)
    "notification_backend",  # L3+  — NotificationBackend
    "recall_backend",        # L28  — RecallBackend
    "summary_provider",      # L11  — SummaryProvider
    "router_backend",        # L5   — RouterBackend
    # ADR-0233 (additive backends; core keeps its own guarantees)
    "user_backend",          # L18-21 — UserBackend
})
