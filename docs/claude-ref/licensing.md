# Licensing 1.0.0 Architecture Reference

**Status:** ACCEPTED (ADR-0700-0704, 2026-09-17)  
**Applies to:** CorvinOS release 3.0.0 and later  
**License:** Apache-2.0  
**Compliance:** GDPR Art. 5/6/30/32, EU AI Act Art. 5/50, § 327r BGB

---

## Table of Contents

1. [Tiers and Pricing](#tiers-and-pricing)
2. [Capability Matrix](#capability-matrix)
3. [Credential Lifecycle](#credential-lifecycle)
4. [Enforcement Architecture](#enforcement-architecture)
5. [Quota Counting](#quota-counting)
6. [Audit Trail](#audit-trail)
7. [Compliance Guarantees](#compliance-guarantees)
8. [Migration Guide](#migration-guide)

---

## Tiers and Pricing

### Two Tiers Only

| Tier | Price | Seat | Licence | Notes |
|---|---|---|---|---|
| **Free** | €0 | — | None (absence is the free tier) | Baseline runtime + 10 compute runs/day |
| **Member** | €10/month per seat | One instance per device | Member Credential (MC, 7d TTL) + Licence JWT (period + 14d grace) | Unlimited features except Forge (class L) and A2A (class N) are now member-only. |

**Key Decisions (ADR-0700 §1):**
- Only `free` and `member` tiers exist. Legacy aliases (`universal`, `starter`, `pro`, etc.) collapse to `member`.
- **Seat = one installation** (instance id) on one device. Device fingerprint = `SHA-256("corvin-license-fp-v1" ‖ machine_id)`.
- Members may **move** seats 3 times/month self-service; further moves via support.
- Existing members with ≤5 bound instances pre-1.0.0 retain extra instances as **free seats** until first renewal ≥12 months after migration notice.

---

## Capability Matrix

### Authoritative Source: `operator/license/limits.py::CAPABILITIES`

Every entitlement is a **capability** with an enforcement class per CONCEPT-0041:

| Capability | Class | Free | Member | Enforcer |
|---|---|---|---|---|
| **Baseline (Class B)** | | | | |
| `chat.turns` | B | ∞ | ∞ | — |
| `voice.summaries` | B | ∞ | ∞ | — |
| `bridges.all` | B | ∞ | ∞ | — |
| `engines.all` | B | ∞ | ∞ | — |
| `skills.run_vetted` | B | ✓ | ✓ | signature verified @ load |
| `skills.run_local` | B | ✓ | ✓ | unsigned code confirmation |
| `telemetry.opt_out` | B | ✓ | ✓ | — |
| **Local Gates (Class L)** | | | | |
| `compute.run` | L | 10/day | ∞ | `acs_engine_adapter::_compute_license_gate` |
| `context.enrich` | L | 10/day | ∞ | degrade-not-block |
| `context.enrich_llm` | L | 5/day | ∞ | degrade-not-block |
| `workflows.max` | L | 1 | ∞ | `routes/workflows.py` |
| `workflows.concurrent` | L | 1 | ∞ | `routes/workflows.py` |
| `rag.providers` | L | 1 | ∞ | `DataSourceRegistry.register` |
| `space.domains` | L | 1 | ∞ | existing gates re-pointed |
| `datasource.connections_concurrent` | L | 1 | ∞ | existing gates re-pointed |
| `layers.custom_bc` | L | 1 | ∞ | `custom_layer_gate.py` |
| `skill_forge_per_day` (skill authoring) | L | ∞ while `FORGE_FREE_UNLOCK` is on (today); 5/day (UTC) when off | ∞ | `SkillRegistry.create` charges; console pre-checks (ADR-2095). A Free install is reported as `free`, never as `member` |
| `cost.optimize` (automatic model downgrading) | L | ≤ 10 % measured saving per UTC month (floor USD 5; operator may only lower it) | ∞ | `model_selector.resolve_os_model` via `bridges/shared/savings_cap.py` (ADR-2259) — deterrence, never a lock; pinned models are never capped |
| **Forge (Class L) — one switch, `corvin_operator/license/limits.py::FORGE_FREE_UNLOCK`** | | | | |
| `tool_forge_per_day` (tool generation) | L | ∞ while `FORGE_FREE_UNLOCK` is on (today); 3/day (UTC) when off | ∞ | ADR-0365 |
| `forge.create` | L | ∞ while `FORGE_FREE_UNLOCK` is on (today); ✗ (member-only) when off | ∞ | ADR-0701 chokepoints — Tool Forge, panels, plugin builder; for SKILLS it only opens the gate, the tier comes from the licence (ADR-2095) |
| **Network Gates (Class N)** | | | | |
| `marketplace.publish` | N | ✗ | ✓ | ADR-0704 §5 |
| `a2a.network` | N | ✗ | ∞ peers | ADR-0702 credential verification |

**Legend:**
- **∞** = unlimited (no quota enforcement)
- **✓** = available (always)
- **✗** = denied (never available on this tier)
- **B** = Baseline (no gating needed, always available)
- **L** = Locally gated (offline quota enforcer, licence JWT)
- **N** = Network-verified (requires Member Credential, 7d TTL, CRL check)

---

## Credential Lifecycle

### Class L Credentials (Offline)

**Licence JWT** (`~/.corvin/global/license.key`)
- Issued: at purchase activation or renewal
- Format: `{type: license, tier, seat_fp, device_fp, instance_ref, iat, exp}`
- TTL: period end + 14 days
- Transport: secure file, 0600 permissions
- Renewal: automatic, every 3h (cadence; uses permit for transport)
- Usage: read by `capability_api::active_tier()` for class-L decisions
- Console **Apply Key** (`POST /v1/console/license/key`): owner + CSRF + re-auth token; signature,
  claims, revocation, device and installation binding are checked BEFORE the file is touched; the
  swap keeps the previous bytes and restores them when the reload raises or the pasted licence is not
  the active one — a rejected paste never removes a valid key (`console-security-surfaces.md`).

### Class N Credentials (Network-Verified)

**Member Credential (MC)** (transient, short-lived)
- Issued: after successful refresh (HMAC counter check, CRL validation)
- Format: JWT with MC-specific claims
- TTL: 7 days (capped at licence JWT expiry)
- Transport: HTTP response body, refreshed automatically via `/bind`
- Offline alternative: operator-issued offline credential (30–90 day TTL)
- Usage: validated at every A2A peer pair, marketplace publish

**Refresh Process** (ADR-0703 §3)
1. Client: every 3 hours (permit TTL is 6h)
2. Client sends: HMAC(device_fp, counter), counter (incremented before send)
3. Server: verifies counter > last accepted (prevents replay); checks CRL; re-mints MC
4. Server: responds with fresh MC + permit (session token)
5. Offline: MC expires after 7 days; counter advance is audited but no re-issue

---

## Enforcement Architecture

### Single Authority

**Corvin-Features** (external, ADR-0704)
- Issues: licence JWT, MC, CRL
- Authority for A2A peer identity verification
- Signature: RSA-2048 (licence JWT header `kid: lic-v2`)

### One API

```python
from license.capability_api import require_capability, LicenseDenied

result = require_capability(
    capability="compute.run",      # e.g. "forge.create", "a2a.network"
    requested=1,                   # quantity (default 1)
    tenant_id="<tid>",            # multi-tenant scoped
    entry_point="file:line"        # line of moral responsibility
)
# Returns: CapabilityDecision(decision=ALLOW|DENY, tier, allowed, reason, upgrade_url)
# Raises: LicenseDenied if denied (→ HTTP 402)
```

**Enforcement Sites (Chokepoints):**
1. **L6 (ToolForge):** `operator/forge/forge/registry.py::Registry.create` → `require_capability("forge.create")`
2. **L7 (SkillForge):** `operator/skill-forge/skill_forge/registry.py::SkillRegistry.create` →
   `skill_authoring_tier()`: a member (licence tier `member`, not merely `forge.create` ALLOW)
   writes freely; the free tier writes within `skill_forge_per_day` (unlimited while
   `FORGE_FREE_UNLOCK` is on, else 5 per UTC day), charged by `charge_skill_quota()` right before
   the write (a linter rejection costs nothing). `quota_exempt=True` only for a skill canary's
   approve / rollback — the canary FORK was the charged generation (ADR-2095).
3. **Console UI:** `require_skill_forge_quota(rec)` on `/skill-creator/generate`,
   `/skills/manual` (POST/PUT) and `/autonomous-forge/fork` — a PRE-check, so a minutes-long
   generation is not started with no credit left; answers 402 `{"error": "limit_reached",
   "limit", "used", "resets_at", "message"}`. `GET /skill-creator/quota` feeds the Skill Forge
   panel ("N of 5 new skills left today" / "Daily limit reached").
4. **Compute:** `core/console/routes/compute.py::run_compute` → `require_capability("compute.run")`
5. **Marketplace:** `routes/marketplace.py::publish` → `require_capability("marketplace.publish")`
6. **A2A Pairing:** `a2a_verifier.py::verify_delegation` → `require_capability("a2a.network")`

### Every process loads the licence (deferred first-read load)

`load_license_from_env()` runs at console/gateway/adapter boot, but the MCP servers the engine
spawns (forge, skill_forge, orchestration) are separate processes that never booted it. Until
2026-11-06 they read "no licence" and answered **free** for a paying member: `forge_tool` was
refused with `license_required: forge.create denied (not_available_in_tier)`, `skill-forge:create`
was audited as `free deny`. Now the first licence read in a process (`validator._verified_license`)
performs that load once, through the same verification as boot (signature, CRL, expiry,
device/installation binding):

- it runs only while no licence is set and no load has finished (`_LICENSE_LOAD_DONE`), so a
  licence a caller set — or cleared by tamper response / revocation — is never re-loaded;
- the load holds `_RELOAD_LOCK` (RLock): a concurrent first read in another thread waits for the
  result instead of reading Free; the load's own audit writes re-enter from the same thread;
- a load that raises leaves the Free tier.

Import the validator as `license.validator` (or `corvin_operator.license.validator`, the same
module object). The bare `validator` name (with `corvin_operator/license` on `sys.path`) is a
THIRD, unaliased module with its own licence state — don't use it in a long-lived process.

A lazily loaded licence lives until the process exits, like the adapter's boot load; the
in-process refresh is the console's `reload_from_disk()`.

### Compute access without the Enterprise plugin

`core/compute/corvin_compute/license_gate.py::check_compute_access` (the `compute_run` /
`compute_submit` gate of the forge MCP server) used the Enterprise `corvin_license` package as
its only licence source. That package was deleted in 853ee7c54 (2026-09-15), so every install,
members included, ran on the 500-iteration trial. Without the package the gate now asks the
operator licence: `licensed` when `active_tier()` is paid AND `CAPABILITIES["compute.run"][tier]`
is unlimited, `fabric_allowed` false; anything else (free, unresolvable, finite limit) keeps the
trial. This matches the console's compute status (`routes/compute.py`, `except ImportError`).
Pinned by `tests/license/test_license_deferred_load_subprocess_e2e.py` (real forge stdio server).

### Compute trial accounting (2026-11-06)

The trial (`TRIAL_ITERATION_CAP` 500, Bayesian 50) is a budget of ITERATIONS. A submission is charged
the iteration budget it was GRANTED (`record_trial_iteration(..., n=granted)`) — a reservation; an early
stop is not refunded. It used to charge +1 per submission while granting everything left (500 + 499 +
498 ...). `compute_submit` pipeline/HAC jobs go through `enforce_trial_engine`: pipeline stages share
what is left (each stage <= remaining // n_stages, stage budgets clamped — they used to override the
top budget), HAC gets one round without the backprop gate and manager floors capped to their share;
the job is charged its reservation. Members are unaffected (`mode: licensed`).

### Datasource adapters and the daily compute unit (2026-11-06)

`datasource_adapters_allowed` (Free `["local_file"]`, Member unlimited) is decided in ONE place,
`license.validator.datasource_adapter_allowed`, and is enforced on every path that binds a DSI connection:
`datasource_connect` / `datasource_query` and the console routes (inline), and now also `compute_run` /
`compute_submit(datasources=...)` (worker preflight + runner) and ACS (`acs_delegate` up front,
`_resolve_acs_datasources` at resolution). The free tier's `compute_units_per_day` unit is taken before the
worker is asked and refunded when the worker refuses the run — a refusal is not a run.

### Fail-Closed Contract

**On enforcement error:**
1. Capability lookup fails (malformed limits, missing tier)
2. Audit backend unreachable (disk full, corrupt chain)
3. Credential unreadable (corrupted JWT, missing file)

**Fallback:** return free-tier allowance with in-process quota counter
- Reason: "enforcement_unavailable"
- Audit: error logged (best-effort)
- Result: feature degrades gracefully, operator is alerted

---

## Quota Counting

### Class L Quotas (Local, Offline)

**Per-installation, per-UTC-day:**
```
quota_today = counter_file("~/.corvin/tenants/<tid>/global/license/quota.json", "compute.run")
if quota_today < LIMIT:
    quota_today += requested
    # audit to hash-chain
    # increment counter with fsync + rename
else:
    raise LicenseDenied("quota_exceeded")
```

**Counter Properties:**
- File: `global/license/<capability>_quota.json` (per-tenant, per-capability)
- Lock: `flock` (POSIX) / `msvcrt.locking` (Windows)
- Format: `{date: "YYYY-MM-DD", count: int}`
- Reset: at UTC 00:00, not local midnight
- Atomicity: counter incremented **before** send, fsync + rename (fail-closed)

**Example: compute.run (10/day free)**
```json
{
  "date": "2026-09-17",
  "count": 5,
  "last_reset_utc": "2026-09-17T00:00:00Z"
}
```

### Class N Quotas (Network-Verified)

For `a2a.network` (peers_max):
- Free: NOT on the network at all — `require_a2a_network` denies pairing, pings and every exchange
  (402 `license_limit` / `a2a.network` / `member_seat_required` in the console since 2026-10-21;
  the old "1 peer" allowance is gone with gate 28ad07b54)
- Member: `a2a_peers_max` applies (a pairing the peer ended holds no seat)
- Enforcement: MC carries no peer quota; existence of valid MC = unlimited

For `marketplace.publish`:
- Free: denied (no MC for marketplace)
- Member: allowed if MC present and fresh (≤7d)

---

## Audit Trail

### Every Capability Decision is Audited

**Event Type:** `license.capability_decision`

```json
{
  "event_type": "license.capability_decision",
  "tenant_id": "<tid>",
  "capability": "compute.run",
  "tier": "free",
  "decision": "allow" | "deny",
  "requested": 1,
  "allowed": 10,
  "lom": "core/console/routes/compute.py:1033",
  "timestamp": 1695057034,
  "hash": "sha256(...)"
}
```

**Hash-Chain:**
- Written to: `~/.corvin/tenants/<tid>/global/audit.jsonl`
- Immutable: append-only, no delete/update/rewrite
- Integrity: each event carries `prev_hash` (chain link); verified at boot (ADR-0232)
- Retention: 7 years (RFC 3161 timestamps)

### Audit Verification

```bash
corvin audit verify-chain --tenant=_default
# Output: ✅ Chain height 3417, all hashes verified, 0 gaps

corvin audit trace capability compute.run --task=<task_id>
# Output: all license.capability_decision events for this task
```

---

## Compliance Guarantees

### GDPR Art. 5 (Data Minimization)

**What is collected (Licensing-scoped):**
- Device fingerprint (SHA-256 hash, domain-separated)
- Instance ID (UUID-like, no PII)
- Seat ID (license server-assigned, no PII)
- Token fingerprint (HMAC, not the credential itself)
- Refresh counter (monotonic, audited)

**What is NOT collected:**
- MAC address (FP basis is machine ID only)
- Prompts, code, or user content
- IP address (except for geographic tier routing, separate consent)

**Storage:**
- Persisted in: `~/.corvin/tenants/<tid>/global/license/` (instance-scoped)
- Copied to: Corvin-Features server (licence verification, CRL, rebuild detection)
- Retention: Per-seat, until licence cancelled; then audit trail only

### GDPR Art. 30/32 (Audit Trail)

**Duty of processor (if Corvin-Features is the processor under an enterprise contract):**
- Audit log furnished to data controller on request
- RFC 3161 timestamps (externally signed)
- Hash-chain verified before disclosure
- Certificate of accuracy

**Data controller duty (each operator):**
- Maintains their own `audit.jsonl` per instance
- Responsible for backup + retention of their instance's audit
- May request Corvin-Features' server-side logs for debugging

### EU AI Act Art. 5 (Transparency) / Art. 50 (Bot Disclosure)

**Licensing does not add disclosure requirements** (already met by CorvinOS base layer).
Licensing decisions are logged but not surfaced to the end-user as "a bot decided this."

### GDPR Art. 6 (Lawful Basis)

| Processing | Basis | Duration |
|---|---|---|
| Licence issuance | Contract (Member Agreement v1) | Per billing cycle |
| Device fingerprint | Legitimate interest (fraud prevention) | Per seat lifecycle |
| CRL fetch (hourly) | Legitimate interest (revocation detection) | 7d (MC TTL) |
| Clone detection | Legitimate interest (anti-abuse) | Per seat lifecycle |
| Audit trail | Contract + compliance (GDPR 30/32) | 7 years |

---

## Migration Guide

### For 1.0.0 Release (Existing Members)

**Timeline:**
1. **T-30:** Operator sends migration notice (e-mail, durable medium)
   - Lists changes: device binding, MC + refresh, new capability boundaries (Forge, A2A)
   - Offers Member Agreement v1 acceptance in portal
   - Deadline: 30 days before gate release
   
2. **T-7:** Final notice (no acceptance needed; auto-accept at T-30 or cancel)

3. **T-0:** Gate release ships
   - Member Agreement v1 effective for all accepting members
   - Non-accepting members: licence terminated with pro-rata refund (Paddle adjustment)
   - Grace: none (immediate termination upon gate release)

**Grandfathering:**
- Pre-1.0.0: up to 5 bound instances per seat
- 1.0.0+: 1 bound instance per seat
- **Exception:** members accepting v1 keep extra instances as **free seats until first renewal ≥12 months after notice**
- After grace period: extra instances unbound (no longer accessible)

### For Free Users

No change; baseline features remain the same. Forge and A2A are now member-only (already in beta/restricted, so public impact minimal).

### For Operators

1. Read ADR-0700-0704 (architectural decisions)
2. Update `core/compute/LICENSE-RUNS.md` if new compute entry points are added
3. Update website: pricing table, checkout, refund, terms, imprint, privacy (all generated from CAPABILITIES or ADR-0704 table)
4. Test migration: non-accepting members → automatic termination + refund via Paddle
5. Monitor: licence refresh cadence (3h), CRL freshness (7d), clone detection (reinstate limit, contest)

---

## A2A delegation credentials (local authority store)

`core/licensing/` holds a second, local credential system: an authority key per tenant
(`<tenant_home>/licensing/authority/`) signs member credentials
(`<tenant_home>/licensing/credentials/<credential_id>.json`), and
`A2ADelegationVerifier.verify_signed_task` admits an RSA-signed delegation task only when the
credential carries the authority's signature, is not expired, is not revoked, its tier permits
delegation (`member` / `enterprise`), the task signature verifies, the task is inside its TTL
and has not been seen before. Console routes: `/v1/licensing/a2a/*`
(`routes/a2a_licensing_gate_routes.py`; issue and revoke are owner-only).

A stored credential is a file. Everything read back from the store is treated as untrusted
until the authority signature over it verifies (review 2026-11-19, refutation round):

| Rule | Behaviour | Was |
|---|---|---|
| **Downgrade issues a new credential** | `downgrade_tier` signs a NEW `credential_id` (same member key and expiry, lower tier), revokes the old id, then stores the new one. Order: audit, revoke old, save new - a failure in between leaves no credential, never the old tier. `IssuanceResult.credential` is the new credential. | The same id was re-signed in place. The earlier file still carried a valid authority signature for the higher tier, so copying it back over the store entry restored the old tier (rollback). |
| **Verify before re-signing** | `renew_credential` and `downgrade_tier` refuse a stored credential whose authority signature does not verify (`AuthorityServer.signature_valid`, read-only: it never creates a key). A revoked credential cannot be downgraded. | Both copied member, tier and public key from the file into a freshly signed credential: an edited file (tier raised, key swapped) was laundered into a valid one. |
| **A deleted CRL is fail-closed** | The first save of `licensing/crl.json` leaves the marker `licensing/.crl_initialised`; the first issuance initialises both. "Marker present, `crl.json` absent" means every credential is treated as revoked, `revoke()` and issuance refuse, and `GET /v1/licensing/a2a/crl` answers 503. A list written before the marker existed is marked on first read. | A missing file read as "nothing revoked": deleting it lifted every revocation at once. |
| **Credential info is signature-checked** | `get_credential_info` returns `signature_valid`; `is_valid` and `can_delegate` are false without it. | Tier and validity were reported straight from the file. |

Third round (2026-11-19, `tests/unit/licensing/test_review_r3_licensing.py`):

| Rule | Behaviour | Was |
|---|---|---|
| **A missing CRL is re-evaluated on every read** | `RevocationList._refresh_if_stale` calls `_load()` whenever `crl.json` is absent - only `_load()` looks at the marker. | An instance built before the first list existed compared "no file" with its own "never loaded", kept its empty list after the list was created, used and deleted, and accepted a revoked credential. |
| **A downgrade covers the member** | `downgrade_tier` revokes the named credential AND every other unrevoked credential of that member above the new tier (`CredentialStore.list_for_member`); each gets its own `a2a.license_credential_revoked` (`source: "downgrade"`). | Only the named id was revoked: a sibling (the credential it had been renewed from) stayed at the old tier and could be renewed again. |
| **Renewal replaces** | `renew_credential` revokes the old id (`source: "renewal"`) before it issues the successor. A bad `validity_days` is refused before anything is revoked. | The old credential stayed valid next to its successor. |
| **One store lock** | Renew, downgrade and revoke run under `StoreLock` on `licensing/.store.lock` (re-entrant in-process lock + `flock`); `RevocationList` mutations (refresh, change, save) run under `licensing/.crl.lock`; the replay store's lock is per store, not process-wide. | A renewal and a downgrade of one credential interleaved; two processes revoking at once each wrote a list without the other's entry; one tenant's verifications queued behind another's. |
| **A partial failure says so** | When the old credential is already revoked and the successor could not be stored, the error ends in "the old credential is revoked; issue a new one". | "Downgrade failed: OSError" - and the retry answered "revoked and cannot be downgraded". |
| **TTL** | `SignedTask.effective_ttl_seconds` treats `inf`/`nan`/non-numbers as 0. | `int(float("inf"))` raised `OverflowError` out of the verifier. |

Open, stated: the order is audit, then revoke. If the revocation record commits and the CRL
write then fails, the chain holds an `a2a.license_credential_revoked` record for a credential
the CRL does not list. The caller gets an error and the credential keeps verifying; nothing
reconciles the two at boot yet.

Recovery when `crl.json` is lost: restore it from a backup. Only if no copy exists, remove
`licensing/.crl_initialised` deliberately - the earlier revocations are then gone, and that is an
operator decision, not something the code does on its own.

Boundary, stated: the marker and the authority key live in the same store as the credentials.
These rules hold against a lost, restored or edited file. A process that can write arbitrary
files in the store as the same OS user can replace the authority key itself.

Audit: a downgrade writes `a2a.license_credential_downgraded` (new id as `cred_ref`, the old one
as `previous_cred_ref`) followed by `a2a.license_credential_revoked` for the old id with
`source: "downgrade"`. Tests: `tests/unit/licensing/test_review_r2_licensing.py`,
`test_review_r3_licensing.py`.

---

## Appendix: ADR References

| ADR | Title | Focus |
|---|---|---|
| **ADR-0700** | Canonical Licence Model 1.0.0 | Tier vocabulary, capability matrix, credential lifecycle |
| **ADR-0701** | Forge Member-Only Capability Gate | Forge/SkillForge chokepoints, signed provenance |
| **ADR-0702** | A2A Network Member-Only Credential | Member Credential (MC), CRL, clone detection |
| **ADR-0703** | Licence Runtime Consolidation | `require_capability()` API, fail-closed contract, quota counter |
| **ADR-0704** | Licence Authority Key Custody Lifecycle | Corvin-Features issuance, refresh flow, processing activities |

---

**Generated from ADR-0700-0704 (ACCEPTED, 2026-09-17)**  
**Last updated:** 2026-09-17  
**License:** Apache-2.0
