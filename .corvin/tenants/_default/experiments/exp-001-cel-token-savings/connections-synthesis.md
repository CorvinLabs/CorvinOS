# Synthesis — the connections this experiment surfaced

**Part of EXP-001.** This ties together the relationships the measurements exposed. Written to be
honest first: where a connection is a *confound* it is named as one, not hidden.

## Plain-language summary
CorvinOS has **two different answers to "the AI has too much to deal with"**: **CEL** *injects*
relevant knowledge so the AI is *right*, and **ACS** *splits* huge work across isolated workers so
no single AI *drowns*. They are not competitors — they plug in at different points. Along the way we
found that our "CEL on vs off" test does not mean "memory vs no memory," and that CEL's memory
retrieval is unreliable — which is exactly the knob a later training loop should tune.

---

## Connection 1 — Two-layer memory: the A/B does NOT isolate "memory vs none" (a confound, CONFIRMED)

The console turn's system prompt has **two independent memory layers**, and the `vibe_engineering`
flag gates only one:

| Layer | Source dir | Content | Gated by `vibe_engineering`? |
|---|---|---|---|
| **Memory INDEX** (`MEMORY.md`) | `~/.config/corvin-voice/memory/` | ~200–500 tokens of one-line topic pointers, **always shown** (`chat_runtime._memory_index_block` → `memory.for_system_prompt`, unconditional at `chat_runtime.py:1989`) | **NO — always on** |
| **Memory CONTENT** (topic-file bodies) | `~/.claude/projects/<repo>/memory/` (248 files) | full retrieved `.md` bodies, keyword TF-IDF top-5 (`MemoryLookup`, `stages/memory.py:18`) | **YES — CEL pipeline only** |

**Consequences for the experiment:**
- The two dirs are **different**. The `bench-cel-*` fixtures live only in the CEL-content dir, so
  they are **not** in the always-on index → CEL-off has no legitimate path to those facts.
- Therefore the `cel` A/B is really **"index only" vs "index + retrieved content"**, not
  "no memory vs memory." For the memory-grounded suite this is fine (the facts are content-only),
  but it must be stated: neither arm is context-free.

## Connection 2 — CEL ↔ ACS: inject vs isolate, complementary not competing (CONFIRMED)

| | CEL (Context Engineering Layer) | ACS (Autonomous Compute Shell) |
|---|---|---|
| Core move | **INJECT** relevant external knowledge into one turn | **ISOLATE / SHARD** work across many small-context workers |
| Problem it solves | the model doesn't *know* a context-dependent fact | the data/work is *too large* for one context |
| Effect measured | answer **correctness** (this experiment) | **volume handling** + parallelism (a future EXP-002) |
| Cost | a few extra input tokens per turn | one compute-unit/day + latency |
| Wiring between them | **CEL feeds the ACS *manager* only** (iteration 0, `acs_runtime.py:902-922`), never the workers (isolation, ADR-0279) | workers reach data out-of-context via DSI/files |

**The unifying idea:** both are *context-management* strategies at different scales. CEL raises
per-turn correctness by adding the *right* small context; ACS preserves correctness at scale by
never letting any one context hold everything. Neither is a token *saving* — each **spends a little
to buy a capability** (CEL: correctness; ACS: volume). This reframes the whole "token saving"
marketing claim honestly: the product's value is *capability per token*, not *fewer tokens*.

## Connection 3 — The value finding + two honest anomalies (CONFIRMED, n=3 pilot)

Memory-grounded suite, `cel` A/B, Haiku, confounder-free:

| | quality (correctness) | reached_rate | cost_per_correct |
|---|---|---|---|
| **CEL-off** | 0.167 | 0.167 | $0.425 |
| **CEL-on** | **0.375** | **0.375** | **$0.202** |

CEL-on is **~2.25× more correct** and **halves cost-per-correct** — the first result where CEL's
value is visible, and it lives in the **correctness dimension**, exactly as predicted. But two
things must not be swept under the rug:

- **Anomaly A — CEL-on is only 0.375, not ~1.0.** It *should* retrieve the fact every time
  (pre-flight showed the fixture as top hit, relevance 1.0), yet gets it ~37 % of the time. Per
  task it is inconsistent (`mem-residency-region` 0→1.0 clean; `mem-analytics-port` 0.33→**0.0**,
  CEL-on *worse*). **CEL's keyword-TF-IDF top-5 retrieval is unreliable** — it does not consistently
  surface the right fact, or the model doesn't use it. This is a **CEL weakness the experiment
  found**, and the prime target for LDD training (tune retrieval θ to raise reached_rate).
- **Anomaly B — CEL-off scores 0.167 on unguessable facts.** e.g. `mem-analytics-port` off=0.33,
  `mem-mt-port-derive` off=0.33 (answer 8913 = 8813+100 → implies it *knew* 8813). CEL-off has no
  legitimate path to these facts (Connection 1). This is **either a measurement leak or a
  check artifact and must be investigated before any headline number is trusted.** Candidate
  causes to rule out: session/cache carryover across reps, a partial-match false positive in
  `quality_score`, or a memory path that ignores the flag. **Flagged, not explained.**

**Bottom line:** the memory-grounded class is the right instrument and shows CEL's correctness
value directionally, but n=3 is a pilot and Anomaly B blocks a headline claim until resolved.

## What each connection means for the LDD loss (feeds `metrics-as-loss.json`)

- Connection 1 → the baseline arm must be defined precisely ("index-only"), and a *true* no-memory
  arm may be a third condition worth adding.
- Connection 2 → CEL and ACS get **separate loss functions** (correctness for CEL; volume-handling
  / cost-per-correct-at-scale for ACS) — do not average them.
- Connection 3 → **`reached_rate` is the headline CEL loss component**: training should push CEL's
  retrieval from 0.375 toward 1.0. Anomaly B must be closed first, or the loss signal is polluted.
