"""LDD Learning Loop — In-Task Optimization (ADR-0848)"""

from dataclasses import dataclass


@dataclass
class StrategyDecision:
    model: str
    confidence: float
    loss: float


class LddLearningLoop:
    """Measure loss, update confidence, optimize strategy."""
    
    def __init__(self):
        self.decisions = []
    
    async def record_decision(self, decision: StrategyDecision) -> None:
        self.decisions.append(decision)
    
    async def optimize_next_strategy(self) -> str:
        # TODO: Calculate loss, update confidence, return optimized strategy
        return "claude-opus-5"
