# CorvinOS v1.0.0 Release Notes — ADR-0433 Complete

**Release Date:** 2026-08-20  
**Status:** STABLE (Production Ready)  
**Focus:** Tenant-Native Data Persistence (ADR-0433 Phases A–E Complete)  
**Version Tag:** `v1.0.0-ADR0362`

---

## ⭐ Major Changes

### Tenant-Native Data Persistence — ADR-0433 Complete

All data persisted by CorvinOS is now **tenant-scoped by construction**. This eliminates split-brain audit trails, cross-tenant tool visibility, and bridge credential leakage — fixing 8 CRITICAL/HIGH security findings from the pre-release adversarial audit.

**What changed:**
- ✅ **Skills**: Unified storage in `~/.corvin/tenants/<tenant_id>/skill-forge/`
- ✅ **Tools**: Isolated per-tenant in `~/.corvin/tenants/<tenant_id>/forge/`
- ✅ **Audit Trail**: Single, hash-chained audit file per tenant (no split-brain)
- ✅ **Sessions**: Already tenant-scoped (no change)
- ✅ **Memory**: Already tenant-scoped (no change)
- ✅ **Learning Events**: Already tenant-scoped (no change)

**Compliance Impact:**
- GDPR Art. 5(1)(f) — Integrity/Confidentiality: ✅ Fixed (isolation by construction)
- GDPR Art. 30 — Records of Processing: ✅ Fixed (unified per-tenant audit)
- GDPR Art. 32 — Security: ✅ Fixed (fail-closed validation gates)
- EU AI Act Art. 5, 50 — Transparency & Opt-out: ✅ Fixed (tenant-scoped disclosure)

**Tests:** 96 comprehensive (unit + integration + adversarial) — all passing.

---

## ⚠️ Breaking Changes

### 1. `scope_root()` Signature Change

The central path-resolution API now requires a **mandatory `tenant_id` parameter**:

**Before (v0.11.1):**
```python
path = scope_root("skill")  # ❌ Implicit tenant context
```

**After (v0.11.2):**
```python
from core.paths import tenant_skill_dir

path = tenant_skill_dir("tenant_a")  # ✅ Explicit tenant_id
```

**Action for operators:**
- No action required. Internal API change only — operator-facing CLI commands are unaffected.

### 2. Storage Directory Layout

Global storage is **no longer supported**. All data moves to tenant-scoped directories:

**Before (v0.11.1):**
```
~/.corvin/
├── global/
│   ├── skill-forge/registry.json
│   └── forge/tools/
├── tenants/_default/
│   ├── skill-forge/skills/
│   └── sessions/
```

**After (v0.11.2):**
```
~/.corvin/tenants/
├── _default/
│   ├── skill-forge/
│   │   ├── skills/
│   │   └── registry.json
│   ├── forge/tools/
│   ├── audit.jsonl
│   └── sessions/
├── tenant_a/
│   ├── skill-forge/
│   └── [... same structure ...]
```

---

## 🔄 Migration Guide

### For Operators: One-Step Safe Migration

CorvinOS v0.11.2 includes a **built-in migration tool** that safely moves data from global locations to tenant-scoped directories. It is:
- ✅ **Safe**: Dry-run first to verify
- ✅ **Idempotent**: Run multiple times safely
- ✅ **Backward-compatible**: Old paths kept until cleanup TTL expires (default: 30 days)

**Step 1: Dry-run to verify (no changes):**
```bash
corvin migrate --to-tenant-native --dry-run
```

**Step 2: Run migration (with automatic backups):**
```bash
corvin migrate --to-tenant-native
```

**Step 3: Verify isolation (automated check):**
```bash
corvin verify-isolation
```

**Step 4: Clean up old data (after 30-day TTL, optional):**
```bash
corvin migrate --cleanup-old-paths
```

### What Gets Migrated

| Data Type | Source (v0.11.1) | Destination (v0.11.2) | Status |
|---|---|---|---|
| Skills | `~/.corvin/global/skill-forge/` | `~/.corvin/tenants/_default/skill-forge/` | ✅ Migrated |
| Tools | `~/.corvin/global/forge/tools/` | `~/.corvin/tenants/_default/forge/tools/` | ✅ Migrated |
| Sessions | `~/.corvin/sessions/` | `~/.corvin/tenants/_default/sessions/` | ✅ Migrated |
| Audit Trail | `~/.corvin/audit.jsonl` | `~/.corvin/tenants/_default/audit.jsonl` | ✅ Migrated (hash-chain preserved) |
| Memory | `~/.corvin/memory/` | `~/.corvin/tenants/_default/memory/` | ✅ Migrated |

### Multi-Tenant Operators

If you have multiple tenants (e.g., `tenant_prod`, `tenant_staging`):

1. Migration tool automatically detects all tenants
2. Each tenant's data moves to its own directory
3. Audit trails remain separate per tenant (no cross-tenant mixing)
4. Consent/opt-out state becomes per-tenant

---

## 🐛 Bug Fixes

### Phase A: Tenant-Native Data Persistence Foundation (Commit fb448e1)
- ✅ Implement `core/paths/tenant.py` — canonical tenant-aware path API
- ✅ Add `validate_tenant_id()` with fail-closed regex whitelist
- ✅ Introduce `tenant_home()`, `tenant_skill_dir()`, `tenant_tool_dir()`, `tenant_audit_file()`

### Phase B: Scope Root Refactor (Commit d31b306)
- ✅ Refactor `scope_root()` to require mandatory `tenant_id` parameter
- ✅ Update ~100 call-sites across codebase
- ✅ Fix validator alignment and reserved tenant names

### Phase C: Brain Subsystem Integration (Commit 7ae93ea)
- ✅ Wire SkillForgeSubsystem to use tenant-scoped paths
- ✅ Wire ToolForgeSubsystem to use tenant-scoped paths
- ✅ Update LearningEngine, SafetyValidator, SessionManager

### Phase D: Migration Tool (Commit 8f07ce4)
- ✅ Implement `corvin migrate --to-tenant-native`
- ✅ Add dry-run support
- ✅ Implement TTL-based cleanup

### Phase E: Testing & Adversarial Audit (Commit 6844c08)
- ✅ 96 comprehensive tests (unit + integration + adversarial)
- ✅ Phase E adversarial gate: 0 CRITICAL findings (from 8 pre-release)
- ✅ Verify audit trail integrity (hash-chain, no split-brain)

---

## 📊 Test Coverage

| Test Suite | Count | Status |
|---|---|---|
| Unit Tests (core/paths/, core/tenants/) | 24 | ✅ Passing |
| Integration Tests (Brain subsystems) | 35 | ✅ Passing |
| E2E Tests (CLI, migration, audit) | 20 | ✅ Passing |
| Adversarial Tests (isolation, RCE, token theft) | 17 | ✅ Passing (0 CRITICAL) |
| **Total** | **96** | ✅ **All Passing** |

---

## 🔐 Security & Compliance

### CRITICAL Findings Fixed (Phase E Adversarial Gate)

| Finding | Before | After | Status |
|---|---|---|---|
| Split-Brain Audit Trail | CRITICAL | ✅ Fixed (unified) | Pass |
| ToolForge Cross-Tenant Visibility | CRITICAL | ✅ Fixed (isolated) | Pass |
| Skill Registry Not Tenant-Aware | CRITICAL | ✅ Fixed (isolated) | Pass |
| Bridge Credentials Exposed | CRITICAL | ✅ Fixed (isolated) | Pass |
| Instance Registry Shared | CRITICAL | ✅ Fixed (per-tenant) | Pass |
| Telemetry Consent Not Tenant-Scoped | HIGH | ✅ Fixed (per-tenant) | Pass |
| Bridge State File Shared | HIGH | ✅ Fixed (isolated) | Pass |
| scope_root() Missing tenant_id | HIGH | ✅ Fixed (mandatory param) | Pass |

### Regulatory Compliance

**GDPR Articles:** Art. 5, 6, 7, 17, 30, 32 → ✅ All met  
**EU AI Act:** Art. 5(1), 50 → ✅ Compliant  
**ADR-0007 (Multi-Tenant):** ✅ Fully enforced  

---

## 📝 Documentation Updates

- ✅ ADR-0433 — Complete specification (see Corvin-ADR repository)
- ✅ [Layer 7 SkillForge](docs/claude-ref/layer-7-skillforge.md) — Updated for tenant-scoped storage
- ✅ [Layer 6 Forge](docs/claude-ref/layer-6-forge.md) — Updated for tenant-scoped tools
- ✅ [Multi-Tenant](docs/claude-ref/multi-tenant.md) — Tenant isolation guarantees documented

---

## 📦 Installation & Upgrading

### Fresh Install (v0.11.2)

No changes — installation behavior is identical. All new data is automatically tenant-scoped.

### Upgrade from v0.11.1 or Earlier

**Required:** Run migration after upgrade:

```bash
# Backup first (optional, automatic backup included in migration)
cp -r ~/.corvin ~/.corvin.backup

# Run migration
corvin migrate --to-tenant-native

# Verify
corvin verify-isolation
```

After migration, old paths at `~/.corvin/global/` and `~/.corvin/<old_paths>/` remain for 30 days, then are automatically cleaned up.

---

## 🚀 Next Steps

### For Operators

1. ✅ Upgrade to v0.11.2
2. ✅ Run `corvin migrate --to-tenant-native` (safe, reversible)
3. ✅ Verify with `corvin verify-isolation`
4. Monitor operator logs for any data-access errors

### For Developers

- Tenant-native storage is now the **only supported path strategy**
- Use `core.paths.tenant_*` functions for all new storage paths
- Legacy `scope_root()` behavior is removed; code must provide `tenant_id`
- See ADR-0433 (in Corvin-ADR repository) for architecture

---

## 🤝 Contributing

Contributions to CorvinOS are welcome. Please see [CONTRIBUTING.md](CONTRIBUTING.md) and note:
- All new features must be tenant-scoped (use `core.paths.tenant_*` APIs)
- All PRs must pass the adversarial test suite (Phase E)
- ADR-0433 is now **MANDATORY REFERENCE** for any path-resolution code

---

## 📞 Support

For migration questions or issues:
- Run `corvin migrate --help` for all options
- See `docs/migration/README.md` for detailed operator guide
- File issues at https://github.com/veegee82/CorvinOS/issues with tag `[migration]`

---

**Happy upgrading! 🎉**

CorvinOS v0.11.2 is production-ready and compliant with GDPR + EU AI Act 2026.
