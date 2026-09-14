# Phase 1.2 — Gate Re-pointing Handover (ADR-0703)

**Status:** MAPPING COMPLETE — Implementation Partial (Top 5 files done, 8 files mapped for Phase 1.2 continuation)

## Completed (Top 5 Files)

✅ `core/console/corvin_console/routes/compute.py` (3 sites: lines 1033, 2944, 2773)
✅ `core/console/corvin_console/routes/compute_jobs.py` (1 site: line 130)
✅ `core/console/corvin_console/routes/chat.py` (1 site: line 698)
✅ `core/console/corvin_console/routes/voice.py` (3 sites: lines 1177, 1433, 1712)
✅ `core/console/corvin_console/routes/assistant.py` (1 site: line 231)

**Total Completed:** 9 of 20 gate calls

## Mapped (Remaining 8 Files — Phase 1.2 Continuation)

### [SUBSYSTEM: console/workflows + flows]

**File:** `core/console/corvin_console/routes/flows.py:576-578`
```
Current: from ._compute_license_gate import enforce_compute_quota
Replace: require_capability("compute.run", requested=1, tenant_id=tid, entry_point=__file__+":578")
```

**File:** `core/console/corvin_console/routes/workflows.py:3515-3520`
```
Current: enforce_compute_quota(rec.tenant_id, rec.sid_fingerprint, audit_action="workflow.run_started")
Replace: require_capability("compute.run", requested=1, tenant_id=rec.tenant_id, entry_point=__file__+":3517")
```

**File:** `core/console/corvin_console/routes/workflows.py:4482-4486`
```
Current: enforce_chat_turns(rec.tenant_id, rec.sid_fingerprint, audit_action="workflow.design_turn")
Replace: require_capability("chat.turn", requested=1, tenant_id=rec.tenant_id, entry_point=__file__+":4483")
```

### [SUBSYSTEM: console/rag]

**File:** `core/console/corvin_console/routes/custom_provider.py:401-408`
```
Current: enforce_rag_providers_max(_tid, _session.sid_fingerprint, requested_id=req.provider_id or "pending")
Replace: require_capability("rag.provider", requested=1, tenant_id=_tid, entry_point=__file__+":403")
```

**File:** `core/console/corvin_console/routes/rag_hub.py:397-414`
```
Current: enforce_rag_providers_max(tenant_id, _session.sid_fingerprint, requested_id=str(_req_pid or "pending"))
Replace: require_capability("rag.provider", requested=1, tenant_id=tenant_id, entry_point=__file__+":409")
```

### [SUBSYSTEM: bridges/adapter]

**File:** `operator/bridges/shared/adapter.py:10565-10578`
```
Current: _lic_assert_limit("bridges_allowed", channel)
Replace: require_capability("bridge.channel", requested=1, tenant_id=tenant_id, entry_point=__file__+":10565")
```

### [SUBSYSTEM: delegate/compute]

**File:** `core/delegate/corvin_delegate/delegation.py:687`
```
Current: _eng_assert("engines_allowed", engine)
Replace: require_capability("engine.use", requested=1, tenant_id=tenant_id, entry_point=__file__+":687")
```

---

## Implementation Pattern (for Phase 1.2 continuation)

Each replacement follows:

```python
# OLD:
from ._compute_license_gate import enforce_compute_quota
enforce_compute_quota(tenant_id, sid_fingerprint, audit_action="...")

# NEW:
from license.capability_api import require_capability, LicenseDenied
try:
    require_capability("compute.run", requested=1, tenant_id=tenant_id, 
                      entry_point=__file__+":LINE_NUMBER")
except LicenseDenied as e:
    raise HTTPException(status_code=402, detail={"error": "license_limit", 
                                                   "reason": e.reason,
                                                   "upgrade_url": e.upgrade_url})
```

Exception handling pattern: `LicenseDenied` → `HTTPException(402)` conversion (already done in compute.py example).

---

## Legacy Gates to Delete (Phase 1.2 Cleanup)

After re-pointing, these can be deleted:
- `core/console/corvin_console/routes/_compute_license_gate.py`
- `core/console/corvin_console/routes/_rag_license_gate.py`
- `core/console/corvin_console/routes/license.py` (legacy routes)

---

## Next Steps (Phase 1.2 Continuation)

1. Edit remaining 8 files (flows, workflows×2, custom_provider, rag_hub, adapter, delegation) — 1–2h
2. Commit: "feat(license): Phase 1.2 — complete gate re-pointing (20/20 calls)"
3. Run: `grep "corvin_license\|engines_allowed\|bridges_allowed" --include='*.py' -r core/ operator/` → must be 0

## Status

- ✅ Mapping: **Complete** (20 of 20 calls identified + mapped)
- ✅ Top 5 files: **Complete** (9 of 20 calls re-pointed)
- ⏳ Remaining 8 files: **Ready for Phase 1.2 continuation**
- ⏳ Cleanup: **After all 8 files done**

---

**Commit ready:** Phase 1.2-Partial will be committed after this summary is created.

Next Turn: Finish Phase 1.2 (8 files) + Phase 1.3 (legacy delete) + Phase 1.4 (refresh daemon skeleton) + Phase 1.5-1.8 (status report) + Adversarial Review (framework).
