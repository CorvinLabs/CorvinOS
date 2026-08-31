#!/usr/bin/env python3
"""skill_cleanup — TTL-based pruning of session/task scope skill workspaces.

Three subcommands:

  skill_cleanup.py tasks      --ttl-hours 1
      RM /tmp/.corvin/tasks/<task-id>/skill-forge/ dirs whose mtime is
      older than --ttl-hours.

  skill_cleanup.py sessions   --ttl-days  30
      RM ~/.corvin/sessions/<channel-id>/skill-forge/ dirs whose mtime
      is older than --ttl-days.

  skill_cleanup.py ungraded   --ttl-days  7
      Walk task+session+project skill registries; delete every skill
      whose ``len(grades) == 0`` AND whose ``created_at`` is older than
      --ttl-days. User scope is NEVER pruned.

All modes accept --dry-run.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path

# Make the forge plugin importable so we share corvin_home() and
# the SkillRegistry's audit-event writer.
PLUGINS = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PLUGINS / "forge"))
sys.path.insert(0, str(PLUGINS / "skill-forge"))

from forge.paths import corvin_home  # noqa: E402
from skill_forge.registry import SkillRegistry  # noqa: E402


_TASK_ROOTS = (
    Path("/tmp/.corvin/tasks"),
)


def _prune_dir(root: Path, *, max_age_seconds: float, dry_run: bool,
               subdir: str = "skill-forge") -> tuple[int, int]:
    """For each direct child of ``root``, look for child/<subdir>; if its
    mtime is older than max_age_seconds, RM it (or the whole child if
    its only content is the skill-forge subdir). Returns (deleted, kept)."""
    if not root.is_dir():
        return (0, 0)
    now = time.time()
    deleted = 0
    kept = 0
    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        sf = child / subdir
        if not sf.exists():
            continue
        age = now - sf.stat().st_mtime
        if age > max_age_seconds:
            print(f"  {'WOULD-RM' if dry_run else 'RM'}  {sf}  "
                  f"(age={age/3600:.1f}h)")
            if not dry_run:
                shutil.rmtree(sf, ignore_errors=False)
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
        print(f"[skill tasks cleanup]  root={root}  ttl={args.ttl_hours}h  "
              f"dry-run={args.dry_run}")
        d, k = _prune_dir(root, max_age_seconds=ttl, dry_run=args.dry_run)
        print(f"  done — deleted={d} kept={k}")
        total_d += d
        total_k += k
    if total_d == 0 and total_k == 0:
        print(f"[skill tasks cleanup]  no skill-forge dirs in "
              f"{list(map(str, _TASK_ROOTS))}")
    return 0


def cmd_sessions(args) -> int:
    ttl = args.ttl_days * 86400
    root = corvin_home() / "sessions"
    print(f"[skill sessions cleanup]  root={root}  ttl={args.ttl_days}d  "
          f"dry-run={args.dry_run}")
    d, k = _prune_dir(root, max_age_seconds=ttl, dry_run=args.dry_run)
    print(f"  done — deleted={d} kept={k}")
    return 0


def _ungraded_in(root: Path, *, max_age_seconds: float, dry_run: bool,
                 scope_label: str) -> tuple[int, int]:
    """Walk a SkillRegistry root, delete every skill that has 0 grades
    AND was created longer ago than max_age_seconds. Returns (purged, kept)."""
    if not (root / "skills_registry.json").exists():
        return (0, 0)
    reg = SkillRegistry(root)
    now = time.time()
    purged = 0
    kept = 0
    for spec in reg.list():
        age = now - spec.created_at
        if spec.n_grades == 0 and age > max_age_seconds:
            print(f"  {'WOULD-RM' if dry_run else 'RM'}  "
                  f"[{scope_label}] {spec.name}  "
                  f"(age={age/86400:.1f}d, grades=0)")
            if not dry_run:
                # Use SkillRegistry.delete so the audit event is written
                # ('skill.auto_purge' via reason flag).
                reg._audit("skill.auto_purge", spec,
                           extra={"reason": "ungraded ttl"})
                reg.delete(spec.name, reason="auto_purge ungraded")
            purged += 1
        else:
            kept += 1
    return (purged, kept)


def cmd_ungraded(args) -> int:
    ttl = args.ttl_days * 86400
    print(f"[skill ungraded cleanup]  ttl={args.ttl_days}d  "
          f"dry-run={args.dry_run}")
    purged_total = 0
    kept_total = 0

    # task scope: scan /tmp/.corvin/tasks
    for tasks_root in _TASK_ROOTS:
        if not tasks_root.is_dir():
            continue
        for child in sorted(tasks_root.iterdir()):
            if child.is_dir() and (child / "skill-forge").exists():
                p, k = _ungraded_in(
                    child / "skill-forge",
                    max_age_seconds=ttl, dry_run=args.dry_run,
                    scope_label=f"task/{child.name}",
                )
                purged_total += p
                kept_total += k

    # session scope: <corvin_home>/sessions/<chan>/skill-forge/
    sess_root = corvin_home() / "sessions"
    if sess_root.is_dir():
        for child in sorted(sess_root.iterdir()):
            if child.is_dir() and (child / "skill-forge").exists():
                p, k = _ungraded_in(
                    child / "skill-forge",
                    max_age_seconds=ttl, dry_run=args.dry_run,
                    scope_label=f"session/{child.name}",
                )
                purged_total += p
                kept_total += k

    # project scope: <corvin_home>/skill-forge/ — see forge.scope.scope_root
    # The forge layout puts project workspace at <corvin_home>/forge,
    # so we sit alongside it at <corvin_home>/skill-forge.
    proj_root = corvin_home() / "skill-forge"
    if proj_root.is_dir():
        p, k = _ungraded_in(
            proj_root, max_age_seconds=ttl, dry_run=args.dry_run,
            scope_label="project",
        )
        purged_total += p
        kept_total += k

    # NOTE: user scope (~/.corvin/global/skill-forge/) is intentionally
    # NEVER pruned — those are durable, operator-blessed skills.
    print(f"  done — purged={purged_total} kept={kept_total}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(prog="skill_cleanup")
    ap.add_argument("--dry-run", action="store_true",
                    help="list what would be deleted, do not rm")
    sub = ap.add_subparsers(dest="cmd", required=True)

    tasks = sub.add_parser("tasks",
                           help="prune /tmp/.corvin/tasks/")
    tasks.add_argument("--ttl-hours", type=float, default=1.0)
    tasks.set_defaults(func=cmd_tasks)

    sessions = sub.add_parser("sessions",
                              help="prune <corvin_home>/sessions/")
    sessions.add_argument("--ttl-days", type=float, default=30.0)
    sessions.set_defaults(func=cmd_sessions)

    ungraded = sub.add_parser(
        "ungraded",
        help="purge skills with 0 grades older than ttl-days "
             "(task+session+project; user scope never pruned)",
    )
    ungraded.add_argument("--ttl-days", type=float, default=7.0)
    ungraded.set_defaults(func=cmd_ungraded)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
