"""
Phase 3: Plugin Registry Consistency Synchronization

Ensures all instances have identical plugin sets + versions.

Detects: MISSING, VERSION_MISMATCH, CHECKSUM_MISMATCH
Remediates: Auto-install (safe), Auto-update (safe)
Audits: All operations logged to security event trail
"""

import os
import json
import logging
import shutil
from dataclasses import dataclass, asdict
from typing import List, Dict, Optional, Tuple
from datetime import datetime
from pathlib import Path

from .canonical_manifest import CanonicalManifestManager, PluginEntry
from .hash_verification import PluginHashVerifier, VerificationSeverity
from .dependency_resolver import DependencyResolver

logger = logging.getLogger(__name__)


@dataclass
class PluginDrift:
    """Plugin divergence between instances"""
    plugin_id: str
    drift_type: str  # MISSING, VERSION_MISMATCH, CHECKSUM_MISMATCH
    expected_version: Optional[str] = None
    actual_version: Optional[str] = None
    severity: str = "HIGH"
    is_remediation_available: bool = False


class PluginInstaller:
    """Helper for installing/updating plugins"""

    def __init__(self, plugins_dir: Optional[str] = None):
        """
        Initialize installer.

        Args:
            plugins_dir: Directory for installed plugins
        """
        self.plugins_dir = plugins_dir or self._default_plugins_dir()
        Path(self.plugins_dir).mkdir(parents=True, exist_ok=True)

    def _default_plugins_dir(self) -> str:
        """Get default plugins directory"""
        corvin_home = os.getenv("CORVIN_HOME", os.path.expanduser("~/.corvin"))
        return os.path.join(corvin_home, "plugins", "installed")

    def install_plugin(self, plugin_entry: PluginEntry) -> Tuple[bool, str]:
        """
        Install a plugin from canonical manifest.

        This is a mock implementation. Real implementation would:
        1. Download plugin from S3/artifact repository
        2. Verify checksum
        3. Extract to plugins_dir
        4. Verify extraction succeeded

        Args:
            plugin_entry: Plugin to install

        Returns: (success, message)
        """
        try:
            plugin_path = os.path.join(self.plugins_dir, plugin_entry.plugin_id)

            # Mock: Create plugin directory structure
            Path(plugin_path).mkdir(parents=True, exist_ok=True)

            # Mock: Create plugin.json
            plugin_json = {
                "id": plugin_entry.plugin_id,
                "version": plugin_entry.version,
                "dependencies": plugin_entry.dependencies,
                "boot_layer": plugin_entry.boot_layer,
            }

            with open(os.path.join(plugin_path, "plugin.json"), "w") as f:
                json.dump(plugin_json, f, indent=2)

            logger.info(f"✅ Installed plugin: {plugin_entry.plugin_id}@{plugin_entry.version}")
            return True, f"Installed {plugin_entry.plugin_id}@{plugin_entry.version}"

        except Exception as e:
            logger.error(f"❌ Failed to install {plugin_entry.plugin_id}: {e}")
            return False, str(e)

    def update_plugin(self, plugin_entry: PluginEntry) -> Tuple[bool, str]:
        """
        Update a plugin to new version.

        Creates backup before update (rollback on failure).

        Args:
            plugin_entry: Plugin with new version

        Returns: (success, message)
        """
        try:
            plugin_path = os.path.join(self.plugins_dir, plugin_entry.plugin_id)
            backup_path = f"{plugin_path}.backup"

            # Backup current version
            if os.path.exists(plugin_path):
                if os.path.exists(backup_path):
                    shutil.rmtree(backup_path)
                shutil.copytree(plugin_path, backup_path)

            # Remove current version
            if os.path.exists(plugin_path):
                shutil.rmtree(plugin_path)

            # Install new version
            success, message = self.install_plugin(plugin_entry)

            if success:
                # Remove backup on successful update
                if os.path.exists(backup_path):
                    shutil.rmtree(backup_path)
                logger.info(f"✅ Updated plugin: {plugin_entry.plugin_id}@{plugin_entry.version}")
                return True, f"Updated {plugin_entry.plugin_id}@{plugin_entry.version}"
            else:
                # Restore from backup on failure
                if os.path.exists(backup_path):
                    shutil.rmtree(plugin_path)
                    shutil.copytree(backup_path, plugin_path)
                logger.error(f"❌ Update failed for {plugin_entry.plugin_id}, restored from backup")
                return False, f"Update failed: {message}"

        except Exception as e:
            logger.error(f"❌ Failed to update {plugin_entry.plugin_id}: {e}")
            return False, str(e)


class PluginRegistrySynchronizer:
    """
    Phase 3: Plugin registry consistency and auto-remediation.

    Detects drifts from canonical manifest:
    - MISSING: plugin not installed
    - VERSION_MISMATCH: version differs
    - CHECKSUM_MISMATCH: hash doesn't match (tampering)

    Auto-remediates safe drifts:
    - Missing plugin → auto-install
    - Version mismatch → auto-update (respecting dependencies)

    Logs all operations to security audit trail.
    """

    def __init__(
        self,
        instance_id: str,
        manifest_manager: Optional[CanonicalManifestManager] = None,
        hash_verifier: Optional[PluginHashVerifier] = None,
        dependency_resolver: Optional[DependencyResolver] = None,
        installer: Optional[PluginInstaller] = None,
    ):
        """
        Initialize synchronizer.

        Args:
            instance_id: ID of this instance
            manifest_manager: CanonicalManifestManager instance
            hash_verifier: PluginHashVerifier instance
            dependency_resolver: DependencyResolver instance
            installer: PluginInstaller instance
        """
        self.instance_id = instance_id
        self.manifest_manager = manifest_manager or CanonicalManifestManager()
        self.hash_verifier = hash_verifier or PluginHashVerifier(manifest_manager=self.manifest_manager)
        self.dependency_resolver = dependency_resolver or DependencyResolver(manifest_manager=self.manifest_manager)
        self.installer = installer or PluginInstaller()

    def detect_plugin_drift(self) -> List[PluginDrift]:
        """
        Detect plugin divergence between instance and canonical.

        Returns list of:
        - MISSING: plugin not installed
        - VERSION_MISMATCH: version differs
        - CHECKSUM_MISMATCH: hash doesn't match
        """
        drifts = []

        # Load canonical manifest
        manifest = self.manifest_manager.load_manifest()
        if not manifest.plugins:
            logger.warning("No plugins in canonical manifest")
            return drifts

        # Verify each canonical plugin
        for plugin_entry in manifest.plugins:
            verification = self.hash_verifier.verify_integrity(plugin_entry.plugin_id)

            if verification.is_valid:
                # No drift
                continue

            # Convert verification result to drift
            drift = PluginDrift(
                plugin_id=plugin_entry.plugin_id,
                drift_type=self._severity_to_drift_type(verification.severity),
                expected_version=verification.expected_version,
                actual_version=verification.actual_version,
                severity=verification.severity.name,
                is_remediation_available=self._is_remediation_available(verification.reason),
            )

            drifts.append(drift)
            logger.warning(
                f"⚠️  Plugin drift: {plugin_entry.plugin_id} — {verification.reason}"
            )

        return drifts

    def remediate(self, dry_run: bool = False) -> Tuple[int, List[str]]:
        """
        Auto-remediate detected drifts.

        Safe remediations:
        - Missing plugin → auto-install
        - Version mismatch → auto-update

        Unsafe remediations (not attempted):
        - Checksum mismatch (tampering) → manual review required

        Args:
            dry_run: If True, don't actually remediate (for testing)

        Returns: (remediation_count, remediation_messages)
        """
        drifts = self.detect_plugin_drift()
        remediation_count = 0
        messages = []

        for drift in drifts:
            if drift.drift_type == "CHECKSUM_MISMATCH":
                # Don't auto-remediate tampering
                msg = f"⚠️  Checksum mismatch for {drift.plugin_id} — manual review required"
                logger.warning(msg)
                messages.append(msg)
                self._audit_remediation_attempt(drift.plugin_id, "CHECKSUM_MISMATCH", False, "Tampering detected")
                continue

            # Fetch plugin entry
            plugin_entry = self.manifest_manager.get_plugin(drift.plugin_id)
            if not plugin_entry:
                logger.error(f"Plugin {drift.plugin_id} not in manifest")
                continue

            # Skip remediation on dry run
            if not dry_run:
                if drift.drift_type == "MISSING":
                    success, msg = self.installer.install_plugin(plugin_entry)
                    remediation_count += success
                    messages.append(msg)
                    self._audit_remediation_attempt(drift.plugin_id, "INSTALL", success, msg)

                elif drift.drift_type == "VERSION_MISMATCH":
                    success, msg = self.installer.update_plugin(plugin_entry)
                    remediation_count += success
                    messages.append(msg)
                    self._audit_remediation_attempt(drift.plugin_id, "UPDATE", success, msg)

        return remediation_count, messages

    def get_local_plugins(self) -> Dict[str, str]:
        """
        Get plugins installed on this instance.

        Returns: {plugin_id: version}
        """
        plugins = {}
        plugins_dir = self.installer.plugins_dir

        if not os.path.exists(plugins_dir):
            return plugins

        for plugin_id in os.listdir(plugins_dir):
            plugin_path = os.path.join(plugins_dir, plugin_id)
            if os.path.isdir(plugin_path):
                version = self._read_plugin_version(plugin_path)
                if version:
                    plugins[plugin_id] = version

        return plugins

    def get_canonical_plugins(self) -> Dict[str, str]:
        """
        Get canonical plugins from manifest.

        Returns: {plugin_id: version}
        """
        manifest = self.manifest_manager.load_manifest()
        return {p.plugin_id: p.version for p in manifest.plugins}

    def _severity_to_drift_type(self, severity: VerificationSeverity) -> str:
        """Map verification severity to drift type"""
        severity_map = {
            VerificationSeverity.CRITICAL: "CHECKSUM_MISMATCH",
            VerificationSeverity.HIGH: "VERSION_MISMATCH",
            VerificationSeverity.MEDIUM: "VERSION_MISMATCH",
            VerificationSeverity.LOW: "CHECKSUM_MISMATCH",
        }
        return severity_map.get(severity, "UNKNOWN")

    def _is_remediation_available(self, reason: str) -> bool:
        """Check if drift can be auto-remediated"""
        safe_reasons = ["Hash mismatch", "Version mismatch", "Plugin not found"]
        return any(r in reason for r in safe_reasons)

    def _read_plugin_version(self, plugin_path: str) -> Optional[str]:
        """Read plugin version from plugin.json"""
        try:
            plugin_json_path = os.path.join(plugin_path, "plugin.json")
            if not os.path.exists(plugin_json_path):
                return None

            with open(plugin_json_path, "r") as f:
                plugin_json = json.load(f)

            return plugin_json.get("version")
        except Exception as e:
            logger.warning(f"Failed to read version from {plugin_path}: {e}")
            return None

    def _audit_remediation_attempt(self, plugin_id: str, action: str, success: bool, message: str):
        """Log remediation attempt to audit trail"""
        try:
            from core.compliance.security_events import write_event

            write_event(
                "plugin_remediation_attempted",
                {
                    "instance_id": self.instance_id,
                    "plugin_id": plugin_id,
                    "action": action,
                    "success": success,
                    "message": message,
                    "timestamp": datetime.utcnow().isoformat(),
                },
            )
        except Exception as e:
            logger.warning(f"Failed to audit remediation: {e}")
