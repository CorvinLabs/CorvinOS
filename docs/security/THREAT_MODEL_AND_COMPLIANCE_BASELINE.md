# CorvinOS Threat Model and Security Baseline

**Date:** 2026-09-18  
**Status:** Active (Production)  
**Compliance:** EU AI Act 2026 + GDPR Art. 30, 32, 5, 6  
**Adversarial Tests:** 50+ (all vectors, fail-closed verified)  

---

## Executive Summary

CorvinOS operates in a **high-trust, high-compliance environment**. The system is structured around three load-bearing security principles:

1. **Fail-Closed by Default** — every decision (allow/deny) favors denial when in doubt; no bypass switch
2. **Audit-First** — every decision is logged before execution; no audit commit → operation denied
3. **Immutable Chain** — audit events are append-only, hash-linked, tamper-evident; boot tripwire verifies chain before any code runs

This document defines the threat model (six attack vectors), the mitigations (per-vector), and the compliance proofs (audit, consent, erasure).

---

## Threat Model: Six Attack Vectors

### Vector 1: Plugin Sandbox Escape

**Threat:** A malicious or compromised plugin attempts to:
- Forge its own Line of Moral Responsibility (LoM) to hide its identity
- Read other plugins' private state (secrets, user data)
- Modify or delete audit events to cover its tracks
- Disable house-rules gates or consent checks
- Escalate its own permissions
- Fork a subprocess to escape the Python process boundary

**Severity:** HIGH (plugin is in-process, has direct memory access)

**Attack Surface:**
- LoM validation (lom_hash must match source code location)
- Plugin state encapsulation (private attributes, context isolation)
- Audit chain immutability (no delete/modify after write)
- House-rules enforcement (no env-var kill-switch)
- Permission checks (no escalation via role assignment)
- Process isolation (subprocess spawning denied)

**Mitigations:**
- **Boot Tripwire (ADR-0232):** runs FIRST, non-overridable, verifies audit chain before any code runs
- **LoM Cryptographic Binding (ADR-0537):** every LoM includes lom_hash (SHA256 of source location); spoofing breaks hash
- **Audit-First Model:** plugin execution is logged with original LoM BEFORE plugin runs; if plugin tries to modify its own entry, hash chain breaks
- **Plugin Attribution (not Sandbox):** we log WHAT the plugin did, not prevent it from trying; hostile behavior is caught and attributed post-fact

**Fail-Closed Rules:**
- Plugin cannot modify audit chain → any modification attempt detected via broken hash
- Plugin cannot disable house-rules → gate is meta-level, cannot be overridden
- Plugin cannot access other plugin state → access control on plugin-private attributes enforced

**Tests:** 10 tests in `test_adversarial_comprehensive_security_sweep_50_tests.py::TestPluginSandboxEscape`

---

### Vector 2: Audit Chain Tampering

**Threat:** Attacker attempts to:
- Modify a past audit event (e.g., delete a consent denial)
- Forge a signature on an event (claim it came from a different source)
- Delete events to hide a trail (e.g., remove the event that logged a PII leak)
- Reorder events to change the timeline
- Replay an old event to repeat an action

**Severity:** CRITICAL (breaks GDPR Art. 30/32 record integrity)

**Attack Surface:**
- Event hashing (each event links to previous via hash)
- Signature verification (HMAC or RSA on event payload)
- Chain traversal (cannot skip or reorder events)
- Key rotation (old chain sealed, new chain starts; no cross-sealing)
- Nonce validation (prevent replay attacks)

**Mitigations:**
- **Hash Chaining:** each event carries prev_hash (hash of prior event); modifying any past event breaks the link
- **Signature per Event:** each event signed with key; signature verifies sender identity
- **Boot Tripwire Verification:** on boot, chain is verified end-to-end; broken chain → boot FAILS
- **Nonce Tracking:** each event carries unique nonce; replayed nonce detected via seen_nonces set
- **Timestamp Monotonicity:** events have strictly increasing timestamps; reordering detected

**Fail-Closed Rules:**
- Chain verification fails → boot FAILS (not degrade to read-only)
- Signature mismatch → event rejected (not logged as "maybe invalid")
- Missing event in sequence → gap detected, audit trail incomplete, investigation required

**Tests:** 10 tests in `test_adversarial_comprehensive_security_sweep_50_tests.py::TestAuditChainTampering`

---

### Vector 3: Consent Bypass

**Threat:** Attacker or malicious app attempts to:
- Omit the consent token from a request (try to proceed without consent)
- Use an expired consent token
- Use a consent token with insufficient scope (e.g., "read_skills" to execute a skill)
- Use a consent token revoked by the user
- Use another user's consent token
- Infer consent from a related permission (e.g., "read" implies "delete")
- Submit an A2A task without the user's consent

**Severity:** CRITICAL (violates GDPR Art. 6, 7 — legal basis for processing)

**Attack Surface:**
- Consent token validation (presence, expiration, scope, revocation status)
- Consent scope specificity (no inference, only exact scopes)
- User identity verification (token must belong to requesting user)
- A2A dual-party consent (user + source app both verified)

**Mitigations:**
- **Consent Presence Gate:** request without token → DENY (no fallback)
- **Token Expiration Check:** expired token → DENY
- **Scope Matching:** requested action must be in token's scopes (no superset inference)
- **Revocation Tracking:** revoked token marked and → DENY on next use
- **Tenant Binding:** token valid only for its tenant_id
- **A2A Consent Dual-Check:** (1) source app must sign the envelope, (2) user must have consented to the action
- **Audit-First Consent:** every consent decision (allow/deny) logged before operation proceeds

**Fail-Closed Rules:**
- Missing consent → DENY (no implicit consent)
- Ambiguous scope → DENY (not ALLOW then audit)
- Revocation check failure → DENY (conservative)

**Tests:** 10 tests in `test_adversarial_comprehensive_security_sweep_50_tests.py::TestConsentBypass`

---

### Vector 4: Auth Bypass / Cross-Tenant Access

**Threat:** Attacker attempts to:
- Omit or forge an Authorization header
- Forge a JWT with invalid signature
- Use an expired JWT
- Use a JWT for tenant_a to access tenant_b resources
- Spoof a tenant_id in the JWT
- Use an API key for a different tenant
- Exploit timing differences to infer valid vs invalid credentials

**Severity:** CRITICAL (breaks tenant isolation, violates GDPR Art. 32)

**Attack Surface:**
- JWT signature validation (must verify against shared secret or public key)
- JWT expiration (exp claim must be in future)
- Tenant_id binding in JWT (request tenant must match JWT tenant)
- API key tenant binding (key-to-tenant mapping enforced)
- Authorization header format (must start with "Bearer ", not empty)

**Mitigations:**
- **Strict JWT Validation:** (1) signature verified, (2) expiration checked, (3) tenant_id validated
- **Constant-Time Comparison:** auth check timing does not leak info about valid vs invalid creds
- **Bearer Token Format Validation:** header must be "Bearer <token>", not "Basic", "Digest", or empty
- **Tenant Isolation:** every resource access filtered by tenant_id from JWT; cross-tenant request DENIED
- **API Key Mapping:** api_key → tenant_id validated; mismatch → DENY

**Fail-Closed Rules:**
- Missing Authorization header → DENY
- Invalid signature → DENY
- Expired token → DENY
- Tenant mismatch → DENY

**Tests:** 10 tests in `test_adversarial_comprehensive_security_sweep_50_tests.py::TestAuthBypassCrossTenant`

---

### Vector 5: Path Traversal

**Threat:** Attacker or malicious Skill attempts to:
- Use ../ sequences to escape CORVIN_HOME (e.g., /path/..\\..\\..\\etc/passwd)
- Follow symlinks pointing outside CORVIN_HOME
- Use absolute paths (e.g., /etc/passwd) instead of relative
- Double-encode paths (%252e%252e) to bypass single-decode checks
- Inject null bytes (\x00) to terminate path early
- Use case variation or backslash on Windows to bypass checks
- Write to arbitrary filesystem locations

**Severity:** HIGH (filesystem access control violation, GDPR Art. 32 confidentiality)

**Attack Surface:**
- Path normalization (../ removed, symlinks resolved)
- CORVIN_HOME boundary (all paths must stay within)
- Encoding handling (double-encoding detected)
- Null byte injection prevention
- Symlink resolution (absolute target checked against CORVIN_HOME)

**Mitigations:**
- **Path Normalization:** path is normalized (../ resolved), resolved path must be within CORVIN_HOME
- **Symlink Following:** if path is a symlink, real target is checked; target outside CORVIN_HOME → DENY
- **Absolute Path Rejection:** paths starting with / are rejected (only relative paths allowed)
- **Double-Encoding Detection:** path decoded once, result checked for traversal; then decoded again for double-encoding
- **Null Byte Rejection:** null bytes in path cause ValueError on open(), caught and logged
- **UNC Path Rejection:** Windows UNC paths (\\\\server\\share) rejected on all platforms
- **Audit Logging:** every path traversal attempt logged with attempted_path + reason

**Fail-Closed Rules:**
- Path escapes CORVIN_HOME → DENY (not allow read-only fallback)
- Symlink target outside CORVIN_HOME → DENY
- Absolute path attempted → DENY

**Tests:** 10 tests in `test_adversarial_comprehensive_security_sweep_50_tests.py::TestPathTraversal`

---

### Vector 6: PII Leakage

**Threat:** Attacker or system error causes:
- User prompts or transcripts logged (GDPR Art. 5 minimization violation)
- Email addresses or phone numbers in audit events
- API keys or secrets logged (credential leak)
- Consent decisions disclosed (user privacy violation)
- User IDs correlated across systems (linkability increase)

**Severity:** CRITICAL (GDPR Art. 5, 32 — data minimization, integrity/confidentiality)

**Attack Surface:**
- Audit event payload scrubbing (user prompts, transcripts removed)
- Secret key handling (never logged in plaintext)
- Consent decision visibility (not disclosed)
- User ID hashing in telemetry (but logged plaintext in audit for GDPR Art. 15 access)
- PII regex patterns (email, phone, credit card formats)

**Mitigations:**
- **Payload Scrubbing:** before logging, user_prompt, transcript, output scanned for PII patterns; matched text replaced with [REDACTED]
- **Secret Key Hashing:** encryption keys, API keys logged as SHA256 hashes (irreversible)
- **Consent Privacy:** consent decisions (allow/deny) logged for audit, but details not disclosed
- **User ID Handling:** (1) audit trail: user_id logged plaintext (GDPR Art. 15 required), (2) telemetry: user_id hashed (privacy)
- **Audit Immutability:** PII scrubbing happens at write time, not at read time; if PII gets through, it's permanent in the chain
- **Validation (Fail-Closed):** `_assert_safe()` function validates every audit event before write; event carrying PII shape → DROPPED (not sent)

**Fail-Closed Rules:**
- Event carrying PII pattern → DROPPED (not logged)
- Secret key in plaintext → DROPPED (not logged)
- Consent details leaked → DROPPED (not logged)

**Tests:** 5 tests in `test_adversarial_comprehensive_security_sweep_50_tests.py::TestPIILeakage`

---

## Compliance Baseline

### GDPR Art. 30 — Records of Processing Activity

**Requirement:** Organization maintains records of all processing activities.

**CorvinOS Implementation:**
- Every operation logged to immutable audit chain
- Chain is hash-linked, tamper-evident
- Boot tripwire verifies chain before any code runs
- Operator can audit "show me all operations on user X's data"

**Tests:**
- `test_audit_chain_hash_verification` — chain integrity
- `test_audit_chain_verification_before_boot` — tripwire validation

---

### GDPR Art. 32 — Security of Processing

**Requirement:** Appropriate technical measures to protect personal data (confidentiality, integrity, availability).

**CorvinOS Implementation:**
- **Confidentiality:** path-gate (L10) prevents unauthorized file access, encryption at rest
- **Integrity:** audit chain hash-linking, signature verification, immutability
- **Availability:** graceful degradation (offline audit deferral), failover to local logging

**Tests:**
- `test_audit_chain_tamper_detection` — integrity
- `test_path_traversal_detected_unix_style` — confidentiality
- `test_consent_audit_event_logged_on_deny` — audit trail

---

### GDPR Art. 5 — Data Minimization & Integrity

**Requirement:** Personal data processed minimally (only what's necessary) and kept accurate.

**CorvinOS Implementation:**
- **Minimization:** user prompts, transcripts, emails NOT logged to audit chain
- **Integrity:** audit chain immutable; no selective deletion or rewrite

**Tests:**
- `test_pii_scrubbing_in_audit_event` — minimization
- `test_audit_deletion_detected` — integrity

---

### GDPR Art. 6, 7 — Lawful Basis & Consent

**Requirement:** Processing must have a lawful basis (e.g., consent); if consent-based, must be freely given, specific, informed, unambiguous.

**CorvinOS Implementation:**
- Every user-data operation requires explicit consent
- Consent token required in request; missing token → DENY
- Consent token has explicit scopes (e.g., "skill_routing", not inferred)
- User can revoke consent anytime; revoked tokens → DENY

**Tests:**
- `test_missing_consent_token_denied` — mandatory consent
- `test_consent_scope_mismatch_denied` — specificity
- `test_revoked_consent_token_denied` — revocability

---

### GDPR Art. 17 — Right to Erasure (Right to be Forgotten)

**Requirement:** User can request erasure of their data. System must delete within 30 days (except where retention is legal obligation).

**CorvinOS Implementation:**
- **Audit trail never deleted** — immutable chain is legal record (GDPR Art. 30 requires permanent record)
- **Erasure via logical shadow:** `data_erased` event logged (not data deleted)
- **Inference prevention:** user_id filtered from query results; past events remain but are not accessible
- **Retention age-based:** old data aggregated/anonymized after 90 days

**Tests:**
- Covered by ADR-0232/0233 (boot tripwire, chain immutability)
- Separate erasure suite: ADR-0536 (L36 Erasure Orchestrator)

---

### EU AI Act 2026 — Bot Disclosure & Acceptable Use

**Requirement:** AI system must disclose its nature; must comply with acceptable-use guidelines.

**CorvinOS Implementation:**
- **Bot-Disclosure Card:** one-time display per uid (Art. 50)
- **House-Rules Gate (L44):** unbypassable, fail-closed, audited
- **Acceptable-Use Check:** request evaluated against house rules before execution

**Tests:**
- `test_plugin_cannot_disable_house_rules` — house rules enforcement
- `test_audit_event_type_logged_on_deny` — audit trail

---

## Security Baseline Maturity Levels

### Level 1: Deny-by-Default (IMPLEMENTED)

Every security gate defaults to DENY unless explicit ALLOW conditions are met.

- ✅ Consent gate: requires token
- ✅ House-rules gate: requires pass
- ✅ Path-gate: requires CORVIN_HOME boundary
- ✅ Auth gate: requires valid JWT + tenant match
- ✅ Audit chain: requires unbroken hash link

### Level 2: Audit-First (IMPLEMENTED)

Every decision (allow/deny/error) is logged before execution. No audit commit → operation denied.

- ✅ Consent denial logged
- ✅ Auth failure logged
- ✅ Path traversal logged
- ✅ Plugin execution logged with LoM

### Level 3: Fail-Closed Recovery (IMPLEMENTED)

On component failure, system fails closed (DENY, not ALLOW). Operator can restore service safely.

- ✅ Missing audit backend → operation denied
- ✅ Broken chain on boot → boot fails (tripwire)
- ✅ Expired consent → operation denied
- ✅ Invalid signature → event rejected

### Level 4: Zero-Leakage PII (IMPLEMENTED)

No personally identifiable information leaks into logs, audit trail, or telemetry.

- ✅ User prompts scrubbed
- ✅ Secrets logged as hashes
- ✅ Consent details not disclosed
- ✅ Email/phone patterns filtered

---

## Testing & Validation

### Adversarial Test Suite

**File:** `tests/security/test_adversarial_comprehensive_security_sweep_50_tests.py`

**Test Count:** 55 tests across 6 vectors + 3 meta-tests

**Coverage:**
- Plugin Sandbox Escape: 10 tests
- Audit Chain Tampering: 10 tests
- Consent Bypass: 10 tests
- Auth Bypass / Cross-Tenant: 10 tests
- Path Traversal: 10 tests
- PII Leakage: 5 tests
- Fail-Closed Verification (meta): 3 tests

**Each test verifies:**
1. Attack is attempted
2. System rejects/handles gracefully
3. No state corruption
4. Audit event is logged (with tenant_id, timestamp, LoM)
5. Fail-closed behavior confirmed

### Test Execution

```bash
# Run all adversarial tests
pytest tests/security/test_adversarial_comprehensive_security_sweep_50_tests.py -v

# Run by vector
pytest tests/security/test_adversarial_comprehensive_security_sweep_50_tests.py::TestPluginSandboxEscape -v
pytest tests/security/test_adversarial_comprehensive_security_sweep_50_tests.py::TestAuditChainTampering -v
pytest tests/security/test_adversarial_comprehensive_security_sweep_50_tests.py::TestConsentBypass -v
pytest tests/security/test_adversarial_comprehensive_security_sweep_50_tests.py::TestAuthBypassCrossTenant -v
pytest tests/security/test_adversarial_comprehensive_security_sweep_50_tests.py::TestPathTraversal -v
pytest tests/security/test_adversarial_comprehensive_security_sweep_50_tests.py::TestPIILeakage -v
```

### Compliance Verification

```bash
# Verify audit chain integrity
python3 -m scripts.verify_audit_chain --tenant=_default

# Verify no PII in logs
grep -E "[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}" ~/.corvin/audit.jsonl
# Should return: (empty)

# Verify consent denial audit trail
grep "event_type.*consent" ~/.corvin/audit.jsonl | head -20
```

---

## References

- **ADR-0232/0233:** Boot Tripwire & Audit Chain Integrity
- **ADR-0537:** LoM Cryptographic Binding (lom_hash)
- **ADR-0016:** Consent System
- **ADR-0769:** A2A Task Envelope & Signature
- **L10:** Path-Gate
- **L44:** House-Rules Enforcer

---

## Appendix: Threat Matrix

| Vector | Severity | GDPR Impact | Test Count | Status |
|--------|----------|-------------|-----------|--------|
| Plugin Sandbox Escape | HIGH | Art. 32 | 10 | ✅ IMPLEMENTED |
| Audit Chain Tampering | CRITICAL | Art. 30, 32 | 10 | ✅ IMPLEMENTED |
| Consent Bypass | CRITICAL | Art. 6, 7 | 10 | ✅ IMPLEMENTED |
| Auth Bypass / Cross-Tenant | CRITICAL | Art. 32 | 10 | ✅ IMPLEMENTED |
| Path Traversal | HIGH | Art. 32 | 10 | ✅ IMPLEMENTED |
| PII Leakage | CRITICAL | Art. 5, 32 | 5 | ✅ IMPLEMENTED |
| **TOTAL** | — | — | **55** | ✅ |
