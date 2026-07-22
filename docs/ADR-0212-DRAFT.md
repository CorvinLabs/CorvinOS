# ADR-0212 (DRAFT): Ecosystem Feature Telemetry — Instance × Features Matrix

**Status:** DRAFT  
**Date:** 2026-07-22  
**Authors:** Silvio (maintainer decision)  

---

## Summary

Add **aggregated, closed-enum feature telemetry** to understand which CorvinOS capabilities are actually used in production. Shipped as a unified payload with existing 5-minute heartbeat ping; consent gates to `ping_enabled` (GDPR Art. 6(1)(f) legitimate interest).

---

## Motivation

Current state:
- Ping captures: instance_id, version, platform, python_minor, engines + models
- Analytics can answer: "How many instances use Hermes?" or "Which Python versions?"
- **Cannot answer:** "Do users actually use Workflows?" "What % connected Slack?" "Is LDD enabled anywhere?"

**Problem:** Without feature telemetry, maintainer prioritization is blind. Should we invest in Workflows, A2A, or browser automation? No data.

---

## Decision

Extend ping payload with **instance-level feature aggregates** — not per-action events, only config snapshots + coarse counts:

1. **What to track:** 7 Bridges (connected?), LDD (enabled?), A2A (delegation count), Workflows (created/run counts), Browser (used?), Compute (ACS count), Voice (turn count), etc. — **closed enum only**.

2. **How to collect:** Local snapshot every 5min (piggybacked on heartbeat), stored in `~/.corvin/telemetry/feature_snapshot.json`, validated via fail-closed `_assert_safe_features()`.

3. **Consent:** Reuse `ping_enabled` gate; disable ping → disable all telemetry.

4. **Compliance:** GDPR Art. 6(1)(f) legitimate interest; data minimization (aggregates, no PII, CONTENT-FREE).

5. **Dashboard:** New "Ecosystem Feature Heatmap" tab (instances × features, heatmap by adoption %).

---

## Alternatives Considered

### A. Per-action telemetry
- **Pro:** Detailed usage patterns
- **Con:** High volume (DOS backend), complex consent per event, privacy risk (spammable)
- **Rejected:** Synthesis chose aggregation

### B. Sync heartbeat + separate feature events
- **Pro:** Cleaner separation
- **Con:** Dual consent gates confuse users, double backend load
- **Rejected:** Synthesis chose unified payload

### C. No telemetry (status quo)
- **Pro:** Zero privacy risk
- **Con:** Maintainer has no data for prioritization
- **Rejected:** Problem unsolved

---

## Implementation

### Files to Create/Modify

1. **`aco/telemetry/feature_snapshot.py`** (new)
   - `collect_feature_snapshot(home: Path) -> dict`
   - `_assert_safe_features(snapshot: dict) -> dict`
   
2. **`aco/heartbeat.py`** (modify)
   - Append `features` key to ping payload
   - Write feature_snapshot.json locally

3. **`core/console/routes/stats.py`** (new endpoint)
   - `GET /stats/features` → returns aggregated features from all instances
   - Dashboard backend

4. **`core/console/web-next/src/pages/stats/FeatureHeatmap.tsx`** (new)
   - Matrix table: instances × features
   - Heatmap colors by adoption %

5. **`docs/feature-telemetry-schema.md`** (already created)

### Testing

- Unit: `_assert_safe_features()` drops invalid enums
- Unit: `collect_feature_snapshot()` counts correctly on mock home
- Integration: snapshot → heartbeat → ping payload → backend
- E2E: /stats/features returns heatmap, frontend renders

---

## Risks & Mitigations

| Risk | Mitigation |
|------|-----------|
| Feature bloat (schema unmaintainable) | Closed enums only; new features require ADR review |
| Privacy creep (tracking behaviors) | Fail-closed validator; doc-level commitment to aggregates-only |
| Backend DOS (high volume) | 5min cadence (same as existing ping); counts + booleans, not events |
| User distrust | Transparency: full schema public; opt-out gate clear |

---

## Rollout

1. **v0.10.58:** Deploy feature_snapshot.py + heartbeat integration (locally collected, not yet shipped)
2. **v0.10.59:** Deploy stats endpoint + dashboard tab; begin shipping
3. **User comms:** Blog post explaining telemetry, heatmap usage, opt-out instructions

---

## Next Steps

- Implement collection logic (feature_snapshot.py)
- Integrate heartbeat.py
- Stats API endpoint
- Dashboard heatmap tab + E2E tests

