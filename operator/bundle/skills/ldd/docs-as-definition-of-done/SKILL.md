---
name: docs-as-definition-of-done
description: Use when finishing any code change that modifies behavior, public API, CLI flags, config shape, defaults, error messages, or anything a reader-of-the-docs would model — before committing, pushing, or declaring "done." Forbids deferring doc updates to a follow-up commit, ticket, or "cleanup later."
---

# Docs-as-Definition-of-Done

## Overview

Docs describe code **exactly, but at the conceptual level**. A reader of the markdown must get a faithful, current mental model of the code without reading the code.

**Core principle:** A code change is **not done** until every documentation statement that the change invalidated has been updated **in the same logical task**. No "I'll fix the docs later." No separate doc-cleanup commits. No stub TODOs.

**Why:** Stale docs are worse than missing docs. They confidently mislead readers — future-you, reviewers, new teammates, AI coding assistants loading them as context — into building on a model that no longer matches reality. Each stale statement silently compounds: every downstream decision made against it is already wrong.

## When to Use

Invoke before you `git commit` / `git push` / say "done" for any change that touches:

- Public API shape (signatures, return types, error types, exceptions raised)
- CLI surface (commands, flags, defaults, exit codes)
- Config shape (YAML / TOML / JSON keys, types, defaults, required/optional)
- Behavior under any documented condition (retries, rate limits, timeouts, fallbacks)
- Error or log messages that docs quote verbatim
- Data schemas, database columns, API payload shapes
- Architecture / layer boundaries / module ownership
- Security, policy, or compliance promises
- Version-gated or platform-specific behavior
- Performance or scaling guarantees

## When Not to Use

- Pure internal refactors where no public surface changes and no doc references the internals
- Pure formatting / rename / reorganize with no semantic change
- Dead-code deletion that no doc mentions
- Test-only changes (unless the test itself is documented as an example)

## The Rule

**Definition of done per logical task, not per edit.**

- Mid-task, while iterating on an approach, you do **not** have to resync docs after every file edit.
- Once the approach is settled and the task is about to close (tests green, ready to commit), **every doc statement invalidated by the change must already be updated.** No exceptions, no ticket-filed-for-later.

**Mid-task tolerance** exists so you don't thrash on docs during exploration. It does **not** extend to "I'll do it next commit."

## Sync Reflex — what to check before "done"

For every file you touched in this task, ask:

1. **Did a public signature change?** → grep docs for the old symbol name, type, or signature text.
2. **Did CLI flags / commands / defaults change?** → grep docs, READMEs, help output, examples.
3. **Did a config key / default / type change?** → grep docs AND grep configuration examples AND check schema files.
4. **Did an error / log message change?** → grep docs for the quoted string.
5. **Did a behavioral guarantee change?** (timeouts, retries, order, atomicity, idempotency) → find the section that promised the old behavior.
6. **Did an invariant / rule / constraint change?** → find where it was first documented and where it's referenced.
7. **Are there mental-model / architecture diagrams / mermaid graphs affected?** → check both the source and the rendered artifact (SVG, PNG).

A fast version: `git diff --stat main...HEAD`, then for each changed file name and each identifier in the diff, run one grep across all `*.md` / `*.rst` / `*.txt` / docstrings.

## Red Flags — STOP, you are about to ship stale docs

- "I'll do the docs in a follow-up PR"
- "The code is self-documenting; docs are optional"
- "The README is slightly outdated anyway"
- "Tests are passing, that's what matters"
- "Release freeze at X, I'll catch up tomorrow" *(docs edit is 30 seconds; a wrong safety claim ships in production is much worse)*
- "No one reads the docs anyway"
- "The docs say 'no Y'; I'll just leave it, a user will figure it out"
- "Grep for the old name later once I have time"
- Committing with only code/test files staged when docs reference the changed behavior
- A TODO comment that says "update docs" as the doc update

When one fires: stop, grep, edit docs, re-stage, commit as one logical unit.

## Sync Table (Generic)

| Change | What to check and update |
|---|---|
| Renamed / moved / deleted file, module, class, function | All docs referencing the old symbol or path (README, docstrings, examples, `@link` cross-refs) |
| Added / changed / removed function parameter | Docstring, API docs, examples, tutorials using that function |
| Added / removed / renamed CLI flag or command | README "usage", help output, `man` page, cli-reference.md, tutorials |
| Changed default value | Any doc that cites the old default explicitly or implicitly |
| Changed config schema | README quickstart, schema files, example configs, migration notes |
| Changed error type / message | Troubleshooting sections, FAQ, examples that show the error |
| Changed exception raised | API docs, docstring `Raises:` section, caller-side try/except examples |
| Changed behavior under a named condition | The doc section that names the condition |
| Changed security / privacy / compliance behavior | Security docs, policy docs, customer-facing promises |
| Changed performance characteristic | Performance docs, SLO docs, benchmark tables |
| Added new public behavior | Add a new section where similar behaviors live, not a dangling appendix |
| Changed architecture / ownership | Architecture docs, ownership files (CODEOWNERS, TEAM.md) |
| Changed code covered by an ADR's `paths:`, or editing a doc covered by an ADR's `docs:` | Run `python3 scripts/adr_graph.py <changed-path>` (code) or `--doc <changed-path>` (doc) — a match means an ADR describes this decision and may need an amendment alongside the doc sync (ADR-0264, "ADR Decision Graph") |

Adapt the table to your project — the principle is the same: **code change → specific doc sections to check.**

## The Mental-Model Check

A stricter bar beyond "grep for renamed symbols": after your change, is the **mental model** a reader builds from the docs still correct?

Ask: "If a new engineer read only the docs, would they know this change happened in the right place?" If the docs describe the old concept correctly but your change silently shifts the concept's boundaries, you still owe a doc update — even if no literal string matches.

Example: changing a function from synchronous to async changes the *mental model* even if you kept the name and added only one new parameter. The doc section that described the sync semantics is now a lie.

## How to Apply — checklist

1. Before `git commit`: run `git status` and `git diff --stat` over your change.
2. For each changed public surface (signature, flag, config key, default, behavior), run a quick grep across docs for the old symbol/behavior.
3. Update every hit **now**, not later. Stage the doc changes in the same commit as the code.
4. Re-read the updated doc sections from top to bottom — not just the changed lines. Surrounding paragraphs often still reference the old concept implicitly.
5. Verify diagrams (Mermaid, DOT, SVG) are regenerated if the architecture / flow changed.
6. Only **then** commit. One logical change, code + docs together.
7. Consider adding a doc-drift gate to CI (grep-based) if this is a repeat offender in your repo.

## Common Rationalizations

| Excuse | Reality |
|---|---|
| "I'll update docs in the follow-up PR" | Follow-up PRs for docs get deprioritized, forgotten, or merged by the next person without the context. You are the only person who will do this right; do it now. |
| "The docs are already out of date, what's one more drift" | Each drift compounds. The remedy is to stop adding drift, starting now, not to keep adding because the situation is already bad. |
| "Release freeze, no time" | A doc edit is 30 seconds. Shipping a false safety / behavior claim is a bigger problem than missing a freeze window. Make the call explicit, don't assume docs are cheap to defer. |
| "Tests are the spec" | Tests describe what the code does. Docs describe **what the code promises**. They are different artifacts with different readers. |
| "No one reads the docs" | *You* read the docs when you load them as context for the next feature. So does the next LLM. Stale docs poison future decisions. |
| "The change is internal, no public-facing doc references it" | Grep first. If the grep really is empty, you're right — this is why "When Not to Use" exists. |

## The failure mode this skill prevents

Under release-freeze pressure, the textbook failure is: ship code + tests, leave a `TODO: update README` comment, file a follow-up ticket that never closes. Two weeks later a reader trusts a now-false safety claim and makes a decision against the stale model. The cost of the stale line is paid by the reader, not the author.

With this skill: one commit, code + tests + docs synced. The 30 seconds of `grep + edit` saved a compounding readability cost.

Measured behavior change for this skill is documented in `tests/fixtures/docs-as-definition-of-done/` alongside the scenario used to test it.

## Related

- **`root-cause-by-layer`** — a bug report against a doc-described behavior should walk the ladder on the doc as well as the code; stale docs are a Layer-3 (contract) violation between the reader and the codebase.
- **`loss-backprop-lens`** — docs are a regularizer. Shipping a code change without doc sync raises generalization loss (future decisions built on a stale model).
- **`dialectical-reasoning`** — the antithesis of "done" is "what doc statement is now false?" If you can't name one, you haven't looked.
- **`adr_gate`** — a sibling discipline, not a duplicate: this skill keeps the DOCS surface synced with code; `adr_gate` keeps the DECISION RECORD synced with structural changes. `scripts/adr_graph.py` is the traversal tool both meet at — an ADR's `paths:` is its code surface, its `docs:` is exactly the surface this skill's Sync Table governs, and both feed the same graph (ADR-0264, "ADR Decision Graph"). Before closing a doc-sync task, running `scripts/adr_graph.py --doc <path>` on the doc you just edited is a cheap way to check whether an ADR needs the same attention.
