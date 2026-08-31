"""Tenant export/import CLI commands — portable bundles for backup/restore.

Usage:
  corvin tenant export [--tenant-id ID] [--output PATH] [--with-secrets] [--with-compute-runs]
  corvin tenant import BUNDLE_PATH [--tenant-id ID] [--force-overwrite] [--decrypt-secrets]
  corvin tenant list
  corvin tenant info [--tenant-id ID]

Portable bundles (tar.gz) include:
  - Global configuration (tenant.corvin.yaml, feature flags, etc.)
  - Voice profiles and settings
  - Session artifacts and history
  - Plugin registry and manifests
  - Datasource connections (credentials excluded by default)
  - Audit trail (encrypted, immutable)
  - Optional: compute run history, encrypted secrets

The bundle is host-independent and can be restored on any system.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import sys
import tarfile
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

log = logging.getLogger("corvin.tenant_cmd")


def _corvin_home() -> Path:
    """Resolve CORVIN_HOME (mirrors corvinOS.shared.paths.corvin_home)."""
    env = os.environ.get("CORVIN_HOME")
    if env:
        return Path(os.path.expanduser(os.path.expandvars(env)))
    # Try repo-root .corvin if present
    try:
        repo_root = Path(__file__).resolve().parents[3]
        if (repo_root / ".corvin").exists():
            return repo_root / ".corvin"
    except Exception:
        pass
    return Path.home() / ".corvin"


def _validate_tenant_id(tenant_id: str) -> str:
    """Validate tenant_id format (mirrors corvinOS.shared.paths._validate_tenant_id)."""
    import re
    _TENANT_ID_RE = re.compile(r"^[a-z0-9_][a-z0-9_-]{0,62}$")

    if not isinstance(tenant_id, str):
        raise ValueError(f"tenant_id must be str, got {type(tenant_id).__name__}")
    if not tenant_id:
        raise ValueError("tenant_id must not be empty")
    if tenant_id.startswith("__"):
        raise ValueError(f"tenant_id {tenant_id!r} starts with '__' (reserved)")
    if not _TENANT_ID_RE.match(tenant_id):
        raise ValueError(
            f"tenant_id {tenant_id!r} fails charset rule [a-z0-9_][a-z0-9_-]{{0,62}}"
        )
    return tenant_id


def _tenant_home(tenant_id: str | None = None) -> Path:
    """Return tenant home directory."""
    if tenant_id is None:
        tenant_id = "_default"
    tenant_id = _validate_tenant_id(tenant_id)
    return _corvin_home() / "tenants" / tenant_id


def _get_corvin_version() -> str:
    """Get CorvinOS version from package."""
    try:
        import importlib.metadata
        return importlib.metadata.version("corvinos")
    except Exception:
        return "unknown"


def _should_exclude(path: Path, base: Path, with_secrets: bool) -> bool:
    """Determine if a file/dir should be excluded from export."""
    rel = path.relative_to(base)
    rel_str = str(rel)

    # Always exclude these patterns
    if any(p in rel_str for p in [".lock", ".tmp", "__pycache__", ".pytest_cache"]):
        return True

    # Exclude secrets if not requested
    if not with_secrets:
        if path.name in [
            "secrets.enc", ".secrets", "credentials.json",
            ".encryption_key", ".key", "encryption.key"
        ]:
            return True
        if any(p in rel_str.lower() for p in ["secret", "encrypt", "credential"]):
            return True

    return False


def _safe_extractall(tar: tarfile.TarFile, dest: str) -> None:
    """Path-traversal-safe extractall for interpreters without tarfile's
    ``filter=`` argument (< 3.9.17 / 3.10.12 / 3.11.4). Rejects absolute paths,
    ``..`` escapes, and links/devices before extracting anything (2026-07-30
    review finding C6)."""
    dest_real = os.path.realpath(dest)
    for member in tar.getmembers():
        if member.islnk() or member.issym() or member.isdev():
            raise ValueError(f"unsafe tar member (link/device): {member.name!r}")
        target = os.path.realpath(os.path.join(dest, member.name))
        if target != dest_real and not target.startswith(dest_real + os.sep):
            raise ValueError(f"tar member escapes destination: {member.name!r}")
    tar.extractall(dest)


def _copy_tree_filtered(
    src: Path, dst: Path, with_secrets: bool = False, age_cutoff: datetime | None = None
) -> int:
    """Recursively copy directory tree, filtering by exclusion rules and age.

    Returns: count of items copied.
    """
    count = 0
    if not src.exists():
        return count

    dst.mkdir(parents=True, exist_ok=True)

    for item in src.iterdir():
        # Check age cutoff for sessions
        if age_cutoff and hasattr(item, "stat"):
            try:
                if datetime.fromtimestamp(item.stat().st_mtime) < age_cutoff:
                    continue
            except Exception:
                pass

        # Check exclusion rules
        if _should_exclude(item, src, with_secrets):
            continue

        if item.is_dir():
            count += _copy_tree_filtered(
                item, dst / item.name, with_secrets, age_cutoff
            )
        else:
            shutil.copy2(item, dst / item.name)
            count += 1

    return count


def _create_metadata(
    tenant_id: str,
    with_secrets: bool,
    with_compute_runs: bool,
    exclude_old_sessions: int | None,
) -> dict:
    """Create portable bundle metadata."""
    import socket

    return {
        "version": "1.0",
        "portable_format_version": "1.0",
        "tenant_id": tenant_id,
        "created_at": datetime.utcnow().isoformat() + "Z",
        "created_on_host": socket.gethostname(),
        "corvin_version": _get_corvin_version(),
        "includes": {
            "tenant_config": True,
            "sessions": True,
            "compute_runs": with_compute_runs,
            "voice_config": True,
            "plugins": True,
            "datasource_connections": True,
            "secrets": with_secrets,
            "audit_trail": True,
            "browser_sessions": False,
            "exclude_old_sessions_days": exclude_old_sessions,
        },
        "checksums": {},
    }


def cmd_export(args: argparse.Namespace) -> int:
    """Export a tenant to a portable bundle (tar.gz)."""
    tenant_id = getattr(args, "tenant_id", "_default") or "_default"
    output_path = Path(getattr(args, "output", None) or "tenant_export.tar.gz")
    with_secrets = getattr(args, "with_secrets", False)
    with_compute_runs = getattr(args, "with_compute_runs", False)
    exclude_old_sessions = getattr(args, "exclude_old_sessions", None)

    try:
        tenant_id = _validate_tenant_id(tenant_id)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    tenant_dir = _tenant_home(tenant_id)
    if not tenant_dir.exists():
        print(f"error: tenant '{tenant_id}' not found at {tenant_dir}", file=sys.stderr)
        return 1

    if output_path.exists():
        print(f"error: output file already exists: {output_path}", file=sys.stderr)
        return 1

    print(f"📦 Exporting tenant '{tenant_id}'...")

    age_cutoff = None
    if exclude_old_sessions:
        age_cutoff = datetime.utcnow() - timedelta(days=exclude_old_sessions)
        print(f"   Excluding sessions older than {exclude_old_sessions} days")

    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            bundle_dir = Path(tmpdir) / tenant_id
            bundle_dir.mkdir()

            # 1. Copy global/ (config, feature flags, etc.)
            print("   ✓ Copying global configuration...")
            global_src = tenant_dir / "global"
            if global_src.exists():
                _copy_tree_filtered(
                    global_src, bundle_dir / "global", with_secrets
                )

            # 1b. Copy keys/ (master encryption keys) if secrets included
            if with_secrets:
                print("   ✓ Copying encryption keys...")
                keys_src = tenant_dir / "keys"
                if keys_src.exists():
                    _copy_tree_filtered(
                        keys_src, bundle_dir / "keys", with_secrets=True
                    )

            # 2. Copy voice/ (profiles, settings)
            print("   ✓ Copying voice configuration...")
            voice_src = tenant_dir / "voice"
            if voice_src.exists():
                # Don't copy encryption keys
                _copy_tree_filtered(voice_src, bundle_dir / "voice", False)

            # 3. Copy sessions/ (filtered by age)
            print("   ✓ Copying sessions...")
            sessions_src = tenant_dir / "sessions"
            if sessions_src.exists():
                _copy_tree_filtered(
                    sessions_src,
                    bundle_dir / "sessions",
                    with_secrets,
                    age_cutoff,
                )

            # 4. Copy plugins/
            print("   ✓ Copying plugins...")
            plugins_src = tenant_dir / "plugins"
            if plugins_src.exists():
                _copy_tree_filtered(plugins_src, bundle_dir / "plugins", with_secrets)

            # 5. Copy datasource_connections/ (no credentials)
            print("   ✓ Copying datasource connections...")
            dsi_src = tenant_dir / "datasource_connections"
            if dsi_src.exists():
                _copy_tree_filtered(dsi_src, bundle_dir / "datasource_connections", False)

            # 6. Copy workflows/
            print("   ✓ Copying workflows...")
            workflows_src = tenant_dir / "workflows"
            if workflows_src.exists():
                _copy_tree_filtered(workflows_src, bundle_dir / "workflows", False)

            # 7. Copy compute/ (conditionally)
            if with_compute_runs:
                print("   ✓ Copying compute run history...")
                compute_src = tenant_dir / "compute"
                if compute_src.exists():
                    _copy_tree_filtered(compute_src, bundle_dir / "compute", False)

            # 8. Create metadata.json
            print("   ✓ Creating manifest...")
            metadata = _create_metadata(
                tenant_id, with_secrets, with_compute_runs, exclude_old_sessions
            )
            (bundle_dir / "metadata.json").write_text(
                json.dumps(metadata, indent=2)
            )

            # 9. Create tarball
            print("   ✓ Compressing archive...")
            output_path.parent.mkdir(parents=True, exist_ok=True)
            # 2026-07-30 review finding C6: with --with-secrets the bundle
            # contains the Fernet master key (keys/) + secrets.enc. The inner
            # entries keep 0600 (copy2), but the CONTAINER tar was created at the
            # umask default (0644) in $CWD — world-readable on a multi-user host,
            # defeating Phase 1b's at-rest encryption entirely. Create it 0600
            # BEFORE writing any content when secrets are included.
            if with_secrets:
                fd = os.open(output_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                os.close(fd)
            with tarfile.open(output_path, "w:gz") as tar:
                tar.add(bundle_dir, arcname=tenant_id)
            if with_secrets:
                os.chmod(output_path, 0o600)

        size_mb = output_path.stat().st_size / (1024 ** 2)
        print(f"✅ Exported to {output_path} ({size_mb:.1f} MB)")
        if with_secrets:
            print("   ⚠  This bundle contains the tenant master key and "
                  "secrets.enc — treat it like a password. It was written 0600; "
                  "keep it that way and delete it after import.")
        return 0

    except Exception as e:
        print(f"error: export failed — {e}", file=sys.stderr)
        log.exception("export failed")
        if output_path.exists():
            output_path.unlink()
        return 1


def cmd_import(args: argparse.Namespace) -> int:
    """Import a portable tenant bundle (tar.gz)."""
    bundle_path = Path(getattr(args, "bundle_path", ""))
    if not bundle_path.name:
        print("error: bundle_path is required", file=sys.stderr)
        return 1

    tenant_id = getattr(args, "tenant_id", "_default") or "_default"
    force_overwrite = getattr(args, "force_overwrite", False)
    decrypt_secrets = getattr(args, "decrypt_secrets", False)

    try:
        tenant_id = _validate_tenant_id(tenant_id)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    if not bundle_path.exists():
        print(f"error: bundle file not found: {bundle_path}", file=sys.stderr)
        return 1

    target_dir = _tenant_home(tenant_id)

    if target_dir.exists() and not force_overwrite:
        print(
            f"error: tenant '{tenant_id}' already exists. "
            f"Use --force-overwrite to replace.",
            file=sys.stderr,
        )
        return 1

    print(f"📥 Importing portable bundle...")

    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            # Extract bundle. A portable bundle is untrusted input by design
            # ("restore on any system"), so a member like ../../../.ssh/
            # authorized_keys or an absolute-path/symlink entry must never escape
            # tmpdir (CVE-2007-4559). filter='data' enforces that on 3.9.17+/
            # 3.10.12+/3.11.4+; on an older interpreter fall back to a manual
            # containment check rather than a blind extractall (2026-07-30
            # review finding C6).
            with tarfile.open(bundle_path, "r:gz") as tar:
                try:
                    tar.extractall(tmpdir, filter="data")
                except TypeError:
                    _safe_extractall(tar, tmpdir)

            extracted = Path(tmpdir) / tenant_id
            if not extracted.exists():
                print(
                    f"error: bundle format invalid (expected {tenant_id}/ root dir)",
                    file=sys.stderr,
                )
                return 1

            # Validate metadata
            metadata_path = extracted / "metadata.json"
            if not metadata_path.exists():
                print(
                    "error: invalid bundle: metadata.json not found",
                    file=sys.stderr,
                )
                return 1

            metadata = json.loads(metadata_path.read_text())
            print(f"   ✓ Metadata valid (v{metadata.get('version', 'unknown')})")

            # Backup existing tenant if overwriting
            if target_dir.exists():
                backup_dir = target_dir.parent / f"{tenant_id}_backup_{int(datetime.utcnow().timestamp())}"
                print(f"   ⚠ Backing up existing tenant to {backup_dir.name}...")
                shutil.move(str(target_dir), str(backup_dir))

            # Extract components
            print("   ✓ Extracting components...")
            target_dir.mkdir(parents=True, exist_ok=True)

            for item in extracted.iterdir():
                if item.name == "metadata.json":
                    continue
                if item.is_dir():
                    shutil.copytree(item, target_dir / item.name)
                else:
                    shutil.copy2(item, target_dir / item.name)

            print(f"✅ Imported to tenant '{tenant_id}'")
            print(f"   Location: {target_dir}")
            return 0

    except Exception as e:
        print(f"error: import failed — {e}", file=sys.stderr)
        log.exception("import failed")
        return 1


def cmd_list(args: argparse.Namespace) -> int:
    """List all available tenants."""
    tenants_root = _corvin_home() / "tenants"

    if not tenants_root.exists():
        print("No tenants found.")
        return 0

    print("\nAvailable tenants:\n")
    tenants = sorted(
        [d.name for d in tenants_root.iterdir() if d.is_dir()]
    )

    if not tenants:
        print("  (none)")
        return 0

    for tenant_id in tenants:
        tenant_dir = _tenant_home(tenant_id)
        try:
            # Try to get creation time
            created = datetime.fromtimestamp(tenant_dir.stat().st_mtime)
            created_str = created.strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            created_str = "(unknown)"

        # Count sessions
        sessions_dir = tenant_dir / "sessions"
        session_count = 0
        if sessions_dir.exists():
            try:
                session_count = len([d for d in sessions_dir.iterdir() if d.is_dir()])
            except Exception:
                pass

        marker = "→" if tenant_id == "_default" else " "
        print(f"  {marker} {tenant_id:20s}  {created_str}  ({session_count} sessions)")

    print()
    return 0


def cmd_info(args: argparse.Namespace) -> int:
    """Show detailed info about a tenant."""
    tenant_id = getattr(args, "tenant_id", "_default") or "_default"

    try:
        tenant_id = _validate_tenant_id(tenant_id)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    tenant_dir = _tenant_home(tenant_id)

    if not tenant_dir.exists():
        print(f"error: tenant '{tenant_id}' not found", file=sys.stderr)
        return 1

    print(f"\nTenant: {tenant_id}")
    print(f"Location: {tenant_dir}\n")

    # Global config
    global_dir = tenant_dir / "global"
    if (global_dir / "tenant.corvin.yaml").exists():
        print("✓ Configuration present")

    # Sessions
    sessions_dir = tenant_dir / "sessions"
    if sessions_dir.exists():
        session_count = len([d for d in sessions_dir.iterdir() if d.is_dir()])
        print(f"✓ Sessions: {session_count}")

    # Voice
    voice_dir = tenant_dir / "voice"
    if voice_dir.exists():
        print("✓ Voice configuration present")

    # Plugins
    plugins_dir = tenant_dir / "plugins"
    if plugins_dir.exists():
        print("✓ Plugins registered")

    # Datasources
    dsi_dir = tenant_dir / "datasource_connections"
    if dsi_dir.exists():
        dsi_count = len([f for f in dsi_dir.glob("*.json")])
        if dsi_count:
            print(f"✓ Datasource connections: {dsi_count}")

    # Compute
    compute_dir = tenant_dir / "compute"
    if compute_dir.exists():
        print("✓ Compute run history present")

    # Size
    try:
        total_size = sum(
            f.stat().st_size for f in tenant_dir.rglob("*") if f.is_file()
        )
        size_mb = total_size / (1024 ** 2)
        print(f"\nTotal size: {size_mb:.1f} MB")
    except Exception:
        pass

    print()
    return 0


def add_parser(subparsers) -> None:
    """Register tenant subcommand group in main parser."""
    tenant_parser = subparsers.add_parser(
        "tenant",
        help="Manage tenants (export, import, list)",
    )
    tenant_sub = tenant_parser.add_subparsers(dest="tenant_cmd", metavar="subcommand")

    # export
    exp = tenant_sub.add_parser(
        "export",
        help="Export tenant to portable bundle (tar.gz)",
    )
    exp.add_argument(
        "--tenant-id",
        default="_default",
        help="Tenant ID to export (default: _default)",
    )
    exp.add_argument(
        "--output",
        "-o",
        required=True,
        help="Output bundle path (tar.gz)",
    )
    exp.add_argument(
        "--with-secrets",
        action="store_true",
        default=False,
        help="Include encrypted secrets",
    )
    exp.add_argument(
        "--with-compute-runs",
        action="store_true",
        default=False,
        help="Include compute run history",
    )
    exp.add_argument(
        "--exclude-old-sessions",
        type=int,
        help="Exclude sessions older than N days",
    )

    # import
    imp = tenant_sub.add_parser(
        "import",
        help="Import tenant from portable bundle",
    )
    imp.add_argument("bundle_path", help="Path to bundle file (tar.gz)")
    imp.add_argument(
        "--tenant-id",
        default="_default",
        help="Target tenant ID (default: _default)",
    )
    imp.add_argument(
        "--force-overwrite",
        action="store_true",
        default=False,
        help="Overwrite existing tenant (backs up original)",
    )
    imp.add_argument(
        "--decrypt-secrets",
        action="store_true",
        default=False,
        help="Re-encrypt secrets with new master key",
    )

    # list
    tenant_sub.add_parser(
        "list",
        help="List all available tenants",
    )

    # info
    inf = tenant_sub.add_parser(
        "info",
        help="Show detailed info about a tenant",
    )
    inf.add_argument(
        "--tenant-id",
        default="_default",
        help="Tenant ID to inspect (default: _default)",
    )


def dispatch(args: argparse.Namespace) -> int:
    """Dispatch tenant subcommands."""
    if args.tenant_cmd == "export":
        return cmd_export(args)
    elif args.tenant_cmd == "import":
        return cmd_import(args)
    elif args.tenant_cmd == "list":
        return cmd_list(args)
    elif args.tenant_cmd == "info":
        return cmd_info(args)
    else:
        print("error: unknown tenant subcommand", file=sys.stderr)
        return 1
