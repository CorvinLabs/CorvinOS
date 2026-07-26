# Plugin System Phase 3 Roadmap

**Status:** Planning (Phase 2b complete, ready for Phase 3)  
**Target:** CorvinOS v0.12.0 (Q4 2026)  
**Owner:** [TBD]  

## What's Already Done (Phases 1-2b)

✅ **Phase 1:** Core backend (Registry, Lifecycle, Resolver, Validator, Audit)  
✅ **Phase 1b:** Console integration (REST API, React hook, PluginsPanel)  
✅ **Phase 2a:** Marketplace (Download manager, Install endpoint)  
✅ **Phase 2b-MVP:** E2E foundation + Tier A migration guide  

**Artifacts:**
- 56/56 tests passing (unit + integration)
- ~5000 lines production code
- 4 production commits
- Complete architecture (ADR-0XXX)
- Playwright config ready

---

## Phase 3 Scope

### 1. Full Playwright E2E Suite (k=1-2)

**File:** `core/console/corvin_console/web-next/tests/e2e/plugins.spec.ts`

**Tests to implement:**
- ✅ Load plugins page
- ✅ List installed plugins
- [ ] Click marketplace tab
- [ ] Install plugin from marketplace (with mock)
- [ ] Wait for install progress
- [ ] Enable plugin toggle
- [ ] Verify enabled state persists
- [ ] Change plugin settings
- [ ] Verify settings POST call
- [ ] Disable plugin toggle
- [ ] Uninstall plugin
- [ ] Full lifecycle workflow (mock data)

**Estimated:** 10-15 tests, K_MAX=5

---

### 2. Tier A Migration — Code Review Example (k=3-4)

**Goal:** Migrate built-in Code Review skill to plugin, document process

**Steps:**
1. Extract `/core/skills/code_review.py` → `/core/orchestration/plugin_system/plugins/code-review/`
2. Create manifest, plugin class, settings schema
3. Wire MCP endpoint
4. Test enable/disable in Console
5. Document in TIER_A_MIGRATION_GUIDE.md

**Files to create:**
- `plugins/code-review/__init__.py` (Plugin class)
- `plugins/code-review/manifest.json` (Metadata)
- `plugins/code-review/main.py` (Business logic)
- `tests/test_code_review_migration.py` (Integration test)

**Estimated:** 3-4 hours, K_MAX=5

---

### 3. JSON Schema → React Form Generator (k=5+)

**Goal:** Auto-generate settings forms from JSON Schema (not handwrite each one)

**Design:**
```typescript
// core/console/web-next/src/components/JsonSchemaForm.tsx
export function JsonSchemaForm({
  schema: Record<string, any>,
  values: Record<string, any>,
  onChange: (key: string, value: any) => void
}): JSX.Element
```

**Features:**
- Text inputs for strings
- Selects for enums
- Sliders for integer ranges
- Checkboxes for booleans
- Nested objects (recursive)
- Validation feedback

**Estimated:** 2-3 hours, K_MAX=5

---

### 4. Plugin Ratings + Versioning UI (k=6+)

**Goal:** Show plugin reviews, auto-update policies, version history

**UI Elements:**
- Star rating (1-5) with count
- Install count badge
- Version selector dropdown
- Auto-update policy toggle (major/minor/patch/none)
- Changelog modal

**API Endpoints:**
- GET `/api/plugins/{id}/ratings` (mock for v1)
- GET `/api/plugins/{id}/versions` (version history)
- POST `/api/plugins/{id}/version-policy` (update policy)

**Estimated:** 2-3 hours, K_MAX=5

---

## Recommended Execution Order

1. **E2E Suite** (Phase 3.1) — validates everything works end-to-end
2. **Code Review Migration** (Phase 3.2) — proves Tier A process works
3. **Form Generator** (Phase 3.3) — improves UX for settings
4. **Ratings UI** (Phase 3.4) — marketplace feature completeness

**Total Effort:** 8-12 hours, K_MAX=20 spread across 4 phases

---

## Starting Phase 3

### Prerequisites
- [ ] Playwright installed (`npm install -D @playwright/test`)
- [ ] E2E tests can run locally (`npm run test:e2e`)
- [ ] Code Review skill code is accessible in repo
- [ ] Team agrees on Tier A migration strategy

### First Steps (Day 1)
1. Implement 5-7 core E2E tests
2. Get Playwright running locally
3. Test golden path (install → enable → disable)
4. Document test structure for team

### Second Steps (Day 2-3)
1. Start Code Review migration
2. Test plugin lifecycle integration
3. Update migration guide with learnings
4. Prepare for form generator

---

## Success Criteria

- [ ] 15+ E2E tests passing
- [ ] Code Review skill running as Tier A plugin
- [ ] Form generator handles 80% of use cases (enum, string, integer, boolean)
- [ ] Ratings UI shows mock data correctly
- [ ] All new tests passing (target: 70+ total tests)
- [ ] Zero regressions from Phase 1-2b

---

## Known Risks

1. **Playwright headless mode** — may need special config for CI
2. **Tier A migration** — requires testing each skill independently
3. **Form generator complexity** — nested schemas could be tricky
4. **Ratings API** — currently mock; need real backend later

---

## Reference

- ADR-0XXX: `/docs/concepts/ADR-0XXX-PLUGIN_SYSTEM.md`
- Tier A Guide: `/docs/concepts/TIER_A_MIGRATION_GUIDE.md`
- E2E Framework: `/core/console/corvin_console/web-next/playwright.config.ts`
- API Spec: `/core/orchestration/plugin_system/managers/api.py`

---

**Phase 3 is well-scoped and ready to start. Take it fresh — you've earned it.** ✨
