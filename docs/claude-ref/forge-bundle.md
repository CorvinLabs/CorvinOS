# Forge Bundle — sharing forged artifacts as one ZIP (ADR-2229)

**Source:** `core/forge_bundle/` · **Tests:** `tests/forge_bundle/` · **ADR:** `corvin_decisions/decisions/ADR-2229-*.md`

A Forge Bundle carries Skills, Tools, Layer definitions and Plugins from one
install to another. It is an **envelope**, not a new artifact format: every
artifact keeps its own forge's serialization, and importing one only
**proposes**: each artifact enters through its own forge's existing intake, so
it passes the same gates as a locally forged one. Rule of thumb: a bundle can
never make something active that the operator could not have made active by
hand through the same forge.

## Status

| Phase | What | State |
|---|---|---|
| 1 | Envelope schema + fail-closed validator | **built** |
| 2 | Export | **built** — CLI (`scripts/forge_bundle_cli.py export`) + console route |
| 3 | Import → per-forge intake, incl. the tool quarantine | **built** — `import_module.py`, `tool_quarantine.py`, `inventory.py` |
| 4 | Console UI | **built** — Forge → **Bundles** tab (`/app/forge?tab=bundles`) |

Hardened by a three-round adversarial review on 2026-10-06; every finding has a
named test (`tests/forge_bundle/test_import.py` test names carry the finding id).

## Export (Phase 2) — `core/forge_bundle/export.py`

`build_bundle(*, bundle_id, bundle_version, selections, tenant_id, description=None) -> BundleResult`
collects each selection through that forge's own storage, assembles the
envelope, and — before returning anything — **round-trips the built ZIP
through `validate_bundle`**. A bundle this process could not later import is
never handed out, and a credential-shaped string never reaches the caller.
Export changes no registry or active state. The one write it causes:
`SkillPackager` refreshes the skill folder's own `.forge/` metadata while
packaging.

| Selection | Reads via | Note |
|---|---|---|
| `SkillSelection(skill_id, version)` | `SkillPackager` over `<corvin_home>/skills_gen/<id>/`, packaged FRESH into a temp dir on every export | a cached package could be older than the folder; the version must match `skill.json` |
| `ToolSelection(name, version)` | `MultiRegistry.get()` + the impl file | **`version` is caller-supplied** — `ToolSpec` has none. `spec.json` = `name`/`description`/`input_schema`/`runtime`/`version`/`impl_filename` + `meta` limited to `requirements`/`secrets` (key names)/`budget`/`deterministic` (`tool_quarantine.clean_tool_meta`); never scope, call counts, promotion |
| `LayerSelection(entry_id, version)` | `LayerRegistry.get()` | registry state (`status`, `review_flagged`, `review_flags`, every `_*` key) stripped (`import_module.clean_layer_manifest`) |
| `PluginSelection(plugin_id, version, package_path)` | an ADR-0511 plugin package on disk | must be a ZIP with `manifest.json` naming this id + version and an `author` — the shape the importing StagingManager accepts. A bare wheel is refused HERE, not at import. Export never builds a plugin |

Any error from a forge's internals becomes an `ExportError` with a message that
names the artifact, never a host path (console: 422). `requires` are declared by
the caller, not auto-resolved; the CLI does not expose them.

Audits one event after a successful build: `forge_bundle.exported`.

### CLI

```bash
python scripts/forge_bundle_cli.py export \
  --id acme-automation --version 2.0.0 --output bundle.zip \
  --skill summarize@1.0.0 --tool csv.count@0.2.0 \
  --layer acme.audit-l34@1.0.0 \
  --plugin acme-audit-sink@0.5.0:/path/to/plugin-package.zip \
  [--tenant TID] [--description TEXT]
```

The output directory is checked BEFORE the build (the build records the export),
and the file is written via a temp file + rename. Exit codes: 0 success, 1
refused/failed (JSON on stdout), 2 usage error.

## Import (Phase 3) — `core/forge_bundle/import_module.py`

`import_bundle(data, *, tenant_id, actor, may_install_skills) -> ImportResult`:

1. `check_bundle` = `validate_bundle(data, known=inventory.known(tenant_id))`.
   A refusal is recorded (`import_rejected`, stage only) and raised as
   `BundleImportError(stage, reason)`. An inventory that cannot be read is the
   refusal stage `inventory` (console: 503), never a pass.
2. `import_validated` is recorded.
3. Artifacts are staged in **dependency order** (declared `requires` plus a
   layer manifest's own `dependencies` on layers in the same bundle).
4. Per artifact: `artifact_intake_started` (the intent) commits BEFORE the
   intake; then `artifact_staged` (the actual outcome) or `artifact_failed`
   (`phase`, `error_class`). One artifact failing does not stop the others.
5. A record that cannot commit stops the import there:
   `BundleImportAborted.result` lists every outcome so far and the rest as
   `not_attempted` (console: 503 with that list) — never a blanket "nothing
   changed" after something did.

| Kind | Intake | Outcome |
|---|---|---|
| skill | `SkillInstaller(<corvin_home>/skills_installed)`, checksum = the envelope's sha256. That store is HOST-WIDE, so only the install owner (owner/admin session of the process tenant) may import a skill — same as the manual skill upload | `installed` |
| layer | `LayerForgeOrchestrator(actor="bundle_import").create_layer_definition` — every gate, enforcement rule and the review run on THIS install. Before that: travelled state dropped, ≤ 16 gates, gate/rule ids short identifiers, each gate names ONE test file under `tests/` | `forged`; detail notes a FLAGGED review on this install |
| plugin | `StagingManager.validate_zip_file` + `store_staged_upload` as `<id>-<version>.zip`; an identical package already pending is reported, not re-staged | `pending_approval` — approve at `/plugin-uploads/{id}/approve` |
| tool | `ToolQuarantine.stage` — a name that already exists here is refused (`failed`, "already exists"); an identical entry already queued is reused | `quarantined` |

### Tool quarantine — `core/forge_bundle/tool_quarantine.py`

`<tenant_home>/global/forge_bundle/quarantine/tools/<qid>/{meta.json, impl.py|impl.sh}`,
dir `0700`, files `0600`, written to a temp dir and renamed. `qid` is a random
32-hex token and the only handle accepted (`QID_RE`). `impl_filename` must be
the one its runtime implies, so a tampered `meta.json` cannot redirect a read.
Staging refuses: spec naming another tool, runtime other than `python`/`bash`,
an invalid Tool Forge name, non-UTF-8 code, a credential-shaped string, invalid
travelling meta (requirements must be plain package specifiers — no URLs,
paths or options; secret refs through the vault validator; budget = known limits
→ positive numbers).

A decision first **claims** the entry: one atomic rename `<qid>` →
`.claimed-<qid>`. Of two concurrent decisions exactly one proceeds; a decided
entry is never listed or decidable again even if deleting it fails.
`accept`: claim → hash + credential re-check → name still free → record
`quarantine_accepted` → `MultiRegistry.create(scope="user", meta={…travelled
meta, "origin": "forge_bundle", "bundle_id", "bundle_version",
"bundle_tool_version", "origin_verified": False})` → record `artifact_created`.
A failed create puts the entry back and records `artifact_failed`
(`phase="accept"`); the licence gate's `PermissionError` becomes
`QuarantineForbidden` (403). If `artifact_created` cannot commit after the tool
was created, `OutcomeNotRecorded` says exactly that (503). `reject`: claim →
record `quarantine_rejected` → delete.

### Inventory — `core/forge_bundle/inventory.py`

`known()` reads skills (`skills_gen/*/skill.json` + the installed-skill registry),
tools (`MultiRegistry.list()`), layers (Layer Forge registry); any unreadable
store raises `InventoryUnavailable`. Tools carry no version, so an existing tool
satisfies a requirement on ANY version (`AnyVersion`). Installed plugins are not
enumerated: a bundle requiring a plugin outside itself is refused as stale.
`exportable(tenant_id, include_skills=)` feeds the export picker; skills only
for the install owner; a skill folder whose name differs from its `skill_id` is
not offered; no plugins (CLI only).

## Console routes (Phase 4) — `routes/forge_bundle_routes.py`

Paths are RELATIVE (`/forge-bundles/...`): the console router is mounted under
`/v1/console` by the gateway, so a router-level `/v1/console` prefix doubles it.
`tests/forge_bundle/test_console_routes_e2e.py` mounts the router exactly as the
gateway does and checks both the real and the doubled path.

| Route | Auth | Answers |
|---|---|---|
| `GET /forge-bundles/exportable` | session | `{skills, tools, layers, plugins: []}` |
| `POST /forge-bundles/export` | session + CSRF | ZIP · 400 plugin selection · 403 skill for a non-owner · 422 `ExportError` · 503 audit down |
| `POST /forge-bundles/validate` (multipart `file`) | session + CSRF | `{valid: true, …report}` / `{valid: false, stage, reason}` · 503 inventory unreadable — writes nothing |
| `POST /forge-bundles/import` (multipart `file`) | session + CSRF | `ImportResult.to_dict()` · 413 > 50 MiB · 422 `{stage, reason}` · 503 `{message, …outcomes}` when stopped mid-way · 503 inventory/audit |
| `GET /forge-bundles/quarantine` | session | `{items, count}`; each item lists `requirements` and `secrets` it would get |
| `POST /forge-bundles/quarantine/{qid}/accept` | session + CSRF | 404 unknown/already decided · 409 name taken · 403 licence gate · 422 changed/unsafe · 503 audit (entry back in queue) / outcome unrecorded · 500 never echoes exception text |
| `POST /forge-bundles/quarantine/{qid}/reject` | session + CSRF | 404 · 503 audit |

Platform constraint, not a bundle rule: the audit chokepoint
(`security_events.write_event`, ADR-0562 D2) refuses a record tagged with a
tenant other than the process tenant. A session of another tenant therefore
cannot complete any audited forge-bundle mutation (503, nothing written) — the
same as every other audited console mutation.

UI: `web-next/src/components/forge/ForgeBundlesPanel.tsx` (API in
`src/lib/api/forge-bundles.ts`) — export picker, import (validate → preview with
**Unverified origin** → import → per-artifact outcome, including what landed
when an import stopped), review queue with the packages and secrets each tool
would get. A late answer for a previously picked file is dropped (generation
counter); uploads trigger the same stale-CSRF recovery as `api()`.

## Audit events

| Event | Severity | When | Fields (+ `tenant_id`) |
|---|---|---|---|
| `forge_bundle.exported` | INFO | after a successful build | bundle_id, bundle_version, artifact_count, total_bytes |
| `forge_bundle.import_rejected` | WARNING | bundle refused (incl. stage `inventory`) | rejected_stage, actor |
| `forge_bundle.import_validated` | INFO | before any intake | bundle_id, bundle_version, artifact_count, total_uncompressed_bytes, actor |
| `forge_bundle.artifact_intake_started` | INFO | before each intake | bundle_id, artifact_kind/id/version, status (intended), actor |
| `forge_bundle.artifact_staged` | INFO | after a successful intake | … , status (actual) |
| `forge_bundle.artifact_failed` | WARNING | intake or accept failed | … , phase, error_class |
| `forge_bundle.quarantine_accepted` | INFO | the operator's decision, before the registry write | … , quarantine_id |
| `forge_bundle.artifact_created` | INFO | the accepted tool now exists | … , quarantine_id |
| `forge_bundle.quarantine_rejected` | INFO | before the entry is deleted | … , quarantine_id |

Registered in `core/forge_bundle/audit.py` and `forge/security_events.py`
(`test_module_allowlist_matches_the_central_registry`). Metadata only — never
manifest bodies, code, or free-text reasons. Layer Forge additionally audits an
imported layer's gates under its own events; the gate-id check above keeps
those ids short identifiers.

## Archive layout

```
forge-bundle.json                                     envelope (the only root file)
artifacts/skill/<id>@<version>/<id>_<version>.zip     ADR-0674 skill ZIP (SkillPackager naming)
artifacts/tool/<id>@<version>/spec.json + impl        Tool Forge spec + one impl file
artifacts/layer/<id>@<version>/manifest.json          ADR-2222 layer manifest
artifacts/plugin/<id>@<version>/<package file>        ADR-0511 plugin package
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
| `container` | unreadable ZIP · > 50 MiB compressed / > 200 MiB uncompressed / > 2 000 entries · absolute, `..`, empty-segment, backslash, `:` or control-char names · symlink and encrypted entries · names equal up to case · compression ratio > 100:1 on files ≥ 1 MiB · any file outside `artifacts/` except the envelope · the same rules for every nested archive, an archive nested two levels deep, and nested archives together exceeding the 200 MiB budget |
| `envelope` | missing / > 1 MiB / non-UTF-8-JSON envelope · any schema defect above |
| `integrity` | a declared file missing, an undeclared file present, size or sha256 mismatch, CRC failure |
| `references` | an in-bundle requirement on the wrong version · any dependency cycle |
| `staleness` | (only with `known`) a requirement outside the bundle that the target does not have |
| `secrets` | a credential-shaped string (private key, AWS/GitHub/Slack/Google/`sk-` keys, JWT) anywhere: the envelope and every payload at every nesting level · JSON too deeply nested to scan |

A nested archive is recognised by its CONTENT (`zipfile.is_zipfile`), never by
its name — a wheel or a renamed `.bin` is opened like a `.zip`. Every payload is
scanned whatever its encoding: as raw bytes (latin-1, so one invalid UTF-8 byte
cannot switch the scan off), with NUL bytes removed (ASCII inside UTF-16), and,
when it parses as JSON, after unescaping (`AKIA…`). The scan reuses
`core.pii.sensitive.detect_sensitive_types` (a scan error rejects) but only its
**credential** detectors — its prose detectors fire on ordinary source code. A
rejection names the detector, never the matched value.

`known` is the target install's inventory, `{kind: {id: versions}}`. Without it,
requirements that point outside the bundle come back in
`report.unchecked_references`. An importer must pass `known`.

`report.origin_verified` is always `False`: format v1 carries no origin proof.
Checksums prove the bundle was not altered in transit, not who made it. Any UI
must show "unverified origin".

## Never in a bundle

Audit records, tenant ids, registry status (`proposed`/`accepted`/promoted,
review flags), call counts, operator config, secret VALUES. Everything imported
starts in its forge's entry state on the importing install.

## Relation to the other ZIP formats in the tree

`core/skills/skill_packager.py` (ADR-0674, one skill) and `core/plugins/staging.py`
(ADR-0511, one plugin) are reused as payload formats / intakes. `core/package_manager/`
+ `routes/packages.py` (its docstring cites ADR-0268, which is *task-engine-parameters*
in this corpus; it sits behind the default-off flag `package_marketplace_ui`) and
`core/awpkg/` (workflow packages) are separate, and this ADR does not reconcile them.
