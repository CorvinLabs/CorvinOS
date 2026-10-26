---
name: concept_gate
description: Concept Gate — evaluates after non-trivial tasks whether a reusable WORKING METHOD (not a decision, not a bug fix) is worth archiving; writes to Corvin-Knowledge/concepts/ when yes (or amends an existing entry), names the skip reason when no, requires evidence citations (real commits/tasks), and mints a bootstrap-graded companion SkillForge learned-experience skill so durable methods are auto-injected into future turns, not just archived.
---

# Concept Gate — self-learning working-method archive

Apply this gate once at the END of any non-trivial task, immediately before declaring "done," alongside ADR Gate. Most tasks will not produce a concept — that is the correct and expected outcome.

## The bar is HIGH

The default answer is: **no concept needed.** ADR Gate asks "does this decision need a record?"; Concept Gate asks "did I just execute or discover a reusable WAY OF WORKING — not a one-off decision, not a one-off bug fix — that would save real time if a future agent had it pre-loaded?"

**Do NOT write a concept for:**
- A single bug fix with nothing generalizable about it
- A one-off tooling workaround
- Anything already fully captured by an existing concept — amend that one instead
- Anything a ≤16 KiB SkillForge skill snippet genuinely already covers on its own

## Write or amend a concept only when at least one holds

1. **Recurrence:** the same investigation/fix/verification SHAPE showed up across 2+ distinct tasks in ways that weren't already duplicated code.
2. **Generalizable structural fix:** a root-cause fix (not a symptom patch) generalizes beyond the one bug it closed, worth naming so the next similar bug gets the deep fix on the first pass.
3. **Decisive verification technique:** a live-VM proof, wheel-content inspection, deploy-then-curl-the-real-URL, or similar check caught something static analysis alone would have missed, and the same class of risk will recur.

**Gut-check:** *"Would a future agent, dropped into a similar task with no memory of this one, waste real time rediscovering this approach — or worse, reach for a shallower fix?"*
- Likely yes: write or amend the concept
- Probably not: skip it

## What a concept is (and is not)

CorvinOS already has three kinds of durable, versioned knowledge:

- **ADR** (`Corvin-Knowledge/decisions/`) — what did we decide, and why. Gated by `adr_gate`.
- **Skill** (SkillForge, `type: learned-experience`) — short (≤16 KiB), prompt-injectable, auto-graded from real usage.
- **Concept** (`Corvin-Knowledge/concepts/`) — the narrative middle layer: *why* a way of working keeps paying off, with real evidence (cited commits/tasks), an explicit "when NOT to use" boundary, and the same three-level depth ADR-0264 models, applied to process instead of architecture.

A concept that's durable and narrow enough SHOULD also mint a companion Skill — that's what makes the archive self-learning rather than a document nobody re-reads.

<!-- knowledge-gate-mechanism:begin -->
## Writing it: the shared knowledge-gate mechanism

ADR Gate and Concept Gate answer different questions (a decision versus a working method),
but they write through ONE mechanism and their artifacts are judged by ONE rule source.

1. **Find first.** `python3 /home/shumway/projects/Corvin-Knowledge/scripts/kb.py find <words> --type decision`
   (or `--type concept`) and `kb.py show <id>`. An existing entry is amended with a dated note
   under its Status, never duplicated.
2. **Create with `kb new`, never by hand.**
   `python3 /home/shumway/projects/Corvin-Knowledge/scripts/kb.py new decision --title "..."`
   (or `new concept`), with `--link rel=target` for every real relation. It numbers across the
   whole corpus, writes `id`/`uid`/`title`/`status`/`created`/`links`, refuses a link that does not
   resolve to exactly one entity, and commits in Corvin-Knowledge. Never pick a number with `ls`:
   canonical names are `ADR-NNNN-...`, so a `^[0-9]{4}` scan sees only legacy files and returns a
   number that is already taken. Never commit to Corvin-ADR: it is frozen (ADR-2205), and
   `kb check` reports any commit there as `W02_frozen_source_written`. Call `kb.py` by this
   absolute path — the path gate pins it.
3. **Fill the body and the type's own frontmatter** (below), then commit in Corvin-Knowledge.
4. **Validate against the one rule source.**
   `python3 /home/shumway/projects/Corvin-Knowledge/scripts/kb.py check` must report
   `blocking: 0`. The console's Quality Gates page (`/console/app/quality`) runs this same
   `kb check` over the same checkout: an `error` finding is shown as fail, a `warning` (for
   example the G1-G4 readiness gates) as warn. There is no second rule table to satisfy.
<!-- knowledge-gate-mechanism:end -->

## How to write or amend a concept

**Step 1** — Run step 1 of the shared mechanism (`kb.py find ... --type concept`). If a related concept exists, AMEND it: add a new dated note prepended under Status (same convention ADR-0264 uses), never rewrite prior text, and never touch anything under an existing `## Operator Notes` heading.

**Step 2** — If nothing existing covers it, run `kb.py new concept --title "..."` (shared mechanism, step 2). It writes `concepts/CONCEPT-NNNN-short-kebab-title.md` in its own 4-digit sequence; do not choose the number. Then complete the file:

```markdown
---
kind: concept
id: CONCEPT-NNNN
status: proposed          # proposed | accepted | superseded | frozen
supersedes: []
depends_on: []
related: []
skills: []                # SkillForge skill name(s) seeded from this concept
commits: []                # git SHAs where this method was DEMONSTRATED
paths:                     # optional
docs:                       # optional
---

# CONCEPT-NNNN — [Title]

**Status:** Accepted
**Date:** YYYY-MM-DD
**Deciders:** shumway (or: Claude, extracted from the task it demonstrates)

## Context
What recurred, concretely — cite the real task(s) it came from.

## The Method
The reusable pattern, phrased generally enough to reapply, but grounded in specific detail.

## When to use / When NOT to use
Every method has a domain. Name the boundary.

## Evidence
Concrete instances where this method was applied, and what it caught or avoided.

## Related
Cross-references to ADRs, other concepts, and the seeded Skill(s).

## Operator Notes
_(append-only, timestamped, human-authored. AI amendments NEVER edit or remove anything
under this heading — only add a new dated sub-entry above it.)_
```

**Step 3** — Commit the completed file in Corvin-Knowledge (`concept: CONCEPT-NNNN — [title]`, or `concept: amend CONCEPT-NNNN — [what changed]`) and validate with `kb.py check` (shared mechanism, step 4).

**Step 4** — If the method is durable and narrow enough to fit ≤16 KiB of prompt-injectable prose (ADR-2274), mint or update a companion SkillForge skill (`skill_create`/`skill_promote`, `type: learned-experience`, `scope: project`) distilling the Method section, pointing back at the full concept file for depth. Record the skill's registered name in the concept's `skills:` frontmatter field — note that a persona's namespace gate may require a prefix (e.g. `assistant.<name>`); use the ACTUAL registered name, not the name you intended.

**Step 5 — REQUIRED bootstrap grade.** `skill_inject.py`'s injection gate excludes any skill with `n_grades < 1 or mean_score <= 0` by default (`inject_ungraded` defaults `False`), and a brand-new skill has **no organic path to its own first grade** — auto-grading only scores skills that were already injected, a genuine chicken-and-egg gap. Immediately call `skill_grade` once, score capped at the codebase's own `_AUTO_GRADE_CAP_MAX` (0.3), with notes explicitly disclosing it as a manual seed grade, not earned usage. Skipping this step leaves the skill inert on disk forever — found via adversarial review the same day this gate was added.

## When you skip the concept

Write one sentence — e.g. *"No concept — single isolated config fix, nothing about the approach generalizes beyond this one file."*
Do not leave the gate result implicit. A named skip is as valid as a written concept.

## Related

- `docs/claude-ref/concept-gate.md` — full reference (destination, template, hard rules, known gaps)
- `docs/claude-ref/adr-gate.md` — sibling gate for architectural decisions
- `Corvin-Knowledge/concepts/0001-self-learning-project-concept-archive.md` — the framework itself
- `Corvin-Knowledge/concepts/0002-live-report-driven-root-cause-method.md` — the first seeded concept
