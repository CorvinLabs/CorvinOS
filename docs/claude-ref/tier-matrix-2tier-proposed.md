# Tier Matrix & Pricing Reference (2-Tier Proposed)

**Source:** ADR-0363 (Redesign)  
**Status:** Proposed  
**Last Updated:** 2026-08-17

---

## Quick Reference

| **Feature** | **Free ($0)** | **Member ($19/mo)** |
|---|---|---|
| **Brain v0.2** | ✅ (10 tasks/day) | ✅ (unlimited) |
| **Tool Forge** | ✅ (5/day) | ✅ (unlimited) |
| **Skill Forge** | ✅ (2/day) | ✅ (unlimited) |
| **Voice Guidance** | ❌ (v0.3) | ✅ (v0.3) |
| **Max Plugins** | 1 | unlimited |
| **Plugin Types** | builtin | builtin, vetted, community, custom |
| **Max Workers** | 1 | unlimited |
| **Max Task Duration** | 30 min | unlimited |
| **All 13 Brain Subsystems** | ✅ | ✅ |
| **Custom Subsystems** | ❌ | ✅ |
| **SLA** | None | None (best-effort) |
| **Support** | Community | Email (24–48h) |

---

## Free Tier

**Cost:** $0  
**Target:** Solo practitioners, hobbyists, students, open-source projects  
**License:** No license key required (free tier is default)

### Enabled Features
- **Brain v0.2:** Full 13-subsystem orchestration (quota-limited)
  - Health Monitor
  - Context Bridge
  - Loop Engineer
  - Orchestrator
  - Learning Engine
  - Cost Controller
  - Safety Validator
  - Strategy Advisor
  - Tool Forge Subsystem
  - Skill Forge Subsystem
  - Forged Tool API
  - Forged Skill API
  - Hub

- **Tool Forge:** Dynamic tool generation (5/day)
- **Skill Forge:** Dynamic skill creation (2/day)
- **Plugin System:** 1 builtin plugin (e.g., Console)
- **Context Engineering:** Basic context variables
- **Voice Input:** STT (speech-to-text)
- **Voice Output:** Basic TTS (text-to-speech)
- **Audit Trail:** Yes (GDPR-required)

### Disabled Features
- Voice Guidance (advanced voice features, v0.3+)
- Multiple plugins (max 1 builtin)
- Custom Brain subsystems
- Advanced context bridges (> basic)
- Multi-worker mode (max 1)
- Unlimited task duration (max 30 min)

### Quotas
- `brain_tasks_per_day`: 10
- `tool_forge_per_day`: 5
- `skill_forge_per_day`: 2
- `max_plugins`: 1
- `max_workers`: 1
- `max_task_duration`: 30 minutes

### Quota Reset
- Daily reset at **UTC midnight** (automatic via Redis TTL)
- Reset applies per-tenant independently
- No "grace period" — hard limit at quota

### Error Message (When Quota Exceeded)

```
Brain Task Quota Exceeded

You've used 10/10 brain tasks today. 
Quota resets at 2026-08-18 00:00 UTC (in 23 hours).

→ Upgrade to Member for unlimited ($19/month)
→ Learn More
```

### Use Cases
- Solo developer learning CorvinOS
- Student project
- Open-source project with small team
- Personal research
- Proof of concept / prototype

### Estimated Usage Pattern
- **Tasks/day:** 5–10 (mix of small + medium)
- **Tool forges/day:** 2–5 (creating new tools)
- **Skill forges/day:** 0–2 (creating new skills)
- **Plugins:** 1 (builtin Console)

---

## Member Tier

**Cost:** $19/month  
**Target:** Small teams (2–10 people), startups, indie SaaS, research labs  
**License:** Issued and automatically renewed via Stripe (v1.0); manually renewed (v0.3)

### Enabled Features
- **Brain v0.2:** Full 13-subsystem orchestration (unlimited)
- **Tool Forge:** Unlimited tool generations per day
- **Skill Forge:** Unlimited skill creations per day
- **Plugin System:** Unlimited plugins
  - Builtin plugins
  - Vetted plugins (from Anthropic registry)
  - Community plugins
  - Custom plugins (user-written)
  
- **Context Engineering:** Full Context v2 (all bridges)
- **Voice Guidance:** Yes (v0.3+)
- **Advanced TTS:** Unlimited voices
- **Multi-Worker:** Unlimited concurrent workers
- **Custom Brain Subsystems:** Can extend Brain with custom subsystems

### Quotas (All Unlimited)
- `brain_tasks_per_day`: unlimited
- `tool_forge_per_day`: unlimited
- `skill_forge_per_day`: unlimited
- `max_plugins`: unlimited
- `max_workers`: unlimited
- `max_task_duration`: unlimited

### Quota Reset
- N/A (no quotas)
- Billing cycle: monthly or annual (configurable in v1.0)

### Error Message (If License Expires)

```
License Expired

Your Member tier license expired on 2026-09-17.

Please renew your license to continue using Brain v0.2.

→ Renew Now
→ Downgrade to Free
```

### Support
- **Channel:** Email
- **Response Time:** 24–48 hours
- **SLA:** None (best-effort, no uptime guarantee)
- **Included:** Basic technical support, billing support

### Use Cases
- Small team (2–10 people) building with CorvinOS
- Startup using Brain v0.2 as core infrastructure
- SaaS product using Skill Forge for user customization
- Research lab (5–20 researchers)
- Freelancer / indie developer scaling up
- Open-source project with growing team

### Pricing Examples
- **1 person, 1 month:** $19
- **5-person team, 12 months:** $19 × 12 = $228/year
- **Cost per engineer:** $45.60/year (vs. $1188 for v0.3 Standard tier)

### Estimated Usage Pattern
- **Tasks/day:** 50–500+ (heavy usage)
- **Tool forges/day:** 10–100+ (continuous tool creation)
- **Skill forges/day:** 2–20+ (continuous skill creation)
- **Plugins:** 2–10+ (mix of types)
- **Workers:** 1–5+ (parallel execution)

---

## Feature Progression Table

### Brain Subsystems (All Available in Both Tiers)

| **Subsystem** | **Free** | **Member** |
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

### Forge Features

| **Feature** | **Free** | **Member** |
|---|---|---|
| Tool Forge | 5/day | unlimited |
| Skill Forge | 2/day | unlimited |
| Tool Versioning | ✅ | ✅ |
| Skill Auto-Grading | ✅ | ✅ |
| Tool A/B Testing | ❌ | ✅ |
| Skill Marketplace | ❌ | ✅ |

### Plugin System

| **Feature** | **Free** | **Member** |
|---|---|---|
| Plugin Registry | ✅ | ✅ |
| Max Plugins | 1 | unlimited |
| Builtin Plugins | ✅ | ✅ |
| Vetted Plugins | ❌ | ✅ |
| Community Plugins | ❌ | ✅ |
| Custom Plugins | ❌ | ✅ |
| Private Plugin Registry | ❌ | ✅ |

---

## Quota Details

### Brain Tasks

A "brain task" is one end-to-end orchestration cycle (user request → Brain processes → result).

| **Tier** | **Daily Quota** | **Monthly (30d)** | **Per-Hour** | **Overflow Behavior** |
|---|---|---|---|---|
| Free | 10 | 300 | ~0.4 | Deny request, show upsell |
| Member | unlimited | unlimited | unlimited | (no limit) |

### Tool Forge Requests

A "tool forge request" is one call to generate a tool dynamically.

| **Tier** | **Daily Quota** | **Monthly (30d)** | **Per-Hour** | **Overflow Behavior** |
|---|---|---|---|---|
| Free | 5 | 150 | ~0.2 | Deny request, show upsell |
| Member | unlimited | unlimited | unlimited | (no limit) |

### Skill Forge Requests

A "skill forge request" is one call to create a new skill.

| **Tier** | **Daily Quota** | **Monthly (30d)** | **Per-Hour** | **Overflow Behavior** |
|---|---|---|---|---|
| Free | 2 | 60 | ~0.08 | Deny request, show upsell |
| Member | unlimited | unlimited | unlimited | (no limit) |

### Plugin Count

A plugin is a loaded extension.

| **Tier** | **Max Plugins** | **Builtin Only** | **Vetted** | **Community** | **Custom** |
|---|---|---|---|---|---|
| Free | 1 | ✅ | ❌ | ❌ | ❌ |
| Member | unlimited | ✅ | ✅ | ✅ | ✅ |

### Max Task Duration

How long a single task can run.

| **Tier** | **Max Duration** | **Timeout Behavior** |
|---|---|---|
| Free | 30 minutes | Task cancelled, state saved |
| Member | unlimited | (no timeout) |

### Concurrent Workers

How many tasks can run in parallel.

| **Tier** | **Max Workers** | **Concurrency Model** |
|---|---|---|
| Free | 1 | Sequential (one at a time) |
| Member | unlimited | Parallel within one tenant |

---

## Upgrade / Downgrade Policy

### Upgrading (Free → Member)

1. **Free user clicks "Upgrade to Member"** in Console or error message
2. **Redirected to Stripe checkout** ($19/month or annual)
3. **Payment confirms** → license issued immediately
4. **Features unlock instantly** → unlimited quotas active
5. **Billing:** Charge starts immediately (prorated if mid-month)
6. **No data loss:** All previous brain tasks, tools, skills preserved

### Downgrading (Member → Free)

1. **Member clicks "Downgrade to Free"** in Console Settings
2. **Confirmation modal:** "You'll lose unlimited quotas. Continue?"
3. **Downgrade confirms** → license expires, free tier becomes active
4. **Quotas reduced:** 10 tasks/day, 5 tool forges/day, 2 skill forges/day
5. **Billing:** Prorated credit applied (if applicable)
6. **Plugins reduced:** If > 1 plugin, member must delete excess before downgrade completes

### Cancellation (Member → No License)

1. **Member cancels license** in Console
2. **License expires** at end of current billing period
3. **Default to free tier** (Brain enabled at 10/day quota, 1 plugin)
4. **Data retention:** All audit logs, skills, tools, plugins retained for 90 days (GDPR Art. 17)
5. **Unsubscribe:** Stripe automatically stops charging

---

## Migration Path

### Free → Member
- **Typical trigger:** Team wants more quota
- **Prerequisite:** None (no credit card needed for free)
- **Effort:** Click "Upgrade" → Stripe checkout → license issued → feature unlock
- **Timeline:** Immediate (seconds)
- **Cost:** $19/month (or annual option in v1.0)

### Member → Free (Downgrade)
- **Typical trigger:** Team reducing scope or cost-cutting
- **Prerequisite:** Reduce plugin count if > 1
- **Effort:** Click "Downgrade" → confirm → quotas reduced
- **Timeline:** Immediate
- **Cost:** Becomes $0/month (refund applied if applicable)

### Member → Enterprise (Custom)
- **Typical trigger:** Org needing custom SLA, dedicated support, on-premises
- **Prerequisite:** Multi-year contract negotiation
- **Effort:** Email sales@corvin.io → contract signed → custom license issued
- **Timeline:** 1–4 weeks (sales) + implementation
- **Cost:** Custom (typically $500+/month, negotiable)

---

## Special Cases

### Nonprofit / Educational License

**Availability:** Free and Member tiers  
**Discount:** 50% off Member tier  
**Requirements:**
- 501(c)(3) nonprofit status (US) or equivalent (other countries)
- Accredited educational institution (.edu domain)
- Impact mission (AI research, education, non-commercial use)

**Process:**
1. Apply via [corvin.io/nonprofit](https://corvin.io/nonprofit)
2. Provide proof (IRS 501(c)(3) letter or .edu domain ownership)
3. Discount code issued → apply at checkout → pay $9.50/month

### Trial License

**Availability:** Free tier users  
**Duration:** 14 days  
**Features:** Full Member tier (unlimited everything)  
**No credit card required**

**Process:**
1. Free user clicks "Try Member (free for 14 days)" in Console
2. Trial license issued for 14 days
3. At day 13, reminder email: "Trial ending. Upgrade or downgrade?"
4. After day 14, auto-downgrade to free tier

---

## FAQ

**Q: Can I run two tenants on one Member license?**

A: No. One Member subscription covers one tenant_id. For multi-tenant scenarios (e.g., reseller deploying CorvinOS for multiple customers), contact sales@corvin.io for enterprise licensing.

**Q: What happens if I exceed quota mid-task?**

A: Task fails with `QuotaExceeded` error. Quotas are checked at *request time*, not mid-execution. If you're at 4/5 tool forges and forge one more, the 5th succeeds, then the 6th is denied.

**Q: Can I buy overage above my quota?**

A: Not in v0.3. Planned for v1.0 ($0.01/tool-forge, $0.05/brain-task, etc.). For now, upgrade to Member (unlimited) or contact sales@corvin.io for custom contract.

**Q: What if I downgrade and have too many plugins?**

A: Downgrade is blocked until you manually delete excess plugins. The license validator prevents downgrade if current_plugins > new_max_plugins.

**Q: Is there a month-to-month option?**

A: Yes. Member tier is monthly ($19/mo). Annual option available in v1.0 (typically 15–20% discount).

**Q: Can I cancel anytime?**

A: Yes. Member subscriptions are month-to-month. Cancel anytime; billing stops at end of current month.

**Q: How do quotas reset?**

A: Free tier quotas reset daily at **UTC midnight** (automatic via Redis TTL). Each tenant resets independently. Member tier has no quotas (unlimited).

---

## Roadmap: Features by Version

| **Feature** | **v0.3** | **v1.0** | **v1.5+** |
|---|---|---|---|
| Basic 2-tier pricing | ✅ | ✅ | ✅ |
| Manual license renewal | ✅ | — | — |
| Automatic renewal (Stripe) | — | ✅ | ✅ |
| Usage analytics dashboard | — | ✅ | ✅ |
| Overage pricing | — | ✅ | ✅ |
| Tier recommendations | — | ✅ | ✅ |
| Annual billing option | — | ✅ | ✅ |
| Seat-based licensing | — | — | ✅ |
| Volume licensing (bulk) | — | ✅ | ✅ |
| Custom SLA (enterprise) | ✅ | ✅ | ✅ |
| On-premises deployment | — | ✅ | ✅ |

---

## Comparison: Old (4-Tier) vs. New (2-Tier)

### Old Model (v0.3-rc1)

| **Tier** | **Cost** | **Brain** | **Max Plugins** | **Tool Forge** | **Skill Forge** |
|---|---|---|---|---|---|
| Free | $0 | ❌ disabled | 1 | ❌ | ❌ |
| Standard | $99/mo | 100/day | 5 | 50/day | 20/day |
| Professional | $499/mo | 1000/day | 20 | 500/day | 200/day |
| Enterprise | Custom | unlimited | unlimited | unlimited | unlimited |

### New Model (Proposed)

| **Tier** | **Cost** | **Brain** | **Max Plugins** | **Tool Forge** | **Skill Forge** |
|---|---|---|---|---|---|
| Free | $0 | 10/day | 1 | 5/day | 2/day |
| Member | $19/mo | unlimited | unlimited | unlimited | unlimited |
| Enterprise | Custom | unlimited* | unlimited* | unlimited* | unlimited* |

*Enterprise is custom contract, built on Member tier infrastructure.

### Why This Works Better

1. **Simpler:** 4 tiers → 2 tiers = easier decision
2. **Fairer free tier:** Brain was disabled (unusable) → now enabled (10/day, still usable)
3. **Lower barrier to paid:** $99/mo Standard → $19/mo Member (5x cheaper)
4. **Volume play:** Lower price = more customers = better ecosystem
5. **Better positioning:** $19/mo is "subscription" tier (like Spotify); $99+ is "enterprise"

---

**Last Updated:** 2026-08-17  
**Maintenance:** shumway (architect), Claude (documentation)
