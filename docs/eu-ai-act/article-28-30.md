# Art. 28–30 — Deployer (operator) obligations

> **Short summary:** Art. 28 identifies who is a "deployer" (= operator in EU AI Act terms).
> Art. 29 requires deployers to follow instructions, monitor operation, and report incidents.
> Art. 30 requires deployers to maintain records. Corvin enforces these through the
> Operator Declaration Gate — a boot-time check that blocks `eu_production` deployments
> without a completed Data Protection Impact Assessment.

---

## Who is a "deployer" in the Corvin context?

Under EU AI Act Art. 28, a deployer is any natural or legal person who uses an AI system
under their own authority for a purpose other than personal non-professional use.

**In Corvin terms:** the operator who configures `tenant.corvin.yaml` and runs the
`bridge.sh` daemon in a production environment is the deployer.

The Corvin maintainer provides the platform; the operator provides the deployment.
The declaration gate enforces that this distinction is acknowledged before going live.

---

## The Operator Declaration Gate

**Module:** `operator/bridges/shared/operator_declaration.py`

### How it works

At every boot (`bridge.sh doctor`), the self-test calls `check_operator_declaration()`.
For `eu_production` and `eu_production_ollama` profiles, this checks that the tenant YAML
contains a complete `spec.operator_declaration` block.

Missing or incomplete declaration → CRITICAL self-test failure → container unhealthy.

### Required configuration

```yaml
spec:
  deployment_profile: eu_production   # or eu_production_ollama

  operator_declaration:
    version: "1.0"
    dpia_completed: true              # MUST be true
    dpia_date: "2026-06-15"           # MUST be set
    declared_by: "Jane Doe, DPO"     # optional; stored locally, never in audit
    permitted_use: "internal-coding-assistant"  # optional; stored locally, never in audit

  compliance_manifest:
    min_version: "1.0.0"
```

### Check result scenarios

| Profile | Declaration present | DPIA completed | Boot result |
|---|---|---|---|
| `dev`, `test`, or none | N/A | N/A | ✅ INFO — declaration not required |
| `eu_production` | No | N/A | ❌ CRITICAL — "operator_declaration missing" |
| `eu_production` | Yes | false | ❌ CRITICAL — "dpia_completed is false" |
| `eu_production` | Yes | true, no date | ❌ CRITICAL — "dpia_date missing" |
| `eu_production` | Yes | true, with date | ✅ INFO — declaration verified |
| `eu_production` (no min_version) | ✅ | ✅ | ⚠️ WARNING — pin compliance manifest version |

### Privacy: what stays local

The `declared_by` (DPO name) and `permitted_use` (use-case description) fields are stored
only in `tenant.corvin.yaml`. They **never** enter the audit chain.

`audit_dict()` only contains:

```python
{
    "declaration_version": "1.0",
    "dpia_completed": True,
    "dpia_date": "2026-06-15",
    "deployment_profile": "eu_production",
}
```

**Test coverage:** `operator/bridges/shared/test_operator_declaration.py` (9 tests)

The tests `test_declared_by_not_in_audit_dict` and `test_permitted_use_not_in_audit_dict`
specifically verify that PII never appears in the auditable output.

---

## Art. 29 — Following instructions for use

Art. 29 §1 requires deployers to follow the provider's instructions for use.

The instructions for Corvin are embedded in code as enforced constraints, not advisory text:

| Instruction | Enforcement mechanism |
|---|---|
| Disclose AI nature | L19 disclosure gate — structurally non-bypassable |
| Operate within declared use case | `permitted_use` field in declaration (self-declared, DPO responsibility) |
| Monitor serious incidents | L39 IncidentAutoDetector + daily scan timer |
| Report incidents within 15 days | `corvin-incident notify-draft` + CLI workflow |
| Maintain audit records | L16 hash chain + L37 at-rest encryption + 7-year retention |

Art. 29 §3 requires deployers to suspend operation when they identify risks. The CLI command
for this is: stop the bridge daemon (`bridge.sh stop`) and open/contain the relevant incident.

---

## Art. 30 — Record-keeping

Art. 30 requires deployers to retain records of operations, incidents, and oversight activities.

Corvin meets this through:

### Audit chain (L16 + L37)

Every operation emits a structured event into the hash-chained audit log:

```
<tenant>/global/forge/audit.jsonl          ← live chain
<tenant>/global/forge/audit.TIMESTAMP.jsonl.age  ← sealed segment (AES-256 via age)
```

- **Retention:** 7 years (configurable in `spec.audit.retention_years`)
- **Integrity:** SHA-256 hash chain; `voice-audit verify` checks at every boot
- **Encryption:** At-rest encryption via `age` or `gpg` (operator provides recipient key)
- **Immutability:** Append-only; path-gate blocks any attempt to modify existing entries

### Incident records (L39)

All incidents (open, contained, notified, closed) are retained at:

```
<tenant>/global/incidents/<incident_id>.json    (mode 0600)
```

These are NOT part of the hash chain (descriptions may contain sensitive detail), but are
accessible to authorized DPO processes via `corvin-incident export`.

### Compliance manifest

Every boot verifies the compliance manifest and emits `compliance.manifest_check` into the
audit chain. This creates an auditable record of the compliance posture at each startup.

### Annex IV technical documentation

Auto-generated from existing sources:

```bash
corvin-annex-iv generate --output annex-iv.md
corvin-annex-iv export-package  # full audit package
```

Covers all 14 Annex IV elements required for technical documentation.

---

## DPIA — Data Protection Impact Assessment

A DPIA is required for deployments that process data "likely to result in a high risk" to
natural persons (GDPR Art. 35). The `dpia_completed: true` field in the declaration confirms
the operator has conducted this assessment.

The DPIA template is at: `docs/compliance/DPIA-TEMPLATE.md`

**Key DPIA inputs from Corvin:**
- Data categories processed: conversation text, user identifiers (pseudonymized), voice metadata
- Data retention: audit chain (7 years), recall DB (operator-configurable), sessions (7 days TTL)
- International transfers: depends on engine selection; EU production presets block US cloud
- Right to erasure: implemented via L36 `corvin-erasure <subject_id>`
- Security measures: L16 hash chain, L37 encryption, L10 path-gate, L35 egress lockdown

---

## Permitted and prohibited uses

### Permitted

- Internal productivity tools (coding assistant, document drafting, Q&A)
- Customer-facing support with disclosed AI nature and explicit user consent
- Research assistance with appropriate data residency configuration

### Prohibited (by platform design)

- **Biometric surveillance** — Corvin does not process biometric data
- **Social scoring** — no user scoring or ranking mechanism exists
- **Subliminal manipulation** — no hidden persuasion mechanism; all outputs marked AI-generated
- **Profiling for law enforcement** — not supported; no profiling API exists
- **Processing without consent** — technically impossible: consent gate blocks all processing

---

## Compliance checklist for EU production deployment

```
[ ] deployment_profile: eu_production set in tenant.corvin.yaml
[ ] spec.operator_declaration filled:
    [ ] version: "1.0"
    [ ] dpia_completed: true
    [ ] dpia_date: <YYYY-MM-DD>
    [ ] declared_by: <DPO name>
    [ ] permitted_use: <use-case description>
[ ] spec.compliance_manifest.min_version: "1.0.0" set
[ ] spec.egress.enabled: true with eu_production_ollama or eu_production_http preset
[ ] spec.audit.encryption_at_rest.enabled: true with valid recipient key
[ ] spec.audit.retention_years: 7 (or operator-required value)
[ ] corvin-incident-scan.timer installed and enabled
[ ] corvin-audit-verify.timer installed and enabled
[ ] bridge.sh doctor shows no CRITICAL failures
[ ] DPIA documented and signed by DPO
[ ] Supervisory authority contacts recorded (docs/compliance/INCIDENT-RESPONSE-PLAN.md)
```
