# E2E_TEST_MANIFEST — Forge live suite

Playwright suite that drives the **real console** through all four Forge generators the way a
user does right after installing CorvinOS, and keeps the artifacts it forges.
Config: `../../../playwright.forge-live.config.ts` · Helpers: `live.ts`.

Last full run (2026-10-11, live console :8765 + isolated empty install on :8798): **53 passed**
(10: 36 · 20: 9 · 30: 8). Real-engine runs use the Claude Code CLI (subscription), no API key.

## 1. The e2e_test_* artifacts (kept on purpose)

| Forge | Artifact | Forged via | Run id | Proven |
|---|---|---|---|---|
| Tool | `assistant.e2e_test_tool` | Generator → Tool panel | `run-83b9ec90caac` | sandbox-tested, registered in `/forge/tools`, exported + imported on a fresh install |
| Skill | `assistant.e2e_test_skill` (depends on the tool above) | Generator → Skill panel | `run-c1ca8aefa2fc` | registered, bootstrap-graded (`injectable: true`), listed in the panel |
| Plugin | `community.e2e-test-plugin` (dir `community_e2e_test_plugin`) | Generator → Plugin panel (+ panel request) | `run-cce81ed75154` | staged with provenance, `installed:false`, `signed:false`, `origin_on_install: community`, code contains `count_words` |
| Layer | `e2e_test_layer@1.0.0` | Generator → Layer panel (PLAN → preview → Create) | — | quality gate `tests/layer_forge/test_schema.py` PASS (30 passed), enforcement PASS, status Accepted, exported + imported on a fresh install |

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
5. Names cannot be forced: the skill/layer names are honoured only because the request states them exactly;
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
