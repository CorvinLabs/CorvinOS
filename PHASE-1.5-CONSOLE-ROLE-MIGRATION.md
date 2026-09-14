# Phase 1.5 — Console tier → role Migration

**Scope:** Rename SessionRecord.tier → role (GDPR-aligned terminology)  
**Timeline:** ~20 minutes (3 files, ~150 LoC)

## Files to Update

### 1. `core/console/corvin_console/session_manager.py`
- Dual-key read in `SessionRecord._read_record()` — accept both `tier` + `role`
- Write new field only: `role` (backward compat one release)
- Update all `rec.tier` → `rec.role` readers

### 2. `core/console/corvin_console/routes/auth.py`
- Update `/auth/me` endpoint response
- Replace `{"tier": rec.tier}` → `{"role": rec.role}`

### 3. `core/console/corvin_console/web-next/src/lib/api/personas.ts`
- Update TypeScript: `interface SessionRecord { tier? → role?}`
- Update all consumers to use `.role` instead of `.tier`

### 4. `core/console/corvin_console/web-next/src/components/layout.tsx`
- Replace `session.tier` → `session.role`
- Add marker for verification: `session-role`

## Dual-Read Pattern (Backward Compat)

```python
# In SessionRecord._read_record():
tier = record.get("role") or record.get("tier", "free")
# Write: record["role"] = tier (only new field)
```

## Verification Marker

Add to layout.tsx:
```tsx
{/* ADR-0703: Console role marker for migration verification */}
<div className="debug-marker" data-marker="session-role">{session.role}</div>
```

## Tests

- `test_auth_me_includes_role.py` — verify /auth/me returns `role` field
- `test_session_dual_read.py` — backward-compat tier→role read works
- Console E2E — verify layout renders with role

## Rollout

1. Ship with dual-read + dual-write one release
2. Monitor for tier-only sessions in logs (should be 0)
3. Remove tier-read path in next release
