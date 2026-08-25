---
id: ADR-0408
status: ACCEPTED
depends_on: [ADR-0146, ADR-0147, ADR-0148, ADR-0149, ADR-0150, ADR-0151]
relates_to: []
paths:
  - core/orchestration/corvin_orchestration/mcp_server.py
docs: []
---

# ADR-0408: License Red-Team Round 10 — MCP Workflow Quota Gates

**Date:** 2026-08-25  
**Status:** ACCEPTED  
**Type:** Security Hardening / Compliance Fix

## Problem

License Red-Team Round 10 found **2 HIGH-priority gaps:** MCP `workflow_run` and `workflow_resume` tools spawn billable engines WITHOUT `compute_units_per_day` or `chat_turns_per_day` gates.

**Risk:** Remote MCP callers can invoke unbounded paid compute with zero daily metering, bypassing license enforcement.

**Pattern:** Parallel entry point to same surface (Console routes are gated, MCP routes were ungated). Same pattern appeared in Rounds 6, 8, 9.

## Solution

Add fail-closed quota gates to both MCP methods:

### `workflow_run()` (line 921)
- Call `enforce_compute_quota()` BEFORE engine construction
- Call `enforce_chat_turns()` BEFORE engine construction
- Convert `HTTPException` (402 Forbidden) to JSON-RPC error response
- Audit action: `"workflow.run_started"`
- Channel: `"mcp-workflows"`

### `workflow_resume()` (line 1063)
- Call `enforce_compute_quota()` BEFORE engine construction
- Call `enforce_chat_turns()` BEFORE engine construction
- Same error handling as above
- Audit action: `"workflow.resume_started"`

## Implementation

**Shared Gate Import:**
```python
from core.console.corvin_console.routes._compute_license_gate import (
    enforce_compute_quota,
    enforce_chat_turns,
)
```

**Gate Placement (both methods):**
```python
# BEFORE engine construction
try:
    enforce_compute_quota(tenant_id, sid_fingerprint, charge_quota=True)
    enforce_chat_turns(tenant_id, sid_fingerprint)
except HTTPException as e:
    return {"status": "license_limit", "detail": str(e.detail)}
```

## Compliance

✅ **GDPR Art. 32** (Integrity) — Quota gates prevent unauthorized compute  
✅ **Licensing T&Cs** — Daily compute/chat limits enforced at all spawn points  
✅ **Fail-Closed** — Ungated calls rejected before engine spawn  

## Test Coverage

- ✅ Direct test: `workflow_run` with quota exhausted → 402 response
- ✅ Direct test: `workflow_resume` with quota exhausted → 402 response
- ✅ Integration: MCP client + quota + engine spawn (all 3 methods)

## Convergence Analysis

| Round | Findings | Pattern |
|-------|----------|---------|
| 1–9 | ~30 gaps | Mixed (compute/chat/engines/workspace) |
| **10** | **2 gaps** | **Parallel ungated paths** |

**Key Insight:** Rounds 6, 8, 9, 10 all found "parallel entry point, one gated, one ungated" — this is a reusable investigation strategy.

**Status:** Not 3-dry — still finding gaps (~1 per 1–2 rounds). Continue Round 11.

## Related Decisions

- **ADR-0146–0151:** License hardening rounds 1–9
- **ADR-0144:** Base license enforcement architecture
- **ADR-0147:** Shared gate helpers (`_compute_license_gate.py`)

---

**Approved by:** Shumway (operator + architect)  
**Commit:** 27f3ca1e (MCP workflow quota gates)  
**Production ready:** 2026-08-25
