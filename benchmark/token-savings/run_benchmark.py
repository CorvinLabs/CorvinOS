#!/usr/bin/env python3
"""Token-savings A/B benchmark — REAL runs, no invented data.

Arm A (baseline) = CEL off  (`vibe_engineering=false`)
Arm B (vibe)     = CEL on   (`vibe_engineering=true`)

For each task the SAME prompt is run n times per arm through the real console turn path
(`chat_runtime.stream_turn`); real worker token usage is captured from the result event.
Each answer is scored by an OBJECTIVE fact-presence check, so a token cut bought by a worse
answer is dropped from the savings (never sold). Statistics (bootstrap 95% CI + Mann-Whitney-U)
come from `stats.py`. Raw per-run records are written to results/ as the evidence trail.

`--dry-run` validates wiring (imports, task load, flag toggle) WITHOUT any LLM call and
produces NO savings numbers — honest by construction.

Usage:
  python run_benchmark.py --n 20 --tenant _bench_tokensave
  python run_benchmark.py --dry-run
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import stats  # noqa: E402  (local module)
# Canonical 4-class token summation (fresh + cache-creation + cache-read + output) — the ONE
# place that sums usage correctly, shared with the dashboard fix. Reading only input_tokens
# (as the first draft did) undercounts real input by ~99.99% on a cached turn.
from core.learning.token_accounting import token_components, total_tokens  # noqa: E402
import pricing  # noqa: E402  (local: cache-aware cost, real prices from prices.json)


def quality_score(text: str, check: dict) -> float:
    """Objective quality in [0,1] from a fact-presence check. No LLM, no human."""
    t = text.lower() if check.get("case_insensitive") else text
    exp = check.get("expect", [])
    if not exp:
        return 1.0
    norm = (lambda s: s.lower()) if check.get("case_insensitive") else (lambda s: s)
    present = sum(1 for e in exp if norm(str(e)) in t)
    base = present / len(exp)
    forbid = check.get("forbid", [])
    if forbid and any(norm(str(f)) in t for f in forbid):
        base *= 0.5  # penalise wrong extras, don't zero (partial credit stays honest)
    return max(0.0, min(1.0, base))


def load_suite(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    assert "tasks" in data and isinstance(data["tasks"], list), "suite has no tasks[]"
    for tk in data["tasks"]:
        assert {"id", "type", "check"} <= set(tk), f"task missing keys: {tk.get('id')}"
        assert ("prompt" in tk) or (isinstance(tk.get("turns"), list) and tk["turns"]), \
            f"task {tk.get('id')} needs 'prompt' (single-turn) or non-empty 'turns' (multi-turn)"
    return data


async def _one_turn(cr, sess, prompt: str) -> tuple[str, dict]:
    """One real turn in an existing session → (answer_text, raw_usage_dict). Usage is the
    worker's real usage from the result event (never chars/4). NOTE (review finding 4): this
    trusts the terminal usage to be cumulative over internal tool rounds — true for the
    no-tool suites; a tool-using suite must re-verify before its numbers are trusted."""
    text_parts, usage = [], None
    async for ev in cr.stream_turn(sess, prompt):
        et = ev.get("type")
        if et in ("delta", "result") and ev.get("text"):
            text_parts.append(ev["text"])
        if ev.get("usage"):
            usage = ev["usage"]
    return "".join(text_parts), (usage or {})


async def _run_task(cr, tenant: str, task: dict, model: str, prices: dict) -> tuple[str, dict, dict, "float | None"]:
    """Run a whole TASK. Single-turn tasks have `prompt`; multi-turn tasks have `turns` (a
    list of prompts) run SEQUENTIALLY in ONE session — the unit of measurement is the task,
    so tokens are SUMMED across its turns (this is where CEL's cross-turn value, if any, shows:
    injected context that avoids re-derivation over the conversation). Quality is checked on
    the FINAL answer. Returns (final_text, summed_components, extra, total_cost_or_None)."""
    prompts = task.get("turns") or [task["prompt"]]
    sess = cr.create_session(tenant, "bench")
    summed = {"fresh_input": 0, "cache_creation": 0, "cache_read": 0, "output": 0}
    c5_sum = c1_sum = 0
    cost_sum: "float | None" = 0.0
    final_text = ""
    for p in prompts:
        final_text, usage = await _one_turn(cr, sess, p)
        comp = token_components(usage)
        for k in summed:
            summed[k] += comp[k]
        c5, c1 = pricing.creation_split(usage)
        c5_sum += c5
        c1_sum += c1
        turn_cost = pricing.cost_usd(usage, model, prices)
        cost_sum = None if (turn_cost is None or cost_sum is None) else cost_sum + turn_cost
    return final_text, summed, {"c5": c5_sum, "c1": c1_sum, "n_turns": len(prompts)}, cost_sum


def _set_cel(ff, tenant: str, on: bool) -> None:
    ov = ff._read_overlay(tenant)
    flags = dict(ov.get("flags") or {})
    flags["vibe_engineering"] = bool(on)
    ff._write_overlay(tenant, {**ov, "flags": flags})


async def run(args) -> int:
    suite = load_suite(Path(args.tasks))
    print(f"suite {suite.get('suite_version')} · {len(suite['tasks'])} tasks · n={args.n} · tenant={args.tenant}")

    # Import the REAL console turn path + flags. Fail loudly if the env is wrong —
    # never silently fall back to something that would fabricate numbers.
    try:
        import corvin_console.chat_runtime as cr
        import corvin_core.feature_flags as ff
    except Exception as e:  # noqa: BLE001
        print(f"ERROR: cannot import the console turn path ({e}).\n"
              f"Run via ./run_benchmark.sh so PYTHONPATH/CORVIN_HOME are set.", file=sys.stderr)
        return 2

    if args.dry_run:
        # Validate wiring only: can we toggle the flag and see it resolve? NO LLM call,
        # NO numbers — a dry-run must never produce a savings figure.
        _set_cel(ff, args.tenant, True);  on = ff.is_enabled("vibe_engineering", args.tenant)
        _set_cel(ff, args.tenant, False); off = ff.is_enabled("vibe_engineering", args.tenant)
        ok = (on is True and off is False)
        # suite-independent self-test of the objective check
        q_ok = (quality_score("value is 129 here", {"kind": "contains_all", "expect": ["129"]}) == 1.0
                and quality_score("no match", {"kind": "contains_all", "expect": ["129"]}) == 0.0)
        n_mt = sum(1 for t in suite["tasks"] if t.get("turns"))
        print(f"dry-run: flag toggles correctly = {ok}; quality-check self-test = {q_ok}; "
              f"tasks={len(suite['tasks'])} ({n_mt} multi-turn)")
        print("dry-run produced NO benchmark data (by design).")
        return 0 if (ok and q_ok) else 1

    ts = time.strftime("%Y%m%d-%H%M%S", time.gmtime(0))  # stamped by caller; gmtime(0) placeholder
    raw_path = Path(args.out) / f"raw-{args.run_id}.jsonl"
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    runs: list[dict] = []
    with raw_path.open("w", encoding="utf-8") as fh:
        for tk in suite["tasks"]:
            for arm, cel_on in (("A", False), ("B", True)):
                _set_cel(ff, args.tenant, cel_on)
                for rep in range(args.n):
                    text, comp, extra, cost = await _run_task(cr, args.tenant, tk, args.model, args.prices)
                    total = comp["fresh_input"] + comp["cache_creation"] + comp["cache_read"] + comp["output"]
                    rec = {
                        "task_id": tk["id"], "task_type": tk["type"], "arm": arm,
                        "cel_on": cel_on, "rep": rep, "n_turns": extra["n_turns"],
                        **comp, "cache_creation_5m": extra["c5"], "cache_creation_1h": extra["c1"],
                        "input_all": total - comp["output"], "tokens_total": total,
                        "cost_usd": cost, "quality": quality_score(text, tk["check"]),
                    }
                    runs.append(rec)
                    fh.write(json.dumps(rec) + "\n")
                    print(f"  {tk['id']} arm {arm} rep {rep} ({extra['n_turns']}t): total={total} "
                          f"(fresh={comp['fresh_input']} +create={comp['cache_creation']} "
                          f"+read={comp['cache_read']} +out={comp['output']}) q={rec['quality']:.2f}")

    report = build_report(runs, suite, args)
    rep_path = Path(args.out) / f"report-{args.run_id}.json"
    rep_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print_report(report)
    print(f"\nraw evidence: {raw_path}\nreport: {rep_path}")
    return 0


def build_report(runs: list[dict], suite: dict, args) -> dict:
    """Pair A/B runs per (task, rep), apply the quality gate, and summarise per task type
    and overall. All numbers via stats.py — nothing invented."""
    by = {}
    for r in runs:
        by.setdefault((r["task_id"], r["rep"]), {})[r["arm"]] = r
    pairs = []
    for (task_id, _rep), am in by.items():
        if "A" in am and "B" in am:
            pairs.append({
                "task_id": task_id, "task_type": am["A"]["task_type"],
                "a_tokens": am["A"]["tokens_total"], "b_tokens": am["B"]["tokens_total"],
                "a_quality": am["A"]["quality"], "b_quality": am["B"]["quality"],
            })
    per_type = {}
    for tt in sorted({p["task_type"] for p in pairs}):
        s = stats.summarise([p for p in pairs if p["task_type"] == tt], tt, seed=args.seed)
        per_type[tt] = s.__dict__
    overall = stats.summarise(pairs, "overall", seed=args.seed).__dict__
    # Per-arm component means — the raw-vs-cost story lives here (cache classes priced
    # differently). Reported alongside the raw-token savings, never collapsed into one number.
    def _arm_components(arm: str) -> dict:
        rs = [r for r in runs if r["arm"] == arm]
        n = max(1, len(rs))
        out = {k: round(sum(r[k] for r in rs) / n, 1)
               for k in ("fresh_input", "cache_creation", "cache_read", "output", "tokens_total")}
        costs = [r["cost_usd"] for r in rs if r.get("cost_usd") is not None]
        out["cost_usd"] = round(sum(costs) / len(costs), 6) if costs else None
        return out
    total_input_all = sum(r["input_all"] for r in runs)
    cost_available = pricing.prices_available(args.model, args.prices)
    return {
        "suite_version": suite.get("suite_version"), "n_per_arm": args.n,
        "tenant": args.tenant, "run_id": args.run_id,
        "model": args.model, "cost_available": cost_available,
        # Guard now keys on the SUMMED input (all 3 classes), not fresh input_tokens — the old
        # guard false-passed because input_tokens=2>0 while 62k cache tokens were uncounted.
        "input_captured": total_input_all > 0,
        "components_per_arm": {"A_cel_off": _arm_components("A"), "B_cel_on": _arm_components("B")},
        "note": "tokens_total = fresh_input + cache_creation + cache_read + output (all 4 classes). "
                "Savings (raw count) = (median A − median B)/median A, quality-gated, bootstrap 95% CI, "
                "Mann-Whitney-U (B<A). RAW COUNT ≠ COST: cache-read ~0.1x, cache-creation ~1.25x, "
                "output ~5x — apply your model's prices to components_per_arm for a cost figure. "
                "suite-v1 is single-turn: it measures CEL's per-task COLD cost (always pays "
                "cache_creation, never amortized) — CEL's best case (warm reuse over a session) needs "
                "multi-turn tasks, not yet in the suite.",
        "overall": overall, "per_type": per_type,
    }


def print_report(rep: dict) -> None:
    o = rep["overall"]
    print("\n" + "=" * 60)
    print("TOKEN-SAVINGS BENCHMARK — measured, quality-gated")
    print("=" * 60)
    if not rep.get("input_captured", True):
        print("!! WARNING: the worker reported ZERO input across all runs (all 4 classes). "
              "Something is broken — do not trust these numbers.\n")
    if o["n_pairs"] == 0:
        print("No quality-valid pairs — nothing to claim (all B answers were worse than A).")
        return
    if o["significant"]:
        print(f"OVERALL: {o['savings_point']*100:.1f}% saved  "
              f"(95% CI {o['ci_low']*100:.1f}%..{o['ci_high']*100:.1f}%, "
              f"p={o['p_value']:.4f}, n={o['n_pairs']}) — SIGNIFICANT")
    else:
        print(f"OVERALL: no significant saving measured "
              f"(point {o['savings_point']*100:.1f}%, 95% CI {o['ci_low']*100:.1f}%..{o['ci_high']*100:.1f}%, "
              f"p={o['p_value']:.4f}, n={o['n_pairs']}) — DO NOT claim a saving")
    if o["dropped_quality"]:
        print(f"  ({o['dropped_quality']} pair(s) dropped: B answered worse than A)")
    for tt, s in rep["per_type"].items():
        tag = "sig" if s["significant"] else "n.s."
        print(f"  {tt:12s}: {s['savings_point']*100:6.1f}%  "
              f"[{s['ci_low']*100:.1f}%..{s['ci_high']*100:.1f}%]  p={s['p_value']:.3f}  n={s['n_pairs']}  {tag}")
    # Component breakdown — this is where 'raw count' and 'cost' diverge.
    cp = rep.get("components_per_arm", {})
    if cp:
        print("\nmean tokens per run (RAW COUNT — cost differs: read~0.1x, create~1.25x, out~5x):")
        print(f"  {'arm':14s} {'fresh':>8s} {'cache_cr':>9s} {'cache_rd':>9s} {'output':>8s} {'total':>9s}")
        for arm, c in cp.items():
            print(f"  {arm:14s} {c['fresh_input']:8.0f} {c['cache_creation']:9.0f} "
                  f"{c['cache_read']:9.0f} {c['output']:8.0f} {c['tokens_total']:9.0f}")
        # Cost view — the honest headline (cache-weighted). Blank until real prices are filled.
        a, b = cp.get("A_cel_off", {}), cp.get("B_cel_on", {})
        if rep.get("cost_available") and a.get("cost_usd") and b.get("cost_usd"):
            ac, bc = a["cost_usd"], b["cost_usd"]
            saved = (ac - bc) / ac * 100 if ac else 0.0
            print(f"\nCOST per run ({rep.get('model')}, cache-weighted): "
                  f"A=${ac:.5f}  B=${bc:.5f}  → {saved:+.1f}% "
                  f"({'CHEAPER' if saved>0 else 'more expensive'})")
        else:
            print(f"\nCOST: not shown — fill real prices for '{rep.get('model')}' in prices.json "
                  f"(raw components above; cache-read ~0.1x, create ~1.25–2x, output ~5x).")


def main() -> int:
    ap = argparse.ArgumentParser(description="Token-savings A/B benchmark (real data).")
    ap.add_argument("--n", type=int, default=20, help="runs per arm per task")
    ap.add_argument("--tasks", default=str(HERE / "tasks" / "suite-v1.json"))
    ap.add_argument("--tenant", default="_bench_tokensave", help="throwaway benchmark tenant")
    ap.add_argument("--seed", type=int, default=12345, help="bootstrap seed (reproducible CI)")
    ap.add_argument("--out", default=str(HERE / "results"))
    ap.add_argument("--run-id", default=time.strftime("%Y%m%d-%H%M%S"))
    ap.add_argument("--model", default="", help="worker model for pricing (default: read from tenant config)")
    ap.add_argument("--prices", default=str(HERE / "prices.json"))
    ap.add_argument("--dry-run", action="store_true", help="validate wiring only, no LLM, no numbers")
    args = ap.parse_args()
    args.prices = pricing.load_prices(args.prices)
    if not args.model:
        args.model = _worker_model(args.tenant) or "claude-opus-5"
    return asyncio.run(run(args))


def _worker_model(tenant: str) -> "str | None":
    """The worker model from tenant.corvin.yaml (what the benchmark turn actually runs on)."""
    try:
        import os
        import yaml  # noqa: PLC0415
        home = os.environ.get("CORVIN_HOME", str(Path.home() / ".corvin"))
        cfg = Path(home) / "tenants" / tenant / "global" / "tenant.corvin.yaml"
        if not cfg.is_file():
            cfg = Path(home) / "tenants" / "_default" / "global" / "tenant.corvin.yaml"
        data = yaml.safe_load(cfg.read_text(encoding="utf-8")) or {}
        return (((data.get("spec") or {}).get("engine_models") or {})
                .get("claude_code") or {}).get("worker_model")
    except Exception:  # noqa: BLE001
        return None


if __name__ == "__main__":
    raise SystemExit(main())
