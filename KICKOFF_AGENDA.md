# PHASE 10 KICKOFF AGENDA

**Date:** 2026-09-26 (Thursday)  
**Time:** 10:00 AM – 12:00 PM UTC  
**Duration:** 2 hours  
**Attendees:** All team leads, integration lead, code review lead, security lead

---

## 📋 AGENDA OUTLINE

| Time | Section | Owner | Duration |
|---|---|---|---|
| **10:00–10:15** | Welcome + Timeline | Integration Lead | 15 min |
| **10:15–10:35** | Stream 1: Workflow Optimizer | Stream 1 Lead | 20 min |
| **10:35–10:55** | Stream 2: Security Orchestrator | Stream 2 Lead | 20 min |
| **10:55–11:15** | Stream 3: Flow Guard | Stream 3 Lead | 20 min |
| **11:15–11:35** | Stream 4: Feedback Integration | Stream 4 Lead | 20 min |
| **11:35–12:05** | Team Assignments + Roles | HR / Integration | 30 min |
| **12:05–12:20** | Risk Matrix + Mitigations | Security Lead | 15 min |
| **12:20–12:35** | Phase Gates + Success Criteria | Product Lead | 15 min |
| **12:35–12:50** | Q&A + Open Discussion | All | 15 min |
| **12:50–12:00** | Closing + Next Steps | Integration Lead | 10 min |

---

## 🎯 DETAILED AGENDA

### 10:00–10:15: Welcome + Timeline Overview (15 min)

**Owner:** Integration Lead

**Objectives:**
- Set tone: Phase 10 is **self-optimizing skills + learning loops**
- Recap Phase 9 remediation (what we fixed)
- Confirm mission: 326 tests ✅, 0 critical findings, production by 2026-12-15
- Review timeline at a glance: 12 weeks, 4 parallel streams, 5 phase gates

**Slides:**
1. Phase 10 Vision (1 slide): "Skills 2.0 makes CorvinOS learn from operator feedback"
2. Phase 9 Recap (1 slide): "13 critical security fixes merged + tested"
3. Phase 10 Critical Path (1 slide): "4 streams, 12 weeks, gates at weeks 1, 3, 6, 10, 12"
4. Go/No-Go (1 slide): "Current status: 95% ready, all blockers resolved"

**Exit Criteria:** Team understands the mission and timeline.

---

### 10:15–10:35: Stream 1 — Workflow Optimizer Skill (20 min)

**Owner:** Stream 1 Lead

**Objectives:**
- Present the design: what does this skill do?
- Show architecture: routing logic → feedback loop → config optimization
- Explain learning model: confidence scoring, fallback paths, audit trail
- Clarify timeline: weeks 2–6 (15 working days of core development)

**Talking Points:**
1. **Problem:** Hardcoded task routing doesn't adapt to operator preferences
2. **Solution:** Learn execution chains from operator feedback
3. **Architecture:** `workflow_optimizer.py` (400 LoC) + config manager (250 LoC) + routes (300 LoC) + UI (500 LoC)
4. **Learning Loop:** Task runs → outcome → operator feedback → config delta → next task uses new config
5. **Audit Trail:** Every routing decision + feedback logged immutably (ADR-0232/0233)
6. **Compliance:** Consent gates, house-rules enforced, never fail-open
7. **Timeline:** Development (weeks 2–6), testing (ongoing), gates at weeks 3 + 6
8. **Success Criteria:** 82 tests passing, confidence scoring >85% accurate, >10% latency improvement

**Key Graphic (describe):**
```
Phase 9:                Phase 10:
[Intent Router]    →   [Workflow Optimizer]
  (classify)             (learn routing)
                         
                         ↓ feedback
                         ↓ config delta
                         
                    [Learning Loop]
```

**Q&A Likely Questions:**
- Q: "What if operator feedback is wrong?" → A: Confidence scoring prevents over-trusting noisy feedback
- Q: "Can we roll back to hardcoded routing?" → A: Yes, via console reset button
- Q: "What happens if feedback is contradictory?" → A: Recent feedback weighted higher (decaying average)

**Exit Criteria:** Stream 1 lead + team understand the skill design and timeline.

---

### 10:35–10:55: Stream 2 — Security Orchestrator Skill (20 min)

**Owner:** Stream 2 Lead

**Objectives:**
- Present threat detection patterns (brute force, privilege escalation, exfiltration, distributed)
- Show policy tightening mechanism: detect threat → tighten gate → TTL revert if threat clears
- Emphasize: automatic response, no human action needed, conservative (never bypass house-rules)
- Clarify timeline: weeks 3–10 (parallel to Stream 1), blocks on Stream 1 foundation

**Talking Points:**
1. **Problem:** Static security gates cannot respond to emerging threats
2. **Solution:** Monitor audit trail for attack patterns, dynamically harden gates
3. **Threat Patterns:**
   - Brute force: >N failed auth attempts in T seconds → auth_gate threshold 3→1
   - Privilege escalation: Unauthorized overrides → disable user overrides
   - Data exfiltration: >N high-risk flows to external engine → tighten data classification
   - Distributed attack: >N requests from different IPs → rate limit by IP
4. **Policy Tightening:** Never weakens (conservatives), auto-reverts after TTL if threat clears
5. **Audit Trail:** Every threat detection + policy change logged (security_threat_detected, security_policy_tightened)
6. **Compliance:** Cannot disable house-rules, cannot modify audit chain, all policy changes reversible
7. **Timeline:** Development (weeks 3–10), security review (weeks 8–10)
8. **Success Criteria:** 99 tests passing, threat detection accuracy >90%, false positive rate <10%

**Key Graphic (describe):**
```
[Audit Trail] → [Threat Detector] → [Threat Confidence] → [Policy Tightener]
                                         ↓
                                     [TTL Revert]
```

**Q&A Likely Questions:**
- Q: "What if threat detection is wrong?" → A: False positives expire via TTL; no permanent lockdown
- Q: "Can an attacker disable the security orchestrator?" → A: No, it's a meta-skill immune to tampering
- Q: "How long does policy tightening last?" → A: 1-hour TTL by default; reversible if threat clears

**Exit Criteria:** Stream 2 lead + team understand threat detection + policy mechanism.

---

### 10:55–11:15: Stream 3 — Flow Guard Skill (20 min)

**Owner:** Stream 3 Lead

**Objectives:**
- Present data classification + flow decision logic
- Show conservative learning: allow confidence ↑, deny confidence never ↓
- Explain approval gate: uncertain flows ask operator before allowing
- Clarify timeline: weeks 3–10 (parallel to Stream 2), blocks on Stream 1 foundation

**Talking Points:**
1. **Problem:** Static data flow policies cannot adapt to real operator usage patterns
2. **Solution:** Learn safe data flows from outcomes, never weaken policy
3. **Data Model:**
   - Classify data by sensitivity (PII, credentials, non-sensitive)
   - Track flow outcomes (success, PII leak, error)
   - Update policy confidence (allow ↑ on success, deny ↑ on leak)
4. **Conservative Learning:** Deny confidence never decreases; once a flow is blocked, it stays blocked
5. **Approval Gate:** High-uncertainty flows ask operator before allowing
6. **Audit Trail:** Every allow/deny decision logged (data_flow_classified, data_flow_decision)
7. **Compliance:** Credentials always deny (confidence=1.0), PII respects consent gates, no auto-deny weaken
8. **Timeline:** Development (weeks 3–10), security review (weeks 8–10)
9. **Success Criteria:** 90 tests passing, confidence accuracy >85%, zero unauthorized flows, approval gate prevents high-uncertainty paths

**Key Graphic (describe):**
```
[Data Flow] → [Classifier] → [Policy Lookup] → {Allow | Deny | Uncertain}
                                                     ↓
                                            [Outcome] → [Confidence Update]
                                            (never weaken deny)
```

**Q&A Likely Questions:**
- Q: "What if operator approves an unsafe flow?" → A: Outcome is audited; negative feedback decreases confidence
- Q: "Can policy weakening break compliance?" → A: No, deny confidence only increases, never decreases
- Q: "What counts as 'high-uncertainty'?" → A: Confidence <0.7; operator approval gate activates

**Exit Criteria:** Stream 3 lead + team understand flow policy + conservative learning model.

---

### 11:15–11:35: Stream 4 — Feedback Integration Schema (20 min)

**Owner:** Stream 4 Lead

**Objectives:**
- Present unified feedback schema (all 3 streams consume same format)
- Show simple API: rating (-2..+2), category (accuracy/speed/safety), reasoning (free-text, scrubbed)
- Explain processing pipeline: audit-first, validate, process per-skill, log config delta
- Clarify timeline: weeks 1–2 (fast track), ships by end of Week 1 (gates at Week 1)

**Talking Points:**
1. **Problem:** Each skill (Workflow, Security, Flow Guard) needs different feedback
2. **Solution:** Define ONE unified schema, all skills consume it
3. **Feedback Schema:**
   ```python
   FeedbackEvent(
       feedback_id: UUID,           # Idempotency key
       skill_id: "os.workflow_optimizer",
       subject_id: "task_123",      # task_id | threat_id | flow_id
       rating: -2..+2,              # -2: bad, +2: excellent
       category: "accuracy|speed|safety|other",
       reasoning: "optional text",  # PII scrubbed before storage
   )
   ```
4. **Processing Pipeline:**
   - Submit feedback → audit log (must succeed)
   - Validate (Pydantic fail-closed)
   - Process per-skill (Workflow Optimizer ↑ path priority, Security Orchestrator ↑ threat confidence, Flow Guard ↑ allow/deny confidence)
   - Log config delta (must succeed)
5. **Validation:** Fail-closed (invalid feedback rejected), idempotent (feedback_id prevents duplicates)
6. **PII Scrubbing:** Email, phone, API keys redacted from reasoning field before storage
7. **Compliance:** Consent required, audit-first, immutable
8. **Console UI:** One feedback form, serves all 3 skills, history + ratings visible
9. **Timeline:** Development (weeks 1–2), testing (ongoing), deployed by end of Week 1
10. **Success Criteria:** 55 tests passing, all skills process unified feedback, no PII leaks, audit trail complete

**Key Graphic (describe):**
```
[Operator Feedback] → [Validation] → [Audit Log]
                                          ↓
                        ┌─────────────────┼─────────────────┐
                        ↓                 ↓                 ↓
                   [Workflow]        [Security]        [Flow Guard]
                   (↑ path priority) (↑ threat conf)  (↑ allow/deny conf)
                        ↓                 ↓                 ↓
                        └─────────────────┼─────────────────┘
                                          ↓
                               [Audit Log: config_delta]
```

**Q&A Likely Questions:**
- Q: "Can one feedback event update multiple skills?" → A: Yes, feedback is routed to all relevant skills
- Q: "What if feedback is contradictory across skills?" → A: Each skill weighs feedback independently; no cross-skill conflict
- Q: "How long does feedback influence decisions?" → A: Decay/window strategy defined in ADR-2033 (open question)

**Exit Criteria:** Stream 4 lead + team understand unified feedback schema + processing pipeline.

---

### 11:35–12:05: Team Assignments + Roles (30 min)

**Owner:** HR / Integration Lead

**Objectives:**
- Assign each team lead to a stream (or shared role)
- Confirm FTE commitments + availability
- Distribute ADR responsibilities (each lead owns their ADR)
- Establish escalation paths + backup leads

**Process:**
1. **Read out team roster** (7 roles):
   - Stream 1 Lead (Workflow Optimizer) — FTE 1.2, weeks 2–6
   - Stream 2 Lead (Security Orchestrator) — FTE 1.2, weeks 3–10
   - Stream 3 Lead (Flow Guard) — FTE 1.0, weeks 3–10
   - Stream 4 Lead (Feedback Integration) — FTE 0.3, weeks 1–2
   - Integration Lead (Orchestration) — FTE 0.5, weeks 1–12
   - Code Review Lead (Architecture + Compliance) — FTE 0.8, weeks 1–12
   - Security Lead (Threat modeling + pen testing) — FTE 0.7, weeks 8–12

2. **Confirm availability** (each lead confirms yes/no)

3. **Distribute ADRs:**
   - ADR-2030 → Stream 1 Lead (owner)
   - ADR-2031 → Stream 2 Lead (owner)
   - ADR-2032 → Stream 3 Lead (owner)
   - ADR-2033 → Stream 4 Lead (owner)

4. **Establish escalation:**
   - Integration Lead = overall escalation
   - Code Review Lead = ADR conflicts / architecture questions
   - Security Lead = threat / compliance questions

5. **Slack + Jira assignment:**
   - Each lead gets Jira epic (backlog items pre-populated from ADR)
   - Each lead joins #phase-10-<stream> Slack channel

6. **Weekly sync:**
   - Mondays 10:00 AM UTC (recurring)
   - 30 min syncs: progress update + blockers + next week plan
   - Integration Lead runs the meeting

**Exit Criteria:** All 7 roles assigned, FTE confirmed, escalation paths clear.

---

### 12:05–12:20: Risk Matrix + Mitigations (15 min)

**Owner:** Security Lead

**Objectives:**
- Surface top risks (technical, organizational, compliance)
- Present mitigations per risk
- Establish contingency plans (if risk materializes, what do we do?)

**Key Risks (examples):**

| Risk | Probability | Impact | Mitigation | Contingency |
|---|---|---|---|---|
| Feedback quality poor (noisy) | MEDIUM | HIGH | Confidence scoring + validation rules | Revert to hardcoded routing (Stream 1) |
| Threat detection false positives | MEDIUM | HIGH | Conservative threshold + TTL revert | Manual override + threat review |
| Cross-skill feedback conflicts | LOW | MEDIUM | Independent processing per skill | Audit trail shows which skill won |
| Scope creep (more ADRs added) | MEDIUM | MEDIUM | Strict scope gate at Week 1 | De-scope lowest-priority feature |
| Team member unavailable | LOW | MEDIUM | Cross-training + backup leads | Redistribute FTE; extend timeline |
| Security review finds critical issue | LOW | HIGH | Pen testing starts Week 8 | Postpone release (freeze at Week 10) |

**Discussion:**
- Which risks are most concerning to this team?
- Are there team-specific risks (dependencies, skills, resources)?
- Agree on contingency triggers (e.g., "if false positive rate >15%, we re-tune threat detection in Week 5")

**Exit Criteria:** Team agrees on risk profile + mitigations.

---

### 12:20–12:35: Phase Gates + Success Criteria (15 min)

**Owner:** Product Lead

**Objectives:**
- Make gates concrete and measurable
- Clarify go/no-go decisions at each gate
- Establish who decides (decision-maker) and when

**Phase Gates:**

| Gate | Week | Go Criteria | No-Go Criteria | Decision-Maker |
|---|---|---|---|---|
| **Gate 1** | 1 | ADR-2033 ACCEPTED + feedback routes deployed + staging soak green | ADR not ready OR deployment failed | Kickoff meeting (this week) |
| **Gate 2** | 3 | Stream 1 50% + no critical findings in code review | Critical findings found OR <50% progress | Code Review Lead |
| **Gate 3** | 6 | Stream 1 complete + 82 tests passing + staging deployment approved | Tests failing OR critical issues remain | QA Lead |
| **Gate 4** | 10 | Streams 2–3 complete + 190 tests passing + pen testing complete | High-severity findings OR tests failing | Security Lead |
| **Final** | 12 | All 326 tests passing + 7-day soak test green + 0 critical findings | Any test failing OR critical findings remain | Product Lead |

**Success Metrics (Week 12 final gate):**
- ✅ 326 tests passing (82 + 99 + 90 + 55)
- ✅ Code coverage >85% (all new code)
- ✅ Security review: 0 critical, ≤2 high findings
- ✅ Staging soak test: 7 days, 0 critical incidents
- ✅ Audit trail integrity verified
- ✅ All 4 ADRs status=ACCEPTED

**Exit Criteria:** Team agrees on gate criteria + decision-makers.

---

### 12:35–12:50: Q&A + Open Discussion (15 min)

**Owner:** Integration Lead

**Format:**
- Open floor for questions
- Concerns about timeline / scope / resources
- Requests for clarification on any stream

**Topics to surface (if not asked):**
- "Does Stream 1 really block Streams 2–3?" → Yes, they depend on Workflow foundation
- "What happens if we miss Gate 3?" → De-scope lowest-priority Stream 1 features; extend Week 6→7
- "Can we parallelize Stream 1 + 4?" → Yes, Stream 4 is independent; can start Week 1 day 1
- "What's the rollback plan if Skills 2.0 breaks production?" → Feature flag + ADR-0532 Phase 0 (disabled by default in registry.yaml)

**Exit Criteria:** All open questions answered; team ready to execute.

---

### 12:50–13:00: Closing + Next Steps (10 min)

**Owner:** Integration Lead

**Actions:**
1. **Distribute materials:**
   - PHASE_10_MASTER_ORCHESTRATION.md
   - All 4 ADRs (ADR-2030/2031/2032/2033)
   - This agenda + meeting recording

2. **Confirm next meeting:**
   - Weekly sync: Monday 2026-09-30, 10:00 AM UTC (first sync)
   - Review progress on Stream 4 ADR-2033 (should be ACCEPTED by then)

3. **Action items due by EOD 2026-09-26:**
   - Stream leads: read your ADR + backlog
   - Code review lead: schedule initial architecture review
   - Security lead: schedule threat modeling session

4. **Closing message:**
   - "Phase 10 is a 12-week sprint to make CorvinOS self-optimizing."
   - "We've resolved Phase 9 blockers, validated all ADRs, and are ready to execute."
   - "First gate is Week 1: ADR-2033 ACCEPTED + feedback routes live. That's the real milestone."
   - "Let's ship this with zero critical findings and high confidence."

**Exit Criteria:** Team knows what to do Monday morning.

---

## 📋 ATTENDANCE REQUIRED

**Core Team (must attend):**
- Integration Lead
- Stream 1 Lead (Workflow Optimizer)
- Stream 2 Lead (Security Orchestrator)
- Stream 3 Lead (Flow Guard)
- Stream 4 Lead (Feedback Integration)
- Code Review Lead
- Security Lead

**Optional (recommended):**
- Product Lead (for gates + success criteria section)
- QA Lead (for testing strategy)
- DevOps Lead (for deployment + monitoring)
- Any team member interested in Phase 10 deep-dive

---

## 📎 MATERIALS TO DISTRIBUTE

**Before Kickoff (email 2026-09-25):**
1. This agenda (KICKOFF_AGENDA.md)
2. PHASE_10_MASTER_ORCHESTRATION.md (72-page plan)
3. All 4 ADRs (ADR-2030/2031/2032/2033)
4. PHASE10_KICKOFF_FINAL_PREP.md (status report)
5. Phase 9 Remediation Summary (what we fixed)

**At Kickoff (in-meeting):**
1. Team assignment confirmation form (collect signatures)
2. Escalation contact sheet (phone + Slack + email)
3. Weekly sync calendar invite (recurring Mondays 10:00 AM UTC)
4. Jira epic links (backlog pre-populated for each stream)
5. Slack channel links (#phase-10-engineering, #phase-10-stream-1, etc.)

---

## 🎬 LOGISTICS

- **Platform:** Zoom (or preferred video conference)
- **Recording:** Yes (for team members who cannot attend live)
- **Slides:** Shared during call (or pre-recorded for certain sections)
- **Chat:** Slack #phase-10-engineering (for side conversations)
- **Follow-up:** Meeting notes + action items posted within 24h

---

**Prepared by:** Claude Haiku 4.5  
**Date:** 2026-09-24  
**Status:** Ready for 2026-09-26 kickoff

> **Go Phase 10.** 12 weeks. 4 streams. 326 tests. Zero critical findings. Production ready by Dec 15.
