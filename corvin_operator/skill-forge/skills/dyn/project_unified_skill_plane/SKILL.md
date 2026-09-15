---
name: project_unified_skill_plane
description: Active design for a unified CorvinOS skill system — one store, one resolver, per-surface delivery. Read the full concept in the tenant before working on it.
---

# Unified Skill Plane — working design

Status **PROPOSED**, open decisions still with the operator. This is the
active design for making every generated skill usable on every surface:
console chat, bridges, OS turns and the worker engines.

**Read the full concept before working on it** — this body is a summary:
`.corvin/tenants/_default/global/concepts/unified-skill-plane.md`
(repo copy: `docs/concepts/CONCEPT-UNIFIED-SKILL-PLANE.md`)

## What is broken today (measured 2026-08-20)

- **Five stores.** `<tenant>/skill-forge/` (145 skills, the real one) ·
  `<tenant>/global/skill-forge/` (3, consumed by nothing) ·
  `<repo>/operator/skill-forge/skills/dyn/` (the only path to the native
  engine, **no tenant in it**) · `~/.corvin/packages/` (hardcoded `$HOME`) ·
  `core/skills/corvin_skills/` (a second skill system, used only as a
  manifest validator).
- **Four readers, four rules.** `skill_inject` (grade gate) · CEL
  (binds bodies directly) · the native plugin loader (everything) ·
  `/skills` (directory walk). `/skills` reports 148, the registry 145.
- **Most surfaces get nothing.** Console chat, ACS workers, TDE workers,
  remote triggers and package skills receive no skills at all. Only the
  messenger bridges do.
- **Grade deadlock.** `registry.grade()` has one production caller, so
  144 of 145 skills sit at zero grades while the gate blocks everything
  ungraded. A skill that is never injected can never earn the grade that
  would let it be injected.
- **Two lifecycles, one namespace.** `cel_*` are per-turn caches, never
  cleaned up; `assistant.*` are durable operator-authored capabilities.

## The proposal

One **store** (`<tenant>/skill-forge/`, every producer via
`registry.create()`, new fields `origin` and `lifecycle`), one **resolver**
(classes `pinned` / `graded` / `bound` / `probation`, budgeted per surface),
and thin **delivery adapters** per surface that carry no selection logic of
their own.

`probation` is the load-bearing addition — it breaks the grade deadlock by
giving a new skill a bounded chance to be used and therefore graded.

Do **not** merge the two kinds: forcing CEL turn caches through the grade
gate would bloat every prompt without improving them. Unify the plumbing,
keep the kinds visible.

## Migration order

1-3 are low-risk cleanup (manifest instead of directory walk · manual skills
through the registry · surface `origin`/`lifecycle`). **Step 5 — wiring
console chat to the resolver — is the largest single gain.** Then per-tenant
plugin slot, worker adapters, packages as producers, CEL cleanup.

## Open decisions — ask the operator, do not assume

1. CEL skills: durable, or turn caches with a TTL?
2. `core/skills/corvin_skills/`: absorb or retire?
3. Probation window and cap?
4. Worker skills behind a feature flag? (recommended: yes, default off)

## Known blocker on this install

Corvin's topic-memory root `~/.config/corvin-voice/memory` is a **broken
symlink** into the pre-rebrand `claude-voice-skill/.claudeOS/voice/` tree,
so `write_topic` raises and `for_system_prompt()` always returns empty.
Topic memory is dead here until that is repaired.
