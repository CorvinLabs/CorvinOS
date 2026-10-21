#!/usr/bin/env python3
"""Interruption report: how often turns are cut off, per class C1..C9 (PLAN-0958 P0, T-0201).

Read-only. Reads the tenant audit chain and the chat task directories of one CORVIN_HOME root
and prints a markdown table plus the share of turns with side effects. It never writes to the
root, so it is safe against the live installation.

Classification is not duplicated here: the restart cause test is
``corvin_console.turn_recovery.is_restart_kill`` (RESTART_EXIT_CODES, reaper evidence,
shutdown window), the same predicate the restart re-run uses.

Two sources, two views (both printed, they are not summed):
  * audit  - every ``task.failed`` record in the chain (full history, no cancel records exist)
  * dirs   - the current ``<home>/tenants/<tid>/sessions/*/tasks/*.json`` (terminal status)

Classes with no recorded signal in a source are reported as 0 and named in the notes, not guessed:
C5 (wedge), C6 (budget), C7 (gate refusal) and C8 (permanent) have no field in either source.

Usage:
  CORVIN_HOME=<root> python3 scripts/task_interruption_report.py
  python3 scripts/task_interruption_report.py --home <root> --tenant _default
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "core" / "console"))

# Cancel reason written by chat_runtime.py (event key "reason"); C2 today (ADR-2261 matrix).
DISCONNECT_REASON = "client_disconnected_mid_turn"
RECOVERY_ACTIONS = ("chat.turn.recovery_started", "chat.turn.recovery_exhausted")
# Tools whose call writes a file. Bash is counted separately: the chain records the tool
# name only, not the command, so a Bash call may or may not write.
WRITE_TOOLS = frozenset({"Write", "Edit", "MultiEdit", "NotebookEdit"})
SHELL_TOOLS = frozenset({"Bash", "bash"})
MESSAGE_RE = re.compile(r"send|post|reply|publish", re.IGNORECASE)
CLASSES = tuple(f"C{i}" for i in range(1, 10))
CLASS_LABEL = {
    "C1": "Host restart",
    "C2": "Client disconnect",
    "C3": "Operator cancel",
    "C4": "Worker timeout",
    "C5": "Wedge",
    "C6": "Budget exhausted",
    "C7": "Gate refusal",
    "C8": "Permanent",
    "C9": "Unknown",
}


def _read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _events(path: Path) -> list[dict]:
    out: list[dict] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return out
    for line in lines:
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if isinstance(ev, dict):
            out.append(ev)
    return out


def audit_view(chain: Path, restart_codes: frozenset) -> tuple[Counter, Counter, int, int]:
    """Classify every task.failed in the chain. Returns (classes, recovery actions, lines, unparsable)."""
    classes: Counter = Counter()
    actions: Counter = Counter()
    lines = bad = 0
    if not chain.is_file():
        return classes, actions, lines, bad
    with chain.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            lines += 1
            try:
                rec = json.loads(line)
            except ValueError:
                bad += 1
                continue
            if not isinstance(rec, dict):
                continue
            details = rec.get("details") if isinstance(rec.get("details"), dict) else {}
            etype = rec.get("event_type")
            if etype == "task.failed":
                if details.get("timed_out") is True:
                    classes["C4"] += 1
                elif details.get("exit_code") in restart_codes:
                    classes["C1"] += 1
                else:
                    classes["C9"] += 1
            elif etype == "console.action_performed" and details.get("action") in RECOVERY_ACTIONS:
                actions[details["action"]] += 1
    return classes, actions, lines, bad


def dir_view(tasks_root: Path, marker: object, is_restart_kill, restart_codes: frozenset) -> dict:
    """Classify the current task directories. Returns counts used by the table and shares."""
    classes: Counter = Counter()
    turns = interrupted = side_sure = side_maybe = side_interrupted = 0
    for meta_file in tasks_root.glob("*/tasks/*.json"):
        if meta_file.name == "recovery_attempts.json":
            continue
        meta = _read_json(meta_file)
        status = meta.get("status")
        # "interrupted" (PLAN-0958 P1: reaped orphans, killed workers) is the status this report
        # exists for; skipping it dropped every post-P1 C1 case from the table.
        if not meta.get("task_id") or status not in ("completed", "failed", "cancelled", "interrupted"):
            continue
        events = _events(meta_file.with_name(meta_file.stem + ".events.jsonl"))
        tools = [e.get("tool_name") or "" for e in events if e.get("event") == "tool_use"]
        sure = any(t in WRITE_TOOLS or MESSAGE_RE.search(t) for t in tools)
        maybe = sure or any(t in SHELL_TOOLS for t in tools)
        turns += 1
        side_sure += sure
        side_maybe += maybe
        cls = None
        if status == "failed":
            if is_restart_kill(meta, events[-1] if events else {}, marker):
                cls = "C1"
            else:
                cls = "C9"
        elif status == "cancelled":
            reason = next((e.get("reason") for e in events if e.get("event") == "task.cancelled"), None)
            cls = "C2" if reason == DISCONNECT_REASON else "C3"
        if cls:
            classes[cls] += 1
            interrupted += 1
            side_interrupted += maybe
    return {
        "classes": classes, "turns": turns, "interrupted": interrupted,
        "side_sure": side_sure, "side_maybe": side_maybe, "side_interrupted": side_interrupted,
    }


def _pct(part: int, whole: int) -> str:
    return f"{100 * part / whole:.1f} %" if whole else "n/a"


def render(home: Path, tenant: str, audit, dirs) -> str:
    a_cls, a_actions, a_lines, a_bad = audit
    d = dirs
    rows = []
    for c in CLASSES:
        rows.append(f"| {c} | {CLASS_LABEL[c]} | {a_cls.get(c, 0)} | {d['classes'].get(c, 0)} |")
    rows.append(f"| **Sum** | | **{sum(a_cls.values())}** | **{sum(d['classes'].values())}** |")
    lines = [
        "# Task interruption report",
        "",
        f"Root: `{home}` · tenant: `{tenant}` · audit lines: {a_lines} (unparsable: {a_bad})",
        "",
        "| Class | Meaning | Audit chain (`task.failed`, full history) | Task dirs (current, terminal) |",
        "|---|---|---:|---:|",
        *rows,
        "",
        f"Terminal turns in task dirs: **{d['turns']}** · interrupted: **{d['interrupted']}**",
        "",
        "| Side effects | Turns | Share of all turns | Share of interrupted turns |",
        "|---|---:|---:|---:|",
        f"| Write/Edit or message tool (lower bound) | {d['side_sure']} | {_pct(d['side_sure'], d['turns'])} | "
        f"n/a |",
        f"| plus any Bash call (upper bound) | {d['side_maybe']} | {_pct(d['side_maybe'], d['turns'])} | "
        f"{_pct(d['side_interrupted'], d['interrupted'])} |",
        "",
        f"Recovery events in audit chain: started {a_actions.get(RECOVERY_ACTIONS[0], 0)}, "
        f"exhausted {a_actions.get(RECOVERY_ACTIONS[1], 0)}",
        "",
        "Notes:",
        "- Audit C1 = signal exit (RESTART_EXIT_CODES) without `timed_out`; it has no shutdown evidence. "
        "Dir C1 = `is_restart_kill` (signal exit plus reaper or shutdown-window evidence).",
        "- Audit has no cancel records, so C2/C3 appear only in the dir view.",
        "- The dir view has no `timed_out` field: a timed-out signal kill is C1 there when it has "
        "reaper or shutdown evidence.",
        "- C5-C8 have no recorded field in either source and are reported as 0, not estimated. "
        "C8 exits (`1`) fall into C9.",
    ]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--home", default=os.environ.get("CORVIN_HOME"), help="CORVIN_HOME root (default: $CORVIN_HOME)")
    ap.add_argument("--tenant", default="_default")
    args = ap.parse_args(argv)
    if not args.home:
        ap.error("no root: pass --home or set CORVIN_HOME")
    home = Path(args.home).expanduser().resolve()
    if not home.is_dir():
        ap.error(f"root is not a directory: {home}")
    # The audit path is resolved by the one canonical helper, never composed here (CLAUDE.md).
    os.environ["CORVIN_HOME"] = str(home)
    from core.paths.tenant import tenant_audit_chain
    from corvin_console import turn_recovery
    from corvin_core.failure_classes import RESTART_EXIT_CODES

    codes = RESTART_EXIT_CODES
    marker = turn_recovery._read_json(turn_recovery._marker_path(args.tenant)).get("at")
    audit = audit_view(tenant_audit_chain(args.tenant), codes)
    dirs = dir_view(home / "tenants" / args.tenant / "sessions", marker,
                    turn_recovery.is_restart_kill, codes)
    sys.stdout.write(render(home, args.tenant, audit, dirs))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
