#!/usr/bin/env python3
"""Tool-disabled CEL measurement (EXP-001, Entry 16) — isolates CEL's INJECTION value.

The console OS turn is a fully tool-enabled agent, so it self-retrieves facts off disk and
bypasses CEL (Anomaly B). This measurement removes that leak: it spawns `claude -p` with
`--disallowedTools "*"` (no filesystem access), so the ONLY context channel is what we inject.

Three arms, same tool-disabled model, per task:
  - none : the bare question (no CEL, no tools) -> baseline; the model cannot know the fact.
  - cel  : the REAL deterministic CEL brief prepended (build_brief -> render) + question.
           The pilot showed this brief lists memory/ADR *titles*, not content -> test whether a
           pointer without content is answerable when the agent cannot pull.
  - oracle: the memory-file CONTENT prepended (what CEL *would* inject if it injected content) +
           question -> the upper bound: proves the fact IS answerable when actually injected.

Objective fact-presence check per task (same as the harness). No LLM judge. Deterministic.
Run:  ./run_benchmark.sh  is NOT used here; call directly with env from run_benchmark.sh, e.g.
  CORVIN_HOME=... PYTHONPATH=... python measure_tooldisabled.py --n 3
"""
from __future__ import annotations
import argparse, json, subprocess, sys, statistics
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import context_engineering as ce  # noqa: E402


def quality(text: str, check: dict) -> float:
    t = text.lower() if check.get("case_insensitive") else text
    exp = check.get("expect", [])
    if not exp:
        return 1.0
    norm = (lambda s: s.lower()) if check.get("case_insensitive") else (lambda s: s)
    present = sum(1 for e in exp if norm(str(e)) in t)
    return present / len(exp)


def cel_brief(question: str, tenant: str, include_content: bool = False) -> str:
    try:
        out = ce.build_brief(question, tenant, None)
        brief = out[0] if isinstance(out, tuple) else out
        return (ce.render_brief_to_text(brief, include_content=include_content) or "").strip()
    except Exception as e:  # noqa: BLE001
        return f"(CEL brief unavailable: {e})"


def oracle_content(task: str) -> str:
    """The memory-file body for this task, if a fixture matches its answer — the upper bound."""
    memdir = Path.home() / ".claude" / "projects" / "-home-shumway-projects-CorvinOS" / "memory"
    ans = task["check"]["expect"][0].lower()
    for p in memdir.glob("bench-cel-*.md"):
        raw = p.read_text(encoding="utf-8")
        # match the fixture whose stored fact yields this task's answer (direct or +100 derive)
        if ans in raw.lower() or (ans.isdigit() and str(int(ans) - 100) in raw):
            # Inject ONLY the clean fact body: strip YAML frontmatter and the HTML disclaimer
            # comment ("safe to delete") — that framing made the model refuse/emit nothing.
            body = raw.split("-->")[-1] if "-->" in raw else raw
            return body.strip()
    return ""


def run_turn(prompt: str, model: str) -> str:
    """One tool-disabled claude -p turn. --disallowedTools '*' blocks Read/Grep/Bash so the model
    cannot self-retrieve; the only context is `prompt` itself."""
    p = subprocess.run(
        ["claude", "-p", prompt, "--disallowedTools", "*", "--model", model,
         "--output-format", "text", "--max-turns", "1"],
        capture_output=True, text=True, timeout=180,
    )
    return (p.stdout or "").strip()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=3)
    ap.add_argument("--tenant", default="_default")
    ap.add_argument("--model", default="claude-haiku-4-5-20251001")
    ap.add_argument("--tasks", default=str(HERE / "tasks" / "suite-v4-memory-grounded.json"))
    ap.add_argument("--out", default=str(HERE / "results" / "tooldisabled.json"))
    args = ap.parse_args()

    suite = json.loads(Path(args.tasks).read_text())
    # single-turn memory tasks only (multi-turn derive needs the turn-1 fact; single is the clean probe)
    tasks = [t for t in suite["tasks"] if not t.get("turns")]
    arms = {}
    raw = []
    for arm in ("none", "cel", "cel_content", "oracle"):
        scores = []
        for tk in tasks:
            q = tk["prompt"]
            for rep in range(args.n):
                if arm == "none":
                    prompt = q
                elif arm == "cel":
                    b = cel_brief(q, args.tenant)
                    prompt = f"{b}\n\n{q}" if b else q
                elif arm == "cel_content":
                    b = cel_brief(q, args.tenant, include_content=True)
                    prompt = f"{b}\n\n{q}" if b else q
                else:  # oracle
                    c = oracle_content(tk)
                    prompt = f"{c}\n\n{q}" if c else q
                resp = run_turn(prompt, args.model)
                s = quality(resp, tk["check"])
                scores.append(s)
                raw.append({"arm": arm, "task": tk["id"], "rep": rep, "quality": s,
                            "resp_head": resp[:120]})
                print(f"  {arm:6s} {tk['id']:22s} rep{rep} q={s:.2f}  {resp[:60]!r}", flush=True)
        arms[arm] = round(statistics.mean(scores), 3) if scores else None
        print(f"== ARM {arm}: mean quality = {arms[arm]} (n={len(scores)}) ==\n", flush=True)

    report = {"experiment": "exp-001", "measurement": "tool-disabled", "model": args.model,
              "n_per_task": args.n, "n_single_tasks": len(tasks), "arm_quality": arms,
              "interpretation": {
                  "none": "no context, no tools -> the model cannot know the fact (floor)",
                  "cel": "real deterministic CEL brief (pointers/titles) -> tests if a pointer is "
                         "answerable without agentic pull",
                  "cel_content": "the PROTOTYPE fix (render_brief_to_text include_content=True): "
                                 "CEL brief now injects memory BODIES, not just titles",
                  "oracle": "the memory CONTENT injected -> proves the fact IS answerable when "
                            "actually injected (ceiling)"}}
    Path(args.out).write_text(json.dumps({"report": report, "raw": raw}, indent=2))
    print("\nSUMMARY (tool-disabled):", json.dumps(arms))
    print("report:", args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
