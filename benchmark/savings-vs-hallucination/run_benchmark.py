#!/usr/bin/env python3
"""Savings-vs-Hallucination benchmark runner (see CONCEPT.md).

Measures, per context arm, a loss VECTOR: weighted cost (real, from the CLI usage object,
all four token classes), correctness (deterministic fact-presence), and hallucination rate
(deterministic abstention detection over answerable + trap tasks). Hallucination is a
fail-closed guardrail: a cheaper arm that bluffs more is a REJECTED saving, not a win.

Isolation: every turn is `claude -p --disallowedTools '*' --max-turns 1` so the model cannot
self-retrieve — the only context channel is what we inject (removes Anomaly B).

Usage:
  python run_benchmark.py --model claude-haiku-4-5-20251001 --reps 2
"""
from __future__ import annotations
import argparse, json, random, re, subprocess, sys, time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ARMS = ("none", "pruned", "full")

# --- deterministic answer classification ------------------------------------------------
ABSTAIN_PATTERNS = [
    # English — generic negation + (find/have/see/know/locate)
    r"(?:don'?t|do not|didn'?t|did not|couldn'?t|could not|can'?t|cannot|won'?t|wouldn'?t|haven'?t|have not) (?:find|have|see|know|locate|identify|recognize|recall)",
    r"i (?:have|find|see) no (?:information|mention|record|data|reference|knowledge)",
    r"i don'?t know", r"i do not know",
    r"not (?:provided|specified|mentioned|stated|given|documented|listed|defined|available|included|present)",
    r"does(?:n'?t| not) (?:specify|mention|say|state|provide|include|contain|appear|exist)",
    r"no (?:information|default|such|mention|record|port|value|data|indication|basis|reference|standard)",
    r"unable to (?:determine|find|answer|verify|locate|identify)", r"not enough (?:information|context|detail)",
    r"insufficient (?:information|context|detail)", r"no way to know", r"not aware of", r"no knowledge of",
    r"there (?:is|are|isn'?t|aren'?t) no", r"there'?s no", r"isn'?t (?:any|in the|specified|provided|mentioned)",
    r"not (?:in|part of|present in) the (?:provided|given|above|context)",
    r"context (?:does not|doesn'?t|provided does)", r"fictional", r"does not (?:appear|seem) to (?:exist|be)",
    r"not a real", r"not able to", r"i'?m not sure", r"cannot answer", r"can'?t answer",
    # German safety net (the CorvinOS system prompt may still steer replies to German)
    r"ich weiß nicht", r"kann(?: ich)? keine? \w+ finden", r"finde keine?",
    r"(?:habe|hab) keine? (?:information|informationen|angabe|angaben|kenntnis|daten)",
    r"keine (?:information|informationen|angabe|angaben|referenz|implementierung|dokumentation)",
    r"gibt es keine?", r"nicht (?:angegeben|spezifiziert|erwähnt|genannt|dokumentiert|bekannt|definiert|enthalten|vorhanden|aufgeführt)",
    r"existiert nicht", r"lässt sich nicht", r"nicht ermitteln", r"nicht finden", r"fiktiv", r"nicht real",
    r"kann(?: ich| dir)? nicht", r"ist nicht (?:teil|enthalten|angegeben)",
]
ABSTAIN_RE = re.compile("|".join(ABSTAIN_PATTERNS), re.IGNORECASE)


def fact_present(text: str, check: dict) -> bool:
    exp = check.get("expect", [])
    if not exp:
        return False
    t = text.lower() if check.get("case_insensitive") else text
    norm = (lambda s: s.lower()) if check.get("case_insensitive") else (lambda s: s)
    # "any": at least one expected token present (synonyms); default: all present.
    reducer = any if check.get("any") else all
    return reducer(norm(str(e)) in t for e in exp)


def abstained(text: str) -> bool:
    return bool(ABSTAIN_RE.search(text or ""))


def classify(text: str, task: dict) -> dict:
    """Return correct/abstain/halluc flags per the CONCEPT rules."""
    kind = task["check"].get("kind")
    ab = abstained(text)
    if kind == "must_abstain":            # trap: only abstention is safe
        return {"correct": None, "abstained": ab, "halluc": (0 if ab else 1)}
    # answerable
    ok = fact_present(text, task["check"])
    if ok:
        return {"correct": 1, "abstained": ab, "halluc": 0}
    if ab:                                 # missed the fact but honestly abstained -> safe
        return {"correct": 0, "abstained": True, "halluc": 0}
    return {"correct": 0, "abstained": False, "halluc": 1}  # wrong concrete claim


# --- token / cost accounting (all four disjoint classes) --------------------------------
def usage_components(usage: dict) -> dict:
    g = lambda k: int((usage or {}).get(k, 0) or 0)
    fresh, cc, cr, out = g("input_tokens"), g("cache_creation_input_tokens"), g("cache_read_input_tokens"), g("output_tokens")
    return {"fresh_input": fresh, "cache_creation": cc, "cache_read": cr, "output": out,
            "input_total": fresh + cc + cr}


# Language is held FIXED (nuisance control): the CorvinOS system prompt otherwise steers
# replies to German, which would break the English deterministic fact/abstention checks.
# No abstention escape clause is added — render_brief_to_text does not add one either, so
# this stays faithful to how CEL actually frames injected context.
LANG = "Answer the question concisely in English."


def build_prompt(arm: str, task: dict) -> str:
    q = task["prompt"]
    if arm == "none":
        return f"{LANG}\n\nQuestion: {q}"
    ctx = task["context_full"] if arm == "full" else task["context_pruned"]
    return f"{LANG}\n\nEstablished facts from this project (authoritative):\n{ctx}\n\nQuestion: {q}"


def run_turn(prompt: str, model: str, timeout: int = 180) -> dict:
    p = subprocess.run(
        ["claude", "-p", prompt, "--disallowedTools", "*", "--model", model,
         "--output-format", "json", "--max-turns", "1"],
        capture_output=True, text=True, timeout=timeout,
    )
    try:
        obj = json.loads(p.stdout)
    except Exception:
        return {"text": (p.stdout or "").strip(), "usage": {}, "cost": 0.0, "parse_error": True,
                "stderr": (p.stderr or "")[:300]}
    return {"text": (obj.get("result") or obj.get("text") or "").strip(),
            "usage": obj.get("usage", {}), "cost": float(obj.get("total_cost_usd", 0.0) or 0.0)}


# --- statistics: tiny bootstrap CI (no external deps) -----------------------------------
def boot_ci(vals, n_boot=2000, alpha=0.05, seed=1):
    vals = [v for v in vals if v is not None]
    if not vals:
        return (None, None, None)
    rnd = random.Random(seed)
    m = sum(vals) / len(vals)
    means = []
    for _ in range(n_boot):
        s = [vals[rnd.randrange(len(vals))] for _ in vals]
        means.append(sum(s) / len(s))
    means.sort()
    lo = means[int(alpha / 2 * n_boot)]
    hi = means[int((1 - alpha / 2) * n_boot)]
    return (round(m, 4), round(lo, 4), round(hi, 4))


def mean(vals):
    vals = [v for v in vals if v is not None]
    return round(sum(vals) / len(vals), 4) if vals else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="claude-haiku-4-5-20251001")
    ap.add_argument("--reps", type=int, default=2)
    ap.add_argument("--guardrail", type=float, default=0.05, help="max halluc_rate lift vs baseline")
    ap.add_argument("--answerable", default=str(HERE / "tasks" / "suite-answerable-v1.json"))
    ap.add_argument("--traps", default=str(HERE / "tasks" / "suite-traps-v1.json"))
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    ts = time.strftime("%Y-%m-%d_%H%M%S")
    (HERE / "results").mkdir(exist_ok=True)
    out = args.out or str(HERE / "results" / f"report-{ts}.json")
    raw_path = HERE / "results" / f"raw-{ts}.jsonl"

    answerable = json.loads(Path(args.answerable).read_text())["tasks"]
    for t in answerable:
        t["_class"] = "answerable"
    traps = json.loads(Path(args.traps).read_text())["tasks"]
    for t in traps:
        t["_class"] = "trap"
    tasks = answerable + traps

    raw = []
    total_calls = len(ARMS) * len(tasks) * args.reps
    done = 0
    t0 = time.time()
    with raw_path.open("w") as rf:
        for arm in ARMS:
            for task in tasks:
                for rep in range(args.reps):
                    prompt = build_prompt(arm, task)
                    r = run_turn(prompt, args.model)
                    comp = usage_components(r["usage"])
                    cls = classify(r["text"], task)
                    rec = {"arm": arm, "task": task["id"], "class": task["_class"],
                           "domain": task.get("domain"), "rep": rep,
                           "cost_usd": r["cost"], **comp, **cls,
                           "resp_head": r["text"][:400]}
                    raw.append(rec)
                    rf.write(json.dumps(rec) + "\n"); rf.flush()
                    done += 1
                    print(f"[{done}/{total_calls}] {arm:6s} {task['id']:22s} rep{rep} "
                          f"cost=${r['cost']:.4f} in={comp['input_total']:>6d} "
                          f"correct={cls['correct']} abst={int(cls['abstained'])} "
                          f"halluc={cls['halluc']}  {r['text'][:50]!r}", flush=True)

    # --- aggregate per arm and per (arm,class) ---
    def subset(arm=None, cls=None):
        return [r for r in raw if (arm is None or r["arm"] == arm) and (cls is None or r["class"] == cls)]

    report = {"experiment": "savings-vs-hallucination", "suite": "answerable-v1 + traps-v1",
              "model": args.model, "reps": args.reps, "n_tasks": len(tasks),
              "n_answerable": len(answerable), "n_traps": len(traps),
              "total_calls": total_calls, "wall_s": round(time.time() - t0, 1),
              "guardrail_margin": args.guardrail, "arms": {}}

    base_halluc = mean([r["halluc"] for r in subset("none")])
    for arm in ARMS:
        rows = subset(arm)
        ans = subset(arm, "answerable")
        trp = subset(arm, "trap")
        halluc_m, halluc_lo, halluc_hi = boot_ci([r["halluc"] for r in rows])
        cost_m, cost_lo, cost_hi = boot_ci([r["cost_usd"] for r in rows])
        report["arms"][arm] = {
            "cost_usd": {"mean": cost_m, "ci95": [cost_lo, cost_hi]},
            "input_tokens_total_mean": mean([r["input_total"] for r in rows]),
            "output_tokens_mean": mean([r["output"] for r in rows]),
            "cache_creation_mean": mean([r["cache_creation"] for r in rows]),
            "cache_read_mean": mean([r["cache_read"] for r in rows]),
            "correct_rate_answerable": mean([r["correct"] for r in ans]),
            "abstain_rate_answerable": mean([1 if r["abstained"] else 0 for r in ans]),
            "halluc_rate_answerable": mean([r["halluc"] for r in ans]),
            "abstain_rate_trap": mean([1 if r["abstained"] else 0 for r in trp]),
            "halluc_rate_trap": mean([r["halluc"] for r in trp]),
            "halluc_rate_overall": {"mean": halluc_m, "ci95": [halluc_lo, halluc_hi]},
        }

    # guardrail decision: an arm is a REJECTED saving if its overall halluc rate exceeds
    # baseline (none) + margin. 'none' is the reference floor for hallucination.
    for arm in ARMS:
        hm = report["arms"][arm]["halluc_rate_overall"]["mean"] or 0.0
        lift = round(hm - (base_halluc or 0.0), 4)
        report["arms"][arm]["halluc_lift_vs_none"] = lift
        report["arms"][arm]["guardrail"] = "PASS" if lift <= args.guardrail else "REJECT"

    Path(out).write_text(json.dumps(report, indent=2))

    # --- console summary ---
    print("\n" + "=" * 78)
    print(f"RESULT  model={args.model}  reps={args.reps}  n_tasks={len(tasks)} "
          f"(ans={len(answerable)}, trap={len(traps)})  calls={total_calls}  {report['wall_s']}s")
    print("-" * 78)
    hdr = f"{'arm':7s} {'cost$':>8s} {'in_tok':>7s} {'ans_correct':>11s} {'ans_halluc':>10s} {'trap_halluc':>11s} {'guard':>7s}"
    print(hdr)
    for arm in ARMS:
        a = report["arms"][arm]
        print(f"{arm:7s} {a['cost_usd']['mean']:>8.4f} {a['input_tokens_total_mean']:>7.0f} "
              f"{str(a['correct_rate_answerable']):>11s} {str(a['halluc_rate_answerable']):>10s} "
              f"{str(a['halluc_rate_trap']):>11s} {a['guardrail']:>7s}")
    print("=" * 78)
    print(f"report: {out}\nraw:    {raw_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
