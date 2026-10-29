"""Recording `claude` shim for the adversarial DAG end-to-end suite.

Plan: claude-playground/dag-e2e-adversarial-plan-2026-10-29/PLAN.md (section 3.2).

A script named ``claude`` is put in front of the real one on ``PATH``. The product spawns it exactly
as it spawns the real CLI (``subprocess``), through the unchanged production code path; nothing is
replaced inside the product. The shim is oracle O1: it records the ORIGINAL argv of every call
(before it adds anything), the prompt, the exit code and the answer envelope, independently of the
product's own audit.

Modes (per rule, ``SHIM_MODE`` is the default):

* ``canned``      the answer comes from the rules file (a deterministic fault-injection tier: only the
                  LLM's answer is given, everything else is real).
* ``passthrough`` the real ``claude`` (``REAL_CLAUDE``, an absolute path) answers; the shim only
                  records. ``SHIM_MAX_USD`` adds ``--max-budget-usd`` AFTER the original argv was
                  recorded and never touches ``--model``.

Call kinds: ``gate`` (``--max-turns``: the L44 classifier and the explain helper), ``planner``
(design chat: ``-p --output-format text``, prompt on stdin), ``legacy_node`` (the console's own node
executor: prompt on stdin, ``--output-format json``) and ``dag_node`` (the DAG engine: the prompt is
the positional argument after ``-p``).
"""
from __future__ import annotations

import json
import os
import stat
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

SHIM_SOURCE = r'''#!__PYTHON__
import fcntl
import hashlib
import json
import os
import re
import subprocess
import sys
import time

argv = sys.argv[1:]
t0 = time.time()


def positional_prompt(a):
    out = [x for i, x in enumerate(a) if i and a[i - 1] == "-p" and not x.startswith("--")]
    return out[0] if out else None


pos = positional_prompt(argv)
prompt = pos if pos is not None else sys.stdin.read()
if "--max-turns" in argv:
    kind = "gate"
elif pos is not None:
    kind = "dag_node"
elif "--output-format" in argv and argv[argv.index("--output-format") + 1] == "text":
    kind = "planner"
else:
    kind = "legacy_node"
model = argv[argv.index("--model") + 1] if "--model" in argv else ""

try:
    rules = json.load(open(os.environ["SHIM_RULES"]))
except Exception:
    rules = []
default_mode = os.environ.get("SHIM_MODE", "canned")


def matches(rule):
    m = rule.get("match") or {}
    if m.get("kind") and m["kind"] != kind:
        return False
    if m.get("prompt_regex") and not re.search(m["prompt_regex"], prompt, re.S):
        return False
    if m.get("argv_contains") and m["argv_contains"] not in argv:
        return False
    if m.get("model") and m["model"] != model:
        return False
    return True


action = {}
for idx, rule in enumerate(rules):
    if matches(rule):
        action = dict(rule.get("action") or {})
        seq = action.pop("sequence", None)
        if seq:                       # the n-th matching call gets the n-th entry (the last one repeats)
            counters_path = os.environ["SHIM_RULES"] + ".counters"
            with open(counters_path, "a+", encoding="utf-8") as cf:
                fcntl.flock(cf, fcntl.LOCK_EX)
                cf.seek(0)
                try:
                    counters = json.loads(cf.read() or "{}")
                except Exception:
                    counters = {}
                n = int(counters.get(str(idx), 0))
                counters[str(idx)] = n + 1
                cf.seek(0)
                cf.truncate()
                cf.write(json.dumps(counters))
                fcntl.flock(cf, fcntl.LOCK_UN)
            action.update(seq[min(n, len(seq) - 1)])
        break

mode = "passthrough" if action.pop("passthrough", False) else default_mode
if mode == "passthrough" and kind == "gate" and os.environ.get("SHIM_GATE_CANNED", "1") == "1":
    mode = "canned"          # the gates' classifier calls are not the subject of a model test

exit_code = 0
stdout = ""
stderr = ""
reported = ""
cost = None
usage = {}

if mode == "passthrough":
    real = os.environ["REAL_CLAUDE"]
    cmd = [real] + argv
    if os.environ.get("SHIM_MAX_USD") and "--max-budget-usd" not in argv:
        cmd += ["--max-budget-usd", os.environ["SHIM_MAX_USD"]]
    proc = subprocess.run(cmd, input=None if pos is not None else prompt, capture_output=True, text=True,
                          timeout=int(os.environ.get("SHIM_TIMEOUT_S", "300")))
    exit_code, stdout, stderr = proc.returncode, proc.stdout, proc.stderr
    try:
        env = json.loads(stdout)
        if isinstance(env, dict):
            cost = env.get("total_cost_usd")
            usage = env.get("usage") or {}
            mu = env.get("modelUsage") or {}
            if mu:
                reported = max(mu.items(), key=lambda kv: (kv[1] or {}).get("outputTokens", 0))[0]
    except Exception:
        pass
else:
    if action.get("delay_s"):
        time.sleep(float(action["delay_s"]))
    exit_code = int(action.get("exit_code", 0))
    stderr = str(action.get("stderr", ""))
    reported = action.get("report_model") if action.get("report_model") is not None else (model or "cli-default-model")
    cost = action.get("cost", 0.001)
    usage = action.get("usage") or {"input_tokens": 11, "output_tokens": 7,
                                   "cache_read_input_tokens": 100, "cache_creation_input_tokens": 20}
    if "raw_stdout" in action:
        stdout = str(action["raw_stdout"])
    else:
        if "reply" in action:
            reply = action["reply"]
        elif kind == "gate":
            reply = {"violated_rule_id": "", "confidence": 0.0, "reason": "ok"}
        elif kind == "planner":
            reply = "Understood."
        else:
            reply = {"ok": True}
        text = reply if isinstance(reply, str) else json.dumps(reply)
        text_mode = "--output-format" in argv and argv[argv.index("--output-format") + 1] == "text"
        if text_mode:
            stdout = text
        else:
            envelope = {
                "type": "result", "subtype": "success", "is_error": bool(action.get("is_error", False)),
                "result": text, "total_cost_usd": cost, "usage": usage,
            }
            if reported:
                envelope["modelUsage"] = {reported: {"inputTokens": 11, "outputTokens": 7}}
            stdout = json.dumps(envelope)

row = {
    "ts": t0, "kind": kind, "argv": argv, "model": model, "prompt": prompt,
    "prompt_sha256": hashlib.sha256(prompt.encode("utf-8", "replace")).hexdigest(), "prompt_len": len(prompt),
    "mode": mode, "exit_code": exit_code, "duration_s": round(time.time() - t0, 3),
    "reported_model": reported, "cost_usd": cost, "usage": usage, "stdout_len": len(stdout),
    "stdout_head": stdout[:400] if kind != "gate" else "",
}
with open(os.environ["SHIM_LOG"], "a", encoding="utf-8") as fh:
    fcntl.flock(fh, fcntl.LOCK_EX)
    fh.write(json.dumps(row) + "\n")
    fcntl.flock(fh, fcntl.LOCK_UN)

sys.stdout.write(stdout)
sys.stdout.flush()
if stderr:
    sys.stderr.write(stderr)
sys.exit(exit_code)
'''


@dataclass
class Shim:
    bindir: Path
    log: Path
    rules_path: Path

    def set_rules(self, rules: Iterable[dict[str, Any]]) -> None:
        self.rules_path.write_text(json.dumps(list(rules)), encoding="utf-8")
        counters = Path(str(self.rules_path) + ".counters")
        if counters.exists():
            counters.unlink()          # a new rule set starts every sequence from its first entry

    def rows(self, kind: str | None = None) -> list[dict[str, Any]]:
        if not self.log.exists():
            return []
        out = [json.loads(x) for x in self.log.read_text(encoding="utf-8").splitlines() if x.strip()]
        return [r for r in out if kind is None or r["kind"] == kind]

    def nodes(self) -> list[dict[str, Any]]:
        """Every node spawn (both executors); gate and planner calls are not nodes."""
        return [r for r in self.rows() if r["kind"] in ("dag_node", "legacy_node")]

    def count(self, kind: str | None = None) -> int:
        return len(self.rows(kind))

    def cost_usd(self) -> float:
        return sum(float(r["cost_usd"] or 0.0) for r in self.rows())

    def reset(self) -> None:
        self.log.write_text("", encoding="utf-8")


def model_of(argv: list[str]) -> str | None:
    return argv[argv.index("--model") + 1] if "--model" in argv else None


def install(tmp_path: Path, monkeypatch, *, mode: str = "canned", rules: Iterable[dict[str, Any]] = (),
            real_claude: str | None = None, max_usd: float | None = None) -> Shim:
    """Put the shim first on PATH for this test. ``real_claude`` is only needed for passthrough."""
    bindir = tmp_path / "shimbin"
    bindir.mkdir(parents=True, exist_ok=True)
    script = bindir / "claude"
    script.write_text(SHIM_SOURCE.replace("__PYTHON__", sys.executable), encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    log = tmp_path / "shim_calls.jsonl"
    log.write_text("", encoding="utf-8")
    rules_path = tmp_path / "shim_rules.json"
    rules_path.write_text(json.dumps(list(rules)), encoding="utf-8")
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ.get('PATH', '')}")
    monkeypatch.setenv("SHIM_LOG", str(log))
    monkeypatch.setenv("SHIM_RULES", str(rules_path))
    monkeypatch.setenv("SHIM_MODE", mode)
    if real_claude:
        monkeypatch.setenv("REAL_CLAUDE", real_claude)
    if max_usd is not None:
        monkeypatch.setenv("SHIM_MAX_USD", f"{max_usd:.2f}")
    monkeypatch.delenv("CORVIN_HELPER_MODEL_WORKFLOW_NODE", raising=False)
    return Shim(bindir=bindir, log=log, rules_path=rules_path)
