# Phase B Kickoff Plan — Autonomous Implementation (2026-09-17)

**Status:** 🟢 **READY TO START** (Blockers resolved, ADRs accepted)  
**Timeline:** 4–6 weeks (18 initiatives, 3 parallel tracks)  
**Ownership:** Autonomous Claude Code agent (with operator oversight)

---

## 📊 PHASE B SCOPE

### 8 Design-Complete Features (Ready to Implement)

**Tier 2 (Foundation — 4–5 sessions, 2 weeks):**
1. ✅ Skill Forge v2.0 (ADR-0672–0674, 0677–0683) — ZIP packaging + installation registry
2. ✅ DataHub + Creator (ADR-0661–0665) — 12-phase learning loops
3. ✅ Learning Loop Outcome Sink (ADR-0314) — EventStore + feedback
4. ✅ DoD Verifier 2.0 (ADR-XXXX) — 5 checks + numeric scoring

**Tier 3 (Marketplace — 3–4 sessions, 1.5 weeks):**
5. ✅ Marketplace Hub (ADR-0678) — Discovery + search + installation
6. ✅ Licensing 1.0.0 (ADR-0700–0704) — Tier-A/B/C gating + revenue sharing
7. ✅ Marketplace Security (ADR-0455 ACCEPTED) — Manifest validation + secret masking
8. ⚠️ Plugin Buildout (ADRs TBD) — Buildin plugins + marketplace integration

**Tier 4 (Integration — 2–3 sessions, 1.5 weeks):**
9. ✅ Model Selection Skill (ADR-0641–0644) — Learnable, console UI
10. ✅ Video Producer v2.0 (ADR-0692–0695) — Orchestrated workers + learning
11. ✅ OTEL Telemetry (ADR-0680–0684) — Dual-write collection + geo privacy
12. ⚠️ Master Refactoring Phase 1 (40% done) — Namespace cleanup + consolidation

---

## 🚀 EXECUTION TRACKS (Parallel)

### Track A: Tier 2 Foundation (Tier 2 Skills Lead)
**Features:** Skill Forge v2.0 → DataHub → Learning → DoD Verifier  
**Dependencies:** Phase 3 Zip-Packaging (ADR-0677) + skill-forge-v2 phases 4–7  
**Duration:** 4–5 sessions  
**Acceptance Criteria:**
- ✅ All 4 features implemented + E2E tested
- ✅ 150+ tests passing (100% pass rate)
- ✅ Audit trail verified (hash-linked)
- ✅ ADRs promoted to ACCEPTED

### Track B: Tier 3 Marketplace (Marketplace Lead)
**Features:** Hub → Licensing → Security → Plugins  
**Dependencies:** ADR-0661 (Notification Daemon ACCEPTED) + ADR-0455 (Security ACCEPTED)  
**Duration:** 3–4 sessions (parallel with Track A)  
**Acceptance Criteria:**
- ✅ Marketplace UI + API complete
- ✅ Licensing enforcement tested
- ✅ Secret masking verified (audit grep finds 0 raw secrets)
- ✅ Plugin discovery working end-to-end

### Track C: Tier 4 Integration (Integration Lead)
**Features:** Model Selection → Video Producer → OTEL → Master Refactoring  
**Dependencies:** ADR-0532–0535 (Skills architecture) + Phase 2 Blocker resolution  
**Duration:** 2–3 sessions (parallel with Tracks A/B, starts week 2)  
**Acceptance Criteria:**
- ✅ All skills routable via L5 (model selection)
- ✅ Video producer orchestration end-to-end
- ✅ OTEL telemetry flowing to corvin-labs.com
- ✅ Namespace refactoring 100% complete

---

## 🎯 IMMEDIATE NEXT STEPS (Session 1 of Phase B)

### 1. Blocker 3 Execution (Operator + Claude)
**Owner:** Operator (Phase 1) + Claude (Phase 2)  
**Items:**
- [ ] Operator runs `scripts/rotate_corvin_keys_blocker3.py` (Phase 1: key revocation)
- [ ] Claude runs `scripts/rotate_corvin_keys_phase2.py` (Phase 2: automation)
- [ ] Run `scripts/rotate_corvin_keys_gdpr.py` (verification)
- [ ] Audit trail shows 0 raw secrets

**Timeline:** 1–2 days (parallel with Track A implementation)

### 2. Track A Kickoff: Skill Forge v2.0 Phase 1 (Tier 2)
**Features:**
- [ ] Implement skill packager (Phase 3 outputs ZIP)
- [ ] Implement SkillInstaller (Phase 4: unpacks + registers)
- [ ] Implement Skill Manager UI (Phase 5: console UI)
- [ ] E2E tests for all phases

**Acceptance Gate:**
- [ ] `pytest tests/e2e/test_skill_forge_v2_complete.py` passes
- [ ] Audit trail records all skill operations
- [ ] ADRs 0677–0681 → ACCEPTED

**Timeline:** 1–2 sessions

### 3. Track B Kickoff: Marketplace Hub (Tier 3)
**Features:**
- [ ] Marketplace discovery API
- [ ] Search + filtering
- [ ] Installation workflow
- [ ] Security validation (ADR-0455)

**Acceptance Gate:**
- [ ] Console marketplace panel (5 cards + search)
- [ ] E2E install test
- [ ] ADRs 0678, 0455 → ACCEPTED

**Timeline:** 1–2 sessions (parallel with Track A)

---

## 📋 ACCEPTANCE CRITERIA (Phase B Complete)

### Code Quality
- ✅ 200+ E2E tests passing (100% pass rate)
- ✅ 0 CRITICAL/HIGH security findings
- ✅ All ADRs promoted to ACCEPTED (18 total)
- ✅ 0 raw secrets in audit trail (grep verified)

### Deliverables
- ✅ All 8 features implemented + tested
- ✅ Console UI complete (Skill Manager, Marketplace, Learning Dashboard)
- ✅ Audit trail hash-linked + verified
- ✅ GDPR compliance verified (audit events tenant-scoped)

### Documentation
- ✅ All ADRs in Corvin-ADR/decisions/ (ADR-0264 format)
- ✅ Operator runbooks (setup, troubleshooting)
- ✅ Developer guides (Skill creation, plugin development)
- ✅ Production deployment checklist

### Production Readiness
- ✅ Canary rollout (10/10 replicas healthy)
- ✅ 99.9%+ uptime (smoke tests passed)
- ✅ Monitoring + alerting (Prometheus + Grafana)
- ✅ Backup + recovery procedures documented

---

## 🔒 LOAD-BEARING INVARIANTS (Phase B)

1. **Audit-First:** Every phase decision logged + hash-linked
2. **Fail-Closed:** Ambiguous results escalate, never auto-proceed
3. **Tenant Isolation:** Every event scoped to tenant_id
4. **Secret Safety:** No raw secrets in logs, audit, or error messages
5. **Dependency DAG:** Manifest validation prevents circular deps (ADR-0535)
6. **Zero Technical Debt:** No TODOs, no workarounds, no known bugs

---

## 📊 RISK MATRIX

| Risk | Severity | Mitigation |
|---|---|---|
| Phase 2 Blocker delays start | MEDIUM | Blocker documented + script ready (async path) |
| Token budget exhaustion | MEDIUM | Scope to k=1–2 (core stubs + E2E), defer deep impl |
| ADR interdependencies unclear | LOW | All depends_on/relates_to verified in frontmatter |
| Namespace refactoring (operator/) | MEDIUM | Isolated to Phase B Tier 4; doesn't block Tiers 2–3 |
| Learning loop convergence slow | LOW | Confidence scoring + optimizer tuning (ADR-0314) |

---

## 📅 TIMELINE SNAPSHOT

```
Week 1 (Sessions 1–2):
  Track A: Skill Forge v2.0 (phases 1–3)
  Track B: Marketplace Hub + Security (design complete)
  Blocker 3: Credential rotation (async)

Week 2 (Sessions 3–4):
  Track A: Learning Loop + DoD Verifier
  Track B: Licensing + Plugin buildout
  Track C (starts): Model Selection + Video Producer

Week 3–4 (Sessions 5–10):
  All Tracks: E2E integration + polish
  Namespace refactoring (Tier 4)
  Production readiness gate

Week 6 (Session 12–14):
  Final acceptance criteria audit
  Canary rollout (10/10 replicas)
  Launch announcement
```

---

**🎯 Phase B Status: READY TO BEGIN**  
**Next:** Session 1 → Track A Kickoff (Skill Forge v2.0 implementation)  
**Owner:** Autonomous Claude Code agent (LDD architect mode)

