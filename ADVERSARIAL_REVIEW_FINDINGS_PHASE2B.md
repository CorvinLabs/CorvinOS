---
title: "Adversarial Review Findings: Phase 2B (ADR-0700-0704)"
date: 2026-09-19
severity: BLOCKING (3 CRITICAL findings)
total_findings: 10 (3 CRITICAL, 4 HIGH, 3 MEDIUM)
effort_estimate: "15–25h total; 9.5h critical path (P0 only)"
---

# Adversarial Review — Phase 2B (ADR-0700-0704) Findings

**Status:** k=1-5 LDD Review Complete  
**Date:** 2026-09-19  
**Gate:** Release-BLOCKED until P0 findings resolved  

---

## Executive Summary

Phase 2B (Licensing 1.0, Video Producer, Console E2E) delivered **33 components** and **231 E2E tests** but has **3 CRITICAL compliance gaps**:

1. **G3 Forge gates not implemented** — Free-tier users bypass member-only forge.create via console UI (GDPR/licensing violation)
2. **core/license directory not deleted** — Dead code violates spec (ADR-0703 explicitly states "deleted")
3. **GATE 2 E2E proof identified missing gates** — Only 2/5 chokepoints actually gated (incomplete implementation)

**Release Decision:** Do NOT ship until P0 resolved. This is a **compliance violation** (free users can access member-only features).

---

## Critical Findings (P0 — Release Blocking)

### FINDING 1: G3 License Gates Not Implemented (CRITICAL)

**SEVERITY:** CRITICAL  
**SPEC:** ADR-0701 §3, Chokepoint G3  
**CODE PATH:** `/core/console/corvin_console/routes/skill_creator_api.py`, `skills_manual.py`, `promote.py`, `panels.py`  
**STATUS:** OPEN — Requires Fix

#### Description

The `require_forge_capability` gate is defined in `core/console/corvin_console/routes/license_gates.py` but **NEVER IMPORTED OR USED** in any console routes. ADR-0701 §3 mandates:

> "G3 is required for: POST /skill-creator/generate, POST/PUT /skills/manual, POST /tools/{name}/promote, POST /skills/{name}/promote, POST /panels"

**Current State:** All routes are UNGUARDED. Free-tier users can:
1. Log into console
2. Visit `/console/skill-creator`
3. Submit skill generation → **202 Accepted (should be 402 Forbidden)**
4. Forge a new skill without purchasing membership

#### Evidence

```bash
# No imports of require_forge_capability in console routes:
$ grep -r "require_forge_capability" /core/console/corvin_console/routes/*.py
# Result: 0 matches (only in license_gates.py, never used)

# Skill-Creator route (skill_creator_api.py:203-230):
async def generate_skill(
    req: SkillGenerationRequest,
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],  # ← CSRF only, no license check
) -> Dict[str, Any]:
    # No Depends(require_forge_capability) here
```

#### Impact

- **Licensing Violation:** Free users gain member-only feature (forge.create)
- **Revenue Leakage:** Bypass Paddle paywall entirely
- **Compliance:** GDPR Art. 6 (lawful basis) + EU AI Act Art. 50 (disclosure) may be violated
- **GATE 2 Indicator:** This was identified in commit `3b801860` as "⚠️ MISSING"; fix wasn't applied

#### How to Fix

**Fix:** Add `Depends(require_forge_capability)` to all 5 routes

**File:** `/core/console/corvin_console/routes/skill_creator_api.py`
```python
# Before:
async def generate_skill(
    req: SkillGenerationRequest,
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
) -> Dict[str, Any]:

# After:
async def generate_skill(
    req: SkillGenerationRequest,
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
    _: Annotated[CapabilityDecision, Depends(require_forge_capability)],  # ← ADD THIS
) -> Dict[str, Any]:
```

**Test:** E2E test must verify:
```python
# test_license_g3_gates_e2e.py
def test_free_tier_forge_denied():
    free_cred = create_free_license()
    browser.login(free_cred)
    browser.navigate("/console/skill-creator")
    browser.click("#generate-skill")
    assert browser.status_code == 402  # Forbidden, not 202
    assert "membership required" in browser.text()
```

**Effort:** 1–2 hours (apply gate to 5 routes + E2E test)

**Commit:** `fix(license-g3): implement forge.create gate in console routes [ADR-0701]`

---

### FINDING 2: core/license Directory Not Deleted (CRITICAL)

**SEVERITY:** CRITICAL  
**SPEC:** ADR-0703 §2  
**CODE PATH:** `/core/license/` (entire directory)  
**STATUS:** OPEN — Requires Deletion

#### Description

ADR-0703 §2 explicitly states: *"One client package: operator/license pruned; **core/license/corvin_license and its /v1/license router retired (ADR-0703)**."*

The directory still exists:
```
core/license/
├── corvin_license/
├── models/
├── quota_enforcer.py
├── tier_enforcer.py
└── tests/
```

Per ADR-0703 frontmatter `paths: [core/license/** # deleted]`, this directory should **not exist**.

#### Impact

- **Spec Violation:** ADR-0703 explicitly marks it as "deleted"
- **Code Confusion:** Developers may import from old `core.license` instead of `operator.license`
- **Dead Code:** Routes may still be registered, consuming import time
- **Single Source of Truth:** operator/license is canonical per spec

#### How to Fix

**Fix:** Delete entire directory + verify no imports

```bash
# 1. Delete directory
rm -rf /home/shumway/projects/CorvinOS/core/license/

# 2. Verify no imports remain
grep -r "from core\.license\|from core/license\|import core.license" /core --include="*.py"
# Expected: 0 matches

# 3. Verify no routes reference it
grep -r "core.license\|core/license" /operator --include="*.py"
# Expected: 0 matches
```

**Commit:** `chore(license): delete deprecated core/license directory [ADR-0703]`

**Effort:** 30 minutes

---

### FINDING 3: GATE 2 E2E Proof Identified Missing Forge Gates (CRITICAL)

**SEVERITY:** CRITICAL  
**SPEC:** ADR-0701 §3, Chokepoints G1–G5  
**CODE PATH:** Commit `3b801860` (GATE 2 message)  
**STATUS:** OPEN — Requires Verification & Completion

#### Description

Commit `3b801860` (GATE 2: E2E Wiring Proof) has message: *"⚠️ Forge gates: MISSING (ADR-0701 G1–G5 not yet wired)"*

Yet this commit is listed as "complete" in ADR-0700-0704 frontmatter. The gap persists:

| Gate | Requirement | Status |
|------|---|---|
| **G1** | MCP server (forge_tool, forge_promote) | ✅ Wired |
| **G2** | SkillRegistry.create gate | ✅ Wired |
| **G3** | FastAPI console routes | ❌ **NOT WIRED** (Finding 1) |
| **G4** | /plugin-builder chat path | ⚠️ Needs verification |
| **G5** | Brain v0.2 quota_gate | ⚠️ Needs verification (may be dead code) |

**Current State:** Only 2/5 gates actually enforce forge.create.

#### Impact

- **Incomplete Implementation:** ADR-0701 §3 requires G1–G5 "all required" — only 2 wired
- **Release Blocker:** GATE 2 itself identified the gap; saying "complete" is false
- **Compliance:** Multiple paths bypass member-only gate

#### How to Fix

**Fix 1:** Implement G3 (above, Finding 1)

**Fix 2:** Verify G4 (/plugin-builder)

```bash
# Check if plugin-builder path checks forge.create
grep -n "require_forge_capability\|check_forge_enabled" /operator/chat/plugin_builder_handler.py
# If 0 matches: add gate
```

**Fix 3:** Verify G5 (Brain v0.2 quota_gate)

```bash
# Check if Brain quota_gate is still used
grep -r "brain.*quota_gate\|quota_gate.*brain" /core --include="*.py"
# If used: ensure it checks forge.create
# If unused: delete (dead code)
```

**Effort:** 2–3 hours (verify all gates + add missing ones)

---

## High Findings (P1 — Should Fix Before Release)

### FINDING 4: require_capability() Exception Type Mismatch (HIGH)

**SEVERITY:** HIGH  
**SPEC:** ADR-0703 §2, Fail Contract  
**CODE PATH:** `/corvin_operator/skill-forge/skill_forge/registry.py` line 147–165  
**STATUS:** OPEN

#### Description

The G2 gate calls `require_capability()` which **raises LicenseDenied on denial**, but the calling code catches and re-raises as ValueError:

```python
# skill_forge/registry.py:147–165
try:
    decision = require_capability("forge.create", ...)
    if not decision.allowed:  # ← UNREACHABLE (require_capability already raised)
        raise LicenseDenied(...)
except LicenseDenied as e:
    raise ValueError(f"license_required: {e}") from e  # ← WRONG TYPE
```

#### Impact

- Exception type mismatch confuses error handlers
- The `if not decision.allowed:` check is dead code
- MCP server and CLI may handle ValueError differently than LicenseDenied

#### Fix

**Fix:** Make `require_capability()` behavior explicit

```python
# capability_api.py — clarify behavior
def require_capability(capability: str, ...) -> CapabilityDecision:
    """
    Returns decision if allowed, RAISES LicenseDenied if denied.
    Caller does not check result.allowed.
    """
    decision = ...
    if decision.decision == Decision.DENY:
        raise LicenseDenied(...)
    return decision  # Caller can ignore this return
```

**Update calling code:**
```python
# skill_forge/registry.py
try:
    _decision = require_capability("forge.create", ...)  # Raises on deny
    # If we reach here, allowed is guaranteed
except LicenseDenied as e:
    raise LicenseDenied(f"forge disabled for tier: {e}") from e  # Correct type
```

**Effort:** 45 minutes

---

### FINDING 5: Audit Events Marked But Not Confirmed as Emitted (HIGH)

**SEVERITY:** HIGH  
**SPEC:** ADR-0701 §2, ADR-0702 §1, ADR-0704 §1  
**CODE PATH:** Multiple audit emission points  
**STATUS:** OPEN

#### Unconfirmed Audit Events

| ADR | Event | Expected at | Confirmed? |
|-----|-------|---|---|
| ADR-0701 | `forge.artifact_provenance_signed` | artifact sign-off point | ❓ |
| ADR-0701 | `forge.artifact_provenance_invalid` | signature validation | ❓ |
| ADR-0702 | `a2a.member_credential_verified` | pair handshake | ❓ |
| ADR-0702 | `a2a.member_credential_rejected` | pair handshake | ❓ |
| ADR-0702 | `a2a.crl_stale` | CRL check | ❓ |
| ADR-0704 | `features.seat.fp_change_deferred` | device fingerprint change | ❓ |

#### Impact

- **Audit Trail Incomplete:** GDPR Art. 30 requires complete record
- **Compliance Reports Unreliable:** Cannot verify all decisions
- **Hash Chain Gaps:** If events aren't logged, chain integrity is compromised

#### Fix

**Fix:** Verify each event is actually emitted in production

```bash
# For each event, search production code (not tests):
grep -r "emit.*forge.artifact_provenance_signed" /core /operator --include="*.py"
grep -r "emit.*a2a.member_credential_verified" /core /operator --include="*.py"
# etc.

# If missing, add emission at decision point (FAIL-CLOSED: log first, then execute)
```

**Effort:** 2–3 hours

---

### FINDING 6: Device Fingerprint File Mode 0600 Not Verified (HIGH)

**SEVERITY:** HIGH  
**SPEC:** ADR-0700 §1 (device_id "persisted 0600")  
**CODE PATH:** `/corvin_operator/license/device_fp.py`  
**STATUS:** OPEN

#### Description

ADR-0700 §1 requires: *"random device_id persisted 0600 under corvin_home()/global/license/"*

The file exists but:
1. File mode 0600 **not verified in code** (only stated in spec)
2. **No chmod() or os.open() call found** with 0o600
3. **Rotation/cleanup policy unclear** — persists forever?

#### Impact

- **PII Exposure:** If file is world-readable (default umask), device_id is exposed
- **GDPR Violation:** Personal data must be protected (Art. 5, 32)
- **Clone Detection Broken:** If device_id doesn't persist securely, clone detection fails

#### Fix

**Fix:** Verify and enforce file mode 0600

```python
# device_fp.py
def _write_device_id(device_id: str, path: Path) -> None:
    """Write device_id with mode 0600 (owner read/write only)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    # Use os.open() to create with 0600 atomically:
    fd = os.open(path, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
    try:
        os.write(fd, device_id.encode())
    finally:
        os.close(fd)
    
    # Verify mode (fail-closed)
    mode = stat(path).st_mode & 0o777
    assert mode == 0o600, f"Device ID file mode is {oct(mode)}, expected 0o600"
```

**Test:**
```python
def test_device_id_file_mode():
    path = tmp_path / "device_id"
    write_device_id("abc123", path)
    mode = stat(path).st_mode & 0o777
    assert mode == 0o600  # Owner read/write only
```

**Effort:** 1–2 hours

---

### FINDING 7: Commit Traceability Incomplete (HIGH)

**SEVERITY:** HIGH  
**SPEC:** ADR-0264, ADR-0700-0704 frontmatter `commits:` field  
**CODE PATH:** Corvin-ADR/decisions/  
**STATUS:** OPEN

#### Description

All Licensing ADRs (0700–0704) share the same 3 commits:
```yaml
commits:
  - 3b801860  # GATE 2
  - 5cc9e2e0  # GATE 3
  - 5c21af42  # GATE 4
```

But:
1. **ADR-0704 has no commits field** (checked frontmatter)
2. **Unclear if these 3 commits implement ALL 4 ADRs** or just one
3. **Other commits may be missing** from the list

#### Impact

- Git history doesn't fully trace implementation
- Someone cannot answer "what commits implement ADR-0702?" via git
- Violates ADR-0264 requirement: "commits field is complete record"

#### Fix

**Fix:** Complete commit traceability

```bash
# Find all commits implementing each ADR
git log --all --oneline | grep -E "ADR-0700|ADR-0701|ADR-0702|ADR-0703|ADR-0704|phase2|licensing"

# Update ADRs with complete commit list
# For each ADR: collect all commits touching its `paths:` files + ADR name in message
```

**Effort:** 1 hour

---

## Medium Findings (P2 — Technical Debt)

### FINDING 8: Playwright E2E Tests May Not Exercise Actual Gate Denial (MEDIUM)

### FINDING 9: CAPABILITIES Matrix Synchronization Not Automated (MEDIUM)

### FINDING 10: A2A Member Credential Verification Path Incomplete (MEDIUM)

---

## Summary Table

| # | Finding | Severity | Effort | Blocker |
|---|---------|----------|--------|---------|
| 1 | G3 gates not implemented | 🔴 CRITICAL | 1–2h | YES |
| 2 | core/license not deleted | 🔴 CRITICAL | 0.5h | YES |
| 3 | GATE 2 gap (G1-G5 partial) | 🔴 CRITICAL | 2–3h | YES |
| 4 | Exception type mismatch | 🟠 HIGH | 0.75h | NO |
| 5 | Audit events unconfirmed | 🟠 HIGH | 2–3h | NO |
| 6 | Device FP mode 0600 | 🟠 HIGH | 1–2h | NO |
| 7 | Commit traceability | 🟠 HIGH | 1h | NO |
| 8 | E2E test rigor | 🟡 MEDIUM | 2–3h | NO |
| 9 | CAPABILITIES sync | 🟡 MEDIUM | 2–4h | NO |
| 10 | A2A MC verification | 🟡 MEDIUM | 2–3h | NO |

**Critical Path:** P0 (Findings 1–3) = **3.5–7.5 hours**  
**Full Release-Ready:** P0 + P1 (Findings 1–7) = **10–15 hours**

---

**Report Status:** ✅ Complete — Ready for Recovery Plan Implementation
