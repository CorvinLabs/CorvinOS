# Tool Forge — review gate, retry and rejection audit

Scope: `corvin_operator/skill_creator/{artifact_review,tool_creator}.py` and
`core/console/corvin_console/routes/forge_creator.py` (ADR-2217).

## Phases

`planning → validation → sandbox_test → review → promotion`. A tool reaches the
registry only when every sandbox case passed AND the security review neither
confirmed a boundary finding nor failed to complete.

## Why a run used to die in `review` (measured 2026-10-10)

`assistant.backtest_price_series` was rejected with "reviewer replied without a
verdict" although all sandbox cases passed. Four live security-reviewer replies
on the same artifact: 1 prose report without a `VERDICT:` line, 3 with 4-8
`CONFIRMED` findings about input robustness (empty list, NaN, negative cash).
None would have passed. `max_tokens` is NOT enforced on the `claude -p` path,
so truncation was ruled out.

## Gate semantics now

| Situation | Result | `failure_code` |
|---|---|---|
| reviewer crashed / no `VERDICT:` | asked again, `REVIEW_RETRIES = 2` extra attempts per dimension | — |
| still no verdict after retries | blocks, draft kept | `reviewer_unavailable` |
| `CONFIRMED` + tag `EXFIL`/`SHELL`/`NETWORK`/`FILESYSTEM`/`SECRET` | blocks | `security_confirmed` |
| `CONFIRMED` + **no tag** | blocks (fail-closed) | `security_confirmed` |
| `CONFIRMED` + `[ROBUSTNESS]` and no boundary word | advisory: recorded (`advisory: true`), does not block | — |
| `[ROBUSTNESS]` whose text names `os.system`, `socket`, `environ`, `eval`, … | blocks | `security_confirmed` |
| sandbox cases still failing | blocks | `tests_failed` |

Only an explicit tag downgrades a finding. The static import check and the
bwrap jail (no network, read-only `/usr`) are unchanged.

## Planning guard

A plan reply must carry `name`, `impl`, `input_schema`, `test_cases`
(`PLAN_KEYS`). `ask_json(required_keys=…)` searches the reply for a later object
that does, and a reply with none is asked again (`PLAN_ATTEMPTS = 3`), then fails
with `plan_unusable`. Before, a keyless object became an empty draft ("assistant.tool"),
the repair step was handed that shell, and the engine invented an unrelated tool
(observed in the 2026-10-10 re-forge: `assistant.text_stats` instead of the backtest).

## Nothing is lost on rejection

- The validated draft is written into the run record's `resume` payload as soon
  as it passes validation (`draft_cb`), so a console restart resumes from it.
- A failed run keeps `draft` (impl, cases, `review_raw` = every raw reviewer reply).
- `POST /v1/console/forge-creator/tool/retry/{run_id}` re-runs sandbox tests +
  review from the saved draft of a FAILED run (409 otherwise). It never calls
  the planning engine again.
- `GET /forge-creator/status/{run_id}` additionally returns `failure_code` and `draft_saved`.

## Audit

`forge.tool_rejected` (WARNING, tenant chain): `code`, `phase`, `review_attempts`,
`tests_passed`, `tests_total`, `blocking_findings`, `advisory_findings`. Metadata
only — no code, no reviewer text. Completion rate = `tool.created` vs `forge.tool_rejected`.

## Proof

`core/console/tests/test_forge_creator_e2e.py` drives the real router, the real
bwrap sandbox and a scripted engine: three different tool kinds through all five
phases, a reviewer format slip retried, advisory vs boundary findings, draft
kept + retry without re-planning, rejection audit.
