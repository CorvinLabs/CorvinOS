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


# Quiet-turn confounders: features that fire EXTRA per-turn work (a discarded TDE shadow turn,
# a per-turn cloud synthesis, an outcome-grading write) and so add latency + token noise that is
# ORTHOGONAL to the cache-stable relocation under test. The cachestable A/B forces them OFF for
# BOTH arms so the delta isolates the fix (the deterministic CEL brief's cache class), not these.
# They are in every cachestable arm's flag-map, so the runner's save/restore restores them too.
_QUIET_TURN = {
    "tde_shadow_measurement": False,   # ADR-0392: else each native turn spawns a discarded TDE turn
    "vibe_engineering_active": False,  # ADR-0282/0283: else a per-turn cloud LLM synthesis runs
    "outcome_feedback_loop": False,    # per-turn grade/record write — off during measurement
}

# Arm definitions. Each arm is a flag-map applied before its runs. The classic mode measures
# CEL on/off; the cachestable mode holds CEL ON for BOTH arms and toggles ONLY the ADR-0395
# cache-stable relocation, so the delta isolates the fix (cache_creation collapse), not CEL.
ARM_MODES = {
    "cel": {
        "A": {"vibe_engineering": False},
        "B": {"vibe_engineering": True},
    },
    "cachestable": {
        "A": {"vibe_engineering": True, "cel_cache_stable": False, **_QUIET_TURN},
        "B": {"vibe_engineering": True, "cel_cache_stable": True, **_QUIET_TURN},
    },
}


def _apply_arm(ff, tenant: str, flagmap: dict) -> None:
    ov = ff._read_overlay(tenant)
    flags = dict(ov.get("flags") or {})
    for k, v in flagmap.items():
        flags[k] = bool(v)
    ff._write_overlay(tenant, {**ov, "flags": flags})


def _set_cel(ff, tenant: str, on: bool) -> None:
    _apply_arm(ff, tenant, {"vibe_engineering": bool(on)})


def _cleanup_bench_sessions(cr, tenant: str) -> None:
    """Delete the throwaway 'bench'-titled sessions this run created — no residue on _default."""
    try:
        for s in cr.list_sessions(tenant):
            if getattr(s, "title", "") == "bench":
                cr.delete_session(tenant, s.sid)
    except Exception:  # noqa: BLE001 — cleanup is best-effort
        pass


def _acquire_lock(out_dir: Path, tenant: str) -> "Path | None":
    """Per-tenant lock so two concurrent runs can't clobber each other's A/B flag toggles on
    the SHARED overlay (the exact corruption that ruined the first attempt). Returns the lock
    path on success, None if another LIVE run holds it. A stale lock (dead PID) is stolen."""
    import os as _os
    lock = out_dir / f".bench-lock-{tenant}"
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        fd = _os.open(str(lock), _os.O_CREAT | _os.O_EXCL | _os.O_WRONLY, 0o644)
        _os.write(fd, str(_os.getpid()).encode()); _os.close(fd)
        return lock
    except FileExistsError:
        try:
            holder = int(lock.read_text().strip() or "0")
        except Exception:  # noqa: BLE001
            holder = 0
        if holder and holder != _os.getpid():
            try:
                _os.kill(holder, 0)
                return None  # a live run holds it
            except OSError:
                pass  # stale — steal it
        lock.write_text(str(_os.getpid()))
        return lock


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
        # Validate wiring only: do BOTH arms of the selected mode apply and resolve? NO LLM
        # call, NO numbers — a dry-run must never produce a savings figure. The distinguishing
        # flag is the one whose value differs between arm A and arm B.
        mode = ARM_MODES[args.arms]
        _distinct = next(k for k in {*mode["A"], *mode["B"]}
                         if mode["A"].get(k) != mode["B"].get(k))
        # Save/restore even here: a dry-run must leave the tenant's flags EXACTLY as found
        # (it applies both arms only to prove they resolve — it must not persist that).
        _dov = ff._read_overlay(args.tenant)
        _dsaved = dict(_dov.get("flags") or {})
        _dtouched = sorted({k for fm in mode.values() for k in fm})
        try:
            _apply_arm(ff, args.tenant, mode["A"]); off = ff.is_enabled(_distinct, args.tenant)
            _apply_arm(ff, args.tenant, mode["B"]); on = ff.is_enabled(_distinct, args.tenant)
        finally:
            _cur = ff._read_overlay(args.tenant); _cf = dict(_cur.get("flags") or {})
            for k in _dtouched:
                if k in _dsaved:
                    _cf[k] = _dsaved[k]
                else:
                    _cf.pop(k, None)
            ff._write_overlay(args.tenant, {**_cur, "flags": _cf})
        ok = (on is True and off is False)
        print(f"arms mode '{args.arms}': distinguishing flag = {_distinct}")
        # suite-independent self-test of the objective check
        q_ok = (quality_score("value is 129 here", {"kind": "contains_all", "expect": ["129"]}) == 1.0
                and quality_score("no match", {"kind": "contains_all", "expect": ["129"]}) == 0.0)
        n_mt = sum(1 for t in suite["tasks"] if t.get("turns"))
        print(f"dry-run: flag toggles correctly = {ok}; quality-check self-test = {q_ok}; "
              f"tasks={len(suite['tasks'])} ({n_mt} multi-turn)")
        print("dry-run produced NO benchmark data (by design).")
        return 0 if (ok and q_ok) else 1

    # Concurrency guard: a second run on the same tenant would clobber this run's per-arm flag
    # toggles on the SHARED overlay (the corruption that ruined the first attempt). Refuse rather
    # than silently produce mixed-arm garbage.
    _lock = _acquire_lock(Path(args.out), args.tenant)
    if _lock is None:
        print(f"ERROR: another benchmark run holds the lock for tenant '{args.tenant}'. "
              f"Wait for it or remove {Path(args.out) / ('.bench-lock-' + args.tenant)} if stale.",
              file=sys.stderr)
        return 3

    raw_path = Path(args.out) / f"raw-{args.run_id}.jsonl"
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    runs: list[dict] = []
    mode = ARM_MODES[args.arms]
    _touched = sorted({k for fm in mode.values() for k in fm})  # every flag this run may change
    # Save every touched flag's real value so a run on _default leaves the tenant untouched.
    _orig_overlay = ff._read_overlay(args.tenant)
    _orig_flags = dict(_orig_overlay.get("flags") or {})
    _saved = {k: (k in _orig_flags, _orig_flags.get(k)) for k in _touched}
    try:
        with raw_path.open("w", encoding="utf-8") as fh:
            for tk in suite["tasks"]:
                for arm in ("A", "B"):
                    cel_on = mode[arm].get("vibe_engineering", False)
                    _apply_arm(ff, args.tenant, mode[arm])
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
    finally:
        # Leave every touched flag exactly as it was, and delete the throwaway bench sessions —
        # a run on _default must have no lasting side effect.
        cur = ff._read_overlay(args.tenant)
        flags = dict(cur.get("flags") or {})
        for k, (had, val) in _saved.items():
            if had:
                flags[k] = val
            else:
                flags.pop(k, None)
        ff._write_overlay(args.tenant, {**cur, "flags": flags})
        _cleanup_bench_sessions(cr, args.tenant)
        try:
            _lock.unlink()
        except Exception:  # noqa: BLE001 — lock release is best-effort
            pass

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
    # Arm labels reflect what actually differs between A and B in this run's mode.
    a_label, b_label = (("A_fix_off", "B_fix_on") if args.arms == "cachestable"
                        else ("A_cel_off", "B_cel_on"))
    return {
        "suite_version": suite.get("suite_version"), "n_per_arm": args.n,
        "tenant": args.tenant, "run_id": args.run_id, "arms": args.arms,
        # Confounders held OFF for BOTH arms so the delta isolates the fix, not extra per-turn work.
        "measurement_isolation": (sorted(_QUIET_TURN) if args.arms == "cachestable" else []),
        "model": args.model, "cost_available": cost_available,
        # Guard now keys on the SUMMED input (all 3 classes), not fresh input_tokens — the old
        # guard false-passed because input_tokens=2>0 while 62k cache tokens were uncounted.
        "input_captured": total_input_all > 0,
        "components_per_arm": {a_label: _arm_components("A"), b_label: _arm_components("B")},
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
        # Arms are ordered A then B by insertion; label-agnostic so both modes print correctly.
        _arm_items = list(cp.items())
        (a_lbl, a), (b_lbl, b) = _arm_items[0], _arm_items[1]
        if rep.get("cost_available") and a.get("cost_usd") and b.get("cost_usd"):
            ac, bc = a["cost_usd"], b["cost_usd"]
            saved = (ac - bc) / ac * 100 if ac else 0.0
            print(f"\nCOST per run ({rep.get('model')}, cache-weighted): "
                  f"{a_lbl}=${ac:.5f}  {b_lbl}=${bc:.5f}  → {saved:+.1f}% "
                  f"({'CHEAPER' if saved>0 else 'more expensive'})")
        else:
            print(f"\nCOST: not shown — fill real prices for '{rep.get('model')}' in prices.json "
                  f"(raw components above; cache-read ~0.1x, create ~1.25–2x, output ~5x).")


def main() -> int:
    ap = argparse.ArgumentParser(description="Token-savings A/B benchmark (real data).")
    ap.add_argument("--n", type=int, default=20, help="runs per arm per task")
    ap.add_argument("--arms", choices=sorted(ARM_MODES), default="cel",
                    help="cel: CEL off(A) vs on(B). cachestable: CEL on both, ADR-0395 fix off(A) vs on(B).")
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
