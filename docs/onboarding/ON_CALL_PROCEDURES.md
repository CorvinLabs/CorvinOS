# On-Call Procedures & Escalation

**Version:** 1.0  
**Date:** 2026-09-22  
**Scope:** On-call responsibilities, escalation, communication, shift handoff

---

## Table of Contents

1. [On-Call Responsibilities](#on-call-responsibilities)
2. [Escalation Matrix](#escalation-matrix)
3. [Communication Protocols](#communication-protocols)
4. [Daily Checklist](#daily-checklist)
5. [Shift Handoff](#shift-handoff)
6. [Contact Directory](#contact-directory)

---

## On-Call Responsibilities

### Your Responsibilities

As the on-call operator, you are responsible for:

**Availability:**
- ✅ Be available 24/7 during shift (respond to alerts within SLA)
- ✅ Have phone + laptop accessible
- ✅ Use Do Not Disturb mode responsibly (only during maintenance)
- ✅ Be reachable via Slack, email, phone

**Monitoring:**
- ✅ Monitor alerts via Slack channel: `#corvinOS-alerts`
- ✅ Check dashboard every 4 hours: https://status.corvinlabs.io
- ✅ Review error logs daily: `journalctl --user -u corvin-webui -n 100`
- ✅ Check learning loop health: `corvin learning status`

**Response:**
- ✅ Acknowledge incident within SLA (P1: 5 min, P2: 15 min, P3: 1 h)
- ✅ Follow incident response runbook
- ✅ Update status every 15 minutes (or when status changes)
- ✅ Escalate if you cannot resolve within SLA

**Documentation:**
- ✅ Document all actions taken during incident
- ✅ Collect logs/diagnostics for post-mortem
- ✅ Update runbook based on learnings
- ✅ Create issue for prevention measures

### What You Do NOT Need To Do

- ❌ Wake up someone not on-call unless escalated in escalation matrix
- ❌ Make feature changes (fixes only)
- ❌ Deploy to production without approval
- ❌ Delete data without explicit instruction
- ❌ Bypass compliance/audit checks for "speed"

---

## Escalation Matrix

### P1 - Critical (SLA: 5 minutes)

**Incidents:** Service down, audit chain broken, data loss imminent

```
Minutes 0-5:   Acknowledge incident
                ↓
Minutes 5-10:  Own-call attempts fix
                ↓
If unresolved:  Page on-call tech lead
                ↓
If unresolved:  Page CTO (after 15 min)
                ↓
If unresolved:  Declare SEV-1 incident
                ↓
Actions: Wake up stakeholders, setup war room, document everything
```

**Notification Flow:**
```bash
# Automated (via monitoring)
Slack #corvinOS-alerts → P1: <service> DOWN

# Your action (< 5 min)
:warning: ACK / Investigating

# At 5 min if unresolved
@<on-call-tech-lead> HELP: Cannot resolve <issue>

# At 15 min if unresolved  
@<cto> SEV-1: <service> still down, ETA <X min>
```

---

### P2 - High (SLA: 15 minutes)

**Incidents:** Partial outage, performance degradation, learning loop stuck

```
Minutes 0-15:  Own-call investigates and attempts fix
                ↓
If unresolved:  Page on-call tech lead
                ↓
If unresolved:  Page tech lead manager
                ↓
Actions: Share logs, discuss options, plan next steps
```

**Notification Flow:**
```bash
# At 15 min if unresolved
@<on-call-tech-lead> P2 Escalation: <issue> unresolved
Send: diagnostics + logs via thread
```

---

### P3 - Medium (SLA: 1 hour)

**Incidents:** Console UI issues, single plugin failed, slow skill

```
Hours 0-1:     Own-call investigates
                ↓
If unresolved:  Page on-call tech lead (no rush)
                ↓
Actions: Document for next business day engineer
```

---

### P4 - Low (SLA: 4 hours)

**Incidents:** Documentation typo, minor config issue, non-critical drift

```
Hours 0-4:     Log issue for next shift
                ↓
No paging needed. Fix during business hours.
```

---

## Communication Protocols

### Incident Channels

**Primary:** Slack `#corvinOS-alerts`
- All incident updates go here
- Set topic to: `🚨 INCIDENT: <name> | ETA: <time>`
- Format: Thread-per-incident

**Secondary:** Email (if Slack is down)
- To: `incidents@corvinlabs.io`
- Subject: `[P<N>] <service> <status>`

**War Room:** Video call (P1 only)
- Zoom: https://corvinlabs.zoom.us/incidents
- Join when paged
- Keep lines clear (mute unless speaking)

### Update Frequency

| Severity | Frequency | Format |
|---|---|---|
| P1 | Every 5 minutes | Slack thread (detailed) |
| P2 | Every 15 minutes | Slack thread (summary) |
| P3 | Every 30 minutes | Slack thread (high-level) |
| P4 | Once at end | Slack message (link to issue) |

### Update Template

```
**Time:** HH:MM UTC
**Status:** investigating / recovering / resolved
**Progress:** [========>          ] 60%
**Next action:** <what we're doing>
**ETA:** <estimated recovery time>

Logs: <link to most recent logs>
Issue: <GitHub issue URL>
```

### Status Indicators

Use these in Slack title:

- 🔴 **CRITICAL** — P1, service down, user impact
- 🟠 **MAJOR** — P2, partial outage, degradation
- 🟡 **MINOR** — P3, limited impact, degraded
- ⚪ **RESOLVED** — incident fixed, recovery confirmed

---

## Daily Checklist

### Start of Shift (5 minutes)

```bash
# 1. Check alert history
curl -s https://api.corvinlabs.io/alerts?hours=1 | jq .

# 2. Review last 24h incidents
# (ask previous on-call or check Slack #corvinOS-alerts)

# 3. Test critical paths
curl -X GET http://localhost:8765/v1/health \
  -H "Authorization: Bearer $ANTHROPIC_API_KEY" | jq .

# 4. Verify you can access:
# - Slack (primary comms)
# - GitHub (issues/PRs)
# - Dashboard (https://status.corvinlabs.io)
# - Logs (journalctl)

# 5. Update Slack status
# Profile → Status → "🚨 On-call: <date>"
```

### Every 4 Hours (10 minutes)

```bash
# 1. Check system health
corvin verify --all

# 2. Review recent errors
journalctl --user -u corvin-webui --since=4h | grep -i error | wc -l

# 3. Check learning loop
corvin learning status

# 4. Verify audit chain
corvin audit verify-chain --tenant=_default

# 5. Post update to #corvinOS on-call channel
# "✅ All systems healthy. 4h check passed."
```

### End of Shift (10 minutes)

```bash
# 1. Handoff to next on-call
# See: [Shift Handoff](#shift-handoff)

# 2. Update Slack status
# Profile → Status → clear

# 3. Document any issues
# Create issues for anything unusual
```

---

## Shift Handoff

### Handoff Meeting (30 minutes before shift change)

**Participants:** Current on-call + next on-call + tech lead

**Agenda:**
1. Current status (healthy / issues)
2. Recent incidents (last 24h)
3. Known issues
4. Upcoming maintenance
5. Q&A

**Location:** Slack thread or Zoom call

### Handoff Document

Create a Slack thread with:

```
**Handoff: <date> <current> → <next>**

**Status:** Healthy / Issues
**Alert count (24h):** <N>
**Critical issues:** <list or "none">

**Recent incidents:**
- <incident 1>: <status>
- <incident 2>: <status>

**Known issues:**
- <issue 1>: watching
- <issue 2>: needs fix

**Upcoming:**
- <maintenance 1>: <when>
- <deployment 1>: <when>

**Next on-call checklist:**
- [ ] Read this thread
- [ ] Run health checks
- [ ] Review alert history
- [ ] Test critical paths
- [ ] Ask questions

Handoff signed by: <current on-call>
Acknowledged by: <next on-call>
```

### Running Handoff Ceremony

**Time:** Shift change - 30 minutes
**Duration:** 20 minutes
**Attendees:** 2 on-calls, tech lead
**Recording:** None (just document in thread)

---

## Contact Directory

### Primary Contacts

| Role | Name | Slack | Phone | Email |
|---|---|---|---|---|
| **On-call (Primary)** | TBD | @on-call-primary | +1-XXX-XXXX | oncall@corvinlabs.io |
| **On-call (Tech Lead)** | TBD | @on-call-tech-lead | +1-XXX-XXXX | tech-lead@corvinlabs.io |
| **Engineering Manager** | TBD | @engineering-manager | +1-XXX-XXXX | manager@corvinlabs.io |
| **CTO** | TBD | @cto | +1-XXX-XXXX | cto@corvinlabs.io |

### Escalation Contacts

**For P1 incidents after 15 min:**
- Tech Lead: See above
- CTO: See above

**For security incidents:**
- Security Team: `@security` on Slack or security@corvinlabs.io

**For customer impact:**
- Customer Success: `@customer-success` or support@corvinlabs.io

### External Contacts

| Service | Contact | SLA |
|---|---|---|
| **Anthropic API** | https://status.anthropic.com | Monitor only |
| **GitHub** | https://www.githubstatus.com | Monitor only |
| **AWS** (if used) | https://status.aws.amazon.com | Monitor only |

---

## Tools & Resources

### Monitoring

- **Dashboard:** https://status.corvinlabs.io
- **Alerts:** Slack `#corvinOS-alerts`
- **Logs:** `journalctl --user` (local)

### Documentation

- **Setup Guide:** `docs/onboarding/PHASE1_SETUP_GUIDE.md`
- **API Reference:** `docs/onboarding/PHASE1_API_REFERENCE.md`
- **Troubleshooting:** `docs/onboarding/PHASE1_TROUBLESHOOTING.md`
- **Incident Response:** `docs/onboarding/INCIDENT_RESPONSE_RUNBOOK.md`

### Commands Reference

```bash
# Health checks
corvin verify --all

# Learning status
corvin learning status

# Audit verification
corvin audit verify-chain --tenant=_default

# Skill status
corvin skill status <skill-id>

# Plugin status
corvin plugin status <plugin-id>

# View recent logs
journalctl --user -u corvin-webui -n 100 --no-pager

# Restart services
systemctl --user restart corvin-webui
systemctl --user restart corvin-learning-processor
```

---

## Common Scenarios

### Scenario 1: Alert at 3 AM

```
1. Check Slack #corvinOS-alerts
2. Assess severity (P1? P2?)
3. If P1:
   - Acknowledge immediately: ":warning: ACK / Investigating"
   - Set Slack topic: "🚨 INCIDENT: <name>"
   - Follow incident runbook
   - Alert every 5 minutes
4. If P2:
   - Acknowledge within 15 min
   - Follow incident runbook
   - Alert every 15 min
5. If P3/P4:
   - Log for tomorrow, no rush
```

### Scenario 2: You Can't Resolve Within SLA

```
1. Document what you've tried
2. Collect logs: corvin diagnostics collect
3. Page on-call tech lead with:
   - Issue description
   - Steps taken
   - Diagnostics file
4. Provide context and hand off smoothly
5. Stay available for questions
```

### Scenario 3: System Seems "Weird"

```
1. Check health: corvin verify --all
2. Review logs: journalctl --user -u corvin-webui -n 50
3. Is it an error or a false alarm?
   - If error: follow incident runbook
   - If false alarm: document it
4. Create issue if pattern observed
```

---

## SLA Summary

| Severity | Response | Resolution | Escalate If |
|---|---|---|---|
| P1 | 5 min | 30 min | Unresolved after 15 min |
| P2 | 15 min | 2 hours | Unresolved after 1.5 h |
| P3 | 1 hour | 8 hours | Cannot resolve by EOD |
| P4 | 4 hours | Next day | Can be deferred |

---

## FAQs

**Q: What if I can't reach the tech lead?**
A: Page the CTO directly. This is a critical escalation path.

**Q: Can I make feature changes during on-call?**
A: No. Bug fixes only. Feature work is for business hours.

**Q: What if there's no incident for my whole shift?**
A: Great! Keep monitoring. Run health checks every 4 hours. Document that shift was quiet.

**Q: Should I deploy during an incident?**
A: No. Only if explicitly instructed by tech lead as part of fix.

**Q: How long am I on-call?**
A: Typically 1 week. Dates: Check Slack channel topic or calendar.

---

**Last Updated:** 2026-09-22  
**Version:** 1.0.0
