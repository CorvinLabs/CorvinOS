# Forge Bundle — sharing forged artifacts as one ZIP (ADR-2229)

**Source:** `core/forge_bundle/` · **Tests:** `tests/forge_bundle/` · **ADR:** `corvin_decisions/decisions/ADR-2229-*.md`

A Forge Bundle carries Skills, Tools, Layer definitions and Plugins from one
install to another. It is an **envelope**, not a new artifact format: every
artifact keeps its own forge's serialization, and importing one only
**proposes**: each artifact enters through its own forge's existing intake, so
it passes the same gates as a locally forged one.

## Status

| Phase | What | State |
|---|---|---|
| 1 | Envelope schema + fail-closed validator | **built** |
| 2 | Export | **built** — CLI (`scripts/forge_bundle_cli.py export`) + console route |
| 3 | Import → per-forge intake, incl. the tool quarantine | **built** — `import_module.py`, `tool_quarantine.py`, `inventory.py` |
| 4 | Console UI | **built** — Forge → **Bundles** tab (`/app/forge?tab=bundles`) |

## Export (Phase 2) — `core/forge_bundle/export.py`

`build_bundle(*, bundle_id, bundle_version, selections, tenant_id, description=None) -> BundleResult`
collects each selection through that forge's own storage, assembles the
envelope, and — before returning anything — **round-trips the built ZIP
through `validate_bundle`** (no `known`, since export doesn't know a future
target's inventory). A bundle this process could not later import is never
handed out; this is also the only thing standing between a selection and a
credential-shaped string reaching disk (`test_self_validation_rejects_a_secret_leaking_tool`).
Export is read-only: nothing is written to any registry.

| Selection | Reads via | Note |
|---|---|---|
| `SkillSelection(skill_id, version)` | `core.skills.skill_packager.SkillPackager`, the same `skills_gen`/`skills_packages` roots `/v1/skill-forge/package` already writes | idempotent — re-exporting reuses the existing package ZIP rather than re-running `package()` (which raises `FileExistsError` on a second call) |
| `ToolSelection(name, version)` | `forge.multi_registry.MultiRegistry.get()` + the impl file on disk | **`version` is caller-supplied** — `ToolSpec` has no version field; `spec.json` carries only `name`/`description`/`input_schema`/`runtime`/`version`/`impl_filename`, never `scope`/`call_count`/`promoted`/`meta` |
| `LayerSelection(entry_id, version)` | `core.orchestration.layer_forge.registry.LayerRegistry.get()` | `status`/`_created_at`/`_promoted_at` are stripped before export (D5: registry state never travels) |
| `PluginSelection(plugin_id, version, wheel_path)` | a wheel the operator already built via Plugin Builder (ADR-0262) | export never calls the builder — building is its own audited, mutating operation; `collect()` only reads the given path |

`requires` (cross-artifact dependencies) are declared per selection by the
caller, not auto-resolved from the registries — nothing in Skill/Tool/Layer
exposes a uniform dependency list to walk. The CLI does not expose `requires`
yet; use `build_bundle` directly for that.

Audits exactly one event, after a successful build: `forge_bundle.exported`.
A refused export (self-validation failure, missing artifact) is reported to
the caller but not audited — nothing changed.

### CLI

```bash
python scripts/forge_bundle_cli.py export \
  --id acme-automation --version 2.0.0 --output bundle.zip \
  --skill summarize@1.0.0 --tool csv.count@0.2.0 \
  --layer acme.audit-l34@1.0.0 \
  --plugin acme-audit-sink@0.5.0:/path/to/wheel.whl \
  [--tenant TID] [--description TEXT]
```

Exit codes: 0 success, 1 refused/failed, 2 usage error.

## Import (Phase 3) — `core/forge_bundle/import_module.py`

`import_bundle(data, *, tenant_id, actor) -> ImportResult` first runs
`validate_bundle(data, known=inventory.known(tenant_id))` — the staleness stage
always runs on import. A rejected bundle writes nothing except
`forge_bundle.import_rejected` (stage only, never the reason text) and raises
`BundleImportError(stage, reason)`.

A valid bundle is recorded (`forge_bundle.import_validated`), then each artifact
goes through its OWN forge. For every artifact `forge_bundle.artifact_staged`
commits BEFORE the intake runs; an intake that then fails is recorded as
`forge_bundle.artifact_failed` (`phase`, `error_class`) and reported in that
artifact's outcome — the other artifacts still land. A chain write that does
not commit raises `ForgeBundleAuditError` and stops the import there.

| Kind | Intake | Outcome status |
|---|---|---|
| skill | `SkillInstaller(<corvin_home>/skills_installed)`, checksum = the envelope's sha256 | `installed` |
| layer | `LayerForgeOrchestrator(actor="bundle_import").create_layer_definition` — every gate, enforcement and the review run | `forged` (same registry state as a locally forged layer) |
| plugin | `StagingManager.validate_zip_file` + `store_staged_upload` — the plugin-upload store | `pending_approval` — approve at `/plugin-uploads/{id}/approve` |
| tool | `ToolQuarantine.stage` | `quarantined` — accept/reject below |

A plugin payload must be an ADR-0511 package (`manifest.json` with
`name`/`version`/`author`). A bare wheel — which `PluginSelection` will happily
export — fails at intake with that reason; it is not converted.

### Tool quarantine — `core/forge_bundle/tool_quarantine.py`

`<tenant_home>/global/forge_bundle/quarantine/tools/<qid>/{meta.json, impl.py|impl.sh}`,
dir `0700`, files `0600`, written to a temp dir and renamed. `qid` is a random
32-hex token and the only handle accepted (`QID_RE`) — no path or glob is built
from caller input. Staging refuses: a spec naming a different tool than the
envelope, a runtime other than `python`/`bash`, an invalid Tool Forge name,
non-UTF-8 code, and a credential-shaped string (the validator's credential
detectors).

`accept(tenant_id, qid, actor=)` re-hashes the implementation (refuses if it
changed on disk), re-scans it, refuses if a tool of that name already exists
(`QuarantineConflict`, never overwrites), audits
`forge_bundle.quarantine_accepted`, THEN calls `MultiRegistry.create(scope="user",
meta={"origin": "forge_bundle", "bundle_id", "bundle_version", "bundle_tool_version",
"origin_verified": False})` and removes the entry. A create that fails after the
record (e.g. the ADR-0701 licence gate → `PermissionError`) is recorded as
`artifact_failed` (`phase="accept"`) and the entry stays. `reject` audits
`forge_bundle.quarantine_rejected` and deletes the entry.

### Inventory — `core/forge_bundle/inventory.py`

`known()` reads skills (`skills_gen/*/skill.json` + `skills_installed` registry),
tools (`MultiRegistry.list()`), layers (Layer Forge registry). Tools carry no
version, so an existing tool satisfies a requirement on ANY version
(`AnyVersion`). Installed plugins are not enumerated yet: a bundle requiring a
plugin outside itself is refused as stale. `exportable()` feeds the console's
export picker; it lists no plugins (a plugin export needs a package path on
disk — CLI only).

## Console routes (Phase 4) — `routes/forge_bundle_routes.py`

Paths are RELATIVE (`/forge-bundles/...`): the console router is mounted under
`/v1/console` by the gateway, so a router-level `/v1/console` prefix doubles it.
Until 2026-10-06 every route here lived at `/v1/console/v1/console/forge-bundles/...`
and answered 404 at every path the UI called;
`tests/forge_bundle/test_console_routes_e2e.py` now mounts the router exactly
as the gateway does and checks both the real and the doubled path.

| Route | Auth | Answers |
|---|---|---|
| `GET /forge-bundles/exportable` | session | `{skills, tools, layers, plugins: []}` |
| `POST /forge-bundles/export` | session + CSRF | ZIP download · 400 plugin selection · 422 `ExportError` · 503 audit down |
| `POST /forge-bundles/validate` (multipart `file`) | session + CSRF | `{valid: true, …report}` or `{valid: false, stage, reason}` — writes nothing, audits nothing |
| `POST /forge-bundles/import` (multipart `file`) | session + CSRF | `ImportResult.to_dict()` · 413 > 50 MiB · 422 `{stage, reason}` · 503 audit down |
| `GET /forge-bundles/quarantine` | session | `{items, count}` |
| `POST /forge-bundles/quarantine/{qid}/accept` | session + CSRF | 404 unknown id · 409 name taken · 403 licence gate · 422 changed/unsafe · 503 audit down |
| `POST /forge-bundles/quarantine/{qid}/reject` | session + CSRF | 404 unknown id · 503 audit down |

UI: `web-next/src/components/forge/ForgeBundlesPanel.tsx` (API in
`src/lib/api/forge-bundles.ts`) — Export picker, Import (validate → preview with
**Unverified origin** → import → per-artifact outcome), review queue with
Accept/Reject.

## Audit events

| Event | Severity | When | Fields |
|---|---|---|---|
| `forge_bundle.exported` | INFO | after a successful build | bundle_id, bundle_version, artifact_count, total_bytes |
| `forge_bundle.import_rejected` | WARNING | bundle failed validation | rejected_stage, actor |
| `forge_bundle.import_validated` | INFO | before any intake | bundle_id, bundle_version, artifact_count, total_uncompressed_bytes, unscanned_files_count, actor |
| `forge_bundle.artifact_staged` | INFO | before each intake | bundle_id, artifact_kind/id/version, status (intended), actor |
| `forge_bundle.artifact_failed` | WARNING | intake or accept failed after its record | … , phase, error_class |
| `forge_bundle.quarantine_accepted` | INFO | before the registry write | … , quarantine_id |
| `forge_bundle.quarantine_rejected` | INFO | before the entry is deleted | … , quarantine_id |

All carry `tenant_id`; registered in `core/forge_bundle/audit.py` and
`forge/security_events.py` (`test_module_allowlist_matches_the_central_registry`).
Metadata only — never manifest bodies, code, or free-text reasons.

## Archive layout

```
forge-bundle.json                                   envelope (the only root file)
artifacts/skill/<id>@<version>/<id>-<version>.zip   ADR-0674 skill ZIP
artifacts/tool/<id>@<version>/spec.json + impl      Tool Forge spec + one impl file
artifacts/layer/<id>@<version>/manifest.json        ADR-2222 layer manifest
artifacts/plugin/<id>@<version>/plugin.zip          ADR-0511 plugin ZIP
```

## Envelope (`format_version: 1`)

```json
{
  "format": "corvin.forge-bundle",
  "format_version": 1,
  "id": "acme-automation",
  "version": "2.0.0",
  "created_at": "2026-10-06T12:00:00Z",
  "description": "optional, ≤ 2000 chars",
  "artifacts": [
    {
      "kind": "tool", "id": "csv.count", "version": "0.2.0",
      "files": [{"path": "artifacts/tool/csv.count@0.2.0/impl.py", "sha256": "<64 hex>", "size": 123}],
      "requires": [{"kind": "skill", "id": "summarize", "version": "1.0.0"}]
    }
  ]
}
```

Strict parsing: unknown keys are refused. Ids use the ADR-0674 safe-segment
alphabet (`^[A-Za-z0-9][A-Za-z0-9._+-]{0,127}$`, no `..`), versions are semver,
`kind` ∈ `skill|tool|layer|plugin`, one version per `(kind, id)`. A requirement
without `version` accepts any version.

## Validation — `validate_bundle(data: bytes, *, known=None) -> BundleReport`

Six stages, in order, fail-closed. The first defect raises
`BundleRejected(stage, reason)`; nothing is extracted or written either way.

| Stage | Rejects |
|---|---|
| `container` | unreadable ZIP · > 50 MiB compressed / > 200 MiB uncompressed / > 2 000 entries · absolute, `..`, empty-segment, backslash, `:` or control-char names · symlink and encrypted entries · names equal up to case (Windows/macOS collisions) · compression ratio > 100:1 on files ≥ 1 MiB · any file outside `artifacts/` except the envelope |
| `envelope` | missing / > 1 MiB / non-UTF-8-JSON envelope · any schema defect above |
| `integrity` | a declared file missing, an undeclared file present, size or sha256 mismatch, CRC failure |
| `references` | an in-bundle requirement on the wrong version · any dependency cycle (iterative DFS) |
| `staleness` | (only with `known`) a requirement outside the bundle that the target does not have |
| `secrets` | a credential-shaped string (private key, AWS/GitHub/Slack/Google/`sk-` keys, JWT) in the envelope or any UTF-8 payload, including inside nested `.zip` payloads, which get the same container rules · a `.zip` nested two levels deep |

`known` is the target install's inventory, `{kind: {id: versions}}`, injected
by the caller so the validator stays pure. Without it, requirements that point
outside the bundle come back in `report.unchecked_references`. An importer must
pass `known`.

The secret stage reuses `core.pii.sensitive.detect_sensitive_types` (fail-closed: a
scan error rejects) but only its **credential** detectors. Its prose detectors
(names, addresses, @-handles, entropy, `token = …`) fire on ordinary source code
and on every sha256 in the envelope. A rejection names the detector, never the
matched value. Non-UTF-8 payloads are not scanned and are listed in
`report.unscanned_files`; they are never reported as clean.

`report.origin_verified` is always `False`: format v1 carries no origin proof.
Checksums prove the bundle was not altered in transit, not who made it. Any UI
must show "unverified origin".

## Never in a bundle

Audit records, tenant ids, registry status (`proposed`/`accepted`/promoted),
call counts, operator config, secrets. Everything imported starts in its forge's
entry state.

## Relation to the other ZIP formats in the tree

`core/skills/skill_packager.py` (ADR-0674, one skill) and `core/plugins/staging.py`
(ADR-0511, one plugin) are reused as payload formats / intakes. `core/package_manager/`
+ `routes/packages.py` (its docstring cites ADR-0268, which is *task-engine-parameters*
in this corpus; it sits behind the default-off flag `package_marketplace_ui`) and
`core/awpkg/` (workflow packages) are separate, and this ADR does not reconcile them.
