# Phase 1 Adversarial Review Framework

**Purpose:** Three-reviewer independent attack on ADR-0703 shipped runtime  
**Target:** **ZERO CRITICAL/HIGH findings** (blocking gate to Phase 2)  
**Scope:** Licensing 1.0.0 consolidation (capability_api, keyring, crl, refresh daemon, audit trail)

---

## Reviewer 1: Bypass/Security

**Mandate:** Can `require_capability()` enforcement be bypassed or quota-injected?

### Attack Vectors

1. **Direct ImportError Fallback**
   - Question: Does `require_capability()` fail-closed when enforcement unavailable?
   - Test: Mock `capability_api` ImportError → must return free allowance, not unmetered
   - Success criteria: Zero bypass paths found

2. **Quota Counter Injection**
   - Question: Can quota counters be manipulated (filesystem-based state)?
   - Test: Tamper with `quota.json` files, verify daemon rejects invalid state
   - Success criteria: All tampering detected + rejected

3. **Ring Trust Bypass**
   - Question: Can credentials be forged (embedded ring only, no test hook)?
   - Test: Generate fake JWT, verify ring validation rejects it
   - Success criteria: No fake credentials accepted

4. **Cross-Tenant Capability Leak**
   - Question: Can a free-tier user access member-only resources via multi-tenant confusion?
   - Test: submit compute.run request with tenant_id="attacker" + auth="victim"
   - Success criteria: Isolation enforced

5. **Authority Outage Exploitation**
   - Question: During 7-day authority outage, are class-L capabilities still restricted?
   - Test: Block authority server, verify free tier still limited
   - Success criteria: Fail-closed holds under network blockade

### Review Report Structure

```
[BYPASS/SECURITY REVIEW]

Findings:
1. [finding severity: CRITICAL/HIGH/MEDIUM/LOW]
   File: corvin_operator/license/capability_api.py:145
   Issue: require_capability() returns free allowance on any exception
   Attack: Mock exception handler → attacker gets unlimited compute
   Status: [MITIGATED/OPEN]

[Summary: X vectors tested, N findings, 0 CRITICAL, 0 HIGH]
```

---

## Reviewer 2: Business/Legal/Compliance

**Mandate:** Does the model match ADR-0700–0703? Audit trail sufficient for GDPR?

### Compliance Checks

1. **ADR-0700 Model Adherence**
   - Is two-tier model (free/member) shipped as designed?
   - Are free allowances exactly as ADR-0700 specifies?
   - Are member tier capabilities truly unrestricted?

2. **GDPR Art. 6 Legal Basis**
   - Free tier telemetry: "legitimate interest" (opt-out available?)
   - Member tier: Contractual (license agreement signed?)
   - Audit trail: Records decisions + reasoning (Art. 30 compliance)?

3. **GDPR Art. 32 Security**
   - Is audit trail hash-chained (tamper-evident)?
   - Encryption at rest? (compliance-baseline.md review)
   - Retention policy documented? (90-day default per ADR-0319)

4. **Transparency (EU AI Act Art. 50)**
   - Bot-disclosure card shown to free users? (L44 house-rules)
   - License tier visible in UI?
   - Upgrade path clear?

5. **Right to Erasure (GDPR Art. 17)**
   - Can a user request all their licensing records deleted?
   - Audit trail deletion safe (no crypto breakage)?

### Review Report Structure

```
[BUSINESS/LEGAL/COMPLIANCE REVIEW]

Compliance Basis:
- ADR-0700 Canonical Free/Member Model: ADHERES
- ADR-0703 Runtime Consolidation: ADHERES
- GDPR Art. 6 Legal Basis: [specific evidence]
- GDPR Art. 32 Security: [audit trail hash-chain verified / missing]
- EU AI Act Art. 50 Transparency: [disclosure card present]

Findings:
1. [severity: CRITICAL/HIGH/MEDIUM/LOW]
   Spec: GDPR Art. 6 Consent
   Issue: Free tier telemetry on by default, opt-out not in UI
   Evidence: [screenshot showing no opt-out toggle]
   Status: [MITIGATED/OPEN]

[Summary: 5 requirements checked, 0 CRITICAL blockers]
```

---

## Reviewer 3: Architecture/Reachability

**Mandate:** All entry points wired? Boot tripwire intact? No dead code?

### Wiring Proof

1. **Console Routes**
   - Question: Are all HTTP endpoints calling `require_capability()`?
   - Test: Grep for `compute`, `chat`, `voice`, `rag`, `forge` endpoints → verify all have gate calls
   - Success criteria: Zero ungated execute paths found

2. **CLI Wiring**
   - Question: Does `corvin-license` CLI call `require_capability()`?
   - Test: `corvin-license activate`, `status`, `deactivate` → verify calls gate
   - Success criteria: All commands respect tier limits

3. **Daemon Lifecycle**
   - Question: Does refresh daemon wire into all boot paths (gateway, console, adapter)?
   - Test: Grep `start_background_daemon()` call sites → must appear in all three
   - Success criteria: All three boot paths wire daemon

4. **Boot Tripwire**
   - Question: Is tripwire still checking audit chain integrity (ADR-0232)?
   - Test: Run bootstrap.boot_platform() → verify chain verification NOT skipped
   - Success criteria: Tripwire green, audit chain length > 0

5. **Dead Code**
   - Question: Are all legacy gates (_compute_license_gate, _rag_license_gate) truly unused?
   - Test: Grep for remaining imports → must be 0 outside fallback blocks
   - Success criteria: All legacy gates are fallback-only

### Review Report Structure

```
[ARCHITECTURE/REACHABILITY REVIEW]

Wiring Audit:
- Console routes: 15/15 endpoints have gate calls ✅
- CLI commands: 7/7 commands wire gate ✅
- Daemon entry points: gateway + console + adapter ✅ (3/3)
- Boot tripwire: Audit chain verification active ✅

Dead Code Scan:
- Grep "enforce_compute_quota" outside _compute_license_gate.py: 0 ✅
- Grep "_lic_assert_limit" outside fallback: 0 ✅
- Grep "corvin_license" outside corvin_operator/license/: 0 ✅

Findings:
1. [severity]
   Reachability: Console voice.py:1712 calls require_capability?
   Evidence: grep shows call site exists + tests green
   Status: [VERIFIED/MISSING]

[Summary: Wiring complete, 5/5 entry point categories verified]
```

---

## E2E Proof Checklist (All 5 Must PASS for Review Gate)

- [ ] ✅ Free instance boots with network blocked → zero licensing egress
- [ ] ✅ Console + gateway + bridge on one CORVIN_HOME refresh 3 cycles → zero `clone_suspected`
- [ ] ✅ Five fail-open ImportError branches resolve to free allowance
- [ ] ✅ 7-day authority outage leaves class-L capabilities working
- [ ] ✅ Boot tripwire tests unchanged

---

## Review Gate Output

Each reviewer produces:
- JSON findings array: `[{severity, file, line, issue, evidence, status}]`
- HTML report (auto-generated from JSON)
- Sign-off: "Ready for Phase 2" (if 0 CRITICAL/HIGH)

**Gate:** CRITICAL or HIGH findings → blocks Phase 2, triggers root cause + re-review  
**Success:** 0 CRITICAL/HIGH → Phase 2 unlock immediate

---

## Related ADRs

- ADR-0703: Runtime consolidation architecture
- ADR-0700: Canonical free/member model
- ADR-0232: Audit chain (boot tripwire)
- ADR-0319: Retention policy (90-day default)
