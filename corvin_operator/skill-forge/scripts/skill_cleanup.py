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


def _sessions_roots() -> list[Path]:
    """Every ``sessions/`` dir: tenant-native ``<home>/tenants/<tid>/sessions``
    plus the legacy ``<home>/sessions`` alias, de-duplicated by real path
    (the alias is a symlink onto ``tenants/_default/sessions``)."""
    home = corvin_home()
    seen: set[Path] = set()
    out: list[Path] = []
    for base in [home / "sessions", *sorted((home / "tenants").glob("*/sessions"))]:
        if not base.is_dir():
            continue
        key = base.resolve()
        if key in seen:
            continue
        seen.add(key)
        out.append(base)
    return out


def _session_skill_forge_roots() -> list[tuple[str, Path]]:
    """``(label, <sessions>/<chan>/skill-forge)`` for every session workspace."""
    out: list[tuple[str, Path]] = []
    for base in _sessions_roots():
        for child in sorted(base.iterdir()):
            sf = child / "skill-forge"
            if child.is_dir() and sf.exists():
                out.append((f"session/{child.name}", sf))
    return out


def _project_skill_forge_root() -> Path | None:
    """The project-scope workspace, ONLY when the operator names the repo.

    Project scope lives inside a repository (``<repo>/.corvin/skill-forge``);
    this script cannot enumerate repositories. ``<corvin_home>/skill-forge``
    — which the previous version pruned as "project" — is the USER scope in
    the tenant-native layout (``<tenant_home>/skill-forge``; the compat
    symlink made it look like a home-level dir). User scope is NEVER pruned.
    """
    root = os.environ.get("CORVIN_PROJECT_ROOT", "").strip()
    if not root:
        return None
    from forge.scope import scope_root  # noqa: PLC0415
    return scope_root("project", tenant_id="_default", project_root=Path(root)).parent / "skill-forge"


def cmd_sessions(args) -> int:
    ttl = args.ttl_days * 86400
    d_total = k_total = 0
    for root in _sessions_roots():
        print(f"[skill sessions cleanup]  root={root}  ttl={args.ttl_days}d  "
              f"dry-run={args.dry_run}")
        d, k = _prune_dir(root, max_age_seconds=ttl, dry_run=args.dry_run)
        d_total += d
        k_total += k
    print(f"  done — deleted={d_total} kept={k_total}")
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

    # session scope: <corvin_home>/tenants/<tid>/sessions/<chan>/skill-forge/
    for label, sf_root in _session_skill_forge_roots():
        p, k = _ungraded_in(
            sf_root, max_age_seconds=ttl, dry_run=args.dry_run,
            scope_label=label,
        )
        purged_total += p
        kept_total += k

    # project scope: <repo>/.corvin/skill-forge — only when CORVIN_PROJECT_ROOT
    # names the repo (see _project_skill_forge_root).
    proj_root = _project_skill_forge_root()
    if proj_root is not None and proj_root.is_dir():
        p, k = _ungraded_in(
            proj_root, max_age_seconds=ttl, dry_run=args.dry_run,
            scope_label="project",
        )
        purged_total += p
        kept_total += k

    # NOTE: user scope (<tenant_home>/skill-forge/) is intentionally
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
