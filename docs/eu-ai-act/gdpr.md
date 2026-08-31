# GDPR compliance

> Corvin processes personal data (conversation content, user identifiers, voice metadata).
> This document maps each applicable GDPR article to the technical mechanism that meets it.

---

## Lawful basis: Art. 6 + Art. 7 — Consent (Layer 16, Phase 4)

**Module:** `operator/bridges/shared/consent.py`

GDPR Art. 6(1)(a) and Art. 7 require that consent be freely given, specific, informed, and
unambiguous. Corvin implements a **deny-by-default, per-user, TTL-capped consent gate**.

### How it works

No message is processed without a valid `consent.granted` entry for the sending uid.
The only paths to granting consent are:

| Command | Mechanism | Duration |
|---|---|---|
| `/consent on` | Durable grant | Until `/consent off` |
| `/consent <duration>` | TTL-bounded grant | 60s–30 days |
| `/share <text>` | One-shot admission | Single message only |

### Invariants (enforced by code, verified by tests)

| Invariant | Test |
|---|---|
| Deny-by-default: no processing without consent | `test_consent_gate.py` |
| Owner cannot grant consent on behalf of another user | code: uid from bridge protocol |
| Expired consent = no processing (re-validated at consume) | `test_consent_expiry` |
| TOCTOU guard: stale epoch (>30s) re-validates from disk | `is_granted_with_epoch()` |
| `/share` one-shot is audited and cannot be batched | `admit_share_one_shot()` |

### Consent storage (mode 0600)

```
<tenant>/global/consent/<channel>__<chat>.json
```

```json
{
  "user_42": {
    "mode": "time_bounded",
    "granted_at": 1778204770.0,
    "expires_at": 1778208370.0,
    "channel": "discord",
    "granted_via": "slash",
    "ttl_s": 3600
  }
}
```

### Audit events

All consent operations land in the hash chain:

| Event | Severity |
|---|---|
| `consent.granted` | INFO |
| `consent.revoked` | INFO |
| `consent.expired` | INFO |
| `consent.observer_dropped` | INFO |
| `consent.share_admitted` | INFO |
| `consent.toctou_drop` | WARNING |
| `consent.store_corrupted` | CRITICAL |
| `consent.gate_unavailable_drop` | WARNING |
| `erasure.user_model_deleted` | INFO |

---

## Data minimisation: Art. 5(1)(b)(c) — Layer 23 + Layer 32

### Voice transcription (Layer 23)

**Module:** `operator/voice/scripts/stt/`

When a voice note is transcribed, only **metadata** enters the audit chain:

```json
{
  "event_type": "voice.transcribed",
  "details": {
    "provider": "openai_whisper",
    "language": "de",
    "audio_s": 12.4,
    "char_count": 180
  }
}
```

**The transcript text never appears in the audit chain.** This is enforced structurally:
`write_event()` is called with the metadata dict, not the transcript string.

**Regulation:** GDPR Art. 5(1)(c) (data minimisation — only what's necessary)

### Strict anonymisation (Layer 32)

For data snapshots that go through the analytics pipeline (`data_classification.py`),
strict anonymisation can be applied:

- **k-Anonymity bucketing** (k=5): any value that appears fewer than 5 times is suppressed
- **Laplace noise** on row counts: differential privacy for frequency data
- **Post-projection PII scan**: regex scan after anonymisation to catch any surviving identifiers

**Regulation:** GDPR Art. 5(1)(b) (purpose limitation), Art. 5(1)(c) (data minimisation)

---

## Data subject rights

### Art. 17 — Right to erasure (Layer 36)

**Module:** `operator/bridges/shared/erasure_orchestrator.py`

The erasure orchestrator provides a cross-layer deletion mechanism:

```bash
corvin-erasure <subject_id>     # pseudonymous identifier, not raw email
```

#### Subject-ID format

Raw email addresses and names are **rejected** at the API:

```python
_SUBJECT_ID_RE = re.compile(r"^[A-Za-z0-9.:_\-]{1,128}$")
# Accepted: "discord:1234567890", "user_42", "telegram:abc123"
# Rejected: "user@example.com", "Jane Doe", "user/tenant"
```

The operator maintains their own mapping from real identity to subject_id. This mapping
is the only thing erased — the audit chain uses the same pseudonymous identifier and
remains intact, but becomes untraceable once the mapping is gone.

#### Erasure across layers

| Layer | Content erased | Method |
|---|---|---|
| L7 (Skill-Forge) | User-scope skills | File deletion |
| L24 (Data) | PII-tagged snapshots | Schema purge |
| L28 (Recall) | Conversation history | `DELETE FROM turns WHERE user_id = ?` |
| L28.2 (User Model) | Distilled user model JSON | File deletion — `<tenant>/global/memory/user_model/<channel>__<chat>.json` |
| L33 (Artifacts) | Unpinned session artifacts | File deletion |
| L16 (Audit identity) | Identity → subject_id mapping | Mapping table purge |

The L28.2 user model (a distilled JSON summary of communication patterns, goals, and preferences derived from conversation history) is covered by the erasure chain. `L28UserModelHandler` handles this: on erasure, all user model files matching the subject's scope are deleted. The distilled model is PII-bearing — it contains LLM-inferred patterns that may be traceable to a person even without the raw conversation turns.

#### Audit chain and Art. 17(3)(b)

The audit chain events themselves are **not erased**. The events remain — they just
reference an opaque `subject_id` that, once the identity mapping is deleted, is no longer
traceable to a natural person. This is consistent with Art. 17(3)(b) (erasure not required
where legal obligation to retain applies — GDPR Art. 30 record-keeping for 7 years).

### Art. 20 — Data portability

Users can access their events via `/audit me` (scoped view of their own chain events).
Full export: `voice-audit export --uid <subject_id>`.

### Art. 15 — Right of access

The `/audit me` command shows the user their own audit events. Operators can provide full
exports via the CLI.

---

## Records of processing: Art. 30 + Art. 32 (Layers 16, 37)

### Hash-chained audit log (Layer 16)

Every processing activity emits into a SHA-256-chained append-only log:

```
<tenant>/global/forge/audit.jsonl
```

The chain ensures any post-hoc modification of records is detectable. An auditor can
run `voice-audit verify` with no Corvin instance — just the raw JSONL file.

**Regulation:** GDPR Art. 30 (records of processing activities)

### Encryption at rest (Layer 37)

Rotated audit segments are encrypted via `age` (recommended) or `gpg`:

```
audit.2026-05-26T030000.jsonl.age    ← AES-256-GCM encrypted
```

Plaintext is securely overwritten after sealing. Sealed file permissions: 0444 (read-only).

**Rotation defaults:** 100 MB or 30 days, whichever comes first.
**Retention:** 7 years (configurable per tenant).

**Regulation:** GDPR Art. 32 (security of processing — encryption, integrity, availability)

---

## Security of processing: Art. 32

### Path-Gate (Layer 10)

Prevents direct writes to audit logs, policy files, vault, and forge workspaces from any
tool (including Claude Code's own Write/Edit tools).

Detected patterns include: shell redirects, `sed -i`, Python `open()` write mode, `mv`/`cp`
targeting protected paths, and `eval`/`exec`/backtick constructs.

**Regulation:** GDPR Art. 32 (technical measures to ensure security)

### Secret vault

Secrets (API keys, credentials) are stored in `~/.config/corvin-voice/secrets.json`
(mode 0600). They are injected into the engine subprocess via environment variables —
they never appear in:
- Audit chain entries (names only, never values)
- LLM context (bwrap environment isolation)
- Log files

**Regulation:** GDPR Art. 32 (security of processing — access control)

### No PII in Prometheus / logs / audit details

The audit chain allow-list mechanism ensures PII never enters monitoring infrastructure:

| What is never in audit | What is there instead |
|---|---|
| Email addresses | uid_hash (sha256[:8]) |
| Names | pseudonymous subject_id |
| Phone numbers | — (filtered by PII scan) |
| Transcript text | char_count, language, audio_s |
| Prompt / output text | token counts, run_id |
| Secret values | secret names (env var names) |
| File paths with PII | path category (e.g., "vault") |

---

## GDPR compliance manifest rules

From `compliance/gdpr.yaml`:

| Rule ID | Article | Implementation | Test |
|---|---|---|---|
| `gdpr.art6.7.consent` | Art. 6, 7 | `consent.py` | `test_consent_gate.py` |
| `gdpr.art30.32.audit_chain` | Art. 30, 32 | `audit.py` | `test_audit_unified.py` |
| `gdpr.art32.secrets` | Art. 32 | `vault.py` | `test_vault.py` |
| `gdpr.art32.path_gate` | Art. 32 | `path_gate.py` | `test_path_gate.py` |
| `gdpr.art5.voice_transcription` | Art. 5 | `stt/` | `test_stt.py` |
| `gdpr.art17.erasure` | Art. 17 | `erasure_orchestrator.py` | `test_erasure_orchestrator.py` |
| `gdpr.art17.erasure_user_model` | Art. 17 | `erasure_handlers.py` (`L28UserModelHandler`) | `test_erasure_orchestrator.py` |
| `gdpr.art5.anonymisation` | Art. 5 | `data_classification.py` | — |
| `gdpr.art5.pii_labels` | Art. 5 | `audit.py` | `test_audit_unified.py` |

All rules are checked by `bridge.sh doctor` and blocked at PR time by `compliance-check.yml`.

---

## GDPR contact information

| Role | Responsibility |
|---|---|
| Data Controller | The operator who deploys Corvin |
| Data Processor | Corvin (platform) |
| DPO | Named in `spec.operator_declaration.declared_by` |
| Supervisory Authority | BfDI (for German deployments), local DPA elsewhere |

Templates for privacy notice and DPO contact: `docs/compliance/PRIVACY-NOTICE-TEMPLATE.md`
