"""Stream 1 Phase 3: A/B Testing Framework (Days 5–10 of Week 3).

Canary deployment logic for learned routing weights:
- 10% canary (learned) vs 90% control (baseline)
- Measure accuracy improvement
- Promotion decision: 10%→25%→50%→100% if improvement sustained

**Promotion thresholds:**
- Week 1 (10% canary): Promote if improvement > 2%
- Week 2 (25% canary): Promote if improvement > 1.5%
- Week 3 (50% canary): Promote if improvement > 1%
- Full rollout (100%): All tasks use learned weights

**Compliance:**
- GDPR Art. 30/32: All routing decisions audited
- ADR-0314: Metrics emitted as LearningEvent (metric type)
- Fail-closed: accuracy cannot decrease (revert on > 2% drop)
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Optional, Dict, List, Tuple, Any
from uuid import uuid4

from core.learning.event_store import EventStore
from core.learning.learning_events import LearningEvent, EventType
from core.skills.os_skills.learning_loop_utilities import (
    SyntheticTask,
    calculate_accuracy,
    estimate_confidence_interval,
)

logger = logging.getLogger(__name__)


class CanaryStage(str, Enum):
    """Canary deployment stages."""
    DISABLED = "disabled"      # No canary (100% control)
    STAGE_10 = "stage_10"      # 10% canary, 90% control
    STAGE_25 = "stage_25"      # 25% canary, 75% control
    STAGE_50 = "stage_50"      # 50% canary, 50% control
    STAGE_100 = "stage_100"    # 100% canary (full rollout)


@dataclass(frozen=True)
class CanaryConfig:
    """Immutable canary configuration."""
    stage: CanaryStage = CanaryStage.DISABLED
    enabled: bool = False
    canary_percentage: float = 0.0
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")
    promoted_at: Optional[str] = None
    promotion_reason: Optional[str] = None


@dataclass(frozen=True)
class CanaryMetrics:
    """Metrics for current canary stage."""
    stage: CanaryStage
    total_routed: int
    canary_routed: int
    control_routed: int
    canary_accuracy: float
    control_accuracy: float
    improvement_pct: float
    confidence_interval: Tuple[float, float]
    meets_promotion_threshold: bool
    recommendation: str  # "promote", "keep_monitoring", "revert"
    measured_at: str


class CanaryManager:
    """Manages A/B testing canary deployment for learned weights (Phase 3).

    Responsibilities:
    1. Track canary split (canary_percentage)
    2. Route incoming tasks: sample determines canary vs control
    3. Measure accuracy: track metrics per stage
    4. Promotion decision: compare against thresholds
    5. Audit trail: emit metrics as LearningEvent per config update
    """

    def __init__(
        self,
        event_store: EventStore,
        tenant_id: str = "_default",
        skill_id: str = "os.workflow_optimizer_l5",
        skill_version: str = "1.0.0",
        config_dir: Optional[Path] = None,
    ):
        """Initialize canary manager.

        Args:
            event_store: EventStore for metrics (audit-first)
            tenant_id: Tenant scope (GDPR)
            skill_id: Skill identifier
            skill_version: Skill version
            config_dir: Directory to persist canary config (default: ~/.corvin/tenants/<tid>/global/)
        """
        self.event_store = event_store
        self.tenant_id = tenant_id
        self.skill_id = skill_id
        self.skill_version = skill_version

        # Config directory
        if config_dir is None:
            from core.paths.tenant import tenant_home
            config_dir = Path(tenant_home(tenant_id)) / "workflow_optimizer_canary"
        self.config_dir = Path(config_dir)
        self.config_dir.mkdir(parents=True, exist_ok=True)

        self.config_file = self.config_dir / "canary_config.json"
        self.metrics_file = self.config_dir / "canary_metrics.jsonl"

        # Load or initialize config
        self.config = self._load_config()

        # Routing counters (per stage)
        self.routed_count = {"canary": 0, "control": 0}
        self.accuracy = {"canary": {}, "control": {}}  # task_id -> correct (bool)

    def start_canary(self, stage: CanaryStage) -> CanaryConfig:
        """Start canary deployment at specified stage.

        Args:
            stage: CanaryStage (STAGE_10, STAGE_25, etc.)

        Returns:
            Updated CanaryConfig

        Raises:
            ValueError: Invalid stage transition
            RuntimeError: Config write failed (audit-first)
        """
        if stage == CanaryStage.DISABLED:
            raise ValueError("Cannot start canary at DISABLED stage")

        if self.config.stage != CanaryStage.DISABLED:
            raise ValueError(
                f"Canary already running at {self.config.stage.value}, "
                f"cannot start at {stage.value}"
            )

        # Determine canary percentage
        canary_pct = {
            CanaryStage.STAGE_10: 0.10,
            CanaryStage.STAGE_25: 0.25,
            CanaryStage.STAGE_50: 0.50,
            CanaryStage.STAGE_100: 1.0,
        }.get(stage)

        if canary_pct is None:
            raise ValueError(f"Unknown canary stage: {stage}")

        # Create new config
        new_config = CanaryConfig(
            stage=stage,
            enabled=True,
            canary_percentage=canary_pct,
            created_at=datetime.utcnow().isoformat() + "Z",
        )

        # Persist config
        self._save_config(new_config)
        self.config = new_config

        # Emit audit event
        config_event = LearningEvent.create(
            event_type=EventType.CONFIG,
            skill_id=self.skill_id,
            tenant_id=self.tenant_id,
            signal={
                "action": "canary_started",
                "stage": stage.value,
                "canary_percentage": canary_pct,
            },
            skill_version=self.skill_version,
            lom="workflow_optimizer_skill.ab_testing:start_canary:L93",
        )

        try:
            self.event_store.write_event(config_event)
        except (RuntimeError, IOError) as e:
            logger.error(f"Failed to write canary start event: {e}")
            raise

        logger.info(f"Canary started: stage={stage.value}, percentage={canary_pct:.1%}")
        return new_config

    def should_use_canary(self) -> bool:
        """Determine if next routing should use canary (learned) weights.

        Uses deterministic random sampling based on canary percentage.

        Returns:
            True if should use canary, False if should use control (baseline)
        """
        if not self.config.enabled or self.config.canary_percentage == 0:
            return False

        # Deterministic sampling: use random() < percentage
        import random
        return random.random() < self.config.canary_percentage

    def record_routing(
        self,
        task_id: str,
        used_canary: bool,
        predicted_model: str,
        correct_model: str,
    ) -> None:
        """Record a routing decision for metrics.

        Args:
            task_id: Task identifier
            used_canary: True if canary (learned) weights used, False if control
            predicted_model: Model we predicted
            correct_model: Ground-truth correct model
        """
        arm = "canary" if used_canary else "control"
        self.routed_count[arm] += 1

        correct = predicted_model == correct_model
        if arm not in self.accuracy:
            self.accuracy[arm] = {}
        self.accuracy[arm][task_id] = correct

    def compute_metrics(self) -> CanaryMetrics:
        """Compute current canary metrics.

        Returns:
            CanaryMetrics with accuracy, improvement, recommendation
        """
        canary_correct = sum(1 for v in self.accuracy.get("canary", {}).values() if v)
        control_correct = sum(1 for v in self.accuracy.get("control", {}).values() if v)

        canary_total = self.routed_count["canary"]
        control_total = self.routed_count["control"]

        canary_accuracy = (
            canary_correct / canary_total if canary_total > 0 else 0.0
        )
        control_accuracy = (
            control_correct / control_total if control_total > 0 else 0.0
        )

        if control_accuracy == 0:
            improvement_pct = 0.0
        else:
            improvement_pct = (canary_accuracy - control_accuracy) / control_accuracy * 100

        ci = estimate_confidence_interval(
            accuracy=canary_accuracy,
            sample_size=canary_total,
        )

        # Promotion threshold logic
        thresholds = {
            CanaryStage.STAGE_10: 2.0,
            CanaryStage.STAGE_25: 1.5,
            CanaryStage.STAGE_50: 1.0,
        }

        stage_threshold = thresholds.get(self.config.stage, 0.0)
        meets_threshold = improvement_pct >= stage_threshold

        # Recommendation
        if improvement_pct < -2.0:
            recommendation = "revert"
        elif meets_threshold:
            recommendation = "promote"
        else:
            recommendation = "keep_monitoring"

        metrics = CanaryMetrics(
            stage=self.config.stage,
            total_routed=canary_total + control_total,
            canary_routed=canary_total,
            control_routed=control_total,
            canary_accuracy=canary_accuracy,
            control_accuracy=control_accuracy,
            improvement_pct=improvement_pct,
            confidence_interval=ci,
            meets_promotion_threshold=meets_threshold,
            recommendation=recommendation,
            measured_at=datetime.utcnow().isoformat() + "Z",
        )

        return metrics

    def promote_canary(self) -> CanaryConfig:
        """Promote canary to next stage.

        Updates config: STAGE_10 → STAGE_25 → STAGE_50 → STAGE_100 (full rollout).

        Returns:
            Updated CanaryConfig

        Raises:
            ValueError: Cannot promote from current stage
            RuntimeError: Config write failed (audit-first)
        """
        promotion_map = {
            CanaryStage.STAGE_10: CanaryStage.STAGE_25,
            CanaryStage.STAGE_25: CanaryStage.STAGE_50,
            CanaryStage.STAGE_50: CanaryStage.STAGE_100,
            CanaryStage.STAGE_100: CanaryStage.STAGE_100,  # Already full
        }

        next_stage = promotion_map.get(self.config.stage)
        if next_stage is None:
            raise ValueError(f"Cannot promote from stage {self.config.stage.value}")

        # Determine new canary percentage
        canary_pct = {
            CanaryStage.STAGE_25: 0.25,
            CanaryStage.STAGE_50: 0.50,
            CanaryStage.STAGE_100: 1.0,
        }.get(next_stage)

        # Create new config
        new_config = CanaryConfig(
            stage=next_stage,
            enabled=self.config.enabled,
            canary_percentage=canary_pct,
            created_at=self.config.created_at,
            promoted_at=datetime.utcnow().isoformat() + "Z",
            promotion_reason=f"Promoted from {self.config.stage.value}",
        )

        # Persist config
        self._save_config(new_config)
        self.config = new_config

        # Emit audit event
        promotion_event = LearningEvent.create(
            event_type=EventType.CONFIG,
            skill_id=self.skill_id,
            tenant_id=self.tenant_id,
            signal={
                "action": "canary_promoted",
                "from_stage": self.config.stage.value,
                "to_stage": next_stage.value,
                "new_canary_percentage": canary_pct,
            },
            skill_version=self.skill_version,
            lom="workflow_optimizer_skill.ab_testing:promote_canary:L258",
        )

        try:
            self.event_store.write_event(promotion_event)
        except (RuntimeError, IOError) as e:
            logger.error(f"Failed to write promotion event: {e}")
            raise

        logger.info(
            f"Canary promoted: {self.config.stage.value} → {next_stage.value}, "
            f"percentage={canary_pct:.1%}"
        )

        return new_config

    def _load_config(self) -> CanaryConfig:
        """Load canary config from disk.

        Returns:
            CanaryConfig (default DISABLED if file doesn't exist)
        """
        if not self.config_file.exists():
            return CanaryConfig()

        try:
            data = json.loads(self.config_file.read_text())
            return CanaryConfig(
                stage=CanaryStage(data["stage"]),
                enabled=data["enabled"],
                canary_percentage=data["canary_percentage"],
                created_at=data["created_at"],
                promoted_at=data.get("promoted_at"),
                promotion_reason=data.get("promotion_reason"),
            )
        except (json.JSONDecodeError, ValueError, KeyError) as e:
            logger.warning(f"Failed to load canary config: {e}, using default")
            return CanaryConfig()

    def _save_config(self, config: CanaryConfig) -> None:
        """Save canary config to disk.

        Args:
            config: CanaryConfig to persist

        Raises:
            IOError: File write failed
        """
        data = {
            "stage": config.stage.value,
            "enabled": config.enabled,
            "canary_percentage": config.canary_percentage,
            "created_at": config.created_at,
            "promoted_at": config.promoted_at,
            "promotion_reason": config.promotion_reason,
        }

        try:
            self.config_file.write_text(json.dumps(data, indent=2))
            logger.info(f"Canary config saved to {self.config_file}")
        except IOError as e:
            logger.error(f"Failed to save canary config: {e}")
            raise
