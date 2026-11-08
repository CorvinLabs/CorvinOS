# Projects (ADR-2315, CONCEPT-0126)

A **project** is a directory the user owns, with a `project.yaml`. CorvinOS keeps only an index of it.
Losing the index loses no project. Design: `Corvin-Knowledge` CONCEPT-0126; decisions ADR-2315
(project object), ADR-2316 (several KBs, scope), ADR-2317 (chat context); plan PLAN-0970.
Status 2026-11-08: registry, manifest, fragment engine and the API exist (T-0351, T-0352, T-0355); the
template catalogue is the first-party plugin `project_templates` (T-0354, T-0356, in Corvin-Marketplace, published); the console's Projects panel exists as a list (T-0357); wizard, inspector, scope selector, videos action and doctor (T-0358 to T-0362) are next.

## Where things live

| Piece | Path |
|---|---|
| `project.yaml` schema 1, validation, atomic write | `core/projects/manifest.py` |
| Registry (index) + path rules for client-supplied locations | `core/projects/registry.py` |
| Fragment/template engine: plan, apply, repair, closure checks | `core/projects/fragments.py` |
| Atomic writes that never follow a symlink, cross-platform lock | `core/projects/fsutil.py` |
| Service layer (audit, plugin state, bound engine, plans, create/adopt/toggle/repair/open/health) | `core/console/corvin_console/projects_service.py` |
| HTTP routes `/v1/console/projects` | `core/console/corvin_console/routes/projects.py` (mounted in `app.py`) |
| The catalogue (fragments, templates) | `Corvin-Marketplace/plugins/buildin/knowledge_management/project_templates/` (docs in the plugin: `docs/CATALOGUE.md`) |
| Registry file | `<tenant_home>/projects/registry.json` (0o600, dir 0o700) |
| Operator's projects root | `<tenant_home>/projects/settings.json` (`projects_root`, default `~/projects`) |
| Proof | `tests/e2e/test_projects_registry_e2e.py`, `test_projects_fragments_e2e.py`, `test_projects_api_e2e.py`, `test_project_templates_catalogue_e2e.py` (the REAL catalogue) |

## The directory is the truth

`project.yaml` (schema 1): `id` (kebab-case, 1-28 chars: the projector's scope label `prj-<id>` must
pass its 32-char rule), `title`, `tenant`, `template` (`<id>@<n>`), `docs_mode` (`nested`|`plain`,
default `nested`, see ADR-2315 amendment), `features` (id -> bool), optional `videos`, `directory.peers`.
No secret and no machine-local path. Unknown top-level keys are kept on rewrite.

The registry holds `{"schema": 1, "projects": [{id, path, template, added_at}]}` and is rebuilt from
the directories by `registry.rebuild(tenant)` (adds, never removes, never touches a directory). The
operator's `projects_root` lives in a SEPARATE file so that deleting the index never loses the place
`rebuild` has to look in. `corvin_console.kb_projection.project_kbs` reads this same shape.

## Locations a client may name (`registry.validate_location`)

Only `plan`/create and `adopt` take a path from a client. Refused (`ProjectPathError`, 422, the message
names the rule and never a path): not absolute, `..`, outside the projects root or the root itself,
inside `CORVIN_HOME` (or containing it), a symlink anywhere between the root and the target (judged on
the path as given), a protected component (`.git`, `.corvin`, `session_ledger`, `.corvin-ledger`,
`cel_anchors`, `pending_notifications`, `tenants`), a hidden top-level directory, a directory another
tenant already registered, a non-empty directory (create), a missing or invalid `project.yaml` or one
that names another tenant (adopt). One directory belongs to exactly one tenant.

## The fragment engine (`core/projects/fragments.py`)

A catalogue is `<src>/fragments/<id>/fragment.yaml` (+ template files) and `<src>/templates/<id>.yaml`.
Fragments are DATA: no template code runs. The only active steps are the closed action set
`git_init`, `git_commit`, `kb_scaffold`, `kb_seed`, which the host implements.

* `requires` is hard, transitive and acyclic; `suggests` is a hint; `validate_catalogue` reports
  unknown ids, cycles and templates (default AND offered features) that are not requires-closed.
  `closure` orders dependencies first; `dependants` is the cascade a switch-off needs.
* **plan** (`build_plan`) writes nothing and returns the ops with a status (`new`, `changed`,
  `unchanged`, `skip`), diffs of changed generated files, conflicts (non-empty target on create),
  warnings and the tenant plugins the active features need. **apply** (`apply_plan`) is idempotent:
  `seed` files are written once and never overwritten, `generated` files are rewritten when they differ,
  `local` files (`.corvin/local/**`, `.mcp.json`) are mode 0o600 and git-ignored.
* `CLAUDE.md` is composed from the active features' `claude` sections; the text between
  `<!-- user:begin -->` and `<!-- user:end -->` is kept byte-identical on repair; an existing
  `CLAUDE.md` that carries no generated marker is never overwritten (a warning instead).
  `.gitignore` is managed between `# corvin:begin`/`# corvin:end`; lines outside are the user's.
  In `docs_mode: nested` the project's `.gitignore` lists `docs/` (docs is its own repository; a gitlink
  would record no content - the trap the reference project hit).
* **Order of a create plan:** `git_init`, then `kb_scaffold` BEFORE any file (the scaffold step refuses a
  `docs/` that already holds something it did not create), then every fragment's files, `kb_seed`
  (initiative, epic `E-001`, the fragments' `seed_tasks` through the BOUND KB's engine), then the nested
  `docs/` repository's own commit and finally the project's commit. `CLAUDE.md` is written only while the
  `claude` feature is on (any active fragment may contribute a section); `.mcp.json` whenever a fragment
  declares MCP servers (`acs`). A template's `videos:` block and `directory.peers: []` seed the manifest.
* Machine-local files (`.mcp.json`, `.corvin/local/kb-commands.md`) carry the absolute interpreter and
  engine paths and are regenerated per machine; committed files carry none (a test greps for them).
* Placeholders are a closed set (`{{project.id}}`, `{{paths.python}}` ...); an unknown or unvalued one
  is an error, never an empty string. Writes go through `fsutil.atomic_write` (fresh temp file +
  `os.replace`) and refuse any symlinked component.

**Must NOT do:** execute code from a template · overwrite a seed or a non-generated `CLAUDE.md` ·
write an absolute path into a committed file · follow a symlink · take a path from a request outside
`plan`/`adopt` · store `projects_root` in the index.

## API (`/v1/console/projects`, tenant = session)

Session for every call, CSRF for every mutation. The tenant is ALWAYS `rec.tenant_id`; a project of
another tenant is 404 on every route (never a hint). Paths from a client exist only in `plan` and
`adopt`; everything else carries a project id and resolves the path from the registry.

| Method, path | Purpose |
|---|---|
| `GET /` | the tenant's projects (state `ok`/`missing`/`invalid`, active features) + `projects_root` |
| `GET /templates` | templates, features (requires/suggests), the tenant plugins each needs and their state; `available:false` + reason when the template plugin is not enabled for the tenant |
| `POST /plan` | `{template, title, [project_id, location, features, docs_mode]}`: NO write; returns the file list with owners, `pulled` dependencies, warnings, conflicts, plugin states, `plan_id` (15 min, in memory); audit `project.plan_created` |
| `POST /` | `{plan_id}`: re-validates location, catalogue version and plugins, then writes `project.yaml`, registers, applies, seeds the KB; 201 |
| `POST /adopt` | `{location}`: registers an existing directory whose `project.yaml` names this tenant; writes nothing in it |
| `GET /{id}`, `GET /{id}/health` | detail with the feature catalogue (dependants, plugin states, suggestions); a checklist of real checks (manifest, features closed, generated files in sync, tenant plugins, `kb check`) - never a score |
| `PUT /{id}/features` | `{set: {feature: bool}, accept_dependencies, cascade}`: 409 `has_dependants` / `needs_dependencies` / `plugin_missing` carry the lists the UI shows; switching off never deletes a file |
| `POST /{id}/repair` | `{dry_run}`: pending changes with diffs; repair rewrites generated and machine-local files only |
| `POST /{id}/open` | audit `project.opened`; returns the context and a terminal command (`chat_attachment: false` until ADR-2317's spike) |
| `DELETE /{id}` | removes the registry entry ONLY; the directory stays |

**External prerequisites are warnings, never a refusal** (`external_warnings`, plan response `warnings`):
with `acs` the tenant's compute worker is probed read-only on its socket (`corvin_compute.endpoint.connect`,
1 s, nothing sent; `health` carries a `compute_worker` check) and a licence note says the engine cannot verify the
tier. A missing tenant PLUGIN, by contrast, is a refusal (409 `plugin_missing`).

**Create flow.** plan -> create: `project.yaml` is written first (the truth), then registered (the
index), then the fragments are applied; the KB scaffold is the Knowledge Graph plugin's real
`kb_init.py --no-engine` (`--no-git` in `plain` mode), seeded with `kb new` through the BOUND KB's
engine (the one the path gate pins). A failure after the registration leaves a registered project
whose health shows what is missing. A project can never enable a tenant plugin: a missing one is
409 `plugin_missing` before anything is written.

**Audit (audit-first, fail-closed).** `project.plan_created`, `project.created` (`outcome`
`requested` before, `applied`/`error` after), `project.adopted`, `project.opened`,
`project.feature_toggled` (one per feature, `to` on/off), `project.repaired`,
`project.registry_removed`, `project.blocked` (a refusal; best effort). Allowlist: `project_id`,
`feature`, `to`, `template`, `files` (count), `reason_class`, `outcome`, `tenant_id` - never a path.
`project.feature_files_removed` and `project.video_created` are registered with their tasks.

## Console panel (T-0357)

`/app/projects` (sidebar: Projects, below Tasks). **Two registrations** are needed and both exist: `PANELS`
(`web-next/src/panels/registry.tsx`, `rc("projects", ...)`) mounts the route, `NAV_GROUPS`
(`web-next/src/components/layout.tsx`) draws the sidebar entry; `tests/unit/panel-nav-wiring.test.ts` fails on
only one of the two. The page is a DIRECTORY (`src/pages/projects/`); a sibling `pages/projects.tsx` would shadow
it silently (`tests/unit/page-dir-shadow.test.ts`).

| Piece | Path |
|---|---|
| Page (list, empty state, error) | `web-next/src/pages/projects/index.tsx` |
| Mappings (state, feature names, empty-state text, sorting) | `web-next/src/pages/projects/encodings.ts` (unit-tested; no mapping lives in the component) |
| API client | `web-next/src/lib/api/projects.ts` |
| Unit tests | `tests/unit/projects-encodings.test.ts`, `tests/unit/projects-page.test.tsx` |
| Browser test (real bundle, network stubbed) | `tests/e2e/projects-panel.spec.ts` |

Behaviour: a project whose folder is gone is SHOWN as "Directory missing" with the server's reason and sorted
first - never dropped; the cards show readable feature names (`core` is not a chip), what the template offers but
the project switched off, the template and the folder. With no project the empty state explains the folder model
("removing it from this list never deletes a file") and, when the template plugin is not enabled for the tenant,
names that and links to the Marketplace. There is deliberately NO "New project" / "Adopt" button yet: a button
that does nothing is a stub on a production surface; the wizard (T-0358) adds it. UI text is English and carries
no ADR id and no plugin index id (asserted).

To run the browser test against the live console (it only stubs and reads): `CORVIN_E2E_ALLOW_LIVE=1
CONSOLE_BASE_URL=http://127.0.0.1:8765/console npx playwright test -c playwright.config.ts
tests/e2e/projects-panel.spec.ts --project=chromium`. The console is `data-theme` driven, so the theme test sets
`localStorage["corvin-theme"]` (plus `corvin-dark-default-v2`, or the boot script migrates light to dark) and asserts
the rendered `data-theme` first - a run that renders one theme twice proves nothing.

## New-project wizard (T-0358)

"New project" on `/app/projects` opens a four-step dialog (`web-next/src/pages/projects/wizard-dialog.tsx`):
template, name and optional folder, features (requirements shown, a missing tenant plugin named), review. The
review is the server's **plan** (`POST /v1/console/projects/plan`): nothing is written until "Create", which sends
the plan id (`POST /v1/console/projects`). Create stays disabled unless the plan says `ready`; a conflict or a
missing plugin is shown in the review, a refusal (path rule, unknown template) is shown as the server's own
sentence and does not advance. Every decision lives in `pages/projects/wizard.ts` (unit-tested): switching a feature
on pulls in its requirements first, core is never a choice, an empty folder means the server default.

Proof: `tests/unit/projects-wizard.test.ts` (incl. a mutation: dropping the toggled feature fails it, which it did
before the fix), `tests/e2e/projects-wizard.spec.ts` (the served bundle, network stubbed: four steps, gating,
review, a pulled requirement, a refused plan, a conflict). The real create path is proven over HTTP in
`tests/e2e/test_projects_api_e2e.py`; the browser spec never creates a real project.
