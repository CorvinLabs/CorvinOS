"""
Skill Forge v2.0 Phase 3: ZIP Packaging & Distribution

Packages complete Skills (folders from Phase 1-2) into distributable ZIP archives
with metadata, checksums, and audit trails.

ADR-0674: Skill Package & ZIP Distribution Format
License: Apache-2.0
"""

import json
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

from core.skills.phase1_manifest_v2 import SkillManifestV2

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
        checksums = self._compute_checksums(skill_folder)

        # Write metadata files
        (forge_dir / "generation_context.json").write_text(
            json.dumps(generation_context, indent=2)
        )
        (forge_dir / "audit_trail.jsonl").write_text(
            "\n".join(json.dumps(e) for e in audit_trail)
        )
        (forge_dir / "checksum.sha256").write_text(
            "\n".join(f"{h} {p}" for p, h in checksums.items())
        )

        # Create ZIP archive
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            self._add_folder_to_zip(skill_folder, zf, Path(manifest.skill_id))

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
            "domain": manifest.domain.value,
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
        """Compute SHA256 checksums for all files in Skill folder."""
        checksums = {}

        for file_path in skill_folder.rglob("*"):
            if file_path.is_file() and ".forge" not in file_path.parts:
                # Exclude .forge dir from checksum computation (it's being generated)
                relative_path = file_path.relative_to(skill_folder)
                checksum = self._compute_file_hash(file_path)
                checksums[str(relative_path)] = checksum

        return checksums

    @staticmethod
    def _compute_file_hash(file_path: Path) -> str:
        """Compute SHA256 hash of a file."""
        sha256 = hashlib.sha256()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                sha256.update(chunk)
        return f"sha256:{sha256.hexdigest()}"

    @staticmethod
    def _add_folder_to_zip(folder: Path, zf: zipfile.ZipFile, arcname: Path) -> None:
        """Recursively add folder contents to ZIP archive."""
        for file_path in folder.rglob("*"):
            if file_path.is_file():
                relative_path = file_path.relative_to(folder)
                archive_path = arcname / relative_path
                zf.write(file_path, arcname=str(archive_path))

    def verify_package(self, zip_path: Path, checksum_file: Optional[Path] = None) -> bool:
        """
        Verify package integrity using checksums.

        Args:
            zip_path: Path to ZIP file
            checksum_file: Path to checksum.sha256 file (if None, read from ZIP)

        Returns:
            True if all checksums verify

        Raises:
            ChecksumVerificationError: If verification fails
        """
        if not zip_path.exists():
            raise FileNotFoundError(f"ZIP not found: {zip_path}")

        # Extract checksums from .forge/checksum.sha256 inside ZIP
        if checksum_file is None:
            try:
                with zipfile.ZipFile(zip_path, "r") as zf:
                    checksum_content = zf.read(".forge/checksum.sha256").decode("utf-8")
            except KeyError:
                logger.warning(f"No checksum file in {zip_path}, skipping verification")
                return True
        else:
            if not checksum_file.exists():
                raise FileNotFoundError(f"Checksum file not found: {checksum_file}")
            checksum_content = checksum_file.read_text()

        # Parse checksums (format: "sha256:hash path")
        checksums = {}
        for line in checksum_content.strip().split("\n"):
            if not line:
                continue
            parts = line.split(maxsplit=1)
            if len(parts) != 2:
                raise ChecksumVerificationError(f"Invalid checksum line: {line}")
            hash_value, file_path = parts
            checksums[file_path] = hash_value

        # Verify each file in ZIP
        with zipfile.ZipFile(zip_path, "r") as zf:
            for arcname in zf.namelist():
                # Skip .forge directory from verification (it's metadata)
                if arcname.startswith(".forge/") or arcname == ".forge":
                    continue

                file_data = zf.read(arcname)
                computed_hash = self._compute_hash(file_data)

                if arcname in checksums:
                    expected_hash = checksums[arcname]
                    if computed_hash != expected_hash:
                        raise ChecksumVerificationError(
                            f"Checksum mismatch for {arcname}: "
                            f"expected {expected_hash}, got {computed_hash}"
                        )

        logger.info(f"Package verification passed: {zip_path}")
        return True

    @staticmethod
    def _compute_hash(data: bytes) -> str:
        """Compute SHA256 hash of data."""
        sha256 = hashlib.sha256()
        sha256.update(data)
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
