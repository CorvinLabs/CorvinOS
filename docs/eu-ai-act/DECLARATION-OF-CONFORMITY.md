# Declaration of Conformity — CorvinOS

> **Document type:** Voluntary Declaration of Conformity  
> **Format:** Modelled on the voluntary form described in EU AI Act Art. 47, adapted for  
> Limited-Risk AI systems (Art. 47 formally covers high-risk systems; this is issued  
> voluntarily as a transparency measure and operator reference document)  
> **Issued:** 2026-06-12  
> **Issuer:** CorvinOS maintainer (git user: shumway)

---

## Important notice regarding Art. 47

Article 47 of the EU AI Act formally requires a Declaration of Conformity from
**providers of high-risk AI systems** (Annex III). CorvinOS is classified as a
**Limited-Risk AI system** (Art. 50) and is therefore not legally required to issue
a Declaration of Conformity under Art. 47.

This document is issued voluntarily. Its purpose is:

1. To document in a single place the applicable regulatory obligations and how
   they are met
2. To give CorvinOS operators a clear reference point for their own compliance
   assessments
3. To demonstrate transparent regulatory positioning to users, regulators,
   and auditors

This document does not constitute a legally binding certification and does not
replace a conformity assessment by a notified body.

---

## 1. Product identification

| Field | Value |
|---|---|
| **Product name** | CorvinOS — Conversational AI Agent Framework |
| **Description** | A multi-bridge, multi-persona, multi-engine conversational AI assistant platform with GDPR-compliant audit chain, consent management, federated agent-to-agent communication, and an EU AI Act compliance layer |
| **Version** | v0.14.0 (as of 2026-06-12; see git tag) |
| **Primary use case** | Conversational AI assistant for Discord, Telegram, WhatsApp, Matrix, and web console channels |
| **Deployment contexts** | Individual operators, enterprise teams, federated agent networks |

---

## 2. Responsible party

| Field | Value |
|---|---|
| **Maintainer** | shumway (git identity: `shumway`) |
| **Repository** | `github.com/CorvinLabs/CorvinOS` |
| **Contact** | See `CONTRIBUTING.md` for maintainer contact |
| **Legal entity** | Individual maintainer (open-source project) |

---

## 3. Risk classification

**Classification: Limited-Risk AI System** under the EU AI Act (Regulation (EU) 2024/1689).

| Criterion | Status | Basis |
|---|---|---|
| Prohibited practice (Art. 5) | Not applicable | See `docs/eu-ai-act/article-5.md` |
| High-risk system (Annex III) | No | Not used in: biometric ID, critical infrastructure, education, employment, essential services, law enforcement, migration, justice |
| GPAI model with systemic risk | No | CorvinOS does not train or deploy a foundation model |
| Art. 50 transparency obligation | **Yes** | Chatbot interacting with natural persons |
| Applicable regulatory tier | **Limited Risk (Art. 50)** | `docs/compliance/RISK-CLASSIFICATION.md` |

Formal risk classification with reclassification conditions is in
`docs/compliance/RISK-CLASSIFICATION.md`.

---

## 4. Applicable obligations and conformity status

### 4a. EU AI Act

| Article | Obligation | Status | Documentation |
|---|---|---|---|
| Art. 5 — Prohibited practices | No prohibited feature in CorvinOS | ✅ Compliant | `docs/eu-ai-act/article-5.md` |
| Art. 50(1) — Chatbot disclosure | Bot-disclosure card, one-time per user, structurally enforced | ✅ Compliant | `docs/eu-ai-act/article-50.md` |
| Art. 50(4) — Content marking | Machine-readable `provenance` block on every final message | ✅ Compliant | `docs/eu-ai-act/article-50.md` |
| Art. 14 — Human oversight | Consent gate, engine allowlist, data classification, egress lockdown | ✅ Compliant | `docs/eu-ai-act/article-14.md` |
| Art. 25 — Provider obligations (CorvinOS) | Instructions for use, incident notification pathway, Annex IV CLI | ✅ Compliant | `docs/eu-ai-act/gpai-deployer-obligations.md` |
| Art. 26 — Deployer obligations | Enforced via `operator_declaration` gate; full application 2 August 2026 | ✅ Infrastructure ready | `docs/eu-ai-act/gpai-deployer-obligations.md` |
| Art. 73 — Incident reporting | L39 IncidentAutoDetector, 15-day tracker, report CLI | ✅ Compliant | `docs/eu-ai-act/article-73.md` |
| Recital 80/97 — Agentic AI | Safeguards documented and enforced for all multi-agent features | ✅ Compliant | `docs/eu-ai-act/agentic-safeguards.md` |

### 4b. GDPR

| Article | Obligation | Status | Documentation |
|---|---|---|---|
| Art. 5 — Data minimisation | Metadata-only in audit chain; no transcript/prompt content | ✅ Compliant | `docs/eu-ai-act/gdpr.md` |
| Art. 6/7 — Lawful basis / consent | Per-user consent gate, deny-by-default, TTL-capped, revocable | ✅ Compliant | `docs/eu-ai-act/gdpr.md` |
| Art. 13/14 — Information obligations | Disclosure card (AI nature, operator identity, commands) | ✅ Compliant | `docs/eu-ai-act/article-50.md` |
| Art. 17 — Right to erasure | Cross-layer erasure orchestrator (`corvin-erasure`) | ✅ Compliant | `docs/compliance/README.md` |
| Art. 30 — Records of processing | Hash-chained audit log + `corvin-compliance-reports gdpr-30` | ✅ Compliant | `docs/eu-ai-act/gdpr.md` |
| Art. 32 — Security of processing | Vault capability split, path-gate, hash chain, 0600 permissions | ✅ Compliant | `docs/eu-ai-act/gdpr.md` |

### 4c. Known limitations and residual risks

1. **GPAI provider dependency:** Claude, Hermes, OpenAI, Copilot are third-party models. CorvinOS cannot inspect model internals. Art. 26(4) compliance (upstream policy check per engine) is an operator responsibility documented in `gpai-deployer-obligations.md`.

2. **Workplace voice deployment:** If CorvinOS is deployed in a workplace with voice input, the operator must independently assess whether the deployment constitutes emotion recognition under Art. 5(1)(f). CorvinOS does not perform emotion classification; the assessment obligation is the operator's.

3. **Art. 47 technical file completeness:** The `corvin-annex-iv generate` CLI produces a technical documentation file. This is not reviewed or validated by a notified body. For high-risk deployments (Annex III reclassification triggers met), a notified body assessment would be required.

---

## 5. Technical documentation

The CorvinOS technical file can be generated on any deployment:

```bash
corvin-annex-iv generate --output annex-iv.md
corvin-annex-iv export-package           # zip with all compliance artefacts
```

The generated file includes:
- System description and intended purpose
- Engine configuration and data-classification matrix
- Audit chain integrity status
- Active operator declaration
- Incident records (current reporting period)
- Compliance manifest version

---

## 6. Structural enforcement evidence

The compliance mechanisms in CorvinOS are not policy documents — they are code gates
that run at every message boundary. This declaration is backed by:

```bash
# Run every gate check, every CRITICAL invariant
bridge.sh doctor --json

# Verify audit chain integrity (exit-1 = compliance violation)
voice-audit verify

# Run the full test suite (434 tests)
bash operator/bridges/run-all-tests.sh

# Generate current Annex IV technical file
corvin-annex-iv generate --output annex-iv.md
```

All CRITICAL checks must pass before any production deployment. A deployment with a
failing CRITICAL check is not in conformity with this declaration.

---

## 7. Validity and change management

This declaration is valid for the CorvinOS version identified in section 1.

This declaration must be updated when:
- A new feature is added that materially changes the risk profile
- A new engine is added to the default `allowed_engines` list
- The risk classification changes (Annex III reclassification trigger met)
- The EU AI Act is amended by delegated acts that affect Limited-Risk systems
- The GPAI model provider policies for any listed engine change materially

**Next scheduled review:** 2026-09-12 (prior to any v0.15.0 release)

---

## 8. Signatory

| Field | Value |
|---|---|
| Name | shumway |
| Role | CorvinOS maintainer |
| Date | 2026-06-12 |
| Repository commit | See `git log --format="%H" -1` at time of deployment |

*This declaration was prepared with reference to Regulation (EU) 2024/1689 (EU AI Act)
and Regulation (EU) 2016/679 (GDPR). It reflects the compliance status of CorvinOS as
of the review date. It is not a legal opinion and does not constitute legal advice.*
