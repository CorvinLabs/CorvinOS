# Licensing Architecture Delivery — Complete Design Package

**Delivered:** 2026-08-17  
**Status:** Ready for Implementation  
**Source:** ADR-0363

---

## Overview

This package contains the complete licensing architecture design for CorvinOS Brain v0.2 and
Forge features. It defines a tier-based licensing model with cryptographic verification,
daily quota metering, and five enforcement gates.

---

## Deliverables

### 1. Architectural Decision Record (ADR)

**Location:** `/home/shumway/projects/Corvin-ADR/decisions/ADR-0363-licensing-architecture-brain-forge.md`

**Contents:**
- Problem statement (why licensing is needed)
- Four-tier pricing model (Free, Standard, Professional, Enterprise)
- Cryptographic license key structure (Ed25519 signatures)
- Five enforcement gates (L48–L52) with fail-closed semantics
- Design rationale (why Ed25519, why daily reset, why fail-closed)
- Integration with GDPR Art. 6, 30, 32 and EU AI Act Art. 5
- Testing and rollout plan
- Acceptance criteria (8 checkboxes)

**Status:** Proposed (ready for technical review)

---

### 2. Implementation Specification

**Location:** `/home/shumway/projects/CorvinOS/docs/claude-ref/licensing-architecture.md`

**Contents:**
- Complete LicenseKey dataclass definition
- LicenseValidator implementation (5 gates + signature verification)
- LicenseStore dual-layer design (PostgreSQL + Redis)
- UsageLogger quota metering (daily reset via TTL)
- Error types (FeatureLocked, QuotaExceeded, PluginLimitExceeded)
- Integration points (Brain, Tool Forge, Skill Forge, Plugins)
- CLI commands (issue, install, info)
- PostgreSQL schema + migrations
- Testing strategy (unit, integration, E2E)
- Monitoring & observability (metrics, logging, dashboards)
- Troubleshooting guide

**Status:** Complete technical specification, ready for coding

---

### 3. Tier Matrix & Pricing

**Location:** `/home/shumway/projects/CorvinOS/docs/claude-ref/tier-matrix.md`

**Contents:**
- Quick reference table (Free, Standard, Professional, Enterprise)
- Detailed tier descriptions with quotas
- Feature progression table (subsystems, forge features, plugins)
- Quota details (brain tasks, tool forge, skill forge, plugins)
- Upgrade/downgrade policy
- Migration paths (Free → Standard → Professional → Enterprise)
- Special cases (nonprofit, trial, volume licensing)
- Pricing examples
- FAQ (20+ common questions)
- Roadmap (v0.3, v1.0, v1.5+ features)

**Status:** Reference guide for sales, product, and operators

---

### 4. Implementation Roadmap

**Location:** `/home/shumway/projects/CorvinOS/docs/implementation/licensing-implementation-roadmap.md`

**Contents:**
- 4-week implementation plan (4 phases)
  - **Phase 1 (Week 1):** Core infrastructure (LicenseKey, LicenseValidator, gates)
  - **Phase 2 (Week 2):** Quota metering & CLI (UsageLogger, license commands)
  - **Phase 3 (Week 3):** Console UI & upsell (settings panel, locked features)
  - **Phase 4 (Week 4):** Monitoring & billing (metrics, operator dashboard)
- Detailed deliverables for each phase
- Success criteria per phase
- Timeline and milestone dates
- Testing summary (unit, integration, E2E, performance)
- Deployment strategy (rc1 → rc2 → GA → v1.0)
- Risk mitigation (6 identified risks)
- Dependencies and related documents

**Status:** Execution plan ready for engineering team

---

## Key Design Decisions

### 1. Cryptographic License Keys

**Decision:** Ed25519 signatures (asymmetric cryptography)

**Rationale:**
- Customers cannot forge a free→standard upgrade
- Offline verification (no server call needed)
- Key rotation possible (multi-key validator)
- Tamper-evident (signature verification failure is logged)

### 2. Daily Quota Reset via Redis TTL

**Decision:** Automatic expiry (no cron jobs, no manual reset)

**Rationale:**
- Scalable (O(1) per-tenant check, O(log n) for reports)
- Precise (each tenant's quota resets independently)
- Fail-closed (if Redis down, quota check denies the request)
- No operational overhead

### 3. Fail-Closed Enforcement

**Decision:** When license verification fails, feature is DENIED (never fail-open)

**Rationale:**
- GDPR Art. 32 compliance (when state is unknown, deny)
- EU AI Act Art. 5 transparency (can't claim feature is licensed if we can't verify)
- Prevents billing fraud (accidental free→paid escalation on outage)
- Maintains audit trail integrity

### 4. Five Enforcement Gates (L48–L52)

**Decision:** Separate gates for Brain, Tool Forge, Skill Forge, Plugin Limit, Quota Meter

**Rationale:**
- Clear separation of concerns
- Easy to audit (each gate logs independently)
- Extensible (new features get new gates)
- Testable (each gate can be unit tested in isolation)

### 5. Hybrid License Storage (PostgreSQL + Redis)

**Decision:** Authoritative DB + caching layer

**Rationale:**
- PostgreSQL is source of truth (survives restarts)
- Redis cache eliminates DB hits (fast path)
- Hybrid is resilient (works if either layer fails)
- Multi-key rotation is possible

---

## Tier Definitions

### Free Tier ($0/month)
- **Brain:** Disabled
- **Quotas:** 1 plugin (builtin only)
- **Target:** Hobbyists, personal projects
- **License:** None (default tier)

### Standard Tier ($99/month)
- **Brain:** Enabled (100 tasks/day)
- **Tool Forge:** 50/day
- **Skill Forge:** 20/day
- **Plugins:** 5 (builtin, vetted, community)
- **Target:** Small teams 2–10 people
- **License:** Manual renewal (v0.3), auto-renewal (v1.0)

### Professional Tier ($499/month)
- **Brain:** Enabled (1000 tasks/day)
- **Tool Forge:** 500/day
- **Skill Forge:** 200/day
- **Plugins:** 20 (all types including custom)
- **Custom Subsystems:** Allowed
- **Target:** Companies 50–500 people
- **License:** Auto-renewal via Stripe

### Enterprise Tier (Custom Pricing)
- **Brain:** Unlimited
- **All Quotas:** Unlimited (negotiable)
- **Dedicated Support:** 24/7 + dedicated engineer
- **Custom Hosting:** On-premises option
- **Target:** Organizations 500+ people, mission-critical
- **License:** Multi-year contract

---

## Integration Architecture

```
┌─────────────────────────────────────────────┐
│ User Request (Brain, Tool Forge, etc.)      │
└──────────────────┬──────────────────────────┘
                   │
                   ▼
        ┌──────────────────────┐
        │ L48: Brain Gate      │ (check_brain_enabled)
        └──────────┬───────────┘
                   │
                   ▼
        ┌──────────────────────┐
        │ L49: Tool Forge Gate │ (check_tool_forge_enabled)
        └──────────┬───────────┘
                   │
                   ▼
        ┌──────────────────────┐
        │ L50: Skill Forge Gate│ (check_skill_forge_enabled)
        └──────────┬───────────┘
                   │
                   ▼
        ┌──────────────────────┐
        │ L51: Plugin Limit    │ (check_plugin_limit)
        └──────────┬───────────┘
                   │
                   ▼
        ┌──────────────────────┐
        │ Feature Execution    │
        └──────────┬───────────┘
                   │
                   ▼
        ┌──────────────────────┐
        │ L52: Quota Meter     │ (record_usage)
        │ → Redis INCR         │
        │ → Audit Trail        │
        └──────────────────────┘
```

---

## Error Handling

### FeatureLocked Exception
```json
{
  "error": "feature_locked",
  "title": "Brain v0.2 requires license",
  "message": "Brain v0.2 is not included in free tier.",
  "feature": "brain",
  "current_tier": "free",
  "required_tier": "standard",
  "upsell_url": "https://corvin.io/pricing?feature=brain"
}
```

### QuotaExceeded Exception
```json
{
  "error": "quota_exceeded",
  "title": "Daily quota exceeded",
  "message": "You've used 50/50 tool forges today.",
  "feature": "tool_forge",
  "used_today": 50,
  "quota": 50,
  "reset_time": "2026-08-18T00:00:00Z",
  "upgrade_tier": "professional"
}
```

### PluginLimitExceeded Exception
```json
{
  "error": "plugin_limit_exceeded",
  "title": "Plugin limit reached",
  "message": "You have 5/5 plugins installed.",
  "current_count": 5,
  "limit": 5,
  "upgrade_to_tier": "professional"
}
```

---

## Compliance Alignment

### GDPR Article 6(1)(f) — Legitimate Interest
- License verification protects Anthropic's IP in Brain v0.2 and Forge systems
- Transparent to customer (displayed in Console Settings)
- Non-discriminatory enforcement

### GDPR Article 30 — Record of Processing
- All license checks recorded in `audit.jsonl` with signature verification status
- Event type: `license_check` (granted, denied, expired, signature_failed)

### GDPR Article 32 — Security
- License keys cryptographically signed (Ed25519)
- Signature verification is fail-closed
- PostgreSQL encrypted at rest (ADR-0037)

### EU AI Act Article 5 — Transparency
- Clear error messages explain which tier unlocks each feature
- License info visible in Console Settings
- Pricing and tier matrix public on website

---

## Rollout Timeline

### v0.3-rc1 (Week 1–2)
- Implement all gates (L48–L52)
- Ship with free tier default (no licenses issued)
- Brain, Forge disabled for all customers
- Internal testing only

### v0.3-rc2 (Week 3–4)
- Issue licenses to beta testers (Tier A/B)
- Verify quota metering, upsells, renewal workflow
- Public beta: 20% of installs
- Gather feedback

### v0.3.0 (Week 5)
- GA release with licensing enabled
- Tier A/B/C available for purchase
- Manual license renewal (CLI)
- Stripe integration (v1.0)

### v1.0 (Q4 2026)
- Automatic renewal via Stripe webhooks
- Usage analytics dashboard
- Enterprise tier with custom support
- On-premises deployment option

---

## Known Limitations (v0.3)

1. **Manual renewal:** Operator must manually reissue license before expiry
2. **No revocation:** License cannot be revoked mid-term (requires manual reissue)
3. **No seat licensing:** Only tenant-level tiers (per-user pricing in v1.0)
4. **No overage pricing:** Quota exceeded = deny, no pay-as-you-go option (v1.0)

---

## File References

| File | Location | Purpose |
|------|----------|---------|
| ADR-0363 | `/Corvin-ADR/decisions/ADR-0363-licensing-architecture-brain-forge.md` | Architectural decision record |
| Implementation Spec | `/CorvinOS/docs/claude-ref/licensing-architecture.md` | Technical implementation guide |
| Tier Matrix | `/CorvinOS/docs/claude-ref/tier-matrix.md` | Pricing and feature matrix |
| Roadmap | `/CorvinOS/docs/implementation/licensing-implementation-roadmap.md` | 4-week execution plan |
| This Summary | `/CorvinOS/docs/LICENSING_ARCHITECTURE_DELIVERY.md` | Package overview |

---

## Next Steps

### For Product Team
1. Review tier definitions and pricing
2. Decide on trial policy (14-day Professional trial?)
3. Plan go-to-market (when to announce licensing)
4. Prepare customer communication (opt-in for beta testers)

### For Engineering Team
1. Review ADR-0363 (technical review + approval)
2. Review implementation spec (coding guidelines)
3. Create GitHub issues for Phase 1 work items
4. Assign implementation owner (suggest one engineer per phase)

### For Compliance Team
1. Review GDPR alignment (Art. 6, 30, 32)
2. Review EU AI Act alignment (Art. 5)
3. Approve audit trail format (license events)
4. Verify key management process (HSM for private key)

### For Sales Team
1. Prepare pricing page (mockup)
2. Prepare customer pitch (tier positioning)
3. Decide on nonprofit discount (50% recommended)
4. Prepare FAQ (expected 20+ questions)

---

## Questions?

For technical details, see:
- **ADR-0363** for architectural decisions
- **licensing-architecture.md** for implementation details
- **tier-matrix.md** for pricing and feature matrix

For roadmap details, see:
- **licensing-implementation-roadmap.md** for 4-week execution plan

For audit and compliance, see:
- **CLAUDE.md** (compliance baseline, §Licensing)
- **ADR-0363** (§Compliance section)

---

**Status:** Ready for review and implementation  
**Last Updated:** 2026-08-17  
**Next Review:** After implementation Phase 1 (Week 2)
