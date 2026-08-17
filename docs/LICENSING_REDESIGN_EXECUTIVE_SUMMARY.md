# Brain v0.2 Licensing Redesign: Executive Summary

**Date:** 2026-08-17  
**Prepared by:** Claude Code (research + design)  
**Target Audience:** shumway (maintainer decision)

---

## The Proposal

**Simplify CorvinOS licensing from 4 tiers to 2 tiers:**

| **Current (v0.3)** | **Proposed (v0.3.1+)** |
|---|---|
| Free (Brain disabled, 1 plugin) | **Free ($0):** Brain 10/day, 5 tool forges/day, 2 skill forges/day, 1 plugin |
| Standard ($99/mo): Brain 100/day | **Member ($19/mo):** Brain unlimited, unlimited tool forges/day, unlimited skill forges/day, unlimited plugins |
| Professional ($499/mo): Brain 1000/day | (consolidated) |
| Enterprise (Custom) | **Enterprise (Custom):** Contact sales (same model, negotiated per customer) |

---

## Why This Matters

### Current Problems (4-Tier Model)
1. **Free tier unusable:** Brain is disabled entirely. No way to experience the product.
2. **Pricing gap too wide:** $99/mo → $499/mo is a 5x jump. Mid-market has no option.
3. **Decision paralysis:** 4 choices make it hard to pick. Is a 50-person startup Standard or Professional?
4. **Competitive mismatch:** ChatGPT Plus ($20), GitHub Copilot ($10), Figma Pro ($12). Corvin Standard at $99 feels out of step.

### Proposed Solution (2-Tier Model)
1. **Free tier is usable:** Brain enabled at 10 tasks/day. Enough for experimentation, students, hobbyists.
2. **Simple decision:** "Want unlimited?" Yes → $19/mo. No → stay free.
3. **Aggressive pricing:** $19/mo feels like a subscription (like Netflix), not enterprise software.
4. **Competitive:** $19 is comparable to other SaaS: ChatGPT Plus ($20), Copilot ($10), Linear ($15).

---

## Key Numbers

| **Metric** | **Current (4-Tier)** | **Proposed (2-Tier)** | **Impact** |
|---|---|---|---|
| **Tier count** | 4 | 2 | Simpler ✓ |
| **Free entry barrier** | High (Brain disabled) | Low (Brain 10/day) | More users ✓ |
| **Paid entry price** | $99/mo (Standard) | $19/mo (Member) | 81% cheaper ✓ |
| **Estimated conversion** | ~3% | ~8% | Better ✓ |
| **Est. revenue @ 1000 users** | ~$30k/mo (if 30% buy Standard) | ~$15k/mo (if 8% buy Member) | Lower/mo ✗ |
| **Est. total ecosystem** | Smaller | Larger | Better long-term ✓ |

**Bottom line:** We trade margin per user for volume. 1000 free users + 80 paid = $1520/mo. 100 free users + 15 paid = $1485/mo. At 10,000 users: $152k/mo vs. $148.5k/mo. Volume wins.

---

## What Stays the Same

All **licensing infrastructure** is unchanged:
- ✅ Ed25519 cryptographic signatures (tamper-proof)
- ✅ Fail-closed gates (deny by default)
- ✅ Redis quota metering with daily TTL reset
- ✅ PostgreSQL + Redis cache (hybrid storage)
- ✅ Audit trail integration (all checks logged)
- ✅ Multi-tenant isolation (tenant_id verified)
- ✅ CLI commands (`issue`, `install`, `info`)
- ✅ GDPR/EU AI Act compliance

**Only parameters change:** tier names and quota numbers.

---

## Implementation Effort

| **Task** | **Effort** | **Duration** | **Owner** |
|---|---|---|---|
| **Code changes** (L48–L52 gates, CLI, console) | Medium | 1–2 weeks | Claude Code + team |
| **Database migration** (standard→member, professional→member) | Low | 1 day | DevOps |
| **Testing** (unit, integration, E2E) | Medium | 1 week | QA |
| **Documentation** (tier-matrix, ADR-0363, docs) | Low | 3 days | Claude Code |
| **Customer communication** (emails to Standard/Professional customers) | Low | 2 days | Ops |
| **Console UI updates** (pricing page, settings panel) | Low | 1 week | Frontend |
| **Total** | **Medium** | **4 weeks** | **Team** |

**Timeline:** Fits in v0.3.1 post-GA (Week 1–4 after v0.3.0 ships).

---

## Grandfathering Plan

**Existing Standard ($99/mo) customers:**
- 1 month courtesy at $99/mo (no change)
- Email: "We simplified pricing. Member tier is now $19/mo."
- Auto-renew at $19/mo (saves them $960/year!)
- Expected: 80% stay, 20% cancel

**Existing Professional ($499/mo) customers:**
- 1 month courtesy at $499/mo (no change)
- Email: "We simplified pricing. Member is $19/mo. For bulk/custom, email sales."
- Can choose: (a) Member at $19/mo, or (b) negotiate custom Enterprise contract
- Expected: 50% to Member, 30% cancel, 20% negotiate Enterprise

**New customers:**
- Only see Free and Member tiers (simpler)

---

## Risks & Mitigations

| **Risk** | **Likelihood** | **Impact** | **Mitigation** |
|---|---|---|---|
| **Existing customers upset about $99→$19 "downgrade"** | Low | Medium | Communicate as "simplification" + "better value", email first, offer early choice to stay on old plan |
| **Revenue drop (fewer Standard/Professional, more cheap Member)** | Medium | High | Volume increases offset margin loss; Enterprise contracts still exist (contact sales) |
| **Confused marketing message** | Low | Medium | Clear messaging: "Freemium model like Spotify/Figma" + "Enterprise for custom needs" |
| **Integration with Stripe (v1.0) delayed** | Low | Low | v0.3.1 uses manual renewal (as designed); auto-renewal can slip to v1.0 without blocking launch |
| **Free tier abuse** (users stay at 10 tasks/day forever, never upgrade) | Medium | Low | Quotas are generous but fair; natural growth drives upgrade; monitor conversion rate |

**None are blockers.** This is a **low-risk redesign** of a parameter set, not architectural change.

---

## Competitor Benchmarks

| **Product** | **Free Tier** | **Paid Tier** | **Positioning** |
|---|---|---|---|
| **ChatGPT** | $0 (limited, GPT-3.5) | $20/mo (GPT-4) | Freemium |
| **GitHub Copilot** | $0 (students only) | $10/mo | Freemium (edu focus) |
| **Figma** | $0 (limited files/month) | $12/mo (unlimited) | Freemium |
| **Slack** | $0 (message history, integrations limited) | $7.25/mo (unlimited history) | Freemium |
| **Linear** | $0 (non-profit, open-source) | $15/mo (team) | Freemium (team-focused) |
| **Vercel** | $0 (deployments/month limit) | $20/mo (team, priority support) | Freemium |
| **Corvin (proposed)** | $0 (10 brain tasks/day) | $19/mo (unlimited) | Freemium |

**Corvin Member at $19/mo fits the freemium SaaS playbook.**

---

## Success Metrics (Post-Launch)

**Track these to measure if redesign worked:**

| **Metric** | **Target (v0.3.1)** | **Measurement** |
|---|---|---|
| **Free tier activation** | >20% of new installs | Segment in analytics |
| **Free→Member conversion** | >8% | Stripe data + license logs |
| **Standard customer retention** | >70% (post-grandfathering) | License DB + customer surveys |
| **Net revenue impact** | Neutral or positive by month 3 | Finance dashboard |
| **Ecosystem growth** | >50% YoY user growth | License activations |
| **Support load** | <10% increase | Support tickets, response time |

---

## Decision Required

**Shumway decision point:** Do we approve this 2-tier redesign?

- ✅ **Yes:** Implement in v0.3.1 (4-week plan above)
- ⚠️ **Maybe:** Get feedback from (a) product team, (b) a few existing customers, (c) sales/finance
- ❌ **No:** Keep 4-tier model; defer pricing simplification to v1.0

**Recommendation:** This is a **low-risk, high-impact redesign** that improves user experience (free→paid funnel), reduces decision paralysis, and positions Corvin competitively. **Worth doing in v0.3.1, not waiting for v1.0.**

---

## Deliverables Provided

1. **LICENSING_REDESIGN_2TIER.md** (10-part comprehensive design doc)
   - Existing licensing model review
   - Free vs Member tier design
   - Mapping to Tier A/B/C
   - Revised ADR-0363 embedded
   - Revised tier matrix
   - Pricing recommendation
   - Implementation checklist
   - FAQ & rationale
   - Summary

2. **tier-matrix-2tier-proposed.md** (doc reference version)
   - Ready to replace `docs/claude-ref/tier-matrix.md` if approved

3. **ADR-0363-REVISED-2TIER.md** (ADR format)
   - Ready to commit to Corvin-ADR (supersedes original ADR-0363)

---

## Next Steps

1. **Shumway reviews** this summary + LICENSING_REDESIGN_2TIER.md
2. **Team discussion** (product, sales, finance, engineering)
3. **Decision:** Approve, modify, or defer
4. **If approved:** Create implementation sprint in Week 1 of v0.3.1 cycle
5. **If modified:** Update docs and reiterate

---

## Contact

Questions about this redesign? Review the **10-part design doc** or reach out.

**Document versions:**
- Executive summary: `/docs/LICENSING_REDESIGN_EXECUTIVE_SUMMARY.md` (this file)
- Full design doc: `/docs/LICENSING_REDESIGN_2TIER.md` (comprehensive)
- ADR (proposed): `/docs/ADR-0363-REVISED-2TIER.md` (formal decision)
- Tier matrix (proposed): `/docs/claude-ref/tier-matrix-2tier-proposed.md` (reference)

---

**Last Updated:** 2026-08-17  
**Status:** Ready for decision  
**Prepared by:** Claude Code
