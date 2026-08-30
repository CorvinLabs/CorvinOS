# Corvin Marketplace — Plugin-First Architecture (ADR-0511)

**Status:** Phase A (Directory Setup) in progress  
**Related:** ADR-0511 (Marketplace Plugin-First), Phase 1-4 Autonomous Plan

---

## 🏗️ Directory Structure

```
operator/marketplace/
├── plugins/                          ← PLUGIN SPOTLIGHT (central hub)
│   ├── buildin/                      (Apache 2.0 + CLA)
│   │   ├── memory/
│   │   ├── security_compliance/
│   │   ├── integration/
│   │   ├── data_processing/
│   │   └── observability/
│   │
│   └── contributor/                  (MIT)
│       ├── memory/
│       ├── security_compliance/
│       ├── integration/
│       ├── data_processing/
│       └── observability/
│
├── extensions/                       ← SECONDARY EXTENSIONS
│   ├── skills/
│   ├── tools/
│   ├── connectors/
│   └── layers/
│
├── index/                            ← GENERATED (CI/CD)
│   ├── plugins.json                  (all plugins, categorized)
│   ├── extensions.json               (all extensions)
│   ├── categories.json               (category metadata)
│   └── discovery.json                (unified discovery index)
│
├── generate_index.py                 ← INDEX GENERATOR (updated)
├── plugin-schema.json                ← PLUGIN MANIFEST SCHEMA (ADR-0511)
├── index-schema.json                 ← INDEX SCHEMA (legacy, updated)
└── CATEGORIES.md                     ← CATEGORY DEFINITIONS (new)
```

---

## 📁 Plugin Categories (Derived from CorvinOS)

| Category | Purpose | Examples | Layer Reference |
|----------|---------|----------|-----------------|
| **memory** | Session recall, user modeling, learning | CEL session memory, user model, learning events | L28, ADR-0314-0321 |
| **security_compliance** | Auth, audit, consent, encryption | Consent gate, audit chain, path-gate, flow-guard | L16, L10, L34 |
| **integration** | Hooks, bridges, MCP, multi-persona | Hook registry, cowork hub, bridges, MCP | L4, L38 |
| **data_processing** | Extraction, classification, anonymization | Artifact extraction, PII classifier, anonymization | L25, L34, L36 |
| **observability** | Telemetry, diagnostics, self-repair | Telemetry collector, heartbeat, health diagnostics, ACO L5 | L36 |

---

## 🔄 Integration with Phase 1-2

### Phase 1: Data Layer
**Existing:** API endpoints (7) + cache layer (marketplace.py, marketplace_cache.py)

**ADR-0511 Integration:**
- Endpoints remain unchanged
- Responses now categorized by plugin category
- Index includes both buildin + contributor plugins

### Phase 2: UI Layer
**Existing:** marketplace.tsx (Browse/Search/Detail/Installed/Install-Progress)

**ADR-0511 Integration:**
- UI shows plugin categories (tabs/sidebar)
- Buildin plugins marked with "Supported" badge
- Contributor plugins marked with community badge
- Category filtering on browse view

### Phase 3: Job API Wiring
**Existing:** Mock install-progress component

**ADR-0511 Integration:**
- Real job tracking for plugin installation
- No structural changes (already design-compatible)

---

## 📋 Tier System (Licensing)

### Tier 1: Buildin Plugins (Apache 2.0 + CLA)
- Location: `plugins/buildin/[category]/[plugin_id]/`
- Author: Anthropic PBC or CLA-signed contributor
- License: Apache 2.0 with headers in every file
- SLA: 48h bugfix, 24h security patch
- Distribution: Source (git) + Wheel (pre-built, signed)
- Security Audit: Required (findings=0)
- Support: Anthropic maintainer
- Console Badge: ✅ Supported (Enterprise-Grade)

### Tier 2: Contributor Plugins (MIT)
- Location: External repo or `plugins/contributor/[category]/[plugin_id]/`
- Author: Community member (retains copyright)
- License: MIT (no CLA)
- SLA: None (community-driven)
- Distribution: Source + optional Wheel
- Security Audit: Optional
- Support: Author (best-effort)
- Console Badge: ✨ Community Plugin

---

## 🔄 CI/CD Pipeline (Phase B)

**Index Generation (generate_index.py, updated):**
1. Scan `plugins/buildin/` → extract all plugin.json files
2. Validate against plugin-schema.json
3. Scan `plugins/contributor/` → same
4. Generate `index/plugins.json` (categorized, tier-aware)
5. Scan `extensions/` → generate `index/extensions.json`
6. Merge both into `index/discovery.json`
7. Publish to GitHub Releases (on-push or hourly)

**Validation Gates:**
- ✅ JSON schema (plugin-schema.json)
- ✅ Buildin plugins: Apache 2.0 headers + security_audit object
- ✅ No PII in plugin.json
- ✅ Version semver format
- ✅ Dependency resolution (no circular deps)

---

## 📝 Plugin Manifest (plugin.json)

Every plugin has `plugin.json` in its root directory:

```json
{
  "id": "plugin:buildin-memory-cel_session_memory",
  "type": "plugin",
  "name": "CEL Session Memory",
  "version": "1.0.0",
  "author": "Anthropic PBC",
  "license": "Apache-2.0",
  "tier": "buildin",
  "category": "memory",
  "description": "CEL-based session recall with embeddings",
  "distribution": {
    "supports_source": true,
    "supports_wheel": true,
    "wheel_url": "https://..."
  },
  "security_audit": {
    "findings": 0,
    "last_audit_date": "2026-08-30"
  }
}
```

Schema: See `plugin-schema.json` (JSONSchema validation)

---

## 🚀 Next Steps

### Phase A (THIS WEEK)
- [ ] README + CATEGORIES.md (this file)
- [ ] Migrate existing marketplace code to extensions/
- [ ] Setup plugin.json validation CI/CD gate
- [ ] Create .gitkeep files in category dirs

### Phase B (NEXT WEEK)
- [ ] Update generate_index.py (plugin-aware)
- [ ] Update marketplace.py APIs (tier-aware responses)
- [ ] Create first buildin plugins (from CorvinOS core features)
- [ ] E2E tests

### Phase C (WEEK 3)
- [ ] Update marketplace.tsx UI (category tabs)
- [ ] Add tier badges (Supported/Community)
- [ ] Test install flow end-to-end
- [ ] Feature flag rollout

---

## 📚 Related Documentation

- **ADR-0511:** Marketplace Plugin-First Architecture (Corvin-ADR)
- **plugin-schema.json:** JSON validation schema
- **marketplace_phase1_2_autonomous_plan.md:** Existing implementation roadmap
- **marketplace_phase2_week2_complete.md:** Phase 2 completion status

---

**Status:** Setup phase complete | Phase B ready to start  
**Owner:** Autonomous (LDD-driven)  
**Last Updated:** 2026-08-30 23:25 UTC
