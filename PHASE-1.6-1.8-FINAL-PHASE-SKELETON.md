# Phase 1.6–1.8 — Boundary Tests, Audit Events, Documentation

---

## Phase 1.6: Boundary E2E Tests (~500 LoC, 30 min)

**File:** `tests/license/test_licensing_boundary_e2e.py`

### Test Suite Structure

```python
class TestCapabilityAPIBoundary:
    def test_http_endpoint_402_on_deny(self): ...
    def test_cli_activate_e2e(self): ...
    def test_mcp_tool_enforcement(self): ...

class TestCRLMerge:
    def test_monotonicity_enforcement(self): ...
    def test_three_merge_cycles(self): ...

class TestRefreshDaemon:
    def test_three_refresh_cycles(self): ...
    def test_cross_platform_lock(self): ...

class TestTwoGatewayFixture:
    def test_isolated_rings(self): ...
    def test_zero_clone_suspected(self): ...
```

**Coverage:**
- HTTP 402 error path (real API call)
- CLI subprocess (real binary)
- MCP tool invocation
- CRL merge monotonicity
- Daemon three-cycle proof
- Two-gateway isolation

---

## Phase 1.7: Audit Events (~100 LoC, 15 min)

**File:** `corvin_operator/license/audit_events.py` (new)

### Event Registration

```python
AUDIT_EVENTS = {
    "license.capability_decision": {"required": ["capability", "tier", "decision", "allowed"]},
    "license.crl_updated": {"required": ["num_pages", "last_issued_at"]},
    "license.crl_stale": {"required": ["age_seconds"]},
    "license.credential_loaded": {"required": ["seat_fp", "exp"]},
    "license.refresh_cycle": {"required": ["cycle_num", "crl_merged", "asrl_fetched"]},
    "license.features_url_override": {"required": ["url"]},
}
```

### Rename Migrations

| Old Event | New Event | Reason |
|-----------|-----------|--------|
| `token_source` | `license.credential_loaded` | ADR-0703 v1→v2 consolidation |
| `invalid_token` | `license.credential_rejected` | Audit consistency |
| `free_tier` | (removed) | Redundant with `license.capability_decision` |
| `reload_throttled` | (removed) | Refresh daemon replaces |
| `gate_bypassed` | (removed) | Never emitted, security theater |

### Implementation

- Register all events in `forge/audit.py::register_events()`
- Delete v1 paths from `corvin_operator/license/validator.py`
- Preserve: `license.chain_dna_seeded` (ADR-0117 seam verification)

---

## Phase 1.8: Documentation & Freeze (~300 lines, 15 min)

### CLAUDE.md Update

Add compliance row:

```
| Licensing (ADR-0703) | require_capability() unified gate | GDPR Art. 6, 32 |
```

Full text:
```
Licence capability gate — enforcement failure resolves to free allowance
(never below); free baseline never gated; no env kill-switch; no test 
trust root in product (tests monkeypatch ring).
```

### `docs/claude-ref/licensing.md` (Generated)

```markdown
# Licensing Model (ADR-0700–0703)

## Free Tier Capabilities

[Table auto-generated from corvin_operator/license/limits.py::CAPABILITIES]

- compute.run: 10 per day
- chat.turn: unlimited
- workflow.run: 5 per day
- rag.provider: 1
- forge.create: 0 (member-only)
- a2a.send: 0 (member-only)

## Member Tier

[All capabilities unrestricted]
```

### `docs/claude-ref/layer-10-path-gate.md` Update

Add under "Protected Set":
```
Forge roots (new in ADR-0703):
- forge.create (gated: member-only via require_capability)
- forge.execute (gated: member-only)
```

### Delete Stale Docs

- Delete: `docs/licensing-v1-eol.md` (legacy)
- Delete: `docs/sos-audit-trail.md` (replaced by audit events)

---

## Completion Checklist

- [ ] Phase 1.6: All 4 test classes green
- [ ] Phase 1.7: Event registration complete, old events migrated
- [ ] Phase 1.8: CLAUDE.md + licensing.md + layer-10 updated
- [ ] Stale docs deleted
- [ ] `grep "engines_allowed\|bridges_allowed" --include='*.py' -r core/` → 0
- [ ] `grep "corvin_license" --include='*.py' -r core/` → 0

---

## Integration with Adversarial Review

All three phases feed into review gate:
- Phase 1.6 provides E2E proof (real-world call paths work)
- Phase 1.7 provides audit trail (all decisions logged + immutable)
- Phase 1.8 provides compliance documentation (GDPR alignment)

Review verifies:
- ✅ No bypass paths (Phase 1.6 coverage)
- ✅ Audit trail sufficient for compliance (Phase 1.7 events)
- ✅ Documentation matches implementation (Phase 1.8 sync)
