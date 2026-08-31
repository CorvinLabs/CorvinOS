"""ST5 E2E: forge cleanup, forge runs, forge show, forge sync.

We exercise the CLI directly (no MCP server in the loop) on a workspace
we hand-prep with N synthetic runs, then validate that:
  - cleanup keeps exactly the most recent K
  - cleanup --purge-cache wipes the cache directory
  - runs lists newest-first with the right metadata
  - show returns the manifest+completion+summary for the requested id
  - sync copies promoted skills into a target dir, refuses non-forge skills
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from forge.registry import Registry
from forge.runner import run_tool


PASS = 0
FAIL = 0


def t(label: str, ok: bool, *, detail: str = "") -> None:
    global PASS, FAIL
    print(f"  {'PASS' if ok else 'FAIL'}  {label}{(' — ' + detail) if detail else ''}")
    if ok:
        PASS += 1
    else:
        FAIL += 1


PRIME_IMPL = '''#!/usr/bin/env python3
import json, sys
p = json.loads(sys.stdin.read())
print(json.dumps({
    "ok": True, "status": 200,
    "data": {"n": p["n"], "answer": p["n"] * 2},
    "error": None,
    "meta": {"deterministic": True}
}))
'''
PRIME_SCHEMA = {"type": "object", "required": ["n"],
                "properties": {"n": {"type": "integer"}}}


def _make_runs(root: Path, count: int) -> list[str]:
    """Direct registry+runner, no MCP, to keep this test fast."""
    reg = Registry(root)
    reg.create("doubler", "doubles n", PRIME_SCHEMA, PRIME_IMPL,
               meta={"deterministic": True})
    ids: list[str] = []
    for i in range(count):
        # different inputs so the cache doesn't merge them
        r = run_tool(reg, "doubler", {"n": i}, permission_mode="yes")
        ids.append(r.run_id)
        time.sleep(0.01)  # ensure distinct seconds-resolution timestamps
    return ids


def _cli(root: Path, *args: str) -> tuple[int, str]:
    proc = subprocess.run(
        [sys.executable, str(ROOT / "forge.py"), "--root", str(root), *args],
        capture_output=True, text=True, cwd=str(ROOT),
    )
    return proc.returncode, proc.stdout + proc.stderr


def _cli_stdout(root: Path, *args: str) -> tuple[int, str]:
    """Like _cli but returns stdout only — for callers that json.loads the
    output. stderr may carry Corvin strangler-fig deprecation lines that
    must not pollute structured-data output."""
    proc = subprocess.run(
        [sys.executable, str(ROOT / "forge.py"), "--root", str(root), *args],
        capture_output=True, text=True, cwd=str(ROOT),
    )
    return proc.returncode, proc.stdout


def test_cleanup_keeps_recent_n():
    print("\n[forge cleanup --keep 3 keeps newest 3 runs]")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        ids = _make_runs(root, 7)
        rc, out = _cli(root, "cleanup", "--keep", "3")
        t("cleanup rc=0", rc == 0)
        try:
            data = json.loads(out.strip().splitlines()[-1] if False else out)
        except json.JSONDecodeError:
            # output is the json plus possibly other lines; parse first {} block
            i = out.index("{")
            j = out.rindex("}")
            data = json.loads(out[i:j+1])
        t("cleanup deleted = 4", data.get("deleted") == 4,
          detail=f"deleted={data.get('deleted')}")
        t("cleanup kept = 3", data.get("kept") == 3)
        kept_ids = set(data.get("kept_ids", []))
        # IDs are timestamp_uuid; the newest 3 (last 3 created) should remain
        expected = set(ids[-3:])
        t("kept ids are the newest 3", kept_ids == expected,
          detail=f"got={sorted(kept_ids)} want={sorted(expected)}")
        runs_dir = root / "runs"
        t("only 3 dirs left on disk",
          sum(1 for p in runs_dir.iterdir() if p.is_dir()) == 3)


def test_cleanup_purges_cache():
    print("\n[cleanup --purge-cache wipes cache/]")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _make_runs(root, 2)
        # cache should have entries because tool is deterministic, but each
        # input is distinct → 2 entries
        cache_dir = root / "cache"
        t("cache populated by deterministic tool",
          cache_dir.exists() and len(list(cache_dir.glob("*.json"))) >= 1)
        rc, _ = _cli(root, "cleanup", "--keep", "0", "--purge-cache")
        t("cleanup --purge-cache rc=0", rc == 0)
        t("cache dir empty after purge",
          not cache_dir.exists() or len(list(cache_dir.glob("*.json"))) == 0)


def test_runs_lists_newest_first():
    print("\n[forge runs lists newest first]")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        ids = _make_runs(root, 4)
        rc, out = _cli(root, "run-list")
        t("rc=0", rc == 0)
        # Find each id in output and check ordering (newest first → ids[-1] before ids[0])
        lines = [ln for ln in out.splitlines() if "doubler" in ln]
        t("got 4 lines", len(lines) == 4, detail=f"got {len(lines)}")
        positions = [next(i for i, ln in enumerate(lines) if rid in ln)
                     for rid in ids]
        # Reverse-sorted (newest first): the line with ids[-1] comes first.
        t("newest run is first", positions[-1] < positions[0],
          detail=f"positions={positions}")


def test_show_returns_summary():
    print("\n[forge show returns the run's summary]")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        ids = _make_runs(root, 3)
        # default = most recent
        rc, out = _cli(root, "run-show")
        t("rc=0", rc == 0)
        t("most recent id appears in output", ids[-1] in out)
        # explicit id
        rc, out = _cli(root, "run-show", "--id", ids[0])
        t("rc=0 for explicit id", rc == 0)
        t("named run id appears", ids[0] in out)
        # JSON format
        rc, out = _cli_stdout(root, "run-show", "--id", ids[1], "--format", "json")
        rec = json.loads(out)
        t("json format has manifest", "manifest" in rec)
        t("json format has completion", "completion" in rec)
        t("json manifest.tool = doubler",
          rec["manifest"]["tool"] == "doubler")


def test_sync_copies_promoted_skill_to_target():
    print("\n[forge sync → target dir gets the SKILL.md folder]")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td) / "ws"
        target = Path(td) / "user_skills"
        # Prepare a workspace with a promoted skill
        reg = Registry(root)
        reg.create("doubler", "x2", PRIME_SCHEMA, PRIME_IMPL)
        reg.promote("doubler")
        t("promoted skill exists in workspace",
          (root / "skills" / "doubler" / "SKILL.md").exists())

        rc, out = _cli(root, "sync", "--target", str(target))
        t("sync rc=0", rc == 0)
        skill = target / "doubler" / "SKILL.md"
        t("skill copied to target", skill.exists())
        body = skill.read_text()
        t("copied SKILL.md still has forge frontmatter",
          "promoted_from: forge" in body)

        # Idempotency: a second sync just updates
        rc, out = _cli(root, "sync", "--target", str(target))
        t("second sync rc=0", rc == 0)
        t("output reports updated", "updated" in out)


def test_sync_refuses_to_overwrite_non_forge_skill():
    print("\n[sync refuses to clobber a non-forge SKILL.md at the target]")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td) / "ws"
        target = Path(td) / "user_skills"
        # Pre-populate target with a hand-written non-forge skill
        d = target / "doubler"
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text(
            "---\nname: doubler\ndescription: hand-written\n---\nbody\n"
        )
        # Now create a forge skill with the same name
        reg = Registry(root)
        reg.create("doubler", "x2", PRIME_SCHEMA, PRIME_IMPL)
        reg.promote("doubler")

        rc, out = _cli(root, "sync", "--target", str(target))
        t("sync rc=0", rc == 0)
        t("output says refused", "refused" in out)
        # the hand-written file is still in place
        body = (target / "doubler" / "SKILL.md").read_text()
        t("hand-written body untouched", "hand-written" in body)


def test_dry_run_makes_no_changes():
    print("\n[sync --dry-run does not write]")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td) / "ws"
        target = Path(td) / "user_skills"
        reg = Registry(root)
        reg.create("doubler", "x2", PRIME_SCHEMA, PRIME_IMPL)
        reg.promote("doubler")
        rc, out = _cli(root, "sync", "--target", str(target), "--dry-run")
        t("rc=0", rc == 0)
        t("output says skipped", "skipped" in out)
        t("nothing copied",
          not (target / "doubler" / "SKILL.md").exists())


def main() -> int:
    test_cleanup_keeps_recent_n()
    test_cleanup_purges_cache()
    test_runs_lists_newest_first()
    test_show_returns_summary()
    test_sync_copies_promoted_skill_to_target()
    test_sync_refuses_to_overwrite_non_forge_skill()
    test_dry_run_makes_no_changes()
    print(f"\n{PASS} passed, {FAIL} failed")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
