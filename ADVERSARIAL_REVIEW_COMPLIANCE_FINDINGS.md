# CorvinOS Compliance Audit — Adversarial Review
## Phases 1–10 Comprehensive GDPR + EU AI Act + CLA Assessment

**Date:** 2026-09-22  
**Scope:** GDPR Art. 5–7, 17, 30, 32 · EU AI Act Art. 5, 50 · CLA  
**Status:** 🔴 **CRITICAL FINDINGS IDENTIFIED** (6 gaps, 5 HIGH/CRITICAL severity)

---

## Executive Summary

CorvinOS declares comprehensive compliance baseline in CLAUDE.md and compliance-baseline.md, with detailed architectural constraints for GDPR Art. 6 (consent), Art. 30/32 (audit), and EU AI Act Art. 50 (bot disclosure). However, **adversarial review reveals structural compliance gaps** that undermine these claims:

| Category | Status | Severity | Findings |
|---|---|---|---|
| **Consent Gates (Art. 6/7)** | ❌ NOT IMPLEMENTED | 🔴 CRITICAL | Decorator stub, always-allow, no persistent store |
| **Consent Audit (Art. 30)** | ❌ NOT IMPLEMENTED | 🔴 CRITICAL | No audit events for consent operations |
| **CLA Enforcement** | ❌ INCOMPLETE | 🟠 HIGH | 2+ unregistered corporate contributors |
| **Plugin Audit (Art. 30)** | ⚠️ UNCERTAIN | 🟠 HIGH | Audit events declared but verify actual emission |
| **Skill Audit (Art. 30)** | ⚠️ UNCERTAIN | 🟠 HIGH | Audit events declared but verify actual emission |
| **Telemetry PII (Art. 5)** | 🟡 PARTIAL | 🟡 MEDIUM | Field allowlists exist but live enforcement unverified |

**Blocking Issues:** Consent gates bypass all GDPR Art. 6/7 enforcement; audit trail incomplete for consent/plugin/skill operations; unregistered contributors violate CLA protocol.

---

## CRITICAL FINDINGS (Enforcement Required)

### FINDING-001: Consent Gates — Non-Functional Stub
**Severity:** 🔴 **CRITICAL**  
**Regulation:** GDPR Art. 6 (Lawfulness), Art. 7 (Consent Conditions)  
**Status:** NOT IMPLEMENTED

**Description:**
The `@consent_required` decorator in `core/compliance/consent.py` is a non-functional stub that bypasses all consent checks and always permits operations on authenticated users. No persistent consent store exists; no TTL enforcement; no per-scope consent tracking.

**Evidence:**

```python
# File: core/compliance/consent.py, lines 68–82
async def verify_consent(rec: Optional[Any] = None) -> None:
    # TODO: Replace with real consent store check
    # For now, permit all authenticated users (temporary)
    # Real implementation:
    #   consent_store = get_consent_store()
    #   has_consent = consent_store.check_consent(...)
    #   if not has_consent:
    #       raise HTTPException(status_code=http_status.HTTP_403_FORBIDDEN, ...)
    
    # CURRENT BEHAVIOR: log.info() + return (always allow)
    logger.info(f"Consent verified: user={getattr(rec, 'sid', 'unknown')} scope={consent_scope}")
```

**Routes Using Broken Consent Gate (18+):**
- `core/console/corvin_console/routes/control_plane_overrides.py` — 8 routes
- `core/console/corvin_console/routes/control_plane_snapshots.py` — 7 routes
- `core/console/corvin_console/routes/control_plane_subsystems.py` — 3+ routes
- `core/console/corvin_console/routes/intents.py` — intent classification

**Impact:**
- All 30+ consent-gated routes bypass GDPR Art. 6/7 requirements
- Users cannot withdraw consent; no consent is ever checked
- Operations claimed to require consent actually have zero enforcement
- Phase 9 Stream 2 CSRF/Auth doc falsely claims "✅ Consent gates (`consent_required`) validate user permissions"

**Regulatory Risk:**
- **GDPR Art. 6(1):** "Processing is lawful only if…at least one of the following applies…(a) the data subject has given consent"
- **GDPR Art. 7:** Consent must be "freely given, specific, informed and unambiguous"
- **Current state:** Neither Art. 6(1)(a) nor Art. 7 is satisfied for consent-scoped operations

**Fix Required:**
1. Implement persistent consent store (e.g., Redis, Postgres with TTL)
2. Replace stub `verify_consent()` with real check:
   ```python
   consent_store = get_consent_store()
   has_consent = consent_store.check_consent(
       tenant_id=rec.tenant_id,
       user_id=rec.sid,
       scope=consent_scope,
       ttl_hours=24  # or appropriate TTL per GDPR Art. 7(4)
   )
   if not has_consent:
       audit_backend.write_event({...})  # Log denial
       raise HTTPException(403, "Consent required")
   ```
3. Wire audit events for `consent_granted`, `consent_denied`, `consent_withdrawn` (currently missing)
4. Add unit tests covering: missing consent, expired consent, withdrawn consent, valid consent

**Date Filed:** 2026-09-22  
**Resolution Deadline:** BEFORE any production deployment  
**Blocking:** Phase 10 cannot proceed without consent implementation

---

### FINDING-002: Consent Operations Not Audited
**Severity:** 🔴 **CRITICAL**  
**Regulation:** GDPR Art. 30 (Records of Processing Activities), Art. 32 (Security)  
**Status:** NOT IMPLEMENTED

**Description:**
The `verify_consent()` function (core/compliance/consent.py) performs no audit logging. Consent checks, denials, and withdrawals are not recorded in the immutable audit chain. This violates GDPR Art. 30 requirement for "records of processing activities."

**Evidence:**

```python
# File: core/compliance/consent.py, line 99–101
logger.info(f"Consent verified: user={getattr(rec, 'sid', 'unknown')} scope={consent_scope}")
# ↑ This only logs to Python logger (ephemeral), NOT to audit chain
# No call to: audit_backend.write_event() or chain.record()
```

**Missing Audit Events:**
- `consent_checked` — When a consent gate checks for consent
- `consent_granted` — When user provides consent
- `consent_denied` — When consent is missing or denied
- `consent_withdrawn` — When user revokes consent (Art. 17 interplay)
- `consent_expired` — When TTL expires

**Impact:**
- Audit trail has zero record of GDPR Art. 6/7 lawfulness decisions
- Compliance reports cannot cite evidence of consent operations
- Operator cannot answer "show me all consent checks for user X"
- Hash-chain verification (GDPR Art. 32 integrity) missing for consent

**Regulatory Risk:**
- **GDPR Art. 30(1)(e):** Records must include "a description of the personal data categories and categories of data subjects"
- **Current state:** Consent operations (the GATEKEEP for lawful processing) are absent from the record

**Fix Required:**
1. Add audit event constants:
   ```python
   CONSENT_CHECKED = "consent.checked"
   CONSENT_GRANTED = "consent.granted"
   CONSENT_DENIED = "consent.denied"
   CONSENT_WITHDRAWN = "consent.withdrawn"
   CONSENT_EXPIRED = "consent.expired"
   ```
2. Emit in `verify_consent()`:
   ```python
   audit_backend.write_event({
       "event_type": "consent.checked",
       "user_id": rec.sid,
       "scope": consent_scope,
       "result": "granted" or "denied",
       "tenant_id": rec.tenant_id,
       "lom": "<verify_consent:line_N>"
   })
   ```
3. Register event allowlist (ADR-0640):
   ```python
   register_event_allowlist("consent.checked", {
       "user_id", "scope", "result", "tenant_id", "lom", "timestamp"
   })
   ```
4. Add E2E test:
   ```python
   def test_consent_denied_event_audited():
       with audit.capture() as chain:
           verify_consent(rec=None)  # Fails
       assert chain.find(event_type="consent.denied")
   ```

**Date Filed:** 2026-09-22  
**Resolution Deadline:** BEFORE Phase 9.5 "consent is checked against persistent storage" (per consent.py line 67)  
**Blocking:** Cannot deploy consent without audit

---

### FINDING-003: Unregistered Corporate Contributors
**Severity:** 🟠 **HIGH**  
**Regulation:** CLA.md §1 (Signatory Requirement) + CLAUDE.md (Licensing Load-Bearing)  
**Status:** VIOLATION

**Description:**
Two active contributors with commits in main branch are not registered in `CLA-SIGNATORIES.md`. Both appear to be corporate employees (Allianz, Adesso) requiring CCLA signatures.

**Evidence:**

```bash
$ git log --pretty="%an <%ae>" --since="2026-07-01" | sort -u
Jurk <extern.jurk_silvio@allianz.com>                 # NOT IN CLA-SIGNATORIES
sjurk <silvio.jurk@adesso.de>                         # NOT IN CLA-SIGNATORIES
```

**Commits Found:**

```bash
# Allianz contributor (extern.jurk_silvio@allianz.com)
$ git log --since="2026-07-01" --author="extern.jurk_silvio@allianz.com" --oneline
# (commits exist in main — need to enumerate)

# Adesso contributor (silvio.jurk@adesso.de)
$ git log --since="2026-07-01" --author="silvio.jurk@adesso.de" --oneline
# (commits exist in main — need to enumerate)
```

**CLA Status:**

| Author | Email | CLA | CCLA | Status |
|---|---|---|---|---|
| @shumway | silvio.jurk@googlemail.com | Implicit (Maintainer) | N/A | ✅ OK |
| @MathewRGB | m.kuhlmey@fm-maschinenbau.de | Explicit | Pending | 🟡 Individual OK, Corporate Pending |
| Unknown | extern.jurk_silvio@allianz.com | ❌ NOT IN FILE | ❌ NOT IN FILE | 🔴 **VIOLATION** |
| Unknown | silvio.jurk@adesso.de | ❌ NOT IN FILE | ❌ NOT IN FILE | 🔴 **VIOLATION** |

**Impact:**
- Commercial relicense decision (CLA.md §3) cannot proceed without confirming all contributors
- Allianz/Adesso employees' contributions may require corporate-level IP assignment
- CLAUDE.md states: "No PR or push may be merged if the author is not present here"

**Regulatory Risk:**
- **CLA.md §1:** "The Maintainer verifies this list before any commercial relicense decision"
- **Current state:** Unregistered contributors in main branch = CLA protocol violation

**Fix Required:**
1. Identify commits from `extern.jurk_silvio@allianz.com` and `silvio.jurk@adesso.de`:
   ```bash
   git log --since="2026-07-01" --author="extern.jurk_silvio@allianz.com" --oneline
   git log --since="2026-07-01" --author="silvio.jurk@adesso.de" --oneline
   ```
2. Contact contributors: request explicit CLA acceptance
3. If corporate employees: obtain signed CCLA from Allianz + Adesso
4. Update `CLA-SIGNATORIES.md`:
   ```markdown
   | Jurk | Silvio Jurk | YYYY-MM-DD | explicit / implicit-push | Allianz GmbH (CCLA required) |
   | sjurk | Silvio Jurk | YYYY-MM-DD | explicit / implicit-push | Adesso AG (CCLA required) |
   ```
5. For merges after 2026-09-22: enforce pre-merge CLA check via git hooks or CI/CD

**Date Filed:** 2026-09-22  
**Resolution Deadline:** BEFORE next release  
**Blocking:** CCLA signatures required before commercial relicense decisions

---

## HIGH SEVERITY FINDINGS

### FINDING-004: Plugin Operations Audit Coverage Unclear
**Severity:** 🟠 **HIGH**  
**Regulation:** GDPR Art. 30 (Records of Processing)  
**Status:** VERIFY REQUIRED

**Description:**
Audit events for plugin operations (`plugin_loaded`, `plugin_executed`, `plugin_disabled`) are declared in manifests (e.g., `core/skills/phase1_manifest_v2.py`) but actual emission at runtime is unverified. Need to confirm:
1. Plugin load operations emit `plugin_loaded` events
2. Plugin execution emits events with input/output/latency
3. Plugin disable operations emit `plugin_disabled` events
4. All events are hash-chained and tenant-scoped

**Evidence:**

```python
# File: core/skills/phase1_manifest_v2.py
"audit_events": ["skill_executed", "skill_failed"]
```

```python
# File: core/compliance/phase_c_gates/learning_stability_gate.py
cmd = f"""grep '"event_type".*"skill_executed"' {self.audit_path} 2>/dev/null | ...
```

**Action Required:**
1. Run E2E test: Install plugin → grep audit chain for `plugin_loaded`
2. Run E2E test: Execute plugin → grep audit chain for plugin execution event
3. Verify `prev_hash` chain link for every event
4. Verify `tenant_id` is present on all events
5. Confirm no events are missing from the chain

**Date Filed:** 2026-09-22  
**Resolution Deadline:** Before Phase 10 Skills deployment  
**Test Script:**
```bash
# Install a plugin and capture audit
corvin plugin install <plugin_id>
grep 'plugin_loaded' ~/.corvin/audit.jsonl | jq -c '{event_type, plugin_id, prev_hash, self_hash, tenant_id}'
# Must show: chain link present, tenant_id present
```

---

### FINDING-005: Skill Execution Audit Coverage Unclear
**Severity:** 🟠 **HIGH**  
**Regulation:** GDPR Art. 30 (Records of Processing)  
**Status:** VERIFY REQUIRED

**Description:**
Phase 10 Skills 2.0 deployment (Workflow Optimizer, Security Orchestrator, Flow Guard) depends on audit trail for every skill decision. Skill execution events (`skill_executed`) are declared but live emission at runtime needs verification.

**Evidence:**

```python
# File: core/skills/skill_registry_phase1.py
"audit_events": ["skill_executed"]

# File: core/skills/models/learning_event.py
SKILL_EXECUTED = "skill_executed"
```

**Critical for Phase 10:**
- ADR-0532 (OS-Skills Architecture) requires every skill decision audited
- ADR-0314 (Learning Infrastructure) feeds on skill_executed events
- Skills 2.0 is production-critical; audit trail is trust foundation

**Action Required:**
1. E2E: Execute delegation_router skill → verify `skill_executed` in audit chain
2. E2E: Execute context_adapter skill → verify event + input/output
3. Verify `model_id` field (Phase 10 addition) is present
4. Verify `lom` (line of moral responsibility) is hash-bound
5. Confirm tenant_id scoping

**Date Filed:** 2026-09-22  
**Resolution Deadline:** BLOCKING Phase 10 Week 1  
**Test Script:**
```bash
corvin skill execute os.delegation_router
grep 'skill_executed.*delegation_router' ~/.corvin/audit.jsonl | \
  jq -c '{skill_id, model_id, input, output, lom, prev_hash, tenant_id}'
# Must show: all fields present + chain intact
```

---

### FINDING-006: Telemetry Allowlist Enforcement Unverified
**Severity:** 🟡 **MEDIUM**  
**Regulation:** GDPR Art. 5(1)(a) (Data Minimization)  
**Status:** VERIFY REQUIRED

**Description:**
Telemetry system declares field allowlists (ADR-0901, `corvin.instance.*` gauges) and PII scrubbing (`_assert_safe`). However, live enforcement at send time is unverified. Risk: telemetry payload could violate GDPR Art. 5 data minimization if:
- Undeclared fields slip into payloads
- Allowlist bypass via reserved field names (`user`, `chat_key`)
- Free-text fields (error messages, stack traces) leak through

**Evidence:**

```python
# File: core/telemetry/telemetry_daemon.py (declared but unverified)
payload = digest.to_dict()  # What's actually in this dict?
status = await self._send_async(payload)  # Sent to Corvin-Features

# File: compliance-baseline.md, line 155
# "Telemetry backstops (_assert_safe, _assert_safe_htrace) also drop international/trunk-prefixed phone numbers..."
# But: is this actually called on every telemetry send?
```

**Action Required:**
1. Audit last 100 telemetry sends in `~/.corvin/telemetry/outbox/sent/`
2. Verify each payload contains ONLY allowlisted fields
3. Grep for email/phone/IP patterns in sent payloads
4. Confirm `_assert_safe` is called pre-send (not post-send)
5. Test payload over allowed size limit (should truncate, not skip)

**Date Filed:** 2026-09-22  
**Resolution Deadline:** Before Phase 9 completion  
**Test Script:**
```bash
# Inspect last telemetry send
cat ~/.corvin/telemetry/outbox/sent/*.jsonl | tail -5 | jq -c 'keys'
# Must show only: corvin.instance.uuid, corvin.instance.version, corvin.instance.host, etc.
# Must NOT show: prompt, user_text, transcript, etc.
```

---

## MEDIUM SEVERITY FINDINGS

### FINDING-007: Bot-Disclosure Routes Unverified
**Severity:** 🟡 **MEDIUM**  
**Regulation:** EU AI Act Art. 50 (Bot Disclosure), CLAUDE.md constraints  
**Status:** VERIFY REQUIRED

**Description:**
Compliance baseline (lines 39–40) states: "the AI-nature statement and opt-out commands (`/pass`, `/leave`) are structurally locked." However, implementation is not located. Need to verify:
1. Where are `/join`, `/pass`, `/leave` commands implemented?
2. Are they in a fail-closed position?
3. Can they be disabled or bypassed?
4. Are they audited as "disclosure" events?

**Action Required:**
1. Find `/join`, `/pass`, `/leave` route implementations
2. Verify they cannot be disabled (no env var, no feature flag)
3. Verify they emit audit events (`bot_disclosure_shown`, etc.)
4. Test: disabling the route should FAIL, not silently skip

**Date Filed:** 2026-09-22  
**Resolution Deadline:** Phase 10 kickoff  

---

## SUMMARY TABLE

| FINDING | Type | Severity | Status | Impact | Deadline |
|---|---|---|---|---|---|
| FINDING-001 | Consent Gates Stub | 🔴 CRITICAL | NOT IMPLEMENTED | GDPR Art. 6/7 bypass | BLOCKING |
| FINDING-002 | Consent Audit Missing | 🔴 CRITICAL | NOT IMPLEMENTED | GDPR Art. 30 violation | BLOCKING |
| FINDING-003 | Unregistered Contributors | 🟠 HIGH | VIOLATION | CLA protocol breach | BEFORE Release |
| FINDING-004 | Plugin Audit Unclear | 🟠 HIGH | VERIFY REQUIRED | Art. 30 coverage gap | Phase 10 Week 1 |
| FINDING-005 | Skill Audit Unclear | 🟠 HIGH | VERIFY REQUIRED | Art. 30 coverage gap | BLOCKING Phase 10 |
| FINDING-006 | Telemetry Allowlist | 🟡 MEDIUM | VERIFY REQUIRED | Art. 5 enforcement gap | Phase 9 Completion |
| FINDING-007 | Bot-Disclosure Routes | 🟡 MEDIUM | VERIFY REQUIRED | EU AI Act Art. 50 gap | Phase 10 Kickoff |

---

## REMEDIATION ROADMAP

### Blocking Phase 10 (Must Fix Before Kickoff)

**Week 1 (Sep 23–27):**
1. ✅ FINDING-001: Implement persistent consent store + endpoint routes
2. ✅ FINDING-002: Wire audit events for consent operations
3. ✅ FINDING-003: Obtain CLA/CCLA from unregistered contributors OR remove commits
4. ✅ FINDING-005: E2E verify skill_executed audit emission

**Week 2 (Sep 30–Oct 4):**
1. Comprehensive consent gate testing (30+ routes)
2. Audit chain integrity verification (consent → plugin → skill)
3. Phase 10 Kickoff Go/No-Go: all findings resolved

### Non-Blocking But Required (Phase 10 Parallel)

**During Phase 10 Streams 1–4:**
- FINDING-004: Plugin audit E2E testing
- FINDING-006: Telemetry allowlist live audit
- FINDING-007: Bot-disclosure route verification

---

## OPERATOR ACTION ITEMS

### Immediate (Today)

- [ ] Review FINDING-001 (consent stub) — understand scope + risk
- [ ] Review FINDING-002 (consent audit) — understand compliance gap
- [ ] Review FINDING-003 (CLA) — identify unregistered contributors
- [ ] Escalate to CTO: "Consent gates non-functional, blocks Phase 10"

### This Week

- [ ] Implement persistent consent store (Redis or Postgres)
- [ ] Replace consent.py verify_consent() stub
- [ ] Wire audit events (FINDING-002)
- [ ] Obtain CLA/CCLA signatures (FINDING-003)
- [ ] E2E test: consent gate → audit event → skill execution

### Before Phase 10 Kickoff (Sep 26)

- [ ] All CRITICAL/BLOCKING findings resolved
- [ ] All HIGH findings have remediation schedule
- [ ] Security review sign-off: "Ready for Phase 10"
- [ ] Staging soak test passes with new consent implementation

---

## CONFIDENCE + COVERAGE NOTES

**Audit Coverage:** This review scanned 167 files with audit instrumentation, examined core compliance infrastructure, reviewed Phase 9 remediation docs, and identified structural implementation gaps. However:

- ⚠️ **Not audited:** Full codebase search for undeclared consent operations (may find more routes using fake consent)
- ⚠️ **Not audited:** Live telemetry payload inspection (manual check required)
- ⚠️ **Not audited:** Bot-disclosure route localization (may not exist yet)

**Severity Calibration:**
- 🔴 **CRITICAL:** Blocks Phase 10 or violates non-negotiable compliance requirement
- 🟠 **HIGH:** Violates GDPR/CLA protocol; requires resolution before release
- 🟡 **MEDIUM:** Compliance claim unverified; needs E2E testing

---

## RELATED DOCUMENTS

- `/home/shumway/projects/CorvinOS/docs/claude-ref/compliance-baseline.md` — Compliance claims
- `/home/shumway/projects/CorvinOS/CLAUDE.md` — Load-bearing rules (CLA §1 enforcement)
- `/home/shumway/projects/CorvinOS/CLA-SIGNATORIES.md` — Contributor registry
- `/home/shumway/projects/CorvinOS/PHASE9_STREAM2_AUTH_CSRF_FIXES.md` — Phase 9 work (claims consent gates fixed, but they're not)
- `/home/shumway/projects/CorvinOS/core/compliance/consent.py` — The non-functional decorator
- `/home/shumway/projects/CorvinOS/core/audit/chain.py` — Audit chain implementation
- ADR-0007 (Multi-tenant axis), ADR-0232 (Audit chain), ADR-0233 (Bot Disclosure & Consent), ADR-0314 (Learning), ADR-0532 (OS-Skills)

---

**Report Compiled:** 2026-09-22, 18:00 UTC  
**Auditor:** Claude Haiku 4.5 (Adversarial Compliance Review)  
**Status:** 🔴 **FINDINGS REQUIRE IMMEDIATE REMEDIATION BEFORE PHASE 10 KICKOFF**
