# Spike Owner Orientation Runbook

**For:** Shumway (all 5 spikes, solo execution)  
**Effective:** Week 0 (2026-09-02 to 2026-09-06)  
**Template:** Use for each spike (Spike 1–5)

---

## What is a Spike?

A **spike** is a time-boxed investigation (3–5 days) that measures ONE assumption. Not a feature, not a bugfix — just **measurement**.

**vs. Normal Task:**
- Normal Task: "Build X" (output: working code)
- Spike: "Is X feasible?" (output: velocity number, yes/no answer)

---

## The Five Spikes (Sequence)

### **Spike 1 (Mon–Wed): Timeline Velocity**
- **Question:** Can we rewrite one file in < 10 hours?
- **Work:** Pick admin.py (or similar), rewrite all feature flags → Skills API
- **Measure:** Days 1–3, total time = velocity
- **Report:** Extrapolate to 20 files. Does it fit in 10 days?
- **Success:** Velocity ≤ 10h/file OR escalate to NO-GO

### **Spike 2 (Tue–Thu): Rollback Strategy**
- **Question:** Can we recover from a broken deployment in < 1 hour?
- **Work:** Test both rollback strategies in staging (Shallow, Deep)
- **Measure:** Time from "broken state" → "working again"
- **Report:** Choose ONE strategy, document recovery time
- **Success:** Recovery < 1 hour OR escalate to NO-GO

### **Spike 3 (Wed–Fri): Load Test**
- **Question:** Does registry sustain 1000 concurrent with ZERO audit drops?
- **Work:** Run load test, 1000 users, 10+ min sustained
- **Measure:** Latency, throughput, error rate, **event loss count**
- **Report:** If drops > 0, escalate immediately (compliance violation)
- **Success:** Zero drops (non-negotiable) OR escalate to NO-GO

### **Spike 4 (Mon–Fri): Equivalence Scope**
- **Question:** What does "new code = old code" mean?
- **Work:** Define 4 dimensions (output, latency, error codes, edge cases)
- **Measure:** Tolerance thresholds per dimension
- **Report:** Create automated equivalence test
- **Success:** All 4 dimensions defined OR escalate to NO-GO

### **Spike 5 (Mon–Fri): Team Readiness**
- **Question:** Who's the backup if I get sick?
- **Work:** Identify backups, brief them, create escalation contacts
- **Measure:** Backup roster complete, phone numbers confirmed
- **Report:** Escalation contact list ready
- **Success:** Full redundancy OR escalate to NO-GO

---

## Report Template (Use for All Spikes)

**Filename:** `docs/SPIKE_[N]_REPORT.md`

```markdown
# Spike [N]: [Title] — REPORT

## Findings
[2–3 bullets: What did you measure? What did you learn?]

## Success Criteria Status
- [x] Criterion 1: [result]
- [x] Criterion 2: [result]
- [ ] Criterion 3: [blocked by X]

## Go/No-Go Input
**Question:** [What does go/no-go gate ask this spike?]  
**Answer:** ☑️ PASS / ⚠️ CAUTION / ❌ FAIL  
**Rationale:** [Why?]

## Risks Discovered
| Risk | Probability | Mitigation |
|---|---|---|
| [Example] | HIGH | [Action] |
```

---

## Daily Cadence (Mon–Fri, Week 0)

- **09:00 UTC:** You wake up, check spike status
- **09:15 UTC:** Daily standup with yourself (or note in spike doc)
  - Yesterday: [progress]
  - Today: [plan]
  - Blocker: [any issue?]
- **16:00 UTC:** EOD checkpoint (update spike doc)
- **Thursday:** Review week's findings (Friday report incoming)
- **Friday EOD:** Submit spike report (template filled)

---

## Escalation (When Stuck)

**If blocker > 2 hours:**
1. Document the blocker (what exactly is stuck?)
2. Pause the spike (don't waste time grinding)
3. Reassess: Is this spike still worth it? Or pivot?
4. Decision: Continue, modify scope, or abort spike

**Example:**
- Day 2 morning: Spike 1 discovers test setup is broken
- Investigation: 2+ hours trying to fix tests
- Decision: Skip test-heavy file, pick simpler file instead
- Continue: Measure velocity on simpler file, extrapolate with **safety factor**

---

## Friday Spike Report Checklist

By EOD Friday 2026-09-06, all 5 spike reports MUST include:

- [ ] Findings (2–3 bullets)
- [ ] Success criteria status (all 3+ checked or noted)
- [ ] Go/No-Go input (PASS / CAUTION / FAIL)
- [ ] Rationale (why PASS or why FAIL?)
- [ ] Risks discovered (any surprises?)
- [ ] Time log (how many hours per day?)
- [ ] Recommendation to leadership (GO or NO-GO?)

**If ANY spike is incomplete:** Gate cannot proceed, defer to Monday 09-16

---

## Go/No-Go Gate (Friday 14:00 UTC)

You (Shumway) fill in `docs/PHASE1_GO_NO_GO_GATE_TEMPLATE.md`:

- Spike 1 velocity: [X] h/file
- Spike 2 recovery: [Y] hours
- Spike 3 drops: [0] events (MUST be 0)
- Spike 4 dimensions: [4] defined
- Spike 5 backups: [trained]

**Decision:** GO ✅ / NO-GO ❌ / CONDITIONAL ⚠️

---

**Orientation Complete.** You're ready for Spike 1 Monday 09:00 UTC.
