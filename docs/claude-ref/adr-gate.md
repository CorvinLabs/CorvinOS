# ADR Gate — Architectural Decision Records (Standard Quality Discipline)

**adr-gate is now a standard quality discipline.** It is automatically injected into all default personas
(assistant, coder, browser, research) via the skill system. After every non-trivial implementation task,
the skill is available in your prompt; follow its rubric before declaring "done."

## The High Bar

The gate has a **HIGH BAR**: the default answer is **NO ADR needed**.

**Most tasks produce no ADR — that is correct and expected.**

An ADR is a design document for future engineers. If your change doesn't change how future engineers
will approach this subsystem, you don't need an ADR.

## When to Write an ADR

**Write an ADR only when BOTH of the following hold:**

1. **A real design choice was made:**
   - You chose option A over option B (genuine alternatives existed)
   - The choice constrains future code (e.g., "we always X when Y happens")
   - It's not the obvious/only solution

2. **At least one structural trigger:**
   - **New protocol / wire-format / schema** (changes how data flows between systems)
   - **Security or compliance mechanism** (new cryptographic operation, audit event, access gate)
   - **Irreversible default** (fail-open vs. fail-closed, deny vs. allow, can't be easily reversed)
   - **Cross-repo binding** (affects ≥2 repos, standardizes a boundary)
   - **New layer-level contract** (changes how a layer or subsystem works for all callers)

## When to Skip an ADR

**Skip an ADR for:**
- Bug fixes (even if complex) — you're restoring the intended behavior, not changing it
- Pure refactors — same external behavior, different internal code
- Config tuning — changing a number or flag, not the mechanism
- Test-only or docs-only changes — no code behavior change
- Anything trivially reversible without migration
- Simple feature additions that follow an established pattern

**When you skip, name the reason in one sentence — never skip silently.**

Example: "Skipped ADR because this is a pure refactor of the Forge MCP handler (no behavior change)."

## How to Apply adr-gate

1. **At the END** of any non-trivial task (feature, multi-file bugfix, refactor with behavior change, etc.)
2. **Load and read the adr-gate skill prompt** (it will be injected if available)
3. **Follow the three-level analysis:**
   - **Conceptual:** Does this task touch a design decision or a new mechanism?
   - **Structural:** Does it meet one of the trigger criteria above?
   - **Implementation:** Would future engineers benefit from knowing why we chose this way?
4. **If yes:** Write the ADR (see ADR Destination below)
5. **If no:** Name the skip reason in one sentence in your summary

## ADR Destination

**ADRs live in `Corvin-ADR/decisions/` (sibling repository), NOT in this repo.**

```
Corvin-ADR/decisions/XXXX-short-title.md
```

**Numbering:** max existing number + 1, four digits zero-padded (0042, 0156, etc.).

**Commit message:**
```
adr: add ADR-XXXX — [title]
```

Example:
```
adr: add ADR-0160 — Custom Layer System Tier Licensing
```

## ADR Structure (Lightweight Template)

Every ADR opens with a machine-readable frontmatter block ahead of the prose (ADR-0264 —
"ADR Decision Graph: hermeneutic-circle traversal for architectural history"). The prose
below is unchanged and still what a human reads; the frontmatter is what lets
`scripts/adr_graph.py` find this ADR from a code path and build the minimal relevant
subgraph instead of a reader needing the whole corpus.

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

## Status
Accepted | Proposed | Superseded

## Context
Why are we making this decision? What problem are we solving?

## Decision
What did we decide? (One sentence.)

## Rationale
Why this way and not the alternatives?

## Consequences
What changes as a result? What becomes easier/harder?

## Related
- [ADR-YYYY](...) — Previous decisions this builds on
- [Layer 16](../docs/claude-ref/layer-16-security.md) — Implementation details
```

Never hand-fill `superseded_by` — omit it. `scripts/adr_graph.py` derives it automatically
from every other ADR's `supersedes` list at read time, so the ADR being superseded never
needs a retroactive edit (that hand-maintained back-reference is exactly the class of fact
that silently rots — see ADR-0264's Context).

## ADR Decision Graph — traversal (ADR-0264)

`scripts/adr_graph.py` (in this repo, reads `../Corvin-ADR/decisions/`) answers "which
ADRs govern this file, and in what order should I read them":

```bash
python3 scripts/adr_graph.py core/plugins/plugin_builder/generators/adr.py
python3 scripts/adr_graph.py --adr 0264 --format json   # machine-readable, for an agent
```

It returns the seed ADR(s) whose `paths:` glob matches the query, plus their `depends_on`
closure, topologically sorted (dependencies first) and annotated with current status via
the derived `superseded_by` chain. A path matching no ADR is the expected default (not an
error) for the pre-ADR-0264 corpus — that older corpus is deliberately not retrofitted;
see ADR-0264's "Decision" §4 and fall back to this file's own prose cross-references.

Any document-generator in this repo that produces an ADR-shaped artifact (e.g.
Plugin-Builder's per-plugin classification ADR, `core/plugins/plugin_builder/generators/
adr.py`) emits this same frontmatter, with a plugin-scoped `id` (`{plugin_id}-ADR-0001`)
rather than a bare `ADR-NNNN`, which is reserved for this repo's own sequence.

## Hard Rules (Must NOT do)

1. **Don't write ADR content into the Corvin repo** — ADRs live in Corvin-ADR only.
   The sibling repo is the source of truth.

2. **Don't auto-skip for security/compliance mechanisms** without explicit written justification.
   Security/compliance changes almost always warrant an ADR (they constrain future work).

3. **Don't declare "done"** on a structural change without running adr-gate.
   If you're unsure, run the gate — it takes 5 minutes and catches overconfidence.

4. **Don't leave a skip implicit** — always write one sentence explaining why you skipped.
   Implicit skips become lost decisions.

## Recent ADRs Implemented

- **ADR-0264 (ADR Decision Graph):** frontmatter schema (`depends_on`/`related`/`paths`/
  derived `superseded_by`) + `scripts/adr_graph.py` traversal tool; wired into
  Plugin-Builder's generated ADR (`core/plugins/plugin_builder/generators/adr.py`)
- **ADR-0069 (EAOS — Engine-Agnostic OS Shell):** Tool Execution Broker (TEB), Engine Command Interface (ECI), Function-Call Bridge (FCB), SkillCompiler — all non-CC engines share L10/L16/L33 guarantees
- **ADR-0071 (CopilotCliEngine):** GitHub Copilot CLI as 5th WorkerEngine (worker-only, `copilot -p`, task-type steering)
- **ADR-0141 (Layer Integrity Protocol):** Cryptographic layer presence verification (CAP_VERSIONS + RS256-signed manifest)
- **ADR-0142 (Layer Extension API):** Vendor-layer extensibility (Tier-A/B/C licensing model)
- **ADR-0156 (Custom Layer System):** Custom layer installation, licensing gate, boot enforcement

## Related

- [CLAUDE.md](../../CLAUDE.md) — Main conventions document
- [compliance-baseline.md](compliance-baseline.md) — Compliance constraints (ADRs often needed here)
- [Corvin-ADR repository](https://github.com/anthropics/corvin-adr) — Public ADR source of truth
