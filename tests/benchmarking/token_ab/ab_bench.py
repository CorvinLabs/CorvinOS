#!/usr/bin/env python3
"""Reproducible A/B token benchmark harness (stdlib only).

Design (see CONCEPT-0122 in Corvin-Knowledge):
  * fixed corpus (corpus/tasks_<profile>.jsonl, SHA-256 pinned in corpus/MANIFEST.json), deterministic graders, no LLM judge
  * paired design: every task is run on every arm, `reps` times, arm order shuffled per (task, rep) with the seed
  * pinned model ids (config/models.json); live runs refuse unpinned or alias ids
  * cost = measured tokens in four components (input/output/cache_read/cache_write) and cost_usd from the CLI envelope
  * decision = cluster bootstrap over tasks (seeded): savings CI and non-inferiority of the success rate
  * record/replay: `run` appends raw.jsonl; `analyze` reads ONLY raw.jsonl, so statistics replay bit for bit

Usage:
  ab_bench.py run --runner fake|claude --profile smoke|full [--seed N] [--reps K] [--arms a,b,...] [--tasks-per-category n]
                  [--run-id ID] [--results-dir DIR] [--max-calls N] [--max-cost-usd X] [--allow-code-exec] [--resume]
                  [--claude-arg ARG ...]
  ab_bench.py analyze --run-dir DIR [--bootstrap B]
  ab_bench.py power --p0 0.9 --delta 0.05 [--alpha 0.05] [--power 0.8]
  ab_bench.py selftest

HONESTY: the `fake` runner draws from config/sim.json, arbitrary fixtures. Its output is a test of the harness, never a
measurement, and every report made from it carries a SIMULATION banner.
"""
import argparse
import hashlib
import itertools
import json
import math
import os
import random
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
CORPUS_DIR = HERE / "corpus"
CONFIG_DIR = HERE / "config"
COMPONENTS = ("input", "output", "cache_read", "cache_write")


def die(msg):
    raise SystemExit(f"ab_bench: {msg}")


def sha256_bytes(b):
    return hashlib.sha256(b).hexdigest()


def sha256_file(p):
    return sha256_bytes(Path(p).read_bytes())


def canon(obj):
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


# --------------------------------------------------------------------------------------------- corpus

def load_corpus(profile):
    man = json.loads((CORPUS_DIR / "MANIFEST.json").read_text(encoding="utf-8"))
    ent = man["profiles"].get(profile)
    if not ent:
        die(f"profile {profile!r} not in corpus/MANIFEST.json")
    path = CORPUS_DIR / ent["file"]
    digest = sha256_file(path)
    if digest != ent["sha256"]:
        die(f"corpus hash mismatch for {path.name}: {digest} != {ent['sha256']} (corpus is pinned, regenerate with gen_corpus.py)")
    tasks = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return tasks, digest


def select_tasks(tasks, per_category):
    if not per_category:
        return list(tasks)
    seen, out = {}, []
    for t in tasks:
        seen[t["category"]] = seen.get(t["category"], 0) + 1
        if seen[t["category"]] <= per_category:
            out.append(t)
    return out


# --------------------------------------------------------------------------------------------- graders

ANSWER_RE = re.compile(r"^\s*ANSWER:\s*(.+?)\s*$", re.M)
CODE_RE = re.compile(r"```(?:python|py)?[ \t]*\n(.*?)```", re.S)


def extract_answer(text):
    m = ANSWER_RE.findall(text or "")
    return m[-1] if m else None


def extract_code(text):
    m = CODE_RE.findall(text or "")
    return m[-1] if m else None


def _norm(s):
    return str(s).strip().strip("`'\"").strip().rstrip(".").casefold()


_RUNNER = (
    "import json, sys\n"
    "ns = {}\n"
    "exec(compile(open('cand.py').read(), 'cand.py', 'exec'), ns)\n"
    "f = ns['f']\n"
    "tests = json.load(open('tests.json'))\n"
    "ok = 0\n"
    "for args, exp in tests:\n"
    "    try:\n"
    "        r = json.loads(json.dumps(f(*args)))\n"
    "    except Exception:\n"
    "        continue\n"
    "    ok += (r == exp)\n"
    "print(json.dumps({'ok': ok, 'n': len(tests)}))\n"
)


def _limits():  # pragma: no cover - runs in the child
    try:
        import resource
        resource.setrlimit(resource.RLIMIT_CPU, (5, 5))
        resource.setrlimit(resource.RLIMIT_AS, (1 << 29, 1 << 29))
        resource.setrlimit(resource.RLIMIT_FSIZE, (1 << 20, 1 << 20))
    except Exception:
        pass


def bwrap_available():
    return shutil.which("bwrap") is not None and os.path.exists("/usr/bin/python3")


def _bwrap_argv(workdir):
    """bubblewrap command line: no network, no host filesystem except a read-only /usr, a read-only work directory,
    a private tmpfs, a fresh /proc and /dev, gone when the parent dies."""
    argv = ["bwrap", "--unshare-all", "--die-with-parent", "--new-session", "--ro-bind", "/usr", "/usr"]
    for p in ("/lib", "/lib64", "/bin", "/sbin"):
        if os.path.islink(p):
            argv += ["--symlink", os.readlink(p), p]
        elif os.path.isdir(p):
            argv += ["--ro-bind", p, p]
    argv += ["--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp", "--ro-bind", str(workdir), "/work", "--chdir", "/work",
             "--setenv", "PYTHONHASHSEED", "0", "--setenv", "PATH", "/usr/bin:/bin", "/usr/bin/python3", "-I", "run.py"]
    return argv


def run_code_tests(code, tests, timeout=8, sandbox="auto"):
    """Run candidate code against tests in a throwaway directory.

    sandbox="auto" uses bubblewrap when installed (no network, read-only filesystem, private /tmp) and otherwise falls
    back to `python3 -I` with CPU/memory/file-size rlimits and a timeout, which is NOT a sandbox. "bwrap" refuses to run
    without bubblewrap. A live run requires bubblewrap (check_live_ready). Fail closed: any error counts as no test passed.
    """
    use_bwrap = sandbox == "bwrap" or (sandbox == "auto" and bwrap_available())
    if sandbox == "bwrap" and not bwrap_available():
        die("sandbox=bwrap requested but bubblewrap (bwrap) or /usr/bin/python3 is missing")
    with tempfile.TemporaryDirectory(prefix="abb-") as d:
        Path(d, "cand.py").write_text(code, encoding="utf-8")
        Path(d, "tests.json").write_text(json.dumps(tests), encoding="utf-8")
        Path(d, "run.py").write_text(_RUNNER, encoding="utf-8")
        try:
            if use_bwrap:
                cp = subprocess.run(_bwrap_argv(d), capture_output=True, text=True, timeout=timeout, preexec_fn=_limits)
            else:
                cp = subprocess.run([sys.executable, "-I", "run.py"], cwd=d, capture_output=True, text=True, timeout=timeout,
                                    env={"PATH": os.environ.get("PATH", ""), "PYTHONHASHSEED": "0"}, preexec_fn=_limits)
            out = json.loads(cp.stdout.strip().splitlines()[-1])
            return int(out["ok"]), int(out["n"])
        except Exception:
            return 0, len(tests)


def visible_check(task, text, allow_exec=True):
    """Cheap, NON-oracle check an arm may use to decide on escalation: format validity / visible examples only."""
    if task["answer_kind"] == "code":
        code = extract_code(text)
        if not code or not allow_exec:
            return False
        vis = task["tests"][: task["visible_n"]]
        ok, n = run_code_tests(code, vis)
        return ok == n
    ans = extract_answer(text)
    return ans is not None and re.search(task["format_regex"], ans.strip()) is not None


def is_correct(task, text, allow_exec=True):
    """Hidden oracle (never visible to an arm)."""
    if task["answer_kind"] == "code":
        code = extract_code(text)
        if not code or not allow_exec:
            return False
        ok, n = run_code_tests(code, task["tests"])
        return ok == n
    ans = extract_answer(text)
    if ans is None:
        return False
    if task["answer_kind"] == "int":
        try:
            return int(ans.replace(",", "").strip()) == int(task["expected"])
        except ValueError:
            return False
    return _norm(ans) == _norm(task["expected"])


def _wrong(s):
    """Same shape as `s` (so the visible format check still passes) but guaranteed different: change the last character."""
    c = s[-1]
    return s[:-1] + (str((int(c) + 1) % 10) if c.isdigit() else ("x" if c != "x" else "y"))


def reference_text(task, correct=True):
    """The text a perfect (correct=True) or a plausible-but-wrong (correct=False) answer would have."""
    if task["answer_kind"] == "code":
        if not correct:
            return "```python\ndef f(*a):\n    return None\n```"
        table = {json.dumps(args): exp for args, exp in task["tests"]}
        return "```python\nimport json\nT = " + repr(table) + "\n\ndef f(*a):\n    return T[json.dumps(list(a))]\n```"
    return f"ANSWER: {task['expected'] if correct else _wrong(task['expected'])}"


# --------------------------------------------------------------------------------------------- runners

class FakeRunner:
    """Seeded stand-in drawing from config/sim.json. A test fixture for the harness, never a measurement."""
    name = "fake"
    live = False

    def __init__(self, sim, seed):
        self.sim, self.seed = sim, seed

    def call(self, tier, model_id, task, rep, stage):
        rng = random.Random(f"{self.seed}|{tier}|{task['task_id']}|{rep}|{stage}")
        sim = self.sim
        if rng.random() < sim["p_format_fail"][tier]:
            text = "I am not sure; this depends on details."
        else:
            text = reference_text(task, rng.random() < sim["p_correct"][tier][task["category"]])
        out = max(1, int(rng.gauss(sim["tokens"]["out"][tier], sim["tokens"]["out"][tier] * 0.2)))
        inp = sim["tokens"]["in_base"] + len(task["prompt"]) // 3
        return {"tier": tier, "model": model_id, "text": text, "input": inp, "output": out,
                "cache_read": sim["tokens"].get("cache_read", {}).get(tier, 0), "cache_write": 0,
                "cost_usd": None, "latency_s": round(0.4 + out / 60.0, 3), "error": None}


def parse_cli_envelope(stdout):
    """Split a `claude -p --output-format json` reply into (text, usage, error); mirrors tde/worker_ipc.py:278."""
    try:
        env = json.loads((stdout or "").strip())
    except ValueError:
        return "", None, "not_json"
    if not isinstance(env, dict) or not ("result" in env or env.get("type") == "result" or "is_error" in env):
        return "", None, "not_envelope"
    u = env.get("usage") if isinstance(env.get("usage"), dict) else {}
    usage = {"input": int(u.get("input_tokens", 0) or 0), "output": int(u.get("output_tokens", 0) or 0),
             "cache_read": int(u.get("cache_read_input_tokens", 0) or 0),
             "cache_write": int(u.get("cache_creation_input_tokens", 0) or 0), "cost_usd": env.get("total_cost_usd")}
    err = None
    if env.get("is_error") is True or env.get("subtype") not in ("success", None):
        err = str(env.get("api_error_status") or env.get("subtype") or "error")
    res = env.get("result", "")
    return (res if isinstance(res, str) else json.dumps(res)), usage, err


class ClaudeCliRunner:
    """Live runner: one `claude -p` call per stage, usage and cost from the JSON envelope. Costs real money."""
    name = "claude"
    live = True

    def __init__(self, timeout=300, extra_args=()):
        self.timeout, self.extra = timeout, list(extra_args)

    def call(self, tier, model_id, task, rep, stage):
        # same flags as the production one-shots (tde/worker_ipc.py, tde/analysis_runner.py): one turn, no tools
        cmd = ["claude", "-p", "--model", model_id, "--output-format", "json", "--max-turns", "1", "--disallowedTools", "*"] + self.extra
        t0 = time.monotonic()
        with tempfile.TemporaryDirectory(prefix="abb-live-") as d:  # neutral cwd: no repo CLAUDE.md in the context
            try:
                cp = subprocess.run(cmd, input=task["prompt"], capture_output=True, text=True, timeout=self.timeout, cwd=d)
            except subprocess.TimeoutExpired:
                return {"tier": tier, "model": model_id, "text": "", "input": 0, "output": 0, "cache_read": 0, "cache_write": 0,
                        "cost_usd": None, "latency_s": round(time.monotonic() - t0, 3), "error": "timeout"}
        text, usage, err = parse_cli_envelope(cp.stdout)
        usage = usage or {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0, "cost_usd": None}
        if cp.returncode != 0 and not err:
            err = f"rc={cp.returncode}"
        return {"tier": tier, "model": model_id, "text": text, **usage, "latency_s": round(time.monotonic() - t0, 3), "error": err}


def check_live_ready(models, allow_exec, max_calls):
    pins = models.get("pins", {})
    for tier in ("haiku", "sonnet", "opus"):
        mid = pins.get(tier, "")
        if not mid or "latest" in mid or not re.search(r"\d", mid):
            die(f"live run refused: model pin for {tier!r} missing or not a concrete id ({mid!r})")
    if not allow_exec:
        die("live run refused: code tasks execute model-written code; pass --allow-code-exec")
    if not bwrap_available():
        die("live run refused: bubblewrap (bwrap) and /usr/bin/python3 are required to grade model-written code in a sandbox")
    if not max_calls:
        die("live run refused: pass --max-calls N (a live run spends real tokens)")


# --------------------------------------------------------------------------------------------- arms

def run_arm(arm, task, rep, runner, models, allow_exec):
    pins, calls = models["pins"], []

    def one(tier, stage):
        c = runner.call(tier, pins[tier], task, rep, stage)
        calls.append(c)
        return c

    kind = arm["kind"]
    if kind == "direct":
        final = one(arm["tier"], 0)
    elif kind == "route":
        final = one(arm["map"][task["label"]], 0)
    elif kind == "cascade":
        tiers = arm["tiers"]
        for s, tier in enumerate(tiers):
            final = one(tier, s)
            if s == len(tiers) - 1:
                break
            if not final["error"] and visible_check(task, final["text"], allow_exec):
                break
    else:
        die(f"unknown arm kind {kind!r}")
    ok = (not final["error"]) and is_correct(task, final["text"], allow_exec)
    tok = {k: sum(c[k] for c in calls) for k in COMPONENTS}
    tok["total"] = sum(tok.values())
    costs = [c["cost_usd"] for c in calls]
    tok["cost_usd"] = None if any(c is None for c in costs) else round(sum(costs), 8)
    return {"task_id": task["task_id"], "category": task["category"], "label": task["label"], "rep": rep, "arm": arm["name"],
            "stages": [c["tier"] for c in calls], "correct": bool(ok), "visible_pass": bool(visible_check(task, final["text"], allow_exec)),
            "tokens": tok, "calls": calls}


# --------------------------------------------------------------------------------------------- statistics

def percentile(xs, q):
    ys = sorted(xs)
    if not ys:
        return float("nan")
    pos = q * (len(ys) - 1)
    lo, hi = int(math.floor(pos)), int(math.ceil(pos))
    return ys[lo] + (ys[hi] - ys[lo]) * (pos - lo)


def n_required(p0, delta, alpha=0.05, power=0.8):
    """Per-arm tasks for a non-inferiority margin `delta` (unpaired normal approximation, conservative for a paired design)."""
    nd = statistics.NormalDist()
    p = min(max(p0, 0.05), 0.95)
    return math.ceil((nd.inv_cdf(1 - alpha) + nd.inv_cdf(power)) ** 2 * 2 * p * (1 - p) / delta ** 2)


def compare(tok_x, tok_b, suc_x, suc_b, delta, alpha, boot, seed, cap_underpowered=True):
    """Paired cluster bootstrap over tasks. tok_*: primary cost per task, suc_*: success rate per task.

    ``cap_underpowered``: an ADOPT on fewer tasks than the power formula asks for is capped to INCONCLUSIVE. With a handful of
    tasks and no variance the bootstrap interval collapses to a point, which would read as certainty (found by the
    2026-10-27 pilot: 10 tasks, all solved by every arm, "ADOPT" at n 10 < 27). REJECT is never capped: a visible harm or a
    higher cost is not made less true by a small sample."""
    n = len(tok_x)
    rng = random.Random(seed)
    sav, dsu = [], []
    idx_all = range(n)
    for _ in range(boot):
        idx = rng.choices(idx_all, k=n)
        sx, sb = sum(tok_x[i] for i in idx), sum(tok_b[i] for i in idx)
        sav.append(1.0 - sx / sb if sb > 0 else 0.0)
        dsu.append(sum(suc_x[i] - suc_b[i] for i in idx) / n)
    s_pt = 1.0 - sum(tok_x) / sum(tok_b) if sum(tok_b) > 0 else 0.0
    d_pt = sum(a - b for a, b in zip(suc_x, suc_b)) / n
    s_lo, s_hi = percentile(sav, alpha / 2), percentile(sav, 1 - alpha / 2)
    d_lo, d_hi = percentile(dsu, alpha), percentile(dsu, 1 - alpha)
    non_inf, inferior = d_lo >= -delta, d_hi < -delta
    saves, costs_more = s_lo > 0, s_hi < 0
    decision = "ADOPT" if (non_inf and saves) else "REJECT" if (inferior or costs_more) else "INCONCLUSIVE"
    p_b = sum(suc_b) / n
    req = n_required(p_b, delta, alpha)
    # one-sided bootstrap p-value of H0: "the success difference is at most -delta" (small = non-inferiority is supported)
    p_ni = (1 + sum(1 for x in dsu if x <= -delta)) / (boot + 1)
    capped = bool(cap_underpowered and decision == "ADOPT" and n < req)
    if capped:
        decision = "INCONCLUSIVE"
    return {"capped_underpowered": capped, "n_tasks": n, "savings": {"point": s_pt, "ci": [s_lo, s_hi]}, "d_success": {"point": d_pt, "lo_one_sided": d_lo, "hi_one_sided": d_hi},
            "non_inferior": non_inf, "inferior": inferior, "saves": saves, "costs_more": costs_more, "decision": decision,
            "p_noninferior": p_ni, "power": {"n_required_unpaired": req, "n_have": n, "underpowered": n < req}}


def holm_reject(pvals, alpha):
    """Holm step-down over a family of one-sided p-values: {name: p} -> {name: True when H0 is rejected family-wise}."""
    order = sorted(pvals, key=lambda k: (pvals[k], k))
    m, out = len(order), {k: False for k in order}
    for i, k in enumerate(order):
        if pvals[k] <= alpha / (m - i):
            out[k] = True
        else:
            break
    return out


def apply_class_gate(c, delta, alpha, min_tasks):
    """ADR-2306 decisions 5: Holm over the per-category non-inferiority tests, a worst-class guard, and the rule that a
    category with fewer than `min_tasks` tasks is exploratory and never yields ADOPT. Mutates and returns `c`."""
    cats = {k: s for k, s in c["strata"].items() if k.startswith("category:")}
    family = {k: s["p_noninferior"] for k, s in cats.items() if not s["exploratory"]}
    flags = holm_reject(family, alpha) if family else {}
    for k, s in cats.items():
        if s["exploratory"]:
            s["holm_non_inferior"], s["adjusted_decision"] = None, "EXPLORATORY"
            continue
        s["holm_non_inferior"] = flags[k]
        s["adjusted_decision"] = ("ADOPT" if flags[k] and s["saves"] else "REJECT" if (s["inferior"] or s["costs_more"]) else "INCONCLUSIVE")
    c["decision_before_class_gate"] = c["decision"]
    if not family:
        c["class_gate"] = {"evaluated": False, "reason": "no category has enough tasks (all exploratory)", "guard_triggered": False}
        return c
    worst = min(family, key=lambda k: (cats[k]["d_success"]["lo_one_sided"], k))
    guard = cats[worst]["d_success"]["point"] < -delta
    all_ni = all(flags.values())
    c["class_gate"] = {"evaluated": True, "family_size": len(family), "all_holm_non_inferior": all_ni, "guard_triggered": guard,
                       "worst_class": worst, "worst_class_point": cats[worst]["d_success"]["point"]}
    if guard:
        c["decision"] = "REJECT"
    elif c["decision"] == "ADOPT" and not all_ni:
        c["decision"] = "INCONCLUSIVE"
    return c


def load_raw(run_dir):
    recs = [json.loads(line) for line in (Path(run_dir) / "raw.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    return recs


def analyze(run_dir, boot=None):
    run_dir = Path(run_dir)
    man = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    cfg = man["decision"]
    boot = boot or cfg["bootstrap"]
    recs = load_raw(run_dir)
    arms = sorted({r["arm"] for r in recs})
    baseline = cfg["baseline"]
    if baseline not in arms:
        die(f"baseline arm {baseline!r} has no records in {run_dir}")
    use_cost = all(r["tokens"]["cost_usd"] is not None for r in recs)
    metric = "cost_usd" if use_cost else "total"
    by = {}
    for r in recs:
        by.setdefault(r["arm"], {}).setdefault(r["task_id"], []).append(r)
    reps = max(len(v) for a in by.values() for v in a.values())
    complete = {a: {t for t, v in by[a].items() if len(v) == reps} for a in arms}
    common = set.intersection(*complete.values())
    meta = {r["task_id"]: (r["category"], r["label"]) for r in recs}
    tasks = sorted(common)

    def per_task(arm, tid, key):
        rs = by[arm][tid]
        if key == "success":
            return sum(r["correct"] for r in rs) / len(rs)
        if key == "metric":
            return sum(r["tokens"][metric] for r in rs) / len(rs)
        return sum(r["tokens"][key] for r in rs) / len(rs)

    summary_arms = {}
    for a in arms:
        suc = [per_task(a, t, "success") for t in tasks]
        rng = random.Random(f"{man['seed']}|armci|{a}")
        bs = sorted(sum(rng.choices(suc, k=len(suc))) / len(suc) for _ in range(boot))
        summary_arms[a] = {"tasks": len(tasks), "reps": reps,
                           "success": {"mean": sum(suc) / len(suc), "ci": [percentile(bs, cfg["alpha"] / 2), percentile(bs, 1 - cfg["alpha"] / 2)]},
                           "tokens_per_run": {k: sum(per_task(a, t, k) for t in tasks) / len(tasks) for k in (*COMPONENTS, "total")},
                           "cost_usd_per_run": (sum(per_task(a, t, "cost_usd") for t in tasks) / len(tasks)) if use_cost else None,
                           "primary_per_run": sum(per_task(a, t, "metric") for t in tasks) / len(tasks),
                           "mean_calls_per_run": sum(len(r["calls"]) for t in tasks for r in by[a][t]) / sum(len(by[a][t]) for t in tasks)}

    def run_cmp(arm, subset, tag):
        if not subset:
            return None
        return compare([per_task(arm, t, "metric") for t in subset], [per_task(baseline, t, "metric") for t in subset],
                       [per_task(arm, t, "success") for t in subset], [per_task(baseline, t, "success") for t in subset],
                       cfg["delta"], cfg["alpha"], boot, f"{man['seed']}|boot|{arm}|{tag}",
                       cap_underpowered=cfg.get("cap_underpowered", True))

    comparisons = {}
    for a in arms:
        if a == baseline:
            continue
        c = run_cmp(a, tasks, "all")
        c["strata"] = {}
        for kind, getter in (("label", lambda t: meta[t][1]), ("category", lambda t: meta[t][0])):
            for val in sorted({getter(t) for t in tasks}):
                sub = [t for t in tasks if getter(t) == val]
                s = run_cmp(a, sub, f"{kind}:{val}")
                s["exploratory"] = len(sub) < cfg["min_stratum_tasks"]
                c["strata"][f"{kind}:{val}"] = s
        apply_class_gate(c, cfg["delta"], cfg["alpha"], cfg["min_stratum_tasks"])
        c["role"] = man.get("roles", {}).get(a, "")
        if c["role"] == "retest":
            # control arm: the same policy as the baseline. If it is NOT non-inferior at delta, delta is below the noise.
            c["sanity"] = {"identical_policy_non_inferior": c["non_inferior"],
                           "reading": "delta is above the baseline's own noise" if c["non_inferior"] else "delta is BELOW the baseline's own noise: the test cannot be passed, widen delta or add tasks"}
        comparisons[a] = c
    dropped = sorted(set(meta) - set(tasks))
    return {"primary_metric": metric, "baseline": baseline, "delta": cfg["delta"], "alpha": cfg["alpha"], "bootstrap": boot,
            "runner": man["runner"], "arms": summary_arms, "comparisons": comparisons,
            "excluded_incomplete_tasks": dropped, "raw_sha256": sha256_file(run_dir / "raw.jsonl")}


def fmt_ci(ci, pct=True):
    f = (lambda x: f"{x * 100:+.1f} %") if pct else (lambda x: f"{x:.3f}")
    return f"[{f(ci[0])}, {f(ci[1])}]"


def render_report(summary, man):
    L = []
    if summary["runner"] == "fake":
        L += ["> **SIMULATION. Keine Messung.** Die Zahlen stammen aus `config/sim.json` (beliebige Testwerte) und prüfen nur das Harness. Nicht zitieren.", ""]
    L += [f"# A/B-Token-Benchmark: Lauf `{man['run_id']}`", "",
          f"Primäre Kostengröße: `{summary['primary_metric']}` (cost_usd, wenn jeder Aufruf sie liefert, sonst Token-Summe). "
          f"Baseline: `{summary['baseline']}`. Nicht-Unterlegenheitsgrenze δ = {summary['delta']}, α = {summary['alpha']}, Bootstrap B = {summary['bootstrap']}.", "",
          "## Reproduzierbarkeit", "",
          f"- Korpus: `{man['corpus']['profile']}`, SHA-256 `{man['corpus']['sha256']}`, {man['corpus']['tasks_run']} Aufgaben, davon übersprungen: {len(man['corpus']['skipped'])}",
          f"- Seed: {man['seed']}, Wiederholungen je Aufgabe und Arm: {man['reps']}",
          f"- Modell-Pins: {canon(man['models'])}", f"- Konfiguration SHA-256: `{man['config_sha256']}`", f"- Harness SHA-256: `{man['harness_sha256']}`",
          f"- Runner: `{man['runner']}`, Python {man['python']}" + (f", claude {man['claude_version']}" if man.get("claude_version") else ""),
          f"- raw.jsonl SHA-256: `{summary['raw_sha256']}` (Analyse liest nur diese Datei)", "",
          "## Arme (Mittel je Lauf, 95 %-Intervall für den Erfolg per Cluster-Bootstrap über Aufgaben)", "",
          "| Arm | Erfolg | Intervall | input | output | cache_read | cache_write | total | cost_usd | Aufrufe |", "|---|---|---|---|---|---|---|---|---|---|"]
    for a, s in summary["arms"].items():
        t = s["tokens_per_run"]
        cost = "n/a" if s["cost_usd_per_run"] is None else f"{s['cost_usd_per_run']:.5f}"
        L.append(f"| {a} | {s['success']['mean']:.3f} | {fmt_ci(s['success']['ci'], False)} | {t['input']:.0f} | {t['output']:.0f} | {t['cache_read']:.0f} | "
                 f"{t['cache_write']:.0f} | {t['total']:.0f} | {cost} | {s['mean_calls_per_run']:.2f} |")
    L += ["", "## Vergleich mit der Baseline", "",
          "| Arm | Ersparnis | 95 %-Intervall | Δ Erfolg | untere 1-seitige Grenze | nicht unterlegen | Entscheidung | Power |", "|---|---|---|---|---|---|---|---|"]
    for a, c in summary["comparisons"].items():
        pw = c["power"]
        pn = f"unterpowert (n {pw['n_have']} < {pw['n_required_unpaired']})" if pw["underpowered"] else "ok"
        L.append(f"| {a} | {c['savings']['point'] * 100:+.1f} % | {fmt_ci(c['savings']['ci'])} | {c['d_success']['point']:+.3f} | "
                 f"{c['d_success']['lo_one_sided']:+.3f} | {'ja' if c['non_inferior'] else 'nein'} | **{c['decision']}**"
                 f"{' (ADOPT gedeckelt: unterpowert)' if c.get('capped_underpowered') else ''} | {pn} |")
    L += ["", "Entscheidungsregel: ADOPT nur wenn die untere einseitige Grenze von Δ Erfolg ≥ −δ liegt UND das Ersparnis-Intervall über 0 liegt. "
          "REJECT, wenn die obere Grenze unter −δ liegt oder die Variante teurer ist. Sonst INCONCLUSIVE. Ein ADOPT bei zu wenigen Aufgaben wird zu INCONCLUSIVE gedeckelt "
          "(`cap_underpowered`, Standard an). „Unterpowert“ heißt: Mit dieser Aufgabenzahl "
          "ist Nicht-Unterlegenheit bei δ nur unter hoher Paarkorrelation belegbar; mehr Aufgaben (Profil `full`) oder größeres δ nötig.", "",
          "## Strata (Kategorien: Holm-korrigiert, sofern genug Aufgaben; Labels explorativ)", "",
          "| Arm | Stratum | n | Ersparnis | Δ Erfolg (untere Grenze) | p (nicht unterlegen) | Entscheidung | nach Holm | explorativ |", "|---|---|---|---|---|---|---|---|---|"]
    for a, c in summary["comparisons"].items():
        for k, s in c["strata"].items():
            L.append(f"| {a} | {k} | {s['n_tasks']} | {s['savings']['point'] * 100:+.1f} % | {s['d_success']['lo_one_sided']:+.3f} | {s['p_noninferior']:.4f} | "
                     f"{s['decision']} | {s.get('adjusted_decision', '')} | {'ja' if s['exploratory'] else 'nein'} |")
    L += ["", "## Klassen-Gate und Kontrollarm", ""]
    for a, c in summary["comparisons"].items():
        g = c["class_gate"]
        line = f"- `{a}`: Gesamtentscheidung **{c['decision']}** (vor Klassen-Gate {c['decision_before_class_gate']}). "
        line += ("Klassen-Gate nicht auswertbar: " + g["reason"] + "." if not g["evaluated"] else
                 f"Klassen-Gate: {g['family_size']} Klassen, alle nach Holm nicht unterlegen: {'ja' if g['all_holm_non_inferior'] else 'nein'}; "
                 f"Wächter für die schlechteste Klasse (`{g['worst_class']}`, Δ {g['worst_class_point']:+.3f}) {'ausgelöst' if g['guard_triggered'] else 'nicht ausgelöst'}.")
        if c.get("sanity"):
            line += f" Kontrollarm: {c['sanity']['reading']}."
        L.append(line)
    if summary["excluded_incomplete_tasks"]:
        L += ["", f"Ausgeschlossen (unvollständige Paare): {', '.join(summary['excluded_incomplete_tasks'])}"]
    return "\n".join(L) + "\n"


# --------------------------------------------------------------------------------------------- commands

def load_config(runner_name, arms_config=None):
    models = json.loads((CONFIG_DIR / "models.json").read_text(encoding="utf-8"))
    armcfg = json.loads(Path(arms_config or CONFIG_DIR / "arms.json").read_text(encoding="utf-8"))
    sim = json.loads((CONFIG_DIR / "sim.json").read_text(encoding="utf-8"))
    return models, armcfg, sim


def cmd_run(a):
    models, armcfg, sim = load_config(a.runner, a.arms_config)
    tasks, corpus_sha = load_corpus(a.profile)
    tasks = select_tasks(tasks, a.tasks_per_category)
    arms = armcfg["arms"]
    if a.arms:
        want = a.arms.split(",")
        arms = [x for x in arms if x["name"] in want]
        if {x["name"] for x in arms} != set(want):
            die(f"unknown arm in --arms {a.arms}")
    if armcfg["baseline"] not in {x["name"] for x in arms}:
        die(f"--arms must include the baseline arm {armcfg['baseline']!r}")
    allow_exec = a.allow_code_exec or a.runner == "fake"
    if a.runner == "claude":
        check_live_ready(models, a.allow_code_exec, a.max_calls)
        runner = ClaudeCliRunner(extra_args=a.claude_arg or [])
    else:
        runner = FakeRunner(sim, a.seed)
    run_id = a.run_id or f"{a.profile}-s{a.seed}-{a.runner}"
    run_dir = Path(a.results_dir) / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    raw_path = run_dir / "raw.jsonl"
    done = set()
    if raw_path.exists():
        if not a.resume:
            die(f"{raw_path} exists; pass --resume to continue or choose another --run-id (raw files are append-only, never overwritten)")
        done = {(r["arm"], r["task_id"], r["rep"]) for r in load_raw(run_dir)}
    skipped = [t["task_id"] for t in tasks if t["answer_kind"] == "code" and not allow_exec]
    cfg_blob = {"models": models, "arms": arms, "baseline": armcfg["baseline"], "decision": armcfg["decision"]}
    if a.runner == "fake":
        cfg_blob["sim"] = sim
    claude_version = None
    if a.runner == "claude":
        try:
            claude_version = subprocess.run(["claude", "--version"], capture_output=True, text=True, timeout=30).stdout.strip()
        except Exception:
            claude_version = "unknown"
    man = {"run_id": run_id, "runner": a.runner, "seed": a.seed, "reps": a.reps, "models": models["pins"], "python": sys.version.split()[0],
           "claude_version": claude_version, "harness_sha256": sha256_file(__file__), "config_sha256": sha256_bytes(canon(cfg_blob).encode()),
           "corpus": {"profile": a.profile, "sha256": corpus_sha, "tasks_run": len(tasks) - len(skipped), "skipped": skipped},
           "arms": [x["name"] for x in arms], "roles": {x["name"]: x["role"] for x in arms if x.get("role")},
           "decision": armcfg["decision"] | {"baseline": armcfg["baseline"]},
           "created_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "status": "running"}
    (run_dir / "manifest.json").write_text(json.dumps(man, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    calls = spend = 0
    status = "complete"
    with raw_path.open("a", encoding="utf-8") as fh:
        for task in tasks:
            if task["task_id"] in skipped:
                continue
            for rep in range(a.reps):
                order = [x for x in arms]
                random.Random(f"{a.seed}|order|{task['task_id']}|{rep}").shuffle(order)
                for arm in order:
                    if (arm["name"], task["task_id"], rep) in done:
                        continue
                    rec = run_arm(arm, task, rep, runner, models, allow_exec)
                    fh.write(canon(rec) + "\n")
                    fh.flush()
                    calls += len(rec["calls"])
                    spend += rec["tokens"]["cost_usd"] or 0
                    if a.max_calls and calls >= a.max_calls:
                        status = "stopped_max_calls"
                    if a.max_cost_usd and spend >= a.max_cost_usd:
                        status = "stopped_max_cost"
                    if status != "complete":
                        break
                if status != "complete":
                    break
            if status != "complete":
                break
    man["status"] = status
    man["calls_this_session"] = calls
    (run_dir / "manifest.json").write_text(json.dumps(man, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"run {run_id}: {status}, {calls} calls, raw sha256 {sha256_file(raw_path)}")
    return 0 if status == "complete" else 3


def cmd_analyze(a):
    run_dir = Path(a.run_dir)
    man = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    summary = analyze(run_dir, a.bootstrap)
    (run_dir / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    report = render_report(summary, man)
    (run_dir / "report.md").write_text(report, encoding="utf-8")
    print(f"summary sha256 {sha256_file(run_dir / 'summary.json')}\nreport: {run_dir / 'report.md'}")
    for arm, c in summary["comparisons"].items():
        print(f"  {arm:14s} savings {c['savings']['point'] * 100:+6.1f} %  d_success {c['d_success']['point']:+.3f}  -> {c['decision']}")
    return 0


def cmd_power(a):
    print(f"tasks per arm (unpaired, conservative): {n_required(a.p0, a.delta, a.alpha, a.power)}")
    return 0


def cmd_selftest(a):
    import unittest
    suite = unittest.defaultTestLoader.discover(str(HERE), pattern="test_token_ab_bench.py")
    res = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if res.wasSuccessful() else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)
    r = sp.add_parser("run")
    r.add_argument("--runner", choices=["fake", "claude"], required=True)
    r.add_argument("--profile", choices=["smoke", "full", "hard"], default="smoke")
    r.add_argument("--seed", type=int, default=1)
    r.add_argument("--reps", type=int, default=3)
    r.add_argument("--arms", default=None)
    r.add_argument("--arms-config", default=None, help="alternative arms.json (default config/arms.json)")
    r.add_argument("--tasks-per-category", type=int, default=None)
    r.add_argument("--run-id", default=None)
    r.add_argument("--results-dir", default=str(HERE / "results"))
    r.add_argument("--max-calls", type=int, default=None)
    r.add_argument("--max-cost-usd", type=float, default=None)
    r.add_argument("--allow-code-exec", action="store_true")
    r.add_argument("--resume", action="store_true")
    r.add_argument("--claude-arg", action="append")
    r.set_defaults(fn=cmd_run)
    an = sp.add_parser("analyze")
    an.add_argument("--run-dir", required=True)
    an.add_argument("--bootstrap", type=int, default=None)
    an.set_defaults(fn=cmd_analyze)
    pw = sp.add_parser("power")
    pw.add_argument("--p0", type=float, required=True)
    pw.add_argument("--delta", type=float, required=True)
    pw.add_argument("--alpha", type=float, default=0.05)
    pw.add_argument("--power", type=float, default=0.8)
    pw.set_defaults(fn=cmd_power)
    st = sp.add_parser("selftest")
    st.set_defaults(fn=cmd_selftest)
    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
