# Phase 0: Blocker Resolution (2026-09-26 to 2026-09-27)

**Master Plan for Phase 6 Blockers → Phase B Launch Gate**  
**ADR:** ADR-2065 (Autonomous Orchestration Master)  
**Status:** ACTIVE  
**Quality Gate:** All 3 blockers resolved + tested before Phase 1 starts

---

## Blocker 1: Watchdog Timer Installation Missing

**File:** `install.sh` (line ~312)  
**Impact:** Fresh installs lack service healing  
**Duration:** 1 hour + 30min test

### Implementation Checklist

```
STEP 1: Verify Current install.sh
  [ ] Read: /home/shumway/projects/CorvinOS/install.sh (lines 300-320)
  [ ] Check: Is watchdog timer block present?
  [ ] Reference: /corvin_operator/bridges/shared/systemd/corvin-voice-bridge-watchdog.*

STEP 2: Restore Watchdog Block
  [ ] Copy from main history (git show main:install.sh | grep -A 20 "watchdog")
  [ ] Insert after L312 (systemd timer registration)
  [ ] Test: Unit file syntax (systemd-analyze verify)

STEP 3: E2E Test
  [ ] Fresh install in temp directory
  [ ] Check: systemctl --user is-active corvin-voice-bridge-watchdog.timer
  [ ] Verify: watchdog sends heartbeat signals (logs)
  [ ] Rollback if: any startup failures

STEP 4: Documentation
  [ ] Update: INSTALLATION.md (watchdog feature)
  [ ] Commit: "fix(install): restore watchdog timer installation [skip-adr-check]"
```

**Quality Gate:** E2E Test must pass (real fresh install, not mocked)

---

## Blocker 2: Docker Uninstall Coverage Missing

**File:** `corvin-uninstall` script  
**Impact:** Docker deployments can't cleanly uninstall  
**Duration:** 1 hour + 30min test

### Implementation Checklist

```
STEP 1: Audit Current corvin-uninstall
  [ ] Read: /home/shumway/projects/CorvinOS/corvin-uninstall
  [ ] Check: Only systemd cleanup? Docker section missing?
  [ ] Reference: Docker service files in /corvin_operator/bridges/shared/docker/

STEP 2: Add Docker Mode Detection
  [ ] Detect: systemctl --user list-unit-files | grep -i docker
  [ ] If present: Add Docker cleanup section
  [ ] Cleanup actions:
    - Stop docker service: docker compose down
    - Remove data: rm -rf ~/.corvin/docker-volumes/
    - Uninstall: docker system prune -a

STEP 3: Test Both Modes
  [ ] Systemd uninstall: systemctl --user status corvin-*
  [ ] Docker uninstall: docker ps (should be empty)
  [ ] Verify: ~/.corvin/ cleaned up
  [ ] Rollback if: any cleanup failures

STEP 4: Documentation
  [ ] Update: UNINSTALL.md (Docker mode)
  [ ] Commit: "fix(uninstall): add docker mode detection + cleanup [skip-adr-check]"
```

**Quality Gate:** E2E Test must pass (Docker system cleanup verified)

---

## Blocker 3: Credential Rotation Phase 1 (Parallel)

**Owner:** Operator (Phase 1) + Claude (Phase 2)  
**Impact:** Security key rotation  
**Duration:** 1 hour operator + 1 hour Claude (parallel)

### Implementation Checklist

```
STEP 1: Operator Phase 1 (Credential Revocation)
  [ ] Identify: All 14 old credentials in ~/.corvin/
  [ ] Revoke each: `corvin credential revoke <cred_id>`
  [ ] Verify: ~/.corvin/credentials_revoked.json created
  [ ] Signal: Operator posts "Phase 1 COMPLETE" → triggers Phase 2

STEP 2: Claude Phase 2 (Key Rotation + Re-issuance)
  [ ] Monitor: credentials_revoked.json exists
  [ ] Action: Generate new credentials
  [ ] Install: Update ~/.corvin/.credentials.json
  [ ] Verify: All services restart with new creds
  [ ] Test: Cross-service auth (A2A peer connectivity)

STEP 3: Audit & Cleanup
  [ ] Audit: Verify all old creds in audit trail (marked revoked)
  [ ] Cleanup: Archive old credentials to ~/.corvin/archive/
  [ ] Log: "Credential rotation Phase 1+2 COMPLETE"

STEP 4: Documentation
  [ ] Update: CREDENTIAL-ROTATION.md
  [ ] Commit: "chore(credentials): rotate old keys [skip-adr-check]"
```

**Quality Gate:** All services restart with new credentials, A2A connectivity maintained

---

## Phase 0 Execution Schedule

```
2026-09-26 (TODAY)
  09:00 — Blocker 1: Watchdog Fix (1.5h)
  10:30 — Blocker 2: Docker Uninstall (1.5h)
  12:00 — Blocker 3 Phase 1: Operator Revokes Creds (1h, parallel with above)
  13:00 — Blocker 3 Phase 2: Claude Rotates Keys (1h, triggered by Phase 1)
  14:00 — Sync Point: All 3 blockers green
  14:30 — Quality Gate Verification (30min)

2026-09-27 (OPTIONAL BUFFER)
  If any blocker fails: Retry + escalate
```

---

## Phase 0 Success Criteria

| Criterion | Measurement | Target |
|---|---|---|
| **Watchdog** | Fresh install → timer active | ✅ YES |
| **Docker** | Docker uninstall → clean | ✅ YES |
| **Credentials** | New creds issued + services restart | ✅ YES |
| **Audit Trail** | All blockers logged | ✅ 100% |
| **Zero Escalations** | No hard gates failed | ✅ YES |

---

## Phase 1 Unblock Gate

**Once Phase 0 is COMPLETE:**

```
GATE CHECKS:
  ✓ Watchdog timer installed + active
  ✓ Docker uninstall verified
  ✓ New credentials issued
  ✓ All services healthy
  ✓ Audit trail complete

THEN: Phase 1 "Quality Gates Activation" starts
  → ADRGate, ConceptGate, PlanGate all wired
  → Audit events flowing to audit_backend
  → Ready for Phase 2 (Phase B Orchestration)
```

---

## Handoff: Next Session

**If Phase 0 completes this session:**

1. **Commit Changes**
   ```bash
   git add install.sh corvin-uninstall
   git commit -m "fix(phase-0): resolve 3 blockers — watchdog + docker + credentials [ADR-2065]"
   git push origin main
   ```

2. **Start Phase 1 (Quality Gates Activation)**
   - ADR-0688 validators (ADRGate, ConceptGate, etc.)
   - Wiring to audit_backend
   - Console endpoints

3. **Prepare Phase B (18 Initiatives)**
   - Task Orchestrator DAG construction
   - Worker pools (A/B/C/D streams)
   - Heuristics store initialization

---

**Status: READY TO EXECUTE**  
**Current Token Budget:** 14.9M / 15M  
**Checkpoint Interval:** Every hour + final verification
