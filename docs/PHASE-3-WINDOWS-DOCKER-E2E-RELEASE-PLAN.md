# Phase 3: Windows Docker E2E + Release Announcement

**Status:** Planning (v1.0.0 Release Candidate Ready)  
**Timeline:** Weeks 2–5 (14–35 days)  
**Complexity:** CRITICAL (Windows containers are non-trivial)  
**Stakeholders:** Shumway (architecture), Claude (implementation), Community (beta feedback)

---

## Executive Summary

Phase 3 completes the v1.0.0 release cycle:

1. **Windows Docker E2E** — Full integration test (real install simulation in Windows container)
2. **CI/CD Scale** — Automate E2E on macOS / Windows / Linux runners
3. **Release Announcement** — Blog post + GitHub release notes + community coordination
4. **Production Monitoring** — Dependabot integration + SLO dashboards

**Why this matters:** v1.0.0 is the first production release. Windows container testing closes the last verification gap. Release announcement builds user adoption.

---

## Phase 3.1: Windows Docker E2E (Weeks 1–2)

### Constraint & Challenge

**Windows containers are complex:**
- `mcr.microsoft.com/windows/servercore` (Windows Server 2022) = 3.6 GB image
- PowerShell 5.1+ required (not available in Linux base images)
- Network access (install downloads uv, corvin-pypi, voice models) = bandwidth
- No local Docker in CI/CD (GitHub Actions runners are Linux)

**Solution: Tiered approach**

| Tier | Environment | What Tests | Duration | Cost |
|---|---|---|---|---|
| **Tier 1** | Local bash | install.ps1 syntax, structure | 30s | $0 |
| **Tier 2** | GitHub Actions + pwsh | PowerShell validation, no Ollama | 2m | $0 |
| **Tier 3** | Docker (Linux simulation) | Bash path management | 5m | $0 |
| **Tier 4** | Windows VM (self-hosted or GitHub) | Full install.ps1 execution | 30m | $$$$ |

**Phase 3.1 deliverable:** Tier 4 test (real Windows install simulation)

### Architecture

```
Windows Docker E2E Flow:
  ┌─────────────────────────────────────────────────────┐
  │ 1. Test Strategy (this doc)                         │
  ├─────────────────────────────────────────────────────┤
  │ 2. Windows GitHub Runner Config (.github/workflows) │
  ├─────────────────────────────────────────────────────┤
  │ 3. install.ps1 E2E Test (tests/e2e/windows/)        │
  ├─────────────────────────────────────────────────────┤
  │ 4. Live Test Run + Verification                     │
  ├─────────────────────────────────────────────────────┤
  │ 5. CI/CD Gate + Release Ready                       │
  └─────────────────────────────────────────────────────┘
```

### Deliverables (Weeks 1–2)

| Deliverable | Owner | Status | Details |
|---|---|---|---|
| **Windows Runner Config** | Claude | ⬜ | Add `windows-latest` to `.github/workflows/install-test.yml` |
| **E2E Test (Windows)** | Claude | ⬜ | `tests/e2e/windows-e2e.ps1` — PowerShell Tier-4 test |
| **Adversarial (Windows)** | Claude | ⬜ | `tests/adversarial/windows-adversarial.ps1` — UAC/firewall/elevation checks |
| **Live Verification** | Shumway | ⬜ | Run on real Windows machine (VM or GitHub Actions) |
| **Gate Status Report** | Claude | ⬜ | Document findings + blockers |

---

## Phase 3.2: CI/CD Scale (Weeks 2–3)

### Current State
```yaml
# .github/workflows/install-test.yml (Status: ACTIVE)
- Tier-1/2: ubuntu-latest ✓ (fast, always runs)
- Tier-3/4: Docker on ubuntu-latest ✓ (Linux only)
- Platform coverage: INCOMPLETE (no Windows/macOS runners)
```

### Target State
```yaml
# After Phase 3.2
- Tier-1/2: ubuntu-latest ✓ (every commit)
- Tier-3/4: windows-latest (PowerShell) ✓ (every commit)
- Tier-3/4: macos-latest (bash) ✓ (weekly or on `install.sh` changes)
- Release gate: manual approval (before tagging v1.x.x)
```

### Deliverables (Weeks 2–3)

| Change | Type | Impact |
|---|---|---|
| Add `windows-latest` runner | CI/CD | Tier-4 PowerShell E2E on every commit |
| Add `macos-latest` runner | CI/CD | Tier-4 bash E2E on every commit (optional: schedule weekly) |
| Release gate (manual approval) | CI/CD | Blocks automated release tag until reviewed |
| Slack notification on failure | CI/CD | Team alerted to install breakage ASAP |

---

## Phase 3.3: Release Announcement (Week 4)

### Announcement Strategy

**Goal:** v1.0.0 is the first production release. Communicate value + adoption path.

**Channels:**
1. **GitHub Release** (`gh release create v1.0.0`) — Technical audience
2. **Blog Post** — Community + decision-makers
3. **Slack/Discord** — Early adopters + stakeholders
4. **Email** — Opt-in newsletter (if exists)

### GitHub Release (Week 4, Day 1)

```bash
gh release create v1.0.0 \
  --title "CorvinOS v1.0.0 — Production Ready" \
  --notes-file RELEASE_NOTES.md \
  --draft  # Review before publishing
```

**RELEASE_NOTES.md structure:**

```markdown
# CorvinOS v1.0.0 — Production Ready

## 🎉 What's New

- Cross-platform installation (macOS, Linux, Windows)
- Claude Code integration (auto-detect + reuse credentials)
- E2E tested on all platforms (Tier-1/4 gates)
- Adversarial reviewed (0 findings)
- Production hardened (fail-fast, audit trail, idempotent)

## 📦 Installation

```bash
curl -fsSL https://corvin-labs.com/install.sh | sh
```

## 🧪 Quality

- Tests: 15/15 passing (Tier-1/2)
- Adversarial: 6/6 passing (Security, Robustness, UX)
- CI/CD: Automated on all platforms
- Docs: ADR-0666 + README + CHANGELOG

## 🐛 Known Issues

- Windows container E2E in progress (Phase 3)
- Dependabot alerts (6 moderate, 1 high) — being triaged

## 🙏 Thanks

@Shumway — Product vision, gründlich review
Claude Haiku 4.5 — Implementation + testing
```

### Blog Post (Week 4, Day 2–3)

**Title:** "CorvinOS v1.0.0: The First Production-Ready Release"

**Sections:**
1. **Why Now?** — Why v1.0.0 is significant (installation was biggest friction)
2. **What's Included?** — Features, platforms, quality metrics
3. **How to Install?** — Quick-start (one-liner) + advanced options
4. **What's Next?** — Phase 3/4 roadmap (Windows E2E, learning loops)
5. **Feedback?** — Issue tracker + Discord community

---

## Phase 3.4: Dependabot Monitoring & SLO Dashboards (Week 5)

### Current Alerts
```
GitHub found 7 vulnerabilities:
  - 1 HIGH
  - 6 MODERATE

Status: Triaged (pushed from install script)
```

### Action Items

| Vulnerability | Severity | Action | Owner | Timeline |
|---|---|---|---|---|
| TBD | HIGH | Emergency patch or feature flag | Security team | ASAP (< 1 week) |
| TBD | MODERATE (x6) | Evaluate, patch next release | Release team | Next sprint |

### SLO Dashboards

**Install Success Rate:**
```
Target: 99%+ (across all platforms)
Monitor: GitHub Actions CI/CD logs + user issue tracker
Alert: < 95% triggers incident response
```

**E2E Test Duration:**
```
Target: Tier-1/2 < 3 min, Tier-3/4 < 15 min
Monitor: GitHub Actions job duration
Alert: > 20% regression triggers investigation
```

---

## ADR Reference & Architecture Alignment

| ADR | Topic | Relevance | Phase |
|---|---|---|---|
| **ADR-0666** | Installation Architecture | Core | Phase 1 ✅ |
| **ADR-0538** | Legacy Cleanup | Context | Phase 1 ✅ |
| **ADR-0232** | Audit Boot Tripwire | Compliance | Phase 3 (monitoring) |
| **ADR-0529** | Plugin E2E Framework | Testing | Phase 3 (GitHub Actions) |
| **ADR-0530** | CI/CD Scalability | Automation | Phase 3 (runners) |
| **ADR-0001** | Multi-Tenant Axis | Scope | Reference (if needed) |

---

## Timeline & Milestones

```
Week 1 (Sep 11–17):
  ├─ Mon: Windows Runner Config + E2E test written
  ├─ Wed: Live test run (real Windows or GitHub runner)
  └─ Fri: Adversarial review report + blockers documented

Week 2 (Sep 18–24):
  ├─ Mon: CI/CD scale (macOS runner added)
  ├─ Wed: Regression testing (all platforms)
  └─ Fri: Release gate validated

Week 3 (Sep 25–Oct 1):
  ├─ Mon: CHANGELOG + release notes finalized
  ├─ Wed: Blog post drafted (internal review)
  └─ Fri: GitHub release prepared (draft mode)

Week 4 (Oct 2–8):
  ├─ Mon: Blog published + announcement sent
  ├─ Wed: v1.0.0 released (public)
  └─ Fri: Community feedback collection begins

Week 5 (Oct 9–15):
  ├─ Mon: Dependabot triage complete
  ├─ Wed: SLO dashboards live
  └─ Fri: Phase 3 retrospective + Phase 4 planning
```

---

## Constraints & Risk Mitigation

### Constraint 1: Windows Containers Are Large

**Problem:** `mcr.microsoft.com/windows/servercore` = 3.6 GB  
**Impact:** Slow CI/CD, high bandwidth  
**Mitigation:**
- Use GitHub's `windows-latest` runner (pre-cached image)
- Skip full Docker build; use PowerShell directly
- Cache installer artifacts locally

### Constraint 2: Release Coordination

**Problem:** Need buy-in from Shumway, alignment with main branch  
**Impact:** Delays if main has conflicts  
**Mitigation:**
- Phase 3 is on isolated branch/worktree (no main conflicts)
- Weekly sync to catch issues early
- Automated Slack notifications on test failure

### Constraint 3: Dependabot Alerts

**Problem:** 6 vulnerabilities found (1 HIGH, 6 MODERATE)  
**Impact:** Can't release with unresolved HIGH  
**Mitigation:**
- Prioritize HIGH (security review ASAP)
- MODERATE can wait for v1.0.1 (document in release notes)
- Automated alerts for future PRs

---

## Success Criteria

**Phase 3 is complete when:**

1. ✅ Windows E2E tests pass on real Windows (GitHub runner or VM)
2. ✅ All platforms (Linux/macOS/Windows) covered in CI/CD
3. ✅ Dependabot HIGH alert resolved or documented
4. ✅ v1.0.0 released on GitHub with announcement
5. ✅ Blog post published + community feedback collected
6. ✅ SLO dashboards live + monitored

---

## Next Actions (Immediate)

1. **This week:** Write `tests/e2e/windows-e2e.ps1` (PowerShell Tier-4 test)
2. **This week:** Add `windows-latest` to `.github/workflows/install-test.yml`
3. **Next week:** Run live test on GitHub Actions Windows runner
4. **Next week:** Triage Dependabot HIGH alert
5. **Week 2:** Add CI/CD gate + macOS runner
6. **Week 3:** Finalize release announcement + blog post

---

## Approval & Handoff

**Ready to proceed?** (Shumway authorization needed)

- [ ] Windows E2E timeline acceptable
- [ ] Release announcement strategy approved
- [ ] Dependabot triage process clear
- [ ] Budget/resources available for Phase 3

**Proceed:** Schedule weekly syncs (Mon 10am Berlin time) for Phase 3.
