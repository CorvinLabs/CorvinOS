# Concept Gate — Self-Learning Working-Method Archive

**Sibling gate to ADR Gate — same placement, same discipline.** ADR Gate asks "does this
decision need a record?" Concept Gate asks "did I just execute or discover a reusable WAY OF
WORKING — not a one-off decision, not a one-off bug fix — that would save real time if a future
agent had it pre-loaded?" Apply it at the same moment ADR Gate fires: the end of a non-trivial
task, before declaring "done."

## The High Bar

The gate has a **HIGH BAR**: the default answer is **NO concept needed**.

**Most tasks produce no concept — that is correct and expected**, exactly like ADR Gate.

A concept is a design document for future WORKING METHOD, not a record of what a specific piece
of code does. If nothing about *how you solved this* generalizes to the next unrelated task, you
don't need a concept.

## What a Concept Is (and Is Not)

CorvinOS already has two kinds of durable, versioned knowledge:

| | ADR (`Corvin-ADR/decisions/`) | Skill (SkillForge, `type: learned-experience`) | Concept (`Corvin-ADR/concepts/`) |
|---|---|---|---|
| Answers | What did we decide, and why? | How, mechanically, every time? | Why does this way of working keep paying off? |
| Length | As long as the decision needs | ≤8KB, prompt-injectable | As long as the evidence needs |
| Gated by | `adr_gate`, structural triggers | Grading/promotion ladder | Concept Gate, recurrence/generalization |
| Surfaced by | Manual read / `scripts/adr_graph.py` | Automatic prompt injection + auto-grade | Manual read (deep dive) — a durable concept SHOULD also mint a companion Skill for automatic surfacing |
| Amended | New ADR supersedes | New grades / re-created body | Dated amendment note, `## Operator Notes` never touched |

A concept is the **narrative middle layer**: rich enough to carry the reasoning, the false
starts, the trade-offs considered, and concrete evidence — the same depth ADR-0264 models,
applied to *process* instead of *architecture*.

## When to Write or Amend a Concept

**Write/amend a concept when at least one holds:**

1. **Recurrence:** the same investigation/fix/verification SHAPE showed up across 2+ distinct
   tasks in ways that weren't already duplicated code (this is `method-evolution`'s own trigger,
   generalized from "skill/rubric violation" to "any working method").
2. **Generalizable structural fix:** a root-cause fix (not a symptom patch) generalizes beyond
   the one bug it closed, worth naming so the next similar bug gets the deep fix on the first
   pass instead of after a symptom patch.
3. **Decisive verification technique:** a live-VM proof, wheel-content inspection, deploy-then-
   curl-the-real-URL, or similar check caught something static analysis alone would have missed,
   and the same class of risk will recur.

## When to Skip a Concept

**Skip a concept for:**
- A single bug fix with nothing generalizable about it.
- A one-off tooling workaround.
- Anything already fully captured by an existing concept — **amend that one** (see below),
  never create a near-duplicate.
- Anything a ≤8KB Skill snippet genuinely already covers — a full concept adds nothing.

**When you skip, name the reason in one sentence — never skip silently.**

Example: "Skipped Concept Gate — this was a single isolated config fix, nothing about the
approach generalizes beyond this one file."

## How to Apply Concept Gate

1. **At the END** of any non-trivial task, right alongside ADR Gate.
2. **Ask the three trigger questions above.**
3. **If yes and a related concept already exists:** amend it — add a new dated note (mirroring
   ADR-0264-style amendments, prepended under Status), never rewrite or remove prior text, and
   never touch anything under `## Operator Notes`.
4. **If yes and nothing existing covers it:** write a new concept (see Structure below).
5. **If the method is durable and narrow enough to fit ≤8KB of prompt-injectable prose:** mint
   or update a companion SkillForge skill (`skill_create`/`skill_promote`, `type:
   learned-experience`, `scope: project`) distilling the Method section, pointing back at the
   full concept file for depth. Record the skill name in the concept's `skills:` frontmatter
   field. **Then immediately call `skill_grade` once** — `skill_inject.py`'s injection gate
   excludes any skill with `n_grades < 1 or mean_score <= 0` by default
   (`inject_ungraded` defaults `False`), and a brand-new skill has **no organic path to its own
   first grade** (auto-grading only scores skills that were already injected — a real
   chicken-and-egg gap, found via adversarial review 2026-08-02, see CONCEPT-0001's Amendment).
   Score the bootstrap grade at the codebase's own `_AUTO_GRADE_CAP_MAX` ceiling (0.3), with
   notes disclosing it as a manual seed, not earned usage. Skipping this step leaves the skill
   inert on disk forever.
6. **If no:** name the skip reason in one sentence in your summary.

## Concept Destination

**Concepts live in `Corvin-ADR/concepts/` (sibling repository), NOT in this repo** — same rule
as ADRs. Fallback only if Corvin-ADR is genuinely unreachable to the current session:
`CorvinOS/docs/concepts/`, same schema, same gate.

```
Corvin-ADR/concepts/CONCEPT-NNNN-short-title.md
```

**Numbering:** independent 4-digit sequence starting at `0001`, own namespace, never
`ADR-NNNN` — a concept's `id:` is always `CONCEPT-NNNN`.

**Commit message:**
```
concept: add CONCEPT-NNNN — [title]
concept: amend CONCEPT-NNNN — [what changed]
```

## Concept Structure (Template)

```markdown
---
kind: concept
id: CONCEPT-NNNN
status: proposed | accepted | superseded | frozen
supersedes: []          # concept ids this one fully replaces
depends_on: []           # concepts/ADRs to understand first
related: []               # associative cross-references (either namespace)
skills: []                 # SkillForge skill name(s) seeded from this concept
commits: []                 # git SHAs where this method was DEMONSTRATED
paths:                     # optional: code areas where this method most applies
docs:                       # optional: docs kept in sync by this concept's practice
---

# CONCEPT-NNNN — [Title]

**Status:** ...
**Date:** ...
**Deciders:** ...

## Context
What recurred, concretely — cite the real task(s) it came from.

## The Method
The reusable pattern, phrased generally enough to reapply, but grounded in specific detail
(not generic advice).

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

## Namespace Gate for the Companion Skill

`skill_create` enforces a per-persona namespace gate: a skill created under the `assistant`
persona must be named `assistant.<name>` (see `operator/skill-forge/README.md`). This is not a
naming choice — the tool rejects a bare name with `namespace-gate: persona '<persona>' may only
register tools starting with '<persona>.'`. Record the actual registered name (with prefix) in
the concept's `skills:` field, not the name you originally intended.

## Hard Rules (Must NOT do)

1. **Don't write concept content into the CorvinOS repo** when Corvin-ADR is reachable —
   concepts live in Corvin-ADR only, same rule as ADRs.
2. **Don't create a near-duplicate concept** — amend the existing one instead.
3. **Don't edit or delete anything under an existing concept's `## Operator Notes` heading** —
   append a new dated sub-entry above it, only.
4. **Don't mint a SkillForge skill above a persona's namespace-gate prefix** — see above.
5. **Don't declare "done"** on a task that clearly meets a Concept Gate trigger without running
   this gate.
6. **Don't leave a skip implicit** — always write one sentence explaining why you skipped.

## Concepts Seeded So Far

- **CONCEPT-0001 (self-learning project concept archive):** this framework itself — location,
  frontmatter schema, body structure, the gate, the self-learning loop through SkillForge, the
  Operator Notes convention.
- **CONCEPT-0002 (live-report-driven root-cause method):** the systematic
  investigate-root-cause-fix-verify-release method demonstrated across five real Windows/A2A
  bug fixes in one session (0.10.92–0.10.95) — take vague reports as signal but not diagnosis,
  read real code first, separate CONFIRMED from PLAUSIBLE, verify live when proportionate, fix
  the structural root cause, check for a deeper bug before stopping, test what a regression
  would look like, close the loop with docs+release+deploy-verify in the same change. Companion
  skill: `assistant.corvinOS_live_report_root_cause` (project scope), bootstrap-graded (0.3,
  manual seed) same day after adversarial review found the zero-grade injection gap above.

**Known, separately tracked gap (not blocking, not caused by this concept):**
`SkillRegistry._write_slot()`'s plugin-slot mirror for Claude Code's native engine skill loader
resolves to `<CORVIN_HOME>/plugin-slot/` whenever `CORVIN_HOME` is set — true for essentially
every real live session, not just the test sandboxes the code comment names. This likely makes
that second surfacing path structurally unreachable in normal production use, for every
SkillForge-created skill, not specific to this framework. Flagged in CONCEPT-0001's Amendment;
not fixed here (shared registry precedence, needs its own investigation).

## Related

- [CLAUDE.md](../../CLAUDE.md) — Main conventions document, "Concept Gate" section
- [adr-gate.md](adr-gate.md) — Sibling gate for architectural decisions
- [ldd-mandatory.md](ldd-mandatory.md) — LDD skill dispatch table (Concept Gate's row)
- `operator/skill-forge/README.md` — SkillForge scopes, grading, promotion, namespace gate
- Corvin-ADR repository, `concepts/` directory — the actual archive
