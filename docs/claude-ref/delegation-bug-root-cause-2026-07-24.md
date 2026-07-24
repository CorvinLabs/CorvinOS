# Web-Chat Delegation (ADR-0114) — Root Cause of Broken Task Propagation

Status: **FIXED + VERIFIED E2E** (2026-07-24, same day as this investigation).
The single-line patch in the "Suggested fix direction" section below was
applied to `chat_runtime.py`, the regression assertion was added to
`test_delegation_spec_is_valid_awp`, and the full data flow (spec construction
→ `RunContext.state` → manager-prompt render → worker task-field extraction)
was re-verified. The "Fix verification" section documented that flow via
manual reproduction of `acs_runtime.py`'s own construction (no ACS run was
actually executed). That gap is closed by the **"Real E2E verification"**
section further below: a new test,
`core/console/tests/test_web_delegation.py::test_delegation_spec_e2e_worker_receives_real_task_not_placeholder`,
drives the actual `acs_runtime.ACSRuntime.run()` delegation loop (manager
loop → `_dispatch_workers` → `_build_worker_prompt`) with only the two LLM
subprocess boundaries faked, and captures the literal prompt string a worker
subprocess would have been launched with. The narrative below is left
as-authored (originally "investigation only") to preserve the historical
root-cause record.

## TL;DR

`_build_delegation_spec()` in `core/console/corvin_console/chat_runtime.py`
(line 2834) builds the AWP `delegation_loop` spec for every web-chat ACS
delegation run. It correctly threads the user's real task text into
`workflow.description`, but **hardcodes `state.initial.task` to the literal
placeholder string `"web-chat delegated turn (ADR-0114)"` instead of the
`task` argument it was passed**:

```python
def _build_delegation_spec(task: str, budget: dict) -> dict:
    """Wrap a chat task into a minimal AWP delegation_loop workflow."""
    return {
        "awp": "1.0.0",
        "workflow": {
            "name": "web-chat-delegation",
            "description": task,                                             # ✓ real task
            "version": "1.0.0",
        },
        "orchestration": {
            "engine": "delegation_loop",
            "delegation_loop": {"budget": dict(budget)},
        },
        "state": {"initial": {"task": "web-chat delegated turn (ADR-0114)"}}, # ✗ hardcoded placeholder — BUG
    }
```

This means every web-chat ACS `delegation_loop` run (mechanism 2 in
`docs/claude-ref/delegation-routing.md`) seeds its shared workflow **state**
with a meaningless constant instead of the actual work item, from the very
first manager iteration onward.

## Affected files / lines

- `core/console/corvin_console/chat_runtime.py:2834-2848`
  (`_build_delegation_spec`) — the defect itself.
- `core/console/corvin_console/chat_runtime.py:4319` (and the duplicate
  call site at the quota-fallback branch, `chat_runtime.py:4245`-ish —
  grep `_build_delegation_spec(task_text` for both) — every caller passes
  the real `task_text`, so the bug is entirely inside the builder, not at
  the call sites.
- `operator/bridges/shared/acs_runtime.py:2946` — `initial_state =
  dict(spec.get("state", {}).get("initial") or {})` copies the polluted
  dict into `RunContext.state` (`ctx.state`), so the placeholder isn't
  confined to iteration 0.
- `operator/bridges/shared/acs_runtime.py:878-880` — `CURRENT STATE:` is
  rendered into the **manager prompt on every single iteration** of the
  delegation loop from `ctx.state`, meaning the manager LLM sees
  `{"task": "web-chat delegated turn (ADR-0114)"}` as the run's "state"
  block for the whole run, not just the first turn.
- `operator/bridges/shared/acs_runtime.py:893-894` — `INITIAL STATE:` is
  additionally rendered (iteration 0 only) directly from
  `ctx.workflow_spec["state"]["initial"]`, doubling the misleading signal
  on the very first manager decision.
- `core/console/tests/test_web_delegation.py:348-356`
  (`test_delegation_spec_is_valid_awp`) — the existing unit test for
  `_build_delegation_spec` only asserts
  `spec["workflow"]["description"] == "do the thing"`; it never asserts
  anything about `spec["state"]["initial"]`, which is why this shipped
  and stayed unnoticed.

## Why this is the actual delegation-path defect

`DESCRIPTION: {description}` (from `workflow.description`, correctly the
real task) IS present in every manager prompt
(`acs_runtime.py:_build_manager_prompt`, line 860), so the manager LLM is
not left with *zero* signal about the real task — this is not a total
black-box failure. But:

1. The **state** channel — the part of the prompt explicitly labelled
   `CURRENT STATE` / `INITIAL STATE`, which is the schema-shaped, structured
   view of "what this run is working on" that workers and later iterations
   key off of (`initial_state.update(inputs)` at `acs_runtime.py:2947`
   merges caller inputs into the very same dict) — carries a **decoy**
   value under the key `task`. A manager LLM constructing step objects for
   workers is exactly as likely to copy `state["task"]` verbatim into a
   step's `task`/`instructions` field (the convention workers themselves
   read from, `acs_runtime.py:2513`:
   `st.get("instructions") or st.get("task") or st.get("description")`)
   as it is to re-derive the task from the free-text `DESCRIPTION` line.
   When that happens, a worker receives literally the string
   `"web-chat delegated turn (ADR-0114)"` as its task instead of the
   user's real request — a silent misrouting: the run completes
   "successfully" (no exception, no gate trip) but does the wrong (or a
   generic/empty) thing.
2. Every iteration re-renders `CURRENT STATE` from `ctx.state`, so this
   pollution is not a one-turn artifact — it persists for the life of the
   run unless a later worker's own state update happens to overwrite the
   `task` key.
3. This reproduces deterministically and needs no live LLM call, network
   access, or race condition — it's a pure data-construction bug, 100%
   reproducible from the function's return value alone (see below).

## Reproduction steps

```bash
cd /home/shumway/projects/CorvinOS
uv run python -c "
import sys
sys.path.insert(0, 'core/console')
from corvin_console import chat_runtime as cr
spec = cr._build_delegation_spec(
    'Analysiere die Verkaufszahlen aus drei Quellen und vergleiche sie',
    cr._DELEGATION_BUDGET_DEFAULTS,
)
print('workflow.description :', spec['workflow']['description'])
print('state.initial.task   :', spec['state']['initial']['task'])
assert spec['state']['initial']['task'] != spec['workflow']['description']
print('BUG CONFIRMED: state.initial.task ignores the real task text')
"
```

Output (observed):

```
workflow.description : Analysiere die Verkaufszahlen aus drei Quellen und vergleiche sie
state.initial.task   : web-chat delegated turn (ADR-0114)
BUG CONFIRMED: state.initial.task ignores the real task text
```

This is exactly the constant embedded in the source — no test in the
existing suite (`core/console/tests/test_web_delegation.py`,
`test_delegation_spec_is_valid_awp`, `test_delegation_spec_passes_acs_validator`)
catches it because none of them assert on `spec["state"]["initial"]`
content, only on `spec["workflow"]["description"]` and AWP-validator
structural validity (which does not know or care what the state values
*mean*).

## What is NOT the cause (ruled out during this investigation)

The current uncommitted working-tree diff (`git status` /
`git diff --stat`: `chat_runtime.py`, `web-next/src/lib/{api.ts,
chat-registry.ts}`, `web-next/src/pages/chat.tsx`, the two associated test
files, `docs/claude-ref/{delegation-routing.md,layer-engines.md}`,
`operator/orchestration/tde/tde_engine.py`,
`tests/test_tde_engine_summarize_honesty.py`, and the new
`docs/claude-ref/tde-graph-concept.md`) is **unrelated, complete, and
fully tested** feature work for the ADR-0216 TDE inline chat badge
(quota/task_type/complexity fields on `TdeProgress`). It does not touch
`_build_delegation_spec` or the ACS `delegation_loop` path at all —
it is scoped entirely to the opt-in `/use-engine tiered_delegation` (TDE)
path, a structurally separate mechanism from ADR-0114's ACS
`delegation_loop`. Verified all green, no reproduction of any failure
there:

- `uv run python -m pytest tests/test_tde_engine_summarize_honesty.py -v`
  → 9/9 passed
- `uv run python -m pytest core/console/tests/test_web_delegation.py
  core/console/tests/test_adr0213_context_sync.py -v` → 80/80 passed
- `npx vitest run tests/integration/chat/chat-page.test.tsx
  tests/unit/lib/chat-registry.test.ts` (in `web-next/`) → 87/87 passed
- `npx tsc --noEmit -p .` (in `web-next/`) → clean, no errors
- `docs/claude-ref/delegation-routing.md` / `layer-engines.md` diffs are
  in sync with the code changes (docs-as-definition-of-done satisfied for
  that WIP slice)

So the diff in the working tree is not the source of a delegation
failure; the `_build_delegation_spec` placeholder bug is a
**pre-existing, independent defect** in the ADR-0114 web-chat ACS
delegation path that has been present since the function was introduced
(confirmed via `git log -p` — the placeholder string was added in the
same commit that introduced the function itself, i.e. this was never
correct, not a regression).

## Suggested fix direction (not applied — analysis only per task scope)

Replace the hardcoded literal with the actual `task` parameter:

```python
"state": {"initial": {"task": task}},
```

and add a regression assertion to
`test_delegation_spec_is_valid_awp` (or a new dedicated test) asserting
`spec["state"]["initial"]["task"] == task` for a non-trivial task string,
so this class of "builder ignores its own parameter" defect can't silently
reappear. Out of scope for this investigation per the task instructions
(analysis + documentation only, no fixes).

## Fix verification (2026-07-24, e2e_verify_delegation_fix subtask)

The fix above was applied verbatim (`"state": {"initial": {"task": task}}`,
`chat_runtime.py:2847`) and the regression assertion was added at
`test_web_delegation.py:352-353`. End-to-end verification, not just the
isolated spec-builder check:

1. **Spec construction** — `_build_delegation_spec(REAL_TASK, ...)` now
   yields `spec["state"]["initial"]["task"] == spec["workflow"]["description"]
   == REAL_TASK`.
2. **`RunContext.state` propagation** — reproduced `acs_runtime.py`'s own
   construction (`initial_state = dict(spec.get("state", {}).get("initial")
   or {})`, then `RunContext(..., state=initial_state)`, mirroring
   `acs_runtime.py:2946`/`2983`) and called the real `_build_manager_prompt(ctx)`.
   Both the `CURRENT STATE:` block (rendered every iteration, line 881) and
   the `INITIAL STATE:` block (iteration 0 only, line 895) now contain
   `REAL_TASK` and **not** the placeholder string.
3. **Worker task-field extraction** — replicated the precedence read at
   `acs_runtime.py:2513` (`st.get("instructions") or st.get("task") or
   st.get("description")`) against a subtask dict built from `ctx.state`,
   confirming a worker constructed this way receives `REAL_TASK` verbatim.
4. **No remaining hardcoded placeholder** — `grep -rn "web-chat delegated
   turn (ADR-0114)"` across the repo returns only this document (historical
   record) and a code comment in `test_web_delegation.py` naming the
   *historical* placeholder for context; zero live code paths still
   construct it.
5. **Test suites** — all 49 test files that import `acs_runtime` and/or
   `chat_runtime` were run (730 tests). 6 failures found, all confirmed
   **pre-existing and unrelated** to this fix (reproduced identically with
   the fix reverted via `git stash`): 2 in `test_acs_runtime.py` are a
   cross-file test-order pollution (an L44 house-rules classifier state
   leak from an earlier-running `core/console/tests` file, not from
   `acs_runtime.py` itself — both tests pass standalone and alongside every
   other `acs_runtime`-importing test file); 3 in `operator/voice/scripts/
   test_summarize.py` are a pre-existing voice-summarize language-directive
   bug (fails identically in isolation, unrelated to delegation); 1 in
   `test_engine_span_coverage.py` is a pre-existing static-scan finding
   (`patch_generator.py` missing an `engine.span` emission, unrelated to
   delegation). `test_web_delegation.py` and `test_adr0213_context_sync.py`
   (the two suites named explicitly in the verification task) are fully
   green (80/80).
6. **`docs/claude-ref/delegation-routing.md`** was checked against the fix:
   it documents mechanism *selection* (which of the 9 mechanisms handles a
   task) and never described `_build_delegation_spec`'s internal
   `state.initial` payload construction, so it made no claim contradicted
   by the bug or the fix — no update needed there. Its already-present diff
   in this working tree is unrelated ADR-0216 TDE-badge documentation.

## Real E2E verification (2026-07-24, real_e2e_delegation_run_and_doc_gate subtask)

The section above ("Fix verification") manually reproduced `acs_runtime.py`'s
internal construction (built a `RunContext` by hand, called
`_build_manager_prompt` directly) rather than running the actual
`ACSRuntime.run()` delegation loop — a real gap: it proved the mechanism
*would* work, not that it *does* when driven through the real codepath.

`core/console/tests/test_web_delegation.py::test_delegation_spec_e2e_worker_receives_real_task_not_placeholder`
closes that gap:

1. Builds the spec via the exact production call —
   `chat_runtime._build_delegation_spec(real_task, cr._DELEGATION_BUDGET_DEFAULTS)`
   — with `real_task = "Analysiere die Verkaufszahlen aus drei Quellen und
   vergleiche sie"`.
2. Instantiates the real `operator/bridges/shared/acs_runtime.ACSRuntime` and
   calls its real `.run(spec)`, so the run goes through the actual
   `_manager_loop`, `_dispatch_workers`, `_build_manager_prompt` and
   `_build_worker_prompt` — no hand-rolled `RunContext`.
3. Fakes only the two LLM subprocess boundaries (`_call_manager_sync`,
   `_call_worker_sync` — the same seam the pre-existing `test_acs_runtime.py`
   suite already fakes, e.g.
   `test_call_worker_sync_prompt_delivered_via_stdin`) plus the L34/L35/L44
   spawn gates (stubbed to "allow", since they gate on engine/tenant policy,
   not on this bug) and `_resolve_worker_engine`. The fake manager returns one
   `DELEGATE` decision, then `COMPLETE`; the fake worker call records the
   exact `prompt` string it was invoked with.
4. Asserts on the **captured worker prompt** — not the spec dict — that it
   contains `real_task` (rendered via the `CONTEXT STATE:` block
   `_build_worker_prompt` builds from `ctx.state`) and does **not** contain
   the literal string `"web-chat delegated turn (ADR-0114)"`.
5. **Regression-checked against the bug**: temporarily reverting
   `chat_runtime.py`'s fixed line back to the hardcoded placeholder (and
   restoring it immediately after) reproduces a **failing** test, with the
   assertion diff showing the captured worker prompt literally containing
   `CONTEXT STATE:\n{"task": "web-chat delegated turn (ADR-0114)"}` — proving
   this test actually exercises the bug's mechanism end-to-end rather than
   passing vacuously.
6. **`grep -rn "web-chat delegated turn" --include=*.py --include=*.ts
   --include=*.tsx --include=*.js --include=*.yaml --include=*.yml .`**
   (excluding `.venv`/`node_modules`) returns exactly two hits, both in
   `test_web_delegation.py`: the regression-guard comment at line 353 and the
   `placeholder` string literal inside the new E2E test itself (used to
   assert its *absence* from the worker prompt). Zero occurrences in any
   production code path.
7. **Suites re-run green**: `core/console/tests/test_web_delegation.py`
   (81 tests, includes the new E2E test) + `core/console/tests/
   test_adr0213_context_sync.py` (8 tests) = 89/89 passed. Also re-ran
   `operator/bridges/shared/test_acs_runtime.py` (66/66 passed) as a
   non-regression check on the shared `acs_runtime` module the new test
   imports and monkeypatches.
8. **`docs/claude-ref/delegation-routing.md`** — re-checked against this
   round's changes: still only documents mechanism *selection*, unaffected
   by this data-construction fix; its diff in the working tree remains the
   unrelated ADR-0216 TDE-badge documentation noted above. No update needed.

### ADR-gate: no ADR required (explicit skip)

Per `docs/claude-ref/adr-gate.md`, an ADR is written only when a real design
choice was made **and** at least one structural trigger applies (new
protocol/wire-format/schema, new security/compliance mechanism, an
irreversible fail-open/closed default, a cross-repo binding, or a new
layer-level contract). This change is a one-line bug fix — `"task": task`
instead of a hardcoded literal — inside an existing function's existing
return shape. It introduces no new protocol, schema, security mechanism, or
cross-repo contract, and the "design choice" (thread the real task through
`state.initial`) is what the function was always documented and intended to
do. **Skip reason: pure bug fix, no structural trigger — matches the
explicit "bug fixes" skip category in the ADR gate rubric.**
