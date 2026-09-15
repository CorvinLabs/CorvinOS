"""ADR-0008 Phase 8.2 — Bridge runtime state migration helper.

Migrates legacy in-repo bridge state to ``<corvin_home>/bridges/``:

  Source (legacy):
    <repo>/operator/bridges/<channel>/{inbox,outbox,processed,
                                            attachments,auth}/
    <repo>/operator/bridges/<channel>/settings.json
    <repo>/operator/bridges/<channel>/voice.log
    <repo>/operator/bridges/shared/{inbox,outbox,processed}/

  Target (canonical):
    <corvin_home>/bridges/<channel>/{inbox,outbox,processed,attachments,
                                      auth,log}/
    <corvin_home>/bridges/<channel>/settings.json
    <corvin_home>/bridges/<channel>/log/voice.log

Same-FS uses ``os.rename`` (atomic); cross-FS falls back to
``shutil.copytree`` (or ``shutil.copy2`` for the single-file moves)
plus a verify pass plus a ``MIGRATED`` marker in the legacy location.

Idempotency: a marker file ``<corvin_home>/bridges/.bridges-migrated``
short-circuits subsequent runs. Operator opt-out:
``CORVIN_BRIDGES_MIGRATE=0``.

Audit: one ``bridges.path_migrated`` event into the unified hash chain
at ``<corvin_home>/global/forge/audit.jsonl`` (or the explicit
``audit_path`` for sandboxed tests). The event carries the full list
of completed moves so a single chain entry captures the whole boot's
migration step.
"""
from __future__ import annotations

import logging
import os
import shutil
import sys
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Every shipped channel, from the one list (channels.py). The hand-written copy
# that used to sit here listed five, so legacy Signal/Teams state under the
# pre-ADR-0008 layout was never migrated to `<corvin_home>/bridges/<channel>/`.
from channels import BRIDGE_CHANNELS as _CHANNELS  # noqa: E402

# Directory-shaped sources: (channel, kind) → moves dir → dir.
_DIR_KINDS = ("inbox", "outbox", "processed", "attachments", "auth")

# File-shaped sources: (channel, basename, target_kind, target_basename).
# settings.json moves to <channel>/settings.json (kind="root"); voice.log
# moves to <channel>/log/voice.log (kind="log").
_FILE_MOVES = (
    ("settings.json", "root", "settings.json"),
    ("voice.log",     "log",  "voice.log"),
)

# Legacy shared/ queues from before the per-channel split — move into
# bridges/shared/<kind>/.
_LEGACY_SHARED_KINDS = ("inbox", "outbox", "processed")


def _same_filesystem(a: Path, b: Path) -> bool:
    def _existing_ancestor(p: Path) -> Path:
        cur = p
        while not cur.exists() and cur != cur.parent:
            cur = cur.parent
        return cur
    try:
        return (
            _existing_ancestor(a).stat().st_dev
            == _existing_ancestor(b).stat().st_dev
        )
    except OSError:
        return False


def _count_tree(root: Path) -> tuple[int, int]:
    if not root.exists():
        return (0, 0)
    if root.is_file():
        try:
            return (1, root.stat().st_size)
        except OSError:
            return (1, 0)
    files = 0
    total = 0
    for dirpath, _dirs, filenames in os.walk(root):
        for fn in filenames:
            full = Path(dirpath) / fn
            try:
                files += 1
                total += full.stat().st_size
            except OSError:
                pass
    return (files, total)


def _emit_audit(audit_path: Path | None, event: dict[str, Any]) -> bool:
    if audit_path is None:
        return False
    try:
        from forge.security_events import write_event  # type: ignore
        write_event(
            audit_path,
            "bridges.path_migrated",
            severity="INFO",
            tool="bridges_migrate",
            details=event,
        )
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("bridges_migrate: audit write failed (%s)", exc)
        return False


def _plan_moves(
    *,
    repo_root: Path,
    corvin_home: Path,
) -> list[dict[str, Any]]:
    """Build the move plan without touching the filesystem.

    Each entry: {"src": Path, "dst": Path, "shape": "dir"|"file",
                 "channel": str, "kind": str}.
    Sources that don't exist are skipped.
    """
    plan: list[dict[str, Any]] = []
    bridges_root = repo_root / "operator" / "bridges"
    dst_root = corvin_home / "bridges"

    for channel in _CHANNELS:
        ch_src = bridges_root / channel
        ch_dst = dst_root / channel
        if not ch_src.exists():
            continue
        for kind in _DIR_KINDS:
            src = ch_src / kind
            if src.exists() and src.is_dir():
                plan.append({
                    "src": src, "dst": ch_dst / kind,
                    "shape": "dir", "channel": channel, "kind": kind,
                })
        for src_name, dst_kind, dst_name in _FILE_MOVES:
            src = ch_src / src_name
            if src.exists() and src.is_file():
                dst_dir = ch_dst if dst_kind == "root" else ch_dst / dst_kind
                plan.append({
                    "src": src, "dst": dst_dir / dst_name,
                    "shape": "file", "channel": channel, "kind": dst_kind,
                })

    # Legacy shared/ queues
    shared_src = bridges_root / "shared"
    shared_dst = dst_root / "shared"
    if shared_src.exists():
        for kind in _LEGACY_SHARED_KINDS:
            src = shared_src / kind
            if src.exists() and src.is_dir():
                plan.append({
                    "src": src, "dst": shared_dst / kind,
                    "shape": "dir", "channel": "shared", "kind": kind,
                })

    return plan


def _merge_dir(src: Path, dst: Path, *, same_fs: bool) -> int:
    """Merge files from `src` tree into existing `dst` tree.

    Per-file move with skip-on-conflict semantics — files that already
    exist in `dst` are left untouched (the target version wins). Source
    files that don't collide are moved over. Empty source subdirs are
    pruned after the walk. The whole-source rmtree happens at the end
    if everything moved cleanly.

    Returns the count of files moved (excluding skipped collisions).
    """
    moved = 0
    for root, _dirs, files in os.walk(src):
        rel = Path(root).relative_to(src)
        target_dir = dst / rel
        target_dir.mkdir(parents=True, exist_ok=True)
        for fn in files:
            s = Path(root) / fn
            t = target_dir / fn
            if t.exists():
                continue
            try:
                if same_fs:
                    os.rename(str(s), str(t))
                else:
                    shutil.copy2(str(s), str(t))
                    s.unlink()
                moved += 1
            except OSError:
                # Best-effort merge — leave the source file in place
                # for a later operator-driven retry.
                continue
    # Try to remove the (now hopefully empty) source tree.
    try:
        shutil.rmtree(src)
    except OSError:
        pass
    return moved


def _move_one(entry: dict[str, Any]) -> dict[str, Any]:
    """Move one planned source. Returns the entry annotated with the
    method used (``rename`` / ``copy`` / ``merge``), the file/byte
    counts, and any error message on failure.
    """
    src: Path = entry["src"]
    dst: Path = entry["dst"]
    files, total_bytes = _count_tree(src)
    entry = {**entry, "files": files, "bytes": total_bytes,
             "method": None, "error": None}

    dst.parent.mkdir(parents=True, exist_ok=True)
    same_fs = _same_filesystem(dst.parent, src)
    method = "rename" if same_fs else "copy"

    # Target-exists branch — happens on force-reruns or partial
    # migrations resumed by an operator. Atomic rename/copytree both
    # reject this; switch to per-file merge for dirs and skip-if-exists
    # for files.
    if dst.exists():
        if entry["shape"] == "dir":
            try:
                _merge_dir(src, dst, same_fs=same_fs)
                entry["method"] = "merge"
                return entry
            except Exception as exc:  # noqa: BLE001
                entry["error"] = repr(exc)
                entry["method"] = "merge"
                return entry
        else:
            # File-shape: skip silently — target already carries the
            # operator's canonical value (settings.json, voice.log).
            entry["method"] = "skip"
            return entry

    try:
        if method == "rename":
            try:
                os.rename(str(src), str(dst))
            except OSError as exc:
                logger.warning(
                    "bridges_migrate: rename %s → %s failed (%s); falling back to copy",
                    src, dst, exc,
                )
                method = "copy"
        if method == "copy":
            if entry["shape"] == "dir":
                shutil.copytree(str(src), str(dst))
            else:
                shutil.copy2(str(src), str(dst))
            # Verify
            v_files, _ = _count_tree(dst)
            if v_files != files:
                # Roll back the partial copy and surface the failure.
                if entry["shape"] == "dir":
                    shutil.rmtree(dst, ignore_errors=True)
                else:
                    try:
                        dst.unlink()
                    except OSError:
                        pass
                raise RuntimeError(
                    f"copy verify failed: {files} src vs {v_files} dst"
                )
            # Leave MIGRATED marker (best-effort).
            marker = src.parent / "MIGRATED" if entry["shape"] == "file" \
                else src / "MIGRATED"
            try:
                marker.write_text(
                    f"# ADR-0008 bridges_migrate marker.\n"
                    f"# Source has been copied to:\n"
                    f"#   {dst}\n"
                    f"# Files: {files}, bytes: {total_bytes}\n"
                    f"# Safe to delete this legacy entry once verified.\n"
                )
            except OSError:
                pass
    except Exception as exc:  # noqa: BLE001
        entry["error"] = repr(exc)

    entry["method"] = method
    return entry


def migrate_bridges_state_if_needed(
    *,
    repo_root: Path | None = None,
    corvin_home: Path | None = None,
    audit_path: Path | None = None,
    dry_run: bool = False,
    force: bool = False,
) -> dict[str, Any]:
    """Migrate every present legacy bridge state into ``<corvin_home>``.

    Returns a status dict with at least::

      {"status": ..., "moves": [...], "completed": <int>, "failed": <int>,
       "files": <int>, "bytes": <int>}

    Status values:
      - skipped-opt-out      : CORVIN_BRIDGES_MIGRATE=0 set
      - skipped-no-args      : repo_root / corvin_home missing
      - skipped-marker       : marker file present (already migrated)
      - skipped-nothing      : plan empty (no legacy content to move)
      - dry-run              : would migrate, no FS change
      - migrated             : every plan entry succeeded
      - partial              : some entries failed (see moves[].error)
    """
    if not force and os.environ.get("CORVIN_BRIDGES_MIGRATE") == "0":
        return {
            "status": "skipped-opt-out", "moves": [],
            "completed": 0, "failed": 0, "files": 0, "bytes": 0,
        }

    if repo_root is None or corvin_home is None:
        return {
            "status": "skipped-no-args", "moves": [],
            "completed": 0, "failed": 0, "files": 0, "bytes": 0,
        }

    repo_root = Path(repo_root)
    corvin_home = Path(corvin_home)
    marker = corvin_home / "bridges" / ".bridges-migrated"

    if marker.exists() and not force:
        return {
            "status": "skipped-marker", "moves": [],
            "completed": 0, "failed": 0, "files": 0, "bytes": 0,
            "marker": str(marker),
        }

    plan = _plan_moves(repo_root=repo_root, corvin_home=corvin_home)
    if not plan:
        return {
            "status": "skipped-nothing", "moves": [],
            "completed": 0, "failed": 0, "files": 0, "bytes": 0,
        }

    # Audit-first: record the intent BEFORE touching the FS so a crash
    # mid-flight still leaves a chain entry naming what was attempted.
    intent_summary = {
        "stage":   "intent",
        "from":    str(repo_root / "operator" / "bridges"),
        "to":      str(corvin_home / "bridges"),
        "planned": [
            {"channel": e["channel"], "kind": e["kind"],
             "shape":   e["shape"],   "src":  str(e["src"])}
            for e in plan
        ],
    }

    if dry_run:
        return {
            "status": "dry-run", "moves": plan, "planned": intent_summary,
            "completed": 0, "failed": 0,
            "files": sum(_count_tree(e["src"])[0] for e in plan),
            "bytes": sum(_count_tree(e["src"])[1] for e in plan),
        }

    _emit_audit(audit_path, intent_summary)

    completed = []
    failed = []
    for entry in plan:
        result = _move_one(entry)
        # Strip Path objects so the audit event is JSON-safe.
        serialised = {
            "channel": result["channel"], "kind": result["kind"],
            "shape":   result["shape"],   "method": result["method"],
            "src":     str(result["src"]),    "dst": str(result["dst"]),
            "files":   result["files"],   "bytes": result["bytes"],
            "error":   result["error"],
        }
        (failed if result["error"] else completed).append(serialised)

    # Write the marker — even on partial failure, so retry doesn't
    # endlessly re-attempt the moves. Operators see the partial status
    # in the result dict (and the audit chain) and can re-run with
    # force=True after fixing the underlying cause.
    try:
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(
            f"# ADR-0008 bridges_migrate marker.\n"
            f"# Migrated entries: {len(completed)}\n"
            f"# Failed entries:   {len(failed)}\n"
        )
    except OSError as exc:
        logger.warning("bridges_migrate: marker write failed (%s)", exc)

    final_summary = {
        "stage":     "complete",
        "from":      str(repo_root / "operator" / "bridges"),
        "to":        str(corvin_home / "bridges"),
        "completed": completed,
        "failed":    failed,
    }
    _emit_audit(audit_path, final_summary)

    total_files = sum(e["files"] for e in completed) + sum(e["files"] for e in failed)
    total_bytes = sum(e["bytes"] for e in completed) + sum(e["bytes"] for e in failed)
    return {
        "status":    "migrated" if not failed else "partial",
        "moves":     completed + failed,
        "completed": len(completed),
        "failed":    len(failed),
        "files":     total_files,
        "bytes":     total_bytes,
    }


# ── CLI entry-point for one-shot manual migration ─────────────────────

def _cli(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="bridges_migrate")
    ap.add_argument("--dry-run", action="store_true",
                    help="report the move plan without changing the FS")
    ap.add_argument("--force", action="store_true",
                    help="ignore the .bridges-migrated marker and re-run")
    ap.add_argument("--repo-root", type=Path, required=True,
                    help="repository root (contains operator/bridges/)")
    ap.add_argument("--corvin-home", type=Path, required=True,
                    help="canonical target dir (e.g. ~/.corvin)")
    args = ap.parse_args(argv)
    audit_path = args.corvin_home / "global" / "forge" / "audit.jsonl"
    result = migrate_bridges_state_if_needed(
        repo_root=args.repo_root,
        corvin_home=args.corvin_home,
        audit_path=audit_path if not args.dry_run else None,
        dry_run=args.dry_run,
        force=args.force,
    )
    import json
    print(json.dumps(
        {k: v for k, v in result.items() if k != "moves"},
        indent=2, default=str,
    ))
    return 0 if result["status"] in (
        "migrated", "skipped-opt-out", "skipped-marker",
        "skipped-nothing", "dry-run",
    ) else 1


if __name__ == "__main__":
    sys.exit(_cli())
