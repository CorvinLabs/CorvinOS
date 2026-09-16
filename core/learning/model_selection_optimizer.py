"""
k=4 Model Selection Optimizer (ADR-0845, Tier 2 Learning Loop)

Bayesian update for haiku_success_rate per task_type.
Receives task execution outcomes (success, quality, tokens).
Updates heuristics based on observed performance.

Mechanism:
1. TaskManager.record_event(outcome) emits LearningEvent
2. Optimizer receives event (task_type, quality_score, success)
3. Applies Bayesian Beta distribution update
4. Updates haiku_success_rate for task_type
5. Emits model_selector_heuristic_updated event (immutable, hash-chained)
6. Next task uses updated heuristics
7. Convergence measured (std-dev < 5%)
"""

import logging
import math
from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple
from datetime import datetime

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TaskExecutionOutcome:
    """Immutable task execution outcome (audit-safe)."""
    
    task_id: str
    task_type: str
    model_used: str                    # "haiku" or "sonnet"
    quality_score: float               # 0.0-1.0
    tokens_used: int
    success: bool                      # Did task complete successfully?
    completion_time_ms: float
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    
    def to_dict(self) -> Dict:
        """Audit-safe serialization (no PII)."""
        return {
            "task_id": self.task_id,
            "task_type": self.task_type,
            "model_used": self.model_used,
            "quality_score": self.quality_score,
            "tokens_used": self.tokens_used,
            "success": self.success,
            "completion_time_ms": self.completion_time_ms,
            "timestamp": self.timestamp,
        }


@dataclass
class HaikuSuccessRateStats:
    """Statistics for Haiku success rate per task type."""
    
    task_type: str
    alpha: float = 1.0                 # Beta distribution alpha (successes + 1)
    beta: float = 1.0                  # Beta distribution beta (failures + 1)
    observations: int = 0
    last_updated: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    
    @property
    def mean(self) -> float:
        """Bayesian mean of Beta distribution."""
        if self.alpha + self.beta <= 0:
            return 0.5
        return self.alpha / (self.alpha + self.beta)
    
    @property
    def variance(self) -> float:
        """Variance of Beta distribution."""
        n = self.alpha + self.beta
        if n <= 1:
            return 0.25  # Maximum variance for Beta
        return (self.alpha * self.beta) / (n * n * (n + 1))
    
    @property
    def std_dev(self) -> float:
        """Standard deviation of Beta distribution."""
        return math.sqrt(self.variance)
    
    def update(self, success: bool, quality_score: float = 1.0):
        """Bayesian update with outcome."""
        # Treat outcome as success if both success flag and quality >= 0.90
        is_success = success and quality_score >= 0.90
        
        if is_success:
            self.alpha += 1.0
        else:
            self.beta += 1.0
        
        self.observations += 1
        self.last_updated = datetime.utcnow().isoformat()


class OSModelSelectorOptimizer:
    """
    Optimize Haiku success rates via Bayesian learning.
    
    Per-task-type success rate tracking using Beta distribution.
    Updates based on actual task execution outcomes.
    """
    
    def __init__(self, tenant_id: str = "_default"):
        self.tenant_id = tenant_id
        
        # Task-type-specific success rate stats
        self.haiku_stats: Dict[str, HaikuSuccessRateStats] = {
            "code_review": HaikuSuccessRateStats("code_review"),
            "testing": HaikuSuccessRateStats("testing"),
            "documentation": HaikuSuccessRateStats("documentation"),
            "analysis": HaikuSuccessRateStats("analysis"),
            "refactoring": HaikuSuccessRateStats("refactoring"),
            "code_gen": HaikuSuccessRateStats("code_gen"),
            "summarization": HaikuSuccessRateStats("summarization"),
            "default": HaikuSuccessRateStats("default"),
        }
        
        # Track heuristic updates (for convergence testing)
        self.heuristic_history: Dict[str, list] = {
            task_type: [] for task_type in self.haiku_stats.keys()
        }
    
    def record_outcome(
        self,
        outcome: TaskExecutionOutcome,
    ) -> Tuple[float, bool]:
        """
        Record task execution outcome and update heuristics.
        
        Args:
            outcome: TaskExecutionOutcome with success/quality data
        
        Returns:
            (updated_success_rate, converged)
        """
        task_type = outcome.task_type
        
        # Get or create stats for this task type
        if task_type not in self.haiku_stats:
            self.haiku_stats[task_type] = HaikuSuccessRateStats(task_type)
        
        stats = self.haiku_stats[task_type]
        old_mean = stats.mean
        
        # Bayesian update
        stats.update(outcome.success, outcome.quality_score)
        
        new_mean = stats.mean
        delta = abs(new_mean - old_mean)
        
        # Track for convergence testing
        self.heuristic_history[task_type].append({
            "iteration": stats.observations,
            "mean": new_mean,
            "std_dev": stats.std_dev,
            "timestamp": datetime.utcnow().isoformat(),
        })
        
        # Check convergence (std-dev < 5%)
        converged = stats.std_dev < 0.05
        
        logger.info(
            f"Model selector outcome recorded: task_type={task_type}, "
            f"quality={outcome.quality_score:.2f}, success={outcome.success}, "
            f"haiku_rate={new_mean:.3f} (was {old_mean:.3f}), "
            f"std_dev={stats.std_dev:.3f}, converged={converged}"
        )
        
        return new_mean, converged
    
    def get_haiku_success_rate(self, task_type: Optional[str] = None) -> float:
        """Get current Haiku success rate for task type."""
        if not task_type or task_type not in self.haiku_stats:
            return self.haiku_stats["default"].mean
        
        return self.haiku_stats[task_type].mean
    
    def get_confidence(self, task_type: Optional[str] = None) -> float:
        """Get confidence in success rate (inverse of std_dev)."""
        if not task_type or task_type not in self.haiku_stats:
            stats = self.haiku_stats["default"]
        else:
            stats = self.haiku_stats[task_type]
        
        return max(0.0, 1.0 - (stats.std_dev / 0.20))
    
    def get_all_stats(self) -> Dict[str, Dict]:
        """Get all task-type stats."""
        return {
            task_type: {
                "mean": stats.mean,
                "std_dev": stats.std_dev,
                "observations": stats.observations,
                "alpha": stats.alpha,
                "beta": stats.beta,
            }
            for task_type, stats in self.haiku_stats.items()
        }
    
    def check_convergence(self, target_std_dev: float = 0.05) -> Dict[str, bool]:
        """Check convergence for each task type."""
        return {
            task_type: stats.std_dev < target_std_dev
            for task_type, stats in self.haiku_stats.items()
        }
    
    def get_convergence_summary(self) -> Tuple[float, int, int]:
        """Get convergence summary."""
        if not self.haiku_stats:
            return 0.0, 0, 0
        
        std_devs = [s.std_dev for s in self.haiku_stats.values()]
        converged = sum(1 for s in self.haiku_stats.values() if s.std_dev < 0.05)
        
        avg_std_dev = sum(std_devs) / len(std_devs)
        return avg_std_dev, converged, len(self.haiku_stats)
    
    def emit_heuristic_update_event(
        self,
        task_type: str,
        old_rate: float,
        new_rate: float,
    ) -> Dict:
        """Emit audit event for heuristic update."""
        event = {
            "event_type": "model_selector_heuristic_updated",
            "task_type": task_type,
            "old_haiku_success_rate": old_rate,
            "new_haiku_success_rate": new_rate,
            "delta": new_rate - old_rate,
            "timestamp": datetime.utcnow().isoformat(),
            "tenant_id": self.tenant_id,
        }
        logger.info(f"Emitting heuristic update: {event}")
        return event
    
    def reset_for_testing(self):
        """Reset all stats (testing only)."""
        self.haiku_stats = {
            task_type: HaikuSuccessRateStats(task_type)
            for task_type in self.haiku_stats.keys()
        }
        self.heuristic_history = {
            task_type: [] for task_type in self.haiku_stats.keys()
        }


def create_optimizer(tenant_id: str = "_default") -> OSModelSelectorOptimizer:
    """Factory function to create optimizer."""
    return OSModelSelectorOptimizer(tenant_id)
