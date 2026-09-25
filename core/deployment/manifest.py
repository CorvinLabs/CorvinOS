"""
Manifest Management — Calculate code hash + version signatures

ADR-0407, ADR-0516 compliance: Detect code divergence between instances
"""

import hashlib
import json
import subprocess
from dataclasses import dataclass
from typing import Optional
from datetime import datetime


@dataclass
class ManifestSnapshot:
    """Immutable code manifest snapshot"""
    git_sha: str
    version_tag: str
    manifest_hash: str  # SHA256 of all code
    timestamp: str
    instance_id: str
    tenant_id: str = "_default"


class ManifestManager:
    """Central manifest hashing + versioning"""

    MANIFEST_PATHS = [
        "core/",
        "corvin_operator/",
        "scripts/",
    ]

    @staticmethod
    def get_git_sha() -> str:
        """Get current git commit SHA"""
        try:
            return subprocess.check_output(
                ["git", "rev-parse", "HEAD"],
                cwd="/home/shumway/projects/CorvinOS",
                text=True
            ).strip()
        except Exception as e:
            raise RuntimeError(f"Cannot get git SHA: {e}")

    @staticmethod
    def get_version_tag() -> str:
        """Get version tag from git or fallback to env"""
        try:
            return subprocess.check_output(
                ["git", "describe", "--tags", "--always"],
                cwd="/home/shumway/projects/CorvinOS",
                text=True
            ).strip()
        except:
            return "0.unknown"

    @staticmethod
    def calculate_manifest_hash(paths: list = None) -> str:
        """
        Calculate SHA256 hash of all code in paths.
        Ensures all instances have identical code.
        """
        if paths is None:
            paths = ManifestManager.MANIFEST_PATHS

        hasher = hashlib.sha256()

        for path in paths:
            try:
                full_path = f"/home/shumway/projects/CorvinOS/{path}"
                # Hash all .py files in directory
                result = subprocess.check_output(
                    ["find", full_path, "-name", "*.py", "-type", "f", "-exec", "sha256sum", "{}", "+"],
                    text=True,
                    stderr=subprocess.DEVNULL
                )
                hasher.update(result.encode())
            except Exception:
                # Path may not exist — skip
                pass

        return hasher.hexdigest()

    @staticmethod
    def create_snapshot(instance_id: str, tenant_id: str = "_default") -> ManifestSnapshot:
        """Create immutable manifest snapshot"""
        return ManifestSnapshot(
            git_sha=ManifestManager.get_git_sha(),
            version_tag=ManifestManager.get_version_tag(),
            manifest_hash=ManifestManager.calculate_manifest_hash(),
            timestamp=datetime.utcnow().isoformat() + "Z",
            instance_id=instance_id,
            tenant_id=tenant_id,
        )

    @staticmethod
    def to_dict(snapshot: ManifestSnapshot) -> dict:
        """Convert snapshot to JSON-serializable dict"""
        return {
            "git_sha": snapshot.git_sha,
            "version_tag": snapshot.version_tag,
            "manifest_hash": snapshot.manifest_hash,
            "timestamp": snapshot.timestamp,
            "instance_id": snapshot.instance_id,
            "tenant_id": snapshot.tenant_id,
        }

    @staticmethod
    def from_dict(data: dict) -> ManifestSnapshot:
        """Deserialize snapshot from dict"""
        return ManifestSnapshot(**data)
