"""tenant_migrate — ADR-0007 Phase 1.4 on-disk migration helper.

Moves legacy single-operator paths under ``<corvin_home>/global/`` (and
the sibling top-level subdirectories ``sessions/``, ``forge/``,
``skill-forge/``, ``voice/``, ``cowork/``) into the new tenant layout
``<corvin_home>/tenants/_default/<sub>/``. After the physical move,
the helper leaves a relative symlink at the legacy location so the
existing state-store default branches (which pass ``tenant_id=None``
and resolve to legacy paths) keep reading and writing to the same
underlying files.

Strategy: ``os.rename`` plus ``os.symlink``.

* Single filesystem assumption — both source and target are subtrees
  of ``<corvin_home>``. Cross-FS migration is not supported here; the
  Phase 4 rebrand helper (``corvin_migrate.py``) handles the
  ``~/.corvin → ~/.corvin`` cross-FS scenario at a layer above.
* Idempotent — a ``.tenant-migrated`` marker file at the home root
  short-circuits subsequent boots. If the marker is removed by an
  operator, the helper will re-evaluate and only migrate subdirs that
  are not already symlinks.
* Audit-first — every successful migration emits one
  ``tenant.path_migrated`` event into the unified hash chain BEFORE
  the rename, so the record lands in the chain that is about to move.
  The audit write is best-effort; an audit failure never blocks the
  physical migration.
* Opt-out — ``CORVIN_TENANT_MIGRATE=0`` (env) skips the helper
  entirely and leaves the legacy layout addressable.

NOT wired into adapter boot in this commit. Operator-driven migration
only — Phase 1.5 or a dedicated rollout commit will wire it in.
"""
from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Top-level subdirectories of <corvin_home> that need to live under
# tenants/_default/ after migration. The list mirrors the survey from
# the implementation plan: every state-store and audit-chain consumer
# touches one of these subdirs.
_SUBDIRS_TO_MIGRATE = (
    "global",
    "sessions",
    "forge",
    "skill-forge",
    "voice",
    "cowork",
)

DEFAULT_TENANT_ID = "_default"
_MARKER_FILENAME = ".tenant-migrated"


def count_existing_tenants(corvin_home_path: Path) -> int:
    """Return the number of tenant directories that currently exist on disk.

    Used by the future tenant-create REST endpoint to enforce ``tenants_max``
    (ADR-0144 F-05).  The caller MUST check
    ``assert_limit("tenants_max", count_existing_tenants(...) + 1)``
    before calling any mkdir — see ADR-0144 §F-05 for the enforcement contract.
    """
    tenants_root = Path(corvin_home_path) / "tenants"
    if not tenants_root.is_dir():
        return 0
    return sum(
        1 for p in tenants_root.iterdir()
        if p.is_dir() and not p.name.startswith(".")
    )


def _emit_pre_migration_audit(
    audit_path: Path,
    to_migrate: list[tuple[str, Path, Path]],
) -> None:
    """Write one ``tenant.path_migrated`` event before any rename.

    Best-effort; failures are swallowed because audit observability
    must not block the migration itself.
    """
    try:
        # Lazy import — security_events lives in the same package.
        from .security_events import write_event

        write_event(
            audit_path,
            event_type="tenant.path_migrated",
            severity="INFO",
            details={
                "tenant_id": DEFAULT_TENANT_ID,
                "method": "rename+symlink",
                "subdirs": [s[0] for s in to_migrate],
                "subdir_count": len(to_migrate),
            },
        )
    except Exception:  # noqa: BLE001
        logger.debug("tenant_migrate: audit emit failed (best-effort)")


def migrate_to_default_tenant_if_needed(
    *,
    corvin_home_path: Path,
    audit_path: Path | None = None,
    dry_run: bool = False,
    force: bool = False,
) -> dict[str, Any]:
    """Perform the Phase 1.4 migration on the given corvin_home tree.

    Parameters
    ----------
    corvin_home_path
        The directory currently used as ``<corvin_home>``. The helper
        operates exclusively under this root; nothing outside is touched.
    audit_path
        Where to write the pre-migration audit event. Default:
        ``<corvin_home>/global/forge/audit.jsonl`` (the chain that's
        about to move). Tests pass an explicit sandbox path.
    dry_run
        When True, no filesystem changes happen; the returned dict
        describes what *would* be migrated.
    force
        When True, ignore the ``.tenant-migrated`` marker and the
        ``CORVIN_TENANT_MIGRATE=0`` opt-out. Used by tests; not
        intended for production callers.

    Returns
    -------
    dict
        Status report. Keys:
          * ``status``: one of ``ok`` / ``noop`` / ``skipped`` / ``would-migrate``
          * ``moved``: list of subdir names migrated (status=ok)
          * ``reason``: short string when status=skipped
          * ``subdirs``: list of subdir names that would be migrated
            (dry_run only)
    """
    home = Path(corvin_home_path)

    # Opt-out gate
    if not force and os.environ.get("CORVIN_TENANT_MIGRATE", "1") == "0":
        return {"status": "skipped", "reason": "CORVIN_TENANT_MIGRATE=0"}

    marker = home / _MARKER_FILENAME
    if not force and marker.exists():
        return {"status": "skipped", "reason": "already-migrated"}

    if not home.exists():
        return {"status": "skipped", "reason": "no-corvin-home"}

    tenants_dir = home / "tenants" / DEFAULT_TENANT_ID

    # Survey what actually needs migrating.
    to_migrate: list[tuple[str, Path, Path]] = []
    for sub in _SUBDIRS_TO_MIGRATE:
        legacy = home / sub
        if not legacy.exists():
            continue
        if legacy.is_symlink():
            # Already migrated in a previous run; skip silently.
            continue
        target = tenants_dir / sub
        if target.exists():
            # Target already populated; can't move on top of it. Either
            # a partial migration or operator-side reorg. Skip with a
            # warning rather than overwriting.
            logger.warning(
                "tenant_migrate: %s already exists at target, skipping %s",
                target,
                legacy,
            )
            continue
        to_migrate.append((sub, legacy, target))

    if not to_migrate:
        # Fresh install or no-op migration. Write the marker so we
        # don't re-survey on every boot.
        if dry_run:
            return {"status": "noop", "subdirs": []}
        try:
            tenants_dir.mkdir(parents=True, exist_ok=True)
            marker.write_text(
                f"migrated_at={time.time():.6f}\nstatus=noop\nsubdirs=\n",
                encoding="utf-8",
            )
        except OSError as exc:
            logger.warning("tenant_migrate: marker write failed: %s", exc)
        return {"status": "noop", "moved": []}

    if dry_run:
        return {
            "status": "would-migrate",
            "subdirs": [s[0] for s in to_migrate],
        }

    # Audit BEFORE the move (chain still at legacy path).
    chain_path = audit_path if audit_path is not None else home / "global" / "forge" / "audit.jsonl"
    _emit_pre_migration_audit(chain_path, to_migrate)

    # Ensure target parent exists.
    try:
        tenants_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return {"status": "failed", "reason": f"mkdir failed: {exc}"}

    moved: list[str] = []
    for sub, legacy, target in to_migrate:
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            os.rename(str(legacy), str(target))
            # Relative symlink so the tree stays portable across
            # operator-driven corvin_home moves.
            relative = Path("tenants") / DEFAULT_TENANT_ID / sub
            os.symlink(str(relative), str(legacy))
            moved.append(sub)
        except OSError as exc:
            logger.error(
                "tenant_migrate: failed to migrate %s -> %s: %s", legacy, target, exc
            )
            # Continue with the rest; partial migration is still useful
            # and the marker captures what landed.
            continue

    try:
        marker.write_text(
            f"migrated_at={time.time():.6f}\nstatus=ok\nsubdirs={','.join(moved)}\n",
            encoding="utf-8",
        )
    except OSError as exc:
        logger.warning("tenant_migrate: marker write failed: %s", exc)

    return {"status": "ok", "moved": moved}
