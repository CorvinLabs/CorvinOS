# TDE-Visibility: Blocking Bugs & Architectural Fixes (k=8 + k=9)

**Status:** BLOCKER FINDINGS from Adversarial Review (0b89c60..5956239)  
**Date:** 2026-07-24  
**Severity:** 3 CRITICAL, 3 HIGH

---

## CRITICAL BLOCKERS

### 1. **Persistence Architecture Broken** — CRITICAL
**File:** `core/console/corvin_console/web-next/src/pages/chat.tsx:1145–1158`

**Problem:**
- `tdeProgress` computed frontend-only from `engine_progress` event
- No backend persistence mechanism exists
- Not written to `turns.jsonl`
- **Result:** All TDE metrics lost on page reload

**Proposed Fix (k=8 — Option A):**
Move computation to Backend in `chat_runtime.py::_stream_tde_turn()`:
```python
# After execution, construct TdeProgress before _append_turn()
tde_progress = {
    "run_id": run_id,
    "total_steps": step_count,
    "completed_steps": succeeded,
    "delegated_count": delegated,
    "local_count": local_count,
    "l34_forced": l34_forced,
}

# Pass to _append_turn as metadata field (or extend ChatMessage schema)
_append_turn(sess, "assistant", _turn_parts, tde_progress=tde_progress)
```

Backend automatically persists via existing turns.jsonl save.

---

### 2. **React Query Cache Empty** — CRITICAL
**File:** `core/console/corvin_console/web-next/src/pages/chat.tsx:1146–1158`

**Problem:**
- Code calls `qc.getQueryData(["chat", "messages", sid])`
- **No useQuery hook backing this key** — cache is always empty
- `cached?.messages` is always falsy
- **Result:** Persistence block never executes (dead code)

**Proposed Fix (k=9 — Option B):**
Add PATCH endpoint for message metadata updates:
```python
# chat_runtime.py or new routes/chat.py
@router.patch("/chat/sessions/{sid}/messages/{msg_id}")
async def update_message_metadata(
    sid: str, msg_id: str, body: UpdateMessageRequest
):
    # body: {"tde_progress": {...}}
    # Update turns.jsonl entry, audit-trail the change
    ...
```

Frontend calls this only if needed (async):
```typescript
// Only if engine_progress arrives after message persisted
const updateTdeProgress = async (progress: TdeProgress) => {
  if (lastMsg?.id) {
    await fetch(`/chat/sessions/${sid}/messages/${lastMsg.id}`, {
      method: "PATCH",
      body: JSON.stringify({tde_progress: progress}),
    });
  }
};
```

---

### 3. **Metrics Card Disappears on Reload** — CRITICAL (consequence of 1+2)
**File:** `core/console/corvin_console/web-next/src/components/TdeAuditGraphPanel.tsx:56–88`

**Problem:**
- `latestTdeProgress` is null after reload (since persistence is broken)
- Component conditional `{latestTdeProgress && (...)}` renders nothing
- **Violates ADR-0214 design** ("survives reload/reconnect")

**Fix:** Implement k=8 or k=9 (restores persistence)

---

## HIGH-SEVERITY FINDINGS

### 4. **Unvalidated run_id in Stream Events** — HIGH
**File:** `core/console/corvin_console/web-next/src/pages/chat.tsx:1135`

**Fix:** Validate format before passing to ComputeGraphView:
```typescript
if (!evt.run_id?.match(/^tde-\d+-[a-f0-9]{8}$/)) {
  console.warn("Invalid run_id format", evt.run_id);
  return;
}
```

### 5. **Multiple TDE Turns Silently Overwrite** — HIGH
**File:** `core/console/corvin_console/web-next/src/pages/chat.tsx:1140–1157`

**Fix:** Attach run_id to message for correlation:
```typescript
if (lastMsg && lastMsg.role === "assistant") {
  lastMsg.tdeRunId = evt.run_id;  // Link to the specific TDE run
  lastMsg.tdeProgress = tdeProgress;
}
```

### 6. **Zero-Step Plans UI/Audit Asymmetry** — MEDIUM
**File:** `core/console/corvin_console/chat_runtime.py:3440–3469`

**Fix:** Emit engine_progress even when step_count == 0:
```python
# Always emit (not just if step_count > 0)
if step_count >= 0:  # changed from > 0
    yield {"type": "engine_progress", ...}
```

---

## Implementation Plan (k=8 + k=9)

### k=8: Backend Persistence (Option A) — RECOMMENDED FIRST
**Effort:** 2-3 hours  
**Risk:** Medium (extends ChatMessage schema + turns.jsonl)

1. Update ChatMessage/TdeProgress types  
2. Modify `_stream_tde_turn()` to construct TdeProgress  
3. Extend `_append_turn()` to accept tde_progress kwarg  
4. Persist to turns.jsonl (extends existing message save)  
5. Frontend reads from persisted message (auto-survives reload)

**Advantage:** No new endpoints, automatic persistence, simple.

### k=9: PATCH Endpoint (Option B) — FUTURE ENHANCEMENT
**Effort:** 1-2 hours  
**Risk:** Low (new endpoint, opt-in)

1. Add POST `/v1/chat/sessions/{sid}/messages/{msg_id}` endpoint  
2. Update message metadata in turns.jsonl  
3. Audit-log the update  
4. Frontend calls if race-condition (engine_progress after message saved)

**Advantage:** Handles edge cases, decouples backend/frontend timing.

---

## Next Steps

- **Do k=8 first** (simpler, fixes root cause)
- **Optional k=9** (handles edge cases, deferred to future sprint)
- **Rerun final adversarial review** after fixes

---

## Commit Tracking

- k=1-7: Completed (cd1fce7..5956239)  
- k=8: TBD (Backend Persistence — BLOCKING)  
- k=9: TBD (PATCH Endpoint — Enhancement)  
- Final Review: TBD (Post-fix verification)

---

**Status:** READY FOR ESCALATION to k=8 implementation (architecture agreed, scope clear).
