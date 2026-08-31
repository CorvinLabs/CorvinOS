# Security Policy

## Supported Versions

| Version | Security fixes |
|---------|---------------|
| v1.0+ (current) | Yes |
| < v1.0 | No — pre-release tags are unsupported |

Only the latest tagged release in the `v1.*` line receives security patches.
Pre-release builds (commits past the latest tag, `-rc` and `-beta` tags) are
unsupported.

---

## Reporting a Vulnerability

**Please do not open a public GitHub issue for security vulnerabilities.**

Email: **security@corvin-labs.com**

A PGP public key is available on request — reply to the auto-acknowledgement
and we will provide the fingerprint and key material.

### What to include

- **Component affected** — e.g. "Forge sandbox (bwrap)", "path-gate hook",
  "audit chain", "API gateway", specific bridge daemon.
- **Steps to reproduce** — the minimum sequence required to observe the issue.
  Include configuration snippets (redact secrets), log excerpts, and OS/runtime
  version.
- **Impact assessment** — your view of confidentiality, integrity, and
  availability impact.
- **CVE coordination** — if you need a CVE identifier we can request one on
  your behalf; please mention this in your report.

### Response timeline

| Milestone | Target |
|-----------|--------|
| Acknowledgement | ≤ 72 hours |
| Triage + severity classification | ≤ 14 days |
| Fix target (critical/high) | ≤ 30 days from triage |
| Fix target (medium/low) | Next scheduled release |
| Public advisory | After fix is tagged (see Disclosure Policy) |

---

## Scope

### In scope

- Bridge daemons: Discord, Telegram, WhatsApp, Slack, Email, Microsoft Teams, Signal
- Python adapter (`operator/bridges/shared/adapter.py` and supporting modules)
- Forge sandbox — bwrap confinement, path-gate hook, policy enforcement
- SkillForge linter — prompt-injection detection, secret-leakage checks
- Hash-chained audit log and the `voice-audit verify` toolchain
- Web console (`corvin-console`) and API gateway
- Consent gate, bot-disclosure card, role/quota enforcement
- Data classification (L34) and egress lockdown (L35) modules
- A2A remote-trigger receiver — HMAC verification, replay protection, prompt-injection framing
- Erasure orchestrator (L36) and audit-at-rest encryption (L37)
- Layer Integrity Protocol (ADR-0141) — capability attestation and manifest verification

### Out of scope

- The upstream **Claude CLI binary** (`claude`) — report to Anthropic directly.
- Third-party npm/pip dependencies that have received an upstream fix within
  the last 30 days — update the dependency and retest first.
- Issues that require **physical access** to the host machine.
- **Social engineering** of CorvinOS operators or contributors.
- Issues in your own deployment configuration (misconfigured firewall rules,
  weak credentials, etc.) that are not caused by CorvinOS defaults.

---

## Security Architecture Overview

CorvinOS is built with security as a structural property, not a configuration
option. The following layers form a defense-in-depth stack; each operates
independently so that a bypass of one layer does not collapse the others.

- **Whitelist-gated inbox (L16)** — every inbound message is re-validated
  against the per-channel whitelist at consume time, preventing TOCTOU races;
  no message can enter the processing pipeline from an unauthorised sender.

- **Path-gate hook — fail-closed (L10)** — a `PreToolUse` hook denies all
  filesystem writes targeting forge workspaces, audit chains, policy files,
  skill-forge slot mirrors, and license trees; deny is the default on any parse
  ambiguity; a boot self-test fires `path_gate.self_test_failed` (CRITICAL) on
  any miss.

- **bwrap sandbox for Forge tools** — runtime-generated tools execute inside a
  bubblewrap namespace; network access is persona-scoped (denied by default;
  explicit allow-list required for browser/research personas); secrets are
  injected into the bwrap environment and never placed in the LLM context or
  audit chain.

- **Hash-chained audit log (L16)** — every security-relevant event is appended
  to an `audit.jsonl` file where each entry carries a SHA-256 link to the
  previous entry; tampering with any entry invalidates the chain; `voice-audit
  verify` detects breaks offline; audit-at-rest encryption (L37) seals
  rotated segments with optional RFC 3161 timestamping.

- **Consent gate — deny-by-default (L16)** — observer-transcript sharing is
  opt-in per user, TTL-capped, and re-validated at the point of consumption;
  no auto-admit shortcut exists.

- **Data classification + egress lockdown (L34 / L35)** — task data is
  classified by sensitivity (PUBLIC → INTERNAL → CONFIDENTIAL → SECRET) before
  engine dispatch; a declarative egress policy enforces which hosts a given
  tenant may reach; `data_flow.blocked` and `egress.blocked` events are
  CRITICAL and fail-closed.

- **A2A HMAC verification + replay protection (L38)** — every agent-to-agent
  task envelope is verified with HMAC-SHA256 (constant-time comparison) and a
  TTL-keyed nonce store; the audit chain receives the `A2A.envelope_received`
  event before any spawn; prompt-injection framing wraps inbound instructions
  in a structural defence block.

- **Layer Integrity Protocol (ADR-0141)** — mandatory security layer presence
  is cryptographically verifiable; missing capabilities emit
  `security.capability_missing` (CRITICAL) and block engine spawns; a
  RS256-signed layer manifest is checked at boot and propagated to A2A peers.

- **GDPR / EU AI Act structural controls** — bot-disclosure card (Art. 50),
  per-user consent gate (Art. 6/7), hash-chained audit log (Art. 30/32),
  compliance-zone routing, engine-policy allowlist, and GDPR Art. 17 erasure
  orchestrator (L36) are structural constraints, not configuration options;
  none carries a disable switch.

---

## Disclosure Policy

CorvinOS follows **coordinated (responsible) disclosure**:

1. Reporter submits the vulnerability privately to security@corvin-labs.com.
2. We acknowledge within 72 hours and begin triage.
3. A **90-day embargo** applies from the date of acknowledgement. For critical
   issues affecting active deployments the embargo may be shortened by mutual
   agreement.
4. We prepare a fix, tag a release, and publish a **GitHub Security Advisory**
   simultaneously with or immediately after the tag.
5. Reporter is credited in the advisory unless anonymity is requested.
6. If a fix cannot be delivered within 90 days, we will inform the reporter
   and agree on a revised timeline or a partial advisory.

We do not offer a bug bounty programme at this time.
