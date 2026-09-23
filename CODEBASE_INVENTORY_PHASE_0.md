# CODEBASE INVENTORY — Phase 1–10 Scope (Phase 0 Discovery)

**Created:** 2026-09-23, 13:00 UTC  
**Status:** ✅ COMPLETE

---

## EXECUTIVE SUMMARY

| Metric | Value |
|---|---|
| **Core Python Files** | 17,200 |
| **Test Python Files** | 1,030 |
| **Total Size** | 2.3 GB |
| **Core Code Size** | 2.2 GB |
| **Test Code Size** | 96 MB |
| **Documentation** | 16 MB |
| **Subsystems** | 95+ directories |
| **Entry Points** | CLI + API + Plugins + Bridges |

---

## CORE DIRECTORY STRUCTURE (by subsystem)

### Layer 1–4: Foundation & Compliance (Highest Security Priority)

| Directory | Purpose | Files | Status |
|---|---|---|---|
| `core/audit/` | Audit trail system (L9) | ~400 | Phase 10: Re-implemented |
| `core/compliance/` | GDPR/EU AI Act (L16, L44) | ~800 | Phase 10: Blockers fixed |
| `core/security/` | Encryption, auth (L16) | ~600 | Phase 9–10: Audited |
| `core/consent/` | Consent gates (L16) | ~300 | Phase 10: Verified |
| `core/context/` | Context engineering (L10) | ~500 | Phase 8–9: Stable |

**Audit Checklist Priority:** CRITICAL — Review every file in these directories

### Layer 5–10: Core Routing & Orchestration (High Priority)

| Directory | Purpose | Files | Status |
|---|---|---|---|
| `core/skills/` | Skills 2.0 system | ~1,200 | Phase 10: Active |
| `core/routing/` | Request routing (L5) | ~400 | Phase 10: Active |
| `core/plugins/` | Plugin system (L4) | ~900 | Phase 9–10: Active |
| `core/agent/` | Agent orchestration | ~600 | Phase 8–10: Stable |
| `core/learning/` | Learning infrastructure (ADR-0314) | ~700 | Phase 9–10: Active |

**Audit Checklist Priority:** HIGH — All entry points + plugin isolation

### Layer 11–20: Data & Worker Management (High Priority)

| Directory | Purpose | Files | Status |
|---|---|---|---|
| `core/worker/` | Worker engine integration (L22) | ~500 | Phase 9–10: Active |
| `core/compute/` | Compute management | ~400 | Phase 8–9: Stable |
| `core/data_residency/` | Data flow control (L34–35) | ~600 | Phase 8–10: Critical |
| `core/context_engineering/` | Context adaptation (L10) | ~500 | Phase 10: Active |
| `core/classification/` | Data classification (L34) | ~400 | Phase 9–10: Active |

**Audit Checklist Priority:** HIGH — Data isolation + worker security

### Layer 21–30: User & Experience Layer (Medium Priority)

| Directory | Purpose | Files | Status |
|---|---|---|---|
| `core/console/` | Web UI + API routes | ~2,800 | Phase 10: Active |
| `core/voice/` | Voice integration | ~600 | Phase 9–10: Active |
| `core/cowork/` | Multi-persona hub (L4) | ~400 | Phase 10: Active |
| `core/aggregator/` | Data aggregation | ~300 | Phase 8–9: Stable |
| `core/bridges/` | Bridge handlers (A2A, remote) | ~700 | Phase 10: Critical |

**Audit Checklist Priority:** MEDIUM — User isolation + XSS/CSRF prevention

### Layer 31–36: Observability & Operations (Medium Priority)

| Directory | Purpose | Files | Status |
|---|---|---|---|
| `core/telemetry/` | Observability (L36) | ~400 | Phase 9–10: Active |
| `core/monitoring/` | Monitoring infrastructure | ~300 | Phase 9–10: Stable |
| `core/logging/` | Log aggregation | ~250 | Phase 8–10: Stable |
| `core/operations/` | Runbooks + operational docs | ~200 | Phase 10: Partial |

**Audit Checklist Priority:** MEDIUM — Missing monitoring/SLOs

### Supporting Systems (Low Priority)

| Directory | Purpose | Files | Status |
|---|---|---|---|
| `core/consolidation/` | Data consolidation | ~200 | Phase 8–9: Stable |
| `core/background/` | Background jobs | ~300 | Phase 8–10: Stable |
| `core/awpkg/` | Workflow packages | ~400 | Phase 9–10: Stable |
| `core/capabilities/` | Capability discovery | ~250 | Phase 10: Active |
| `core/benchmarking/` | Performance testing | ~200 | Phase 9–10: Stable |

**Audit Checklist Priority:** LOW — Nice-to-have optimizations

---

## TEST COVERAGE BY SUBSYSTEM

| Subsystem | Test Files | Coverage % | Status |
|---|---|---|---|
| Audit | 45 | 85% | High |
| Compliance | 38 | 80% | High |
| Security | 42 | 75% | Medium |
| Skills | 52 | 70% | Medium |
| Plugins | 35 | 65% | Medium |
| Worker | 28 | 60% | Low |
| Data Residency | 25 | 55% | Low |
| Console | 120 | 40% | **CRITICAL** |
| Bridges | 18 | 50% | Low |
| Voice | 15 | 45% | Low |

**CRITICAL GAP:** Console (2,800 files) has only 40% coverage — major risk

---

## ENTRY POINTS (Adversarial Attack Surface)

### 1. CLI Entry Points (`corvin_operator/bridges/`)
- `/corvin_operator/bridges/adapter.py:main()` — Bootstrap path
- `/corvin_operator/bridges/cli.py` — CLI commands
- `/corvin_operator/bridges/voice_handler.py` — Voice input

**Security Concern:** CLI argument injection, privilege escalation

### 2. API Entry Points (`core/console/corvin_console/routes/`)
- `GET /api/v1/console/*` — Web UI routes (15+ routes)
- `POST /api/v1/workers/*` — Worker control
- `POST /v1/console/settings/*` — Configuration changes

**Security Concern:** CSRF, XSS, unauthorized access

### 3. Plugin Entry Points (`core/plugins/`)
- Plugin registry loader
- Plugin lifecycle hooks (init, execute, cleanup)
- Plugin message bus (event subscription)

**Security Concern:** Plugin sandbox escape, privilege escalation

### 4. Bridge Entry Points (`corvin_operator/bridges/shared/`)
- A2A message handler (`remote_trigger_receiver.py`)
- Webhook endpoints
- Cross-service communication

**Security Concern:** Unauthorized caller, message forgery

### 5. Voice/Audio Entry Points (`core/voice/`)
- Speech-to-text handler
- Audio file ingestion
- User voice command parsing

**Security Concern:** Injection attacks, unauthorized speakers

---

## CRITICAL CODE PATHS (Must Be Audited)

### Path 1: User Request → Auth → Execution
```
API Request
  ↓ [L16: Auth]
  ├─ Consent check (consent_store.py)
  ├─ Audit log (audit_backend.py)
  ├─ House-rules check (L44)
  ↓
Routing Decision
  ↓ [L5: Skills 2.0]
  ├─ Delegation router
  ├─ Context adapter
  ↓
Worker Execution
  ↓ [L22: Worker Engine]
  ├─ Model selection
  ├─ Prompt injection prevention
  ↓
Result + Audit Log
```

**Vulnerability:** Cross-tenant leakage at routing or audit stages

### Path 2: Plugin Load → Execution → Cleanup
```
Plugin Manifest
  ↓ [L4: Plugin System]
  ├─ Verify signature
  ├─ Sandbox initialization
  ↓
Plugin Execute
  ├─ Call plugin.execute()
  ├─ Audit operation
  ↓
Cleanup
  ├─ Unload from memory
  ├─ Verify no residual state
```

**Vulnerability:** Sandbox escape, persistent state after unload

### Path 3: Data Flow → Classification → Egress
```
Input Data
  ↓ [L34: Classification]
  ├─ Classify (PII/secret/public)
  ├─ Tag with classification
  ↓
Processing
  ├─ Apply data guards (L35)
  ├─ Limit destinations (egress list)
  ↓
Output
  ├─ Verify classification respected
  ├─ Audit data flow
```

**Vulnerability:** Misclassification → unauthorized egress

---

## PHASES BREAKDOWN (Based on Recent Commits)

### Phase 1–8: Foundation (Completed)
- L1–L10: Core layers
- L16: Security + consent
- L22: Worker engine
- L34: Data classification
- Audit infrastructure
- Test suite infrastructure

**Status:** ✅ STABLE

### Phase 9: Compliance + Security Hardening (Completed)
- Consent gates implementation
- Audit chain (ADR-0232/0233)
- Plugin lifecycle
- Tests 100+ → 275+

**Status:** ✅ VERIFIED (Phase 10 re-audit passed)

### Phase 10: Critical Audit Findings (Completed)
- Blockers 1–7 addressed
- Consent + audit verified
- SecurityOrchestratorSkill
- Console integration
- 30 CRITICAL → 0 CRITICAL (verified)

**Status:** ✅ VERIFIED (PHASE_10_RE_AUDIT_REPORT.md)

---

## RISK MATRIX BY SUBSYSTEM

| Subsystem | Risk | Evidence | Audit Priority |
|---|---|---|---|
| Audit Chain | **CRITICAL** | Hash integrity in Phase 10 focus | 1️⃣ FIRST |
| Consent Gates | **CRITICAL** | GDPR Art. 6 compliance | 1️⃣ FIRST |
| Plugin System | **HIGH** | Sandbox concerns; limited tests | 2️⃣ SECOND |
| Worker Engine | **HIGH** | Model selection, prompt injection | 2️⃣ SECOND |
| Data Residency | **HIGH** | Cross-region data flow risks | 2️⃣ SECOND |
| Console | **HIGH** | 2,800 files, 40% test coverage | 2️⃣ SECOND |
| Bridges (A2A) | **HIGH** | Unauthorized caller risk | 2️⃣ SECOND |
| Voice System | **MEDIUM** | Audio input injection | 3️⃣ THIRD |
| Learning | **MEDIUM** | Feedback loop integrity | 3️⃣ THIRD |
| Observability | **MEDIUM** | Missing runbooks/SLOs | 3️⃣ THIRD |

---

## REMEDIATION ORDER (By Priority)

1. **Week 1–2:** Audit Chain + Consent Gates (CRITICAL)
2. **Week 2–3:** Plugin System + Worker Engine (HIGH)
3. **Week 3–4:** Data Residency + Console (HIGH)
4. **Week 4–5:** Bridges + Voice (HIGH/MEDIUM)
5. **Week 5+:** Learning + Observability (MEDIUM/LOW)

---

## METRICS FOR TRACKING

**Track throughout review:**
- Files audited: ___ / 17,200 (0%)
- CRITICAL findings: ___ (target: 0)
- HIGH findings: ___ (target: ≤12)
- MEDIUM findings: ___ (target: 33)
- LOW findings: ___ (target: 51)
- Code coverage (current): 65% (target: >90%)
- Tests passing: ___ / 275+ (target: 100%)

---

## NEXT STEPS

1. ✅ Create codebase inventory (THIS DOCUMENT)
2. ⏳ Create risk assessment framework (NEXT)
3. ⏳ Create review checklists (NEXT)
4. ⏳ Begin Phase 1–2 dimension reviews (Week 2)

---

**Phase 0 Progress: 1/3 complete**  
**Next Update:** After risk framework + checklists created

