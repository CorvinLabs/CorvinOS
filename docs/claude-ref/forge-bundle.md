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
1659428f9, eb31b1772; 36 + 31 + 19 findings). Proven findings
have regression tests in `tests/forge_bundle/` and the console's
`tests/unit/forge-bundles-panel.test.tsx`; most test names carry the finding id
(C…, A…, S… round 1; R2A/R2B round 2; R3A/R3B round 3). The round-3 fixes were
verified by those tests, not by a fourth review round.

**2026-10-08 — Playwright suite + review of all four forges** (see
[E2E suite](#e2e-and-adversarial-suite-2026-10-08)): four weaknesses found
and fixed in the primitives (ADV-01 case-variant tool names racing through
accept, ADV-03 import flood starving sync routes, ADV-09 a decided plugin
package re-proposed on re-import, ADV-10 non-pytest test consoles projecting
into the real KB), plus the quadratic JWT/e-mail regexes found while fixing.
Each has a regression test that fails without the fix. A refutation round against
those fixes (five separate reviewers, executed repros required) found 20 more
(1 high, 8 medium, 11 low); the ones that held up are fixed — see
[Refutation round 4](#refutation-round-4-2026-10-08). That round's own fixes are
verified by tests only, not by a further reviewer.

**2026-10-13 — Per-Item Export (Phase 4b): Claude-Web Skill ZIP Format**

Individual artifacts (skills, tools, os-skills, layers, plugins) are now downloadable as ZIPs
from the Forge Console (`/app/forge?tab=...`). Skills are exported in **Claude Web format**
to be directly importable into [claude.ai](https://claude.ai) custom skills (see
[Claude Web Skill ZIP Format](#claude-web-skill-zip-format)).

### Per-Item Export (Phase 4b) — Single artifacts as ZIPs

`GET /v1/console/forge-bundles/export/{type}/{id}?version=…`

Exports a single artifact (skill, tool, layer, plugin, os_skill) as a ZIP. Skills use Claude-Web-compatible format.

| Artifact Type | Format | Structure |
|---|---|---|
| **skill** | Claude-Web (ADR-2229 Phase 4b) | `<skill-name>/SKILL.md` (frontmatter: name, description) + `.corvin-skill.json` (Corvin metadata) + optional scripts/, references/, assets/ |
| **os_skill** | Claude-Web (read-only) | `<skill-id>/SKILL.md` (frontmatter, marked read-only) + `.corvin-skill.json` (includes `"read_only": true`) |
| **tool** | Native format | `<tool-name>/spec.json` (name, description, input_schema, runtime, version, impl_filename, meta) + `<impl_filename>` (implementation) |
| **layer** | Native format | `<layer-id>/manifest.json` (layer definition, no registry state) |
| **plugin** | Pre-built | Pre-built plugin ZIP (already compressed, passed through as-is) |

**Claude-Web Skill ZIP Format (Skills + OS-Skills):**

```
my-skill/
  ├── SKILL.md                    (YAML frontmatter: name, description; body is markdown)
  ├── .corvin-skill.json          (Corvin metadata: corvin_skill_id, corvin_version, exported_at, original_manifest [skills only])
  ├── scripts/                    (optional)
  │   ├── helper.py
  │   └── …
  ├── references/                 (optional)
  │   └── …
  └── assets/                     (optional)
      └── …
```

**Frontmatter fields (Claude-Web compatible):**
- `name` — Sanitized skill name (lowercase, alphanumerics + hyphens; max 64 chars; no `anthropic`/`claude` prefix)
- `description` — Brief description of the skill

**Corvin Metadata (.corvin-skill.json, not visible to Claude-Web):**
- `corvin_skill_id` — Original skill ID in CorvinOS format
- `corvin_version` — Skill version (semver)
- `exported_at` — ISO 8601 timestamp
- `skill_type` — "skill" or "os_skill"
- `read_only` — Boolean; true for OS-Skills
- `original_manifest` — Full skill.json manifest (skills only, for lossless re-import)

**Name Sanitization Rules:**
- Replace `_` and `.` with `-`
- Remove non-alphanumeric characters (except `-`)
- Lowercase
- Strip leading/trailing `-`
- If starts with `anthropic` or `claude`, prepend `user-`
- If >64 chars, truncate to 52 chars + `--` + 8-char SHA256 hash suffix

**Single-Item Import Detection**

`POST /v1/console/forge-bundles/import` auto-detects single-item ZIPs and imports them directly:

1. **Bundle ZIP** — Has `forge-bundle.json` at root → import_bundle() (existing logic)
2. **Single Skill** — Single top-level folder with `SKILL.md` + `.corvin-skill.json` → _import_single_skill()
3. **Single Tool** — Single top-level folder with `spec.json` + impl file → _import_single_tool()
4. **Single Layer** — Single top-level folder with `manifest.json` → _import_single_layer()
5. **Single Plugin** — Pre-built plugin ZIP → _import_single_plugin()

Each type enters through its own forge's intake, passes the same gates as a locally forged artifact.

### Audit Events (Per-Item Export/Import)

Registered in `corvin_operator/forge/forge/security_events.py` (EVENT_SEVERITY + _EVENT_ALLOWLIST):

| Event | Severity | Fields | Notes |
|---|---|---|---|
| `forge_bundle.item_exported` | INFO | artifact_type, artifact_id, artifact_version, size_bytes, sha256, tenant_id | Emitted after successful export |
| `forge_bundle.artifact_intake_started` | INFO | bundle_id, artifact_kind, artifact_id, artifact_version, status, actor, tenant_id | Emitted at import start (before validation) |
| `forge_bundle.artifact_staged` | INFO | bundle_id, artifact_kind, artifact_id, artifact_version, status, actor, tenant_id | Emitted after successful intake (skill installed, tool quarantined, layer forged, plugin staged) |
| `forge_bundle.artifact_failed` | WARNING | bundle_id, artifact_kind, artifact_id, artifact_version, phase, error_class, actor, tenant_id | Emitted on import failure (phase: import, quarantine, forge, staging) |

All events include `tenant_id` (fail-closed: null → denied). Metadata only; no manifest bodies, file contents, or exception messages. Hash-chained via `forge.security_events.audit_write_or_die()` using `core.paths.tenant_audit_chain(tenant_id)`.

**Audit-First Semantics:**
- Export: record after successful ZIP build
- Import: record intake_started BEFORE validation, staged/failed AFTER result

**Size and Count Limits:**
- Max upload: 50 MiB
- Max file count per ZIP: checked per forge (layer quarantine: max 16 gates per bundle, etc.)

**Security:**
- ZIP extraction uses `zipfile.ZipFile` with path validation (no symlinks, no hard links, no `..` escapes)
- Tenant isolation: all reads/writes filtered by `tenant_id` from SessionRecord
- Host-wide resources (skills, layers): restricted to install owner's tenant (owner/admin tier)

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
| skill | `SkillInstaller(<corvin_home>/skills_installed)`, checksum = the envelope's sha256. That store is HOST-WIDE, so only the install owner (owner/admin session of the process tenant) may import a skill — same as the manual skill upload. After the install the package's `SKILL.md` is handed to SkillForge — see [Installed skills become prompt skills](#installed-skills-become-prompt-skills-g9) | `installed`; the detail says whether the skill is also available as a prompt skill, or why it is stored but not active |
| layer | `LayerForgeOrchestrator(actor="bundle_import").create_layer_definition` — every gate, enforcement rule and the review run on THIS install. Before that: travelled state dropped, ≤ 16 gates, gate/rule/target/dependency ids short identifiers (they reach Layer Forge's audit records), each gate names ONE test file under `tests/` (no `::node`) | `forged`; detail notes a FLAGGED review on this install, or that Layer Forge wrote the layer but could not record its last step (only when the stored definition equals the bundle's, so a concurrent writer's layer is never reported as ours) |
| plugin | `StagingManager.validate_zip_file` + `store_staged_upload` as `<id>-<version>.zip`; an identical package already pending is reported, not re-staged; one **already decided** (approved or rejected — `StagingManager.get_decision`, remembered under `plugin_staging/decisions/<upload_id>.json`, keyed on the package's content hash; see [Plugin decision memory](#plugin-decision-memory)) is refused: "already been decided (approved\|rejected)". The memory is a record, not a lock: the owner's manual `/plugin-uploads` re-upload stays possible, and rejection writes the record BEFORE deleting (a failed write leaves the upload pending) | `pending_approval` — approve at `/plugin-uploads/{id}/approve` |
| tool | `ToolQuarantine.stage` — a name that already exists here (case-insensitively) is refused (`failed`, "already exists"); an entry identical in EVERYTHING the operator reviews (version, description, schema, runtime, code, meta, bundle) is reused, anything else is a new entry | `quarantined` |

The installer (`core/skills/skill_installer.py`) records `verified` plus a `verification`
map (`checksum: caller_supplied`, `manifest`, `signature`) for every install. The ZIP checksum
comes from the same caller as the ZIP, so it proves nothing about who built the package.
`verified: true` means exactly: the package carries `skill.sig`, a trusted operator key of THIS
install verifies it over the manifest plus the package's checksum list, every file in the ZIP
matches that list, and the manifest names the skill it is installed as (ADR-2252,
`core/skills/package_signing.py`). `signature` is `absent` (no `skill.sig`), `unverifiable`
(signed, but this install has no operator key; installs, flagged), `valid`, or the install is
refused (signature does not verify, a listed file is missing or changed, or a file exists that
the signature does not cover). A manifest that names a different `skill_id`/`version` than the
install is refused; one that names nothing is recorded `incomplete`, a package without one
`absent`. The packager signs only when the operator has generated a key
(`OperatorKeyManager(<corvin_home>).generate_keypair()`; never done implicitly), so Forge
Bundle imports from other instances stay unverified.

Hardened after the 2026-10-12 adversarial review: a key store that EXISTS but cannot be read refuses
a signed package (`refused:operator key store exists but is unreadable`) instead of installing it as
`unverifiable`, and one malformed rotated key no longer discards the current key; every ZIP entry must
lie inside the signed folder, carry no backslash, no empty/`.`/`..` segment and no case-folded twin
(each could be extracted next to or over a verified file on Windows/macOS); the packager builds the
ZIP from exactly the hashed set (symlinks are never followed or shipped), hashes POSIX paths, and
covers `.forge/generation_context.json` and `.forge/audit_trail.jsonl` with the signature - only the
checksum list itself and `skill.sig` sit outside it. `SkillPackager.verify_package` (no caller, and it
returned True for a tampered package) was removed.

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
`PermissionError` becomes `QuarantineForbidden` (403), and `Registry.create`'s
`FileExistsError` — it now refuses a name that differs from an existing one only
in case, checked under its own lock — becomes `QuarantineConflict` (409): the
caller-side `_name_taken` check runs outside that lock, so two concurrent
accepts of `geo.fence` / `Geo.Fence` both passed it (ADV-01). If `artifact_created` cannot commit after the tool
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

### Plugin decision memory

`StagingManager.record_decision` / `get_decision` / `withdraw_rejection` /
`clear_decision`. It exists because approve (`move_to_installed`) and reject
(`delete_staged_upload`) both delete the staging `.meta`, so "was this exact
package decided before?" had no answer. Rules (each one a finding of the
2026-10-08 refutation round):

- **Memory, not a lock.** It only stops a *bundle import* from re-proposing a
  package; a re-proposal would wait for the operator's approval anyway.
- **An unreadable or invalid record counts as "no decision"** (logged). Failing
  closed blocked a legitimate plugin for good after a truncated write, with no way out.
- **The owner's manual upload (`POST /plugin-uploads`) withdraws a *rejection*.**
  That is the only way to clear one. An *approval* is kept: the package is installed.
- **An approval is sticky:** rejecting a re-upload of an installed package does not
  rewrite the memory to "rejected".
- **Content-keyed, so byte-sensitive (known limit, not fixed):** a package rebuilt
  with one extra byte has a new id and is proposed again. Keying on name@version
  would instead block every legitimate new build of the same plugin.
- Reject writes the record BEFORE deleting; a failed write leaves the upload pending.

## Console routes (Phase 4) — `routes/forge_bundle_routes.py`

Every route belongs to the install's OWN tenant (router dependency: the session
tenant must equal the process tenant, else 403 "available to the install owner's
tenant only"). The audit chokepoint accepts records only for the process tenant
(ADR-0562 D2) and the skill and project-scope tool stores are host-wide, so
another tenant could neither complete an import nor be kept out of host-wide
data (inventory oracle via `/validate`, owner's tools via `/exportable`).

**Heavy-slot limit (ADV-03).** `validate` and `import` are sync handlers and a
layer import runs pytest gates plus the LLM review inside the request, so each
holds a thread-pool worker for its whole duration; 48 concurrent imports stalled
every other sync console route for the review's length (5.5 s measured; async
`/healthz` stayed at 5 ms, so a liveness probe never noticed). At most
`_HEAVY_SLOTS = 2` of EACH kind run at once — validate and import have separate
pools, because validate is cheap and the UI calls it on every file pick (with one
shared pool a validate flood made the operator's own import answer 429 in 6 of 8
attempts) — process-wide (Forge Bundles serve the install owner's tenant only); a request that finds no free slot is answered **429
immediately** and never waits — a waiting handler would hold the very worker the
guard keeps free. The slot is released in `finally`; the UI shows the 429 as
"Import refused: …" and offers re-picking.

Paths are RELATIVE (`/forge-bundles/...`): the console router is mounted under
`/v1/console` by the gateway, so a router-level `/v1/console` prefix doubles it.
`tests/forge_bundle/test_console_routes_e2e.py` mounts the router exactly as the
gateway does and checks both the real and the doubled path.

| Route | Auth | Answers |
|---|---|---|
| `GET /forge-bundles/exportable` | session | `{skills, tools, layers, plugins: []}` · 503 a store unreadable |
| `POST /forge-bundles/export` | session + CSRF | ZIP · 400 plugin selection · 403 skill for a non-owner · 422 `ExportError` · 503 audit down |
| `POST /forge-bundles/validate` (multipart `file`) | session + CSRF | `{valid: true, …report}` / `{valid: false, stage, reason}` · 503 `{stage: "inventory", reason}` · **429** + `Retry-After: 2` when both validate slots are busy — writes nothing |
| `POST /forge-bundles/import` (multipart `file`) | session + CSRF | `ImportResult.to_dict()` · 413 > 50 MiB · 422 `{stage, reason}` · 503 `{message, …outcomes}` when stopped mid-way · 503 inventory/audit · **429** + `Retry-After: 15` when both import slots are busy |
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

## Installed skills become prompt skills (G9)

An installed package runs nothing by itself, and `boot_skills` never read `skills_installed/`: until 2026-10-21 a
skill the console reported as "installed" could not take part in a turn. `core/skills/installed_prompt_skills.py`
is the one bridge, and it adds no injection path of its own:

* **Where it runs:** after the install in both import paths (`item_import._import_single_skill` — the SKILL.md is read
  from the uploaded ZIP because the packager layout drops it; `import_module._intake_skill` — read from the installed
  package) and as a bounded catch-up in `skills.boot.boot_skills` (≤ 3 verified, not-yet-present skills per boot; a
  quota refusal is retried at the next start). A skill already present at the installed version is `unchanged`;
  one present at an older version is re-synced **without** spending an authoring credit, and the synced version is
  recorded in `meta.source_version` so the next boot reads `unchanged` (until 2026-10-21 the old version stayed in
  the meta and every boot re-wrote the body and spent a credit).
* **What it creates:** a SkillForge skill `installed.<id>` (characters SkillForge rejects become `_`) in scope `user`,
  type `domain`, `meta.origin = forge-bundle-import`. Because it goes through `SkillRegistry.create`, the **linter runs
  fail-closed** (prompt-injection patterns), the namespace gate applies and the free-tier authoring credit is spent
  like any authoring.
* **What it never does:** overwrite a skill of another origin (reported as "already exists"), shadow a curated skill
  (the `installed.` prefix), read a SKILL.md through a symlink or beyond 256 KiB, or grade the skill.
* **Why it is safe to enable:** the skill starts **ungraded**, so the injector's grade gate keeps it out of automatic
  injection. It runs when the operator asks for it by name (`_honor_explicit_requests`) or after real grades.
  `prompt_skill_adapter` then registers it as `prompt.installed.<id>` (audited, `skill.migrated`).
* **Outcome is always reported:** the import detail is `available as prompt skill '…'`, `already available…`, or
  `stored but not active as a prompt skill: <reason>` (linter, quota, name taken, no SKILL.md, SkillForge unavailable).
  The reason names the matched pattern, never the body.

Proof: `tests/forge_bundle/test_installed_prompt_skills_e2e.py` — real console route (session + CSRF) → production
readers `collect_prompt_sources` and `boot_skills` → `prompt.installed.<id>` in the audited registry; linter refusal,
no-overwrite, boot catch-up and the symlink refusal each have a test. Mutation controls: removing the import hook fails
3 tests, removing the boot catch-up fails 1.

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
(fail-closed). JWT shapes are matched by `core.pii.jwt_scan.LinearJwtPattern`
(this stage drops the trailing word boundary, so it can only reject more): the
regex it replaces is QUADRATIC on input like `eyJ-eyJ-…` — measured 2026-10-08,
80 KB 0.73 s, 4× the input = 16× the time. (An earlier note here said a
999-byte upload cost 43 s; that did not reproduce, the growth law did.)
`core.pii.email_scan` does the same for the structured e-mail detector (64 KB
1.9 s). Both matchers are proven span-identical to the original regexes by
differential tests (`tests/pii/`). **This stage matches without a leading word
boundary** (`start_boundary=False`): the former check treated any non-ASCII
character as a boundary while `\b` is Unicode, so a JWT glued to `é` or `²`
passed the first linear port (511 of 1 M fuzz cases). It can only reject more. Measured: ≈ 9 s per 40 MiB of random binary, ≈ 4 s per 40 MiB of text,
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

## E2E and adversarial suite (2026-10-08)

`web-next/playwright.forge-system.config.ts` · specs in
`web-next/tests/e2e/forge-system/` (88 tests) · run
`npx playwright test -c playwright.forge-system.config.ts` from `web-next/`.

Two isolated installs of the real `corvin_gateway.app` on throwaway
`CORVIN_HOME`s (harness `harness/isolated_forge_backend.py`): **A** "Nordwind
HQ" (`:8799`, seeded with skills, tools, layers and plugin packages) and **B**
"Depot Hamburg" (`localhost:8798`, empty). Different HOSTS on purpose — cookies
are per host, not per port. Exports go to `~/Downloads/corvin-forge-e2e/<run>/`
(`FORGE_E2E_DOWNLOADS` overrides). The only replaced boundaries are the licence
tier and Layer Forge's LLM review (a stub honouring the real contract: it never
raises, it returns `ReviewVerdict("ERROR")`); quality gates run real pytest.

| Spec | Covers |
|---|---|
| 01 | UI export on A → `~/Downloads` → UI import on B → review queue → re-export (lossless) |
| 02 | Layer Forge: real gates, reviewer down / flagged, ghost gate, gate caps, smuggled state |
| 03 | Plugin Forge: console refuses plugin export, CLI export, approval queue, decided-package memory |
| 04 | Skill and Tool Forge specifics, `requires` (references / staleness), queue contract |
| 05 | 27 hostile bundles, one stage each (container / envelope / integrity / secrets) |
| 06 | races (concurrent accepts, case variants, duplicate imports), timeouts, 429 shedding, UI latency |
| 07 | XSS, header injection, auth/CSRF matrix, skill identity, scanner limits |
| 98 / 99 | the isolation proof's detector is itself tested (98); 99 runs last and compares the live audit chain against a baseline pinned in `global-setup.ts` before spec 01 |

Hostile bundles are forged by `harness/bundle_lab.py` (stdlib only, never the
code under test) and the audit chain is verified by the production
`verify_chain` (`harness/chain_check.py`). **Isolation:** the harness points
`CORVIN_KB_REPO` at nothing and `chdir`s into the throwaway home — Tool Forge's
project scope resolves from the cwd via git, so `CORVIN_HOME` alone does not
isolate it (ADV-10).

Not defects, measured: obfuscated credentials (concatenated / base64 / reversed)
pass the pattern scanner (ADV-07) — the review queue is the control.

## Refutation round 4 (2026-10-08)

Five separate reviewers (one per surface, isolated worktrees, an executed repro
required for every finding) attacked the fixes of the Playwright review: 20
findings. **Fixed:** validate/import share no slots (A3-1) · unreadable decision
records no longer lock a plugin out, a manual upload withdraws a rejection, an
approval is sticky (A9-2/3) · `validate` lost its Unicode leading boundary (R-1) ·
negative `pos`/`endpos` clamp like `re` (R-3) · the remaining quadratic JWT copies
(`outcome_feedback`, data_hub `scanner`, `htrace`, `telemetry`) use the linear
matchers (R-2) · `kb_projection` decides "canonical home" with the account's passwd
home, not `$HOME` (I-1) · the KB plugin's `/sync` route refuses the operator's
checkout on a sandboxed home (I-2) · the Playwright isolation proof is baselined
BEFORE the first spec, finds the live chain through the main checkout, fails on a
rotated/truncated/rewritten chain, and has its own detector spec (I-3) · ADV-09 is
self-contained (I-4) · ports and homes are configurable (I-5) · the ReDoS probe has
real margin (I-6) · no marketplace network sync and old Downloads folders are pruned (I-7) ·
the sys.path guard imports both hosts and compares real paths (I-8) · a call-site
test per migrated module (I-9).

**Not fixed, on purpose:** content-keyed decisions (A9-1, above) · `Retry-After`
is a constant while an import has no deadline (A3-2: `review.py` calls Anthropic
without an explicit timeout — a separate change to Layer Forge) · the JavaScript
copy in `bridges/shared/js/logger.js` is still quadratic (needs JS tests via
`run-all-tests.sh`) · tool ids that already collide by case before the fix can no
longer be overwritten under either name — delete one (A1-1) · promote of `Foo.Bar`
into a scope holding `foo.bar` is refused, uniqueness is per scope (A1-2/3).

Run settings: `FORGE_E2E_PORT_A/B`, `FORGE_E2E_HOME_A/B`, `FORGE_E2E_KEEP`
(Downloads run folders kept, default 10), `FORGE_E2E_LIVE_CHAIN`.

## Claude Web Skill ZIP Format (Phase 4b)

CorvinOS skills can be exported as ZIPs compatible with [Claude Web](https://claude.ai)
custom skills upload (verified against
[Anthropic Support](https://support.claude.com/en/articles/12512198-how-to-create-custom-skills),
[Platform Docs](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/overview),
and [Claude Code Docs](https://code.claude.com/docs/en/skills)).

### ZIP Structure

```
skill-name.zip
└── skill-name/
    ├── SKILL.md (required: YAML frontmatter + Markdown)
    ├── .corvin-skill.json (Corvin metadata, optional for Claude Web)
    ├── scripts/ (optional: Python/JS deterministic processing)
    ├── references/ (optional: static files loaded on demand)
    └── assets/ (optional: templates, fonts, icons)
```

### SKILL.md Frontmatter (Claude Web Contract)

```yaml
---
name: skill-name-in-kebab-case
description: "Brief description (1-1024 characters)"
---

# Heading

Markdown content with instructions for Claude...
```

**Constraints (from official Anthropic documentation):**
- `name`: 1-64 characters, kebab-case (lowercase alphanumerics and hyphens)
- `description`: 1-1024 characters
- Folder name MUST match the `name` field
- ZIP MUST contain the folder at its root (not nested)

### CorvinOS Name Sanitization

Corvin skill names (e.g., `os.delegation_router`, `assistant.foo_bar`) are converted to Claude Web format:

- Dots (`.`) and underscores (`_`) → hyphens (`-`)
- Uppercase → lowercase
- Non-alphanumeric characters (except hyphens) → stripped
- Length > 64 chars → truncated with SHA256 hash suffix (deterministic)
- Prefix `anthropic` or `claude` → prepended with `user-`

**Examples:**
- `os.delegation_router` → `os-delegation-router`
- `assistant.foo_bar_baz` → `assistant-foo-bar-baz`
- `my_very_long_skill_name_that_exceeds_the_limit` → `my-very-long-skill-name-tha--<8-char-hash>`

### Lossless Re-Import via `.corvin-skill.json`

The exported ZIP includes a `.corvin-skill.json` file with Corvin-specific metadata:

```json
{
  "corvin_skill_id": "assistant.original_name",
  "corvin_version": "1.0.0",
  "exported_at": "2026-10-13T12:34:56Z",
  "original_manifest": { "name": "...", "description": "...", ... }
}
```

When re-importing into a CorvinOS install, this metadata is restored, allowing round-trip
export → (use in Claude Web) → import back with original identity and version.

**Note:** Claude Web ignores `.corvin-skill.json`; it is only for CorvinOS imports.

### Per-Artifact Types

| Type | Export Format | Claude Web Compatible | Notes |
|------|---|---|---|
| **Skill** | SKILL.md + .corvin-skill.json + scripts/, references/, assets/ | ✅ Yes | Ready for claude.ai custom skills |
| **OS-Skill** | SKILL.md (read-only description) + .corvin-skill.json | ⚠️ Read-only | System skills exported as documentation only |
| **Tool** | spec.json + impl file (Python/Bash) | ❌ No | Tools are Forge-specific; import to quarantine |
| **Layer** | manifest.json | ❌ No | Layers are CorvinOS-specific; import via Layer Forge |
| **Plugin** | Pre-built ADR-0511 ZIP package | ❌ No | Plugins are staged for operator review |

### Export Routes

- **Console:** `GET /v1/console/forge-bundles/export/{artifact_type}/{artifact_id}?version=X`
  - `artifact_type` accepts singular (`tool`, `skill`, `os_skill`, `layer`, `plugin`) and plural (`tools`, …) spellings
  - `version` is optional: skills/layers use the newest inventory version, tools/OS-Skills have none of their own and get `1.0.0`
  - Plugins answer `400` (a built package on disk is needed; CLI only)
  - Returns ZIP file with `Content-Disposition: attachment`
  - The forge tabs' "Download selected" fetches one ZIP per selected item (there is no multi-item single-ZIP route besides the bundle `POST /export`)
  - Import (`POST /forge-bundles/import`, multipart field `file`, CSRF) auto-detects a single-item ZIP. Tools go to quarantine, layers pass the same `_check_imported_layer` checks as a bundle, skills are re-packed with `SkillPackager` and installed by `SkillInstaller` (owner/admin only), then offered to SkillForge as a prompt skill (see below)
  - Audit event: `forge_bundle.item_exported` (tenant-scoped, hash-chained)

- **CLI:** `scripts/forge_bundle_cli.py export --skill ID@VER` (not yet implemented for per-item; use bundle export for now)

### Security Properties

- **ZIP Slip Protection:** All path components validated; no `..`, absolute paths, or symlinks
- **Symlink Rejection:** Skills containing symlinks are refused at export time
- **Hard Link Rejection:** Hard links are detected and refused
- **Audit-First:** Export events logged before response sent
- **Tenant Isolation:** All events carry `tenant_id`; fail-closed if missing
- **Rate Limiting:** Max 2 concurrent exports per type (via `_HEAVY_SLOTS` semaphore)

### Import Detection

The import handler (`item_import.py::detect_item_type_from_zip`) automatically detects:
1. Bundle format (has `forge-bundle.json` → use bundle import)
2. Single-item format (single top-level folder)
   - **Skill:** `folder/SKILL.md` + `.corvin-skill.json` → read `corvin_skill_id` + `corvin_version`
   - **Tool:** `folder/spec.json` + impl file → read `name` + `version`
   - **Layer:** `folder/manifest.json` → read `id` + `version`
   - **Plugin:** Pre-validated ADR-0511 ZIP → read manifest

### Test Coverage

- `tests/forge_bundle/test_item_export.py` — export tests for all artifact types
- `tests/forge_bundle/test_item_import.py` — format detection and validation
- `tests/forge_bundle/test_item_export_import_e2e.py` — round-trip export → import → verify

Run with sandbox environment:
```bash
export CORVIN_HOME=$(mktemp -d)
export XDG_CONFIG_HOME=$(mktemp -d)
export CORVIN_AUDIT_ANCHOR_KEY=$(mktemp)
pytest tests/forge_bundle/ -v
```

**Do NOT set `FORGE_ROOT`** for tests (breaks isolation).
