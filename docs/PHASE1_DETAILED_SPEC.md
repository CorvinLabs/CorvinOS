# Phase 1 Detailed Specification: User Stories & Acceptance Tests

**Status:** 🟢 **READY FOR EXECUTION**  
**Date:** 2026-09-22  
**Target Launch:** 2026-10-15  
**Duration:** 4 weeks (development + testing)  
**Spec Owner:** [Tech Lead]

---

## Table of Contents

1. [Stream 1: Marketplace Integration](#stream-1-marketplace-integration)
2. [Stream 2: Learning Loop Activation](#stream-2-learning-loop-activation)
3. [Stream 3: Cost Insights & Optimization](#stream-3-cost-insights--optimization)
4. [Stream 4: Operator Onboarding](#stream-4-operator-onboarding--documentation)
5. [Cross-Stream Dependencies](#cross-stream-dependencies)
6. [Risk & Assumptions](#risk--assumptions)
7. [ADR Requirements](#adr-requirements)

---

## Stream 1: Marketplace Integration

**Goal:** Users can browse, install, enable Skills from marketplace  
**Timeline:** Weeks 1–2 (14 days)  
**Team:** 2 backend engineers, 1 frontend engineer  
**Total Story Points:** 60 (S=5, M=8, L=13, XL=21)

### User Stories

#### US-1-1: Discover Skills in Marketplace (M, 8 points)

**As an** operator  
**I want to** browse available Skills in the marketplace by category  
**So that** I can discover and understand what Skills are available for my system

**Acceptance Criteria:**
- AC1: Marketplace panel loads in <200ms with list of 20+ Skills
- AC2: Skills grouped by category (routing, context, security, learning, analytics)
- AC3: Each Skill card shows: name, description, version, author, rating, install button
- AC4: Search/filter by category or keyword works correctly
- AC5: Pagination works for >50 Skills (load next page in <500ms)

**Acceptance Tests:**
- `test_marketplace_discovery_panel_loads` — Panel renders, shows ≥20 skills, category tabs visible
- `test_marketplace_filter_by_category` — Filter to "routing" category, only routing skills shown
- `test_marketplace_search_skills` — Search "context", returns os.context_adapter + others
- `test_marketplace_pagination` — Click "next", loads next 20 skills in <500ms

**Dependencies:** None (prerequisites: Phase 0 plugins stable)

**Story Points:** 8

---

#### US-1-2: Install Skill from Marketplace (L, 13 points)

**As an** operator  
**I want to** install a Skill from the marketplace  
**So that** I can extend CorvinOS with new functionality

**Acceptance Criteria:**
- AC1: Click "Install" → download manifest + verify signature (SHA256)
- AC2: Verify checksum matches marketplace record (prevent tampering)
- AC3: Check skill dependencies (e.g., os.context_adapter needs os.delegation_router)
- AC4: If dependencies missing, prompt to auto-install or warn
- AC5: Create audit event `skill_installed` with skill_id, version, checksum, tenant_id
- AC6: Skill available for loading at next boot (or hot-reload if supported)
- AC7: Installation takes <30 seconds (download + verify + save)

**Acceptance Tests:**
- `test_skill_install_from_marketplace` — Install os.context_adapter, verify file created at `~/.corvin/skills/installed/`
- `test_skill_install_signature_verification` — Tampered manifest rejected (bad SHA256)
- `test_skill_install_dependency_check` — Missing dependency triggers warning, auto-install offered
- `test_skill_install_audit_event` — Verify `skill_installed` event in audit chain with correct payload
- `test_skill_install_performance` — Installation completes in <30 seconds

**Dependencies:** US-1-1 (browse available skills), ADR-2029 (Control Plane plugin manager)

**Story Points:** 13

---

#### US-1-3: Enable/Disable Installed Skills (M, 8 points)

**As an** operator  
**I want to** enable or disable installed Skills at runtime  
**So that** I can control which Skills are active without uninstalling

**Acceptance Criteria:**
- AC1: Skills panel shows list of installed Skills with enable/disable toggle
- AC2: Clicking toggle changes skill status immediately (no boot required)
- AC3: Disabled skill is not executed on next routing decision
- AC4: Emit audit event `skill_disabled` or `skill_enabled` (with reason if available)
- AC5: Disable is idempotent (disabling an already-disabled skill is safe)
- AC6: UI reflects state correctly after toggle

**Acceptance Tests:**
- `test_skill_enable_disable_toggle` — Install skill, toggle off, verify not executed on next route
- `test_skill_disable_audit_event` — Disable skill, verify `skill_disabled` event logged
- `test_skill_enable_after_disable` — Disable then enable, skill executes again on next route
- `test_skill_disable_idempotent` — Disable same skill twice, no errors

**Dependencies:** US-1-2 (install skill), ADR-2029 (Control Plane)

**Story Points:** 8

---

#### US-1-4: Manage Skill Versions & Upgrades (L, 13 points)

**As an** operator  
**I want to** see available upgrades and upgrade installed Skills  
**So that** I can stay on current versions and get bug fixes

**Acceptance Criteria:**
- AC1: Installed Skills panel shows "Version 1.0.5 → 1.0.6 available" indicator
- AC2: Click "Upgrade" → download new version, verify signature, backup old version
- AC3: If skill config unchanged, hot-reload new version (no downtime)
- AC4: If skill config changed, require operator confirmation before applying
- AC5: Keep prior 2 versions on disk (enable quick rollback)
- AC6: Rollback button → revert to prior version in <5 seconds
- AC7: Emit audit event `skill_upgraded` (old_version, new_version, rollback_available)

**Acceptance Tests:**
- `test_skill_upgrade_available` — New version shown in UI when available on marketplace
- `test_skill_upgrade_no_config_change` — Upgrade without config changes, hot-reload succeeds, downtime=0
- `test_skill_upgrade_config_change` — Config changes detected, operator confirmation required
- `test_skill_rollback_to_prior_version` — Rollback in <5 seconds, audit event logged
- `test_skill_upgrade_signature_verification` — Bad signature rejected during upgrade

**Dependencies:** US-1-2 (install skill), ADR-2029 (Control Plane)

**Story Points:** 13

---

#### US-1-5: Resolve Skill Dependencies Automatically (S, 5 points)

**As an** operator  
**I want to** install a Skill that has dependencies, and have them auto-install  
**So that** I don't manually hunt down and install dependent skills

**Acceptance Criteria:**
- AC1: When installing a skill with dependencies, show list of required skills
- AC2: "Auto-install dependencies" button → recursively installs all deps
- AC3: Respects version constraints (e.g., "needs os.delegation_router v1.0+")
- AC4: Detects circular dependencies (e.g., A→B→A) and warns
- AC5: Installation order respects dependency graph (leaf nodes first)

**Acceptance Tests:**
- `test_skill_dependencies_auto_install` — Install os.context_adapter, auto-installs os.delegation_router
- `test_skill_dependencies_version_constraint` — Correct version installed (respects v1.0+)
- `test_skill_dependencies_circular_detection` — Circular dependency detected + warned
- `test_skill_dependencies_topological_sort` — Install order respects dependency graph

**Dependencies:** US-1-2 (install skill)

**Story Points:** 5

---

#### US-1-6: Marketplace API & Skill Index (L, 13 points)

**As a** backend engineer  
**I want to** provide a stable marketplace API for skill discovery and installation  
**So that** operators can discover and install skills programmatically

**Acceptance Criteria:**
- AC1: GET /v1/console/marketplace/index → returns JSON list of 50+ skills
- AC2: Each skill has: id, name, description, version, author, category, dependencies, checksum
- AC3: GET /v1/console/marketplace/skills/{skill_id} → returns full skill metadata
- AC4: GET /v1/console/marketplace/skills/{skill_id}/manifest → returns plugin.json + signature
- AC5: Response time <500ms even for 500+ skills (use caching + pagination)
- AC6: Verify signature of each manifest against marketplace cert (prevent MITM)
- AC7: Rate limit: 100 req/min per operator (prevent abuse)

**Acceptance Tests:**
- `test_marketplace_api_index` — GET /v1/console/marketplace/index returns 50+ skills
- `test_marketplace_api_skill_details` — GET /v1/console/marketplace/skills/os.context_adapter returns correct metadata
- `test_marketplace_api_manifest_download` — GET manifest endpoint, signature verifies
- `test_marketplace_api_performance` — 500+ skills loaded in <500ms
- `test_marketplace_api_rate_limiting` — 101st request in same minute returns 429 Too Many Requests

**Dependencies:** None (new backend service)

**Story Points:** 13

---

#### US-1-7: Skill Signature Verification (M, 8 points)

**As the** security team  
**I want to** verify all marketplace skills are cryptographically signed  
**So that** operators can't install tampered skills

**Acceptance Criteria:**
- AC1: Every skill manifest signed with Corvin marketplace certificate (RSA-2048)
- AC2: Signature verification happens before installation (fail-closed)
- AC3: Manifest tampering detected → installation aborted + audited
- AC4: Trust chain: marketplace CA → skill publisher cert → manifest signature
- AC5: Revoked certificates checked (via CRL or OCSP, timeout <5s)
- AC6: Audit event `skill_signature_verification` logs result (pass/fail/revoked)

**Acceptance Tests:**
- `test_skill_signature_valid` — Valid manifest installed successfully
- `test_skill_signature_invalid` — Tampered manifest rejected (bad signature)
- `test_skill_signature_revoked_cert` — Revoked cert detected + installation blocked
- `test_skill_signature_timeout` — CRL check timeout handled gracefully (fail-closed)

**Dependencies:** US-1-2 (install skill), security infrastructure

**Story Points:** 8

---

### Acceptance Tests (Stream 1 Summary)

| Test | Scenario | Expected Result |
|---|---|---|
| `test_stream_1_complete_flow` | Browse → install → enable → upgrade → rollback | All steps succeed, 0 errors, 5 audit events logged |
| `test_stream_1_marketplace_api` | Call all 5 marketplace endpoints | All respond <500ms, signatures verify |
| `test_stream_1_dependency_resolution` | Install skill with 3-level dependency tree | Correct topological order, all installed |
| `test_stream_1_concurrent_installs` | 5 operators install different skills simultaneously | All succeed, no race conditions |

### Dependencies & Sequencing (Stream 1)

```
US-1-1 (Discover)
  ↓
US-1-2 (Install) — depends on US-1-1 (must see before install)
  ├→ US-1-3 (Enable/Disable) — requires installed skill
  ├→ US-1-4 (Upgrade) — requires installed skill
  ├→ US-1-5 (Dependencies) — parallelizable with US-1-2
  └→ US-1-6 (Marketplace API) — backend work, parallelizable
  
US-1-7 (Signatures) — parallelizable with all (security concern)
```

**Parallel Work:** US-1-1 + US-1-6 can start in Week 1; US-1-2–5, 7 start Week 1 after US-1-6 API stabilized.

### Risk & Assumptions (Stream 1)

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| **Marketplace unavailable** | LOW | MEDIUM | Fallback to local skill repo, retry logic |
| **Dependency graph circular** | MEDIUM | LOW | Topological sort + cycle detection in US-1-5 |
| **Signature verification slow** | LOW | MEDIUM | Cache certs, use hardware accel, CRL caching |
| **Version constraint parsing breaks** | LOW | LOW | Unit tests for semver parsing (6 test cases) |

**Assumptions:**
- Marketplace provides stable API + 50+ skills by Week 1 (external dependency)
- Skill authors provide valid manifests + signatures (marketplace validates before publishing)
- No major breaking changes to skill schema during Phase 1

---

## Stream 2: Learning Loop Activation

**Goal:** Operators provide feedback, Skills optimize autonomously  
**Timeline:** Weeks 1–2 (14 days)  
**Team:** 2 backend engineers, 1 frontend engineer  
**Total Story Points:** 55 (S=5, M=8, L=13, XL=21)

### User Stories

#### US-2-1: Collect Feedback from Operators (M, 8 points)

**As an** operator  
**I want to** provide feedback on routing decisions  
**So that** the system learns from my input and improves

**Acceptance Criteria:**
- AC1: Show recent routing decisions (last 20 in-memory or last 24h from audit trail)
- AC2: Each decision shows: timestamp, input request (first 100 chars), routed_to, confidence_score
- AC3: Operator can rate: "Correct", "Incorrect", "Unclear" (radio buttons)
- AC4: Optional comment field (max 256 chars)
- AC5: Submit feedback → emit FeedbackEvent (audited, tenant_id included)
- AC6: Success message: "Thanks, we've recorded your feedback"
- AC7: Form clears after submit (ready for next feedback)

**Acceptance Tests:**
- `test_feedback_collection_ui_renders` — Panel shows recent decisions with feedback buttons
- `test_feedback_submit_correct_decision` — Submit "Correct" feedback, FeedbackEvent logged
- `test_feedback_submit_with_comment` — Submit feedback + comment, both stored
- `test_feedback_audit_event` — Verify FeedbackEvent in audit chain with skill_id, confidence, rating

**Dependencies:** None (uses existing audit trail to fetch recent decisions)

**Story Points:** 8

---

#### US-2-2: Run Optimizer Loop Hourly (L, 13 points)

**As the** backend  
**I want to** read feedback events and update Skill configuration hourly  
**So that** Skills improve continuously based on operator feedback

**Acceptance Criteria:**
- AC1: Hourly cron job (configurable time, default 00:00 UTC) runs optimizer
- AC2: Read all FeedbackEvent from past 24h (grouped by skill_id, tenant_id)
- AC3: Calculate metrics: accuracy (% correct), confidence (avg score), agreement (concordance)
- AC4: If accuracy >0.85, recommend confidence threshold increase (+5 percentage points)
- AC5: If accuracy <0.70, recommend threshold decrease (−5 percentage points)
- AC6: Apply config update to skill (in-memory + disk persistence)
- AC7: Emit audit event `skill_config_updated` (old_config, new_config, reason, feedback_count)
- AC8: Handle no-feedback gracefully (skip optimizer, log info event)

**Acceptance Tests:**
- `test_optimizer_hourly_schedule` — Cron job runs at expected time
- `test_optimizer_config_update` — 20 feedback events (18 correct, 2 incorrect), threshold increased
- `test_optimizer_feedback_grouping` — Feedback grouped by skill_id + tenant_id correctly
- `test_optimizer_audit_event` — Config update event logged with old/new config + reason
- `test_optimizer_no_feedback_graceful` — Optimizer runs with 0 feedback, logs info, no crash

**Dependencies:** US-2-1 (collect feedback), Phase 0 EventStore (read learning events)

**Story Points:** 13

---

#### US-2-3: Show Convergence Dashboard (L, 13 points)

**As an** operator  
**I want to** see how well the Skill is performing and whether it's improving  
**So that** I understand if the learning loop is working

**Acceptance Criteria:**
- AC1: Dashboard shows: skill name, current confidence (0.0–1.0), feedback count, accuracy (%)
- AC2: Trend line (sparkline) shows confidence over last 7 days (1 data point/day)
- AC3: Status indicator: "Converging ↑↑" (↑ >0.05/day), "Flat →" (−0.05 to +0.05), "Diverging ↓↓" (↓ <−0.05/day)
- AC4: Show feedback count: "147 feedback events, 92% consistent"
- AC5: Recommend action based on status:
  - If converging: "Good! Keep providing feedback"
  - If flat: "Feedback is mixed, try being more consistent"
  - If diverging: "Something's wrong, consider resetting or checking your feedback"
- AC6: "Reset optimizer" button → clear learned config, revert to v0, log reset event

**Acceptance Tests:**
- `test_convergence_dashboard_renders` — Panel loads, shows all 5 fields
- `test_convergence_trend_calculation` — 7-day trend calculated correctly from daily scores
- `test_convergence_status_indicator` — Correct status (converging/flat/diverging) based on slope
- `test_convergence_recommendation_text` — Recommendation text matches status
- `test_convergence_reset_optimizer` — Click reset, old config restored, audit event logged

**Dependencies:** US-2-2 (optimizer running), Phase 0 EventStore (historical scores)

**Story Points:** 13

---

#### US-2-4: Feedback Quality Gate (M, 8 points)

**As the** system  
**I want to** detect and handle low-quality feedback  
**So that** the optimizer doesn't get misled by contradictory or noisy feedback

**Acceptance Criteria:**
- AC1: Detect contradiction: same input → "Correct" from one operator, "Incorrect" from another
- AC2: Detect noise: >20% disagreement on same input → flag feedback as low-quality
- AC3: Flag events with `quality_score` (0.0–1.0) during feedback collection
- AC4: Optimizer only uses feedback with quality_score >0.6 (configurable)
- AC5: Audit event `feedback_quality_flagged` for low-quality submissions
- AC6: Show operator confidence: "This feedback is consistent with 94% of similar decisions"

**Acceptance Tests:**
- `test_feedback_quality_contradiction_detected` — Same input rated yes + no, both flagged
- `test_feedback_quality_noise_calculation` — 21% disagreement on 100 samples, flagged as low-quality
- `test_feedback_quality_audit_event` — Flagged event appears in audit trail
- `test_feedback_quality_optimizer_filter` — Optimizer skips low-quality feedback (<0.6 score)
- `test_feedback_quality_operator_notification` — Operator shown "94% consistent" message

**Dependencies:** US-2-1 (collect feedback)

**Story Points:** 8

---

#### US-2-5: Learning Event Persistence & Audit Integration (S, 5 points)

**As the** backend  
**I want to** ensure all learning events are persisted to disk and audit-chained  
**So that** the learning loop is durable and auditable

**Acceptance Criteria:**
- AC1: All FeedbackEvent, SkillConfigUpdated events written to EventStore (date-partitioned JSON)
- AC2: Each event has: event_id, event_type, skill_id, tenant_id, timestamp, prev_hash, hash
- AC3: Hash-chain verified on read (prevent tampering)
- AC4: If EventStore write fails, audit chain write happened first (immutable)
- AC5: EventStore location: `~/.corvin/tenants/<tenant_id>/global/events/`
- AC6: Retain events for 365 days (automatic cleanup, configurable)

**Acceptance Tests:**
- `test_learning_event_persistence` — Write 100 feedback events, all persist to disk
- `test_learning_event_hash_chain` — Read events, verify all hash-links intact
- `test_learning_event_audit_integration` — Events also appear in audit.jsonl (not duplicated)
- `test_learning_event_write_failure` — Simulated write failure, audit chain still committed
- `test_learning_event_retention_cleanup` — 366-day-old event deleted on next cleanup

**Dependencies:** Phase 0 EventStore, ADR-0314 event schema

**Story Points:** 5

---

#### US-2-6: Learning Loop E2E Integration (M, 8 points)

**As the** system  
**I want to** prove the learning loop works end-to-end  
**So that** operators know feedback directly improves the system

**Acceptance Criteria:**
- AC1: E2E test: make routing decision → operator gives feedback → optimizer runs → new config applied
- AC2: Verify confidence score improved (or stayed same if feedback contradictory)
- AC3: All 5 events logged: skill_executed, feedback_submitted, feedback_quality_assessed, skill_config_updated, learning_confirmed
- AC4: Audit trail is complete + hash-chained
- AC5: No silent optimization (all changes visible via dashboard or audit)

**Acceptance Tests:**
- `test_learning_loop_e2e_happy_path` — Route request, feedback, optimize, verify improvement
- `test_learning_loop_e2e_contradictory_feedback` — Conflicting feedback, optimizer handles gracefully
- `test_learning_loop_e2e_audit_trail_complete` — All 5 events present + chained
- `test_learning_loop_e2e_performance` — Complete cycle <5 seconds (1 feedback + optimize)

**Dependencies:** All Stream 2 stories (2-1 through 2-5)

**Story Points:** 8

---

### Acceptance Tests (Stream 2 Summary)

| Test | Scenario | Expected Result |
|---|---|---|
| `test_stream_2_complete_feedback_loop` | Feedback → optimizer → convergence dashboard | Confidence improves, all events logged |
| `test_stream_2_learning_persistence` | 100 feedback events written + reboot | All events recovered from disk |
| `test_stream_2_quality_gate_prevents_noise` | 50% disagreement on same input | Flagged as low-quality, optimizer skips |

### Dependencies & Sequencing (Stream 2)

```
US-2-1 (Collect Feedback)
  ↓
US-2-2 (Optimizer) — needs feedback data
  ├→ US-2-3 (Dashboard) — shows optimizer results
  ├→ US-2-4 (Quality Gate) — improves optimizer quality
  └→ US-2-5 (Persistence) — parallelizable
  
US-2-6 (E2E Integration) — final integration test
```

**Parallel Work:** US-2-5 (persistence) can start in Week 1; US-2-1–4 in Week 1; US-2-6 in Week 2 after US-2-1–5 complete.

### Risk & Assumptions (Stream 2)

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| **Learning loop diverges** | MEDIUM | HIGH | Feedback quality gate, reset capability, manual threshold |
| **Optimizer gets stuck** (contradictory feedback) | MEDIUM | MEDIUM | Quality gate detects, operator warned |
| **Convergence too slow** | LOW | LOW | Seed optimizer with training data (Phase 1.5) |
| **Feedback data loss** | LOW | CRITICAL | EventStore is audit-chained + backed up |

**Assumptions:**
- Operators provide reasonably consistent feedback (quality gate handles 20% noise)
- Hourly optimizer run is sufficient frequency (can tune in Phase 1.5)
- EventStore disk I/O can handle 500 events/day sustained

---

## Stream 3: Cost Insights & Optimization

**Goal:** Operator sees costs, understands spending, sees savings  
**Timeline:** Week 3 (7 days, parallel work)  
**Team:** 1 backend engineer, 0.5 frontend engineer  
**Total Story Points:** 34 (S=5, M=8, L=13)

### User Stories

#### US-3-1: Cost Dashboard (L, 13 points)

**As an** operator  
**I want to** see a real-time dashboard of API costs  
**So that** I understand spending and can optimize

**Acceptance Criteria:**
- AC1: Dashboard loads in <500ms with cached cost data
- AC2: Show total cost (this month): $XXX, trend vs last month (↑ 5%, ↓ 3%, → flat)
- AC3: Show cost breakdown by model (Haiku 4.5, Sonnet 5, Opus 5) as stacked bar
- AC4: Show cost by engine (if multi-worker: ACS, gateway, A2A) as pie chart
- AC5: Forecast burn rate: if current rate continues, month-end cost will be $YYY (±5% confidence)
- AC6: Show daily cost trend (last 30 days, line chart)
- AC7: Data refreshes every 5 minutes (background cron job)

**Acceptance Tests:**
- `test_cost_dashboard_loads` — Panel renders, all 5 sections visible, <500ms
- `test_cost_dashboard_total_cost_accuracy` — Total matches sum of model costs (within 1%)
- `test_cost_dashboard_breakdown_by_model` — Haiku/Sonnet/Opus costs correct ratios
- `test_cost_dashboard_forecast_accuracy` — Forecast within ±5% of actual (measured post-month)
- `test_cost_dashboard_refresh_cycle` — Data updates every 5 minutes

**Dependencies:** Phase 0 model usage tracking (from audit trail + Anthropic API billing)

**Story Points:** 13

---

#### US-3-2: Model Routing Distribution Analysis (M, 8 points)

**As an** operator  
**I want to** see what % of requests go to each model  
**So that** I understand the routing behavior and can validate it

**Acceptance Criteria:**
- AC1: Show distribution: Haiku 45%, Sonnet 30%, Opus 25% (pie chart or stacked bar)
- AC2: Show decisions count per tier: Haiku 1,247 decisions, Sonnet 658, Opus 441
- AC3: Show average confidence per tier: Haiku 0.92, Sonnet 0.87, Opus 0.91
- AC4: Show savings: "If all Opus: would cost $4,200. Actual cost: $2,840. Savings: $1,360 (32%)"
- AC5: Breakdown by request type (if available: skill gen vs autonomy vs feedback)
- AC6: Historical trend (last 7 days, line chart showing % per model over time)

**Acceptance Tests:**
- `test_model_distribution_accuracy` — Ratios match audit trail counts (within 1%)
- `test_model_distribution_confidence_per_tier` — Averages calculated correctly
- `test_model_distribution_savings_calculation` — Savings = (opus_cost_if_all) - (actual_cost), accurate within 1%
- `test_model_distribution_trend` — 7-day trend matches daily snapshots

**Dependencies:** Phase 0 model usage tracking, US-3-1 (cost data)

**Story Points:** 8

---

#### US-3-3: Cost Optimization Recommendations (M, 8 points)

**As an** operator  
**I want to** receive suggestions to reduce costs  
**So that** I can optimize my spending

**Acceptance Criteria:**
- AC1: If Haiku confidence low (<0.70) on >20% of requests, suggest: "Lower Haiku threshold to 0.60 → estimate 10% more Haiku requests, 8% cost savings"
- AC2: If Sonnet unused (<5% routing), suggest: "Disable Sonnet tier → estimate 3% cost savings"
- AC3: If high error rate (>0.5%) correlated with a model, suggest: "Haiku has 2x error rate of Opus, consider higher threshold"
- AC4: If cache hit rate low (<10%), suggest: "Enable context caching → estimate 20% savings"
- AC5: Each recommendation has: action, estimated impact, confidence (%, calculated from data quality)
- AC6: Operator can "Try" recommendation → applies config change temporarily, measures impact

**Acceptance Tests:**
- `test_cost_recommendation_threshold_tuning` — Low confidence detected, threshold change suggested
- `test_cost_recommendation_tier_disable` — Unused tier suggested for disable
- `test_cost_recommendation_error_correlation` — Error rate spike correlated with model
- `test_cost_recommendation_impact_estimation` — Estimated savings within ±10% of actual

**Dependencies:** US-3-1 (cost data), US-3-2 (model distribution)

**Story Points:** 8

---

#### US-3-4: Cost Anomaly Detection & Alerting (S, 5 points)

**As the** system  
**I want to** detect and alert on cost anomalies  
**So that** operator is notified of unexpected spending

**Acceptance Criteria:**
- AC1: Background job (hourly) checks if cost >20% above 7-day rolling average
- AC2: If anomaly detected, emit audit event `cost_anomaly_detected` + send email alert
- AC3: Alert includes: current cost, expected range, delta, likely cause (if detectable: error rate ↑, latency ↑, routing change)
- AC4: "Acknowledge anomaly" button → logs acknowledgment, stops repeating alert
- AC5: Alert threshold tunable (default 20%, configurable per tenant)
- AC6: False positive rate <5% (anomalies >2σ from baseline)

**Acceptance Tests:**
- `test_cost_anomaly_detection_true_positive` — Cost spikes 25%, anomaly detected within 1 hour
- `test_cost_anomaly_detection_false_positive_rate` — 100 normal days, 0–5 false alerts expected
- `test_cost_anomaly_alert_email` — Alert email sent with correct values
- `test_cost_anomaly_acknowledge` — Acknowledgment stops repeat alerts

**Dependencies:** US-3-1 (cost tracking), monitoring infrastructure

**Story Points:** 5

---

### Acceptance Tests (Stream 3 Summary)

| Test | Scenario | Expected Result |
|---|---|---|
| `test_stream_3_cost_dashboard_complete` | View dashboard for full month of data | All sections render, accuracy ±1% |
| `test_stream_3_cost_anomaly_detection` | Cost spikes 30%, system running | Alert fired within 1 hour, email sent |
| `test_stream_3_optimization_recommendations` | Low confidence on Haiku, high error rate | Suggest threshold change + disable Sonnet |

### Dependencies & Sequencing (Stream 3)

```
US-3-1 (Cost Dashboard) — needs billing data from Phase 0
  ├→ US-3-2 (Distribution Analysis) — uses cost dashboard data
  ├→ US-3-3 (Recommendations) — uses distribution + cost data
  └→ US-3-4 (Anomaly Detection) — background service, parallelizable
```

**Parallel Work:** All stories parallelizable after US-3-1 core infrastructure (cost aggregation) done in Week 1.

### Risk & Assumptions (Stream 3)

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| **Cost calculation inaccurate** | MEDIUM | MEDIUM | Validate against Anthropic billing monthly, correction FAQ |
| **Recommendation wrong** | MEDIUM | LOW | A/B test recommendations in Phase 1.5, gather feedback |
| **Anomaly threshold too loose** | LOW | LOW | Tune threshold based on first 2 weeks of data |

**Assumptions:**
- Anthropic API billing data available daily (external dependency)
- Operator has clear cost optimization goals (default: minimize cost)
- False positive rate on anomalies acceptable at 2–5%

---

## Stream 4: Operator Onboarding & Documentation

**Goal:** Users can adopt CorvinOS, understand how it works, use it effectively  
**Timeline:** Weeks 2–4 (14 days, ongoing)  
**Team:** 1 product/docs engineer, 0.5 backend/frontend  
**Total Story Points:** 21 (S=5, M=8, L=13)

### User Stories

#### US-4-1: Getting Started Guide (S, 5 points)

**As a** new operator  
**I want to** follow a step-by-step guide to install and configure CorvinOS  
**So that** I can get the system running on my platform

**Acceptance Criteria:**
- AC1: Guide covers: Linux, macOS, Windows (separate sections)
- AC2: Each section: prereqs, install steps, verify installation, first routing decision
- AC3: Estimated time: <30 min from start to first decision
- AC4: Includes screenshots of console at key steps
- AC5: Links to FAQ for troubleshooting
- AC6: Guide lives in `/docs/getting-started.md` + published on website

**Acceptance Tests:**
- `test_getting_started_guide_linux` — Follow Linux section exactly, system boots successfully
- `test_getting_started_guide_macos` — Follow macOS section, system boots successfully
- `test_getting_started_guide_first_decision` — First routing decision logged + audited
- `test_getting_started_guide_screenshots_current` — All screenshots match latest UI (no stale images)

**Dependencies:** Phase 1 feature complete (all streams)

**Story Points:** 5

---

#### US-4-2: FAQ & Troubleshooting (M, 8 points)

**As a** operator facing an issue  
**I want to** find a clear answer in the FAQ  
**So that** I can resolve it without escalating

**Acceptance Criteria:**
- AC1: FAQ has ≥20 Q&As covering:
  - Installation (Linux/macOS/Windows, troubleshooting)
  - Routing (why routed to X model, confidence scores)
  - Feedback (how to provide, what happens)
  - Costs (why cost changed, how to optimize)
  - Audit (how to interpret events, security questions)
  - Disasters (restore from backup, data loss)
- AC2: Each answer is clear, <200 words, includes links to relevant docs
- AC3: FAQ searchable (by keyword or category)
- AC4: FAQ updated monthly based on support tickets

**Acceptance Tests:**
- `test_faq_completeness` — All major feature areas (routing, feedback, costs) have >3 Q&As
- `test_faq_search_functionality` — Search "restore backup", >2 relevant answers returned
- `test_faq_answer_clarity` — Randomly select 5 answers, verify <200 words + links present

**Dependencies:** Phase 1 feature complete, support feedback

**Story Points:** 8

---

#### US-4-3: Video Tutorials (S, 5 points)

**As a** visual learner  
**I want to** watch short videos showing how to use CorvinOS  
**So that** I understand workflows faster than reading docs

**Acceptance Criteria:**
- AC1: Create 3–5 videos (3–5 min each):
  - Installation walkthrough (all platforms, 5 min)
  - First routing decision + reading audit (3 min)
  - Providing feedback + monitoring convergence (4 min)
  - Reading cost dashboard + optimization (3 min)
- AC2: All videos in English, clear audio, captions
- AC3: Hosted on YouTube (or equivalent) + linked from docs
- AC4: Video production: screencap + voiceover (use high-quality voice, not AI-generated)

**Acceptance Tests:**
- `test_video_installation_completeness` — Video covers all 3 platforms (or separate video per platform)
- `test_video_feedback_accuracy` — Video shows correct UI + correct flow
- `test_video_audio_quality` — Audio clear, captions accurate (spot-check 2 videos)

**Dependencies:** Final UI stable (end of Week 3)

**Story Points:** 5

---

#### US-4-4: Team Training & Certification (M, 8 points)

**As a** team member  
**I want to** be trained on how to operate, debug, and support CorvinOS  
**So that** I can help users and handle incidents

**Acceptance Criteria:**
- AC1: Deliver training in 3 sessions:
  - Morning (1h): Overview — what is CorvinOS, Phase 0/1, architecture, key concepts
  - Hands-on Lab (2h): Install, make routing decision, provide feedback, read dashboard, check audit
  - Q&A Session (1h): Answer team questions, discuss operational concerns
- AC2: Training materials: slides, lab guide, reference cheat sheet (1-pager)
- AC3: Attendance: >90% of team (required for go/no-go decision)
- AC4: Post-training survey: >4/5 satisfaction (to measure clarity)
- AC5: Certification test: each trainee passes 5-question quiz (>80%)

**Acceptance Tests:**
- `test_training_attendance` — Attendance list shows >90%
- `test_training_satisfaction_survey` — Mean score ≥4.0/5
- `test_training_certification_pass_rate` — >90% of trainees pass quiz

**Dependencies:** Phase 1 feature complete, team availability

**Story Points:** 8

---

### Acceptance Tests (Stream 4 Summary)

| Test | Scenario | Expected Result |
|---|---|---|
| `test_stream_4_documentation_complete` | All 4 stories done | Getting Started + FAQ + 3+ videos + training completed |
| `test_stream_4_getting_started_end_to_end` | New operator follows guide | System boots, first decision made, audit logged |
| `test_stream_4_team_trained` | Training session completed | >90% attendance, >4/5 satisfaction, >80% certification pass rate |

### Dependencies & Sequencing (Stream 4)

```
US-4-1 (Getting Started) — can start in Week 2 (features mostly stable)
  ├→ US-4-2 (FAQ) — collect from team, support channels
  ├→ US-4-3 (Videos) — record in Week 3, needs stable UI
  └→ US-4-4 (Training) — deliver Week 4 (after all features done)
```

**Parallel Work:** US-4-1 + US-4-2 start Week 2; US-4-3 in Week 3; US-4-4 in Week 4.

### Risk & Assumptions (Stream 4)

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| **Documentation outdated quickly** | MEDIUM | LOW | Update process (monthly refresh), owner assigned |
| **Training attendance low** | LOW | MEDIUM | Schedule outside peak hours, make mandatory |
| **Video production bottleneck** | LOW | LOW | Use screencap tool, outsource voiceover if needed |

**Assumptions:**
- Team availability for training (4h total, scheduled with 1-week notice)
- Documentation is not locked into console UI (use generic screenshots)
- Video host accessible (YouTube or internal server)

---

## Cross-Stream Dependencies

### Feature Dependency Graph

```
Phase 0 Foundation (stable)
  ├→ Stream 1: Marketplace (install skills)
  ├→ Stream 2: Learning Loop (optimize from feedback)
  │   └→ depends on: Skill execution (from Stream 1)
  ├→ Stream 3: Cost Insights (track spending)
  │   └→ depends on: Model routing (stable from Phase 0)
  └→ Stream 4: Documentation (teach operators)
      └→ depends on: All streams (1–3) feature-complete
```

### Parallel Work Schedule

**Week 1:**
- Stream 1: US-1-1, US-1-6 (API + UI foundation)
- Stream 2: US-2-1, US-2-5 (feedback collection + persistence)
- Stream 3: US-3-1 (cost dashboard backend)
- Stream 4: US-4-2 (FAQ research)

**Week 2:**
- Stream 1: US-1-2 through US-1-5 (install, enable, upgrade)
- Stream 2: US-2-2, US-2-3, US-2-4 (optimizer + dashboard)
- Stream 3: US-3-2, US-3-3, US-3-4 (analysis + recommendations)
- Stream 4: US-4-1, US-4-3 (getting started + videos)

**Week 3:**
- Stream 1: Testing + hardening
- Stream 2: Testing + hardening
- Stream 3: Testing + hardening
- Stream 4: US-4-4 (team training)

**Week 4:**
- All streams: Final integration + go/no-go assessment

---

## Risk & Assumptions

### Global Risks

| Risk | Likelihood | Impact | Mitigation | Owner |
|---|---|---|---|---|
| **Phase 0 regresses** | LOW | CRITICAL | Daily health checks, code review gate, compliance testing | SRE Lead |
| **Marketplace API unstable** | MEDIUM | HIGH | Fallback to local repo, retry logic, timeout handling | Backend Lead |
| **Learning loop diverges** | MEDIUM | HIGH | Feedback quality gate, reset capability, manual override | Tech Lead |
| **Major scope creep** | MEDIUM | MEDIUM | Weekly scope freeze, "no new stories" after Week 2 | Product Lead |
| **Team member unavailable** | MEDIUM | MEDIUM | Cross-train on all 4 streams, no single points of failure | Tech Lead |

### Global Assumptions

1. **Phase 0 foundation stable** — Audit chain, compliance gates, learning infrastructure all green
2. **Marketplace operational** — API stable, 50+ skills available, signatures verified
3. **Operator feedback consistent** — >80% agreement on same routing decisions
4. **Cost data available** — Anthropic API billing accessible, daily refresh
5. **Team availability** — 4.5 FTE, no major leaves, communication cadence met
6. **Skill schema stable** — No major breaking changes during Phase 1

---

## ADR Requirements

### New ADRs for Phase 1

| ADR | Title | Stream | Status | Owner |
|---|---|---|---|---|
| ADR-2030 | Marketplace Discovery & Installation Flow | Stream 1 | PROPOSED | Backend Lead |
| ADR-2031 | Learning Loop Feedback Quality Gate | Stream 2 | PROPOSED | Tech Lead |
| ADR-2032 | Cost Tracking & Anomaly Detection | Stream 3 | PROPOSED | Backend Lead |
| ADR-2033 | Operator Onboarding UX | Stream 4 | PROPOSED | Product Lead |

**Timeline for ADR Completion:** All ADRs written + migrated to Corvin-ADR by end of Week 1.

---

## Acceptance Criteria Summary (All Streams)

### Code Quality
- [ ] All code reviewed (2-reviewer rule)
- [ ] Unit test coverage >80%
- [ ] E2E test coverage >95% (5+ scenarios per story)
- [ ] Adversarial tests: security + isolation + compliance gates
- [ ] Linting: no violations (mypy, pylint, eslint)
- [ ] Performance: p99 latency <500ms (benchmarks: dashboard load, optimizer run, cost calc)

### Testing
- [ ] pytest: all tests passing
- [ ] E2E: 40+ tests, >95% passing
- [ ] Adversarial: 15+ security tests (injection, privilege escalation, data leakage)
- [ ] Compliance: all 6 gates still enforcing, zero gate bypasses
- [ ] Performance: 5 benchmarks (routing, dashboard, feedback, cost, skill install)

### Documentation
- [ ] ADRs migrated to Corvin-ADR (all ADR-0264 compliant)
- [ ] Getting Started guide complete
- [ ] FAQ with ≥20 Q&As
- [ ] 3–5 video tutorials
- [ ] Code comments on complex logic

### Operations
- [ ] Monitoring live (health checks, alerting)
- [ ] Runbook updated (Phase 1 procedures)
- [ ] On-call schedule published
- [ ] Team trained (>90% attendance)
- [ ] Disaster recovery drilled (backup restore <5 min)

---

**END OF PHASE 1 DETAILED SPECIFICATION**

**Owner:** [Tech Lead]  
**Next:** PHASE1_TEAM_STRUCTURE.md (team roles + communication cadence)
