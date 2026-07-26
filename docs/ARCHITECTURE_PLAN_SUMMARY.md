# CorvinOS Compartmentalization Plan
## Open Core Architecture with Headless Core Engine

**Status:** 9 ADRs Approved (ADR-0234 through ADR-0242)  
**Phase 1 Timeline:** 10 weeks (Weeks 1-10)  
**Target Release:** v0.11.0 (after Phase 1)

---

## Quick Summary

CorvinOS transforms from monolithic to compartmentalized:

```
OLD: Bridge-centric Monolith
  └─ One crash = everything down

NEW: Headless Core + Open Core Plugins
  ├─ Core (Tier-0/1) = stable, immutable compliance
  ├─ Plugins (Tier-2/3) = optional, replaceable
  └─ Bridges = bundled, enable/disable per tenant
```

---

## Architecture Layers

### Tier-0: Mandatory Compliance (~2.4 KB)
- Audit, Consent, Flow Guard, House Rules, Erasure
- Hardcoded, tripwired, regulatory (GDPR/EU AI Act)
- ❌ Cannot disable, cannot replace

### Tier-1: Core Infrastructure (~6.6 KB)
- A2A, TDE, Recall, ACS, Compute, Delegation, Workflows, Engine, Voice Summary, Admin
- Reference implementations (our defaults)
- ✅ Can customize via hooks (50+ extension points)
- ✅ Can replace entirely (plugin replacement)

### Tier-2: Bundled Bridges (~3-4 KB)
- Discord, Slack, Telegram, WhatsApp, Web UI, CLI
- Pre-installed, enable/disable per tenant config
- ✅ Can disable, replace, or extend

### Tier-3: Premium Plugins
- Licensed features (STT, ML Classification, OKTA, Postgres)
- User-installed, optional

---

## Key Innovation: Open Core Philosophy

**NOT:**
- "You must use our ACS"
- "Voice Summary is locked in"
- "Bridges are mandatory"

**YES:**
- "Here's our battle-tested default"
- "Want to customize? Use hooks"
- "Want to replace? Build your own plugin"
- "All bridges included, choose what to enable"

---

## Deployment Models

| Model | Use Case |
|-------|----------|
| **Complete** | All bridges (Discord, Slack, Telegram, WhatsApp, Web UI, Forge, SkillForge) |
| **Typical** | Web + Chat (Web UI, Discord, Slack, Forge, SkillForge) |
| **API-Only** | Enterprise backend (no bridges, pure API) |
| **Custom UI** | CLI-only or custom dashboard (no web_ui) |

---

## W1-W10 Implementation Plan

| Week | Phase | Objective | Tests |
|------|-------|-----------|-------|
| 1-2 | Directory refactor | tier_0, tier_1_core, tier_2_bundled | 200 ✓ |
| 3-4 | Plugin registry | Enable/disable, replacement logic | 45 ✓ |
| 5-6 | Admin API | REST + gRPC extraction | 45 ✓ |
| 7-8 | Bridges | Bundled + enable/disable | 65 ✓ |
| 9-10 | Testing | E2E, load, hardening | 720 ✓ |

---

## Related Documents

| Document | Focus |
|----------|-------|
| **ADR-0234** | 3-layer architecture + open core principle |
| **ADR-0235** | Plugin classification (Tier A/B/C) |
| **ADR-0236** | Minimal core specification (~9 KB) |
| **ADR-0237** | Extensible core plugins + replacement pattern |
| **ADR-0238** | Bundled bridges architecture |
| **ADR-0239** | Admin API vs. Web UI separation |
| **ADR-0240** | Plugin scoping (global vs. tenant) |
| **ADR-0241** | Headless core architecture |
| **ADR-0242** | Implementation plan (10 weeks) |

## Technical Docs (in this directory)

- `HEADLESS_CORE_ARCHITECTURE.md` — API-driven core, subprocess isolation
- `EXTENSIBLE_CORE_PLUGINS.md` — Hook patterns, replacement examples
- `BUNDLED_BRIDGES_STRUCTURE.md` — Bridge interface, enable/disable
- `PLUGIN_DIRECTORY_STRUCTURE.md` — Global vs. tenant plugin layout
- `IMPLEMENTATION_PLAN_PHASE_1.md` — Detailed week-by-week plan

---

## Success Criteria (Week 10)

✅ 720 tests pass (200+45+45+65+365)  
✅ Directory structure refactored  
✅ Plugin registry with enable/disable  
✅ Admin API (REST + gRPC) live  
✅ All bridges bundled + optional  
✅ Performance: <100ms API latency  
✅ Isolation: bridge crash ≠ core crash  
✅ Documentation complete  
✅ Ready for v0.11.0 release

---

## Why This Matters

### For Community
- Open core = no fork penalty
- All features included
- Can customize without forking
- Clear upgrade path

### For Enterprise
- Headless mode (API-driven)
- Replace any component (custom ACS, recall, routing)
- Tenant isolation (multi-tenant, secure)
- Compliance always enforced (GDPR tripwired)

### For Maintainers
- Clear separation (tier-0, tier-1, tier-2)
- Easier to test (core isolated)
- Easier to scale (modular)
- Easier to extend (open plugin system)

---

## Next Step

**Week 1:** Start Phase 1 (directory refactor)  
**Reference:** ADR-0242 implementation plan

