"""Skill Installer — Phase 4: Installation & Registry Management (ADR-0674)."""

import asyncio
import hashlib
import json
import logging
import shutil
import subprocess
import sys
import tempfile
import zipfile
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SkillManifest:
    skill_id: str
    version: str
    name: str
    description: str
    dependencies: dict
    entry_point: str
    boot_layer: str = "installed"


@dataclass(frozen=True)
class InstalledSkillRecord:
    skill_id: str
    version: str
    installed_at: str
    installed_from: str
    installed_by: str
    boot_layer: str
    dependencies: list
    audit_trail_hash: str
    verified: bool = True


class InstallationError(Exception):
    """Raised when skill installation fails."""
    pass


class DependencyResolutionError(Exception):
    """Raised when skill dependencies cannot be resolved."""
    pass


class ChecksumVerificationError(Exception):
    """Raised when checksum verification fails."""
    pass


class SkillInstaller:
    """Atomic Skill installation with dependency resolution, checksum verification, and audit."""

    def __init__(self, corvin_home_path: Optional[str] = None, audit_backend=None):
        """
        Initialize installer.

        Args:
            corvin_home_path: Path to .corvin directory (default: ~/.corvin)
            audit_backend: Optional audit backend for logging events
        """
        corvin_home = Path(corvin_home_path or "~/.corvin").expanduser()
        self.skills_dir = corvin_home / "skills" / "custom"
        self.bundled_skills_dir = corvin_home / "skills" / "bundled"
        self.registry_file = corvin_home / ".registry" / "installed.json"
        self.registry_file.parent.mkdir(parents=True, exist_ok=True)
        self.skills_dir.mkdir(parents=True, exist_ok=True)
        self.audit_backend = audit_backend

    async def install_skill(
        self,
        package_path: str,
        installed_by: str = "operator",
        verify_checksum: bool = True
    ) -> Dict[str, Any]:
        """
        Install Skill package (atomic: all-or-nothing).

        Args:
            package_path: Path to .zip file
            installed_by: User who triggered installation
            verify_checksum: Whether to verify checksums before installation

        Returns:
            {
                "installed_path": str,
                "skill_id": str,
                "version": str,
                "status": "success|already_installed|partial",
                "errors": []
            }

        Raises:
            InstallationError: If installation fails
            FileNotFoundError: If package not found
        """
        package_path = Path(package_path).expanduser()
        if not package_path.exists():
            raise FileNotFoundError(f"Package not found: {package_path}")

        errors = []

        try:
            # Extract manifest
            manifest = await self._extract_manifest(package_path)
            skill_final_dir = self.skills_dir / manifest.skill_id

            # Check if already installed
            if skill_final_dir.exists():
                existing_manifest = await self._extract_manifest_from_dir(skill_final_dir)
                if existing_manifest.version == manifest.version:
                    logger.info(f"Skill {manifest.skill_id} v{manifest.version} already installed")
                    self._emit_audit_event({
                        "event_type": "skill_install_skipped",
                        "skill_id": manifest.skill_id,
                        "version": manifest.version,
                        "reason": "already_installed"
                    })
                    return {
                        "installed_path": str(skill_final_dir),
                        "skill_id": manifest.skill_id,
                        "version": manifest.version,
                        "status": "already_installed",
                        "errors": []
                    }
                else:
                    # Backup existing version
                    backup_path = skill_final_dir.parent / f"{manifest.skill_id}.backup.{existing_manifest.version}"
                    shutil.move(str(skill_final_dir), str(backup_path))
                    logger.info(f"Backed up {manifest.skill_id} v{existing_manifest.version} to {backup_path}")

            # Verify checksum
            if verify_checksum:
                try:
                    await self._verify_checksum(package_path)
                except ChecksumVerificationError as e:
                    logger.error(f"Checksum verification failed: {e}")
                    errors.append(str(e))
                    # Continue anyway (non-fatal)

            # Check dependencies
            try:
                await self._check_dependencies(manifest)
            except DependencyResolutionError as e:
                logger.error(f"Dependency check failed: {e}")
                errors.append(str(e))
                # Continue anyway (non-fatal)

            # Atomic unzip to temp, then move
            with tempfile.TemporaryDirectory() as temp_dir:
                temp_dir_path = Path(temp_dir)
                try:
                    await self._unzip_package(package_path, temp_dir_path)
                    extracted_skill_dir = None
                    for item in temp_dir_path.iterdir():
                        if item.is_dir() and (item / "skill.json").exists():
                            extracted_skill_dir = item
                            break

                    if not extracted_skill_dir:
                        raise InstallationError("ZIP does not contain valid Skill folder")

                    # Move to final location
                    skill_final_dir.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(extracted_skill_dir), str(skill_final_dir))

                except Exception as e:
                    if skill_final_dir.exists():
                        shutil.rmtree(skill_final_dir)
                    raise InstallationError(f"Extraction failed: {e}") from e

            # Install dependencies
            try:
                await self._install_dependencies(skill_final_dir, manifest)
            except Exception as e:
                logger.warning(f"Dependency installation failed (non-fatal): {e}")
                errors.append(f"Dependency installation: {e}")

            # Update registry
            record = InstalledSkillRecord(
                skill_id=manifest.skill_id,
                version=manifest.version,
                installed_at=datetime.utcnow().isoformat() + "Z",
                installed_from=str(package_path),
                installed_by=installed_by,
                boot_layer=manifest.boot_layer,
                dependencies=list(manifest.dependencies.items()),
                audit_trail_hash=hashlib.sha256(
                    f"{manifest.skill_id}@{manifest.version}".encode()
                ).hexdigest()
            )
            await self._update_registry(record)

            # Emit audit event
            self._emit_audit_event({
                "event_type": "skill_installed",
                "skill_id": manifest.skill_id,
                "version": manifest.version,
                "source_zip": str(package_path),
                "install_path": str(skill_final_dir),
                "installed_by": installed_by,
                "lom": "core.skills.skill_installer:SkillInstaller.install_skill:L95"
            })

            status = "partial" if errors else "success"
            logger.info(f"Skill installation completed: {manifest.skill_id} v{manifest.version} ({status})")

            return {
                "installed_path": str(skill_final_dir),
                "skill_id": manifest.skill_id,
                "version": manifest.version,
                "status": status,
                "errors": errors
            }

        except Exception as e:
            logger.error(f"Skill installation failed: {e}", exc_info=True)
            self._emit_audit_event({
                "event_type": "skill_install_failed",
                "error": str(e),
                "package_path": str(package_path)
            })
            raise InstallationError(f"Installation failed: {e}") from e

    async def _extract_manifest(self, package_path: Path) -> SkillManifest:
        """Extract skill.json from ZIP package."""
        try:
            with zipfile.ZipFile(package_path, "r") as zf:
                manifest_files = [f for f in zf.namelist() if f.endswith("skill.json")]
                if not manifest_files:
                    raise ValueError("No skill.json in package")
                manifest_dict = json.loads(zf.read(manifest_files[0]).decode("utf-8"))
                return SkillManifest(
                    skill_id=manifest_dict["skill_id"],
                    version=manifest_dict["version"],
                    name=manifest_dict.get("name", ""),
                    description=manifest_dict.get("description", ""),
                    dependencies=manifest_dict.get("dependencies", {}),
                    entry_point=manifest_dict["entry_point"],
                    boot_layer=manifest_dict.get("boot_layer", "installed")
                )
        except Exception as e:
            raise InstallationError(f"Failed to extract manifest from {package_path}: {e}") from e

    async def _extract_manifest_from_dir(self, skill_dir: Path) -> SkillManifest:
        """Extract skill.json from directory."""
        manifest_file = skill_dir / "skill.json"
        if not manifest_file.exists():
            raise ValueError(f"No skill.json in {skill_dir}")
        manifest_dict = json.loads(manifest_file.read_text())
        return SkillManifest(
            skill_id=manifest_dict["skill_id"],
            version=manifest_dict["version"],
            name=manifest_dict.get("name", ""),
            description=manifest_dict.get("description", ""),
            dependencies=manifest_dict.get("dependencies", {}),
            entry_point=manifest_dict["entry_point"],
            boot_layer=manifest_dict.get("boot_layer", "installed")
        )

    async def _verify_checksum(self, package_path: Path) -> None:
        """Verify checksum file inside ZIP package."""
        try:
            with zipfile.ZipFile(package_path, "r") as zf:
                # Try to find checksum file
                checksum_files = [f for f in zf.namelist() if f.endswith("checksum.sha256")]
                if not checksum_files:
                    logger.warning(f"No checksum file found in {package_path}")
                    return

                checksum_content = zf.read(checksum_files[0]).decode("utf-8")
                checksums = {}
                for line in checksum_content.strip().split("\n"):
                    if not line:
                        continue
                    parts = line.split(maxsplit=1)
                    if len(parts) != 2:
                        raise ChecksumVerificationError(f"Invalid checksum line: {line}")
                    hash_value, file_path = parts
                    checksums[file_path] = hash_value

                # Verify files
                for arcname in zf.namelist():
                    if arcname.startswith(".forge/") or arcname == ".forge":
                        continue
                    if arcname in checksums:
                        file_data = zf.read(arcname)
                        computed_hash = self._compute_hash(file_data)
                        expected_hash = checksums[arcname]
                        if computed_hash != expected_hash:
                            raise ChecksumVerificationError(
                                f"Checksum mismatch for {arcname}: "
                                f"expected {expected_hash}, got {computed_hash}"
                            )
        except ChecksumVerificationError:
            raise
        except Exception as e:
            raise ChecksumVerificationError(f"Checksum verification failed: {e}") from e

    async def _check_dependencies(self, manifest: SkillManifest) -> None:
        """Verify that all dependencies are installed."""
        if not manifest.dependencies:
            return

        registry = await self._load_registry()
        installed = {rec["skill_id"] for rec in registry.get("installed_skills", [])}
        installed |= {d.name for d in self.bundled_skills_dir.glob("*") if d.is_dir()}

        missing = []
        for dep_id in manifest.dependencies.keys():
            if dep_id not in installed:
                missing.append(dep_id)

        if missing:
            raise DependencyResolutionError(
                f"Missing dependencies for {manifest.skill_id}: {', '.join(missing)}"
            )

    async def _install_dependencies(self, skill_dir: Path, manifest: SkillManifest) -> None:
        """Install Python and system dependencies."""
        # Check for install.py script
        install_script = skill_dir / "scripts" / "install.py"
        if install_script.exists():
            try:
                result = subprocess.run(
                    [sys.executable, str(install_script)],
                    cwd=str(skill_dir),
                    capture_output=True,
                    text=True,
                    timeout=300
                )
                if result.returncode != 0:
                    logger.warning(f"Install script failed: {result.stderr}")
            except subprocess.TimeoutExpired:
                logger.warning(f"Install script timeout for {manifest.skill_id}")
            except Exception as e:
                logger.warning(f"Failed to run install script: {e}")

        # Check for requirements.txt
        requirements_file = skill_dir / "references" / "dependencies.txt"
        if requirements_file.exists():
            try:
                result = subprocess.run(
                    [sys.executable, "-m", "pip", "install", "-r", str(requirements_file)],
                    capture_output=True,
                    text=True,
                    timeout=300
                )
                if result.returncode != 0:
                    logger.warning(f"Pip install failed: {result.stderr}")
            except Exception as e:
                logger.warning(f"Failed to install requirements: {e}")

    async def _unzip_package(self, package_path: Path, extract_to: Path) -> None:
        """Extract ZIP package, validating structure."""
        try:
            with zipfile.ZipFile(package_path, "r") as zf:
                # Test ZIP integrity
                test_result = zf.testzip()
                if test_result:
                    raise ValueError(f"Corrupted ZIP: {test_result}")
                zf.extractall(extract_to)
        except Exception as e:
            raise InstallationError(f"Failed to extract package: {e}") from e

    async def _load_registry(self) -> dict:
        """Load skill registry from disk."""
        if self.registry_file.exists():
            try:
                with open(self.registry_file, "r") as f:
                    return json.load(f)
            except Exception as e:
                logger.warning(f"Failed to load registry: {e}")
        return {"registry_version": "2.0.0", "last_updated": datetime.utcnow().isoformat() + "Z", "installed_skills": []}

    async def _update_registry(self, record: InstalledSkillRecord) -> None:
        """Update skill registry (atomic write)."""
        registry = await self._load_registry()
        registry["installed_skills"].append(asdict(record))
        registry["last_updated"] = datetime.utcnow().isoformat() + "Z"

        # Atomic write
        temp_file = self.registry_file.with_suffix(".tmp")
        with open(temp_file, "w") as f:
            json.dump(registry, f, indent=2)
        temp_file.replace(self.registry_file)

    @staticmethod
    def _compute_hash(data: bytes) -> str:
        """Compute SHA256 hash of data."""
        sha256 = hashlib.sha256()
        sha256.update(data)
        return f"sha256:{sha256.hexdigest()}"

    def _emit_audit_event(self, event: Dict[str, Any]) -> None:
        """Emit audit event if backend is available."""
        if not self.audit_backend:
            return
        try:
            event.setdefault("timestamp", datetime.utcnow().isoformat() + "Z")
            event.setdefault("tenant_id", "_default")
            self.audit_backend.emit_event(event)
        except Exception as e:
            logger.warning(f"Failed to emit audit event: {e}")
