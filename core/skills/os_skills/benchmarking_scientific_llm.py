"""
SkillForge Skill: Scientific LLM Benchmarking

Implements CONCEPT-0047 — Executes scientific LLM benchmarking with:
- Stratified sampling (N=180 tasks across 6 categories × 3 complexity tiers)
- Three primary metrics (token savings, latency, quality)
- Statistical validation (p<0.05 significance)
- Publication-quality reports

Usage:
    skill = ScientificLLMBenchmarkingSkill()
    result = skill.execute(config={
        "dataset_path": "tests/benchmarking/datasets/benchmark_tasks_v1.jsonl",
        "baseline_model": "claude-opus-5",
        "routing_system": intelligent_router,
    })
    print(result["verdict"])
"""

from typing import Dict, Any, Optional
from core.benchmarking import run_benchmark, BenchmarkConfig


class ScientificLLMBenchmarkingSkill:
    """SkillForge skill for scientific LLM benchmarking."""

    skill_id = "benchmarking.scientific_llm_benchmarking"
    version = "1.0.0"
    type = "learned-experience"
    scope = "project"
    category = "benchmarking"
    bootstrap_grade = 0.25  # Manual seed (not earned)

    description = """
    Execute scientific LLM benchmarking with real calls on stratified samples.
    Measures token savings, speed, quality with statistical rigor.
    Reusable methodology for validating routing, model selection, optimization.

    Implements CONCEPT-0047 — Scientific LLM Benchmarking methodology.
    """

    def __init__(self):
        """Initialize the skill."""
        pass

    def execute(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """
        Execute the benchmark.

        Args:
            config: Configuration dict with keys:
                - dataset_path: Path to benchmark_tasks_v1.jsonl
                - baseline_model: Model to use as baseline (default: claude-opus-5)
                - routing_system: Routing system to test (IntelligentRouter instance)
                - output_dir: Output directory for results (default: tests/benchmarking/results)

        Returns:
            Dict with:
                - verdict: "ACCEPT" or "REJECT" (based on all 3 metrics)
                - token_savings_pct: Mean token savings
                - latency_improvement_pct: Mean latency improvement
                - quality_accuracy_pct: Overall accuracy
                - metrics: Full AllMetrics object (serializable to dict)
                - report_path: Path to markdown report
        """
        # Parse config
        dataset_path = config.get("dataset_path")
        baseline_model = config.get("baseline_model", "claude-opus-5")
        routing_system = config.get("routing_system")
        output_dir = config.get("output_dir", "tests/benchmarking/results")

        if not dataset_path:
            return {
                "status": "error",
                "error": "dataset_path is required",
            }

        if not routing_system:
            return {
                "status": "error",
                "error": "routing_system is required",
            }

        # Create benchmark config
        benchmark_config = BenchmarkConfig(
            dataset_path=dataset_path,
            baseline_model=baseline_model,
            routing_system=routing_system,
            output_dir=output_dir,
        )

        # Run benchmark
        try:
            result = run_benchmark(benchmark_config)
        except Exception as e:
            return {
                "status": "error",
                "error": str(e),
            }

        # Extract verdict
        metrics = result.metrics
        verdict = "ACCEPT" if metrics.accepts_h1_all_three else "REJECT"

        # Return summary
        return {
            "status": "success",
            "verdict": verdict,
            "token_savings_pct": metrics.token_metrics.mean_savings_pct,
            "latency_improvement_pct": metrics.latency_metrics.mean_improvement_pct,
            "quality_accuracy_pct": metrics.quality_metrics.accuracy_routed_pct,
            "metrics": metrics.to_dict(),
            "baseline_file": str(result.baseline_file),
            "routing_file": str(result.routing_file),
            "quality_file": str(result.quality_file),
            "metrics_file": str(result.metrics_file),
            "report": result.report,
            "summary": metrics.summary,
        }

    def describe(self) -> Dict[str, Any]:
        """Return skill metadata."""
        return {
            "skill_id": self.skill_id,
            "version": self.version,
            "type": self.type,
            "scope": self.scope,
            "category": self.category,
            "description": self.description,
        }
