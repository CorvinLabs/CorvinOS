# CorvinOS Plugin Marketplace

**Status:** Phase 1 — Foundation (Week 1 complete)

This directory contains the CorvinOS Plugin Marketplace infrastructure.

---

## Directory Structure

```
operator/marketplace/
├── README.md                    ← This file
├── plugins/                     ← Plugin storage
│   ├── buildin/                 (Apache 2.0 + CLA)
│   │   ├── memory/
│   │   ├── security_compliance/
│   │   ├── integration/
│   │   ├── data_processing/
│   │   └── observability/
│   └── contributor/             (MIT)
│       └── [same categories]
├── extensions/                  ← Other extensions
│   ├── skills/
│   ├── tools/
│   ├── connectors/
│   └── layers/
├── schemas/                     ← JSON schemas
│   └── plugin-schema.json       (plugin manifest schema)
├── templates/                   ← CLA/MIT templates
│   ├── BUILDIN_PLUGIN_CLA.md
│   └── CONTRIBUTOR_PLUGIN_MIT.md
└── index/                       ← Generated indices (Week 2)
    ├── plugins.json
    ├── skills.json
    ├── tools.json
    ├── connectors.json
    └── layers.json
```

---

## Plugin Categories

All plugins must be categorized in one of 5 categories:

| Category | Purpose | Examples |
|----------|---------|----------|
| **memory** | Session recall, user modeling, learning (L28) | CEL Session Memory, User Model |
| **security_compliance** | Auth, audit, path-gate, flow guard (L16, L10, L34) | Consent Gate, Audit Chain |
| **integration** | Hooks, cowork, bridges, MCP servers (L4, L38) | Bridge Handler, Cowork Hub |
| **data_processing** | Artifact extraction, classification, anonymization (L25, L34, L36) | Artifact Extractor, Data Classifier |
| **observability** | Telemetry, heartbeat, diagnostics (ACO L5) | Telemetry Collector, Health Monitor |

---

## Plugin Manifest (plugin.json)

Every plugin requires a `plugin.json` manifest validated against `schemas/plugin-schema.json`.

**Phase 1 Deliverables (✅ Complete):**
- ✅ Schema: `operator/marketplace/schemas/plugin-schema.json`
- ✅ CLA: `operator/marketplace/templates/BUILDIN_PLUGIN_CLA.md`
- ✅ MIT License: `operator/marketplace/templates/CONTRIBUTOR_PLUGIN_MIT.md`
- ✅ Developer Guide: `docs/plugin-developer-guide.md`
- ✅ Tests: `tests/unit/marketplace/test_plugin_schema.py`

---

## Contributing

**Buildin Plugins:** Requires CLA, Apache 2.0, security audit, SLA guarantee  
**Contributor Plugins:** MIT license, no CLA, community-driven

See `docs/plugin-developer-guide.md` for complete instructions.

---

## Phase Roadmap

| Phase | Week | Goals | Status |
|-------|------|-------|--------|
| **Phase 1** | W1 | Schema, CLA/MIT, Dev Guide | ✅ COMPLETE |
| **Phase 2** | W2 | Directory structure, Wheel pipeline | ⏳ PENDING |
| **Phase 3** | W3 | Migrate 25+ core plugins | ⏳ PENDING |
| **Phase 4** | W4 | Console API + UI redesign | ⏳ PENDING |
| **Phase 5** | W5 | Community launch, first plugins | ⏳ PENDING |

---

**Last Updated:** 2026-08-31  
**Next:** Week 2 (Infrastructure)
