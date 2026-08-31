# Brain v0.2 Licensing Redesign: Free + Member Tiers

**Date:** 2026-08-17  
**Status:** Proposed  
**Deciders:** shumway, Claude

---

## PART 1: EXISTING LICENSING MODEL REVIEW

### Current State (ADR-0363, v0.3-rc1)

CorvinOS today has a **4-tier licensing model**:

| **Tier** | **Cost** | **Brain** | **Max Plugins** | **Tool Forge** | **Skill Forge** | **Use Case** |
|----------|----------|----------|---|---|---|---|
| **Free** | $0 | ❌ disabled | 1 | ❌ | ❌ | Personal hobbyist |
| **Standard (Tier A)** | $99/mo | ✅ 100 tasks/day | 5 | 50/day | 20/day | Small team |
| **Professional (Tier B)** | $499/mo | ✅ 1000 tasks/day | 20 | 500/day | 200/day | Company |
| **Enterprise (Tier C)** | Custom | ✅ Unlimited | unlimited | unlimited | unlimited | Org 500+ |

### Key Findings

#### Complexity Issues
1. **Four tiers create decision paralysis:** Free→Standard costs $99/mo for 100 tasks/day. Standard→Professional is $400/mo more for 10x quota. No middle ground.
2. **Free tier is nearly unusable:** Brain disabled, only 1 builtin plugin, no forge. Forces free users to upgrade immediately.
3. **Pricing has rough edges:** $99/mo Standard feels "cheap," $499/mo Professional feels "expensive." Gap is too wide (5x).
4. **Unclear segment:** Professional is positioned for "50–500 person companies" but starting at $499/mo is steep for most 50-person startups.

#### What Works Today
1. **Fail-closed licensing:** All gates (L48–L52) deny by default until licensed — SOLID.
2. **Cryptographic Ed25519 signatures:** License tampering is prevented — SOLID.
3. **Quota metering via Redis TTL:** Daily resets work automatically — SOLID.
4. **Audit trail integration:** All license checks logged to `audit.jsonl` — SOLID.
5. **Multi-tenant isolation:** Tenant_id isolation verified — SOLID.

#### What's Undecided
1. **Automatic renewal:** v0.3 uses manual renewal (operator manually reissues license). Stripe integration planned for v1.0.
2. **Overage pricing:** Not yet implemented. Planned for v1.0 ($0.01/tool-forge, $0.05/brain-task).
3. **Seat licensing:** Only tenant-level tiers. Per-user pricing deferred to v1.0.

---

## PART 2: FREE VS MEMBER TIER DESIGN

### Simplified 2-Tier Model

The goal is to **simplify to Free + Member** while keeping:
- All 13 Brain v0.2 subsystems available
- Tool Forge and Skill Forge available
- Transparent upsell path (free → member is one click)

#### Free Tier (Tier F)
```yaml
tier: free
cost: $0
features:
  brain: true                          # ← CHANGE: was disabled
    subsystems: 13 (all)              # ← NEW: full access
  tool_forge: true                     # ← CHANGE: was disabled
  skill_forge: true                    # ← CHANGE: was disabled
  plugin_system: true
    max_plugins: 1
  context_engineering: true            # Basic only
  voice_input: true (STT)
  voice_output: true (basic TTS)
  audit_trail: true (GDPR-required)

quotas:
  brain_tasks_per_day: 10             # Generous free allowance
  tool_forge_per_day: 5               # 
  skill_forge_per_day: 2              # 
  max_plugins: 1
  max_workers: 1
  max_task_duration: 30 minutes

billing:
  renewal: automatic (no license key needed)
  no_credit_card: true
  expires: never

use_cases:
  - Personal projects
  - Solo practitioners
  - Experimentation with Brain v0.2
  - Student projects
```

#### Member Tier (Tier M)
```yaml
tier: member
cost: $19/month                         # ← Aggressive: $19 (not $99)
                                        # Justification: Lower barrier to entry
                                        # "Coffee per month" pricing
features:
  brain: true
    subsystems: 13 (all, same as free)
  tool_forge: true
  skill_forge: true
  plugin_system: true
    max_plugins: unlimited
    plugin_types: builtin, vetted, community, custom
  context_engineering: true (advanced)
  voice_guidance: true (v0.3)          # Premium voice features
  voice_input: true (advanced STT)
  voice_output: true (advanced TTS, unlimited voices)
  audit_trail: true

quotas:
  brain_tasks_per_day: unlimited       # ← No daily limit
  tool_forge_per_day: unlimited        # ← No daily limit
  skill_forge_per_day: unlimited       # ← No daily limit
  max_plugins: unlimited
  max_workers: unlimited
  max_task_duration: unlimited
  custom_subsystems: true              # Can write custom Brain subsystems

billing:
  renewal: automatic (Stripe, monthly or annual)
  credit_card: required
  auto_renew: true
  cancel_anytime: true

support:
  channel: email
  response_time: 24–48 hours
  sla: none (best-effort)

use_cases:
  - Small teams (2–10 people)
  - Startups building on CorvinOS
  - Indie SaaS using Brain v0.2
  - Research teams
```

### Why This Works

#### Simpler Decision Tree
- **Free?** "Yes" → use free tier forever (no credit card needed)
- **Want unlimited?** "Yes" → $19/mo member tier
- **Very large org?** Contact sales (still uses Member tier infrastructure, bulk discounts available)

#### Better Free Tier
- **Enables experimentation:** All 13 Brain subsystems available from day 1 (same as paid)
- **Fair quotas:** 10 brain tasks/day lets a solo developer do ~2 complete workflows/day, or 50 mini-tasks
- **No "brain disabled" trap:** Previous free tier made Brain inaccessible; new one doesn't
- **Community value:** Enables open-source projects, students, hobbyists to use the full platform

#### Aggressive Member Pricing
- **$19/month is "impulse buy" territory:** Comparable to a streaming service or SaaS starter tier
- **Removes friction:** $99/mo Standard felt like "enterprise software"; $19/mo feels accessible
- **Sustainable for Anthropic:** 1000 customers at $19/mo = $19k/mo / $228k/year. At $99/mo = $99k/mo / $1.188M/year. We want scale over margin early.

#### Transition Path (Existing Customers)
- **Free-tier customers:** No change (free → free). New quotas (10/5/2) apply immediately.
- **Standard customers ($99/mo):** Auto-map to Member ($19/mo) for 1 month grace period, then confirm renewal. Saves them $960/year.
- **Professional customers ($499/mo):** No change (still Member tier, email sales for bulk/custom pricing).
- **Enterprise customers:** No change (email sales, custom contract).

---

## PART 3: MAPPING TO TIER A/B/C STRUCTURE

### Relationship to Existing Tiers

The **Tier A/B/C terminology** in `docs/claude-ref/layer-plugins.md` refers to **capability tiers**, not **commercial tiers**:

- **Tier A:** Core features (audit, compliance, path-gate, consent)
- **Tier B:** Intermediate features (context engineering, plugins, forge)
- **Tier C:** Advanced features (custom subsystems, custom integrations, on-premises)

The **new Free/Member commercial tiers** are **independent**:
- **Free tier grants:** Tier A + limited Tier B (brain, limited forge, 1 plugin)
- **Member tier grants:** Tier A + full Tier B + Tier C (all subsystems, custom plugins, unlimited everything)

There is **no "Tier D" or "unlicensed Tier"** — both Free and Member are fully licensed.

### License Key Structure (Unchanged)

The cryptographic `LicenseKey` remains the same, but tiers simplify:

```python
@dataclass(frozen=True)
class LicenseKey:
    tenant_id: str
    tier: Literal["free", "member"]          # ← CHANGE: was ["free", "standard", "professional", "enterprise"]
    issued_at: str
    expires_at: str
    features: FrozenDict[str, bool]
    quotas: FrozenDict[str, int]
    public_key_id: str
    signature: bytes
```

### Gate Behavior (Unchanged Logic, Simplified)

| **Layer** | **Gate Name** | **Free Behavior** | **Member Behavior** |
|---|---|---|---|
| L48 | Brain Feature Gate | ✅ Enabled (10 tasks/day) | ✅ Enabled (unlimited) |
| L49 | Tool Forge Gate | ✅ Enabled (5/day) | ✅ Enabled (unlimited) |
| L50 | Skill Forge Gate | ✅ Enabled (2/day) | ✅ Enabled (unlimited) |
| L51 | Plugin Limit Gate | 1 max (builtin only) | unlimited (all types) |
| L52 | Quota Meter | Record usage | Record usage |

All gates remain **fail-closed** — no degradation.

---

## PART 4: REVISED ADR-0363 (2-TIER VERSION)

### Summary

Replace the 4-tier licensing model (Free, Standard $99/mo, Professional $499/mo, Enterprise Custom) with a simplified 2-tier model:
- **Free:** $0, 10 brain tasks/day, 5 tool forges/day, 2 skill forges/day, 1 plugin
- **Member:** $19/month, unlimited everything

This **removes decision paralysis** (4 options → 2), **enables free experimentation** (brain disabled → brain enabled), and **improves pricing efficiency** (per-customer cost lower, volume higher).

### Key Changes

1. **Tier nomenclature:** `free` → `free`, `standard` → `member`, `professional` → (removed), `enterprise` → (removed; contact sales)
2. **Member pricing:** $19/month (vs. $99/Standard, $499/Professional)
3. **Free-tier features:** All 13 Brain subsystems enabled (was disabled)
4. **Free-tier quotas:** 10 brain tasks/day (new), 5 tool forges/day (new), 2 skill forges/day (new)
5. **Grandfathering:** Existing Standard/Professional customers map to Member tier with 1-month courtesy period

### Enforcement Stack (L48–L52)

Identical to current ADR-0363; only quota numbers change:

| **Layer** | **Gate** | **Check** | **Exception** |
|---|---|---|---|
| L48 | Brain Feature Gate | Brain enabled in license | `FeatureLocked("brain")` |
| L49 | Tool Forge Gate | tool_forge enabled + quota | `QuotaExceeded` |
| L50 | Skill Forge Gate | skill_forge enabled + quota | `QuotaExceeded` |
| L51 | Plugin Limit Gate | current_plugins < max_plugins | `PluginLimitExceeded` |
| L52 | Quota Meter | Record usage post-execution | (audit log entry) |

### Quotas

#### Free Tier

| **Feature** | **Daily Limit** | **Monthly (30d)** | **Per-Hour** |
|---|---|---|---|
| Brain Tasks | 10 | 300 | ~0.4 |
| Tool Forge | 5 | 150 | ~0.2 |
| Skill Forge | 2 | 60 | ~0.08 |
| Max Plugins | 1 | — | — |
| Max Workers | 1 | — | — |
| Max Task Duration | 30 min | — | — |

**Overflow behavior:** Deny request with `QuotaExceeded` error + upsell to Member.

#### Member Tier

All quotas **unlimited**:

| **Feature** | **Limit** |
|---|---|
| Brain Tasks/Day | ∞ |
| Tool Forge/Day | ∞ |
| Skill Forge/Day | ∞ |
| Max Plugins | ∞ |
| Max Workers | ∞ |
| Max Task Duration | ∞ |

**Overflow behavior:** None (unlimited).

### User-Facing Errors (Updated)

#### Free Tier → Member Upsell

```python
raise FeatureLocked(
    title="Feature requires Member tier",
    message="Tool Forge (unlimited) is included in Member tier ($19/month).",
    feature="tool_forge",
    current_tier="free",
    required_tier="member",
    upsell_url="https://corvin.io/pricing?feature=tool_forge&tier=member",
)
```

#### Quota Exceeded (Free Only)

```python
raise QuotaExceeded(
    title="Daily quota exceeded",
    message="You've used 5/5 tool forges today. Quota resets at 00:00 UTC.",
    used_today=5,
    quota=5,
    reset_time="2026-08-18T00:00:00Z",
    upgrade_suggestion="Upgrade to Member for unlimited tool forges ($19/month)",
    upgrade_url="https://corvin.io/pricing?tier=member",
)
```

### License Provisioning (CLI, Unchanged)

```bash
corvin-cli license issue \
  --tenant-id "org-acme-corp" \
  --tier "member" \
  --expires-in "1 year" \
  --output license.json
```

#### v0.3 (Manual Renewal)
Operator manually reissues license before expiry.

#### v1.0 (Automatic Renewal)
Stripe webhook automatically reissues license 7 days before expiry.

### Migration & Rollout

#### Phase 1 (Week 1–2): Implement 2-Tier Model
- Update `LicenseKey` to support only `free` and `member` tiers
- Update L48–L51 gates with new quota numbers
- Update `tier-matrix.md` with simplified comparison

#### Phase 2 (Week 3): Grandfathering
- **Free-tier customers:** Quotas auto-update to 10/5/2
- **Standard customers ($99/mo):** Get email offering Member tier at $19/mo, 1-month grace at $99/mo
- **Professional customers ($499/mo):** Get email offering bulk/custom pricing on Member tier

#### Phase 3 (Week 4): GA Release
- Release v0.3.1 with 2-tier model
- Public announcement: "Simplified pricing, free tier now includes Brain v0.2"
- Update all marketing materials, docs, console UI

### Testing

**Unit Tests:**
- License tier validation (only `free` and `member` accepted)
- Quota gates for free tier (10, 5, 2 limits)
- Unlimited quotas for member tier

**Integration Tests:**
- Free → Member upgrade path
- Standard → Member grandfathering
- Quota reset for free tier (daily at UTC midnight)

**E2E Tests:**
- Free user hits quota, sees upsell, clicks "Upgrade" → redirects to Stripe checkout
- Member user has unlimited quota (no quota errors)

---

## PART 5: REVISED TIER MATRIX

### Quick Reference (2-Tier)

| **Feature** | **Free ($0)** | **Member ($19/mo)** |
|---|---|---|
| **Brain v0.2** | ✅ | ✅ |
| **Tool Forge** | ✅ (5/day) | ✅ (unlimited) |
| **Skill Forge** | ✅ (2/day) | ✅ (unlimited) |
| **Voice Guidance** | ❌ (v0.3) | ✅ (v0.3) |
| **Max Plugins** | 1 | unlimited |
| **Plugin Types** | builtin only | builtin, vetted, community, custom |
| **Brain Tasks/Day** | 10 | unlimited |
| **Max Task Duration** | 30 min | unlimited |
| **Subsystems** | 13 (all) | 13 (all) |
| **Custom Subsystems** | ❌ | ✅ |
| **SLA** | None | None (best-effort) |
| **Support** | Community | Email (24–48h) |

### Feature Parity Table

| **Feature** | **Free** | **Member** |
|---|---|---|
| Health Monitor | ✅ | ✅ |
| Context Bridge | ✅ | ✅ |
| Loop Engineer | ✅ | ✅ |
| Orchestrator | ✅ | ✅ |
| Learning Engine | ✅ | ✅ |
| Cost Controller | ✅ | ✅ |
| Safety Validator | ✅ | ✅ |
| Strategy Advisor | ✅ | ✅ |
| Tool Forge Subsystem | ✅ (limited) | ✅ (unlimited) |
| Skill Forge Subsystem | ✅ (limited) | ✅ (unlimited) |
| Forged Tool API | ✅ | ✅ |
| Forged Skill API | ✅ | ✅ |
| Hub | ✅ | ✅ |
| Custom Brain Subsystems | ❌ | ✅ |
| Audit Trail | ✅ | ✅ |
| Context Engineering | ✅ (basic) | ✅ (full) |
| Plugin Registry | ✅ | ✅ |
| Voice Input (STT) | ✅ | ✅ |
| Voice Output (TTS) | ✅ (basic) | ✅ (advanced) |

### Use Case Examples

#### Free Tier
- Solo developer learning CorvinOS
- Student project using Brain v0.2
- Hobbyist building personal AI tools
- Open-source project with small team
- **Typical usage:** 5–10 brain tasks/day, a few tool forges, 1 plugin

#### Member Tier ($19/month)
- Small team (2–10 people) building with CorvinOS
- Startup using Brain v0.2 as core infrastructure
- SaaS product using Skill Forge for user customization
- Research lab with 5–20 researchers
- **Typical usage:** 100+ brain tasks/day, 50+ tool forges/day, multiple plugins

#### Enterprise (Custom)
- Large org (100+ people) requiring:
  - Custom SLA (99.99% uptime)
  - Dedicated support engineer
  - On-premises deployment
  - SAML/SSO integration
- **Contact sales:** sales@corvin.io

---

## PART 6: PRICING RECOMMENDATION

### Proposed Pricing Strategy

#### Free Tier
- **Cost:** $0 (always free)
- **Sustainability:** Supported by:
  - Low server cost (10 brain tasks/day average is light)
  - Conversion funnel (some % of free users upgrade to Member)
  - "Loss leader" (attracts users, builds community)

#### Member Tier
- **Cost:** **$19/month** (recommended, vs. $99 Standard)
- **Rationale:**
  1. **Lower barrier to entry:** $19 feels like "trying something new" vs. $99 = "big commitment"
  2. **Volume play:** Anthropic wants scale. 1000 users @ $19 = $228k/year. 100 users @ $99 = $119k/year. At 10,000 users, difference is $2.28M vs. $1.19M.
  3. **Comparable to other SaaS:** 
     - ChatGPT Plus: $20/month
     - GitHub Copilot: $10/month
     - Vercel Pro: $20/month
     - Linear: $15/month (team plan)
  4. **Psychological pricing:** $19 feels like a subscription (recurring); $99 feels like software (one-time big purchase)

#### Alternative Pricing Options

| **Tier** | **Option A (Conservative)** | **Option B (Recommended)** | **Option C (Aggressive)** |
|---|---|---|---|
| Free | $0 | $0 | $0 |
| Member | $29/month | **$19/month** | $9/month |
| **Rationale** | Mid-market focus; higher margin | Consumer-friendly; volume play | Capture all users; lose margin |
| **Estimated ARR (1000 customers)** | $348k | **$228k** | $108k |
| **Breakeven point (server cost)** | ~50 active | ~75 active | ~150 active |
| **Upgrade rate from free** | ~5% | ~8% | ~15% |

**Recommendation:** Go with **Option B ($19/month)**. It optimizes for scale and has proven successful in the SaaS market.

### Grandfathering Existing Customers

**Standard tier customers ($99/mo):**
- Automatic 1-month courtesy at $99/mo
- Week 2: Email offering Member at $19/mo (81% discount)
- After 1 month: Renew at $19/mo (if they don't opt into higher tier)
- **Expected outcome:** ~80% migrate to Member, 20% cancel (no Enterprise need yet)

**Professional tier customers ($499/mo):**
- Automatic 1-month courtesy at $499/mo
- Week 2: Email offering:
  - Member at $19/mo (96% discount) + note "Email sales@corvin.io for bulk pricing"
  - OR custom contract negotiation
- **Expected outcome:** ~50% migrate to Member + email sales, 30% cancel, 20% negotiate custom

**Enterprise customers:**
- No change (already on custom contracts)
- Email: "Clarification: Enterprise is now handled as custom contract on Member tier infrastructure"

---

## PART 7: SIDE-BY-SIDE COMPARISON

### v0.3 Current (4-Tier) vs. Proposed (2-Tier)

```
CURRENT (ADR-0363 as-is)

Free       $0       Brain: disabled      1 plugin    10/day tasks?
Standard   $99/mo   Brain: 100/day       5 plugins   50/day TF, 20/day SF
Prof       $499/mo  Brain: 1000/day      20 plugins  500/day TF, 200/day SF
Enterprise Custom   Brain: unlimited     ∞ plugins   unlimited TF, SF


PROPOSED (2-Tier Redesign)

Free       $0       Brain: 10/day        1 plugin    5/day TF, 2/day SF
Member     $19/mo   Brain: unlimited     ∞ plugins   unlimited TF, SF


DIFFERENCES

1. Tier count: 4 → 2
2. Free Brain: disabled → enabled (10/day)
3. Free Forge quotas: 0 → 5/2 (TF/SF)
4. Standard eliminated → Member ($19/mo, was $99/mo)
5. Professional eliminated → Member (same tier, email for bulk)
6. Enterprise unchanged (contact sales, custom contract)
```

---

## PART 8: IMPLEMENTATION CHECKLIST

### Code Changes (Core)

- [ ] Update `LicenseKey.tier` enum: `["free", "standard", "professional", "enterprise"]` → `["free", "member"]`
- [ ] Update `LicenseValidator.check_brain_enabled()`: free tier now checks 10/day quota (not disabled)
- [ ] Update `LicenseValidator.check_tool_forge_enabled()`: free tier checks 5/day quota
- [ ] Update `LicenseValidator.check_skill_forge_enabled()`: free tier checks 2/day quota
- [ ] Update `LicenseStore.default_free()`: set quotas to {brain_tasks: 10, tool_forge: 5, skill_forge: 2}
- [ ] Remove `standard`, `professional` tier paths from gates (consolidate into single `member` path)
- [ ] Update error messages (upsell to "Member tier" instead of "Standard tier")

### CLI Changes

- [ ] Update `corvin-cli license issue --tier` accepted values: remove `standard`, `professional`
- [ ] Update `corvin-cli license info` output to show "Free" or "Member" only
- [ ] Update help text / docs in CLI

### Console UI Changes

- [ ] Update Settings → Licensing panel to show only "Free (current)" or "Upgrade to Member"
- [ ] Update pricing table on `/console/pricing` page to show 2-tier matrix
- [ ] Update upsell modals to reference "Member" instead of "Standard/Professional"
- [ ] Add grandfathering message for existing Standard/Professional customers

### Documentation Changes

- [ ] Update `/docs/claude-ref/tier-matrix.md` with 2-tier comparison
- [ ] Update `/docs/claude-ref/licensing-architecture.md` with new `LicenseKey` schema
- [ ] Update ADR-0363 with 2-tier decision (supersede current 4-tier version)
- [ ] Update CLAUDE.md licensing section if needed
- [ ] Update `/docs/implementation/licensing-implementation-roadmap.md` with migration plan

### Testing Changes

- [ ] Unit test: free tier with 10 brain tasks/day quota
- [ ] Unit test: member tier with unlimited quotas
- [ ] Unit test: signature verification works for both tiers
- [ ] Integration test: free → member upgrade flow
- [ ] Integration test: standard/professional → member grandfathering
- [ ] E2E test: free user hits quota, sees Member upsell
- [ ] E2E test: member user has no quota errors

### Database Migration

- [ ] Schema: no changes needed (tier is enum, already exists)
- [ ] Migration script: update existing licenses (standard → member, professional → member)
- [ ] Backup: before running migration

### Billing Integration (v1.0)

- [ ] Update Stripe product IDs (remove Standard/Professional, create Member)
- [ ] Update webhook handlers for Member tier
- [ ] Update renewal logic for $19/month billing cycle

---

## PART 9: FAQ & RATIONALE

**Q: Why enable Brain in Free tier? Wasn't it disabled intentionally?**

A: Free tier had Brain disabled to force upgrade to Standard ($99/mo). But that created a "cliff" — free tier was nearly unusable. Enabling Brain with modest quotas (10 tasks/day) makes free tier valuable for hobbyists while still creating clear upgrade path (if you want unlimited, pay $19/mo). This is the "freemium" model (Spotify, Figma, etc.).

**Q: Why $19/month and not $29 or $99?**

A: Psychological pricing. $19 is in the "subscription" tier (like Netflix, Spotify, GitHub Copilot). $99 is the "software purchase" tier (feels like enterprise). $19 optimizes for volume (more users paying) vs. margin (fewer users paying more). Anthropic likely wants both, but volume is better for ecosystem.

**Q: What about Tier A/B/C in `layer-plugins.md`? Are we renaming that?**

A: No. Tier A/B/C are **capability tiers** (what features can be built), not **commercial tiers** (what you pay for). They're orthogonal:
- Free tier includes Tier A + limited Tier B
- Member tier includes Tier A + full Tier B + Tier C
Both remain in place.

**Q: What if someone has Standard tier with 100 brain tasks/day quota and we downgrade them to 10?**

A: We don't. Grandfathering means:
1. Standard customer remains at 100/day for 1 month (courtesies period)
2. Email: "We simplified pricing. Member tier is now $19/mo (was $99/mo) with unlimited quota."
3. After 1 month: Auto-renew at $19/mo with unlimited quota (upgrade!)

**Q: Will we lose money on this pricing change?**

A: Probably in the short term (Standard at $99/mo → Member at $19/mo = 81% discount). But:
- Volume likely increases (lower price = more customers)
- Customer lifetime value might improve (easier to upgrade to bulk/enterprise)
- Competitive positioning improves (pricing becomes comparable to ChatGPT Plus)

**Q: When do we implement this?**

A: Recommend Week 1–2 of v0.3.1 cycle (parallel to other licensing bug fixes). Ready for GA in Week 4.

---

## PART 10: SUMMARY

### Existing Model (ADR-0363 Current)
- **4 tiers:** Free (brain disabled), Standard ($99/mo), Professional ($499/mo), Enterprise (custom)
- **Problem:** Complex decision tree, free tier unusable, pricing gaps too wide
- **Strength:** Fail-closed licensing, cryptographic security, quota metering, audit integration all solid

### Proposed Model (This Redesign)
- **2 tiers:** Free (10 tasks/day, $0), Member (unlimited, $19/mo)
- **Benefit:** Simpler decision tree, free tier useful for experimentation, lower barrier to membership
- **Same strength:** All compliance/security mechanisms unchanged

### Estimated Impact
| **Metric** | **Current (4-tier)** | **Proposed (2-tier)** | **Change** |
|---|---|---|---|
| Customer acquisition friction | High ($99 entry) | Low ($0 entry) | ↓ Better |
| Upgrade friction | Medium (multiple choices) | Low (Free → Member only) | ↓ Better |
| Estimated free→paid conversion | ~3% | ~8% | ↑ +5pp |
| Avg revenue per customer | High ($500+) | Medium ($19 × 8%) | ↓ Lower |
| Volume (customers) | Lower | Higher | ↑ Better |
| Ecosystem health | Medium | Higher | ↑ Better |

---

## DELIVERABLES

✅ **Part 1:** Existing licensing model review (4-tier → 2-tier complexity issues identified)  
✅ **Part 2:** Free vs Member tier design (with quotas and use cases)  
✅ **Part 3:** Mapping to Tier A/B/C (capability tiers remain orthogonal)  
✅ **Part 4:** Revised ADR-0363 (2-tier decision document)  
✅ **Part 5:** Revised tier matrix (simplified comparison)  
✅ **Part 6:** Pricing recommendation ($19/month Member tier, with alternatives)  
✅ **Part 7:** Side-by-side comparison (current vs. proposed)  
✅ **Part 8:** Implementation checklist (code, CLI, console, docs, tests, DB, billing)  
✅ **Part 9:** FAQ & rationale (addresses key questions)  
✅ **Part 10:** Summary (executive overview)  

---

**Next step:** Present this to shumway + team for decision. If approved, proceed with ADR-0363 revision + implementation in Week 1 of v0.3.1 cycle.
