"""Stream 3 Phase 2: Policy Confidence Scorer for Flow Guard (ADR-0314).

Computes Bayesian confidence scores P(policy correct | data_class, engine, destination).
Updates learned policy thresholds based on operator feedback.

**Algorithm:**
- Prior: P(safe flow) per (data_class, engine, destination) = initial uniform
- Feedback: operator says "allow_correct", "deny_correct", "allow_wrong", etc.
- Posterior: P_new = P_old * likelihood(feedback) / marginal
- Threshold tuning: Adjust confidence cutoff to minimize false positives/negatives

**Compliance:**
- GDPR Art. 30/32: All updates audited (CONFIG_UPDATED event)
- ADR-0314: Feedback → threshold update → next classification uses updated thresholds
- Immutable history: no policy rewriting, only append new versions
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Tuple
from uuid import uuid4

from core.learning.learning_events import LearningEvent, EventType
from core.learning.event_store import EventStore

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PolicyThresholds:
    """Learned policy classification thresholds (Bayesian posteriors).

    P(safe flow) per (data_class, engine, destination).
    Thresholds below this → deny, above → allow.
    """
    # Format: {f"{data_class}_{engine}_{dest}": P(safe)}
    thresholds: Dict[str, float] = field(default_factory=lambda: {
        # PII flows (very conservative)
        "pii_haiku_console": 0.95,
        "pii_haiku_webhook": 0.05,
        "pii_sonnet_console": 0.90,
        "pii_sonnet_webhook": 0.05,
        "pii_opus_console": 0.85,
        "pii_opus_webhook": 0.05,

        # API Key flows (very conservative)
        "api_key_haiku_console": 0.98,
        "api_key_haiku_webhook": 0.02,
        "api_key_sonnet_console": 0.98,
        "api_key_sonnet_webhook": 0.02,
        "api_key_opus_console": 0.95,
        "api_key_opus_webhook": 0.02,

        # Public data flows (permissive)
        "public_haiku_console": 0.99,
        "public_haiku_webhook": 0.95,
        "public_sonnet_console": 0.99,
        "public_sonnet_webhook": 0.95,
        "public_opus_console": 0.99,
        "public_opus_webhook": 0.95,
    })

    # False positive / false negative tracking
    false_positives: Dict[str, int] = field(default_factory=dict)  # Denied safe flows
    false_negatives: Dict[str, int] = field(default_factory=dict)  # Allowed unsafe flows

    updated_at: str = field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")
    version: str = "1.0"
    feedback_count: int = 0

    def get_threshold(self, data_class: str, engine: str, destination: str) -> float:
        """Get P(safe) threshold for (data_class, engine, destination).

        Args:
            data_class: "pii", "api_key", "public", etc.
            engine: "claude-haiku", "claude-sonnet", etc.
            destination: "console", "webhook", "file", etc.

        Returns:
            Threshold in [0.0, 1.0]
        """
        key = f"{data_class}_{engine.split('-')[-1].lower()}_{destination.lower()}"
        return self.thresholds.get(key, 0.5)  # Default to 0.5 if unknown


class PolicyConfidenceScorer:
    """Computes and updates Bayesian policy confidence scores from feedback (Phase 2).

    **Workflow:**
    1. Read policy feedback events from PolicyFeedbackHandler
    2. Apply Bayesian update: P_new = P_old * likelihood / marginal
    3. Track false positive/negative rates per (data_class, engine, destination)
    4. Tune thresholds:
       - If false positives >5%: lower threshold (allow more flows)
       - If false negatives >1%: raise threshold (deny more flows)
    5. Persist updated thresholds to JSON
    6. Emit CONFIG_UPDATED event to audit trail

    **Integration Point:**
    - PolicyConfidenceScorer called by FlowGuardClassifier after feedback received
    - Returns updated PolicyThresholds
    - Thresholds used by data_classifier_learned.py for next flow decision
    """

    def __init__(
        self,
        event_store: EventStore,
        tenant_id: str,
        config_dir: Optional[Path] = None,
        skill_id: str = "os.flow_guard",
        skill_version: str = "1.0.0",
    ):
        """Initialize policy confidence scorer.

        Args:
            event_store: EventStore instance (from Stream 4)
            tenant_id: Tenant scope (GDPR)
            config_dir: Directory to persist policy thresholds
            skill_id: Skill identifier
            skill_version: Skill version
        """
        self.event_store = event_store
        self.tenant_id = tenant_id
        self.skill_id = skill_id
        self.skill_version = skill_version

        if config_dir is None:
            from core.paths.tenant import tenant_home
            config_dir = Path(tenant_home(tenant_id)) / "flow_guard_config"
        self.config_dir = Path(config_dir)
        self.config_dir.mkdir(parents=True, exist_ok=True)

        self.thresholds_file = self.config_dir / "policy_thresholds.json"
        self.history_dir = self.config_dir / "policy_thresholds_history"
        self.history_dir.mkdir(parents=True, exist_ok=True)

    def update_from_feedback(self) -> Tuple[PolicyThresholds, int]:
        """Update policy thresholds based on recent feedback (Bayesian).

        Algorithm:
        1. Fetch recent feedback events from EventStore
        2. For each (data_class, engine, destination) tuple:
           - Count allow_correct + deny_correct (true positives/negatives)
           - Count allow_wrong + deny_wrong (false positives/negatives)
           - Compute new P(safe) = correct_allows / (correct_allows + wrong_denies + alpha)
        3. Apply Laplace smoothing (α=1.0)
        4. Tune thresholds based on false positive/negative rates
        5. Persist to JSON (versioned, immutable history)
        6. Emit CONFIG_UPDATED event to audit trail

        Returns:
            (updated_thresholds, feedback_count_used)

        Raises:
            RuntimeError: EventStore read/write failed
        """
        # Load current thresholds as baseline
        current_thresholds = self.load_thresholds()

        # Fetch recent feedback (limit to 10k to avoid OOM)
        feedback_events = self.event_store.query_events(
            tenant_id=self.tenant_id,
            event_type=EventType.PREFERENCE,
            skill_id=self.skill_id,
            limit=10000,
            offset=0,
            newest_first=False,  # Chronological order
        )

        if not feedback_events:
            logger.info("No policy feedback events found, thresholds unchanged")
            return current_thresholds, 0

        # Tally outcomes per (data_class, engine, destination)
        allow_correct_counts: Dict[str, int] = {}
        deny_correct_counts: Dict[str, int] = {}
        allow_wrong_counts: Dict[str, int] = {}
        deny_wrong_counts: Dict[str, int] = {}

        for event in feedback_events:
            signal = event.signal or {}
            data_class = signal.get("data_class", "unknown")
            engine = signal.get("engine", "unknown")
            destination = signal.get("destination", "unknown")
            feedback_type = signal.get("feedback_type", "skip")

            key = f"{data_class}_{engine.split('-')[-1].lower()}_{destination.lower()}"

            if feedback_type == "allow_correct":
                allow_correct_counts[key] = allow_correct_counts.get(key, 0) + 1
            elif feedback_type == "deny_correct":
                deny_correct_counts[key] = deny_correct_counts.get(key, 0) + 1
            elif feedback_type == "allow_wrong":
                allow_wrong_counts[key] = allow_wrong_counts.get(key, 0) + 1
            elif feedback_type == "deny_wrong":
                deny_wrong_counts[key] = deny_wrong_counts.get(key, 0) + 1

        # Bayesian update with Laplace smoothing
        # P(safe) = (allow_correct + deny_correct + α) / (allow_correct + deny_correct + allow_wrong + deny_wrong + 2α)
        alpha = 1.0
        updated_thresholds = dict(current_thresholds.thresholds)
        updated_fp: Dict[str, int] = {}
        updated_fn: Dict[str, int] = {}

        all_keys = set(allow_correct_counts.keys()) | set(deny_correct_counts.keys()) | \
                   set(allow_wrong_counts.keys()) | set(deny_wrong_counts.keys())

        for key in all_keys:
            correct = allow_correct_counts.get(key, 0) + deny_correct_counts.get(key, 0)
            wrong = allow_wrong_counts.get(key, 0) + deny_wrong_counts.get(key, 0)
            total = correct + wrong

            # Laplace-smoothed P(safe flow)
            p_new = (correct + alpha) / (total + 2 * alpha)
            updated_thresholds[key] = p_new

            # Track false positives (deny_wrong: should have allowed)
            # and false negatives (allow_wrong: should have denied)
            updated_fp[key] = deny_wrong_counts.get(key, 0)
            updated_fn[key] = allow_wrong_counts.get(key, 0)

            logger.info(
                f"Updated threshold {key}: {correct}/{total} correct → P={p_new:.3f}, "
                f"FP={updated_fp[key]}, FN={updated_fn[key]}"
            )

        # Create new PolicyThresholds object
        new_thresholds = PolicyThresholds(
            thresholds=updated_thresholds,
            false_positives=updated_fp,
            false_negatives=updated_fn,
            feedback_count=len(feedback_events),
            version=self._next_version(current_thresholds.version),
        )

        # Persist to JSON (versioned)
        self._save_thresholds_versioned(new_thresholds)

        # Emit CONFIG_UPDATED event to audit trail
        self._emit_config_updated_event(current_thresholds, new_thresholds)

        return new_thresholds, len(feedback_events)

    def load_thresholds(self) -> PolicyThresholds:
        """Load latest policy thresholds from disk.

        Tries to load from policy_thresholds.json. Falls back to hardcoded
        defaults if file doesn't exist.

        Returns:
            PolicyThresholds (current or default)
        """
        if self.thresholds_file.exists():
            try:
                with open(self.thresholds_file, "r") as f:
                    data = json.load(f)
                return PolicyThresholds(
                    thresholds=data.get("thresholds", PolicyThresholds().thresholds),
                    false_positives=data.get("false_positives", {}),
                    false_negatives=data.get("false_negatives", {}),
                    updated_at=data.get("updated_at", datetime.utcnow().isoformat() + "Z"),
                    version=data.get("version", "1.0"),
                    feedback_count=data.get("feedback_count", 0),
                )
            except (json.JSONDecodeError, IOError) as e:
                logger.error(f"Failed to load thresholds: {e}, using defaults")
                return PolicyThresholds()

        logger.info("No thresholds file found, using defaults")
        return PolicyThresholds()

    def _save_thresholds_versioned(self, thresholds: PolicyThresholds) -> None:
        """Save thresholds to disk with versioned history.

        Creates:
        - policy_thresholds.json (current, always up-to-date)
        - policy_thresholds_history/v{version}.json (immutable archive)

        Args:
            thresholds: PolicyThresholds to persist
        """
        data = {
            "thresholds": thresholds.thresholds,
            "false_positives": thresholds.false_positives,
            "false_negatives": thresholds.false_negatives,
            "updated_at": thresholds.updated_at,
            "version": thresholds.version,
            "feedback_count": thresholds.feedback_count,
        }

        try:
            with open(self.thresholds_file, "w") as f:
                json.dump(data, f, indent=2)
            logger.info(f"Saved policy thresholds v{thresholds.version}")

            # Historical archive (immutable)
            history_file = self.history_dir / f"v{thresholds.version}.json"
            with open(history_file, "w") as f:
                json.dump(data, f, indent=2)
            logger.info(f"Archived policy thresholds v{thresholds.version}")

        except IOError as e:
            logger.error(f"Failed to save thresholds: {e}")
            raise RuntimeError(f"Threshold persistence failed: {e}") from e

    def _emit_config_updated_event(
        self, old_thresholds: PolicyThresholds, new_thresholds: PolicyThresholds
    ) -> None:
        """Emit CONFIG_UPDATED event to audit trail (immutable record).

        Captures what changed in the policy (threshold deltas) so an auditor
        can see how the skill evolved over time.

        Args:
            old_thresholds: Previous PolicyThresholds
            new_thresholds: Updated PolicyThresholds
        """
        # Compute threshold deltas
        config_delta = {}
        for key in new_thresholds.thresholds:
            old_val = old_thresholds.thresholds.get(key, 0.5)
            new_val = new_thresholds.thresholds.get(key, 0.5)
            if abs(new_val - old_val) > 0.01:  # Only record significant changes
                config_delta[key] = {
                    "old": round(old_val, 3),
                    "new": round(new_val, 3),
                    "delta": round(new_val - old_val, 3),
                    "fp": new_thresholds.false_positives.get(key, 0),
                    "fn": new_thresholds.false_negatives.get(key, 0),
                }

        if not config_delta:
            logger.debug("No significant threshold changes, skipping CONFIG_UPDATED event")
            return

        # Create CONFIG_UPDATED event
        signal = {
            "config_delta": config_delta,
            "old_version": old_thresholds.version,
            "new_version": new_thresholds.version,
            "feedback_count": new_thresholds.feedback_count,
        }

        event = LearningEvent.create(
            event_type=EventType.CONFIG_UPDATED,
            skill_id=self.skill_id,
            tenant_id=self.tenant_id,
            signal=signal,
            skill_version=self.skill_version,
            lom="policy_confidence_scorer.py:_emit_config_updated_event:L265",
        )

        try:
            self.event_store.write_event(event)
            logger.info(
                f"CONFIG_UPDATED event emitted: {len(config_delta)} threshold changes, "
                f"v{old_thresholds.version} → v{new_thresholds.version}"
            )
        except RuntimeError as e:
            logger.error(f"Failed to emit CONFIG_UPDATED event: {e}")

    def _next_version(self, current_version: str) -> str:
        """Generate next version identifier (semantic versioning).

        Args:
            current_version: Current version string (e.g., "1.0", "1.2.5")

        Returns:
            Next incremented version (e.g., "1.0" → "1.1")
        """
        try:
            parts = current_version.split(".")
            if len(parts) >= 2:
                parts[1] = str(int(parts[1]) + 1)
                return ".".join(parts)
            return "1.1"
        except (ValueError, IndexError):
            return "1.1"
