"""E2E: skill_cleanup — TTL prune of tasks/sessions, ungraded mode purges
skills, user scope never touched."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
SCRIPT = REPO / "operator" / "skill-forge" / "scripts" / "skill_cleanup.py"

PASS = 0
FAIL = 0


def t(label, ok, *, detail=""):
    global PASS, FAIL
    print(f"  {'PASS' if ok else 'FAIL'}  {label}{(' — ' + detail) if detail else ''}")
    if ok:
        PASS += 1
    else:
        FAIL += 1


def _set_old_mtime(path: Path, age_seconds: float):
    target = time.time() - age_seconds
    os.utime(path, (target, target))


def _set_old_mtime_recursive(path: Path, age_seconds: float):
    target = time.time() - age_seconds
    for p in [path, *path.rglob("*")]:
        try:
            os.utime(p, (target, target))
        except OSError:
            pass


# Pre-import path so the script's own sys.path.insert doesn't surprise us
sys.path.insert(0, str(REPO / "operator" / "skill-forge"))
from skill_forge.registry import SkillRegistry  # noqa: E402

# Sandbox the plugin-slot mirror so this test never touches the real
# operator/skill-forge/skills/dyn/ tree.
_SLOT_TMP = tempfile.mkdtemp(prefix="sf-slot-test-")
os.environ["CORVIN_PLUGIN_SLOT_DIR"] = _SLOT_TMP


GOOD_BODY = (
    "# x\n\nA reasonable heuristic body for a skill, no injection, "
    "no secrets, fits within 8 KiB.\n"
)


def test_tasks_prune():
    print("\n[tasks cleanup — old gone, young stays]")
    prefix = f"sf-{uuid.uuid4().hex[:8]}"
    # Synthesize task dirs under the canonical root.
    tasks_root = Path("/tmp/.corvin/tasks")
    tasks_root.mkdir(parents=True, exist_ok=True)
    old_dir = tasks_root / f"{prefix}-old" / "skill-forge"
    young_dir = tasks_root / f"{prefix}-young" / "skill-forge"
    old_dir.mkdir(parents=True)
    young_dir.mkdir(parents=True)
    _set_old_mtime(old_dir, 7200)  # 2h
    _set_old_mtime(young_dir, 60)  # 1m

    r = subprocess.run(
        [sys.executable, str(SCRIPT), "tasks", "--ttl-hours", "1"],
        capture_output=True, text=True, check=False,
    )
    t("script exit 0", r.returncode == 0,
      detail=f"stdout={r.stdout!r} stderr={r.stderr!r}")
    t("old skill-forge gone", not old_dir.exists())
    t("young skill-forge kept", young_dir.exists())

    # cleanup
    for p in (tasks_root / f"{prefix}-old", tasks_root / f"{prefix}-young"):
        if p.exists():
            shutil.rmtree(p, ignore_errors=True)


def test_sessions_dry_run():
    print("\n[sessions cleanup — dry-run lists, doesn't rm]")
    with tempfile.TemporaryDirectory() as td:
        sess_root = Path(td) / "sessions"
        old_chan = sess_root / "discord:test-old" / "skill-forge"
        young_chan = sess_root / "discord:test-young" / "skill-forge"
        old_chan.mkdir(parents=True)
        young_chan.mkdir(parents=True)
        _set_old_mtime(old_chan, 86400 * 60)
        _set_old_mtime(young_chan, 86400 * 1)

        r = subprocess.run(
            [sys.executable, str(SCRIPT), "--dry-run", "sessions",
             "--ttl-days", "30"],
            capture_output=True, text=True, check=False,
            env={**os.environ, "CORVIN_HOME": td},
        )
        t("dry-run exit 0", r.returncode == 0,
          detail=f"stderr={r.stderr!r}")
        t("WOULD-RM mentioned for old chan",
          "WOULD-RM" in r.stdout and "test-old" in r.stdout,
          detail=r.stdout)
        t("old skill-forge still exists (dry run)", old_chan.exists())
        t("young skill-forge still exists", young_chan.exists())


def test_sessions_actual_rm():
    print("\n[sessions cleanup — real rm of old]")
    with tempfile.TemporaryDirectory() as td:
        sess_root = Path(td) / "sessions"
        old_chan = sess_root / "discord:test-real" / "skill-forge"
        young_chan = sess_root / "discord:test-young" / "skill-forge"
        old_chan.mkdir(parents=True)
        young_chan.mkdir(parents=True)
        (old_chan / "skills_registry.json").write_text("{}")
        _set_old_mtime(old_chan, 86400 * 60)
        _set_old_mtime(young_chan, 86400 * 1)

        r = subprocess.run(
            [sys.executable, str(SCRIPT), "sessions", "--ttl-days", "30"],
            capture_output=True, text=True, check=False,
            env={**os.environ, "CORVIN_HOME": td},
        )
        t("exit 0", r.returncode == 0)
        t("old skill-forge removed", not old_chan.exists())
        t("young skill-forge kept", young_chan.exists())


def test_ungraded_mode():
    print("\n[ungraded mode — old + 0 grades = purge]")
    with tempfile.TemporaryDirectory() as td:
        # Use session scope under CORVIN_HOME so we don't hit /tmp tasks.
        sess_dir = Path(td) / "sessions" / "ch1" / "skill-forge"
        sess_dir.mkdir(parents=True)
        reg = SkillRegistry(sess_dir)
        # Two ungraded skills; one old one young.
        reg.create(name="old.ungraded", type="domain", body_md=GOOD_BODY,
                   description="ancient", claim={})
        reg.create(name="young.ungraded", type="domain", body_md=GOOD_BODY,
                   description="fresh", claim={})
        # One graded but old → should be SAFE
        reg.create(name="old.graded", type="domain", body_md=GOOD_BODY,
                   description="old but graded", claim={})
        reg.grade("old.graded", "r1", 0.7)
        # Mark old.* as 8 days old by editing manifest's created_at
        old_age = time.time() - 86400 * 8
        import json
        man = sess_dir / "skills_registry.json"
        data = json.loads(man.read_text())
        data["old.ungraded"]["created_at"] = old_age
        data["old.graded"]["created_at"] = old_age
        man.write_text(json.dumps(data))

        r = subprocess.run(
            [sys.executable, str(SCRIPT), "ungraded", "--ttl-days", "7"],
            capture_output=True, text=True, check=False,
            env={**os.environ, "CORVIN_HOME": td},
        )
        t("exit 0", r.returncode == 0,
          detail=f"stderr={r.stderr!r}")
        # Re-read registry
        reg2 = SkillRegistry(sess_dir)
        names = {s.name for s in reg2.list()}
        t("old.ungraded purged", "old.ungraded" not in names,
          detail=f"names={names}")
        t("young.ungraded kept (too young)", "young.ungraded" in names)
        t("old.graded kept (has grades)", "old.graded" in names)


def test_user_scope_never_pruned():
    print("\n[user scope skills survive ungraded mode]")
    with tempfile.TemporaryDirectory() as td:
        # User scope dir per forge.scope.scope_root('user'):
        # corvin_home() / 'global' / 'forge'  -> SkillForge sibling:
        # corvin_home() / 'global' / 'skill-forge'
        user_dir = Path(td) / "global" / "skill-forge"
        user_dir.mkdir(parents=True)
        reg = SkillRegistry(user_dir)
        reg.create(name="user.ancient", type="domain", body_md=GOOD_BODY,
                   description="durable", claim={})
        # Make it 30 days old
        import json
        man = user_dir / "skills_registry.json"
        data = json.loads(man.read_text())
        data["user.ancient"]["created_at"] = time.time() - 86400 * 30
        man.write_text(json.dumps(data))

        r = subprocess.run(
            [sys.executable, str(SCRIPT), "ungraded", "--ttl-days", "7"],
            capture_output=True, text=True, check=False,
            env={**os.environ, "CORVIN_HOME": td},
        )
        t("exit 0", r.returncode == 0)
        reg2 = SkillRegistry(user_dir)
        names = {s.name for s in reg2.list()}
        t("user-scope skill survives", "user.ancient" in names,
          detail=f"names={names}")


def main() -> int:
    test_tasks_prune()
    test_sessions_dry_run()
    test_sessions_actual_rm()
    test_ungraded_mode()
    test_user_scope_never_pruned()
    print(f"\n{PASS} passed, {FAIL} failed")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
