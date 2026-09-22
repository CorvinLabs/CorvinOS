# Phase 1 Roadmap: Feature Launch & Optimization

**Status:** 🟡 **PLANNED** (Phase 0 ready, Phase 1 launch pending)  
**Date:** 2026-09-22  
**Target Launch:** 2026-10-15 (Phase 1 GA)  
**Duration:** 3–4 weeks (feature dev + testing + launch prep)

---

## Executive Summary

Phase 1 is the **first production release** of CorvinOS to end users. It delivers:

1. **Operator Control Plane** (Phase 9) — Intent Router, Plugin Manager, Subsystem Control, Overrides, Snapshots
2. **Intent-Driven Routing** — Classify requests (Skill Gen vs. Autonomy vs. Feedback), route to appropriate engine
3. **Autonomous Skill Deployment** — Deploy Skills via Marketplace, enable/disable at runtime
4. **Learning-Driven Optimization** — Feedback loop optimizes routing + Skills every hour
5. **Production Observability** — Dashboard shows costs, convergence, audit trail

**Success Criteria:**
- ✅ Phase 0 foundation stable (no critical bugs)
- ✅ 1,000+ users onboarded (internal test + beta)
- ✅ Converge routing model (confidence >0.85)
- ✅ <1% error rate, <500ms p99 latency
- ✅ Zero compliance violations (all gates enforced)
- ✅ Full audit trail logged + verified

---

## Phase 1 High-Level Features

### Stream 1: Marketplace Integration (2 weeks)

**Goal:** Users can browse, install, enable Skills from marketplace

**Deliverables:**

1. **Marketplace Discovery UI** (console panel)
   - Browse available Skills by category (routing, context, security, etc.)
   - See skill metadata (description, version, author, compatibility)
   - Install button → downloads + enables skill
   - Uninstall button → disables + removes skill

2. **Skill Installation Flow**
   - Download skill manifest from marketplace (plugin.json)
   - Verify signature + checksum
   - Install to `~/.corvin/skills/installed/`
   - Load skill at next boot (or hot-reload if supported)
   - Emit audit event: `skill_installed`

3. **Dependency Resolution**
   - Detect skill dependencies (e.g., `os.context_adapter` requires `os.delegation_router`)
   - Warn if dependency not installed
   - Offer to auto-install dependencies

4. **Version Management**
   - Display installed version vs. available version
   - Upgrade button → downloads new version, hot-deploys if no config changes
   - Rollback support (keep prior 2 versions on disk)

**Dependencies:**
- [ ] Phase 9 complete (Control Plane)
- [ ] Marketplace API stable (plugin discovery endpoint)
- [ ] Skill manifest schema finalized (version 2.0)

**Timeline:** Weeks 1–2 (14 days, 2 people)

**Acceptance Criteria:**
- [ ] Install skill from marketplace (E2E test)
- [ ] Skill loaded and audited (verify audit event)
- [ ] Dependency resolution working (install dependent skills auto)
- [ ] Version management working (upgrade, rollback)
- [ ] UI responsive (<200ms load)

---

### Stream 2: Learning Loop Activation (2 weeks)

**Goal:** Operators provide feedback, Skills optimize autonomously

**Deliverables:**

1. **Feedback Collection UI** (console panel)
   - Show recent routing decisions
   - "Was this routing correct?" → yes/no/unclear
   - "Rate the Skill output" → 1-5 stars
   - "Preferred this style next time" → dropdown (LLM-generated, deterministic, neither)
   - Submit feedback → emits FeedbackEvent (audited)

2. **Optimizer Loop** (backend, automatic)
   - Read feedback events every hour
   - Update skill config (thresholds, weights)
   - Emit `skill_config_updated` event (audited)
   - Publish new config → all skill instances

3. **Convergence Dashboard** (console panel)
   - Show confidence score per skill (0.0 - 1.0)
   - Trend line (last 7 days)
   - "Converging" (↑↑) vs "Flat" (→) vs "Diverging" (↓↓)
   - Feedback count (how much data we have)
   - Recommended actions (if stuck, suggest what to do)

4. **Feedback Quality Gate**
   - Detect contradictory feedback (user says "yes" for same input twice)
   - Flag noisy feedback (>20% disagreement on same input)
   - Prompt operator to provide more consistent feedback

**Dependencies:**
- [ ] Phase 0 complete (Learning Infrastructure)
- [ ] Skills 2.0 wired (at least delegation_router live)
- [ ] ADR-0314 event schema finalized

**Timeline:** Weeks 1–2 (14 days, 2 people)

**Acceptance Criteria:**
- [ ] Operator submits feedback (E2E test)
- [ ] FeedbackEvent audited (verify audit trail)
- [ ] Optimizer reads feedback + updates config (hourly)
- [ ] Config update audited (verify skill_config_updated event)
- [ ] Convergence dashboard live (shows trending confidence)
- [ ] No silent optimization (all updates visible + audited)

---

### Stream 3: Cost Insights & Optimization (1 week, parallel with streams 1–2)

**Goal:** Operator sees costs, understands where money is spent, sees savings

**Deliverables:**

1. **Cost Dashboard** (console panel)
   - Total cost (this month): $XXX
   - Cost by skill (which skills are most expensive?)
   - Cost by model (Haiku 4.5 vs Sonnet 5 vs Opus 5)
   - Cost by tenant (if multi-tenant setup)
   - Forecast (burn rate, projected month-end cost)

2. **Model Routing Analysis**
   - Show distribution: % routed to Haiku vs Sonnet vs Opus
   - Show savings vs baseline (if all Opus: would cost $YYY)
   - Show confidence per tier (routing decisions are 87% correct)

3. **Cost Optimization Recommendations**
   - "Try tuning complexity threshold to 0.65" → estimate savings
   - "Enable caching for context adapter" → estimate latency/cost tradeoff
   - "Disable os.context_adapter" → estimate cost savings

4. **Cost Anomaly Detection**
   - Alert if cost >20% above rolling 7-day average
   - Alert if error rate increases (suggests wasted API calls)
   - Alert if latency increases (suggests model bottleneck)

**Dependencies:**
- [ ] Phase 0 complete (audit trail + model usage tracking)
- [ ] Models console working (knows which model per request)

**Timeline:** Week 3 (7 days, 1 person, parallel work)

**Acceptance Criteria:**
- [ ] Cost dashboard loads (<500ms)
- [ ] Costs accurate vs API billing (within 1%)
- [ ] Model distribution shown correctly
- [ ] Savings forecast accurate (within 5%)
- [ ] Anomaly detection working (5 tests with synthetic data)

---

### Stream 4: Operator Onboarding & Documentation (ongoing, 1–2 weeks)

**Goal:** Users can adopt CorvinOS, understand how it works, use it effectively

**Deliverables:**

1. **Getting Started Guide** (markdown)
   - Installation instructions (all platforms)
   - First routing decision (hello world)
   - How to read audit trail
   - How to interpret confidence scores
   - How to provide feedback

2. **FAQ & Troubleshooting** (markdown)
   - "Why was this routed to Haiku when I expected Sonnet?"
   - "How do I disable a Skill?"
   - "What does this audit event mean?"
   - "How do I restore from backup?"

3. **Video Tutorials** (3–5 minutes each)
   - Installation walkthrough
   - First feedback submission
   - Reading cost dashboard
   - Understanding convergence

4. **Team Training** (internal)
   - Morning: 1h overview (what is CorvinOS, what's Phase 0, what's Phase 1)
   - Hands-on: 2h lab (install, route, feedback, read dashboard)
   - Q&A: 1h (team questions, operational concerns)

**Dependencies:**
- [ ] Phase 0 + Phase 1 features complete
- [ ] Console UI stable (no breaking changes)

**Timeline:** Weeks 2–4 (ongoing, 0.5 person)

**Acceptance Criteria:**
- [ ] Getting Started guide complete + reviewed
- [ ] FAQ has >15 answered questions
- [ ] 3–5 video tutorials uploaded + tested
- [ ] Team trained (>90% attendance, >4/5 satisfaction)

---

## Dependencies on Phase 0 (Gating Criteria)

Phase 1 cannot launch until Phase 0 is stable. **Go/No-Go decision required:**

| Component | Phase 0 Status | Must Work | Verify By |
|---|---|---|---|
| **Audit Chain** | ✅ LIVE | Chain verified daily, zero data loss | Backup restore test passes |
| **Compliance Gates** | ✅ LIVE | All 6 gates active + fail-closed | Health check shows ✅ all gates |
| **Learning Infrastructure** | ✅ LIVE | Events persist + optimizer runs | 24h test: >100 events, config updated |
| **Skills 2.0** | 🟡 WIRED (shadow mode) | delegation_router audited + learning feedback works | E2E: route request, get feedback, verify audit |
| **Plugin System** | ✅ LIVE | All 5 boot layers load, zero crashes | Status check shows all layers ✅ |
| **Multi-Tenant Isolation** | ✅ LIVE | Zero cross-tenant data leaks | Isolation verification test passes |
| **Monitoring & Alerting** | ✅ LIVE | Daily audits, readiness probes, health checks | Morning checklist all green |

**Gating Rule:** Phase 1 launch blocked until ALL components show ✅.

**Override Authority:** Manager + SRE lead can approve launch with ONE ⚠️ (yellow status) if:
1. Issue is non-blocking (workaround exists)
2. Fix is planned for Phase 1 or 1.5
3. Risk is accepted in writing (document in PHASE0_READY_REPORT.md)

---

## Risk Assessment

### Critical Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| **Learning loop diverges** (feedback is contradictory, optimizer gets stuck) | MEDIUM | HIGH (routing decisions get worse) | Feedback quality gate, manual intervention capability, reset optimizer to v0 |
| **Marketplace skill broken** (user installs skill, system crashes) | MEDIUM | HIGH (operator must uninstall, service interruption) | Skill signature verification, dependency check, rollback support |
| **Compliance gate regression** (new code weakens a gate) | LOW | CRITICAL (legal liability, GDPR violation) | Code review checklist, pre-commit testing, CI/CD gate validation |
| **Tenant data leak during optimizer update** (config update touches wrong tenant) | LOW | CRITICAL (data privacy violation, legal liability) | Unit tests, tenant isolation audit, optimizer reads tenant_id from event |

### High Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| **High latency spike** (optimizer update causes pause, p99 > 1s) | MEDIUM | MEDIUM (poor UX, high cost perception) | Optimizer runs async + off-peak, latency monitoring + alerting |
| **Cost explosion** (bug causes route to always use Opus) | LOW | MEDIUM (unexpected bills, operator unhappy) | Cost anomaly detection, model routing validation in tests |
| **User confusion** (feedback UI not clear, operator provides bad feedback) | MEDIUM | MEDIUM (poor learning, low satisfaction) | A/B test UI, training, feedback quality gate |

### Medium Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| **Marketplace unavailable** (download fails, user can't install skill) | LOW | LOW (user can install later, system still works) | Fallback to local skills, retry logic, error messages |
| **Performance regression** (new dashboard queries are slow) | MEDIUM | LOW (can optimize in Phase 1.5) | Performance testing on console panels, caching strategy |
| **Bug in cost calculation** (numbers don't match billing) | MEDIUM | LOW (corrected in Phase 1.5, user sees discrepancy) | Unit tests, spot-check vs actual billing, correction FAQ |

---

## Resource Allocation

**Team size:** 4–6 people (depends on availability)  
**Duration:** 3–4 weeks (2 weeks feature dev + 1 week testing + 1 week launch prep)

| Role | People | Effort | Tasks |
|---|---|---|---|
| **Backend Engineer** | 2 | 10–12 weeks | Marketplace integration, Learning loop, Cost dashboard, Skill deployment |
| **Frontend Engineer** | 1.5 | 10–12 weeks | UI panels (marketplace, feedback, convergence, costs) |
| **QA/Testing** | 1 | 6–8 weeks | E2E tests, adversarial testing, performance testing, stress testing |
| **SRE/Ops** | 0.5 | 3–4 weeks | Monitoring setup, runbook updates, on-call training |
| **Product/Docs** | 0.5 | 2–3 weeks | User docs, FAQ, video tutorials, team training |
| **Tech Lead** | 0.5 | 4–6 weeks | Architecture review, risk assessment, go/no-go decision |

**Total:** ~4.5 people × 4 weeks = ~18 person-weeks (well-staffed)

---

## Launch Checklist (Week 4)

Before Phase 1 GA launch, verify:

### Code Quality (week 3)
- [ ] All code reviewed (2-reviewer rule)
- [ ] All tests passing (unit + E2E + adversarial)
- [ ] Performance benchmarks met (p99 <500ms, error rate <0.1%)
- [ ] Security review passed (OWASP top 10, auth/authz, secrets)
- [ ] Compliance gates still working (all 6 active)

### Documentation (week 3–4)
- [ ] Getting Started guide published + reviewed
- [ ] FAQ complete (>15 Q&As)
- [ ] Video tutorials uploaded + tested (3–5 videos)
- [ ] Runbook updated (Phase 1 procedures added)
- [ ] ADRs migrated to Corvin-ADR (all Phase 1 ADRs)

### Operability (week 4)
- [ ] Monitoring live (health checks, alerting)
- [ ] On-call schedule published
- [ ] Team trained (>90% attendance, >4/5 satisfaction)
- [ ] Escalation procedures tested (incident drill)
- [ ] Backup restore tested (can recover in <5 min)

### User Readiness (week 4)
- [ ] Beta testers onboarded (50–100 internal users)
- [ ] Feedback collected + addressed (top 3 issues fixed)
- [ ] FAQ updated based on feedback
- [ ] Known limitations documented (what's not in Phase 1)
- [ ] Release notes drafted (features, known issues, upgrade path)

### Go/No-Go Decision (Friday week 4)
- [ ] Tech Lead: all criteria met? → ✅ Go or ❌ No-Go
- [ ] If No-Go: document blocker + replan for Phase 1.1
- [ ] If Go: proceed to launch (Monday week 5)

---

## Launch Plan (Monday Week 5)

### Pre-Launch (Sunday 23:00 UTC)
- [ ] All systems verified (health checks, backups, monitoring)
- [ ] Status page updated → "Maintenance window 00:00-02:00 UTC"
- [ ] Team on standby (on-call active)

### Launch Window (Monday 00:00–02:00 UTC)
- [ ] Push Phase 1 release to main (final commit)
- [ ] Deploy to staging (verify everything boots)
- [ ] Run smoke tests (5 critical flows)
- [ ] Rollback plan ready (can restore from backup in <5 min)
- [ ] Switch traffic to Phase 1 (gradual: 10% → 25% → 50% → 100%)

### Post-Launch (Monday 02:00–12:00 UTC)
- [ ] Monitor health checks every 5 minutes
- [ ] Check cost tracking (costs should be normal)
- [ ] Monitor error rate (<0.1% expected)
- [ ] Monitor latency (p99 <500ms expected)
- [ ] Monitor learning loop (events being captured)

### Launch Verification (Monday 12:00 UTC)
- [ ] All metrics green?
- [ ] No critical bugs?
- [ ] Users successfully onboarded?
- [ ] If all yes: **LAUNCH SUCCESSFUL** 🎉
- [ ] If no: Initiate rollback (restore from pre-launch backup)

---

## Success Metrics

### Week 1–2 (Feature Delivery)
- [ ] Code delivered on time (no slippage >3 days)
- [ ] Tests passing (>90% coverage)
- [ ] Code review feedback <5 days round-trip

### Week 3 (Integration & Testing)
- [ ] E2E tests: >95% passing
- [ ] Adversarial tests: zero critical vulns
- [ ] Performance test: p99 <500ms
- [ ] Compliance test: all gates enforcing

### Week 4 (Launch Prep)
- [ ] Documentation complete + reviewed
- [ ] Team trained + ready
- [ ] Beta testers satisfied (>4/5 rating)
- [ ] Go/No-Go decision: ✅ Go

### Post-Launch (Production)
- [ ] Error rate <0.1% (sustained >24h)
- [ ] Latency p99 <500ms (sustained >24h)
- [ ] Zero compliance violations (audited)
- [ ] Learning loop converging (confidence trending ↑)
- [ ] User satisfaction >4/5 (survey)

---

## Known Limitations (Not in Phase 1)

These features are planned for Phase 1.5 or later:

1. **Skill Dependency Upgrade** — Automatically upgrade dependencies when new version released
2. **A/B Testing Skills** — Run two versions concurrently, measure which is better
3. **Cost Attribution** — Detailed cost breakdown per Skill per request
4. **Advanced Analytics** — Cohort analysis, retention curves, skill adoption trends
5. **Custom Skill Development** — SDK for writing custom Skills (Phase 2)
6. **Multi-Region Deployment** — Failover to standby in different region
7. **Compliance Audit Export** — GDPR Art. 17 erasure + audit trail export

---

## Transition to Phase 1.5 (Post-Launch)

Once Phase 1 is stable (>7 days without critical issues), we begin Phase 1.5:

1. **Skill Dependency Upgrade** — Auto-update related skills
2. **Convergence Acceleration** — Provide training data to optimizer to speed convergence
3. **Cost Optimization** — Auto-suggest skill disables if ROI is negative
4. **Advanced Observability** — Detailed traces, flame graphs, cost per request

---

## Sign-Off

**Phase 1 Roadmap Owner:** [To be assigned]  
**Tech Lead:** [To be assigned]  
**Product Manager:** [To be assigned]  
**SRE Lead:** [To be assigned]  

**Sign-off:** [After Phase 0 Ready Report approved]

---

**Next:** PHASE0_READY_REPORT.md (go/no-go decision for Phase 1 launch)
