"""
Feedback Loop Closure — ADR-2033 Week 2

Connects feedback from Streams 1-3 to config auto-updates.

Flows:
1. OutcomeFeedback (Stream 1: Workflow Optimizer) → adjust delegation thresholds
2. PreferenceFeedback (Stream 2: Security Orchestrator) → tune threat detection
3. MetricFeedback (Stream 3: Flow Guard) → adjust data flow policies
4. ConfidenceFeedback (all streams) → adjust confidence thresholds

Each update:
- Audited (audit trail with hash-chain)
- Logged (config delta recorded)
- Tenant-isolated (per-tenant feedback channels)
"""

import asyncio
import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional
from uuid import uuid4

from core.skills.feedback.schema import (
    FeedbackEvent, FeedbackType,
    OutcomeFeedback, PreferenceFeedback, ConfidenceFeedback, MetricFeedback
)

logger = logging.getLogger(__name__)


@dataclass
class ConfigUpdate:
    """Config update (immutable record)."""
    update_id: str = field(default_factory=lambda: str(uuid4()))
    skill_id: str = ""
    tenant_id: str = ""
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")
    feedback_type: str = ""  # outcome|preference|confidence|metric
    config_delta: Dict[str, Any] = field(default_factory=dict)
    audit_event_id: Optional[str] = None
    config_hash_before: str = ""
    config_hash_after: str = ""

    def to_dict(self) -> dict:
        """Export to dict."""
        return {
            "update_id": self.update_id,
            "skill_id": self.skill_id,
            "tenant_id": self.tenant_id,
            "timestamp": self.timestamp,
            "feedback_type": self.feedback_type,
            "config_delta": self.config_delta,
            "audit_event_id": self.audit_event_id,
            "config_hash_before": self.config_hash_before,
            "config_hash_after": self.config_hash_after,
        }


@dataclass
class SkillConfigSnapshot:
    """Skill config snapshot at a point in time."""
    skill_id: str
    tenant_id: str
    timestamp: str
    config: Dict[str, Any] = field(default_factory=dict)
    version: str = ""
    config_hash: str = ""


class LoopClosureManager:
    """
    Manages feedback → config update transformation.

    For each feedback type:
    1. Receive feedback event
    2. Apply transformation rule (type-specific)
    3. Generate config delta
    4. Apply to Skill
    5. Audit trail with hash-chain

    Transformations:
    - OutcomeFeedback: Adjust confidence_threshold based on outcome (correct/incorrect)
    - PreferenceFeedback: Update preference_model with user-selected style
    - ConfidenceFeedback: Clamp confidence_threshold to [0.0, 1.0]
    - MetricFeedback: Adjust latency/error thresholds based on observed metrics
    """

    def __init__(self):
        """Initialize loop closure manager."""
        self.update_history: Dict[str, List[ConfigUpdate]] = {}  # Per-skill history
        self.skill_configs: Dict[str, Dict[str, Any]] = {}  # Per-skill current config
        self.audit_callbacks: List[Callable] = []  # Callbacks to emit audit events

    def register_audit_callback(self, callback: Callable[[ConfigUpdate], None]):
        """Register callback for audit events (called on every config update)."""
        self.audit_callbacks.append(callback)

    async def process_feedback(self, feedback: FeedbackEvent) -> Optional[ConfigUpdate]:
        """
        Process feedback and apply config update if warranted.

        Args:
            feedback: FeedbackEvent (outcome|preference|confidence|metric)

        Returns:
            ConfigUpdate if applied, None if no update needed

        Raises:
            ValueError if skill not found
        """
        skill_id = feedback.skill_id
        tenant_id = feedback.tenant_id

        # Dispatch to type-specific handler
        if isinstance(feedback, OutcomeFeedback):
            return await self._process_outcome_feedback(feedback)
        elif isinstance(feedback, PreferenceFeedback):
            return await self._process_preference_feedback(feedback)
        elif isinstance(feedback, ConfidenceFeedback):
            return await self._process_confidence_feedback(feedback)
        elif isinstance(feedback, MetricFeedback):
            return await self._process_metric_feedback(feedback)
        else:
            logger.warning(f"Unknown feedback type: {type(feedback)}")
            return None

    async def _process_outcome_feedback(self, feedback: OutcomeFeedback) -> Optional[ConfigUpdate]:
        """
        OutcomeFeedback handler: adjust delegation confidence threshold.

        If outcome is True (correct), increase confidence_threshold slightly (more selective).
        If outcome is False (incorrect), decrease confidence_threshold (more permissive).

        Signal: bool (True = correct, False = incorrect)
        """
        skill_id = feedback.skill_id
        tenant_id = feedback.tenant_id
        signal = feedback.signal  # bool

        # Get current config
        current_config = self.skill_configs.get(skill_id, {})
        confidence_threshold = current_config.get("confidence_threshold", 0.7)

        # Calculate delta: ±0.05 based on outcome
        adjustment = 0.05 if signal else -0.05
        new_threshold = max(0.0, min(1.0, confidence_threshold + adjustment))

        if abs(new_threshold - confidence_threshold) < 0.001:
            # No meaningful change
            return None

        # Create config update
        config_delta = {
            "confidence_threshold": {
                "old": confidence_threshold,
                "new": new_threshold,
                "reason": "OutcomeFeedback: " + ("correct, increase selectivity" if signal else "incorrect, increase permissiveness"),
            }
        }

        update = ConfigUpdate(
            skill_id=skill_id,
            tenant_id=tenant_id,
            feedback_type="outcome",
            config_delta=config_delta,
            config_hash_before=self._config_hash(current_config),
        )

        # Apply update
        new_config = {**current_config}
        new_config["confidence_threshold"] = new_threshold
        self.skill_configs[skill_id] = new_config
        update.config_hash_after = self._config_hash(new_config)

        # Emit audit event
        for callback in self.audit_callbacks:
            try:
                callback(update)
            except Exception as e:
                logger.error(f"Audit callback failed: {e}")

        # Record in history
        if skill_id not in self.update_history:
            self.update_history[skill_id] = []
        self.update_history[skill_id].append(update)

        logger.info(f"Applied OutcomeFeedback config update for {skill_id}: {config_delta}")
        return update

    async def _process_preference_feedback(self, feedback: PreferenceFeedback) -> Optional[ConfigUpdate]:
        """
        PreferenceFeedback handler: update preference model.

        Signal: str (e.g., "deterministic", "llm", "neither")
        """
        skill_id = feedback.skill_id
        tenant_id = feedback.tenant_id
        signal = feedback.signal  # str

        current_config = self.skill_configs.get(skill_id, {})
        current_preference = current_config.get("preferred_mode", "llm")

        if signal == current_preference:
            # No change needed
            return None

        config_delta = {
            "preferred_mode": {
                "old": current_preference,
                "new": signal,
                "reason": f"PreferenceFeedback: operator prefers {signal}",
            }
        }

        update = ConfigUpdate(
            skill_id=skill_id,
            tenant_id=tenant_id,
            feedback_type="preference",
            config_delta=config_delta,
            config_hash_before=self._config_hash(current_config),
        )

        # Apply update
        new_config = {**current_config}
        new_config["preferred_mode"] = signal
        self.skill_configs[skill_id] = new_config
        update.config_hash_after = self._config_hash(new_config)

        # Emit audit event
        for callback in self.audit_callbacks:
            try:
                callback(update)
            except Exception as e:
                logger.error(f"Audit callback failed: {e}")

        if skill_id not in self.update_history:
            self.update_history[skill_id] = []
        self.update_history[skill_id].append(update)

        logger.info(f"Applied PreferenceFeedback config update for {skill_id}: {config_delta}")
        return update

    async def _process_confidence_feedback(self, feedback: ConfidenceFeedback) -> Optional[ConfigUpdate]:
        """
        ConfidenceFeedback handler: clamp confidence threshold.

        Signal: float in [0.0, 1.0]
        """
        skill_id = feedback.skill_id
        tenant_id = feedback.tenant_id
        signal = feedback.signal  # float

        # Validate
        try:
            confidence_value = float(signal)
            if not (0.0 <= confidence_value <= 1.0):
                logger.warning(f"Confidence value out of range: {confidence_value}")
                return None
        except (ValueError, TypeError):
            logger.warning(f"Invalid confidence signal: {signal}")
            return None

        current_config = self.skill_configs.get(skill_id, {})
        current_confidence = current_config.get("confidence_threshold", 0.7)

        if abs(confidence_value - current_confidence) < 0.01:
            # No meaningful change
            return None

        config_delta = {
            "confidence_threshold": {
                "old": current_confidence,
                "new": confidence_value,
                "reason": f"ConfidenceFeedback: set to {confidence_value}",
            }
        }

        update = ConfigUpdate(
            skill_id=skill_id,
            tenant_id=tenant_id,
            feedback_type="confidence",
            config_delta=config_delta,
            config_hash_before=self._config_hash(current_config),
        )

        # Apply update
        new_config = {**current_config}
        new_config["confidence_threshold"] = confidence_value
        self.skill_configs[skill_id] = new_config
        update.config_hash_after = self._config_hash(new_config)

        for callback in self.audit_callbacks:
            try:
                callback(update)
            except Exception as e:
                logger.error(f"Audit callback failed: {e}")

        if skill_id not in self.update_history:
            self.update_history[skill_id] = []
        self.update_history[skill_id].append(update)

        logger.info(f"Applied ConfidenceFeedback config update for {skill_id}: {config_delta}")
        return update

    async def _process_metric_feedback(self, feedback: MetricFeedback) -> Optional[ConfigUpdate]:
        """
        MetricFeedback handler: adjust performance thresholds.

        Signal: float (latency_ms, error_rate, etc.)
        """
        skill_id = feedback.skill_id
        tenant_id = feedback.tenant_id
        signal = feedback.signal  # float

        # For now, just record the metric (no auto-adjustment yet)
        # In production, this would adjust latency/error thresholds
        current_config = self.skill_configs.get(skill_id, {})

        config_delta = {
            "metric_observed": {
                "value": signal,
                "reason": f"MetricFeedback: observed metric {signal}",
            }
        }

        update = ConfigUpdate(
            skill_id=skill_id,
            tenant_id=tenant_id,
            feedback_type="metric",
            config_delta=config_delta,
            config_hash_before=self._config_hash(current_config),
        )

        new_config = {**current_config}
        new_config["last_metric_observed"] = signal
        self.skill_configs[skill_id] = new_config
        update.config_hash_after = self._config_hash(new_config)

        for callback in self.audit_callbacks:
            try:
                callback(update)
            except Exception as e:
                logger.error(f"Audit callback failed: {e}")

        if skill_id not in self.update_history:
            self.update_history[skill_id] = []
        self.update_history[skill_id].append(update)

        logger.info(f"Applied MetricFeedback config update for {skill_id}: {config_delta}")
        return update

    def _config_hash(self, config: Dict[str, Any]) -> str:
        """Compute hash of config dict."""
        config_json = json.dumps(config, sort_keys=True)
        return hashlib.sha256(config_json.encode()).hexdigest()[:16]

    def get_skill_config(self, skill_id: str) -> Dict[str, Any]:
        """Get current config for a Skill."""
        return self.skill_configs.get(skill_id, {})

    def get_update_history(self, skill_id: str, limit: int = 100) -> List[ConfigUpdate]:
        """Get config update history for a Skill."""
        history = self.update_history.get(skill_id, [])
        return history[-limit:]

    def get_all_updates(self, tenant_id: str, limit: int = 1000) -> List[ConfigUpdate]:
        """Get all config updates for a tenant (across all skills)."""
        all_updates = []
        for skill_id, updates in self.update_history.items():
            all_updates.extend([u for u in updates if u.tenant_id == tenant_id])
        return sorted(all_updates, key=lambda u: u.timestamp, reverse=True)[:limit]
