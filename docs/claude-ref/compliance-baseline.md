# Compliance Baseline — EU AI Act 2026 + GDPR

Corvin is **structurally constrained** by EU AI Act 2026 + GDPR as a hard design requirement.
Every feature must answer: *does this weaken a structural compliance guarantee?*

## Mechanisms

| Mechanism | Layer | Regulation | Status |
|---|---|---|---|
| Bot-disclosure card (`/join`/`/pass`/`/leave`, one-time per uid) | L19 | EU AI Act Art. 50 | ✅ Locked |
| Per-user consent gate (`/consent on\|off\|<ttl>`, deny-by-default) | L16 Phase 4 | GDPR Art. 6, 7 | ✅ Locked |
| Hash-chained tamper-evident audit log (`audit.jsonl` + daily verify) | L16 | GDPR Art. 30, 32 | ✅ Locked |
| Boot tripwire seals a broken tail with a chained `compliance.chain_discontinuity` seam record — NEVER truncates; whole-chain failure and current-state anchor problems (`tail_truncated`, `anchor_key_insecure_mode`) refuse the boot (`tripwire.py::audit_chain_intact`, 2026-09-07 F-A13) | L16 / ADR-0232 | GDPR Art. 30, 32 | ✅ Locked |
| Every audit record chains: a hash-less record is admissible ONLY as `audit.chain_gap_detected` bound to the tail it was written after (`prev_hash` + keyed `mac`); anything else is `unchained_record` and `write_event(hash_chain=False)` is refused for every other event (`security_events.py`, 2026-09-07 F-A1) | L16 | GDPR Art. 30, 32 | ✅ Locked |
| Out-of-tree tail anchor (`<key dir>/chain_tails/<genesis>`) — deleting the last records is reported as `tail_truncated` by the verifier AND stamped into the next record by the writer; anchor key with group/other bits is refused (`anchor_key_insecure_mode`); markers are genesis-keyed and never written for tmp chains (F-A12/F-A14) | L16 / ADR-0137 | GDPR Art. 32 | ✅ Locked |
| PATH-keyed chain identity (`<key dir>/chain_ids/<sha256(path)>` → genesis + tail + legacy-prefix length + mac flag) — a whole-file rewrite that mints a NEW genesis has no genesis-keyed marker to contradict it, so the file self-verified; a differing genesis is now `chain_replaced` (verify fails, tripwire refuses, writer stamps `_chain_replaced_from`), a grown hash-less prefix is `records_prepended`, and the mac-strip detector also consults this record plus the `audit_mac_active` host sentinel for `<root>/global/forge/audit.jsonl`. A Layer 37 rotation is exempt ONLY on an out-of-tree fact (R3-A1): `rotation_genesis`, written by `note_chain_rotation()` which `rotate_and_seal()` calls under the rotation flock, or the link's own anchor-key `mac`. Recognising the rotation by the SHAPE of record 1 was forgeable — the recorded tail is byte-identical to the file's own last hash. A genesis whose `prev_hash` is neither `initial_prev` nor an authenticated rotation link is `unanchored_genesis`, reported LINE-LESS so the tripwire blocks regardless of the file length the attacker chose (R2-A1/R2-A2/R3-A1) | L16 / ADR-0137 | GDPR Art. 32 | ✅ Locked |
| Audit-detail floor is DEFAULT-DENY for keys: a per-event allowlist (`register_event_allowlist`) or the universal metadata vocabulary `_AUDIT_KNOWN_KEYS`; unknown keys are dropped and named in `_dropped_fields`; free-text keys (`reason`/`detail`/`summary`/`message`/…) are scanned for email/phone shapes on EVERY event type — a registered allowlist no longer skips the value scan — and `register_event_allowlist` REFUSES a denylisted field name (`AuditAllowlistRefused`), since a registered key is exempt from the M1 denylist (R2-A6); tenant mismatch is refused at the chokepoint (`write_event`) for every caller; every `lom` gets a `lom_hash` (F-A4/F-A6/F-A17) | L16 / ADR-0129, ADR-0537 | GDPR Art. 5, 30 | ✅ Locked |
| Both shipped hosts run `assert_all()` UNCONDITIONALLY from `corvin_compliance_reports` before the plugin import; an absent `corvin_plugins` is a boot failure; an env-only chain redirect (`VOICE_AUDIT_PATH`/`FORGE_ROOT`) is refused unless it resolves to the resolver's OWN chain path — outside the `CORVIN_HOME` root and a different file inside it are both refused, and the `PYTEST_CURRENT_TEST` tolerance is GONE (it was a real env-var override of a fail-closed tripwire; tests pin `CORVIN_HOME` instead) (F-A2/F-A3, R2-A3) | L16 / ADR-0232 | GDPR Art. 30, 32 | ✅ Locked |
| The boot tripwire set runs ONCE per boot: the host lifespan call is authoritative and `boot_platform` stands down when the same chain already PASSED in this process (`tripwire.already_asserted()`, keyed by chain path; a FAILED assertion is never recorded). Two runs meant two `compliance.chain_discontinuity` seam records appended for one break (R2-A11) | L16 / ADR-0232 | GDPR Art. 30 | ✅ Locked |
| Boot tripwire asserts the audit WRITER is loaded (`audit.writer_available()`; `tripwire.py::audit_writer_reachable`, `bootstrap._assert_core_audit_inline`) — a stripped or broken forge import is a refusal to boot, and `verify_audit` never answers `(True, [])` without a writer (2026-09-03 A1/A8) | L16 / ADR-0232 | GDPR Art. 30, 32 | ✅ Locked |
| Tenant-mismatched audit writes are refused, logged at ERROR and recorded as `audit.tenant_mismatch` (type/count only) under the context tenant — never dropped silently (2026-09-03 A2) | L16 / ADR-0007 | GDPR Art. 30, 32 | ✅ Locked |
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
   - **The boot tripwire never truncates** (`tripwire.py::audit_chain_intact`, F-A13,
     2026-09-07 — replaces the 2026-07-30 "bounded healing"): a break inside the
     `TAIL_RECORDS` window is SEALED by appending one chained
     `compliance.chain_discontinuity` seam record (`seam: true`, `first_break_line`,
     `last_break_line`, `total_records`); the writer counts as sound iff that record
     verifies against the file. Re-boots do not stack seams. A whole-chain failure
     (every record `mac_tampered` — the lost/rotated anchor-key shape) and any
     current-state anchor problem (`tail_truncated`, `anchor_key_insecure_mode`,
     `mac_stripped_chain`) REFUSE the boot so the operator restores
     `~/.config/corvin-voice/audit_anchor.key` / a backup — never by deleting records.
     Don't reintroduce truncation, don't add an override.
   - **Only one hash-less record shape exists** (F-A1): `audit.chain_gap_detected`,
     written by `audit_health_check` with `prev_hash` = the current tail and a keyed
     `mac`. `verify_chain` reports every other hash-less record — and a gap marker
     anywhere but at the tail it names — as `unchained_record`;
     `write_event(hash_chain=False)` raises for any other event type.
   - **Chain identity is path-keyed as well as genesis-keyed** (R2-A1/R2-A2): every
     chained write also updates `<anchor key dir>/chain_ids/<sha256(abspath)>`, holding
     the chain's genesis, tail, legacy-prefix length and mac flag. The genesis-keyed
     markers answer "what do we know about the chain hashing to G?" and are silent about
     a file whose first record was REPLACED — a rewrite with recomputed hashes and no
     macs mints a new genesis, has no markers, and self-verifies. A genesis that differs
     from the anchored one is `chain_replaced`; a hash-less prefix longer than the
     recorded one is `records_prepended`. NOT a rule: "the genesis carries a mac, so
     there can be no legacy prefix" — a migrated legacy install really does have one and
     its first chained record is written today (see
     `operator/forge/tests/test_tenant_migration_roundtrip.py` R5). The chain-identity
     record is keyed on the RESOLVED path (R3-A3), matching `tripwire._current_chain_key`
     — with `abspath` the same physical chain reached through the compat symlink
     `<corvin_home>/global` hashed to a different key and the aliasing reader saw no
     record at all; the old location stays readable, never written.
   - **A rotation is recognised out of tree, never by record shape** (R3-A1): the ONE
     legitimate genesis change is a Layer 37 rotation, and it counts only when
     `chain_ids/…` carries `rotation_genesis` (written by `note_chain_rotation()`, called
     by `rotate_and_seal()` under the rotation flock) or the `audit.rotation_link` carries
     a valid anchor-key `mac`. The round-2 shape test — "record 1 is a rotation_link whose
     `prev_hash` equals the recorded tail" — was forgeable: the recorded tail is set to the
     last chained record's hash on every write, so it is byte-identical to the last `hash`
     in the file the attacker is rewriting. A genesis whose `prev_hash` is neither
     `initial_prev` nor an authenticated rotation link is `unanchored_genesis`, reported
     WITHOUT a line number so `audit_chain_intact` blocks it as a current-state problem
     regardless of how long the attacker padded the file.
   - **Tail anchor + key hygiene** (F-A12/F-A14): every chained write updates
     `<anchor key dir>/chain_tails/<genesis-hash>`; a verify whose recorded tail is gone
     reports `tail_truncated`, and the next writer stamps `_tail_truncated_since` into its
     record so the deletion is permanent evidence. An anchor key with group/other mode
     bits is refused (no mac written, `anchor_key_insecure_mode` on verify → boot refused);
     fix with `chmod 600`. Per-chain markers are keyed by the genesis hash and never
     written for chains under a tmp dir against the operator's real key.

5. **Don't leak PII** into Prometheus labels, audit details, or log lines.
   Audit allow-lists are strictly enforced per layer; see the respective layer docs.
   - **The detail floor is default-deny for keys** (`security_events.py`, F-A4): an event
     type with `register_event_allowlist()` admits only its keys; every other event admits
     only `_AUDIT_KNOWN_KEYS` (ids, counts, codes, hashes — never `prompt`/`text`/
     `email`/`snippet`/`userName`…). Unknown keys are dropped and NAMED in
     `_dropped_fields`; string values on unregistered events are also scanned for email
     and phone shapes. A writer whose keys vanish registers an allowlist — the vocabulary
     is never widened back to allow-all.
   - **The reserved spine is scanned, not exempt** (R3, 2026-09-07):
     `user` / `chat_key` / `channel` / `persona` / `tenant_id` are exempt from the KEY
     filters (they are what makes a record attributable, GDPR Art. 30) but no longer from
     the VALUE scan. Measured on the live chains, 2 962 `user` and 2 580 `chat_key` values
     out of 596 039 records carried an email or phone shape — the email/messenger bridges
     pass the sender through and `adapter._pii_fp` only covers the adapter's own emitter.
     A reserved value with a PII shape is now replaced by its `sha256[:8]` fingerprint —
     the SAME transform `adapter._pii_fp` applies, so both paths land in one pseudonym
     namespace — and the KEY NAMES (never values) are listed under `_pii_fingerprinted`.
     Fingerprinted rather than dropped on purpose: a chain with the actor removed is not a
     safer trail, it is an unusable one.
   - **Tenant isolation at the chokepoint** (F-A6): `write_event` itself refuses a
     `details.tenant_id` that is not the process tenant (`AuditTenantMismatch`, a
     `ValueError`), after recording `audit.tenant_mismatch` (type/count only) under the
     context tenant — no direct caller can bypass the wrapper's check any more.
   - **`lom` ⇒ `lom_hash`** (ADR-0537, F-A17): the writer binds every LoM label to its
     source (`lom_hash_for`), so no caller can forget it.
   - Telemetry backstops (`_assert_safe`, `_assert_safe_htrace`, `_is_pii_safe_error`)
     also drop international/trunk-prefixed phone numbers, IBANs and free text of four or
     more words (F-A15); `debug_logging.redact()` masks e-mail and phone shapes (F-A16);
     the STT route logs transcript LENGTH only.
   - **Data residency default is restrictive** (L34, F-A10): `CONFIDENTIAL → {local,
     eu_cloud}` in `DEFAULT_MATRIX`; an unparseable classification is `None` and DENIED
     (`unknown_classification`), never `INTERNAL`.
   - **Art. 17 coverage** (L36, F-A9): `real_handler_chain()` includes
     `LearningEventHandler` (ADR-0314 learning store) and `InfiniteSessionHandler`
     (session state + checkpoints); the CCC `/erase` route runs the real orchestrator;
     `tests/security/test_erasure_coverage_guard.py` fails on any tenant-home directory no
     handler claims.

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
