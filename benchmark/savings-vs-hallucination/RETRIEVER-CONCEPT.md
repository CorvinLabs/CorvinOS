# Concept — Context Retriever Redesign (precision-first, benchmark-driven)

**Status:** proposed (concept only)
**Date:** 2026-09-05
**Author:** Claude (Opus), from a request by shumway
**Location:** `benchmark/savings-vs-hallucination/`
**Depends on / motivated by:** this benchmark's finding that **correctness is a switch on
whether the load-bearing body is present**, and content-injection is now the default
(`cel_brief_includes_content`, committed 2026-09-05). Content injection only pays off if the
retriever actually *finds* the right body — this concept is that other half.

---

## 1. Plain-language summary

CorvinOS now injects the *content* of a remembered fact into the prompt instead of just its
title. But it still *finds* which memory to inject with a crude method: plain keyword matching.
If you ask a question in different words than the note was written in, the keyword matcher
misses it, nothing relevant is injected, and the answer is wrong — even though the fact was
sitting in memory the whole time. This concept replaces the finder with a real one, and pins
success to the benchmark we already built: does the retriever put the *right* fact in front of
the model, at the *lowest* token cost?

---

## 2. The problem, from the code

| Component | Today | File |
|---|---|---|
| Memory retrieval | keyword **substring** match; title ×2, body ×1; threshold 0.3; top-5 | `operator/context_engineering/memory_lookup.py` (`search`, `_calculate_relevance`) |
| "Semantic" filter | **hash-based pseudo-embedding STUB** — cosine over meaningless vectors | `operator/context_engineering/selective_injection.py` (`_embed`) |
| Storage/index | flat `.md` files, `glob("*.md")` re-scanned **every turn** (~180 files) | `memory_lookup.py` |
| Body rendering | hard cut at **800 chars**, top-5 matches only | `pipeline.py` (`_memory_body`, `render_brief_to_text`) |

**Failure mode:** lexical recall gap. A note titled "Sable key rotation" is missed by a query
"how do I roll the signing credential?" — no shared token — so the fact is never injected and
the model (honestly) abstains or, on a weaker model, guesses. This is the recall side of the
same pointer-vs-content failure class the operator flagged as recurring.

### Prior art checked (2026-09-05) — no reusable semantic retriever exists
Three "Palace" things exist; **none does embedding-based retrieval**, so there is nothing to
reactivate:
- `core/vibe_engineering/memory_palace.py::MemoryPalace` — named "Semantic recall" but its
  `recall()` (`:54`) is keyword-substring (`# MVP: simple keyword matching (v1.1: vector DB)`).
  The vector-DB version was **never built** (deferred in `IMPLEMENTATION_STATUS.md:24-27`), and
  the class is **orphaned** — only reachable via `VibeEngine`, which no production path imports;
  it is not in the CEL turn path. RAM-only, no persistence.
- `core/quality/palace/IdeaPalace` — a filesystem ADR/idea-storage metaphor, not a turn-memory
  retriever. Unrelated.
- `Corvin-ADR/MIGRATION_PLAN_MEMPLACE.md` — an unexecuted plan to migrate ADRs into IdeaPalace
  (`Status: PLAN`). Unrelated to retrieval.

So this concept is effectively the never-built "v1.1 vector DB" — but placed where the LIVE
retriever is (`MemoryLookup` / the non-removable `MemoryStage`), not on the dead `MemoryPalace`.

---

## 3. Goal & success metric (tied to this benchmark)

Maximize **retrieval precision@correctness** at **minimum injected tokens**. Concretely, extend
the validated `savings-vs-hallucination` harness with a new arm that runs the REAL retriever
end to end (not a hand-injected context), and measure:

- **retrieval hit-rate** — did the load-bearing body land in the brief? (new metric)
- **correctness** — did the model answer correctly through the real pipeline?
- **marginal tokens** — how many context tokens did it cost? (cache-stable)
- **hallucination / abstention** — unchanged axes; the guardrail still gates.

A redesign wins only if it raises hit-rate/correctness **without** raising tokens or
hallucination past the pre-registered guardrail.

---

## 4. Design — four layers, each independently shippable

### L1. Real semantic retrieval (replace the hash stub)
Embed memory bodies and the query with a real model, rank by cosine.
- **Local option (no network):** `sentence-transformers` (e.g. a small MiniLM). No egress, no
  per-call cost, GDPR-clean. Preferred default.
- **Hosted option:** the Vault already holds `openai_api_key` — OpenAI embeddings behind the
  existing consent/egress gates. Faster to stand up, but a network + cost + data-flow event.
- Decision is a data-flow concern (L34/L35): local by default, hosted opt-in.

### L2. Hybrid + fusion (don't throw away lexical)
Keep BM25/keyword (great for exact IDs, flag names, ADR numbers) AND add semantic (great for
paraphrase). Merge with **Reciprocal-Rank Fusion** — robust, no tuning of score scales. Lexical
catches "ADR-0396"; semantic catches "roll the signing credential" → Sable.

### L3. Reranker (put the one body at rank 1)
Rerank the fused top-K (K≈20) with a cross-encoder or a cheap LLM-rerank, keep top-N (N≈2–3).
This directly defends against the top-5 cap and the 800-char body cut: the load-bearing body is
ranked first, so the caps never drop it. Rerank cost is bounded (K fixed).

### L4. Adaptive retrieval on abstention (uses this benchmark's key finding)
The model abstains honestly when the fact is missing (measured: abstain 0.975, hallucination
0.025). Make abstention a **signal**: inject a small brief first (cheap); if the answer
abstains, widen retrieval (raise N, relax threshold, add the reranked runner-ups) and retry.
Average cost stays low; correctness approaches the ceiling because only misses pay for more.

### Supporting fixes
- **Persistent index** (embed on write, not on every turn) — kills the O(n) `glob` re-scan.
- **Fact-preserving body cap** — extract the reranked-relevant passage instead of a blind
  `[:800]`; never cut through the load-bearing sentence.

---

## 5. How much this can save / gain (honest estimate)
- **Precision, not volume, is the token lever.** A 30-token oracle body and a 1500-token full
  context both score 1.00 (this benchmark). L3 lets CorvinOS inject ~1–2 right bodies (~300 tok)
  instead of top-5 (~1000 tok): **~700 marginal tokens/turn saved at equal-or-higher correctness.**
- **The bigger cost lever is orthogonal:** `cel_cache_stable` (ADR-0395) — deliver the brief on
  the user turn so the ~51k system+tools cache stays cache-READ (0.1×) instead of re-created
  (1.25×). Flag warning: +147% multi-turn. Pair it with content-injection.
- **Quality gain is the real prize:** L1+L2 raise hit-rate on paraphrased queries, which today
  silently return 0.00. That is correctness the system already had but couldn't reach.

---

## 6. Alternatives considered (dialectical check)
- **Thesis: just raise the caps** (top-5→top-20, 800→3000 chars). Rejected: more tokens, more
  cost, and it doesn't fix the *recall* miss — a lexically-missed body is absent at any cap.
- **Antithesis: a better keyword matcher** (real TF-IDF/BM25 instead of substring). Helps exact
  terms, still misses paraphrase. Necessary (L2) but not sufficient.
- **Synthesis: precision via hybrid retrieval + rerank** — find the right body regardless of
  wording, then inject only it. Both axes (tokens ↓, correctness ↑) improve. This is the design.
- **LLM-rerank vs cross-encoder:** cross-encoder is cheaper/local and deterministic; LLM-rerank
  is stronger but costs tokens and can itself err. Start cross-encoder, keep LLM-rerank optional.

## 7. Risks / confounders
- Embedding staleness (index not rebuilt on memory edit) → re-embed on write.
- Local model download / cold-start latency → warm at boot, cache the model.
- Rerank latency on the hot path → bound K, run only when memory-stage returns candidates.
- Hosted embeddings = egress + cost + PII flow → local default, hosted behind L34/L35 gates.
- Measurement trap: test the REAL retriever end-to-end (a hand-injected oracle proves nothing
  about recall). New arm must drive `MemoryLookup`/the fused retriever, not a fixture.

## 8. Falsifiable predictions (to pre-register before the v3 run)
| # | Prediction |
|---|---|
| R1 | On paraphrased-query tasks, semantic (L1) hit-rate ≥ +0.30 over keyword baseline |
| R2 | Hybrid+rerank (L2+L3) correctness ≥ keyword baseline on exact-term tasks (no regression) |
| R3 | Rerank-to-top-2 injects ≥ 500 fewer marginal tokens than top-5 at equal correctness |
| R4 | Adaptive-on-abstention (L4) reaches within 0.05 of full-context correctness at < 60% of its tokens |

## 9. Change list (CorvinOS)
- Replace `selective_injection.py::_embed` stub with a real embedder (local default).
- Add a hybrid retriever + RRF behind the memory-stage (`stages/memory.py`), keep `MemoryLookup`
  lexical as one arm of the fusion.
- Add a rerank stage (cross-encoder) before render; feed it the reranked passage for the body cap.
- Persistent embedding index (embed-on-write); drop per-turn `glob` re-scan.
- Wire L4 adaptive retry at the CEL/brief boundary (abstention detector → widen → retry).
- Extend this benchmark with a real-retriever arm + `retrieval_hit_rate` metric; pre-register R1–R4.

---

_Concept only. No retriever code changed yet. Next step: build L1 (local semantic) behind the
memory-stage and measure R1 with the validated harness before touching L2–L4._
