#!/usr/bin/env python3
"""audit_rotate.py — daily L37 rotation + sealing + retention driver.

Invoked by `corvin-audit-rotate.service` (and the matching `.timer`).
Walks every tenant under ``<corvin_home>/tenants/``, loads its
``tenant.corvin.yaml::spec.audit`` block via
:func:`audit_sealer.policy_from_tenant_config`, and:

  1. Checks ``should_rotate()`` on the live audit segment.
  2. If due, calls ``rotate_and_seal()`` — which writes an
     ``audit.rotation_link`` event into the fresh live file, seals
     the rotated segment if encryption is enabled, and emits
     ``audit.segment_sealed`` (or ``audit.rotation_failed``) into
     the new live chain.
  3. Calls ``enforce_retention()`` to remove sealed segments older
     than ``retention_years`` — audit-first via
     ``audit.segment_retired``.

Exit codes:

  0  every tenant processed without error (rotation may or may not
     have occurred; either is fine)
  1  at least one tenant raised during rotation or retention
  2  fatal infrastructure problem (PyYAML missing, audit_sealer
     module unimportable)

Logs go to the systemd journal via stdout/stderr — each tenant gets
one summary line.

Hand-invocation::

    python3 operator/voice/scripts/audit_rotate.py
    python3 operator/voice/scripts/audit_rotate.py --tenant my_tenant
    python3 operator/voice/scripts/audit_rotate.py --dry-run
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "operator" / "bridges" / "shared"))


def _corvin_home() -> Path:
    env = os.environ.get("CORVIN_HOME") or os.environ.get("CORVIN_HOME")
    if env:
        return Path(os.path.expanduser(os.path.expandvars(env)))
    new = Path.home() / ".corvin"
    legacy = Path.home() / ".corvinOS"
    if new.is_dir():
        return new
    if legacy.is_dir():
        return legacy
    return new


def _list_tenants(root: Path) -> list[str]:
    tenants_dir = root / "tenants"
    if not tenants_dir.is_dir():
        # Legacy single-tenant layout — pretend default exists at the root.
        return ["_default"] if (root / "global").is_dir() else []
    out = []
    for p in tenants_dir.iterdir():
        if p.is_dir():
            out.append(p.name)
    return sorted(out)


def _tenant_paths(root: Path, tenant_id: str) -> tuple[Path, Path]:
    """Return (audit_path, tenant_yaml_path) for a tenant."""
    if (root / "tenants" / tenant_id).is_dir():
        base = root / "tenants" / tenant_id / "global"
    else:
        base = root / "global"
    audit_path = base / "forge" / "audit.jsonl"
    tenant_yaml = base / "tenant.corvin.yaml"
    return audit_path, tenant_yaml


def _load_yaml(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        import yaml  # type: ignore
    except ImportError:
        return None
    try:
        return yaml.safe_load(path.read_text("utf-8"))
    except Exception:  # noqa: BLE001
        return None


def process_tenant(tenant_id: str, root: Path, *, dry_run: bool) -> int:
    """Process one tenant. Returns 0 on success, 1 on per-tenant failure.
    Never raises."""
    audit_path, tenant_yaml = _tenant_paths(root, tenant_id)
    if not audit_path.exists():
        print(f"[{tenant_id}] no audit file at {audit_path} — skip")
        return 0

    cfg = _load_yaml(tenant_yaml)

    try:
        from audit_sealer import (  # type: ignore
            enforce_retention,
            make_forge_audit_writer,
            policy_from_tenant_config,
            rotate_and_seal,
            should_rotate,
        )
    except ImportError as e:
        print(f"[{tenant_id}] FATAL: audit_sealer unimportable: {e}",
              file=sys.stderr)
        return 2

    try:
        policy = policy_from_tenant_config(cfg)
    except ValueError as e:
        print(f"[{tenant_id}] config error: {e}", file=sys.stderr)
        return 1

    audit_writer = make_forge_audit_writer(audit_path)

    # 1. Rotation gate
    decision = should_rotate(audit_path, policy.rotation)
    if decision.should:
        if dry_run:
            print(f"[{tenant_id}] DRY-RUN would rotate "
                  f"(size={decision.size_mb:.1f}MB, age={decision.age_days:.1f}d, "
                  f"reason={decision.reason})")
        else:
            try:
                result = rotate_and_seal(audit_path, policy,
                                         audit_writer=audit_writer)
                if result.sealed_path:
                    print(f"[{tenant_id}] rotated + sealed → {result.sealed_path.name}")
                elif result.rotated_path:
                    print(f"[{tenant_id}] rotated (plaintext) → {result.rotated_path.name}")
                else:
                    print(f"[{tenant_id}] {result.reason}")
            except RuntimeError as e:
                print(f"[{tenant_id}] rotation failed: {e}", file=sys.stderr)
                return 1
    else:
        print(f"[{tenant_id}] under thresholds "
              f"(size={decision.size_mb:.1f}MB, age={decision.age_days:.1f}d)")

    # 2. Retention enforcement
    if dry_run:
        print(f"[{tenant_id}] DRY-RUN skipping retention sweep")
    else:
        try:
            removed = enforce_retention(audit_path.parent, policy.retention,
                                        audit_writer=audit_writer)
            if removed:
                print(f"[{tenant_id}] retention removed {len(removed)} segment(s)")
            else:
                print(f"[{tenant_id}] retention: no segments past {policy.retention.retention_years}y")
        except Exception as e:  # noqa: BLE001
            print(f"[{tenant_id}] retention failed: {e}", file=sys.stderr)
            return 1

    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="audit_rotate",
        description="Daily L37 audit rotation + sealing + retention",
    )
    parser.add_argument("--tenant", default=None,
                        help="process only this tenant id (default: all)")
    parser.add_argument("--dry-run", action="store_true",
                        help="report what would happen without modifying state")
    parser.add_argument("--home", default=None,
                        help="override corvin_home (default: $CORVIN_HOME or ~/.corvin)")
    args = parser.parse_args(argv)

    # ADR-0135 M2 fix: load license so write_chain_anchor inside rotate_and_seal
    # uses the paid-tier HMAC key — the same key verify_chain_anchor uses at
    # adapter boot.  Without this, paid-tier installs get a free-tier anchor HMAC
    # on rotation and a false hmac_invalid CRITICAL at the next boot.
    # Best-effort — free-tier or missing validator falls back to instance seed.
    _lic_dir = REPO_ROOT / "operator" / "license"
    if str(_lic_dir) not in sys.path:
        sys.path.insert(0, str(_lic_dir))
    try:
        import validator as _lic_v  # type: ignore[import-not-found]
        if not _lic_v.is_loaded():
            _lic_v.load_license_from_env()
    except Exception:  # noqa: BLE001
        pass  # free-tier or validator missing — anchor uses instance seed

    root = Path(args.home).expanduser() if args.home else _corvin_home()
    if not root.is_dir():
        print(f"FATAL: corvin_home not found at {root}", file=sys.stderr)
        return 2

    tenants = [args.tenant] if args.tenant else _list_tenants(root)
    if not tenants:
        print(f"no tenants found under {root}/tenants — nothing to do")
        return 0

    print(f"audit_rotate: home={root}  tenants={len(tenants)}  ts={int(time.time())}")
    rc = 0
    for tid in tenants:
        try:
            rc |= process_tenant(tid, root, dry_run=args.dry_run)
        except Exception as e:  # noqa: BLE001
            print(f"[{tid}] unexpected error: {e}", file=sys.stderr)
            rc |= 1
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
