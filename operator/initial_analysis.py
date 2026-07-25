"""Stub: initial_analysis module for testing/linting without full dependencies.

This is a minimal stub that allows the TDE modules to be imported for testing
without requiring the full InitialAnalysisRequest infrastructure.
Real implementations import the full version from claude.ai connectors.
"""

from dataclasses import dataclass
from typing import Any


@dataclass
class GlobalPlan:
    """Minimal stub."""
    estimated_tokens: int = 1000
    steps: list[Any] = None

    def __post_init__(self):
        if self.steps is None:
            self.steps = []


@dataclass
class Step:
    """Minimal stub."""
    step: int = 1
    action: str = ""
    description: str = ""
    can_parallelize: bool = False


@dataclass
class Classification:
    """Minimal stub."""
    task_type: str = "code"
    complexity: str = "medium"
    confidence: float = 0.8


@dataclass
class InitialAnalysisRequest:
    """Minimal stub."""
    classification: Classification = None
    global_plan: GlobalPlan = None

    def __post_init__(self):
        if self.classification is None:
            self.classification = Classification()
        if self.global_plan is None:
            self.global_plan = GlobalPlan()
