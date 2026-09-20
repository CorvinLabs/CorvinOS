"""
Scientific LLM Benchmarking Framework

Implements CONCEPT-0047: Scientific LLM Benchmarking
Validates routing, model selection, and optimization systems with:
- Representative stratified task sampling (N=180)
- Three primary metrics (token savings, latency, quality)
- Statistical validation (p<0.05 significance)
- Publication-quality reports + plots

Usage:
    from core.benchmarking import run_benchmark, BenchmarkConfig

    config = BenchmarkConfig(
        dataset_path="tests/benchmarking/datasets/benchmark_tasks_v1.jsonl",
        baseline_model="claude-opus-5",
        routing_system=IntelligentRouter(),
    )
    result = run_benchmark(config)
    report = result.generate_report()
    print(report)
"""

from .benchmark_harness import (
    BenchmarkConfig,
    BenchmarkResult,
    run_benchmark,
)
from .metrics import (
    TokenMetrics,
    LatencyMetrics,
    QualityMetrics,
    ConfoundMetrics,
    calculate_metrics,
)
from .golden_truth import LLMJudge, GradeResult

__all__ = [
    "BenchmarkConfig",
    "BenchmarkResult",
    "run_benchmark",
    "TokenMetrics",
    "LatencyMetrics",
    "QualityMetrics",
    "ConfoundMetrics",
    "calculate_metrics",
    "LLMJudge",
    "GradeResult",
]
