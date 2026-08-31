# Brain v0.2 Licensing Redesign — Complete Documentation Index

**Date:** 2026-08-17  
**Status:** Complete & Ready for Review  
**Prepared by:** Claude Code

---

## Overview

This is a complete redesign of CorvinOS licensing from the 4-tier model proposed in ADR-0363 (original) to a simplified 2-tier model optimized for user acquisition and long-term ecosystem growth.

**Core proposal:** 
- Simplify from **Free + Standard + Professional + Enterprise** (4 tiers)
- To **Free + Member + Enterprise** (2 tiers, with Enterprise as custom)
- Free tier: $0, all 13 Brain subsystems enabled at limited quota (10 tasks/day, 5 tool forges/day, 2 skill forges/day)
- Member tier: $19/month, unlimited everything

---

## Documents (In Reading Order)

### 1. **LICENSING_REDESIGN_EXECUTIVE_SUMMARY.md** ← **START HERE**
**Purpose:** 2-page summary for decision-makers  
**Contents:**
- The proposal (side-by-side comparison)
- Why it matters (current problems, proposed solution)
- Key numbers (pricing, volume, revenue impact)
- What stays the same (licensing infrastructure)
- Implementation effort (4 weeks)
- Grandfathering plan (how to handle existing customers)
- Risks & mitigations
- Competitor benchmarks
- Success metrics
- Decision required (recommendation: approve in v0.3.1)

**Read this if:** You need a quick overview and recommendation before diving deeper

**Time:** ~5 minutes

---

### 2. **LICENSING_REDESIGN_2TIER.md** ← **COMPREHENSIVE DESIGN**
**Purpose:** Full 10-part design document with all context and rationale  
**Contents:**
- **Part 1:** Existing licensing model review (what's in ADR-0363 today)
- **Part 2:** Free vs Member tier design (detailed quotas, use cases, costs)
- **Part 3:** Mapping to Tier A/B/C structure (explains relationship to capability tiers)
- **Part 4:** Revised ADR-0363 (2-tier version)
- **Part 5:** Revised tier matrix (simplified comparison)
- **Part 6:** Pricing recommendation ($19/month, with alternatives)
- **Part 7:** Side-by-side comparison (old 4-tier vs. new 2-tier)
- **Part 8:** Implementation checklist (code, CLI, console, docs, tests, DB, billing)
- **Part 9:** FAQ & rationale (addresses key questions)
- **Part 10:** Summary (executive overview)

**Read this if:** You want full context, rationale for every decision, and implementation details

**Time:** ~30 minutes

---

### 3. **ADR-0363-REVISED-2TIER.md** ← **FORMAL DECISION RECORD**
**Purpose:** ADR format (to commit to Corvin-ADR repo)  
**Contents:**
- Executive summary of changes
- Context (original 4-tier problems)
- Decision (2-tier model details)
- Rationale (why this approach)
- Alternatives considered & rejected
- Implementation sequence (4 phases)
- Testing strategy (unit, integration, E2E)
- Compliance (GDPR, EU AI Act)
- Rollout plan (v0.3.1 → v1.0+)
- Future enhancements
- Known limitations
- Q&A
- Acceptance criteria

**Read this if:** You need the formal decision record or want to commit this to Corvin-ADR

**Time:** ~15 minutes

---

### 4. **tier-matrix-2tier-proposed.md** ← **REFERENCE DOCUMENTATION**
**Purpose:** Reference doc to replace/supplement `docs/claude-ref/tier-matrix.md`  
**Contents:**
- Quick reference (2-tier comparison table)
- Free tier details (features, quotas, use cases)
- Member tier details (features, quotas, use cases)
- Feature progression tables (by tier)
- Quota details (daily, monthly, per-hour)
- Upgrade/downgrade policy
- Migration paths
- Special cases (nonprofit, trial licenses)
- FAQ
- Roadmap (features by version)
- Comparison: old 4-tier vs. new 2-tier
- Why this works better

**Read this if:** You're implementing the changes or documenting for customers

**Time:** ~10 minutes

---

## Key Findings

### Current State (ADR-0363 Original, v0.3)

| **Aspect** | **Finding** |
|---|---|
| **Tier structure** | 4 tiers: Free, Standard ($99/mo), Professional ($499/mo), Enterprise (Custom) |
| **Free tier** | Brain disabled, 1 plugin only, no forge — nearly unusable |
| **Paid entry** | $99/mo (Standard) — feels expensive for small teams |
| **Pricing gaps** | $99→$499 is 5x jump (Standard→Professional) — no mid-market option |
| **Competitor positioning** | Corvin at $99+ vs. ChatGPT Plus ($20), Copilot ($10), Figma ($12) — out of step |
| **Licensing infrastructure** | **Solid:** Ed25519 signatures, fail-closed gates, quota metering, audit integration all work well |

### Proposed Solution (2-Tier Redesign)

| **Aspect** | **Change** |
|---|---|
| **Tier structure** | 2 tiers: Free, Member ($19/mo) + Enterprise (Custom) |
| **Free tier** | Brain enabled at 10 tasks/day, 5 tool forges/day, 2 skill forges/day — usable for experimentation |
| **Paid entry** | $19/mo (Member) — feels like a subscription (like Spotify, not enterprise software) |
| **Pricing gaps** | $0→$19 is clear binary choice (yes/no), no confusion |
| **Competitor positioning** | Corvin Member at $19/mo in line with ChatGPT Plus ($20), Linear ($15), other SaaS |
| **Licensing infrastructure** | **Unchanged:** All existing security/audit mechanisms remain identical |

### Impact Analysis

| **Metric** | **Current** | **Proposed** | **Outcome** |
|---|---|---|---|
| **Decision complexity** | High (4 options) | Low (2 options) | ✓ Simpler |
| **Free-tier usability** | Low (Brain disabled) | High (Brain at 10/day) | ✓ Better UX |
| **Paid entry friction** | High ($99/mo) | Low ($19/mo) | ✓ Higher conversion |
| **Margin per customer** | High ($99/mo) | Low ($19/mo) | ✗ Lower |
| **Volume potential** | Low | High | ✓ Better |
| **Long-term revenue** | Medium | High (at scale) | ✓ Better |
| **Ecosystem health** | Medium | High | ✓ Better |

---

## Implementation Plan

### Phase 1: Code Changes (Week 1–2)
- Update `LicenseKey.tier` enum: `free`, `member` only
- Update L48–L52 gates with new quota numbers
- Update CLI: accept `--tier free|member`
- Update error messages to reference "Member tier ($19/month)"

### Phase 2: Console UI (Week 2–3)
- Update Settings → Licensing panel
- Update pricing page with 2-tier comparison
- Add "Upgrade to Member" upsell in errors
- Add grandfathering message for existing customers

### Phase 3: Testing & Docs (Week 3–4)
- Unit tests: free tier 10/5/2 quotas, member unlimited
- Integration tests: free→member upgrade, quota reset
- E2E tests: quota exceeded → upsell → Stripe checkout
- Update `tier-matrix.md`, `licensing-architecture.md`, ADR-0363

### Phase 4: Migration & GA (Week 4)
- Database migration: standard→member, professional→member
- Send grandfathering emails
- Release v0.3.1 with 2-tier licensing

---

## Grandfathering Strategy

### Standard ($99/mo) Customers
- **Month 1:** No change (stay at $99/mo)
- **Month 2+:** Auto-renew at $19/mo (unless manually change billing)
- **Expected:** 80% stay at new Member price (save $960/year), 20% cancel

### Professional ($499/mo) Customers
- **Month 1:** No change (stay at $499/mo)
- **Month 2+:** Offered Member ($19/mo) + bulk/custom pricing option
- **Expected:** 50% to Member, 30% cancel, 20% email sales for custom contract

### New Customers
- Only see Free and Member tiers (simpler choice)

---

## Risk Assessment

| **Risk** | **Likelihood** | **Impact** | **Mitigation** |
|---|---|---|---|
| Customer backlash (confused about $99→$19 "downgrade") | Low | Medium | Communicate as "simplification", email first, offer transition option |
| Revenue drop initially | Medium | High | Monitor closely; volume should offset margin loss by Q2 |
| Stripe integration delays (v1.0) | Low | Low | Use manual renewal in v0.3.1; auto-renewal can slip to v1.0 |
| Free tier abuse | Medium | Low | Monitor conversion rate; adjust quotas if needed |

**Recommendation:** All mitigable. **Proceed with implementation.**

---

## Success Criteria

**Post-launch, measure these to validate the redesign worked:**

1. **Free-tier adoption:** >20% of new installs (up from ~0% in 4-tier model with disabled Brain)
2. **Free→Member conversion:** >8% (industry freemium average is 2–5%, so 8% is ambitious but achievable)
3. **Standard customer retention:** >70% after grandfathering
4. **Net revenue:** Neutral or positive by month 3 of v0.3.1 (volume offset margin loss)
5. **Support load:** <10% increase (free users might ask more questions)
6. **Ecosystem growth:** >50% YoY user growth

---

## Files Created (All in `/home/shumway/projects/CorvinOS/docs/`)

1. **LICENSING_REDESIGN_INDEX.md** (this file)
   - Overview, document index, key findings, plan, assessment

2. **LICENSING_REDESIGN_EXECUTIVE_SUMMARY.md**
   - 2-page summary for decision-makers

3. **LICENSING_REDESIGN_2TIER.md**
   - 10-part comprehensive design doc

4. **ADR-0363-REVISED-2TIER.md**
   - Formal ADR (ready to commit to Corvin-ADR)

5. **tier-matrix-2tier-proposed.md**
   - Reference doc (ready to replace `docs/claude-ref/tier-matrix.md`)

---

## Next Steps

### For Shumway (Maintainer)
1. Review **LICENSING_REDESIGN_EXECUTIVE_SUMMARY.md** (5 min)
2. Skim **LICENSING_REDESIGN_2TIER.md** (10 min) for rationale
3. **Decision:** Approve, modify, or defer?
4. If approved: Kick off implementation sprint in v0.3.1 cycle

### For Product/Sales/Finance
1. Review competitor benchmarks in Executive Summary
2. Validate pricing ($19/mo Member) aligns with business goals
3. Confirm grandfathering strategy is acceptable
4. Sign off on success metrics

### For Engineering
1. Use **ADR-0363-REVISED-2TIER.md** as design spec
2. Use **Implementation checklist in LICENSING_REDESIGN_2TIER.md** as task list
3. Reference **tier-matrix-2tier-proposed.md** for quota details
4. Code implementation ~4 weeks (parallel to other v0.3.1 work)

### For QA/Testing
1. Use **Testing strategy in ADR-0363-REVISED-2TIER.md** for test plan
2. Cover unit (quota validation), integration (upgrade/downgrade), E2E (Stripe flow) paths
3. Validate all tiers work (free, member, enterprise) 

---

## Appendix: Pricing Decision Rationale

### Why $19/month?

1. **Psychology:** $19 = subscription tier (like Spotify $15–20, Netflix $15–20, Copilot $10, ChatGPT Plus $20)
2. **Conversion:** Lower price = higher free→paid conversion (industry: 2–5% at $99, potentially 8%+ at $19)
3. **Volume:** 1000 customers @ $19 = $228k/year. 100 @ $99 = $119k/year. We want scale.
4. **Competitive:** Aligns with SaaS pricing norms (Linear $15, Figma $12, GitHub $10 for edu)
5. **Sustainable:** Anthropic's infrastructure cost is low; volume economics work

### Why Freemium (vs. Pure Free or Pure Paid)?

- **Freemium works:** Spotify, Figma, Slack, GitHub all use freemium. Proven model.
- **Free tier enables:** Students, researchers, hobbyists → builds community → some convert to paid
- **No "trial trap":** Free tier is permanent (no expiration), so users can decide at their pace

### Why Not Keep 4 Tiers?

- **Decision paralysis:** Is a 50-person team Standard or Professional? Hard to say.
- **Pricing appears greedy:** $99→$499 jump feels like price discrimination, not value progression
- **Operational complexity:** 4 tiers = 4 quota sets, 4 support strategies, 4 messaging variants

---

## Questions?

Refer to the **FAQ section in LICENSING_REDESIGN_2TIER.md** (Part 9) for detailed answers.

---

**Prepared by:** Claude Code  
**Date:** 2026-08-17  
**Status:** Complete, awaiting decision  
**Next review:** After shumway decision (approve/modify/defer)
