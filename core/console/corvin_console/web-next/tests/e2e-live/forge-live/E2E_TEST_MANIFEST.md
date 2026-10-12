# E2E_TEST_MANIFEST — Forge live suite

Playwright suite that drives the **real console** through all four Forge generators the way a
user does right after installing CorvinOS, and keeps the artifacts it forges.
Config: `../../../playwright.forge-live.config.ts` · Helpers: `live.ts`.

Suite size (2026-10-12): **86 tests** — 10: 36 · 15: 22 · 16: 5 · 17: 1 · 18: 5 · 20: 9 · 30: 8.
Measured on 2026-10-12 against the live console :8765: specs 10 + 15 + 16 together (without the 12-minute refine) **62 passed** in
2.4 min; the real-engine tests passed in their own runs — the refine in spec 16 (11.8 min), spec 17 (23 s), and 20/30 (earlier, see
the run ids below). Real-engine runs use the Claude Code CLI (subscription), no API key. Specs 15–18 belong to the composer / run feed / run history work; spec 18 passed 5/5 on its own run against the live console.

## 1. The e2e_test_* artifacts (kept on purpose)

| Forge | Artifact | Forged via | Run id | Proven |
|---|---|---|---|---|
| Tool | `assistant.e2e_test_tool` | Generator → Tool panel | `run-83b9ec90caac` | sandbox-tested, registered in `/forge/tools`, exported + imported on a fresh install |
| Skill | `assistant.e2e_test_skill` (depends on the tool above) | Generator → Skill panel | `run-c1ca8aefa2fc` | registered, bootstrap-graded (`injectable: true`), listed in the panel |
| Plugin | `community.e2e-test-plugin` (dir `community_e2e_test_plugin`) | Generator → Plugin panel (+ panel request) | `run-cce81ed75154` | staged with provenance, `installed:false`, `signed:false`, `origin_on_install: community`, code contains `count_words` |
| Layer | `e2e_test_layer@1.0.0` | Generator → Layer panel (PLAN → preview → Create) | — | quality gate `tests/layer_forge/test_schema.py` PASS (30 passed), enforcement PASS, status Accepted, exported + imported on a fresh install |
| Layer | `e2e_test_layer_runs@1.0.0` | Layer panel by keyboard (Enter → PLAN, Create) — spec 17 | plan `run-8d60937a5233`, create `run-2f3fd79057db` | each operation is its own run with its phases; the real quality gate and the review verdict are events; live progress was visible while PLAN ran |
| Plugin | `community.e2e-test-spike-plugin` | measurement run for the event log (T-0146) | — | staged; 36 events / 9 276 B |
| Skill | `assistant.e2e_test_spike_skill` | measurement run (T-0146), 429 s | — | registered; 29 events / 7 963 B |
| Tool | *(none)* `assistant.e2e_test_spike_tool` | measurement run | `run-3ea7323b1a9d` | **not registered**: the security reviewer confirmed a finding — a genuine refusal, kept as the failed-run example in spec 16 |

Idempotent: an existing artifact is reused; `FORGE_LIVE_REGEN=1` deletes and re-forges.
**Orphan, not an e2e_test_ artifact:** skill `assistant.data_change_review` came from the first run, before
Skill Forge accepted digits in names (see 4.1). Safe to delete.

## 2. Preconditions

- Live console on `FORGE_LIVE_URL` (default `http://127.0.0.1:8765`), local-login works, licence tier allows Forge.
- Claude Code CLI signed in (specs 20 only; 10 and 30 spend nothing).
- Spec 30 starts its own empty install (`tests/e2e/forge-system/harness/isolated_forge_backend.py`,
  `FORGE_E2E_SEED=0`, own `CORVIN_HOME` and audit chain); `FORGE_LIVE_SKIP_B=1` skips it (specs 10/20 only).
- Run order: `10` (seconds) → `20` (≈15 min on first forge, seconds when reusing) → `30` (needs 20's artifacts).

```bash
cd core/console/corvin_console/web-next
npx playwright test -c playwright.forge-live.config.ts 10     # robustness, no LLM
npx playwright test -c playwright.forge-live.config.ts 20     # real forging
npx playwright test -c playwright.forge-live.config.ts 30     # empty-install delivery
```

## 3. Tests

### 10-robustness.spec.ts (36) — no engine
| Group | What it proves |
|---|---|
| reachable (4+3) | deep link `?tab=generator&sub=` opens each of the four; one row of sub-tabs, `?sub=` stays in sync; legacy `?tab=` links land right; unknown `sub` falls back instead of a blank page |
| tool + plugin UI (8 each) | <10 chars refused inline and nothing sent; blank input keeps submit disabled; gate refusal 403 shown; busy engine 429 shown; FAILED run shows reason + engine and frees the form; run unknown to server (404) is reported; run survives hard reload and tab switch; transient poll 503 does not end the run |
| request shape (1) | only the plugin request carries `panel_request` |
| skill UI (2) | generate disabled until text, sends `async:true` + CSRF; failed run shows error + phase stepper |
| layer UI (3) | Plan needs id + intent; failed PLAN (422) / audit outage (503) surfaced; Discard creates nothing, duplicate Create (409) shows the reason |
| live API (7) | anonymous → 401 on every route; missing/wrong CSRF → 403; schema (short, extra field, wrong type, unknown kind, panel on tool); unknown/foreign/malformed run id → 404; plugin path traversal; layer bad ids / malformed manifests write nothing; illegal transitions 4xx |

### 20-create-live.spec.ts (9) — real engine
| Test | Interaction |
|---|---|
| tool: forge CSV-diff tool from the panel | types request, submits, polls run to `success` |
| tool: live registry member | `/forge/tools` lists it |
| skill: forge skill that depends on the tool | request names the tool; run `success`, `injectable` |
| skill: listed + visible | `/forge/skills` and the panel filter show it |
| plugin: forge with a panel | exact display name requested (id is derived from it); staged, not installed |
| plugin: provenance + content | detail route returns files; **`count_words` must really be implemented** ("success" alone is not usable) |
| layer: PLAN (LLM) → preview → Create | real PLAN, manifest preview, Create runs gates + enforcement; UI switches to the Layers tab and lists the layer |
| layer: registry + promotion | status accepted/deployed |
| all four side by side | tool, skill, layer, plugin exist together |

### 15-composer.spec.ts (22) — the shared composer, no engine
Per generator (skill, tool, plugin): Enter sends a valid request once with the exact text; Shift+Enter inserts a newline and sends
nothing; Enter on a too-short request shows the inline refusal; Enter on blank input sends nothing; the counter follows the text and
an over-long request cannot be sent; hold-Space dictates (stubbed speech recognition) without sending and a tap types a space.
Plus: switching the four sub-tabs never accumulates Space listeners (with a positive control that the count is > 0), and the layer
composer (Enter runs PLAN only, never creates; no id sends nothing; dictation).

### 16-run-feed.spec.ts (5) — the Production feed
One REAL test against a real run's real event log, a live run that fills in and stops saying "live", a reload that re-attaches, a run
with no log, a capped log with findings grouped by dimension and an error line; and the **REAL refine**: the e2e_test_skill is refined
from the UI by keyboard (11.8 min) and the feed shows the diff `previous version → iteration` with an added line.

### 17-layer-runs.spec.ts (1) — Layer Forge operations are runs, real engine
A keyboard PLAN shows the feed while the request is going, Create runs the real gate, plan and create are two runs with their phases,
the gate and review verdicts are events, the layer ends accepted.

### 18-runs-history.spec.ts (5) — the Runs tab (history across all four Forges)
REAL: the table equals the live API's runs (count, newest first, all four kinds); the kind/status filters narrow it to exactly what the API
says and a combination with no match says so; a row opens the same Production feed with the real event count and an artifact links to
where it lives. At the browser boundary only: Retry is offered only where the API says `retryable`, sends the CSRF token and lands on
the Tool generator; an unreachable API says "not available on this build" and a missing model/duration reads `unknown` / `—`.

### 30-fresh-install.spec.ts (8) — empty install B
| Test | Result |
|---|---|
| B is empty | no skills/tools/layers |
| KNOWN GAP skill | UI-forged skill is **not** in the bundle inventory (see 4.2) |
| KNOWN GAP plugin | console refuses plugin bundle export with a CLI pointer (see 4.3) |
| export tool + layer as one bundle | ZIP from the live install |
| B validates, writes nothing | valid, artifacts = layer + tool, B still empty |
| B imports | layer `forged`; tool `quarantined` (unverified origin), quarantine count 1, tool not usable yet |
| operator accepts the tool | tool registered on B, quarantine empty |
| second import | no duplicate |

## 4. Product defects this suite found and fixed

1. **Skill names could never contain digits** — `SKILL_NAME_RE` was `^[a-z_]+$` and the normaliser silently
   stripped them (`e2e_test_skill` → `ee_test_skill`, `sha256_check` → `sha_check`). Now `^[a-z][a-z0-9_]*$`
   (`corvin_operator/skill_creator/skill_creator.py`, unit test added).
2. **Layer Forge was unusable on a subscription-only install** — PLAN and REVIEW built `anthropic.Anthropic()`
   unconditionally ("Could not resolve authentication method"). New `core/orchestration/layer_forge/llm_client.py`:
   API key if set, else the configured engine. Tests are pinned off the real engine
   (placeholder key + stubbed REVIEW), 179 Layer Forge tests green.
3. **Layer PLAN output was rejected by CREATE.** The first real PLAN produced a manifest that CREATE refused
   with "quality_gate needs gate_id + test_path" (the draft itself was not kept, so which field was
   misspelled is not recorded). The PLAN prompt named the constraints but never the exact field names;
   it now states the exact shapes with an example, and the next real PLAN created `e2e_test_layer` cleanly
   (one run — not a proof that the model never drifts again).
5. **The synchronous skill path left an untruthful record** (`async: false`: "success, 5 %, Initializing…, engine unknown", or
   "failed" pinned to the last phase with no error and no run id) — now the same work function as the async path.
6. **Two latent run-store defects** found by the adversarial reviews of the run ADR: a second live host re-spawned or failed a run another
   host still owned, and `get_run` resumed a run before checking the caller may see it. Both fixed with route-level tests.
7. **A failed run showed no tests and no findings** in its log; they are now emitted from the failure result too.
8. **The run history filled with request text** (found by looking at the new Runs tab): the API negative tests posted malformed layer
   manifests and each became a failed run whose "artifact" was the hostile id (`../x@1.0.0`, 300×`x`). A manifest that names no artifact (no
   well-formed id and semver) now records **no run and no artifact reference**, like a promote of an unknown definition.
4. Layer manifest `id` had no length cap (ENAMETOOLONG → 500); the Skill panel cleared the request after a
   failed run (minutes of work lost). Both fixed earlier in this effort (see git history).

## 5. Known issues / not covered (named, not hidden)

1. **Skill → bundle gap.** Bundle export reads `skills_gen/<id>/skill.json`; Skill Forge writes `SKILL.md` into
   the skill registry. A skill forged in the UI cannot be shipped in a bundle. Pinned by a test that fails the
   day it is closed.
2. **Plugin → bundle is CLI-only** (`scripts/forge_bundle_cli.py export --plugin ID@VER:PATH`); the console says so.
3. **Plugin quality.** The review scores the forged plugin `quality: 0` and lists confirmed defects (scaffold
   `plugin.py`, panel re-implements the logic in JS, tokenizer keeps apostrophes). The suite asserts the
   requested function exists, not that the review is clean.
4. **Skill review** also scores `quality: 0` with confirmed findings; the skill is still injectable.
5. **Tools have no "previous version" and cannot be refined**: the tool contract refuses a second tool of an existing name, so the diff
   "against the previous version" exists only for a skill refine.
6. Names cannot be forced: the skill/layer names are honoured only because the request states them exactly;
   the plugin id is derived from the display name (`community.<slug>`, `_` becomes `-`).
6. A skill forge run takes 15+ minutes (5 LDD iterations + 3-dimension review); the suite allows 30.
7. Skipped by design: Windows/macOS, multi-tenant isolation (covered by `tests/e2e/forge-system`), concurrency
   races (same), visual regression.

## 6. Coverage map

| Concern | 10 | 20 | 30 |
|---|---|---|---|
| Tool forge | ✔ UI + API negatives | ✔ real | ✔ ship + quarantine |
| Skill forge | ✔ | ✔ real | gap pinned |
| Plugin forge | ✔ | ✔ real + content | gap pinned |
| Layer forge | ✔ | ✔ real PLAN→accepted | ✔ ship |
| Fresh-install usability | — | — | ✔ |
| Cross-forge interplay | — | skill depends on tool | tool + layer in one bundle |

Related: `tests/e2e/forge-system/` (isolated, seeded suite), `docs/claude-ref/layer-forge.md`.

- `19-intake.spec.ts` (3 tests): Tool Forge attachments - redacted table admitted and its id sent, secret file refused and blocks Send, no attach button on Skill/Plugin. Measured: 3 passed (27 s). Names use `e2e_test_`.
- `21-intake-live.spec.ts` (1 test, real engine, ~2.5 min): table with an e-mail column attached to the Tool Forge -> `assistant.e2e_test_intake_tool` registered, 4/4 sandbox tests, the engine wrote its cases with the `<email>` placeholder, no raw address in the run record or its events. Reused unless FORGE_LIVE_REGEN=1 (a regen run needs the old tool removed first: the name must be unique).
- `22-intake-document-live.spec.ts` (1 test, real engine, ~7 min): markdown runbook with an e-mail address attached to the Skill Forge -> `assistant.e2e_test_intake_skill` registered under the exact requested name, injectable, its SKILL.md carries the document marker term `NIGHTLY-REINDEX` and no raw address; run record and events free of the address. Reused unless FORGE_LIVE_REGEN=1. `19-intake.spec.ts` now has 3 tests incl. a Skill-Forge document + secret refusal (3 passed, 45 s).
- `23-intake-layer-live.spec.ts` (1 test, real engine, PLAN only): policy document attached to the Layer Forge, Enter plans, manifest preview shows, no raw address sent or returned. `19-intake.spec.ts` + `23`: 4 passed (1.0 min). PLAN creates nothing, so no new e2e_test_ layer artifact (the existing `e2e_test_layer@1.0.0` stays).
