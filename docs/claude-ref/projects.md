# Projects (ADR-2315, CONCEPT-0126)

A **project** is a directory the user owns, with a `project.yaml`. CorvinOS keeps only an index of it.
Losing the index loses no project. Design: `Corvin-Knowledge` CONCEPT-0126; decisions ADR-2315
(project object), ADR-2316 (several KBs, scope), ADR-2317 (chat context); plan PLAN-0970.
Status 2026-11-08: registry, manifest, fragment engine and the API exist (T-0351, T-0352, T-0355); the
template catalogue is the first-party plugin `project_templates` (T-0354, T-0356, in Corvin-Marketplace, published); the console's Projects panel, wizard, inspector, scope selector, videos action and doctor exist (T-0357 to T-0362). Status 2026-11-11 (PLAN-0972 phase 1, ADR-2321): the managed workspace is the default place, one location rule judges every path a client names, and the console shows it (Settings sections, Where row and filter, default-place dialog, wizard step 2) - verified on Linux; the Windows VM run of phase 1 (T-0389) has NOT been done.

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
| Where new projects go | `<tenant_home>/projects/settings.json` (schema 2: `default_storage` `managed`\|`root`, `projects_root` - unset = `~/projects` as the fallback folder rebuild looks in, `workspace_key`, `managed_root`); written only by `registry.write_settings` |
| Proof | `tests/e2e/test_projects_registry_e2e.py`, `test_projects_fragments_e2e.py`, `test_projects_api_e2e.py`, `test_project_templates_catalogue_e2e.py` (the REAL catalogue) |
| Platform data directory, managed workspace path, user places, drives, the lists the location rule judges against | `core/paths/user_dirs.py` (ADR-2321 Decision 7, stdlib only, Python 3.11-safe) |

### Erasure and retiring a tenant (ADR-2321 Decision 13)

GDPR Art. 17 (L36): the layer `L-projects` (`erasure_handlers.ProjectsMetadataHandler`, in `real_handler_chain`)
reports `SKIPPED` for every subject with the note "projects metadata carries no per-person key; project files are
user-owned and outside Art. 17 automation — review them in the Projects panel", deletes nothing and never logs the
subject id; `<tenant_home>/projects` is listed in `UNATTRIBUTABLE_DIRS`. Proof:
`tests/security/test_erasure_projects_handler_e2e.py` (through `ErasureOrchestrator` with the real chain; every
planted file and project folder byte-identical afterwards).

**Retiring a tenant** - CorvinOS has no tenant-offboarding entry point (NOTE-0020 O10). An operator removes by hand:
`<tenant_home>/projects/` (registry, settings, chat attachments, locks; later picker history, import staging, export
jobs) and the tenant's `managed_root` (the path in `settings.json`, including `.trash/`). `own` folders are the
user's and are never part of it.

### The managed workspace (ADR-2321 Decision 7)

A tenant's *Corvin workspace* lives in the platform's per-user data directory, OUTSIDE `CORVIN_HOME`:

| Platform | `managed_root` |
|---|---|
| Linux | `$XDG_DATA_HOME/corvin/projects/<key>/` (default `~/.local/share/corvin/projects/<key>/`) |
| macOS | `~/Library/Application Support/Corvin/projects/<key>/` |
| Windows | `%LOCALAPPDATA%\Corvin\projects\<key>\` |

`user_dirs.managed_parent()` is `<data>/corvin/projects/`; `<key>` is an opaque `w-<10 lowercase hex>`
(`user_dirs.new_workspace_key()`, `secrets.token_hex(5)`). The key rule: never a tenant id, never a word the
path gate's `_looks_protected` matches, never derived from where `CORVIN_HOME` lies; it is stored in the tenant's
`settings.json` (`workspace_key`) and in the workspace marker. `user_dirs.managed_projects_root(key)` refuses
anything that is not `w-<10 hex>`. The module also answers the lists the location rule uses:
`system_prefixes()` (Decision 10 check 6; `/opt`, `/srv`, `/media` deliberately absent),
`is_under_system_prefix()` (given and resolved form; the own home's subtree is exempt when the home itself lies
below a prefix, and so is a path strictly below `/run/media/<own user>/<label>`), `home_roots()`,
`app_data_dirs()`, `platform_links()`, `user_places()`, `drives()` (never raises; a mapped network drive is a
`drive` with note `network`, never probed there) and `display_path()`.

**Migration (existing installs).** Nothing moves. Every project registered before the managed workspace existed reads
`storage: own` and stays where it is; a tenant whose `settings.json` already names a `projects_root` keeps creating there
(`default_storage: root`), a fresh tenant creates in the workspace. The managed workspace is OUTSIDE the tenant home, so a
backup of `CORVIN_HOME` does not contain it: back it up with "Export all projects" once E-058 ships; until then copy the
folder whose path the Projects page names under Change… (the `managed_root`).

**Tests never reach the real workspace.** The repo-root `conftest.py` puts `XDG_DATA_HOME` (on Windows also
`LOCALAPPDATA`/`APPDATA`) into the session sandbox for every test process, and its tripwire lists the real
`<data dir>/corvin/projects` of every platform form (resolved once, before any env change) around every test: a
new entry there fails the test ("wrote into the real Corvin workspace"). `corvin_operator/bridges/run-all-tests.sh`
exports the same for plain-script suites. `HOME` stays the operator's (the live suites need `~/.claude`); on macOS
the data dir follows `HOME`, so there the guard is the tripwire plus the projects test `Sandbox`, which moves `HOME`.
Proof: `tests/projects/test_user_dirs.py`, `tests/security/test_conftest_data_dir_sandbox.py`.

## The directory is the truth

`project.yaml` (schema 1): `id` (kebab-case, 1-28 chars: the projector's scope label `prj-<id>` must
pass its 32-char rule), `title`, `tenant`, `template` (`<id>@<n>`), `docs_mode` (`nested`|`plain`,
default `nested`, see ADR-2315 amendment), `features` (id -> bool), optional `videos`, `directory.peers`.
No secret and no machine-local path. Unknown top-level keys are kept on rewrite.

The registry holds `{"schema": 1, "projects": [{id, path, template, added_at, storage}]}` and is rebuilt from
the directories by `registry.rebuild(tenant)` (adds, never removes, never touches a directory). The
operator's `projects_root` lives in a SEPARATE file so that deleting the index never loses the place
`rebuild` has to look in. `corvin_console.kb_projection.project_kbs` reads this same shape.

**Storage kinds (ADR-2321 Decisions 1, 3, 9).** Every entry has `storage`: `managed` (its folder lies directly
in the tenant's managed root) or `own` (anywhere else; every pre-ADR-2321 project, e.g. `~/projects/x`, reads
`own` and is never moved or touched). The built-in `corvin` tile is `builtin`. The kind is derived from the path
(`registry.storage_of`), healed on read for an entry without it and written back on the next save; `relocate`
recomputes it. A NEW project's place, first match wins (`registry.default_location`): an explicit folder from
the client; `default_storage: root` -> `<projects_root>/<id>`; `default_storage: managed` (the shipped default)
-> `<managed_root>/<id>`. `rebuild` scans the managed root AND the projects root (direct children, dot-named
ones skipped, `project.yaml` naming this tenant); an `own` project elsewhere is re-found only by adopt.

**Settings, schema 2 (ADR-2321 Decision 8).** `<tenant_home>/projects/settings.json` (0o600):
`{"schema": 2, "default_storage": "managed"|"root", "projects_root"?, "managed_root", "workspace_key"}`.
A schema-1 file (`{"projects_root": …}`) or none reads `default_storage = root` when a root is set, else
`managed`; the value is fixed at the first write, so a tenant that had set a root keeps creating there.
`registry.write_settings(tenant, **changes)` is the ONE writer: read-merge-write under the registry lock,
unknown keys kept, `default_storage: root` without a root refused (`root_required`). `set_projects_root`
merges through it, so `PUT /projects/root` keeps `default_storage`, `managed_root` and `workspace_key`.
A tenant without `settings.json` (the default `~/projects` was never set explicitly) reads `managed`: its new
projects go to the workspace from now on, its existing ones stay where they are.

**Workspace binding (ADR-2321 Decision 7).** `registry.ensure_managed_root` creates the managed parent and the
key directory 0o700 (re-applied when wider; a link is refused), writes the key into `settings.json` and the
marker `workspace.json` `{"schema": 1, "tenant", "workspace_key", "corvin_home": "<resolved>"}`. Rules:
(a) marker names this tenant, key and home -> used; (b) another home that no longer exists (a moved
`CORVIN_HOME`) -> the marker is rewritten, `project.storage_changed outcome=rebound`; (c) another home that
still exists (a copied install) -> `workspace_foreign`, 422 "This workspace belongs to another Corvin
installation.", nothing written; (d) another tenant or key -> `workspace_foreign`; (e) no key in
`settings.json` -> the managed parent's children are scanned for a marker naming this tenant AND this home
(`outcome=recovered`), else a new key. Every binding outcome is audited BEFORE the write. `GET /projects` binds
the workspace on read and reports `workspace: ok|foreign|unavailable`. `registry.rekey_workspace` starts a new
workspace (`outcome=rekeyed`; the Settings action, T-0387/T-0408).

## Locations a client may name (`registry.validate_location`)

ONE rule (ADR-2321 Decisions 4, 10, 10a, 15; replaces ADR-2315 Decision 6):
`registry.validate_location(tenant_id, raw, *, purpose, project_id=None) -> Path`, `purpose` in `create`
(plan and create), `adopt`, `projects_root` (`PUT /projects/root`; `validate_root` is this purpose), `move`,
`relocate`, `import`. A client-supplied location may be `<managed_root>/<id>`, a folder under the projects
root, or **any other folder that passes the rule** — "only under the projects root" is withdrawn (the one
deliberate widening: "Add existing folder" from anywhere the rule allows). The picker's verdict and the mutation
call the same function; the mutation re-validates on confirm. A refusal is a `ProjectPathError` with
`reason_class` (the HTTP 422 `detail.reason_class` and the `project.blocked` record carry it) and the class's
fixed sentence (`registry.SENTENCES`), never a path.

Two forms are judged: *given* (`normalize_input`, made absolute lexically, no link followed) and *resolved*.
Every component check runs over ALL components of both forms, from the anchor to the target. Order:

| # | Class | Refuses |
|---|---|---|
| 1 | `path_rule` | not a string, empty, NUL, > 1000 characters, not absolute, `..`; on Windows `normalize_input` first: every UNC (`\\server\share`, `\\?\UNC\…`) and device path, for every purpose, before anything is stat-ed |
| 2 | (`path_rule`) | `_check_windows_names`: device names to be created, trailing dot/space, `< > : " \| ? *` |
| 3 | (`path_rule`) | `_check_length`: the MAX_PATH budget when long paths are off |
| 4 | `top_level` | the anchor; fewer than three parts (`D:\projects` - use `D:\work\projects`); any user's home (a direct child of `/home`, `/Users`, `/var/home`, `C:\Users`, or `/root`); the own home or an ancestor of it; a mount point `user_dirs.drives()` reports (a folder below it is fine) |
| 5 | `corvin_home` | inside, above or equal to the resolved `corvin_home()`, the real `~/.corvin` or `$CORVIN_HOME` as given; also by identity (`os.path.samefile` on the target and each existing ancestor), so a bind mount of the home is caught |
| 6 | `system_prefix` | `user_dirs.is_under_system_prefix` on both forms (own-home subtree exempt when the home lies below a prefix; strictly below `/run/media/<own user>/<label>` exempt) |
| 7 | `protected` | a component in `PROTECTED_COMPONENTS` (`.git`, `.corvin`, `session_ledger`, `.corvin-ledger`, `cel_anchors`, `pending_notifications`, `tenants`, case-folded) at any depth; a path inside another user's home |
| 8 | `hidden` | a dot component below the own home (outside it: below the anchor); an application-data tree (`user_dirs.app_data_dirs()`: `~/.config`, `~/.local/share`, `~/.local/state`, `~/.cache`, `~/Library`, `%APPDATA%`, `%LOCALAPPDATA%`); the components of the own managed root are exempt |
| 9 | `other_tenant` / `workspace_root` | the managed parent, or anything under it outside the own `managed_root` (`other_tenant`); a directory another tenant's registry lists (`other_tenant`); the own `managed_root` itself or anything below a dot-named entry of it, e.g. `.trash/` (`workspace_root`) |
| 10 | `symlink` | any component of the GIVEN form that is a link by `fsutil.is_link` (symlink, junction, name-surrogate reparse point), except the ancestors of the own home and `user_dirs.platform_links()` (macOS `/etc`, `/tmp`, `/var`) |
| 11 | `nested` | inside or containing any tenant's registered project (the moved project ignored; re-adopting the tenant's own registered path passes, the service answers `id_taken`); a valid `project.yaml` in any ancestor; the projects folder itself (an added refusal) |
| 12 | per purpose | `create`/`move`/`import`: absent or empty (`not_empty`); `adopt`: exists (`missing`), `project.yaml` absent or naming this tenant (`other_tenant`; invalid -> `wrong_manifest`; a linked one -> `symlink`); `relocate`: holds this project's manifest of this tenant (`wrong_manifest`); `projects_root`: not a file |

**Grandfathering (REVIEW-0062 N1).** For every purpose except `projects_root`, a target below the stored
projects root (given below given AND resolved below resolved) is judged by checks 4, 6, 7, 8 and 10 only on the
components BELOW that root (and the root itself must not be a link); check 5 and checks 1-3, 9, 11, 12 run in
full. So a stored `~/.projects`, a root below a linked folder or a root in `/var/tmp` keeps accepting creates,
while `<root>/a/.git/b`, `<root>/.hidden/b` or a link below the root are still refused. The root itself is
re-judged by the full rule only when it is set again (`PUT /projects/root` with the same value of a `~/.projects`
root answers `hidden`). Registered projects are never re-judged on read.

**Kept from 65b587f2e (Decision 10a)** - the rule only adds refusals: tenant id validation, the input
type/NUL/length rule, `normalize_input` with the UNC/device refusal, absolute and no `..`, Windows names, the
length budget, the mapped-network-drive rule (a drive letter that resolves to UNC keeps the letter), fewer than
three parts, inside/above/equal `CORVIN_HOME`, `PROTECTED_COMPONENTS`, hidden, the link walk and a linked
projects root, `_other_tenants_claiming`, `nesting_conflict`, the adopt manifest checks, the create rule, a file
is not a folder. `tests/e2e/test_projects_location_e2e.py::LocationRule::test_the_kept_checks_refuse_as_before`
runs the same inputs through the function at 65b587f2e (loaded from git) and the current one. The pre-ADR-2321
keywords `adopt`/`unmanaged`/`ignore` are still accepted by `validate_location` ONLY so the regression tests of
6250cd0b6/65b587f2e run unchanged; no production caller passes them.

**Test sandbox.** The rule refuses `/tmp` (system prefix) and other users' homes, so every projects test makes
its temp dir the OWN home: `tests/e2e/_projects_fixtures.py::Sandbox` and `sandbox_home_env(tmp)` set `HOME`,
`XDG_DATA_HOME` (on Windows also `USERPROFILE`, `LOCALAPPDATA`, `APPDATA`) into it and restore them. There is no
test-only mode in the rule.

## Picker: the server-side folder browser (ADR-2322, T-0390)

`core/projects/browse.py` is the pure core (no FastAPI import, no audit call; stdlib only, Python 3.11-safe). The route
(T-0391) adapts it and owns the budget, the throttle and the audit.

**What is listed.** Directories only - a listing never returns a file name, size, count, mtime or content; a link to a file
is skipped like a file. Each entry: `name`, `path` (where the row leads), `kind: "dir"`, `hidden`, `symlink`, `readable`
(`os.access(R_OK|X_OK)`), `project` (`mine` with `id` + `title` from the manifest, `other` - existence only, never an id or a
title -, or `none`) and an advisory `refusal` (`symlink`, `corvin_home`, `system_prefix`, `other_tenant`, `protected`,
`hidden`, `nested`, `top_level`). An entry is a link when `entry.is_symlink() or fsutil.dirent_is_junction(entry)`; a link is
never listed into or entered (`symlink: true`, `refusal: "symlink"`). Hidden entries (dot names, the Windows hidden
attribute) only with `hidden=1`, never below the own managed root. Sorted case-insensitively; 200 per page, `next_page` is
the last name of the page, `truncated` when more remain (or more than 20 000 entries were scanned). The listing also
carries `path`, `display` (`user_dirs.display_path`), `parent`, `breadcrumbs`, `readable` of the folder itself and the
`verdict`.

**What is never entered** (answers `entries: []` with the reason as the verdict, the class's fixed sentence): a path with a
linked component (the link walk of check 10, from the anchor or from a stored projects root the path lies below) ->
`symlink`; at or below a Corvin home, lexically or by `st_dev`/`st_ino` identity of the path or an ancestor -> `corvin_home`
(an ANCESTOR of a home is listed, the home is a row in it); a system prefix (`user_dirs.is_under_system_prefix`, the
own-home and `/run/media/<own user>/<label>/…` exemptions) -> `system_prefix`; the managed parent, a workspace below it
other than the own `managed_root`, or at/below another tenant's registered project -> `other_tenant`. The managed parent is
one row "Corvin workspaces" whose `path` is the own `managed_root` (no workspace yet: the row carries `refusal:
"other_tenant"`); no other workspace key is ever named.

**Input rule.** `browse.check_input` - checks 1-3 of ADR-2321 Decision 10 - runs before anything touches the filesystem:
type, NUL, at most 1000 characters, `registry.normalize_input` (every UNC/device path refused with its sentence "… map the
share to a drive letter"), absolute, no `..`; nothing is expanded (`~/x` is refused). Purpose outside `registry.PURPOSES`,
or `move`/`relocate` without `project_id`, is the same refusal. Windows names and length run next. A violation is
`BrowseInputError` (`reason_class = "path_rule"`).

**Verdict = the location rule.** `browse.verdict(tenant, path, purpose, project_id)` is `registry.validate_location`
caught into `{ok, reason_class, message[, warnings: ["hint_word"], notes: [{class, message}]]}` (`notes` carries the
sentence per warning class with the matched hint word, `registry.location_warnings` - "The assistant may refuse some shell
commands in a folder whose path contains 'forge'." - so the picker shows the server's sentence, T-0393); `ok` reads "Can be used — a new folder will be
created when you continue." for create/move/import, "Can be used" otherwise. A listing that can be entered carries exactly
this verdict; a blocked one carries the block's class (so `/etc` answers `system_prefix`, while the rule alone would say
`top_level` for that two-part path). The mutation re-validates on confirm.

**Places** (`browse.places`): `managed` ("Corvin workspace", when the tenant has a key), `root` ("Projects folder", when
set), `user_dirs.user_places()` (home, desktop, documents), `user_dirs.drives()`, then `recent` and `favourite` rows.
Nothing is created by reading places.

**Picker store** `<tenant_home>/projects/picker.json` (0o600, `atomic_write`, under `picker.lock`):
`{schema: 1, recent: [...], favourites: [...]}`. `record_recent` keeps the last 8 confirmed locations, newest first,
deduplicated (`os.path.normcase`); `set_favourites` replaces the list (at most 10 -> "At most 10 favourites.", each judged
by the input rule); `clear_recent` empties `recent`. Never audited; the `L-projects` erasure handler reports it `SKIPPED`
(no per-person key, ADR-2321 Decision 13). Recent places are recorded on success by `plan_project` (only with an
explicit `location`), `adopt_project`, `move_project`, `relocate_project`, `set_root` and `set_default_storage` with a
projects root - never by the browser.

**Routes, bounds, audit (T-0391, ADR-2322 Decisions 5-8, 11, 13).** `GET /v1/console/projects/-/browse/places`,
`GET /v1/console/projects/-/browse?path&purpose[&project_id][&hidden=1][&page]`, `PUT …/-/browse/favourites {paths}` and
`DELETE …/-/browse/recent` (CSRF for both). Session required, the tenant is the session's, no extra toggle or login (the
host guard of ADR-2313 applies as on every console route). Order of one listing request, all non-blocking:

1. per-session token bucket (10 requests/s, burst 10, bounded LRU of 256 sessions) - beyond it 429 `{"message": "Too many
   folder requests at once; wait a moment.", "reason_class": "throttled"}`;
2. `browse.check_input` on the loop (pure; a UNC/device path never reaches the filesystem) - 422 `path_rule` with the rule's
   sentence;
3. a finished late result of the same request (tenant, path, purpose, project id, hidden, page) younger than 10 s is
   answered at once;
4. slots: per tenant 2, global 4 (`projects_service.BrowseGate`, counters under one lock; only tenants with a running listing
   are tracked) - none free -> 200 `slow` at once;
5. the listing runs on `loop_health.browse_pool()` - a dedicated `ThreadPoolExecutor(max_workers=4,
   thread_name_prefix="corvin-browse")`, pre-warmed by `loop_health._prewarm_then_watch` in BOTH hosts' lifespan
   (`install_serving`), never the default executor that `asyncio.to_thread` and audit writes share - awaited with
   `asyncio.wait_for(asyncio.wrap_future(…), 2.0)`. On timeout 200 with `entries: []` and verdict `{ok: false,
   reason_class: "slow", message: "This folder did not answer in time (network share?). Try again or choose another
   place."}`. The thread is not cancelled: its slots are released by the future's done-callback when it really returns, and
   its result is kept 10 s for the next identical request. So at most 4 hung listings exist host-wide, 2 per tenant, and
   nothing else in the console waits for them.

`/browse/places` runs on the same pool with the same throttle and slots (`slow` -> `{places: [], verdict}`).
**Audit:** `project.browse_refused` (WARNING) `{reason_class, tenant_id}` on 422 `path_rule`, `slow`, `throttled` and a
listing answered with a refused verdict of class `corvin_home`, `system_prefix` or `symlink`; at most one record per
session per minute (an in-memory stamp per session id); written best-effort through an awaited `asyncio.to_thread`.
Successful listings, favourites and recents are never audited. Proof: `tests/e2e/test_projects_browse_e2e.py` (real HTTP
through the console router, concurrent requests in one event loop).

**The dialog (T-0392, `web-next/src/components/folder-picker.tsx`; every decision in `pages/projects/picker-logic.ts`,
unit-tested).** `FolderPicker {open, purpose, projectId?, title?, initialPath?, inputTestId?, onChoose(path), onCancel}` and
the small `FolderField {value, onChange, purpose, …}` (the chosen path + "Choose a folder…"/"Change…"). Title "Choose a folder
for "<title>"" (or "Choose a folder"), the line "Folders on the computer that runs Corvin. Files are not shown.". Left: the
places in four groups - Places (Corvin workspace, Projects folder, Home, Desktop, Documents), Drives and network, Recent (with
"Clear recent places"), Favourites; an unavailable place is greyed with its note. Right: breadcrumbs with "‹" up (more than
five crumbs collapse the middle to "…"), a star that adds/removes the current folder as a favourite (`PUT …/favourites`, at
most 10), "Show hidden", the rows ("<name>" + the note: "Already a project (<title>)", "belongs to another workspace", "link —
cannot be chosen", "no permission", "Corvin's own data", "system folder", "protected place", "hidden", "inside a project").
A row that is a link, another workspace's, Corvin's data, a system folder or unreadable cannot be entered (`enterable`).
"New folder here [name]" only for `create`, `move`, `projects_root` and `import` (adopting and locating need an existing
folder): virtual - the picker returns `<current>/<name>` and the action creates it; the name rule is shown inline ("Use
letters, digits, '-', '_' or '.', no leading dot, at most 80 characters.") and a valid name gets its OWN verdict (a debounced
`GET /browse` of the target) before "Choose" turns on. The verdict box in the footer shows the target path and the sentence:
"Can be used — a new folder will be created when you continue." (create/move/import) or "Can be used", else the server's
message; a hint-word note is an extra amber line and does not turn "Choose" off. "Choose" is enabled only for a loaded folder
whose verdict is `ok`. "Enter a path instead…" (Ctrl/Cmd+L) opens the advanced field, pre-filled with the current folder and
selected; "Go" lists that path and judges it by the same verdict. States: skeleton rows while listing; "No folders here."; a
blocked folder "This folder cannot be opened here."; an unreadable one "No permission to read this folder."; `slow` -> "did not
answer in time" with **Retry** (the verdict line carries the fixed sentence); 429 -> "Too many folder requests at once; wait a
moment."; 422 -> the rule's sentence; the dialog never closes on an error. **Keyboard:** arrows move, Home/End, Enter opens the
focused folder, Backspace goes up, typing jumps by prefix (700 ms buffer; one letter cycles through the matches), Space marks
the current folder as the choice and moves the focus to "Choose" (Enter then chooses), Esc cancels (only the picker: the
dialog behind it stays), Ctrl/Cmd+L opens the path field. Keys and submits inside the picker never reach the form that
opened it (React events bubble through the portal; Enter in the path field once submitted "Default place" with an empty
folder). **375 px:** a full-screen sheet; places become a horizontal chip row, breadcrumbs collapse to "‹ <parent>" + the
current name, rows are 44 px, the verdict and the buttons sit in a sticky footer. Cancel leaves the field as it was.

**Where it is used (T-0393).** Wizard step 2 "Choose a folder…" (`create`, test id of the advanced field `wizard-location`);
"Add existing folder" (`adopt`, `adopt-location`); Settings > Location **Move…** (`move` + project id, `move-input`) ->
"Move here" -> "Yes, move the folder"; **Locate folder…** (`relocate` + project id, `locate-input`; Choose relinks at once,
moves nothing); "Default place for new projects" > "A folder of my own" (`projects_root`, `place-root-input`). No location
dialog shows a bare path field before the picker is opened. The import target (T-0402) will use the same component.

Proof: unit `tests/unit/projects-picker.test.ts` (38: a scripted keyboard session, the name rule table, verdict per purpose,
row notes, breadcrumbs, places); browser on the served bundle with a deterministic stubbed tree (`tests/e2e/projects-picker-stub.ts`):
`projects-picker.spec.ts` (Choose off until `ok`, foreign/link/unreadable rows not enterable, slow + Retry, keyboard-only path,
new folder, hint warning, favourites + clear recent, no ADR id or German word, the 375 px sheet, screenshots 1280/375 x both
themes), and the dialogs' own specs (`projects-wizard`, `-manage`, `-settings`, `-panel`).

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
| `GET /` | the tenant's projects (state `ok`/`missing`/`invalid`, active features, `storage` `managed`/`own`/`builtin`, `display_path` `Corvin workspace › <id>` / `~/…` / OS form) + `default_storage`, `managed_root`, `projects_root`, `projects_root_set`, `workspace` (`ok`/`foreign`/`unavailable`); binds the managed workspace on read (ADR-2321) |
| `GET /templates` | templates, features (requires/suggests), the tenant plugins each needs and their state; `available:false` + reason when the template plugin is not enabled for the tenant |
| `POST /plan` | `{template, title, [project_id, location, features, docs_mode, storage]}`: no project write; without `location` the place is `storage` when given (`managed`\|`root`, the wizard's step 2 - it replaces the tenant default for this one create; `root` without a stored projects folder is 409 `root_required`, another value 400), else `default_storage` (`<managed_root>/<id>` or `<projects_root>/<id>`); a `location` wins over `storage`; returns `location`, `storage`, the file list with owners, `pulled` dependencies, warnings, conflicts, plugin states, `plan_id` (15 min, in memory); audit `project.plan_created`; 422 `workspace_foreign` when the workspace belongs to another installation |
| `POST /` | `{plan_id}`: re-validates location, catalogue version and plugins, then writes `project.yaml`, registers, applies, seeds the KB; 201 |
| `POST /adopt` | `{location}`: registers an existing directory whose `project.yaml` names this tenant; writes nothing in it |
| `GET /{id}`, `GET /{id}/health` | detail with the feature catalogue (dependants, plugin states, suggestions) and `location_warnings` (`[{class: "hint_word", message}]` for an `own` folder whose path the path gate's hint test matches, else `[]`; the Settings tab's Location section shows the message); a checklist of real checks (manifest, features closed, generated files in sync, tenant plugins, `kb check`) - never a score |
| `PUT /{id}/features` | `{set: {feature: bool}, accept_dependencies, cascade}`: 409 `has_dependants` / `needs_dependencies` / `plugin_missing` carry the lists the UI shows; switching off never deletes a file |
| `POST /{id}/repair` | `{dry_run}`: pending changes with diffs; repair rewrites generated and machine-local files only |
| `POST /{id}/open` | audit `project.opened`; returns the context and a terminal command (`chat_attachment: false` until ADR-2317's spike) |
| `DELETE /{id}` | removes the registry entry ONLY; the directory stays |
| `PUT /settings/storage` | `{default_storage: managed\|root, projects_root?}`: where NEW projects go; moves nothing; audit-first `project.storage_changed` requested/applied; 200 `{default_storage, projects_root, managed_root, outside: [ids]}`; 409 `root_required` ("Choose a folder of your own before switching to it."); a bad root is the rule's 422. `{rekey: true}` starts a new workspace for a copied installation (binding rule (c), `outcome=rekeyed`); 409 `not_foreign` ("This workspace belongs to this installation.") while the workspace is this installation's (ADR-2321 Decisions 8, 16) |
| `PUT /root` | `{path}`: the ALIAS of `set_default_storage("root", path)` - not a second settings writer; response `{projects_root, outside}` as before; audit-first `project.root_changed`, plus `project.storage_changed` (`to: root`) only when `default_storage` flips (REVIEW-0062 N9) |
| `POST /{id}/relocate` | `{location}` ("Locate folder"): re-points the index at a folder that already holds this project's manifest (purpose `relocate`), re-attaches chats, runs repair; audit `project.relocated` requested/applied; 409 `not_missing` while the recorded path still holds the manifest; 422 `wrong_manifest` for a folder holding another project (ADR-2321 Decision 11) |
| `GET /-/browse/places` | the picker's places (`managed`, `root`, `home`, `desktop`, `documents`, `drive`, `recent`, `favourite`; each `{kind, label, path, available, note}`); bounded like a listing (§ Picker) |
| `GET /-/browse?path&purpose[&project_id][&hidden=1][&page]` | one page of a server folder's DIRECTORIES with the location rule's verdict for `path` + `purpose` (§ Picker); 422 `path_rule`, 429 `throttled`, 200 `slow` |
| `PUT /-/browse/favourites` | `{paths}` (CSRF): replaces the favourites (at most 10, each by the input rule; else 422 `path_rule` "At most 10 favourites."); answers `{places}` = the favourite rows |
| `DELETE /-/browse/recent` | (CSRF) "Clear recent places"; answers `{places}` |
| `POST /{id}/move` | `{location}`: same-disk rename only; a rename that fails with `EXDEV` (Windows error 17) answers 409 `cross_device` ("The new place is on another disk. Export the project and import it there, or copy the folder yourself and use Locate folder."), tree and index unchanged, `project.blocked reason_class=cross_device`; moving into/out of the managed root sets `storage` |

**Collection namespace `/projects/-/…` (ADR-2321 Decision 16, REVIEW-0061 M4).** `manifest.RESERVED_IDS` is NOT extended
(an existing project named `import`, `files` or `trash` would turn invalid on load). Collection-level routes of
ADR-2321..2323 (browse, export, import, trash) live under the first segment `-`, which `PROJECT_ID_RE` can never
match. The sub-router `routes/projects.py::collection` is registered BEFORE every `/{project_id}` route; it holds the
folder-browser routes (T-0391) and, last, a catch-all that answers 404 `not_found`, never a project lookup. Projects named `import`, `browse`,
`trash`, `export`, `exports`, `files` keep loading and answer detail, health and move.

**Hint words (ADR-2321 Decision 2, REVIEW-0061 M5).** `core/projects/hints.py` loads `_looks_protected` from
`corvin_operator/voice/hooks/path_gate.py` by file path (`importlib.util.spec_from_file_location`, once) - no copy
of `_PROTECTED_HINTS`; a load failure raises `HintSourceUnavailable`. A NEW folder directly in the managed root
(`create`/`move`/`import`) whose path the gate's test matches is refused 422 `hint_word` ("Commands that name this
folder would be refused by the assistant's safety check; choose another id."); so is every managed create while the
hint source cannot be loaded. An own folder only warns: the plan response carries `location_warnings: ["hint_word"]`
and the sentence "The assistant may refuse some shell commands in a folder whose path contains '<word>'." in `warnings`.

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
`project.registry_removed`, `project.blocked` (a refusal; best effort; its `reason_class` is the class of the
refusal), `project.storage_changed` (WARNING; `to` managed|root, `outcome`
requested|applied|rebound|recovered|rekeyed|error - never a path or a workspace key), `project.relocated` (WARNING;
`project_id`, `outcome`). Allowlist: `project_id`,
`feature`, `to`, `template`, `files` (count), `reason_class`, `outcome`, `tenant_id`, `storage` (managed|own on
`project.plan_created`/`project.created`) - never a path. A `ProjectPathError` answers 422
`{message, reason_class}` with the exception's own class.
`project.feature_files_removed` and `project.video_created` are registered with their tasks.
`project.browse_refused` (WARNING; `reason_class`, `tenant_id` only - never a path or a name): § Picker.

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
the project switched off, the template and where the folder lives (the "Where" row, T-0388). With no project the empty
state explains the folder model ("removing it from this list never deletes a file") and, when the template plugin is
not enabled for the tenant, names that and links to the Marketplace. "New project" (the wizard) and "Add existing
folder" (adopt; any folder the location rule accepts, no longer only inside the projects root) sit in the header. UI text is English and carries
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
on pulls in its requirements first, core is never a choice, an empty folder means the server default. Step 2's own folder is
chosen in the folder picker (§ Picker).

Proof: `tests/unit/projects-wizard.test.ts` (incl. a mutation: dropping the toggled feature fails it, which it did
before the fix), `tests/e2e/projects-wizard.spec.ts` (the served bundle, network stubbed: four steps, gating,
review, a pulled requirement, a refused plan, a conflict). The real create path is proven over HTTP in
`tests/e2e/test_projects_api_e2e.py`; the browser spec never creates a real project.

## Inspector (T-0359)

`/app/projects/:projectId` (a detail route: mounted in `web-next/src/App.tsx`, not PANELS; not in the sidebar).
Built tabs: **Overview** (the server's health checks, headline never "all good" over a red check), **Features**
(switch on/off; a switch-off that would take dependants away asks first and then sends `cascade`; a switch-on that
needs more asks first and then sends `accept_dependencies`; a feature whose tenant plugin is not enabled cannot be
switched on and says why), **Settings** (seven sections since T-0387, see "Settings sections, Where and the default
place" below; Move and Locate folder choose the folder in the folder picker, § Picker). Pure decisions in `pages/projects/inspector-logic.ts` (unit-tested).

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

## Settings sections, Where and the default place (T-0387, T-0388, ADR-2321 Decision 18, CONCEPT-0131 § C)

**Settings tab** (`web-next/src/pages/projects/settings-sections.tsx`; which sections and actions are drawn is the pure
`settingsSections(detail, {workspace})` in `inspector-logic.ts`, unit-tested over the matrix state `ok`/`missing`/`invalid` x
storage `managed`/`own`/builtin). Order and what each action touches:

| Section | Contents | Drawn when |
|---|---|---|
| General | title, renamed in place (`PUT /{id}/title`, only `project.yaml`); for 10 s an **Undo** re-sends the old title; id (read-only, copy), template, role, added | always (rename only for `ok`) |
| Location | storage badge (`Corvin workspace` / `Own folder` / `Projects folder`, `badges.tsx::StorageBadge`, palette tokens only), `display_path` (full path on hover, copy), "Show open command"; **Move…** opens the folder picker (`MoveFlow` in `manage.tsx`, purpose `move`, opens at the folder's parent) -> "New place: <path>" with "Move here" -> "Yes, move the folder" (`POST /{id}/move`; another disk is the server's `cross_device` sentence); `missing`: a box "Folder missing: <reason>" with **Locate folder…** (the picker, purpose `relocate`; Choose sends `POST /{id}/relocate`; the badge and path update without a reload); a `foreign` workspace (`GET /projects` -> `workspace`): "This workspace belongs to another Corvin installation." + "Start a new workspace for this installation" after typing `new workspace` (`PUT /settings/storage {rekey: true}`); an own folder's `location_warnings` sentence | always (Move only `ok`, Locate only `missing`) |
| Protection | `ProtectionSection` (unchanged) | `ok` |
| Use in a chat | `AttachSection` (unchanged) | `ok`; the only section of Corvin itself |
| Maintenance | "<n> checks, <m> failed — <first failed detail>" (`doctorSummary`; doctor notes are not counted) + "Open Overview"; Repair preview -> apply (`POST /{id}/repair`) | `ok`, `invalid` (repair off: needs a readable project.yaml) |
| Share | "Export…" and "Duplicate…" disabled with the tooltip "Available soon" (E-058) | `ok` |
| Danger zone | "Remove from the list — the folder and every file stay." two clicks (`DELETE /{id}`); "Delete project files…" disabled "Available in a later update" for `managed` (E-059), "(not available: own folder)" for `own` | always |

A `missing` or `invalid` project shows these sections under its state card (General, Location, [Maintenance], Danger zone), so
"Locate folder…" and "Remove from the list" are reachable without the tabs.

**List** (`index.tsx`): the header sentence `defaultPlaceSentence()` - "New projects go to the Corvin workspace." or "New
projects go to <projects_root>." - with **Change…**, which opens **Default place for new projects** (`DefaultPlaceDialog` in
`manage.tsx`, replaces the old `RootEditor`): "Corvin workspace (recommended)" with the `managed_root` and "— managed by Corvin
for this workspace", or "A folder of my own" with "Choose a folder…" (the folder picker, purpose `projects_root`); footer "Projects already somewhere else keep working;
nothing is moved."; Save -> `PUT /settings/storage`; a refusal stays in the dialog as the server's sentence. Every card has a
**Where** row (badge + shortened path, full path in `title`; Corvin itself: "Corvin's knowledge base"); a missing card links
"Locate folder…" to its Settings. The **Where** filter (All places / Corvin workspace / Own folder / Projects folder) appears
only when the user's own projects have more than one kind. `Projects folder` is derived in the client: the server reports
`managed`/`own`/`builtin` per entry, and an `own` entry strictly below a set `projects_root` reads `root`
(`encodings.ts::whereKind`, separator-aware, case-insensitive for Windows paths).

**Wizard step 2** (`wizard-dialog.tsx`, `wizard.ts`): an optional **Id** field and **Where**: "Corvin workspace (recommended)",
"Projects folder" (only while one is set) and "Choose a folder…" (a button that opens the folder picker, purpose `create`; the
chosen path is shown with its storage badge; Cancel keeps the wizard's state). The start is the tenant's default place (`initialPlace`). The first two send `storage`, the third only `location`.
The review shows the storage badge and the target path. A refusal about the id (`hint_word`, `id_taken`) brings the wizard back to
step 2 with the sentence under the id field (`idProblem`).

**Chat menu** (`chat-menu.tsx`): each project carries its storage badge next to class and access; no behaviour change.

Proof: unit `tests/unit/projects-encodings.test.ts` (whereLabel/whereKind/whereFilter/presentWhere/defaultPlaceSentence),
`projects-inspector.test.ts` (settingsSections matrix, doctorSummary), `projects-wizard.test.ts` (place, storage, id);
browser on the served bundle with the network stubbed: `tests/e2e/projects-settings.spec.ts` (sections, rename Undo with a fake
clock, Locate folder, two-click remove, typed rekey, 1280/375 x light/dark screenshots without sideways scroll),
`projects-panel.spec.ts`, `projects-wizard.spec.ts`, `projects-chat-menu.spec.ts`, `projects-manage.spec.ts`; server:
`tests/e2e/test_projects_location_e2e.py::WizardStorageChoice`; the running console, nothing stubbed (keep-live, opt-in):
`tests/e2e/projects-settings-live.spec.ts` - creates `p1-live-check` through the wizard in the managed workspace, checks the
sections, rename Undo through the API, remove + re-adopt (the folder stayed), then removes the entry and deletes exactly that one
folder.

## Scope of the Tasks board and the Knowledge Graph (T-0360, switcher 2026-11-09)

Both pages show ONE switcher (`ScopeSelect` in `pages/projects/scope-select.tsx`, placed under the page title,
above every number it governs) with the same entries in the same order: **All knowledge bases** (`all`), **Corvin**
(`bound`, the tenant's own knowledge base), then **every** registered project (`project:<id>`). A project that
cannot be shown is listed DISABLED with its reason (`projectUnavailableReason`: "no knowledge base - switch it on in
Settings", "knowledge base not created yet", "folder missing: ...", "invalid project.yaml", "still being created"),
and a line under the select ("Not available here: ...") links to where it is fixed (the project's Settings tab, or
its page when the folder is missing/invalid). The choice is the URL (`?scope=`), so a reload and a shared link keep
it. Defaults: `all` on Tasks, `bound` on the Knowledge Graph (`DEFAULT_SCOPE`). `both` (the graph's former word for
the joined view) is read as `all`; `project:corvin` is `bound`. An unknown or unavailable project id falls back to
the page default and SAYS so (`scope-note`: 'There is no project "x". Showing ... instead.'). While the project list
is still loading, a `project:<id>` link waits for it instead of showing the default first (`resolveScope` → `pending`).
Pure decisions in `pages/projects/scope.ts` (`tests/unit/projects-scope.test.ts`, 13 tests).

- **Tasks:** `getTaskItems(signal, false, scope)` passes the parameter to `GET /task-tracking/items`; the scope survives
  every change of view (the page rebuilds its query string, so `setQuery` carries it over explicitly). The server
  counts `summary` over the SCOPED list, so the header ("<scope>: N items · M active initiatives") and the KPI tiles
  ("Counts for <scope> ...") never mix scopes. With a project chosen, the bound KB's banners (projection state, human
  queue) are hidden and the project's own projection state is shown instead (`/kb/status` → `projects[<id>]`); the
  Runs section says it is install-wide ("not filtered by knowledge base").
- **Knowledge Graph:** one request per knowledge base (`/graph` or `/graph?project=<id>`), joined by `mergeGraphs`:
  a project's labels are qualified (`alpha/T-0001`), two equal local ids stay separate nodes, relations never cross
  knowledge bases, so the graphs form islands. A node's document is read with its own project
  (`/doc/{key}?project=<id>`). Each answer carries `repo_path` and `graph_state` (`ok`/`built`/`empty`/`missing`/
  `error` + `graph_error`); the stats line shows "<scope> · Repository: <that path>" (or "N knowledge bases"), an
  empty project KB says so with its own path, and a graph that could not be built is named in a banner. Pull/Push/Sync
  are shown only for Corvin (they act on the bound checkout). The project ids are part of the query key only for the
  joined view, which waits for the project list (elsewhere their arrival discarded a loaded graph: "0 nodes" for a moment).
- **Project graphs are built by the console** (`kb_projection.ensure_project_graph`/`index_project_graph`): on a read
  whose `kb/graph` is missing or older than the KB's sources (`graph_stale`, bounded scan), and after a projector pass
  that exported a changed project KB — always with the bound engine, off the event loop (`asyncio.to_thread`), one
  lock per KB folder (`graph_lock`). A folder whose git does not ignore `kb/graph/` is refused with that reason.
- **Card moves** still use the existing board path (`kbTransition` → `/kb-transition`); the server resolves the
  project from the card's label (proven over HTTP in `tests/e2e/test_project_kb_writes_e2e.py`).

Proof: `tests/e2e/projects-scope.spec.ts` (6 browser tests on the served bundle, endpoints stubbed),
`tests/e2e/test_project_graph_scope_e2e.py` (2, real routes: on-demand build, staleness, empty KB, refused folder,
projector build, scoped summary), `tests/e2e/projects-scope-live.spec.ts` (keep-live: both pages on the running
console, both themes, 1280/375 px, screenshots in `/tmp/scope-switch`).

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
- **Doctor, location checks (ADR-2321 Decision 17, T-0386).** `storage_marker` (managed projects: `workspace.json`
  names this tenant, this workspace and this installation; a missing marker says "Repair writes the workspace marker",
  and `POST /{id}/repair` then writes exactly that one file, audited `project.repaired`), `path_under_base` (the
  folder lies where its `storage` says; a stored projects root the current rule would refuse is reported here),
  `no_engine_copy` (no `ENGINE.json` - bounded walk, links not followed - and no `docs/scripts/kb.py` inside the
  project; not repairable: "remove ENGINE.json from the project; the tenant engine is used"), `import_leftovers` (no
  `<id>.importing-*` sibling), `pending_import` (an entry marked `pending: "import"` while no apply holds the project
  lock; its repair action ships with the import, T-0412), `hint_source` (`core/projects/hints.py` can load the path
  gate's hint test), and the NOTE `path_gate_hint` (`ok: true, note: true`: the path contains a hint word). Each
  seeded defect is found by exactly its check and a clean managed project has none
  (`DoctorCalibration::test_the_location_checks_of_adr_2321_find_each_seeded_defect`).
- **Adopt** (`POST /projects/adopt`, body `location`, optional `title`, `project_id`): any folder the location rule
  allows (ADR-2321 Decision 4; until then: inside the projects root only).
  A folder with a `project.yaml` is registered as is. A folder without one gets exactly that file - docs mode and the `kb`
  feature are read from what is on disk, nothing else is written, `.git` included. Proven on a copy of the real
  `LLM_lie_detector` repository (every file hashed before and after; only `project.yaml` is new).
- **Rename** (`PUT /projects/{id}/title`): the title in `project.yaml` only; the id (registry key, KB scope label) never changes.
- **Move** (`POST /projects/{id}/move`, body `location`): a same-disk rename to an absent or empty place the
  location rule allows (purpose `move`); never into itself, never over content, never across disks (409
  `cross_device`). The index follows, then repair regenerates the machine-local files
  (they hold absolute paths); the response carries `repaired_files`. Audit: `project.renamed`, `project.moved`.
- UI: "Add existing folder" on the list (the folder chosen in the picker), Rename and Move in the Settings tab (Move: the picker,
  then a second confirming click).
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


## Explorer: a live view of how the project is worked on (T-0374..T-0379, CONCEPT-0128, ADR-2320, REVIEW-0060)

The project menu has one more entry, **Explorer**, and a live dot on its button while an agent works on the project (the dot polls the cheap snapshot
every 8 s; it never opens a stream). The panel shows, top to bottom: a status line in words (Live, Reconnecting, paused, could not connect), **Now**
(one card per chat working on the project: current tool, elapsed time, tool count; finished chats collapse to a line), **Tasks in progress** (the project's
board items), **Activity** (newest first, All / Tools / Files, repeated tool rows collapse to "x N", ids link to Tasks and the Knowledge Graph).

- **Channel** `core/projects/activity.py`: in memory, per (tenant, project), 300 events, lost on restart (`epoch` in every snapshot and in every event id
  `<epoch>:<seq>` makes a restart visible; the panel says so). A closed field set: a tool event carries `detail` derived by ONE function
  (`describe_tool`: Bash -> the program name, file tools -> the file NAME, WebFetch -> the host, everything else nothing) - never an input, a command body,
  a URL query, a search text, a prompt, an answer or a file's content, even if a producer hands a raw dict to `publish`.
- **Producers**: `chat_runtime.stream_turn` starts a `TurnTracker` for a chat attached to a project, the tool hook sits in `_stream_turn_impl` at the
  `tool_use` block (where `parent_tool_use_id` is visible -> `sub`), the finish is published exactly once in the wrapper's `finally` (`done`, `error` or
  `closed`); no error reaches the turn. A per-project git watcher (`working_changes_ex`, no line counting, 8 s git timeout, back-off, silent first look,
  baseline kept across restarts) runs only while a stream is open.
- **API** `GET /projects/{id}/activity[?after=<epoch>:<seq>]` (snapshot: agents, backlog, `reset`, `gap`, `oldest_seq`, `watcher`; starts no watcher) and
  `GET /projects/{id}/activity/stream` (SSE: `retry`, `snapshot`, `activity`, `end`; `Last-Event-ID` resumes; 429 above 8 streams per tenant, 404 for another tenant).
- **A stream lives 10 s** (`STREAM_LIFETIME_S`) and the browser reconnects: uvicorn force-cancels an in-flight response at its 15 s graceful timeout and never tells the app.
  Measured on an isolated instance with the production timeout: one open explorer stream -> the server stops in 6.3 s, 0 forced cancels; one open `settings/stream`
  (30 min lifetime, T-0380) -> 15.3 s and a forced cancel. Every restart of the live console shows that forced cancel today; the explorer is not the cause.
- **Watcher and a busy host**: optional work yields to `loop_health.host_saturated()` - EXCEPT while an agent works on the project (found live: starting a real turn
  stalls the loop 2 s, which marked the host saturated for 120 s and paused file watching exactly while the user watched). Idle + saturated -> paused, the panel says so.
  The watcher also keeps looking for `WATCH_GRACE_S` (20 s) after the last turn/tool event, saturated or not: the last write of a turn lands just before the turn ends,
  the host is busiest then, and without the grace window the file appeared only with the NEXT turn (measured live 2026-11-09, one run in five; 6/6 after the fix).
- **The assistant is told where the project is** (`chat_runtime._attached_project_block`): the attached project is NOT its working directory; files for it belong in
  the project folder, by absolute path. Without that sentence "create a file in the project" landed in the chat's own folder (found by the live explorer test; the explorer
  showed the tool call and, correctly, no file change).
- **The panel's agents follow the events** (`activity-logic.ts::agentsAfter`), a snapshot replaces them. Waiting for the next snapshot made the Working card appear up to 15 s late.
- **Proof**: `tests/e2e/test_projects_activity_e2e.py` (real HTTP + real git; watcher under load; privacy markers; mutation probes; a live half with the real CLI - the turn's tool
  messages equal the explorer's rows in order and no marker leaks), `tests/e2e/projects-explorer.spec.ts` (served bundle, fake EventSource for deterministic states),
  `tests/e2e/projects-explorer-live.spec.ts` (nothing stubbed: a real turn typed in the browser, the real stream; Write frame -> visible row, Enter -> Working card under 5 s),
  unit tests `projects-activity`. Note: Starlette's `TestClient` buffers a whole response, so receipt latency is measured over a real socket (`httpx` against the running console) instead.
- **Not covered, stated**: chats of other surfaces, ACS/worker runs (not linked to projects), persistence, per-user filtering, steering from the explorer.


## Corvin itself as a built-in project (CONCEPT-0129)

`core/projects/builtin.py`: `id: corvin` (reserved) stands for the tenant's OWN knowledge base (`kb_projection.kb_repo(tenant)`: the operator's bound repository, else
`<tenant_home>/knowledge`). Synthesised on every read - no `project.yaml`, no registry entry. It is first in `GET /projects` (`builtin: true`), has a real
`kb check` in its health, and its structure, change view, explorer, board scope (`project:corvin` = the tenant's own board) and graph scope work like any project's.
Every mutation (remove, rename, move, settings, features, repair, new video) answers 409 `builtin`; class `internal`, assistant access read-only; attaching it to a chat
uses the same gates, deny rules and per-turn judgement. A tenant without a knowledge base gets state `missing` with the reason - never another tenant's repository.
UI: a "Built in" card at the top of the Projects page (the "no project of your own yet" hint stays), an inspector with Overview/Structure/Docs/Knowledge/Tasks and a
Settings tab that only offers attaching it to a chat, an entry in the chat menu. Proof: `tests/e2e/test_projects_builtin_e2e.py`, `web-next/tests/e2e/projects-builtin.spec.ts`.
