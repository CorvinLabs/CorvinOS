"""
Skill Forge v2.0 Phase 3: ZIP Packaging & Distribution

Packages complete Skills (folders from Phase 1-2) into distributable ZIP archives
with metadata, checksums, and audit trails.

ADR-0674: Skill Package & ZIP Distribution Format
License: Apache-2.0
"""

import json
import os
import zipfile
import hashlib
import shutil
import sys
import subprocess
from pathlib import Path
from datetime import datetime
from typing import Dict, Tuple, Optional, List
from dataclasses import asdict, dataclass
import logging

from core.skills.manifest_v2 import SkillManifestV2
from core.skills import package_signing

logger = logging.getLogger(__name__)


class PackagingError(Exception):
    """Raised when skill packaging fails."""
    pass


class ChecksumVerificationError(Exception):
    """Raised when checksum verification fails."""
    pass


@dataclass(frozen=True)
class PackageMetadata:
    """Immutable package metadata."""
    skill_id: str
    version: str
    packaged_at: str
    zip_path: str
    zip_hash: str
    file_count: int
    size_bytes: int
    generation_context: Dict
    audit_trail_events: int


class SkillPackager:
    """Package complete Skills into distributable ZIP archives."""

    def __init__(self, output_dir: Optional[Path] = None):
        """
        Initialize packager.

        Args:
            output_dir: Where to store generated ZIP files
                       (default: ~/.corvin/skills_packages/)
        """
        if output_dir is None:
            output_dir = Path.home() / ".corvin" / "skills_packages"
        self.output_dir = output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def package(self, skill_folder: Path, manifest: SkillManifestV2) -> Tuple[Path, str, Dict]:
        """
        Package a complete Skill into a ZIP archive.

        Args:
            skill_folder: Path to Skill folder (Phase 1-2 output)
            manifest: SkillManifestV2 instance

        Returns:
            (zip_path, zip_hash, metadata_dict)

        Raises:
            ValueError: Invalid folder structure or missing required files
            FileExistsError: Package already exists
        """
        # Validate folder structure
        self._validate_skill_folder(skill_folder, manifest)

        # Create output filename
        zip_filename = f"{manifest.skill_id}_{manifest.version}.zip"
        zip_path = self.output_dir / zip_filename

        if zip_path.exists():
            raise FileExistsError(f"Package already exists: {zip_path}")

        # Create .forge metadata directory (if not already present)
        forge_dir = skill_folder / ".forge"
        forge_dir.mkdir(exist_ok=True)

        # Generate metadata
        generation_context = self._create_generation_context(skill_folder, manifest)
        audit_trail = self._create_audit_trail(skill_folder, manifest)

        # Write metadata files (fixes H2: File Permissions) BEFORE hashing: the checksum list -
        # and so the signature - must cover them, or a "verified" install carries files anyone
        # could have rewritten after signing (ADR-2252 review, PKG-6).
        gen_ctx_path = forge_dir / "generation_context.json"
        gen_ctx_path.write_text(json.dumps(generation_context, indent=2))
        gen_ctx_path.chmod(0o600)  # owner read+write only

        audit_path = forge_dir / "audit_trail.jsonl"
        audit_path.write_text("\n".join(json.dumps(e) for e in audit_trail))
        audit_path.chmod(0o600)

        checksums = self._compute_checksums(skill_folder)
        checksum_path = forge_dir / "checksum.sha256"
        checksum_path.write_text("\n".join(f"{h} {p}" for p, h in checksums.items()))
        checksum_path.chmod(0o600)

        # Create ZIP archive from EXACTLY the hashed set (plus the checksum list and the
        # signature): a file the list does not name - a symlink target, a nested .forge/ - is
        # never shipped, so the packager cannot sign what its own installer must refuse.
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            arc = manifest.skill_id
            for rel in checksums:
                zf.write(skill_folder / rel, arcname=f"{arc}/{rel}")
            zf.write(checksum_path, arcname=f"{arc}/{package_signing.CHECKSUM_REL}")
            # Signed only when this install's operator has generated a key (ADR-2252);
            # otherwise the package is unsigned and the installer will say so.
            signature = self._sign_package(skill_folder, checksum_path)
            if signature:
                zf.writestr(f"{manifest.skill_id}/{package_signing.SIGNATURE_NAME}", signature)

        # Compute ZIP hash
        zip_hash = self._compute_file_hash(zip_path)

        # Prepare return metadata
        metadata = {
            "skill_id": manifest.skill_id,
            "version": manifest.version,
            "packaged_at": datetime.utcnow().isoformat() + "Z",
            "zip_path": str(zip_path),
            "zip_hash": zip_hash,
            "generation_context": generation_context,
            "checksum_count": len(checksums),
            "audit_trail_events": len(audit_trail),
            "signed": bool(signature),
        }

        logger.info(f"Packaged Skill: {manifest.skill_id} v{manifest.version} → {zip_path}")

        return zip_path, zip_hash, metadata

    def _validate_skill_folder(self, skill_folder: Path, manifest: SkillManifestV2) -> None:
        """Validate that folder has all required Phase 1-2 outputs."""
        if not skill_folder.exists():
            raise ValueError(f"Skill folder not found: {skill_folder}")

        required_dirs = ["src", "hooks", "tests", "scripts", "docs", "references"]
        for dir_name in required_dirs:
            dir_path = skill_folder / dir_name
            if not dir_path.exists():
                raise ValueError(f"Missing required directory: {dir_path}")

        required_files = ["skill.json", "README.md"]
        for file_name in required_files:
            file_path = skill_folder / file_name
            if not file_path.exists():
                raise ValueError(f"Missing required file: {file_path}")

    @staticmethod
    def _sign_package(skill_folder: Path, checksum_path: Path) -> Optional[str]:
        """Signature over manifest + checksum list, or None when no usable operator key."""
        key = package_signing.signing_key()
        if key is None:
            return None
        try:
            return package_signing.sign(
                (skill_folder / package_signing.MANIFEST_NAME).read_bytes(),
                checksum_path.read_bytes(), key)
        except Exception as exc:  # noqa: BLE001 — never lose the package over a signing fault
            logger.warning("package left unsigned: %s", type(exc).__name__)
            return None

    def _create_generation_context(
        self, skill_folder: Path, manifest: SkillManifestV2
    ) -> Dict:
        """Create generation_context.json metadata."""
        forge_metadata_file = skill_folder / ".forge" / "generation_context.json"
        if forge_metadata_file.exists():
            # Use existing generation context (from Phase 1-2)
            return json.loads(forge_metadata_file.read_text())

        # Create new one (fallback)
        return {
            "generated_at": datetime.utcnow().isoformat() + "Z",
            "generated_by": "skill-forge-v2.0",
            "generator_version": "2.0.0",
            "skill_id": manifest.skill_id,
            "version": manifest.version,
            "boot_layer": manifest.boot_layer,
            "phases_completed": [
                "phase1_skeleton_generated",
                "phase2_llm_code_generated",
                "phase3_packaging_complete",
            ],
            "manifest_hash": self._compute_file_hash(skill_folder / "skill.json"),
        }

    def _create_audit_trail(self, skill_folder: Path, manifest: SkillManifestV2) -> List[Dict]:
        """Create audit_trail.jsonl with generation events."""
        forge_audit_file = skill_folder / ".forge" / "audit_trail.jsonl"
        if forge_audit_file.exists():
            # Use existing audit trail + add packaging event
            events = [
                json.loads(line) for line in forge_audit_file.read_text().strip().split("\n") if line
            ]
        else:
            events = []

        # Add packaging event
        events.append({
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "event": "package_created",
            "phase": 3,
            "skill_id": manifest.skill_id,
            "version": manifest.version,
        })

        return events

    def _compute_checksums(self, skill_folder: Path) -> Dict[str, str]:
        """SHA-256 of every regular file in the skill folder, keyed by POSIX relative path.

        Symlinks are never followed or listed (C4). Only the top-level ``.forge/checksum.sha256``
        (the list itself) is excluded; ``.forge`` metadata is covered. Keys are ``as_posix()``:
        ``str()`` of a Windows path uses backslashes, which no ZIP arcname does (PKG-5).
        """
        checksums = {}
        root = skill_folder.resolve()
        for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
            dirnames[:] = sorted(d for d in dirnames if not (Path(dirpath) / d).is_symlink())
            for name in sorted(filenames):
                file_path = Path(dirpath) / name
                if file_path.is_symlink():
                    logger.warning(f"Skipping symlink (not allowed): {file_path}")
                    continue
                if not file_path.is_file():
                    continue
                rel = file_path.relative_to(root).as_posix()
                if rel == package_signing.CHECKSUM_REL:
                    continue
                checksums[rel] = self._compute_file_hash(file_path)
        return checksums

    @staticmethod
    def _compute_file_hash(file_path: Path) -> str:
        """Compute SHA256 hash of a file."""
        sha256 = hashlib.sha256()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                sha256.update(chunk)
        return f"sha256:{sha256.hexdigest()}"


def package_skill(
    skill_folder: Path, manifest: SkillManifestV2, output_dir: Optional[Path] = None
) -> Tuple[Path, str]:
    """
    Convenience function: package a Skill.

    Args:
        skill_folder: Path to Skill folder
        manifest: SkillManifestV2 instance
        output_dir: Where to save ZIP (default: ~/.corvin/skills_packages/)

    Returns:
        (zip_path, zip_hash)
    """
    packager = SkillPackager(output_dir)
    zip_path, zip_hash, _ = packager.package(skill_folder, manifest)
    return zip_path, zip_hash
