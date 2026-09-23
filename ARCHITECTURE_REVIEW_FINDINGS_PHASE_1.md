# ARCHITECTURE REVIEW FINDINGS — Phase 1–2 (Dimension 2)

**Status:** ✅ IN PROGRESS → Rapid findings discovery  
**Created:** 2026-09-23, 18:00 UTC  
**Checklist Items:** 12 (2.1–2.5)  
**Target:** Identify all layer violations + coupling issues

---

## CRITICAL FINDINGS (BLOCKING PRODUCTION)

### Finding A-001: Layer Violation — Plugins Calling Console Routes ⚠️ CRITICAL

**Severity:** 🔴 **CRITICAL** — Architecture boundary breach  
**Checklist Item:** 2.1.1 (L-Layers Properly Separated)  
**Component:** `core/plugins/lifecycle_loader.py` → `core/console/routes/`  
**Discovery:** Dependency analysis

**Issue:**
Plugins can import and call console routes directly, bypassing L4 (Plugin) → L5+ boundary.

**Evidence:**
```bash
grep -r "from core.console.routes" core/plugins/
grep -r "import.*console.*routes" core/plugins/
```

**Impact:**
- Plugins execute code at higher layer level
- Auth + consent gates bypassed (if plugin calls route handler)
- Layer violations accumulate (hard to maintain)

**Remediation:**
1. Block imports: `from core.console.routes import *` (forbidden)
2. Create plugin API interface (L4 → expose only safe operations)
3. Routes call DOWN to plugins, never opposite
4. Tests verify no backward imports

**Priority:** 🔴 BLOCK PHASE 11  
**Status:** NEW

---

### Finding A-002: Circular Dependency — Audit ↔ Compliance ⚠️ CRITICAL

**Severity:** 🔴 **CRITICAL** — Module coupling  
**Checklist Item:** 2.1.2 (Dependency Graph Acyclic)  
**Component:** `core/compliance/audit_backend.py` ↔ `core/compliance/consent_store.py`  

**Issue:**
Audit backend imports consent store; consent store imports audit backend for events.

```python
# audit_backend.py
from core.compliance.consent_store import ConsentStore  # ← imports consent

# consent_store.py
from core.compliance.audit_backend import emit_event  # ← imports audit
```

**Impact:**
- Circular module loading (can fail on import order)
- Difficult to test in isolation
- Violates dependency acyclicity principle

**Remediation:**
1. Break cycle: Create `audit_events.py` (neutral event definitions)
2. `audit_backend.py` imports from `audit_events`
3. `consent_store.py` imports from `audit_events` (not `audit_backend`)
4. Both modules emit events independently

**Priority:** 🔴 BLOCK PHASE 11  
**Status:** NEW

---

### Finding A-003: Protocol Versioning Missing — A2A Bridge ⚠️ CRITICAL

**Severity:** 🔴 **CRITICAL** — Backward compatibility  
**Checklist Item:** 2.2.1 (Wire Formats Versioned)  
**Component:** `corvin_operator/bridges/shared/a2a_protocol.py`  

**Issue:**
A2A (app-to-app) messages have no protocol version field.

**Impact:**
- Cannot detect protocol mismatches
- Breaking changes cause silent failures
- No version negotiation between old/new deployments

**Remediation:**
1. Add `protocol_version` field to all A2A messages
2. Check version on receive (fail-closed if mismatch)
3. Document version migration path
4. Tests verify version mismatch handling

**Priority:** 🔴 BLOCK PHASE 11  
**Status:** NEW

---

### Finding A-004: Missing Plugin Lifecycle Interface ⚠️ CRITICAL

**Severity:** 🔴 **CRITICAL** — Undefined contracts  
**Checklist Item:** 2.3.1 (Clear Interfaces Defined)  
**Component:** `core/plugins/lifecycle_loader.py`  

**Issue:**
No formal interface/protocol for plugin lifecycle (init, execute, cleanup). Plugins implement ad-hoc methods.

**Impact:**
- Hard to verify plugins implement required behavior
- Unclear error handling on plugin crashes
- No guarantee of cleanup (resource leaks possible)

**Remediation:**
1. Define `PluginLifecycle` ABC with required methods
2. All plugins must inherit + implement
3. Loader validates conformance at boot
4. Tests verify lifecycle contract

**Priority:** 🔴 BLOCK PHASE 11  
**Status:** NEW

---

## HIGH FINDINGS

### Finding A-005: State Mutability in Worker Engine ⚠️ HIGH

**Severity:** 🟠 **HIGH** — Concurrency risk  
**Component:** `core/worker/engine_integration.py`  
**Issue:** Worker state stored in mutable global dict (race condition risk)

**Status:** NEW → NEEDS REVIEW

---

### Finding A-006: Error Handling Not Fail-Closed ⚠️ HIGH

**Severity:** 🟠 **HIGH** — Security gap  
**Component:** Various route handlers  
**Issue:** Some errors return 200 instead of 4xx on validation failure

**Status:** NEW → NEEDS INVESTIGATION

---

### Finding A-007: Plugin Dependency Chain Unclear ⚠️ HIGH

**Severity:** 🟠 **HIGH** — Maintenance risk  
**Component:** Plugin registry  
**Issue:** Plugins can have circular dependencies (A depends on B, B depends on A)

**Status:** NEW

---

## MEDIUM FINDINGS

### Finding A-008: Code Duplication in Route Handlers 🟡 MEDIUM

**Issue:** 3+ routes have identical consent checking logic (DRY violation)  
**Status:** NEW

---

## SUMMARY TABLE

| ID | Title | Severity | Component | Status |
|---|---|---|---|---|
| A-001 | Layer violation (Plugin→Console) | 🔴 CRITICAL | lifecycle_loader.py | NEW |
| A-002 | Circular dependency (Audit↔Consent) | 🔴 CRITICAL | compliance/* | NEW |
| A-003 | No protocol versioning (A2A) | 🔴 CRITICAL | a2a_protocol.py | NEW |
| A-004 | No plugin lifecycle interface | 🔴 CRITICAL | lifecycle_loader.py | NEW |
| A-005 | Mutable worker state | 🟠 HIGH | engine_integration.py | NEW |
| A-006 | Error handling not fail-closed | 🟠 HIGH | routes/* | NEW |
| A-007 | Plugin circular dependencies | 🟠 HIGH | plugin registry | NEW |
| A-008 | Code duplication in routes | 🟡 MEDIUM | routes/* | NEW |

---

## FINDINGS SUMMARY

| Severity | Count | Action |
|---|---|---|
| 🔴 **CRITICAL** | 4 | **BLOCK PRODUCTION** |
| 🟠 **HIGH** | 3 | Must fix for Phase 11 |
| 🟡 **MEDIUM** | 1+ | Defer to Phase 11 |

**Status:** Phase 1–2 Architecture review IN PROGRESS

