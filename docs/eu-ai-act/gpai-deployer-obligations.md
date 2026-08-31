# GPAI Deployer Obligations — Art. 26 and Art. 53

> **Status:** Authoritative  
> **Reviewed:** 2026-06-12  
> **Regulation:** EU AI Act 2026 (Regulation (EU) 2024/1689)  
> **Applicable since:** Full Art. 26 deployer obligations apply **2 August 2026** (Article 113(2))  
> **CorvinOS position:** Deployer of GPAI model-based AI systems

---

## Regulatory context

The EU AI Act distinguishes three roles in the GPAI supply chain:

| Role | Definition | Examples |
|---|---|---|
| **GPAI model provider** | Places a general-purpose AI model on the market | Anthropic (Claude), OpenAI (GPT-4), Meta (Llama) |
| **Provider** (Art. 3(3)) | Develops an AI system using a GPAI model and places it on the market | A company shipping an AI product built on Claude |
| **Deployer** (Art. 3(4)) | Uses an AI system (or GPAI model) under the provider's authority for professional purposes | CorvinOS operator deploying CorvinOS with Claude |

**CorvinOS's position in this chain:**

1. **Anthropic** is the GPAI model provider for Claude
2. **CorvinOS maintainer** is the provider of the CorvinOS AI system framework  
3. **CorvinOS operators** (organisations deploying CorvinOS) are deployers under Art. 26

CorvinOS is **not itself a GPAI model provider** — it does not train or release a
foundation model. However, because CorvinOS integrates GPAI models (Claude, Hermes/Ollama,
OpenAI Codex, GitHub Copilot) as backends, its operators fall under the deployer
obligations cascade in Art. 26.

---

## Why the GPAI cascade matters

Art. 26 (deployer obligations) and Art. 53 (GPAI model provider obligations) interact
via a liability cascade:

```
Anthropic (GPAI provider, Art. 53)
    ↓  Usage Policy + Acceptable Use Terms
CorvinOS maintainer (provider, Art. 25)
    ↓  engine-policy allowlist, data-classification matrix, compliance documentation
CorvinOS operator (deployer, Art. 26)
    ↓  operator_declaration, DPIA, deployment profile, incident reporting
End users
```

At each link in the chain, the downstream party inherits obligations from the upstream
party's policy, and is responsible for ensuring those policies are honoured within their
deployment.

---

## Art. 26 obligations — what operators must do

Art. 26 applies in full from **2 August 2026**. The obligations below are binding for
any operator deploying CorvinOS in a professional context.

### Art. 26(1) — Follow instructions for use

> Deployers shall take appropriate technical and organisational measures to ensure they
> use AI systems in accordance with the instructions for use accompanying them.

**CorvinOS implementation:**

- The `OPERATOR-OBLIGATIONS.md` document is the primary instructions-for-use document.
  It is required reading before any production deployment.
- `bridge.sh doctor` enforces a subset of these requirements at boot time. A deployment
  that fails `doctor` is not in compliance with the instructions for use.
- The `tenant.corvin.yaml::spec.operator_declaration` field records that the operator has
  read and accepted the obligations. This is a structural field, not an advisory checklist.

**Required operator action:** Set `spec.operator_declaration.accepted: true` with a valid
`dpia_completed: true` and `dpia_date` before any production deployment.

### Art. 26(2) — Human oversight

> Deployers shall assign human oversight to natural persons who have the necessary
> competence, authority, and resources, and shall implement adequate human oversight measures.

**CorvinOS implementation:**

- The consent gate (L16 Phase 4) ensures no user is processed without active consent.
- The `/btw` injection mechanism (L13) allows designated supervisors to intervene in live
  agentic pipelines.
- The L39 incident dashboard surfaces CRITICAL events to designated operators in real time.
- The roles system (L18) supports designating `admin` or `owner` accounts with elevated
  oversight capability (`audit_self`, `roles_read`, `delegate_admin` capabilities).

**Required operator action:** Designate at least one natural person as the responsible
operator (must have `admin` or `owner` role in the CorvinOS deployment) who monitors
the incident dashboard and can revoke consent or access if required.

### Art. 26(3) — Monitor and report serious incidents (Art. 73)

> Deployers shall monitor the operation of the AI system and report serious incidents
> to the provider, relevant market surveillance authority, and affected users.

**CorvinOS implementation:**

- IncidentAutoDetector (L39) automatically opens incident records for CRITICAL audit events.
- `corvin-incident export` produces the Art. 73 incident report in the required format.
- The 15-day reporting window is tracked in the incident record (`created_at` → `reported_at` delta).

**Required operator action:**
1. Install `corvin-incident-scan.timer` for daily automated scan.
2. Configure `spec.incident_contact` in `tenant.corvin.yaml` with the responsible person's
   contact details (internal reference only; not stored in the audit chain).
3. Verify the incident dashboard is accessible via the web console before going live.

### Art. 26(4) — Upstream GPAI provider instructions

> Deployers that are subject to a GPAI model provider's usage policy must comply with
> that policy and forward its requirements downstream.

**Relevant GPAI model policies:**

| GPAI model | Provider | Usage policy reference |
|---|---|---|
| Claude | Anthropic | Anthropic Usage Policy (current version) |
| Hermes / Llama-based | Meta / HuggingFace | Llama Community License |
| Codex / GPT-4 | OpenAI | OpenAI Usage Policies |
| GitHub Copilot | GitHub / Microsoft | GitHub Copilot Terms |

CorvinOS's engine-policy allowlist (`allowed_engines` in `tenant.corvin.yaml`) must be
configured in compliance with the applicable GPAI model provider policies. Specifically:

- Do not list an engine as `allowed` if the operator's use case violates the provider's
  acceptable use terms (e.g., using Claude for high-risk medical decisions without
  Anthropic's commercial agreement).
- The `forbidden_engines` list can be used to explicitly prohibit engines that are not
  appropriate for a given deployment context.

**Required operator action:** Review the usage policy for each engine listed in
`allowed_engines` and confirm that the deployment context is permitted. Document this
in `spec.operator_declaration.engine_policy_review`.

### Art. 26(5) — Record-keeping

> Deployers shall keep logs and records of the operation of the AI system to the
> extent technically feasible and appropriate for the AI system's intended purpose.

**CorvinOS implementation:**

- L16 hash-chained audit log provides tamper-evident records of all gate events.
- L37 audit-at-rest encryption and retention (default: 7 years).
- `corvin-compliance-reports gdpr-30` generates an Art. 30 RoPA from the audit chain.
- `voice-audit verify` confirms chain integrity.

**Required operator action:** Ensure `spec.audit.retention_years` is set to at least
7 (default) and that `corvin-session-timeout.timer` is not configured to purge audit
chains (it purges only session state, not audit logs — but operators should verify).

---

## Art. 25 obligations — CorvinOS maintainer

Where the CorvinOS maintainer is acting as a **provider** (makes CorvinOS available
to operators as an AI system), the following Art. 25 obligations apply:

### Art. 25(1)(a) — Provide instructions for use

`OPERATOR-OBLIGATIONS.md`, `docs/compliance/`, `docs/eu-ai-act/`, and the
`bridge.sh doctor` tooling constitute the instructions for use.

### Art. 25(1)(b) — Inform deployers of serious incidents

The L39 incident notification system generates draft notifications. For incidents
affecting all deployments (e.g., a discovered vulnerability in CorvinOS itself),
the maintainer publishes security advisories via the standard GitHub Security Advisory
mechanism.

### Art. 25(1)(c) — Cooperate with market surveillance authorities

The `corvin-annex-iv generate` CLI produces the Annex IV technical documentation
required by market surveillance authorities. The generated file is the authoritative
technical file for a specific CorvinOS deployment.

---

## Art. 53 obligations — what Anthropic and other GPAI providers must do

Art. 53 obligations fall on the GPAI model providers (Anthropic, OpenAI, Meta, etc.),
not on CorvinOS. However, CorvinOS's engine-allowlist mechanism enforces that operators
only use GPAI models that:

1. Have publicly available acceptable use policies (required by Art. 53(1)(b))
2. Have documented their training data sources to the extent required by Art. 53(1)(c)
3. Have copyright compliance processes per Art. 53(1)(d)

If a GPAI model provider fails to meet Art. 53 obligations (e.g., publishes no usage
policy), CorvinOS operators **should not** list that engine in `allowed_engines`.
The engine-policy allowlist is the operator's mechanism for enforcing this.

---

## GPAI-cascade compliance summary

| Obligation | Responsible party | CorvinOS mechanism | Status |
|---|---|---|---|
| Art. 53 — GPAI provider obligations | Anthropic / OpenAI / Meta / GitHub | Outside CorvinOS scope | ✅ Anthropic/OpenAI have published policies |
| Art. 25 — Provider obligations (CorvinOS maintainer) | CorvinOS maintainer | Instructions for use, doctor tooling, Annex IV CLI | ✅ Implemented |
| Art. 26(1) — Follow instructions for use | CorvinOS operator | `operator_declaration` gate, `bridge.sh doctor` | ✅ Gate enforced |
| Art. 26(2) — Human oversight | CorvinOS operator | Consent gate, roles system, `/btw` injection, incident dashboard | ✅ Implemented |
| Art. 26(3) — Monitor + report incidents | CorvinOS operator | L39 IncidentAutoDetector, 15-day tracker, CLI | ✅ Implemented |
| Art. 26(4) — Upstream policy compliance | CorvinOS operator | Engine-policy allowlist, `forbidden_engines` | ⚠️ Operator must review per engine |
| Art. 26(5) — Record-keeping | CorvinOS operator | L16 chain, L37 encryption, 7-year retention | ✅ Implemented |

**⚠️ — operator action required:** Art. 26(4) cannot be pre-satisfied by CorvinOS
because the operator must review each GPAI provider's current usage policy against their
specific use case. CorvinOS provides the mechanism (allowlist); the compliance judgment
is the operator's.

---

## Timeline

| Date | Event |
|---|---|
| 1 August 2024 | EU AI Act enters into force |
| 2 February 2025 | Art. 5 (prohibited practices) applies |
| 2 August 2025 | Art. 50, 53 (transparency, GPAI provider obligations) apply |
| **2 August 2026** | **Full application including Art. 26 deployer obligations** |
| Today (12 June 2026) | 51 days until full Art. 26 applies |

Operators deploying CorvinOS in production before 2 August 2026 should treat Art. 26
as already applicable — the obligations reflect good practice regardless of the formal
application date.
