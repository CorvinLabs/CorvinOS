"""
Phase 3: Plugin Hash Verification

Verifies installed plugins have not been tampered with.
Compares checksums against canonical manifest.
"""

import os
import hashlib
import logging
from pathlib import Path
from typing import Dict, List, Optional
from dataclasses import dataclass
from enum import Enum

from .canonical_manifest import CanonicalManifestManager, PluginEntry

logger = logging.getLogger(__name__)


class VerificationSeverity(Enum):
    """Verification severity levels"""
    CRITICAL = "CRITICAL"  # Tampering detected
    HIGH = "HIGH"  # Version mismatch
    MEDIUM = "MEDIUM"  # Minor drift
    LOW = "LOW"  # Informational


@dataclass
class VerificationResult:
    """Result of plugin integrity verification"""
    plugin_id: str
    is_valid: bool
    severity: VerificationSeverity
    reason: str  # e.g., "Hash mismatch", "Version mismatch", "Missing"
    expected_version: Optional[str] = None
    actual_version: Optional[str] = None
    expected_checksum: Optional[str] = None
    actual_checksum: Optional[str] = None


class PluginHashVerifier:
    """
    Verifies installed plugins against canonical manifest.

    Detects:
    - Hash mismatch (tampering)
    - Version mismatch
    - Missing plugins
    """

    def __init__(self, plugins_dir: Optional[str] = None, manifest_manager: Optional[CanonicalManifestManager] = None):
        """
        Initialize verifier.

        Args:
            plugins_dir: Directory containing installed plugins
                        Falls back to ~/.corvin/plugins/installed/
            manifest_manager: CanonicalManifestManager instance
        """
        self.plugins_dir = plugins_dir or self._default_plugins_dir()
        self.manifest_manager = manifest_manager or CanonicalManifestManager()

    def _default_plugins_dir(self) -> str:
        """Get default plugins directory"""
        corvin_home = os.getenv("CORVIN_HOME", os.path.expanduser("~/.corvin"))
        return os.path.join(corvin_home, "plugins", "installed")

    def verify_integrity(self, plugin_id: str) -> VerificationResult:
        """
        Verify a single plugin's integrity.

        Returns: VerificationResult with status and details
        """
        # Load canonical entry
        canonical_entry = self.manifest_manager.get_plugin(plugin_id)
        if not canonical_entry:
            return VerificationResult(
                plugin_id=plugin_id,
                is_valid=False,
                severity=VerificationSeverity.CRITICAL,
                reason="Not in canonical manifest",
            )

        # Check if plugin is installed
        plugin_path = os.path.join(self.plugins_dir, plugin_id)
        if not os.path.exists(plugin_path):
            return VerificationResult(
                plugin_id=plugin_id,
                is_valid=False,
                severity=VerificationSeverity.CRITICAL,
                reason="Plugin not found (missing)",
                expected_version=canonical_entry.version,
            )

        # Read plugin version
        actual_version = self._read_plugin_version(plugin_path)
        if actual_version != canonical_entry.version:
            return VerificationResult(
                plugin_id=plugin_id,
                is_valid=False,
                severity=VerificationSeverity.HIGH,
                reason="Version mismatch",
                expected_version=canonical_entry.version,
                actual_version=actual_version,
            )

        # Compute plugin hash
        actual_checksum = self._compute_plugin_hash(plugin_path)
        if actual_checksum != canonical_entry.checksum:
            return VerificationResult(
                plugin_id=plugin_id,
                is_valid=False,
                severity=VerificationSeverity.CRITICAL,
                reason="Hash mismatch (tampering detected)",
                expected_checksum=canonical_entry.checksum,
                actual_checksum=actual_checksum,
            )

        # All checks passed
        return VerificationResult(
            plugin_id=plugin_id,
            is_valid=True,
            severity=VerificationSeverity.LOW,
            reason="Integrity verified",
            expected_version=canonical_entry.version,
            actual_version=actual_version,
        )

    def verify_all_plugins(self) -> List[VerificationResult]:
        """
        Verify all installed plugins.

        Returns: List of VerificationResult for each plugin
        """
        results = []

        # Ensure plugins dir exists
        if not os.path.exists(self.plugins_dir):
            logger.warning(f"Plugins directory not found: {self.plugins_dir}")
            return results

        # Verify each plugin
        installed_plugins = [
            d for d in os.listdir(self.plugins_dir)
            if os.path.isdir(os.path.join(self.plugins_dir, d))
        ]

        for plugin_id in installed_plugins:
            result = self.verify_integrity(plugin_id)
            results.append(result)

        return results

    def _compute_plugin_hash(self, plugin_path: str) -> str:
        """
        Compute SHA256 hash of plugin source tree.

        Hashes all files in plugin directory in deterministic order.
        """
        try:
            hasher = hashlib.sha256()

            # Walk directory in sorted order for determinism
            for root, dirs, files in os.walk(plugin_path):
                dirs.sort()  # Ensure deterministic order
                for filename in sorted(files):
                    filepath = os.path.join(root, filename)
                    try:
                        with open(filepath, "rb") as f:
                            for chunk in iter(lambda: f.read(8192), b""):
                                hasher.update(chunk)
                    except (IOError, OSError) as e:
                        logger.warning(f"Could not hash file {filepath}: {e}")

            return hasher.hexdigest()

        except Exception as e:
            logger.error(f"❌ Failed to compute hash for {plugin_path}: {e}")
            return ""

    def _read_plugin_version(self, plugin_path: str) -> Optional[str]:
        """
        Read plugin version from plugin.json.

        Args:
            plugin_path: Path to plugin directory

        Returns: Version string or None if not found
        """
        try:
            import json

            plugin_json_path = os.path.join(plugin_path, "plugin.json")
            if not os.path.exists(plugin_json_path):
                return None

            with open(plugin_json_path, "r") as f:
                plugin_json = json.load(f)

            return plugin_json.get("version")

        except Exception as e:
            logger.warning(f"Failed to read plugin version from {plugin_path}: {e}")
            return None

    def get_verification_summary(self, results: List[VerificationResult]) -> Dict:
        """Generate summary of verification results"""
        total = len(results)
        valid = sum(1 for r in results if r.is_valid)
        invalid = total - valid

        by_severity = {
            VerificationSeverity.CRITICAL: sum(1 for r in results if r.severity == VerificationSeverity.CRITICAL),
            VerificationSeverity.HIGH: sum(1 for r in results if r.severity == VerificationSeverity.HIGH),
            VerificationSeverity.MEDIUM: sum(1 for r in results if r.severity == VerificationSeverity.MEDIUM),
            VerificationSeverity.LOW: sum(1 for r in results if r.severity == VerificationSeverity.LOW),
        }

        return {
            "total_plugins": total,
            "valid": valid,
            "invalid": invalid,
            "by_severity": {k.name: v for k, v in by_severity.items()},
            "all_valid": invalid == 0,
        }
