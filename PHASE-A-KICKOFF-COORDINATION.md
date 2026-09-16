# Phase A Kickoff & Track Coordination (Session 3, 2026-09-16)

**Reference:** ADR-0689 (Tier-2 Orchestration Master Plan)  
**Status:** READY TO EXECUTE  
**Estimated Duration:** 3–5 sessions (Sessions 3–5)

---

## Overview: Three Parallel Tracks

| Track | Owner | Duration | Start | Phase |
|---|---|---|---|---|
| **Track 1: OS-Skills k=2** | Infrastructure | 1–2 sessions | Session 3 | Adversarial review + load test |
| **Track 2: Marketplace UI** | Frontend | 1–2 sessions | Session 3 | Implement 5 cards + search |
| **Track 3: Blocker 3 Phase 2** | Security | 0.5 sessions | After operator | Execute rotation script |

---

## Track 1: OS-Skills Infrastructure Phase 2

### Owner Assignment: [TBD — Infrastructure Team]

### Current State (k=1 Complete)
- ✅ Health Monitor Skill (subsystem state tracking)
- ✅ Context Bridge Skill (auto-context-splitting, session continuity)
- ✅ Basic Orchestrator Skill (plugin loading, task routing, composition)
- ✅ Mock audit trail (file-based, hash-chained, tenant-isolated)
- ✅ 5 manual E2E tests: all pass

### Phase 2 Roadmap (k=2 Refinement)

**Week 1 (Sessions 3–4):**
1. Adversarial Review
   - [ ] Vector 1: Input injection (verify field sanitization)
   - [ ] Vector 2: Composition DAG bypass (verify dependency constraints)
   - [ ] Vector 3: Timeout enforcement (verify per-call budgets)
   - [ ] Vector 4: Audit trail gaps (verify all executions logged)
   - Target: 0 CRITICAL findings

2. Load Testing
   - [ ] Mock ≥500 concurrent skill executions
   - [ ] Verify no memory leaks
   - [ ] Measure latency (p50, p99)
   - [ ] Verify audit trail under load

**Week 2 (Session 5):**
3. Timeout Enforcement
   - [ ] Per-call budgets (hardcoded or configurable?)
   - [ ] Graceful degradation (timeout → fallback)
   - [ ] Test timeout recovery

### Acceptance Criteria (k=2 COMPLETE)
- [ ] 0 CRITICAL adversarial findings
- [ ] Load test: ≥500 concurrent executions pass
- [ ] Timeout enforcement verified (all 3 skills)
- [ ] Audit trail: 100% execution coverage
- [ ] Composition DAG: dependency verification automated
- [ ] Merge PR created with k=2 results

### Merge Condition
- **Independent:** No downstream dependencies — can merge standalone
- **Trigger:** Acceptance criteria all checked

### Related Files
- Implementation: `core/skills/phase1_manifest_v2.py`, `core/skills/phase1_skeleton_generator.py`
- Tests: `core/skills/test_phase1.py` (expand with load + adversarial)
- ADR: ADR-0690 (Track-specific, to be written by owner)

---

## Track 2: Marketplace Hub UI Feature

### Owner Assignment: [TBD — Frontend Team]

### Current State (Partial)
- ✅ Page component created
- ✅ Route registered (`/marketplace`)
- ❌ Full UI NOT implemented (5 cards missing)
- ❌ API integration NOT done

### Phase A Roadmap (Full UI + API)

**Week 1 (Sessions 3–4):**
1. Card Component Implementation (5 types)
   - [ ] Plugin Card (icon, name, version, install button)
   - [ ] Skill Card (icon, name, confidence score, install)
   - [ ] Dataset Card (icon, name, rows, download)
   - [ ] Service Card (icon, name, health status, endpoint)
   - [ ] Template Card (icon, name, preview, use)
   - Style: consistent with console design system

2. Search UI
   - [ ] Query input (text field)
   - [ ] Results table (sortable, filterable)
   - [ ] Filters: type (Plugin/Skill/Dataset/Service/Template)
   - [ ] Responsive design (mobile, tablet, desktop)

3. API Wiring
   - [ ] `/marketplace/plugins` endpoint mock
   - [ ] `/marketplace/search` endpoint mock
   - [ ] Wire React hooks (useMarketplace, useSearch)
   - [ ] Handle loading/error states

**Week 2 (Session 5):**
4. E2E Test
   - [ ] Full workflow: search → view details → install mockup
   - [ ] All 5 card types render
   - [ ] Search filters work
   - [ ] Responsive design verified

### Acceptance Criteria (Full UI Complete)
- [ ] All 5 card types rendering correctly
- [ ] Search UI functional (query + filters)
- [ ] API endpoints wired (mock or real)
- [ ] Responsive design verified (mobile, tablet, desktop)
- [ ] E2E test passes (Playwright or similar)
- [ ] Merge PR created with UI screenshots

### Merge Condition
- **Independent:** No upstream dependencies — can merge standalone
- **Trigger:** Acceptance criteria all checked

### Related Files
- Implementation: `core/console/corvin_console/web-next/src/pages/marketplace.tsx` (extend)
- Tests: `tests/e2e/test_marketplace_hub.py` (new or extend)
- ADR: ADR-0691 (Track-specific, to be written by owner)

---

## Track 3: Blocker 3 Phase 2 Execution

### Owner Assignment: Security Team + Operator

### Current State (Ready)
- ✅ Rotation script written (`scripts/rotate_corvin_keys_phase2.py`)
- ✅ Phase 1 plan documented (BLOCKER_3_PHASE2_READINESS.md)
- ⏸️ Phase 1 BLOCKING (operator manual revocation)

### Phase 2 Roadmap (Execution)

**When Operator Completes Phase 1 (Any Time):**
1. Manual Revocation (Operator Action)
   - [ ] GitHub: revoke all PATs with "corvin" (https://github.com/settings/tokens)
   - [ ] Hetzner: revoke API tokens (https://console.hetzner.cloud/account/security/api-tokens)
   - [ ] Cloudflare: roll API tokens (https://dash.cloudflare.com/profile/api-tokens)
   - [ ] OpenAI: delete old keys (https://platform.openai.com/account/api-keys)
   - [ ] Gmail: remove app password (https://myaccount.google.com/apppasswords)
   - [ ] PyPI: delete API token (https://pypi.org/account/settings/)
   - [ ] Resend: delete old key (https://resend.com/settings/api-keys)
   - [ ] Ollama: regenerate locally (N/A for cloud)

2. Phase 2 Execution (Automated)
   ```bash
   cd /home/shumway/projects/CorvinOS
   python3 scripts/rotate_corvin_keys_phase2.py
   ```
   - [ ] Backup created (mode 0600)
   - [ ] 14 credentials replaced with placeholders
   - [ ] Audit event logged to audit.jsonl
   - [ ] No errors in stdout

3. Verification
   - [ ] All credential files show placeholders (grep PLACEHOLDER)
   - [ ] Audit trail shows `secret_rotation_phase2` event
   - [ ] Services fail with 401 when using placeholders (test one)

### Acceptance Criteria (Phase 2 Complete)
- [ ] Phase 1 complete (all 8 services revoked)
- [ ] Phase 2 script executed without errors
- [ ] All 14 credentials replaced with placeholders
- [ ] Audit trail shows rotation event
- [ ] Backup available (mode 0600)

### Merge Condition
- **Independent:** Orthogonal to all features — can merge anytime
- **Trigger:** Execution complete + verification passed

### Related Files
- Script: `scripts/rotate_corvin_keys_phase2.py`
- Documentation: `BLOCKER_3_PHASE2_READINESS.md`
- ADR: ADR-0692 (Track-specific, post-execution)

---

## Phase A Success Definition

**Phase A COMPLETE when:**
- [ ] Track 1: k=2 complete (merged, acceptance criteria checked)
- [ ] Track 2: Full UI complete (merged, screenshots verified)
- [ ] Track 3: Rotation complete (merged, credentials rotated)
- [ ] All three tracks merged without conflicts
- [ ] No rollback needed (all acceptance criteria met)

**Then:** Phase B (Learning Integration) starts on clean foundation (Session 5+)

---

## Session Timeline & Coordination

### Session 3 (Now)
- [ ] Assign track owners (DECISION REQUIRED)
- [ ] Finalize Track 1 implementation path (Owner: Infrastructure)
- [ ] Finalize Track 2 UI design (Owner: Frontend)
- [ ] Prepare Blocker 3 execution (Owner: Security)
- [ ] Kick off all three tracks in parallel

### Sessions 4–5
- Tracks execute in parallel
- Daily sync: status updates per track
- Merge PRs as ready (Track 3 likely first, then Track 1, then Track 2)

### Session 5+
- Track 1 k=2 verification complete
- Phase B (Learning Integration) unblocked
- Begin Learning track

---

## Coordination Best Practices

### Daily Standup (Minimal)
- **Track 1:** Latest adversarial finding + load test status
- **Track 2:** UI component completion % + API wiring status
- **Track 3:** Operator Phase 1 status + Phase 2 readiness

### Merge Workflow
1. Track owner creates PR (link to Track-specific ADR)
2. Review checklist: all acceptance criteria checked
3. Merge to main (no conflicts expected)
4. Update ADR-0689 `commits:` field with merge commit hash

### Risk Watch
- **Track 1 blocked:** Ask infrastructure owner + review acceptance criteria
- **Track 2 blocked:** Ask frontend owner + check design blocker
- **Track 3 blocked:** Await operator Phase 1 (not a blocker for Tracks 1–2)

---

## Next Immediate Actions

### Right Now (This Turn)
1. [ ] **Assign track owners** — who owns each track?
2. [ ] **Create track-specific ADRs** (0690, 0691, 0692)
   - Track 1 implementation details
   - Track 2 UI/API design
   - Track 3 execution notes
3. [ ] **Kick off parallel execution** — all teams start simultaneously

### Then (Sessions 3–5)
- Tracks run independently
- Coordinate only on merge order (Track 3 → 1 → 2)
- Monitor acceptance criteria per track

---

**Coordination Master:** ADR-0689  
**Status:** Ready to execute (awaiting owner assignment)  
**Owner Accountability:** Each track owner responsible for acceptance criteria + merge readiness
