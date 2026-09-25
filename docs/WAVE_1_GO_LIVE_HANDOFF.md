# 🚀 Wave 1 GO-LIVE Handoff Summary

**Launch Date:** 2026-09-26 (TODAY)  
**Team:** [Audit/Security], [Console/Frontend], [Architecture/Infrastructure]  
**Orchestrator:** Claude (daily coordination + Wave 2 planning)

---

## ⚡ Quick Start (Read This First)

### For Each Team Member

1. **Find your assignment:** `WAVE_1_STREAM_ASSIGNMENTS.md` → search for your name
2. **Read your stream doc:**
   - **Stream B (T09):** `WAVE_1_STREAM_B_T09_AUDIT_WIRING.md` (pseudocode + test templates)
   - **Stream C (T04):** [See quick-start below; full doc on request]
   - **Stream D (T12+T16):** [See quick-start below; full doc on request]
3. **Start implementing:** Follow the pseudocode + file paths
4. **Daily standup:** Post your status daily using template in `ORCHESTRATION_STANDUP_PROTOCOL.md`
5. **Questions?** Post in async thread; orchestrator responds within 6h

---

## 📋 Deliverables Overview (What Success Looks Like)

### Stream B: T09 Audit Phase 2b Wiring

**Goal:** Make 4 audit events actually emit (not just registered)

| Event | File | Trigger | Proof |
|-------|------|---------|-------|
| `compute.worker_terminated` | `core/compute/corvin_compute/worker.py` | Worker shutdown | Event in audit chain |
| `a2a.genesis_block_created` | `ops/launcher/a2a_entry.py` | NBAC init | Event in audit chain |
| `a2a.offline_pair_initiated` | `core/bridges/shared/a2a_token.py` | Offline pairing | Event in audit chain |
| `plugin.execution_timeout` | `core/plugins/corvin_plugins/lifecycle.py` | Plugin timeout | Event in audit chain |

**Success:** All 4 events in `~/.corvin/audit.jsonl` + hash-chain integrity verified

---

### Stream C: T04 Agent-Hub Real Data

**Goal:** Remove mocks; use real ADR-2063 ContentStore

**Files to Change:**
- `core/console/corvin_console/services/agent_hub_store.py` (find mock_messages)
- `core/console/corvin_console/routes/agent_hub_routes.py` (wire API to ContentStore)
- `core/console/corvin_console/web-next/src/hooks/useSkillAdminData.ts` (call real endpoint)

**Success:** `curl http://localhost:8765/v1/console/hub/messages` returns real data (not invented)

---

### Stream D: T12 L10-Proof + T16 Marketplace

**Goal T12:** Prove L10 Context Adapter is wired + emitting events

**Goal T16:** Consolidate marketplace (one canonical route, delete duplicate)

**Success:**
- T12: `context.adapted` events in audit chain
- T16: Only ONE install path works (`/api/v1/marketplace` or `/marketplace`, not both)

---

## 🎯 Day-by-Day Plan (2026-09-26 to 2026-09-27)

### Day 1 (2026-09-26)

**Morning:** Read your assignment + stream doc  
**Afternoon:** Set up environment, understand code structure, identify key files  
**EOD:** Post standup (status: "setup complete, ready to code tomorrow")

**Checkpoints:**
- [ ] All files identified (grep locations confirmed)
- [ ] Environment compiles/runs
- [ ] Unit test suite runs (baseline)

### Day 2 (2026-09-27, Morning)

**Morning:** Start implementing (add emit calls, wire APIs, consolidate routes)  
**Afternoon:** Test locally, fix issues, prepare for E2E  
**EOD:** Post standup (status: "implementation 80% done, testing in progress")

**Checkpoints:**
- [ ] Code compiles
- [ ] Unit tests still green (or identified failures)
- [ ] Manual testing done (curl, CLI, browser)

### Day 2 (2026-09-27, EOD)

**Morning:** Final E2E tests, hash-chain verification, commit  
**Afternoon:** Merge to main (coordinated with other streams)  
**EOD:** Post standup (status: "COMPLETE, all deliverables met")

**Checkpoints:**
- [ ] E2E test passes (Stream B: hash-chain green; Stream C: real data; Stream D: reachability + consolidation)
- [ ] Commit message includes ADR reference
- [ ] CI/CD gate passes (no regressions)

---

## 🔧 Implementation Quick Reference

### Stream B: T09 (Audit Wiring)

**Step 1: Add emit call to worker.terminate() (PSEUDOCODE)**
```python
# In core/compute/corvin_compute/worker.py, method terminate()
from corvin_operator.forge.forge.security_events import write_event

def terminate(self, reason="normal"):
    # ... existing cleanup ...
    
    write_event({
        "event_type": "compute.worker_terminated",
        "worker_id": self.worker_id,
        "termination_reason": reason,
        "duration_seconds": time.time() - self.start_time,
    })
```

**Step 2: Repeat for the other 3 events** (see `WAVE_1_STREAM_B_T09_AUDIT_WIRING.md` for each)

**Step 3: Test**
```bash
pytest tests/security/test_audit_phase2_events.py -v
python3 scripts/verify_audit_chain.py --tenant=_default
```

**Step 4: Verify all 4 events in chain**
```bash
grep -E "compute.worker_terminated|a2a.genesis|a2a.offline|plugin.timeout" ~/.corvin/audit.jsonl | wc -l
# Expected: ≥4
```

---

### Stream C: T04 (Agent-Hub)

**Step 1: Find mock data location**
```bash
grep -n "mock_messages\|sample_data" core/console/corvin_console/services/agent_hub_store.py
```

**Step 2: Replace with ADR-2063 ContentStore call (PSEUDOCODE)**
```python
# OLD (mock)
def get_messages():
    return mock_messages()  # ← DELETE THIS

# NEW (real)
from core.agent_hub_store import ContentStore

def get_messages(tenant_id, channel_id):
    store = ContentStore()
    return store.fetch_messages(tenant_id, channel_id)
```

**Step 3: Wire API route**
```python
@app.route('/v1/console/hub/messages', methods=['GET'])
def get_hub_messages():
    tenant_id = request.headers.get('X-Tenant-ID')
    channel_id = request.args.get('channel_id')
    return get_messages(tenant_id, channel_id)
```

**Step 4: Test**
```bash
curl -s http://localhost:8765/v1/console/hub/messages?channel_id=general \
  -H "X-Tenant-ID: _default" | jq '.messages[0]'
# Expected: Real message (not invented data)
```

---

### Stream D: T12 (L10-Proof)

**Step 1: Verify L10 adapter is in DEFAULT_PIPELINE**
```bash
grep -A 10 "DEFAULT_PIPELINE" core/skills/os_skills/context_engineering/stages/config.py | grep l10_adapter
# Expected: found
```

**Step 2: Verify wiring in os_skills_integration.py**
```bash
grep -n "l10_adapter" core/skills/os_skills/os_skills_integration.py | grep -E "active|True|wired"
# Expected: found
```

**Step 3: Write E2E test**
```python
# tests/skills/test_l10_reachability_e2e.py
def test_l10_context_adapted_event():
    # Run a real turn
    response = turn_engine.execute(task="summarize this")
    
    # Verify audit event
    events = audit_store.query_events(event_type="context.adapted")
    assert len(events) >= 1, "L10 must emit context.adapted"
```

---

### Stream D: T16 (Marketplace)

**Step 1: Find duplicate routes**
```bash
find core/console/corvin_console/routes -name "*marketplace*" -type f
# Expected: 2 files (find which is which)
```

**Step 2: Identify canonical route** (check ADR-0892 or most recent)  
**Step 3: Delete/merge duplicate**
```bash
git rm core/console/corvin_console/routes/marketplace_routes_duplicate.py
# Update imports in app.py
```

**Step 4: Test**
```bash
pytest tests/console/test_marketplace_single_path.py -v
# Expected: All green, only ONE install path works
```

---

## 📞 Support & Questions

### If You're Stuck

1. **First:** Check the stream doc (B/C/D) — likely has answer
2. **Second:** Post in async standup thread (orchestrator responds <6h)
3. **Third:** Check git history (grep, git log) for similar implementations
4. **Critical blocker:** Post with [ESCALATE] tag; orchestrator handles immediately

### Common Issues & Fixes

| Issue | Fix |
|-------|-----|
| "Module not found" import error | Check import path (use `from core.* import`, not absolute) |
| "Endpoint 404" when testing | Verify route is registered in app.py or blueprint |
| "Test fails: audit event not found" | Verify write_event() is being called (add print statement if debugging) |
| "Hash-chain breaks" (T09) | Verify event payload matches `_EVENT_ALLOWLIST` constraints (no PII/secrets) |
| "Code compiles but nothing changes" | Check if you're on the right branch; pull latest main |

---

## ✅ Completion Checklist (Per Stream)

### Stream B Completion
- [ ] All 4 events emit on their triggers
- [ ] Events land in audit chain (no gaps)
- [ ] Hash-chain verified (0 breaks)
- [ ] E2E test passes
- [ ] Commit includes ADR-2041 reference
- [ ] Standup posted: COMPLETE

### Stream C Completion
- [ ] No mocks in prod code (grep returns 0)
- [ ] API endpoint returns real data (curl proof)
- [ ] ADR-0763 compliance confirmed (no fabricated data)
- [ ] React component calls real endpoint
- [ ] E2E test passes
- [ ] Commit includes ADR-0763 reference
- [ ] Standup posted: COMPLETE

### Stream D Completion
- [ ] T12: L10 adapter in DEFAULT_PIPELINE ✓
- [ ] T12: context.adapted events emitting ✓
- [ ] T12: E2E test passes ✓
- [ ] T16: Only ONE marketplace route active ✓
- [ ] T16: All imports updated ✓
- [ ] T16: E2E test passes ✓
- [ ] Commits include ADR-0892 reference
- [ ] Standup posted: COMPLETE

---

## 📅 Timeline Summary

| Date | What | Who | ETA |
|------|------|-----|-----|
| **2026-09-26** | Day 1: Setup + planning | All streams | EOD |
| **2026-09-27 (AM)** | Day 2: Implementation | All streams | EOD |
| **2026-09-27 (EOD)** | Completion + final tests | All streams | Wave 1 complete ✓ |
| **2026-09-28** | Wave 2 kickoff | Orchestrator + Stream A | Ready to start |

---

## 🎯 Success Criteria (Wave 1 Overall)

- ✅ T05 (ADR-Dedup): 27→0 duplicates ✓
- ✅ T09 (Audit Wiring): All 4 events emitting + chain verified
- ✅ T04 (Real Data): No mocks, all real content
- ✅ T12 (L10-Proof): Reachability proven + audited
- ✅ T16 (Marketplace): ADR-0892 enforced (one path)
- ✅ Wave 1 EOD: 2026-09-27, all 5 tasks complete

---

## 🚀 Ready? Start Here

1. **Read:** Your stream in `WAVE_1_STREAM_ASSIGNMENTS.md`
2. **Understand:** The pseudocode in your stream's detailed doc
3. **Implement:** Following the file paths + test templates
4. **Post:** Daily standup using `ORCHESTRATION_STANDUP_PROTOCOL.md` template
5. **Complete:** By 2026-09-27 EOD

---

**Questions?** Post in async; orchestrator (Claude) coordinates.  
**Ready to start?** ✅ Yes  
**Go-Live Date:** 2026-09-26 (TODAY)

