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

## Inspector (T-0359)

`/app/projects/:projectId` (a detail route: mounted in `web-next/src/App.tsx`, not PANELS; not in the sidebar).
Built tabs: **Overview** (the server's health checks, headline never "all good" over a red check), **Features**
(switch on/off; a switch-off that would take dependants away asks first and then sends `cascade`; a switch-on that
needs more asks first and then sends `accept_dependencies`; a feature whose tenant plugin is not enabled cannot be
switched on and says why), **Settings** (repair: preview, then apply; show the open command; remove from the list
with a confirmation that the files stay). Pure decisions in `pages/projects/inspector-logic.ts` (unit-tested).

**Also built** (T-0359): Structure (`GET /projects/{id}/structure`, read-only, two levels, .git left out, symlinks listed
never followed), Docs (knowledge-base counts from the same read), Knowledge (opens the project's graph with
`?scope=project:<id>`), Tasks (the project's items from `GET /task-tracking/items?scope=project:<id>`, a link to the
scoped board), Videos (the rows of videos/INDEX.md, each with whether its folder exists), Directory (the project's
`directory.peers`, empty explained), Compute (the compute worker check from Overview health, and a note that runs are not
linked to a project yet). A tab whose feature is off shows **Feature off** with the requirements and a switch that sends
`accept_dependencies`. Tab gating: `pages/projects/inspector-logic.ts` (`TABS`, `tabGate`); bodies: `inspector-tabs.tsx`.
Proof: `tests/e2e/test_projects_structure_e2e.py` (folder listing unchanged after reading; another tenant without
structure), `tests/e2e/projects-tabs.spec.ts` (5 browser tests on the served bundle, incl. a tab with its feature on and
the feature-off switch), `tests/unit/projects-inspector.test.ts` (13).

**Not built yet** (T-0359 stays open until this is verified on the other tabs' data): nothing in the list above is
missing; the open part is a Playwright run of every tab against a real created project (the stubs answer the shape). Proof so far: `tests/unit/projects-inspector.test.ts` (9, incl. a mutation
probe on the dependants confirmation), `tests/e2e/projects-inspector.spec.ts` (6, served bundle, network stubbed).

## Scope of the Tasks board and the Knowledge Graph (T-0360)

Both pages read one URL parameter, `?scope=`: `all` (Tasks only, the default there), `bound` (the tenant's own
knowledge base), `project:<id>` (one project's knowledge base), `both` (Knowledge Graph only: the bound knowledge base
and every selectable project joined). Unknown project ids fall back to the default rather than showing an empty board.
Pure decisions in `pages/projects/scope.ts` (9 unit tests incl. a mutation probe on the qualification).

- **Tasks:** `getTaskItems(signal, false, scope)` passes the parameter to `GET /task-tracking/items`; the scope survives
  every change of view (the page rebuilds its query string, so `setQuery` carries it over explicitly).
- **Knowledge Graph:** one request per knowledge base (`/graph` or `/graph?project=<id>`), joined by `mergeGraphs`:
  a project's labels are qualified (`alpha/T-0001`), two equal local ids stay separate nodes, relations never cross
  knowledge bases, so the two graphs form two islands. A node's document is read with its own project
  (`/doc/{key}?project=<id>`). The query key carries the project ids: a direct link is parsed before the project list
  has arrived, and the graph must load again once it has (found by a screenshot; the test now covers a late list).
- **Card moves** still use the existing board path (`kbTransition` → `/kb-transition`); the server resolves the
  project from the card's label (proven over HTTP in `tests/e2e/test_project_kb_writes_e2e.py`).

Proof: `tests/e2e/projects-scope.spec.ts` (3 browser tests on the served bundle, endpoints stubbed, incl. the direct
link with a delayed project list). Screenshot checked: two islands, qualified labels, "4 of 4 nodes shown".

## New video (T-0361)

`POST /v1/console/projects/{id}/videos` `{name}` (session + CSRF) creates one video's folder under the project's
`videos.root` (default `../Corvin-Videos`, relative to the project; refused inside CORVIN_HOME): `source/`, `build/`
(each with a `.gitkeep`), `sources.md` from the project's `videos/TEMPLATE-sources.md` (its title line replaced; refused
when the template lacks its eight sections), and a row in `videos/INDEX.md`. With the `kb` feature on it also creates
the epic `VIDEO-<name>` under the project's first initiative and the tasks Narration/Render per language, the number
check (DE against EN, when both are present) and the acceptance task, each with a definition of done, through the bound
engine. Refused: a bad name (lowercase kebab-case, at most 60), an existing folder (409 `exists`), the videos feature
off (409 `feature_off`). Audit-first: `project.video_created` requested, then applied or error; on any failure what this
call created is removed and nothing that existed before. The inspector's Overview holds the form; the name rule is the
same in the page (`videoNameProblem`) and on the server.

Proof: `tests/e2e/test_projects_videos_e2e.py` (4: one run with the eight sections, the index row, the epic and the six
tasks, a fresh `kb check` blocking 0 and the audit sequence; no knowledge base; names and existing folders refused
before any write; the feature off). Mutation probe: writing a generic sources.md fails the convention test.
Browser: `tests/e2e/projects-videos.spec.ts` (4, served bundle, network stubbed).


## Doctor calibration, Adopt, Rename, Move (T-0362)

- **Doctor** (`GET /projects/{id}/health`): a checklist of real checks. Calibrated against nine seeded defects
  (directory missing, unparseable manifest, wrong id, foreign tenant, a requirement switched off, a hand-edited
  generated section, `docs/kb` removed, an inconsistent KB entry, a missing tenant plugin) - all found, and a clean project
  has no failed check (`tests/e2e/test_projects_ops_e2e.py::DoctorCalibration`). A `CLAUDE.md` WITHOUT the generated
  marker is the user's own file: it is never rewritten and never reported as drift (by design, not a gap).
- **Adopt** (`POST /projects/adopt`, body `location`, optional `title`, `project_id`): inside the projects root only.
  A folder with a `project.yaml` is registered as is. A folder without one gets exactly that file - docs mode and the `kb`
  feature are read from what is on disk, nothing else is written, `.git` included. Proven on a copy of the real
  `LLM_lie_detector` repository (every file hashed before and after; only `project.yaml` is new).
- **Rename** (`PUT /projects/{id}/title`): the title in `project.yaml` only; the id (registry key, KB scope label) never changes.
- **Move** (`POST /projects/{id}/move`, body `location`): a same-disk rename to an absent or empty place under the
  projects root; never into itself, never over content. The index follows, then repair regenerates the machine-local files
  (they hold absolute paths); the response carries `repaired_files`. Audit: `project.renamed`, `project.moved`.
- UI: "Add existing folder" on the list, Rename and Move in the Settings tab (Move needs a second confirming click).
  Browser proof: `tests/e2e/projects-manage.spec.ts`.


## Chat attachment (T-0364, ADR-2317)

A project can be attached to one console chat (Settings tab, "Use in a chat"; API `POST /projects/{id}/attach`,
`POST /projects/detach`, `GET /projects/chat-attachment?session_id=`). The attachment is console state per chat in
`<tenant_home>/projects/chat_attachments.json` (0o600, names a project id only - never a path, never inside the chat's
working directory). The project is NEVER the CLI's cwd. On each spawn (`chat_runtime._build_args`,
`_project_attachment_parts`; implementation `core/projects/attachment.py`) the CLI gets, verified with real runs (NOTE-0018):

| Need | Mechanism |
|---|---|
| project rules in context | `--add-dir <project>` + env `CLAUDE_CODE_ADDITIONAL_DIRECTORIES_CLAUDE_MD=1` (`--add-dir` alone only grants file access) |
| project MCP servers | an extra `--mcp-config <project>/.mcp.json` (additive: the persona's servers stay; a symlinked or oversized file is not passed) |
| read-only project | `--disallowedTools Write(//<abs>/**) Edit(//...) NotebookEdit(//...)` - the `//` form holds even under `--dangerously-skip-permissions`; the `/<abs>/**` form does NOT |
| data flow | the project's `CLAUDE.md` passes L34 against the OS engine per spawn; a refusal or an error withholds the whole attachment |

Boundaries (stated, not hidden): a Bash write is outside what a tool rule can see (same as the path gate); `--resume <id>` works
from any cwd, so chat isolation rests on the console never passing another chat's id; Windows path form of the deny rule is unverified.
Proof: `tests/e2e/test_projects_attach_e2e.py` (API half always; live half with `CLAUDE_LIVE_E2E=1` drives a real chat over the real
WebSocket: rules in context, project MCP tool answers, a write into the project is refused, rules gone after detach, project tree
byte-identical) and `tests/e2e/projects-manage.spec.ts`. Audit: `project.attached`, `project.detached`.

**Project menu (replaces the former chip).** A chat's header shows ONE button (`pages/projects/chat-menu.tsx`): the attached project's
title, a count of changed files, or "Project" when none. It opens a multiple-choice list ("No project" first, then every project in state
`ok` with role, class and access) - one click attaches or detaches, the menu closes; a refusal (the data class may not go to this engine) is shown
in words and the choice stays. Entries: Changes, Storage (`?tab=structure`), Settings (`?tab=settings`), All projects. On a phone the menu is a
fixed full-width sheet. See "UI" below.


## Roles, classification and assistant access (T-0368..T-0370, ADR-2319, CONCEPT-0127)

- **Role templates** (plugin `project_templates` 1.1.0): software, data science, marketing, product, sales, legal and compliance, plus video,
  research and knowledge-only. A template names a closed-list `role`, a default `classification` (L34 vocabulary) and `assistant_access`, and an
  `outcome` in plain words; the host refuses a value outside the lists. `GET /projects/templates` returns them; `POST /projects/plan` accepts
  `classification` and `assistant_access` overrides and returns the resolved values.
- **`project.yaml`** carries optional `role`, `classification` (default `internal`), `assistant_access` (default `read`).
- **Settings** `PUT /projects/{id}/settings` `{classification?, assistant_access?, confirm?}`: raising the class applies at once; LOWERING it or raising
  access to `write` answers 409 `confirmation_required` until `confirm: true`. Audit `project.settings_changed` (enums only).
- **Enforcement** is the existing L34 gate: `POST /projects/{id}/attach` and every spawn of an attached chat run
  `chat_runtime.project_class_refusal` (= `check_l34(<OS engine>, tenant, classification=<project class>)`); a refusal answers 409
  `reason_class: classification`. `assistant_access: write` removes the write-deny rules of THAT project only (`attachment.spawn_parts`).
  With no tenant data-classification policy L34 is opt-in, except that a `secret` class is still refused.
- **Rights** mean what the assistant may do. A per-user access list is not built: there is no credential login to give it a subject.
- **Change view** (`core/projects/changes.py`): `GET /projects/{id}/changes[?commit=]` lists files that differ from the last commit in the project and
  the nested `docs/` repository (status, +/- lines, area code/docs/knowledge, ids found in the file name as links) plus recent commits;
  `GET /projects/{id}/changes/file?path=[&commit=]` returns the unified diff, or the text of a new file. Read-only git with
  `GIT_OPTIONAL_LOCKS=0`; a path outside the repository, through a symlink, `.git`, or not in the listing is refused (400); binaries and
  files over 200 KB are listed, not rendered. Proof: `tests/e2e/test_projects_roles_e2e.py`, `tests/e2e/test_project_templates_catalogue_e2e.py`.


## UI of roles, protection and changes (T-0371..T-0373, CONCEPT-0127)

- **Wizard** (`wizard-dialog.tsx`, words and grouping in `roles.ts`): step 1 shows the templates as role cards in three families - Build
  (software, data science, video), Grow (marketing, product, sales), Govern and learn (legal, research, knowledge) - each with its outcome in plain
  words, its class badge and its access badge. Step 3 starts with "Protection" (class and access), pre-filled from the role; the review shows it.
- **List** (`index.tsx`): every card shows role, class and access (`badges.tsx`: label + icon shape + tone, never colour alone); a Role and a Data
  class filter appear when there is something to filter, offer only values that exist, and an empty result says so with "Show all".
- **Settings** (`manage.tsx::ProtectionSection`): class and access. The server decides what needs confirmation (409 `confirmation_required`);
  the UI then asks once ("This lowers the protection of the project ... Are you sure?") and resends with `confirm: true`.
- **Changes panel** (`changes-panel.tsx`, logic in `changes-logic.ts`): a side panel next to the chat. Files grouped Code, Documents, Knowledge base,
  each with a status letter AND word, +/- counts; click opens the diff (lines typed add/del/hunk/ctx, the leading +/- stays in the text) or the text of a
  new file; a binary is listed, not shown. "Show" switches between changes not yet committed and a recent commit. Ids in file names and commit subjects
  link to the project's Tasks board (`/app/initiatives?scope=project:<id>`) and Knowledge Graph (`/app/corvin-knowledge?scope=project:<id>`); a change
  with no id has no link. The list is read again every time the panel opens and every 10 s, and the menu button reloads the count when an answer finishes.
- **Perception choices, checked on screenshots** (both themes, 1280 and 375 px): nine roles in three rows (chunking, Hick's law), outcomes before feature
  jargon (recognition over recall), badges always in the same shape and place (similarity), safe default (read only) and a second look only in the
  direction of less protection (forgiveness), no animation beyond the dialog's fade. Two defects were found only by looking: access badges that wrapped
  in the wizard, and a menu that left the screen on a phone.
- Proof: `tests/e2e/projects-roles.spec.ts`, `projects-chat-menu.spec.ts` (real chat session, stubbed project endpoints), unit tests `projects-roles`,
  `projects-changes`, `projects-wizard`.
