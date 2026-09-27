"""
Manifest Management — Calculate code hash + version signatures

ADR-0407, ADR-0516 compliance: Detect code divergence between instances
"""

import hashlib
import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from datetime import datetime

# The source tree this module was imported from — the code that is actually
# running. Until 2026-09-27 every git/hash call hard-wired
# /home/shumway/projects/CorvinOS, so on any other install registration failed
# ("Cannot get git SHA") and on this host it described a tree that is not
# necessarily the one being served.
REPO_ROOT = Path(__file__).resolve().parents[2]

# Directories that are not the product's code: virtualenvs, caches, VCS and
# frontend dependencies. core/console/.venv alone held ~7 600 .py files and made
# a boot-time snapshot take ~7.6 s inside the async lifespan.
_SKIP_DIRS = frozenset({".git", ".venv", "venv", "node_modules", "__pycache__", "dist", "build"})


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
                cwd=str(REPO_ROOT),
                text=True,
                stderr=subprocess.DEVNULL,
            ).strip()
        except Exception as e:
            raise RuntimeError(f"Cannot get git SHA: {e}")

    @staticmethod
    def get_version_tag() -> str:
        """Get version tag from git or fallback to env"""
        try:
            return subprocess.check_output(
                ["git", "describe", "--tags", "--always"],
                cwd=str(REPO_ROOT),
                text=True,
                stderr=subprocess.DEVNULL,
            ).strip()
        except Exception:
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

        # Deterministic across hosts: files are visited in sorted order and
        # hashed under their path RELATIVE to the repo root. The previous
        # `find <abs path> -exec sha256sum` fed absolute paths and
        # filesystem-order output into the digest, so two instances with
        # byte-identical code at different locations (or on different
        # filesystems) always reported MANIFEST_HASH_MISMATCH.
        root = REPO_ROOT
        for path in paths:
            base = root / path
            if not base.is_dir():
                continue  # Path may not exist — skip
            for dirpath, dirnames, filenames in os.walk(base):
                dirnames[:] = sorted(d for d in dirnames if d not in _SKIP_DIRS and not d.startswith("."))
                for name in sorted(filenames):
                    if not name.endswith(".py"):
                        continue
                    fp = Path(dirpath) / name
                    try:
                        digest = hashlib.sha256(fp.read_bytes()).hexdigest()
                    except OSError:
                        continue
                    hasher.update(f"{digest}  {fp.relative_to(root).as_posix()}\n".encode())

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
