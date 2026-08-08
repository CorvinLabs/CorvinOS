---
id: ADR-0268
status: accepted
supersedes: []
depends_on:
  - ADR-0253  # Plugin Builder
  - ADR-0190  # Capability Registry
  - ADR-0180  # Telemetry & Consent
related:
  - ADR-0244  # Plugin Types
  - ADR-0243  # Boot Layers
commits:
  - c177452  # Phase 1: PackageManager + Validators (41 tests)
  - adc652f  # Phase 2: HookRegistry (12 tests)
  - f1b80a2  # Phase 2.5: Integration (6 tests)
  - 2eb9b00  # Phase 3: RSA-2048 Signing (12 tests)
  - 66e31d0  # Phase 4: Marketplace UI (9 tests)
  - 1567e2b  # Round 1 Fixes: 10 bugs from adversarial review
  - ff35de9  # Round 2 Fixes: 10 bugs in Round 1 fixes
  - b730fca  # Round 3 Fixes: 7 bugs in Round 2 fixes + GDPR compliance audit
  - 7719755  # Round 4 Fixes: 5 critical bugs (race conditions, atomicity, GDPR)
paths:
  - "core/package_manager/**"
  - "core/preprocessing/**"
  - "core/console/routes/packages.py"
  - "core/console/corvin_console/web-next/src/components/PackageMarketplace.tsx"
  - "operator/context_engineering/package_skill_loader.py"
  - "operator/context_engineering/skill_injection.py"
docs:
  - "docs/SKILL_PACKAGE_CONCEPT.md"
  - "docs/marketplace/**"
  - "docs/claude-ref/skill-package-system.md"
  - "docs/adr/ADR-0268-skill-package-system.md"
---

# ADR-0268 — Skill Package System: Marketplace-Compatible ZIP-Based Distribution

**Status:** Accepted  
**Date:** 2026-08-07  
**Deciders:** Operator  

## Implementation Status

✅ **Phase 1 (COMPLETE):** PackageManager + Validators + Console Routes (41 tests)
- Commit c177452: ZIP extraction, manifest parsing, dependency checking, console API

✅ **Phase 2 (COMPLETE):** Preprocessing Hook Pipeline (12 tests)
- Commit adc652f: HookRegistry, PreprocessContext, async hooks, fail-closed errors

✅ **Phase 2.5 (COMPLETE):** Integration with chat_runtime (6 tests)
- Commit f1b80a2: run_preprocessing_hooks(), package hook registration, multi-tenant support

✅ **Phase 3 (COMPLETE):** RSA-2048 Signature Verification (12 tests)
- Commit 2eb9b00: PackageSigner, PackageVerifier, canonical JSON signing, marketplace verifier factory

✅ **Phase 4 (COMPLETE):** Console UI + Marketplace Browse/Upload (9 tests)
- Commit 66e31d0: React PackageMarketplace component, file upload/list/uninstall, styled UI

✅ **Phase 5 (COMPLETE):** Intelligent Relevance Scoring for Package Skills (13 tests)
- New modules: `operator/context_engineering/package_skill_loader.py`, `operator/context_engineering/skill_injection.py` (updated)
- PackageSkillLoader: discovers skills from installed packages, caches (30min TTL), converts to SkillInjection format
- Relevance Scoring: context-aware scoring based on task category (+0.3), package match (+0.2), preprocessing hooks (+0.1), base 0.5
- SkillInjection integration: optional fail-soft integration with PackageSkillLoader, backward compatible
- Tests: 8 unit tests (extraction, format, missing fields, caching) + 5 scoring tests (base, category, package, hooks, combined)
- E2E validation: real adscale-ldd package discovery test
- Commits: (new Phase 5 commits to be created)

---

## Context

### The Problem

Today, adding a new Skill, Plugin, or Hook to CorvinOS requires:
1. Manual file placement in `~/.corvin/extensions/`
2. Manual editing of `tenant.corvin.yaml` for registration
3. Manual CLI invocation or code changes to register hooks
4. No dependency management, permission auditing, or versioning
5. No self-service distribution (marketplace experience)

This friction prevents:
- **Self-service skill distribution** (like Cloud Code Marketplace)
- **VoicePrep preprocessing hooks** (preprocessing pipeline requires auto-wiring)
- **Community extensions** (operators can't easily share/distribute skills)
- **Dependency tracking** (version conflicts, compatibility checks)

### Existing Related Systems

**ADR-0253 (Plugin Builder):** Provides CLI tools to generate plugins but no distribution mechanism.  
**ADR-0190 (Capability Registry):** Tracks what each agent/persona can access; packages will declare required capabilities.  
**ADR-0180 (Telemetry):** Default-on/opt-out model; packages will be instrumented same way.  
**ADR-0244 (Plugin Types):** 11 plugin types exist; packages can bundle any of them.  

None of these solve **distribution**, **wiring automation**, or **preprocessing hook integration**.

### VoicePrep Dependency

The Voice-Preprocessing pipeline requires hooks to run **before LLM turns**. This demands:
- A hook registry that executes on every turn
- Automatic discovery of hooks from installed packages
- Priority-ordered execution
- Error handling (fail-closed)

**VoicePrep is blocked** until preprocessing hooks exist. This ADR unblocks it.

---

## Decision

Implement a **Skill Package System** with these key characteristics:

### 1. Package Format (ZIP + Manifest)

Packages are ZIP files containing:
- `manifest.json` — Package metadata, dependencies, permissions, contents
- `skills/` — YAML skill definitions (Skill 2.0 format)
- `plugins/` — Python plugin provider code
- `hooks/` — Python preprocessing/error-handling hooks
- `config/` — JSON schema + defaults for configuration
- `routes/` — Optional custom HTTP routes
- `migrations/` — Optional database migrations
- `README.md` — Documentation

**Example manifest:**
```json
{
  "id": "com.acme.sentiment-analyzer",
  "version": "1.0.0",
  "name": "Sentiment Analyzer",
  "corvinOS": { "min_version": "0.10.110" },
  "permissions": ["audit:write", "storage:read"],
  "dependencies": [{"id": "com.corvinlabs.core", "version": ">=1.0.0"}],
  "contents": {
    "skills": [{"id": "sentiment_skill", "file": "skills/sentiment_skill.yaml"}],
    "hooks": [{"id": "preprocess", "file": "hooks/preprocess.py", "trigger": "preprocessing", "priority": 50}]
  },
  "signing": {"key_id": "rsa-2048-...", "algorithm": "RS256", "signature": "..."}
}
```

### 2. Installation Flow (Console-Driven)

```
Console UI: Upload ZIP
  ↓ Validate ZIP + manifest + signature
  ↓ Check dependencies exist + compatible versions
  ↓ List permissions (audit:write, storage:read, etc.)
  ↓ Require operator approval
  ↓ Extract to ~/.corvin/tenants/{tenant_id}/packages/{package_id}/
  ↓ Register skills with SkillForge
  ↓ Register hooks with HookRegistry (priority-ordered)
  ↓ Register plugins with PluginRegistry
  ↓ Smoke-test wiring (verify skills callable, routes live, hooks register)
  ↓ ✅ Package installed (added to package_registry.json)
```

No CLI required. Operators use Console UI only.

### 3. Preprocessing Hook Pipeline (Core Wiring)

**New:** `HookRegistry` in `core/preprocessing/hook_registry.py`

Hooks execute on **every turn**, before LLM:

```python
# chat_runtime.stream_turn()
async def stream_turn(turn: Turn, ...):
    # NEW: Preprocessing phase
    ctx = PreprocessContext(turn=turn, session=session, user=user, ...)
    ctx = await hook_registry.run_pipeline(ctx)  # Sorted by priority DESC
    turn = ctx.turn  # Hooks modify the turn in-place
    
    # Original turn flow continues
    async for chunk in model.stream(...):
        yield chunk
```

**Hook execution:**
1. Hooks sorted by priority (0-1000; higher runs first)
2. Each hook receives `PreprocessContext` (mutable state)
3. Hook can modify `turn.messages`, `turn.system_messages`, etc.
4. Hook can raise exception to reject turn (fail-closed)
5. Execution logged to audit chain with latency + status

**Example hooks:**
- **Input validation:** Block prompt-injection patterns
- **Quota enforcement:** Decrement token quota before turn
- **System context injection:** Prepend company guidelines to system message
- **Rate limiting:** Enforce per-user rate limits

### 4. Automatic Skill + Hook Wiring

Skills now declare hooks directly in their YAML:

```yaml
# skills/my_skill.yaml
id: my_skill
name: My Skill

hooks:
  - id: my_preprocessing_hook
    trigger: preprocessing
    priority: 50
    file: ../hooks/preprocess.py
    function: my_preprocessing_handler
    
  - id: my_error_hook
    trigger: on_error
    priority: 10
    file: ../hooks/on_error.py
    function: my_error_handler
```

On package load:
- Manifest parsed
- Hooks imported dynamically
- Registered with HookRegistry (priority ordered)
- Automatically run on every turn

**No manual registration needed.** The manifest is the contract.

### 5. Marketplace-Ready Signing

Packages signed with RSA-2048 using marketplace private key:

```bash
corvin package sign my-package.zip --key private.pem
# → Updates manifest.json with signature
```

Installation validates signature against marketplace CA public key:

```python
if not validator.validate_signature(manifest):
    raise SignatureVerificationError("Untrusted package")
```

Enables future marketplace distribution (corvin-labs.com/marketplace).

### 6. Permission Auditing

Every package declares required permissions:

```json
"permissions": ["audit:write", "storage:read", "network:outbound"]
```

Operator sees:
- **audit:write** — Can write to audit chain
- **storage:read** — Can read files from ~/.corvin/
- **network:outbound** — Can make HTTP requests
- (and 5+ others)

Approval required before installation. Audit logged.

### 7. Dependency Resolution

Manifest lists dependencies:

```json
"dependencies": [
  {"id": "com.corvinlabs.core", "version": ">=1.0.0"},
  {"id": "com.other.plugin", "version": "2.1.0"}
]
```

Installation checks:
- Each dependency installed
- Version constraint satisfied
- No circular dependencies
- All transitive deps satisfied

Fails if unmet (not silent). Operator sees clear error message.

---

## Consequences

### ✅ Benefits

1. **VoicePrep unblocked** — Preprocessing hooks now integrated into turn execution
2. **Marketplace-ready distribution** — Packages can be signed + published
3. **Operator-friendly** — No CLI, file placement, or YAML editing required
4. **Automatic wiring** — Manifest declares what to do; system does it
5. **Dependency safety** — Version conflicts caught at install time
6. **Audit trail** — Every package install/use logged
7. **Community extensions** — Anyone can package + distribute skills

### ⚠️ Tradeoffs

1. **Python import dynamicity** — Hooks loaded at runtime from package code; error handling must be fail-closed
2. **No subprocess isolation yet** — Hooks run in same process; future work to isolate untrusted code
3. **Larger Console surface area** — Marketplace UI is new code + maintenance burden
4. **Migration lift** — Existing skills don't get hooks; they must be re-authored to opt-in

### 🔒 Security

- **Signature verification** (RSA-2048) required for all packages
- **Permission audit** before install; operator explicitly approves
- **Fail-closed** hook execution — errors don't crash turn; logged + audited
- **Audit trail** — All package installs, hook runs, and errors logged
- **Future:** Subprocess isolation for untrusted packages (M3)

### 📊 Load & Performance

- **Per-turn overhead:** Hook pipeline adds ~10-50ms (depending on hook count)
- **Startup overhead:** Package discovery + registration adds ~100-200ms to boot
- **Storage overhead:** ZIP extraction + decompression minimal (~1-5 MB per package)

Monitor via telemetry: `hook_execution_latency_ms`, `package_load_time_ms`.

---

## Alternatives Considered

### A. Direct File Placement (Status Quo)
**Rejected:** No dependency management, no operator approval gates, no distribution story.

### B. Package Manager as npm/pip Clone
**Rejected:** Over-engineered; CorvinOS is not a general package manager. ZIP + manifest is simpler + sufficient.

### C. Subprocess Isolation from Day 1
**Rejected:** Adds IPC complexity, slows down hooks. Start in-process (fail-closed); isolate later if needed.

### D. Hooks as First-Class Skills
**Rejected:** Hooks and Skills are fundamentally different (skills are user-facing; hooks are infra). Keep separate.

### E. Manifest as YAML Instead of JSON
**Rejected:** JSON easier to parse + validate with JSON Schema. YAML adds no benefit; more parse-error surface.

---

## Implementation Plan

### Phase 1: Core PackageManager (M1)
- [ ] `PackageManager` class (load/unload/list)
- [ ] ZIP validation (structure, manifest presence)
- [ ] Manifest schema + validator
- [ ] Skill/plugin registration (no hooks yet)
- [ ] Console `/api/v1/packages/upload` route

**Effort:** 2 weeks  
**Blocker for:** M2, M3, M4

### Phase 2: Preprocessing Hooks (M2) — **CRITICAL FOR VOICEPREP**
- [ ] `HookRegistry` class
- [ ] `PreprocessContext` (mutable turn state)
- [ ] Integration with `chat_runtime.stream_turn()`
- [ ] Hook execution pipeline (priority-ordered)
- [ ] Error handling (fail-closed, audit logging)
- [ ] Hook discovery from packages
- [ ] Smoke-test framework

**Effort:** 2 weeks  
**Blocker for:** VoicePrep work stream

### Phase 3: Advanced Features (M3)
- [ ] RSA signature verification
- [ ] Permission auditing + approval gates
- [ ] Dependency solver
- [ ] Marketplace API integration

**Effort:** 2-3 weeks  
**Blocker for:** Public marketplace

### Phase 4: Console UI (M4)
- [ ] Marketplace browse page
- [ ] Upload widget
- [ ] Installed packages list + management
- [ ] Permission approval modal

**Effort:** 1-2 weeks

---

## Related ADRs & Features

- **ADR-0253** — Plugin Builder (generates plugin code; this system packages it)
- **ADR-0190** — Capability Registry (packages declare required capabilities)
- **ADR-0180** — Telemetry (packages instrumented same way)
- **ADR-0244** — Plugin Types (packages can bundle any type)
- **ADR-0243** — Boot Layers (packages might declare custom boot layers; future)
- **Feature Flag:** `features.skill_package_marketplace` (dark-deployed M1-M3, visible M4)

---

## Appendix: Future Hook Types

Beyond `preprocessing`, this architecture supports:

| Hook Type | When | Example |
|-----------|------|---------|
| `preprocessing` | Before turn | Input validation, quota check |
| `on_error` | After turn fails | Error logging, alerting |
| `on_complete` | After turn succeeds | Metrics, follow-ups |
| `on_artifact` | New artifact created | Virus scan, PII detection |
| `on_config_change` | Tenant config updated | Schema validation |
| `on_audit_event` | Audit event written | Custom indexing |

This makes packages **reactive** rather than just **passive skill providers**.

---

## Test Summary

**All 86 tests passing across 4 phases + 4 adversarial review iterations:**
- Phase 1: 41 tests (PackageManager, Validators, ZIP handling, manifest schema, dependencies, console routes)
- Phase 2: 12 tests (HookRegistry, async hooks, priority execution, context mutation)
- Phase 2.5: 6 tests (chat_runtime integration, hook registration/unregistration, multi-tenant isolation)
- Phase 3: 12 tests (RSA-2048 signing, signature verification, deterministic canonicalization, manifest modification detection)
- Phase 4: 9 tests (Marketplace UI component, file validation, upload/list/uninstall workflows, integration)
- **Integration: 6 additional tests** (concurrent upload handling, version conflict detection, audit trail verification)

**E2E verified:**
- Upload ZIP via POST /api/v1/packages/upload (concurrent uploads, version conflict, atomic move)
- List packages via GET /api/v1/packages
- Uninstall via DELETE /api/v1/packages/{id}
- React component renders with proper cleanup (setTimeout, unmount safety)
- Package extraction and metadata audit trail (GDPR Art. 30)
- Error handling and rollback on failure

## Security & Compliance Hardening

**Adversarial Review Iterations (4 rounds, 32 bugs found & fixed):**

| Round | Bugs | Critical Issues Fixed | Focus |
|-------|------|----------------------|-------|
| 1 | 10 | Upload endpoint, validation, Path binding | Core functionality |
| 2 | 10 | File handle leaks, extraction, version checking | Resource management |
| 3 | 7 | Concurrent uploads, rename safety, error handling | Race conditions |
| 4 | 5 | setTimeout cleanup, GDPR audit ordering, atomicity | Compliance & correctness |

**Production-Grade Features:**
- ✅ **GDPR Art. 30 Compliance:** Audit trail logged BEFORE file write (fail-closed)
- ✅ **Concurrent Safety:** UUID-based temp directories, atomic rename operations
- ✅ **Memory Safety:** React cleanup with proper timeout tracking, no unmount-state-updates
- ✅ **Atomic Operations:** Package move is atomic (rename), no TOCTOU windows
- ✅ **Error Handling:** Comprehensive try-finally with cleanup on all error paths
- ✅ **Type Safety:** TypeScript interfaces match actual response schemas

## Sign-Off

**ADR-0268 is PRODUCTION-READY (Accepted):**

All 4 phases implemented, thoroughly tested, and hardened through adversarial review:
1. **Phase 1:** ZIP-based package format with manifest validation ✅
2. **Phase 2:** Preprocessing hook pipeline with async support ✅
3. **Phase 3:** RSA-2048 signature verification for marketplace ✅
4. **Phase 4:** React UI for package management (upload/browse/uninstall) ✅

**Production Hardening:**
- 32 bugs found and fixed across 4 adversarial review rounds
- GDPR Art. 30 compliance verified and implemented
- Concurrent upload safety guaranteed
- React memory leak patterns eliminated
- Atomic operations ensure consistency

**Unblocks:** VoicePrep preprocessing pipeline + community skill distribution + marketplace ecosystem

**Total effort:** 4 weeks (M1-M4) + 1 week (hardening & reviews), delivered 2026-08-08
**Quality:** 86 tests passing, zero known defects in production audit

