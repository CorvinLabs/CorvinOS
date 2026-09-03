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

import hashlib
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


class SkillOrigin(str, Enum):
    """Where a Skill comes from."""
    BUILTIN = "builtin"  # Part of CorvinOS core
    VETTED = "vetted"    # Reviewed & signed by Corvin team
    COMMUNITY = "community"  # User-contributed


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
        """Convert to audit trail event format."""
        return {
            "event_type": "SKILL_EXECUTED",
            "skill_id": self.skill_id,
            "status": self.status,
            "output": self.output,
            "execution_time_ms": self.execution_time_ms,
            "error_message": self.error_message,
            "timestamp": self.timestamp,
            "lom": self.lom,
            "lom_hash": self.lom_hash,
            "tenant_id": self.tenant_id,
        }


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
        """Unregister a Skill."""
        if skill_id in self._skills:
            del self._skills[skill_id]
            del self._metadata_by_id[skill_id]
            logger.info(f"Unregistered Skill: {skill_id}")

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
    def _compute_lom_hash(lom: Optional[str]) -> Optional[str]:
        """SHA256 of the source line at the LoM (``file:function:line`` or ``file:function:L<line>``).

        Falls back to hashing the LoM string itself when the source cannot be
        resolved; the fallback is logged so a non-binding hash is observable.
        """
        if not lom:
            return None

        try:
            parts = lom.split(":")
            if len(parts) < 3:
                return None

            file_path, line_str = parts[0], parts[2].strip()
            if line_str[:1] in ("L", "l"):
                line_str = line_str[1:]
            line_num = int(line_str)

            source_path = Path(file_path)
            if not source_path.is_absolute():
                source_path = _REPO_ROOT / source_path

            if not source_path.exists():
                logger.warning(f"LoM source file not found: {source_path}")
                return hashlib.sha256(lom.encode()).hexdigest()

            lines = source_path.read_text(encoding="utf-8", errors="ignore").split("\n")
            if line_num < 1 or line_num > len(lines):
                logger.warning(f"LoM line {line_num} outside file length {len(lines)}")
                return hashlib.sha256(lom.encode()).hexdigest()

            return hashlib.sha256(lines[line_num - 1].encode()).hexdigest()

        except Exception as e:  # noqa: BLE001
            logger.warning(f"Failed to compute LoM hash for '{lom}': {e}")
            return hashlib.sha256(lom.encode()).hexdigest()

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
                if isinstance(key, str) and _PII_KEY_PATTERN.search(key):
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
            lom: Line of moral responsibility (source code location)
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

        # FIX #10: Scrub PII from input before Skill execution (GDPR Art. 32)
        scrubbed_input = self._scrub_pii_from_output(input)

        holder = _ThreadResult()

        def _runner() -> None:
            try:
                holder.value = skill.execute(scrubbed_input)
            except BaseException as exc:  # noqa: BLE001 — recorded, re-raised on the caller side
                holder.exc = exc

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

    def _track_failure(self, skill_id: str, tenant_id: Optional[str] = None) -> None:
        """Track consecutive failures per (skill, tenant); auto-disable after threshold."""
        effective_tenant_id = tenant_id or self.tenant_id or "_default"
        key = (skill_id, effective_tenant_id)
        with self._failure_lock:  # FIX #3: Prevent TOCTOU race
            self._failure_count[key] = self._failure_count.get(key, 0) + 1
            if self._failure_count[key] >= self.AUTO_DISABLE_THRESHOLD and key not in self._auto_disabled:
                logger.error(
                    f"Skill {skill_id} auto-disabled for tenant {effective_tenant_id} "
                    f"after {self.AUTO_DISABLE_THRESHOLD}+ consecutive failures"
                )
                self._auto_disabled.add(key)
                disabled = True
            else:
                disabled = False
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

    A lazily created registry is EMPTY — ``core.skills.boot.boot_skills`` (called
    from ``corvin_plugins.bootstrap.boot_platform``) is what populates it.
    """
    global _global_registry
    if _global_registry is None:
        initialize_registry()
    return _global_registry


def execute_skill(
    skill_id: str,
    input: Dict[str, Any],
    timeout_ms: int = 5000,
    lom: Optional[str] = None,
) -> SkillExecutionResult:
    """Execute a Skill via global registry."""
    registry = get_registry()
    return registry.execute(skill_id, input, timeout_ms, lom)


def is_skill_enabled(skill_id: str, version: Optional[str] = None) -> bool:
    """Check if a Skill is enabled (replacement for feature flags)."""
    registry = get_registry()
    return registry.is_enabled(skill_id, version)
