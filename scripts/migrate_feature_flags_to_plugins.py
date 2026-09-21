#!/usr/bin/env python3
"""Migration script: Feature flags → plugin system (ADR-0903).

Converts old tenant.corvin.yaml::spec.features_whitelist to new
tenant.corvin.yaml::plugins structure.

One-time migration per tenant. Safe to run multiple times (idempotent).

Usage:
    python3 scripts/migrate_feature_flags_to_plugins.py --tenant=_default [--dry-run]
"""
import argparse
import json
import logging
from pathlib import Path
from typing import Any

import yaml

# Feature flag → Plugin ID mapping (definitive)
FLAG_TO_PLUGIN = {
    "vibe_engineering": "ai.vibe_engineering",
    "vibe_engineering_active": "ai.vibe_engineering",  # becomes config
    "tree_of_thoughts": "reasoning.tree_of_thoughts",
    "learning_objectives": "learning.user_objectives",
    "token_metrics": "observability.token_metrics",
    "outcome_feedback_loop": "learning.feedback_loop",
    "cross_device_sync": "session.device_sync",
}

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)


def get_tenant_config_path(tenant_id: str) -> Path:
    """Get path to tenant.corvin.yaml."""
    home = Path.home()
    return home / ".corvin" / "tenants" / tenant_id / "global" / "tenant.corvin.yaml"


def read_tenant_config(tenant_id: str) -> dict[str, Any]:
    """Read tenant config, handle missing file."""
    path = get_tenant_config_path(tenant_id)
    if not path.exists():
        logger.warning(f"Tenant config not found: {path}")
        return {}

    try:
        content = yaml.safe_load(path.read_text("utf-8"))
        return content if isinstance(content, dict) else {}
    except Exception as e:
        logger.error(f"Failed to read config: {e}")
        return {}


def write_tenant_config(tenant_id: str, config: dict[str, Any]) -> None:
    """Write tenant config back."""
    path = get_tenant_config_path(tenant_id)
    path.parent.mkdir(parents=True, exist_ok=True)

    # Use safe_dump with default_flow_style=False for readability
    content = yaml.dump(config, default_flow_style=False, sort_keys=False)
    path.write_text(content, "utf-8")
    logger.info(f"Wrote config: {path}")


def migrate_tenant(tenant_id: str, dry_run: bool = False) -> dict[str, bool]:
    """Migrate feature flags to plugins for a tenant.

    Returns: dict[plugin_id] -> was_enabled (for audit logging)
    """
    config = read_tenant_config(tenant_id)
    spec = config.get("spec", {})
    features_whitelist = spec.get("features_whitelist", [])

    if not features_whitelist:
        logger.info(f"No feature flags to migrate (tenant={tenant_id})")
        return {}

    logger.info(f"Migrating {len(features_whitelist)} features (tenant={tenant_id})")

    # Build plugin states from old flags
    plugin_states = {}
    migration_log = {}

    for flag_id in features_whitelist:
        plugin_id = FLAG_TO_PLUGIN.get(flag_id)
        if not plugin_id:
            logger.warning(f"Unknown flag: {flag_id}, skipping")
            continue

        # Map flag to plugin enable state
        enabled = flag_id in features_whitelist

        if plugin_id not in plugin_states:
            plugin_states[plugin_id] = {"enabled": enabled}

        migration_log[flag_id] = {
            "plugin_id": plugin_id,
            "was_enabled": enabled,
            "migrated_at": "2026-09-20"  # Placeholder
        }

    if dry_run:
        logger.info(f"DRY-RUN: Would migrate {len(plugin_states)} plugins")
        for plugin_id, state in plugin_states.items():
            logger.info(f"  {plugin_id}: enabled={state['enabled']}")
        return migration_log

    # Apply migration
    config["plugins"] = plugin_states

    # Remove old key
    if "spec" in config and "features_whitelist" in config["spec"]:
        del config["spec"]["features_whitelist"]

    write_tenant_config(tenant_id, config)

    logger.info(f"✅ Migrated {len(plugin_states)} plugins")

    # Log migration for audit trail
    _emit_audit_events(tenant_id, migration_log)

    return migration_log


def _emit_audit_events(tenant_id: str, migration_log: dict[str, Any]) -> None:
    """Emit audit events for the migration."""
    for flag_id, entry in migration_log.items():
        # Placeholder: real implementation would emit to audit chain
        logger.info(
            f"Audit: flag_migrated flag_id={flag_id} "
            f"plugin_id={entry['plugin_id']} "
            f"was_enabled={entry['was_enabled']}"
        )


def main():
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Migrate feature flags to plugins (ADR-0903)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Dry-run (show what would happen)
  python3 scripts/migrate_feature_flags_to_plugins.py --tenant=_default --dry-run

  # Actual migration
  python3 scripts/migrate_feature_flags_to_plugins.py --tenant=_default
        """
    )

    parser.add_argument(
        "--tenant",
        default="_default",
        help="Tenant ID (default: _default)"
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be migrated without making changes"
    )

    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Verbose output"
    )

    args = parser.parse_args()

    if args.verbose:
        logger.setLevel(logging.DEBUG)

    # Run migration
    result = migrate_tenant(args.tenant, dry_run=args.dry_run)

    if args.dry_run:
        logger.info(f"DRY-RUN COMPLETE: {len(result)} flags would be migrated")
    else:
        logger.info(f"✅ MIGRATION COMPLETE: {len(result)} flags migrated")

    return 0 if result else 1


if __name__ == "__main__":
    exit(main())
