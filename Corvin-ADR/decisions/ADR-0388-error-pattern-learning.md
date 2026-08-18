# ADR-0388: Error Pattern Learning & Failure Prediction

**ID:** ADR-0388  
**Status:** ACCEPTED  
**Depends on:** ADR-0314 (Learning Infrastructure)  
**Related to:** ADR-0317 (Outcome Feedback)  
**Paths:**
- `core/learning/error_patterns.py`
- `core/learning/tests/test_v0_4_weeks3_4.py`

**Docs:**
- `docs/RELEASE_NOTES_v0.4.md`

---

## Summary

Learns error patterns from observations and predicts task failure with 70%+ precision.

- **Pattern Detection:** Identifies recurring error types per task type (≥3 observations)
- **Failure Prediction:** Combines pattern base rate with operator error history
- **Root Cause Analysis:** Correlates errors across task types and operators
- **Confidence Tracking:** Patterns have confidence ≥frequency/N

## Decision

**Problem:** Errors are reactive — detected after they happen. Operators want early warning and root cause analysis to prevent failures.

**Solution:** Two-stage learning:
1. **PatternDetector**: Groups errors by (task_type, error_type) pair, tracks frequency
2. **ErrorPredictor**: Combines pattern base rate (70% of estimate) with operator history (30%) for failure probability

**Why:** Patterns capture systemic issues (e.g., "code_gen always times out after 5min"). Operator history personalizes prediction (some operators are inherently more error-prone). Blended approach avoids overfitting to either signal.

## Consequences

**Positive:**
- Early failure warning (before execution completes)
- Root cause tracking (which task types have highest error rates)
- Per-operator calibration (personalized predictions)
- Pattern confidence is quantifiable (frequency-based)

**Negative:**
- Requires sufficient error data to form patterns (min 3 observations)
- Predictions improve slowly with sparse errors
- Circular dependency: need errors to learn patterns, but want to prevent errors

## Compliance

**GDPR Art. 5 (Data minimization):** Error logging only captures error_type and task_type (no task content)  
**GDPR Art. 32 (Integrity):** All error observations are hash-chained in audit trail

## Test Coverage

- ✓ Pattern detection (min observations, severity levels)
- ✓ Failure prediction (no patterns, with patterns, per-operator)
- ✓ Precision/recall (70%+ target validation)
- ✓ Root cause analysis (task type and operator breakdowns)
- ✓ Correlation matrix (error × task type)
