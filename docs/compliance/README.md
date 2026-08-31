# Corvin Compliance Hub

> **Corvin v0.14.0 — EU AI Act 2026 · GDPR · ISO/IEC 42001 · NIST AI RMF**
>
> Classification: **Limited-Risk AI System** (Art. 3(1), Annex I EU AI Act 2026).
> All applicable obligations are enforced structurally in code — not by policy checklist.

This is the single entry point for everything compliance-related. Whether you are
a developer, DPO, auditor, or enterprise evaluator, start here.

---

## Quick navigation — "I need to..."

| Goal | Go to |
|---|---|
| Understand Corvin's EU AI Act status | [Risk Classification](#risk-classification) |
| See which article maps to which layer | [Regulatory obligation map](#regulatory-obligation-map) |
| Run the compliance check right now | [CLI commands](#cli-commands) |
| Generate an audit package for an auditor | [`corvin-annex-iv export-package`](#certification-package) |
| Find all compliance documents in one table | [Document index](#document-index) |
| Understand what's enforced vs. what's operator responsibility | [Structural invariants vs. operator duties](#structural-vs-operator) |
| Prepare a pre-deployment DPO checklist (German) | [`DSB-CHECKLIST.md`](DSB-CHECKLIST.md) |
| Fill in a DPIA | [`DPIA-TEMPLATE.md`](DPIA-TEMPLATE.md) |
| Report a serious incident (Art. 73) | [`INCIDENT-RESPONSE-PLAN.md`](INCIDENT-RESPONSE-PLAN.md) |
| Understand permitted / prohibited use cases | [`OPERATOR-OBLIGATIONS.md`](OPERATOR-OBLIGATIONS.md) |
| See the machine-readable compliance rules | [`compliance/`](../../compliance/) |

---

## Risk Classification

**Corvin is a Limited-Risk AI System under EU AI Act 2026.**

| Question | Answer |
|---|---|
| Annex III high-risk? | No — not biometric, critical infrastructure, credit scoring, or law enforcement |
| Art. 5 prohibited practice? | No — no subliminal manipulation, no social scoring, no real-time remote biometrics |
| Art. 50 disclosure obligation? | **Yes** — conversational AI interacting with natural persons |
| General-Purpose AI Model? | No — Corvin does not train or publish model weights |
| Regulatory tier | **Limited Risk** (Art. 50 + Art. 28–30 deployer obligations) |

Conditional reclassification: if an operator deploys Corvin as a component in
an **Annex III** workflow (e.g. CV screening, credit scoring), the operator bears
Art. 25–28 cascade obligations. Corvin provides the structural audit, consent,
and data-flow machinery; the operator must complete the Notified Body engagement
for that deployment.

→ Full formal statement: [`RISK-CLASSIFICATION.md`](RISK-CLASSIFICATION.md)

---

## Regulatory Obligation Map

### EU AI Act 2026

| Article | Obligation | Implementation | Layer / Module |
|---|---|---|---|
| Art. 50 §1 | Disclose AI nature to every user | Bot-disclosure card, one-time per (channel, uid), structurally locked | **L19** `disclosure.py` |
| Art. 50 §4 | Machine-readable AI content marking | `provenance` block in every final message envelope (`ai_generated`, `generator_id`, `persona`, `session_id`, `timestamp_utc`) | **adapter.py `_envelope()`** |
| Art. 14 | Human oversight | Compliance-zone routing, engine-policy allowlist, data classification matrix | **L34 / L35** |
| Art. 28 | Deployer identification + use-case documentation | Operator Declaration Gate — boots CRITICAL in `eu_production` without completed DPIA | **L40** `operator_declaration.py` |
| Art. 29 | Deployer monitoring | `bridge.sh doctor`, daily audit-verify timer, incident auto-detection | **L39 / self-test** |
| Art. 30 | Deployer record-keeping | Hash-chained audit log, 7-year retention, encryption-at-rest | **L16 / L37** |
| Art. 43 | Technical documentation (Annex IV) | `corvin-annex-iv generate` — reproducible from manifests + ADRs | **CLI** `corvin_annex_iv.py` |
| Art. 73 | Serious incident reporting (15-day window) | L40 IncidentAutoDetector + `corvin-incident notify-draft` | **L40** `incident_tracker.py` |

### GDPR

| Article | Obligation | Implementation | Layer |
|---|---|---|---|
| Art. 5(1)(c) | Data minimisation | Voice transcription audit emits metadata only — never transcript text | **L23** |
| Art. 6 / 7 | Lawful basis + consent | Per-user consent gate, deny-by-default, TTL-capped | **L16 Phase 4** `consent.py` |
| Art. 13 / 14 | Privacy notice obligation | Operator uses [`PRIVACY-NOTICE-TEMPLATE.md`](PRIVACY-NOTICE-TEMPLATE.md) | Operator |
| Art. 17 | Right to erasure | Cross-layer erasure orchestrator (`corvin-erasure`) | **L36** `erasure_orchestrator.py` |
| Art. 30 | Records of processing | Hash-chained audit log | **L16** `audit.py` |
| Art. 32 | Security of processing | Secret vault (mode 0600), bwrap sandbox, path-gate, encryption-at-rest | **L10 / L16 / L37** |
| Art. 25 (pseudonymisation) | Privacy by design — pseudonymous audit chain | CorvinID: `instance_id` (UUID, no PII) in every audit event; operator_email only in local cert (mode 0600); explicit deanonymisation gate with CRITICAL audit | **ADR-0153** `instance_identity.py` |

### ISO/IEC 42001 + NIST AI RMF

| Framework | Coverage | Status |
|---|---|---|
| ISO/IEC 42001:2023 | 22 operational clauses — AIMS policy, risk management, continual improvement | `compliance/iso-42001.yaml` — all clauses mapped |
| NIST AI RMF 1.0 | 22 subcategories across GOVERN / MAP / MEASURE / MANAGE | `compliance/nist-ai-rmf.yaml` — all subcategories mapped |

**Cross-reference:** every NIST subcategory links to its ISO 42001 clause and EU AI Act article.
Generate the full table: `corvin-annex-iv cross-reference`

---

## Structural vs. Operator

### What Corvin enforces in code (cannot be disabled)

| Invariant | Code location | Consequence if violated |
|---|---|---|
| AI-nature disclosure | `disclosure.py` + L19 | Message blocked; `disclosure.shown` never emitted |
| Per-user consent | `consent.py` + L16 Phase 4 | Message blocked; audit CRITICAL |
| Audit chain write | `audit.py` + L16 | Every gate event; hash link mandatory |
| Engine policy | `engine_policy.py` + L34 / L35 | Spawn blocked; `data_flow.blocked` CRITICAL |
| Path-gate | `path_gate.py` + L10 | Write blocked; self-test on every boot |
| Operator declaration (eu_production) | `operator_declaration.py` + L40 | Adapter refuses to start |
| CorvinID pseudonymisation | `instance_identity.py` + ADR-0153 | Every audit event carries only `instance_id` (UUID, no PII); deanonymisation requires explicit `corvin-id resolve` + CRITICAL audit event |

### What the operator must do (outside Corvin's scope)

| Obligation | Document | Owner |
|---|---|---|
| Complete DPIA | [`DPIA-TEMPLATE.md`](DPIA-TEMPLATE.md) | DPO |
| Fill operator declaration in `tenant.corvin.yaml` | [`OPERATOR-OBLIGATIONS.md`](OPERATOR-OBLIGATIONS.md) | DPO / Legal |
| Publish privacy notice | [`PRIVACY-NOTICE-TEMPLATE.md`](PRIVACY-NOTICE-TEMPLATE.md) | Legal |
| Commission penetration test | [`PENTEST-SCOPE.md`](PENTEST-SCOPE.md) | Security team |
| Notify supervisory authority on serious incidents | [`INCIDENT-RESPONSE-PLAN.md`](INCIDENT-RESPONSE-PLAN.md) | DPO |
| BSI-Grundschutz / Notified Body engagement (if Annex III) | — | Operator (18–36 month lead time) |

---

## CLI Commands

```bash
# Check all 60 compliance rules across 4 frameworks (requires GPG key)
corvin-compliance-check --all-frameworks

# Check without signature verification (CI or dev)
corvin-compliance-check --all-frameworks --no-sig

# Check a single framework
corvin-compliance-check --framework eu-ai-act

# Generate Annex IV Technical Documentation
corvin-annex-iv generate

# Cross-reference table: NIST ↔ ISO 42001 ↔ EU AI Act ↔ GDPR
corvin-annex-iv cross-reference

# Generate ISO 42001 Statement of Applicability
corvin-annex-iv generate --framework iso-42001

# Generate NIST AI RMF Organisational Profile
corvin-annex-iv generate --framework nist-ai-rmf

# Bundle full certification package for a Notified Body / auditor
corvin-annex-iv export-package --output-dir /tmp/corvin-cert-$(date +%Y%m%d)

# Validate Annex IV document (no remaining [OPERATOR: FILL IN] placeholders)
corvin-annex-iv validate docs/ANNEX-IV.md

# Incident tracking
corvin-incident list
corvin-incident open --category chain_integrity --trigger-event audit.chain_gap_detected
corvin-incident notify-draft <incident_id> --authority BSI --operator-name "Acme GmbH"
corvin-incident scan --since 30d

# GDPR Art. 17 erasure
corvin-erasure <subject_id>

# System health + compliance checks
bridge.sh doctor
bridge.sh doctor --json

# Re-sign compliance manifest (after YAML edits)
./compliance/sign.sh sign
./compliance/sign.sh verify
```

---

## Certification Package

One command bundles everything an auditor, DPO, or Notified Body needs:

```bash
corvin-annex-iv export-package --output-dir /tmp/corvin-cert-$(date +%Y%m%d)
```

Package contents:

```
corvin-cert-YYYYMMDD/
├── README.md                       # Navigation guide for auditor
├── ANNEX-IV.md                     # EU AI Act Annex IV Technical Documentation
├── RISK-CLASSIFICATION.md          # Formal Limited Risk classification
├── OPERATOR-OBLIGATIONS.md         # Art. 28-30 pre-deployment checklist
├── INCIDENT-RESPONSE-PLAN.md       # Art. 73 detection + notification workflow
├── compliance/
│   ├── eu-ai-act.yaml              # Machine-readable EU AI Act rules (signed)
│   ├── gdpr.yaml                   # GDPR rules
│   ├── iso-42001.yaml              # ISO/IEC 42001 clauses
│   ├── nist-ai-rmf.yaml            # NIST AI RMF subcategories
│   └── manifest.sig                # GPG signature over all 4 YAMLs
├── audit/
│   ├── compliance-check-report.json  # corvin-compliance-check --json output
│   └── incidents-export.json         # corvin-incident export output
├── tests/
│   └── test-summary.txt              # run-all-tests.sh output
└── declarations/
    └── operator-declaration.yaml     # tenant spec.operator_declaration (PII-stripped)
```

The package is GPG-signed as a whole. Reproducible: same inputs → same non-timestamp content.

---

## Machine-Readable Manifests

The `compliance/` directory at the repo root contains the authoritative, GPG-signed
machine-readable rules that drive CI enforcement and `corvin-compliance-check`.

| File | Framework | Rules | Status |
|---|---|---|---|
| [`eu-ai-act.yaml`](../../compliance/eu-ai-act.yaml) | EU AI Act 2026 | 21 rules | Signed ✓ |
| [`gdpr.yaml`](../../compliance/gdpr.yaml) | GDPR | 11 rules | Signed ✓ |
| [`iso-42001.yaml`](../../compliance/iso-42001.yaml) | ISO/IEC 42001:2023 | 22 clauses | Signed ✓ |
| [`nist-ai-rmf.yaml`](../../compliance/nist-ai-rmf.yaml) | NIST AI RMF 1.0 | 22 subcategories | Signed ✓ |
| [`manifest.sig`](../../compliance/manifest.sig) | GPG signature | — | Valid ✓ |

**Current check result:** 60 rules across 4 frameworks — **60 passed, 0 warnings**.

CI gate: every PR touching compliance-relevant code runs `compliance-check.yml`
(Haiku-powered review). Critical findings block merge.

---

## Document Index

### Enterprise overview

| Document | Audience | Purpose |
|---|---|---|
| [`Corvin-Whitepaper-Structural-Compliance.pdf`](Corvin-Whitepaper-Structural-Compliance.pdf) | CTO · CISO · Compliance Officer | Executive whitepaper: how Corvin makes EU AI Act compliance architecturally impossible to bypass — suitable for enterprise evaluations and regulatory discussions |

### Governance artifacts (operator fills these in)

| Document | Audience | Purpose |
|---|---|---|
| [`RISK-CLASSIFICATION.md`](RISK-CLASSIFICATION.md) | DPO · Legal · Auditor | Formal Limited Risk classification statement |
| [`OPERATOR-OBLIGATIONS.md`](OPERATOR-OBLIGATIONS.md) | DPO · Legal · DevOps | Art. 28–30 pre-deployment checklist + permitted / prohibited uses |
| [`INCIDENT-RESPONSE-PLAN.md`](INCIDENT-RESPONSE-PLAN.md) | DPO · On-call | Art. 73 serious-incident detection, 15-day response, CLI workflow |
| [`DPIA-TEMPLATE.md`](DPIA-TEMPLATE.md) | DPO | GDPR Art. 35 Data Protection Impact Assessment (DE + EN) |
| [`DSB-CHECKLIST.md`](DSB-CHECKLIST.md) | DPO (DE) | Pre-go-live checklist in German |
| [`PRIVACY-NOTICE-TEMPLATE.md`](PRIVACY-NOTICE-TEMPLATE.md) | Legal | Datenschutzerklärung template (DE + EN) |
| [`PRIVACY-NOTICE-EXAMPLE.md`](PRIVACY-NOTICE-EXAMPLE.md) | Legal | Filled example privacy notice |
| [`PENTEST-SCOPE.md`](PENTEST-SCOPE.md) | Security firm | Penetration-test scope and constraints |
| [`COMPLIANCE-REPORT-GUIDE.md`](COMPLIANCE-REPORT-GUIDE.md) | Operator · DPO | How to generate + interpret monthly compliance reports |

### Technical deep-dives (article-by-article)

| Document | Covers |
|---|---|
| [`../eu-ai-act/README.md`](../eu-ai-act/README.md) | Overview + complete article-to-layer map |
| [`../eu-ai-act/DECLARATION-OF-CONFORMITY.md`](../eu-ai-act/DECLARATION-OF-CONFORMITY.md) | Voluntary Declaration of Conformity (obligations + status) |
| [`../eu-ai-act/architecture.md`](../eu-ai-act/architecture.md) | How all compliance layers interlock |
| [`../eu-ai-act/article-5.md`](../eu-ai-act/article-5.md) | Art. 5 prohibited practices — formal assessment for all CorvinOS features |
| [`../eu-ai-act/article-50.md`](../eu-ai-act/article-50.md) | Art. 50 §1 (bot-disclosure, re-issuance policy) and §4 (content marking) |
| [`../eu-ai-act/article-14.md`](../eu-ai-act/article-14.md) | Art. 14 human oversight — zones, data classification, egress |
| [`../eu-ai-act/article-73.md`](../eu-ai-act/article-73.md) | Art. 73 serious incident detection + 15-day response |
| [`../eu-ai-act/article-28-30.md`](../eu-ai-act/article-28-30.md) | Art. 28–30 operator obligations + declaration gate |
| [`../eu-ai-act/gpai-deployer-obligations.md`](../eu-ai-act/gpai-deployer-obligations.md) | Art. 26/53 GPAI cascade — Anthropic→CorvinOS→Operator chain |
| [`../eu-ai-act/agentic-safeguards.md`](../eu-ai-act/agentic-safeguards.md) | Agentic AI safeguards — orchestrator/worker, A2A, skill-promotion (Recital 80/97) |
| [`../eu-ai-act/gdpr.md`](../eu-ai-act/gdpr.md) | GDPR Art. 5–7, 17, 30, 32 — full coverage map |
| [`../eu-ai-act/audit-chain.md`](../eu-ai-act/audit-chain.md) | Hash-chain mechanics, tamper evidence, retention, encryption |

### Architecture decision records (compliance-relevant)

| Feature | Topic |
|---|---|
| Compliance Manifest | Living Compliance Manifest architecture |
| EU AI Act Gap Closure | EU AI Act certification gap closure |
| Framework Alignment | ISO 42001 + NIST AI RMF alignment |
| L40 Feature Set | Content Marking · L40 Incident Tracker · Operator Declaration · Annex IV |
| L34 | Data Classification + Flow Guard |
| L35 | Network Egress Lockdown |
| L37 | Audit-at-rest Encryption + Retention |
| L36 | GDPR Art. 17 Erasure Orchestrator |
| L38 | Remote Trigger + A2A Protocol |
| ADR-0153 | CorvinID: instance identity, audit attestation, GDPR pseudonymisation model |

---

## What is outside Corvin's scope

These items require human decision-makers and external parties. Corvin provides
the supporting tooling but cannot fulfil these obligations on the operator's behalf.

- **DPO appointment** — required by GDPR if processing triggers Art. 37 thresholds
- **DPO / Legal sign-off** on the DPIA, privacy notice, and Annex IV document
- **BSI-Grundschutz certification** — 18–36 month engagement; start no later than Q3 2026
  for a 2028 certification window
- **Penetration test by accredited lab** — scope in [`PENTEST-SCOPE.md`](PENTEST-SCOPE.md)
- **Supervisory authority registration** — required if tenant uses Corvin in an Annex III workflow
- **Notified Body engagement** — required for Annex III high-risk AI system conformity assessment
- **GPG key rotation plan** — when the maintainer signing key changes, `compliance/manifest.sig`
  must be regenerated and the new fingerprint committed to `tenant.corvin.yaml`
