# Self-Managed Sessions Activation Plan

**Date:** 2026-08-23  
**Status:** IMPLEMENTATION READY  
**Blocker Resolution:** Key-Custody = Operator-Only (Option A)

---

## Overview

Self-Managed Sessions enable Corvin to autonomously:
1. Open sessions for multi-phase tasks
2. Generate plugins for session-local functionality (via ADR-0262/0263)
3. Manage lifecycle (checkpoint, transfer context, close)

**Current State:**
- Stages 1-5 (Ideation → Build → E2E Tests) ✅ COMPLETE (ADR-0262/0263)
- Stage 6 (Install/Trust) ⏸️ BLOCKED on key custody
- **Resolution:** Operator-only key custody (console manual install)

---

## Activation Checklist

### Feature Flags (ship dark, default OFF)

```yaml
spec.features:
  plugin_builder_idea_first_interview: false      # ADR-0262
  plugin_builder_checkpoint_review: false          # ADR-0262
  plugin_builder_generate_e2e_tests: false         # ADR-0262
  plugin_builder_ideas_mode: false                 # ADR-0263
  
  self_managed_sessions_enabled: false             # NEW: session management
  self_managed_sessions_auto_install: false        # NEW: Stage 6 automated (deferred)
```

### Key Custody Configuration

**File:** `~/.corvin/plugins/trust.key` (operator-controlled)

```python
# core/plugins/plugin_builder/stage6_install.py
from pathlib import Path

TRUST_KEY_PATH = Path.home() / ".corvin" / "plugins" / "trust.key"

def load_operator_trust_key() -> str:
    """Load operator's plugin installation key (Stage 6)."""
    if not TRUST_KEY_PATH.exists():
        raise PluginInstallError(
            f"Operator trust key not found at {TRUST_KEY_PATH}. "
            "Run: corvin plugin init-trust-key"
        )
    return TRUST_KEY_PATH.read_text().strip()

def install_generated_plugin(
    plugin_code: str,
    plugin_name: str,
    session_id: str,
    trust_key: str
) -> bool:
    """
    Install a generated plugin (Stage 6 — Operator-Only).
    
    Args:
        plugin_code: Generated Python code from Stage 5 (Scaffold)
        plugin_name: e.g., "session_vibe_inspector_2026_08_23"
        session_id: e.g., "sess_abc123" (session-local scope)
        trust_key: Operator's trust.key (cryptographic approval)
    
    Returns:
        True if installation succeeds
    
    Flow:
        1. Verify trust_key signature (HMAC-SHA256 of plugin_code)
        2. Write to ~/.corvin/plugins/<session_id>/<plugin_name>.py
        3. Audit-log: "plugin_installed_operator_approved" (ADR-0016)
        4. Return plugin_id for session registration
    """
    # Implementation deferred to Phase 2 (async key sync)
    pass
```

### CLI Commands (New)

```bash
# Initialize operator trust key (one-time setup)
corvin plugin init-trust-key
  → Generates Ed25519 keypair
  → Stores private key at ~/.corvin/plugins/trust.key
  → Prints public key fingerprint for audit log

# List pending generated plugins (awaiting install)
corvin plugin list-pending --session-id sess_abc123
  → Shows: stage-5-scaffold output, E2E test results, install readiness

# Manually install (Stage 6, Operator-Approved)
corvin plugin install-generated \
  --session-id sess_abc123 \
  --plugin-name vibe_inspector_2026_08_23
  → Verifies trust key
  → Installs to ~/.corvin/plugins/sess_abc123/
  → Registers in session-store
  → Logs audit event

# Revoke plugin (cleanup after session closes)
corvin plugin revoke \
  --session-id sess_abc123 \
  --plugin-name vibe_inspector_2026_08_23
  → Unregisters from session
  → Deletes plugin files
  → Logs audit event
```

### Console UI Integration

**Panel:** Settings → Developer → Plugin Management

```typescript
// components/DeveloperPluginPanel.tsx
export function DeveloperPluginPanel() {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Plugin Management (Operator-Only)</CardTitle>
      </CardHeader>
      
      <Tabs defaultValue="generated">
        {/* Tab 1: Pending Generated Plugins */}
        <TabsContent value="generated">
          <PluginListPending 
            sessionId={currentSessionId}
            onInstall={handleInstallPlugin}
          />
        </TabsContent>
        
        {/* Tab 2: Installed Session-Local Plugins */}
        <TabsContent value="installed">
          <PluginListInstalled 
            sessionId={currentSessionId}
            onRevoke={handleRevokePlugin}
          />
        </TabsContent>
        
        {/* Tab 3: Trust Key Management */}
        <TabsContent value="trust-key">
          <TrustKeyManager 
            fingerprint={operatorKeyFingerprint}
            onRotate={handleRotateKey}
          />
        </TabsContent>
      </Tabs>
    </Card>
  );
}
```

---

## Phases

### Phase 1: Operator-Only Install (NOW)

**Scope:** Stages 1-5 automated, Stage 6 manual via console  
**Timeline:** 1 week  
**Effort:** 30h

- [ ] Feature flags added to `spec.features`
- [ ] `stage6_install.py` skeleton (load trust key, audit log)
- [ ] CLI: `corvin plugin init-trust-key`, `list-pending`, `install-generated`, `revoke`
- [ ] Console UI: Plugin Management panel (pending + installed tabs)
- [ ] E2E test: full lifecycle (ideation → build → audit → manual install)
- [ ] Docs: operator quickstart guide

**Success Criteria:**
- Operator can generate plugin via `/plugin-builder --ideas`
- Operator sees pending plugin in Console UI
- Operator installs via UI (trust key verified)
- Session-local plugin registered + operational
- Audit trail complete (GDPR Art. 30/32)

### Phase 2: Distributed Key Sync (Future)

**Scope:** Bridges get session-scoped install capability  
**Timeline:** TBD  
**Effort:** TBD

- Implement async key-exchange protocol (Console ↔ Bridges)
- Bridges validate trust key before local install
- Per-bridge installation audit trail
- Degrade path if key unavailable (keep Operator-Only as fallback)

### Phase 3: External PKI (Future)

**Scope:** Sysadmin-managed certificate authority  
**Timeline:** TBD  
**Effort:** TBD

- Integration with operator's existing PKI (if available)
- CSR signing via `/etc/corvin/ca.key`
- Plugin certificate pinning per bridge
- Revocation via CRL

---

## Key-Custody Architecture (Option A: Operator-Only)

```
┌─────────────────────────────────────────┐
│  Self-Managed Session                   │
│  (e.g., audit task, runs 16 hours)      │
└─────────────────────────┬───────────────┘
                          │
                   Stage 5: Scaffold
                   (generated code)
                          │
                          ▼
┌─────────────────────────────────────────┐
│  Pending Plugin Queue                   │
│  (awaiting operator approval)            │
│                                         │
│  vibe_inspector_2026_08_23.py          │
│  ├─ E2E tests: PASS                    │
│  ├─ Code review: CLEAN                 │
│  └─ Ready for install: YES             │
└─────────────────────────┬───────────────┘
                          │
                   Stage 6: Install
                   (operator-approved)
                          │
            ┌─────────────┴──────────────┐
            │                            │
            ▼                            ▼
      ┌──────────┐            ┌─────────────────┐
      │ Console  │            │ Operator Key    │
      │ UI Panel │  ──verify──│ ~/.corvin/      │
      │  "Install"│           │ plugins/        │
      └──────────┘            │ trust.key       │
            │                 └─────────────────┘
            │
            ▼
    ~/.corvin/plugins/
    sess_abc123/
    └─ vibe_inspector_2026_08_23.py (installed)
            │
            ▼
    ┌─────────────────────────┐
    │  Session-Local Registry │
    │  (linked to session_id) │
    └─────────────────────────┘
            │
            ▼
    Self-Managed Session resumes
    (plugin available for rest of task)
```

---

## Compliance Notes (ADR-0016, ADR-0092)

**Audit Trail:** Every install/revoke logged
```json
{
  "event": "plugin_installed_operator_approved",
  "timestamp": "2026-08-23T14:32:00Z",
  "session_id": "sess_abc123",
  "plugin_name": "vibe_inspector_2026_08_23",
  "trust_key_fingerprint": "sha256:abc123...",
  "operator": "corvin@example.com",
  "scope": "session_local",
  "hash_chain": "sha256:prev_event_hash"
}
```

**GDPR Art. 30/32:** Audit logs encrypted at rest, TTL 90 days  
**EU AI Act Art. 50:** Plugin disclosure in session summary (what was installed, why)

---

## Test Plan

### Unit Tests
- [ ] `test_load_operator_trust_key` (success + missing key)
- [ ] `test_install_generated_plugin` (verify signature, write, audit)
- [ ] `test_stage6_install_happy_path` (full lifecycle)

### E2E Tests
- [ ] `test_self_managed_session_installs_generated_plugin` (real session)
- [ ] `test_operator_installs_via_console_ui` (UI click → install)
- [ ] `test_audit_trail_complete` (verify audit entry for each install)

### Negative Tests
- [ ] `test_install_rejects_invalid_trust_key`
- [ ] `test_install_rejects_tampered_plugin_code`
- [ ] `test_revoke_cleans_up_files` (no orphans)

---

## Next Steps

1. **Code:** Implement `stage6_install.py` + CLI commands
2. **Tests:** Unit + E2E as listed above
3. **Docs:** Operator quickstart guide + troubleshooting
4. **Commit:** Push with ADR-0262/0263 + key-custody review
5. **Activate:** Set feature flags to `true` (operator-controlled)

---

**Target Date for Phase 1:** 2026-08-30 (1 week)  
**Blocker Resolution:** Key-Custody = Operator-Only Install (this document)
