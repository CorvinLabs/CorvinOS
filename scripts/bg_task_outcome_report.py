#!/usr/bin/env python3
"""Background-task outcome report (PLAN bg-task robustness, P3). Read-only.

Measures the acceptance numbers of the plan from the task records and the tenant audit
chain, so the 7-day check is a command, not a judgement:

* every task that ended exit 143 is either explained (a ``process.signal_sent`` record for
  a signal shortly before its end) or external (no record in the window);
* reruns after a service restart: how many chains exceeded the attempt budget, how many
  reruns carry a restart prefix more than once (must be 0 after P1), and how many reruns
  were announced as recovered without an answer (must be 0 after P1).

Usage::

    python3 scripts/bg_task_outcome_report.py --home ~/.corvin --since 2026-10-19
    python3 scripts/bg_task_outcome_report.py --home .corvin --json

Reads only. It never writes to the chain or to a task record.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
import time
from collections import Counter
from pathlib import Path

RESTART_MARK = "[Automatic restart"
EXPLAIN_WINDOW_S = 10.0   # a recorded signal this close before the end explains an exit 143


def _parse_since(value: str | None) -> float:
    if not value:
        return 0.0
    return time.mktime(time.strptime(value, "%Y-%m-%d"))


def _load_tasks(home: Path, since: float) -> list[dict]:
    out: list[dict] = []
    for path in glob.glob(str(home / "tenants" / "*" / "sessions" / "*" / "tasks" / "*.json")):
        if path.endswith("recovery_attempts.json"):
            continue
        try:
            rec = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        started = rec.get("started_at") or rec.get("created_at") or 0
        if isinstance(started, (int, float)) and started >= since:
            rec["_path"] = path
            out.append(rec)
    return out


def _signal_times(home: Path, tenant: str = "_default") -> list[float]:
    chain = home / "tenants" / tenant / "global" / "forge" / "audit.jsonl"
    times: list[float] = []
    if not chain.exists():
        return times
    with chain.open(encoding="utf-8") as fh:
        for line in fh:
            if '"process.signal_sent"' not in line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if (rec.get("details") or {}).get("action") == "process.signal_sent":
                times.append(float(rec.get("ts") or 0))
    return times


def _announced_recovered(task: dict) -> bool:
    events = Path(str(task.get("_path", ""))[: -len(".json")] + ".events.jsonl")
    try:
        lines = events.read_text(encoding="utf-8").splitlines()
    except OSError:
        return False
    for line in lines:
        try:
            if json.loads(line).get("notice_kind") == "recovered":
                return True
        except ValueError:
            continue
    return False


def _recovery_outcomes(home: Path, tenant: str = "_default") -> Counter:
    chain = home / "tenants" / tenant / "global" / "forge" / "audit.jsonl"
    c: Counter = Counter()
    if not chain.exists():
        return c
    with chain.open(encoding="utf-8") as fh:
        for line in fh:
            if "chat.turn.recovery_" not in line:
                continue
            try:
                action = (json.loads(line).get("details") or {}).get("action", "")
            except ValueError:
                continue
            if action.startswith("chat.turn.recovery_"):
                c[action] += 1
    return c


def build_report(home: Path, since: float) -> dict:
    tasks = _load_tasks(home, since)
    signals = _signal_times(home)
    statuses = Counter(str(t.get("status")) for t in tasks)

    exit143 = [t for t in tasks if t.get("exit_code") == 143]
    explained, external = 0, 0
    for t in exit143:
        end = t.get("ended_at") or 0
        if isinstance(end, (int, float)) and any(abs(end - s) <= EXPLAIN_WINDOW_S for s in signals):
            explained += 1
        else:
            external += 1

    reruns = [t for t in tasks if str((t.get("input") or {}).get("instruction", "")).startswith(RESTART_MARK)]
    stacked = [t for t in reruns if str(t["input"]["instruction"]).count(RESTART_MARK) > 1]
    # "recovered" is announced from the task's own event log; it must not be announced for a run that failed
    # the notice is written on the task that was interrupted, so every task is checked
    recovered_without_answer = [t for t in tasks if t.get("status") != "completed"
                                and _announced_recovered(t)]
    outcomes = _recovery_outcomes(home)

    total = len(tasks)
    completed = statuses.get("completed", 0)
    return {
        "since": time.strftime("%Y-%m-%d", time.localtime(since)) if since else None,
        "tasks": total,
        "completed": completed,
        "completion_rate": round(completed / total, 3) if total else None,
        "by_status": dict(statuses),
        "exit_143": {"total": len(exit143), "explained_by_recorded_signal": explained,
                     "external_no_record": external},
        "reruns": {"total": len(reruns), "stacked_prefix": len(stacked),
                   "failed_but_announced": len(recovered_without_answer)},
        "recovery_audit": dict(outcomes),
        "acceptance": {
            "no_stacked_prefix": len(stacked) == 0,
            "no_recovered_without_answer": len(recovered_without_answer) == 0,
        },
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--home", default=os.environ.get("CORVIN_HOME") or str(Path.home() / ".corvin"),
                    help="CORVIN_HOME to read (default: $CORVIN_HOME or ~/.corvin)")
    ap.add_argument("--since", help="YYYY-MM-DD; tasks started before this are ignored")
    ap.add_argument("--json", action="store_true", help="print JSON instead of text")
    args = ap.parse_args(argv)

    report = build_report(Path(args.home).expanduser(), _parse_since(args.since))
    if args.json:
        json.dump(report, sys.stdout, indent=2, ensure_ascii=False)
        sys.stdout.write("\n")
        return 0
    print(f"Tasks seit {report['since'] or 'Beginn'}: {report['tasks']}")
    print(f"  abgeschlossen: {report['completed']} ({report['completion_rate']})")
    print(f"  nach Status: {report['by_status']}")
    e = report["exit_143"]
    print(f"  Exit 143: {e['total']} — erklärt durch Signal-Record: {e['explained_by_recorded_signal']}, "
          f"extern (kein Record): {e['external_no_record']}")
    r = report["reruns"]
    print(f"  Wiederholungen: {r['total']} — mit mehrfachem Präfix: {r['stacked_prefix']}, "
          f"als erfolgreich gemeldet ohne Ergebnis: {r['failed_but_announced']}")
    print(f"  Recovery-Audit: {report['recovery_audit']}")
    print("  Akzeptanz: " + ", ".join(f"{k}={'ja' if v else 'nein'}" for k, v in report["acceptance"].items()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
