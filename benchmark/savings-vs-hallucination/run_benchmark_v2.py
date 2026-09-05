#!/usr/bin/env python3
"""Savings-vs-Hallucination benchmark v2 (see CONCEPT.md, PRE-REGISTRATION-v2.md).

Fixes over v1:
  - realistic context sizes (fact embedded in themed filler) so the savings axis is non-degenerate
  - reports MARGINAL context tokens = input_total(arm) - input_total(none): cache-stable, subtracts
    the fixed system-prompt overhead
  - separates cold (cache-creation) vs warm (cache-read) cost instead of averaging them away
  - guardrail on halluc_rate_trap (not overall) — v1's overall gate hid the trap effect
  - permutation test on the P7 trap-hallucination difference (full vs pruned)
  - --reaggregate re-runs stats on a raw file to prove reproducibility

Isolation unchanged: `claude -p --disallowedTools '*' --max-turns 1`, language held fixed.
"""
from __future__ import annotations
import argparse, json, random, statistics, subprocess, sys, time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import run_benchmark as rb  # reuse validated classify/abstained/usage_components/boot_ci

ARMS = ("none", "pruned", "full")

# Authoritative-sounding, on-topic filler written as flowing handbook prose (NOT numbered
# "Note N:" lines — v2 calibration showed the model flags repetitive numbered notes as a
# prompt-injection/padding attack, which confounds the authority test). The pool is large and
# DIVERSE so a ~1000-word context reads as real documentation, not obvious padding. No numbers
# or names that could collide with any task's expected-answer substring.
FILLER = [
    "The {topic} is a first-class component of the CorvinOS platform and is described in the internal engineering handbook.",
    "Operators interact with the {topic} through the standard console as well as the audited command surface.",
    "Every action taken by the {topic} is recorded in the hash-chained audit log to satisfy the platform's compliance obligations.",
    "The {topic} honours the platform's tenant-isolation rules and never allows state to cross a tenant boundary.",
    "Telemetry for the {topic} is exported on the platform's regular cadence and surfaced on the observability dashboard.",
    "The {topic} participates in the platform's readiness and liveness checks and reports its health at boot.",
    "Runtime configuration for the {topic} lives in the tenant configuration file and is validated when the process loads.",
    "Under sustained load the {topic} degrades gracefully and applies backpressure to protect downstream consumers.",
    "Changes to the {topic} are gated by the standard review process and captured in an architectural decision record.",
    "The {topic} is exercised by the platform's integration test suite and its public contract is version-pinned.",
    "The {topic} emits structured events that other subsystems may subscribe to over the internal bus.",
    "Access to the {topic} passes through the consent gate and the house-rules enforcement layer before proceeding.",
    "The {topic} was designed to be observable end to end, so its internal transitions are traceable after the fact.",
    "Documentation for the {topic} is kept in sync with its implementation as part of the definition of done.",
    "The {topic} follows the platform convention of failing safely rather than continuing in an ambiguous state.",
    "When the {topic} starts up it registers itself with the local service directory and announces its capabilities.",
    "The maintainers of the {topic} publish a short runbook covering the most common operational scenarios.",
    "The {topic} is deployed as part of the standard bundle and is enabled by default on local installations.",
    "Requests handled by the {topic} carry a correlation identifier so they can be traced across subsystems.",
    "The {topic} keeps its hot path allocation-free where practical to hold tail latency within budget.",
    "Historical behaviour of the {topic} is retained in the platform's decision record repository for context.",
    "The {topic} exposes its metrics in the same format as the rest of the platform for uniform dashboards.",
    "Operators can drain the {topic} cleanly before maintenance so that in-flight work is not lost.",
    "The {topic} treats its inputs as untrusted and validates them against a schema before acting.",
    "A dedicated section of the handbook explains how the {topic} interacts with the scheduler and the cache.",
    "The {topic} is reviewed periodically for drift between its documented contract and its actual behaviour.",
    "The {topic} supports a dry-run mode that lets operators preview an action without committing it.",
    "The platform's release process ships the {topic} behind the same canary and rollback machinery as everything else.",
    "The {topic} records structured errors with enough context for an operator to reconstruct what happened.",
    "By convention the {topic} never logs personal data and scrubs sensitive fields before emitting a line.",
    "The {topic} coordinates with neighbouring components through well-defined, versioned interfaces only.",
    "The team keeps a set of example workflows for the {topic} so new contributors can get started quickly.",
    "The {topic} is idempotent where the platform requires it, so retries do not produce duplicate effects.",
    "Capacity planning for the {topic} is reviewed each quarter against observed traffic patterns.",
    "The {topic} participates in the platform's graceful-shutdown protocol and flushes its buffers on exit.",
    "Alerts for the {topic} are tuned to fire on symptoms operators can act on rather than on raw internals.",
    "The {topic} keeps backward compatibility across minor versions so dependents are not forced to change.",
    "Security review of the {topic} confirmed it holds no ambient authority beyond what its role requires.",
    "The {topic} is documented with a short conceptual overview followed by the operational details.",
    "Contributors extending the {topic} are expected to add coverage that exercises the new path end to end.",
    "The {topic} is written to be restart-safe, so an unexpected process exit does not leave corrupt state behind.",
    "Internal reviews of the {topic} check that its logging is structured and free of free-form secrets.",
    "The {topic} exposes a small, stable surface so that dependents can reason about its behaviour easily.",
    "When the platform rotates credentials, the {topic} picks up the new material without a restart.",
    "The {topic} is designed so that a slow dependency cannot cascade into a platform-wide stall.",
    "The handbook notes that the {topic} should be observed through its metrics rather than by reading its logs live.",
    "The {topic} keeps a bounded amount of in-memory state and spills the rest to durable storage.",
    "Operators are advised to change settings for the {topic} through configuration rather than by patching it.",
    "The {topic} carries a version tag in its events so consumers can adapt to schema changes over time.",
    "The {topic} was reviewed against the platform's data-flow rules and passed without exceptions.",
    "A conceptual diagram in the handbook shows where the {topic} sits relative to the rest of the platform.",
    "The {topic} favours explicit, auditable behaviour over clever implicit shortcuts.",
    "Load tests for the {topic} are run before each release to confirm its latency budget still holds.",
    "The {topic} reports a clear, actionable message when a downstream dependency is unavailable.",
    "The onboarding guide lists the {topic} among the components a new operator should understand first.",
]


def build_context(topic: str, fact: str | None, target_words: int) -> str:
    intro = f"The following is background documentation on the {topic}, drawn from the CorvinOS engineering handbook."
    lines = [intro]
    if fact:
        lines.append(fact)
    i = 0
    while len(" ".join(lines).split()) < target_words and i < len(FILLER):
        lines.append(FILLER[i].format(topic=topic))
        i += 1
    return "\n".join(lines)


def build_prompt(arm: str, task: dict, full_words: int, pruned_words: int) -> str:
    q = task["prompt"]
    if arm == "none":
        return f"{rb.LANG}\n\nQuestion: {q}"
    if arm == "pruned":
        ctx = build_context(task["topic"], None, pruned_words)
    else:  # full
        ctx = build_context(task["topic"], task.get("fact"), full_words)
    return f"{rb.LANG}\n\n{ctx}\n\nQuestion: {q}"


def run_turn(prompt: str, model: str, timeout: int = 180) -> dict:
    p = subprocess.run(
        ["claude", "-p", prompt, "--disallowedTools", "*", "--model", model,
         "--output-format", "json", "--max-turns", "1"],
        capture_output=True, text=True, timeout=timeout,
    )
    try:
        obj = json.loads(p.stdout)
    except Exception:
        return {"text": (p.stdout or "").strip(), "usage": {}, "cost": 0.0, "parse_error": True}
    return {"text": (obj.get("result") or "").strip(), "usage": obj.get("usage", {}),
            "cost": float(obj.get("total_cost_usd", 0.0) or 0.0)}


def perm_test(a, b, n=20000, seed=1):
    """Two-sided permutation test on mean(a)-mean(b) for 0/1 lists."""
    a = [x for x in a if x is not None]; b = [x for x in b if x is not None]
    if not a or not b:
        return {"diff": None, "p": None}
    obs = statistics.mean(a) - statistics.mean(b)
    pool = a + b; na = len(a); rnd = random.Random(seed); cnt = 0
    for _ in range(n):
        rnd.shuffle(pool)
        d = statistics.mean(pool[:na]) - statistics.mean(pool[na:])
        if abs(d) >= abs(obs) - 1e-12:
            cnt += 1
    return {"diff": round(obs, 4), "p": round(cnt / n, 4), "n_a": len(a), "n_b": len(b)}


def aggregate(raw, guardrail_margin):
    def sub(arm=None, cls=None):
        return [r for r in raw if (arm is None or r["arm"] == arm) and (cls is None or r["class"] == cls)]

    none_in = rb.mean([r["input_total"] for r in sub("none")]) or 0.0
    base_trap = rb.mean([r["halluc"] for r in sub("none", "trap")]) or 0.0
    rep = {"arms": {}, "guardrail_margin": guardrail_margin}
    for arm in ARMS:
        rows, ans, trp = sub(arm), sub(arm, "answerable"), sub(arm, "trap")
        in_m = rb.mean([r["input_total"] for r in rows])
        cold = [r["cost_usd"] for r in rows if r["cache_creation"] > r["cache_read"]]
        warm = [r["cost_usd"] for r in rows if r["cache_read"] >= r["cache_creation"]]
        th_m, th_lo, th_hi = rb.boot_ci([r["halluc"] for r in trp])
        rep["arms"][arm] = {
            "input_tokens_mean": in_m,
            "marginal_context_tokens": round((in_m or 0) - none_in, 1),
            "cost_cold_usd_mean": rb.mean(cold), "cost_warm_usd_mean": rb.mean(warm),
            "correct_rate_answerable": rb.mean([r["correct"] for r in ans]),
            "abstain_rate_answerable": rb.mean([1 if r["abstained"] else 0 for r in ans]),
            "halluc_rate_answerable": rb.mean([r["halluc"] for r in ans]),
            "abstain_rate_trap": rb.mean([1 if r["abstained"] else 0 for r in trp]),
            "halluc_rate_trap": {"mean": th_m, "ci95": [th_lo, th_hi]},
        }
        lift = round((th_m or 0) - base_trap, 4)
        rep["arms"][arm]["trap_halluc_lift_vs_none"] = lift
        rep["arms"][arm]["guardrail"] = "PASS" if lift <= guardrail_margin else "REJECT"
    # P7: full vs pruned trap hallucination
    rep["P7_full_vs_pruned_trap"] = perm_test(
        [r["halluc"] for r in sub("full", "trap")], [r["halluc"] for r in sub("pruned", "trap")])
    return rep


def print_summary(rep, meta):
    print("\n" + "=" * 82)
    print(f"RESULT v2  model={meta['model']}  reps={meta['reps']}  "
          f"tasks={meta['n_tasks']}(ans={meta['n_ans']},trap={meta['n_trap']})  "
          f"calls={meta['total_calls']}  {meta['wall_s']}s")
    print("-" * 82)
    print(f"{'arm':7s} {'marg_tok':>8s} {'cold$':>7s} {'warm$':>7s} {'ans_corr':>8s} "
          f"{'ans_hal':>7s} {'trap_hal':>8s} {'guard':>7s}")
    for arm in ARMS:
        a = rep["arms"][arm]
        print(f"{arm:7s} {a['marginal_context_tokens']:>8.0f} "
              f"{(a['cost_cold_usd_mean'] or 0):>7.4f} {(a['cost_warm_usd_mean'] or 0):>7.4f} "
              f"{str(a['correct_rate_answerable']):>8s} {str(a['halluc_rate_answerable']):>7s} "
              f"{str(a['halluc_rate_trap']['mean']):>8s} {a['guardrail']:>7s}")
    p7 = rep["P7_full_vs_pruned_trap"]
    print("-" * 82)
    print(f"P7 trap-halluc(full) - trap-halluc(pruned) = {p7['diff']}  (perm p={p7['p']}, "
          f"n_full={p7.get('n_a')}, n_pruned={p7.get('n_b')})")
    print("=" * 82)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="claude-haiku-4-5-20251001")
    ap.add_argument("--reps", type=int, default=4)
    ap.add_argument("--full-words", type=int, default=1000)
    ap.add_argument("--pruned-words", type=int, default=90)
    ap.add_argument("--guardrail", type=float, default=0.05)
    ap.add_argument("--answerable", default=str(HERE / "tasks" / "suite-answerable-v2.json"))
    ap.add_argument("--traps", default=str(HERE / "tasks" / "suite-traps-v2.json"))
    ap.add_argument("--limit", type=int, default=0, help="cap tasks per suite (calibration)")
    ap.add_argument("--reaggregate", default=None, help="path to a raw jsonl; re-run stats and exit")
    args = ap.parse_args()

    if args.reaggregate:
        raw = [json.loads(l) for l in Path(args.reaggregate).read_text().splitlines() if l.strip()]
        rep = aggregate(raw, args.guardrail)
        print(json.dumps(rep, indent=2))
        return 0

    ts = time.strftime("%Y-%m-%d_%H%M%S")
    (HERE / "results").mkdir(exist_ok=True)
    raw_path = HERE / "results" / f"raw-v2-{ts}.jsonl"
    out = HERE / "results" / f"report-v2-{ts}.json"

    ans = json.loads(Path(args.answerable).read_text())["tasks"]
    trp = json.loads(Path(args.traps).read_text())["tasks"]
    if args.limit:
        ans, trp = ans[:args.limit], trp[:args.limit]
    for t in ans:
        t["_class"] = "answerable"
    for t in trp:
        t["_class"] = "trap"
    tasks = ans + trp

    total = len(ARMS) * len(tasks) * args.reps
    done, t0, raw = 0, time.time(), []
    with raw_path.open("w") as rf:
        for arm in ARMS:
            for task in tasks:
                for rep_i in range(args.reps):
                    prompt = build_prompt(arm, task, args.full_words, args.pruned_words)
                    r = run_turn(prompt, args.model)
                    comp = rb.usage_components(r["usage"])
                    cls = rb.classify(r["text"], task)
                    rec = {"arm": arm, "task": task["id"], "class": task["_class"],
                           "domain": task.get("domain"), "rep": rep_i, "cost_usd": r["cost"],
                           **comp, **cls, "resp_head": r["text"][:400]}
                    raw.append(rec); rf.write(json.dumps(rec) + "\n"); rf.flush()
                    done += 1
                    print(f"[{done}/{total}] {arm:6s} {task['id']:14s} rep{rep_i} "
                          f"in={comp['input_total']:>6d} correct={cls['correct']} "
                          f"abst={int(cls['abstained'])} hal={cls['halluc']}  {r['text'][:45]!r}",
                          flush=True)

    rep = aggregate(raw, args.guardrail)
    meta = {"model": args.model, "reps": args.reps, "n_tasks": len(tasks), "n_ans": len(ans),
            "n_trap": len(trp), "total_calls": total, "wall_s": round(time.time() - t0, 1)}
    rep["meta"] = meta
    out.write_text(json.dumps(rep, indent=2))
    print_summary(rep, meta)
    print(f"report: {out}\nraw:    {raw_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
