# Phase 9 Hot Fixes & Quick-Fix Process (ADR-2028, ADR-2029)

**Version:** 1.0  
**Date:** 2026-09-22  
**Scope:** Quick-fix implementation procedures, active incident tracking

---

## Overview

This document tracks:
- **Active Incidents** — P0 items currently being worked on
- **Quick-Fix Process** — 1-hour SLA procedures
- **Resolved Issues** — Fixed hotfixes with learnings
- **Rollback Procedures** — How to revert a bad hotfix

---

## Active Incidents

### Current Status

**Last Updated:** 2026-09-22 15:00 UTC

| ID | Title | Component | Severity | Status | ETA |
|---|---|---|---|---|---|
| (none currently) | — | — | — | — | — |

---

## Quick-Fix Process (Standard 1-Hour SLA)

### Phase 1: Alert & Triage (5 min)

**Trigger:** P0 feedback received

```
1. On-call engineer receives alert (email + Slack)
2. Opens feedback item in admin panel
3. Reviews:
   - Reproduction steps
   - Affected users
   - System impact
4. Decision:
   - "Quick fix possible" → Phase 2
   - "Complex fix needed" → Open GitHub issue + escalate
```

**Rollback**: If unclear, ask for more details (non-blocking)

### Phase 2: Implement Fix (45 min)

**Criteria:**
- Single file change OR
- ≤ 20 lines of code OR
- No database migration required OR
- No architectural change required

**Steps:**

```bash
# 1. Verify reproduction locally
cd /home/shumway/projects/CorvinOS
# Reproduce the issue (steps from feedback)

# 2. Diagnose root cause
grep -r "error_pattern" src/
# Check logs, traces

# 3. Implement fix
vim core/console/corvin_console/routes/[affected_module].py
# Make minimal, targeted change

# 4. Verify syntax
python3 -m py_compile core/console/corvin_console/routes/[affected_module].py

# 5. Run affected tests
pytest tests/test_[module]_e2e.py -v

# 6. Local verification
# (Manual test in staging matching reproduction steps)

# 7. Commit with clear message
git add [files]
git commit -m "hotfix: [title] [short description]

Fixes P0 feedback #[feedback_id].

Changes:
- Line X: [what changed]
- Line Y: [why it works]

Tested: [local/staging reproduction]"
```

**Example Commits:**

```
hotfix: console 503 Service Unavailable

Fixes P0 feedback #1234-abc.

The middleware was missing a try-catch around async route handler,
causing unhandled promise rejection → 503 response.

Added error handler in middleware stack.

Tested: POST /v1/console/feedback/status returns 200 ✓
```

### Phase 3: Test in Staging (10 min)

**Before deploy:**

```bash
# 1. Push to staging branch
git push origin hotfix/[short-name] -u

# 2. Staging deploys automatically (CI/CD)
# Wait for: ✅ Build passed, ✅ Tests passed

# 3. Manual verification in staging
curl -s https://staging.console.corvin-labs.com/v1/console/feedback/status

# 4. Reproduction steps in staging
# (Follow user's steps to confirm fix)
```

**Fail condition:**
- If tests fail → revert, diagnose, try again
- If manual test fails → revert, investigate further

### Phase 4: Deploy to Production (5 min)

**Approval:**
- Single-approver (on-call lead) — no full code review for hotfixes
- Must have passing tests + manual verification

**Deploy:**

```bash
# 1. Tag the release
git tag -a hotfix/2026-09-22-console-503 -m "Hotfix for P0: console 503"
git push origin hotfix/2026-09-22-console-503

# 2. Deploy to production
# (Automated via CD pipeline, or manual if needed)
corvin-deploy --environment=production --hotfix

# 3. Monitor logs
# Watch for errors in production logs
journalctl -u corvin-webui -f
tail -f ~/.corvin/logs/console.log

# 4. Verification
curl -s https://console.corvin-labs.com/v1/console/feedback/status
# Expected: 200 OK with feedback counts
```

### Phase 5: Communication (5 min)

**Communicate fix:**

```
Post to #phase9-critical + @user_email:

✅ **FIXED: Voice stops after 5 minutes**
   Feedback ID: #abc-123-def
   Time to fix: 1.2 hours
   
   **Root cause:** Connection timeout was not properly handled,
   causing the session to silently close after network idle.
   
   **Fix:** Added explicit timeout handler with graceful reconnection.
   
   **Deployed:** 2026-09-22 16:30 UTC
   **Verification:** Tested with 10-minute continuous voice session ✓
   
   Please test on your end and confirm the issue is resolved.
   Thank you for the detailed reproduction steps!
```

**Request confirmation** from original reporter

---

## Incident Log

### Template

```
## [Date] [Title] — [Component]

**Feedback ID:** #[id]  
**Reported by:** [email]  
**Severity:** P0  
**Status:** ✅ RESOLVED

### Timeline
- **15:30 UTC** — Feedback received
- **15:35 UTC** — Triaged as P0 critical
- **15:42 UTC** — Root cause identified
- **16:00 UTC** — Fix implemented & tested in staging
- **16:10 UTC** — Deployed to production
- **16:12 UTC** — Verified in production
- **16:15 UTC** — User notified

### Root Cause
[1-2 sentence explanation]

### Fix Applied
[Code change summary]

### Verification
[How it was tested]

### Prevention
[How to prevent in future]

### Metrics
- Time to detect: 1 min (user report)
- Time to fix: 40 min
- Time to deploy: 5 min
- **Total SLA: 46 minutes** ✅ Within 1h target
- User impact: Resolved immediately after deploy
```

---

## Common Quick Fixes (Library)

### Pattern 1: Missing Error Handler

**Symptom:** 500 error, no clear error message  
**Quick fix:** Add try-catch + log

```python
# Before
@router.post("/endpoint")
async def endpoint(req: Request):
    result = await some_async_call()
    return result

# After
@router.post("/endpoint")
async def endpoint(req: Request):
    try:
        result = await some_async_call()
        return result
    except Exception as e:
        logger.exception(f"endpoint_error: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")
```

**Effort:** 5 min

---

### Pattern 2: Config Value Typo

**Symptom:** Feature not working, config looks correct  
**Quick fix:** Fix typo in env var or config file

```bash
# Before
VOICE_TIMEOUT_SECONDS=300
# Code reads: VOICE_TIMEOUT_SECONDS_MS (typo)

# After
VOICE_TIMEOUT_MS=300000
# Code reads correct var
```

**Effort:** 2 min (1 min to find, 1 min to fix & test)

---

### Pattern 3: Async/Await Forgotten

**Symptom:** Operation hangs indefinitely  
**Quick fix:** Add missing `await`

```python
# Before
async def process():
    result = some_async_function()  # Missing await!
    return result

# After
async def process():
    result = await some_async_function()
    return result
```

**Effort:** 5 min

---

### Pattern 4: Logic Error (Null Check)

**Symptom:** Null pointer exception in production  
**Quick fix:** Add missing null check

```python
# Before
def get_value(data):
    return data["key"].upper()  # KeyError if missing

# After
def get_value(data):
    value = data.get("key", "")
    return value.upper() if value else ""
```

**Effort:** 5 min

---

### Pattern 5: Cache Invalidation

**Symptom:** Old data being served  
**Quick fix:** Clear cache or invalidate TTL

```python
# Before
@cache.cached(timeout=3600)  # 1-hour cache
def get_settings():
    return settings.load()

# After
@cache.cached(timeout=60)  # 1-minute cache (temporarily)
def get_settings():
    return settings.load()
```

**Effort:** 2 min (1 min change, 1 min test)

---

## Rollback Procedure

### If Hotfix Causes Regression

**Decision:** Rollback immediately if:
- New P0 issue introduced by hotfix
- Multiple user reports of regression
- Hotfix doesn't fix original issue

**Steps:**

```bash
# 1. Immediately revert
git revert hotfix/[commit-hash]
git push origin main

# 2. CD pipeline auto-deploys revert
# (Wait for deployment confirmation)

# 3. Verify rollback successful
curl -s https://console.corvin-labs.com/v1/console/feedback/status

# 4. Post incident summary
Post to #phase9-critical:
  ❌ Hotfix reverted due to regression
  Commit: [hash]
  Reason: [brief explanation]
  Next step: Root cause analysis scheduled
```

### Post-Incident Review

After any rollback or complex fix:

```
## Post-Incident Review

**Date:** [date]  
**Incident:** [title]  
**Attendees:** [team members]  
**Duration:** 30 min

### What went well
- [+] Fast detection
- [+] Reproducible steps provided

### What went wrong
- [-] Missed edge case
- [-] No pre-deployment testing

### Action items
- [ ] Add integration test for this case
- [ ] Document edge case in code
- [ ] Schedule team sync on [topic]
```

---

## Monitoring & Alerting

### Alert Conditions (Trigger P0)

The following automatically trigger P0 alerts:

| Condition | Example | Action |
|---|---|---|
| 503 Service Unavailable | >10 per minute | Page on-call |
| Database connection failure | Conn pool depleted | Page on-call |
| Critical API timeout | >10s latency | Page on-call |
| Authentication failure | >20% 401 responses | Page on-call |
| Voice session crash | >50% failure rate | Page on-call |
| Memory leak detected | RSS >90% | Page on-call |

### Monitoring Queries

```bash
# Monitor error rate
curl -s http://localhost:8765/v1/console/monitoring/error-rate
# Alert if > 5%

# Monitor response time
curl -s http://localhost:8765/v1/console/monitoring/latency-p99
# Alert if > 2 seconds

# Monitor voice stability
curl -s http://localhost:8765/v1/console/monitoring/voice-session-success-rate
# Alert if < 95%
```

---

## Training & Runbook

### Quick-Fix Checklist

Before deploying a hotfix, verify:

- [ ] Reproduction steps confirmed locally
- [ ] Root cause identified & documented
- [ ] Fix is minimal (single file, <20 lines)
- [ ] Syntax validated (`py_compile`)
- [ ] Affected tests pass
- [ ] Staging tests pass
- [ ] Manual verification in staging done
- [ ] Commit message is clear
- [ ] Code review approval received (lightweight)
- [ ] Production logs monitored post-deploy
- [ ] User notified of fix

### On-Call Runbook

**If P0 alert received:**

1. ✓ Read feedback description (2 min)
2. ✓ Reproduce issue locally (5 min)
3. ✓ Decide: Quick fix or complex? (2 min)
4. If quick fix:
   - ✓ Implement (30 min)
   - ✓ Test (5 min)
   - ✓ Deploy (5 min)
   - ✓ Verify (3 min)
5. Communicate status (3 min)

**Total:** <60 minutes, every time

---

## Escalation

### If Fix Exceeds 1 Hour

1. **Stop** active fix attempt
2. **Post** status: "Complex fix needed, escalating"
3. **Create** GitHub issue with details
4. **Assign** to engineering lead
5. **Set** SLA: 24 hours for P1 resolution

### If Unclear How to Fix

1. **Ask** in #phase9-critical for context
2. **Wait** max 5 minutes for response
3. If no response, escalate to team lead
4. Document investigation for future

---

## Metrics & SLA

### SLA Targets

| Priority | Time to Fix | Time to Deploy | Total |
|---|---|---|---|
| P0 | 45 min | 5 min | **60 min** |
| P1 | 12 hours | 1 hour | **24 hours** |
| P2 | 5 days | 1 day | **7 days** |

### Tracking

Weekly report includes:
- Mean time to fix (MTTF)
- Mean time to deploy (MTTD)
- Actual vs target SLA compliance
- Root cause categories
- Prevention measures taken

---

**Status:** 🟢 READY FOR PHASE 9  
**Owner:** On-call engineer  
**Last Updated:** 2026-09-22
