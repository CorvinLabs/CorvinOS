# Phase 9 User Feedback Loop: Triage & Prioritization (ADR-2028, ADR-2029)

**Version:** 1.0  
**Date:** 2026-09-22  
**Scope:** User feedback collection, automatic triage, operator notification, quick-fix implementation

---

## Overview

The Phase 9 Feedback Portal provides a comprehensive system for collecting user feedback and routing it efficiently to engineering teams.

### Components

1. **Feedback Collection** — Bug reports, feature requests, NPS surveys
2. **Auto-Triage** — Deterministic prioritization (P0-P3) based on severity, impact, component
3. **Operator Notification** — Email alerts for P0-P1 items
4. **Tracking** — Immutable audit trail, tenant-scoped (GDPR Art. 32)
5. **Quick-Fix Implementation** — Rapid response for critical issues

---

## Triage Priority Levels

### P0: CRITICAL (Fix within 1 hour)

**Criteria:**
- System-wide outage (console unresponsive, voice completely down)
- Data loss risk
- Security vulnerability
- Authentication/authorization broken
- Production environment affected

**Examples:**
- "Console returns 503 Service Unavailable"
- "Voice connection crashes on every call"
- "Video producer loses all rendered files"
- "Database connection fails"

**Action:**
- Immediate Slack alert to #phase9-incidents
- Page on-call engineer
- Implement hotfix within 1 hour
- Post-incident review required

### P1: HIGH (Fix within 24 hours)

**Criteria:**
- Major feature broken (not workaround)
- Significant user impact (affects workflow)
- High-severity component (console, voice, video)
- Production issue with reproducible steps
- Performance degradation >50%

**Examples:**
- "Video rendering fails for 4K resolution"
- "Voice transcription has poor accuracy (>30% error rate)"
- "Console takes >30s to load"
- "NPS score drops to 4/10 or below"

**Action:**
- Email to team within 1 hour
- Prioritize above planned work
- Estimate effort and assign
- Daily status updates in #feedback-p1

### P2: MEDIUM (Fix within 1 week)

**Criteria:**
- Partial feature loss (workaround exists)
- Low-to-medium severity component
- Staging/local environment
- Non-blocking issue

**Examples:**
- "Dark mode toggle doesn't persist"
- "Export to PDF has formatting issues"
- "Marketplace search results incomplete"
- "Settings panel slow on first load"

**Action:**
- Add to sprint backlog
- Estimate for planning
- Schedule in next sprint

### P3: LOW (Backlog/Won't Fix)

**Criteria:**
- Feature requests (non-critical)
- Cosmetic issues
- Documentation gaps
- Nice-to-have improvements
- Duplicates of existing requests

**Examples:**
- "Add dark mode"
- "Improve console color scheme"
- "Add keyboard shortcut for X"
- "Localize UI to Spanish"

**Action:**
- Archive to feedback backlog
- Aggregate similar requests
- Consider for future releases

---

## Auto-Triage Algorithm

The TriageEngine assigns priority deterministically based on:

### 1. Feedback Type (Base Score)

| Type | Base | Reasoning |
|---|---|---|
| Bug (Critical) | 8-10 | System failures = highest priority |
| Bug (High) | 6-8 | Significant issues |
| Bug (Medium) | 4-6 | Partial failures |
| Bug (Low) | 2-4 | Minor issues |
| NPS (Detractor, ≤3) | 8-9 | Dissatisfied users = fix needed |
| NPS (Passive, 4-6) | 5-6 | Moderate satisfaction |
| NPS (Promoter, 7-10) | 2-3 | Satisfied users |
| Feature Request | 3-5 | Backlog items |

### 2. Component Criticality Multiplier

| Component | Weight | Reasoning |
|---|---|---|
| console | 1.0 | Core UI, everyone uses |
| voice | 0.95 | Key feature, many users |
| video_producer | 0.8 | High-value but specialized |
| learning | 0.7 | Important but non-blocking |
| marketplace | 0.6 | Optional ecosystem |
| settings | 0.5 | Configuration |
| docs | 0.3 | Reference |

**Formula:** `score += (component_weight * 2.0)`

### 3. Environment Multiplier

| Environment | Multiplier | Reasoning |
|---|---|---|
| production | 1.5 | Affects real users |
| staging | 1.2 | Pre-release testing |
| local | 1.0 | Developer environment |

**Formula:** `score *= environment_multiplier`

### 4. Reproducibility Bonus

- **+0.5 points** if reproduction steps provided (easier to fix)

### 5. Final Mapping

| Score | Priority | Action | Effort |
|---|---|---|---|
| ≥9.0 | P0 | Page engineer | Quick fix |
| 7.0-9.0 | P1 | Email team | 1-2h |
| 4.0-7.0 | P2 | Backlog | Half day |
| <4.0 | P3 | Archive | Multiple days |

---

## Effort Estimation

Quick estimation based on feedback type and reproducibility:

### Bug Reports

| Reproducibility | Complexity | Estimate |
|---|---|---|
| Full steps provided | Console UI | **quick_fix** (30 min) |
| Full steps provided | Voice | **1-2h** |
| Full steps provided | Other | **half_day** (2-4h) |
| Partial steps | Any | **half_day** |
| No steps | Any | **multi_day** (>4h) |

### Feature Requests

All feature requests → **multi_day** (requires design, testing, docs)

### NPS Surveys

No effort estimate (feedback only, possible action item)

---

## Triage Workflow

### Step 1: Feedback Submission

User submits via Portal:
- **Bug Report:** title, description, severity, component, reproduction steps
- **Feature Request:** title, description, component, use case
- **NPS Survey:** score (0-10), optional comment

```
POST /v1/console/feedback/bug-report
{
  "title": "Voice stops after 5 minutes",
  "description": "When using voice input, connection drops after ~5 minutes...",
  "severity": "high",
  "component": "voice",
  "user_email": "user@example.com",
  "reproduction_steps": "1. Start voice session. 2. Speak for 5 minutes. 3. Voice stops.",
  "environment": "production",
  "version": "v2.0.0"
}
```

### Step 2: Auto-Triage

TriageEngine immediately:
1. Calculates priority score
2. Assigns P0-P3 priority
3. Estimates effort
4. Saves to `triaged/` directory

```json
{
  "feedback_report": { ... },
  "priority": "p0",
  "triage_reason": "Critical issue affecting core functionality",
  "estimated_effort": "quick_fix",
  "assigned_to": null,
  "triage_timestamp": "2026-09-22T15:30:00Z"
}
```

### Step 3: Operator Notification

For P0-P1:
- **Email** sent to ops@corvin-labs.com (if configured)
- **Slack** message to #phase9-incidents (optional, admin config)
- Subject: `[P0] Voice stops after 5 minutes - user@example.com`

### Step 4: Quick Fix (if applicable)

For P0 items:
1. Engineer investigates (start < 5 min)
2. If quick fix identified (< 1 hour effort):
   - Implement hotfix
   - Test in staging
   - Deploy to production
   - Post status update
3. If complex:
   - Open GitHub issue
   - Add to sprint
   - Post ETA

### Step 5: Resolution & Archive

Once fixed:
1. Mark as resolved (manual, TBD admin panel)
2. Archive to `resolved/` directory
3. Log in feedback registry
4. Generate weekly report

---

## Quick-Fix Process (P0 Only)

### Criteria for Quick Fix

- **Effort:** < 1 hour (no redesign, minimal testing)
- **Scope:** Single component affected
- **Rollback:** Easy (no data migration)
- **Test:** Automated tests pass + manual verification
- **Examples:**
  - Config value typo
  - Single-line logic error
  - Missing error handler
  - API endpoint timing issue

### Quick-Fix Steps

1. **Triage Alert** (< 5 min)
   - Operator receives P0 notification
   - Reviews feedback + context
   - Decides: "quick fix" vs "complex fix"

2. **Implement** (< 45 min)
   - Fix applied to code
   - Local tests pass
   - Code review (async, lightweight)

3. **Test** (< 10 min)
   - Automated test suite passes
   - Manual reproduction in staging
   - Verification that issue is resolved

4. **Deploy** (< 5 min)
   - Push to production
   - Monitor logs for errors
   - Verify in live environment

5. **Communicate** (< 5 min)
   - Post status: "✅ FIXED (1.5h elapsed)"
   - Include commit hash
   - Thank user for report
   - Request confirmation from reporter

**Total SLA: 1 hour from alert to fix deployed**

---

## Feedback Report Structure

### Raw Report (reports/)

```json
{
  "feedback_id": "uuid",
  "tenant_id": "default",
  "timestamp": "2026-09-22T15:30:00Z",
  "feedback_type": "bug_report",
  "title": "Voice stops after 5 minutes",
  "description": "When using voice input, the connection drops after ~5 minutes of continuous conversation...",
  "severity": "high",
  "component": "voice",
  "user_email": "user@example.com",
  "environment": "production",
  "version": "v2.0.0",
  "reproduction_steps": "1. Start voice session. 2. Speak for 5 minutes. 3. Voice stops.",
  "signature": "sha256(...)",
  "audit_event_id": "audit-uuid"
}
```

### Triaged Report (triaged/)

```json
{
  "feedback_report": { ... },
  "priority": "p1",
  "triage_reason": "High-priority issue affecting core voice component in production",
  "estimated_effort": "1-2h",
  "assigned_to": null,
  "triage_timestamp": "2026-09-22T15:30:05Z"
}
```

### Notification Record (notified/)

```json
{
  "feedback_id": "uuid",
  "priority": "p1",
  "title": "Voice stops after 5 minutes",
  "component": "voice",
  "severity": "high",
  "user_email": "user@example.com",
  "notified_at": "2026-09-22T15:30:10Z"
}
```

---

## Triage Dashboard (Admin Panel)

**URL:** `https://console.corvin-labs.com/admin/feedback`

**Sections:**

### 1. Priority Summary
```
P0 Critical:   1 item (fix within 1h)
P1 High:       3 items (fix within 24h)
P2 Medium:     12 items (fix within 1 week)
P3 Low:        47 items (backlog)
─────────────────────
Total:         63 items
```

### 2. Open P0 Items

Table with columns:
- Title
- Component
- Submitted (time ago)
- Status (open / in_progress / resolved)
- Action buttons (assign, quick_fix, mark_resolved)

### 3. Recent Notifications

Timeline of notifications sent:
- "P1: Video rendering fails (sent to ops@...)"
- "P0: Console 503 (FIXED in 1h)"

### 4. Feedback Analytics

Charts:
- **Priority distribution** (pie chart)
- **Component breakdown** (bar chart)
- **NPS trend** (line chart)
- **Resolution time** (box plot)

---

## Escalation & Rollback

### Escalation (P0 → Engineering Lead)

If quick fix fails or issue is complex:

1. Post in #phase9-critical channel
2. Include:
   - Feedback ID
   - Reproduction case
   - Initial diagnosis
   - Estimated effort
3. Engineering lead triages to team

### Rollback Procedure

If hotfix introduces regression:

1. Revert commit immediately
2. Post: "Hotfix rolled back pending investigation"
3. Create new P1 issue
4. Schedule for next sprint

---

## Weekly Triage Report

**Recipients:** ops@corvin-labs.com, #phase9-feedback  
**Schedule:** Every Monday 9 AM UTC

**Contents:**

```
# Weekly Feedback Report (Week of 2026-09-22)

## Summary
- 42 new feedback items
- 8 P0 resolved (avg time: 1.2 hours)
- 15 P1 resolved (avg time: 8 hours)
- 6 P2 resolved
- NPS average: 7.4/10

## Open P0 Items (By Component)
- voice (2) — high impact
- console (1) — auth failure

## Open P1 Items (By Component)
- video_producer (3)
- marketplace (2)
- learning (1)

## Action Items
- [ ] Investigate voice stability
- [ ] Review marketplace search performance
- [ ] Schedule P2 backlog review

## Metrics
- Mean time to triage: 2 min
- Mean time to P0 fix: 1.3h
- NPS trend: ↑ +0.3 from last week
```

---

## Configuration & Customization

### Environment Variables

```bash
# Email notifications
FEEDBACK_NOTIFY_EMAIL=ops@corvin-labs.com
FEEDBACK_NOTIFY_SLACK=#phase9-feedback

# SLA thresholds
FEEDBACK_P0_SLA_HOURS=1
FEEDBACK_P1_SLA_HOURS=24
FEEDBACK_P2_SLA_HOURS=168  # 1 week

# Auto-priority thresholds
FEEDBACK_CRITICAL_SCORE=9.0
FEEDBACK_HIGH_SCORE=7.0
FEEDBACK_MEDIUM_SCORE=4.0
```

### Customization (Per Tenant)

```yaml
# tenant.corvin.yaml
feedback:
  enabled: true
  auto_triage: true
  notify_operators: true
  critical_components:
    - console
    - voice
  component_weights:
    video_producer: 0.9  # Override default
  escalation_slack_channel: "#my-team"
```

---

## Compliance Notes

### GDPR Art. 32 (Tenant Isolation)

- ✅ All feedback is tenant-scoped
- ✅ Audit trail immutable
- ✅ No cross-tenant data leakage
- ✅ Audit events hash-chained

### Data Retention

- Raw reports: 90 days (configurable per tenant)
- Triaged/resolved: 1 year
- Audit logs: Permanent (hash-chained)

### PII Handling

- User email stored (opt-in: user provided)
- Description not scrubbed (user consent implicit in submission)
- No IP addresses logged
- Session IDs redacted if provided

---

## Reference

**ADRs:**
- ADR-2028 — Intent Router & Phase 9 Implementation
- ADR-2029 — Operator Control Plane

**Code:**
- `core/feedback/phase9_feedback_portal.py` — Main portal
- `core/feedback/feedback_triage.py` — Triage engine
- `core/feedback/feedback_models.py` — Data models
- `core/console/corvin_console/routes/feedback_portal_routes.py` — API routes

**Tests:**
- `tests/test_phase9_feedback_portal.py` — Unit + integration tests

---

## Next Steps (Phase 9b)

1. **Email Integration** — SmtpNotifier + email templates
2. **Slack Integration** — Post P0-P1 to #incidents
3. **Dashboard UI** — Admin feedback panel (React component)
4. **Bulk Import** — Import legacy feedback from email/Slack
5. **Feedback Loop Learning** — ADR-0314 integration (learn from patterns)
6. **Public Status Page** — Show open issues to users (optional)

---

**Status:** 🟢 IMPLEMENTED & TESTED  
**Maintainer:** ops@corvin-labs.com  
**Last Updated:** 2026-09-22
