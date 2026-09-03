"""Phase 4: Hybrid Context Model — immutable base + injected layers (versioned, fail-closed).

Architecture:
- TIER 1: IMMUTABLE BASE (Phase 3: decisions, outcomes, profiles, attention_budget)
- TIER 2: INJECTED LAYERS (versioned, hash-chained)
  - user_style (learned from outcomes)
  - attention_allocation (per-task budget)
  - session_context (conversation state)
  - real_time_metrics (Phase 3.6)
- TIER 3: MERGE ENGINE (fail-closed)
  - merge_with_fallback() → if layer fails, drop it, merge continues

GDPR Compliance:
- Base immutable (Art. 5)
- Layers hash-chained (Art. 32)
- Cascade delete on erasure (Art. 17)
- Fail-closed on any validation failure

Integration with Phase 3:
- Accepts Phase 3 adapters (DecisionAdapter, OutcomeAdapter, ProfileAdapter)
- Snapshot interfaces decouple implementation from Phase 3 storage
- Cascade delete verification step (GDPR Art. 17)
"""

from __future__ import annotations

import copy
import hashlib
import inspect
import json
import logging
import re
import time
from dataclasses import dataclass, asdict, field
from typing import Optional, Any, Protocol

from .event_persistence import core_audit_event

logger = logging.getLogger(__name__)

def _get_lom() -> str:
    """Get line of moral responsibility (caller's file:function:line)."""
    frame = inspect.currentframe()
    if frame is None:
        return "unknown"  # Frame introspection failed
    caller_frame = frame.f_back
    if caller_frame is None:
        return "unknown"  # No caller frame (shouldn't happen)
    return f"{caller_frame.f_code.co_filename}:{caller_frame.f_code.co_name}:{caller_frame.f_lineno}"

# Tier 1 immutable fields that layers cannot override (GDPR Art. 5)
TIER1_IMMUTABLE_KEYS = {
    "tenant_id", "user_id", "session_id",
    "timestamp_utc", "base_hash", "prev_base_hash",
    "recent_decisions", "user_profile", "success_rate", "attention_budget_remaining"
}

# Phase 3 Adapter Interfaces (decouple HybridContextModel from Phase 3 storage)

class DecisionAdapter(Protocol):
    """Adapter for reading decisions from Phase 3 DecisionHistoryStore."""

    def get_recent_decisions(self, user_id: str, tenant_id: str, limit: int = 10) -> list[dict]:
        """Get recent decisions for a user."""
        ...


class OutcomeAdapter(Protocol):
    """Adapter for reading outcomes from Phase 3 OutcomeFeedbackStore."""

    def get_success_rate(self, user_id: str, tenant_id: str) -> float:
        """Get success rate (small-n suppressed)."""
        ...


class ProfileAdapter(Protocol):
    """Adapter for reading profiles from Phase 3 UserProfileManager."""

    def get_profile(self, user_id: str, tenant_id: str) -> dict:
        """Get learned user preferences."""
        ...


class AttentionAdapter(Protocol):
    """Adapter for reading the remaining attention budget (Phase 3 AttentionTracker, ADR-0319)."""

    def get_remaining_budget(self, user_id: str, tenant_id: str) -> int:
        """Remaining context tokens for this user (0 when unknown)."""
        ...


@dataclass
class CascadeDeleteResult:
    """Result of cascade delete operation with verification."""

    user_id: str
    deleted_bases: int
    deleted_layers: int
    verification_complete: bool  # True if all deletions verified
    errors: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return self.deleted_bases + self.deleted_layers


@dataclass(frozen=True)
class ImmutableContextBase:
    """TIER 1: Immutable snapshot of Phase 3 data (never modified)."""

    tenant_id: str
    user_id: str
    session_id: str

    # Phase 3 snapshots (immutable)
    recent_decisions: list[dict] = field(default_factory=list)
    user_profile: dict = field(default_factory=dict)  # learned preferences
    success_rate: float = 0.5  # small-n suppressed
    attention_budget_remaining: int = 0  # tokens available

    # Audit trail
    timestamp_utc: str = ""
    base_hash: str = ""  # sha256 of this snapshot
    prev_base_hash: str = ""  # chain link to prior snapshot


@dataclass(frozen=True)
class InjectedLayer:
    """TIER 2: A versioned, hash-chained context layer."""

    layer_name: str
    version: str  # e.g., "1.0", "2.1"
    data: dict  # layer-specific payload
    timestamp_utc: str
    hash: str  # sha256(version + data + prev_hash)
    prev_hash: str  # chain link to prior version
    lom: str  # line of moral responsibility (file:line:function)
    status: str = "injected"  # injected | failed | dropped


class HybridContextModel:
    """Hybrid context = immutable base + injected layers (versioned, fail-closed)."""

    def __init__(
        self,
        tenant_id: str,
        decision_adapter: Optional[DecisionAdapter] = None,
        outcome_adapter: Optional[OutcomeAdapter] = None,
        profile_adapter: Optional[ProfileAdapter] = None,
    ):
        """Initialize hybrid context model for a tenant.

        Args:
            tenant_id: Tenant identifier
            decision_adapter: Adapter for reading Phase 3 decisions (optional)
            outcome_adapter: Adapter for reading Phase 3 outcomes (optional)
            profile_adapter: Adapter for reading Phase 3 profiles (optional)

        When adapters are not provided, Phase 3 snapshots must be injected manually.
        """
        self.tenant_id = tenant_id
        self.decision_adapter = decision_adapter
        self.outcome_adapter = outcome_adapter
        self.profile_adapter = profile_adapter
        self.base_snapshots: dict[str, ImmutableContextBase] = {}  # user_id:session_id -> base
        self.injected_layers: dict[str, list[InjectedLayer]] = {}  # user_id -> history

    def get_context(
        self, user_id: str, session_id: str
    ) -> dict[str, Any]:
        """Get hybrid context for LLM prompt injection.

        Returns: {
            "base": {immutable Phase 3 snapshot},
            "layers": [{layer_name, version, data, hash, status}, ...],
            "merged": {merged_prompt_context} (result of merge_with_fallback)
        }

        Raises:
            ValueError: if tenant_id mismatch or user not found
        """
        if not user_id or not session_id:
            raise ValueError("user_id and session_id required")

        # Fetch immutable base
        key = f"{user_id}:{session_id}"
        if key not in self.base_snapshots:
            raise ValueError(f"No base context for user {user_id}")

        base = self.base_snapshots[key]

        # Fetch injected layers
        layers = [
            asdict(layer) for layer in self.injected_layers.get(user_id, [])
        ]

        # Merge with fallback
        merged = self.merge_with_fallback(base, layers)

        return {
            "base": asdict(base),
            "layers": layers,
            "merged": merged,
        }

    def inject_layer(
        self,
        user_id: str,
        layer_name: str,
        data: dict,
        lom: str,
        version: str = "1.0",
    ) -> str:
        """Inject a new context layer.

        Validates data (no PII, correct schema), appends to chain.
        Emits audit event on success.

        Args:
            user_id: User identifier
            layer_name: Layer name (e.g., "user_style", "session_context")
            data: Layer-specific payload (must be serializable)
            lom: Line of Moral Responsibility (file:line:function)
            version: Semantic version (default "1.0")

        Returns:
            layer_hash (sha256 of this layer)

        Raises:
            ValueError: if validation fails (PII detected, schema invalid, etc.)
        """
        if not user_id or not layer_name or not data:
            raise ValueError("user_id, layer_name, data required")

        # Snapshot on ingestion: the layer is hash-chained, so it must not share
        # storage with the caller's dict (a later caller-side mutation would
        # silently invalidate the hash — L-05).
        data = copy.deepcopy(data)

        # Validate no PII (fail-closed)
        self._validate_no_pii(data)

        # Validate that the layer neither shadows a Tier 1 field by NAME (the
        # merge writes ``merged[layer_name]`` — L-03) nor carries one as a key.
        violation = self._tier1_violation(layer_name, data)
        if violation is not None:
            error_msg = (
                f"Layer {layer_name} attempted to override immutable Tier 1 field '{violation}'"
            )
            logger.error(error_msg)
            self._audit(
                "tier1_immutable_violation_attempted",
                user_id=user_id,
                details={"layer_name": layer_name, "field": violation, "lom": lom},
            )
            raise ValueError(error_msg)

        # Get previous hash (for chain link)
        prev_layers = self.injected_layers.get(user_id, [])
        prev_hash = prev_layers[-1].hash if prev_layers else ""

        # Create layer
        timestamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        layer_hash = self._compute_hash(version, data, prev_hash)

        layer = InjectedLayer(
            layer_name=layer_name,
            version=version,
            data=data,
            timestamp_utc=timestamp,
            hash=layer_hash,
            prev_hash=prev_hash,
            lom=lom,
            status="injected",
        )

        # Audit FIRST (fail-closed): a layer that could not be audited is never
        # appended to the chain (ADR-0232/0233).
        self._audit(
            "tier2_layer_injected",
            user_id=user_id,
            details={
                "layer_name": layer_name,
                "version": version,
                "hash": layer_hash,
                "prev_hash": prev_hash,
                "lom": lom,
                "lom_audit_write": _get_lom(),
            },
        )

        if user_id not in self.injected_layers:
            self.injected_layers[user_id] = []
        self.injected_layers[user_id].append(layer)

        logger.info(
            f"Injected layer {layer_name} v{version} for user {user_id} "
            f"(hash={layer_hash[:8]}..., lom={lom})"
        )

        return layer_hash

    def merge_with_fallback(
        self, base: ImmutableContextBase, layers: list[dict]
    ) -> dict:
        """Merge hybrid context for LLM (fail-closed).

        If a layer fails validation: drops layer, continues with base + healthy layers.
        All operations audit-logged.

        Args:
            base: Immutable base context
            layers: List of injected layers (dicts)

        Returns:
            merged_prompt_context (ready for LLM injection)
        """
        merged = {
            "recent_decisions": copy.deepcopy(base.recent_decisions),
            "user_profile": copy.deepcopy(base.user_profile),
            "success_rate": base.success_rate,
            "attention_budget_remaining": base.attention_budget_remaining,
        }

        failed_layers = []
        expected_prev = ""  # hash of the last VERIFIED layer (chain continuity)

        # Try to merge each layer (fail-closed: if one fails, skip it)
        for layer in layers:
            layer_name = layer.get("layer_name", "unknown")
            try:
                layer_data = layer.get("data", {})

                # Verify the layer's own hash and its chain link (L-04). A layer
                # whose hash does not recompute, or whose prev_hash does not
                # point at the last verified layer, is forged/tampered → drop.
                stated_hash = layer.get("hash", "")
                recomputed = self._compute_hash(
                    layer.get("version", ""), layer_data, layer.get("prev_hash", "")
                )
                if stated_hash != recomputed:
                    raise ValueError("hash mismatch (layer content does not match its hash)")
                if layer.get("prev_hash", "") != expected_prev:
                    raise ValueError("chain break (prev_hash does not link to the previous layer)")
                # Integrity verified: the chain advances even if the layer is
                # dropped below for CONTENT reasons (PII / Tier 1) — those are
                # policy drops, not chain breaks, and must not cascade.
                expected_prev = stated_hash

                # Validate no PII (fail-closed)
                self._validate_no_pii(layer_data)

                # Validate that layer doesn't attempt to override Tier 1 immutable fields
                violation = self._tier1_violation(layer_name, layer_data)
                if violation is not None:
                    self._audit(
                        "tier1_immutable_violation_attempted",
                        user_id=base.user_id,
                        details={"layer_name": layer_name, "field": violation, "stage": "merge"},
                    )
                    raise ValueError(
                        f"Layer {layer_name} attempted to override immutable Tier 1 field '{violation}'"
                    )

                # Merge layer into context
                merged[layer_name] = copy.deepcopy(layer_data)
                logger.debug(f"Merged layer {layer_name}")

            except ValueError as e:
                logger.warning(f"Failed to merge layer {layer_name}: {e} (dropped)")
                failed_layers.append((layer_name, str(e)))
                self._audit(
                    "tier2_layer_rejected",
                    user_id=base.user_id,
                    details={"layer_name": layer_name, "reason": str(e)[:200]},
                )

        if failed_layers:
            logger.warning(
                f"Dropped {len(failed_layers)} layers due to validation failures: "
                f"{failed_layers}"
            )

        # Emit audit event (hash-chained)
        self._emit_merge_event(
            base.user_id,
            base.session_id,
            len(layers),
            len(failed_layers),
            base.tenant_id,
        )

        return merged

    def snapshot_base_context(
        self,
        user_id: str,
        session_id: str,
        decisions: list[dict],
        profile: dict,
        success_rate: float,
        attention_budget: int,
    ) -> str:
        """Snapshot immutable base context (Phase 3 data).

        Called once per session to capture base at session start.
        Never modified thereafter.

        Returns:
            base_hash (sha256 of snapshot)
        """
        if not user_id or not session_id:
            raise ValueError("user_id and session_id required")

        timestamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        key = f"{user_id}:{session_id}"

        # Snapshot on ingestion (L-05): the base is immutable and hashed, so it
        # must not alias the caller's lists/dicts.
        decisions = copy.deepcopy(decisions)
        profile = copy.deepcopy(profile)

        # Compute hash of base (for chain link)
        prev_base = self.base_snapshots.get(key)
        prev_hash = prev_base.base_hash if prev_base else ""
        base_hash = self._compute_hash("base", {
            "decisions": decisions,
            "profile": profile,
            "success_rate": success_rate,
        }, prev_hash)

        base = ImmutableContextBase(
            tenant_id=self.tenant_id,
            user_id=user_id,
            session_id=session_id,
            recent_decisions=decisions,
            user_profile=profile,
            success_rate=success_rate,
            attention_budget_remaining=attention_budget,
            timestamp_utc=timestamp,
            base_hash=base_hash,
            prev_base_hash=prev_hash,
        )

        # Audit FIRST (fail-closed), then store.
        self._audit(
            "tier1_base_snapshotted",
            user_id=user_id,
            details={
                "session_id": session_id,
                "base_hash": base_hash,
                "prev_hash": prev_hash,
                "decisions_count": len(decisions),
                "attention_budget": attention_budget,
                "lom_audit_write": _get_lom(),
            },
        )

        self.base_snapshots[key] = base
        logger.info(
            f"Snapshotted base context for user {user_id} session {session_id} "
            f"(hash={base_hash[:8]}..., decisions={len(decisions)}, "
            f"attention_budget={attention_budget})"
        )

        return base_hash

    def delete_user_context(self, user_id: str) -> CascadeDeleteResult:
        """Delete all context (base + layers) for a user (GDPR Art. 17).

        Cascades across all sessions and layers with verification step.
        Idempotent: second call returns 0 for all counts.

        Args:
            user_id: User to erase

        Returns:
            CascadeDeleteResult with verification_complete flag

        Raises:
            ValueError: if user_id is empty
        """
        if not user_id:
            raise ValueError("user_id required")

        errors: list[str] = []
        deleted_bases = 0
        deleted_layers = 0

        # Step 1: Delete all base snapshots for this user (exact match, not prefix)
        # Key format is "user_id:session_id", so match only keys starting with "user_id:"
        keys_to_delete = [k for k in self.base_snapshots if k.startswith(user_id + ":")]
        for key in keys_to_delete:
            try:
                del self.base_snapshots[key]
                deleted_bases += 1
            except Exception as e:
                errors.append(f"Failed to delete base {key}: {e}")

        # Step 2: Delete all injected layers for this user
        if user_id in self.injected_layers:
            try:
                deleted_layers = len(self.injected_layers[user_id])
                del self.injected_layers[user_id]
            except Exception as e:
                errors.append(f"Failed to delete layers for user {user_id}: {e}")

        # Step 3: Verify deletion (fail-closed)
        verification_complete = True
        if any(k.startswith(user_id + ":") for k in self.base_snapshots):
            errors.append(f"Verification failed: base snapshots still present for {user_id}")
            verification_complete = False

        if user_id in self.injected_layers:
            errors.append(f"Verification failed: layers still present for {user_id}")
            verification_complete = False

        # Log result
        result = CascadeDeleteResult(
            user_id=user_id,
            deleted_bases=deleted_bases,
            deleted_layers=deleted_layers,
            verification_complete=verification_complete,
            errors=errors,
        )

        if verification_complete:
            logger.info(
                f"Cascade delete verified for user {user_id}: "
                f"{deleted_bases} bases, {deleted_layers} layers"
            )
        else:
            logger.error(
                f"Cascade delete INCOMPLETE for user {user_id}: {errors}"
            )

        # Audit (hash-chained, GDPR Art. 17) — counts only.
        self._audit(
            "user_context_cascade_deleted",
            user_id=user_id,
            details={
                "deleted_bases": deleted_bases,
                "deleted_layers": deleted_layers,
                "verification_complete": verification_complete,
                "error_count": len(errors),
                "lom_audit_write": _get_lom(),
            },
        )

        return result

    # Private helpers

    @staticmethod
    def _compute_hash(version: str, data: dict, prev_hash: str) -> str:
        """Compute sha256 hash of (version + data + prev_hash)."""
        payload = json.dumps({
            "version": version,
            "data": data,
            "prev_hash": prev_hash,
        }, sort_keys=True)
        return hashlib.sha256(payload.encode()).hexdigest()

    @staticmethod
    def _tier1_violation(layer_name: str, data: dict) -> Optional[str]:
        """Return the Tier 1 field a layer would override, or None.

        Both the layer NAME (``merged[layer_name] = data`` would replace the
        Tier 1 entry) and the layer's top-level keys are checked.
        """
        if layer_name in TIER1_IMMUTABLE_KEYS:
            return layer_name
        for key in data.keys():
            if key in TIER1_IMMUTABLE_KEYS:
                return key
        return None

    @staticmethod
    def _validate_no_pii(data: dict) -> None:
        """Validate that data contains no PII (fail-closed).

        Two independent checks, applied recursively:
        - VALUE shapes: email, phone, IBAN, SSN, Luhn-valid card numbers,
          API-key / token shapes, street addresses.
        - KEY names: whole-token match (``phone`` matches ``phone`` and
          ``home_phone``, not ``phonetic``; ``token`` matches ``token``, not
          ``tokens_used``; ``health`` matches ``health_data``, not
          ``healthy_status``).

        Raises:
            ValueError: if PII-like patterns detected
        """
        hit = _find_pii(data)
        if hit is not None:
            raise ValueError(f"Potential PII detected in data (pattern: {hit})")

    def _audit(self, event_type: str, *, user_id: str, details: dict) -> str:
        """Write a hash-chained audit record via the CORE writer (fail-closed).

        Raises ``RuntimeError`` when the core audit writer is unavailable or
        the write did not commit (ADR-0232/0233) — never silently skips.
        """
        return core_audit_event(
            event_type,
            tenant_id=self.tenant_id,
            user=user_id,
            details={"component": "hybrid_context", **details},
        )

    def _emit_merge_event(
        self, user_id: str, session_id: str, total_layers: int, failed_count: int,
        tenant_id: str = "_default"
    ) -> None:
        """Emit audit event for merge operation (hash-chained, GDPR Art. 30/32)."""
        logger.info(
            f"hybrid_context_merge: user={user_id}, session={session_id}, "
            f"total_layers={total_layers}, failed={failed_count}"
        )
        self._audit(
            "hybrid_context_merge",
            user_id=user_id,
            details={
                "session_id": session_id,
                "total_layers": total_layers,
                "failed_count": failed_count,
                "lom_audit_write": _get_lom(),
            },
        )


# ── PII detection (value shapes + whole-token keys) ─────────────────────────

_PII_VALUE_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("email", re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")),
    ("phone", re.compile(r"(?<![\w/])(?:\+|00)\d[\d\s\-().]{6,}\d(?!\w)")),
    ("phone", re.compile(r"\b\d{3}[-.\s]\d{3}[-.\s]\d{4}\b")),
    ("ssn", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    ("iban", re.compile(r"\b[A-Z]{2}\d{2}(?:\s?[A-Z0-9]{4}){2,7}(?:\s?[A-Z0-9]{1,4})?\b")),
    ("api_key", re.compile(r"\b(?:sk|pk|rk|ak)[_-](?:live|test|prod)?[_-]?[A-Za-z0-9]{12,}\b")),
    ("api_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("api_key", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b")),
    ("api_key", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    ("token", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}(?:\.[A-Za-z0-9_-]{8,})?")),
    ("token", re.compile(r"\b[Bb]earer\s+[A-Za-z0-9._~+/=-]{16,}")),
    ("address", re.compile(
        r"\b[A-ZÄÖÜ][\w-]+\s?(?:[Ss]tr(?:\.|aße|asse)|[Ss]treet|[Aa]venue|[Rr]oad|[Ll]ane)\s+\d{1,4}\b"
    )),
]
_CARD_CANDIDATE = re.compile(r"(?<!\d)(?:\d[ -]?){12,18}\d(?!\d)")

_PII_KEY_TOKENS = frozenset({
    "email", "phone", "mobile", "telephone", "ssn", "password", "passwd", "secret",
    "token", "iban", "bic", "medical", "health", "diagnosis", "birthday", "birthdate",
    "dob", "address", "surname", "passport",
})
_PII_KEY_PHRASES = frozenset({
    ("api", "key"), ("credit", "card"), ("card", "number"), ("bank", "account"),
    ("account", "number"), ("social", "security"), ("first", "name"), ("last", "name"),
    ("full", "name"), ("private", "key"), ("access", "key"), ("date", "of", "birth"),
})


def _luhn_ok(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def _key_tokens(key: str) -> list[str]:
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", str(key))  # camelCase → camel_Case
    return [t for t in re.split(r"[^a-z0-9]+", spaced.lower()) if t]


def _key_is_pii(key: str) -> bool:
    tokens = _key_tokens(key)
    if any(t in _PII_KEY_TOKENS for t in tokens):
        return True
    for phrase in _PII_KEY_PHRASES:
        n = len(phrase)
        if any(tuple(tokens[i:i + n]) == phrase for i in range(len(tokens) - n + 1)):
            return True
    return False


def _value_pii(value: str) -> Optional[str]:
    for name, pattern in _PII_VALUE_PATTERNS:
        if pattern.search(value):
            return name
    for m in _CARD_CANDIDATE.finditer(value):
        digits = re.sub(r"[ -]", "", m.group(0))
        if 13 <= len(digits) <= 19 and _luhn_ok(digits):
            return "credit_card"
    return None


def _find_pii(obj: Any) -> Optional[str]:
    """Depth-first scan; returns the pattern name of the first hit, else None."""
    if isinstance(obj, dict):
        for key, value in obj.items():
            if _key_is_pii(str(key)):
                return f"key:{key}"
            hit = _find_pii(value)
            if hit is not None:
                return hit
        return None
    if isinstance(obj, (list, tuple, set)):
        for item in obj:
            hit = _find_pii(item)
            if hit is not None:
                return hit
        return None
    if isinstance(obj, str):
        return _value_pii(obj)
    return None
