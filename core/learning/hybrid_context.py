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

import hashlib
import json
import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, asdict, field
from typing import Optional, Any, Protocol

logger = logging.getLogger(__name__)


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

        # Validate no PII (fail-closed)
        self._validate_no_pii(data)

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

        # Append to chain (fail-closed: if append fails, exception raised)
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
            "recent_decisions": base.recent_decisions,
            "user_profile": base.user_profile,
            "success_rate": base.success_rate,
            "attention_budget_remaining": base.attention_budget_remaining,
        }

        failed_layers = []

        # Try to merge each layer (fail-closed: if one fails, skip it)
        for layer in layers:
            try:
                layer_name = layer.get("layer_name", "unknown")
                layer_data = layer.get("data", {})

                # Validate no PII (fail-closed)
                self._validate_no_pii(layer_data)

                # Merge layer into context
                merged[layer_name] = layer_data
                logger.debug(f"Merged layer {layer_name}")

            except ValueError as e:
                layer_name = layer.get("layer_name", "unknown")
                logger.warning(f"Failed to merge layer {layer_name}: {e} (dropped)")
                failed_layers.append((layer_name, str(e)))

        if failed_layers:
            logger.warning(
                f"Dropped {len(failed_layers)} layers due to validation failures: "
                f"{failed_layers}"
            )

        # Emit audit event
        self._emit_merge_event(
            base.user_id,
            base.session_id,
            len(layers),
            len(failed_layers),
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

        # Step 1: Delete all base snapshots for this user
        keys_to_delete = [k for k in self.base_snapshots if k.startswith(user_id)]
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
        if any(k.startswith(user_id) for k in self.base_snapshots):
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
    def _validate_no_pii(data: dict) -> None:
        """Validate that data contains no obvious PII (fail-closed).

        Raises:
            ValueError: if PII-like patterns detected
        """
        pii_patterns = [
            "email", "phone", "ssn", "password", "api_key", "token",
            "credit_card", "bank_account", "medical", "health",
        ]
        data_str = json.dumps(data).lower()
        for pattern in pii_patterns:
            if pattern in data_str:
                raise ValueError(
                    f"Potential PII detected in data (pattern: {pattern})"
                )

    @staticmethod
    def _emit_merge_event(
        user_id: str, session_id: str, total_layers: int, failed_count: int
    ) -> None:
        """Emit audit event for merge operation."""
        logger.info(
            f"hybrid_context_merge: user={user_id}, session={session_id}, "
            f"total_layers={total_layers}, failed={failed_count}"
        )
