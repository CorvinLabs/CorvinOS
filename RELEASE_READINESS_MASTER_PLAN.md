# CorvinOS Release Readiness Master Plan

**Target:** Production + Release Ready (0 Findings)  
**Status:** INITIATED 2026-09-25  
**Strategy:** 3x Adversarial Review Cycles → E2E Tests → Release

---

## Phase 1: ADVERSARIAL REVIEW (3 Cycles)

### Round 1: Code Security & Reachability
- [ ] Scan all core modules for dead code
- [ ] Check: every entry point (CLI, API, plugin) has E2E test
- [ ] Security: no hardcoded secrets, no SQL injection, no XSS
- [ ] Audit: all audit events logged, hash-chain intact
- **Target:** Find & fix all critical findings

### Round 2: Architecture & Compliance
- [ ] ADR compliance (ADR-0264 frontmatter complete)
- [ ] No duplicate code between repos
- [ ] Tenant isolation enforced (all queries scoped)
- [ ] PII detection fail-closed
- **Target:** Zero architectural violations

### Round 3: Integration & Release
- [ ] All components wired (no orphaned code)
- [ ] No breaking changes to public API
- [ ] Documentation matches code
- [ ] Console UI matches backend API
- **Target:** Release-ready state

---

## Phase 2: INITIATIVE COMPLETION AUDIT

Query Corvin-Knowledge:
```bash
# Find all PROPOSED (not ACCEPTED) items
curl http://localhost:8000/v1/entities?status=proposed | jq '.entities | length'

# Check which are actually COMPLETE but marked PROPOSED
# Move to ACCEPTED if work is done
```

**Items to check:**
- [ ] Tenant Skill Architecture — DONE? (mark ACCEPTED)
- [ ] Context Pipeline V2 — DONE? (mark ACCEPTED)
- [ ] OS Skills Foundation — DONE? (mark ACCEPTED)
- [ ] Console Update — fully shipped? (mark ACCEPTED)
- [ ] Installation Architecture — all platforms? (mark ACCEPTED)

---

## Phase 3: E2E TESTING

### 3a: Installation Tests
```bash
# Linux
- [ ] Fresh install from GitHub (pip install)
- [ ] No missing dependencies
- [ ] corvin serve works
- [ ] console at http://localhost:3000

# Windows
- [ ] Fresh install from GitHub (pip install)
- [ ] No missing dependencies
- [ ] corvin serve works
- [ ] console at http://localhost:3000
```

### 3b: Feature E2E Tests
- [ ] Login → Create entity → Query graph → Export
- [ ] Skill creation → Deploy → Execute → Audit logged
- [ ] Plugin install → Enable → Verify working
- [ ] A2A invitation → Accept → Task execution
- [ ] Multi-tenant isolation (cross-tenant access denied)

### 3c: Playwright UI Tests
- [ ] Console loads (no 404, no errors)
- [ ] Navigation works (all panels reachable)
- [ ] Search/filter works
- [ ] Graph visualization renders
- [ ] Dark/light mode toggle works
- [ ] Real data loaded (not mocked)

---

## Phase 4: RELEASE PREPARATION

### Code Quality
- [ ] Zero eslint/mypy/type-check errors
- [ ] Zero security vulnerabilities (dependabot)
- [ ] Test coverage ≥ 80% for core paths
- [ ] All docs updated (no stale references)

### Deployment
- [ ] All secrets moved to env vars (no hardcoded)
- [ ] Docker image builds (if applicable)
- [ ] Version bumped (0.10.XX → 0.11.0 or major bump)
- [ ] CHANGELOG.md updated
- [ ] Release notes written

### Go-Live
- [ ] Tag git commit as release
- [ ] Push to GitHub
- [ ] Create GitHub Release
- [ ] Announce in channels

---

## Timeline (Multi-Session)

| Session | Work | Duration |
|---|---|---|
| **1** (NOW) | Master plan + audit setup | ~30min |
| **2** | Adversarial Round 1 (security) | ~120min |
| **3** | Adversarial Round 2 (architecture) | ~120min |
| **4** | Adversarial Round 3 (integration) | ~120min |
| **5-6** | E2E Tests (install + features) | ~240min |
| **7** | Playwright UI Tests + fixes | ~120min |
| **8** | Final release push | ~60min |

**Total:** ~10-12 hours → Production Ready

---

## Key Files to Check

| Component | File | Status |
|---|---|---|
| Installation | `/core/install/` | ✅ Complete |
| Console | `/core/console/corvin_console/web-next/` | 🟡 Check UI |
| Tenant Skills | `/core/skills/tenant_skill_config.yaml` | ✅ Done |
| Context V2 | `/core/context/context_pipeline_v2.py` | ✅ Done |
| OS Skills | `/core/skills/os_skills_registry.py` | ✅ Done |
| Tests | `/tests/tier1_e2e_complete.py` | 🟡 Expand |
| Knowledge Graph | Corvin-Knowledge repo | ✅ Live |

---

## Success Criteria

✅ **Adversarial Round 3:** Zero findings  
✅ **E2E Tests:** All pass (Linux + Windows + UI)  
✅ **Initiative Audit:** All Tier-1 items marked ACCEPTED  
✅ **Console:** Fully functional, no errors  
✅ **Docs:** All up-to-date, links working  
✅ **Deployment:** Can be deployed to production  

**Status: IN PROGRESS (Session 1 of 8)**
