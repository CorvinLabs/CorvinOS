#!/usr/bin/env python3
"""Migrate Skill manifests from v1 to v2 (Phase 2, ADR-0667).

Migrates all 34 builtin Skills from v1 manifest format to v2 with license binding.
Each manifest is signed with the operator private key.

Usage:
    python3 scripts/migrate_manifests_v1_to_v2.py [--dry-run] [--operator-key /path/to/key.pem]
"""

from __future__ import annotations

import argparse
import json
import logging
import shutil
from pathlib import Path
from typing import List

from core.skills.manifest_validator import SkillManifest as SkillManifestV1
from core.skills.manifest_v2 import SkillManifestV2, migrate_manifest_v1_to_v2, LicenseBindingMetadata
from core.skills.signature import SkillManifestSigner

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)


# Builtin Skills that require "paid" tier (router-related)
PAID_TIER_SKILLS = {
    "routing.optimized",
    "routing.adaptive",
    "routing.cost_optimizer",
}


def find_builtin_manifests(buildin_dir: Path) -> List[Path]:
    """Find all builtin manifest files.

    Args:
        buildin_dir: Path to plugins/buildin/ directory

    Returns:
        List of manifest.json file paths
    """
    manifests = list(buildin_dir.glob("*/*/manifest.json"))
    logger.info(f"Found {len(manifests)} builtin manifests")
    return manifests


def assign_tier_for_skill(skill_id: str) -> str:
    """Assign license tier based on Skill type.

    Args:
        skill_id: Skill identifier

    Returns:
        "free" or "paid"
    """
    if any(skill_id.startswith(prefix) for prefix in PAID_TIER_SKILLS):
        return "paid"
    return "free"


def migrate_manifest_file(
    manifest_path: Path,
    signer: SkillManifestSigner,
    private_key,
    operator_public_key,
    backup_dir: Optional[Path] = None,
) -> bool:
    """Migrate a single manifest file.

    Args:
        manifest_path: Path to manifest.json
        signer: SkillManifestSigner instance
        private_key: Operator private key for signing
        operator_public_key: Operator public key (for validation)
        backup_dir: Optional directory to backup v1 manifests

    Returns:
        True if successful, False otherwise
    """
    try:
        # Load v1 manifest
        with open(manifest_path, "r") as f:
            v1_data = json.load(f)

        # Parse as v1
        v1_manifest = SkillManifestV1(
            skill_id=v1_data.get("skill_id", "unknown"),
            version=v1_data.get("version", "1.0.0"),
            boot_layer=v1_data.get("boot_layer", "bundled"),
            parameters=v1_data.get("parameters", []),
            dependencies=v1_data.get("dependencies", []),
            entry_point=v1_data.get("entry_point"),
            audit_events=v1_data.get("audit_events", []),
        )

        # Determine tier
        tier = assign_tier_for_skill(v1_manifest.skill_id)

        # Migrate to v2
        v2_manifest = migrate_manifest_v1_to_v2(v1_manifest, default_tier=tier)

        # Sign the v2 manifest
        signature = signer.sign_manifest(v2_manifest, private_key)

        # Create license binding
        license_binding = LicenseBindingMetadata(
            required_tier=tier,
            binding_hash="",  # Placeholder (would be computed from manifest + key)
            operator_signature=signature,
            timestamp="",  # Will be set by signer
        )

        # Add license binding to v2 manifest
        v2_with_binding = SkillManifestV2(
            skill_id=v2_manifest.skill_id,
            version=v2_manifest.version,
            boot_layer=v2_manifest.boot_layer,
            parameters=v2_manifest.parameters,
            dependencies=v2_manifest.dependencies,
            entry_point=v2_manifest.entry_point,
            audit_events=v2_manifest.audit_events,
            license_binding=license_binding,
        )

        # Backup v1 manifest if requested
        if backup_dir:
            backup_path = backup_dir / manifest_path.relative_to(manifest_path.parent.parent.parent)
            backup_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(manifest_path, backup_path)
            logger.info(f"Backed up {manifest_path.name} to {backup_path}")

        # Write v2 manifest
        with open(manifest_path, "w") as f:
            json.dump(v2_with_binding.to_dict(), f, indent=2)

        logger.info(f"✅ Migrated {v1_manifest.skill_id} (tier={tier})")
        return True

    except Exception as e:
        logger.error(f"❌ Failed to migrate {manifest_path}: {e}")
        return False


def main():
    """Main migration script."""
    parser = argparse.ArgumentParser(description="Migrate Skill manifests v1→v2")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be done without modifying files")
    parser.add_argument(
        "--operator-key",
        type=str,
        help="Path to operator private key (default: generate new)",
    )
    parser.add_argument(
        "--buildin-dir",
        type=str,
        default="core/plugins/buildin",
        help="Path to plugins/buildin directory",
    )
    parser.add_argument(
        "--backup",
        action="store_true",
        help="Backup v1 manifests before overwriting",
    )

    args = parser.parse_args()

    # Setup paths
    buildin_dir = Path(args.buildin_dir)
    if not buildin_dir.exists():
        logger.error(f"Buildin directory not found: {buildin_dir}")
        return 1

    # Initialize signer
    signer = SkillManifestSigner()

    # Load or generate operator keypair
    if args.operator_key:
        logger.info(f"Loading operator key from {args.operator_key}")
        private_key = signer.load_operator_key(args.operator_key)
        operator_public_key = private_key.public_key()
    else:
        logger.info("Generating new operator keypair")
        operator_public_key, private_key = signer.generate_operator_keypair()

    # Find all builtin manifests
    manifests = find_builtin_manifests(buildin_dir)

    if not manifests:
        logger.error("No builtin manifests found")
        return 1

    # Setup backup directory if requested
    backup_dir = None
    if args.backup and not args.dry_run:
        backup_dir = Path("backups/manifests_v1_pre_migration")
        backup_dir.mkdir(parents=True, exist_ok=True)
        logger.info(f"Backing up v1 manifests to {backup_dir}")

    # Migrate each manifest
    success_count = 0
    for manifest_path in manifests:
        if args.dry_run:
            logger.info(f"[DRY RUN] Would migrate {manifest_path}")
        else:
            if migrate_manifest_file(
                manifest_path,
                signer,
                private_key,
                operator_public_key,
                backup_dir=backup_dir,
            ):
                success_count += 1

    # Report results
    logger.info(f"\nMigration complete: {success_count}/{len(manifests)} manifests migrated")

    if success_count == len(manifests):
        logger.info("✅ All manifests successfully migrated")
        return 0
    else:
        logger.warning(f"⚠️  {len(manifests) - success_count} manifests failed to migrate")
        return 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
