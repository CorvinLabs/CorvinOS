"""Phase 1 Skills Registry: Audit-first, feature-flag replacement Skills.

This module implements the core Skills registry for Phase 1 big bang feature flags refactoring.

Architecture:
- SkillsRegistry: central registry for builtin/os-skills
- Skill: abstract base for all executable skills
- SkillExecutionResult: audit-ready result format
- CoreAuditBackend: adapter onto the hash-chained core audit writer
- LearningEmitterBackend: adapter onto the ADR-0314 EventEmitter
- A2A-ready: all executions can be invoked via A2A messaging

Compliance:
- GDPR Art. 30: All executions logged to audit trail
- GDPR Art. 32: Execution results immutable, PII-scrubbed
- EU AI Act Art. 50: LoM binding in every execution (ADR-0537)
- ADR-0544: Phase 1 big bang feature flags refactoring

Execution model (adversarial review 2026-09-03):
- ``execute()`` runs the Skill on a dedicated daemon thread and joins it with the
  requested timeout. The previous ``asyncio.run()`` raised ``RuntimeError`` from
  inside every ``async def`` route (the console capabilities route, the vibe
  pipeline route) and its ``wait_for`` could never interrupt a synchronous
  ``Skill.execute`` — the timeout was decorative. A thread join works from sync
  and async callers alike; a Skill that overruns is reported as ``timeout`` and
  abandoned (daemon thread — it cannot block interpreter exit).
"""

from __future__ import annotations

import ast
import hashlib
import json
import logging
import re
import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from threading import Lock
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

# Repo root, derived — never hardcoded. core/skills/skill_registry_phase1.py → parents[2]
_REPO_ROOT = Path(__file__).resolve().parents[2]

# LoM source admissibility (ADR-0537). A LoM names CorvinOS SOURCE — not data,
# not a vendored dependency, and not the runtime-writable state tree. Anything
# under these path segments is refused before the file is read, so a LoM can
# never bind to a file an attacker (or the running system itself) can rewrite.
_LOM_EXCLUDED_PARTS = frozenset({
    ".corvin",          # runtime-writable tenant state
    ".claude",          # agent worktrees/state the running system rewrites —
                        # `.claude/worktrees/` holds full .py-bearing copies of
                        # the repo INSIDE _REPO_ROOT, so a LoM naming one used
                        # to bind and yield a normal-looking source hash, i.e.
                        # an audited decision attributed to source that is not
                        # the shipped source (round-4 review, F8).
    ".venv", "venv",    # vendored interpreters (core/console/.venv/**)
    "site-packages", "dist-packages",
    "node_modules",
    ".git",
})
_LOM_MAX_SOURCE_BYTES = 2 * 1024 * 1024  # a 2 MB .py is not a LoM target

# ``_compute_lom_hash`` reads AND ``ast.parse``s the named source; execute()
# calls it twice per execution (the gate, then the result). Measured 3.4–79.6 ms
# per call on real call sites, i.e. up to 160 ms of pure re-parsing per Skill
# execution. Memoised on (lom, path, st_mtime_ns, st_size): an edit to the named
# file changes mtime/size and invalidates the entry, so the hash still binds to
# the source as it is on disk NOW.
_LOM_HASH_CACHE: Dict[tuple, Optional[str]] = {}
_LOM_HASH_CACHE_LOCK = Lock()
_LOM_HASH_CACHE_MAX = 1024

# PII Patterns (GDPR Art. 32 redaction, FIX #8: Enhanced domain-specific patterns)
# Applied to string VALUES.
_PII_PATTERNS = {
    "password": re.compile(r"(password|passwd|pwd)\s*[:=]\s*\S+", re.IGNORECASE),
    "api_key": re.compile(r"(api[_-]?key|token|secret)\s*[:=]\s*\S+", re.IGNORECASE),
    "email": re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b"),
    "credit_card": re.compile(r"\b\d{4}[- ]?\d{4}[- ]?\d{4}[- ]?\d{4}\b"),
    "ssn": re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    # FIX #8: Domain-specific patterns
    "aws_access_key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "github_token": re.compile(r"\b(ghp_|ghu_|ghs_|ghr_)[A-Za-z0-9_]{36,255}\b"),
    "openai_style_key": re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b"),
    "jwt": re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"),
    "bearer": re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{16,}", re.IGNORECASE),
    "phone_number": re.compile(r"\b(?:\+?1[-.\s]?)?\(?[0-9]{3}\)?[-.\s]?[0-9]{3}[-.\s]?[0-9]{4}\b"),
}

# Applied to dict KEYS: a key that *names* a secret/PII field is redacted whole,
# regardless of the value shape. The previous key check reused the value
# patterns, which require a trailing ``:`` / ``=`` — so ``{"password": "x"}``
# and ``{"api_key": "sk-..."}`` passed straight into the audit chain.
# Segment-anchored so ``input_tokens`` / ``attention_budget`` are NOT redacted.
_PII_KEY_PATTERN = re.compile(
    r"(?:^|[_\-.])(password|passwd|pwd|secret|token|api_?key|apikey|access_?key|"
    r"private_?key|ssn|credit_?card|card_?number|e-?mail|phone|iban|authorization|"
    r"cookie|session_?id)(?:$|[_\-.])",
    re.IGNORECASE,
)

_REDACTED = "[REDACTED_PII]"
_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")


def _key_is_sensitive(key: str) -> bool:
    """Segment-anchored match on snake_case AND camelCase keys (``apiKey``, ``userEmail``)."""
    return bool(_PII_KEY_PATTERN.search(_CAMEL_BOUNDARY.sub("_", key)))


class SkillOrigin(str, Enum):
    """Where a Skill comes from."""
    BUILTIN = "builtin"  # Part of CorvinOS core
    VETTED = "vetted"    # Reviewed & signed by Corvin team
    COMMUNITY = "community"  # User-contributed


class SkillTier(str, Enum):
    """Disableability tier — the Skills twin of the plugin ``boot_layer`` axis.

    ``compliance`` Skills have NO off switch: ``unregister()`` refuses, the
    three-failure auto-disable refuses, and there is no env var or flag that
    changes that (CLAUDE.md § Plugin-Based Isolation — "Security/compliance
    mechanism: always on, never toggleable"). Until 2026-09-07 three timed-out
    ``os.capabilities`` calls disabled the capability manifest for the tenant
    and every flag-gated console panel vanished (adversarial review F-K3).
    """
    COMPLIANCE = "compliance"
    CORE = "core"
    INSTALLED = "installed"


class SkillDisableRefused(RuntimeError):
    """A compliance-tier Skill was asked to go away; it did not (audited)."""


@dataclass(frozen=True)
class SkillMetadata:
    """Skill identification + versioning."""
    id: str  # e.g., "os.vibe_engineering"
    name: str
    description: str
    version: str  # semver
    origin: SkillOrigin
    owner: str
    tags: List[str] = field(default_factory=list)
    #: Whether executions feed the ADR-0314 learning store. Every execution is
    #: still AUDITED (compliance is not optional); ``learn=False`` is for
    #: deterministic flag/manifest lookups (``os.capabilities`` ran 346× in ten
    #: minutes of console polling, doubling the chain and filling the learning
    #: store with events no optimizer can use — adversarial review F31).
    learn: bool = True
    #: See :class:`SkillTier`. ``compliance`` cannot be unregistered or disabled.
    tier: SkillTier = SkillTier.CORE


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class SkillExecutionResult:
    """Result of a Skill execution (audit-ready, immutable).

    Attributes:
        skill_id: Which Skill was executed
        status: "success" | "failure" | "timeout" | "error"
        output: Return value (None if failed)
        execution_time_ms: Wall-clock time
        error_message: If status != "success"
        timestamp: ISO8601 execution time (UTC, tz-aware)
        lom: Line of moral responsibility (source code location)
        lom_hash: SHA256 of source code at LoM
        tenant_id: Tenant scope for audit isolation
    """
    skill_id: str
    status: str  # "success", "failure", "timeout", "error"
    output: Optional[Any] = None
    execution_time_ms: float = 0.0
    error_message: Optional[str] = None
    timestamp: str = field(default_factory=_utc_now_iso)
    lom: Optional[str] = None  # Line of moral responsibility
    lom_hash: Optional[str] = None  # SHA256 of source
    tenant_id: str = "_default"

    def to_audit_event(self) -> Dict[str, Any]:
        """Convert to audit trail event format.

        The raw ``output`` is deliberately NOT part of the audit record: the
        core writer's denylist floor (``forge.security_events``) drops any
        ``output`` key, so until 2026-09-07 every ``skill.executed`` chain
        record carried ``lom: null`` and no decision at all — an execution was
        provable, its DECISION was not (adversarial review F-K2). What goes
        into the chain is :func:`decision_summary`: an allowlisted, PII-free
        projection (engine, flag counts + hash, enabled/mode …) under keys the
        floor keeps.
        """
        return {
            "event_type": "SKILL_EXECUTED",
            "skill_id": self.skill_id,
            "status": self.status,
            "decision": decision_summary(self.output),
            "execution_time_ms": self.execution_time_ms,
            "error_message": self.error_message,
            "timestamp": self.timestamp,
            "lom": self.lom,
            "lom_hash": self.lom_hash,
            "tenant_id": self.tenant_id,
        }


#: Scalar output fields that may appear verbatim in the audit chain. Every key
#: here is chosen to survive ``forge.security_events._AUDIT_FORBIDDEN_EXACT`` /
#: ``_AUDIT_FORBIDDEN_SUBSTR`` (no "output", "message", "content", "token", …)
#: and to name a DECISION, never user content: which engine, whether a feature
#: is on, which mode, how confident. Free-text fields (``reasoning``,
#: ``task_description`` echoes) are never copied.
_DECISION_SCALAR_KEYS: tuple[str, ...] = (
    "engine",
    "bundled_engine",
    "shadow",
    "confidence",
    "confidence_threshold",
    "learned_config_version",
    "enabled",
    "enabled_source",
    "headless_enabled",
    "mode",
    "source",
    "vibe_score",
    "priority_adjustment",
)
_DECISION_MAX_STR = 64


def decision_summary(output: Any) -> Optional[Dict[str, Any]]:
    """Allowlisted, content-free projection of a Skill output for the audit chain.

    * scalar decision fields (``engine``, ``enabled``, ``mode`` …) are copied
      when they are short scalars;
    * a ``flags`` mapping (``os.capabilities``) becomes ``flag_count`` /
      ``flags_on`` / ``flags_hash`` (sha256 over the sorted flag states, 16 hex)
      so a reviewer can prove WHICH manifest was served without listing it;
    * a 3-tier context (``merged_tier``) is reduced to its engine + priority.

    Returns ``None`` for ``None`` output. Never raises.
    """
    if output is None:
        return None
    if not isinstance(output, dict):
        return {"kind": type(output).__name__}
    summary: Dict[str, Any] = {}
    for key in _DECISION_SCALAR_KEYS:
        value = output.get(key)
        if isinstance(value, bool) or isinstance(value, (int, float)):
            summary[key] = value
        elif isinstance(value, str) and 0 < len(value) <= _DECISION_MAX_STR:
            summary[key] = value
    flags = output.get("flags")
    if isinstance(flags, dict):
        states = sorted((str(k), bool(v)) for k, v in flags.items())
        summary["flag_count"] = len(states)
        summary["flags_on"] = sum(1 for _k, v in states if v)
        summary["flags_hash"] = hashlib.sha256(
            json.dumps(states, separators=(",", ":")).encode()
        ).hexdigest()[:16]
    merged = output.get("merged_tier")
    if isinstance(merged, dict):
        engine = merged.get("engine")
        if isinstance(engine, str) and 0 < len(engine) <= _DECISION_MAX_STR:
            summary["engine"] = engine
        priority = merged.get("priority")
        if isinstance(priority, (int, float)) and not isinstance(priority, bool):
            summary["priority"] = priority
        summary["injected_tier"] = output.get("injected_tier") is not None
    return summary


class Skill(ABC):
    """Abstract base class for all Skills.

    Every Skill must:
    1. Implement execute(input) -> output
    2. Provide metadata (id, version, description)
    3. Be deterministic (same input → same output)
    4. Handle errors gracefully (fail-closed)
    """

    def __init__(self, metadata: SkillMetadata):
        self.metadata = metadata

    @abstractmethod
    def execute(self, input: Dict[str, Any]) -> Any:
        """Execute the Skill.

        Args:
            input: Skill input dictionary

        Returns:
            Output value (serializable to JSON)

        Raises:
            Exception on error (caught by registry, logged to audit trail)
        """
        pass

    def __str__(self) -> str:
        return f"Skill({self.metadata.id}:v{self.metadata.version})"


#: Positive allowlists for the Skill audit events (ADR-0129 M2). The core
#: writer's key floor is DEFAULT-DENY (``forge.security_events._AUDIT_KNOWN_KEYS``);
#: an event type that registers its exact field set keeps those keys and only
#: those. Every key here is metadata: ids, a status/tier/operation code, a
#: number, a hash, the LoM label — ``decision`` is :func:`decision_summary`
#: (allowlisted scalars + counts + hash, never output content).
SKILL_AUDIT_ALLOWLISTS: Dict[str, frozenset] = {
    # ONE event type, three emitters (this registry; ``core/skills/executor.py``
    # and ``core/skills/skill_manager.py`` via ``skill_audit.emit_skill_audit``)
    # — the union of their metadata fields, registered once here.
    "skill.executed": frozenset({
        "skill_id", "status", "decision", "execution_time_ms", "error_message",
        "timestamp", "lom", "lom_hash", "tenant_id",
        "skill_version", "latency_ms", "run_id", "timeout_ms", "exc_type",
        "phase_completed", "error_class",
    }),
    "skill.auto.disabled": frozenset({"skill_id", "timestamp", "tenant_id", "failures"}),
    "skill.disable.refused": frozenset({
        "skill_id", "tier", "operation", "timestamp", "tenant_id", "failures",
    }),
    "skill.manually.enabled": frozenset({"skill_id", "timestamp", "tenant_id"}),
    "skill.manually.disabled": frozenset({"skill_id", "timestamp", "tenant_id"}),
}


def _register_skill_audit_allowlists() -> bool:
    """Fold the Skill event field sets into the core writer (idempotent)."""
    try:
        from forge.security_events import register_event_allowlist  # type: ignore[import-not-found]
    except ImportError:
        return False
    for event_type, fields in SKILL_AUDIT_ALLOWLISTS.items():
        register_event_allowlist(event_type, fields)
    return True


_register_skill_audit_allowlists()


class CoreAuditBackend:
    """Audit backend that writes Skill events into the hash-chained core audit log.

    Wraps an ``audit_emit(event_type, details)`` callable — the very same one
    ``corvin_plugins.bootstrap`` hands to plugins — so Skill decisions land in
    the same chain as plugin lifecycle events (GDPR Art. 30/32). When no callable
    is given, the core writer (``audit.audit_event``) is resolved lazily; an
    absent writer is logged, never silently ignored.
    """

    def __init__(
        self,
        tenant_id: str = "_default",
        audit_emit: Optional[Callable[[str, dict], None]] = None,
    ):
        self.tenant_id = tenant_id
        self._audit_emit = audit_emit
        self.write_failures = 0

    def _resolve_emit(self) -> Optional[Callable[[str, dict], None]]:
        if self._audit_emit is not None:
            return self._audit_emit
        try:
            from audit import audit_event  # type: ignore[import-not-found]
        except ImportError:
            return None
        _register_skill_audit_allowlists()  # the writer is importable now — bind the field sets

        tenant_id = self.tenant_id

        def emit(event_type: str, details: dict) -> None:
            audit_event(event_type, details=details, tenant_id=tenant_id)

        self._audit_emit = emit
        return emit

    def write_event(self, event: Dict[str, Any]) -> None:
        emit = self._resolve_emit()
        if emit is None:
            self.write_failures += 1
            logger.error(
                "core audit writer unavailable — skill event %s NOT chained",
                event.get("event_type"),
            )
            return
        event_type = str(event.get("event_type", "SKILL_EXECUTED")).lower().replace("_", ".")
        details = dict(event)
        details.pop("event_type", None)
        try:
            emit(event_type, details)
        except Exception as exc:  # noqa: BLE001
            self.write_failures += 1
            logger.error("skill audit emit failed (%s)", type(exc).__name__)


class LearningEmitterBackend:
    """Adapter: registry learning dicts → ``EventEmitter.emit(LearningEvent)``.

    The registry speaks one protocol only — ``emit_event(dict)``. The learning
    emitter (``core.learning.event_emitter.EventEmitter``) persists
    ``core.learning.learning_events.LearningEvent`` through ``EventStore``; this
    adapter is the single place where the two meet.
    """

    def __init__(self, emitter: Any, instance_id: str = "corvinos", session_id: str = "skills"):
        self.emitter = emitter
        self.instance_id = instance_id
        self.session_id = session_id
        self.dropped = 0

    def emit_event(self, event: Dict[str, Any]) -> bool:
        from core.learning.learning_events import EventType, LearningEvent

        signal = {
            k: v for k, v in event.items()
            if k not in ("event_type", "tenant_id", "skill_id", "lom")
        }
        signal["instance_id"] = self.instance_id
        signal["session_id"] = self.session_id
        learning_event = LearningEvent.create(
            event_type=EventType.SKILL_EXECUTED,
            skill_id=str(event.get("skill_id") or "unknown"),
            tenant_id=str(event.get("tenant_id") or "_default"),
            signal=signal,
            lom=event.get("lom"),
        )
        ok = bool(self.emitter.emit(learning_event))
        if not ok:
            self.dropped += 1
        return ok


class _ThreadResult:
    __slots__ = ("value", "exc")

    def __init__(self) -> None:
        self.value: Any = None
        self.exc: Optional[BaseException] = None


class SkillsRegistry:
    """Central registry for all executable Skills.

    Features:
    - Register/unregister Skills
    - Execute Skills with audit logging
    - Tenant-scoped isolation
    - Failure tracking + auto-disable (per skill AND tenant)
    - A2A-ready (all executions callable via A2A)

    Compliance:
    - GDPR Art. 30: Every execution logged
    - GDPR Art. 32: Immutable audit trail
    - ADR-0537: LoM binding in all events
    """

    AUTO_DISABLE_THRESHOLD = 3
    MAX_IN_FLIGHT_PER_SKILL = 8

    def __init__(
        self,
        audit_backend: Optional[Any] = None,
        tenant_id: str = "_default",
        learning_backend: Optional[Any] = None,
    ):
        """Initialize Skills registry.

        Args:
            audit_backend: Audit trail backend (implements write_event)
            tenant_id: Tenant scope for isolation (always whitelisted)
            learning_backend: Learning event backend (implements emit_event, ADR-0314)
        """
        self._skills: Dict[str, Skill] = {}
        self._metadata_by_id: Dict[str, SkillMetadata] = {}
        self.audit_backend = audit_backend
        self.learning_backend = learning_backend
        self.tenant_id = tenant_id
        # Failure counters are keyed per (skill_id, tenant_id) — a shared counter
        # let tenant A's failures disable the Skill for tenant B.
        self._failure_count: Dict[tuple, int] = {}
        self._failure_lock = Lock()  # FIX #3: Prevent TOCTOU race on failure counter
        # FIX #12: Tenant-scoped auto-disable: (skill_id, tenant_id) tuples
        self._auto_disabled: set = set()
        # Tenant isolation whitelist: the registry's own tenant is always allowed.
        self._allowed_tenants: set = {"_default", tenant_id} if tenant_id else {"_default"}
        self.learning_emit_failures = 0
        # Abandoned (timed-out) worker threads keep running until the Skill
        # returns. Without a cap, a hanging Skill called in a loop leaks one
        # thread per call — so in-flight executions are bounded per skill.
        self._in_flight: Dict[str, int] = {}
        self._in_flight_lock = Lock()

    # ── registration ────────────────────────────────────────────────────────

    def register(self, skill: Skill) -> None:
        """Register a Skill in the registry.

        Raises:
            ValueError: If Skill ID already registered
        """
        skill_id = skill.metadata.id
        if skill_id in self._skills:
            raise ValueError(f"Skill {skill_id} already registered")

        self._skills[skill_id] = skill
        self._metadata_by_id[skill_id] = skill.metadata
        logger.info(f"Registered Skill: {skill_id}:{skill.metadata.version}")

    def unregister(self, skill_id: str) -> None:
        """Unregister a Skill.

        Raises:
            SkillDisableRefused: for a ``tier=compliance`` Skill (audited as
                ``skill.disable.refused``); the Skill stays registered.
        """
        if skill_id not in self._skills:
            return
        self._refuse_if_compliance(skill_id, operation="unregister", tenant_id=self.tenant_id)
        del self._skills[skill_id]
        del self._metadata_by_id[skill_id]
        logger.info(f"Unregistered Skill: {skill_id}")

    def _tier_of(self, skill_id: str) -> SkillTier:
        meta = self._metadata_by_id.get(skill_id)
        tier = getattr(meta, "tier", SkillTier.CORE)
        try:
            return SkillTier(tier)
        except ValueError:
            return SkillTier.CORE

    def _refuse_if_compliance(self, skill_id: str, *, operation: str, tenant_id: str) -> None:
        """Audit + raise when ``skill_id`` is a compliance-tier Skill.

        One helper for every path that could remove a Skill from service
        (``unregister``, ``disable_skill``, and — without the raise — the
        auto-disable in :meth:`_track_failure`), so a future off switch cannot
        forget the check.
        """
        if self._tier_of(skill_id) is not SkillTier.COMPLIANCE:
            return
        self._audit_disable_refused(skill_id, operation=operation, tenant_id=tenant_id)
        raise SkillDisableRefused(
            f"{skill_id} is a compliance-tier Skill and cannot be {operation}d "
            f"(no off switch — CLAUDE.md § Plugin-Based Isolation)"
        )

    def _audit_disable_refused(
        self, skill_id: str, *, operation: str, tenant_id: str, failures: Optional[int] = None,
    ) -> None:
        logger.error(
            "compliance-tier Skill %s: %s REFUSED for tenant %s", skill_id, operation, tenant_id
        )
        event: Dict[str, Any] = {
            "event_type": "SKILL_DISABLE_REFUSED",
            "skill_id": skill_id,
            "tier": SkillTier.COMPLIANCE.value,
            "operation": operation,
            "timestamp": _utc_now_iso(),
            "tenant_id": tenant_id,
        }
        if failures is not None:
            event["failures"] = failures
        self._write_audit(event)

    def get(self, skill_id: str) -> Optional[Skill]:
        """Get a Skill by ID."""
        return self._skills.get(skill_id)

    def list_skills(self) -> List[SkillMetadata]:
        """List all registered Skills."""
        return list(self._metadata_by_id.values())

    def add_tenant(self, tenant_id: str) -> None:
        """Register a tenant for isolation (GDPR Art. 5, 6)."""
        if not tenant_id:
            raise ValueError("tenant_id must be non-empty")
        self._allowed_tenants.add(tenant_id)
        logger.info(f"Tenant {tenant_id} added to Skills registry whitelist")

    # ── LoM binding (ADR-0537) ───────────────────────────────────────────────

    @staticmethod
    def _resolve_lom_source(file_part: str) -> Optional[Path]:
        """The admissible source file a LoM names, or None (fail-closed).

        Admissible means: inside the repo root, a ``.py`` file that exists, not
        under a vendored/runtime-writable segment (``_LOM_EXCLUDED_PARTS``), and
        small enough to parse. Everything else is refused BEFORE the file is
        read, so ``_compute_lom_hash`` is never a hash oracle over arbitrary
        readable content.
        """
        if not file_part:
            return None
        source_path = Path(file_part)
        if not source_path.is_absolute():
            source_path = _REPO_ROOT / source_path
        try:
            source_path = source_path.resolve()
        except OSError as exc:
            logger.warning("LoM path not resolvable: %s (%s)", file_part, exc)
            return None

        # A LoM names CorvinOS source. Anything outside the repo root (``../``,
        # absolute paths) would turn this into a hash oracle over arbitrary
        # readable files — refuse.
        if not source_path.is_relative_to(_REPO_ROOT):
            logger.warning("LoM outside repo root refused: %s", source_path)
            return None
        if source_path.suffix != ".py":
            logger.warning("LoM source is not a .py file: %s", source_path)
            return None
        rel_parts = source_path.relative_to(_REPO_ROOT).parts
        if any(part in _LOM_EXCLUDED_PARTS for part in rel_parts):
            logger.warning("LoM source in a non-source tree refused: %s", source_path)
            return None
        if not source_path.is_file():
            logger.warning("LoM source file not found: %s", source_path)
            return None
        return source_path

    @staticmethod
    def _find_lom_function(
        tree: ast.AST, func_name: str
    ) -> List[Any]:
        """Every ``def``/``async def`` a LoM's function part can name.

        ``Class.method`` binds to that method inside that class; a bare name
        binds to any def with that name (module level or method). A name can be
        defined more than once in a file (an overload, a method on two classes,
        a module-level function shadowed by a method) — all matches are
        returned so the ``:L<line>`` form can pick the one containing the line.
        """
        cls_name, _, meth_name = func_name.rpartition(".")
        matches: List[Any] = []
        if cls_name:
            for cls in ast.walk(tree):
                if isinstance(cls, ast.ClassDef) and cls.name == cls_name:
                    for node in cls.body:
                        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == meth_name:
                            matches.append(node)
            return matches
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == func_name:
                matches.append(node)
        return matches

    @staticmethod
    def _compute_lom_hash(lom: Optional[str]) -> Optional[str]:
        """SHA256 binding a LoM to the source it names (ADR-0537).

        Two shapes are accepted:

        * ``file:function`` — the hash of the named function's source segment
          (resolved with ``ast``, so it survives line drift above it). This is
          the shape production call sites use.
        * ``file:function:L<line>`` / ``file:function:<line>`` — the hash of
          that one source line (``os_skills_integration._lom`` derives it from
          the live frame). The FUNCTION is resolved first and the line must fall
          inside it (decorators included): before 2026-09-07 this form returned
          ``sha256(line)`` without ever looking the function up, so a fabricated
          function name still produced a "bound" hash — and a blank line
          produced the constant ``sha256("")`` for ANY file (round-3 review,
          R3-B1). A blank/whitespace-only line is now refused too.

        Returns None when the LoM does not bind — ``execute()`` refuses such a
        LoM, so an unresolvable LoM is never indistinguishable from a real
        source hash.
        """
        if not lom:
            return None

        parts = lom.split(":")
        if len(parts) < 2:
            logger.warning("LoM %r has no function part — unresolvable", lom)
            return None

        source_path = SkillsRegistry._resolve_lom_source(parts[0])
        if source_path is None:
            return None
        try:
            stat = source_path.stat()
        except OSError as exc:
            logger.warning("LoM source not stat-able: %s (%s)", source_path, exc)
            return None
        if stat.st_size > _LOM_MAX_SOURCE_BYTES:
            logger.warning(
                "LoM source too large to bind (%d bytes > %d): %s",
                stat.st_size, _LOM_MAX_SOURCE_BYTES, source_path,
            )
            return None

        key = (lom, str(source_path), stat.st_mtime_ns, stat.st_size)
        with _LOM_HASH_CACHE_LOCK:
            if key in _LOM_HASH_CACHE:
                return _LOM_HASH_CACHE[key]

        value = SkillsRegistry._compute_lom_hash_uncached(lom, parts, source_path)

        with _LOM_HASH_CACHE_LOCK:
            if len(_LOM_HASH_CACHE) >= _LOM_HASH_CACHE_MAX:
                _LOM_HASH_CACHE.clear()  # bounded; a cold cache only costs a re-parse
            _LOM_HASH_CACHE[key] = value
        return value

    @staticmethod
    def _compute_lom_hash_uncached(
        lom: str, parts: List[str], source_path: Path
    ) -> Optional[str]:
        """The read + parse half of :meth:`_compute_lom_hash` (memoised there)."""
        try:
            func_name = parts[1].strip()
            if not func_name:
                logger.warning("LoM %r has an empty function part — unresolvable", lom)
                return None

            text = source_path.read_text(encoding="utf-8", errors="ignore")
            try:
                tree = ast.parse(text)
            except SyntaxError as exc:
                logger.warning("LoM source does not parse: %s (%s)", source_path, exc)
                return None

            matches = SkillsRegistry._find_lom_function(tree, func_name)
            if not matches:
                logger.warning(
                    "LoM function %s not found in %s — unresolvable", func_name, source_path
                )
                return None

            if len(parts) >= 3:
                line_str = parts[2].strip()
                if line_str[:1] in ("L", "l"):
                    line_str = line_str[1:]
                try:
                    line_num = int(line_str)
                except ValueError:
                    logger.warning("LoM line part %r is not a number", parts[2])
                    return None
                lines = text.split("\n")
                if line_num < 1 or line_num > len(lines):
                    logger.warning(f"LoM line {line_num} outside file length {len(lines)}")
                    return None
                # The line must lie INSIDE the named function (decorators
                # included) — otherwise the function part is decorative and any
                # fabricated name binds (R3-B1).
                inside = False
                for node in matches:
                    first = min(
                        [node.lineno] + [d.lineno for d in getattr(node, "decorator_list", [])]
                    )
                    last = node.end_lineno or node.lineno
                    if first <= line_num <= last:
                        inside = True
                        break
                if not inside:
                    logger.warning(
                        "LoM line %d is outside %s in %s — unresolvable",
                        line_num, func_name, source_path,
                    )
                    return None
                line = lines[line_num - 1]
                if not line.strip():
                    # sha256("") is the same constant for every blank line in
                    # every file — a hash that binds to nothing.
                    logger.warning(
                        "LoM line %d in %s is blank — unresolvable", line_num, source_path
                    )
                    return None
                return hashlib.sha256(line.encode()).hexdigest()

            # ``file:function`` — hash the function's source segment.
            for node in matches:
                segment = ast.get_source_segment(text, node)
                if segment:
                    return hashlib.sha256(segment.encode()).hexdigest()
            logger.warning(
                "LoM function %s in %s has no source segment — unresolvable",
                func_name, source_path,
            )
            return None

        except Exception as e:  # noqa: BLE001
            logger.warning(f"Failed to compute LoM hash for '{lom}': {e}")
            return None

    # ── tenant isolation ─────────────────────────────────────────────────────

    def _validate_tenant_id(self, tenant_id: Optional[str]) -> bool:
        """Validate tenant_id is in whitelist (fail-closed GDPR enforcement)."""
        if not tenant_id or not isinstance(tenant_id, str) or tenant_id not in self._allowed_tenants:
            logger.warning(f"Tenant isolation violation: {tenant_id!r} not in whitelist")
            return False
        return True

    # ── PII scrubbing (GDPR Art. 32) ─────────────────────────────────────────

    @staticmethod
    def _scrub_string(value: str) -> str:
        for pattern in _PII_PATTERNS.values():
            value = pattern.sub(_REDACTED, value)
        return value

    @staticmethod
    def _scrub_pii_from_output(output: Any, visited: Optional[set] = None, depth: int = 0) -> Any:
        """Redact PII from Skill output (dict keys + string values, circular-safe)."""
        if depth > 100:  # FIX #2: Depth limit for DoS protection
            return "[REDACTED_DEEP_NESTING]"

        if visited is None:
            visited = set()

        obj_id = id(output)
        if isinstance(output, (dict, list)) and obj_id in visited:
            return "[REDACTED_CIRCULAR_REF]"

        if isinstance(output, dict):
            visited.add(obj_id)
            scrubbed = {}
            for key, value in output.items():
                if isinstance(key, str) and _key_is_sensitive(key):
                    scrubbed[key] = _REDACTED
                elif isinstance(value, (dict, list)):
                    scrubbed[key] = SkillsRegistry._scrub_pii_from_output(value, visited, depth + 1)
                elif isinstance(value, str):
                    scrubbed[key] = SkillsRegistry._scrub_string(value)
                else:
                    scrubbed[key] = value
            visited.discard(obj_id)
            return scrubbed
        elif isinstance(output, list):
            visited.add(obj_id)
            result = [SkillsRegistry._scrub_pii_from_output(item, visited, depth + 1) for item in output]
            visited.discard(obj_id)
            return result
        elif isinstance(output, str):
            return SkillsRegistry._scrub_string(output)
        else:
            return output

    # ── enable / disable ─────────────────────────────────────────────────────

    def is_enabled(self, skill_id: str, version: Optional[str] = None) -> bool:
        """Check if a Skill is enabled (tenant-unaware: disabled for ANY tenant → False)."""
        if skill_id not in self._skills:
            return False

        if any(skill_id == sid for sid, _tid in self._auto_disabled):
            return False

        if version:
            skill_version = self._metadata_by_id[skill_id].version
            if not self._version_matches(skill_version, version):
                return False

        return True

    def _is_skill_enabled_for_tenant(self, skill_id: str, tenant_id: str) -> bool:
        """Check if a Skill is enabled for a specific tenant (FIX #12)."""
        if skill_id not in self._skills:
            return False
        return (skill_id, tenant_id) not in self._auto_disabled

    def enable_skill(self, skill_id: str, tenant_id: Optional[str] = None) -> bool:
        """Manually re-enable an auto-disabled Skill for one tenant (FIX #6, #12).

        Returns:
            True if enabled (or already enabled), False if not registered
        """
        if skill_id not in self._skills:
            return False

        effective_tenant_id = tenant_id or self.tenant_id or "_default"
        key = (skill_id, effective_tenant_id)

        with self._failure_lock:
            was_disabled = key in self._auto_disabled
            self._auto_disabled.discard(key)
            self._failure_count[key] = 0

        if was_disabled:
            self._write_audit({
                "event_type": "SKILL_MANUALLY_ENABLED",
                "skill_id": skill_id,
                "timestamp": _utc_now_iso(),
                "tenant_id": effective_tenant_id,
            })
        return True

    # ── execution ────────────────────────────────────────────────────────────

    def execute(
        self,
        skill_id: str,
        input: Dict[str, Any],
        timeout_ms: int = 5000,
        lom: Optional[str] = None,
        tenant_id: Optional[str] = None,
    ) -> SkillExecutionResult:
        """Execute a Skill.

        Args:
            skill_id: Which Skill to execute
            input: Input dictionary
            timeout_ms: Execution timeout in milliseconds
            lom: Line of moral responsibility — REQUIRED. ``"<file>:<function>"``
                (or ``"<file>:<function>:L<line>"``) naming the call site; a
                missing/empty LoM is refused like a tenant violation (audited
                error result, the Skill does not run).
            tenant_id: Override registry tenant_id. ``None`` → registry tenant;
                an EMPTY string is a violation, not a default (fail-closed).

        Returns:
            SkillExecutionResult with audit metadata

        Compliance:
            - Logs all executions to audit trail
            - Auto-disables Skill per tenant after 3+ consecutive failures
            - Immutable result (frozen dataclass)
            - Tenant isolation (fail-closed, GDPR Art. 5, 6)
        """
        start_time = datetime.now(timezone.utc)
        effective_tenant_id = self.tenant_id if tenant_id is None else tenant_id

        # Validate tenant isolation (GDPR Art. 5, 6 — fail-closed)
        if not self._validate_tenant_id(effective_tenant_id):
            return self._finish_error(
                skill_id, f"Tenant isolation violation: {effective_tenant_id!r} not authorized",
                effective_tenant_id or "", lom, start_time, track=False,
            )

        # LoM is REQUIRED (ADR-0537, EU AI Act Art. 50): a decision nobody is
        # responsible for is not executed. Refused like a tenant violation — an
        # audited error result, the Skill never runs (adversarial review F-K2:
        # every production call site passed no LoM and the chain said ``null``).
        if not isinstance(lom, str) or not lom.strip():
            return self._finish_error(
                skill_id,
                "LoM missing: pass lom='<file>:<function>' naming the line of moral "
                "responsibility for this Skill execution (ADR-0537)",
                effective_tenant_id, None, start_time, track=False,
            )

        # The LoM must BIND to source (ADR-0537): an unresolvable file:function
        # used to fall back to sha256(label), indistinguishable from a real
        # source hash (round-2 review, R2-B1). Refused like a missing LoM.
        if self._compute_lom_hash(lom) is None:
            return self._finish_error(
                skill_id,
                f"LoM unresolvable: {lom!r} does not name an existing "
                "<repo-relative file>:<function> (ADR-0537)",
                effective_tenant_id, None, start_time, track=False,
            )

        # Check if Skill exists
        if skill_id not in self._skills:
            return self._finish_error(
                skill_id, f"Skill not found: {skill_id}", effective_tenant_id, lom, start_time, track=False,
            )

        # FIX #12: Check if Skill is auto-disabled for THIS tenant
        if not self._is_skill_enabled_for_tenant(skill_id, effective_tenant_id):
            return self._finish_error(
                skill_id, f"Skill auto-disabled after {self.AUTO_DISABLE_THRESHOLD}+ failures: {skill_id}",
                effective_tenant_id, lom, start_time, track=False,
            )

        skill = self._skills[skill_id]

        with self._in_flight_lock:
            if self._in_flight.get(skill_id, 0) >= self.MAX_IN_FLIGHT_PER_SKILL:
                saturated = True
            else:
                saturated = False
                self._in_flight[skill_id] = self._in_flight.get(skill_id, 0) + 1
        if saturated:
            return self._finish_error(
                skill_id,
                f"Skill saturated: {self.MAX_IN_FLIGHT_PER_SKILL} executions still in flight "
                f"(timed-out workers not yet returned): {skill_id}",
                effective_tenant_id, lom, start_time, track=True,
            )

        # FIX #10: Scrub PII from input before Skill execution (GDPR Art. 32)
        scrubbed_input = self._scrub_pii_from_output(input)

        holder = _ThreadResult()

        def _runner() -> None:
            try:
                holder.value = skill.execute(scrubbed_input)
            except BaseException as exc:  # noqa: BLE001 — recorded, re-raised on the caller side
                holder.exc = exc
            finally:
                with self._in_flight_lock:
                    self._in_flight[skill_id] = max(0, self._in_flight.get(skill_id, 1) - 1)

        worker = threading.Thread(
            target=_runner, name=f"skill:{skill_id}", daemon=True,
        )
        worker.start()
        worker.join(timeout=max(timeout_ms, 0) / 1000.0)

        end_time = datetime.now(timezone.utc)
        execution_time_ms = (end_time - start_time).total_seconds() * 1000

        if worker.is_alive():
            result = SkillExecutionResult(
                skill_id=skill_id,
                status="timeout",
                error_message=f"Skill execution timeout after {timeout_ms}ms",
                execution_time_ms=execution_time_ms,
                timestamp=end_time.isoformat(),
                tenant_id=effective_tenant_id,
                lom=lom,
                lom_hash=self._compute_lom_hash(lom),
            )
            self._track_failure(skill_id, effective_tenant_id)
        elif holder.exc is not None:
            result = SkillExecutionResult(
                skill_id=skill_id,
                status="error",
                error_message=str(holder.exc) or type(holder.exc).__name__,
                execution_time_ms=execution_time_ms,
                timestamp=end_time.isoformat(),
                tenant_id=effective_tenant_id,
                lom=lom,
                lom_hash=self._compute_lom_hash(lom),
            )
            self._track_failure(skill_id, effective_tenant_id)
        else:
            result = SkillExecutionResult(
                skill_id=skill_id,
                status="success",
                output=holder.value,
                execution_time_ms=execution_time_ms,
                timestamp=end_time.isoformat(),
                tenant_id=effective_tenant_id,
                lom=lom,
                lom_hash=self._compute_lom_hash(lom),  # ADR-0537: cryptographic LoM binding
            )
            with self._failure_lock:
                self._failure_count[(skill_id, effective_tenant_id)] = 0

        self._emit_audit_event(result)
        self._emit_learning_event(result)  # ADR-0314 learning loop
        return result

    def _finish_error(
        self,
        skill_id: str,
        message: str,
        tenant_id: str,
        lom: Optional[str],
        start_time: datetime,
        *,
        track: bool,
    ) -> SkillExecutionResult:
        end_time = datetime.now(timezone.utc)
        result = SkillExecutionResult(
            skill_id=skill_id,
            status="error",
            error_message=message,
            execution_time_ms=(end_time - start_time).total_seconds() * 1000,
            timestamp=end_time.isoformat(),
            tenant_id=tenant_id,
            lom=lom,
            lom_hash=self._compute_lom_hash(lom),
        )
        if track:
            self._track_failure(skill_id, tenant_id)
        self._emit_audit_event(result)
        return result

    def disable_skill(self, skill_id: str, tenant_id: Optional[str] = None) -> bool:
        """Manually disable a Skill for one tenant (the operator's off switch).

        Returns True if disabled (or already disabled), False if not registered.

        Raises:
            SkillDisableRefused: for a ``tier=compliance`` Skill (audited).
        """
        if skill_id not in self._skills:
            return False
        effective_tenant_id = tenant_id or self.tenant_id or "_default"
        self._refuse_if_compliance(skill_id, operation="disable", tenant_id=effective_tenant_id)
        key = (skill_id, effective_tenant_id)
        with self._failure_lock:
            newly = key not in self._auto_disabled
            self._auto_disabled.add(key)
        if newly:
            self._write_audit({
                "event_type": "SKILL_MANUALLY_DISABLED",
                "skill_id": skill_id,
                "timestamp": _utc_now_iso(),
                "tenant_id": effective_tenant_id,
            })
        return True

    def _track_failure(self, skill_id: str, tenant_id: Optional[str] = None) -> None:
        """Track consecutive failures per (skill, tenant); auto-disable after threshold.

        A ``tier=compliance`` Skill is never auto-disabled: the refusal is
        audited (``skill.disable.refused``, once per threshold crossing) and the
        Skill keeps answering — a flaky compliance Skill is an incident, not a
        reason to switch the compliance mechanism off.
        """
        effective_tenant_id = tenant_id or self.tenant_id or "_default"
        key = (skill_id, effective_tenant_id)
        compliance = self._tier_of(skill_id) is SkillTier.COMPLIANCE
        refused = False
        with self._failure_lock:  # FIX #3: Prevent TOCTOU race
            self._failure_count[key] = self._failure_count.get(key, 0) + 1
            failures = self._failure_count[key]
            if failures >= self.AUTO_DISABLE_THRESHOLD and key not in self._auto_disabled:
                if compliance:
                    disabled = False
                    refused = failures == self.AUTO_DISABLE_THRESHOLD
                else:
                    logger.error(
                        f"Skill {skill_id} auto-disabled for tenant {effective_tenant_id} "
                        f"after {self.AUTO_DISABLE_THRESHOLD}+ consecutive failures"
                    )
                    self._auto_disabled.add(key)
                    disabled = True
            else:
                disabled = False
        if refused:
            self._audit_disable_refused(
                skill_id, operation="auto_disable", tenant_id=effective_tenant_id, failures=failures,
            )
        if disabled:
            self._write_audit({
                "event_type": "SKILL_AUTO_DISABLED",
                "skill_id": skill_id,
                "timestamp": _utc_now_iso(),
                "tenant_id": effective_tenant_id,
                "failures": self.AUTO_DISABLE_THRESHOLD,
            })

    # ── learning (ADR-0314) ──────────────────────────────────────────────────

    @staticmethod
    def _validate_confidence_score(score_dict: Dict[str, Any]) -> Dict[str, Any]:
        """Clamp all numeric score fields to [0.0, 1.0] (FIX #9)."""
        validated: Dict[str, Any] = {}
        for key, value in score_dict.items():
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                validated[key] = value
            else:
                clamped = max(0.0, min(1.0, float(value)))
                if clamped != value:
                    logger.warning(f"Confidence score {key}={value} out of bounds [0.0, 1.0]; clamped to {clamped}")
                validated[key] = clamped
        return validated

    def _emit_learning_event(self, result: SkillExecutionResult) -> None:
        """Emit learning event for Skill execution (ADR-0314, FIX #7: scrubbed output)."""
        if not self.learning_backend:
            return  # Learning backend not configured (optional)
        skill = self.get(result.skill_id)
        if skill is not None and not getattr(skill.metadata, "learn", True):
            return  # audited above; deliberately NOT a learning signal

        try:
            scrubbed_output = (
                self._scrub_pii_from_output(result.output) if result.output is not None else None
            )
            learning_event = {
                "event_type": "skill_executed",
                "skill_id": result.skill_id,
                "status": result.status,
                "execution_time_ms": result.execution_time_ms,
                "timestamp": result.timestamp,
                "tenant_id": result.tenant_id,
                "lom": result.lom,
                "lom_hash": result.lom_hash,
                "output": scrubbed_output,
                "confidence_score": self._validate_confidence_score({
                    "skill_id": result.skill_id,
                    "reliability": 0.95 if result.status == "success" else 0.0,
                    "relevance": 0.8,  # TODO: derive from user feedback
                    "combined": 0.8 if result.status == "success" else 0.0,
                }),
            }
            self.learning_backend.emit_event(learning_event)
        except Exception as e:  # noqa: BLE001
            self.learning_emit_failures += 1
            logger.error(f"Failed to emit learning event: {e}")

    # ── audit (GDPR Art. 30/32) ──────────────────────────────────────────────

    def _write_audit(self, event: Dict[str, Any]) -> None:
        if self.audit_backend:
            try:
                self.audit_backend.write_event(event)
            except Exception as e:  # noqa: BLE001
                logger.error(f"Failed to write audit event: {e}")
        else:
            logger.info(f"{event.get('event_type')}: {event}")

    def _emit_audit_event(self, result: SkillExecutionResult) -> None:
        """Emit audit event for Skill execution (PII-scrubbed, GDPR Art. 32, FIX #4)."""
        scrubbed_result = SkillExecutionResult(
            skill_id=result.skill_id,
            status=result.status,
            output=self._scrub_pii_from_output(result.output) if result.output is not None else None,
            execution_time_ms=result.execution_time_ms,
            error_message=self._scrub_string(result.error_message) if result.error_message else None,
            timestamp=result.timestamp,
            lom=result.lom,
            lom_hash=result.lom_hash,
            tenant_id=result.tenant_id,
        )
        self._write_audit(scrubbed_result.to_audit_event())

    @staticmethod
    def _version_matches(skill_version: str, constraint: str) -> bool:
        """Check if skill_version matches constraint (exact match)."""
        return skill_version == constraint


# Global singleton registry
_global_registry: Optional[SkillsRegistry] = None
_global_lock = Lock()


def initialize_registry(
    audit_backend: Optional[Any] = None,
    tenant_id: str = "_default",
    learning_backend: Optional[Any] = None,
) -> SkillsRegistry:
    """Initialize (replace) the global Skills registry."""
    global _global_registry
    with _global_lock:
        _global_registry = SkillsRegistry(audit_backend, tenant_id, learning_backend)
    logger.info("Skills registry initialized (Phase 1 big bang)")
    return _global_registry


def get_registry() -> SkillsRegistry:
    """Get the global Skills registry (lazy init on first call).

    A lazily created registry carries the builtin OS Skills (they are pure
    functions of the per-tenant flag registry), but NO core audit backend —
    events go to the application logger until ``core.skills.boot.boot_skills``
    (called from ``corvin_plugins.bootstrap.boot_platform``) replaces it with the
    audited registry. This matters because consumers run BEFORE the lifespan
    boots the platform: ``mount_static()`` asks ``os.headless_mode`` while the
    app is being built, and an empty registry there meant "Skill not found" →
    headless mode could never engage (adversarial review 2026-09-03).
    """
    global _global_registry
    if _global_registry is None:
        with _global_lock:
            if _global_registry is None:
                registry = SkillsRegistry()
                try:
                    from .os_skills_phase1 import register_builtin_skills  # noqa: PLC0415

                    register_builtin_skills(registry)
                except Exception as exc:  # noqa: BLE001 — never block a consumer on import trouble
                    logger.error("builtin Skills could not be registered lazily: %s", exc)
                _global_registry = registry
    return _global_registry


def is_skill_enabled(skill_id: str, version: Optional[str] = None) -> bool:
    """Check if a Skill is enabled (replacement for feature flags)."""
    registry = get_registry()
    return registry.is_enabled(skill_id, version)
