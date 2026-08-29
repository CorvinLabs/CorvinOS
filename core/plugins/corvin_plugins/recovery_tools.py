"""Registry recovery and verification tools (ADR-0250 Part 2).

Provides CLI-friendly utilities for operators to:
- List available backups
- Verify registry and backup integrity
- Restore from specific backups
- Inspect backup contents
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Optional

import yaml

from .state import (
    BackupManager,
    RegistryCorrupt,
    TenantRegistry,
    registry_backups_dir,
    registry_path,
)

log = logging.getLogger("corvin.plugins.recovery")


class RegistryRecoveryTools:
    """Tools for registry recovery and verification."""

    def __init__(self, *, tenant_id: Optional[str] = None, corvin_home_path: Optional[Path] = None):
        self.tenant_id = tenant_id or "_default"
        self.corvin_home_path = corvin_home_path
        self.registry_path = registry_path(
            tenant_id=self.tenant_id, corvin_home_path=self.corvin_home_path
        )
        self.backup_mgr = BackupManager(self.registry_path)

    def list_backups(self) -> list[dict]:
        """List all available backups with metadata.

        Returns:
            List of dicts with keys: name, path, size, mtime, is_valid
        """
        backups_info = []
        for backup_path in self.backup_mgr.list_backups():
            try:
                stat = backup_path.stat()
                content = backup_path.read_text()
                yaml.safe_load(content)  # Validate
                is_valid = True
            except Exception:
                is_valid = False

            backups_info.append({
                "name": backup_path.name,
                "path": str(backup_path),
                "size_bytes": stat.st_size if stat else 0,
                "mtime": stat.st_mtime if stat else 0,
                "is_valid": is_valid,
            })
        return backups_info

    def verify_registry(self) -> tuple[bool, list[str]]:
        """Verify current registry integrity.

        Returns:
            (is_valid, error_messages)
        """
        if not self.registry_path.exists():
            return (True, ["Registry does not exist (no plugins installed)"])

        try:
            reg = TenantRegistry.load(
                tenant_id=self.tenant_id, corvin_home_path=self.corvin_home_path, auto_recover=False
            )
            return reg.verify_integrity()
        except RegistryCorrupt as exc:
            return (False, [str(exc)])
        except Exception as exc:
            return (False, [f"Unexpected error: {exc}"])

    def verify_backups(self) -> dict[str, bool]:
        """Verify all backups are valid YAML.

        Returns:
            Dict mapping backup name -> is_valid
        """
        return self.backup_mgr.verify_all_backups()

    def restore_backup(self, backup_name: str) -> tuple[bool, str]:
        """Restore from a specific backup.

        Args:
            backup_name: Name of backup file (e.g., "registry.2026-08-29T10:30:45.123Z.yaml")

        Returns:
            (success, message)
        """
        backups_dir = registry_backups_dir(
            tenant_id=self.tenant_id, corvin_home_path=self.corvin_home_path
        )
        backup_path = backups_dir / backup_name

        if not backup_path.exists():
            return (False, f"Backup not found: {backup_path}")

        success = self.backup_mgr.restore_from_backup(backup_path)
        if success:
            return (True, f"Restored from {backup_name}")
        else:
            return (False, f"Failed to restore from {backup_name}")

    def restore_most_recent(self) -> tuple[bool, str]:
        """Restore from the most recent valid backup.

        Returns:
            (success, message)
        """
        reg = TenantRegistry(self.registry_path)
        success = reg.restore_from_timestamped_backup()
        if success:
            return (True, "Restored from most recent backup")
        else:
            return (False, "No valid backup available for recovery")

    def inspect_backup(self, backup_name: str) -> tuple[bool, str]:
        """Show contents of a backup.

        Args:
            backup_name: Name of backup file

        Returns:
            (success, contents_or_error)
        """
        backups_dir = registry_backups_dir(
            tenant_id=self.tenant_id, corvin_home_path=self.corvin_home_path
        )
        backup_path = backups_dir / backup_name

        if not backup_path.exists():
            return (False, f"Backup not found: {backup_path}")

        try:
            content = backup_path.read_text()
            yaml.safe_load(content)  # Verify it's valid YAML
            return (True, content)
        except Exception as exc:
            return (False, f"Error reading backup: {exc}")

    def cleanup_old_backups(self, keep: int = 5) -> tuple[int, list[str]]:
        """Remove old backups, keeping the most recent N.

        Args:
            keep: Number of recent backups to keep (default 5)

        Returns:
            (number_removed, names_of_removed)
        """
        backups_dir = registry_backups_dir(
            tenant_id=self.tenant_id, corvin_home_path=self.corvin_home_path
        )

        if not backups_dir.exists():
            return (0, [])

        backups = sorted(backups_dir.glob("registry.*.yaml"))
        if len(backups) <= keep:
            return (0, [])

        removed = []
        for old_backup in backups[:-keep]:
            try:
                old_backup.unlink()
                removed.append(old_backup.name)
                log.info(f"removed old backup: {old_backup.name}")
            except Exception as exc:
                log.error(f"failed to remove {old_backup.name}: {exc}")

        return (len(removed), removed)

    def export_registry(self, output_path: Path) -> tuple[bool, str]:
        """Export current registry to a file for backup/analysis.

        Args:
            output_path: Where to write the exported registry

        Returns:
            (success, message)
        """
        if not self.registry_path.exists():
            return (False, "Registry does not exist")

        try:
            content = self.registry_path.read_text()
            output_path.write_text(content)
            return (True, f"Exported to {output_path}")
        except Exception as exc:
            return (False, f"Export failed: {exc}")

    def import_registry(self, input_path: Path, backup_first: bool = True) -> tuple[bool, str]:
        """Import registry from a file (overwrites current).

        Args:
            input_path: File to import from
            backup_first: If True, create a backup before importing

        Returns:
            (success, message)
        """
        if not input_path.exists():
            return (False, f"Input file not found: {input_path}")

        try:
            content = input_path.read_text()
            yaml.safe_load(content)  # Validate it's valid YAML
        except Exception as exc:
            return (False, f"Invalid YAML in input file: {exc}")

        # Create backup if requested
        if backup_first and self.registry_path.exists():
            self.backup_mgr.create_backup()

        try:
            self.registry_path.parent.mkdir(parents=True, exist_ok=True)
            self.registry_path.write_text(content)
            return (True, f"Imported from {input_path}")
        except Exception as exc:
            return (False, f"Import failed: {exc}")

    def print_status(self) -> None:
        """Print current registry status to stdout."""
        print(f"Tenant: {self.tenant_id}")
        print(f"Registry: {self.registry_path}")
        print()

        # Verify registry
        is_valid, errors = self.verify_registry()
        print(f"Registry Valid: {is_valid}")
        if errors:
            for err in errors:
                print(f"  - {err}")
        print()

        # List backups
        backups = self.list_backups()
        print(f"Backups Available: {len(backups)}")
        for backup in backups:
            status = "✓" if backup["is_valid"] else "✗"
            print(f"  {status} {backup['name']} ({backup['size_bytes']} bytes)")
        print()

        # Backup health
        if backups:
            valid_count = sum(1 for b in backups if b["is_valid"])
            print(f"Valid Backups: {valid_count}/{len(backups)}")
        else:
            print("No backups found")


def main():
    """CLI entry point for recovery tools."""
    import argparse

    parser = argparse.ArgumentParser(description="Plugin registry recovery tools")
    parser.add_argument("--tenant-id", default="_default", help="Tenant ID")
    parser.add_argument("--corvin-home", type=Path, help="Corvin home directory")

    subparsers = parser.add_subparsers(dest="command", help="Commands")

    # Status command
    subparsers.add_parser("status", help="Show registry status")

    # List backups command
    subparsers.add_parser("list", help="List available backups")

    # Verify command
    subparsers.add_parser("verify", help="Verify registry and backups")

    # Restore command
    restore_parser = subparsers.add_parser("restore", help="Restore from backup")
    restore_group = restore_parser.add_mutually_exclusive_group(required=True)
    restore_group.add_argument("--backup", help="Backup file name to restore from")
    restore_group.add_argument("--recent", action="store_true", help="Restore from most recent")

    # Inspect command
    inspect_parser = subparsers.add_parser("inspect", help="Show backup contents")
    inspect_parser.add_argument("backup", help="Backup file name")

    # Cleanup command
    cleanup_parser = subparsers.add_parser("cleanup", help="Remove old backups")
    cleanup_parser.add_argument("--keep", type=int, default=5, help="Keep N recent backups")

    # Export command
    export_parser = subparsers.add_parser("export", help="Export registry to file")
    export_parser.add_argument("output", type=Path, help="Output file path")

    # Import command
    import_parser = subparsers.add_parser("import", help="Import registry from file")
    import_parser.add_argument("input", type=Path, help="Input file path")
    import_parser.add_argument("--no-backup", action="store_true", help="Skip creating backup")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    tools = RegistryRecoveryTools(
        tenant_id=args.tenant_id, corvin_home_path=args.corvin_home
    )

    if args.command == "status":
        tools.print_status()

    elif args.command == "list":
        backups = tools.list_backups()
        if backups:
            print(f"Found {len(backups)} backups:")
            for backup in backups:
                status = "valid" if backup["is_valid"] else "CORRUPTED"
                print(f"  {backup['name']} ({status}, {backup['size_bytes']} bytes)")
        else:
            print("No backups found")

    elif args.command == "verify":
        is_valid, errors = tools.verify_registry()
        print("Registry:", "VALID" if is_valid else "CORRUPTED")
        if errors:
            for err in errors:
                print(f"  Error: {err}")

        backup_status = tools.verify_backups()
        print(f"Backups: {len(backup_status)} total")
        for name, valid in backup_status.items():
            print(f"  {name}: {'valid' if valid else 'corrupted'}")

    elif args.command == "restore":
        if args.backup:
            success, msg = tools.restore_backup(args.backup)
        else:
            success, msg = tools.restore_most_recent()
        print(msg)
        sys.exit(0 if success else 1)

    elif args.command == "inspect":
        success, content = tools.inspect_backup(args.backup)
        if success:
            print(content)
        else:
            print(content, file=sys.stderr)
            sys.exit(1)

    elif args.command == "cleanup":
        removed_count, removed_names = tools.cleanup_old_backups(keep=args.keep)
        if removed_count > 0:
            print(f"Removed {removed_count} old backups:")
            for name in removed_names:
                print(f"  - {name}")
        else:
            print(f"No backups to remove (keeping {args.keep}, have ≤ {args.keep})")

    elif args.command == "export":
        success, msg = tools.export_registry(args.output)
        print(msg)
        sys.exit(0 if success else 1)

    elif args.command == "import":
        success, msg = tools.import_registry(args.input, backup_first=not args.no_backup)
        print(msg)
        sys.exit(0 if success else 1)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
