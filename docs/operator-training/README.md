# Brain v0.2 Operator Training Program
## Complete Curriculum for Production Operations

**Version:** 1.0 (2026-08-23)  
**Target Audience:** All production operators, on-call engineers, SREs  
**Total Duration:** ~5 hours (spread over 1–2 weeks)  
**Success Rate Target:** >95% operator certification  
**Maintenance:** Quarterly refresher (optional)

---

## Overview

This training program certifies operators to manage CorvinOS Brain v0.2 in production. It covers:

- **Architecture:** How Brain v0.2 works, the Hub design, 13 core subsystems
- **Monitoring:** Reading metrics, setting up alerts, interpreting dashboards
- **Incident Response:** Diagnosing problems, recovering from crashes, escalating issues
- **Operations:** Startup, shutdown, configuration, backup & restore, deployment
- **Hands-On Lab:** 3 real production scenarios (crash, leak, overflow)
- **Competency Assessment:** Final validation before on-call rotation

---

## Training Path (Recommended)

### Week 1: Knowledge Foundation (2 hours)

| Module | Duration | Topics | Status |
|--------|----------|--------|--------|
| **Module 1: Architecture** | 45 min | Hub design, 13 subsystems, event-driven comms | → [Read Now](MODULE-1-ARCHITECTURE.md) |
| **Module 2: Monitoring** | 45 min | Dashboard, 10 critical metrics, alerting rules | → [Read Now](MODULE-2-MONITORING.md) |

**Checkpoint:** You should understand how Brain v0.2 works and how to read metrics.

### Week 2: Hands-On Training (3 hours)

| Module | Duration | Topics | Status |
|--------|----------|--------|--------|
| **Module 3: Incident Response** | 60 min | 4 real scenarios (crash, leak, corruption, overflow) | → [Read Now](MODULE-3-INCIDENT-RESPONSE.md) |
| **Module 4: Runbooks** | 45 min | Startup, shutdown, config, backup, deployment | → [Read Now](MODULE-4-RUNBOOKS.md) |
| **Module 5: Hands-On Lab** | 60 min | 3 simulated incidents, practice responses | → [Do Lab](MODULE-5-HANDS-ON-LAB.md) |

**Checkpoint:** You should pass the lab with ≥80 points.

### Week 3: Certification (30 min)

| Module | Duration | Topics | Status |
|--------|----------|--------|--------|
| **Module 6: Competency Validation** | 30 min | Knowledge test, incident walkthrough, CLI test | → [Take Assessment](MODULE-6-COMPETENCY-VALIDATION.md) |

**Checkpoint:** You should score ≥80 points to be certified.

---

## Quick Start (For Impatient Operators)

**If you have 30 minutes right now:**

```bash
# 1. Read the summary below (10 min)
# 2. Watch the demo video (5 min)
#    https://corvin-training.internal/demo-v0.2.mp4
# 3. Try 3 commands (5 min)
corvin status all          # See all subsystems
corvin metrics query 'rate(corvin_errors_total[5m])'  # Check error rate
corvin health check        # Verify everything is healthy
# 4. Schedule full training (1–2 weeks)
```

---

## 30-Second Brain v0.2 Summary

**What is it?**
Brain v0.2 is an autonomous orchestration system with 13 specialized subsystems coordinated through a central Hub.

**What does it do?**
- Selects recovery strategies when tasks fail
- Tracks costs and enforces budgets
- Validates policies and detects anomalies
- Learns from outcomes, grades strategies, promotes tools & skills

**Key Subsystems:**
```
Foundation:     HealthMonitor, ContextBridge, LoopEngineer, Orchestrator
Learning:       LearningEngine, CostController, SafetyValidator, StrategyAdvisor
Generation:     ToolForgeSubsystem, SkillForgeSubsystem
Coordination:   ContextAPI, ContextBus, Hub RequestRouter
```

**How to tell if it's healthy:**
- Error rate < 1% ✓
- Latency p95 < 1300ms ✓
- Memory stable < 600MB ✓
- All 13 subsystems online ✓
- Audit chain valid ✓

**What can go wrong?**
1. **Subsystem crashes** → restart (5 min recovery)
2. **Memory leaks** → database cleanup (10 min recovery)
3. **Event queue overflow** → disable slow subscriber (5 min recovery)
4. **Audit chain corruption** → escalate to maintainer (critical, needs deep fix)

**What's your job?**
- Monitor metrics, respond to alerts
- Diagnose problems using logs & metrics
- Execute recovery procedures
- Escalate when needed
- Keep the system running 24/7

---

## Module-by-Module Summary

### Module 1: Architecture (45 min)

**You'll learn:**
- What the Hub does and why it's better than direct imports
- The 13 subsystems and their responsibilities
- Two communication patterns: Events (async) and Requests (sync)
- How to trace a request from entry to response
- Common failure modes and what they mean

**Key Takeaway:** Brain v0.2 is a loosely-coupled system of specialist subsystems. Each problem is isolated, failure of one doesn't take down others.

**[→ Read Module 1](MODULE-1-ARCHITECTURE.md)**

---

### Module 2: Monitoring (45 min)

**You'll learn:**
- How to access the monitoring dashboard
- 10 critical metrics: error rate, latency, memory, throughput, cost, strategy success, policy violations, subsystem health, tool reuse, and queue depth
- Healthy ranges for each metric
- How to set up Prometheus alerts
- How to read a live dashboard snapshot

**Key Takeaway:** Metrics are your eyes into the system. Always check metrics before restarting anything.

**[→ Read Module 2](MODULE-2-MONITORING.md)**

---

### Module 3: Incident Response (60 min)

**You'll learn:**
- A 5-phase incident response flowchart
- Scenario 1: Subsystem crash (restart + verify)
- Scenario 2: Memory leak (identify + cleanup)
- Scenario 3: Audit chain corruption (escalate, don't restart!)
- Scenario 4: Event queue overflow (disable slow subscriber)
- When to rollback a deployment
- When to escalate vs. recover

**Key Takeaway:** Most incidents are resolved by restarting a subsystem. The only critical one is audit corruption — never restart in that case, escalate immediately.

**[→ Read Module 3](MODULE-3-INCIDENT-RESPONSE.md)**

---

### Module 4: Runbooks (45 min)

**You'll learn:**
- Startup procedure (pre-flight → bootstrap → health check → subsystems → smoke test)
- Graceful shutdown (drain tasks → stop accepting → stop service)
- Configuration changes (backup → edit → validate → reload)
- Backup & restore (daily snapshots, recovery procedures)
- Deployment procedure (canary 10% → expand 25% → full)

**Key Takeaway:** There's a repeatable procedure for everything. Follow it exactly, don't improvise.

**[→ Read Module 4](MODULE-4-RUNBOOKS.md)**

---

### Module 5: Hands-On Lab (60 min)

**You'll do:**
1. Simulate a subsystem crash, diagnose it, restart it, verify recovery
2. Simulate a memory leak, confirm it's real (not GC), clean it up, verify stability
3. Simulate an event queue overflow, find the bottleneck, fix it, verify recovery

**You must score ≥80 points to pass.**

**Key Takeaway:** These are real production scenarios. Once you've solved them in the lab, you can handle them in production.

**[→ Do Lab](MODULE-5-HANDS-ON-LAB.md)**

---

### Module 6: Competency Validation (30 min)

**You'll complete:**
1. 15-question knowledge assessment (80% pass = 12+ correct)
2. Unscripted incident walkthrough (solve a real problem on the spot)
3. CLI proficiency check (5 key commands)

**You must score ≥80 points to be certified.**

**Key Takeaway:** This certification means you're ready for on-call production rotation. Take it seriously.

**[→ Take Assessment](MODULE-6-COMPETENCY-VALIDATION.md)**

---

## Success Criteria

### To Complete Training

✓ Read all 6 modules  
✓ Complete hands-on lab with ≥80 points  
✓ Pass competency validation with ≥80 points  
✓ Sign off on acknowledgment  

### To Join On-Call Rotation

✓ Training completed and certified  
✓ Shadow current on-call engineer (1 shift)  
✓ Take first on-call shift with backup present  
✓ Successfully handle 1 incident (defined as: diagnose + recover or escalate in <30 min)  

### To Go Solo (No Backup Required)

✓ 3+ successful on-call shifts  
✓ Handled ≥2 distinct incident types  
✓ Demonstrated correct escalation procedures  

---

## FAQ

**Q: How long will this take?**  
A: ~5 hours over 1–2 weeks. Spread it out, don't cram.

**Q: Can I skip modules?**  
A: No. All 6 modules are required. They build on each other.

**Q: What if I fail the lab?**  
A: That's OK! Most operators retake it once. You have unlimited attempts. Schedule another session in 1 week.

**Q: What if I fail the final assessment?**  
A: Retake it in 1 week. Focus on your weak areas. Most operators pass the second time.

**Q: I have production experience elsewhere (AWS/GCP/etc). Do I still need all modules?**  
A: Yes. Brain v0.2 is unique. General ops experience helps, but you need to learn the specifics. You'll move faster through modules 1–2, then focus on 3–5.

**Q: How often do I need to recertify?**  
A: Every 90 days. Recertification is shorter (1 knowledge test, 1 incident walk, 30 min total).

**Q: I'm on vacation for 2 weeks. Do I need to do training before I leave?**  
A: Ideally yes, but not mandatory. Do it when you get back. Training is ongoing.

**Q: What if Brain v0.2 gets updated while I'm training?**  
A: Minor updates don't invalidate the training. If there's a major change (new subsystem, protocol change), we'll issue an update to the modules. You'll be notified.

**Q: Who do I contact if I'm stuck?**  
A: ops-training@corvin.ai for training questions, ops-team@corvin.ai for environment issues.

---

## Time Estimates

| Module | Time | Format | Effort |
|--------|------|--------|--------|
| Module 1: Architecture | 45 min | Reading + diagrams | Medium |
| Module 2: Monitoring | 45 min | Reading + metric examples | Medium |
| Module 3: Incident Response | 60 min | Reading + scenarios | High |
| Module 4: Runbooks | 45 min | Reading + copy-paste procedures | Low |
| Module 5: Hands-On Lab | 60 min | Hands-on simulation | High |
| Module 6: Assessment | 30 min | Test + walkthrough | High |
| **Total** | **~5 hours** | **Spread over 1–2 weeks** | |

---

## Resource Links

### Internal Wikis & Docs
- [Release Notes v0.2-rc1](../RELEASE_NOTES_v0.2-rc1.md)
- [Deployment Safety Checklist](../deployment/BRAIN_V0.2_DEPLOYMENT_SAFETY_CHECKLIST.md)
- ADR-0347: Brain Hub Architecture (see Corvin-ADR repo)
- ADR-0373: Cost Optimization (see Corvin-ADR repo)
- ADR-0374: Safety Gate Hardening (see Corvin-ADR repo)

### Tools & Dashboards
- [Prometheus Dashboard](http://localhost:9090/graph)
- [CLI Tool](../../../core/orchestration/brain.py) (`corvin` command)
- [Staging Environment](https://github.com/corvinOS/staging)

### Training Support
- Email: ops-training@corvin.ai
- Slack: #ops-training
- Office Hours: Tuesdays 2–3 PM UTC

---

## Operator Onboarding Checklist

**Before Training:**
- [ ] Ops account created + SSH access
- [ ] Staging environment access verified
- [ ] Slack #ops-training + #on-call-engineering channels joined
- [ ] PagerDuty account configured

**During Training:**
- [ ] Module 1–2 read (1 week)
- [ ] Module 3–4 read (1 week)
- [ ] Module 5 lab completed (≥80 pts)
- [ ] Module 6 assessment passed (≥80 pts)

**After Training:**
- [ ] Certification signed off
- [ ] On-call runbook reviewed
- [ ] 1 shadowing shift completed
- [ ] First on-call shift assigned (with backup)
- [ ] Added to on-call rotation

---

## Training Metrics

We track these to improve the program:

- **Completion Rate:** Target >95% (measure: who completes all 6 modules)
- **Pass Rate:** Target >90% (measure: who scores ≥80 on lab + assessment)
- **Time to First Incident:** Target <7 days on-call
- **MTTR (Mean Time To Resolve):** Target <15 min for restart-able incidents
- **Escalation Accuracy:** Target >95% (measure: correct escalation decisions)

---

## Version History

| Version | Date | Changes |
|---------|------|---------|
| 1.0 | 2026-08-23 | Initial release for Brain v0.2-rc1 |
| TBD | TBD | Updates for v0.2 stable |
| TBD | TBD | v0.3 modules (voice guidance, optimizations) |

---

## Feedback & Improvements

Found an error in the training? Have a suggestion?

→ File a GitHub issue: [Training Improvements](https://github.com/corvinOS/corvinOS/issues/new?template=training-feedback.md)

→ Email feedback to: ops-training@corvin.ai

---

## Legal & Compliance

This training program is proprietary to CorvinOS. All materials are confidential.

By completing this training, you agree to:
- Keep materials confidential
- Use knowledge only for CorvinOS production support
- Escalate security issues immediately
- Report incidents accurately in audit trail

---

**Ready to get started?**

**→ [Begin Module 1: Architecture](MODULE-1-ARCHITECTURE.md)**

---

**Questions?** Contact ops-training@corvin.ai  
**Status:** Training program v1.0, ready for deployment  
**Last Updated:** 2026-08-23
