"""Skill Installer — Phase 4: Installation & Registry Management (ADR-0680)."""

import asyncio, hashlib, json, tempfile, zipfile
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional

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

class SkillInstaller:
    """Atomic Skill installation with dependency resolution."""
    
    def __init__(self, corvin_home_path: Optional[str] = None):
        self.skills_dir = Path(corvin_home_path or "~/.corvin") / "skills_installed"
        self.registry_file = Path(corvin_home_path or "~/.corvin") / "skills_registry.json"
        self.registry_file.parent.mkdir(parents=True, exist_ok=True)

    async def install_skill(self, package_path: str, installed_by: str = "operator") -> InstalledSkillRecord:
        """Install Skill package (atomic: all-or-nothing)."""
        package_path = Path(package_path)
        if not package_path.exists():
            raise FileNotFoundError(f"Package not found: {package_path}")

        # Extract manifest
        manifest = await self._extract_manifest(package_path)
        skill_version_dir = self.skills_dir / manifest.skill_id / manifest.version

        if skill_version_dir.exists():
            raise ValueError(f"Skill {manifest.skill_id}@{manifest.version} already installed")

        # Check dependencies
        await self._check_dependencies(manifest)

        # Atomic unzip
        with tempfile.TemporaryDirectory() as temp_dir:
            try:
                await self._unzip_package(package_path, Path(temp_dir))
                skill_version_dir.parent.mkdir(parents=True, exist_ok=True)
                (Path(temp_dir) / manifest.skill_id).rename(skill_version_dir)
            except Exception as e:
                if skill_version_dir.exists():
                    import shutil
                    shutil.rmtree(skill_version_dir)
                raise RuntimeError(f"Installation failed: {e}") from e

        # Update registry
        record = InstalledSkillRecord(
            skill_id=manifest.skill_id,
            version=manifest.version,
            installed_at=datetime.utcnow().isoformat() + "Z",
            installed_from=str(package_path),
            installed_by=installed_by,
            boot_layer=manifest.boot_layer,
            dependencies=list(manifest.dependencies.items()),
            audit_trail_hash=hashlib.sha256(f"{manifest.skill_id}@{manifest.version}".encode()).hexdigest()
        )
        await self._update_registry(record)
        return record

    async def _extract_manifest(self, package_path: Path) -> SkillManifest:
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
                entry_point=manifest_dict["entry_point"]
            )

    async def _check_dependencies(self, manifest: SkillManifest) -> None:
        registry = await self._load_registry()
        installed = {rec["skill_id"] for rec in registry.get("installed_skills", [])}
        for dep_id in manifest.dependencies.keys():
            if dep_id not in installed:
                raise ValueError(f"Dependency missing: {dep_id}")

    async def _unzip_package(self, package_path: Path, extract_to: Path) -> None:
        with zipfile.ZipFile(package_path, "r") as zf:
            if zf.testzip():
                raise ValueError("Corrupted package")
            zf.extractall(extract_to)

    async def _load_registry(self) -> dict:
        if self.registry_file.exists():
            with open(self.registry_file, "r") as f:
                return json.load(f)
        return {"installed_skills": []}

    async def _update_registry(self, record: InstalledSkillRecord) -> None:
        registry = await self._load_registry()
        registry["installed_skills"].append(asdict(record))
        temp_file = self.registry_file.with_suffix(".tmp")
        with open(temp_file, "w") as f:
            json.dump(registry, f, indent=2)
        temp_file.replace(self.registry_file)
