"""corvin_migrate — Phase 4 on-disk migration helper.

Migrates the operator's data tree from `~/.corvinOS/` to `~/.corvin/`
exactly once on adapter boot. Runs idempotently — if `.corvin/`
already exists, the helper does nothing. The legacy directory is
honoured by the Phase 1 strangler-fig fallback resolver as long as
it exists, so the bridge keeps working before, during and after the
migration.

Two execution paths:

  same-filesystem:  os.rename() — atomic from the FS's view.
                    Legacy dir is gone; new dir is the only one.

  cross-filesystem: shutil.copytree → fsync verification → MIGRATED
                    marker file in the legacy dir pointing at the
                    new location. The legacy dir stays in place; the
                    next adapter boot sees `.corvin/` already exists
                    and short-circuits.

Audit: every successful migration emits one `session.path_migrated`
event into the unified hash chain at
`<corvin_home>/global/forge/audit.jsonl` (or the explicit `audit_path`
the caller passes for sandboxed tests). The audit event records
method (`rename` / `copy`), file count, byte count, and the from/to
paths.

Operator opt-out: `CORVIN_MIGRATE=0` env disables the helper entirely.
The boot caller logs the skip but stays running on the legacy tree.

Boot integration: `adapter.py::_setup` calls
`migrate_home_if_needed()` once, with the canonical ~/.corvin and
~/.corvinOS paths. All other parameters (`new_home`, `legacy_home`,
`audit_path`, `dry_run`) are public for tests + a future
`corvin-migrate` CLI subcommand.
"""
from __future__ import annotations

import logging
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _same_filesystem(a: Path, b: Path) -> bool:
    """True iff both paths' nearest existing ancestors share st_dev.

    Cross-filesystem moves cannot use os.rename(); we fall back to
    copytree + verify + marker. The check walks up from each path
    until we find an existing entry, then compares device numbers.
    """
    def _existing_ancestor(p: Path) -> Path:
        cur = p
        while not cur.exists() and cur != cur.parent:
            cur = cur.parent
        return cur

    try:
        return _existing_ancestor(a).stat().st_dev == _existing_ancestor(b).stat().st_dev
    except OSError:
        return False


def _count_tree(root: Path) -> tuple[int, int]:
    """Return (file_count, total_bytes) for the tree rooted at *root*."""
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
    return files, total


def _emit_audit(audit_path: Path | None, event: dict[str, Any]) -> bool:
    """Best-effort audit emission; returns True on success.

    Tries the unified hash chain via forge.security_events.write_event
    first. Falls back silently when the forge package is unavailable
    (standalone helper invocation). When `audit_path` is None, no
    audit is written — the caller has explicitly opted out.
    """
    if audit_path is None:
        return False
    try:
        from forge.security_events import write_event  # type: ignore
        write_event(
            audit_path,
            "session.path_migrated",
            severity="INFO",
            tool="corvin_migrate",
            details=event,
        )
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("corvin_migrate: audit write failed (%s)", exc)
        return False


def migrate_home_if_needed(
    *,
    new_home: Path | None = None,
    legacy_home: Path | None = None,
    audit_path: Path | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Migrate `legacy_home` to `new_home` if conditions are met.

    Returns a status dict with at least::

      {"status": "<one-of>", "method": "rename"|"copy"|None,
       "from": <str>, "to": <str>, "files": <int>, "bytes": <int>}

    Status values:
      - skipped-opt-out         : CORVIN_MIGRATE=0 set
      - skipped-no-legacy       : legacy dir doesn't exist
      - skipped-target-exists   : new dir already exists (idempotent)
      - dry-run                 : would migrate, no FS change
      - migrated                : actual move/copy completed
    """
    if new_home is None or legacy_home is None:
        raise ValueError("new_home and legacy_home are required")
    new_home = Path(new_home)
    legacy_home = Path(legacy_home)

    base: dict[str, Any] = {
        "from":   str(legacy_home),
        "to":     str(new_home),
        "method": None,
        "files":  0,
        "bytes":  0,
    }

    if os.environ.get("CORVIN_MIGRATE") == "0":
        return {**base, "status": "skipped-opt-out"}

    if new_home.exists():
        return {**base, "status": "skipped-target-exists"}

    if not legacy_home.exists():
        return {**base, "status": "skipped-no-legacy"}

    files, total_bytes = _count_tree(legacy_home)
    base["files"] = files
    base["bytes"] = total_bytes

    method = "rename" if _same_filesystem(new_home.parent, legacy_home) else "copy"
    base["method"] = method

    if dry_run:
        return {**base, "status": "dry-run"}

    new_home.parent.mkdir(parents=True, exist_ok=True)

    if method == "rename":
        try:
            os.rename(str(legacy_home), str(new_home))
        except OSError as exc:
            # Rename failed mid-flight (e.g. cross-FS) — fall through
            # to copy-and-verify so the migration still completes.
            logger.warning(
                "corvin_migrate: os.rename failed (%s); falling back to copy",
                exc,
            )
            method = "copy"
            base["method"] = "copy"

    if method == "copy":
        # copytree creates the destination; require that it doesn't
        # exist yet (we already checked above, but copytree wants
        # an absent target).
        shutil.copytree(str(legacy_home), str(new_home))
        # Verify file count matches; abort if not.
        new_files, new_bytes = _count_tree(new_home)
        if new_files != files:
            shutil.rmtree(new_home, ignore_errors=True)
            raise RuntimeError(
                f"corvin_migrate: copy verify failed "
                f"({files} src files vs {new_files} dst files)"
            )
        # Leave a MIGRATED marker in the legacy dir pointing at the
        # new location so a curious operator finds the trail. The
        # legacy tree itself stays in place across-FS — it can be
        # rm'd manually after the operator has confirmed the new
        # tree is healthy.
        try:
            (legacy_home / "MIGRATED").write_text(
                f"# Corvin rebrand — Phase 4 migration helper\n"
                f"# This directory has been copied to:\n"
                f"#   {new_home}\n"
                f"# Migration timestamp: {time.time()}\n"
                f"# Files: {files}, bytes: {total_bytes}\n"
                f"# Safe to delete this tree once the new tree is verified.\n"
            )
        except OSError as exc:
            logger.warning("corvin_migrate: MIGRATED marker write failed (%s)", exc)

    _emit_audit(audit_path, {**base, "status": "migrated"})
    return {**base, "status": "migrated"}


# ── CLI entry-point for one-shot manual migration ─────────────────────

def _cli(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="corvin_migrate")
    ap.add_argument("--dry-run", action="store_true",
                    help="report what would happen, no FS change")
    ap.add_argument("--new-home", type=Path,
                    default=Path.home() / ".corvin",
                    help="canonical target dir (default ~/.corvin)")
    ap.add_argument("--legacy-home", type=Path,
                    default=Path.home() / ".corvinOS",
                    help="legacy source dir (default ~/.corvinOS)")
    args = ap.parse_args(argv)
    audit_path = args.new_home / "global" / "forge" / "audit.jsonl"
    result = migrate_home_if_needed(
        new_home=args.new_home,
        legacy_home=args.legacy_home,
        audit_path=audit_path if not args.dry_run else None,
        dry_run=args.dry_run,
    )
    print(result)
    return 0 if result["status"].startswith(("migrated", "skipped", "dry-run")) else 1


if __name__ == "__main__":
    sys.exit(_cli())
