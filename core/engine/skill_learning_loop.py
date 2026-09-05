"""
Skill Learning Loop (ADR-0601 Simplified)
Single-harness feedback + optimization.
"""

from dataclasses import dataclass, field
from typing import Dict, Any, Optional, List
from datetime import datetime, timezone
import logging
import asyncio

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SkillFeedback:
    """Immutable feedback event."""
    tenant_id: str
    skill_id: str
    request_id: str
    outcome: str  # "success" | "failure" | "partial"
    confidence: float  # 0.0–1.0
    quality_score: float  # 0.0–1.0 (user rating)
    latency_ms: int
    cost_usd: float
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


@dataclass
class SkillConfig:
    """Skill configuration (mutable for learning)."""
    skill_id: str
    version: str
    temperature: float = 0.7
    max_tokens: int = 2048
    timeout_s: int = 30
    preferred_provider: str = "openai"


class SkillLearningLoop:
    """Process feedback → optimize Skill config."""

    def __init__(self, audit_backend=None):
        # Key: (tenant_id, skill_id) to enforce tenant isolation (GDPR Art. 5, 6)
        self.feedback_history: Dict[tuple, List[SkillFeedback]] = {}
        self.config_history: Dict[tuple, List[SkillConfig]] = {}
        self.audit_backend = audit_backend  # For audit events (ADR-0314)

    async def record_feedback(self, feedback: SkillFeedback) -> None:
        """Record feedback event (tenant-isolated, audited)."""
        key = (feedback.tenant_id, feedback.skill_id)
        if key not in self.feedback_history:
            self.feedback_history[key] = []

        self.feedback_history[key].append(feedback)

        # Emit audit event (ADR-0314, ADR-0232)
        if self.audit_backend:
            await self.audit_backend.write_event({
                "tenant_id": feedback.tenant_id,
                "event_type": "skill_feedback_recorded",
                "skill_id": feedback.skill_id,
                "outcome": feedback.outcome,
                "confidence": feedback.confidence,
                "request_id": feedback.request_id,
            })

        logger.info(f"Feedback recorded for {feedback.skill_id} (tenant {feedback.tenant_id}): outcome={feedback.outcome}")

    async def optimize_config(self, tenant_id: str, skill_id: str, current_config: SkillConfig) -> SkillConfig:
        """Optimize Skill config based on feedback history (tenant-isolated, audited)."""
        key = (tenant_id, skill_id)
        if key not in self.feedback_history:
            return current_config

        feedback_list = self.feedback_history[key]
        if len(feedback_list) < 5:
            logger.debug(f"Not enough feedback for {skill_id} (need 5, have {len(feedback_list)})")
            return current_config

        # Calculate metrics
        avg_confidence = sum(f.confidence for f in feedback_list) / len(feedback_list)
        avg_quality = sum(f.quality_score for f in feedback_list) / len(feedback_list)
        failure_count = sum(1 for f in feedback_list if f.outcome == "failure")
        failure_rate = failure_count / len(feedback_list)

        new_config = SkillConfig(
            skill_id=current_config.skill_id,
            version=f"{current_config.version}.1",
            temperature=current_config.temperature,
            max_tokens=current_config.max_tokens,
            timeout_s=current_config.timeout_s,
            preferred_provider=current_config.preferred_provider,
        )

        # Optimization rules (simple)
        optimized = False
        if avg_quality > 0.8 and avg_confidence > 0.7:
            new_config.temperature = max(0.0, current_config.temperature - 0.1)
            optimized = True

        if failure_rate > 0.3:
            new_config.max_tokens = int(current_config.max_tokens * 1.2)
            optimized = True

        # Store new config
        if key not in self.config_history:
            self.config_history[key] = [current_config]
        self.config_history[key].append(new_config)

        # Emit audit event (ADR-0232, ADR-0314)
        if optimized and self.audit_backend:
            await self.audit_backend.write_event({
                "tenant_id": tenant_id,
                "event_type": "skill_config_updated",
                "skill_id": skill_id,
                "old_version": current_config.version,
                "new_version": new_config.version,
                "optimized": optimized,
            })

        if optimized:
            logger.info(f"Optimized {skill_id} for tenant {tenant_id}: v{current_config.version} → v{new_config.version}")

        return new_config

    def get_stats(self, tenant_id: str, skill_id: str) -> Dict[str, Any]:
        """Get Skill performance stats (tenant-isolated)."""
        key = (tenant_id, skill_id)
        if key not in self.feedback_history:
            return {}

        feedback_list = self.feedback_history[key]
        if not feedback_list:
            return {}

        success_count = sum(1 for f in feedback_list if f.outcome == "success")

        return {
            "total_invocations": len(feedback_list),
            "avg_confidence": sum(f.confidence for f in feedback_list) / len(feedback_list),
            "avg_quality": sum(f.quality_score for f in feedback_list) / len(feedback_list),
            "success_rate": success_count / len(feedback_list),
            "avg_latency_ms": sum(f.latency_ms for f in feedback_list) / len(feedback_list),
            "total_cost_usd": sum(f.cost_usd for f in feedback_list),
        }
