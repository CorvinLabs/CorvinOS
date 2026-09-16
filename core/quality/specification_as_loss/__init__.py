"""Specification Loss Signal Architecture (ADR-0731).

3-Layer Loss Landscape for Quality Gate Navigation.
Spec-as-Loss: quality becomes a continuous, learnable loss field.
"""
from .quality_orchestrator import (
    QualityOrchestrator,
    TaskSize,
    QualityProfile,
    FailureAnalysis,
    QualityResult,
    loss_landscape,
    SpecConvergenceOptimizer,
    QUALITY_PROFILES,
)
from .llm_bridge import (
    LLMBridge,
    OllamaRedirectHandler,
    QualityOrchestratorWithLLM,
)

__all__ = [
    "QualityOrchestrator",
    "TaskSize",
    "QualityProfile",
    "FailureAnalysis",
    "QualityResult",
    "loss_landscape",
    "SpecConvergenceOptimizer",
    "QUALITY_PROFILES",
    "LLMBridge",
    "OllamaRedirectHandler",
    "QualityOrchestratorWithLLM",
]
