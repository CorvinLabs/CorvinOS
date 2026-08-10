# Programmable Context Brain — Implementation Plan

Realises CONCEPT-0006 via ADR-0280..0285. Every phase ships behind the existing
`vibe_engineering` flag (dark by default) and keeps the load-bearing invariants:
additive-to-context-never-to-authority (ADR-0277), reach boundary (ADR-0279), CE
degrades-never-blocks (ADR-0276), content-free audit (ADR-0278).

**Build status:** planning. Nothing built yet. Order is strict: P-A is the seam
every later phase composes on.

## ⚠ Revised after adversarial review R1 (2026-08-10)

Three critics found the plan below assumed today's side-effect-free / text-only /
in-process-trusted pipeline. The structural corrections (full detail: CONCEPT-0006
§8 + each ADR's R1 amendment) override the phase text where they conflict:

- **Async, dependency-ordered runner (P-A).** `build_brief` becomes async; blocking
  stages run via `asyncio.to_thread` (a sync `claude -p` in `stream_turn` would block
  the console event loop). Stages declare `requires` + `effect{pure,egress,forge}` +
  `trust`; the runner topo-sorts, memory is the non-removable root. `resolve_pipeline`
  returns `list[StageSpec]` (id + per-stage config), not `list[str]`.
- **Gate-split (P-A/C/D).** `build_context_pre_gate()` (pure stages, today's
  position) + `build_context_post_gate()` (egress/forge stages, only on a spawn the
  L44/L34/L35 gates already approved). Fixes the CRITICAL: an LLM call / forge must
  not fire for a task the gates would refuse.
- **`bind ≠ authorise` (P-B).** Re-validate bound tools against the persona tool
  policy after the merge (allowed_tools IS the authority boundary; L34/L44/L35 don't
  inspect it). Skills ≠ tools. Pass the bundle OBJECT (not rendered text) to all
  THREE consumers (chat_runtime, adapter, acs_runtime); the default
  `--dangerously-skip-permissions` path makes `mcp_config` the load-bearing sub-point.
  A2A/ACS-remote strip bindings by construction (ADR-0279).
- **LLM egress compliance (P-C).** The synthesis call is egress → same L34/L35 as a
  spawn; `egress=none` (Hermes/EU) ⇒ deterministic fallback. Own fallback internal.
- **Forge safety + no in-process API (P-D).** Same-turn = template impls only;
  LLM-authored impls behind a default-off sub-flag + AST allowlist check. First
  extract an in-process build API from `forge/runner.py` (don't shell the MCP server).
- **Community isolation (P-F).** No mechanism exists → until a subprocess sandbox is
  built, only `origin=builtin` runs in-process; others refused + audited.
- **Pre-existing bug to fix first:** the live Decision-Record write is best-effort
  (`chat_runtime.py` `except: pass`), contradicting ADR-0278's durability claim.
  Resolve the ADR-0276-vs-0278 tension (surface the audit-write failure to the L16
  chain, keep the turn running) before adding LLM/forge metadata to that record.

### Revised again after review R2 (2026-08-10) — the CRITICAL that R1 only moved

R2 showed the gate-split moved the bypass to the output side. Binding decisions:

- **TWO gate passes (P-A, load-bearing).** L44/L34/L35 run on the raw task (before
  egress/forge stages) AND on the **final payload** (synthesised prompt + bound
  tools) right before the worker spawn. The spawn the worker gets is always what
  Gate-2 inspected. This is the fix for the R2 CRITICAL — build it in P-A, before any
  post-gate stage exists.
- **P-0 (do first): audit-durability.** Fix the live best-effort `except: pass` so a
  Decision-Record write failure surfaces to the L16 chain without blocking the turn.
- **Async is scoped:** post-gate stages async on the console path first; bridge/ACS
  follow. No signature cascade forced up front.
- **Bind is class-based (P-B):** a stage binds a tool from a capability class the
  persona already allows (forge_enabled ⇒ mcp__forge__*); the Forge sandbox is the
  real guard for forged tools.
- **NEW phase P-G — community-stage subprocess sandbox.** Community stages leave P-F
  (which is first-party only). P-G designs the serialized-bundle bwrap sandbox; until
  it ships, only `origin=builtin` runs in-process and the palette is builtin-only.
- **One source of truth:** RichTaskBrief is authoritative; scratch/text_sections are
  derived, not dual-written. 0280 ships the scratch key table. Editor PUT validates
  the requires-DAG (acyclic + all requires satisfied), not just root+non-empty.

**Order now:** P-0 (audit durability) → P-A (contract + async runner + **two-gate** +
requires-DAG + scratch table) → P-B → P-C → P-D → P-E → P-F (first-party) → P-G
(community sandbox). Nothing ships that runs an LLM/forge stage before the two-gate
model exists.

---

## P-A — ContextStage contract + config-driven pipeline (ADR-0280)

**New files** `operator/context_engineering/stages/`:
- `base.py` — `ContextBundle` (dataclass), `StageCtx` (tenant, session, budget
  handle), `StageTelemetry` (status, confidence_tier, duration_ms, sources, notes),
  `ContextStage` Protocol (`run(bundle, ctx) -> (bundle, telemetry)`).
- `registry.py` — `register_stage(id, factory)`, `get_stage(id)`, `known_ids()`.
  In-process dict keyed by id; first-party stages self-register at import.
- `memory.py, graph.py, skill.py, approach.py, blocker.py` — the five current
  stages re-expressed as `ContextStage`s. Each WRAPS today's logic from
  `pipeline.py` (move, don't rewrite) so behaviour is preserved.
- `config.py` — `resolve_pipeline(tenant_id) -> list[str]`. Reads
  `spec.context_engineering.pipeline` (mtime-cached like feature_flags); absent →
  `DEFAULT_PIPELINE`. Unknown id → dropped + `cel.stage_unknown` audit.

**Changed** `pipeline.py::build_brief`: becomes the runner — resolve pipeline →
for each stage id, `get_stage(id).run(bundle, ctx)` in a per-stage try/except
(record failed, never break), accumulate telemetry into `trace`. License meter
stays ONE call at the top (ADR-0276). `render_brief_to_text` reads
`bundle.text_sections` (+ `synthesised_prompt` when set).

**Tests:** each stage unit-tested in isolation; runner runs a custom pipeline
order; unknown-id dropped+audited; default pipeline == today's five stages;
behaviour parity vs current `build_brief` on a fixed task (golden brief).

**Risk:** the refactor must be behaviour-preserving. Gate: a parity test asserting
the default-config brief byte-matches the pre-refactor brief for sample tasks.

---

## P-B — ContextBundle dual channel (ADR-0281)

**Changed** `base.py::ContextBundle`: add `tools_to_bind: list[ToolRef]`,
`skills_to_bind: list[SkillRef]` (bounded; a per-turn cap constant).
**Changed** the boundary consumers — `chat_runtime._build_args` and
`adapter._resolve_spawn_inputs`: after `build_brief`, pass the bundle's
tools/skills to the resolver composition (`operator/cowork/lib/resolver.py`
`_inject_forge_capability`/`_inject_skill_forge_capability` shape) so they enter
`--allowedTools` + `mcp_servers` for THIS turn. Record bound ids in the Decision
Record (ADR-0278).

**Tests:** a stage that adds a ToolRef → the ref appears in the turn's
allowed_tools; the per-turn cap is enforced; default pipeline binds nothing
(text-only parity); bound ids appear in the record; ADR-0279 — nothing binds on
the A2A path.

**Risk:** touching the resolver/allowed_tools is authority-adjacent. Gate: an
explicit test that a bound tool is STILL subject to the L34/L44/L35 spawn gates
(bind ≠ authorise).

---

## P-C — LLM synthesis stage (ADR-0282)

**New** `stages/llm_synthesis.py`: reads `scratch` (prior stages' findings),
builds a meta-prompt, `subprocess.run([resolve_claude_bin(), "-p", prompt,
"--append-system-prompt", sys, "--model", cfg_model, "--disallowedTools", "*",
"--output-format", "json"], timeout=…, check=True)`; parse usage like
`worker_ipc.py`. Sets `bundle.synthesised_prompt` + `scratch["needs"]={tools,skills}`.
**Changed** `license_gate.py`: add `enforce_ce_llm_quota(tenant)` — own
`feature="ce_llm_units_per_day"` + `counter_file="ce_llm_quota.json"`. Called
inside the stage. Over budget / timeout / unavailable → return without setting
`synthesised_prompt` (deterministic `approach_synthesis` output stands).
**Config:** `{stage: llm_synthesis, when: always|complex|never, model: …}`;
`complex` = a cheap deterministic task-complexity heuristic.

**Tests:** flag+quota on → subprocess invoked (mocked), synthesised_prompt set;
over budget → stage no-ops, deterministic output kept, turn not blocked;
subprocess timeout → fallback; audit records model+tokens+sha256, NEVER the
prompt text (content-free assertion, reuse ADR-0278 `assert_content_free`).

**Risk:** cost + latency + a real subprocess. Gate: the quota test + a timeout
test + the content-free-audit test must pass before it can be added to any
default pipeline.

---

## P-D — ToolForge + SkillForge stages (ADR-0283)

**New** `stages/toolforge.py`, `stages/skillforge.py`: read `scratch["needs"]`;
`toolforge` calls the Forge MCP (`forge_tool` create-if-absent by content hash,
then add `mcp__forge__<name>` to `tools_to_bind`); `skillforge` selects existing
skills or `skill_create`s a task-scoped one and adds to `skills_to_bind`. The
`impl`/body is authored by the P-C synthesis call or a template. Both pass Forge/
SkillForge's own gate chains (name_allowed → namespace_check → import check).

**Tests:** needs-a-tool → forged + bound + worker-callable (forge_exec same-turn);
dedup (same tool not re-forged); forged tool has NO network/subprocess (Forge
sandbox invariant); namespace-gate honoured; forge failure → fail-safe (turn
proceeds); every forged id audited.

**Risk:** provisioning is the most powerful stage. Gate: the sandbox-invariant
test + the "bind ≠ authorise" test (P-B) + per-turn cap.

---

## P-E — Context Pipeline editor (ADR-0284)

**New route** `routes/vibe_engineering.py`: `GET /pipeline` (current config +
palette from `registry.known_ids()`), `PUT /pipeline` (validate against known ids,
keep default-safe minimum, write `spec.context_engineering.pipeline`). CSRF on PUT.
**Frontend** `pages/vibe-engineering.tsx`: editor mode — drag-reorder, toggle,
per-stage config form, palette. Run view unchanged. Per-stage window shows config
+ telemetry + source graph.

**Tests:** PUT validates unknown/ungraded ids (400); can't drop the default-safe
minimum; round-trips config; tenant-isolated; Playwright screenshot of the editor.

**Risk:** a bad config could empty the pipeline. Gate: server-side minimum guard +
a warn in the UI; PUT rejects an empty/all-removed pipeline.

---

## P-F — Grade-gate + community stages + self-improving loop (ADR-0285)

**New** stage grade store (SkillForge grade shape: `{n_grades, mean_score}` per
stage id). **Gate:** a stage enters a *default* pipeline only above a mean-score
threshold over a min sample; new stage needs a bootstrap grade (the SkillForge
`n_grades<1 ⇒ inert` lesson). **Community stages:** ride registry axes
(boot_layer/tier/origin); `origin=community` never claims privileged boot_layer
(downgrade+audit); untrusted → subprocess (ADR-0233). **Loop:** outcome-feedback
(ADR-0269 Phase-4b) attributes turn success to the stages/config that ran →
accrues grades.

**Tests:** ungraded stage can't enter default (only opt-in); community privileged-
boot-layer claim downgraded+audited; grade accrual from a simulated outcome;
compliance invariants hold for a high-scoring stage (still gated).

**Risk:** weak outcome signal → slow learning (not wrong). Gate: the loop is
advisory (proposes), operator disposes (ADR-0284) — never auto-applied silently.

---

## Cross-cutting

- **Flag:** all behind `vibe_engineering`; new sub-behaviours (LLM, provisioning)
  additionally gated by config `when` + their own quota pools.
- **Audit:** every stage, LLM call, and forged artifact recorded content-free in
  the ADR-0278 Decision Record; full text/impl in the erasable Layer-B sidecar.
- **Degrade ladder:** any stage failure / budget exhaustion → that stage no-ops,
  the pipeline continues, worst case = today's plain context. Never a block.
- **Test discipline:** each phase = unit tests + one E2E through the real boundary
  (e2e-wiring-proof) + a parity/behaviour gate before it can be default-on.
