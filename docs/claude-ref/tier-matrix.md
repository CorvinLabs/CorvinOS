# Tier Matrix & Pricing Reference

**Source:** ADR-0363  
**Status:** Proposed  
**Last Updated:** 2026-08-17

---

## Quick Reference

| **Feature** | **Free** | **Standard ($99/mo)** | **Professional ($499/mo)** | **Enterprise (Custom)** |
|---|---|---|---|---|
| **Brain v0.2** | ❌ | ✅ | ✅ | ✅ |
| **Tool Forge** | ❌ | ✅ (50/day) | ✅ (500/day) | ✅ (unlimited) |
| **Skill Forge** | ❌ | ✅ (20/day) | ✅ (200/day) | ✅ (unlimited) |
| **Voice Guidance** | ❌ | ❌ (v0.3) | ✅ (v0.3) | ✅ |
| **Max Plugins** | 1 | 5 | 20 | unlimited |
| **Plugin Types** | builtin | builtin, vetted, community | builtin, vetted, community, custom | all |
| **Brain Tasks/Day** | unlimited* | 100 | 1000 | unlimited |
| **Max Task Duration** | 30 min | 2 hrs | unlimited | unlimited |
| **Subsystems** | 2 (basic) | 13 (full) | 13 (full) | 13 + custom |
| **SLA** | None | 99% | 99.9% | custom |
| **Support** | Community | Email | Priority email + phone | Dedicated engineer |

*Free tier has Brain disabled entirely; basic subsystems (LoopEngineer, CostController only) still available for non-Brain use cases.

---

## Tier Details

### Free Tier

**Cost:** $0  
**Target:** Solo practitioners, hobbyists, personal projects  
**License:** Always free (no license key required)

#### Enabled Features
- **Plugin System:** 1 builtin plugin (e.g., Console) — no custom/community plugins
- **Context Engineering:** Basic context variables only
- **Voice Input:** Yes (STT)
- **Voice Output:** Basic TTS only
- **Audit Trail:** Yes (GDPR-required)

#### Disabled Features
- Brain v0.2 (all 13 subsystems)
- Tool Forge
- Skill Forge
- Voice Guidance (advanced)
- Multi-worker mode (max 1 worker)
- Custom context bridges

#### Quotas
- `max_plugins`: 1
- `max_task_duration`: 30 minutes
- `max_workers`: 1

#### Error Message (When Feature Locked)
```
Brain v0.2 requires a Standard or higher license ($99/mo).
[Upgrade to Standard]  [Learn More]
```

---

### Standard Tier (Tier A)

**Cost:** $99/month  
**Target:** Small teams 2–10 people, startups, indie projects  
**License:** Issued and manually renewed monthly

#### Enabled Features
- **Brain v0.2:** Full 13-subsystem orchestration
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
  - Hub (namespace + quota)

- **Tool Forge:** 50 tool generations per day
- **Skill Forge:** 20 skill creations per day
- **Plugin System:** 5 plugins (builtin, vetted, community)
- **Context Engineering:** Full Context v2 (all 4 context bridges)
- **Voice Guidance:** Not included (v0.3)
- **Advanced TTS:** 3 voices (builtin)
- **Multi-Worker:** Up to 3 workers

#### Quotas
- `max_plugins`: 5
- `brain_tasks_per_day`: 100
- `tool_forge_per_day`: 50
- `skill_forge_per_day`: 20
- `max_workers`: 3
- `max_task_duration`: 2 hours

#### Use Cases
- Pair programming with AI (2 developers)
- Small SaaS: 1–2 engineers iterating on product
- Research team: <10 researchers
- Early-stage startup incubator

#### Pricing Example
- 3 developers, 2 months: 3 × $99 × 2 = **$594**
- Auto-renewal: operator pays monthly via Stripe

---

### Professional Tier (Tier B)

**Cost:** $499/month  
**Target:** Companies 50–500 people, scaling teams, SaaS  
**License:** Issued and automatically renewed via Stripe

#### Enabled Features
- **Brain v0.2:** Full (same as Standard, but higher quotas)
- **Tool Forge:** 500 tool generations per day
- **Skill Forge:** 200 skill creations per day
- **Plugin System:** 20 plugins (all types: builtin, vetted, community, custom)
- **Custom Brain Subsystems:** Can write custom subsystems extending Brain architecture
- **Context Engineering:** Full Context v2 + custom context bridges
- **Voice Guidance:** Yes (v0.3)
- **Advanced TTS:** Unlimited voices
- **Multi-Worker:** Up to 10 workers
- **Dedicated Ops Support:** Email + Slack channel

#### Quotas
- `max_plugins`: 20
- `brain_tasks_per_day`: 1000
- `tool_forge_per_day`: 500
- `skill_forge_per_day`: 200
- `max_workers`: 10
- `max_task_duration`: unlimited
- `custom_subsystems`: true

#### Use Cases
- Growing engineering team (50–150 people)
- AI-native SaaS product
- ML research lab
- Enterprise pilot program
- System integrator (building on Corvin)

#### Pricing Example
- 100-person team, 12 months: **$499 × 12 = $5,988/year**
- Cost per engineer: $60/year (vs. $99/mo individual)
- Automatic renewal + usage analytics dashboard included

---

### Enterprise Tier (Tier C)

**Cost:** Custom pricing (volume discounts available)  
**Target:** Organizations 500+ people, mission-critical use, custom SLAs  
**License:** Multi-year agreement, signed contract

#### Enabled Features
- **Everything in Professional, plus:**
- **Brain v0.2:** Custom quota negotiation (10,000+ tasks/day possible)
- **Tool Forge:** Unlimited (or negotiated)
- **Skill Forge:** Unlimited (or negotiated)
- **Plugin System:** Unlimited (private/proprietary plugin support)
- **Dedicated Support:** 24/7 phone + Slack + dedicated engineer
- **Custom Integrations:** OAuth, SAML/SSO, LDAP integration
- **SLA:** 99.99% uptime (custom contract)
- **Security Audit:** Quarterly pen tests, SOC 2 certification
- **Training:** Onboarding training for team
- **Custom Hosting:** On-premises deployment option (CorvinOS Appliance)

#### Quotas
- All quotas: **unlimited** (or per negotiated contract)

#### Use Cases
- Fortune 500 enterprise
- Regulated industry (healthcare, finance, defense)
- Mission-critical AI infrastructure
- Multi-tenant SaaS platform using CorvinOS as backend

#### Pricing Example
- **Base:** $10,000/month + per-request fees
- **Estimated annual:** $120,000–$500,000+
- **Negotiable:** Volume discounts, multi-year contracts, usage overages

---

## Feature Progression Table

### Brain Subsystems by Tier

| **Subsystem** | **Free** | **Standard** | **Professional** | **Enterprise** |
|---|---|---|---|---|
| Health Monitor | — | ✅ | ✅ | ✅ |
| Context Bridge | — | ✅ | ✅ | ✅ |
| Loop Engineer | — | ✅ | ✅ | ✅ |
| Orchestrator | — | ✅ | ✅ | ✅ |
| Learning Engine | — | ✅ | ✅ | ✅ |
| Cost Controller | — | ✅ | ✅ | ✅ |
| Safety Validator | — | ✅ | ✅ | ✅ |
| Strategy Advisor | — | ✅ | ✅ | ✅ |
| Tool Forge Subsystem | — | ✅ | ✅ | ✅ |
| Skill Forge Subsystem | — | ✅ | ✅ | ✅ |
| Forged Tool API | — | ✅ | ✅ | ✅ |
| Forged Skill API | — | ✅ | ✅ | ✅ |
| Hub | — | ✅ | ✅ | ✅ |
| Custom Subsystems | — | ❌ | ✅ | ✅ |

### Forge Features by Tier

| **Feature** | **Free** | **Standard** | **Professional** | **Enterprise** |
|---|---|---|---|---|
| Tool Forge | ❌ | 50/day | 500/day | unlimited |
| Skill Forge | ❌ | 20/day | 200/day | unlimited |
| Tool Versioning | — | ✅ | ✅ | ✅ |
| Skill Auto-Grading | — | ✅ | ✅ | ✅ |
| Tool A/B Testing | — | ❌ | ✅ | ✅ |
| Skill Marketplace | — | ❌ | ✅ | ✅ |

### Plugin System by Tier

| **Feature** | **Free** | **Standard** | **Professional** | **Enterprise** |
|---|---|---|---|---|
| Plugin Registry | ✅ | ✅ | ✅ | ✅ |
| Max Plugins | 1 | 5 | 20 | unlimited |
| Builtin Plugins | ✅ | ✅ | ✅ | ✅ |
| Vetted Plugins | ❌ | ✅ | ✅ | ✅ |
| Community Plugins | ❌ | ✅ | ✅ | ✅ |
| Custom Plugins | ❌ | ❌ | ✅ | ✅ |
| Private Plugin Registry | ❌ | ❌ | ✅ | ✅ |

---

## Quota Details

### Brain Tasks

A "brain task" is one end-to-end orchestration cycle (user request → Brain processes → result).

| **Tier** | **Daily Quota** | **Monthly (30-day)** | **Per-Hour** | **Overflow Behavior** |
|---|---|---|---|---|
| Free | disabled | — | — | Feature locked |
| Standard | 100 | 3,000 | ~4 | Quota exceeded error |
| Professional | 1,000 | 30,000 | ~42 | Quota exceeded error |
| Enterprise | unlimited | unlimited | unlimited | (per contract) |

### Tool Forge Requests

A "tool forge request" is one call to generate a tool dynamically.

| **Tier** | **Daily Quota** | **Monthly (30-day)** | **Per-Hour** | **Overflow Behavior** |
|---|---|---|---|---|
| Free | disabled | — | — | Feature locked |
| Standard | 50 | 1,500 | ~2 | Quota exceeded error |
| Professional | 500 | 15,000 | ~21 | Quota exceeded error |
| Enterprise | unlimited | unlimited | unlimited | (per contract) |

### Skill Forge Requests

A "skill forge request" is one call to create a new skill.

| **Tier** | **Daily Quota** | **Monthly (30-day)** | **Per-Hour** | **Overflow Behavior** |
|---|---|---|---|---|
| Free | disabled | — | — | Feature locked |
| Standard | 20 | 600 | ~1 | Quota exceeded error |
| Professional | 200 | 6,000 | ~8 | Quota exceeded error |
| Enterprise | unlimited | unlimited | unlimited | (per contract) |

### Plugin Count

A plugin is a loaded extension.

| **Tier** | **Max Plugins** | **Builtin Only** | **Vetted** | **Community** | **Custom** |
|---|---|---|---|---|---|
| Free | 1 | ✅ | ❌ | ❌ | ❌ |
| Standard | 5 | ✅ | ✅ | ✅ | ❌ |
| Professional | 20 | ✅ | ✅ | ✅ | ✅ |
| Enterprise | unlimited | ✅ | ✅ | ✅ | ✅ |

### Max Task Duration

How long a single task can run.

| **Tier** | **Max Duration** | **Timeout Behavior** |
|---|---|---|
| Free | 30 minutes | Task cancelled, state saved |
| Standard | 2 hours | Task cancelled, state saved |
| Professional | unlimited | (no timeout) |
| Enterprise | (per contract) | (custom SLA) |

### Workers (Concurrent Execution)

How many tasks can run in parallel.

| **Tier** | **Max Workers** | **Concurrency Model** |
|---|---|---|
| Free | 1 | Sequential (one at a time) |
| Standard | 3 | Parallel within one tenant |
| Professional | 10 | Parallel within one tenant |
| Enterprise | unlimited | (custom allocation) |

---

## Upgrade / Downgrade Policy

### Upgrading

1. **Operator selects new tier** in Console Settings → Licensing
2. **License file is re-issued** with new quotas
3. **Features unlock immediately**
4. **Billing:** Prorated (e.g., upgrade mid-month = partial month charge + full next month)
5. **No data loss:** All skills, tools, plugins preserved

### Downgrading

1. **Operator selects lower tier**
2. **License re-issued** with lower quotas
3. **Active tasks complete** (no immediate termination)
4. **New tasks subject to new quotas**
5. **Overage:** If current plugin count > new max_plugins, operator must remove excess plugins before downgrade completes
6. **Billing:** Credit applied (if applicable)

### Cancellation

1. **Operator cancels license** in Console
2. **License expires at end of current billing period**
3. **Default to free tier** (Brain disabled, plugins reduced to 1)
4. **Data retention:** All audit logs, skills, tools retained for 90 days (GDPR Art. 17 erasure)

---

## Migration Paths

### Free → Standard
- **Typical trigger:** Team wants to use Brain + forge features
- **Prerequisite:** None (Standard accepts free tenants)
- **Effort:** Click "Upgrade" → auto-issue license → verify unlock
- **Timeline:** Immediate (license issued within seconds)

### Standard → Professional
- **Typical trigger:** Team outgrowing 100 tasks/day quota
- **Prerequisite:** None (Professional accepts Standard customers)
- **Effort:** Click "Upgrade" → license reissued with 1000/day quota
- **Timeline:** Immediate
- **Cost impact:** $99 → $499/month (+$400)

### Free → Professional (Direct)
- **Typical trigger:** Large team joining early
- **Effort:** Click "Upgrade to Professional" → license issued directly
- **Timeline:** Immediate
- **Cost impact:** $0 → $499/month

### Professional → Enterprise
- **Typical trigger:** Org needing custom SLA, dedicated support, on-premises deployment
- **Prerequisite:** Multi-year contract negotiation
- **Effort:** Sales cycle → contract signed → custom license issued
- **Timeline:** 1–4 weeks (sales) + implementation
- **Cost impact:** $499/month → custom (typically $1000+/month)

---

## Special Cases

### Nonprofit / Educational License

**Availability:** All tiers  
**Discount:** 50% off Standard/Professional  
**Requirements:**
- 501(c)(3) nonprofit status (US) or equivalent
- Accredited educational institution (.edu domain)
- Impact mission (AI research, education, non-commercial use)

**Process:**
1. Apply via [corvin.io/nonprofit](https://corvin.io/nonprofit)
2. Provide proof (IRS 501(c)(3) letter or .edu domain ownership)
3. License issued at 50% discount

### Trial License

**Availability:** Free tier customers  
**Duration:** 14 days  
**Features:** Full Professional tier (1000 brain tasks/day, 500 tool forges/day, etc.)
**No credit card required**

**Process:**
1. Operator clicks "Try Professional" in Console
2. Trial license issued for 14 days
3. At day 13, reminder email to upgrade or downgrade
4. After day 14, automatically downgrade to free

### Volume Licensing (Enterprise Only)

**When:** Organization deploying CorvinOS across 100+ seats  
**Negotiation:** Contact sales@corvin.io

**Example tiers:**
- 1–100 seats: $499/month per deployment
- 101–500 seats: $399/seat (20% discount)
- 500+ seats: Custom pricing (30%+ discount possible)

---

## FAQ

**Q: Can we test before upgrading?**  
A: Yes. Free tier is permanent, no credit card required. Try features, then upgrade when ready. Trial licenses (14-day Professional) available on request.

**Q: What happens if we exceed quota mid-task?**  
A: Task fails with `QuotaExceeded` error. Quotas are checked at *request time*, not mid-execution. If you're at 49/50 tool forges and forge one more, the 50th succeeds, then the 51st is denied.

**Q: Can we buy overage?**  
A: Not in v0.3. Planned for v1.0 ($0.01/tool-forge request above quota, $0.05/brain-task, etc.). For now, upgrade to higher tier or contact sales@corvin.io for custom contract.

**Q: What if we downgrade and have too many plugins?**  
A: Operator must manually delete excess plugins before downgrade completes. The license validator blocks downgrade if current_plugins > new_max_plugins.

**Q: Is there a month-to-month option?**  
A: Yes. All Standard/Professional tiers are monthly. Enterprise is annual or multi-year.

**Q: Can we run two tenants on one license?**  
A: No. One license per `tenant_id`. Multi-tenant scenarios (e.g., reseller) require separate licenses per customer tenant or custom enterprise agreement.

---

## Roadmap: Tier Features (v1.0+)

| **Feature** | **v0.3** | **v1.0** | **v1.5+** |
|---|---|---|---|
| Manual license renewal | ✅ | ↓ | ↓ |
| Automatic renewal (Stripe) | — | ✅ | ✅ |
| Usage analytics dashboard | — | ✅ | ✅ |
| Overage pricing | — | ✅ | ✅ |
| Tier recommendations ("upgrade hint") | — | ✅ | ✅ |
| Seat-based licensing (per-user) | — | — | ✅ |
| Volume licensing (bulk discounts) | — | ✅ | ✅ |
| Custom SLA (enterprise only) | ✅ | ✅ | ✅ |
| On-premises deployment | — | ✅ | ✅ |
