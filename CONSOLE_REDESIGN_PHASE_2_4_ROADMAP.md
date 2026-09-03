# Console Redesign Phase 2-4 Completion Roadmap

**Status:** Phase 1 ✅ DONE | Phase 2-4 🚧 IN PROGRESS

**Commit:** 9127ebfc (Phase 1: manifest endpoint + tests)

---

## Phase 2: Manifest-Driven UI Rendering

**DONE:**
- ✅ Backend `/api/console/manifest` endpoint (capabilities.py)
- ✅ Frontend `useConsoleManifest()` hook (capabilities.ts)
- ✅ `ManifestNavRenderer` component (sidebar from manifest)
- ✅ `manifestPanelRoutes()` (routes from manifest)
- ✅ `layout-refactored.tsx` (manifest-driven AppLayout)
- ✅ Fallback to builtin panels if manifest fails

**TODO:**
- [ ] Replace old layout.tsx with layout-refactored.tsx
- [ ] Delete hardcoded PANELS + NAV_GROUPS from panels/registry.tsx
- [ ] Update App.tsx to use manifestPanelRoutes()
- [ ] Delete old `vibe-engineering.tsx` shadow file
- [ ] Delete old `components/layout.tsx`
- [ ] Phase 2 integration tests (manifest → nav → routes)
- [ ] All tests green

**Effort:** 3 days (1 dev)

---

## Phase 3: Plugin + Skill Auto-Registration

**DONE:**
- ✅ Plugin panel registry exists (ADR-0455)
- ✅ Skill registry exists (skill_registry_phase1.py)
- ✅ `_get_plugin_panels()` in capabilities.py
- ✅ `_get_skill_panels()` in capabilities.py

**TODO:**
- [ ] Build `GenericPluginInspector.tsx` (config + audit + enable/disable)
- [ ] Build `SkillInspector.tsx` (learning + audit + config tabs)
- [ ] Wire plugin_panel_registry → manifest builder (DONE in code, test)
- [ ] Wire skill_registry → manifest builder (DONE in code, test)
- [ ] E2E test: install plugin → manifest updates → nav shows it
- [ ] E2E test: register skill → manifest updates → nav shows it
- [ ] All tests green

**Effort:** 4 days (1-2 devs)

---

## Phase 4: Vibe Engineering Redesign + Cleanup

**TODO:**
- [ ] Redesign: 5 Vibe panels → 1 Dashboard + 5 tabs
  - Dashboard (primary)
  - Brain Monitor (tab)
  - Context Intelligence (tab)
  - Learning Hub (tab)
  - Session Explorer (tab)
- [ ] Build `VibeDashboard.tsx` (3-column layout + tab navigation)
- [ ] Delete old pages (brain-monitor.tsx, context-intelligence.tsx, learning-hub.tsx, session-explorer.tsx)
- [ ] Update manifest to single Vibe entry
- [ ] Tests: vibe nav, route, tabs
- [ ] All tests green

**Effort:** 3 days (1 dev)

---

## Production Readiness Checklist

- [ ] All Phase 1-4 tests green
- [ ] E2E proof: plugin install → auto-appears → click → works
- [ ] E2E proof: skill register → auto-appears → click → works
- [ ] Manifest endpoint latency <100ms (p99)
- [ ] Manifest cache hit rate >90%
- [ ] Fallback behavior verified (manifest down → core panels work)
- [ ] No hardcoded PANELS/NAV_GROUPS left
- [ ] No shadow files (vibe-engineering.tsx deleted)
- [ ] Vibe simplified (5 panels → 1)
- [ ] Audit events logged (panel_opened, etc.)
- [ ] Docs updated (plugin-console-integration.md, etc.)
- [ ] ADRs complete (0560, 0561)
- [ ] All commits ADR-compliant

---

## Risk Mitigations (ADR-0561 Synthesis)

✅ **Latency:** 200ms timeout + fallback to builtin panels  
✅ **Staleness:** Hash-based invalidation on registry change  
✅ **Gating bypass:** Dual-layer (manifest + route checks capability)  
✅ **Bundle security:** iframe sandbox + CSP + SRI hash + audit  
✅ **Versioning:** Manifest v2.0 with forward compat  

---

## Estimated Timeline

- Phase 2: 3 days (replace layout, delete hardcoded arrays, tests)
- Phase 3: 4 days (plugin/skill inspectors, auto-reg, E2E tests)
- Phase 4: 3 days (Vibe redesign, cleanup)
- **Total:** 10 days (1-2 devs)

---

## Quick Wins (Parallelizable)

- GenericPluginInspector (Phase 3) can be built in parallel with Phase 2
- SkillInspector (Phase 3) can be built in parallel with Phase 2
- Vibe Dashboard design (Phase 4) can start while Phase 2-3 finish

---

## Success Metrics (Production Ready)

| Metric | Target | Measure |
|---|---|---|
| Manifest latency (p99) | <100ms | APM |
| Cache hit rate | >90% | Browser DevTools |
| Panel load time | <200ms | E2E test |
| Test coverage (console) | >90% | Jest |
| E2E proof (plugin) | PASS | install → nav → click → works |
| E2E proof (skill) | PASS | register → nav → click → works |
| Hardcoded arrays | 0 (deleted) | Code grep |
| Vibe panels | 1 (was 5) | Code review |
| Audit events | All logged | Audit trail inspection |

---

**Next:** Implement Phase 2-4 in parallel where possible. Aim for Production Ready in 10 days.
