---
name: adr_gate
description: ADR Gate — evaluates after non-trivial tasks whether an ADR is warranted; writes to Corvin-ADR repo when yes, names the skip reason when no, requires three-level analysis (conceptual/structural/implementation), emits ADR-0264 decision-graph frontmatter (depends_on/related/paths) so scripts/adr_graph.py can traverse it, and generates E2E tests for every new structural invariant so LDD has a loss signal.
---

# ADR Gate — Architectural Decision Record discipline

Apply this gate once at the END of any non-trivial implementation task, immediately before declaring "done." Most tasks will not produce an ADR — that is the correct and expected outcome.

## The bar is HIGH

The default answer is: **no ADR needed.** An ADR is only warranted when a future contributor would be genuinely confused about *why* the system works a certain way — and that confusion would cost them real time or lead them to inadvertently break a structural invariant.

**Do NOT write an ADR for:**
- Bug fixes with no design decision (one obvious fix, no real alternative)
- Pure refactors where behaviour is unchanged
- Config value or threshold changes
- Test additions or fixes
- Documentation, comment, or copy changes
- UI tweaks, renaming, small cleanups
- Anything trivially reversible without data migration or cross-repo coordination

## Write an ADR only when BOTH conditions hold

**Condition A — A real design choice was made:**
You chose A over B, and that choice non-trivially constrains future code. There was a genuine alternative.

**Condition B — At least one structural trigger applies:**
- New or changed protocol, wire format, audit schema field, MCP tool contract, or CLI flag shape
- New security or compliance mechanism (anything touching L10, L16, L34, L35, GDPR, EU AI Act)
- Irreversible or hard-to-reverse default: fail-open/closed, deny/allow, opt-in/opt-out
- Decision binds two or more repos (e.g. Corvin + Adapter + Cloud)
- New layer-level mechanism, plugin contract, or cross-layer invariant

**Gut-check:** *"Would a capable new contributor, six months from now, spend hours puzzling over why this works this way — or worse, accidentally undo it?"*
- Likely yes: write the ADR
- Probably not: skip it

## Three-level analysis (mandatory)

Before deciding whether to write an ADR — and when writing one — think through all three levels explicitly. A decision described at only one level is an incomplete record; the missing levels will drift independently until someone breaks something.

**Conceptual level** — What is the core principle or idea being decided? What mental model does this create for future contributors? What rule of thumb or invariant follows from this choice that a reader should carry in their head?

**Structural level** — How does this decision constrain the architecture? Which layers, interfaces, protocols, contracts, or cross-repo boundaries does it shape? What cannot be changed without revisiting this decision?

**Implementation level** — What specific code, config, runtime behaviour, file layout, or schema shape is locked in? What does a developer need to know concretely to implement something that respects this decision?

All three levels must appear explicitly in the ADR's **Context** and **Decision** sections. If one level has nothing to say (rare), name that explicitly — e.g. *"Implementation level: no specific shape is locked in beyond the structural constraint above."*

## How to write the ADR

**Step 1** — Find the next number:

```bash
ls ../Corvin-ADR/decisions/ | grep -E '^[0-9]{4}' | sort | tail -1
```

Add 1, zero-pad to 4 digits (e.g. `0068`).

**Step 2** — Write to `../Corvin-ADR/decisions/XXXX-short-kebab-title.md`. Every ADR
carries **both** the human-facing prose header **and** a machine-readable frontmatter
block (ADR-0264 — "ADR Decision Graph"): the frontmatter is what makes an ADR a node a
coding agent can traverse to instead of a document it must have already read.

```markdown
---
id: ADR-XXXX
status: proposed          # proposed | accepted | superseded | frozen
supersedes: []            # ADR ids this one fully replaces
depends_on: []            # structural prerequisites — read these first
related: []               # associative, non-blocking cross-references
commits: []               # git SHAs implementing this, filled in at merge
paths:                    # globs this ADR structurally constrains
  - "path/or/glob/**"
---

# ADR-XXXX — [Title]

**Status:** Accepted
**Date:** YYYY-MM-DD
**Deciders:** shumway

## Context

[What situation, constraint, or requirement forced a decision here?]

## Decision

[What was decided. Direct statement.]

## Consequences

### Positive
-

### Negative / Trade-offs
-

### Alternatives considered

[What else was evaluated and why it was rejected.]
```

Do NOT hand-fill `superseded_by` — leave it absent. It is derived automatically (by
`scripts/adr_graph.py`, see ADR-0264) from every OTHER ADR's `supersedes` field, so the
node being superseded never needs editing after the fact.

Populate `depends_on`/`related` with real ADR ids only when a real relationship exists;
an empty list is the correct default for a freestanding decision. `paths` should name the
actual files/globs this decision structurally constrains — this is the field
`scripts/adr_graph.py adrs_for_path()` matches against, so an empty or inaccurate list
makes this ADR invisible to that traversal, not merely undocumented.

**Step 3** — Commit in the Corvin-ADR repo:

```bash
cd ../Corvin-ADR
git add decisions/XXXX-short-kebab-title.md
git commit -m "adr: add ADR-XXXX — [title]"
```

**Step 4** — If the ADR documents a load-bearing mechanism in Corvin, add a one-line reference at the end of the relevant CLAUDE.md section:

```
→ ADR: `Corvin-ADR: decisions/XXXX-short-title.md`
```

This prose pointer stays — it is what a human skimming CLAUDE.md sees — but it is no
longer the only way to find this ADR from the code. `paths:` in Step 2's frontmatter is
the machine-readable equivalent: run `python3 scripts/adr_graph.py <path>` from a
CorvinOS checkout to confirm the new ADR is actually discoverable from the code it
constrains before moving on.

A generated (not hand-authored) ADR — e.g. Plugin-Builder's per-plugin classification
ADR (`core/plugins/plugin_builder/generators/adr.py`) — carries this same frontmatter
schema too, with a plugin-scoped `id` (`{plugin_id}-ADR-0001`, never a bare `ADR-NNNN`,
which is reserved for this repo's own sequence) and `related` pointing into the real
Corvin-ADR corpus. Any new document-generator that produces an ADR-shaped artifact
should follow this same convention rather than emitting unstructured prose.

**Step 5 — E2E tests for the ADR's mechanism (LDD loss signal)**

Immediately after writing the ADR, ask: *"Can I write one or more E2E tests that would FAIL if this mechanism broke or was accidentally reverted?"*

If yes, write the test(s) — this is the LDD loss signal for the new invariant. Rules:

- **Real subprocess, real filesystem, real bwrap** — no mocks for anything touching security, forge, policy, or audit (per `per_subtask_e2e` layer). Mocks are only acceptable for pure protocol-shape tests where the real runtime is unavailable.
- **Name to the ADR:** prefix the test file with the ADR number, e.g. `test_adr_0068_egress_lockdown.py`. This makes regressions immediately traceable.
- **One test per structural invariant** stated in the ADR's "Must NOT do" list. Each invariant is a distinct test case.
- **Register in the suite:** add the new file to `operator/bridges/run-all-tests.sh` (or the relevant sub-suite) so it runs on every `git push`.
- **Commit together** with the implementation, not as a follow-up — a structural decision without a sensor is an untested invariant.

If an E2E test is genuinely infeasible (external system unavailable at test time, hardware dependency, etc.), name the reason explicitly — e.g. *"No E2E test — requires live TSA endpoint; covered by contract test in `test_adr_0073_tsa_contract.py`."* Never skip silently.

**Why this matters for LDD:** The outer loop of LDD (`loop-driven-engineering`) drives refinement via measurable loss. An ADR that ships without a test provides no loss signal — drift goes undetected until a human notices. E2E tests convert structural guarantees into machine-checkable facts.

## When you skip the ADR

Write one sentence — e.g. *"No ADR — bug fix, no design decision."*
Do not leave the gate result implicit. A named skip is as valid as a written ADR.

If the ADR is skipped but the task introduced observable new behavior, still evaluate Step 5 independently: E2E tests are warranted for significant behavior changes even without a formal ADR.
