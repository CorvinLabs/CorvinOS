#!/usr/bin/env python3
"""Sample which processes hold the memory of the CorvinOS user units (PLAN bg-task P2, item 8).

Measured 2026-10-19: ``corvin-voice-bridge-adapter`` reported ``MemoryPeak`` 24.8 GB on a 30 GB
host, and ``systemd-oomd`` killed ``corvin-webui`` on 2026-10-16. The peak itself is not recorded
per process, so the holder cannot be named after the fact. This sampler records it while it
happens, so a limit (``MemoryHigh``, a scope for heavy children) can be set from data.

It writes one JSON line per sample to ``--out`` **only when a unit's resident memory is above
``--threshold-gb``**, plus one baseline line per ``--baseline-every`` seconds. A line holds the
unit, its total RSS and its top holders as ``comm`` (the executable name) + RSS. It never records
command lines or environments: those can carry secrets.

Read-only. Linux cgroup v2 only; on anything else it exits 0 with a note.

    python3 scripts/unit_memory_sampler.py --once --threshold-gb 0
    python3 scripts/unit_memory_sampler.py --out ~/memory_samples.jsonl --interval 30 --threshold-gb 6
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

DEFAULT_UNITS = ("corvin-voice-bridge-adapter", "corvin-webui")
TOP_N = 8


def unit_procs_file(unit: str) -> Path | None:
    """``cgroup.procs`` of a user unit, or None when unavailable."""
    try:
        cg = subprocess.run(
            ["systemctl", "--user", "show", f"{unit}.service", "-p", "ControlGroup", "--value"],
            capture_output=True, text=True, timeout=10, check=False).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    if not cg:
        return None
    path = Path("/sys/fs/cgroup") / cg.lstrip("/") / "cgroup.procs"
    return path if path.exists() else None


def rss_kb(pid: int) -> int:
    try:
        for line in Path(f"/proc/{pid}/status").read_text(encoding="utf-8").splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1])
    except (OSError, ValueError, IndexError):
        pass
    return 0


def comm(pid: int) -> str:
    try:
        return Path(f"/proc/{pid}/comm").read_text(encoding="utf-8").strip()
    except OSError:
        return "?"


def sample(unit: str, procs_file: Path) -> dict:
    """One sample of *unit*: total RSS and the top holders (executable name only)."""
    try:
        pids = [int(x) for x in procs_file.read_text(encoding="utf-8").split() if x.isdigit()]
    except OSError:
        pids = []
    rows = sorted(((rss_kb(p), p) for p in pids), reverse=True)
    total_kb = sum(kb for kb, _ in rows)
    return {
        "unit": unit,
        "total_rss_gb": round(total_kb / 1024 / 1024, 2),
        "processes": len(pids),
        "top": [{"comm": comm(pid), "rss_gb": round(kb / 1024 / 1024, 2), "pid": pid}
                for kb, pid in rows[:TOP_N] if kb],
    }


def run(units, out: Path | None, interval: float, threshold_gb: float, baseline_every: float,
        once: bool, procs_override: dict[str, Path] | None = None) -> int:
    last_baseline = 0.0
    while True:
        now = time.time()
        due_baseline = (now - last_baseline) >= baseline_every
        for unit in units:
            procs = (procs_override or {}).get(unit) or unit_procs_file(unit)
            if procs is None:
                continue
            s = sample(unit, procs)
            if s["total_rss_gb"] >= threshold_gb or due_baseline:
                s["ts"] = round(now, 1)
                s["kind"] = "high" if s["total_rss_gb"] >= threshold_gb else "baseline"
                line = json.dumps(s, ensure_ascii=False)
                if out is None:
                    print(line)
                else:
                    with out.open("a", encoding="utf-8") as fh:
                        fh.write(line + "\n")
        if due_baseline:
            last_baseline = now
        if once:
            return 0
        time.sleep(interval)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--unit", action="append", help="user unit to sample (repeatable)")
    ap.add_argument("--out", help="JSONL file to append to (default: print)")
    ap.add_argument("--interval", type=float, default=30.0)
    ap.add_argument("--threshold-gb", type=float, default=6.0)
    ap.add_argument("--baseline-every", type=float, default=3600.0)
    ap.add_argument("--once", action="store_true", help="take one sample and exit")
    args = ap.parse_args(argv)
    if not sys.platform.startswith("linux"):
        print("unit_memory_sampler: Linux cgroup v2 only; nothing to do on this platform")
        return 0
    out = Path(args.out).expanduser() if args.out else None
    return run(args.unit or list(DEFAULT_UNITS), out, args.interval, args.threshold_gb,
               args.baseline_every, args.once)


if __name__ == "__main__":
    raise SystemExit(main())
