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
| **Member Only (Class L)** | | | | |
| `forge.create` | L | ✗ | ∞ | ADR-0701 chokepoints G1–G5 |
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
2. **L7 (SkillForge):** `operator/skill-forge/skill_forge/registry.py::SkillRegistry.create` → same
3. **Console UI:** `FastAPI require_forge_capability(rec)` on `/skill-creator/generate`, `/skills/manual`
4. **Compute:** `core/console/routes/compute.py::run_compute` → `require_capability("compute.run")`
5. **Marketplace:** `routes/marketplace.py::publish` → `require_capability("marketplace.publish")`
6. **A2A Pairing:** `a2a_verifier.py::verify_delegation` → `require_capability("a2a.network")`

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
- Free: 1 peer (hardcoded, not counter-based)
- Member: unlimited
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
