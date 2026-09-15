---
name: corvinOS_review_compliance
description: CorvinOS Code Review: GDPR + EU AI Act + Security (L16–L38 deep-dive)
---

# CorvinOS Review: Compliance & Security Deep-Dive

## PHASE 3: GDPR & EU AI ACT

### 3a. GDPR Art. 5 (Data Minimisation)
- [ ] Only metadata in audit events, never prompt-text?
- [ ] Only metadata in voice-transcribe events, never transcript?
- [ ] User-Recall/User-Model PII-redacted before INSERT?
- [ ] No raw email/name as `subject_id` in L36 erasure?
- [ ] Per-UID consent-gate, deny-by-default, TTL-capped?

### 3b. GDPR Art. 17 (Right to Deletion / L36)
If erasure code:
- [ ] All `ErasureHandler` registered (L7/L24/L28/L33)?
- [ ] Trail-file mode 0600?
- [ ] `subject_id` regex: `^[A-Za-z0-9.:_\-]{1,128}$`?
- [ ] Audit pseudonyms remain but untraceable?

### 3c. EU AI Act 2026
- [ ] Bot-disclosure (`/join`/`/pass`/`/leave` card) intact?
- [ ] AI-nature statement structurally locked (not bypassable)?
- [ ] Compliance-zone routing (`tenant.corvin.yaml::data_residency`) respected?
- [ ] Engine-policy allowlist (`allowed_engines`/`forbid_engines`) enforced?

### 3d. Secret Vault (L16 v3)
- [ ] Secrets as env-var names only (never values)?
- [ ] Vault file mode 0600?
- [ ] Never in LLM context?
- [ ] BwrapEnv-Injection, not direct file-read?

---

## PHASE 4: SECURITY HARDENING

### 4a. Path-Gate Hook (L10)
- [ ] Bash write-detect correct? (redirects, tee, mv/cp, sed -i, dd)?
- [ ] eval/exec/$(…)/backticks blocked?
- [ ] Forge/skill-forge workspace protected?
- [ ] Self-test passes on boot (`path_gate.self_test_failed` CRITICAL)?

### 4b. Injection Attacks
- [ ] NFKC normalization for user-input?
- [ ] HTML-attribute-escape for A2A origin attrs?
- [ ] Control-char strip?
- [ ] `<a2a_instruction>` framing block present (A2A)?

### 4c. Secrets Handling
- [ ] No secrets in logs/audit?
- [ ] No secret caching?
- [ ] BwrapEnv-inject, not file-read?
- [ ] Vault permissions 0600?

### 4d. Observer-Transcript (L16)
If multi-user:
- [ ] Framing block at START of system-prompt?
- [ ] Read-only role enforces no writes?

---

## PHASE 5: AUDIT INTEGRITY (L16 Chain)

### 5a. Hash-Chain Continuity
- [ ] Each event has prev_hash correct?
- [ ] Rotation-links (`audit.rotation_link`) present at seal?
- [ ] `voice-audit verify` returns exit 0?

### 5b. Audit-Event Allowlist
- [ ] No prompt/output/task-text in `details`?
- [ ] Only allowlisted fields per event-type?
- [ ] Sensitive-info (URLs, passwords, email) excluded?

### 5c. Audit-First Invariant
For Forge/Skill-Forge/Policy writes:
- [ ] Audit-event written BEFORE operation?
- [ ] Audit-write failure blocks operation?

### 5d. Encryption at Rest (L37)
If sealing active:
- [ ] Rotation-trigger (size/age) respected?
- [ ] TSA timestamping (if enabled)?
- [ ] Unseal logs `audit.unseal_requested` (WARNING)?
- [ ] Key-material never in `details`?

---

## DECISION MATRIX

| Finding | Action |
|---|---|
| GDPR violation | REQUEST CHANGES (cite Art. #) |
| Secret exposed | REJECT |
| Hash-chain broken | REJECT |
| Audit allowlist breached | REQUEST CHANGES |
| No event for gated op | REQUEST CHANGES |
| All compliance checks green | → Continue to Frontend Testing |

