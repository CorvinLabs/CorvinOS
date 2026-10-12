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


class PackagingError(ValueError):
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

        # Create .forge metadata directory (if not already present). Never through a link: the
        # packager used to write (and chmod) its metadata into whatever .forge or one of its files
        # pointed at, outside the skill (review R2-AP-01).
        forge_dir = skill_folder / ".forge"
        if forge_dir.is_symlink() or (forge_dir.exists() and not forge_dir.is_dir()):
            raise PackagingError(".forge must be a real directory inside the skill folder")
        forge_dir.mkdir(exist_ok=True)

        # Generate metadata
        generation_context = self._create_generation_context(skill_folder, manifest)
        audit_trail = self._create_audit_trail(skill_folder, manifest)

        # Write metadata files BEFORE hashing: the checksum list - and so the signature - must
        # cover them (ADR-2252 review, PKG-6). Written via a fresh temp file + replace, which
        # replaces a planted link instead of following it.
        self._write_private(forge_dir, "generation_context.json", json.dumps(generation_context, indent=2))
        self._write_private(forge_dir, "audit_trail.jsonl", "\n".join(json.dumps(e) for e in audit_trail))

        payload = self._collect_payload(skill_folder)
        checksums = {rel: self._compute_hash(data) for rel, data in payload.items()}
        checksum_text = "\n".join(f"{h} {p}" for p, h in checksums.items())
        checksum_path = self._write_private(forge_dir, "checksum.sha256", checksum_text)

        # Create the ZIP from EXACTLY the bytes that were hashed (one read per file - no
        # hash-then-reopen window), plus the checksum list and the signature.
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            arc = manifest.skill_id
            for rel, data in payload.items():
                zf.writestr(f"{arc}/{rel}", data)
            zf.writestr(f"{arc}/{package_signing.CHECKSUM_REL}", checksum_text)
            # Signed only when this install's operator has generated a key (ADR-2252);
            # otherwise the package is unsigned and the installer will say so.
            signature = self._sign_payload(payload.get(package_signing.MANIFEST_NAME, b""),
                                           checksum_text.encode("utf-8"))
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
    def _sign_payload(manifest_raw: bytes, checksum_raw: bytes) -> Optional[str]:
        """Signature over manifest + checksum list (the bytes shipped), or None without a usable key."""
        key = package_signing.signing_key()
        if key is None:
            return None
        try:
            return package_signing.sign(manifest_raw, checksum_raw, key)
        except Exception as exc:  # noqa: BLE001 — never lose the package over a signing fault
            logger.warning("package left unsigned: %s", type(exc).__name__)
            return None

    @staticmethod
    def _write_private(directory: Path, name: str, text: str) -> Path:
        import tempfile

        fd, tmp = tempfile.mkstemp(prefix=f".{name}.", dir=str(directory))
        try:
            os.fchmod(fd, 0o600) if hasattr(os, "fchmod") else None
            with os.fdopen(fd, "w") as f:
                f.write(text)
            os.replace(tmp, directory / name)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        return directory / name

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
        """SHA-256 per shipped file (see :meth:`_collect_payload`)."""
        return {rel: self._compute_hash(data) for rel, data in self._collect_payload(skill_folder).items()}

    def _collect_payload(self, skill_folder: Path) -> Dict[str, bytes]:
        """Every file the package ships, read ONCE, keyed by POSIX relative path.

        * symlinks are never followed or listed (C4); each file is opened with O_NOFOLLOW;
        * a file with more than one hard link is refused - its content may live outside the
          folder (review R2-AP-02);
        * the checksum list itself and a stray root ``skill.sig`` are not part of the payload
          (the packager writes its own; shipping both made a package its installer refuses);
        * two names that collide on a case-insensitive/normalising file system are refused.
        Keys are ``as_posix()``: ``str()`` of a Windows path uses backslashes (PKG-5).
        """
        import stat as _stat

        payload: Dict[str, bytes] = {}
        seen: Dict[str, str] = {}
        root = skill_folder.resolve()
        nofollow = getattr(os, "O_NOFOLLOW", 0)
        for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
            dirnames[:] = sorted(d for d in dirnames if not (Path(dirpath) / d).is_symlink())
            for name in sorted(filenames):
                file_path = Path(dirpath) / name
                rel = file_path.relative_to(root).as_posix()
                if rel in (package_signing.CHECKSUM_REL, package_signing.SIGNATURE_NAME):
                    continue
                if file_path.is_symlink():
                    logger.warning(f"Skipping symlink (not allowed): {file_path}")
                    continue
                try:
                    fd = os.open(str(file_path), os.O_RDONLY | nofollow)
                except OSError as exc:
                    raise PackagingError(f"cannot read {rel}: {type(exc).__name__}") from exc
                try:
                    st = os.fstat(fd)
                    if not _stat.S_ISREG(st.st_mode):
                        continue
                    if st.st_nlink != 1:
                        raise PackagingError(f"{rel} has {st.st_nlink} hard links; refusing to ship it")
                    with os.fdopen(fd, "rb") as f:
                        fd = -1
                        data = f.read()
                finally:
                    if fd != -1:
                        os.close(fd)
                key = package_signing.collision_key(rel)
                if key in seen:
                    raise PackagingError(f"{rel} and {seen[key]} name the same file on Windows/macOS")
                seen[key] = rel
                payload[rel] = data
        return payload

    @staticmethod
    def _compute_hash(data: bytes) -> str:
        """SHA-256 of bytes, ``sha256:<hex>``."""
        return f"sha256:{hashlib.sha256(data).hexdigest()}"

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
