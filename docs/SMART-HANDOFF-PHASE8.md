# Smart-Handoff: Phase 8+ Resumption Guide

**Date:** 2026-09-17  
**Prepared by:** Phase 7 Consolidation  
**For:** Phase 8 (Advanced Analytics) or next operators/sessions  
**Read time:** 10–15 minutes

---

## Quick Summary (60 seconds)

**Current state (end of Phase 7):**
- ✅ CorvinOS v1.0.0 is **production-ready**
- ✅ All 160+ tests passing (100%)
- ✅ All 5 services live: Console, Telemetry, Skill Forge, Notifications, Marketplace
- ✅ 4,100+ LoC integrated, zero blockers
- ✅ GDPR + security compliance verified

**What works NOW:**
- Skill generation (Forge) <2s end-to-end
- Console UI (dashboard, presets, settings)
- Telemetry (world map, live metrics)
- Notifications (Discord, systemd daemon)
- Marketplace (discovery, install, licensing)
- Video Producer (Director Mode with learning loops)

**What's pending (Phase 8+ roadmap):**
- Real-time streaming (WebSocket/SSE) — not in v1.0
- Advanced analytics (Prometheus export, time-series retention > 7 days)
- Enterprise features (multi-channel notifications, permission management)

**How to resume Phase 8:**
1. Read "System Architecture" section (5 min)
2. Set up dev environment (10 min, see "Prerequisites")
3. Pick one Phase 8 task from the roadmap
4. Run tests + deploy as usual (loop-driven-engineering)

---

## System Architecture (30 seconds per component)

### Layer 1: Console UI
**File:** `core/console/corvin_console/`  
**What:** React SPA + Python FastAPI backend  
**Does:** Render dashboard (skills, telemetry, settings), feature gating, user settings  
**Key endpoints:**
- `GET /console/` — SPA entry point
- `GET /v1/console/dashboard/` — dashboard data
- `POST /v1/console/api/feature-status/preset` — update feature preset  
**Status:** ✅ Live (Phase 5)

### Layer 2: Telemetry Aggregator
**File:** `core/telemetry/` + `core/learning/`  
**What:** Collects metrics from live instances, learns patterns, emits confidence scores  
**Does:** <100ms latency aggregation, multi-tenant isolation, pattern discovery  
**Key endpoints:**
- `GET /v1/stats/` — live world map + metrics
- `POST /v1/learning/feedback/` — receive operator feedback  
**Status:** ✅ Live (Phase 5)

### Layer 3: Skill Forge
**File:** `core/skills/` + `core/skills/os_skills/`  
**What:** Runtime skill generation, packaging, installation  
**Does:** Skeleton → spec convergence → ZIP packaging → install → deploy  
**Key functions:**
- `SkillForge.generate()` — create skill skeleton
- `SkillPackager.package()` — create ZIP
- `SkillInstaller.install()` — deploy to system  
**Status:** ✅ Live (Phase 2–3)

### Layer 4: Notification Daemon
**File:** `corvin_operator/bridges/notification_daemon/`  
**What:** Systemd user service that routes completion events  
**Does:** Monitor `~/.corvin/completion_events/`, format Discord messages, delivery  
**Start/stop:**
```bash
systemctl --user start corvin-notification-daemon
systemctl --user stop corvin-notification-daemon
systemctl --user status corvin-notification-daemon
```
**Status:** ✅ Live (Phase 4)

### Layer 5: Marketplace Hub
**File:** `core/plugins/` + marketplace discovery  
**What:** Central skill repository, discovery, licensing  
**Does:** Index skills, one-click install, license gating  
**Key endpoints:**
- `GET /v1/console/marketplace/index/` — list available skills
- `POST /v1/console/marketplace/install/<skill>` — install skill  
**Status:** ✅ Live (Phase 6)

### Layer 6: Video Producer Director Mode
**File:** `core/skills/os_skills/video_producer/`  
**What:** AI-driven video generation with cinematic optimization  
**Does:** 7-phase pipeline (analyze → storyboard → workers → assemble → upload)  
**Key skill:** `VideoProducerSkill.execute(request: VideoRequest)`  
**Status:** ✅ Live (Phase 5+)

---

## Phase 8 Prerequisites

### Environment Setup

**1. Python 3.8+, pip**
```bash
python3 --version  # should be 3.8+
pip --version
```

**2. Install dependencies**
```bash
pip install -r requirements.txt
pip install -r requirements-dev.txt  # for testing
```

**3. Systemd user services**
```bash
# Enable user-level systemd
loginctl enable-linger  # allows services to run when you log out

# Daemon should auto-start on reboot
systemctl --user enable corvin-notification-daemon
```

**4. Verify services are live**
```bash
curl http://localhost:8765/console/        # Console UI
curl http://localhost:8765/v1/stats/       # Telemetry endpoint
systemctl --user status corvin-notification-daemon  # Daemon
```

### Code Structure

**Key directories:**
- `core/console/` — Console UI (React + FastAPI)
- `core/telemetry/` — Telemetry aggregation
- `core/learning/` — Learning infrastructure (feedback, outcomes)
- `core/skills/` — Skill Forge + skill implementations
- `core/plugins/` — Marketplace + plugin registry
- `corvin_operator/bridges/` — Notification daemon, bridges
- `tests/` — Test suite (160+ tests)

**Key files to read before Phase 8:**
- `CLAUDE.md` — repo conventions, must-NOT-do rules
- `docs/ARCHITECTURE.md` — system design
- `docs/RELEASE-NOTES-PHASE7.md` — what shipped
- `ADR-0516` — ADR centralization rule (Corvin-ADR repo)
- `ADR-0688` — Master Plan (Phases 1–6 design)

---

## Phase 8 Roadmap (Pick One)

### Option A: Real-Time Analytics (WebSocket/SSE)
**Scope:** Add live streaming to telemetry dashboard  
**Effort:** 3–4 weeks  
**Files to touch:** `core/telemetry/`, `core/console/web-next/`  
**Success:** Dashboard updates without page refresh, <500ms end-to-end latency  
**Related ADR:** ADR-0851 (Live Stats Observability) — PROPOSED

### Option B: Historical Time-Series Retention
**Scope:** Extend telemetry storage beyond 7 days  
**Effort:** 2–3 weeks  
**Files to touch:** `core/telemetry/`, storage layer (TBD: DB or S3)  
**Success:** Dashboard shows 30/90-day trends, Prometheus export available  
**Related ADR:** New ADR needed (TBD-###)

### Option C: Enterprise Multi-Channel Notifications
**Scope:** Add Slack, Teams, Email to notification daemon  
**Effort:** 3–4 weeks  
**Files to touch:** `corvin_operator/bridges/notification_daemon/`  
**Success:** Completion events route to multiple channels simultaneously  
**Related ADR:** New ADR needed (TBD-###)

### Option D: Advanced Permission Management
**Scope:** Fine-grained role-based access control (RBAC)  
**Effort:** 4–5 weeks  
**Files to touch:** `core/console/`, `core/plugins/` (registry)  
**Success:** Operators can grant/revoke skill access per user  
**Related ADR:** New ADR needed (TBD-###)

**Recommendation:** Start with **Option A (WebSocket)** — lowest complexity, highest dashboard UX impact.

---

## Testing & Deployment Workflow

### For Phase 8 work:

**1. Create feature branch**
```bash
git checkout -b feature/phase8-websocket
```

**2. Make changes** (loop-driven-engineering, k ≤ 5 iterations)

**3. Run tests**
```bash
# Unit tests
python -m pytest tests/unit/ -v

# Integration tests
python -m pytest tests/integration/ -v

# E2E tests (if applicable)
python -m pytest tests/e2e/ -v
```

**4. Check coverage**
```bash
python -m pytest tests/ --cov=core --cov-report=term-missing
```

**5. Lint & type check**
```bash
ruff check .
mypy core/
```

**6. Create ADR** (before committing)
```bash
# Draft in /home/shumway/projects/Corvin-ADR/decisions/ADR-NNNN-*.md
# Must include: id, status: PROPOSED, depends_on, paths, docs, commits
```

**7. Commit**
```bash
git add .
git commit -m "feat(phase8): description

Implements Option A: WebSocket real-time telemetry.

ADR-NNNN documents the design."

git push origin feature/phase8-websocket
```

**8. Create PR** (for code review)
```bash
gh pr create --title "Phase 8: WebSocket Telemetry" --body "..."
```

---

## Known Issues & Troubleshooting

### Issue 1: Console UI doesn't load (blank page)

**Symptom:** `http://localhost:8765/console/` returns blank page  
**Cause:** React bundle not built, or stale bundle cache  
**Fix:**
```bash
cd core/console/corvin_console/web-next
npm run build  # rebuild frontend
```
Then hard-refresh browser (`Ctrl+Shift+R` / `Cmd+Shift+R`)

### Issue 2: Telemetry endpoint returns 500 error

**Symptom:** `curl http://localhost:8765/v1/stats/` → 500 error  
**Cause:** Telemetry aggregator crashed or database disconnected  
**Fix:**
```bash
# Check logs
tail -f ~/.corvin/logs/console.log

# Restart console
systemctl --user restart corvin-console
```

### Issue 3: Notification daemon not delivering messages

**Symptom:** Completion events emitted but Discord message not received  
**Cause:** Daemon not running, or Discord webhook misconfigured  
**Fix:**
```bash
# Check daemon status
systemctl --user status corvin-notification-daemon

# Check Discord config
cat ~/.corvin/config/discord_webhook.txt  # should contain valid webhook URL

# Restart daemon
systemctl --user restart corvin-notification-daemon
```

### Issue 4: Tests failing after my changes

**Symptom:** `pytest tests/ -v` → some tests red  
**Cause:** Broken dependency, missing fixture, or behavioral change  
**Fix:**
```bash
# Run only failed tests to understand the failure
pytest tests/ -v -k "test_name_of_failed_test" -s

# If you changed a public API, update related tests
# If new feature, add test for it
# See `docs/API.md` for contract changes

# Re-run full suite after fix
pytest tests/ -v
```

See `docs/ops/TROUBLESHOOTING.md` for more issues.

---

## Communication & Handoff

### When starting Phase 8:

1. **Read this memo** (done ✓)
2. **Read CLAUDE.md** (must-NOT-do rules)
3. **Review ADR-0688** (master plan, load-bearing decisions)
4. **Pick Phase 8 option** (A/B/C/D above)
5. **Create ADR** for your feature (PROPOSED status)
6. **Loop (k ≤ 5):** code → test → measure → iterate
7. **Close:** `docs-as-definition-of-done`, commit, create PR

### Support:

- **Questions about system design:** Read `docs/ARCHITECTURE.md` + relevant ADRs
- **Questions about API contracts:** Read `docs/API.md` + code comments
- **Questions about ops/deployment:** Read `docs/ops/DEPLOYMENT.md`
- **Questions about code patterns:** Ask in PR review; establish conventions with maintainer

---

## Compliance Reminders

### Load-Bearing Constraints (DO NOT WEAKEN)

1. **Audit Chain (ADR-0232/0233):** Every event must be logged + hash-chained. Never skip audit.
2. **Tenant Isolation (ADR-0007):** All queries filtered by `tenant_id`. No cross-tenant leakage.
3. **GDPR Compliance (ADR-0297):** No PII in logs, labels, or audit trails. Fail-closed on PII detection.
4. **House-Rules Gate (ADR-0044):** Acceptable-use enforcement, non-disableable.

### When adding Phase 8 features:

- [ ] New API endpoint → update `docs/API.md`
- [ ] New config option → update `docs/CONFIG.md`
- [ ] New CLI command → update `docs/CLI.md`
- [ ] Behavior change → write/update ADR, update code comments
- [ ] New test → explain test intent in docstring
- [ ] Compliance implication → verify ADR-0222 still holds

---

## Next Steps (Start Here)

1. **Verify environment** (5 min)
   ```bash
   python3 --version
   pip --version
   systemctl --user status corvin-notification-daemon
   curl http://localhost:8765/console/
   ```

2. **Read architecture** (10 min)
   - `docs/ARCHITECTURE.md` (system overview)
   - `CLAUDE.md` (repo rules)

3. **Pick Phase 8 option** (5 min)
   - Review A/B/C/D above
   - Decide which adds most value

4. **Create feature branch + ADR** (15 min)
   - `git checkout -b feature/phase8-<option>`
   - Draft ADR in Corvin-ADR/decisions/

5. **Start loop-driven-engineering** (k=1)
   - Make small code change
   - Run tests
   - Iterate ≤5 times
   - Commit

---

## Questions?

- **System design:** See `docs/ARCHITECTURE.md` + ADR-0688
- **API details:** See `docs/API.md`
- **Code patterns:** See `CLAUDE.md` + relevant ADRs
- **Production ops:** See `docs/ops/` + maintainer

---

**Prepared by:** Phase 7 Consolidation (2026-09-17)  
**Status:** Ready for Phase 8 kickoff  
**Version:** v1.0.0-phase7

🚀 **Phase 8 is ready to start. Pick an option and begin!**

Co-Authored-By: Claude Haiku 4.5 <noreply@anthropic.com>
