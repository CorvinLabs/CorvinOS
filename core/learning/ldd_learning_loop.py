"""LDD Learning Loop (ADR-0848) — k=4 PRODUCTION

In-task optimization with Bayesian confidence updates + strategy adaptation.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional
from datetime import datetime
from enum import Enum
import math


class ModelStrategy(Enum):
    HAIKU = "haiku"
    SONNET = "sonnet"
    OPUS = "opus"


@dataclass
class StrategyDecision:
    """Single decision + outcome."""
    model: ModelStrategy
    confidence: float  # Prior confidence
    quality_score: float  # Observed: 0-1
    cost: float  # Observed cost in USD
    latency_ms: int  # Observed latency
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")
    
    @property
    def loss(self) -> float:
        """Compute loss = -quality + cost_weight * cost + latency_weight * latency."""
        # Simple weighted combination
        quality_loss = 1.0 - self.quality_score
        cost_loss = self.cost * 10  # Normalize cost to 0-1 range
        latency_loss = min(self.latency_ms / 1000, 1.0)  # Cap at 1.0
        
        return quality_loss * 0.6 + cost_loss * 0.25 + latency_loss * 0.15


class LddLearningLoop:
    """Production learning loop — measure loss, adapt strategy."""
    
    name = "ldd_learning_loop"
    version = "1.0.0"
    
    def __init__(self):
        self.decisions: List[StrategyDecision] = []
        self.model_confidence: Dict[str, float] = {
            ModelStrategy.HAIKU.value: 0.5,
            ModelStrategy.SONNET.value: 0.5,
            ModelStrategy.OPUS.value: 0.5
        }
        # Bayesian: alpha=beta=1 (uniform prior)
        self.model_alpha: Dict[str, float] = {m.value: 1.0 for m in ModelStrategy}
        self.model_beta: Dict[str, float] = {m.value: 1.0 for m in ModelStrategy}
    
    async def record_decision(self, decision: StrategyDecision) -> None:
        """Record decision outcome, update Bayesian posterior."""
        self.decisions.append(decision)
        
        # Update Beta distribution for this model
        model_key = decision.model.value
        
        # Outcome: 1 if quality high + cost low, 0 otherwise
        success = 1 if decision.quality_score > 0.7 and decision.loss < 0.3 else 0
        
        # Beta update: α += successes, β += failures
        self.model_alpha[model_key] += success
        self.model_beta[model_key] += (1 - success)
        
        # Compute posterior mean (expected probability)
        alpha = self.model_alpha[model_key]
        beta = self.model_beta[model_key]
        posterior_mean = alpha / (alpha + beta)
        self.model_confidence[model_key] = posterior_mean
    
    async def optimize_next_strategy(self, task_difficulty: Optional[str] = None) -> ModelStrategy:
        """
        Compute optimized strategy based on loss history.
        Task difficulty: "simple" / "medium" / "complex"
        """
        if not self.decisions:
            # Default: start with Haiku (cheapest)
            return ModelStrategy.HAIKU
        
        # Group decisions by model
        by_model = {}
        for model in ModelStrategy:
            by_model[model.value] = [d for d in self.decisions if d.model == model]
        
        # Compute average loss per model
        model_avg_loss = {}
        for model_key, decisions_for_model in by_model.items():
            if decisions_for_model:
                avg_loss = sum(d.loss for d in decisions_for_model) / len(decisions_for_model)
                model_avg_loss[model_key] = avg_loss
            else:
                model_avg_loss[model_key] = float('inf')  # No data → don't prefer
        
        # Decision rule based on task difficulty
        if task_difficulty == "complex":
            # Complex tasks: prefer Opus if quality is good
            if (model_avg_loss.get("opus", float('inf')) < model_avg_loss.get("sonnet", float('inf')) or
                self.model_confidence["opus"] > 0.7):
                return ModelStrategy.OPUS
            return ModelStrategy.SONNET
        
        elif task_difficulty == "simple":
            # Simple tasks: prefer Haiku (cheap)
            if self.model_confidence["haiku"] > 0.6:
                return ModelStrategy.HAIKU
            return ModelStrategy.SONNET
        
        else:
            # Medium: pick lowest-loss model
            best_model = min(model_avg_loss, key=model_avg_loss.get)
            return ModelStrategy(best_model)
    
    async def update_confidence(self, model: ModelStrategy, outcome: float) -> None:
        """Manual confidence adjustment (feedback-driven)."""
        model_key = model.value
        
        # Update Beta distribution
        if outcome > 0.5:
            self.model_alpha[model_key] += 1
        else:
            self.model_beta[model_key] += 1
        
        # Update posterior
        alpha = self.model_alpha[model_key]
        beta = self.model_beta[model_key]
        self.model_confidence[model_key] = alpha / (alpha + beta)
    
    def get_confidence_interval(self, model: ModelStrategy, confidence_level: float = 0.95) -> tuple:
        """Get Bayesian credible interval for a model."""
        model_key = model.value
        alpha = self.model_alpha[model_key]
        beta = self.model_beta[model_key]
        
        # Using normal approximation for simplicity
        mean = alpha / (alpha + beta)
        variance = (alpha * beta) / ((alpha + beta) ** 2 * (alpha + beta + 1))
        std_dev = math.sqrt(variance)
        
        z_score = 1.96 if confidence_level == 0.95 else 1.64
        lower = max(0, mean - z_score * std_dev)
        upper = min(1, mean + z_score * std_dev)
        
        return (lower, upper)
    
    def get_status(self) -> dict:
        """Get current learning status."""
        total_decisions = len(self.decisions)
        avg_loss = sum(d.loss for d in self.decisions) / total_decisions if self.decisions else 0
        
        return {
            "total_decisions": total_decisions,
            "avg_loss": round(avg_loss, 3),
            "confidence_scores": {k: round(v, 3) for k, v in self.model_confidence.items()},
            "alpha_beta_state": {
                model.value: {
                    "alpha": self.model_alpha[model.value],
                    "beta": self.model_beta[model.value]
                }
                for model in ModelStrategy
            }
        }
