---
id: ADR-0363 (REVISED)
status: proposed
depends_on: [ADR-0347, ADR-0359, ADR-0360]
related: [ADR-0233, ADR-0243]
supersedes: [ADR-0363 (original 4-tier version)]
commits: []
paths:
  - core/compliance/corvin_compliance_reports/license_validator.py
  - core/compliance/corvin_compliance_reports/license_store.py
  - core/compliance/corvin_compliance_reports/usage_logger.py
  - core/compliance/corvin_compliance_reports/quota_gate.py
  - core/console/corvin_console/routes/license_admin.py
  - core/cli/corvin_cli/commands/license.py
docs:
  - docs/claude-ref/licensing-architecture.md
  - docs/claude-ref/tier-matrix.md
---

# ADR-0363 (REVISED) — Simplified 2-Tier Licensing for Brain v0.2 + Forge

**Status:** Proposed · **Date:** 2026-08-17 · **Deciders:** shumway, Claude

---

## Executive Summary

This is a **revised version of ADR-0363** that simplifies the 4-tier licensing model (Free, Standard, Professional, Enterprise) to a cleaner 2-tier model:

- **Free Tier:** $0, 10 brain tasks/day, 5 tool forges/day, 2 skill forges/day, 1 plugin
- **Member Tier:** $19/month, unlimited everything

**Rationale:**
1. **Reduce decision paralysis:** 4 tiers create confusion; 2 tiers enable clear choice
2. **Enable free experimentation:** Original free tier had Brain disabled (unusable); new free tier enables Brain at limited quota
3. **Lower paid entry:** $99/mo Standard felt expensive; $19/mo Member feels like a subscription (comparable to ChatGPT Plus, GitHub Copilot)
4. **Better economics:** Volume of customers increases at lower price point

**Scope:** This revision supersedes the 4-tier decision in ADR-0363 original. All enforcement mechanisms (L48–L52, signature verification, fail-closed gates) remain unchanged.

---

## Context

CorvinOS v0.2 ships three premium subsystems:
- **Brain v0.2** (13-subsystem orchestration, ADR-0347)
- **Tool Forge** (runtime tool generation, ADR-0359)
- **Skill Forge** (runtime skill generation, ADR-0360)

### Original ADR-0363 Approach (4-Tier)

The original ADR proposed:
- **Free:** Brain disabled, 1 plugin, no forge
- **Standard ($99/mo):** Brain enabled (100 tasks/day), 5 plugins, 50 tool forges/day, 20 skill forges/day
- **Professional ($499/mo):** Brain enabled (1000 tasks/day), 20 plugins, 500 tool forges/day, 200 skill forges/day
- **Enterprise (Custom):** Unlimited everything

### Problems with 4-Tier Model

1. **Free tier unusable:** Brain disabled entirely; only 1 plugin allowed. Forces immediate upgrade (no true "free" experience).
2. **Pricing cliff:** $99/mo Standard to $499/mo Professional = 5x jump for 10x quota increase. Mid-market ($150–250) has no option.
3. **Decision complexity:** 4 options create analysis paralysis. Which tier for a 10-person team? Hard to say.
4. **Competitive positioning:** ChatGPT Plus = $20/mo, Figma Pro = $12/mo, GitHub Copilot = $10/mo. Corvin Standard at $99/mo felt out of step.

### Why Revise Now

The licensing system (Ed25519 signatures, fail-closed gates, quota metering, audit integration) is sound and complete. Only the **tier structure and pricing** need simplification. This is a **low-risk revision** that doesn't touch infrastructure, just parameters.

---

## Decision

Implement a **simplified 2-tier licensing system** with identical enforcement mechanisms but cleaner tier structure and better free-tier user experience.

### Tier Matrix (Revised)

| **Tier** | **Cost** | **Brain** | **Tool Forge** | **Skill Forge** | **Max Plugins** | **Max Workers** | **Use Case** |
|----------|----------|----------|---|---|---|---|---|
| **Free (F)** | $0 | 10 tasks/day | 5/day | 2/day | 1 | 1 | Hobbyist, student, solo dev |
| **Member (M)** | $19/mo | unlimited | unlimited | unlimited | unlimited | unlimited | Startup, team, SaaS |
| **Enterprise (E)** | Custom | unlimited* | unlimited* | unlimited* | unlimited* | unlimited* | Large org, custom needs |

*Enterprise is a custom contract built on Member tier infrastructure with dedicated support.

### License Key (Unchanged Structure, Simplified Enum)

```python
@dataclass(frozen=True)
class LicenseKey:
    tenant_id: str
    tier: Literal["free", "member"]      # ← CHANGE: only 2 values
    issued_at: str                        # ISO 8601
    expires_at: str                       # ISO 8601
    features: FrozenDict[str, bool]       # {brain: true, tool_forge: true, ...}
    quotas: FrozenDict[str, int]          # {brain_tasks_per_day: 10, ...}
    public_key_id: str
    signature: bytes                      # Ed25519(SHA256(canonical_json))
```

### Enforcement Stack (L48–L52, Unchanged Logic)

| **Layer** | **Gate Name** | **Check** | **Free Behavior** | **Member Behavior** | **Exception** |
|---|---|---|---|---|---|
| L48 | Brain Feature Gate | Brain enabled in license | ✅ 10/day quota | ✅ unlimited | `FeatureLocked` |
| L49 | Tool Forge Gate | tool_forge enabled + quota | ✅ 5/day quota | ✅ unlimited | `QuotaExceeded` |
| L50 | Skill Forge Gate | skill_forge enabled + quota | ✅ 2/day quota | ✅ unlimited | `QuotaExceeded` |
| L51 | Plugin Limit Gate | current_plugins < max_plugins | 1 max | unlimited | `PluginLimitExceeded` |
| L52 | Quota Meter | Record usage post-execution | Daily TTL reset | N/A | (audit log entry) |

All gates remain **fail-closed**: if verification fails, feature is denied.

### Quotas (Simplified)

#### Free Tier

| **Feature** | **Daily** | **Monthly (30d)** |
|---|---|---|
| Brain Tasks | 10 | 300 |
| Tool Forge | 5 | 150 |
| Skill Forge | 2 | 60 |
| Max Plugins | 1 | — |
| Max Workers | 1 | — |
| Max Task Duration | 30 min | — |

#### Member Tier

All quotas **unlimited**.

| **Feature** | **Limit** |
|---|---|
| Brain Tasks/Day | ∞ |
| Tool Forge/Day | ∞ |
| Skill Forge/Day | ∞ |
| Max Plugins | ∞ |
| Max Workers | ∞ |
| Max Task Duration | ∞ |

### Pricing & Billing

#### Free Tier
- **Cost:** $0/month (always free)
- **Renewal:** Automatic (no license key required; default for new tenants)
- **Credit card:** Not required
- **Cancellation:** N/A (always available)

#### Member Tier
- **Cost:** $19/month (or annual option in v1.0, ~15% discount)
- **Renewal:** Automatic (Stripe webhook, v1.0) or manual (operator reissues, v0.3)
- **Credit card:** Required (Stripe)
- **Cancellation:** Anytime (refund prorated to end of billing period)
- **Upgrade trigger:** Free user clicks "Upgrade to Member" in error message or Console
- **Downgrade trigger:** Member user clicks "Downgrade to Free" in Settings (loses unlimited quotas, refund applied)

#### Enterprise Tier
- **Cost:** Custom (contact sales)
- **Typical range:** $500+/month (negotiable per contract)
- **Renewal:** Multi-year contract
- **Support:** Dedicated engineer, 24/7 SLA, custom integrations
- **Trigger:** Email sales@corvin.io for large org, custom SLA, on-premises deployment

### Grandfathering (Migration from 4-Tier)

**Existing Standard ($99/mo) customers:**
- 1-month courtesy at $99/mo (no change)
- Email: "We simplified pricing. Member tier is now $19/mo (unlimited quotas)."
- Auto-renew at $19/mo after 1 month (81% savings!)
- Expected conversion: ~80% to Member, ~20% cancel

**Existing Professional ($499/mo) customers:**
- 1-month courtesy at $499/mo (no change)
- Email: "We simplified pricing. Member tier is now $19/mo. For bulk pricing, email sales@corvin.io."
- Can choose: (a) auto-renew at $19/mo + email sales for bulk, or (b) negotiate custom contract
- Expected conversion: ~50% to Member + sales contact, ~30% cancel, ~20% negotiate Enterprise

---

## Rationale

### Why 2 Tiers Instead of 4?

**Simplicity wins.**
- 4 tiers force operators to choose: Is our 50-person team Standard or Professional? (Gap is 5x in price.)
- 2 tiers force a binary question: Do we want unlimited? (If yes, pay $19/mo. If no, stay free.)
- Real-world SaaS benchmarks (Figma, Slack, GitHub) use 2–3 tiers. 4 is unusual.

### Why Enable Brain in Free Tier?

**User experience and community.**
- Original free tier had Brain disabled, making it nearly unusable for experimentation.
- New free tier enables Brain at 10 tasks/day quota — enough for ~2 workflows/day or 50 mini-tasks.
- This enables students, researchers, hobbyists to experience the full platform.
- Freemium model (Spotify, Figma, Slack) proves that good free tiers drive conversion, not prevent it.

### Why $19/month for Member?

**Psychology and volume.**
- $19 is in the "subscription" category (like Netflix $15–20, GitHub Copilot $10, ChatGPT Plus $20).
- $99 is in the "enterprise software" category (feels expensive, requires approval).
- Lower price = more customers = better ecosystem and network effects.
- 1000 customers @ $19 = $228k/year. 100 customers @ $99 = $119k/year. We want scale.
- Anthropic's incentives are: (a) expand CorvinOS user base, (b) capture Enterprise segment for custom contracts. Member at $19 achieves both.

### Why No Per-Tier Features?

**Alignment with Brain architecture.**
- All 13 Brain v0.2 subsystems are fully available in both Free and Member tiers.
- The only difference is quotas (requests per day), not capabilities.
- This allows free-tier users to learn the full system and migrate to paid without relearning.
- Contrast: in the 4-tier model, Standard and Professional had the same subsystems but different quotas (confusing).

### Why Enterprise Stays Custom?

**One-size-fits-none for large orgs.**
- Enterprise needs (SLA, dedicated support, custom integrations, on-premises) vary per customer.
- Fixed "Enterprise tier" would either be too expensive (if trying to cover all cases) or too cheap (and thus low-margin).
- Keeping Enterprise as "contact sales" lets Anthropic negotiate per customer needs and capture maximum value.

---

## Alternatives Considered

### A1: 3-Tier Model (Free, Starter, Pro)
**Rejected.** Similar to existing 4-tier; same decision paralysis (Is our team Starter or Pro?). Benefits of 2-tier (simplicity) are lost.

### A2: Free Tier with Brain Disabled (Keep Original)
**Rejected.** Original free tier was nearly unusable for experimentation. Defeats the "freemium" goal.

### A3: Member at $29 or $49 Instead of $19
**Considered, rejected.** Higher price reduces conversion (fewer free→paid). $19 optimizes for scale. Margin per customer is lower, but volume is higher. Better long-term positioning.

### A4: "Free" vs "Hobby" vs "Pro" (3 Tiers, Different Names)
**Rejected.** Naming doesn't solve the decision problem. 3 tiers still creates paralysis.

---

## Implementation Sequence

### Phase 1 (Week 1–2): Code Changes
- Update `LicenseKey.tier` enum: remove `standard`, `professional`; keep `free`, `member`
- Update `default_free()`: set quotas to `{brain_tasks: 10, tool_forge: 5, skill_forge: 2}`
- Update L48–L51 gates: remove old tier checks, consolidate into `free` and `member` paths
- Update error messages: "Upgrade to Member tier" instead of "Upgrade to Standard"
- Update CLI: `corvin-cli license issue --tier free|member` (reject `standard`, `professional`)
- Update database: no schema changes needed (tier is enum, already exists)

### Phase 2 (Week 2–3): Console UI
- Update Settings → Licensing panel: show "Free (current)" or "Upgrade to Member"
- Update pricing page: show 2-tier matrix (Free vs Member)
- Update upsell modals: "Unlock unlimited for $19/month"
- Add grandfathering message: "Existing Standard/Professional customers see special offer..."

### Phase 3 (Week 3–4): Documentation & Testing
- Update `docs/claude-ref/tier-matrix.md`: 2-tier comparison
- Update `docs/claude-ref/licensing-architecture.md`: new tier structure
- Update ADR-0363: this revised version
- Add unit tests: free tier with 10/5/2 quotas, member tier with unlimited
- Add integration tests: free → member upgrade, quota reset
- Add E2E tests: free user hits quota, sees upsell, clicks to Stripe checkout

### Phase 4 (Week 4): Migration & GA
- Run migration script: map existing licenses to new tiers
  - `standard` → `member`
  - `professional` → `member`
  - `enterprise` → stays `enterprise` (contact sales)
  - `free` → stays `free` (with new quotas)
- Backup database before running migration
- Send migration emails to affected customers
- Release v0.3.1 with 2-tier licensing enabled
- Announce: "Simplified pricing, free tier now includes Brain v0.2"

---

## Testing

### Unit Tests
- Signature verification (valid, tampered, unknown key) — unchanged
- Quota validation for free tier (10 tasks/day, 5 tool forges/day, 2 skill forges/day)
- Quota validation for member tier (all unlimited)
- Tier enum validation (only `free` and `member` accepted)
- License expiration checks — unchanged

### Integration Tests
- Full flow: issue license (free) → verify signature → check quotas
- Full flow: issue license (member) → verify signature → no quota checks
- Quota reset: free tier hits 10/day, wait 24h, quota resets
- Multi-tenant isolation: tenant A's quota doesn't affect tenant B
- Upgrade path: free → member (license reissued with unlimited quotas)
- Downgrade path: member → free (license reissued with 10/5/2 quotas)

### E2E Tests
- CLI: `corvin-cli license issue --tier free --output free.json` → feature accessible
- CLI: `corvin-cli license issue --tier member --output member.json` → unlimited access
- Console: free user hits brain quota (10/day), sees error message + "Upgrade to Member" button
- Console: free user clicks "Upgrade" → redirects to Stripe checkout (v1.0)
- Console: free user upgrades → license updated → unlimited quotas active
- Audit trail: all license checks appear in `audit.jsonl` with correct tier

---

## Compliance

**GDPR Art. 6(1)(f)** (Legitimate Interest): Licensing protects Anthropic's intellectual property in Brain v0.2 and Forge systems. License verification is lawful use of customer tenant_id.

**GDPR Art. 30** (Record of Processing): License checks are recorded in `audit.jsonl` with signature verification status.

**GDPR Art. 32** (Security): License keys are cryptographically signed; Redis quotas are cached only (not authoritative); PostgreSQL is encrypted at rest.

**EU AI Act Art. 5** (Transparency): Customers receive clear error messages explaining which tier unlocks each feature.

---

## Rollout Plan

### v0.3.1 (Week 1–4, v0.3-post-GA)
- Implement 2-tier licensing (code, CLI, console)
- Migrate existing licenses (standard/professional → member)
- GA release with simplified pricing
- Public announcement: "Freemium model — Brain v0.2 now free to try"

### v1.0 (Q4 2026)
- Automatic renewal (Stripe webhook)
- Usage analytics dashboard
- Overage pricing (optional, $0.01/tool-forge, $0.05/brain-task)
- Tier recommendations ("Based on your usage, you'd save $X/mo with Member tier")
- Annual billing option (15–20% discount vs. monthly)

### v1.5+ (2027)
- Seat-based licensing (per-user pricing)
- Volume licensing (100+ seats, bulk discounts)
- SSO/SAML integration (for Enterprise)
- On-premises deployment option

---

## Future Enhancements (Beyond v0.3.1)

1. **Automatic renewal:** Stripe webhook → auto-issue new license 7 days before expiry
2. **Usage analytics:** Dashboard showing quota consumption, trends, recommendations
3. **Overage pricing:** Pay-as-you-go above quota (opt-in)
4. **Tier recommendations:** "You've used 95% of your monthly quota; Professional would save $X"
5. **Annual billing:** 15–20% discount for 12-month prepayment
6. **Seat licensing:** Per-user pricing for teams
7. **Volume licensing:** Bulk discounts for 100+ users
8. **Custom SLA:** Enterprise contracts with uptime guarantees

---

## Known Limitations

1. **Manual renewal (v0.3):** Operator must manually reissue license before expiry. Automatic renewal via Stripe (v1.0).

2. **Offline validation only:** License must be installed before going offline. No dynamic revocation (feature deletion requires manual license reissue).

3. **No per-user licensing yet:** Only tenant-level tiers. Per-user seat pricing deferred to v1.0.

4. **No overage pricing yet:** Quotas hard-limit (no soft overage). Overage pricing planned for v1.0.

---

## Questions & Answers

**Q: What if my team is currently Standard and I downgrade to Free?**

A: You lose unlimited quotas (reduced to 10 tasks/day). Your current brain tasks, tools, skills are preserved. Quota reset happens daily at UTC midnight. You can re-upgrade anytime by clicking "Upgrade to Member."

**Q: What if my license expires mid-task?**

A: Task fails with `LicenseExpired` exception. You must renew license and resubmit. This is acceptable because v0.3 uses manual renewal; v1.0 automatic renewal prevents this scenario.

**Q: Can I buy overage above quota?**

A: Not in v0.3. Planned for v1.0 ($0.01/tool-forge, $0.05/brain-task). For now, upgrade to Member (unlimited) or contact sales@corvin.io for custom contract.

**Q: What about air-gapped (offline) installs?**

A: License must be installed before disconnecting. Operator can pre-issue licenses for 1-year terms to cover offline deployments.

**Q: How do we prevent license key leaks?**

A: License files contain no secrets (public_key_id and signature are visible by design, making verification auditable). Anthropic's private key is never embedded in the license-issuing service (air-gapped).

**Q: Why Member at $19 and not $29?**

A: Psychology and volume. $19 is subscription-tier pricing (like Netflix, Spotify, ChatGPT Plus). Lower price → more customers → better ecosystem. Anthropic's economics favor scale over margin early. Volume discounts available for Enterprise.

**Q: What happens to my free-tier quota if I upgrade to Member?**

A: Limits are removed. You immediately get unlimited brain tasks, tool forges, skill forges. No "ramping up" period.

**Q: Can I request a custom contract on the free tier?**

A: Contact sales@corvin.io. Enterprise contracts are custom and negotiated per customer needs. If you need Enterprise features (SLA, custom support, on-premises) but don't want the Member monthly fee, discuss with sales.

---

## Acceptance Criteria

- [ ] LicenseKey.tier accepts only `free` and `member` (rejects others)
- [ ] Free tier quotas (10, 5, 2) are enforced at L48–L50
- [ ] Member tier quotas are unlimited (no errors for high usage)
- [ ] Quota resets daily at UTC midnight for free tier
- [ ] License check is recorded in audit.jsonl
- [ ] CLI accepts only `--tier free|member` (rejects `standard`, `professional`)
- [ ] Console UI displays 2-tier pricing table
- [ ] Upsell error messages reference "Member tier ($19/month)"
- [ ] Migration script maps existing licenses correctly (standard→member, etc.)
- [ ] All unit, integration, E2E tests pass
- [ ] Documentation updated (tier-matrix.md, licensing-architecture.md, ADR-0363)
- [ ] Public announcement prepared

---

## Amendment History

None yet (document is new/revised 2026-08-17).

---

## Deciders & Sign-Off

**Proposed by:** Claude Code  
**Date:** 2026-08-17  
**Status:** Awaiting shumway approval  

Once approved by shumway, this supersedes ADR-0363 original (4-tier version) and becomes the authoritative licensing model for v0.3.1+.
