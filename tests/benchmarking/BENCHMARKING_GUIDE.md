# Scientific LLM Benchmarking Guide

**Document Version:** 1.0.0  
**Date:** 2026-09-20  
**Related:** CONCEPT-0047, ADR-0867, SkillForge skill `benchmarking.scientific_llm_benchmarking`

---

## Overview

This guide explains how to run, interpret, and extend the scientific LLM benchmarking framework. The framework implements CONCEPT-0047, which provides a rigorous, reproducible methodology for validating LLM routing, model selection, and optimization claims with real API calls on a representative stratified sample.

**Key Features:**
- **Stratified Sampling:** 180 representative tasks across 6 categories × 3 complexity tiers
- **Three Metrics:** Token savings, latency improvement, quality accuracy
- **Statistical Rigor:** p<0.05 significance, confidence intervals, distribution tests
- **Reusable:** Same framework works for future optimizations (Workflow Optimizer, Security Orchestrator, etc.)
- **Immutable Baselines:** Results saved for future A/B testing

---

## Quick Start

### 1. Setup (One-time)

```bash
# Create directories
mkdir -p tests/benchmarking/{datasets,results}

# Install dependencies (if not already installed)
pip install scipy numpy anthropic pytest

# Copy sample task dataset (or create your own)
cp tests/benchmarking/datasets/benchmark_tasks_sample.jsonl \
   tests/benchmarking/datasets/benchmark_tasks_v1.jsonl
```

### 2. Run Benchmark

```python
from core.benchmarking import run_benchmark, BenchmarkConfig
from core.skills.os_skills.intelligent_router import IntelligentRouter

# Create config
config = BenchmarkConfig(
    dataset_path="tests/benchmarking/datasets/benchmark_tasks_v1.jsonl",
    baseline_model="claude-opus-5",
    routing_system=IntelligentRouter(),
    output_dir="tests/benchmarking/results",
)

# Run benchmark
result = run_benchmark(config)

# Print summary
print(result.metrics.summary)

# Save report
result.save_report("tests/benchmarking/results/BENCHMARK_REPORT.md")
```

**Expected Duration:** ~30 minutes (180 baseline calls + 180 routing calls + quality grading)

### 3. Interpret Results

The benchmark produces a verdict for each metric:

| Metric | Threshold | p-value | Notes |
|---|---|---|---|
| **Token Savings** | ≥20% | p<0.05 | Paired t-test |
| **Latency** | ≥10% mean, p99 improves | p<0.05 | Mann-Whitney U |
| **Quality** | ≥98% accuracy, ≤2% regression | p<0.05 | Chi-square |

**Verdict:**
- ✅ **ACCEPT:** All three metrics pass
- ❌ **REJECT:** One or more metrics fail

---

## Dataset Format

Tasks are stored in JSONL format (one JSON object per line):

```json
{
  "id": "code_simple_001",
  "task_description": "Write a function to add two numbers",
  "expected_output": "Function that returns sum of two numbers",
  "golden_output": "def add(a, b):\n    return a + b",
  "category": "code",
  "tier": "simple"
}
```

**Fields:**
- `id`: Unique task identifier (string)
- `task_description`: The prompt/instruction for the LLM (string)
- `expected_output`: Brief description of expected result (string)
- `golden_output`: Reference/ground-truth answer for quality grading (string)
- `category`: Task category: code | data | writing | debugging | qa | summarization (string)
- `tier`: Complexity tier: simple | medium | complex (string)

**Dataset Requirements:**
- **Total Tasks:** N=180 recommended (minimum N=30 for statistical validity)
- **Stratification:** Balanced across (category, tier) combinations
  - 6 categories × 3 tiers = 18 combinations
  - 180 tasks / 18 = 10 tasks per combination
- **Immutability:** Save as `benchmark_tasks_v1.jsonl`; never modify in-place
- **Diversity:** Sample from real user workloads, GitHub issues, StackOverflow, academic datasets

---

## Running Benchmarks

### Command-Line (Single Run)

```bash
python -c "
from core.benchmarking import run_benchmark, BenchmarkConfig
from core.skills.os_skills.intelligent_router import IntelligentRouter

config = BenchmarkConfig(
    dataset_path='tests/benchmarking/datasets/benchmark_tasks_v1.jsonl',
    baseline_model='claude-opus-5',
    routing_system=IntelligentRouter(),
)
result = run_benchmark(config)
print(f'Verdict: {result.metrics.summary}')
result.save_report('tests/benchmarking/results/REPORT_2026-09-20.md')
"
```

### Python (Programmatic)

```python
from core.benchmarking import run_benchmark, BenchmarkConfig
from core.skills.os_skills.intelligent_router import IntelligentRouter

config = BenchmarkConfig(
    dataset_path="tests/benchmarking/datasets/benchmark_tasks_v1.jsonl",
    baseline_model="claude-opus-5",
    routing_system=IntelligentRouter(),
    temperature=0.0,  # Deterministic
    max_tokens=4096,
    output_dir="tests/benchmarking/results",
)

result = run_benchmark(config)

# Access metrics
print(f"Token Savings: {result.metrics.token_metrics.mean_savings_pct:.1f}%")
print(f"Latency Improvement: {result.metrics.latency_metrics.mean_improvement_pct:.1f}%")
print(f"Quality Accuracy: {result.metrics.quality_metrics.accuracy_routed_pct:.1f}%")
print(f"Verdict: {result.metrics.summary}")

# Save results
result.save_report("BENCHMARK_REPORT.md")
```

### SkillForge (Skill Integration)

```python
from core.skills.os_skills.benchmarking_scientific_llm import ScientificLLMBenchmarkingSkill
from core.skills.os_skills.intelligent_router import IntelligentRouter

skill = ScientificLLMBenchmarkingSkill()

result = skill.execute(config={
    "dataset_path": "tests/benchmarking/datasets/benchmark_tasks_v1.jsonl",
    "baseline_model": "claude-opus-5",
    "routing_system": IntelligentRouter(),
})

print(f"Verdict: {result['verdict']}")
print(f"Token Savings: {result['token_savings_pct']:.1f}%")
print(f"Report: {result['report']}")
```

---

## Interpreting Results

### Token Savings

**What It Measures:** Do smaller models (Haiku/Sonnet) use fewer tokens than always-Opus?

**Interpretation:**
- **✅ ≥20% savings:** Routing is cost-effective
- **⚠️ 10-20% savings:** Marginal benefit; consider if quality trade-off is worth it
- **❌ <10% savings:** Routing offers little cost advantage; investigate tier classification

**If Savings Are Low:**
1. Check tier classification (are SIMPLE tasks being misclassified as MEDIUM?)
2. Review token estimation heuristics (are they too generous?)
3. Check if smaller models are actually being used (routing decision logging)

### Latency Improvement

**What It Measures:** Do smaller models respond faster than Opus?

**Key Insight:** Look at **p99 latency**, not just mean:
- Mean latency can improve even if p99 (worst-case) gets worse
- p99 is what users experience in worst case

**Interpretation:**
- **✅ ≥10% improvement (all percentiles):** Routing is faster
- **⚠️ ≥10% mean, but p99 worse:** Inconsistent benefit; may not be worth it
- **❌ No improvement or worse:** Routing adds overhead; investigate routing decision latency

**If Latency Doesn't Improve:**
1. Check engine selection (is native mode always chosen, adding latency overhead?)
2. Profile routing decision overhead (how long does route_task() take?)
3. Check if delegated engines (ACS/TDE) would be faster for MEDIUM tasks

### Quality Accuracy

**What It Measures:** Does routing maintain output correctness vs. Opus baseline?

**Interpretation:**
- **✅ ≥98% accuracy, ≤2% regression:** Quality is maintained
- **⚠️ 97-98% accuracy, 2-5% regression:** Slight quality loss; acceptable if token savings justify
- **❌ <97% accuracy or >5% regression:** Quality significantly degraded; do not deploy

**Per-Tier Regression:**
- **❌ SIMPLE tier regression:** Critical — SIMPLE tasks should always be correct
- **⚠️ MEDIUM tier regression:** Concerning — Sonnet should handle MEDIUM well
- **✅ COMPLEX tier regression (small):** Expected — Haiku may not handle complex reasoning

**If Quality Is Poor:**
1. Analyze failing tasks by tier/category (which categories fail most?)
2. Check LLM judge rubric (is the rubric too strict or incorrect?)
3. Review golden truth (is the golden output actually correct?)

---

## Customizing the Benchmark

### Use Your Own Task Dataset

```python
# Create your own JSONL file with 180 tasks
# (or whatever size you want; minimum N=30 for statistical validity)

config = BenchmarkConfig(
    dataset_path="path/to/your/tasks.jsonl",
    baseline_model="claude-opus-5",
    routing_system=your_routing_system,
)
result = run_benchmark(config)
```

### Compare Two Models Instead of Routing

```python
# Instead of comparing always-Opus vs. routing,
# compare two models (e.g., Claude 3.5 vs. Claude 3.6)

# Create a simple "router" that always returns the same model:
class SingleModelRouter:
    def route_task(self, task_input, **kwargs):
        return {"model": "claude-3.6-sonnet-20260101"}

config = BenchmarkConfig(
    dataset_path="tasks.jsonl",
    baseline_model="claude-opus-5",
    routing_system=SingleModelRouter(),
)
result = run_benchmark(config)
# Result will show Claude 3.6 vs. Opus comparison
```

### Adjust Sample Size

```python
# For quick validation, use smaller sample (N=30)
# For publication-quality results, use larger sample (N=500)

# Just adjust the dataset file to have fewer/more tasks
# Metrics calculations work with any N >= 10
```

---

## A/B Testing with Baselines

Benchmarks create **immutable baseline runs** that can be compared to future runs:

```bash
# First benchmark (baseline)
python benchmark_run.py > tests/benchmarking/results/baseline_opus_run_2026-09-20.jsonl
python benchmark_run.py > tests/benchmarking/results/routing_run_2026-09-20.jsonl

# Later benchmark (new routing version)
python benchmark_run.py > tests/benchmarking/results/baseline_opus_run_2026-09-25.jsonl
python benchmark_run.py > tests/benchmarking/results/routing_run_v2_2026-09-25.jsonl

# Compare
from core.benchmarking import compare_runs
comparison = compare_runs(
    old_routing="tests/benchmarking/results/routing_run_2026-09-20.jsonl",
    new_routing="tests/benchmarking/results/routing_run_v2_2026-09-25.jsonl",
)
print(f"Token savings improvement: {comparison.token_delta}%")
```

---

## Troubleshooting

| Problem | Diagnosis | Solution |
|---|---|---|
| **LLM API errors (timeout, rate limit)** | API overloaded or auth failure | Stagger calls (already done in harness), check API key, retry with backoff |
| **Token counts don't match expectations** | Token estimation heuristic wrong | Verify with `tokenizer` tool, adjust `_estimate_tokens()` in router |
| **Quality scores all incorrect** | LLM judge rubric too strict or wrong | Review rubric in `golden_truth.py`, adjust scoring criteria |
| **Statistics show p-value > 0.05** | Sample size too small or effect too small | Run more tasks (increase N), check if effect is real |
| **p99 latency worse than p50** | High-variance model (likely) | This is OK; means p99 is rare case; check if acceptable |
| **SIMPLE tasks have poor quality** | Tier classification wrong | Review tier boundaries in `intelligent_router.py` (currently <50 tokens) |

---

## Next Steps

1. **Run the benchmark** on your routing system and capture baseline results
2. **Analyze the verdict** — does your system meet all three thresholds?
3. **Optimize if needed** — use root-cause-by-layer (CONCEPT-0001) to debug failures
4. **Re-benchmark** after changes to validate improvements
5. **Deploy to production** once all metrics pass with p<0.05

---

## References

- **CONCEPT-0047:** Scientific LLM Benchmarking methodology (full details)
- **ADR-0867:** Intelligent Routing design
- **ADR-0314:** Learning infrastructure (uses benchmarking metrics for feedback loop)
- **CONCEPT-0001:** Root-cause-by-layer debugging methodology (use if metrics fail)

---

## Performance Notes

**Approximate runtimes:**
- Baseline run (180 Opus calls): ~3-5 minutes
- Routing run (180 routing calls, mix of Haiku/Sonnet/Opus): ~4-6 minutes
- Quality grading (LLM judge on 180 outputs): ~10-15 minutes
- Metrics calculation: ~1 minute
- **Total:** ~20-30 minutes

**Cost (rough estimates):**
- Baseline run: ~$0.50-1.00 (180 × Opus call)
- Routing run: ~$0.20-0.50 (cheaper models)
- Quality grading: ~$0.30-0.50 (180 × Opus judge)
- **Total:** ~$1.00-2.00 per full benchmark

---

**Author:** Claude Haiku 4.5 (Anthropic)  
**Last Updated:** 2026-09-20  
**Status:** Production-Ready
