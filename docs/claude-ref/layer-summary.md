# Layer Stack Overview (36+ Security + Compliance Layers)

Each layer enforces one security/compliance mechanism. Layers are independent; failures in one
layer don't compromise others (defense-in-depth).

## Core Layers (L4–L44)

| Layer | Subsystem | Purpose | Status |
|---|---|---|---|
| **L4** | Cowork (multi-persona hub) | Route each chat to a different persona | [layer-plugins.md](layer-plugins.md) |
| **L5** | Auto-routing | Keyword-based persona selection (heuristic or API) | [layer-plugins.md](layer-plugins.md) |
| **L6** | Forge | Runtime tool generation, schema-bound, MCP server | [layer-plugins.md](layer-plugins.md) |
| **L7** | SkillForge | Runtime skill generation, prompt-injected | [layer-plugins.md](layer-plugins.md) |
| **L8** | Session lifecycle | `/new /clear /reset`, audit-first lifecycle | [layer-model.md § Layer 8](../layer-model.md#layer-8--session-bound-lifecycle-new-clear-reset--7-day-timeout-sweep) |
| **L10** | Path-Gate | FS-write protection, fail-closed, Bash detection | [layer-10-path-gate.md](layer-10-path-gate.md) |
| **L11** | Dialectic decision-points | 6 integration sites (skill_promotion, auto_routing, etc.) | [layer-voice-ldd.md § L11](layer-voice-ldd.md#layer-11--native-dialectic-decision-points-ldd-integrated) |
| **L12** | Voice listener-profile | TTS tuning for the listener (`voice_audience_*` fields) | [layer-voice-ldd.md § L12](layer-voice-ldd.md#layer-12--voice-listener-profile-tts-audience-tuning) |
| **L13** | /btw mid-stream injection | In-flight note pushing via side-channel envelope | [layer-voice-ldd.md § L13](layer-voice-ldd.md#layer-13--btw-text-mid-stream-injection) |
| **L14** | LDD-Toggle-System | Enable/disable all 12 LDD layers per persona/scope | [layer-voice-ldd.md § L14](layer-voice-ldd.md#layer-14--ldd-toggle-system-per-layer-enabledisable) |
| **L16** | Security hardening | Consent gate, audit framing, TOCTOU re-validation | [layer-security.md](layer-security.md) |
| **L18** | Roles (owner/admin/member/observer) | 4-tier hierarchy, 7-day TTL | [layer-users.md](layer-users.md) |
| **L19** | Disclosure (one-time AI-nature card) | EU AI Act Art. 50 compliance | [layer-users.md § L19](layer-users.md#layer-19--bot-disclosure-card--join-self-service) |
| **L20** | Quota (per-bundle rate-limit) | owner=unlimited, admin=500/day, member=100 | [layer-users.md § L20](layer-users.md#layer-20--quota--audit-view-delegated-budget-visibility) |
| **L21** | Proposals (stack of instructions) | `/propose` → `/go [steering]`, atomic consume | [layer-users.md § L21](layer-users.md#layer-21--curated-proposal-stack-multi-user-input-owner-go) |
| **L22** | WorkerEngine protocol | 5 engines: ClaudeCode, Hermes, Copilot, Codex, OpenCode | [layer-engines.md § L22](layer-engines.md#layer-22--workerengine-protocol-awp-integration-phase-1--2) |
| **L23** | Speech-to-Text | Metadata-only audit (never transcript text) | [layer-23-stt.md](layer-23-stt.md) |
| **L24** | Large-Data Snapshot | `data_register`/`data_snapshot`/`data_unregister` MCP | [layer-data.md § L24](layer-data.md#layer-24--large-data-snapshot-layer-adr-0012-pii-aware-data-locality) |
| **L25** | Compute Worker | Out-of-LLM-loop optimization driver | [layer-data.md § L25](layer-data.md#layer-25--compute-worker-opt-in-iterative-big-data-adr-0013) |
| **L28** | Conversation Recall + User Modeling | FTS5 SQLite recall.db, distilled user model | [layer-users.md § L28](layer-users.md#layer-28--conversation-recall--user-modeling-adr-0016) |
| **L29–30** | Delegation + Engine-Agnostic Forge | Worker processes via MCP, hardening + confinement | [layer-engines.md § L29](layer-engines.md#layer-29--delegation-claude-os--swappable-worker-engines) |
| **L32** | Strict Anonymisation | k-Anonymity (k=5), Laplace noise on rowcount | [layer-data.md § L32](layer-data.md#layer-32--strict-anonymisation-snapshot-mode-adr-0023) |
| **L33** | Session Artifact Memory | Non-text artifacts (PDF, image, CSV), FTS5 recall | [layer-33-artifacts.md](layer-33-artifacts.md) |
| **L34** | Data Classification + Flow Guard | 4-stage (PUBLIC/INTERNAL/CONFIDENTIAL/SECRET) × engine matrix | [layer-34-data-classification.md](layer-34-data-classification.md) |
| **L35** | Network Egress Lockdown | `allowed_hosts`/`forbidden_hosts` per tenant | [layer-35-egress-lockdown.md](layer-35-egress-lockdown.md) |
| **L36** | GDPR Art. 17 Erasure Orchestrator | Right-to-deletion across all layers | [layer-36-erasure.md](layer-36-erasure.md) |
| **L37** | Audit-at-rest Encryption + Retention | Rotate hash chain, seal via age/gpg, RFC 3161 TSA | [layer-37-audit-at-rest.md](layer-37-audit-at-rest.md) |
| **L38** | A2A TaskEnvelope Protocol | RemoteTriggerReceiver, Protocol v6, instance attestation | [layer-38-a2a-network.md](layer-38-a2a-network.md) |
| **L44** | House-Rules (acceptable-use gate) | No military, offensive-cyber, disinformation | [layer-44-house-rules.md](layer-44-house-rules.md) |

## Meta-Layers

| Layer | Subsystem | Purpose | Status |
|---|---|---|---|
| **LIP** | Layer Integrity Protocol (ADR-0141) | Cryptographic layer presence verification | [layer-integrity-protocol.md](layer-integrity-protocol.md) |
| **CLS** | Custom Layer System (ADR-0156) | Vendor layers, Tier-A/B/C licensing | [layer-cls.md](layer-cls.md) |
| **LDD** | Loss-Driven Development | 12 skill layers for development discipline | [ldd-mandatory.md](ldd-mandatory.md) |

## How to Read This Stack

1. **For a specific layer:** Read the corresponding doc (e.g., `layer-10-path-gate.md`)
2. **For compliance/audit questions:** See [compliance-baseline.md](compliance-baseline.md)
3. **For security architecture:** See [layer-security.md](layer-security.md)
4. **For multi-tenant routing:** See CLAUDE.md ADR-0007 section and [portable-tenants.md](portable-tenants.md)
5. **For delegation and workers:** See [layer-engines.md](layer-engines.md) (L22, L29–30) and [delegation-routing.md](delegation-routing.md)
6. **For data protection (L34+L35+L36):** See `layer-34-data-classification.md`, `layer-35-egress-lockdown.md`, `layer-36-erasure.md`
7. **For audit and compliance:** See [layer-security.md](layer-security.md) and [layer-37-audit-at-rest.md](layer-37-audit-at-rest.md)

## Defense-in-Depth

Layers are independent. A compromise in one layer does NOT compromise others:

- **Compromise L6 (Forge)** → L10 (path-gate) still denies FS writes to audit/license/memory
- **Compromise L22 (WorkerEngine)** → L34 (data classification) still gates data flow
- **Compromise L38 (A2A)** → LIP (Layer Integrity) still validates crypto signatures

## Related

- [CLAUDE.md](../../CLAUDE.md) — Main conventions document (compact, points to ref files)
- [compliance-baseline.md](compliance-baseline.md) — EU AI Act + GDPR constraints
- [CONTRIBUTING.md § License + CLA](../../CONTRIBUTING.md#license--cla) — Apache-2.0 + CLA §3 requirements
- [ldd-mandatory.md](ldd-mandatory.md) — Loss-driven development mandate
