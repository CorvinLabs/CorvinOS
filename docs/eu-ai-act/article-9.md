# EU AI Act Art. 9 — Risk Management System

> **Scope:** Applies to providers of high-risk AI systems (Annex III) and providers
> of general-purpose AI (GPAI) models with systemic risk.  CorvinOS's classification
> as a **Limited-Risk AI System** means Art. 9 obligations apply in a reduced form;
> this document records the risk management measures in place regardless.

## 1. System Classification

| Criterion | Status |
|---|---|
| High-risk (Annex III) | **No** — CorvinOS is a conversational AI agent framework, not a system used in safety-critical applications listed in Annex III (biometric ID, critical infrastructure, education, employment, essential services, law enforcement, migration, justice). |
| GPAI with systemic risk | **No** — CorvinOS does not train or deploy foundation models; it orchestrates third-party engines (Claude, Hermes/Ollama, Copilot). |
| Classification | **Limited-Risk AI System** (transparency obligations only: Art. 50, 52) |

Full risk classification rationale: [`docs/compliance/RISK-CLASSIFICATION.md`](../compliance/RISK-CLASSIFICATION.md).

## 2. Risk Management Measures

Even as a Limited-Risk system, CorvinOS implements structural risk controls that exceed the minimum Art. 50 requirement:

### 2a. Identification and Analysis of Known Risks

| Risk | Mechanism | Layer |
|---|---|---|
| Prompt injection (cross-tenant) | NFKC normalisation + control-char strip + framing block | L16, L38 |
| Credential / secret leak | Vault capability split: names only to LLM, values via bwrap env | L16 v3 |
| Unauthorised file-system writes | Path-gate hook, fail-closed on forge/skill-forge/audit/policy | L10 |
| Unauthorised data egress | Egress lockdown, allow-list + default-deny per tenant | L35 |
| Data residency violation | Compliance-zone routing + engine allowlist | L34 / L35 |
| Unconsented data processing | Per-user consent gate, deny-by-default, TTL-capped | L16 Phase 4 |
| AI identity deception | Bot-disclosure card on first contact, structurally locked | L19 |
| Audit tampering | SHA-256 hash chain + flock cross-process locking | L16 |
| Data breach (audit at rest) | Optional age/gpg encryption + RFC 3161 TSA timestamping | L37 |
| Unverified agent-to-agent instructions | HMAC-SHA256 signed envelopes, replay-proof nonce, instance pinning | L38 |
| AI deliberation bypass (human override) | `human_oversight.override` audit event for operator-level disables | L11 |
| Prohibited / out-of-scope use (military, offensive-cyber, disinformation) | Acceptable-use gate, fail-closed, repo-defined signed policy, runs before every engine spawn | L44 (ADR-0143) |

### 2b. Residual Risk Acceptance

- **Engine trust hierarchy:** third-party engines (Hermes/Ollama, Copilot) run under trust-tier `low` with pre-spawn L34/L35 gate checks. Residual risk: model output quality; mitigated by output cap (64 KB) and faithfulness judge.
- **GPAI provider dependency:** Claude, Codex, OpenAI are upstream providers; CorvinOS cannot control their model internals. Residual risk: managed via `allowed_engines` + zone gate.

### 2c. Testing and Evaluation

| Activity | Mechanism |
|---|---|
| Boot self-test | `bridge.sh doctor` / Docker HEALTHCHECK — 7 CRITICAL checks |
| Audit-chain integrity | Daily `voice-audit verify` timer (03:30, exit-1 silenced = CRITICAL) |
| Path-gate self-test | `path_gate.self_test_failed` CRITICAL on adapter start |
| L16 A2A HMAC self-test | HMAC-key file permission check (CRITICAL on world-readable) |
| E2E test suite | 434 pytest tests + Playwright (Chromium + Firefox) |

### 2d. Incident Management

EU AI Act Art. 73 (serious incident reporting) is implemented via the Incident Tracker plugin:
- `incident.opened` (CRITICAL), `incident.status_changed` (INFO), `incident.closed` (INFO)
- Full reference: [`docs/eu-ai-act/article-73.md`](article-73.md)

## 3. Logging and Traceability (Art. 12 cross-reference)

Art. 9(7) requires that logging capabilities enable post-hoc verification of system functioning. CorvinOS satisfies this via:

- **L16 hash chain:** every bridge event, forge operation, and OS turn is appended to a tamper-evident SHA-256 chain (`audit.jsonl`).
- **Art. 30 RoPA:** `corvin-compliance-reports gdpr-30` reconstructs the record of processing activities from the chain, including engine usage, data handles, voice metadata, memory activity, worker delegation (L29), and A2A data flows (L38).
- **Art. 50 evidence:** `corvin-compliance-reports ai-act-50` produces the active-disclosure attestation.

## 4. Ongoing Monitoring

| Frequency | Activity |
|---|---|
| Per boot | Self-test + path-gate self-test |
| Daily 03:30 | Audit-chain verify + session TTL sweep |
| Per PR | 434-test CI suite (operator/bridges/run-all-tests.sh) |
| Quarterly | Compliance drift scan (manual, DSB-CHECKLIST.md) |

## 5. Conditional Reclassification

If a deployment of CorvinOS is integrated into a workflow that meets Annex III criteria (e.g., used in employment screening, credit scoring, biometric authentication), the operator **must** perform a full Art. 9 risk management cycle under the high-risk regime. The conditions are documented in `docs/compliance/RISK-CLASSIFICATION.md § Reclassification Triggers`.
