# Plugin Marketplace — Implementation Plan

**Status:** Ready to Build  
**Duration:** 4 weeks  
**Risk:** Medium

---

## Phase 1: Backend Foundation (Week 1)

**API Endpoints** → `core/console/routes/plugins.py`
- `POST /api/v1/plugins/install` — from GitHub repo
- `GET /api/v1/plugins/marketplace` — browse available
- `GET /api/v1/plugins/installed` — list installed
- `GET/POST /api/v1/plugins/{id}/config` — manage config
- `DELETE /api/v1/plugins/{id}` — uninstall

**Installation Engine** → `core/orchestration/tasks/plugin_install_task.py`
- Git clone → `~/.corvin/plugins/`
- Manifest validation + panel registration
- Audit logging + rollback on failure

**Registry Manager** → `core/plugins/plugin_registry.py`
- Track installed plugins in `registry.json`
- Config versioning + change detection

**GitHub Client** → `core/console/github_client.py`
- Search (topic:corvin-plugin)
- Rate-limit handling + 24h cache
- Manifest verification

**Success:** Install plugin in <5s, all ops logged

---

## Phase 2: Console UI (Week 2)

**Plugins Section** → `web-next/src/pages/vibe/plugins.tsx`
- Tabs: Installed | Marketplace
- Search + install buttons
- Progress indicator during install

**Settings Panel** → `web-next/src/components/PluginSettingsPanel.tsx`
- Auto-render config form from manifest
- Dark mode support
- Save + validation

**Navigation** → `web-next/src/components/layout.tsx`
- Add "Plugins" under Vibe Engineering
- Auto-populate from installed registry

**Success:** Panel visible after install, no crashes

---

## Phase 3: Integration (Week 3)

**Auto-Registration**
- Plugin install → immediately register panel
- Manifest → PANELS registry + NAV_GROUPS

**Error Handling**
- Rollback: remove directory on failure
- Config: restore on error
- UI: graceful degradation

**Audit Verification**
- Verify all events in audit.jsonl
- Check signatures (plugin_id, timestamp)

**Success:** 10+ end-to-end tests pass, 0 crashes

---

## Phase 4: Production Hardening (Week 4)

**Security** — manifest validation, permission model, secret masking
**Performance** — GitHub API, 20+ plugins, UI responsiveness
**Documentation** — install guide, troubleshooting, dev guide
**Rollback** — procedure tested

**Success:** Security review 0 high findings, canary-ready

---

## Critical Dependencies

```
Phase 1 → Phase 2 → Phase 3 → Phase 4
(API)     (UI)       (Integration) (Hardening)
```

- Brain Engine must be available for PluginInstallTask
- GitHub network access (with cached fallback)
- audit.jsonl write access
- Console PANELS registry modification

---

## Risks & Mitigations

| Risk | Mitigation |
|---|---|
| GitHub rate-limit | Cache marketplace (24h), fallback to local |
| Plugin crash breaks Console | Run in subprocess, isolated |
| Manifest injection | Schema validation, fail-closed |
| Config secrets leak | Hash in audit, never log values |
| Panel registration fails | Install succeeds, plugin disabled + warning |

---

## Success Criteria

- [ ] <5s install time (happy path)
- [ ] Failed install ≠ console break
- [ ] All ops in audit.jsonl with signatures
- [ ] Plugins can't read each other's config
- [ ] 10+ test plugins work end-to-end
- [ ] Operator troubleshoots in <5min
- [ ] Security review: 0 high findings

---

**Effort:** 40–50 eng hours | **Complexity:** Medium | **Start:** ASAP
