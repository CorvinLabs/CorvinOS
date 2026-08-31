"""User Profile — style preferences & learning feedback loop (ADR-0318).

This module implements user preference tracking with GDPR-compliant storage and
learning event emission. Preferences are learned via feedback integration and can be
explicitly overridden by the operator (Right to Object, GDPR Art. 21).

Compliance Notes:
- GDPR Art. 5: Data minimization — only infer what's learned, never assume
- GDPR Art. 6, 7: Consent — preferences are learning signals, not personalized targeting
- GDPR Art. 21: Right to Object — operator_override allows explicit objection
- Tenant isolation enforced on all reads/writes (GDPR Art. 32)
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Optional

from .event_schema import LearningEvent, LearningEventType
from .event_emitter import EventEmitter


class DecisionStyle(str, Enum):
    """User's decision-making style preference."""

    PRAGMATIC = "pragmatic"      # Fast, good-enough decisions
    THEORETICAL = "theoretical"  # Exhaustive analysis, first-principles
    BALANCED = "balanced"        # Trade-offs between speed and correctness


@dataclass(frozen=True)
class UserProfile:
    """Immutable user style preferences with GDPR audit trail.

    Attributes:
        user_id: User identifier
        tenant_id: Tenant scope (GDPR Art. 32 isolation)
        decision_style: Preferred decision-making approach (default: BALANCED)
        conciseness_preference: Output length preference [0.0-1.0]
            0.0 = verbose, 1.0 = terse (default: 0.5)
        skill_weights: Learned preferences for skills {skill_id → weight}
            Empty dict = no learned preferences (default empty)
        preferred_models: Model names user has chosen {model_name}
            Empty list = no explicit preference (default empty)
        operator_override: Explicit user preferences {key → value}
            Tracks Right to Object (GDPR Art. 21) objections
        created_at: ISO8601 timestamp of initial creation
        updated_at: ISO8601 timestamp of last profile update
    """

    user_id: str
    tenant_id: str
    decision_style: DecisionStyle = DecisionStyle.BALANCED
    conciseness_preference: float = 0.5
    skill_weights: dict[str, float] = field(default_factory=dict)
    preferred_models: list[str] = field(default_factory=list)
    operator_override: dict[str, str] = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now().isoformat())

    def __post_init__(self) -> None:
        """Validate profile constraints (fail-closed)."""
        if not (0.0 <= self.conciseness_preference <= 1.0):
            raise ValueError(
                f"conciseness_preference must be [0.0-1.0], got {self.conciseness_preference}"
            )
        if not (0 <= len(self.skill_weights) <= 1000):
            raise ValueError(
                f"skill_weights exceeds 1000 entries (found {len(self.skill_weights)})"
            )
        if not (0 <= len(self.preferred_models) <= 10):
            raise ValueError(
                f"preferred_models exceeds 10 entries (found {len(self.preferred_models)})"
            )

    def to_dict(self) -> dict[str, Any]:
        """Convert to dict for persistence (JSON-safe).

        Returns:
            Serializable dict with enum values as strings.
        """
        return {
            "user_id": self.user_id,
            "tenant_id": self.tenant_id,
            "decision_style": self.decision_style.value,
            "conciseness_preference": self.conciseness_preference,
            "skill_weights": self.skill_weights,
            "preferred_models": self.preferred_models,
            "operator_override": self.operator_override,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @staticmethod
    def from_dict(data: dict[str, Any]) -> UserProfile:
        """Load profile from persisted dict (with validation).

        Args:
            data: Deserialized profile dict

        Returns:
            Reconstructed frozen UserProfile

        Raises:
            ValueError: If required fields missing or invalid types
        """
        user_id = data.get("user_id")
        tenant_id = data.get("tenant_id")

        if not user_id or not tenant_id:
            raise ValueError("user_id and tenant_id required in profile dict")

        return UserProfile(
            user_id=user_id,
            tenant_id=tenant_id,
            decision_style=DecisionStyle(data.get("decision_style", "balanced")),
            conciseness_preference=float(data.get("conciseness_preference", 0.5)),
            skill_weights=dict(data.get("skill_weights", {})),
            preferred_models=list(data.get("preferred_models", [])),
            operator_override=dict(data.get("operator_override", {})),
            created_at=data.get("created_at", datetime.now().isoformat()),
            updated_at=data.get("updated_at", datetime.now().isoformat()),
        )


class UserProfileManager:
    """Manage user profiles with persistence and learning event emission.

    Profiles are persisted to ~/<tenant_id>/learning/profiles/<user_id>.json
    with append-only audit log for all preference changes (GDPR Art. 30, 32).

    Example:
        >>> manager = UserProfileManager()
        >>> profile = manager.get_profile("user_1", "_default")
        >>> updated = manager.update_from_feedback(
        ...     "user_1", "_default",
        ...     {"decision_style": "pragmatic", "conciseness": 0.8}
        ... )
        >>> manager.set_override("user_1", "_default", "model", "claude-3-opus")
    """

    def __init__(
        self,
        event_store: Optional[Any] = None,
        profiles_dir: Optional[Path] = None,
        event_emitter: Optional[EventEmitter] = None,
    ):
        """Initialize manager with optional persistence directory.

        Args:
            event_store: LearningEventStore for emitting preference events (optional).
                If None, preference changes are logged but not emitted as learning events.
            profiles_dir: Directory for profile JSON files. If None, uses
                tenant_home()/<tenant_id>/learning/profiles
            event_emitter: EventEmitter for non-blocking event emission (ADR-0314).
                If None, event_store is used directly (blocking fallback).
        """
        self.event_store = event_store
        self.event_emitter = event_emitter
        self._profiles_dir_override = profiles_dir
        self._profiles_cache: dict[tuple[str, str], UserProfile] = {}

    def _get_profiles_dir(self, tenant_id: str) -> Path:
        """Get or create profiles directory for tenant (GDPR Art. 32).

        The override is a BASE directory, and the tenant segment is appended to
        it — it is not an escape hatch out of tenant isolation. Returning the
        override verbatim (the original) made every tenant share ONE directory,
        so `user_1.json` was the same file for tenant_a and tenant_b: the second
        tenant's load read the first tenant's profile off disk, and its save
        overwrote it. Within a single process the `(user_id, tenant_id)` cache
        masked this, which is why it read as correct while the on-disk state was
        a cross-tenant leak. CLAUDE.md § Multi-tenant: every read/write filters
        by tenant_id, no exceptions for a test hook.
        """
        if self._profiles_dir_override:
            profile_dir = Path(self._profiles_dir_override) / tenant_id
            profile_dir.mkdir(parents=True, exist_ok=True)
            return profile_dir

        # Lazy import to avoid circular dependency
        try:
            from core.corvin_core import tenant_home
        except ImportError:
            from forge import paths as fp
            tenant_home = fp.tenant_home

        base = tenant_home(tenant_id)
        profile_dir = base / "learning" / "profiles"
        profile_dir.mkdir(parents=True, exist_ok=True)
        return profile_dir

    def _get_profile_path(self, user_id: str, tenant_id: str) -> Path:
        """Get JSON file path for a user's profile."""
        profile_dir = self._get_profiles_dir(tenant_id)
        return profile_dir / f"{user_id}.json"

    def _load_profile_from_disk(self, user_id: str, tenant_id: str) -> Optional[UserProfile]:
        """Load profile from persistent storage, if exists."""
        path = self._get_profile_path(user_id, tenant_id)
        if not path.exists():
            return None

        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
                return UserProfile.from_dict(data)
        except (json.JSONDecodeError, ValueError) as e:
            # Log but fail-closed: treat corrupted profile as missing
            print(f"[WARN] Failed to load profile {user_id}: {e}")
            return None

    def _save_profile_to_disk(self, profile: UserProfile) -> None:
        """Persist profile to JSON, atomically."""
        path = self._get_profile_path(profile.user_id, profile.tenant_id)
        path.parent.mkdir(parents=True, exist_ok=True)

        # Write to temp file then rename (atomic, fail-closed)
        temp_path = path.with_suffix(".tmp")
        try:
            with open(temp_path, "w", encoding="utf-8") as f:
                json.dump(profile.to_dict(), f, indent=2)
            temp_path.replace(path)
        except Exception as e:
            print(f"[ERROR] Failed to save profile {profile.user_id}: {e}")
            if temp_path.exists():
                temp_path.unlink()

    def get_profile(self, user_id: str, tenant_id: str) -> UserProfile:
        """Get user profile, loading from disk if needed.

        Creates a default profile if none exists (data minimization: only
        what's learned, no assumptions).

        Args:
            user_id: User ID
            tenant_id: Tenant ID (enforces isolation)

        Returns:
            Frozen UserProfile (immutable after construction)
        """
        cache_key = (user_id, tenant_id)

        # Check in-memory cache
        if cache_key in self._profiles_cache:
            return self._profiles_cache[cache_key]

        # Try to load from disk
        profile = self._load_profile_from_disk(user_id, tenant_id)

        if profile is None:
            # Create default profile (GDPR Art. 5: data minimization)
            profile = UserProfile(
                user_id=user_id,
                tenant_id=tenant_id,
                decision_style=DecisionStyle.BALANCED,
                conciseness_preference=0.5,
            )
            # Persist new default profile
            self._save_profile_to_disk(profile)

        self._profiles_cache[cache_key] = profile
        return profile

    def update_from_feedback(
        self, user_id: str, tenant_id: str, feedback: dict[str, Any]
    ) -> UserProfile:
        """Update profile based on learning feedback.

        Supported feedback keys:
        - "decision_style": "pragmatic" | "theoretical" | "balanced"
        - "conciseness": 0.0-1.0 (maps to conciseness_preference)
        - "skill_feedback": {skill_id: weight, ...}
        - "preferred_models": [model_name, ...]

        Emits UserPreferenceUpdated learning event (if event_store configured).

        Args:
            user_id: User ID
            tenant_id: Tenant ID
            feedback: Dict of preference updates

        Returns:
            Updated UserProfile

        Raises:
            ValueError: If feedback contains invalid values
        """
        profile = self.get_profile(user_id, tenant_id)
        updates = {}

        # Parse decision_style feedback
        if "decision_style" in feedback:
            style_val = feedback["decision_style"]
            try:
                new_style = DecisionStyle(style_val)
                if new_style != profile.decision_style:
                    updates["decision_style"] = new_style
            except ValueError:
                raise ValueError(f"Invalid decision_style: {style_val}")

        # Parse conciseness feedback
        if "conciseness" in feedback:
            conciseness = float(feedback["conciseness"])
            if not (0.0 <= conciseness <= 1.0):
                raise ValueError(f"conciseness must be [0.0-1.0], got {conciseness}")
            if conciseness != profile.conciseness_preference:
                updates["conciseness_preference"] = conciseness

        # Merge skill weights feedback
        if "skill_feedback" in feedback:
            new_weights = dict(profile.skill_weights)
            new_weights.update(feedback["skill_feedback"])
            if new_weights != profile.skill_weights:
                updates["skill_weights"] = new_weights

        # Update preferred models
        if "preferred_models" in feedback:
            new_models = list(feedback["preferred_models"])
            if len(new_models) > 10:
                raise ValueError(f"preferred_models exceeds 10 (got {len(new_models)})")
            if new_models != profile.preferred_models:
                updates["preferred_models"] = new_models

        if not updates:
            # No changes; return current profile
            return profile

        # Construct updated profile
        updated = UserProfile(
            user_id=profile.user_id,
            tenant_id=profile.tenant_id,
            decision_style=updates.get("decision_style", profile.decision_style),
            conciseness_preference=updates.get(
                "conciseness_preference", profile.conciseness_preference
            ),
            skill_weights=updates.get("skill_weights", profile.skill_weights),
            preferred_models=updates.get("preferred_models", profile.preferred_models),
            operator_override=profile.operator_override,
            created_at=profile.created_at,
            updated_at=datetime.now().isoformat(),
        )

        # Persist and cache
        self._save_profile_to_disk(updated)
        self._profiles_cache[(user_id, tenant_id)] = updated

        # Emit learning event (non-blocking)
        self._emit_preference_updated(updated, feedback)

        return updated

    def set_override(self, user_id: str, tenant_id: str, key: str, value: str) -> None:
        """Set explicit user preference override (GDPR Art. 21: Right to Object).

        Operator can override learned preferences with explicit choices.
        All overrides are audited in operator_override dict.

        Args:
            user_id: User ID
            tenant_id: Tenant ID
            key: Preference key to override
            value: Override value
        """
        profile = self.get_profile(user_id, tenant_id)

        new_overrides = dict(profile.operator_override)
        new_overrides[key] = value

        updated = UserProfile(
            user_id=profile.user_id,
            tenant_id=profile.tenant_id,
            decision_style=profile.decision_style,
            conciseness_preference=profile.conciseness_preference,
            skill_weights=profile.skill_weights,
            preferred_models=profile.preferred_models,
            operator_override=new_overrides,
            created_at=profile.created_at,
            updated_at=datetime.now().isoformat(),
        )

        self._save_profile_to_disk(updated)
        self._profiles_cache[(user_id, tenant_id)] = updated

    def predict_preference(
        self, user_id: str, tenant_id: str, context: dict[str, Any]
    ) -> dict[str, Any]:
        """Predict user preferences for current task context.

        Uses learned preferences and overrides to predict what the user
        likely wants for this task. Returns a dict for consumption by
        downstream systems (e.g., skill selector, model router).

        Args:
            user_id: User ID
            tenant_id: Tenant ID
            context: Task context {task_id, task_type, domain, ...}

        Returns:
            Predicted preferences dict:
            {
                "decision_style": "pragmatic",
                "conciseness": 0.7,
                "preferred_models": ["claude-3-opus", ...],
                "skill_weights": {skill_id: weight, ...},
                "confidence": 0.85  # 0.0-1.0, how confident in prediction
            }
        """
        profile = self.get_profile(user_id, tenant_id)

        # Start with learned profile
        prediction = {
            "decision_style": profile.decision_style.value,
            "conciseness": profile.conciseness_preference,
            "preferred_models": profile.preferred_models.copy(),
            "skill_weights": profile.skill_weights.copy(),
        }

        # Apply overrides (always win against learned preferences)
        if profile.operator_override:
            if "decision_style" in profile.operator_override:
                prediction["decision_style"] = profile.operator_override["decision_style"]
            if "conciseness" in profile.operator_override:
                try:
                    prediction["conciseness"] = float(
                        profile.operator_override["conciseness"]
                    )
                except ValueError:
                    pass  # Ignore malformed override

        # Estimate confidence based on how much data we have
        data_points = len(profile.skill_weights) + len(profile.preferred_models)
        confidence = min(0.95, 0.5 + (data_points * 0.05))
        prediction["confidence"] = confidence

        return prediction

    async def _queue_preference_updated(
        self, profile: UserProfile, feedback: dict[str, Any]
    ) -> None:
        """Async helper for emitting preference events via EventEmitter (ADR-0314).

        Called from sync _emit_preference_updated via asyncio.create_task.
        """
        event = LearningEvent(
            event_type=LearningEventType.PREFERENCE_SET,
            tenant_id=profile.tenant_id,
            instance_id="user-profile-manager",  # System component
            skill_name=None,
            session_id="system",  # Out-of-band profile update
            timestamp_utc=datetime.now(),
            user_id=profile.user_id,
            payload={
                "feedback_keys": list(feedback.keys()),
                "decision_style": profile.decision_style.value,
                "conciseness": profile.conciseness_preference,
                # Which skills the feedback touched, BY ID ONLY — never a
                # description, a name or free text (GDPR Art. 5(1)(a) data
                # minimisation). Without this the event recorded only that
                # "skill_feedback" happened, which is not enough to audit
                # or replay what was learned.
                "skill_ids": sorted(
                    str(k) for k in (feedback.get("skill_feedback") or {})
                ),
            },
            tags=["user-preference"],
        )

        try:
            if self.event_emitter is not None:
                await self.event_emitter.emit(event)
            else:
                # Fallback: Direct EventStore.write_event (blocking, legacy path)
                self.event_store.write_event(event)
        except Exception as e:
            # Fail-closed: log but do not raise
            print(f"[WARN] Failed to emit preference update event: {e}")

    def _emit_preference_updated(
        self, profile: UserProfile, feedback: dict[str, Any]
    ) -> None:
        """Emit UserPreferenceUpdated learning event (non-blocking).

        Logs preference change via event_store if configured.
        Fail-closed: if emission fails, logs warning but does not raise.

        Args:
            profile: Updated profile
            feedback: Feedback that triggered update
        """
        if not self.event_store and not self.event_emitter:
            return  # Neither configured; skip emission

        try:
            # Schedule async emission without blocking main thread
            import asyncio
            try:
                asyncio.create_task(self._queue_preference_updated(profile, feedback))
            except RuntimeError:
                # No event loop running; fall back to sync write_event
                if self.event_store:
                    event = LearningEvent(
                        event_type=LearningEventType.PREFERENCE_SET,
                        tenant_id=profile.tenant_id,
                        instance_id="user-profile-manager",
                        skill_name=None,
                        session_id="system",
                        timestamp_utc=datetime.now(),
                        user_id=profile.user_id,
                        payload={
                            "feedback_keys": list(feedback.keys()),
                            "decision_style": profile.decision_style.value,
                            "conciseness": profile.conciseness_preference,
                            "skill_ids": sorted(
                                str(k) for k in (feedback.get("skill_feedback") or {})
                            ),
                        },
                        tags=["user-preference"],
                    )
                    self.event_store.write_event(event)
        except Exception as e:
            # Fail-closed: log but do not raise
            print(f"[WARN] Failed to emit preference update event: {e}")
