# Licensing Architecture — Implementation Roadmap

**Source:** ADR-0363  
**Status:** Ready for Phase 1  
**Last Updated:** 2026-08-17

---

## Executive Summary

This roadmap outlines the four-week implementation plan to ship licensing architecture supporting
Brain v0.2 and Forge features. The system uses Ed25519-signed license keys, Redis quota metering,
and five enforcement gates (L48–L52).

---

## Phase 1: Core Infrastructure (Week 1)

### Goals
- Implement license key structure and signature verification
- Build LicenseValidator with fail-closed gates
- Integrate L48–L51 gates at critical call sites
- PostgreSQL schema for license storage

### Deliverables

#### 1.1 LicenseKey Dataclass
**File:** `core/compliance/corvin_compliance_reports/license_key.py`

```python
@dataclass(frozen=True)
class LicenseKey:
    tenant_id: str
    tier: Literal["free", "standard", "professional", "enterprise"]
    issued_at: str
    expires_at: str
    features: FrozenDict[str, bool]
    quotas: FrozenDict[str, int]
    public_key_id: str
    signature: bytes
```

**Tests:**
- `test_license_key_immutability` — verify `frozen=True` prevents mutation
- `test_license_key_serialization` — verify to_json/from_json roundtrip
- `test_default_free_license` — verify free tier defaults

#### 1.2 LicenseValidator
**File:** `core/compliance/corvin_compliance_reports/license_validator.py`

```python
class LicenseValidator:
    async def check_brain_enabled(self, tenant_id: str) -> None
    async def check_tool_forge_enabled(self, tenant_id: str) -> None
    async def check_skill_forge_enabled(self, tenant_id: str) -> None
    async def check_plugin_limit(self, tenant_id: str) -> None
    async def record_usage(self, tenant_id: str, feature: str, amount: int) -> None
    def _verify_signature(self, license: LicenseKey) -> bool
```

**Tests:**
- `test_brain_gate_enabled` — allow access if brain=true
- `test_brain_gate_disabled` — deny access if brain=false
- `test_brain_gate_expired_license` — deny if expires_at < now
- `test_brain_gate_signature_verification` — verify Ed25519 validation
- `test_tool_forge_gate_quota_check` — deny if quota exhausted
- `test_plugin_gate_limit` — deny if plugin count >= max

#### 1.3 PostgreSQL Schema
**File:** `core/compliance/migrations/001_create_licenses_table.sql`

```sql
CREATE TABLE licenses (
    id SERIAL PRIMARY KEY,
    tenant_id VARCHAR(255) UNIQUE NOT NULL,
    tier VARCHAR(50) NOT NULL,
    issued_at TIMESTAMP WITH TIME ZONE,
    expires_at TIMESTAMP WITH TIME ZONE,
    features JSONB,
    quotas JSONB,
    public_key_id VARCHAR(255),
    signature BYTEA,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    INDEX idx_tenant_id (tenant_id)
);
```

**Migration script:** `alembic upgrade head`

#### 1.4 LicenseStore
**File:** `core/compliance/corvin_compliance_reports/license_store.py`

- Implement PostgreSQL queries (get, set, count_plugins)
- Implement Redis caching (24h TTL)
- Hybrid lookups (cache → DB → default free)

**Tests:**
- `test_license_store_get_cached` — verify Redis hit
- `test_license_store_get_from_db` — verify DB fallback
- `test_license_store_get_default_free` — verify default tier
- `test_license_store_cache_invalidation` — verify Redis delete on update

#### 1.5 Integration Points (L48–L51)
**Files:**
- `core/brain/orchestrator.py::initialize_brain` — L48 gate
- `core/forge/tool_registry.py::forge_tool_from_impl` — L49 gate
- `core/forge/skill_registry.py::skill_create` — L50 gate
- `core/plugins/registry.py::create_plugin` — L51 gate

**Implementation pattern:**
```python
async def function_xyz(tenant_id: str, ...):
    try:
        await license_validator.check_feature_enabled(tenant_id)
    except (FeatureLocked, QuotaExceeded) as e:
        raise e
    
    # ... do work ...
    
    await license_validator.record_usage(tenant_id, "feature_name", 1)
```

**Tests:**
- E2E: feature locked on free tier
- E2E: feature accessible on Standard tier
- E2E: usage recorded to audit trail

### Timeline
- **Days 1–2:** Implement LicenseKey + LicenseValidator
- **Days 3–4:** PostgreSQL schema + LicenseStore
- **Days 5–6:** Integration at call sites
- **Day 7:** Review + E2E test

### Success Criteria
- ✅ All unit tests pass
- ✅ All E2E tests pass
- ✅ No license checks logged to audit.jsonl for free tier (feature locked)
- ✅ License installed on Standard tier → feature accessible

---

## Phase 2: Quota Metering & CLI (Week 2)

### Goals
- Implement UsageLogger (Redis quota tracking with daily reset)
- Build CLI commands (issue, install, info)
- Test quota exhaustion and auto-reset

### Deliverables

#### 2.1 UsageLogger
**File:** `core/compliance/corvin_compliance_reports/usage_logger.py`

```python
class UsageLogger:
    async def record(self, tenant_id: str, feature: str, amount: int) -> None
    async def get_daily_usage(self, tenant_id: str, feature: str) -> int
    async def get_historical_usage(self, tenant_id: str, feature: str, days: int) -> dict
```

**Key:**
- Usage key format: `usage:{tenant_id}:{feature}:{date_iso}`
- Redis INCR + EXPIRE (24h TTL)
- Auto-reset at UTC midnight + 24h

**Tests:**
- `test_usage_record_increments` — verify increment works
- `test_usage_reset_daily` — verify TTL expiry
- `test_quota_check_quota_exceeded` — deny when limit reached
- `test_quota_reset_after_24h` — verify reset (wait simulation)
- `test_historical_usage_aggregation` — query 30-day trend

#### 2.2 CLI License Commands
**File:** `core/cli/corvin_cli/commands/license.py`

**Commands:**
- `corvin-cli license issue` — issue license (operator only)
- `corvin-cli license install` — install license from file
- `corvin-cli license info` — display current license info

**Subcommand: issue**
```bash
corvin-cli license issue \
  --tenant-id "org-acme" \
  --tier "standard" \
  --expires-in "1 year" \
  --output license.json
```

**Subcommand: install**
```bash
corvin-cli license install license.json
```

**Subcommand: info**
```bash
corvin-cli license info
# Output:
# Tenant:     org-acme
# Tier:       standard
# Expires:    2027-08-17 (in 364 days)
# Features:   brain, tool_forge, skill_forge
# Quotas:     100 brain_tasks/day, 50 tool_forge/day, 20 skill_forge/day
# Usage Today:
#   Brain:      0 / 100
#   Tool Forge: 12 / 50
#   Skill Forge: 3 / 20
```

**Tests:**
- `test_license_issue_creates_valid_signature` — verify Ed25519
- `test_license_issue_output_json_format` — verify JSON schema
- `test_license_install_updates_db` — verify PostgreSQL write
- `test_license_install_invalidates_cache` — verify Redis delete
- `test_license_info_displays_correctly` — verify formatted output
- `test_license_info_with_no_license_shows_free_tier` — verify default

#### 2.3 License Provisioning Service
**File:** `core/cli/corvin_cli/commands/license_issuer.py`

- Load Anthropic private key (from secure store, not env)
- Reconstruct canonical JSON
- Sign with Ed25519
- Output JSON with signature

**Key rotation:**
- Public keys in validator keyring (updated monthly)
- Private key stored in HSM / secure ops system (not in repo)

**Tests:**
- `test_signature_verification_matches_issued` — verify roundtrip
- `test_key_rotation_with_multiple_keys` — verify multi-key validation

#### 2.4 Quota Check in Validator
**Integration with LicenseValidator from Phase 1**

```python
async def _check_quota(
    self,
    tenant_id: str,
    feature: str,
    amount: int = 1,
) -> None:
    """Check if quota allows request, raise QuotaExceeded if not."""
    license = await self.store.get_license(tenant_id)
    quota_key = f"{feature}_per_day"
    max_quota = license.quotas.get(quota_key)
    
    if max_quota is None:
        raise QuotaUndefined(f"No quota for {feature}")
    
    used_today = await self.usage_logger.get_daily_usage(tenant_id, feature)
    
    if used_today + amount > max_quota:
        raise QuotaExceeded(
            title="Quota exceeded",
            message=f"You've used {used_today}/{max_quota} {feature} today",
            feature=feature,
            used_today=used_today,
            quota=max_quota,
            reset_time=(datetime.now() + timedelta(hours=24)).isoformat(),
        )
```

**Tests:**
- Integrated in quota tests from Phase 1

### Timeline
- **Days 1–2:** Implement UsageLogger + Redis integration
- **Days 3–5:** Build CLI commands (issue, install, info)
- **Days 6–7:** Integration testing + quota reset simulation

### Success Criteria
- ✅ CLI commands work end-to-end
- ✅ Quota exhaustion blocks requests
- ✅ Quota resets daily via Redis TTL
- ✅ Usage logged to audit.jsonl
- ✅ No manual quota reset cron needed

---

## Phase 3: Console UI & Upsell (Week 3)

### Goals
- Add Console Settings → Licensing panel
- Display locked features with upsell buttons
- Show quota usage and reset time
- Build upgrade flow

### Deliverables

#### 3.1 Console License Settings Panel
**Files:**
- `core/console/corvin_console/routes/settings/licensing.py` — backend
- `core/console/corvin_console/web-next/src/pages/Settings/Licensing.tsx` — frontend

**Display:**
- Current tier (Free, Standard, Professional, Enterprise)
- License expiry date + countdown
- Features table (enabled/disabled per tier)
- Current quota usage (bar chart or progress)
  - Brain tasks: 23 / 100 (23%)
  - Tool Forge: 45 / 50 (90% — warning)
  - Skill Forge: 8 / 20 (40%)

**Actions:**
- "Upgrade to Standard" button (links to pricing page)
- "Manage License" link (for enterprise)
- "View Audit Log" link (for compliance team)

#### 3.2 Locked Feature UI
**Files:**
- `core/console/corvin_console/web-next/src/components/FeatureGate.tsx` — reusable gate component

**Display when feature locked:**
```
[🔒] Brain v0.2
Requires Standard tier or higher ($99/mo)
[Upgrade Now]  [Learn More]
```

**Usage:**
```jsx
<FeatureGate
  feature="brain"
  currentTier={license.tier}
  requiredTier="standard"
  onUpgrade={() => navigate("/pricing?tier=standard")}
>
  {/* Brain UI here; only renders if gate passes */}
</FeatureGate>
```

#### 3.3 Quota Exceeded UI
**Display in chat when quota exhausted:**
```
Tool Forge quota exceeded: 50/50 used today
Quota resets at 2026-08-18 00:00 UTC (in 23 hours)

[Upgrade to Professional for 500/day]  [Wait for reset]
```

**Implementation:**
- Catch QuotaExceeded exception in chat handler
- Display error banner at top of chat
- Show reset timer (countdown)
- Show upgrade button

#### 3.4 Pricing & Upgrade Flow
**Files:**
- `core/console/corvin_console/routes/pricing.py` — pricing page
- `core/console/corvin_console/web-next/src/pages/Pricing.tsx` — pricing UI

**Features:**
- Tier matrix (Free, Standard, Professional, Enterprise)
- Feature comparison table
- Per-tier card with "Upgrade" button
- FAQ (what features unlock at each tier)
- Contact sales link (for Enterprise)

**Stripe Integration (future):**
- In v1.0, clicking "Upgrade to Standard" → Stripe checkout
- For v0.3, clicking button → links to Stripe with pre-filled tier/email

#### 3.5 Audit Log Viewer
**Files:**
- `core/console/corvin_console/routes/audit_log.py`
- `core/console/corvin_console/web-next/src/pages/AuditLog.tsx`

**Filters:**
- Event type: `license_check`, `license_usage`, `license_renewed`
- Date range
- Feature (brain, tool_forge, skill_forge)

**Columns:**
- Timestamp
- Event Type
- Feature
- Status (granted, denied, quota_exceeded, expired)
- Details (error message if denied)

### Timeline
- **Days 1–3:** Build Console backend routes
- **Days 4–6:** Build React components
- **Day 7:** Integration + user testing

### Success Criteria
- ✅ License info displays correctly
- ✅ Locked features show upsell button
- ✅ Quota exceeded shows reset timer
- ✅ Audit log is filterable and readable

---

## Phase 4: Monitoring & Billing (Week 4)

### Goals
- Add telemetry for feature adoption
- Build operator dashboard (quota usage per tenant)
- Prepare for Stripe integration (v1.0)

### Deliverables

#### 4.1 License Metrics
**File:** `core/metrics/corvin_metrics/license_metrics.py`

**Counters:**
- `license.checks_total` — total checks by result (granted, denied, expired, signature_failed)
- `license.quota_usage` — gauge of daily usage per feature per tenant

**Histograms:**
- `license.signature_verification_time_ms` — latency of Ed25519 verify

**Example Prometheus queries:**
```
# Feature access rate by tier
license.checks_total{result="granted",feature="brain",tier="standard"} (per minute)

# Quota utilization % by feature
(license.quota_usage{feature="tool_forge"} / license.quota_limit{feature="tool_forge"}) * 100

# Signature verification latency
histogram_quantile(0.95, rate(license.signature_verification_time_ms[5m]))
```

#### 4.2 Operator Dashboard (Backend)
**File:** `core/console/corvin_console/routes/admin/license_dashboard.py`

**Endpoints:**
- `GET /api/admin/licenses` — list all licenses (admin only)
  - Columns: tenant_id, tier, expires_at, current_quota_usage
  - Sort: by quota_usage %, expiry date
- `GET /api/admin/licenses/:tenant_id/usage` — detailed usage for tenant
  - 30-day trend per feature
- `GET /api/admin/licenses/expiring` — licenses expiring in 7 days

#### 4.3 Operator Dashboard (Frontend)
**File:** `core/console/corvin_console/web-next/src/pages/Admin/LicenseDashboard.tsx`

**Views:**
1. **Overview**
   - Total tenants by tier (pie chart)
   - Avg quota utilization % per feature (bar chart)
   - Licenses expiring in 7 days (table)

2. **Top Quota Consumers**
   - Table: tenant_id, tier, brain_usage%, tool_forge_usage%, skill_forge_usage%
   - Filter by feature, tier

3. **Renewal Reminders**
   - List tenants expiring in 7, 14, 30 days
   - One-click "send renewal email" button

#### 4.4 Billing Integration Prep
**File:** `core/billing/corvin_billing/stripe_integration.py` (skeleton)

**Scope (v0.3):**
- Stripe webhook receiver (for v1.0 auto-renewal)
- License issuance via Stripe customer metadata

**Scope (v1.0):**
- Automatic renewal webhook
- Usage metering API (for overage pricing)

#### 4.5 End-to-End Testing
**File:** `tests/integration/test_licensing_e2e.py`

**Test scenarios:**
1. Free → Standard upgrade → features unlock
2. Tool Forge quota exhaustion → wait 24h → quota reset
3. License expiry → feature denied
4. Multi-tenant isolation (tenant A's usage doesn't affect B)
5. Signature tampering → license rejected
6. Audit trail records all license events

### Timeline
- **Days 1–2:** Implement metrics + Prometheus integration
- **Days 3–4:** Build operator dashboard (backend + frontend)
- **Days 5–7:** E2E testing + documentation

### Success Criteria
- ✅ Metrics exported to Prometheus
- ✅ Admin dashboard shows licenses & quota usage
- ✅ E2E tests pass
- ✅ Operator manual is updated

---

## Testing Summary

### Unit Tests (Phase 1–2)
- License key immutability, serialization
- Signature verification (valid, tampered, unknown key)
- Quota increment & daily reset
- Fail-closed behavior (missing, expired, invalid)

### Integration Tests (Phase 2–3)
- Full flow: issue → install → verify → access feature
- Quota exhaustion → wait 24h → reset
- Multi-tenant isolation
- Audit trail recording

### E2E Tests (Phase 3–4)
- CLI: issue → install → feature works
- Console: upgrade → feature unlock → quota management
- Dashboard: view usage, see expiring licenses

### Performance Tests (Phase 4)
- Signature verification latency: <10ms (p95)
- Quota check latency: <5ms (p95)
- Cache hit rate: >95%

---

## Deployment Strategy

### v0.3-rc1 (Pre-Release)
- Deploy L48–L51 gates
- All tiers default to "free" (no licenses issued)
- Brain, Forge, plugins disabled for all customers
- Internal testing only

### v0.3-rc2 (Beta)
- Issue licenses to beta testers (Tier A/B)
- Verify quota metering, upsell messages
- Public beta: 20% of installs
- Gather feedback

### v0.3.0 (GA)
- Licensing enabled by default
- Tier A/B/C available for purchase (Stripe integration in v1.0)
- Automatic renewal (webhook-based) in v1.0

### v1.0 (Q4 2026)
- Enterprise tier + custom support
- Automatic renewal via Stripe
- Usage analytics dashboard
- Overage pricing (optional)

---

## Risk Mitigation

### Risk: License File Corruption
**Mitigation:** Signature verification fails fast; operator can re-issue license

### Risk: Redis Quota Cache Loss
**Mitigation:** Dual-layer check (DB query on Redis miss); worst-case: quota check is slower but still works

### Risk: Key Rotation Complexity
**Mitigation:** Multi-key validator; public keys updated via config, no code changes needed

### Risk: Quota Reset Delay
**Mitigation:** Redis TTL is automatic; no cron jobs needed; if Redis unavailable, deny access (fail-closed)

### Risk: Stripe Webhook Failures (v1.0)
**Mitigation:** Manual license renewal CLI as fallback; operator can re-issue licenses

---

## Documentation

### Operator Docs
- `docs/operator/license-administration.md` — how to issue, install, renew licenses
- `docs/operator/license-dashboard.md` — how to view quota usage, expiring licenses
- `docs/compliance/licensing-audit.md` — how licenses are audited

### Developer Docs
- `docs/development/licensing-architecture.md` — (published as part of Phase 1)
- `docs/development/tier-matrix.md` — (published as part of Phase 1)
- ADR-0363 — Licensing Architecture for Brain v0.2 + Forge Features

### User Docs
- `docs/user/pricing.md` — tier overview, feature matrix, pricing
- `docs/user/upgrading.md` — how to upgrade from Free to Standard
- `docs/user/quotas.md` — quota limits, reset times, overage options

---

## Success Metrics

### Adoption
- % of tenants on Standard+ (target: 20% by end of 2026)
- MRR (Monthly Recurring Revenue) growth (target: +50% per quarter)

### Quality
- License check latency <10ms (p95)
- Signature verification success rate >99.9%
- Zero unauditable quota usage

### Compliance
- 100% of quota checks logged to audit trail
- Zero failed signature verifications (would indicate tampering)
- GDPR Art. 6, 30, 32 compliance verified

---

## Dependencies

- PostgreSQL (for license storage)
- Redis (for quota tracking)
- cryptography.hazmat.primitives.asymmetric.ed25519 (for signatures)
- Stripe (v1.0, for billing)

---

## Related

- **ADR-0363:** Licensing Architecture for Brain v0.2 + Forge Features
- **docs/claude-ref/licensing-architecture.md:** Implementation spec
- **docs/claude-ref/tier-matrix.md:** Tier details & pricing
- **ADR-0347:** Brain Subsystem Architecture
- **ADR-0359:** Tool Forge Subsystem Integration
- **ADR-0360:** Skill Forge Subsystem Integration
