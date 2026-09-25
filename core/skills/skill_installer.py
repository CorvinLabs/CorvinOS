"""SkillInstaller — Phase 4: Atomic Installation + Dependency Resolution (ADR-0680)"""

from pathlib import Path
from typing import Dict, List, Tuple
import json, zipfile, hashlib, shutil


class SkillInstaller:
    """Atomic skill installation with dependency resolution."""
    
    def __init__(self, install_root: Path = None):
        if install_root is None:
            install_root = Path.home() / ".corvin" / "skills_installed"
        self.install_root = install_root
        self.registry_path = self.install_root / "skills_registry.json"
        self.install_root.mkdir(parents=True, exist_ok=True)
        if not self.registry_path.exists():
            self._write_registry({})
    
    def install_skill(self, zip_path: Path, zip_hash: str, metadata: Dict) -> Tuple[bool, str]:
        """Atomically install skill from ZIP."""
        try:
            if not self._verify_checksum(zip_path, zip_hash):
                return False, f"Checksum mismatch"
            
            skill_id, version = metadata.get("skill_id"), metadata.get("version")
            if not skill_id or not version:
                return False, "Missing skill_id or version"
            
            registry = self._load_registry()
            if skill_id in registry and any(s["version"] == version for s in registry[skill_id]):
                return False, f"{skill_id}@{version} already installed"
            
            unmet = self._resolve_dependencies(metadata.get("dependencies", []), registry)
            if unmet:
                return False, f"Unmet dependencies: {', '.join(unmet)}"
            
            target_dir = self.install_root / skill_id / version
            self._atomic_unzip(zip_path, target_dir)
            
            if skill_id not in registry:
                registry[skill_id] = []
            registry[skill_id].append({
                "version": version, "boot_layer": "installed",
                "dependencies": metadata.get("dependencies", []), "verified": True
            })
            self._write_registry(registry)
            return True, f"✅ {skill_id}@{version} installed"
        except Exception as e:
            return False, f"Error: {str(e)}"
    
    def _verify_checksum(self, zip_path: Path, expected_hash: str) -> bool:
        if not zip_path.exists(): return False
        sha256 = hashlib.sha256()
        with open(zip_path, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                sha256.update(chunk)
        return sha256.hexdigest() == expected_hash
    
    def _resolve_dependencies(self, deps: List[Dict], registry: Dict) -> List[str]:
        unmet = []
        for dep in deps:
            skill_id = dep.get("skill_id")
            if skill_id not in registry or not registry[skill_id]:
                unmet.append(f"{skill_id}({dep.get('version', '*')})")
        return unmet
    
    def _atomic_unzip(self, zip_path: Path, target_dir: Path) -> None:
        temp_dir = target_dir.parent / f".{target_dir.name}_tmp"
        temp_dir.mkdir(parents=True, exist_ok=True)
        try:
            with zipfile.ZipFile(zip_path, 'r') as zf:
                zf.extractall(temp_dir)
            if target_dir.exists():
                old_dir = target_dir.parent / f".{target_dir.name}_prev"
                if old_dir.exists(): shutil.rmtree(old_dir)
                target_dir.rename(old_dir)
            temp_dir.rename(target_dir)
        except Exception as e:
            if temp_dir.exists(): shutil.rmtree(temp_dir, ignore_errors=True)
            raise
    
    def _load_registry(self) -> Dict:
        if not self.registry_path.exists(): return {}
        with open(self.registry_path, "r") as f:
            return json.load(f)
    
    def _write_registry(self, registry: Dict) -> None:
        temp_path = self.registry_path.parent / f".{self.registry_path.name}.tmp"
        with open(temp_path, "w") as f:
            json.dump(registry, f, indent=2)
        temp_path.replace(self.registry_path)
