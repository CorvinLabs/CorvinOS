"""Marketplace Plugin Installer for OS-Skills (ADR-0511 Phase 2).

Implements:
1. Download + verify skill plugins from marketplace
2. Version management with canary deployment (10% → 50% → 100%)
3. Registry integration with auto-discovery
4. Rollback support and version recovery
5. Audit-first design: all installations logged to compliance chain

ADR-0511: Marketplace Plugin-First Architecture
ADR-0533: OS-Skill Manifest Schema & Versioning
ADR-0314: Learning Infrastructure (event emission)
ADR-0232: Boot Tripwire & Audit Chain (compliance)

NOT WIRED: no production caller as of 2026-09-27 (adversarial review).
Only ``local://`` sources are implemented; any other scheme is refused.
"""

from __future__ import annotations

import json
import hashlib
import logging
import tempfile
import shutil
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any
from uuid import uuid4
import re
import threading

import yaml

logger = logging.getLogger(__name__)

#: A skill id / version is used as a path component of the install dir, so it
#: must be a single safe segment: ``plugin.json``'s ``id`` is marketplace
#: content, and ``../../x`` used to write the copy outside the install root.
_SAFE_SEGMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")

#: Canary progression; promotion may only move forward along this list.
_CANARY_ORDER = ("canary_10", "canary_50", "promoted")

#: Positive field allowlists for this module's chain events. Free text
#: (exception messages, rollback reasons) and filesystem paths stay OUT of
#: the chain; an error is recorded by its type name.
_ID = {"install_id", "skill_id"}
_INSTALLER_AUDIT_FIELDS: Dict[str, frozenset] = {
    "skill_registry_save": frozenset({"skill_count"}),
    "skill_registry_saved": frozenset({"skill_count"}),
    "skill_registry_save_failed": frozenset({"error_type"}),
    "skill_discovery_error": frozenset({"skill_dir_name", "error_type"}),
    "marketplace_discovery_failed": frozenset({"error_type", "tier"}),
    "skill_install_initiated": frozenset(_ID | {"version", "source_scheme", "checksum_sha256",
                                                "deployment_stage", "tenant_scopes"}),
    "skill_installed": frozenset(_ID | {"version", "deployment_stage", "checksum_sha256"}),
    "skill_verification_failed": frozenset(_ID | {"error_type"}),
    "skill_install_failed": frozenset(_ID | {"error_type"}),
    "skill_canary_promotion_initiated": frozenset(_ID | {"from_stage", "to_stage"}),
    "skill_canary_promoted": frozenset(_ID | {"from_stage", "to_stage"}),
    "skill_canary_promotion_failed": frozenset(_ID | {"error_type"}),
    "skill_rollback_initiated": frozenset(_ID | {"version"}),
    "skill_rolled_back": frozenset(_ID | {"version"}),
    "skill_rollback_failed": frozenset(_ID | {"error_type"}),
    "skill_registration_failed": frozenset({"skill_id", "version", "error_type"}),
}


def _safe_segment(value: str, what: str) -> str:
    if not isinstance(value, str) or not _SAFE_SEGMENT.match(value) or ".." in value:
        raise SkillVerificationError(f"unsafe {what}: {value!r}")
    return value


class DeploymentStage(str, Enum):
    """Canary deployment stages."""
    CANARY_10 = "canary_10"      # 10% traffic
    CANARY_50 = "canary_50"      # 50% traffic
    PROMOTED = "promoted"        # 100% traffic
    ROLLBACK = "rollback"        # Rolled back to previous version
    FAILED = "failed"            # Installation failed


class SkillInstallError(Exception):
    """Raised when skill installation fails."""
    pass


class SkillVerificationError(Exception):
    """Raised when skill verification fails."""
    pass


@dataclass(frozen=True)
class SkillPackage:
    """Immutable skill package metadata."""
    skill_id: str
    version: str
    source_url: str
    checksum_sha256: str
    manifest: Dict[str, Any]
    dependencies: List[str] = field(default_factory=list)
    boot_layer: str = "installed"
    origin: str = "marketplace"
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())


@dataclass
class InstallationRecord:
    """Tracks a skill installation (mutable for status updates)."""
    install_id: str
    skill_id: str
    version: str
    source_url: str
    deployment_stage: DeploymentStage
    installed_at: datetime
    status: str = "installing"  # installing, installed, failed, rolling_back
    error_message: Optional[str] = None
    checksum_sha256: str = ""
    local_path: Path = field(default_factory=Path)
    tenant_scopes: List[str] = field(default_factory=lambda: ["_default"])

    def to_dict(self) -> Dict[str, Any]:
        """Convert to JSON-serializable dict."""
        return {
            "install_id": self.install_id,
            "skill_id": self.skill_id,
            "version": self.version,
            "source_url": self.source_url,
            "deployment_stage": self.deployment_stage.value,
            "installed_at": self.installed_at.isoformat(),
            "status": self.status,
            "error_message": self.error_message,
            "checksum_sha256": self.checksum_sha256,
            "local_path": str(self.local_path),
            "tenant_scopes": self.tenant_scopes,
        }


class MarketplaceSkillInstaller:
    """Download, verify, and install OS-Skills from marketplace with canary deployment."""

    def __init__(
        self,
        marketplace_root: Optional[Path] = None,
        skills_install_dir: Optional[Path] = None,
        audit_emit: Optional[callable] = None,
    ):
        """
        Initialize marketplace installer.

        Args:
            marketplace_root: Path to marketplace plugins (default: Corvin-Marketplace)
            skills_install_dir: Where to install skills (default: ~/.corvin/skills_installed/)
            audit_emit: Audit event emitter (reaches compliance chain, GDPR Art. 30/32)
        """
        self.marketplace_root = marketplace_root or Path.home() / "projects" / "Corvin-Marketplace" / "plugins"
        if skills_install_dir is None:
            # Honour CORVIN_HOME (CLAUDE.md: never hard-wire ~/.corvin in skills).
            from core.paths.tenant import corvin_home  # noqa: PLC0415

            skills_install_dir = corvin_home() / "skills_installed"
        self.skills_install_dir = Path(skills_install_dir)
        self.audit_emit = audit_emit or self._default_audit_emit

        self.skills_install_dir.mkdir(parents=True, exist_ok=True)
        self.registry_lock = threading.RLock()

        # Load existing installations
        self.registry_path = self.skills_install_dir / "registry.json"
        self.installations: Dict[str, InstallationRecord] = self._load_registry()

    def _default_audit_emit(self, event_type: str, payload: Dict[str, Any]) -> None:
        """Append to the tenant's hash-chained audit log (audit-FIRST).

        This used to only ``logger.info`` the event while the module claimed
        "all installations logged to compliance chain". A failed chain write
        now raises, so the audited action does not proceed unrecorded.
        """
        from ._chain_audit import chain_write  # noqa: PLC0415

        chain_write(event_type, payload,
                    fields=_INSTALLER_AUDIT_FIELDS.get(event_type, frozenset()))

    def _load_registry(self) -> Dict[str, InstallationRecord]:
        """Load existing skill installations from registry."""
        if not self.registry_path.exists():
            return {}

        try:
            with open(self.registry_path, "r") as f:
                data = json.load(f)

            installations = {}
            for install_id, record in data.get("installations", {}).items():
                installations[install_id] = InstallationRecord(
                    install_id=install_id,
                    skill_id=record["skill_id"],
                    version=record["version"],
                    source_url=record["source_url"],
                    deployment_stage=DeploymentStage(record["deployment_stage"]),
                    installed_at=datetime.fromisoformat(record["installed_at"]),
                    status=record.get("status", "installed"),
                    error_message=record.get("error_message"),
                    checksum_sha256=record.get("checksum_sha256", ""),
                    local_path=Path(record.get("local_path", "")),
                    tenant_scopes=record.get("tenant_scopes", ["_default"]),
                )
            return installations
        except Exception as e:
            logger.warning(f"Failed to load registry: {e}. Starting fresh.")
            return {}

    def _save_registry(self) -> None:
        """Persist installation registry to disk (audit-first: log before write)."""
        with self.registry_lock:
            # Emit audit event FIRST (ADR-0232: audit-first design)
            self.audit_emit("skill_registry_save", {
                "skill_count": len(self.installations),
            })

            try:
                data = {
                    "version": "1.0",
                    "last_updated": datetime.utcnow().isoformat(),
                    "installations": {
                        install_id: record.to_dict()
                        for install_id, record in self.installations.items()
                    },
                }

                # Write to temporary file first, then atomic rename (safety)
                temp_path = self.registry_path.with_suffix(".tmp")
                with open(temp_path, "w") as f:
                    json.dump(data, f, indent=2, default=str)

                temp_path.replace(self.registry_path)

                # Emit success event
                self.audit_emit("skill_registry_saved", {
                    "skill_count": len(self.installations),
                })
            except Exception as e:
                logger.error(f"Failed to save registry: {e}")
                self.audit_emit("skill_registry_save_failed", {
                    "error_type": type(e).__name__,
                })
                raise SkillInstallError(f"Registry save failed: {e}")

    def discover_skills(self, tier: str = "buildin") -> List[SkillPackage]:
        """
        Discover available skills from marketplace.

        Args:
            tier: Either 'buildin' (Apache-2.0 + CLA) or 'contributor' (MIT)

        Returns:
            List of discoverable SkillPackage objects
        """
        tier_path = self.marketplace_root / tier
        if not tier_path.exists():
            logger.warning(f"Marketplace tier not found: {tier_path}")
            return []

        skills = []
        try:
            # Scan tier/<category>/<skill_name>/plugin.json
            for category_dir in tier_path.iterdir():
                if not category_dir.is_dir():
                    continue

                for skill_dir in category_dir.iterdir():
                    if not skill_dir.is_dir():
                        continue

                    plugin_json = skill_dir / "plugin.json"
                    manifest_yaml = skill_dir / "manifest.yaml"

                    if not plugin_json.exists() or not manifest_yaml.exists():
                        logger.debug(f"Skipping incomplete skill: {skill_dir.name}")
                        continue

                    try:
                        with open(plugin_json) as f:
                            plugin_meta = json.load(f)

                        with open(manifest_yaml) as f:
                            manifest = yaml.safe_load(f)

                        skill_id = _safe_segment(plugin_meta.get("id", skill_dir.name), "skill_id")
                        _safe_segment(plugin_meta.get("version", "0.0.0"), "version")
                        skill = SkillPackage(
                            skill_id=skill_id,
                            version=plugin_meta.get("version", "0.0.0"),
                            source_url=f"local://{skill_dir}",
                            checksum_sha256=self._compute_checksum(skill_dir),
                            manifest=manifest,
                            dependencies=manifest.get("depends_on", []),
                            boot_layer=manifest.get("boot_layer", "installed"),
                            origin=tier,
                        )
                        skills.append(skill)
                    except Exception as e:
                        logger.error(f"Failed to parse skill {skill_dir.name}: {e}")
                        self.audit_emit("skill_discovery_error", {
                            "skill_dir_name": skill_dir.name,
                            "error_type": type(e).__name__,
                        })

        except Exception as e:
            logger.error(f"Marketplace discovery failed: {e}")
            self.audit_emit("marketplace_discovery_failed", {
                "error_type": type(e).__name__,
                "tier": tier,
            })

        return skills

    def _compute_checksum(self, path: Path) -> str:
        """Compute SHA256 checksum of directory."""
        hash_obj = hashlib.sha256()
        for file_path in sorted(path.rglob("*")):
            if file_path.is_file():
                hash_obj.update(file_path.relative_to(path).as_posix().encode() + b"\0")
                with open(file_path, "rb") as f:
                    hash_obj.update(f.read())
        return hash_obj.hexdigest()

    def install_skill(
        self,
        skill: SkillPackage,
        deployment_stage: DeploymentStage = DeploymentStage.CANARY_10,
        tenant_scopes: Optional[List[str]] = None,
    ) -> InstallationRecord:
        """
        Install a skill with canary deployment.

        Args:
            skill: SkillPackage to install
            deployment_stage: Starting deployment stage (default: 10% canary)
            tenant_scopes: Tenant scopes for this skill (default: _default)

        Returns:
            InstallationRecord tracking the installation

        Raises:
            SkillInstallError: If installation fails
            SkillVerificationError: If skill verification fails
        """
        tenant_scopes = tenant_scopes or ["_default"]
        install_id = str(uuid4())

        # Audit-FIRST: log attempt. If this record cannot be chained the
        # default emitter raises and nothing is installed.
        self.audit_emit("skill_install_initiated", {
            "install_id": install_id,
            "skill_id": skill.skill_id,
            "version": skill.version,
            "source_scheme": skill.source_url.split("://", 1)[0] if "://" in skill.source_url else "",
            "checksum_sha256": skill.checksum_sha256,
            "deployment_stage": deployment_stage.value,
            "tenant_scopes": tenant_scopes,
        })

        try:
            # Verify skill before installation (ids, manifest, source, checksum)
            self._verify_skill(skill)
            source_path = self._verified_source(skill)

            # Create installation directory — must stay inside the install root
            root = self.skills_install_dir.resolve()
            install_path = (self.skills_install_dir / f"{skill.skill_id}_{skill.version}")
            if install_path.resolve().parent != root:
                raise SkillVerificationError("install path escapes the install root")
            install_path.mkdir(parents=True, exist_ok=True)

            # Copy skill to installation directory
            for file_path in source_path.rglob("*"):
                if file_path.is_file():
                    rel_path = file_path.relative_to(source_path)
                    target_path = install_path / rel_path
                    target_path.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(file_path, target_path)

            # Create installation record
            record = InstallationRecord(
                install_id=install_id,
                skill_id=skill.skill_id,
                version=skill.version,
                source_url=skill.source_url,
                deployment_stage=deployment_stage,
                installed_at=datetime.utcnow(),
                status="installed",
                checksum_sha256=skill.checksum_sha256,
                local_path=install_path,
                tenant_scopes=tenant_scopes,
            )

            with self.registry_lock:
                self.installations[install_id] = record

            self._save_registry()

            # Emit success event
            self.audit_emit("skill_installed", {
                "install_id": install_id,
                "skill_id": skill.skill_id,
                "version": skill.version,
                "deployment_stage": deployment_stage.value,
                "checksum_sha256": skill.checksum_sha256,
            })

            logger.info(f"✅ Skill installed: {skill.skill_id}@{skill.version} → {install_path}")
            return record

        except SkillVerificationError as e:
            logger.error(f"❌ Skill verification failed: {e}")
            self.audit_emit("skill_verification_failed", {
                "install_id": install_id,
                "skill_id": skill.skill_id,
                "error_type": type(e).__name__,
            })
            raise

        except Exception as e:
            logger.error(f"❌ Skill installation failed: {e}")
            self.audit_emit("skill_install_failed", {
                "install_id": install_id,
                "skill_id": skill.skill_id,
                "error_type": type(e).__name__,
            })
            raise SkillInstallError(f"Installation failed: {e}")

    def _verify_skill(self, skill: SkillPackage) -> None:
        """
        Verify skill before installation.

        Checks:
        1. Manifest schema compliance (ADR-0533)
        2. Required fields present
        3. Dependency entries well-formed (NOT resolved against a registry)
        4. Checksum integrity — in :meth:`_verified_source`, at install time

        Raises:
            SkillVerificationError: If any check fails
        """
        _safe_segment(skill.skill_id, "skill_id")
        _safe_segment(skill.version, "version")

        # ADR-0533: the full manifest validator runs before installation. This
        # used to check six keys and log "verification passed".
        if not isinstance(skill.manifest, dict):
            raise SkillVerificationError("manifest must be an object")
        from .skill_validator import validate_skill_manifest_dict  # noqa: PLC0415

        report = validate_skill_manifest_dict(skill.manifest)
        if not report.is_valid:
            raise SkillVerificationError(
                "manifest failed ADR-0533 validation: " + "; ".join(report.blockers[:5])
            )

        # Check version matches
        if skill.manifest["version"] != skill.version:
            raise SkillVerificationError(
                f"Version mismatch: package={skill.version}, manifest={skill.manifest['version']}"
            )

        # Check dependencies are valid
        for dep in skill.dependencies:
            if not isinstance(dep, (str, dict)):
                raise SkillVerificationError(f"Invalid dependency format: {dep}")

        logger.info(f"Skill manifest verified: {skill.skill_id}@{skill.version}")

    def _verified_source(self, skill: SkillPackage) -> Path:
        """Resolve the package source and verify its checksum (fail-closed).

        Only ``local://`` is implemented. A missing source used to be skipped
        silently and the install reported ``installed`` with an empty
        directory; the declared checksum was never compared at all.
        """
        if not skill.source_url.startswith("local://"):
            raise SkillVerificationError(
                f"not_implemented: source scheme of {skill.source_url.split('://', 1)[0]!r}"
            )
        source_path = Path(skill.source_url[len("local://"):])
        if not source_path.is_dir():
            raise SkillVerificationError("package source directory does not exist")
        actual = self._compute_checksum(source_path)
        if actual != skill.checksum_sha256:
            raise SkillVerificationError("package checksum mismatch")
        return source_path

    def promote_canary(
        self,
        install_id: str,
        next_stage: DeploymentStage,
    ) -> InstallationRecord:
        """
        Promote a canary deployment to the next stage.

        Progression: CANARY_10 → CANARY_50 → PROMOTED

        Args:
            install_id: Installation ID to promote
            next_stage: Next deployment stage

        Returns:
            Updated InstallationRecord

        Raises:
            SkillInstallError: If promotion fails or skill not found
        """
        if install_id not in self.installations:
            raise SkillInstallError(f"Installation not found: {install_id}")

        record = self.installations[install_id]
        old_stage = record.deployment_stage

        # Only forward along canary_10 → canary_50 → promoted. A rolled-back
        # or failed installation used to be promotable straight to 100%.
        if (old_stage.value not in _CANARY_ORDER or next_stage.value not in _CANARY_ORDER
                or _CANARY_ORDER.index(next_stage.value) <= _CANARY_ORDER.index(old_stage.value)):
            raise SkillInstallError(
                f"Invalid promotion: {old_stage.value} -> {next_stage.value}"
            )

        # Audit-FIRST: log promotion attempt
        self.audit_emit("skill_canary_promotion_initiated", {
            "install_id": install_id,
            "skill_id": record.skill_id,
            "from_stage": old_stage.value,
            "to_stage": next_stage.value,
        })

        try:
            with self.registry_lock:
                record.deployment_stage = next_stage

            self._save_registry()

            # Emit success event
            self.audit_emit("skill_canary_promoted", {
                "install_id": install_id,
                "skill_id": record.skill_id,
                "from_stage": old_stage.value,
                "to_stage": next_stage.value,
            })

            logger.info(f"✅ Skill promoted: {record.skill_id} ({old_stage.value} → {next_stage.value})")
            return record

        except Exception as e:
            logger.error(f"❌ Promotion failed: {e}")
            self.audit_emit("skill_canary_promotion_failed", {
                "install_id": install_id,
                "skill_id": record.skill_id,
                "error_type": type(e).__name__,
            })
            raise SkillInstallError(f"Promotion failed: {e}")

    def rollback_skill(
        self,
        install_id: str,
        reason: str = "Manual rollback",
    ) -> InstallationRecord:
        """
        Rollback a skill to previous version.

        Args:
            install_id: Installation ID to rollback
            reason: Reason for rollback

        Returns:
            Updated InstallationRecord

        Raises:
            SkillInstallError: If rollback fails or no previous version exists
        """
        if install_id not in self.installations:
            raise SkillInstallError(f"Installation not found: {install_id}")

        record = self.installations[install_id]

        # Audit-FIRST: log rollback attempt
        # ``reason`` is operator free text: logged locally, never chained.
        logger.info("rollback %s requested: %s", install_id, reason)
        self.audit_emit("skill_rollback_initiated", {
            "install_id": install_id,
            "skill_id": record.skill_id,
            "version": record.version,
        })

        try:
            # Mark as rolled back
            with self.registry_lock:
                record.deployment_stage = DeploymentStage.ROLLBACK

            self._save_registry()

            # Emit success event
            self.audit_emit("skill_rolled_back", {
                "install_id": install_id,
                "skill_id": record.skill_id,
                "version": record.version,
            })

            logger.info(f"✅ Skill rolled back: {record.skill_id}@{record.version}")
            return record

        except Exception as e:
            logger.error(f"❌ Rollback failed: {e}")
            self.audit_emit("skill_rollback_failed", {
                "install_id": install_id,
                "skill_id": record.skill_id,
                "error_type": type(e).__name__,
            })
            raise SkillInstallError(f"Rollback failed: {e}")

    def get_installed_skill(self, skill_id: str) -> Optional[InstallationRecord]:
        """Get current installed version of a skill."""
        # Return the most recent non-rolled-back installation
        for record in sorted(
            self.installations.values(),
            key=lambda r: r.installed_at,
            reverse=True,
        ):
            if record.skill_id == skill_id and record.deployment_stage != DeploymentStage.ROLLBACK:
                return record
        return None

    def list_installations(self) -> List[InstallationRecord]:
        """List all skill installations."""
        return list(self.installations.values())

    def get_installation(self, install_id: str) -> Optional[InstallationRecord]:
        """Get installation by ID."""
        return self.installations.get(install_id)
