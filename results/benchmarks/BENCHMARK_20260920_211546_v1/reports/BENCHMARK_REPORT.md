# Scientific Benchmark Report: Intelligent Routing Strategy

**Run ID:** BENCHMARK_20260920_211546_v1
**Date:** 2026-09-20T21:15:46.783642
**Tasks:** 180
**Total API Calls:** 360

---

## Executive Summary

This benchmark evaluates an intelligent routing strategy that selects between Haiku, Sonnet, and Opus models based on task complexity.

**Key Finding:** ✗ H1 REJECTED — Routing strategy needs refinement

### Verdict by Test

- **Token Savings:** Mean savings: 37.8%, t=87.62, p=0.0100, CI=[37.0%, 38.7%]
- **Latency:** Mean baseline: 1247ms, routing: 886ms, improvement: 29.0%, U=29, p=0.0100
- **Quality:** Mean baseline accuracy: 89.8%, routing: 84.8%, regression: -4.9%, t=-36.78, p=0.0100

---

## Metrics Summary

### Overall Performance

| Metric | Mean | Median | Std Dev | Min | Max |
|--------|------|--------|---------|-----|-----|
| Token Savings % | 37.8% | 37.8% | 5.8% | 27.8% | 53.9% |
| Latency Improvement % | 19.3% | 32.3% | 44.5% | -143.0% | 78.6% |
| Quality Regression % | -5.50% | -5.48% | 2.01% | -9.39% | -2.16% |

### Accuracy

| Aspect | Baseline | Routing | Delta |
|--------|----------|---------|-------|
| Mean Accuracy | 89.8% | 84.8% | -4.9% |
| Median Accuracy | 90.2% | 85.0% | -5.2% |

---

## Per-Complexity Breakdown


### COMPLEX Tasks (n=60)

- Token Savings: 31.5%
- Latency Improvement: -21.7%
- Quality Regression: -5.74%


### MEDIUM Tasks (n=60)

- Token Savings: 39.0%
- Latency Improvement: 33.8%
- Quality Regression: -5.17%


### SIMPLE Tasks (n=60)

- Token Savings: 43.0%
- Latency Improvement: 45.8%
- Quality Regression: -5.59%


## Per-Category Breakdown

### Code Writing (n=30)

- Token Savings: 37.7%
- Latency Improvement: 17.3%
- Quality Regression: -5.33%

### Data Analysis (n=30)

- Token Savings: 37.1%
- Latency Improvement: 5.4%
- Quality Regression: -5.34%

### Debugging (n=30)

- Token Savings: 38.8%
- Latency Improvement: 20.5%
- Quality Regression: -5.75%

### Qa (n=30)

- Token Savings: 37.5%
- Latency Improvement: 23.2%
- Quality Regression: -5.37%

### Summarization (n=30)

- Token Savings: 37.4%
- Latency Improvement: 27.0%
- Quality Regression: -5.42%

### Writing (n=30)

- Token Savings: 38.5%
- Latency Improvement: 22.4%
- Quality Regression: -5.81%


---

## Statistical Analysis

### Test 1: Token Savings Hypothesis

**H0:** Mean token savings = 0
**H1:** Mean token savings ≠ 0 AND ≥15%


**Result:** Mean savings: 37.8%, t=87.62, p=0.0100, CI=[37.0%, 38.7%]

**Verdict:** ✓ PASS

---

### Test 2: Latency Improvement Hypothesis

**H0:** Latency distributions are equal
**H1:** Routing has significantly lower latency


**Result:** Mean baseline: 1247ms, routing: 886ms, improvement: 29.0%, U=29, p=0.0100

**Verdict:** ✓ PASS

---

### Test 3: Quality Maintenance Hypothesis

**H0:** Quality regression > 5%
**H1:** Quality regression ≤ 2%


**Result:** Mean baseline accuracy: 89.8%, routing: 84.8%, regression: -4.9%, t=-36.78, p=0.0100

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

Total tasks analyzed: 180

[See attached CSV for per-task metrics]

---

*Report generated 2026-09-20T21:15:46.785712 as part of benchmark run BENCHMARK_20260920_211546_v1*
