# CONCEPT: Unified Skill Plane

**Status:** PROPOSED
**Author:** Claude Code
**Date:** 2026-08-20
**Scope:** cross-cutting — SkillForge, CEL, Skill-Creator, packages, console, bridges, worker engines

> Goal, in the operator's words: *every skill, wherever it came from, usable
> everywhere — in the OS and in the worker engine.*

---

## 1. Where we actually are

Measured on this install, 2026-08-20. Not inferred — every number below came
from the running system.

### Five stores

| # | Location | Contents | Written by | Read by |
|---|---|---|---|---|
| 1 | `<tenant>/skill-forge/` | **145** skills + manifest | Skill-Creator, CEL, SkillForge MCP | `skill_inject`, CEL, `/skill-creator/skills` |
| 2 | `<tenant>/global/skill-forge/` | **3** skills, no manifest | `skills_manual.py`, pre-fix Skill-Creator | `/skills` only — **nothing consumes it** |
| 3 | `<repo>/operator/skill-forge/skills/dyn/` | **36** mirrors | `registry.create()` for `user`/`project` scope | the native `claude` plugin loader — **the only path to the engine** |
| 4 | `~/.corvin/packages/<tenant>/installed/` | ZIP packages | `/packages` upload | `/packages` UI only |
| 5 | `core/skills/corvin_skills/` | a second full skill system (store, grader, versioning, learning loop, composition) | — | `packages.py`, for manifest validation |

Store 3 has **no tenant in its path**. It is the only route by which a skill
reaches the native engine, and it is shared across every tenant on the
install. Nothing has gone wrong yet because this install has one tenant; the
structure does not depend on that.

Store 4 is hardcoded to `Path.home() / ".corvin"` and ignores `CORVIN_HOME`,
so on this repo-rooted install it points somewhere the rest of the system
never looks.

### Four readers, four different rules

| Reader | Source | Selection rule |
|---|---|---|
| `skill_inject` (bridges, via `adapter.py`) | manifest | grade gate: `n_grades >= 1 AND mean_score > 0`, cap 5 |
| CEL `prompt_assembly` (console turns) | the turn's own payload | binds the body it just produced; registry is a side effect |
| native engine plugin loader | `skills/dyn/` mirror | everything present |
| `/skills` console route | directory walk over stores 1+2 | everything present |

`/skills` reports **148**, `/skill-creator/skills` reports **145**. The
difference is exactly store 2. `/skills` also labels store 1 as
`session-default` and store 2 as `user` — precisely inverted, since store 1
*is* what `MultiSkillRegistry._root_for("user")` resolves.

### Who is not reached at all

| Surface | Gets skills? |
|---|---|
| Console web-chat | **No** — `chat_runtime.py` never calls `skill_inject` |
| Messenger bridges | Yes — `adapter.py` calls `collect_active_skills` and `auto_grade_from_output` |
| ACS workers | **No** |
| TDE workers | **No** |
| Remote triggers | **No** |
| Package skills | **No** — never reach a registry |

### The grade deadlock

`registry.grade()` has exactly **one** production caller: the Skill-Creator's
bootstrap seed. `auto_grade_from_output` is called only from `adapter.py`.
So grades are produced only by messenger traffic, while the gate that
consumes them blocks everything ungraded. 144 of 145 skills sit at zero
grades.

For CEL skills this is harmless — CEL binds its bodies directly and never
consults the gate. For every other producer it is fatal: a skill that is
never injected can never earn the grade that would let it be injected.

### Two lifecycles in one namespace

|  | `cel_*` (144) | `assistant.*` (1) |
|---|---|---|
| Created | automatically, per turn | by the operator, deliberately |
| Nature | materialised turn context | reusable capability |
| Selected by | direct binding | grade gate |
| Cleaned up | **never** — `purge_session_skills` only clears `sessions/` | operator deletes |

They share a store, a listing and a badge column, and the UI presents them as
if they were the same kind of thing. They are not.

---

## 2. What "unified" has to mean — and what it must not

The tempting reading is *one store, one gate, one list*. That reading is
wrong, and it is worth saying why before building on it.

CEL skills are **turn caches**. Forcing them through a grade gate would not
improve them — CEL does not read the gate — and promoting 144 turn caches
into permanently injectable skills would bloat every prompt on the install.
A unification that erases the distinction makes the system worse.

The real defect is not that two kinds of skill exist. It is that:

* the same kind of skill is stored in five places and read by four different
  rules,
* the surfaces that should consume skills mostly do not, and
* the UI gives the operator no way to tell the kinds apart.

So: **unify the plumbing, keep the kinds distinct and visible.**

---

## 3. The proposal — one plane, three layers

```
        ┌──────────── producers ────────────┐
        │ Skill-Creator   CEL   manual   packages   MCP skill_create │
        └──────────────────┬────────────────┘
                           │  registry.create(origin=…, lifecycle=…)
                           ▼
        ┌──────────────────────────────────────────┐
        │  STORE   <tenant>/skill-forge/           │  ONE location
        │          manifest + <name>/SKILL.md      │  ONE writer API
        └──────────────────┬───────────────────────┘
                           │  resolve(surface, context) → [Skill]
                           ▼
        ┌──────────────────────────────────────────┐
        │  RESOLVER   one selection policy         │  classes, not one gate
        └──────────────────┬───────────────────────┘
                           │
     ┌────────┬────────────┼────────────┬───────────┐
     ▼        ▼            ▼            ▼           ▼
  console   bridges   ACS worker   TDE worker   native engine
   chat                                          (dyn/ mirror)
        └──────────── delivery adapters ──────────┘
```

### Layer 1 — Store: one location, one writer API

`<tenant>/skill-forge/` becomes the only store. Every producer writes through
`registry.create()`, which is what maintains the manifest, runs the
fail-closed linter, mirrors to the plugin slot and appends to the skill audit
chain. Writing a directory by hand stops being a supported path.

Two fields become first-class on every skill:

* **`origin`** — `skill-creator` · `cel` · `manual` · `package` · `mcp`.
  The data already exists as `created_by`; it is simply never surfaced.
* **`lifecycle`** — `durable` (survives until deleted) · `turn` (a cache CEL
  produced for one turn) · `session` (valid for one chat).

`lifecycle` is what makes cleanup possible at all: today CEL skills are
written with `user` scope and no expiry, and nothing ever removes them.

### Layer 2 — Resolver: one policy, several classes

One module answers *"which skills apply to this turn?"* for every surface.
It replaces four ad-hoc rules with one, and it replaces the single grade gate
with eligibility **classes**:

| Class | Eligible when | Covers |
|---|---|---|
| `pinned` | operator marked it always-on | quality-discipline skills (today a hardcoded tuple in `skill_inject`) |
| `graded` | `n_grades >= 1 AND mean_score > 0` | the current gate — earned skills |
| `bound` | named by this turn's context builder | CEL's direct binding, unchanged in behaviour |
| `probation` | created < N days ago, under a cap | **breaks the grade deadlock**: a new skill gets a bounded chance to be used and therefore graded |

`probation` is the load-bearing addition. Without it, the only way a skill
ever becomes eligible is a bootstrap seed someone remembered to write.

The resolver returns skills with a **budget** per surface (token cap, count
cap), so "usable everywhere" cannot mean "everything, everywhere".

### Layer 3 — Delivery adapters: one per surface, no local policy

| Surface | Mechanism | Status today |
|---|---|---|
| Console chat | system-prompt block via CEL's existing assembly | **new wiring** — currently absent |
| Bridges | `--append-system-prompt` (already built) | keep, re-point at resolver |
| ACS workers | worker prompt assembly | **new** |
| TDE workers | worker prompt assembly | **new** |
| Native engine | `skills/dyn/` mirror | **needs tenant scoping** |

No adapter carries its own selection logic. That rule is what keeps the
promise from decaying back into four rules.

### The plugin slot needs a tenant

`plugin_slot_dir()` resolves to a repo-global path with no tenant component.
Any multi-tenant install would leak one tenant's skills into another's turns
through the native engine. The mirror needs to be per-tenant, with the engine
invocation pointed at the caller's directory. This is a correctness and
compliance item (CLAUDE.md § Multi-tenant axis), not a nice-to-have.

### Packages become a producer, not an island

A package's skills are extracted and **registered like any other skill**,
with `origin=package` and the package id retained for uninstall. Uninstalling
the package deletes exactly those skills. Nothing else about the package
system changes; it stops being a store and becomes a producer.

Store 4's hardcoded `Path.home()` should move to `corvin_home()` in the same
pass, or packages remain invisible on any install that sets `CORVIN_HOME`.

### `core/skills/corvin_skills/` — decide, don't drift

A second complete skill system exists (store, grader, versioning, learning
loop, composition, multitenancy), reached in production only as a manifest
validator for packages. Two options, and the choice belongs to the
maintainer:

1. **Absorb** — adopt its grader/learning-loop as the resolver's scoring
   input and drop its store.
2. **Retire** — keep the manifest schema, delete the rest.

Leaving it undecided is the expensive option: it looks like the skill system
to a reader and is not one.

---

## 4. Migration — smallest safe order

Each step is independently shippable and independently reversible.

| # | Step | Risk | Unblocks |
|---|---|---|---|
| 1 | `/skills` reads the manifest instead of walking directories | low | kills the 3 ghosts and the inverted scope labels in one change |
| 2 | `skills_manual.py` writes through `registry.create()` into store 1 | low | manual skills become findable at all |
| 3 | Add `origin` + `lifecycle`, surface them in the UI | low | operator can finally tell the kinds apart |
| 4 | Extract the resolver from `skill_inject`; re-point bridges at it | medium | one policy exists |
| 5 | Wire console chat to the resolver | medium | **the single largest gain** — skills start working where most operators are |
| 6 | Per-tenant plugin slot | medium | closes the isolation gap |
| 7 | ACS + TDE worker adapters | medium | "in the worker engine" |
| 8 | Packages register their skills | medium | package skills become usable |
| 9 | `lifecycle=turn` cleanup for CEL | low | store stops growing without bound |
| 10 | Decide `corvin_skills` | — | removes a decoy |

Steps 1–3 are cleanup with almost no behavioural surface. Step 5 is where an
operator would first notice the system working.

---

## 5. What could go wrong

* **Prompt bloat.** "Usable everywhere" without budgets means every turn
  carries every skill. The per-surface budget in the resolver is not
  optional; it is the thing that makes the goal safe.
* **`probation` lets bad skills in.** It is bounded by a cap and a window,
  and a skill that gets used and graded badly falls out. The alternative —
  today's deadlock — lets *nothing* in.
* **Worker injection changes worker behaviour.** ACS/TDE workers currently
  run without skills; adding them changes measured behaviour. It belongs
  behind a flag with both states tested (CLAUDE.md § Feature flags).
* **The resolver becomes the new monolith.** It has one job — selection —
  and must not acquire rendering, grading or storage. If a fifth rule appears
  inside it, the unification has failed in a new place.

---

## 6. Open decisions for the maintainer

1. **CEL skills: persist or expire?** Are they durable artifacts or turn
   caches with a TTL? This determines step 9 and whether the 144 existing
   ones are migrated or purged.
2. **`corvin_skills`: absorb or retire?**
3. **Probation window and cap** — how much unproven skill is an operator
   willing to carry per turn?
4. **Worker skills behind a flag?** Recommended yes, defaulting off, per the
   ship-dark rule.

---

## 7. Related

- [[ADR-0405]] — Skill-Creator on the Claude Code engine; registry promotion,
  reachability contract, console lifecycle
- [[CONCEPT-SKILL-CREATOR]] — the generator that produces `assistant.*` skills
- `operator/context_engineering/stages/skillforge.py` — CEL skill binding.
  Its code cites ADR-0283, which is not present in `Corvin-ADR/decisions/`
  on this checkout (that directory starts at ADR-0321); the code comments are
  the current record.
- `core/console/corvin_console/routes/packages.py` — package system, cites
  ADR-0268, likewise not present in this checkout.
- CLAUDE.md § Multi-tenant axis — the tenant rule the plugin slot breaks

---

## Operator Notes

*(append-only, human-authored — AI amendments never edit or remove anything
under this heading)*
