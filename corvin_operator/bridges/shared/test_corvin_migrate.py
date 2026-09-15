"""Per-subtask E2E for Phase 4 — corvin_migrate on-disk helper.

Covers the contract documented in CLAUDE.md "Phase 4 — On-disk migration
helper":

  - Trigger: ~/.corvin/ doesn't exist AND ~/.corvinOS/ does
  - Same-FS path: os.rename() (atomic)
  - Cross-FS path: copytree + verify + MIGRATED marker
  - Idempotent: ~/.corvin/ already exists → no-op
  - dry_run leaves both dirs untouched
  - CORVIN_MIGRATE=0 disables the helper
  - Audit event session.path_migrated written into the unified
    hash chain (when forge package is importable)
"""
from __future__ import annotations

import importlib
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "operator" / "bridges" / "shared"))
sys.path.insert(0, str(REPO / "operator" / "forge"))

PASS = 0
FAIL = 0


def t(label: str, ok: bool, *, detail: str = "") -> None:
    global PASS, FAIL
    suffix = f" — {detail}" if detail else ""
    print(f"  {'PASS' if ok else 'FAIL'}  {label}{suffix}")
    if ok:
        PASS += 1
    else:
        FAIL += 1


def _fresh_module():
    sys.modules.pop("corvin_migrate", None)
    return importlib.import_module("corvin_migrate")


def _seed_legacy_tree(legacy: Path, *, files: int = 3) -> None:
    legacy.mkdir(parents=True)
    (legacy / "global" / "forge").mkdir(parents=True)
    for i in range(files):
        (legacy / "global" / "forge" / f"f{i}.txt").write_text(f"data-{i}")
    (legacy / "audit.jsonl").write_text("{}\n")


def test_no_legacy_no_op() -> None:
    print("\n[no legacy dir → no-op silent]")
    with tempfile.TemporaryDirectory() as td:
        td_p = Path(td)
        m = _fresh_module()
        result = m.migrate_home_if_needed(
            new_home=td_p / ".corvin",
            legacy_home=td_p / ".corvinOS",
        )
        t("status == skipped-no-legacy", result["status"] == "skipped-no-legacy",
          detail=str(result))
        t("new dir not created (defensive)",
          not (td_p / ".corvin").exists())


def test_corvin_already_exists_no_op() -> None:
    print("\n[.corvin already exists → idempotent no-op]")
    with tempfile.TemporaryDirectory() as td:
        td_p = Path(td)
        legacy = td_p / ".corvinOS"
        new = td_p / ".corvin"
        _seed_legacy_tree(legacy)
        new.mkdir()
        (new / "marker").write_text("already-here")
        m = _fresh_module()
        result = m.migrate_home_if_needed(new_home=new, legacy_home=legacy)
        t("status == skipped-target-exists",
          result["status"] == "skipped-target-exists",
          detail=str(result))
        t("legacy still intact", legacy.exists() and (legacy / "audit.jsonl").exists())
        t("corvin marker untouched",
          (new / "marker").read_text() == "already-here")


def test_opt_out_env() -> None:
    print("\n[CORVIN_MIGRATE=0 → no-op]")
    with tempfile.TemporaryDirectory() as td:
        td_p = Path(td)
        legacy = td_p / ".corvinOS"
        new = td_p / ".corvin"
        _seed_legacy_tree(legacy)
        os.environ["CORVIN_MIGRATE"] = "0"
        try:
            m = _fresh_module()
            result = m.migrate_home_if_needed(new_home=new, legacy_home=legacy)
            t("status == skipped-opt-out",
              result["status"] == "skipped-opt-out", detail=str(result))
            t("legacy still intact", legacy.exists())
            t("new not created", not new.exists())
        finally:
            os.environ.pop("CORVIN_MIGRATE", None)


def test_dry_run() -> None:
    print("\n[dry_run=True → reports plan, no FS change]")
    with tempfile.TemporaryDirectory() as td:
        td_p = Path(td)
        legacy = td_p / ".corvinOS"
        new = td_p / ".corvin"
        _seed_legacy_tree(legacy, files=2)
        m = _fresh_module()
        result = m.migrate_home_if_needed(
            new_home=new, legacy_home=legacy, dry_run=True,
        )
        t("status == dry-run", result["status"] == "dry-run")
        t("method reported", result.get("method") in ("rename", "copy"))
        t("legacy still intact", legacy.exists())
        t("new not created", not new.exists())


def test_same_fs_rename() -> None:
    print("\n[same-FS happy path → atomic rename]")
    with tempfile.TemporaryDirectory() as td:
        td_p = Path(td)
        legacy = td_p / ".corvinOS"
        new = td_p / ".corvin"
        _seed_legacy_tree(legacy, files=4)
        # Sandbox the audit chain target
        audit_path = td_p / "audit.jsonl"
        m = _fresh_module()
        result = m.migrate_home_if_needed(
            new_home=new, legacy_home=legacy, audit_path=audit_path,
        )
        t("status == migrated", result["status"] == "migrated", detail=str(result))
        t("method == rename", result["method"] == "rename")
        t("new dir exists", new.exists())
        t("legacy dir gone", not legacy.exists())
        t("contents transferred",
          (new / "global" / "forge" / "f0.txt").read_text() == "data-0")
        t("audit.jsonl in new tree",
          (new / "audit.jsonl").exists())
        # Audit event landed
        if audit_path.exists():
            line = audit_path.read_text().strip().splitlines()[-1]
            ev = json.loads(line)
            t("audit event session.path_migrated",
              ev.get("event_type") == "session.path_migrated",
              detail=ev.get("event_type"))
            t("audit details has from/to",
              "from" in ev.get("details", {}) and "to" in ev["details"])


def test_cross_fs_copy_with_marker(monkeypatched_rename: bool = True) -> None:
    print("\n[cross-FS → copytree + verify + MIGRATED marker]")
    with tempfile.TemporaryDirectory() as td:
        td_p = Path(td)
        legacy = td_p / ".corvinOS"
        new = td_p / ".corvin"
        _seed_legacy_tree(legacy, files=3)
        audit_path = td_p / "audit.jsonl"
        m = _fresh_module()

        # Force cross-FS branch by monkey-patching os.rename to raise OSError
        original_rename = os.rename

        def fake_rename(src, dst):
            raise OSError(18, "Invalid cross-device link")

        os.rename = fake_rename
        try:
            result = m.migrate_home_if_needed(
                new_home=new, legacy_home=legacy, audit_path=audit_path,
            )
        finally:
            os.rename = original_rename

        t("status == migrated", result["status"] == "migrated")
        t("method == copy", result["method"] == "copy")
        t("new dir exists", new.exists())
        t("contents copied",
          (new / "global" / "forge" / "f0.txt").read_text() == "data-0")
        # Cross-FS path leaves the legacy dir + a MIGRATED marker file
        t("MIGRATED marker placed in legacy",
          (legacy / "MIGRATED").exists())
        marker_text = (legacy / "MIGRATED").read_text()
        t("MIGRATED marker points at new", str(new) in marker_text)


def test_audit_chain_verifiable_after_migration() -> None:
    print("\n[hash chain verifiable across migration]")
    with tempfile.TemporaryDirectory() as td:
        td_p = Path(td)
        legacy = td_p / ".corvinOS"
        new = td_p / ".corvin"
        _seed_legacy_tree(legacy)
        audit_path = td_p / "audit.jsonl"
        # Pre-seed an event so the chain has a prev_hash to link to
        from forge.security_events import write_event, verify_chain
        write_event(audit_path, "session.boot", severity="INFO",
                    details={"who": "test"})

        m = _fresh_module()
        m.migrate_home_if_needed(
            new_home=new, legacy_home=legacy, audit_path=audit_path,
        )
        ok, problems = verify_chain(audit_path)
        t("verify_chain ok", ok, detail=str(problems[:3]))


def test_invalid_args_raise() -> None:
    print("\n[invalid args raise ValueError]")
    m = _fresh_module()
    raised = False
    try:
        m.migrate_home_if_needed(new_home=None, legacy_home=Path("/tmp/x"))
    except (TypeError, ValueError):
        raised = True
    t("None new_home rejected", raised)


def main() -> int:
    test_no_legacy_no_op()
    test_corvin_already_exists_no_op()
    test_opt_out_env()
    test_dry_run()
    test_same_fs_rename()
    test_cross_fs_copy_with_marker()
    test_audit_chain_verifiable_after_migration()
    test_invalid_args_raise()
    print(f"\n{PASS} passed, {FAIL} failed")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
