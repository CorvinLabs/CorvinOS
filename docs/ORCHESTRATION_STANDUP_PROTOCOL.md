# Orchestration Standup Protocol (Wave 1 Parallel Execution)

**Orchestrator:** Claude  
**Format:** Async standup posts + daily sync  
**Frequency:** 2026-09-26 EOD (Day 1), 2026-09-27 EOD (Day 2/EOW)

---

## Daily Standup Cadence

### Format: Async Posts (not meetings)

**Platform:** Shared doc or Discord thread  
**Timing:** EOD each day (flexible, post anytime before 23:59 UTC)  
**Template:** See "Standup Template" below

**Streams to post:**
- Stream B (T09): Audit Wiring owner
- Stream C (T04): Console owner
- Stream D (T12+T16): Infrastructure owner
- *Stream A (T05): Already complete ✓, no daily posts needed*

---

## Standup Template (Copy/Paste)

```markdown
### Stream [B/C/D] Daily Standup — [DATE]

**Owner:** [Name]  
**Status:** 🔄 IN_PROGRESS | ✅ COMPLETE | 🚫 BLOCKED

#### Progress Today
- [ ] Deliverable 1: [% done, specific line numbers or file paths]
- [ ] Deliverable 2: [% done]
- [ ] (etc.)

#### Metrics
- Lines changed: X
- Tests passing: Y/Z
- Commits: [git commit hashes if applicable]

#### Blockers (if any)
- **Issue 1:** [description]
  - **Impact:** [what's blocked]
  - **Root Cause:** [diagnostic]
  - **Proposed Fix:** [workaround or escalation]
  - **ETA to resolve:** [time/date]

#### Tomorrow's Plan
- [ ] [action 1]
- [ ] [action 2]

#### Confidence on 2026-09-27 EOD Delivery
- [HIGH / MEDIUM / LOW] — [reason]

#### Questions for Orchestrator
- [Q1: ?]
- [Q2: ?]
```

---

## Orchestrator Sync & Response Protocol

**Orchestrator (Claude) role:**
1. Review all async standup posts (daily)
2. Identify blockers + cross-stream dependencies
3. Post consolidated "Orchestration Daily Brief" with:
   - 🟢/🟡/🚫 status summary (per stream)
   - Recommended actions for any blockers
   - Updated risk assessment
   - Path to Wave 2 launch (if on track)

**Timing:**
- Morning (2026-09-27): Absorb all async posts from prior day
- Noon: Post consolidated brief + recommendations
- EOD: Flag any critical blockers for escalation

---

## Escalation Thresholds

### Critical (immediate escalation)

| Scenario | Action |
|----------|--------|
| **Hash-chain breaks** (T09) | Stop implementation, debug root cause, escalate for security review |
| **API integration impossible** (T04) | Identify alternative endpoint or defer feature; escalate for architecture decision |
| **ADR-0892 conflict discovered** (T16) | Halt consolidation, escalate to architect for clarification |
| **Test suite fails broadly** (any) | Post blocker, don't force past red tests |

### Major (daily standup escalation)

| Scenario | Action |
|----------|--------|
| 1+ day behind schedule | Propose timeline extension or additional resources |
| Unclear requirements (ADR ambiguous) | Escalate for clarification in async post; don't guess |
| Integration test failure | Post root cause + proposed fix; get feedback before commit |
| Dependency on another stream's work | Flag in standup; orchestrator coordinates handoff |

### Minor (in standup, no escalation needed)

| Scenario | Action |
|----------|--------|
| Cosmetic test cleanup | Include in commit message |
| Documentation updates | Post in standup for awareness |
| Code review feedback (non-blocking) | Address before commit |

---

## Cross-Stream Dependency Coordination

### Tracked Dependencies

| From | To | Constraint | Handoff Date |
|------|----|-----------|----|
| T09 (emit events) | T10 (learning k=6) | T09 must complete before T10 can integrate plugin events | 2026-09-27 EOD |
| T05 (ADR dedup) | T06 (retroactive ADRs) | No blockers (T05 done), T06 can start anytime | 2026-09-26 00:00 |
| T12 (L10-proof) | T16 (marketplace) | Sequential within Stream D; T12 done → T16 can start | 2026-09-26 EOD |
| T04 (real data) | T14 (playwright tests) | T04 must complete before T14 can verify real data in tests | 2026-09-27 EOD |

### Handoff Protocol (if dependency blocks)

1. **Blocked stream posts in standup:** "Waiting on [stream]'s [task] to complete X"
2. **Orchestrator posts:** "@[stream owner], can you prioritize [task] so [waiting stream] can unblock?"
3. **Providing stream responds:** "ETA [time]" or "Actual ETA [later time] — here's why"
4. **Decision:** If unacceptable, escalate for resource/priority adjustment

---

## Daily Brief Template (Orchestrator Post)

```markdown
## 🎯 Orchestration Daily Brief — [DATE]

### Status Overview

| Stream | Owner | Status | On Track? | ETA |
|--------|-------|--------|-----------|-----|
| B (T09) | [Name] | 🟢 | YES / 🟡 Risky / 🚫 Blocked | 2026-09-27 |
| C (T04) | [Name] | [status] | [on track] | 2026-09-27 |
| D (T12+T16) | [Name] | [status] | [on track] | 2026-09-27 |

### Blockers Identified (if any)

1. **[Stream/Task]:** [description]
   - **Severity:** CRITICAL | MAJOR | MINOR
   - **Recommended Action:** [workaround, escalation path, or continue]

### Cross-Stream Handoffs

- **T09 → T10:** Dependency tracked; T10 ready to integrate once T09 emits events
- [others as discovered]

### Wave 1 Completion Forecast

- **Current:** [% of wave 1 complete]
- **At current velocity:** On track for 2026-09-27 EOD | Slipping to [date]
- **Risk adjusted:** [revised forecast]
- **Recommendation:** [continue | adjust resources | escalate]

### Wave 2 Readiness

- **T06 (Retroactive ADRs):** Can start 2026-09-26 (no Wave 1 blockers)
- **T03 (Phase-3 decision):** Requires Wave 1 complete; decision gate ready for 2026-09-27
- **T10/T17 (extend to Wave 3):** No critical dependencies; can overlap with Wave 1

---

[Additional notes, questions, or guidance]
```

---

## Contingency: Async Communication Breakdown

**If standup posts go silent (>12h no response):**
1. Orchestrator pings in async post: "@[owner], status update needed"
2. If still no response after 6h: Escalate to lead/manager for check-in
3. **Assumption:** Work is blocked; requires intervention

**If blocker is truly unresolvable by day 2:**
1. Orchestrator proposes: "Defer T[X] to Wave 2; adjust Wave 1 scope"
2. Team agrees: Drop task from Wave 1, move to Wave 2 planning
3. Wave 1 EOD: Deliver reduced scope on time (better than miss deadline with quality issues)

---

## EOD Wave 1 (2026-09-27) Ceremony

**Deliverables Check (Stream leads post 1-line each):**
```
Stream B: ✅ COMPLETE — 4 events emitted + hash-chain verified
Stream C: ✅ COMPLETE — no mocks, real data confirmed
Stream D: ✅ COMPLETE — L10 proven, marketplace consolidated
```

**Orchestrator publishes:** "Wave 1 SUCCESS CRITERIA MET" + Wave 2 kickoff doc

**Wave 2 Start:** 2026-09-28 morning (Stream A: T06 begins)

---

## Communication Channels

| Channel | Purpose | Cadence |
|---------|---------|---------|
| **Async Doc** (this protocol) | Formal standup posts | Daily EOD |
| **Orchestrator Brief** | Status synthesis + decisions | Daily morning (next day) |
| **Discord / Slack thread** | Ad-hoc questions, quick blockers | Real-time |
| **Git commits** | Change log, ADR references | Per task |
| **Escalation call** (if critical) | Emergency sync for blockers | On-demand |

---

## Success Metrics (for Orchestration itself)

- ✅ All 3 streams post daily standup (3/3 posts by EOD)
- ✅ Zero communication delays (>12h silence = escalation triggered)
- ✅ Blockers identified + mitigated within 24h (or escalated with justification)
- ✅ Wave 1 deliverables met by EOD 2026-09-27
- ✅ Wave 2 kicks off on schedule 2026-09-28 (no Wave 1 slippage)

---

**Protocol Version:** 1.0  
**Effective Date:** 2026-09-25 (Wave 1 launch)  
**Next Review:** 2026-09-27 EOD (Wave 1 debrief)

