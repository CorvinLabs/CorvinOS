# ADR-0389: Operator Fingerprinting — 4D Style Model

**ID:** ADR-0389  
**Status:** ACCEPTED  
**Depends on:** ADR-0314 (Learning Infrastructure)  
**Related to:** ADR-0383 (Operator Fingerprint Data Model)  
**Paths:**
- `core/learning/operator_fingerprint.py`
- `core/learning/tests/test_v0_4_weeks3_4.py`

**Docs:**
- `docs/RELEASE_NOTES_v0.4.md`

---

## Summary

Learns 4-dimensional operator style profile from task decisions and outcomes:
1. **Risk Tolerance** (0=conservative, 1=aggressive) — inferred from accuracy-latency tradeoffs
2. **Speed Preference** (0=thorough, 1=fast) — inferred from average task latency
3. **Communication Style** (terse/neutral/detailed) — inferred from feedback text length
4. **Expertise Profile** (per task_type) — inferred from per-task-type success rates

Converges after 50 observations with confidence ≥0.7.

## Decision

**Problem:** One-size-fits-all guidance doesn't work. Conservative operators want safe recommendations. Fast operators want quick wins. Guidance should match operator style.

**Solution:** Bayesian learning of 4D fingerprint from observed decisions:
- **Risk:** Computed as (mean_accuracy - variance) / 2.0 (high-consistency high-performer = aggressive)
- **Speed:** Computed as (200 - mean_latency) / 150 normalized to [0,1]
- **Communication:** Threshold-based on mean feedback length (<20=terse, 20-100=neutral, >100=detailed)
- **Expertise:** Per-task-type accuracy (no aggregation, task-specific)

Convergence: confidence = 0.9 if last 20 tasks stable ±0.05 vs overall mean, else 0.7 after 50 obs.

**Why:** 4D model captures essential operator dimensions without over-parameterization. Bayesian learning allows early guidance (before convergence) with uncertainty quantification. Per-task-type expertise avoids false generalization.

## Consequences

**Positive:**
- Personalized guidance improves operator satisfaction (NPS +18% target)
- Convergence at 50 tasks is achievable in typical workflows
- Separate expertise per task type prevents over-fitting
- Confidence metric allows adaptive guidance (low-confidence → generic, high-confidence → personalized)

**Negative:**
- Requires 50 diverse observations to converge (slow on repetitive workloads)
- Style might drift over time (would need periodic re-convergence)
- Communication style is brittle (depends on operator verbosity preferences)

## Compliance

**GDPR Art. 5 (Data minimization):** Fingerprint stores aggregated metrics only (no task content, no feedback text)  
**GDPR Art. 6 (Lawful basis):** Legitimate interest — operator personalization  
**GDPR Art. 22 (Automated decision-making):** Fingerprint does NOT make automated decisions; it informs guidance which operator reviews

## Test Coverage

- ✓ Dimension computation (risk, speed, communication, expertise)
- ✓ Convergence detection (50 observations, confidence threshold)
- ✓ Registry management (multi-operator, per-operator fingerprints)
- ✓ Stability over time (last 20 vs overall mean)
- ✓ Edge cases (no observations, single observation, diverse data)
