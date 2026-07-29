# Quality Disciplines in CorvinOS

CorvinOS integrates structural quality gates as **runnable skills** — prompt-injected wisdom that guides implementation decisions in real-time. These are not linters or CI checks; they are *live reasoning tools* embedded in every Claude Code session.

---

## ADR Gate — Architectural Decision Records

**Tier:** Core quality discipline — standard in all default personas (assistant, coder, browser, research).

**When to apply:** After every non-trivial implementation task, immediately before declaring "done."

**High bar:** Default answer is **NO ADR needed.** Most tasks produce no ADR — that is correct and expected.

### What is an ADR?

An **Architectural Decision Record** documents a design choice where:
1. A real alternative existed (chose A over B, not just "the obvious way")
2. The choice constrains future code non-trivially
3. At least one structural trigger applies (see below)

ADRs live in `../Corvin-ADR/decisions/` — separate from the CorvinOS codebase. This separation keeps them accessible, version-controlled, and divorced from code cleanup refactors.

### When to write an ADR

**BOTH conditions must hold:**

**Condition A — A real design choice was made:**
- Multiple genuine alternatives existed
- You chose one over the others
- The choice constrains future implementation

**Condition B — At least one structural trigger:**
1. **Protocol / wire-format / schema change:** New audit field, MCP tool contract, CLI flag shape, REST API endpoint
2. **Security or compliance mechanism:** Anything touching L10 (path-gate), L16 (audit chain), L34 (data classification), L35 (egress lockdown), GDPR Art. 17 (erasure), EU AI Act Art. 50 (disclosure)
3. **Irreversible or hard-to-reverse default:** fail-open vs fail-closed, deny vs allow, opt-in vs opt-out, one-way data migrations
4. **Cross-repo binding:** Decision touches ≥2 repositories (e.g. CorvinOS + Adapter + Cloud)
5. **New layer-level mechanism:** New Layer contract, plugin boundary, cross-layer invariant

### When to skip an ADR

**Skip when:**
- Bug fix with only one obvious solution (no real alternative)
- Pure refactor where behaviour is unchanged
- Config value tuning (thresholds, timeouts, feature-flag toggles)
- Test addition or fix
- Documentation, comments, copy changes
- UI tweaks, renaming, small cleanups
- Anything trivially reversible without data migration or cross-repo coordination

**When skipping, name the reason in one sentence** — e.g., "No ADR — bug fix, only obvious solution" or "No ADR — pure refactor, behaviour unchanged."

Never skip silently. The one-sentence reason is part of the record.

### Three-level analysis (mandatory)

Every ADR decision (and every skip decision) requires explicit analysis at three levels. Missing any level leaves your reasoning incomplete; future readers will puzzle over the missing intent and drift away from the invariants.

**Conceptual level:**
- What is the core principle or idea?
- What mental model does this create for future contributors?
- What rule of thumb or invariant should they carry in their head?
- Example: "Audit events are immutable; once written, never modified or deleted."

**Structural level:**
- How does this constrain the architecture?
- Which layers, interfaces, protocols, or contracts are shaped by this?
- What cannot be changed without revisiting this decision?
- Example: "Audit chain uses SHA-256 hash-linking; changing to MD5 breaks the tamper-evidence guarantee."

**Implementation level:**
- What specific code, config, runtime behaviour, file layout, schema shape is locked in?
- What does a developer need to know concretely to respect this decision?
- What tests prove the invariant holds?
- Example: "Audit events written to `audit.jsonl` as JSONL, not JSON array. Each line is `sha256(prev_hash + event)` in the `_chain_link` field. Sealing rotates the file but preserves the hash chain via `rotation_link` event."

**All three levels MUST appear in the ADR's Context and Decision sections.** If a level is empty (rare), state that explicitly: *"Implementation level: no specific shape is locked in; the structural constraint above is sufficient."*

### How to write an ADR

**1. Find the next number:**

```bash
ls ../Corvin-ADR/decisions/ | grep -E '^[0-9]{4}' | sort | tail -1
```

Add 1, zero-pad to 4 digits (e.g., `0068`).

**2. Write the ADR file:**

Path: `../Corvin-ADR/decisions/XXXX-short-kebab-title.md`

Template:

```markdown
# ADR-XXXX — [Title]

**Status:** Accepted
**Date:** YYYY-MM-DD
**Deciders:** shumway

## Context

[What situation, constraint, or requirement forced a decision here?
Include the three-level analysis of the *problem*, not the solution yet.]

## Decision

[What was decided. Direct statement.
Include the three-level analysis of the *solution* — conceptual/structural/implementation.
If a level is empty, state it explicitly.]

## Consequences

### Positive
- [What becomes easier, safer, more explicit, or more maintainable?]

### Negative / Trade-offs
- [What becomes harder, more constrained, or requires coordination?]

### Alternatives considered
- [What else was evaluated and why it was rejected? Include the three-level impact for each alternative.]
```

**3. Commit in the Corvin-ADR repo:**

```bash
cd ../Corvin-ADR
git add decisions/XXXX-short-kebab-title.md
git commit -m "adr: add ADR-XXXX — [title]"
git push
```

**4. Reference in CorvinOS** (optional, only if load-bearing):

If the ADR describes a structural constraint that touches CorvinOS code, add a reference at the end of the relevant CLAUDE.md section:

```markdown
→ ADR: `Corvin-ADR: decisions/XXXX-short-kebab-title.md`
```

**5. Write E2E tests for new invariants:**

After writing an ADR, ask: *"If this mechanism broke or were accidentally reverted, what would fail?"*

- Real subprocess, real filesystem, real bwrap — **no mocks for security/forge/policy/audit**
- Test filename: `test_adr_XXXX_mechanism_name.py`
- One test per structural invariant from the ADR's "Must NOT do" list
- Register in test suite: `/operator/bridges/run-all-tests.sh`
- Commit together with implementation, not as a follow-up

### Examples: When to write ADRs

**YES, write an ADR:**
- Adding a new audit event type to the hash chain (structural trigger: audit schema)
- Choosing between Redis vs in-memory cache for session state (structural trigger: persistence default)
- Switching from JWT to session cookies (structural trigger: security mechanism, cross-repo binding)
- Adding a new Layer (e.g., Layer 39 for observability) (structural trigger: layer-level mechanism)
- Implementing GDPR erasure (structural trigger: compliance mechanism)

**NO, skip (name the reason):**
- Fixing a bug in error handling → "No ADR — bug fix, one obvious solution"
- Renaming a function → "No ADR — pure refactor, behaviour unchanged"
- Adding a test for existing behaviour → "No ADR — test addition only"
- Changing a timeout value from 30s to 60s → "No ADR — config tuning"
- Improving the speed of an existing query → "No ADR — pure refactor, API unchanged"

### Recent ADRs

| Number | Title | Scope |
|---|---|---|
| ADR-0069 | EAOS — Engine-Agnostic OS Shell | Layer 22: WorkerEngine protocol, TEB, ECI, FCB, SkillCompiler |
| ADR-0071 | CopilotCliEngine | Layer 22: 5th WorkerEngine (GitHub Copilot CLI) |

---

## E2E Wiring Proof — reachability + functional proof

**Tier:** Core quality discipline — standard in all default personas, sibling gate to ADR Gate.

**When to apply:** Immediately before declaring "done" on any code generation that adds a new entry point — function, endpoint, route, CLI command, UI component, plugin, hook, or bridge handler.

**The bar:** New code is unreachable until proven otherwise. A unit test proves a function returns the right value *when called* — it says nothing about whether anything in the running system *calls* it.

**Why this exists:** CorvinOS has shipped structural instances of exactly this failure — plugin types that register successfully but are never invoked by any real code path, and an auth invariant that is unit-tested but reachable from zero live call sites. Both were "tested," both were dead.

### Two-phase gate

**Phase 1 — Reachability proof (cheap, first):** trace the call graph for the new symbol/route/component. Require at least one call site outside the definition file AND outside test files, traceable to a real trigger (routing table, CLI registration, UI render tree, plugin registry, cron config, message-bus subscription). Zero call sites found → **block**, dispatch `root-cause-by-layer` to find why the code is orphaned. Do not write a test for code nothing calls.

**Phase 2 — Generate or extend one E2E test (only after Phase 1 passes):** the test must exercise the new code through its **real, user-facing entry point** — an actual HTTP request, an actual CLI subprocess, an actual browser interaction, an actual plugin-registry dispatch, an actual bridge message, an actual MCP tool call. **Hard anti-gaming rule:** a test that imports the target directly and calls it, skipping the transport/interface boundary, is a unit test wearing an E2E label and does NOT satisfy this gate.

**Phase 3 — Execute and capture evidence:** run the test, capture the real exit code/output, include it as evidence. A bare "I added a test" claim without an execution transcript does not close the loop.

**Infeasibility exception:** when the real entry point genuinely can't be driven end-to-end (hardware dependency, unavailable external system), name the reason explicitly instead of skipping silently. Phase 1 still applies unconditionally.

Full rubric: `/operator/bundle/skills/ldd/e2e-wiring-proof/SKILL.md`

---

## Complementary Disciplines

### docs-as-definition-of-done

**Tier:** Cross-cutting — applies to every feature, config change, CLI flag, default value, or error message.

**Rule:** Before you declare "done," every code change that alters **observable behaviour** must be mirrored in docs **in the same commit.**

Examples:
- New CLI flag → document it in the CLI reference
- New config field → update `tenant.corvin.yaml` reference docs
- Error message changes → update troubleshooting guide
- Protocol version bump → update wire-format docs and JSON examples

**Must NOT do:** Defer doc updates to follow-up commits, PRs, or "cleanup later."

### e2e-driven-iteration

**Tier:** Applied to failing tests, incident response, observable behaviour changes.

**Cycle:** End-to-end test → observe loss → 5-why analysis → fix → E2E retest → done.

Never stop at "tests pass" — verify the real system works as intended.

---

## Integration with LDD

Quality disciplines are **LDD Layer 14 (LDD-Toggle-System)**.

Each persona declares which disciplines are active via `ldd_preset`:
- `"off"` (default) — disciplines available but not auto-recommended
- `"audit"` — core disciplines only (ADR Gate, docs-as-definition-of-done)
- `"all"` — all 12 LDD layers enabled

Standard personas (assistant, coder, browser, research) use `"off"` but have `skill_forge_enabled: true` — disciplines are **available in the prompt**, not enforced. This respects the philosophy: *guidance available, judgment yours.*

---

## How Quality Disciplines Are Delivered

**Mechanism:** Skill-Forge (Layer 7) — Markdown skills prompt-injected into Claude Code sessions.

**Activation:** Personas with `skill_forge_enabled: true` receive bundled skills:
- Located at: `/operator/bundle/skills/ldd/<name>/SKILL.md`
- Injected as Markdown into the system prompt
- Appear as "available tools" in your session
- Can be invoked by name or discovered dynamically

**Visibility:**
- You see the skill name in the available-tools list
- When relevant, the skill is highlighted or suggested
- You can always scroll up and re-read the full rubric mid-task

---

## Must NOT Do

- Weaken the ADR Gate (auto-skip security/compliance mechanisms without explicit written justification)
- Leave a skip implicit (the one-sentence reason is mandatory)
- Declare "done" on a structural change without running the gate
- Write ADR content into the CorvinOS repo (ADRs live in Corvin-ADR only)
- Defer docs to a follow-up commit when docs-as-definition-of-done applies
- Add E2E tests after committing (tests and implementation same commit)
- Mix code changes and ADR changes (separate PRs in separate repos)
- Declare a code-generation task "done" without running E2E Wiring Proof when it adds a new entry point
- Treat "unit tests pass" as equivalent to "this is reachable and works end-to-end"
- Write an E2E test that bypasses the real transport/interface boundary and label it as satisfying the wiring gate

---

## Related Concepts

- **Loss-Driven Development (LDD):** Meta-methodology; quality disciplines are Layer 14 within LDD
- **Audit Chain (L16):** Security foundation; ADRs often involve audit changes
- **Path-Gate (L10):** Write protection; structural changes to forge/policy require ADRs
- **SkillForge (L7):** Delivery mechanism; disciplines are bundled as skills
