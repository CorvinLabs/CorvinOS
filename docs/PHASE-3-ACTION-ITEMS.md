# Phase 3: Immediate Action Items (Installation E2E + Release)

**Status:** Ready to Kickoff  
**Owner:** Claude (implementation), Shumway (approval/coordination)  
**Timeline:** Weeks 1–5 (Sep 11 – Oct 15, 2026)

---

## 📋 GitHub Actions Verification (This Week)

### ✅ Completed (v1.0.0 Ready)

```yaml
Workflow: .github/workflows/install-test.yml
Status: ✓ Exists, ✓ Structure valid, ✓ Triggers configured

Current Jobs:
  ✓ tier-1-2: Fast syntax checks (ubuntu-latest)
  ✓ tier-3-4-linux: Docker E2E (ubuntu-latest)
  ✓ tier-3-4-windows: PowerShell validation (ubuntu-latest only — needs update)
  ✓ final-gate: All tests pass gate
  ✓ release-ready: Version sync check (main only)
```

### ⬜ Next: Windows Runner (This Week)

**Action:** Upgrade `tier-3-4-windows` job to use `windows-latest` runner

```yaml
# Current (Tier 2 simulation on Linux)
runs-on: ubuntu-latest
- Install PowerShell (cross-platform)
- Validate install.ps1 structure

# Phase 3 (Real Tier 4 on Windows)
runs-on: windows-latest
- Run install.ps1 directly (native PowerShell 5.1+)
- Verify console starts, Claude Code detected
- Check no Ollama references
```

**Effort:** 30 min (edit + test)

---

## 🔔 Dependabot Monitoring (This Week)

### Current State

```
GitHub Dependabot Alerts (from git push output):
  ✓ 7 vulnerabilities found
  ├─ 1 HIGH ⚠ (URGENT)
  └─ 6 MODERATE (Plan for v1.0.1)

Location: https://github.com/CorvinLabs/CorvinOS/security/dependabot
```

### Action Items

| Severity | Action | Timeline | Owner |
|---|---|---|---|
| **HIGH** | Security review + patch/feature-flag | ASAP (< 7d) | Security team |
| **MODERATE** | Evaluate + schedule for v1.0.1 | Next release | Release team |

**Decision Point:** Can v1.0.0 release with unresolved MODERATE alerts?
- **YES:** Document in release notes ("Known issues" section)
- **NO:** Fix all before tagging v1.0.0

**Recommendation:** Fix HIGH, document MODERATE in RELEASE_NOTES.md

---

## 📝 Phase 3 Deliverables (Weeks 1–5)

### Week 1: Windows E2E Setup

**Tasks:**
```
[ ] Write tests/e2e/windows-e2e.ps1 (PowerShell Tier-4)
    └─ Test 1: install.ps1 syntax validation
    └─ Test 2: URL downloads (verify uv checksum)
    └─ Test 3: Claude Code detection
    └─ Test 4: No Ollama references
    └─ Test 5: Console starts (systemctl check)

[ ] Update .github/workflows/install-test.yml
    └─ Add windows-latest runner
    └─ Wire up windows-e2e.ps1 test
    └─ Fail on any test failure

[ ] Test locally on Windows VM or GitHub Actions
    └─ Verify workflow runs successfully
    └─ Capture test logs for validation
```

**Deliverable:** .github/workflows/install-test.yml updated + windows-e2e.ps1 working

---

### Week 2: CI/CD Scale

**Tasks:**
```
[ ] Add macOS runner (runs-on: macos-latest)
    └─ Test install.sh (bash native)
    └─ Verify iCloud keychain interaction (if applicable)

[ ] Implement Release Gate (manual approval before tag)
    └─ Requires human review + sign-off
    └─ Blocks automated release

[ ] Configure Slack notifications
    └─ Alert #infrastructure on test failure
    └─ Link to failed job for debugging
```

**Deliverable:** Multi-platform CI/CD running on every commit

---

### Week 3: Release Preparation

**Tasks:**
```
[ ] Write RELEASE_NOTES.md
    ├─ Installation instructions (one-liner)
    ├─ New features (Claude Code, no Ollama, E2E tested)
    ├─ Quality metrics (15 tests passing, 0 adversarial findings)
    ├─ Known issues (Dependabot MODERATE if unresolved)
    └─ Thanks + credits

[ ] Draft blog post
    ├─ "CorvinOS v1.0.0: Production Ready"
    ├─ Why v1.0.0 matters (installation friction solved)
    ├─ What's included (cross-platform, Claude Code)
    ├─ Installation quickstart
    └─ Community feedback call

[ ] Validate GitHub release workflow
    └─ gh release create v1.0.0 --draft
    └─ Review markdown rendering
```

**Deliverable:** Release ready for public announcement

---

### Week 4: Release Announcement

**Tasks:**
```
[ ] Publish GitHub Release (v1.0.0)
    └─ gh release create v1.0.0 \
       --title "CorvinOS v1.0.0 — Production Ready" \
       --notes-file RELEASE_NOTES.md

[ ] Publish blog post
    └─ Post to https://blog.corvin-labs.com (if exists)
    └─ Or: Medium, Dev.to, or community blog

[ ] Announce on community channels
    └─ Slack: #announcements
    └─ Discord: #releases
    └─ Email: Opt-in mailing list

[ ] Open issue thread for feedback
    └─ GitHub Discussion or Issue: "v1.0.0 Feedback"
    └─ Collect user feedback for Phase 4
```

**Deliverable:** v1.0.0 public + community aware

---

### Week 5: Post-Release Monitoring

**Tasks:**
```
[ ] Monitor issue tracker
    └─ Respond to bug reports
    └─ Escalate critical issues

[ ] Dependabot triage
    └─ Assign MODERATE fixes to backlog
    └─ Plan for v1.0.1

[ ] SLO dashboard setup
    └─ Install success rate: Target 99%+
    └─ E2E test duration: Target < 15 min
    └─ Failure alert: < 95% triggers incident

[ ] Phase 3 retrospective
    └─ What went well?
    └─ What was harder than expected?
    └─ Plan Phase 4 (learning loops, cost optimization)
```

**Deliverable:** Production monitoring active, Phase 4 roadmap ready

---

## 🎯 Success Criteria (v1.0.0 Complete)

- ✅ All 7 vulnerabilities reviewed (1 HIGH fixed, 6 MODERATE documented)
- ✅ Windows E2E tests passing on GitHub Actions `windows-latest` runner
- ✅ macOS E2E tests passing on GitHub Actions `macos-latest` runner
- ✅ Release published on GitHub with RELEASE_NOTES.md
- ✅ Blog post published + community announcement sent
- ✅ SLO dashboards live + monitored
- ✅ Feedback collected for Phase 4

---

## 🚦 Approval & Handoff Checklist

**Phase 3 can start when:**

- [ ] Shumway approves Phase 3 plan (this document)
- [ ] Security team triages HIGH vulnerability
- [ ] GitHub Actions budget OK (Windows runners cost $)
- [ ] Marketing/comms team ready for announcement

**Handoff:** Claude starts Week 1 tasks (Windows E2E setup)

---

## 📞 Contacts & Escalation

| Role | Contact | Reason |
|---|---|---|
| **Architecture** | Shumway | ADR review, Phase 4 planning |
| **Security** | Security team | Dependabot HIGH triage |
| **DevOps** | Infrastructure team | GitHub Actions runners, SLO dashboards |
| **Marketing** | Comms team | Blog post, announcement coordination |

---

## 📚 Reference Documents

- **Phase 3 Plan:** `docs/PHASE-3-WINDOWS-DOCKER-E2E-RELEASE-PLAN.md` (this session)
- **Installation Architecture:** ADR-0666 (Corvin-ADR/decisions/)
- **CI/CD Gate:** `.github/workflows/install-test.yml` (this session)
- **Test Suite:** `tests/e2e/` + `tests/adversarial/` (Phase 1–2)
- **CHANGELOG:** `CHANGELOG.md` (v1.0.0 release notes)

---

**Next sync:** Monday Sep 11, 10:00 Berlin time (Phase 3 kickoff)

🚀 **Ready to ship v1.0.0!**
