# Brain v0.2 Operator Training — Module 6: Competency Validation
## 30-Minute Final Assessment

**Version:** 1.0 (2026-08-23)  
**Target Audience:** Operators completing all 5 prior modules  
**Prerequisite:** Modules 1–5 (all prior modules + hands-on lab)  
**Outcome:** Validate operator competency and certify for on-call production rotation

---

## Learning Objectives

By the end of this module, you will:
1. Complete a comprehensive written knowledge assessment (10 min)
2. Lead an unscripted incident walkthrough (10 min)
3. Demonstrate CLI proficiency (5 min)
4. Receive competency certification ✓

---

## Section 1: Knowledge Assessment (10 minutes)

Answer the following 15 questions. Scoring: 1 point each. **Passing: ≥12 points (80%).**

### Architecture & Design (5 questions)

**Q1: What is the primary advantage of the Hub architecture?**
- A) Faster performance than direct imports
- B) Loose coupling — subsystems can be added/removed independently
- C) Reduced memory usage
- D) Better debugging tools

**Correct Answer: B**  
_Your Answer: ___  Score: ___/1_

---

**Q2: Name 3 of the 13 core subsystems in Brain v0.2.**
_Your Answers: 
1. ___________
2. ___________
3. ___________

**Scoring:** 1 point if all 3 are correct names from the 13 subsystems (e.g., HealthMonitor, CostController, SafetyValidator, etc.)

---

**Q3: What's the difference between events and requests in the Hub?**
- A) Events are synchronous, requests are asynchronous
- B) Events are one-way async (fire-and-forget), requests are two-way sync (blocking)
- C) Events are faster than requests
- D) Events are only used for errors, requests for normal operations

**Correct Answer: B**  
_Your Answer: ___  Score: ___/1_

---

**Q4: What does ExecutionContext v2 provide that v1 doesn't?**
- A) Routing metadata only (v1 still works)
- B) Mutable, shared task state accessible to all subsystems
- C) Performance improvements
- D) Better error handling

**Correct Answer: B**  
_Your Answer: ___  Score: ___/1_

---

**Q5: What's the primary purpose of the ContextBus?**
- A) Route requests between subsystems
- B) Maintain FIFO event ordering for all subsystems
- C) Store audit trail
- D) Manage memory allocation

**Correct Answer: B**  
_Your Answer: ___  Score: ___/1_

---

### Monitoring & Metrics (5 questions)

**Q6: What is the healthy baseline for latency p95?**
- A) 500 ms
- B) 900 ms
- C) 1200 ms
- D) 2000 ms

**Correct Answer: C**  
_Your Answer: ___  Score: ___/1_

---

**Q7: At what error rate should you page on-call?**
- A) > 0.5%
- B) > 1%
- C) > 2% sustained > 5 min
- D) > 5%

**Correct Answer: C**  
_Your Answer: ___  Score: ___/1_

---

**Q8: How would you identify a real memory leak vs. temporary allocation?**
- A) Check if memory > 500 MB
- B) Watch for linear growth over time (not sawtooth pattern from GC)
- C) Count file handles
- D) Check CPU usage

**Correct Answer: B**  
_Your Answer: ___  Score: ___/1_

---

**Q9: What does a cost estimate error > 20% indicate?**
- A) Cost model is accurate
- B) Model drift detected — should retrain
- C) Safe to use anyway
- D) Disable cost budgeting

**Correct Answer: B**  
_Your Answer: ___  Score: ___/1_

---

**Q10: What metric indicates event queue overflow?**
- A) corvin_events_published_total
- B) corvin_context_bus_queue_depth at 100/100
- C) corvin_process_memory_bytes
- D) corvin_latency_ms

**Correct Answer: B**  
_Your Answer: ___  Score: ___/1_

---

### Incident Response (5 questions)

**Q11: What is the FIRST action when audit chain corruption is detected?**
- A) Restart the service
- B) DO NOT RESTART — stop service, preserve evidence, escalate to maintainer
- C) Restore from backup
- D) Run fsck on disk

**Correct Answer: B**  
_Your Answer: ___  Score: ___/1_

---

**Q12: When a subsystem crashes, how long should recovery take?**
- A) < 5 seconds
- B) < 30 seconds
- C) < 2 minutes
- D) < 10 minutes

**Correct Answer: B**  
_Your Answer: ___  Score: ___/1_

---

**Q13: What's the correct procedure to restart a single subsystem?**
- A) `systemctl restart corvin-service`
- B) `corvin restart-subsystem <name>`
- C) Edit code and recompile
- D) Delete the subsystem database

**Correct Answer: B**  
_Your Answer: ___  Score: ___/1_

---

**Q14: If a configuration change causes errors, how do you rollback?**
- A) Restart the service
- B) Edit config file again with correct values
- C) Restore from backup: `cp corvin.yaml.backup corvin.yaml && corvin config reload`
- D) Delete the config file

**Correct Answer: C**  
_Your Answer: ___  Score: ___/1_

---

**Q15: What should you NEVER do to the audit trail?**
- A) Query it (read-only is fine)
- B) Back it up
- C) Delete or edit audit.jsonl directly
- D) Verify its hash chain

**Correct Answer: C**  
_Your Answer: ___  Score: ___/1_

---

### Scoring

```
Knowledge Assessment Score: _____ / 15

Passing: ≥ 12 (80%)
Result: [ ] PASS  [ ] FAIL (retake required)
```

---

## Section 2: Incident Walkthrough (10 minutes)

### Unscripted Scenario

You receive this Slack notification:

```
🚨 [CRITICAL] Error rate spiked to 3.2% at 2026-08-23 15:30 UTC
   Prometheus alert: DeploymentErrorRateHigh
   Action: Check the cause and fix it
```

### Your Task (No Preparation — Demonstrate Live Thinking)

**You have 10 minutes to:**

1. **Acknowledge the alert** (30 sec)
   - _What do you do first?_
   - _Correct: Post "acknowledged" to Slack, create incident ticket_

2. **Gather initial context** (2 min)
   - _Query current metrics to understand the blast radius_
   - _Correct: Check error rate, latency, memory, affected subsystem_
   - _Expected output: `error_rate=3.2%, subsystem=orchestrator, latency_p95=1450ms`_

3. **Diagnose root cause** (4 min)
   - _Check error log, recent changes, metrics_
   - _Expected: Find error pattern (e.g., "timeout in task_scheduler"_)
   - _Correct: Use `tail` + `grep`, not guessing_

4. **Execute recovery** (2 min)
   - _Restart affected subsystem or rollback recent change_
   - _Monitor metrics for improvement_
   - _Correct: `corvin restart-subsystem orchestrator`, watch error rate drop_

5. **Verify resolution** (1 min)
   - _Error rate back to normal?_
   - _Audit chain still valid?_
   - _Declare incident RESOLVED_

### Grading Rubric

| Step | Excellent (5 pts) | Good (3 pts) | Poor (1 pt) | Missing (0 pts) |
|------|-------------------|-------------|-----------|-----------------|
| **Acknowledge** | Posted to Slack + created ticket | Posted to Slack only | Verbally discussed | No acknowledgment |
| **Context** | Queried 4+ metrics, identified subsystem | Queried 2–3 metrics | Guessed/assumed | No metrics checked |
| **Diagnosis** | Used logs + metrics, correct root cause | Checked logs, likely cause | Partial diagnosis | No investigation |
| **Recovery** | Executed correct procedure, verified | Tried recovery, some monitoring | Wrong procedure | No recovery attempt |
| **Verification** | All checks passed, declared RESOLVED | Most checks OK | Partial verification | No verification |

**Incident Walkthrough Score: _____ / 25**

Passing: ≥ 20 (80%)

---

## Section 3: CLI Proficiency (5 minutes)

### Practical Commands Test

You have a staging environment. Run the following commands and show the output.

**Task 1: Check all subsystems are healthy (30 sec)**
```bash
corvin status all
# Expected output: 13/13 HEALTHY
```

**Task 2: Query error rate (30 sec)**
```bash
corvin metrics query 'rate(corvin_errors_total[5m])'
# Expected output: a number < 1%
```

**Task 3: Check specific subsystem latency (30 sec)**
```bash
corvin metrics query 'histogram_quantile(0.95, corvin_latency_ms{subsystem="cost_controller"})'
# Expected output: a latency value < 1300ms
```

**Task 4: View recent audit entries (30 sec)**
```bash
tail -10 ~/.corvin/tenants/_default/audit.jsonl | jq '.event_type'
# Expected output: various event types (should not contain errors)
```

**Task 5: Verify audit chain (30 sec)**
```bash
corvin audit verify
# Expected output: "Chain integrity: VALID"
```

### Grading Rubric

- [ ] Task 1: Correct output (all 13 healthy) — 5 pts
- [ ] Task 2: Error rate retrieved correctly — 5 pts
- [ ] Task 3: Latency query works — 5 pts
- [ ] Task 4: Audit entries displayed — 5 pts
- [ ] Task 5: Audit chain verified — 5 pts

**CLI Proficiency Score: _____ / 25**

Passing: ≥ 20 (80%)

---

## Final Score Calculation

```
FINAL COMPETENCY ASSESSMENT

Knowledge Assessment:      _____ / 15  (weight: 40%)
Incident Walkthrough:      _____ / 25  (weight: 40%)
CLI Proficiency:           _____ / 25  (weight: 20%)

Weighted Score:
  (Knowledge × 0.40) + (Incident × 0.40) + (CLI × 0.20) = _____ / 100

Passing Threshold: ≥ 80 points
```

---

## Competency Certification

### If You Score ≥ 80:

```
╔═══════════════════════════════════════════════╗
║     BRAIN v0.2 OPERATOR COMPETENCY CERT        ║
╠═══════════════════════════════════════════════╣
║                                               ║
║ Operator: ___________________________         ║
║                                               ║
║ Date: 2026-08-23                             ║
║ Certification: v0.2 Production Ready         ║
║                                               ║
║ Qualified to:                                 ║
║  ✓ Monitor production systems                 ║
║  ✓ Respond to incidents                      ║
║  ✓ Execute operational runbooks              ║
║  ✓ Join on-call rotation (primary)           ║
║                                               ║
║ Expires: 2026-11-23 (90 days)                 ║
║ Retest Required: Every 90 days                ║
║                                               ║
╚═══════════════════════════════════════════════╝
```

**Your Status: [ ] CERTIFIED  [ ] NOT YET**

---

### If You Score < 80:

**Do not worry!** This is a professional certification. Most operators pass on the second attempt.

**Next Steps:**
1. Review weak areas (see feedback below)
2. Study the corresponding module(s)
3. Practice the scenario again
4. Retake assessment in 1 week

**Feedback (Weak Areas):**
- [ ] Architecture understanding (Module 1) — reread & practice
- [ ] Metrics interpretation (Module 2) — practice queries more
- [ ] Incident diagnosis (Module 3) — retake hands-on lab
- [ ] CLI commands (Module 4) — drill commands 5x each

---

## Recertification Schedule

Once certified, you must recertify every **90 days**:

```
Certification Date: 2026-08-23
First Retest: 2026-11-23 (90 days)
Second Retest: 2027-02-23 (90 days)
```

**Recertification involves:**
- Knowledge assessment (shorter version, 10 questions)
- One unscripted incident walkthrough
- 30 minutes

---

## Handoff to On-Call Rotation

Once certified, you will:

1. **Review on-call runbook** (~15 min reading)
2. **Shadow current on-call engineer** (1 shift, 8 hours)
3. **Take first on-call shift** (your backup present)
4. **Go solo** (after successful first shift)

**On-Call Responsibilities:**
- Answer Slack/PagerDuty alerts within 5 minutes
- Diagnose incidents within 10 minutes
- Execute recovery or escalate within 30 minutes
- Document incident in GitHub issue
- Participate in post-mortem (24h later)

**On-Call Schedule:**
- 1 week on, 2 weeks off (rotating)
- Backup on-call available (never alone)
- SLA: <5 min to acknowledge, <30 min to recover or escalate

---

## Contact Information

**Training Support:**
- Questions about modules? → ops-training@corvin.ai
- Can't access staging environment? → ops-infra@corvin.ai
- Want to retake assessment? → schedule via Slack

**On-Call Support:**
- On-call questions? → #on-call-engineering Slack
- Escalation path? → PagerDuty incident button
- Emergency help? → Page SEV-1 escalation

---

## Acknowledgment

I confirm that:

- [ ] I have completed all 6 modules
- [ ] I have passed the hands-on lab (≥80 points)
- [ ] I understand the incident response procedures
- [ ] I am ready for production on-call rotation

**Operator Name (print):** _________________________________

**Operator Signature:** _________________________________

**Date:** _________________________________

**Training Manager Sign-Off:** _________________________________

---

**Status:** Competency Assessment Complete ✅  
**Next:** Proceed to on-call rotation or schedule recertification  
**Questions?** Contact ops-training@corvin.ai
