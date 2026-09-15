# Proving a routing decision reached the engine

> **Where this belongs.** Concepts are canonically numbered in the sibling
> `Corvin-ADR/concepts/` repo. That checkout does not exist on this machine, so
> this file lands in the documented fallback (`CorvinOS/docs/concepts/`) under
> this directory's slug naming. Renumber it to `CONCEPT-NNNN` when it is moved.

## The problem this solves

A routing decision — which model, which engine, which worker — is uniquely bad
at being tested. The function that makes it is trivially unit-testable, and a
unit test on it proves nothing that matters, because the failure mode is never
"the function returned the wrong model". It is:

- nobody calls the function (dead tier),
- somebody calls it but discards the answer (shadow mode that was never promoted),
- the answer is computed and then overwritten downstream,
- the answer is correct and the subprocess is launched with a different flag.

All four ship green unit suites. Three of the four have actually happened in this
repo: `autoselect_os_model` had unreachable code behind a default-off env flag,
`ModelSelector` classified every turn in shadow mode and the recommendation was
thrown away, and L10's `adapt_context_l10` is registered at every boot with zero
production call sites.

## The method

**Assert on the argv the engine subprocess was really launched with.**

That single assertion is downstream of every one of the four failure modes. It
cannot pass while the tier is dead, while the decision is discarded, or while
something later overwrites it. Nothing else in the stack has that property.

To get there without paying for an LLM call, use the seam the code already has
for pointing at its own binary (`CORVIN_CLAUDE_BIN` in `chat_runtime.py`) and
substitute a real subprocess that records `sys.argv` and emits the handful of
stream-json events the reader parses. This is not mocking the component under
test: the console app, session auth, WebSocket route, turn pipeline, resolver and
audit writer all stay production code. The only thing replaced is the paid
external dependency — which is exactly the boundary an E2E test is supposed to
stop at.

Three supporting moves make the result trustworthy:

1. **Drive the real transport, including the auth handshake.** For the console
   that means `/auth/local-login` → `/auth/whoami` for CSRF → `POST
   /chat/sessions` → `websocket_connect(...)`. `TestClient(app,
   client=("127.0.0.1", …))` is what makes the loopback-only login gate pass;
   the default `testclient` peer is correctly rejected.

2. **Mark the append-only audit tail BEFORE the action.** The chain is shared
   across runs, so "an `os_model.classified` record exists" passes on records the
   turn under test never wrote. Capture the line count first and let every audit
   assertion read only the suffix. Same for any cumulative panel aggregate:
   assert the *delta* the action caused, not the presence of a value an earlier
   run may have contributed.

3. **Run the negative control.** Delete the one line that wires the tier
   (`task_input=prompt`), re-run, and confirm the substantive assertions go red.
   A routing test that stays green with the routing unwired is the whole failure
   class this method exists to catch, reproduced inside the test suite.

## Worked evidence

Tier 2.9 (2026-09-15, `corvin_operator/bridges/shared/model_selector.py` +
`core/console/corvin_console/chat_runtime.py`,
`tests/e2e/test_os_model_tier29_classifier_e2e.py`): 8 assertions green wired, 5
red unwired, and on the live install the first non-Sonnet OS turn in the audit
chain — `os_model.classified outcome=applied` followed by `os_turn.completed
model=claude-haiku-4-5-20251001`. The Model Cost Optimizer panel's
`cost_model_mix` went from one key to two, which is what turned its displayed
"savings" from a fixed price ratio into a measured blend.

## Alternatives considered

- **Unit-test the resolver.** Rejected: 59 such tests were already green while
  the tier they covered was unreachable in production.
- **Grep for a call site and stop there.** That is the cheap reachability half
  and it is worth doing first, but it cannot see mode 3 or 4 (answer overwritten,
  or flag not passed to the subprocess).
- **Let the real LLM run in the test.** Faithful and unusable: costs money, needs
  credentials, and is nondeterministic on latency and content. The argv assertion
  gets the whole benefit without any of that.
- **Assert on the audit record alone.** Necessary but not sufficient — the audit
  record proves what was *decided*, not what was *launched*. Both, in that order.

## When NOT to use this

- The decision has no external process to launch (a pure in-memory policy with
  no argv, no HTTP call, no wire message). Then there is no downstream artifact
  to assert on and the audit record is the best available evidence — say so
  explicitly rather than implying subprocess-level proof.
- The binary-substitution seam does not exist. Inventing a new env var purely so
  a test can hook it is worse than the test is worth; fix the seam only if
  production needs it too.
- Genuinely trivial edits with existing end-to-end coverage over the same path.

## Operator Notes

<!-- Append-only. AI amendments never edit or remove anything below this line. -->
