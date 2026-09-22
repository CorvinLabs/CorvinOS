# CorvinOS Training Materials

**Version:** 1.0  
**Date:** 2026-09-22  
**Purpose:** Reference materials for operator training  
**Audience:** New operators, team leads, administrators

---

## Table of Contents

1. [Operator Training Slides Outline](#operator-training-slides-outline)
2. [Daily Operations Checklist](#daily-operations-checklist)
3. [Quick Reference Cards](#quick-reference-cards)

---

## Operator Training Slides Outline

**Format:** PowerPoint (20 slides) + Speaker Notes  
**Duration:** 45 minutes presentation + 15 minutes Q&A  
**Audience:** New CorvinOS operators

### Slide Deck

**1. Title Slide**
- CorvinOS Operator Training
- Date
- Version
- Instructor

**2. What is CorvinOS?**
- Overview: An intelligent operating system for automating tasks
- Key capabilities: Skills, learning loop, cost optimization, audit trail
- Benefits: Faster task execution, lower costs, better decisions
- Use cases: Quick fixes, cost optimization, workflow automation

**3. Architecture Overview**
- Diagram: Skills → Execution Engine → Learning Loop → Audit Trail
- Components: Console UI, API server, Learning processor, Plugin system
- Data flow: Request → Skill → Feedback → Optimization
- Multi-tenant: One CorvinOS, many operator teams

**4. Installation & Setup**
- Prerequisites: Python 3.10+, Git, 4GB RAM
- Installation steps: Clone → Virtual env → Bootstrap
- Verification: Health checks, audit chain verification
- First skill installation

**5. Core Concepts: Skills**
- What's a skill? Reusable, learnable, autonomous decision-making module
- Built-in skills: quick_fix, cost_optimizer, workflow_generator
- Installing skills: `corvin skill install <id>`
- Executing skills: Web UI or API

**6. Core Concepts: Learning Loop**
- What's learning? Feedback → Optimization → Better decisions
- Feedback types: Outcome (correct/incorrect), Preference, Confidence
- How learning works: Collect → Analyze → Tune → Deploy
- Viewing improvements: Confidence scores, success rate

**7. Core Concepts: Cost Tracking**
- What's cost? API calls, compute, data transfer
- Tracking: Per-skill, per-user, per-tenant
- Optimization: Skills learn to use cheaper models
- Reporting: Daily/weekly cost dashboards

**8. The Console UI**
- Dashboard: Health, recent activity, alerts
- Skills panel: Install, execute, view status, feedback
- Learning panel: View feedback, optimization history
- Settings panel: Configuration, API keys, learning preferences

**9. Working with the API**
- REST endpoints: `/v1/health`, `/v1/skills`, `/v1/learning`
- Authentication: Bearer token (API key)
- Common calls: Execute skill, send feedback, view audit trail
- Error handling: Status codes, error messages

**10. Skill Execution Walkthrough**
- Step 1: Select skill
- Step 2: Provide input
- Step 3: Execution happens (0.1-2 seconds)
- Step 4: Review output
- Step 5: Send feedback
- Demo: Actually run a skill live

**11. Feedback Loop Walkthrough**
- Step 1: Execute skill
- Step 2: Check result (correct/incorrect?)
- Step 3: Send feedback (outcome signal)
- Step 4: System learns
- Step 5: Next execution is better
- Demo: Show confidence improving

**12. The Audit Trail**
- What: Immutable, hash-chained record of all operations
- Why: Compliance, transparency, debugging
- How to view: `corvin audit trail`
- Verification: `corvin audit verify-chain`
- Real example: Show actual audit events

**13. Monitoring & Health Checks**
- Daily checks: `corvin verify --all`
- Health dashboard: https://status.corvinlabs.io
- Alert channels: Slack `#corvinOS-alerts`
- Response procedures: SLA-driven escalation

**14. Incident Response**
- P1 (Critical): API down, audit broken → 5 min SLA
- P2 (High): Partial outage → 15 min SLA
- P3 (Medium): UI issues → 1 hour SLA
- P4 (Low): Minor issues → 4 hour SLA
- Escalation: Follow runbook, page tech lead if needed

**15. Troubleshooting Common Issues**
- "API connection refused" → Check if server is running
- "Skill timeout" → Restart skill, increase timeout
- "Learning not improving" → Check feedback consistency
- "Audit chain broken" → Run repair or escalate
- More: See `PHASE1_TROUBLESHOOTING.md`

**16. Best Practices**
- ✅ Always verify audit chain daily
- ✅ Send feedback for every execution (helps learning)
- ✅ Monitor cost dashboards (optimize model selection)
- ✅ Review learning status weekly
- ❌ Don't manually edit audit.jsonl
- ❌ Don't disable compliance checks
- ❌ Don't skip feedback (learning won't improve)
- ❌ Don't ignore alerts (escalate if unsure)

**17. Multi-Tenant Setup**
- What: Multiple operator teams on one CorvinOS
- Configuration: `CORVIN_TENANT_ID` environment variable
- Isolation: Audit trail, settings, learning per tenant
- Best practices: Clear naming, monitor all tenants

**18. Advanced: Custom Skills**
- Building a skill: Python code + skill manifest
- Registering: `corvin skill register <skill.json>`
- Testing: Unit tests + E2E tests
- Publishing: To marketplace for others to use
- (Optional: Link to Skill Development Guide)

**19. Advanced: Monitoring & Alerting**
- Setting up alerts: Slack integration, email, webhooks
- Alert types: High error rate, high latency, audit issues
- Dashboards: Custom Grafana dashboards
- Logging: Integrate with ELK / DataDog

**20. Q&A & Wrap-Up**
- Review key takeaways
- Common questions (pre-prepared)
- Resources: Setup guide, API reference, troubleshooting guide
- Next steps: First deployment, hands-on exercise
- Contact: Support channel, escalation path

### Speaker Notes (for each slide)

[Detailed speaker notes would go here for each slide]

---

## Daily Operations Checklist

**For printing/laminating (1-page PDF)**

### Morning (5 minutes)

- [ ] Check system health: `corvin verify --all`
- [ ] Review alerts from last 24h in Slack
- [ ] Verify API is responding: `curl http://localhost:8765/v1/health`
- [ ] Check audit chain: `corvin audit verify-chain --tenant=_default`
- [ ] Update personal Slack status: "✅ On-call: <date>"

### During Day (Every 4 hours)

- [ ] Monitor Slack `#corvinOS-alerts` for new incidents
- [ ] Run health check: `corvin verify --all`
- [ ] Check learning loop: `corvin learning status`
- [ ] Review recent errors: `journalctl --user -u corvin-webui -n 50`
- [ ] Post status to on-call channel (if required)

### Evening (5 minutes)

- [ ] Prepare handoff to next on-call
- [ ] Document any incidents or issues
- [ ] Create tickets for follow-up
- [ ] Clear personal Slack status
- [ ] Confirm next on-call is ready

### Weekly (30 minutes)

- [ ] Review all incidents from week
- [ ] Update runbooks based on lessons learned
- [ ] Check cost trends: `corvin metrics view --timerange=7d`
- [ ] Review skill performance: `corvin skill status --all`
- [ ] Team meeting: Discuss issues and improvements

### Monthly (1 hour)

- [ ] Full health audit: All systems, all metrics
- [ ] Capacity planning: Usage trends, growth
- [ ] Skills review: Performance, cost, learning
- [ ] Incident review: P1/P2 incident root causes
- [ ] Documentation: Update guides if needed
- [ ] Team training: New procedures, tools, skills

---

## Quick Reference Cards

### Card 1: Essential Commands

**For printing/laminating (one-page front/back)**

```
CORVINIOS QUICK REFERENCE

Health & Verification:
  corvin verify --all
  corvin audit verify-chain --tenant=_default
  curl http://localhost:8765/v1/health

Skills:
  corvin skill list
  corvin skill install <id>
  corvin skill execute <id> --input="..."
  corvin skill status <id>

Learning:
  corvin learning status
  corvin learning enable
  corvin learning view-feedback --skill=<id>

API (curl examples):
  curl -H "Authorization: Bearer $ANTHROPIC_API_KEY" \
    http://localhost:8765/v1/health | jq .
  curl -H "Authorization: Bearer $ANTHROPIC_API_KEY" \
    http://localhost:8765/v1/skills | jq .

Logs:
  journalctl --user -u corvin-webui -n 50
  journalctl --user -u corvin-learning-processor -n 50

Restart Services:
  systemctl --user restart corvin-webui
  systemctl --user restart corvin-learning-processor
```

### Card 2: Troubleshooting Quick Fix

**For printing/laminating (one-page front/back)**

```
INCIDENT QUICK FIX FLOWCHART

Is the API responding?
  NO  → systemctl --user restart corvin-webui
  YES → Continue

Is the audit chain broken?
  YES → corvin audit repair --tenant=_default
  NO  → Continue

Is learning processor stuck?
  YES → corvin learning restart
  NO  → Continue

Is a skill timing out?
  YES → Kill skill process, restart
  NO  → Continue

Is the console showing blank?
  YES → Clear browser cache, Ctrl+Shift+R
  NO  → Continue

Is it a P1 incident?
  YES → Page on-call tech lead immediately
  NO  → Continue monitoring

Still broken?
  → Collect diagnostics: corvin diagnostics collect
  → Page tech lead with output
```

### Card 3: Alert Response Guide

**For printing/laminating (one-page front/back)**

```
ALERT RESPONSE GUIDE

P1 - Critical (5 min SLA):
  Action: ACK immediately in Slack
  Do: Follow incident runbook
  If stuck after 5 min: Page tech lead
  If stuck after 15 min: Page CTO

P2 - High (15 min SLA):
  Action: ACK within 15 min
  Do: Investigate, attempt fix
  If stuck after 15 min: Page tech lead
  If stuck after 1.5h: Page manager

P3 - Medium (1 hour SLA):
  Action: Log issue
  Do: Investigate during business hours
  Escalate: If blocking critical users

P4 - Low (4 hour SLA):
  Action: Create ticket
  Do: Fix next shift or business hours
  Escalate: Not needed

Update Status:
  Every 5 min (P1), 15 min (P2), 30 min (P3)
  Format: ✅ ACK / 🔍 Investigating / 🔧 Recovering
```

### Card 4: On-Call Handoff Checklist

**For printing/laminating (one-page front/back)**

```
ON-CALL HANDOFF CHECKLIST

Before You Leave:
  [ ] Create handoff document in Slack
  [ ] Document last 24h incidents
  [ ] List any known issues
  [ ] Mention upcoming maintenance/deploys
  [ ] Tag next on-call + tech lead
  [ ] Answer all Q&A

Next On-Call Should:
  [ ] Read handoff document
  [ ] Run health checks: corvin verify --all
  [ ] Review alert history (last 24h)
  [ ] Test critical API paths
  [ ] Check audit chain verification
  [ ] Set Slack status: "🚨 On-call: <date>"
  [ ] Ask questions if confused
  [ ] Acknowledge in Slack thread

Handoff Ceremony:
  Time: Shift change - 30 min before
  Duration: 20 minutes
  Location: Slack thread or Zoom
  Attendees: Current, Next, Tech Lead
```

---

## Materials to Generate

### PowerPoint Deck

1. **File:** `training/OPERATOR_TRAINING_SLIDES.pptx`
2. **Slides:** 20 (as outlined above)
3. **Format:** Standard 16:9, professional template
4. **Notes:** Speaker notes on each slide
5. **Time:** 45 min presentation + 15 min Q&A

### PDF Checklists

1. **File:** `training/DAILY_OPS_CHECKLIST.pdf`
   - 1-page checklist
   - Laminate for on-call operators
   
2. **File:** `training/QUICK_REFERENCE_COMMANDS.pdf`
   - 1-page command reference
   - Laminate for wall/desk

3. **File:** `training/TROUBLESHOOTING_QUICK_FIX.pdf`
   - 1-page decision tree
   - Laminate for rapid incident response

4. **File:** `training/HANDOFF_CHECKLIST.pdf`
   - 1-page checklist
   - Used at each shift change

### Generated PDFs

To generate PDFs from Markdown:
```bash
# Install markdown-to-pdf tool
npm install -g markdown-pdf

# Generate PDF from this file
markdown-pdf TRAINING_MATERIALS_OUTLINE.md \
  -o training/TRAINING_MATERIALS.pdf
```

Or use an online converter (markdown to PDF).

---

## Next Steps for Operator

1. **Read:** All documents in `docs/onboarding/`
2. **Watch:** Training videos (Stories 4-5, next session)
3. **Practice:** Set up local CorvinOS following PHASE1_SETUP_GUIDE.md
4. **Hands-on:** Execute 5 different skills, send feedback for each
5. **Incident Sim:** Simulate a P2 incident (learning processor stuck)
6. **Shadow:** Observe current on-call for one shift
7. **Go Live:** Take on-call shift with tech lead on standby

---

**Last Updated:** 2026-09-22  
**Version:** 1.0.0  
**Status:** Outline complete; operator should convert slides to PowerPoint and checklists to PDF for printing
