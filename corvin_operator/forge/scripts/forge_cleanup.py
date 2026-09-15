#!/usr/bin/env python3
"""forge_cleanup — TTL-based pruning of session/task scope workspaces.

Two modes (subcommands):

  forge_cleanup.py tasks
      Removes /tmp/.corvin/tasks/<task-id>/ directories whose mtime
      is older than --ttl-hours (default: 1).

  forge_cleanup.py sessions
      Removes ~/.corvin/sessions/<channel-id>/ directories whose
      mtime is older than --ttl-days (default: 30).

Both modes accept --dry-run (list what would be deleted, no rm).

User scope (~/.corvin/global/) and project scope (<repo>/.corvin/)
are NEVER cleaned up — they're permanent by design.
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
import time
from pathlib import Path

# Add operator/forge to sys.path so 'from forge.paths import corvin_home' works
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from forge.paths import corvin_home  # noqa: E402


_TASK_ROOTS = (
    Path("/tmp/.corvin/tasks"),
)


def _prune(root: Path, *, max_age_seconds: float, dry_run: bool) -> tuple[int, int]:
    """Walk root's direct children. Delete every dir whose mtime is older
    than max_age_seconds. Returns (deleted, kept) counts."""
    if not root.is_dir():
        return (0, 0)
    now = time.time()
    deleted = 0
    kept = 0
    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        age = now - child.stat().st_mtime
        if age > max_age_seconds:
            print(f"  {'WOULD-RM' if dry_run else 'RM'}  {child}  "
                  f"(age={age/3600:.1f}h)")
            if not dry_run:
                shutil.rmtree(child, ignore_errors=False)
            deleted += 1
        else:
            kept += 1
    return (deleted, kept)


def cmd_tasks(args) -> int:
    ttl = args.ttl_hours * 3600
    total_d = 0
    total_k = 0
    for root in _TASK_ROOTS:
        if not root.is_dir():
            continue
        print(f"[tasks cleanup]  root={root}  ttl={args.ttl_hours}h  "
              f"dry-run={args.dry_run}")
        d, k = _prune(root, max_age_seconds=ttl, dry_run=args.dry_run)
        print(f"  done — deleted={d} kept={k}")
        total_d += d
        total_k += k
    if total_d == 0 and total_k == 0:
        print(f"[tasks cleanup]  no tasks found in {list(map(str, _TASK_ROOTS))}")
    return 0


def cmd_sessions(args) -> int:
    ttl = args.ttl_days * 86400
    root = corvin_home() / "sessions"
    print(f"[sessions cleanup]  root={root}  ttl={args.ttl_days}d  "
          f"dry-run={args.dry_run}")
    d, k = _prune(root, max_age_seconds=ttl, dry_run=args.dry_run)
    print(f"  done — deleted={d} kept={k}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(prog="forge_cleanup")
    ap.add_argument("--dry-run", action="store_true",
                    help="list what would be deleted, do not rm")
    sub = ap.add_subparsers(dest="cmd", required=True)

    tasks = sub.add_parser("tasks", help="prune /tmp/.corvin/tasks/")
    tasks.add_argument("--ttl-hours", type=float, default=1.0)
    tasks.set_defaults(func=cmd_tasks)

    sessions = sub.add_parser("sessions",
                              help="prune ~/.corvin/sessions/")
    sessions.add_argument("--ttl-days", type=float, default=30.0)
    sessions.set_defaults(func=cmd_sessions)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
