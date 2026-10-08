"""Learning Optimizer for Layer Forge (Phase 3 C, ADR-2227 amendment).

Analyzes operator feedback (review overrides + outcome feedback) to tune the
adversarial REVIEW phase LLM prompt. When patterns emerge (operators frequently
override FLAGGED entries that then succeed), the optimizer suggests relaxing
the review prompt to reduce false-positive flags.

Feedback loop closure:
1. Operator overrides a FLAGGED definition (Phase 3 A)
2. Definition deploys successfully (Phase 3 A outcome feedback)
3. Optimizer detects pattern: override+success shows review was too cautious
4. Optimizer suggests/applies prompt version update (Phase 3 C)
5. Next review runs with updated prompt (fewer false positives)

Config versioning:
- Each REVIEW_PROMPT update is immutable (v1 → v2 → v3...)
- ReviewVerdict records which prompt version was used
- Audit trail: optimizer_config_updated events with (old, new, reason)
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


class OptimizationSignal(Enum):
    """Signals that indicate over-cautious or under-cautious review behavior."""
    OVERCAUTIOUS = "overcautious"      # Overrides succeed → review flags too much
    UNDERCAUTIOUS = "undercautious"    # Overrides fail → review not cautious enough
    NEUTRAL = "neutral"                 # No clear signal (balanced success/fail)


@dataclass(frozen=True)
class FeedbackPattern:
    """Aggregate feedback signal for one confidence threshold."""
    total_overrides: int                # Total operator overrides observed
    successful_overrides: int           # Overrides that led to deployment success
    failed_overrides: int               # Overrides that led to failure
    success_rate: float                 # successful / total (0.0-1.0)
    confidence_delta: float             # Aggregate confidence change
    signal: OptimizationSignal          # Interpretation of the pattern
    review_prompt_version: str          # Current prompt version when these overrides occurred

    @property
    def has_enough_data(self) -> bool:
        """True if there are enough override outcomes to compare rates at all (>= 5)."""
        return self.total_overrides >= 5

    @property
    def is_significant(self) -> bool:
        """True if this pattern merits a prompt update.

        Per signal: enough data AND the rate that signal is defined by — OVERCAUTIOUS needs
        >= 70 % successful overrides, UNDERCAUTIOUS <= 40 % (the thresholds that produce the
        signal in ``analyze``). This used to demand >= 70 % for BOTH, which an undercautious
        pattern (<= 40 % by construction) can never meet: the optimizer could relax a review
        but never tighten one — the half that matters when the review misses real problems.
        """
        if not self.has_enough_data:
            return False
        if self.signal == OptimizationSignal.OVERCAUTIOUS:
            return self.success_rate >= 0.70
        if self.signal == OptimizationSignal.UNDERCAUTIOUS:
            return self.success_rate <= 0.40
        return False


@dataclass(frozen=True)
class PromptVersion:
    """Immutable record of a prompt version (Phase 4 A2: Canary Rollout support)."""
    version: str                        # e.g., "v1.0", "v1.1", "v2.0"
    timestamp: str                      # ISO8601 timestamp when created
    reason: str                         # Why this version was created (e.g., "initial", "overcautious_feedback")
    prompt_text: str                    # The full prompt (immutable)
    parent_version: Optional[str] = None    # Previous version, if updated from one
    rollout_percentage: int = 100       # Canary % (0-100): v1.0 always 100, new versions start at canary_percentage (e.g. 10)


@dataclass(frozen=True)
class OptimizerUpdate:
    """Suggested or applied optimizer update."""
    old_version: str
    new_version: str
    reason: str
    signal: OptimizationSignal
    success_rate: float                 # Evidence: override success rate
    total_overrides: int                # Evidence: number of overrides analyzed
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")


class ReviewPromptVersions:
    """Manages immutable REVIEW_PROMPT version history with Canary rollout (Phase 4 A2).

    Each version is immutable, timestamped, and carries a rollout_percentage (0-100).
    v1.0 always has 100%; new versions start at canary_percentage (e.g., 10%).

    Versions live in a JSON file:
    {
        "current": "v2.0",
        "canary_percentage": 10,
        "versions": {
            "v1.0": {...PromptVersion...},
            "v2.0": {...PromptVersion...}
        }
    }
    """

    def __init__(self, storage_path: Path, canary_percentage: int = 10):
        """Initialize from file. Creates v1.0 if none exists.

        Args:
            storage_path: Where to store prompt version history
            canary_percentage: What % new versions start at (0-100, default 10)
        """
        self.storage_path = Path(storage_path)
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        self.canary_percentage = max(0, min(100, canary_percentage))  # Clamp to 0-100
        self._data = self._load()

    def _load(self) -> dict:
        """Load version history from disk, or initialize with v1.0."""
        if not self.storage_path.exists():
            # Initialize with v1.0 (base prompt, always at 100%)
            from .review import REVIEW_PROMPT
            v1_0 = PromptVersion(
                version="v1.0",
                timestamp=datetime.utcnow().isoformat() + "Z",
                reason="initial",
                prompt_text=REVIEW_PROMPT,
                parent_version=None,
                rollout_percentage=100,  # v1.0 always at 100%
            )
            return {
                "current": "v1.0",
                "canary_percentage": self.canary_percentage,
                "versions": {
                    "v1.0": self._serialize_version(v1_0),
                }
            }
        try:
            data = {}
            with open(self.storage_path, "r") as f:
                data = json.load(f)
            # Ensure canary_percentage is in the loaded data
            if "canary_percentage" not in data:
                data["canary_percentage"] = self.canary_percentage
            return data
        except Exception as e:
            logger.error("Failed to load prompt versions from %s: %s", self.storage_path, e)
            return {}

    @staticmethod
    def _serialize_version(v: PromptVersion) -> dict:
        """Convert PromptVersion to JSON-serializable dict."""
        return {
            "version": v.version,
            "timestamp": v.timestamp,
            "reason": v.reason,
            "prompt_text": v.prompt_text,
            "parent_version": v.parent_version,
            "rollout_percentage": v.rollout_percentage,
        }

    @staticmethod
    def _deserialize_version(d: dict) -> PromptVersion:
        """Convert JSON dict to PromptVersion."""
        return PromptVersion(
            version=d["version"],
            timestamp=d["timestamp"],
            reason=d["reason"],
            prompt_text=d["prompt_text"],
            parent_version=d.get("parent_version"),
            rollout_percentage=d.get("rollout_percentage", 100),  # Default to 100 if missing (backward compat)
        )

    def current_version(self) -> PromptVersion:
        """Get the currently active prompt version."""
        current_key = self._data.get("current", "v1.0")
        versions = self._data.get("versions", {})
        version_dict = versions.get(current_key)
        if not version_dict:
            raise ValueError(f"Current version {current_key} not found in history")
        return self._deserialize_version(version_dict)

    def get_version(self, version: str) -> Optional[PromptVersion]:
        """Get a specific prompt version by key."""
        versions = self._data.get("versions", {})
        version_dict = versions.get(version)
        if not version_dict:
            return None
        return self._deserialize_version(version_dict)

    def add_version(self, new_version: PromptVersion) -> None:
        """Add a new immutable version and mark it as current."""
        versions = self._data.get("versions", {})
        if new_version.version in versions:
            raise ValueError(f"Version {new_version.version} already exists")
        versions[new_version.version] = self._serialize_version(new_version)
        self._data["versions"] = versions
        self._data["current"] = new_version.version
        self._persist()

    def set_current(self, version: str) -> None:
        """Make an EXISTING version the current one (canary rollback). Versions stay immutable:
        nothing is added or changed, only the pointer moves."""
        if version not in self._data.get("versions", {}):
            raise ValueError(f"Cannot make unknown version {version!r} current")
        self._data["current"] = version
        self._persist()

    def _persist(self) -> None:
        """Write version history to disk (atomic swap)."""
        import tempfile
        try:
            with tempfile.NamedTemporaryFile(mode="w", dir=self.storage_path.parent, delete=False) as f:
                json.dump(self._data, f, indent=2)
                temp_path = f.name
            # Atomic move
            temp_path_obj = Path(temp_path)
            temp_path_obj.replace(self.storage_path)
        except Exception as e:
            logger.error("Failed to persist prompt versions to %s: %s", self.storage_path, e)


class FeedbackAnalyzer:
    """Analyzes feedback signals from the learning event store to detect patterns.

    Reads OUTCOME events where:
    - Signal has entry_id + version + success flag
    - Signal has review_override_applied flag (Phase 3 A)

    Aggregates patterns and suggests prompt updates.
    """

    def __init__(self, tenant_id: str, event_store: Optional[Any] = None):
        """Initialize analyzer for a tenant's feedback signals.

        Args:
            tenant_id: The tenant to analyze
            event_store: Learning EventStore (from booted registry); default is None
        """
        self.tenant_id = tenant_id
        self.event_store = event_store

    def get_event_store(self) -> Optional[Any]:
        """Resolve the learning event store (booted registry or passed explicitly)."""
        if self.event_store is not None:
            return self.event_store
        try:
            from core.learning.outcome_sink import learning_emitter
            em = learning_emitter()
            return getattr(em, "store", None) if em else None
        except Exception:
            return None

    def analyze_overrides_per_version(self, limit: int = 50, lookback_days: int = 14) -> dict[str, FeedbackPattern]:
        """Analyze recent overrides grouped by prompt_version (Phase 4 A2: per-version tracking).

        Returns a dict mapping prompt_version → FeedbackPattern.
        """
        store = self.get_event_store()
        if store is None:
            logger.warning("No event store available for per-version feedback analysis (tenant %s)", self.tenant_id)
            return {}

        try:
            from core.learning.learning_events import EventType
            cutoff_date = (datetime.utcnow() - timedelta(days=lookback_days)).isoformat() + "Z"
            events = store.query_events(
                self.tenant_id,
                event_type=EventType.OUTCOME,
                limit=limit * 2,  # Fetch more to account for multiple versions
            )
        except Exception as e:
            logger.warning("Failed to query outcome events (tenant %s): %s", self.tenant_id, type(e).__name__)
            return {}

        # Group events by prompt_version
        by_version: dict[str, list] = {}
        for event in events:
            signal = event.signal or {}
            if signal.get("review_override_applied") and signal.get("entry_id"):
                try:
                    event_ts = datetime.fromisoformat(event.timestamp.replace("Z", "+00:00"))
                    if event_ts.isoformat() >= cutoff_date:
                        pv = signal.get("prompt_version", "v1.0")
                        if pv not in by_version:
                            by_version[pv] = []
                        by_version[pv].append(event)
                except (ValueError, AttributeError):
                    pass

        # Analyze each version separately
        results = {}
        for version, override_events in by_version.items():
            if not override_events:
                continue
            successful = sum(1 for e in override_events if (e.signal or {}).get("success") is True)
            failed = len(override_events) - successful
            success_rate = successful / len(override_events) if override_events else 0.0

            if success_rate >= 0.70:
                signal = OptimizationSignal.OVERCAUTIOUS
                confidence_delta = 0.15
            elif success_rate <= 0.40:
                signal = OptimizationSignal.UNDERCAUTIOUS
                confidence_delta = -0.20
            else:
                signal = OptimizationSignal.NEUTRAL
                confidence_delta = 0.0

            results[version] = FeedbackPattern(
                total_overrides=len(override_events),
                successful_overrides=successful,
                failed_overrides=failed,
                success_rate=success_rate,
                confidence_delta=confidence_delta,
                signal=signal,
                review_prompt_version=version,
            )

        return results

    def analyze_recent_overrides(self, limit: int = 50, lookback_days: int = 14) -> FeedbackPattern:
        """Analyze recent operator overrides to detect review-prompt tuning signals.

        Reads OUTCOME events from the last `lookback_days` where:
        - entry_id exists (Layer Forge definition)
        - review_override_applied = true
        - outcome (success | failure) is recorded

        Args:
            limit: Max events to read
            lookback_days: Only consider events from the last N days

        Returns:
            FeedbackPattern with aggregated signal
        """
        store = self.get_event_store()
        if store is None:
            logger.warning("No event store available for feedback analysis (tenant %s)", self.tenant_id)
            return FeedbackPattern(
                total_overrides=0, successful_overrides=0, failed_overrides=0,
                success_rate=0.0, confidence_delta=0.0, signal=OptimizationSignal.NEUTRAL,
                review_prompt_version="v1.0"
            )

        try:
            from core.learning.learning_events import EventType
            cutoff_date = (datetime.utcnow() - timedelta(days=lookback_days)).isoformat() + "Z"
            events = store.query_events(
                self.tenant_id,
                event_type=EventType.OUTCOME,
                limit=limit,
            )
        except Exception as e:
            logger.warning("Failed to query outcome events (tenant %s): %s", self.tenant_id, type(e).__name__)
            return FeedbackPattern(
                total_overrides=0, successful_overrides=0, failed_overrides=0,
                success_rate=0.0, confidence_delta=0.0, signal=OptimizationSignal.NEUTRAL,
                review_prompt_version="v1.0"
            )

        # Filter to override events only
        override_events = []
        for event in events:
            signal = event.signal or {}
            # Check if this is a Layer Forge override outcome
            if signal.get("review_override_applied") and signal.get("entry_id"):
                # Only count events within lookback window
                try:
                    event_ts = datetime.fromisoformat(event.timestamp.replace("Z", "+00:00"))
                    if event_ts.isoformat() >= cutoff_date:
                        override_events.append(event)
                except (ValueError, AttributeError):
                    pass

        if not override_events:
            return FeedbackPattern(
                total_overrides=0, successful_overrides=0, failed_overrides=0,
                success_rate=0.0, confidence_delta=0.0, signal=OptimizationSignal.NEUTRAL,
                review_prompt_version="v1.0"
            )

        # Aggregate success/failure
        successful = sum(1 for e in override_events if (e.signal or {}).get("success") is True)
        failed = len(override_events) - successful
        success_rate = successful / len(override_events) if override_events else 0.0

        # Compute aggregate confidence delta
        # High override success rate = review was too cautious (prompt should be relaxed)
        if success_rate >= 0.70:
            signal = OptimizationSignal.OVERCAUTIOUS
            confidence_delta = 0.15  # Positive boost to relax the review prompt
        elif success_rate <= 0.40:
            signal = OptimizationSignal.UNDERCAUTIOUS
            confidence_delta = -0.20  # Negative penalty to tighten the review prompt
        else:
            signal = OptimizationSignal.NEUTRAL
            confidence_delta = 0.0

        # Infer current prompt version from events (use the most recent one)
        current_prompt_version = "v1.0"
        for event in override_events:
            signal_data = event.signal or {}
            if "prompt_version" in signal_data:
                current_prompt_version = signal_data["prompt_version"]

        return FeedbackPattern(
            total_overrides=len(override_events),
            successful_overrides=successful,
            failed_overrides=failed,
            success_rate=success_rate,
            confidence_delta=confidence_delta,
            signal=signal,
            review_prompt_version=current_prompt_version,
        )


class OptimizerEngine:
    """Orchestrates feedback analysis and applies optimizer updates.

    Given a FeedbackPattern, suggests or applies prompt version updates.
    """

    def __init__(self, tenant_id: str, storage_root: Path):
        """Initialize optimizer for a tenant.

        Args:
            tenant_id: Tenant scope
            storage_root: Where to store prompt version history
        """
        self.tenant_id = tenant_id
        self.versions = ReviewPromptVersions(storage_root / "prompt_versions.json")

    def check_canary_regression(self, versions: dict[str, FeedbackPattern]) -> Optional[str]:
        """Check if canary version shows regression vs previous version (Phase 4 A2).

        Returns the canary version ID if regression detected, None otherwise.
        Regression = canary success_rate < previous version success_rate.
        """
        if not versions or len(versions) < 2:
            return None

        current = self.versions.current_version()
        current_version_key = current.version

        # Get current version pattern
        current_pattern = versions.get(current_version_key)
        # "Enough data", NOT is_significant: that one also demands a success rate >= 70 %, so a
        # canary that did badly (80 % -> 20 %) counted as "no significant data" and the regression
        # guard only ever fired for mild drops (95 % -> 75 %), never for the severe ones.
        if not current_pattern or not current_pattern.has_enough_data:
            return None  # Too few outcomes for the current version to judge

        # Get parent version pattern
        if not current.parent_version:
            return None  # No parent to compare against

        parent_pattern = versions.get(current.parent_version)
        if not parent_pattern or not parent_pattern.has_enough_data:
            return None  # Too few outcomes for the parent to compare against

        # Check for regression: current success_rate < parent success_rate
        if current_pattern.success_rate < parent_pattern.success_rate:
            logger.warning(
                "Canary regression detected (tenant %s): %s success %.1f%% < parent %s %.1f%%",
                self.tenant_id, current_version_key, current_pattern.success_rate * 100,
                current.parent_version, parent_pattern.success_rate * 100
            )
            return current_version_key

        return None

    def rollback_canary(self, canary_version: str, canary_pattern: Optional[FeedbackPattern] = None,
                        parent_pattern: Optional[FeedbackPattern] = None) -> bool:
        """Rollback canary version to parent (Phase 4 A2).

        Freezes the canary version at current rollout_percentage (never promote further).
        Emits audit event layer_forge.canary_rollback.
        Returns True if successful.

        Args:
            canary_version: The canary version to rollback
            canary_pattern: FeedbackPattern for canary (for audit)
            parent_pattern: FeedbackPattern for parent (for audit)
        """
        try:
            canary = self.versions.get_version(canary_version)
            if not canary or not canary.parent_version:
                logger.error("Cannot rollback %s: no parent version", canary_version)
                return False

            # Get parent to make it current
            parent = self.versions.get_version(canary.parent_version)
            if not parent:
                logger.error("Cannot rollback: parent %s not found", canary.parent_version)
                return False

            # Freeze canary at current rollout_percentage (do NOT promote). The pointer lives in
            # ReviewPromptVersions: this method used to write ``self._data`` / call ``self._persist()``
            # on the ENGINE, which has neither — every rollback raised AttributeError, was swallowed
            # by the except below and returned False, so a regressing canary was never rolled back.
            self.versions.set_current(parent.version)

            # Emit audit event
            try:
                from core.orchestration.layer_forge import audit
                audit.emit(
                    "layer_forge.canary_rollback",
                    tenant_id=self.tenant_id,
                    canary_version=canary_version,
                    parent_version=parent.version,
                    reason="canary_regression_detected",
                    canary_success_rate=canary_pattern.success_rate if canary_pattern else 0.0,
                    parent_success_rate=parent_pattern.success_rate if parent_pattern else 0.0,
                )
            except Exception as e:
                logger.warning("Failed to emit canary_rollback audit event: %s", type(e).__name__)
                # Non-fatal: rollback already persisted, just audit failed

            logger.info(
                "Canary rollback complete (tenant %s): %s rolled back to %s",
                self.tenant_id, canary_version, parent.version
            )
            return True
        except Exception as e:
            logger.error("Rollback failed (tenant %s): %s", self.tenant_id, type(e).__name__)
            return False

    def suggest_update(self, pattern: FeedbackPattern) -> Optional[OptimizerUpdate]:
        """Suggest a prompt update based on feedback pattern.

        Returns OptimizerUpdate if the pattern is significant enough to warrant
        an update, otherwise None.
        """
        if not pattern.is_significant:
            logger.debug(
                "Feedback pattern not significant (tenant %s, %d overrides, %.1f%% success)",
                self.tenant_id, pattern.total_overrides, pattern.success_rate * 100
            )
            return None

        current = self.versions.current_version()
        if pattern.signal == OptimizationSignal.OVERCAUTIOUS:
            # Suggest relaxing the prompt (increase max_tokens, increase response_time, soften language)
            new_version_key = self._next_version(current.version)
            reason = f"Overcautious feedback: {pattern.successful_overrides}/{pattern.total_overrides} overrides succeeded"
            return OptimizerUpdate(
                old_version=current.version,
                new_version=new_version_key,
                reason=reason,
                signal=pattern.signal,
                success_rate=pattern.success_rate,
                total_overrides=pattern.total_overrides,
            )

        elif pattern.signal == OptimizationSignal.UNDERCAUTIOUS:
            # Suggest tightening the prompt (increase scrutiny, add more edge cases)
            new_version_key = self._next_version(current.version)
            reason = f"Undercautious feedback: {pattern.failed_overrides}/{pattern.total_overrides} overrides failed"
            return OptimizerUpdate(
                old_version=current.version,
                new_version=new_version_key,
                reason=reason,
                signal=pattern.signal,
                success_rate=pattern.success_rate,
                total_overrides=pattern.total_overrides,
            )

        return None

    def apply_update(self, update: OptimizerUpdate, new_prompt_text: str, canary_percentage: Optional[int] = None) -> None:
        """Apply a suggested update: create new prompt version and make it current.

        Args:
            update: The OptimizerUpdate to apply
            new_prompt_text: The new prompt text for this version
            canary_percentage: Starting rollout % for canary (default: self.versions.canary_percentage)

        Raises:
            ValueError if the update is invalid or the version already exists
        """
        rollout_pct = canary_percentage if canary_percentage is not None else self.versions.canary_percentage
        rollout_pct = max(0, min(100, rollout_pct))  # Clamp to 0-100

        new_version = PromptVersion(
            version=update.new_version,
            timestamp=update.timestamp,
            reason=update.reason,
            prompt_text=new_prompt_text,
            parent_version=update.old_version,
            rollout_percentage=rollout_pct,  # New versions start at canary percentage
        )
        self.versions.add_version(new_version)
        logger.info(
            "Optimizer applied update (tenant %s): %s → %s at %d%% canary (reason: %s)",
            self.tenant_id, update.old_version, update.new_version, rollout_pct, update.reason
        )

    @staticmethod
    def _next_version(current: str) -> str:
        """Compute the next version string. E.g., v1.0 → v1.1, v1.9 → v2.0."""
        parts = current[1:].split(".")  # Strip 'v', split on '.'
        if len(parts) != 2:
            return "v2.0"  # Fallback
        major, minor = int(parts[0]), int(parts[1])
        if minor < 9:
            return f"v{major}.{minor + 1}"
        else:
            return f"v{major + 1}.0"

    def emit_update_event(self, update: OptimizerUpdate, emitter: Optional[Any] = None) -> bool:
        """Emit an audit event for the optimizer update.

        Event: layer_forge.optimizer_config_updated
        Contains: old_version, new_version, reason, signal, success_rate, total_overrides

        Args:
            update: The OptimizerUpdate
            emitter: Explicit audit emitter (tests); default is through outcome_sink

        Returns:
            True if emitted successfully
        """
        try:
            from core.orchestration.layer_forge import audit
            event_id = audit.emit(
                "layer_forge.optimizer_config_updated",
                tenant_id=self.tenant_id,
                old_version=update.old_version,
                new_version=update.new_version,
                reason=update.reason,
                signal=update.signal.value,
                success_rate=update.success_rate,
                total_overrides=update.total_overrides,
            )
            return bool(event_id)
        except Exception as e:
            logger.warning("Failed to emit optimizer update event (tenant %s): %s", self.tenant_id, type(e).__name__)
            return False


@dataclass(frozen=True)
class GateThresholdPattern:
    """Gate-threshold tuning signal (mirrors FeedbackPattern for gates)."""
    gate_id: str                        # Quality gate identifier
    total_fails: int                    # Total FAIL verdicts for this gate
    override_successes: int             # Overridden FAILs that deployed successfully
    override_failures: int              # Overridden FAILs that failed
    override_success_rate: float        # successful / (successful + failed)
    signal: OptimizationSignal          # overcautious | undercautious | neutral

    @property
    def is_significant(self) -> bool:
        """True if this pattern warrants a threshold adjustment."""
        # Significant if: (1) enough data (>= 5 overrides), and (2) strong signal (>70%)
        total = self.override_successes + self.override_failures
        return total >= 5 and self.override_success_rate >= 0.70


class GateThresholdAnalyzer:
    """Analyzes gate failure patterns to suggest threshold tuning (Phase 4 A1).

    Reads gate FAIL outcomes from registry and correlates them with deployment outcomes.
    When a gate is overcautious (too many false positives), suggests relaxing its threshold.
    """

    def __init__(self, tenant_id: str, analytics_engine: Optional[Any] = None):
        """Initialize analyzer for a tenant's gate thresholds.

        Args:
            tenant_id: Tenant scope
            analytics_engine: LayerForgeAnalytics instance; default is created from registry
        """
        self.tenant_id = tenant_id
        self.analytics_engine = analytics_engine

    def analyze_gate(self, gate_id: str, lookback_days: int = 30) -> GateThresholdPattern:
        """Analyze a specific gate's FAIL→outcome correlation.

        Args:
            gate_id: Quality gate identifier
            lookback_days: Only consider outcomes from the last N days

        Returns:
            GateThresholdPattern with signal (overcautious | undercautious | neutral)
        """
        if self.analytics_engine is None:
            # Default: no analytics data available
            logger.warning("No analytics engine available for gate %s (tenant %s)", gate_id, self.tenant_id)
            return GateThresholdPattern(
                gate_id=gate_id, total_fails=0, override_successes=0, override_failures=0,
                override_success_rate=0.0, signal=OptimizationSignal.NEUTRAL
            )

        try:
            since_iso = (datetime.utcnow() - timedelta(days=lookback_days)).date().isoformat()
            correlation = self.analytics_engine.gate_outcome_correlation(gate_id, since_iso=since_iso)

            # Extract pattern from correlation result
            total_overrides = (
                correlation.get("override_successes", 0) + correlation.get("override_failures", 0)
            )
            success_rate = correlation.get("override_success_rate", 0.0)

            # Map success rate to signal
            if success_rate >= 0.70:
                signal = OptimizationSignal.OVERCAUTIOUS
            elif success_rate <= 0.40:
                signal = OptimizationSignal.UNDERCAUTIOUS
            else:
                signal = OptimizationSignal.NEUTRAL

            return GateThresholdPattern(
                gate_id=gate_id,
                total_fails=correlation.get("total_fails", 0),
                override_successes=correlation.get("override_successes", 0),
                override_failures=correlation.get("override_failures", 0),
                override_success_rate=success_rate,
                signal=signal,
            )
        except Exception as e:
            logger.warning("Failed to analyze gate %s (tenant %s): %s", gate_id, self.tenant_id, type(e).__name__)
            return GateThresholdPattern(
                gate_id=gate_id, total_fails=0, override_successes=0, override_failures=0,
                override_success_rate=0.0, signal=OptimizationSignal.NEUTRAL
            )

    def suggest_threshold_update(self, pattern: GateThresholdPattern) -> Optional[OptimizerUpdate]:
        """Suggest a threshold update based on gate failure pattern.

        Returns OptimizerUpdate if the pattern is significant enough to warrant an update,
        otherwise None.
        """
        if not pattern.is_significant:
            logger.debug(
                "Gate %s pattern not significant (tenant %s, %d overrides, %.1f%% success)",
                pattern.gate_id, self.tenant_id, pattern.override_successes + pattern.override_failures,
                pattern.override_success_rate * 100
            )
            return None

        if pattern.signal == OptimizationSignal.OVERCAUTIOUS:
            reason = (
                f"Overcautious gate: {pattern.override_successes}/"
                f"{pattern.override_successes + pattern.override_failures} overrides succeeded"
            )
            return OptimizerUpdate(
                old_version=f"gate_{pattern.gate_id}_v1.0",
                new_version=f"gate_{pattern.gate_id}_v1.1",
                reason=reason,
                signal=pattern.signal,
                success_rate=pattern.override_success_rate,
                total_overrides=pattern.override_successes + pattern.override_failures,
            )

        elif pattern.signal == OptimizationSignal.UNDERCAUTIOUS:
            reason = (
                f"Undercautious gate: {pattern.override_failures}/"
                f"{pattern.override_successes + pattern.override_failures} overrides failed"
            )
            return OptimizerUpdate(
                old_version=f"gate_{pattern.gate_id}_v1.0",
                new_version=f"gate_{pattern.gate_id}_v1.1",
                reason=reason,
                signal=pattern.signal,
                success_rate=pattern.override_success_rate,
                total_overrides=pattern.override_successes + pattern.override_failures,
            )

        return None


__all__ = [
    "OptimizationSignal",
    "FeedbackPattern",
    "PromptVersion",
    "OptimizerUpdate",
    "ReviewPromptVersions",
    "FeedbackAnalyzer",
    "OptimizerEngine",
    "GateThresholdPattern",
    "GateThresholdAnalyzer",
]
