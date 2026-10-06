# Forge Bundle Phase 4 Implementation — Quarantine Management + Export/Validate

**ADR-2229 Phase 4** — Backend routes for quarantine management, export, and validation.

## Routes Implemented

### Quarantine Management

**GET /v1/console/forge-bundles/quarantine**
- List all quarantined tools and staged plugins for tenant
- Returns: `{tools: [...], plugins: [...]}`
- Each tool: `{quarantine_id, tool_id, version, bundle_id, staged_at, spec}`
- No CSRF required (read-only)

**POST /v1/console/forge-bundles/quarantine/{quarantine_id}/accept**
- Promote quarantined tool to active registry
- Audit-First: emit `forge_bundle.quarantine_accepted` BEFORE registry write
- If audit fails: operation is blocked (fail-closed)
- If registry write fails: quarantine entry remains (not cleaned up)
- Returns: `{status: "accepted", tool_id, version, now_live: true}`
- CSRF required (mutation)

**POST /v1/console/forge-bundles/quarantine/{quarantine_id}/reject**
- Delete quarantined tool without loading into registry
- Query param: `reason` (optional, audit-logged)
- Audit: emit `forge_bundle.quarantine_rejected`
- Deletes quarantine entry
- Returns: `{status: "rejected", tool_id, version, reason}`
- CSRF required (mutation)

### Export & Validation

**GET /v1/console/forge-bundles/export/available**
- List available artifacts for export (skills, tools, layers, plugins)
- Returns: `{skills: [...], tools: [...], layers: [...], plugins: [...]}`
- Each artifact: `{id, version, description}`
- TODO: Implement actual inventory collection from each forge

**POST /v1/console/forge-bundles/export**
- Build and export a Forge Bundle as downloadable ZIP
- Body: `{bundle_id, bundle_version, description, selections: [{kind, id, version}]}`
- Selections drive artifact collection (user controls what's in the bundle)
- Returns: ZIP file with Content-Disposition attachment header
- Audit: `forge_bundle.exported` (emitted by export.build_bundle())
- CSRF required (mutation)
- TODO: Implement Selection → Selection object conversion

**POST /v1/console/forge-bundles/validate**
- Validate a bundle without importing
- Multipart form: `file` (ZIP)
- Stages: container, envelope, integrity, references, staleness, secrets
- Returns: `{bundle_id, bundle_version, origin_verified, artifact_count, total_uncompressed_bytes, unchecked_references, unscanned_files}`
- Audit: `forge_bundle.validated` (emitted before response, best-effort)
- No CSRF required (read-only validation)

## Audit Events (Phase 4)

**forge_bundle.quarantine_accepted**
- Emitted: when operator accepts a quarantined tool
- Timing: BEFORE registry write (audit-first)
- Fields: `artifact_kind`, `artifact_id`, `artifact_version`, `quarantine_id`, `tenant_id`, `user_id`
- Severity: INFO

**forge_bundle.quarantine_rejected**
- Emitted: when operator rejects a quarantined tool
- Timing: BEFORE quarantine cleanup
- Fields: `artifact_kind`, `artifact_id`, `artifact_version`, `quarantine_id`, `tenant_id`, `user_id`
- Severity: INFO

**forge_bundle.validated**
- Emitted: when a bundle is validated (not imported)
- Timing: AFTER validation completes (not audit-first)
- Fields: `bundle_id`, `origin_verified`, `unchecked_references_count`, `unscanned_files_count`, `total_uncompressed_bytes`, `tenant_id`, `user_id`
- Severity: INFO

## Error Handling

| Status | Scenario |
|--------|----------|
| 400 | Missing bundle_id/bundle_version, empty selections, malformed request |
| 401 | tenant_id missing from session |
| 413 | Bundle exceeds size limit (50 MiB) |
| 422 | Validation/quarantine lookup failed, export validation failed |
| 500 | Unexpected error, audit write failed |

**Audit-First Failure (accept route):**
- If audit write fails: operation is blocked, HTTPException 500 raised
- Caller must treat as failed operation
- Quarantine entry remains (operator can retry)

## Implementation Notes

### Accept Flow (Audit-First Pattern)
1. Read quarantine entry (`accept_quarantined_tool()` reads from disk)
2. Emit `forge_bundle.quarantine_accepted` audit event
3. If audit fails: raise HTTPException 500, return without proceeding
4. Load into registry: `ToolRegistry.create(spec, impl_bytes)`
5. Delete quarantine entry: `cleanup_quarantine_entry()`
6. Return success response

### Validation Flow (Best-Effort Audit)
1. Read and validate bundle (pure, no writes)
2. Return validation report to caller
3. Emit audit event (best-effort: failure is not fatal)
4. Caller gets the report regardless

### Export Flow (Read-Only + Audit)
1. List requested artifacts from each forge (read-only)
2. Build ZIP: `build_bundle()` (includes self-validation)
3. Return ZIP bytes with attachment header
4. Audit event emitted by build_bundle() itself

## Testing

**Unit Tests** (`test_phase4_audit.py`):
- Audit event registration in ALLOWED_FIELDS + SEVERITY
- Audit emit success/failure scenarios
- Fail-closed pattern verification

**E2E Tests** (`test_phase4_quarantine_e2e.py`):
- Quarantine list (empty and with tools)
- Accept flow (success, not found, audit failure)
- Reject flow (success, not found, audit failure)
- Export route (missing params, selections, oversized)
- Validate route (empty/invalid/oversized bundles)
- Audit event emission verification

## Known Limitations / TODO

1. **Export artifact inventory** — `list_available_artifacts()` returns empty placeholder
   - Needs: skill registry traversal, tool registry traversal, layer forge traversal, plugin staging manager
   - Dependency: skill/tool/layer/plugin registries must expose query API

2. **Selection → Selection conversion** — `export_bundle()` route has TODO
   - Needs: build SkillSelection, ToolSelection, LayerSelection, PluginSelection from request dicts
   - Dependency: registries must exist and be loadable to validate selections

3. **Plugin quarantine** — only tools are currently quarantined
   - TODO: add staged plugins to `/quarantine` list response
   - Dependency: StagingManager (ADR-0511) must expose query API

4. **Audit-first on reject** — currently best-effort
   - Could strengthen to audit-first if needed (emit before cleanup)
   - Current implementation: audit after read, before cleanup is safe enough

## Security Considerations

- **Audit-First (accept route):** chain write failure blocks operation → system can recover by retrying
- **Size limits:** 50 MiB max bundle (configurable)
- **Credential detection:** tool quarantine scans for secrets (fail-closed)
- **CSRF:** all mutation routes require valid CSRF token
- **Tenant isolation:** all operations scoped to session tenant_id

## Performance

- **Quarantine list:** O(n) directory scan, loaded on demand
- **Accept:** 1 disk read (quarantine), 1 registry write (ToolRegistry), 1 audit write
- **Export:** depends on artifact count and registry traversal
- **Validate:** scales with bundle size (6 validation stages, no writes)

## Migration Path

Phase 4 is backward-compatible with Phase 3:
- Existing quarantined tools can be accepted/rejected
- Import endpoint unchanged
- Export/validate are new, no legacy routes affected

## References

- ADR-2229: Forge Bundle Architecture
- Tool Quarantine Workflow: `core/forge_bundle/tool_quarantine.py`
- Export Logic: `core/forge_bundle/export.py`
- Validation Logic: `core/forge_bundle/validate.py`
- Audit Pattern: `core/forge_bundle/audit.py`
