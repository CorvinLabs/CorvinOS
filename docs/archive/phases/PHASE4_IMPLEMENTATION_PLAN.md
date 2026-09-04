# Phase 4: Plugin Marketplace — Production Hardening (Week 7-8)

**Status:** EXECUTION PLAN  
**Branch:** `feature/marketplace-phase4`  
**Execution Timeline:** 10-12 hours  

## Overview

Phase 4 hardens the Plugin Marketplace for production release. It introduces security constraints, performance optimizations, comprehensive documentation, and resilience patterns (rollback, error recovery).

This document tracks the execution workflow and serves as the master checklist.

---

## Phase 4 Deliverables

### 1. Security Hardening (Manifest + Secrets)

**Scope:** `/home/shumway/projects/CorvinOS/core/plugins/`

#### 1.1 Manifest Validation
- [ ] Add `plugin-manifest-schema.json` (JSON Schema v7)
  - Validate: required fields, types, version ranges
  - Validate: dependency specs, conflicts
  - Validate: boot layer invariants (compliance layer never disableable)
  
- [ ] Implement `ManifestValidator` class
  - Method: `validate(manifest_dict) -> ValidationResult`
  - Returns: `(is_valid: bool, errors: List[str])`
  - Fail-closed: reject invalid manifests
  
- [ ] Add to `marketplace.py`: `register_plugin()` now validates manifest
  - Raises `ManifestValidationError` on invalid manifest

#### 1.2 Secret Masking (Audit Trail)
- [ ] Implement `sanitize_for_audit()` utility
  - Hash API keys, tokens, passwords
  - Never log raw secrets
  - Test: grep audit logs for secrets (must find 0 matches)
  
- [ ] Wiring:
  - `PluginInstallation.config` secrets are hashed before audit write
  - Audit trail shows: `config_hash: sha256(...)` only
  - Config is stored encrypted in user's local keyring/vault
  
- [ ] Test: `tests/core/plugins/test_secret_masking.py`
  - Submit config with API key → verify audit log has hash only
  - Verify actual config is never logged

#### 1.3 Manifest Signature Verification (Future ADR-0456)
- [ ] Document: "Plugins are signed by trusted authors"
- [ ] Placeholder: skip for Phase 4 (requires key infrastructure)
- [ ] Add to docs: "Signature verification coming in Phase 5"

#### 1.4 Permission Model
- [ ] Add `required_permissions` field to `PluginMetadata`
  - Examples: `["storage.read", "network.http", "process.fork"]`
  
- [ ] User sees: "This plugin requires: ..."
  - Operator accepts/rejects permissions before install
  
- [ ] Audit logs: permissions granted + any denials

---

### 2. Performance Optimization

**Scope:** `core/console/corvin_console/routes/marketplace*.py` + web-next frontend

#### 2.1 GitHub API Caching
- [ ] Extend `MarketplaceCacheManager`
  - Add: index cache (24h TTL)
  - Add: per-plugin cache (7d TTL)
  - Add: cache invalidation endpoint (for maintainers)
  
- [ ] Test: `tests/integration/test_marketplace_caching.py`
  - Load marketplace 100x → verify cache hits ≥95%
  - Benchmark: <50ms for cached response

#### 2.2 UI Responsiveness
- [ ] Frontend: lazy-load plugins in marketplace panel
  - Render: first 10 results immediately
  - Render: scroll to load next batch
  
- [ ] Test: 20+ plugins → UI remains interactive
  - No blocking on large lists

#### 2.3 Installation Speed Benchmark
- [ ] Create: `tests/performance/test_marketplace_install_speed.py`
  - Simulate: install small plugin (1-5MB)
  - Target: <5 seconds end-to-end
  - Benchmark results added to release notes
  
- [ ] Test: small plugin install (mock file)
  - Assert: duration < 5s

---

### 3. Documentation

#### 3.1 User Guide → `docs/PLUGIN_MARKETPLACE_USER_GUIDE.md`
- [ ] Screenshots: marketplace UI (search, detail, install button)
- [ ] Step-by-step: how to find, preview, install a plugin
- [ ] Troubleshooting: common errors + solutions
- [ ] FAQ: safe to disable built-in plugins?

#### 3.2 Developer Guide → `docs/PLUGIN_MARKETPLACE_DEVELOPER_GUIDE.md`
- [ ] Reference: Plugin-Builder (link to plugin-builder docs)
- [ ] Manifest format: YAML example with all fields
- [ ] Config fields: types, validation, constraints
- [ ] Best practices: depend on stable APIs only
- [ ] Security: how configs are encrypted, secrets masked

#### 3.3 Operator Guide → `docs/PLUGIN_MARKETPLACE_OPERATOR_GUIDE.md`
- [ ] Managing plugins: enable, disable, uninstall
- [ ] Audit logs: where to find, what's in them
- [ ] Troubleshooting install failures
- [ ] Monitoring plugin resource usage (CPU, memory)
- [ ] Rollback: how to restore before a bad install

---

### 4. Rollback Procedure

#### 4.1 Snapshot + Restore
- [ ] `core/plugins/marketplace_rollback.py`
  - Class: `PluginSnapshot` (manifest, config, metadata at point in time)
  - Method: `create_snapshot()` → JSON file
  - Method: `restore_snapshot(snapshot_id)` → restore plugin state
  
- [ ] Storage: snapshots in `~/.corvin/plugins/snapshots/`
  - Directory structure:
    ```
    ~/.corvin/plugins/snapshots/
    ├── plugin-id/
    │   ├── snap-1.json        # pre-install snapshot
    │   └── snap-2.json        # post-install snapshot
    ```

#### 4.2 Test: `tests/core/plugins/test_marketplace_rollback.py`
- [ ] Test: install plugin → snapshot created
- [ ] Test: corrupt config → restore snapshot → verify restored
- [ ] Test: manual rollback via CLI (future)

#### 4.3 Documentation: `docs/PLUGIN_MARKETPLACE_ROLLBACK.md`
- [ ] Automated rollback: when it triggers
- [ ] Manual rollback: steps + timing
- [ ] Snapshot management: retention policy

---

### 5. Error Handling + Recovery

#### 5.1 Error Taxonomy
- [ ] Document 6 error classes:
  1. **Network**: GitHub API unreachable
  2. **Manifest**: invalid schema, missing fields
  3. **Dependency**: required plugin not found
  4. **Permission**: user rejected permissions
  5. **Conflict**: plugin conflicts with existing install
  6. **Sandbox**: resource limits exceeded
  
#### 5.2 Error Messages (User-Facing)
- [ ] Never leak secrets in error messages
- [ ] Include troubleshooting step for each class
- [ ] Example:
  ```
  Error: "Network error: Could not reach GitHub API"
  Troubleshooting: "Check your internet connection. If you're on a restricted network, contact your admin."
  ```

#### 5.3 Implementation: `core/console/corvin_console/routes/marketplace_errors.py`
- [ ] Class: `MarketplaceError` (base)
  - `NetworkError`, `ManifestError`, `DependencyError`, etc.
  - Each carries user-facing message + troubleshooting
  
- [ ] Test: `tests/core/console/test_marketplace_errors.py`
  - Verify: no secrets in error messages
  - Verify: troubleshooting text present

---

### 6. Security Review Checklist

**Master Checklist:** `docs/PLUGIN_MARKETPLACE_SECURITY_CHECKLIST.md`

```
SECURITY REVIEW — Plugin Marketplace Phase 4
=============================================

Manifest & Config Validation
  [ ] Manifest schema validated (JSON Schema v7)
  [ ] Invalid manifests rejected (fail-closed)
  [ ] Dependency specs validated
  [ ] Config types validated

Secret Masking
  [ ] API keys hashed before audit write
  [ ] Audit grep finds 0 raw secrets
  [ ] Config stored encrypted locally
  [ ] Test: submit config with API key → verify hashing

Plugin Isolation
  [ ] Config isolation tested (one plugin's config ≠ another's)
  [ ] Tenant isolation verified (plugin config scoped to tenant_id)
  [ ] Test: 2 tenants, same plugin → configs isolated

Permission Model
  [ ] Plugin declares required permissions
  [ ] User approves permissions before install
  [ ] Audit logs: permissions granted + denials
  [ ] Test: reject permission → install blocked

Error Messages
  [ ] No secrets in error text
  [ ] User-friendly wording
  [ ] Troubleshooting steps present
  [ ] Test: 6 error classes validated

Rate Limiting
  [ ] GitHub API: respect rate limits (60 req/hr public, 5000 req/hr auth)
  [ ] Internal: throttle repeated requests
  [ ] Test: burst 100 marketplace requests → verify no 429s if under quota

Audit Trail
  [ ] Every install/uninstall logged
  [ ] Plugin config changes logged (with secrets masked)
  [ ] Logs are hash-chained (per GDPR Art. 30, 32)
  [ ] Test: audit grep finds all install events

Rollback
  [ ] Snapshots created before/after installs
  [ ] Restore restores all fields (manifest, config, metadata)
  [ ] Test: corrupt config → restore → verify

Code Review
  [ ] No obvious XSS vectors (marketplace panel)
  [ ] No obvious injection vectors (manifest validation)
  [ ] Config validation is fail-closed
```

---

## Execution Steps

### Step 0: Create Feature Branch
```bash
cd /home/shumway/projects/CorvinOS
git checkout main
git pull
git checkout -b feature/marketplace-phase4
```

### Step 1: Security Hardening (3-4 hours)
1. Implement manifest schema + validator
2. Add secret masking to audit writer
3. Implement permission model
4. Write security tests

### Step 2: Performance Optimization (1-2 hours)
1. Extend cache manager (24h index, 7d per-plugin)
2. Add lazy-load to frontend
3. Create install speed benchmark test
4. Run benchmarks, record results

### Step 3: Documentation (2-3 hours)
1. Write User Guide (screenshots + how-tos)
2. Write Developer Guide (manifest format, best practices)
3. Write Operator Guide (management, troubleshooting)
4. Write Rollback Procedure doc

### Step 4: Rollback + Error Handling (2 hours)
1. Implement PluginSnapshot class
2. Add snapshot creation to install workflow
3. Implement restore functionality
4. Write error taxonomy + messages

### Step 5: Test Suite (1-2 hours)
1. Run full security test suite
2. Run performance benchmarks
3. Run integration tests
4. Fix any failing tests

### Step 6: Security Checklist + Review (1 hour)
1. Fill out security checklist
2. Run code review for marketplace.*
3. Verify no secrets in logs/errors
4. Assess canary-readiness

### Step 7: Commit + Report (30 min)
1. Stage changes
2. Commit to feature branch (DO NOT push)
3. Generate final status report

---

## Success Criteria

- [ ] All 6 security checklist items: ✅
- [ ] 0 high-severity security findings
- [ ] Performance benchmarks met: <5s install, <500ms search
- [ ] 10+ pages documentation
- [ ] Rollback procedure tested + working
- [ ] Canary-ready assessment: YES
- [ ] Branch: clean commits, no pushing

---

## File Tree

```
CorvinOS/
├── core/plugins/
│   ├── marketplace.py                    (extend with validation)
│   ├── marketplace_validator.py           (NEW)
│   ├── marketplace_rollback.py            (NEW)
│   ├── marketplace_errors.py              (NEW)
│   ├── plugin-manifest-schema.json        (NEW)
│   └── tests/
│       ├── test_manifest_validation.py    (NEW)
│       ├── test_secret_masking.py         (NEW)
│       └── test_marketplace_rollback.py   (NEW)
│
├── core/console/corvin_console/
│   ├── routes/
│   │   ├── marketplace.py                 (extend)
│   │   ├── marketplace_errors.py          (NEW)
│   │   └── marketplace_cache.py           (extend)
│   │
│   └── web-next/src/panels/
│       └── marketplace.tsx                (add lazy-load)
│
├── tests/
│   ├── integration/
│   │   ├── test_marketplace_caching.py    (NEW)
│   │   └── test_marketplace_e2e.py        (extend)
│   │
│   ├── performance/
│   │   └── test_marketplace_install_speed.py (NEW)
│   │
│   └── core/console/
│       └── test_marketplace_errors.py     (NEW)
│
└── docs/
    ├── PLUGIN_MARKETPLACE_USER_GUIDE.md          (NEW)
    ├── PLUGIN_MARKETPLACE_DEVELOPER_GUIDE.md     (NEW)
    ├── PLUGIN_MARKETPLACE_OPERATOR_GUIDE.md      (NEW)
    ├── PLUGIN_MARKETPLACE_ROLLBACK.md            (NEW)
    └── PLUGIN_MARKETPLACE_SECURITY_CHECKLIST.md  (NEW)
```

---

## Notes

- **Build + Test:** Run pytest before committing
- **Frontend Cache:** After any marketplace.tsx edit, run `scripts/console-deploy.sh --marker "marketplace"` to verify bundling
- **Secrets:** Manual grep for "api.*key", "token", "password" in audit logs (must find 0 matches)
- **Canary Gate:** At end, verify: no push to remote, branch is clean, checklist filled

---

**Last Updated:** 2026-08-30  
**Phase Lead:** Claude Code (Haiku 4.5)  
**Status:** EXECUTION STARTING
