# ADR-0387: Confidence Alerting System

**ID:** ADR-0387  
**Status:** ACCEPTED  
**Depends on:** ADR-0314 (Learning Infrastructure)  
**Related to:** ADR-0315 (Confidence Intervals), ADR-0319 (Attention Budget)  
**Paths:**
- `core/learning/confidence_alerts.py`
- `core/learning/tests/test_v0_4_weeks3_4.py`

**Docs:**
- `docs/RELEASE_NOTES_v0.4.md`

---

## Summary

Implements operator-facing alerting system for low-confidence decisions:
- Tunable thresholds (default 0.7, per-operator/task-type overrides)
- Rate limiting (max 2 alerts/day per operator, prevent alert fatigue)
- Severity levels (INFO/WARNING/CRITICAL based on gap from threshold)
- Alert history and statistics for monitoring

## Decision

**Problem:** Without alerting, operators don't know when system is uncertain. High-confidence decisions might fail without operator awareness. Alert fatigue is also a risk if unconstrained.

**Solution:** Three-tier alert management:
1. **Threshold Manager**: Operator-tunable confidence thresholds with hierarchy (task-type > operator > default)
2. **Rate Limiter**: Prevents alert spam (max N alerts/day per operator, configurable)
3. **Alert History**: Tracks all alerts for analysis, compliance, and learning

**Why:** Operators need real-time uncertainty feedback. Rate limiting prevents alert fatigue (research shows >2/day reduces effectiveness). Tunable thresholds let operators set risk tolerance.

## Consequences

**Positive:**
- Operators can intervene on uncertain tasks before they fail
- Configurable thresholds allow per-operator/task-type customization
- Rate limiting prevents alert fatigue and maintains alert credibility
- Full audit trail for compliance and learning

**Negative:**
- Adds latency to decision path (checked before commit)
- Requires operator education (understanding confidence scores)
- Tuning thresholds is operator responsibility

## Compliance

**GDPR Art. 6 (Lawful Basis):** Legitimate interest — operator benefit (intervention opportunity)  
**GDPR Art. 32 (Integrity):** Alert history is audit-logged and hash-chained  
**GDPR Art. 30 (Record-keeping):** Every alert recorded with timestamp, severity, context

## Test Coverage

- ✓ Threshold management (default, per-operator, per-task-type)
- ✓ Rate limiting (enforcement, carryover across days)
- ✓ Alert generation (correct severity levels)
- ✓ Alert history (queryable by operator, time-range)
- ✓ Performance (<10ms per alert)
