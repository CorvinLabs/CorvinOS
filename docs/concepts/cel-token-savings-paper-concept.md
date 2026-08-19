# Concept — Scientific Paper: "Token Economics of a Context Engineering Layer"

**Status:** Concept / blueprint (not the paper itself) · **Date:** 2026-08-19
**Author:** Claude (Opus 4.8) for shumway · **Scope:** how to write a *rigorous, reproducible,
honest* paper that measures whether — and how — CorvinOS's Context Engineering Layer (CEL)
saves tokens.

---

## 0. The honest thesis (read this first)

A paper that *assumes* its conclusion is propaganda, not science. This session already
measured the load-bearing nuance empirically, so the paper must be built on it:

- **CEL does NOT reduce the raw per-turn token *count*.** It *injects* context, so a single
  CEL-on turn carries *more* raw input tokens than a CEL-off turn. Measured: raw total is
  ~flat-to-higher.
- **The saving is real but lives in two other dimensions:**
  1. **Cost / cache-class.** With cache-stable relocation (ADR-0395) the injected context
     rides in the cheap `cache_read` class (0.1×) instead of forcing `cache_creation` (1.25–2×)
     of the whole system prefix. Measured n=8 multiturn, Haiku: **cost/run −55.8 %**,
     `cache_creation` −63 %, quality held.
  2. **Cross-turn / task-level efficiency.** Injected context can let the model reach a
     *correct* answer in fewer turns / less re-derivation over a conversation — the tokens the
     brief costs are repaid by tokens it avoids. This is the *actual* "CEL saves tokens" claim
     and it is **not yet measured**; the paper's central experiment must measure it.
- **Selective injection (ADR-0394)** shrinks the brief's own token count 65–82 %, lowering the
  *price of using CEL* — but shrinking a brief 4k→1k does not by itself fix the cache penalty;
  it composes with, and does not replace, ADR-0395.

**Paper's defensible claim:** *"With cache-stable relocation and selective injection, CEL
delivers equal-or-better task outcomes at equal-or-lower total cost, and over multi-turn tasks
reduces cumulative tokens-to-completion at held quality."* The paper proves that *if the data
holds*; if a dimension shows no saving, the paper reports that too. Reviewer credibility (and
the product's) depends on this honesty.

---

## 1. The four load-bearing constraints (must be satisfied or the paper is invalid)

| # | Constraint | Why it binds the measurement | How the paper handles it |
|---|---|---|---|
| **C1** | **ADR-0297 — fail-closed PII detection (BLOCKER)** | The CEL brief is drawn from tenant runtime memory/graph/skills, which may contain PII; the fail-closed detector redacts/rejects PII before it reaches a prompt, audit log, or output. Two consequences: (a) published artifacts (raw token logs, example prompts/briefs) **must be PII-free**; (b) a PII-triggered fail-closed rejection **aborts the turn**, which would silently drop a sample and bias the mean. | Use only **synthetic, PII-free** benchmark suites (primes/budget/refactor — no personal data). Log the PII-scrub as a first-class event; **exclude and count** any fail-closed-aborted turn rather than letting it vanish. Publish only scrubbed briefs. State explicitly that measured briefs are post-scrub. |
| **C2** | **Measure the SUM, not the backend variable** | `input_tokens` alone captured **0.003 %** of true input on a cached turn (2 vs 24 433 cache_read + 38 060 cache_creation). A metric that reads one backend field passes while reality fails silently — the exact class in `feedback-budget-tests-measure-the-sum-not-the-backend`. | Canonical metric = **Σ of all four classes** (`fresh_input + cache_creation + cache_read + output`) via `core/learning/token_accounting.token_components`. Cost = the same components **weighted by real per-class prices**. Never report a single field as "tokens". |
| **C3** | **Call-site / real-path validation** | A token count from a mocked counter proves nothing about the running system. The measurement must traverse the **real** turn path and its real usage-reporting call site. | The harness drives `chat_runtime.stream_turn` (the real console OS-turn) and reads the worker's real terminal `usage`. An **e2e-wiring-proof** test asserts the flag actually toggles the real code path (not a stub). The bridge surface has its own call-site test (`test_adapter_cache_stable_cel.py`). |
| **C4** | **Source-tree vs runtime-dir asymmetry** | CEL's brief content comes from the tenant **runtime dir** (`~/.corvin/tenants/<t>/{memory,graph,skills,grades}`), **not** the source tree. A throwaway tenant with an empty runtime produces an ~empty brief → no measurable effect; `_default` with real runtime state produces a real brief. Measuring on the wrong tenant understates or fabricates the effect. | Declare the **runtime-state provenance** of the measured tenant as an experimental variable. Report brief size distribution. Run on a tenant whose runtime dir is representative; snapshot (hashes, not content) the runtime state so the run is reproducible without publishing PII. |

---

## 2. Paper structure

### Abstract (template, 150–250 words)
> Large-language-model agents pay for every token of context they carry. CorvinOS interposes a
> *Context Engineering Layer* (CEL) that assembles a per-turn brief from tenant memory, a
> knowledge graph, and learned skills. We ask a precise question: **does CEL reduce the total
> token cost of completing a task, and by what mechanism?** We define a backend-agnostic metric —
> the sum of all four Anthropic token classes weighted by real per-class prices — and drive it
> through the unmodified production turn path (no mocks). On PII-free synthetic suites
> (single- and multi-turn) we run a quality-gated A/B (CEL-off vs CEL-on) with n≥10 per arm,
> bootstrap 95 % CIs and a Mann-Whitney-U test. We find that CEL **increases raw token count**
> per turn (it injects context) but, with cache-stable relocation, **reduces cost per run by
> X %** (95 % CI …) by moving injected context from the cache-creation class to the cache-read
> class, and **reduces cumulative tokens-to-completion by Y %** over multi-turn tasks at held
> answer quality. We release the harness, prices, seeds, and raw evidence for exact
> reproduction. [Fill X/Y from the measured baseline experiment.]

### 1. Introduction
- **Problem:** context is the dominant cost in agentic LLM systems; naive context injection is
  assumed to be pure overhead. Is a *structured* context layer net-positive?
- **Why it's non-obvious:** injection adds tokens; the win, if any, is second-order (caching,
  re-derivation avoidance). First-order intuition ("more context = more tokens = more cost") is
  measurable and, we show, incomplete.
- **Contributions:** (i) a backend-agnostic, cache-aware token-cost metric; (ii) a reproducible
  real-path A/B methodology with a quality gate; (iii) an empirical decomposition of CEL's token
  economics into raw-count, cache-class, and cross-turn effects; (iv) the released harness.
- **Related work:** prompt caching, retrieval-augmented generation cost studies, context
  compression / pruning, KV-cache reuse. Position CEL as *structured, audited, tenant-scoped*
  context assembly, distinct from generic RAG.

### 2. Methodology
- **2.1 Metric (C2).** Four token classes; the summed total; cost = Σ(class × price). Publish
  `prices.json`. State that reading a single field is invalid and why (the 0.003 % finding).
- **2.2 Real-path measurement (C3).** Turns run through `chat_runtime.stream_turn`; usage read
  from the terminal `result` event. Call-site tests prove the path. No synthetic token models.
- **2.3 Baseline definition.** The baseline is **CEL-off** (`vibe_engineering=false`), *not* an
  estimate. (Explicitly repudiate any hard-coded baseline — the old `baseline_tokens = 1800 ×
  multiplier` fabrication is named as an anti-pattern.)
- **2.4 Confounder isolation.** Hold OFF, for both arms, every feature that adds orthogonal
  per-turn work (shadow TDE measurement, active-brain cloud synthesis, outcome-feedback writes);
  document them. Otherwise the delta is polluted by unrelated machinery.
- **2.5 Cache regime.** Distinguish **cold** (first turn, always pays cache_creation) from
  **warm** (turn ≥2 reuses the stable prefix). CEL's cache benefit is a *warm-path* effect, so
  multi-turn suites are mandatory; single-turn measures the cold worst case.
- **2.6 Runtime-state provenance (C4).** Record the measured tenant and a content-free hash of
  its runtime dir; report brief-size distribution.
- **2.7 PII handling (C1).** Synthetic suites only; scrub events counted; fail-closed aborts
  excluded-and-reported, never silently dropped.
- **2.8 Reproducibility.** Fixed bootstrap seed; pinned model id; `run_benchmark.sh` one-command
  repro; raw `*.jsonl` evidence trail committed.

### 3. Benchmark design
- **Arms.** (a) *Baseline experiment* — CEL-off vs CEL-on (the paper's headline). (b) *Mechanism
  experiment* — CEL-on cache-stable-off vs cache-stable-on (isolates the caching mechanism;
  already measured). (c) optional — selective-injection off vs on (isolates ADR-0394).
- **Suites.** Single-turn (cold cost) + multi-turn (warm reuse + cross-turn value). Task types:
  coding, reasoning, refactor. Each task carries an **objective** fact-presence quality check
  (no LLM judge in the loop → no circularity).
- **Quality gate.** A token cut bought by a worse answer is **dropped from the savings**, never
  sold. Report dropped-pair count.
- **Sample size.** n ≥ 10 per arm per task (paper target n = 20+); report per-task and overall.
- **Primary metrics.** cost/run (headline), Σtokens/run, per-class breakdown, tokens-to-
  completion (multi-turn), quality. **Stats:** bootstrap 95 % CI on the median-savings, Mann-
  Whitney-U (one-sided B<A), effect size.

### 4. Results
- Table: per-arm 4-class component means + cost + quality, per suite.
- Figure 1: cost/run CEL-off vs CEL-on with CIs (bar + error).
- Figure 2: cache-class migration (stacked bar: creation vs read) off→on — the mechanism made
  visible.
- Figure 3: tokens-to-completion over turn index (multi-turn) — where cross-turn value shows.
- Honest reporting of the raw-count result (flat/up) *next to* the cost result (down): the two
  are different axes and must not be collapsed into one number.

### 5. Discussion
- **Mechanism decomposition:** (i) cache-class relocation (ADR-0395); (ii) brief compression
  (ADR-0394); (iii) cross-turn re-derivation avoidance. Which dominates, when.
- **When CEL does NOT save:** single cold turn, tiny tasks, empty runtime state (C4) — name the
  boundary honestly.
- **Threats to validity:** provider cache TTL variance; runtime-state dependence (C4); model
  choice (OS-model = Haiku on `_default`, worker = Opus — pricing must match the model that
  actually ran); suite representativeness; multi-tenant generalization.
- **Reproducibility statement** + artifact links.

### 6. Conclusion & future work
Learned/adaptive injection thresholds; per-model cache economics; longer conversations.

### References
Anthropic prompt-caching docs; ADR-0275/0282/0283 (CEL), ADR-0394 (selective injection),
ADR-0395 (cache-stable), ADR-0297 (PII fail-closed), ADR-0218 (token routing);
`core/learning/token_accounting.py`; the released harness.

---

## 3. Concrete measurement principles (the checklist the experiments must pass)

1. **Σ all four classes** — never a single field. (C2)
2. **Cost = components × real prices** — cache-read 0.1×, creation 1.25–2×, output 5×.
3. **Baseline = measured CEL-off**, never an estimate or hard-coded constant.
4. **Real turn path only** — driven through `stream_turn`, proven by a call-site test. (C3)
5. **Quality-gate every pair** — a saving with a worse answer is not a saving.
6. **Isolate confounders** — shadow/active-brain/feedback OFF for both arms; documented.
7. **Cold vs warm reported separately** — multi-turn is where CEL's cache value lives.
8. **Declare runtime-state provenance** — tenant + content-free runtime hash + brief-size dist. (C4)
9. **PII-free artifacts** — synthetic suites; scrub/abort events counted, never silently dropped. (C1)
10. **Fixed seed + pinned model + committed raw evidence** — one-command reproduction.

---

## 4. What already exists vs what the paper still needs

**Exists (this session):** the harness (`benchmark/token-savings/`), 4-class accounting,
cache-aware pricing, bootstrap+MWU stats, quality gate, confounder isolation, concurrency lock,
call-site tests, and the *mechanism* experiment (ADR-0395, −55.8 % cost).

**Still needed for the paper:**
- **The baseline experiment** — CEL-off vs CEL-on at n≥20 (the headline claim). The `--arms cel`
  mode already does this; run it confounder-free on a representative-runtime tenant.
- **Tokens-to-completion metric** — instrument the multi-turn harness to record cumulative
  tokens until the objective check passes (the cross-turn-value number).
- **Runtime-state snapshot hashing** (C4) and **brief-size distribution** logging.
- **Selective-injection arm** (ADR-0394 on/off) to attribute the compression mechanism.
- **Figures + LaTeX** (the `academic-paper-generation` skill can render once numbers exist).
