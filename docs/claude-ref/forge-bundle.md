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

Hardened by a three-round adversarial review on 2026-10-06 (commits c2f151880,
1659428f9 and the round-3 fix commit; 36 + 31 + 19 findings). Proven findings
have regression tests in `tests/forge_bundle/` and the console's
`tests/unit/forge-bundles-panel.test.tsx`; most test names carry the finding id
(C…, A…, S… round 1; R2A/R2B round 2; R3A/R3B round 3). The round-3 fixes were
verified by those tests, not by a fourth review round.

## Export (Phase 2) — `core/forge_bundle/export.py`

`build_bundle(*, bundle_id, bundle_version, selections, tenant_id, description=None) -> BundleResult`
collects each selection through that forge's own storage, assembles the
envelope, and — before returning anything — **round-trips the built ZIP
through `validate_bundle`**. A bundle this process could not later import is
never handed out, and a credential-shaped string never reaches the caller.
Export changes no registry, store or active state: a skill is packaged from a
temporary COPY of its folder (the packager writes `.forge/` metadata into what
it packages), so nothing outside a temp dir is written.

| Selection | Reads via | Note |
|---|---|---|
| `SkillSelection(skill_id, version)` | `SkillPackager` over a temp copy of `<corvin_home>/skills_gen/<id>/`, packaged FRESH on every export | a cached package could be older than the folder; the version must match `skill.json`; a skill folder that is, or contains, a symlink is refused (and not offered) |
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

The output directory is checked BEFORE the build (the build records the export);
`--output` naming a directory is a usage error; the file is written through
`mkstemp` (O_EXCL, never follows a planted symlink) + `os.replace`. Exit codes: 0 success, 1
refused/failed (JSON on stdout), 2 usage error.

## Import (Phase 3) — `core/forge_bundle/import_module.py`

`import_bundle(data, *, tenant_id, actor, may_install_skills) -> ImportResult`:

1. `check_bundle` = `validate_bundle(data, known=inventory.known(tenant_id))`.
   A refusal is recorded (`import_rejected`, stage only) and raised as
   `BundleImportError(stage, reason)`. An inventory that cannot be read is the
   refusal stage `inventory` (console: 503), never a pass.
2. Limits: all layers of one bundle together may declare at most 16 quality
   gates (each is a pytest run inside the request) — otherwise refusal stage
   `limits`. Then `import_validated` is recorded.
3. Artifacts are staged in **dependency order** (declared `requires` plus a
   layer manifest's own `dependencies` on layers in the same bundle, with or
   without `type`). An artifact whose in-bundle dependency did not land fails
   ("depends on … which did not import") instead of binding to whatever older
   version this install has — unless this install already holds that exact
   `id@version` (then the requirement is met). Unusable JSON in a manifest or
   spec (including nesting too deep to parse) is a failed artifact, never a 500.
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
| layer | `LayerForgeOrchestrator(actor="bundle_import").create_layer_definition` — every gate, enforcement rule and the review run on THIS install. Before that: travelled state dropped, ≤ 16 gates, gate/rule/target/dependency ids short identifiers (they reach Layer Forge's audit records), each gate names ONE test file under `tests/` (no `::node`) | `forged`; detail notes a FLAGGED review on this install, or that Layer Forge wrote the layer but could not record its last step (only when the stored definition equals the bundle's, so a concurrent writer's layer is never reported as ours) |
| plugin | `StagingManager.validate_zip_file` + `store_staged_upload` as `<id>-<version>.zip`; an identical package already pending is reported, not re-staged | `pending_approval` — approve at `/plugin-uploads/{id}/approve` |
| tool | `ToolQuarantine.stage` — a name that already exists here (case-insensitively) is refused (`failed`, "already exists"); an entry identical in EVERYTHING the operator reviews (version, description, schema, runtime, code, meta, bundle) is reused, anything else is a new entry | `quarantined` |

### Tool quarantine — `core/forge_bundle/tool_quarantine.py`

`<tenant_home>/global/forge_bundle/quarantine/tools/<qid>/{meta.json, impl.py|impl.sh}`,
dir `0700`, files `0600`, written to a temp dir and renamed. `qid` is a random
32-hex token and the only handle accepted (`QID_RE`). `impl_filename` must be
the one its runtime implies, so a tampered `meta.json` cannot redirect a read.
Staging refuses: spec naming another tool, runtime other than `python`/`bash`,
an invalid Tool Forge name, non-UTF-8 code, a credential-shaped string, invalid
travelling meta (requirements must be plain package specifiers — no URLs,
paths, options or names ending in any archive extension pip reads as a local
file — `.whl .zip .tar .gz .tgz .bz2 .tbz .xz .txz .lz .tlz .lzma .zst …`;
secret refs through the vault validator; budget = known limits → finite
positive numbers ≤ 10⁹, compared before any float conversion). Claim/staging leftovers older than an hour are swept.

A decision first **claims** the entry: one atomic rename `<qid>` →
`.claimed-<qid>` (and the claim's mtime is reset, so the sweep ages the claim,
not the staging). Of two concurrent decisions exactly one proceeds; a decided
entry is never listed or decidable again even if deleting it fails.
`accept`: claim → hash + credential re-check → name still free → record
`quarantine_accepted` → `MultiRegistry.create(scope="user", meta={…travelled
meta, "origin": "forge_bundle", "bundle_id", "bundle_version",
"bundle_tool_version", "origin_verified": False})` → record `artifact_created`.
`Registry.create` writes the tool before its own audit record, so after any
create error `accept` looks at the registry: a tool carrying this
`quarantine_id` in its meta exists → it counts as created. Otherwise the entry
goes back and `artifact_failed` (`phase="accept"`) is recorded — if even that
record fails, the ORIGINAL error is reported; the licence gate's
`PermissionError` becomes `QuarantineForbidden` (403). If `artifact_created` cannot commit after the tool
was created, `OutcomeNotRecorded` says exactly that (503). `reject`: claim →
record `quarantine_rejected` → delete.

### Inventory — `core/forge_bundle/inventory.py`

`known()` reads skills (`skills_gen/*/skill.json` + the installed-skill registry),
tools (`MultiRegistry.list()`), layers (Layer Forge registry); any unreadable
store (including an unreadable forged `skill.json`) raises `InventoryUnavailable`. Tools carry no version, so an existing tool
satisfies a requirement on ANY version (`AnyVersion`). Installed plugins are not
enumerated: a bundle requiring a plugin outside itself is refused as stale.
`exportable(tenant_id, include_skills=)` feeds the export picker; skills only
for the install owner; a skill folder whose name differs from its `skill_id` is
not offered; no plugins (CLI only).

## Console routes (Phase 4) — `routes/forge_bundle_routes.py`

Every route belongs to the install's OWN tenant (router dependency: the session
tenant must equal the process tenant, else 403 "available to the install owner's
tenant only"). The audit chokepoint accepts records only for the process tenant
(ADR-0562 D2) and the skill and project-scope tool stores are host-wide, so
another tenant could neither complete an import nor be kept out of host-wide
data (inventory oracle via `/validate`, owner's tools via `/exportable`).

Paths are RELATIVE (`/forge-bundles/...`): the console router is mounted under
`/v1/console` by the gateway, so a router-level `/v1/console` prefix doubles it.
`tests/forge_bundle/test_console_routes_e2e.py` mounts the router exactly as the
gateway does and checks both the real and the doubled path.

| Route | Auth | Answers |
|---|---|---|
| `GET /forge-bundles/exportable` | session | `{skills, tools, layers, plugins: []}` · 503 a store unreadable |
| `POST /forge-bundles/export` | session + CSRF | ZIP · 400 plugin selection · 403 skill for a non-owner · 422 `ExportError` · 503 audit down |
| `POST /forge-bundles/validate` (multipart `file`) | session + CSRF | `{valid: true, …report}` / `{valid: false, stage, reason}` · 503 `{stage: "inventory", reason}` — writes nothing |
| `POST /forge-bundles/import` (multipart `file`) | session + CSRF | `ImportResult.to_dict()` · 413 > 50 MiB · 422 `{stage, reason}` · 503 `{message, …outcomes}` when stopped mid-way · 503 inventory/audit |
| `GET /forge-bundles/quarantine` | session | `{items, count}`; each item lists `requirements` and `secrets` it would get |
| `POST /forge-bundles/quarantine/{qid}/accept` | session + CSRF | 404 unknown/already decided · 409 name taken · 403 licence gate · 422 changed/unsafe · 503 audit (entry back in queue) / outcome unrecorded · 500 never echoes exception text |
| `POST /forge-bundles/quarantine/{qid}/reject` | session + CSRF | 404 · 503 audit ("the tool was not rejected and is still in the review queue") |

UI: `web-next/src/components/forge/ForgeBundlesPanel.tsx` (API in
`src/lib/api/forge-bundles.ts`) — export picker, import (validate → preview with
**Unverified origin** → import → per-artifact outcome, including what landed
when an import stopped), review queue with the packages and secrets each tool
would get. A late answer for a previously picked file is dropped (generation
counter); a failed check offers "Check again" and the same file can be picked
again; a running export locks the form; a refusal names its stage; a decision
refreshes both the queue and the export list and removes the decided row at
once with a confirmation; a layer's versions are alternatives (one per bundle);
an unreadable install inventory (503 stage `inventory`) is shown as the
install's problem, not the bundle's; uploads trigger the same stale-CSRF
recovery as `api()`.

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
| `container` | unreadable ZIP · > 50 MiB compressed / > 200 MiB uncompressed / > 2 000 entries · absolute, `..`, empty-segment, backslash, `:` or control-char names · symlink and encrypted entries · names equal up to case · compression ratio > 100:1 on files ≥ 1 MiB · any file outside `artifacts/` except the envelope · the same rules for every nested archive · a nested archive or gzip/bz2/xz stream expanding > 100:1 as a WHOLE (beyond 1 MiB) · an archive nested two levels deep (incl. inside a tar or a compressed stream) · all nested content together exceeding the 200 MiB budget |
| `envelope` | missing / > 1 MiB / non-UTF-8-JSON envelope (incl. nesting too deep to parse) · any schema defect above |
| `integrity` | a declared file missing, an undeclared file present, size or sha256 mismatch, CRC failure |
| `references` | an in-bundle requirement on the wrong version · any dependency cycle |
| `staleness` | (only with `known`) a requirement outside the bundle that the target does not have |
| `secrets` | a credential-shaped string (private key, AWS/GitHub/Slack/Google/`sk-` keys, JWT) anywhere: the envelope and every payload at every nesting level, inside every member of a gzip/bz2/xz stream, inside tar archives, and in the raw bytes around archive members |

A nested archive is recognised by its CONTENT (`zipfile.is_zipfile`), never by
its name — a wheel or a renamed `.bin` is opened like a `.zip`; its raw bytes
are scanned too (leading data, comment, stored entries). A tar archive (`ustar`)
is opened like a ZIP. Every member of a gzip/bz2/xz stream is decompressed
within the budget and the ratio rule and scanned. Each payload is scanned in
overlapping 1 MiB windows (64 KiB overlap — memory stays flat) through several
views: the bytes projected to printable ASCII (every other byte becomes a
space, so a length prefix or a high byte can never glue onto a key); when a
window is at least 2 % NUL bytes or carries a BOM — which every UTF-16/32 text
does, CJK without a BOM included, while random binary is ~0.4 % — its UTF-16
decodings (both byte orders, both alignments) and, from 40 % NULs, its UTF-32
decodings; and wherever a backslash occurs, the text with `\uXXXX`,
`\u{X…}`, `\UXXXXXXXX`, `\xXX`, octal `\NNN` and `\N{NAME}` escapes resolved
(JSON, JSON Lines, YAML, Python, JS). Deliberate obfuscation (base64, string
splitting, ROT13) is out of scope for any pattern scanner. The regex
credential detectors run through `core.pii.sensitive.detect_named_types`
(fail-closed); JWT shapes are found by a LINEAR check (`_has_jwt`), because the
shared JWT regex backtracks quadratically on input like `eyJ-eyJ-…` (a 999-byte
upload cost 43 s) — the linear check is slightly broader, so it can only reject
more. Measured: ≈ 9 s per 40 MiB of random binary, ≈ 4 s per 40 MiB of text,
≈ 14 s per 40 MiB of NUL-heavy text; the ratio rule caps what a small upload
can expand to (100×). A rejection names the detector, never the matched value.

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
