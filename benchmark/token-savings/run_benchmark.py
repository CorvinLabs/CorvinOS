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
        assert {"id", "type", "prompt", "check"} <= set(tk), f"task missing keys: {tk.get('id')}"
    return data


async def _run_one(cr, tenant: str, prompt: str) -> tuple[str, int, int]:
    """One real turn → (answer_text, input_tokens, output_tokens). Tokens are the WORKER's
    real usage from the result event (never chars/4, never an estimate)."""
    sess = cr.create_session(tenant, "bench")
    text_parts, usage = [], None
    async for ev in cr.stream_turn(sess, prompt):
        et = ev.get("type")
        if et in ("delta", "result") and ev.get("text"):
            text_parts.append(ev["text"])
        if ev.get("usage"):
            usage = ev["usage"]
    text = "".join(text_parts)
    ti = int((usage or {}).get("input_tokens", 0) or 0)
    to = int((usage or {}).get("output_tokens", 0) or 0)
    return text, ti, to


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
        print(f"dry-run: flag toggles correctly = {ok}; quality-check self-test = "
              f"{quality_score('the answer is 404 and 401', suite['tasks'][0]['check']) == 1.0}")
        print("dry-run produced NO benchmark data (by design).")
        return 0 if ok else 1

    ts = time.strftime("%Y%m%d-%H%M%S", time.gmtime(0))  # stamped by caller; gmtime(0) placeholder
    raw_path = Path(args.out) / f"raw-{args.run_id}.jsonl"
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    runs: list[dict] = []
    with raw_path.open("w", encoding="utf-8") as fh:
        for tk in suite["tasks"]:
            for arm, cel_on in (("A", False), ("B", True)):
                _set_cel(ff, args.tenant, cel_on)
                for rep in range(args.n):
                    text, ti, to = await _run_one(cr, args.tenant, tk["prompt"])
                    rec = {
                        "task_id": tk["id"], "task_type": tk["type"], "arm": arm,
                        "cel_on": cel_on, "rep": rep, "cold": rep == 0,
                        "tokens_in": ti, "tokens_out": to, "tokens_total": ti + to,
                        "quality": quality_score(text, tk["check"]),
                    }
                    runs.append(rec)
                    fh.write(json.dumps(rec) + "\n")
                    print(f"  {tk['id']} arm {arm} rep {rep}: {ti+to} tok, q={rec['quality']:.2f}")

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
    total_in = sum(r["tokens_in"] for r in runs)
    total_out = sum(r["tokens_out"] for r in runs)
    return {
        "suite_version": suite.get("suite_version"), "n_per_arm": args.n,
        "tenant": args.tenant, "run_id": args.run_id,
        "total_input_tokens": total_in, "total_output_tokens": total_out,
        # Validity guard: CEL's main cost is a BIGGER input prompt. If the worker usage did
        # not report input_tokens (total_input==0), we measured OUTPUT ONLY — which hides
        # CEL's input cost and can OVERSTATE savings. Flag it loudly; don't silently ship it.
        "input_captured": total_in > 0,
        "note": "Savings = (median A tokens − median B tokens) / median A, quality-gated. "
                "CI = bootstrap 95%. p = Mann-Whitney-U one-sided (B<A). "
                "significant = p<0.05 AND ci_low>0. Model id + suite version make it reproducible.",
        "overall": overall, "per_type": per_type,
    }


def print_report(rep: dict) -> None:
    o = rep["overall"]
    print("\n" + "=" * 60)
    print("TOKEN-SAVINGS BENCHMARK — measured, quality-gated")
    print("=" * 60)
    if not rep.get("input_captured", True):
        print("!! WARNING: input_tokens were NOT captured (worker reported 0). CEL's main")
        print("!! cost is a BIGGER INPUT prompt — measuring output only can OVERSTATE savings.")
        print("!! Fix input-token capture before claiming any saving from this run.\n")
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


def main() -> int:
    ap = argparse.ArgumentParser(description="Token-savings A/B benchmark (real data).")
    ap.add_argument("--n", type=int, default=20, help="runs per arm per task")
    ap.add_argument("--tasks", default=str(HERE / "tasks" / "suite-v1.json"))
    ap.add_argument("--tenant", default="_bench_tokensave", help="throwaway benchmark tenant")
    ap.add_argument("--seed", type=int, default=12345, help="bootstrap seed (reproducible CI)")
    ap.add_argument("--out", default=str(HERE / "results"))
    ap.add_argument("--run-id", default=time.strftime("%Y%m%d-%H%M%S"))
    ap.add_argument("--dry-run", action="store_true", help="validate wiring only, no LLM, no numbers")
    args = ap.parse_args()
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
