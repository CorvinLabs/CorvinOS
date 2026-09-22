# Phase 1 Team Structure & Communication Plan

**Status:** 🟢 **READY FOR TEAM ASSEMBLY**  
**Date:** 2026-09-22  
**Target Kickoff:** Monday 2026-09-25, 09:00 UTC  
**Duration:** 4 weeks (Sep 25 – Oct 22)  
**Team Lead:** [To be assigned]

---

## Table of Contents

1. [Team Roles & Responsibilities](#team-roles--responsibilities)
2. [RACI Matrix](#raci-matrix)
3. [Communication Cadence](#communication-cadence)
4. [Decision Authority & Escalation](#decision-authority--escalation)
5. [Cross-Team Dependencies](#cross-team-dependencies)
6. [Knowledge Sharing & Onboarding](#knowledge-sharing--onboarding)

---

## Team Roles & Responsibilities

### Core Team (5.5 FTE)

#### **Tech Lead** (0.5 FTE, shared with Phase 0 support)

**Role:** Architect, decision authority, risk overseer

**Responsibilities:**
- Architecture review (weekly, Monday 10:00 UTC)
- Technical debt assessment + go/no-go decision
- Code review spot-checks (compliance gates, critical paths)
- Risk assessment + mitigation planning
- ADR migration oversight (all Phase 1 ADRs to Corvin-ADR)
- Go/No-Go decision authority (Friday Week 4)

**Time Commitment:**
- Kickoff: Monday 4h
- Weekly architecture reviews: 1h (Mondays)
- Go/No-Go assessment: 4h (Friday Week 4)
- On-demand consulting: 2–3h/week (standups + escalations)
- **Total: ~15h over 4 weeks**

**Success Criteria:**
- [ ] Zero compliance gate regressions
- [ ] All ADRs migrated to Corvin-ADR by end of Week 1
- [ ] Risk assessment updated weekly (new risks identified early)
- [ ] Go/No-Go decision signed by Friday Week 4

**Reports to:** Manager + SRE Lead

---

#### **Backend Engineer #1** (1 FTE, Stream 1 lead)

**Role:** Marketplace API, Skill installation, dependency resolution

**Responsibilities:**
- Design + implement marketplace API (GET /v1/console/marketplace/*)
- Skill download + signature verification
- Skill dependency resolver (topological sort)
- Skill version management (upgrade + rollback)
- E2E tests (40+ tests for Stream 1)
- Code review for other backend (pair reviews on critical code)

**Time Commitment:**
- Full-time (40h/week × 4 weeks)
- **Total: 160h**

**Deliverables:**
- [ ] Marketplace API: 6 endpoints, all E2E tested
- [ ] Skill installation flow: <30s per skill, all audited
- [ ] Dependency resolver: topological sort + cycle detection
- [ ] Code: 800–1000 LoC, >85% test coverage
- [ ] Tests: 20+ E2E tests, all green
- [ ] ADR-2030 written + migrated

**Success Criteria:**
- [ ] All Stream 1 user stories DONE by end of Week 2
- [ ] Code review feedback addressed in <2 days
- [ ] Performance: skill install <30s, API response <500ms
- [ ] Zero critical bugs post-launch (Week 3+)

**Reports to:** Tech Lead

---

#### **Backend Engineer #2** (1 FTE, Stream 2 lead)

**Role:** Learning loop optimizer, feedback quality gate, persistence

**Responsibilities:**
- Feedback event collection + persistence
- Optimizer hourly cron job (config updates)
- Feedback quality gate (contradiction + noise detection)
- Integration with EventStore (from Phase 0)
- E2E tests (30+ tests for Stream 2)
- Code review for other backend (pair reviews on critical code)

**Time Commitment:**
- Full-time (40h/week × 4 weeks)
- **Total: 160h**

**Deliverables:**
- [ ] Feedback collection: API + UI backend
- [ ] Optimizer loop: hourly cron, config updates, audit events
- [ ] Quality gate: contradiction + noise detection
- [ ] Code: 700–900 LoC, >85% test coverage
- [ ] Tests: 18+ E2E tests, all green
- [ ] ADR-2031 written + migrated

**Success Criteria:**
- [ ] Stream 2 stories DONE by end of Week 2
- [ ] Optimizer runs hourly without errors
- [ ] Feedback quality gate prevents >80% of noisy feedback
- [ ] Zero data loss (all feedback persisted + chained)

**Reports to:** Tech Lead

---

#### **Frontend Engineer** (1.5 FTE, lead for all UI)

**Role:** Console panels (marketplace, feedback, convergence, cost)

**Responsibilities:**
- Marketplace discovery UI (browse, filter, search)
- Skill management UI (install, enable/disable, version mgmt)
- Feedback collection panel (submit feedback, comments)
- Convergence dashboard (confidence trend, recommendations)
- Cost dashboard (breakdown by model, forecast, recommendations)
- Component tests (19+ component tests, Vitest or Jest)
- Responsive design (all panels work on mobile + desktop)
- Accessibility (WCAG 2.1 AA compliance)

**Time Commitment:**
- 1.5 FTE (60h/week × 4 weeks)
- **Total: 240h**

**Deliverables:**
- [ ] 4 console panels (4 React components, ~2500 LoC)
- [ ] Component tests: 19+ tests, all green
- [ ] E2E integration tests: 5+ tests (via Playwright)
- [ ] Responsive: tested on mobile + desktop (screenshot tests)
- [ ] Accessibility: WCAG 2.1 AA score >90
- [ ] Performance: each panel <200ms load, <5s total console load

**Success Criteria:**
- [ ] All Stream 1, 2, 3 UI stories DONE by end of Week 2
- [ ] Zero missing buttons/fields
- [ ] <5 usability issues (A/B testing in Phase 1.5)
- [ ] Mobile-friendly (<500ms load on 4G)

**Reports to:** Tech Lead

---

#### **QA / Testing Engineer** (1 FTE)

**Role:** E2E testing, adversarial testing, performance testing, compliance validation

**Responsibilities:**
- Write + execute E2E tests (40+ test scenarios)
- Adversarial testing: security (injection, privilege escalation), isolation (tenant boundary), compliance (gate enforcement)
- Performance testing: 5 benchmark scenarios (latency, throughput, error rate)
- Compliance testing: verify all 6 gates still enforcing
- Test environment setup (staging, test data)
- Bug triage + test result reporting
- Performance regression detection

**Time Commitment:**
- Full-time (40h/week × 4 weeks)
- **Total: 160h**

**Deliverables:**
- [ ] E2E test suite: 40+ tests, >95% passing
- [ ] Adversarial test suite: 15+ scenarios (security + isolation + compliance)
- [ ] Performance benchmarks: 5 scenarios, baseline established
- [ ] Compliance test report: all 6 gates verified
- [ ] Bug report template + triage process
- [ ] Test execution dashboard (pass rates, latency, coverage)

**Success Criteria:**
- [ ] >95% of E2E tests passing by end of Week 3
- [ ] Zero critical bugs post-launch
- [ ] All 6 compliance gates still enforcing (zero gate bypasses)
- [ ] Performance regression <5% (p99 latency stays <500ms)

**Reports to:** Tech Lead + SRE Lead

---

#### **SRE / Operations Engineer** (0.5 FTE, shared with Phase 0)

**Role:** Monitoring, alerting, runbook, on-call readiness, disaster recovery

**Responsibilities:**
- Set up monitoring for Phase 1 (cost tracking, optimizer health, skill loads)
- Configure alerting (cost anomalies, errors, latency spikes)
- Update runbook (Phase 1 operations procedures)
- On-call schedule creation + escalation chain
- Disaster recovery drills (backup restore, rollback procedures)
- Team training on operational procedures
- Post-launch support (24h monitoring)

**Time Commitment:**
- 0.5 FTE (20h/week × 4 weeks)
- **Total: 80h**

**Deliverables:**
- [ ] Monitoring dashboard: cost, optimizer, skills, compliance gates
- [ ] Alerting rules: 5+ scenarios (cost spike, errors, latency)
- [ ] Runbook: Phase 1 operations (7 sections, <20 pages)
- [ ] On-call schedule: published, escalation chain clear
- [ ] DR drill report: restore time <5 min, all systems recovered
- [ ] Team training: delivered Week 4 (1h) with >90% attendance

**Success Criteria:**
- [ ] Monitoring live by start of Week 3
- [ ] All alerts configured + tested
- [ ] Runbook reviewed by team (>4/5 clarity rating)
- [ ] DR drill passed with flying colors

**Reports to:** Manager + Tech Lead

---

#### **Product Manager / Docs** (0.5 FTE)

**Role:** User documentation, FAQ, release management, user feedback loop

**Responsibilities:**
- Create getting started guide (all platforms)
- Build FAQ (≥20 Q&As)
- Record + edit video tutorials (3–5 videos)
- Gather user feedback (beta testing, early adopter interviews)
- Release notes + communications
- User success metrics tracking
- Scope management (feature freeze, defer non-essential)

**Time Commitment:**
- 0.5 FTE (20h/week × 4 weeks)
- **Total: 80h**

**Deliverables:**
- [ ] Getting started guide: <30 min end-to-end (all platforms)
- [ ] FAQ: ≥20 Q&As, searchable
- [ ] Video tutorials: 3–5 × 3–5 min each, captions
- [ ] Release notes: features, known issues, upgrade path
- [ ] User feedback summary: top 3 issues, addressed in Phase 1 or deferred
- [ ] Success metrics: users onboarded, satisfaction >4/5

**Success Criteria:**
- [ ] Getting started guide allows new operator to boot in <30 min
- [ ] FAQ covers >80% of common questions (measured from support tickets)
- [ ] Videos are clear + accurate (spot-check captions)
- [ ] Beta users report >4/5 satisfaction

**Reports to:** Manager + Tech Lead

---

### Optional Roles (if available)

#### **Security Engineer** (0.25 FTE, part-time review)

**Role:** Security review + compliance validation

**Responsibilities:**
- Code security review (OWASP top 10, auth/authz)
- Compliance gate validation (all 6 gates enforcing)
- Penetration testing (marketplace, skill installation)
- Secret rotation procedures
- Audit trail verification

**Deliverables:**
- [ ] Security review report: zero critical, <5 high findings
- [ ] Compliance gate test report: all 6 gates ✅
- [ ] Penetration test report: no exploitable vulns
- [ ] ADR-2034 (Security Considerations for Phase 1, if issues found)

---

## RACI Matrix

**R**esponsible (does the work) | **A**ccountable (final say) | **C**onsulted (input) | **I**nformed (FYI)

| Activity | Tech Lead | Backend #1 | Backend #2 | Frontend | QA | SRE | Product |
|----------|-----------|-----------|-----------|----------|-----|-----|---------|
| **Architecture design** | A | R | R | C | — | C | — |
| **Marketplace API spec** | C | **R/A** | — | C | — | — | — |
| **Optimizer design** | C | — | **R/A** | — | C | — | — |
| **Dashboard/UI design** | C | — | — | **R/A** | — | — | C |
| **Code review approval** | C | **A** (peer code) | **A** (peer code) | **A** | I | — | — |
| **E2E test design** | — | R | R | R | **A** | C | — |
| **Performance benchmarks** | — | C | C | — | **R/A** | C | — |
| **Compliance gate validation** | A | C | C | — | **R** | C | — |
| **Monitoring setup** | C | — | — | — | — | **R/A** | — |
| **Go/No-Go decision** | **A** | C | C | C | C | **A** | **A** |
| **Release management** | A | — | — | — | — | A | **R** |
| **User documentation** | — | — | — | C | — | — | **R/A** |
| **Beta feedback collection** | — | — | — | C | — | — | **R/A** |

---

## Communication Cadence

### Daily Standup (09:00 UTC, 15 min, core 4)

**Attendees:** Backend #1, Backend #2, Frontend, QA (core contributors)  
**Optional:** Tech Lead (if available)

**Format:** Async-first (Slack thread), 10 min max per person

- 💬 Yesterday: what I did (1 line)
- 🎯 Today: what I'm doing (1 line)
- 🚧 Blockers: anything stopping me? (mention @tech-lead if critical)

**Example:**
```
Backend #1: Reviewed Marketplace API design, started implementation. 
  Today: implement GET /v1/console/marketplace/index endpoint.
  Blocker: need confirmation on pagination schema (asked #phase1-design)
```

**Decision:** Standup facilitator = Tech Lead (rotates weekly if unavailable)

---

### Twice-Weekly Syncs (Tuesday & Friday, 14:00 UTC, 30 min, full team)

**Attendees:** All team members (required), Manager (optional), Customer rep (Friday only)

**Tuesday Sync (Progress Review):**
1. Stream status (each lead: 1 slide, 2 min per stream = 8 min total)
2. Blockers + escalations (5 min)
3. Risk update (3 min)
4. Next week preview (4 min)

**Friday Sync (Weekly Review + Planning):**
1. Week recap (progress vs plan: on-track? ahead? behind?)
2. Metrics (code coverage, test pass rate, lines of code delivered)
3. Demo (15 min) — show working features to customer rep + team
4. Next week planning (capacity check, dependency review)
5. Go/No-Go assessment (Week 4 only)

**Agenda Template:**
```
## Phase 1 Sync — [Date]

### Stream Status (per lead)
- **Stream 1 (Marketplace):** [US-1-1, US-1-2 DONE; US-1-3 in progress] ✅
- **Stream 2 (Learning):** [US-2-1 DONE; US-2-2 blocked on EventStore API] ⚠️
- **Stream 3 (Cost):** [on schedule] ✅
- **Stream 4 (Docs):** [FAQ 12/20 Q&As] 🟡

### Blockers & Escalations
- EventStore API unclear (Backend #2) — needs Tech Lead input by EOD Tuesday
- Marketplace cert not available (Backend #1) — waiting on external team

### Risk Update
- Learning loop divergence: still MEDIUM likelihood, feedback quality gate mitigates
- Scope creep: 2 new feature requests from beta, all deferred to Phase 1.5

### Demo
- Marketplace discovery panel (search, filter, install button)
- Feedback collection form (submit feedback, audit verification)
- Cost dashboard (breakdown by model, forecast)
```

**Decision:** Tech Lead facilitates + time-keeps

---

### Weekly Architecture Review (Monday, 10:00 UTC, 1 hour, tech core)

**Attendees:** Tech Lead, Backend #1, Backend #2, Frontend, QA

**Purpose:** Review critical design decisions, spot blockers early, prevent rework

**Agenda:**
1. Last week's decisions: any issues? (5 min)
2. This week's design reviews: (50 min)
   - Stream 1: Marketplace API design (Week 1), skill upgrading (Week 2)
   - Stream 2: Optimizer algorithm + quality gate (Week 1), feedback persistence (Week 1–2)
   - Stream 3: Cost calculation accuracy + anomaly detection (Week 3)
   - Stream 4: Documentation structure (Week 2)
3. Design debt: any shortcuts we need to revisit? (5 min)

**Preparation:** Each lead submits design doc (1 page) by Sunday 20:00 UTC

**Decision:** Tech Lead approves designs (or requests changes before implementation)

---

### Weekly Code Review Check-in (Friday after sync, 30 min optional)

**Attendees:** Tech Lead, Backend #1, Backend #2, Frontend, QA (optional)

**Purpose:** Spot-check code quality, ensure review SLA met, share patterns

- Code review backlog: any reviews >2 days old? (escalate to author + reviewer)
- Code quality trends: coverage ↑? linting ✅? performance ✅?
- Shared learnings: patterns observed, gotchas avoided

**Decision:** Facilitator = Tech Lead

---

### Escalation Protocol

**Severity Levels:**
- 🔴 **Critical (P0):** Blocks launch, security issue, data loss risk
- 🟠 **High (P1):** Blocks one stream, significant technical debt
- 🟡 **Medium (P2):** Slows one team, nice-to-have optimization

**Escalation Path:**

```
Team member → Tech Lead (same day)
    ↓ (if not resolved in 2h)
Tech Lead → Manager + SRE Lead (same day)
    ↓ (if not resolved by EOD)
Manager + SRE Lead → Steering Committee (next morning)
    ↓ (if not resolved in 8h)
Steering Committee → CTO + VP Eng (emergency decision)
```

**Examples:**
- 🔴 Phase 0 compliance gate failed → escalate to SRE Lead immediately
- 🟠 Marketplace API schema unclear → Tech Lead decides within 2h
- 🟡 Frontend design refactor → discuss in next Wednesday sync

---

## Decision Authority & Escalation

### Authority Matrix

| Decision | Owner | Timeline | Escalation |
|----------|-------|----------|-----------|
| **Technical design** (API spec, algorithm) | Tech Lead | 2 days | Manager if blocked |
| **Code review approval** (merge to main) | 2 reviewers (peer code) | 2 days | Tech Lead (tie-breaker) |
| **Scope change** (new story, defer story) | Product Lead | 1 day | Manager if >4h impact |
| **Performance/quality trade-off** | Tech Lead + Frontend | 1 day | Manager if contentious |
| **Emergency production issue** | SRE Lead | immediate | Manager (next day debrief) |
| **Go/No-Go decision** | Tech Lead + SRE Lead + Product Lead | Friday 15:00 UTC Week 4 | Manager (final sign-off if split) |

### Escalation Procedure (Step-by-Step)

**Step 1: Raise blocker in standup** (async or daily sync)
- Mention @tech-lead + affected parties
- Expected resolution: same day (if P1/P0)

**Step 2: Unresolved after 2h → Tech Lead makes decision**
- Option A: Approve the proposed solution
- Option B: Request alternative + set 2h deadline
- Option C: Escalate to Manager

**Step 3: Unresolved by EOD → Manager involved**
- Manager + Tech Lead discuss trade-offs
- Decision: go forward / pivot / delay

**Example:**
```
Backend #1 (10:00 UTC): "Marketplace API schema unclear. Need 
  decision on pagination: limit+offset vs cursor?"
Tech Lead (10:15 UTC): "Use cursor. Here's why [link]. 
  Implement by EOD. Questions in #design."
Backend #1 (15:00 UTC): "Done. PR #2840 ready for review."
```

---

## Cross-Team Dependencies

### Stream-to-Stream Dependencies

```
Phase 0 Foundation (prerequisite for all)
  ├─ Stream 1 (Marketplace)
  │  └─> needed by: Stream 2 (feedback on skills)
  │
  ├─ Stream 2 (Learning Loop)
  │  └─> needed by: Stream 3 (cost optimization suggestions)
  │
  ├─ Stream 3 (Cost Insights)
  │  └─> needed by: Stream 4 (documentation, FAQ on costs)
  │
  └─ Stream 4 (Documentation)
     └─> helps: all streams (user adoption)
```

### External Dependencies

| Dependency | Owner | Risk | Mitigation |
|-----------|-------|------|-----------|
| **Marketplace API stability** | Marketplace team | MEDIUM | Fallback to local skill repo, retry logic |
| **Anthropic API billing data** | Anthropic + infra team | LOW | Cache data, weekly reconciliation |
| **EventStore (from Phase 0)** | Phase 0 owner | LOW | Already tested + verified stable |
| **Certification for skill signatures** | Security team | LOW | Provide cert by Week 1 Friday |

### Hand-offs Between Streams

**Week 1 Friday:** Backend #1 delivers Marketplace API → Backend #2 uses to install test skill  
**Week 2 Monday:** Backend #2 delivers Optimizer + EventStore integration → Frontend builds convergence dashboard  
**Week 2 Friday:** Backend #1 + #2 deliver complete skill lifecycle → Frontend builds Marketplace + Feedback UI  
**Week 3 Monday:** All backend complete → QA runs full E2E test suite  

---

## Knowledge Sharing & Onboarding

### First Week (Kickoff + Ramp-up)

**Monday Sep 25 (Kickoff):** 4-hour all-hands session
- 09:00–09:30: Welcome + goals (Product Lead)
- 09:30–10:15: Architecture deep-dive (Tech Lead)
- 10:15–11:00: Team roles + RACI (Tech Lead)
- 11:00–12:00: Q&A + risk discussion (all)

**Monday afternoon:** Pairing starts
- Backend #1 pairs with Backend #2 (learn Phase 0 codebase)
- Frontend pairs with QA (learn testing patterns)
- Each pair: 3–4 hours, code walk-through

**Tuesday–Friday:** Normal standups + architecture reviews begin

### Knowledge Artifacts

| Artifact | Owner | Location | Update Frequency |
|----------|-------|----------|------------------|
| **Architecture diagram** | Tech Lead | `/docs/phase1-architecture.md` | Weekly |
| **API spec** (OpenAPI) | Backend #1 | `/core/marketplace/openapi.yaml` | As code changes |
| **Data model** (Pydantic) | Backend #2 | `/core/learning/schema.py` | As schema changes |
| **Component library** | Frontend | Storybook (if used) | Weekly |
| **Test fixtures** | QA | `/tests/fixtures/` | As stories change |
| **Runbook** | SRE | `/docs/runbook.md` | Weekly (final by Week 4) |

### Pair Programming Schedule

**Week 1 (Tuesday–Friday):**
- Backend #1 + Backend #2: 2h/day (learn shared patterns)
- Frontend + QA: 2h/day (learn testing)
- Tech Lead + one backend: 1h/day (architecture Q&A)

**Week 2:**
- Pairs reduce to 4h/week (asynchronous code reviews take over)

**Week 3:**
- Pairs only as needed (P1 bugs, blockers)

### Code Review Cadence

**Target SLA:** 24 hours (code review feedback within 1 business day)

**Rules:**
- Minimum 2 reviewers (one from same stream, one from different)
- No auto-approvals (all PRs require human review)
- Code review comment: resolved + dismissed in same thread
- PRs merged only after: 2 approvals + CI passing + no conflicts

**Code Review Checklist (template):**
```
- [ ] Code follows style guide + patterns (pylint, mypy, eslint)
- [ ] Tests added/updated (>80% coverage maintained)
- [ ] No security issues (auth, secrets, injection)
- [ ] No compliance gate regression (audit trail, consent)
- [ ] Performance impact acceptable (<5% latency regression)
- [ ] Comments explain intent (non-obvious logic)
- [ ] No hardcoded values (use config/env vars)
- [ ] Backwards compatible (no breaking schema changes without migration)
```

---

## Handoff & Transition

### End of Phase 1 (Week 4 Friday)

**Go/No-Go Assessment (15:00 UTC):**
- Tech Lead: feature complete? tests passing? → ✅ or ❌
- SRE Lead: monitoring ready? team trained? → ✅ or ❌
- Product Lead: docs done? users ready? → ✅ or ❌

**If ✅ GO:** Launch window opens Sunday 23:00 UTC  
**If ❌ NO-GO:** Blocker documented, team pivots to Phase 1.1 (1-week fix sprint)

### Post-Launch Support (Week 5)

**Day 1 (Monday):** Launch window (00:00–02:00 UTC)
- All team on standby (no day-off), SRE + one backend + frontend online
- Run smoke tests, monitor health checks every 5 min
- Rollback plan ready (can restore in <5 min)

**Days 2–5 (Tue–Fri):** Post-launch monitoring
- Reduced team (1 backend, 1 frontend on-call)
- Daily check-in (05:00 UTC) + evening check-in (20:00 UTC)
- Bug fixes (P0/P1 only, Phase 1.1 for P2+)

**Day 7 onwards:** Normal operations
- On-call rotation established
- Phase 1.1 sprint begins (smaller fixes, Phase 1.5 features)

---

**END OF PHASE 1 TEAM STRUCTURE**

**Team Lead Signature:** [To be assigned]  
**Next:** PHASE1_DETAILED_TIMELINE.md (5-week execution schedule)
