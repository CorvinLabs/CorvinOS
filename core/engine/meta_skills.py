"""
Meta-Skills: Skills that optimize other Skills (ADR-0602)
- os.skill_optimizer: reads feedback, optimizes config
- os.skill_debugger: analyzes failure patterns
"""

from typing import Dict, Any, Optional
from .skill_learning_loop import SkillLearningLoop, SkillConfig
import logging

logger = logging.getLogger(__name__)


class SkillOptimizer:
    """Meta-Skill: os.skill_optimizer — optimize Skill config via feedback."""

    def __init__(self, learning_loop: SkillLearningLoop):
        self.learning_loop = learning_loop

    async def optimize(self, tenant_id: str, skill_id: str, current_config: SkillConfig) -> SkillConfig:
        """Run optimization loop (tenant-isolated)."""
        logger.info(f"Optimizing {skill_id} v{current_config.version} for tenant {tenant_id}")

        # Get current stats
        stats = self.learning_loop.get_stats(tenant_id, skill_id)
        if not stats or stats.get("total_invocations", 0) < 5:
            logger.warning(f"Not enough data to optimize {skill_id}")
            return current_config

        # Optimize via learning loop
        new_config = await self.learning_loop.optimize_config(tenant_id, skill_id, current_config)

        logger.info(f"Optimization complete for {skill_id}: v{current_config.version} → v{new_config.version}")

        return new_config


class SkillDebugger:
    """Meta-Skill: os.skill_debugger — analyze failure patterns."""

    def __init__(self, learning_loop: SkillLearningLoop):
        self.learning_loop = learning_loop

    async def debug(self, tenant_id: str, skill_id: str) -> Dict[str, Any]:
        """Analyze Skill failures (tenant-isolated, uses public API)."""
        logger.info(f"Debugging {skill_id} for tenant {tenant_id}")

        stats = self.learning_loop.get_stats(tenant_id, skill_id)
        if not stats:
            return {"error": f"No feedback for {skill_id}"}

        # Simple failure analysis using public API
        key = (tenant_id, skill_id)
        feedback_list = self.learning_loop.feedback_history.get(key, [])
        failures = [f for f in feedback_list if f.outcome == "failure"]

        if not failures:
            return {"status": "no_failures", "success_rate": stats.get("success_rate", 1.0)}

        # Group failures by latency bucket (not exact ms)
        failure_patterns = {}
        for failure in failures:
            # Bucket by 500ms intervals for meaningful patterns
            bucket = (failure.latency_ms // 500) * 500
            key = f"latency_{bucket}_ms"
            failure_patterns[key] = failure_patterns.get(key, 0) + 1

        return {
            "skill_id": skill_id,
            "tenant_id": tenant_id,
            "total_failures": len(failures),
            "failure_rate": len(failures) / stats.get("total_invocations", 1),
            "patterns": failure_patterns,
            "recommendation": "Increase timeout" if len(failures) > 3 else "Continue monitoring",
        }
