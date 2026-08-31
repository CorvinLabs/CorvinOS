# CorvinOS Core vs. Plugins: Business-Driven Architecture Strategy

**Date:** 2026-07-26  
**Status:** Strategic Framework (Ready for Leadership Review)  
**Audience:** Engineering + Product + Business  
**Model Used:** Opus 5 (Reasoning)

---

## Executive Summary

**Core Principle:** Minimal mandatory core (~2.4 KB LOC for GDPR compliance), everything else pluggable.

**Business Model:** Free core + standard edition builds community; premium plugins ($X/month) and enterprise services ($X,000s) generate revenue.

**Revenue Targets (Year 1-3):**
- Year 1: 1,000+ free community users (zero revenue)
- Year 2: 30% paid Professional tier ($X/month × 100 teams) = $X,000s/month
- Year 3: Mix of free (40%), Professional (30%), Enterprise (20%), SaaS (10%) = $X,000,000/year

---

## Layer 0: Absolute Core (~2,400 LOC)

**Principle:** Only GDPR + EU AI Act requirements. Everything else is a plugin.

| Component | LOC | Why | Charge |
|-----------|-----|-----|--------|
| HTTP Router | 200 | Everything depends on it | ❌ |
| Audit Trail Core | 400 | GDPR Art. 30, 32 (mandatory) | ❌ |
| Consent Gate | 300 | GDPR Art. 6, 7 (deny-by-default) | ❌ |
| Flow Guard | 200 | PII detection, fail-closed | ❌ |
| House Rules | 200 | EU AI Act Art. 5, 50 | ❌ |
| Erasure Orchestrator | 300 | GDPR Art. 17 automation | ❌ |
| Plugin Registry | 500 | Everything else plugged in | ❌ |
| Session + Auth Middleware | 300 | Identity + consent per request | ❌ |

**Total: 2,400 LOC of hardcoded, tripwired, non-negotiable compliance infrastructure.**

---

## Layer 1: Standard Edition (~3,000 LOC, Pre-installed)

**Principle:** Core product features. Users can uninstall, but 90%+ won't.

| Component | Type | LOC | Status | Charge |
|-----------|------|-----|--------|--------|
| **Forge** | Plugin | 500 | Pre-installed, not replaceable | ❌ FREE (core UX) |
| **SkillForge** | Plugin | 400 | Pre-installed, not replaceable | ❌ FREE (core UX) |
| **TDE (L22)** | Plugin | 600 | Pre-installed, replaceable | ❌ FREE (included) |
| **Conversation Recall** | Plugin | 400 | Pre-installed, replaceable | ❌ FREE (UX) |
| **Structured Logging** | Plugin | 400 | Pre-installed, replaceable | ❌ FREE (ops) |
| **Discord/Slack Bridges** | Plugin | 700 | Pre-installed, replaceable | ❌ FREE (distribution) |

**Total: 3,000 LOC of differentiating, community-friendly product features.**

**Deployment:** `corvinctl install --edition standard` (includes core + all Layer 1 plugins)

---

## Layer 2: Premium Plugins ($X/month)

### Speech-to-Text (L23)
```
Tier 1: Whisper (local, free)          ❌ $0/month
Tier 2: Cloud STT (Azure, Google)      ✅ $0.01-0.05/min (pass-through)
↳ Defensible: Real infrastructure cost, not just markup
```

### Advanced Data Classification (L34 Extensions)
```
Tier 1: Basic PII (email, SSN, CC)     ❌ FREE
Tier 2: Advanced ML (medical, financial) ✅ $X/month
Tier 3: Custom classifier (org-specific) ✅ $X,000s (custom)
↳ Defensible: ML models, training data, accuracy improvements
```

### Enterprise Audit Backends
```
Tier 1: File-based                     ❌ FREE
Tier 2: Postgres backend               ✅ $50/month (or self-hosted)
Tier 3: External SIEM (Splunk, Datadog) ✅ $X/month (pass-through)
↳ Defensible: Infrastructure, integration, support
```

### Advanced Routing (L22 Extensions)
```
Tier 1: Native + TDE                   ❌ FREE
Tier 2: Cost optimization algorithms   ✅ $X/month
Tier 3: Regional routing + data residency ✅ $X/month
↳ Defensible: Proprietary algorithms, real cost savings
```

### Advanced Monitoring (NerveFiber Extensions)
```
Tier 1: Basic health checks            ❌ FREE
Tier 2: Grafana dashboards (cloud)     ✅ $X/month
Tier 3: Predictive alerts (ML-based)   ✅ $X/month
↳ Defensible: Hosting, ML models, support
```

---

## Layer 3: Community Marketplace (FREE)

**Principle:** User-contributed or 3rd-party plugins. Minimal Anthropic support.

- **Specialized LLMs:** Hugging Face, Llama 2, Mistral (community-maintained)
- **Compliance Packs:** HIPAA, PCI-DSS, SOC 2 templates (community or vendor)
- **Custom Bridges:** Telegram, Matrix, WhatsApp/Twilio (community)
- **Domain Tools:** SQL analyzers, AWS CLI wrappers, document processors (community)
- **Analytics:** Usage tracking, cost tracking, performance analytics (community)

**Revenue Model:** Marketplace takes 10-20% of vendor plugins (if any charge). Community plugins free.

---

## Pricing Tiers (Go-to-Market)

### Tier 1: Community (Free)
**Includes:**
- Absolute core (audit, consent, routing, erasure)
- Standard Edition (Forge, Skills, logging, bridges)
- Community marketplace (LDAP, tools, compliance packs)

**Target:** Individual developers, non-profits, startups  
**CAC:** Low (organic, community-driven)  
**LTV:** $0 (but enables lock-in to ecosystem)

**Go-to-Market:** v0.11 launch, emphasize "open, non-negotiable compliance"

---

### Tier 2: Professional ($X/month, est. $99-299)
**Includes everything in Community +**
- Speech-to-Text (Whisper)
- Advanced data classification (premium ML)
- Postgres audit backend
- Advanced routing (cost optimization)
- Priority support (24h response)

**Target:** Small teams (5-50 people), SMBs  
**CAC:** Medium ($500-1,000 per customer)  
**LTV:** $1,200-3,600/year × 2-3 year retention = $2,400-10,000  
**ARR Target:** 100 teams × $200/month = $240,000/year

**Go-to-Market:** 6 months after v0.11 launch; freemium model (free tier → convert on usage)

---

### Tier 3: Enterprise (Custom)
**Includes everything in Professional +**
- Advanced authentication (OKTA, SAML2, LDAP)
- Custom compliance packs (HIPAA, regional data residency)
- Advanced monitoring + predictive alerts
- Dedicated support + SLA (99.9% uptime)

**Target:** Large enterprises, highly regulated (100-1,000+ people)  
**CAC:** High ($10,000-50,000 per customer)  
**LTV:** $50,000-500,000/year × 3-5 year retention = $150,000-2,500,000  
**ARR Target:** 10-20 Enterprise customers × $100,000/year = $1,000,000-2,000,000/year

**Go-to-Market:** 12+ months after v0.11 (prove Professional tier first)

---

### Tier 4: Cloud-Hosted SaaS (Custom)
**Includes:** Full managed CorvinOS on our infrastructure

**Features:**
- Auto-scaling, multi-tenant isolation
- Built-in monitoring, alerting, backup
- Zero ops overhead
- Global CDN

**Target:** Teams that don't want to run their own infrastructure (SMBs → Enterprises)  
**Pricing:** $X per active user per month (SaaS model)  
**CAC:** Medium ($1,000-5,000)  
**LTV:** $10-20 per user per month × 50-500 users × 2 year = $2,400-240,000  

**Go-to-Market:** Year 2 (after Standard + Professional tiers stable)

---

## What's Defensible (Can Charge For)

✅ **Speech-to-Text:** Pay-per-minute transcription (real infrastructure costs)
✅ **Advanced Data Classification:** ML models with ongoing training/updates
✅ **Managed SaaS:** Infrastructure cost, auto-scaling, uptime SLA
✅ **Enterprise Support:** Guaranteed response time, dedicated engineer
✅ **Custom Compliance:** Legal expertise, audit trail templates, certifications
✅ **Advanced Routing:** Proprietary algorithms for cost optimization

---

## What's NOT Defensible (Cannot Charge For)

❌ **Core plugins** (Forge, Skills, TDE) — Community forks in 6 months
❌ **Audit trail** — GDPR requirement, not differentiating
❌ **Basic auth** — LDAP/OKTA already free/cheap
❌ **File logging** — Trivial to implement
❌ **Standard bridges** — Community replaces quickly
❌ **Discord/Slack integrations** — Users can build themselves

---

## Organizational Structure

### Core Team (Platform)
**Size:** 3-4 engineers  
**Owns:**
- Minimal core (~2.4 KB LOC)
- Plugin system + registry
- Tripwire + compliance gates

**OKR:** Keep core < 3,000 LOC; add only mandatory regulatory features

### Product Teams (Layer 1 + 2 Plugins)
**Size:** 2-3 engineers per plugin area

| Team | Plugins | Revenue |
|------|---------|---------|
| **Voice & Data** | STT + Advanced Classification | $X/month |
| **Enterprise Auth** | OKTA, SAML2, LDAP | $X,000s |
| **Advanced Routing** | Cost optimization, regional | $X/month |
| **Monitoring** | Prometheus, Grafana, Alerts | $X/month |
| **Cloud SaaS** | Managed hosting, multi-tenant | $X per user/month |

### Community Team
**Size:** 1-2 engineers  
**Owns:**
- Marketplace management
- Plugin security review + audit
- Documentation for plugin authors
- Community engagement

### Support Team
**Size:** Scales with customers

| Tier | Support |
|------|---------|
| Community | Community forums + async GitHub |
| Professional | 24h response, email + Slack |
| Enterprise | Dedicated engineer on retainer |

---

## Financial Model (Year 1-3)

### Year 1: Build Community (Revenue = $0)
- Launch Core + Standard Edition (FREE)
- 1,000+ downloads
- Focus: Community trust, word-of-mouth
- Engineering investment: 30 engineer-weeks

### Year 2: Monetize (Revenue = $300,000)
- Launch Professional tier ($X/month)
- 100 Professional customers
- 10,000+ community users
- Focus: Sales/product-market fit
- Engineering investment: 60 engineer-weeks

### Year 3: Scale (Revenue = $2,000,000+)
- Launch Enterprise tier ($X,000s)
- Launch SaaS tier ($X per user)
- 20 Enterprise customers
- 500 Professional customers
- 100,000+ community users
- Focus: Sales, support, reliability
- Engineering investment: 100 engineer-weeks

---

## Decision Matrix: Every Component

| Component | Core? | Pre-inst? | Charge? | Owner | Revenue Stream |
|-----------|-------|-----------|---------|-------|-----------------|
| HTTP Router | ✅ | N/A | ❌ | Platform | N/A |
| Audit Trail | ✅ | N/A | ❌ | Platform | Compliance requirement |
| Consent Gate | ✅ | N/A | ❌ | Platform | Compliance requirement |
| Flow Guard | ✅ | N/A | ❌ | Platform | Compliance requirement |
| House Rules | ✅ | N/A | ❌ | Platform | Compliance requirement |
| Erasure | ✅ | N/A | ❌ | Platform | Compliance requirement |
| Plugin Registry | ✅ | N/A | ❌ | Platform | Ecosystem enabler |
| **Forge** | ❌ | ✅ | ❌ | Product | Lock-in |
| **SkillForge** | ❌ | ✅ | ❌ | Product | Lock-in |
| **TDE** | ❌ | ✅ | ❌ | Product | Differentiation |
| **Logging** | ❌ | ✅ | ❌ | Product | Ops feature |
| **Discord** | ❌ | ✅ | ❌ | Product | Distribution |
| **STT** | ❌ | ✅ (basic) | ✅ (premium) | Product | $X/month per customer |
| **Data Classification** | ❌ | ✅ (basic) | ✅ (premium) | Product | $X/month per customer |
| **Audit Backends** | ❌ | ✅ (file) | ✅ (Postgres) | Product | $X/month per customer |
| **Auth (Advanced)** | ❌ | ❌ | ✅ | Product | Enterprise tier |
| **Routing (Advanced)** | ❌ | ✅ (basic) | ✅ (premium) | Product | $X/month per customer |
| **Monitoring** | ❌ | ✅ (basic) | ✅ (premium) | Product | $X/month per customer |
| **Community Plugins** | ❌ | ❌ | Variable | Community | Marketplace revenue share |

---

## Implementation Roadmap

### Phase 0: Core + Standard Edition (v0.11, Q3 2026)
- Extract core (2.4 KB LOC)
- Pre-install Layer 1 plugins
- Release as "CorvinOS Free Edition"
- No revenue model yet; focus on adoption

### Phase 1: Professional Tier (v0.12, Q4 2026)
- Launch Speech-to-Text premium (pay-per-minute)
- Launch Advanced Data Classification ($X/month)
- Launch Postgres audit backend ($X/month)
- Freemium model: free core → convert on usage

### Phase 2: Enterprise + SaaS (v0.13, Q1-Q2 2027)
- Launch Enterprise tier (custom pricing)
- Launch SaaS tier ($X per user/month)
- Build sales + support infrastructure

### Phase 3: Ecosystem Scale (v0.14+, 2027+)
- Grow marketplace (100+ community + vendor plugins)
- Enable 3rd-party monetization (10-20% revenue share)
- Scale to $10M+ ARR

---

## Success Metrics

### Year 1
- ✅ 1,000+ downloads
- ✅ 100+ GitHub stars
- ✅ 10+ community plugins
- ✅ Zero revenue (expected)

### Year 2
- ✅ 100 Professional paying customers ($30K/month ARR)
- ✅ 10,000+ free users
- ✅ 50+ community plugins
- ✅ 50% year-over-year growth

### Year 3
- ✅ 500 Professional + 20 Enterprise customers ($200K/month ARR)
- ✅ 100,000+ free users
- ✅ SaaS tier with 1,000+ active users ($50K/month ARR)
- ✅ Total ARR: $2M+

---

## Conclusion

**CorvinOS Thesis:**
> A minimal compliance core (2.4 KB LOC) + pluggable everything else = smallest viable enterprise platform that doesn't require a team of security lawyers to deploy.

**Business Model:**
> Free core builds community. Premium plugins ($X/month) and enterprise services ($X,000s) generate revenue with zero compromise on compliance or open-source principles.

**Competitive Advantage:**
> We're the only open-source platform where compliance is baked in, non-negotiable, and tripwired — not a feature flag. That's the moat.

---

**Ready for leadership review. Questions?**
