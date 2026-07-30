# Plugin-Builder V2 — Concept

**Status:** Draft / Proposed (concept only, no implementation yet)
**Date:** 2026-07-30
**Builds on:** ADR-0253 (Assisted Plugin Development, Implemented), ADR-0244–0249
(Plugin-Builder series, all Implemented), `docs/implementation/PLUGIN_SYSTEM_ACTIVATION_PLAN.md`
**Scope:** `core/plugins/plugin_builder/` (interview, classifier, generators, session
store) plus the `/plugin-builder` console command and the bridge adapter integration.
**Formalized as:** ADR-0262 (idea-first interview, checkpoint, generated E2E tests)
and ADR-0263 (`--ideas` co-ideation mode) — see `Corvin-ADR/decisions/`.

---

## 1. Why this document exists

The current Plugin-Builder (ADR-0253) is implemented and works: a 4-phase
interview (Problem → Auto-Classification → Dependencies → Review/Confirm)
collects a fixed set of questions, generates four markdown documents (Idea,
Architecture, ADR, Build Plan), and on `confirm` writes a code scaffold to
disk. It never installs or activates anything ("emits, never loads" per
ADR-0244).

The operator has asked for a rebuild with six changes:

1. Stop front-loading narrow, specific questions — stay at the idea level.
2. At the end, the builder should implement everything, not just hand over
   documents.
3. After the markdown documents are generated, discuss the interim result
   with the user (including a voice summary) before moving to
   implementation.
4. The builder should ask its questions in the user's own language.
5. Think end-to-end: idea → … → implementation + test.
6. Generated plugins should ship with auto-generated E2E tests that cover
   edge cases *and* prove the wiring (the real call-site), not just unit
   logic.

This document is the concept for that rebuild — no code changes yet.

---

## 2. Dialectical check on the naive reading

Read literally, points 1–6 would mean: replace the structured interview with
free-form chat, and have the builder auto-write **and auto-wire** a running
plugin at the end. That reading does not survive contact with the existing
design constraints, for three concrete reasons:

- **Classification needs signal.** `classifier.py` picks one of 11 plugin
  types + a Tier (A/B/C) + risk flags from the interview answers. The
  Dependencies-phase questions (`external_libraries`, `requires_auth`,
  `requires_network_egress`, `egress_hosts`, `platform_constraints`) are not
  bureaucratic filler — they are the exact inputs ADR-0247's Validation Gate
  checks against, and `requires_network_egress`/`egress_hosts` map directly
  onto L35 (Network Egress Lockdown). Dropping them for a purely
  idea-level chat would silently degrade classification confidence and
  validation coverage.
- **"Implement everything" collides with a load-bearing boundary.**
  ADR-0244 draws the emits-never-loads line on purpose, and
  `docs/implementation/PLUGIN_SYSTEM_ACTIVATION_PLAN.md` Stage 6 (`install` +
  trust anchor) is explicitly blocked today on an Ed25519 key-custody
  decision the maintainer hasn't made. A builder that auto-installs/activates
  generated code would either bypass that unresolved trust gate or simply
  can't ship until an unrelated blocker resolves. CLAUDE.md's plugin-perimeter
  doctrine is explicit that an in-process plugin is part of the process —
  auto-running LLM-generated code with no human checkpoint is the same
  failure class the doctrine warns about, applied to code instead of
  identity.
- **Generated wiring tests need a live call-site to test against.** Per the
  Activation Plan, 3 of 11 plugin types (`compute_engine`, `worker_engine`,
  `bridge_channel`) have no provider module and no `PluginContext` handle —
  their extension points are structurally dead. An E2E test that "proves the
  wiring" for one of these would either false-pass against nothing or hang.

**Synthesis:** keep the six changes, but scope them to what's real:

- Idea-level conversation *first*, with the classifier extracting as much as
  it can from free text; only the fields that are safety/compliance-relevant
  and still unresolved get a short, explicit confirmation question — framed
  as "I assume X — correct?", not a form field.
- "Implement everything" = scaffold + auto-generated E2E tests, ready to run
  and ready for a human to `corvin plugin install` once that command exists.
  No auto-install, no auto-activation — that boundary stays exactly where
  ADR-0244 put it.
- Generated wiring tests check the Extension-Surface Map (ADR-0245) for a
  live call-site before asserting against it; for the 3 dead types the
  generated test file states explicitly *"wiring test skipped — call-site
  not yet registered, see PLUGIN_SYSTEM_ACTIVATION_PLAN.md Stage 4"* instead
  of faking a pass.
- New interim review checkpoint (voice + text) between doc generation and
  code generation, gating on explicit go-ahead — purely additive, low risk.
- Session-pinned language detection, carried through docs and voice summary.

---

## 3. Target flow

```
User opens /plugin-builder (console or bridge)
        │
        ▼
[Phase 1 — Idea]  1–3 open questions, free text, in the user's language.
   "Tell me about the idea." / "What problem does it solve for whom?"
   No enumerated field list is shown to the user.
        │
        ▼
[Auto-extract]  classifier.classify() runs against the free text and tries
   to fill every field the current Dependencies phase asks for today
   (plugin type signal, external libs, auth, egress) via structured
   extraction from the same conversation, not a second interview.
        │
        ▼
[Phase 2 — Targeted confirmation]  ONLY for fields that are (a) still
   unresolved AND (b) safety/compliance-relevant (egress hosts, auth,
   external deps, platform constraints). Framed as confirm/adjust, not a
   blank field. If everything was inferable, this phase is skipped
   entirely and the user sees zero additional questions.
        │
        ▼
[Generate docs]  Idea Doc, Architecture Concept, (plugin-local) ADR, Build
   Plan — unchanged generators, reused as-is.
        │
        ▼
[Zwischenstand checkpoint — NEW]  Present a text summary + voice summary
   (reusing core/console/corvin_console/voice_summary_smart.py) of the
   docs and the classification (type, tier, risk flags — carried verbatim,
   never dropped/summarized away, per the existing CRITICAL-warnings
   lesson). Wait for explicit go/no-go/adjust. Nothing beyond the docs is
   written to disk before go-ahead.
        │
        ▼ (go-ahead)
[Generate scaffold]  unchanged scaffold.py / corvin plugin new reuse.
        │
        ▼
[Generate E2E tests — NEW]  per plugin: (a) logic/edge-case tests against
   the scaffolded module directly, (b) ONE wiring test that drives the
   real extension-point call-site from the Surface Map if it is live, else
   an explicit skip-with-reason.
        │
        ▼
[Done]  Scaffold + tests land under "Scaffolded by Plugin-Builder"
   (unchanged, still not installed/activated — Stage 6 still gates that).
```

---

## 4. Concrete changes by module

| Module | Change |
|---|---|
| `interview.py` | Collapse Phase 1 (Problem) + Phase 3 (Dependencies) into one open-ended phase plus a *conditional* confirmation phase generated from unresolved fields, not a fixed question list. `InterviewPhase` gains an explicit `CHECKPOINT` phase between `REVIEW` (doc generation) and `DONE` (scaffold + tests). |
| `classifier.py` | Add a free-text field-extraction pass (idea text → best-effort `DependencySpec`/`Constraints`) that runs before the confirmation phase decides what's still missing. Confidence scoring already exists — reuse it to decide which fields need explicit confirmation. |
| New: `language.py` (or extend `models.py`) | Detect language once at session start (bridge-reported locale if available, else inferred from the first free-text message) and pin it for the whole session — including doc generation prose and the checkpoint summary. |
| New: `checkpoint.py` | Owns the Zwischenstand step: builds the text+voice summary from the four generated docs and the classification, calls into `voice_summary_smart.py`, and blocks `DONE`-phase transition on explicit confirmation. |
| `generators/` | Add `generators/e2e_tests.py`: given the classification + Surface Map lookup, emit edge-case tests for the scaffolded module and one wiring test (or a documented skip) per plugin. |
| `session_store.py` / `turn.py` | Extend the phase state machine for the new `CHECKPOINT` phase; no transport changes needed (already transport-agnostic). |
| `feature_flags.py` | New sub-flags (see §5), default off, both states tested per the Feature Flags doctrine. |

No changes are needed to `scaffold.py`'s actual file-writing, to ADR-0244's
emits-never-loads boundary, or to Stage 6 of the Activation Plan — this
rebuild deliberately does not touch install/activation.

---

## 5. Feature flags

This rebuild is layered on top of the existing, already-off
`plugin_builder_enabled` flag. Per CLAUDE.md's flag doctrine, each
behavioral change gets its own flag, default off, degrading quietly to
today's 4-phase flow when off:

- `plugin_builder_idea_first_interview` — swaps the fixed question list for
  the idea-first + conditional-confirmation flow. Off → today's 4-phase
  interview, unchanged.
- `plugin_builder_checkpoint_review` — inserts the Zwischenstand
  voice+text checkpoint between doc generation and scaffold generation. Off
  → `confirm` still writes the scaffold directly, as today.
- `plugin_builder_generate_e2e_tests` — generates edge-case + wiring tests
  alongside the scaffold. Off → scaffold only, as today.

All three can ship and be tested independently; none is compliance/security
infrastructure, so none is exempt from the off-by-default rule.

---

## 5a. `--ideas` mode — AI-moderated co-ideation

`/plugin-builder --ideas` opens a distinct front door: instead of the user
arriving with an idea, CorvinOS and the user develop one together in a
moderated, spoken back-and-forth where both sides contribute.

**Dialectical check.** Read literally — "the AI gives the user new ideas in
a conversation" — this risks three concrete failures: (a) an AI that
free-associates plugin ideas with no grounding produces exactly the "false
confidence" outcome ADR-0253 already lists as a known risk of generated
artifacts; (b) an open-ended dialogue with no convergence rule can run
indefinitely or end arbitrarily; (c) "in einem Gespräch" (voice throughout,
not just a final summary) is a materially bigger surface than the
checkpoint's periodic `voice_summary_smart` recap and, built bespoke, would
duplicate the turn-taking engine Voice Mode 2.0 (ADR-0194) already owns —
plus voice bugs in this codebase have a documented history of being silent
until proven live (`voice-mode-2-adr0194` memory).

**Design (synthesis):**

- **Grounded proposals only.** Every idea the moderator proposes must cite
  which of a fixed set of inspectable sources it came from: a gap in the
  Extension-Surface Map (ADR-0245), one of the 3 structurally dead plugin
  types from the Activation Plan (Stage 4), a sparse category in
  `Corvin-Marketplace/`, or something the user said earlier in *this*
  conversation. No ungrounded "wildcard" suggestions.
- **Bounded structure, not open chat.** Fixed round shape: moderator offers
  1–2 grounded candidates → user reacts/extends → moderator synthesizes →
  repeat for a capped number of rounds, ending in an explicit "let's take
  idea X forward" moment (user-initiated or moderator-asked), mirroring the
  existing Review/Confirm pattern rather than inventing a new one.
- **Reuse Voice Mode 2.0, don't rebuild it.** The moderated dialogue's
  actual voice turn-taking runs through the existing Voice Mode engine;
  `plugin_builder` drives it through the same transport-agnostic
  `InterviewSession.ask()`/`.answer()` contract it already exposes, with an
  added "moderator proposes" input alongside "user answers." No parallel
  TTS/STT loop gets built inside `plugin_builder`.
- **Converges into the existing pipeline, never bypasses it.** Reaching
  consensus does not write anything to disk. It hands the converged idea
  text into the same Phase-1/auto-extract entry point described in §3 —
  ideas-mode is a new front door onto the unchanged downstream pipeline
  (classification → targeted confirmation → docs → checkpoint → scaffold →
  tests), not a shortcut around any of it.
- **Own flag, own explicit opt-in.** `plugin_builder_ideas_mode`, default
  off, and only reachable via the explicit `--ideas` argument even when the
  base `plugin_builder_enabled` flag is on — brainstorming mode is a
  deliberate choice, not a default variant of opening the builder.

---

## 6. Risks / open questions

- **Voice-summary completeness.** The checkpoint summary must carry risk
  flags and low-confidence classification warnings verbatim, not
  paraphrased away — this project has shipped and fixed exactly this bug
  once before (voice summaries silently dropping CRITICAL content). The
  checkpoint implementation must reuse the existing verbatim-warning
  pattern, not re-derive it.
- **Language source of truth.** Bridges may or may not report a reliable
  locale; inferring from free text is a fallback, not a first choice, and
  needs to fail toward a sane default (English) rather than guessing wrong
  mid－session.
- **Extraction accuracy.** Best-effort field extraction from free text is a
  new failure surface — a wrong inferred `egress_hosts` that the user never
  explicitly confirmed is worse than today's explicit-question flow for
  exactly the fields that gate the Validation Gate. The confidence threshold
  for "skip the confirmation question" must be conservative, not tuned for
  fewer questions.
- **Dead call-sites.** The generated wiring-test skip message must point at
  the real, current Activation Plan stage — if Stage 4 progresses and one of
  the 3 dead types gets wired later, the skip logic needs to pick that up
  (read the Surface Map / registry live, not a hardcoded list of 3 names).
- **ADR gate.** Per CLAUDE.md's ADR Gate, this rebuild likely needs its own
  ADR before implementation starts (structural trigger: changes a
  documented, adversarially-reviewed boundary's *surrounding UX*, even
  though it doesn't move the emits-never-loads line itself; also a new
  layer-level contract for generated E2E tests). Recommend writing it once
  this concept is confirmed, not before.

---

## 7. Explicitly out of scope

- Auto-install / auto-activation of generated plugins (Stage 6 stays
  blocked; unrelated to this rebuild).
- Building the `compute_engine` / `worker_engine` / `bridge_channel`
  provider modules themselves (Activation Plan Stage 4's job, not the
  Builder's).
- A bespoke web UI for the interview (ADR-0253 already ruled this out; the
  transport-agnostic session object is preserved unchanged).
- A bespoke voice/turn-taking engine for `--ideas` mode (reuses Voice Mode
  2.0 / ADR-0194, see §5a).
- Ungrounded AI idea generation in `--ideas` mode — every proposal must cite
  a real, inspectable source (see §5a).
