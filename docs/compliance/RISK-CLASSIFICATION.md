# Corvin — Risk Classification Statement

**Status:** Authoritative  
**Date:** 2026-06-19  
**Basis:** EU AI Act 2026  
**Review cycle:** With every MAJOR EU AI Act Annex update

---

## Bottom line — is CorvinOS built for high-risk use?

**No.** CorvinOS is **not** designed, intended, or marketed for high-risk
(Annex III) deployment. Its standalone classification is **Limited-Risk**
(transparency obligations, Art. 50). It ships **no** conformity assessment, CE
marking, or Annex III intended-purpose declaration, and it carries a structural
**acceptable-use gate (L44, ADR-0143)** that *blocks* the prohibited (Art. 5)
and out-of-scope-military use classes outright — so it actively steers away from
the prohibited tier, not toward the high-risk tier.

A high-risk obligation can only arise **conditionally**, if an *operator*
embeds CorvinOS into an Annex III workflow — in which case the obligations fall
on that operator (see "Conditional Reclassification"). CorvinOS supplies the
audit / consent / data-flow / acceptable-use machinery those operators need, but
is not itself a high-risk product.

---

## Standalone Classification

**Category:** Limited-Risk AI System  
**Regulation:** EU AI Act 2026 (OJ L 2024/1689), Art. 3(1), Annex I  

Corvin is an orchestration layer over external LLMs. It does not:

- Train, fine-tune, or serve its own model weights.
- Make automated consequential decisions with legal or similarly significant
  effect without a human in the loop.
- Perform biometric categorisation (Art. 3(36)).
- Manage critical infrastructure (Annex III §1).
- Deliver education or vocational training decisions (Annex III §3).
- Serve as a component of high-risk systems by default.

It is NOT a General-Purpose AI Model (Art. 3(63)) — no weights are published.

It additionally **refuses, by platform design**, the use classes that would push
a deployment toward the prohibited tier or outside the Act's civilian scope:
military / weapons use, unauthorized offensive-cyber operations, and
disinformation / manipulation — enforced fail-closed by the **L44 Acceptable-Use
gate** (ADR-0143), not by policy text alone.

### Structural basis

| Property | Evidence |
|---|---|
| AI-nature transparency | L19 disclosure card fires once per (channel, chat, uid); structurally enforced, not policy-only |
| Consent | L16 deny-by-default per-uid consent; re-validated each message |
| Human oversight | L18 roles, L21 proposals, L14 LDD-Toggle, engine-policy allowlist |
| Audit | L16 hash-chained tamper-evident audit log; daily verify timer |
| Data protection | L34-L37: classification matrix, egress lockdown, erasure, audit-at-rest |
| Acceptable-use enforcement | L44 (ADR-0143): fail-closed gate that blocks military / offensive-cyber / disinformation use before any engine spawn; repo-defined signed policy, no disable switch |
| Incident tracking | L39: CRITICAL event auto-detection + Art. 73 notification workflow |

---

## Conditional Reclassification

If a tenant operator deploys Corvin as a **component** in an Annex III
workflow, the operator bears the Art. 25-28 cascade obligations:

| Annex III Category | Example | Operator obligation |
|---|---|---|
| §3 Employment decisions | Automated CV screening | Art. 25-28 + Notified Body engagement |
| §5 Credit/benefit scoring | Automated credit-eligibility | Art. 25-28 + DPIA |
| §7 Law enforcement | Risk assessment tools | Art. 25-28 + fundamental-rights impact assessment |

Corvin provides the structural audit, consent, data-flow, and acceptable-use
machinery required by those obligations (L16, L34, L35, L36, L37, L44) but does
NOT substitute for the operator's own AI system registration and Notified Body
engagement for that deployment. Note that the L44 acceptable-use gate is a
*floor*: an operator may add stricter rules per tenant but cannot weaken or
disable the shipped military / offensive-cyber / disinformation blocks.

The operator's **DPIA** (docs/compliance/DPIA-TEMPLATE.md) and **operator
declaration** (`tenant.corvin.yaml::spec.operator_declaration`) must reflect
the actual deployment context, including any Annex III applicability.

---

## Review History

| Date | Change | Reviewer |
|---|---|---|
| 2026-05-26 | Initial classification | Corvin maintainer |
| 2026-06-19 | Re-affirmed Limited-Risk (NOT high-risk) after adding the L44 Acceptable-Use gate (ADR-0143); added acceptable-use row to the structural-basis matrix + "Bottom line" section; L44 blocks the prohibited/military tiers fail-closed | Corvin maintainer via Claude Code |
