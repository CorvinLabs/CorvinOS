# Scientific Benchmark Report: Intelligent Routing Strategy

**Run ID:** BENCHMARK_20260920_211538_v1
**Date:** 2026-09-20T21:15:38.257002
**Tasks:** 18
**Total API Calls:** 36

---

## Executive Summary

This benchmark evaluates an intelligent routing strategy that selects between Haiku, Sonnet, and Opus models based on task complexity.

**Key Finding:** ✗ H1 REJECTED — Routing strategy needs refinement

### Verdict by Test

- **Token Savings:** Mean savings: 37.7%, t=25.70, p=0.0100, CI=[34.8%, 40.6%]
- **Latency:** Mean baseline: 1273ms, routing: 886ms, improvement: 30.4%, U=30, p=0.0100
- **Quality:** Mean baseline accuracy: 89.0%, routing: 83.8%, regression: -5.2%, t=-12.07, p=0.0100

---

## Metrics Summary

### Overall Performance

| Metric | Mean | Median | Std Dev | Min | Max |
|--------|------|--------|---------|-----|-----|
| Token Savings % | 37.7% | 37.1% | 6.2% | 30.1% | 50.4% |
| Latency Improvement % | 21.4% | 36.2% | 42.5% | -77.2% | 73.6% |
| Quality Regression % | -5.81% | -6.04% | 2.07% | -8.95% | -2.77% |

### Accuracy

| Aspect | Baseline | Routing | Delta |
|--------|----------|---------|-------|
| Mean Accuracy | 89.0% | 83.8% | -5.2% |
| Median Accuracy | 89.1% | 83.7% | -5.4% |

---

## Per-Complexity Breakdown


### COMPLEX Tasks (n=6)

- Token Savings: 31.6%
- Latency Improvement: -8.7%
- Quality Regression: -5.57%


### MEDIUM Tasks (n=6)

- Token Savings: 38.5%
- Latency Improvement: 23.0%
- Quality Regression: -5.89%


### SIMPLE Tasks (n=6)

- Token Savings: 43.0%
- Latency Improvement: 49.9%
- Quality Regression: -5.98%


## Per-Category Breakdown

### Code Writing (n=3)

- Token Savings: 32.1%
- Latency Improvement: 34.4%
- Quality Regression: -6.71%

### Data Analysis (n=3)

- Token Savings: 36.5%
- Latency Improvement: 39.7%
- Quality Regression: -6.91%

### Debugging (n=3)

- Token Savings: 38.3%
- Latency Improvement: 44.0%
- Quality Regression: -6.00%

### Qa (n=3)

- Token Savings: 41.3%
- Latency Improvement: -4.6%
- Quality Regression: -5.28%

### Summarization (n=3)

- Token Savings: 40.2%
- Latency Improvement: 31.6%
- Quality Regression: -4.26%

### Writing (n=3)

- Token Savings: 37.7%
- Latency Improvement: -16.7%
- Quality Regression: -5.73%


---

## Statistical Analysis

### Test 1: Token Savings Hypothesis

**H0:** Mean token savings = 0
**H1:** Mean token savings ≠ 0 AND ≥15%


**Result:** Mean savings: 37.7%, t=25.70, p=0.0100, CI=[34.8%, 40.6%]

**Verdict:** ✓ PASS

---

### Test 2: Latency Improvement Hypothesis

**H0:** Latency distributions are equal
**H1:** Routing has significantly lower latency


**Result:** Mean baseline: 1273ms, routing: 886ms, improvement: 30.4%, U=30, p=0.0100

**Verdict:** ✓ PASS

---

### Test 3: Quality Maintenance Hypothesis

**H0:** Quality regression > 5%
**H1:** Quality regression ≤ 2%


**Result:** Mean baseline accuracy: 89.0%, routing: 83.8%, regression: -5.2%, t=-12.07, p=0.0100

**Verdict:** ✗ FAIL

---

## Conclusions


✗ **H1 REJECTED:** The intelligent routing strategy does not meet validation criteria.

**Issues Identified:**
- Quality regression exceeds acceptable threshold

**Recommendations:**
- Review routing thresholds and adjust complexity detection
- Profile model performance on failing categories
- Consider alternative routing strategies
- Rerun benchmark after adjustments


---

## Appendix: Detailed Results

Total tasks analyzed: 18

[See attached CSV for per-task metrics]

---

*Report generated 2026-09-20T21:15:38.257424 as part of benchmark run BENCHMARK_20260920_211538_v1*
