# Lab Notebook — EXP-001: Does the Context Engineering Layer save tokens?

**Tenant:** `_default` · **Started:** 2026-08-19 · **Status:** active (pilot complete)
**Keepers:** shumway (operator), Claude (Opus 4.8, experimenter)
**Companion docs:** `CorvinOS/docs/concepts/cel-token-savings-paper-concept.md` (paper blueprint),
`cel-benchmark-sample-size.md` (statistics), `Corvin-Publications/paper-004-cel-token-savings/`.

> **How to read this notebook.** It is written to be *scientifically rigorous* and *understandable
> without a background in LLMs*. Technical terms are defined the first time they appear (in
> _italics_). The structure of every entry is: **Goal → Method → What we did → Observations →
> Conclusions**. Failed attempts are kept, not deleted — they are evidence too.

---

## 0. Plain-language summary (for anyone)

CorvinOS advertises that it *saves tokens*. A _token_ is the unit an AI charges by — roughly a
word-piece; fewer tokens = less money. CorvinOS has a component called the **Context Engineering
Layer (CEL)** that, before every answer, gathers relevant background ("what do we know about this
user / this task?") and hands it to the AI. The intuitive worry: adding background *adds* tokens,
so how could it *save* any?

This experiment measures the truth honestly. The headline finding so far: **CEL does not reduce
the raw number of tokens — it slightly increases them.** Its value, if any, is in two subtler
places: (1) making those tokens *cheaper* by reusing a cache, and (2) helping the AI reach a
correct answer with less back-and-forth over a conversation. We are measuring both, on a wide
variety of tasks, and refusing to claim a saving the data does not show.

---

## 1. Motivation & central question

**Goal.** Decide, with evidence, whether the marketing claim "CorvinOS saves tokens through CEL"
is true — and if so, *by what mechanism and on which kinds of work*.

**Why it is non-obvious.** CEL *injects* context, which costs tokens. Any saving must therefore be
second-order: cheaper token *classes* (caching), or fewer tokens spent *re-deriving* things across
a multi-step conversation. First-order intuition ("more context = more cost") is measurable and,
we will see, incomplete.

**Central hypothesis (falsifiable).**
> H0 (null): CEL-on costs ≥ CEL-off in total token cost, at equal answer quality.
> H1: With cache-stable relocation + relevant context, CEL-on costs ≤ CEL-off at equal quality,
> and reduces cumulative tokens-to-completion on multi-turn tasks.

---

## 2. Chronological record

### 2026-08-19 · Entry 1 — Discovery: CEL was *breaking* prompt caching

**Goal.** Understand why the token-metrics panel looked wrong and whether CEL saves anything.

**Method.** Read the real turn path; measure the four _token classes_ an Anthropic model reports:
- `input_tokens` — fresh input (full price),
- `cache_creation_input_tokens` — input written to the _prompt cache_ (~1.25–2× price),
- `cache_read_input_tokens` — input re-read from the cache (~0.1× price, i.e. 10× cheaper),
- `output_tokens` — generated text (~5× price).

**What we did.** Drove real turns and summed all four classes (see Entry 4 for why summing matters).

**Observations.** CEL-on *shifted ~51 000 tokens per turn from the cheap cache-read class into the
expensive cache-creation class.* Because CEL injects a per-turn brief into the cached system
prompt, it invalidated the cache every turn, forcing the model to re-pay for the whole large
prefix. Measured multi-turn cost penalty: **−147 %** (i.e. CEL-on cost 2.5× CEL-off).

**Conclusion.** The advertised saving was, as built, a *loss*. Root cause: volatile context placed
inside a byte-stable cache region. → led to ADR-0395.

### 2026-08-19 · Entry 2 — Fix: cache-stable relocation (ADR-0395)

**Goal.** Make CEL cache-neutral without changing what context it injects.

**Method / what we did.** Move the volatile CEL brief *out* of the cached system prompt and *into*
the per-turn user message (which sits after the cache boundary), so the large prefix stays
cache-read. Guarded behind a feature flag `cel_cache_stable` (ships off by default).

**Observations (mechanism experiment, n=8 multi-turn, model Haiku, confounder-free):**
`cache_creation` 75 955 → 28 390 per run (**−63 %**); **cost/run −55.8 %**; quality held; raw
token count ~flat.

**Conclusion.** The penalty is eliminated: the saving is a *cache-class* saving, not fewer tokens.
Mirrored the same fix into the messenger bridge (`adapter.py`), proven by a call-site E2E test.

### 2026-08-19 · Entry 3 — Building an honest measurement harness

**Goal.** A reproducible A/B benchmark that cannot fabricate a saving.

**Method (the rules, each load-bearing):**
1. **Measure the SUM of all four token classes**, never a single field (see Entry 4).
2. **Cost = components × real per-class prices** (`prices.json`, real Opus/Haiku rates).
3. **Baseline = measured CEL-off**, never an estimate or hard-coded number.
4. **Drive the REAL turn path** (`chat_runtime.stream_turn`), proven by call-site tests — no mocks.
5. **Quality gate:** a token cut bought by a *worse* answer is dropped, never sold.
6. **Statistics:** bootstrap 95 % CI + Mann-Whitney-U; fixed seed; committed raw evidence.

### 2026-08-19 · Entry 4 — Why we sum, not trust one field (a real trap)

**Observation.** On a cached turn, `input_tokens` read **2** while the true input was ~62 000
(cache_read 24 433 + cache_creation 38 060) — i.e. one field captured **0.003 %** of reality. A
metric that trusts one backend field passes green while the real quantity fails silently.
**Rule:** the canonical metric is the 4-class total. (Same class of error as the "measure the sum,
not the backend" lesson from voice budgeting.)

### 2026-08-19 · Entry 5 — Confounders: the run kept hanging

**Goal.** Find why runs on `_default` hung and were confounded.

**Observation.** Every native turn silently spawned (a) a *discarded TDE shadow turn* and (b) a
*per-turn cloud synthesis* (active brain), turning ~10 s turns into minutes and polluting the
token counts with unrelated work.

**Fix.** The A/B now holds three confounders OFF for **both** arms (`tde_shadow_measurement`,
`vibe_engineering_active`, `outcome_feedback_loop`), with save/restore, so the delta is CEL itself.
Verified: 0 confounder-turns in the clean logs.

### 2026-08-19 · Entry 6 — Two more measurement bugs, fixed

- **Concurrency corruption:** two runs on the same tenant clobbered each other's per-arm flag on
  the shared overlay. → added a per-tenant **lock file**.
- **Dry-run side-effect:** the dry-run applied arm flags without restoring them (mutating live
  config). → dry-run now save/restores.

### 2026-08-19 · Entry 7 — The operator toggled a flag mid-run (taint hardening)

**Observation.** A manual Settings change to `vibe_engineering` *during* a run mis-labelled some
CEL-on reps as if CEL were on when it had been switched off → silent bias shrinking CEL's effect.

**Fix ("measure reality, not the label").** The harness now records the *actually resolved* flag
before AND after each rep; if it disagrees with the arm label the rep is **tainted → dropped and
counted**, with a visible report warning. Interference can no longer silently bias the numbers.

### 2026-08-19 · Entry 8 — Designing for variance (task diversity & sample size)

**Insight.** *Variance is the result, not noise.* CEL should help memory/multi-turn/context-reuse
tasks and do little or hurt on stateless one-liners. So the suite must span that heterogeneity.

**Sample-size reasoning (two-level variance model).** SE(mean effect) ≈
√(σ²_between / T + σ²_within / (T·n)). Reps `n` only shrink rep-noise (saturating ~n=10–15); only
more **tasks T** tighten the *generalizable* estimate. Power (paired, 80 %): large effect ~15
tasks, medium ~34, small/high-spread ~85. **Verdict: 20 = pilot; ~50 = valid headline; ~100 =
per-category significance.** Built `suite-v3-diverse.json` — 18 tasks / 10 strata, answers chosen
so they never appear in their own prompt (no echo pass).

### 2026-08-19 · Entry 9 — Pilot (baseline CEL-off vs CEL-on), n=10, clean

**Result (18 tasks, confounder-free, 0 tainted):**
- **Overall:** raw-token savings −0.6 % (not significant); cost ≈ equal (A $0.0565, B $0.0567).
- **By turn structure:** single-turn CEL **+1.9 % cost** (overhead); multi-turn CEL **−2.5 % cost**.
- **Per multi-turn task — huge spread:** `mt-budget-chain` −20 %, `mt-fib` +19 %. Two tasks, opposite
  signs → empirical proof that a handful of tasks cannot pin the effect (the σ_between argument).

**Conclusion.** On generic, self-contained tasks CEL is a small net *cost*, because the injected
context is not *relevant* to those puzzles. Value must be sought on context-relevant / multi-turn
work — and measured with more tasks.

### 2026-08-19 · Entry 10 — New quality dimensions (LDD define-metric), advisory

**Goal.** Since raw cost does not favour CEL, introduce **new dimensions** that could reveal value,
under the define-metric discipline (advisory-only until calibrated; a vector, not a gamed scalar).

**Dimensions added (deterministic from raw fields):** `quality_mean`, `quality_stdev`
(reliability), `quality_per_1k_tokens` (value density), `cost_per_correct_usd`, `output_tokens_mean`
(directness). See §3.

**Observation / a genuinely new finding.** CEL makes answers **~27 % longer** (single-turn +38 %).
Because output is priced 5×, verbosity is a real cost driver *against* CEL that the raw token sum
had hidden. On multi-turn, CEL is modestly better on value-density (+6.4 %) and cost-per-correct
(−3.5 %), but from only 2–4 tasks → not yet reliable.

**Conclusion.** The quality dimensions do not manufacture a win; they surface an honest, non-obvious
cost mechanism (verbosity) and point to the decisive next metric: `tokens_to_correct` (§4).

### 2026-08-19 · Entry 11 — Instrumented `tokens_to_correct` (LDD inner loop)

**Goal.** Build the most direct probe of CEL's cross-turn value: cumulative tokens until the
answer FIRST becomes correct.

**Method (LDD inner loop, K_MAX=5, test pyramid).** In `_run_task`, apply the objective check
after EACH turn (not just the last), record `turn_to_correct` (1-indexed), `tokens_to_correct`
(cumulative tokens up to that turn), and a per-turn `q_trajectory`. Wired into the raw record and
the advisory `dimensions` block (`tokens_to_correct_mean`, `turn_to_correct_mean`, `reached_rate`).

**What we did.** Tier-1 syntax gate green; Tier-4 smoke E2E (2 tasks, n=1) green.

**Observations.** The instrument records correctly: single-turn `s-hex` → turn_to_correct=1,
trajectory `[1.0]`; multi-turn `mt-reuse` → turn_to_correct=3, trajectory `[0.0, 0.0, 1.0]` — the
trajectory correctly shows the answer is absent in turns 1–2 and appears at turn 3.

**Conclusion (honest, dialectical antithesis confirmed).** On the CURRENT tasks the answer is
designed to appear only at the LAST turn, so `tokens_to_correct` = task total and does **not**
discriminate CEL on/off. The *instrument* is correct and reusable; to make it *demonstrate* value
we need tasks where the answer CAN appear earlier or where CEL's injected context lets the model
skip a clarification turn. That is the next work item (§5).

### 2026-08-19 · Entry 12 — The task class that finally isolates CEL value (memory-grounded)

**Goal.** Build tasks where CEL genuinely *should* win, so the value is measurable — not generic
puzzles that don't use context.

**Key mechanism finding (CONFIRMED, and it is the C4 asymmetry made concrete).** CEL's memory stage
(`operator/context_engineering/stages/memory.py:18`) calls `MemoryLookup()` with **no tenant
argument** → `_default_memory_dir()` = `~/.claude/projects/<escaped-repo-path>/memory/` (the repo's
Claude-Code memory, **248 .md files**). So CEL memory is **repo-scoped, not tenant-isolated**, and
retrieval is keyword TF-IDF (title 2×, threshold 0.3, top-5).

**The design (reframes the whole experiment, honestly).** CEL's real value is **not token savings**
— it is **answer correctness on questions that require injected knowledge**. So: write memory
fixtures holding **unguessable, PII-free** facts (staging-cluster codename `Halberd-9`, build-token
rotation `47 min`, retry limit `29`, mascot `Quill-7`, analytics port `8813`, residency region
`eu-fra-3`), then ask for them. CEL-on injects the fact → correct; CEL-off cannot know → wrong.
Fixtures: `bench-cel-*.md` (removable). Suite: `suite-v4-memory-grounded.json` (6 single-turn + 2
early-answerable multi-turn). **Discriminating dimensions:** `quality_mean` / `reached_rate` (not
raw cost).

**Pre-flight (CONFIRMED, no LLM).** `MemoryLookup.search(["staging","cluster","codename"])` returns
`bench-cel-staging-cluster` as the **top hit, relevance 1.0** — the fixture IS retrievable, so CEL
will inject it. → benchmark launched (`memgrounded-n3`) to prove end-to-end discrimination.

**Conclusion (framing).** This class measures CEL's *value* dimension (correctness), which the raw
token/cost axis could never show. It is the honest home of the "CEL is useful" claim: **CEL trades
a few extra tokens for being RIGHT on context-dependent questions a bare model gets wrong.**

### 2026-08-19 · Entry 13 — ACS (Autonomous Compute Shell) & Big Data (parallel investigation)

**Goal.** The operator asked to also probe ACS and what it brings for Big Data — CorvinOS's *other*
answer to "too much context". Full write-up: `acs-big-data-analysis.md` (this folder).

**Findings (CONFIRMED).** ACS (ADR-0104) is a manager/worker fan-out: a strong manager (sonnet-5,
CEL-informed on iteration 0 only) decomposes the task; cheap haiku workers each run in a fresh
`claude -p` with a **~3 KB context slice** and reach real data **out-of-context** via DSI (DB
connection) / files / snapshots. Big-data value = **decomposition-driven context isolation** — no
single context ever holds the whole dataset — plus parallelism (≤6/iter, ≤64 total) and a
cheap-worker/strong-manager cost split, under a formal budget envelope + L34 compliance gates.
Separate from **L25 Compute + ADR-0026 fabric** (out-of-LLM-loop *numeric* sharding). CEL feeds only
the ACS **manager**, never workers (isolation preserved, ADR-0279) → CEL and ACS are complementary,
orthogonal. Limits: 1 compute-unit/day quota (free tier), 3 KB worker-state cap, native beats ACS
for coding, background-only latency, ADR still *proposed*.

**Conclusion / bridge to the loss work.** ACS gives EXP-002 its own measurable hypotheses
(correctness-vs-volume, wall-clock speedup, $/correct, isolation-cost breakpoint, routing
precision) — same honest, quality-gated methodology as EXP-001, different system under test.

### 2026-08-19 · Entry 14 — Memory-grounded result + the connections (SSOT commit)

**Result (n=3, clean, confounder-free).** CEL-off quality 0.167 / cost_per_correct $0.425;
CEL-on quality **0.375** / cost_per_correct **$0.202**. CEL-on is **~2.25× more correct** and
**halves cost-per-correct** — the first place CEL's value is visible, and it lives in the
**correctness** dimension, as predicted. Per task, clean on `mem-residency-region` (0→1.0),
inconsistent elsewhere.

**Two honest anomalies (see `connections-synthesis.md`).** (A) CEL-on is only 0.375, not ~1.0 —
CEL's keyword-TF-IDF top-5 retrieval is **unreliable** (a CEL weakness, and the prime LDD training
target: push `reached_rate` → 1.0). (B) CEL-off scores 0.167 on **unguessable** facts it has no
legitimate path to — a possible measurement leak or check artifact, **flagged not explained**;
blocks a headline claim until closed.

**Connections investigated (full write-up: `connections-synthesis.md`).**
1. **Two-layer memory** — the console turn ALWAYS carries a memory INDEX (`MEMORY.md`,
   `~/.config/corvin-voice/memory/`) independent of the flag; only the CONTENT retrieval
   (`~/.claude/projects/<repo>/memory/`) is CEL-gated. So the A/B is "index vs index+content," not
   "memory vs none." The dirs differ; the fixtures are content-only, so CEL-off has no legit path.
2. **CEL ↔ ACS** — inject vs isolate; complementary, not competing; CEL feeds the ACS *manager*
   only. Both are context-management at different scales; neither is a token *saving* — each spends
   a little to buy a capability (CEL: correctness; ACS: volume).
3. **Value reframe** — the honest product claim is **capability per token**, not fewer tokens.

**SSOT.** This tenant folder is now the single source of truth for experiments, tracked on the git
`experiments` branch.

### 2026-08-19 · Entry 15 — Anomaly B resolved: the agentic tool-leak (invalidates the memgrounded pilot)

**Goal.** Root-cause why CEL-off scored on unguessable facts (Entry 14, Anomaly B).

**Method (LDD root-cause-by-layer, reproducibility-first).** Reproduced the smoking-gun task
`mem-mt-port-derive` with CEL **off** (+ confounders off, exactly the benchmark arm-A config),
capturing the REAL model responses.

**Observation (decisive).** With CEL off, the model still produced `8913` (= 8813+100) in 2 of 3
reps. Rep 2 said it verbatim: *"Based on the **benchmark test configuration in this project**, the
port reserved…"*. Rep 1 correctly said "unknown" and refused. So the model **found the fact on
disk**, not from CEL.

**Root cause by layer.**
- *L1 symptom:* CEL-off answers unguessable facts.
- *L2 mechanism:* the console OS turn runs `claude -p --dangerously-skip-permissions` with **no
  `--disallowedTools`** (`chat_runtime.py:2150`) → a **fully tool-enabled agent** (Read/Grep/Bash).
- *L3 structural:* memory-grounded tasks put the ground-truth fact on disk (the fixture, and the
  suite JSON's `expect`, both grep-able), so the agent **self-retrieves** it, bypassing CEL. The
  A/B does not isolate CEL.
- *L4 conceptual:* **CEL (push context) competes with agentic retrieval (pull context).** In a
  tool-enabled agent, CEL's marginal value shrinks because the agent can fetch context itself.

**Consequence (honest correction).** The Entry-14 memory-grounded numbers (0.167 vs 0.375) are
**CONTAMINATED and must NOT be used as CEL's value** — both arms could self-retrieve. It also
explains Anomaly A (CEL-on only 0.375: the arms differ little because both can pull).

**Fix for a valid measurement.** Run the benchmark turn **tool-disabled** (`--disallowedTools "*"`),
so CEL injection is the ONLY context channel: then CEL-off truly cannot know (→0), CEL-on injected
(→correct). This is the next measurement; until then the memory-grounded claim is withdrawn.

**New connection (see `connections-synthesis.md` §4).** Push-vs-pull is itself a measurable axis:
CEL's value should be largest exactly where the agent CANNOT pull (no tools / air-gapped / the fact
is not on any reachable path).

### 2026-08-19 · Entry 16 — Valid measurement (tool-disabled): CEL injects POINTERS, not content

**Goal.** Measure CEL's injection value cleanly, with the agentic pull (Anomaly B) removed.

**Method.** A dedicated 3-arm probe (`measure_tooldisabled.py`), each arm a `claude -p`
`--disallowedTools "*"` turn (no filesystem access → the only context is what we inject), Haiku,
n=3 over the 6 single-turn memory tasks:
- **none** — bare question (floor);
- **cel** — the REAL deterministic CEL brief prepended (`build_brief → render_brief_to_text`);
- **oracle** — the memory-file CONTENT prepended (ceiling: what CEL *would* inject if it injected
  content).

**Result (clean, decisive):**

| arm | quality |
|---|---|
| none | **0.00** |
| **cel** | **0.00** |
| oracle | **0.944** |

Per task: none=0, cel=0, oracle=1.0 on 5/6 (retry-limit oracle 0.67). The `none` arm answering
"unknown" also **confirms the tool-disable works** — no leak.

**The finding (this is the real result of EXP-001's value question).** Inspecting the brief:
`build_brief` retrieves the right fixture (`- bench-cel-analytics-port` is the top "Relevant past
memory" line) but renders **only the file NAME/title — never the body**. The fact `8813` is **not
in the brief** (`contains 8813? False`). So:

> **CEL's deterministic brief is a POINTER INDEX, not a content injection.** Tool-disabled it adds
> **zero** answerable value (0.00). Its entire measured "value" in tool-enabled turns (Entry 14)
> came from the **agent pulling the pointed-to files** — not from CEL. The oracle arm proves the
> facts ARE answerable (0.944) **if** the content were injected. The gap 0.00 → 0.944 is exactly
> what CEL is missing.

**Consequences.**
- Anomaly A (Entry 14, "CEL-on only 0.375") is now fully explained: CEL never injected the answer;
  the arms differed only in how often the agent happened to pull.
- **Concrete, actionable improvement target for CEL:** the brief must carry answer-bearing
  **content**, not just titles. This is a clean LDD training signal (new θ `brief_includes_content`;
  loss = distance from the oracle ceiling).
- The **valid measurement protocol** for CEL memory value is tool-disabled (this probe), not the
  full agentic console turn.

**Honest status of the whole "CEL value" question.** As built, CEL's deterministic brief does not
demonstrably improve correctness on context-dependent questions in a tool-disabled setting (0.00),
and is redundant with agentic pull in a tool-enabled one. Its proven, non-null win remains the
cache-stable **cost** neutrality (Entry 2). The correctness value is **latent** — realizable only
by injecting content (oracle 0.944), which the current brief does not do.

### 2026-08-19 · Entry 17 — Prototype fix: brief injects CONTENT → latent value realized (0.00 → 0.833)

**Goal.** Close the pointer-vs-content gap Entry 16 found: make the CEL brief carry memory bodies,
then measure tool-disabled against the oracle ceiling.

**Method (LDD inner loop, ship-dark).** Added `include_content` (keyword-only, default **False**)
to `render_brief_to_text` (`operator/context_engineering/pipeline.py`) + a `_memory_body` helper
that reads the match's `source_file`, strips frontmatter + the HTML disclaimer, caps to 800 chars.
`content_preview` was useless (~50 chars of frontmatter, no fact), so the body is read from disk.
Regression: a positional call equals `include_content=False` byte-for-byte (verified) → **no
production behavior change**. Added a 4th measurement arm `cel_content` (the fix on).

**Result (tool-disabled, n=3, 6 single-turn memory tasks, Haiku):**

| arm | quality |
|---|---|
| none | 0.00 |
| cel (titles) | 0.00 |
| **cel_content (fix)** | **0.833** |
| oracle (full content) | 1.00 |

Per task: 5/6 perfect (1.00); only `mem-mascot` missed (0.00) — a residual *retrieval* miss (the
fixture wasn't surfaced/rendered for that question), not a rendering-of-content failure.

**Conclusion.** The fix **realizes CEL's latent correctness value**: 0.00 → 0.833, ~87 % of the
oracle ceiling, from a one-function, default-off change. This validates θ `brief_includes_content`
as the primary lever (Entry 16's prediction confirmed). The remaining gap to 1.0 is the *other*
known weakness — **retrieval reliability** (keyword TF-IDF sometimes misses the right fixture),
the second training target.

**Not yet production.** The param is prototype-only; a production rollout needs a `cel_brief_
includes_content` feature flag wired into the console/bridge callers (ship-dark, + an ADR for the
brief-content change) and a token-cost check (bodies are larger than titles → re-measure cost).

### 2026-08-19 · Entry 18 — Lever 2 (retrieval) diagnosed + content-fix productionized (flag + ADR-0396)

**Lever 2 — retrieval reliability (the `mem-mascot` miss): NOT retrieval.** Reproduced: `build_brief`
retrieves `bench-cel-mascot` as the **top hit**, and the content-brief **contains "Quill-7"** — yet
the model answered "unknown" (3/3), while the oracle (same fact, clean framing) answered "Quill-7"
(3/3). Even a top-1-only brief still failed. So the residual gap is **not retrieval and not simple
noise** — it is a **brief-framing effect specific to name-type answers**: the "Relevant past memory:
- <title>:" framing makes the model treat a *name* as non-authoritative, where it will still extract
a *number*. Follow-up lever: assertive brief framing / selective injection (ADR-0394), not retrieval.

**Productionization — the validated content-fix, behind a real flag (ship-dark).**
- New flag **`cel_brief_includes_content`** (default off, alpha) in `feature_flags.py`.
- Wired into both deterministic-brief callers: console `chat_runtime.py:4733`, bridge
  `adapter.py:3373` — each reads the flag and passes `include_content=` to `render_brief_to_text`,
  with a `TypeError` fallback to title-only for an older render signature.
- **ADR-0396** documents the decision (`../Corvin-ADR/decisions/0396-…`).
- **e2e-wiring-proof:** through the real `feature_flags.is_enabled` call — flag off → brief lacks
  the fact; flag on → brief carries it. Verified.
- **Cost re-measure:** content brief adds ~1904 chars ≈ **+476 tokens/turn** ≈ **$0.0005/turn** at
  Haiku (cheap fresh user-turn input under ADR-0395) — negligible vs the 0.00→0.833 correctness lift.

**Status.** The primary correctness lever is now a real, default-off, cost-characterized feature an
operator can flip per workload after re-running the benchmark. Lever 2 is redirected from "retrieval"
to "brief framing".

### 2026-08-19 · Entry 19 — Framing lever: LDD caught an overfit; change reverted, honest 0.833 stands

**Goal.** Close the last gap (`mem-mascot` 0.00) — hypothesised as a brief-*framing* effect.

**Method (LDD inner loop + loss-backprop-lens + reproducibility-first).**
- *Iteration 1:* reframed the content brief assertively ("Established facts from this project
  (authoritative)…", fact presented directly, no title prefix). **No regression** (other 5 tasks
  1.0, title-only mode byte-identical) but `mem-mascot` **still 0.00**.
- *Iteration 2 (root-cause):* tested the mascot fact in isolation across formats —
  multiline=0.67, oneline+bold=1.00, plain=1.00. The quality **flips format-independently**: the
  fact "a raven named Quill-7" is inherently ambiguous for a *name* question (the name of what —
  the raven, the mascot? is "Quill-7" a name or a model number?), and in the *full* cluttered brief
  the model discounts it. **This is sampling noise + task ambiguity + brief clutter — not a
  systematic framing bug.** The earlier single 0.00 was partly n=3 bad luck.

**Decision (loss-backprop-lens).** The reframe produced **no measured improvement** (stable n=5
overall = **0.833**, same as Entry 17) and was motivated by a noisy single task. Per LDD "don't
ship a change without loss evidence," the framing change was **reverted** (`git checkout` — working
tree back to the committed Entry-17 render). Chasing `mem-mascot` further would be overfitting to
noise; I stopped.

**Result that stands (stable, n=5).** cel_content = **0.833** (5/6 tasks 1.0). The residual is
**task-inherent** (ambiguous fact phrasing + brief clutter), not a fixable systematic. The honest
lever ranking: (1) `brief_includes_content` — done, +0.833; (2) *selective injection* to cut brief
clutter (ADR-0394) — the real remaining direction, not "retrieval" and not "framing".

**Meta (outer loop).** This entry is itself the value of LDD: the discipline turned a tempting
"fix the failing task" into a correct "that failure is noise; don't ship an unmeasured change."

---

## 3. Metrics registry (the measured quantities)

| Metric | Definition | Unit | Direction (better) | Status |
|---|---|---|---|---|
| `cost_usd` | Σ(token_class × price) | $ | lower | primary |
| `tokens_total` | fresh + cache_creation + cache_read + output | count | — (context) | primary |
| `cache_creation` | tokens written to cache | count | lower | mechanism |
| `quality_mean` | objective fact-presence check ∈[0,1], averaged | rate | higher | advisory |
| `quality_stdev` | rep-to-rep spread of quality (reliability) | rate | lower | advisory |
| `quality_per_1k_tokens` | quality_mean ÷ (tokens_total/1000) | rate | higher | advisory |
| `cost_per_correct_usd` | cost_mean ÷ quality_mean | $ | lower | advisory |
| `output_tokens_mean` | mean generated tokens (directness) | count | lower | advisory |
| `tokens_to_correct` | cumulative tokens until the answer first passes the check | count | lower | advisory (instrumented Entry 11) |
| `turn_to_correct` | 1-indexed turn where the answer first passes | count | lower | advisory |
| `reached_rate` | fraction of reps that ever reached correctness | rate | higher | advisory |

Advisory metrics are **not** decision gates until calibrated (define-metric: n≥5, MAE≤0.15).

---

## 4. Structuring the metrics as LDD loss functions (the training goal)

**Intent (operator).** Later use LDD to treat these metrics as a **loss** and *train CEL* — i.e.
tune CEL's controllable parameters to descend the loss. For that, every metric must be: (a)
quantifiable, (b) reproducible, (c) attributable to controllable inputs.

**Controllable parameters of CEL (θ — what training would tune):**
- `selective_injection.threshold` (how relevant a memory must be to be injected),
- `memory_pruning` rules (age / confidence / quota),
- `adr_reranking` top-k, brief size cap, which stages fire (skill/graph/memory),
- `cel_cache_stable` (on/off — already a win).

**Loss shape — a VECTOR, not one gamed scalar** (cost ↔ quality is a real trade-off):
```
L(θ) = [ cost_per_correct_usd↓ , 1 − quality_mean↓ , quality_stdev↓ , tokens_to_correct↓ ]
```
Each component must descend or hold; a change that trades one for another is a Pareto decision the
operator sees, never a hidden average. `output_tokens_mean` enters as a regulariser (penalise
verbosity).

**Nuisance vs signal (must be controlled during training):**
- *controllable (θ):* the CEL parameters above.
- *nuisances (hold fixed / randomise):* model id, cache warm/cold regime, tenant runtime state,
  confounder flags (shadow/active-brain/feedback OFF), task order. All already pinned by the
  harness (Entries 5–7).

**Data contract for LDD.** Each benchmark run emits `raw-*.jsonl` (one record per rep, all metrics
+ the resolved θ/flags + `tainted`) and `report-*.json` (per-arm, per-stratum aggregates + the
`dimensions` vector). These are the loss samples; the machine-readable schema is in
`metrics-as-loss.json` next to this notebook.

**Calibration precondition.** Before any metric gates a training step it must pass the define-metric
gate (n≥5 predicted-vs-observed pairs, MAE≤0.15). Until then, metrics inform *search direction*
only, never accept/reject.

---

## 5. Open threads / next steps

1. ~~**`tokens_to_correct`** — instrument per-turn checking.~~ ✅ DONE (Entry 11). Next: it needs
   discriminating tasks (below) to actually reveal CEL value.
2. **Context-relevant / early-answerable tasks** — the generic suite under-samples where CEL
   should win; add (a) tasks whose answer depends on injected memory/graph, and (b) multi-turn
   tasks where the answer CAN appear before the last turn, so `tokens_to_correct` discriminates.
3. **Fix `plan-boil-egg`** quality check (q=0.00, too strict → dropped 4 pairs).
4. **Scale** the diverse suite toward T≈50, using the pilot's measured σ_between to set exact T.
5. **Calibrate** the advisory dimensions before using any as a training gate.

---

_This notebook is append-only going forward: new entries are added below with their date; prior
entries are never rewritten (only corrected via a new dated entry that says what changed and why)._
