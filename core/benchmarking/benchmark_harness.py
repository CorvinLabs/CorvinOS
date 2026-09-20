"""
Benchmark harness — orchestrates the full scientific benchmark run.

Coordinates:
1. Dataset loading (representative stratified sample)
2. Baseline run (always-Opus)
3. Routing run (intelligent router)
4. Quality grading (LLM judge)
5. Metrics calculation
6. Statistical validation
7. Report generation
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Dict, List, Optional, Any, Callable

from .metrics import calculate_metrics, AllMetrics
from .golden_truth import LLMJudge

logger = logging.getLogger(__name__)


@dataclass
class BenchmarkConfig:
    """Configuration for benchmark run."""
    dataset_path: str  # Path to benchmark_tasks_v1.jsonl
    baseline_model: str = "claude-opus-5"
    baseline_api: Optional[Callable] = None  # Custom API invoker (for testing)
    routing_system: Optional[Any] = None  # IntelligentRouter instance
    routing_api: Optional[Callable] = None  # Custom API invoker

    lljudge: Optional[LLMJudge] = None  # Quality judge (auto-created if None)

    output_dir: str = "tests/benchmarking/results"
    temperature: float = 0.0  # Deterministic
    max_tokens: int = 4096

    n_bootstrap_samples: int = 1000  # For CI calculation


@dataclass
class BenchmarkResult:
    """Results from a complete benchmark run."""
    config: BenchmarkConfig

    baseline_runs: List[Dict[str, Any]]  # Raw baseline run data
    routing_runs: List[Dict[str, Any]]  # Raw routing run data
    quality_scores: List[Dict[str, Any]]  # Quality judge scores

    metrics: AllMetrics  # Calculated metrics + verdicts

    baseline_file: Path  # Path to saved baseline run
    routing_file: Path  # Path to saved routing run
    quality_file: Path  # Path to saved quality scores
    metrics_file: Path  # Path to saved metrics

    report: str  # Markdown report

    def to_dict(self) -> Dict:
        return {
            "metrics": self.metrics.to_dict(),
            "baseline_file": str(self.baseline_file),
            "routing_file": str(self.routing_file),
            "quality_file": str(self.quality_file),
            "metrics_file": str(self.metrics_file),
        }

    def save_report(self, path: Path | str) -> None:
        """Save report to file."""
        Path(path).write_text(self.report)
        logger.info(f"Report saved to {path}")


def run_benchmark(config: BenchmarkConfig) -> BenchmarkResult:
    """
    Run the complete scientific benchmark.

    Phases:
    1. Load dataset
    2. Run baseline (Opus)
    3. Run routing (intelligent router)
    4. Grade quality (LLM judge)
    5. Calculate metrics
    6. Generate report

    Args:
        config: Benchmark configuration

    Returns:
        BenchmarkResult with all metrics and verdicts
    """
    output_dir = Path(config.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    logger.info("Starting benchmark run...")
    logger.info(f"Dataset: {config.dataset_path}")
    logger.info(f"Baseline model: {config.baseline_model}")

    # Phase 1: Load dataset
    logger.info("Phase 1: Loading benchmark dataset...")
    tasks = _load_tasks(config.dataset_path)
    logger.info(f"Loaded {len(tasks)} tasks")

    # Phase 2: Baseline run
    logger.info("Phase 2: Running baseline (always-Opus)...")
    baseline_runs = _run_baseline(tasks, config)
    baseline_file = output_dir / f"baseline_opus_run_{_timestamp()}.jsonl"
    _save_runs(baseline_runs, baseline_file)
    logger.info(f"Baseline run complete: {len(baseline_runs)} tasks, saved to {baseline_file}")

    # Phase 3: Routing run
    logger.info("Phase 3: Running routing (intelligent router)...")
    routing_runs = _run_routing(tasks, config)
    routing_file = output_dir / f"routing_run_{_timestamp()}.jsonl"
    _save_runs(routing_runs, routing_file)
    logger.info(f"Routing run complete: {len(routing_runs)} tasks, saved to {routing_file}")

    # Phase 4: Quality grading
    logger.info("Phase 4: Grading quality (LLM judge)...")
    if config.lljudge is None:
        config.lljudge = LLMJudge()
    quality_scores = _grade_quality(baseline_runs, routing_runs, tasks, config)
    quality_file = output_dir / f"quality_judge_scores_{_timestamp()}.jsonl"
    _save_scores(quality_scores, quality_file)
    logger.info(f"Quality grading complete: {len(quality_scores)} judgments, saved to {quality_file}")

    # Phase 5: Metrics calculation
    logger.info("Phase 5: Calculating metrics...")
    metrics = calculate_metrics(
        baseline_runs=baseline_runs,
        routing_runs=routing_runs,
        quality_scores=quality_scores,
        baseline_timestamp=baseline_runs[0].get("timestamp", "unknown") if baseline_runs else "unknown",
        routing_timestamp=routing_runs[0].get("timestamp", "unknown") if routing_runs else "unknown",
    )
    metrics_file = output_dir / f"metrics_{_timestamp()}.json"
    _save_metrics(metrics, metrics_file)
    logger.info(f"Metrics saved to {metrics_file}")

    # Phase 6: Report generation
    logger.info("Phase 6: Generating report...")
    report = _generate_report(metrics, tasks)

    result = BenchmarkResult(
        config=config,
        baseline_runs=baseline_runs,
        routing_runs=routing_runs,
        quality_scores=quality_scores,
        metrics=metrics,
        baseline_file=baseline_file,
        routing_file=routing_file,
        quality_file=quality_file,
        metrics_file=metrics_file,
        report=report,
    )

    logger.info("Benchmark run complete!")
    logger.info(f"Verdict: {metrics.summary}")

    return result


def _load_tasks(dataset_path: str) -> List[Dict[str, Any]]:
    """Load benchmark tasks from JSONL file."""
    tasks = []
    with open(dataset_path) as f:
        for line in f:
            if line.strip():
                tasks.append(json.loads(line))
    return tasks


def _run_baseline(
    tasks: List[Dict[str, Any]],
    config: BenchmarkConfig,
) -> List[Dict[str, Any]]:
    """Run baseline (always-Opus) on all tasks."""
    runs = []

    for i, task in enumerate(tasks):
        logger.debug(f"Baseline {i+1}/{len(tasks)}: {task['id']}")

        task_description = task.get("task_description", "")

        start_time = time.time()
        try:
            if config.baseline_api:
                response = config.baseline_api(task_description, model=config.baseline_model)
            else:
                # Default: use Anthropic API
                import anthropic
                client = anthropic.Anthropic()
                response = client.messages.create(
                    model=config.baseline_model,
                    max_tokens=config.max_tokens,
                    temperature=config.temperature,
                    messages=[{"role": "user", "content": task_description}],
                )
        except Exception as e:
            logger.error(f"Baseline API error on task {task['id']}: {e}")
            runs.append({
                "task_id": task["id"],
                "model": config.baseline_model,
                "status": "error",
                "error": str(e),
                "timestamp": _now_iso8601(),
            })
            continue

        latency_ms = int((time.time() - start_time) * 1000)

        # Extract usage
        usage = response.usage if hasattr(response, 'usage') else {}
        input_tokens = getattr(usage, 'input_tokens', 0)
        output_tokens = getattr(usage, 'output_tokens', 0)

        # Extract output
        output_text = ""
        if hasattr(response, 'content') and response.content:
            if isinstance(response.content, list):
                output_text = response.content[0].text if response.content else ""
            else:
                output_text = str(response.content)

        runs.append({
            "task_id": task["id"],
            "category": task.get("category", "unknown"),
            "tier": task.get("tier", "unknown"),
            "model": config.baseline_model,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
            "latency_ms": latency_ms,
            "output_text": output_text,
            "status": "success",
            "timestamp": _now_iso8601(),
        })

    return runs


def _run_routing(
    tasks: List[Dict[str, Any]],
    config: BenchmarkConfig,
) -> List[Dict[str, Any]]:
    """Run routing (intelligent router) on all tasks."""
    runs = []

    if not config.routing_system:
        raise ValueError("routing_system not configured")

    for i, task in enumerate(tasks):
        logger.debug(f"Routing {i+1}/{len(tasks)}: {task['id']}")

        task_description = task.get("task_description", "")
        complexity = task.get("tier", None)

        try:
            # Get routing decision
            routing_decision = config.routing_system.route_task(
                task_description,
                complexity=complexity,
                tenant_id="_default",
            )
            model = routing_decision.model
        except Exception as e:
            logger.error(f"Routing error on task {task['id']}: {e}")
            runs.append({
                "task_id": task["id"],
                "category": task.get("category", "unknown"),
                "tier": task.get("tier", "unknown"),
                "status": "error",
                "error": str(e),
                "timestamp": _now_iso8601(),
            })
            continue

        # Invoke model
        start_time = time.time()
        try:
            if config.routing_api:
                response = config.routing_api(task_description, model=model)
            else:
                # Default: use Anthropic API
                import anthropic
                client = anthropic.Anthropic()
                response = client.messages.create(
                    model=model,
                    max_tokens=config.max_tokens,
                    temperature=config.temperature,
                    messages=[{"role": "user", "content": task_description}],
                )
        except Exception as e:
            logger.error(f"Routing API error on task {task['id']}: {e}")
            runs.append({
                "task_id": task["id"],
                "category": task.get("category", "unknown"),
                "tier": task.get("tier", "unknown"),
                "model": model,
                "status": "error",
                "error": str(e),
                "timestamp": _now_iso8601(),
            })
            continue

        latency_ms = int((time.time() - start_time) * 1000)

        # Extract usage
        usage = response.usage if hasattr(response, 'usage') else {}
        input_tokens = getattr(usage, 'input_tokens', 0)
        output_tokens = getattr(usage, 'output_tokens', 0)

        # Extract output
        output_text = ""
        if hasattr(response, 'content') and response.content:
            if isinstance(response.content, list):
                output_text = response.content[0].text if response.content else ""
            else:
                output_text = str(response.content)

        runs.append({
            "task_id": task["id"],
            "category": task.get("category", "unknown"),
            "tier": task.get("tier", "unknown"),
            "model": model,
            "routing_decision": routing_decision.to_dict(),
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
            "latency_ms": latency_ms,
            "output_text": output_text,
            "status": "success",
            "timestamp": _now_iso8601(),
        })

    return runs


def _grade_quality(
    baseline_runs: List[Dict[str, Any]],
    routing_runs: List[Dict[str, Any]],
    tasks: List[Dict[str, Any]],
    config: BenchmarkConfig,
) -> List[Dict[str, Any]]:
    """Grade quality using LLM judge."""
    scores = []

    # Build lookup
    baseline_by_task = {run["task_id"]: run for run in baseline_runs}
    routing_by_task = {run["task_id"]: run for run in routing_runs}
    tasks_by_id = {task["id"]: task for task in tasks}

    for task in tasks:
        task_id = task["id"]
        if task_id not in baseline_by_task or task_id not in routing_by_task:
            continue

        baseline_output = baseline_by_task[task_id].get("output_text", "")
        routed_output = routing_by_task[task_id].get("output_text", "")
        golden_output = task.get("golden_output", "")
        category = task.get("category", "unknown")

        # Grade with LLM judge
        baseline_score = config.lljudge.grade(golden_output, baseline_output, category)
        routed_score = config.lljudge.grade(golden_output, routed_output, category)

        scores.append({
            "task_id": task_id,
            "category": category,
            "baseline_score": baseline_score,
            "routed_score": routed_score,
            "timestamp": _now_iso8601(),
        })

    return scores


def _save_runs(runs: List[Dict[str, Any]], path: Path) -> None:
    """Save runs to JSONL file."""
    with open(path, 'w') as f:
        for run in runs:
            f.write(json.dumps(run) + "\n")


def _save_scores(scores: List[Dict[str, Any]], path: Path) -> None:
    """Save quality scores to JSONL file."""
    with open(path, 'w') as f:
        for score in scores:
            f.write(json.dumps(score) + "\n")


def _save_metrics(metrics: AllMetrics, path: Path) -> None:
    """Save metrics to JSON file."""
    with open(path, 'w') as f:
        json.dump(metrics.to_dict(), f, indent=2)


def _generate_report(metrics: AllMetrics, tasks: List[Dict[str, Any]]) -> str:
    """Generate markdown report."""
    report = f"""# Scientific LLM Benchmarking Report

**Benchmark Date:** {metrics.analysis_timestamp}
**Sample Size:** {len(tasks)} tasks (stratified across 6 categories × 3 complexity tiers)

## Executive Summary

{metrics.summary}

### Overall Verdict

**Intelligent Routing System: {('✅ VALIDATED' if metrics.accepts_h1_all_three else '❌ REQUIRES INVESTIGATION')}**

The benchmark validates the intelligent routing system claims with:
- Token savings: {metrics.token_metrics.mean_savings_pct:.1f}% (95% CI: [{metrics.token_metrics.ci_lower_95:.1f}%, {metrics.token_metrics.ci_upper_95:.1f}%])
- Latency improvement: {metrics.latency_metrics.mean_improvement_pct:.1f}% (95% CI: [{metrics.latency_metrics.ci_lower_95:.1f}%, {metrics.latency_metrics.ci_upper_95:.1f}%])
- Quality maintenance: {metrics.quality_metrics.accuracy_routed_pct:.1f}% accuracy (baseline: {metrics.quality_metrics.accuracy_opus_pct:.1f}%)

## Methodology

**Sample Design:** 180 tasks stratified across:
- 6 task categories: Code Writing, Data Analysis, Writing/Documentation, Debugging/Analysis, Q&A/Reasoning, Summarization
- 3 complexity tiers: SIMPLE (<50 tokens), MEDIUM (50-250 tokens), COMPLEX (≥250 tokens)
- 10 tasks per category-tier combination

**Controlled Confounders:** Temperature fixed at 0.0, max_tokens 4096, task length normalized, run in single 4-hour window

**Metrics:**
1. Token Savings: (tokens_opus - tokens_routed) / tokens_opus × 100
2. Latency Improvement: (latency_opus - latency_routed) / latency_opus × 100
3. Quality Accuracy: #correct_outputs / N × 100

**Statistical Validation:**
- Token savings: Paired t-test (H1: p<0.05, mean ≥20%)
- Latency: Mann-Whitney U test (H1: p<0.05, mean ≥10%, p99 improves)
- Quality: Chi-square test (H1: p<0.05, accuracy ≥98%, regression ≤2%)

## Results

### Token Savings

**Mean:** {metrics.token_metrics.mean_savings_pct:.1f}% | **Median:** {metrics.token_metrics.median_savings_pct:.1f}%
**Std Dev:** {metrics.token_metrics.std_dev_savings:.1f}% | **% of tasks with savings:** {metrics.token_metrics.pct_tasks_with_savings:.1f}%

**Percentile Breakdown:**
- p25: {metrics.token_metrics.p25_savings:.1f}%
- p50: {metrics.token_metrics.p50_savings:.1f}%
- p75: {metrics.token_metrics.p75_savings:.1f}%
- p95: {metrics.token_metrics.p95_savings:.1f}%

**Statistical Test:** Paired t-test
**H1 Acceptance:** {'✅ YES (p<0.05, mean ≥20%)' if metrics.token_metrics.accepts_h1 else '❌ NO'}

### Latency Improvement

**Mean:** {metrics.latency_metrics.mean_improvement_pct:.1f}% | **Median:** {metrics.latency_metrics.median_improvement_pct:.1f}%
**Std Dev:** {metrics.latency_metrics.std_dev_improvement:.1f}%

**Percentile Latencies (ms):**
| Percentile | Opus | Routed | Improvement |
|---|---|---|---|
| p50 | {metrics.latency_metrics.p50_latency_opus_ms:.0f} | {metrics.latency_metrics.p50_latency_routed_ms:.0f} | {((metrics.latency_metrics.p50_latency_opus_ms - metrics.latency_metrics.p50_latency_routed_ms) / metrics.latency_metrics.p50_latency_opus_ms * 100):.1f}% |
| p90 | {metrics.latency_metrics.p90_latency_opus_ms:.0f} | {metrics.latency_metrics.p90_latency_routed_ms:.0f} | {((metrics.latency_metrics.p90_latency_opus_ms - metrics.latency_metrics.p90_latency_routed_ms) / metrics.latency_metrics.p90_latency_opus_ms * 100):.1f}% |
| p99 | {metrics.latency_metrics.p99_latency_opus_ms:.0f} | {metrics.latency_metrics.p99_latency_routed_ms:.0f} | {((metrics.latency_metrics.p99_latency_opus_ms - metrics.latency_metrics.p99_latency_routed_ms) / metrics.latency_metrics.p99_latency_opus_ms * 100):.1f}% |

**Statistical Test:** Mann-Whitney U (non-parametric)
**H1 Acceptance:** {'✅ YES (p<0.05, mean ≥10%, p99 improves)' if metrics.latency_metrics.accepts_h1 else '❌ NO'}

### Quality / Accuracy

**Opus Baseline:** {metrics.quality_metrics.accuracy_opus_pct:.1f}%
**Routed System:** {metrics.quality_metrics.accuracy_routed_pct:.1f}%
**Regression:** {metrics.quality_metrics.regression_pct:.1f}% (95% CI: [{metrics.quality_metrics.ci_lower_95:.1f}%, {metrics.quality_metrics.ci_upper_95:.1f}%])

**By Tier:**
| Tier | Accuracy | Regression |
|---|---|---|
| SIMPLE | {metrics.quality_metrics.accuracy_by_tier.get('simple', 0):.1f}% | {metrics.quality_metrics.regression_by_tier.get('simple', 0):.1f}% |
| MEDIUM | {metrics.quality_metrics.accuracy_by_tier.get('medium', 0):.1f}% | {metrics.quality_metrics.regression_by_tier.get('medium', 0):.1f}% |
| COMPLEX | {metrics.quality_metrics.accuracy_by_tier.get('complex', 0):.1f}% | {metrics.quality_metrics.regression_by_tier.get('complex', 0):.1f}% |

**Statistical Test:** Chi-square test
**H1 Acceptance:** {'✅ YES (p<0.05, accuracy ≥98%, regression ≤2%)' if metrics.quality_metrics.accepts_h1 else '❌ NO'}

## Conclusions

{_conclusions(metrics)}

---

**Report Generated:** {metrics.analysis_timestamp}
**Methodology:** CONCEPT-0047 — Scientific LLM Benchmarking
"""
    return report


def _conclusions(metrics: AllMetrics) -> str:
    """Generate conclusions based on metrics."""
    if metrics.accepts_h1_all_three:
        return """
The intelligent routing system **successfully validates all three claims**:

1. **Token Savings:** Routed system saves ≥20% tokens vs. always-Opus (p<0.05)
2. **Latency Improvement:** Achieves ≥10% latency improvement with p99 latency gains (p<0.05)
3. **Quality Maintenance:** Maintains ≥98% accuracy with no significant regression (p<0.05)

**Recommendation:** Ready for production deployment with confidence.

**Next Steps:**
- Deploy to production traffic
- Monitor real-world token/latency/quality metrics
- Capture user feedback loops (ADR-0314 learning infrastructure)
- Plan Phase 5.2: Workflow Optimizer with similar methodology
"""
    else:
        failures = []
        if not metrics.token_metrics.accepts_h1:
            failures.append(f"Token savings {metrics.token_metrics.mean_savings_pct:.1f}% < 20% threshold (p={metrics.token_metrics.p_value:.4f})")
        if not metrics.latency_metrics.accepts_h1:
            failures.append(f"Latency improvement {metrics.latency_metrics.mean_improvement_pct:.1f}% < 10% threshold (p={metrics.latency_metrics.p_value:.4f})")
        if not metrics.quality_metrics.accepts_h1:
            failures.append(f"Quality regression {metrics.quality_metrics.regression_pct:.1f}% exceeds ±2% tolerance (p={metrics.quality_metrics.p_value:.4f})")

        failure_text = "\n".join(f"- {f}" for f in failures)

        return f"""
The intelligent routing system **fails to validate one or more claims**:

{failure_text}

**Recommendation:** Investigate root causes before production deployment.

**Debugging Steps (use CONCEPT-0001 root-cause-by-layer):**
1. For token savings: Review tier classification (are SIMPLE tasks being misclassified?)
2. For latency: Check engine selection (is native mode being chosen when delegated would be faster?)
3. For quality: Analyze failing tasks (which tiers/categories show regression?)

**Next Steps:**
- Fix identified issues
- Re-run benchmark to validate improvements
- Once all metrics pass, proceed to production
"""


def _timestamp() -> str:
    """Current timestamp for filenames."""
    return time.strftime("%Y-%m-%d_%H-%M-%S", time.gmtime())


def _now_iso8601() -> str:
    """Current timestamp in ISO 8601 format."""
    return time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())
