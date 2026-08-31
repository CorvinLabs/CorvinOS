# Compliance Baseline — EU AI Act 2026 + GDPR

Corvin is **structurally constrained** by EU AI Act 2026 + GDPR as a hard design requirement.
Every feature must answer: *does this weaken a structural compliance guarantee?*

## Mechanisms

| Mechanism | Layer | Regulation | Status |
|---|---|---|---|
| Bot-disclosure card (`/join`/`/pass`/`/leave`, one-time per uid) | L19 | EU AI Act Art. 50 | ✅ Locked |
| Per-user consent gate (`/consent on\|off\|<ttl>`, deny-by-default) | L16 Phase 4 | GDPR Art. 6, 7 | ✅ Locked |
| Hash-chained tamper-evident audit log (`audit.jsonl` + daily verify) | L16 | GDPR Art. 30, 32 | ✅ Locked |
| Boot tripwire audit-chain healing — bounded, minimal-truncation, fail-closed on whole-chain failure (`tripwire.py::audit_chain_intact`) | L16 / ADR-0232 | GDPR Art. 30, 32 | ✅ Locked |
| Compliance-zone routing (`tenant.corvin.yaml::data_residency`) | ADR-0007 | EU AI Act Art. 14 | ✅ Verified |
| Engine-policy allowlist (`allowed_engines` / `forbid_engines`) | ADR-0007 | EU AI Act Art. 14 | ✅ Verified |
| Secret-vault capability split (vault → bwrap env, never LLM context) | L16 v3 | GDPR Art. 32 | ✅ Locked |
| Path-gate hook (fail-closed on forge/skill-forge/audit/policy writes) | L10 | GDPR Art. 32 | ✅ Locked |
| Voice-transcribe audit emits METADATA ONLY, never transcript text | L23 | GDPR Art. 5 | ✅ Locked |
| Acceptable-use / house-rules gate (no military / offensive-cyber / disinformation) | L44 | EU AI Act Art. 5 + 50 | ✅ Locked |
| Tier 2/3 geo-tracking consent gate — region/city require BOTH `geo_tracking_tier` config AND explicit `geo_tracking_consent_given: true`; Tier 1 (country) stays default-ON/opt-out | ADR-0205/0206 | GDPR Art. 6(1)(a) | ✅ Locked |

## Absolute Constraints (Must NOT do)

1. **Don't weaken disclosure** — the AI-nature statement and opt-out commands (`/pass`, `/leave`) are structurally locked.
   Verify in CLAUDE.md if you're uncertain about disclosure scope.

2. **Don't add house-rules disable switch / env kill-flag.**
   - The repo policy is `operator/policy/house_rules.yaml`, anchored by `EXPECTED_POLICY_SHA256` in `house_rules.py`.
   - Edit both together; fail-closed always.
   - Don't let a tenant overlay weaken a repo rule.

3. **Don't bypass consent** — no auto-admit shortcut, no trusted-observer allowlist, no consent disable.
   Consent is per-uid, deny-by-default, TTL-capped, single-shot `/share`, re-validated at consume.

4. **Don't lower audit-chain integrity** — no event skips the hash-chain link.
   Every spawn, tool-call, audit-emit must write to `audit.jsonl` before the action.
   - **Boot-tripwire healing is bounded and fail-closed** (`tripwire.py::audit_chain_intact`,
     2026-07-30 review): a *tail-local* break (records before it verify AND there is
     established history beyond the `TAIL_RECORDS` window) truncates at the record just
     before the FIRST break — deleting the minimum, never the whole tail window. A break
     that reaches the pre-tail history, or a chain that fits entirely inside the tail
     window, or a whole-chain failure (the anchor-key-loss shape, every record
     `mac_tampered`) is **NOT** healed — boot is refused so the operator restores
     `~/.config/corvin-voice/audit_anchor.key` or a backup. Every heal writes a
     `compliance.chain_discontinuity_healed` event carrying `records_deleted`, and the
     healed file keeps `0600`. Don't make healing delete more, don't let it fire on a
     whole-chain failure, and don't add an override.

5. **Don't leak PII** into Prometheus labels, audit details, or log lines.
   Audit allow-lists are strictly enforced per layer; see the respective layer docs.

6. **Don't widen engine reach** past the tenant's `allowed_engines`/zone gate.
   L34 + L35 gates are fail-closed; data flows through matrix checks at every spawn.

7. **Don't add "compliance-off mode"** via any env var or flag.
   A kill-switch is a backdoor. Compliance must be structural, not toggleable.

8. **Don't accept "we'll add the audit-event later"** — audit is part of the feature, not a follow-up.
   Audit-first invariant: L16 hash chain write **before** action.

9. **Don't silence `voice-audit verify` exit-1** — the daily verification must succeed.
   CRITICAL self-test failure blocks container health.

## Multi-tenant Compliance (ADR-0007 — Tier 2 Verified)

✅ All routes use `rec.tenant_id` from authenticated `SessionRecord` (NOT environment variables).
✅ Engine settings (`engine.py`, `engine_pref.py`) read tenant config from session-backed `tenant_id`.
✅ Local login (`auth_routes.py`) uses hardcoded `"_default"` tenant (never env var).
✅ Audit trail records correct `tenant_id` for every event.
✅ Cross-tenant isolation verified: no unauthorized read/write access between tenants.

**Enforcement points:**
- `auth.py` — `SessionRecord` validation + tenant_id binding
- `engine.py` — Tenant config write with session `tenant_id`
- `engine_pref.py` — Per-chat engine pref scoped to session tenant
- `audit.py` — Audit events recorded to correct tenant's `audit.jsonl`

## Data Residency + Zone Routing

Compliance zones are declared in `tenant.corvin.yaml::spec.data_residency` (default: `global`).
Engine allowlist (`allowed_engines`, `forbid_engines`) is tenant-specific and enforced at spawn.

Three-layer defence (L34 + L35 complementary to ADR-0007):
1. **L34** Data Classification (4-stage × engine matrix, fail-closed)
2. **L35** Network Egress Lockdown (allowed/forbidden hosts, EU_PRODUCTION presets)
3. **ADR-0007** Multi-tenant axis (tenant_id in session, not env)

→ See [Layer 34](layer-34-data-classification.md) and [Layer 35](layer-35-egress-lockdown.md) for details.

## Related

- [Layer 16](layer-16-security.md) — Consent gate, audit chain
- [Layer 19](layer-19-disclosure.md) — Bot-disclosure card
- [Layer 44](layer-44-house-rules.md) — Acceptable-use gate
- [ADR-0007](https://github.com/anthropics/corvin-adr/blob/main/decisions/0007-multi-tenant-axis.md) — Multi-tenant axis
- ADR-0205/0206 — Multi-tier geo-tracking (Tier 1 country default-ON, Tier 2/3 region/city opt-in); Tier 2/3 uses Cloudflare-edge-resolved geo (never a raw IP) and file-based TTL storage, not the original Postgres design
