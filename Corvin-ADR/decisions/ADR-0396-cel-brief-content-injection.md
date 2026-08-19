---
id: ADR-0396
status: proposed
supersedes: []
depends_on: [ADR-0275, ADR-0395]
related: [ADR-0394]
commits: []
paths:
  - operator/context_engineering/pipeline.py
  - core/console/corvin_console/chat_runtime.py
  - operator/bridges/shared/adapter.py
  - core/console/corvin_core/feature_flags.py
docs:
  - docs/concepts/cel-token-savings-paper-concept.md
---

# ADR-0396 — CEL brief injects memory CONTENT, not just titles (flag `cel_brief_includes_content`)

**Date:** 2026-08-19 · **Deciders:** shumway, Claude (Opus 4.8) · **Status:** proposed

## Context

The deterministic CEL brief (`render_brief_to_text`, `operator/context_engineering/pipeline.py`)
rendered each relevant memory as its **title only** — e.g. `- bench-cel-analytics-port` — never the
file body. `content_preview` on a memory match is ~50 chars of frontmatter and carries no fact.

EXP-001 (`~/.corvin/tenants/_default/experiments/exp-001-cel-token-savings/`) measured what this
means, tool-disabled (`claude -p --disallowedTools "*"`, so the agent cannot self-retrieve off disk
and CEL is the only context channel):

| arm | injected | correctness (n=3, 6 memory-grounded tasks) |
|---|---|---|
| none | nothing | 0.00 |
| cel (titles) | the real deterministic brief | **0.00** |
| cel_content | brief with bodies (this ADR) | **0.833** |
| oracle | raw memory body | 1.00 |

**Conceptual level.** CEL's value on context-dependent questions is *correctness*, not token
savings. A pointer (title) is only useful to an agent that can *pull* the pointed-to file; when the
turn cannot pull (tools disabled, air-gapped ACS worker, or knowledge that lives only in the store),
a title carries zero answerable content. CEL must therefore *push the content*, not a pointer.

**Structural level.** The brief is the single push channel into a turn. This ADR makes the memory
section of that channel carry bodies, behind a flag, without changing any other section or the
byte-stable-cache contract of ADR-0395 (the brief still rides the user turn when `cel_cache_stable`
is on).

**Implementation level.** `render_brief_to_text(brief, *, include_content=False)` — keyword-only,
default False so every existing positional caller is byte-identical (verified). When True, a
`_memory_body` helper reads the match's `source_file`, strips YAML frontmatter and any HTML
disclaimer comment, caps to 800 chars, and renders it after the title. Wired into the two
deterministic-brief callers (console `chat_runtime.py`, bridge `adapter.py`) gated by a new
feature flag `cel_brief_includes_content` (default off, ship-dark, alpha).

## Decision

Add `cel_brief_includes_content` (default **false**). When on, the deterministic CEL brief injects
each relevant memory's body (capped) instead of only its title. Default off = today's behaviour.

## Consequences

- **Positive:** unlocks CEL's latent correctness value (0.00 → 0.833 tool-disabled) with a one-
  function, default-off change; makes CEL useful to tool-less / isolated turns (e.g. ACS workers).
- **Cost:** bodies are larger than titles, so the brief grows. This is the exact cost the
  token-savings benchmark exists to measure — **re-run it before flipping the flag on** for a given
  workload; the correctness lift must justify the added input tokens (cheap under ADR-0395's
  cache-stable user-turn placement, but non-zero).
- **Residual:** one of six tasks (a *name*-type answer) still failed even with content present — a
  brief-*framing* effect ("Relevant past memory:" framing vs an assertive statement), not retrieval
  and not this ADR. Tracked as a follow-up (brief framing / selective injection, ADR-0394).
- **Compliance:** memory bodies already pass the same PII/house-rules path as titles; no new egress.

## Alternatives considered

- **Inject `content_preview`** — rejected: it is ~50 chars of frontmatter, no fact.
- **Always-on (no flag)** — rejected: violates ship-dark; the cost impact must be opt-in per
  workload after measurement.
- **Rely on agentic pull** — rejected as the *only* strategy: it fails exactly where CEL should
  matter most (tool-less / isolated turns), and it made the earlier agentic measurement
  uninterpretable (EXP-001 Anomaly B).
