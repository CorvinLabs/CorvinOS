---
name: e2e-wiring-proof
description: Use immediately before declaring "done" on any code generation that adds a new entry point — function, endpoint, route, CLI command, UI component, plugin, hook, or bridge handler. Forbids closing the task without (1) proving the new code is reachable from a real trigger, outside test files, and (2) a generated or extended E2E test that exercises it through that real entry point, executed with captured evidence. A unit test that imports and calls the code directly does not satisfy this gate.
---

# E2E Wiring Proof — reachability + functional proof discipline

Apply this gate once, at the END of any code-generation task that introduces a new entry
point, immediately before `docs-as-definition-of-done` and before declaring "done." It is
the sibling gate to `adr_gate`: `adr_gate` asks "does this decision need a record?"; this
gate asks "does this code actually run, and can I prove it?"

**Why this exists:** a unit test proves a function returns the right value *when called*.
It says nothing about whether anything in the running system *calls* it. CorvinOS has
shipped structural instances of this exact failure — plugin types that register
successfully but are never invoked by any real code path, and an auth invariant that is
unit-tested but reachable from zero live call sites. Both were "tested," both were dead.
This gate exists to make that class of bug structurally harder to ship.

## The bar

The default assumption is: **new code is unreachable until proven otherwise.** Do not
assume wiring succeeded because the code compiles, the unit test passes, or the diff
"looks complete." Prove it.

## When this gate fires

**Fires on:**
- A new function, class, or module intended to be called from outside its own file
- A new HTTP/REST endpoint or route
- A new CLI command or flag that triggers new behavior
- A new UI component, page, or interactive element
- A new plugin, hook, extension point implementation, or event subscriber
- A new bridge/adapter message handler
- Any change where a reasonable reviewer could ask "...but does anything actually call this?"

**Does NOT fire on:**
- Trivial edits (rename, typo fix, comment, formatting)
- Pure refactors with unchanged behavior and existing E2E coverage
- Config value or threshold tuning
- Internal helper functions with no new external entry point, already exercised by existing callers' tests
- Code explicitly marked as an in-progress prototype not yet meant to be wired (state this explicitly if invoked)

When in doubt, treat it as firing — the cost of a skipped gate (dead code shipped as
"done") is higher than the cost of a five-minute reachability check.

## Phase 1 — Reachability proof (cheap, do this first)

Before writing or running any test, trace the call graph:

1. Grep for the new symbol name / route path / component export / CLI subcommand.
2. Find at least one call site that is:
   - **Outside the file where the symbol is defined**, AND
   - **Outside test files**, AND
   - Traceable to a real trigger: a routing table, a CLI argument parser registration, a
     UI render tree, a plugin/extension-point registry, a cron/schedule config, a
     message-bus subscription, an `__init__.py` re-export that something imports.
3. If you find zero such call sites: **STOP.** This is not a warning to note and move
   past — it is a blocking finding. Do not proceed to Phase 2 with orphaned code.
   Dispatch `root-cause-by-layer`: why is this unreachable? Is the registration
   commented out, gated behind a flag nobody sets, or genuinely never wired in? Fix the
   wiring — do not lower the bar by writing a test for code nothing calls.

## Phase 2 — Generate or extend exactly one E2E test

Only after Phase 1 passes. The test must exercise the new code through its **real,
user-facing entry point** — the same interface a real caller would use:

| Entry point type | The test must go through |
|---|---|
| HTTP endpoint/route | A real HTTP request against a running (or test-client-driven) server — not a direct call to the handler function |
| CLI command | A real subprocess invocation of the installed command — not a direct call to the command's Python function |
| UI component | A real browser interaction (Playwright or equivalent) — not a component-level render-and-assert with mocked props |
| Plugin/hook/extension point | Invocation through the actual registry/dispatch mechanism — not a direct call to the plugin class |
| Bridge/adapter handler | A real message pushed through the adapter's ingestion path — not a direct call to the handler |
| MCP tool | A real tool call through the MCP protocol — not a direct Python import of the tool function |

**Hard anti-gaming rule:** a test that imports the new symbol directly and calls it,
skipping the transport/interface boundary, is a unit test wearing an E2E label. It does
NOT satisfy this gate, no matter how thorough its assertions are. If an E2E test already
covers this entry point end-to-end, **extend it** rather than writing a duplicate.

Name the test so a regression is traceable to the feature (e.g.
`test_<feature>_e2e.py`), and register it in the project's test suite
(`operator/bridges/run-all-tests.sh` or the relevant sub-suite) so it runs on every push.

## Phase 3 — Execute and capture evidence

Run the test. Do not declare "done" on the strength of having written it.

- Capture the real exit code and relevant stdout/output.
- Include that evidence in the commit message, PR description, or response — a bare "I
  added an E2E test" claim with no execution transcript does not close the loop
  (`verification-before-completion`).
- A failing or skipped E2E test blocks the gate exactly like a failing reachability
  check. Fix the wiring or the test; do not weaken the assertion to make it pass.

## When Phase 2 is genuinely infeasible

Some entry points cannot be driven end-to-end at commit time (hardware dependency,
external system unavailable in CI, licensed third-party service). In that case:

- Name the reason explicitly — e.g. *"No E2E test — requires a live TPM; covered by a
  contract test against a software TPM simulator in `test_hw_binding_contract.py`."*
- Never skip silently. A named exception is as valid as a passing test; an implicit skip
  is not.
- Phase 1 (reachability proof) still applies unconditionally — infeasibility of the
  functional test never excuses skipping the reachability check.

## Must NOT do

- Declare a code-generation task "done" without running this gate
- Treat "the unit tests pass" as equivalent to "this is reachable and works end-to-end"
- Write an E2E test that imports and calls the target directly, bypassing its real
  transport/interface boundary, and label it as satisfying this gate
- Mock the exact component under test inside its own "E2E" test
- Skip the reachability check because "the code obviously gets called somewhere"
- Leave an infeasibility skip implicit — name the reason
- Add the E2E test in a follow-up commit instead of alongside the implementation

## Relationship to other LDD skills

- **`adr_gate`** — asks whether the *decision* needs a record; this gate asks whether the
  *code* is reachable and proven. Run both at task close; they are independent and often
  both fire on the same task.
- **`root-cause-by-layer`** — dispatched from Phase 1 when reachability fails; do not
  patch around an orphaned call site, find why it's orphaned.
- **`e2e-driven-iteration`** — governs the inner fix-loop rhythm while iterating on a
  known-failing test; this gate is the one-time close-of-task check that a *newly
  introduced* entry point has E2E coverage at all.
- **`docs-as-definition-of-done`** — run immediately after this gate; document the new
  entry point once you've proven it exists and works.
