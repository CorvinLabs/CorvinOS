"""SkillInstaller — Phase 4: Atomic Installation + Dependency Resolution (ADR-0680)"""

from pathlib import Path
from typing import Dict, List, Tuple
import json, re, zipfile, hashlib, shutil

# skill_id / version become path components under install_root. They arrive
# from callers that take them straight off an HTTP form, so anything that is
# not a single, plain path segment ("../..", "a/b", ".", "") is refused before
# a path is built from it.
_SEGMENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,127}$")


def _is_safe_segment(value) -> bool:
    return isinstance(value, str) and bool(_SEGMENT_RE.match(value)) and ".." not in value


class InstallationError(Exception):
    """Raised when skill installation fails.

    Kept for callers that import it (``routes/skill_forge_distribution_routes``):
    removing it made ``corvin_console.app`` fail to import, which takes the
    whole console down on the next gateway restart.
    """


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
            if not _is_safe_segment(skill_id) or not _is_safe_segment(version):
                return False, "Invalid skill_id or version"
            
            registry = self._load_registry()
            if skill_id in registry and any(s["version"] == version for s in registry[skill_id]):
                return False, f"{skill_id}@{version} already installed"
            
            unmet = self._resolve_dependencies(metadata.get("dependencies", []), registry)
            if unmet:
                return False, f"Unmet dependencies: {', '.join(unmet)}"
            
            manifest_status = self._check_package_manifest(zip_path, skill_id, version)
            if manifest_status.startswith("refused:"):
                return False, f"Manifest check failed: {manifest_status[len('refused:'):]}"

            target_dir = self._target_dir(skill_id, version)
            self._atomic_unzip(zip_path, target_dir)

            if skill_id not in registry:
                registry[skill_id] = []
            registry[skill_id].append({
                "version": version, "boot_layer": "installed",
                "dependencies": metadata.get("dependencies", []),
                # Nothing here proves WHO built the package: the checksum is supplied by
                # the same caller as the ZIP, and no package carries a signature. A flag
                # that read True after only that check told the Installed tab "verified"
                # for any ZIP; it is False until a real authenticity check exists.
                "verified": False,
                "verification": {"checksum": "caller_supplied", "manifest": manifest_status,
                                 "signature": "absent"},
            })
            self._write_registry(registry)
            return True, f"✅ {skill_id}@{version} installed"
        except Exception as e:
            return False, f"Error: {str(e)}"
    
    def uninstall_skill(self, skill_id: str, version: str) -> Tuple[bool, str]:
        """Remove one installed version (files + registry entry)."""
        try:
            if not _is_safe_segment(skill_id) or not _is_safe_segment(version):
                return False, "Invalid skill_id or version"
            registry = self._load_registry()
            versions = registry.get(skill_id) or []
            remaining = [s for s in versions if s.get("version") != version]
            if len(remaining) == len(versions):
                return False, f"{skill_id}@{version} not installed"
            target_dir = self._target_dir(skill_id, version)
            if target_dir.exists():
                shutil.rmtree(target_dir)
            if remaining:
                registry[skill_id] = remaining
            else:
                registry.pop(skill_id, None)
                skill_dir = target_dir.parent
                if skill_dir.exists() and not any(skill_dir.iterdir()):
                    skill_dir.rmdir()
            self._write_registry(registry)
            return True, f"{skill_id}@{version} uninstalled"
        except Exception as e:
            return False, f"Error: {str(e)}"

    def _target_dir(self, skill_id: str, version: str) -> Path:
        """Install dir for one version; refuses anything outside install_root."""
        root = self.install_root.resolve()
        target = (root / skill_id / version).resolve()
        if target.parent.parent != root:
            raise InstallationError(f"Install path escapes install root: {skill_id}@{version}")
        return target

    def _verify_checksum(self, zip_path: Path, expected_hash: str) -> bool:
        """Verify ZIP file hash (fixes C3: Hash Comparison)."""
        if not zip_path.exists():
            return False

        # Parse expected_hash (format: "sha256:<hex>" or just "<hex>")
        expected_hex = expected_hash.split(":")[-1] if ":" in expected_hash else expected_hash

        sha256 = hashlib.sha256()
        with open(zip_path, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                sha256.update(chunk)

        actual_hex = sha256.hexdigest()
        if actual_hex != expected_hex:
            return False

        return True
    
    _MANIFEST_MAX_BYTES = 1024 * 1024

    def _check_package_manifest(self, zip_path: Path, skill_id: str, version: str) -> str:
        """Compare the package's own ``skill.json`` with the id/version it is installed as.

        Returns ``"consistent"``, ``"absent"`` (no manifest; allowed, the packager always
        writes one but older bundles may not) or ``"refused:<reason>"``. Without this a
        ZIP could be installed under any id/version the caller names, whatever it holds.
        """
        with zipfile.ZipFile(zip_path, "r") as zf:
            names = [i for i in zf.infolist()
                     if not i.is_dir() and i.filename.split("/")[-1] == "skill.json"
                     and i.filename.count("/") <= 1]
            if not names:
                return "absent"
            if len(names) > 1:
                return "refused:more than one skill.json"
            info = names[0]
            if info.file_size > self._MANIFEST_MAX_BYTES:
                return "refused:skill.json too large"
            try:
                manifest = json.loads(zf.read(info).decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                return "refused:skill.json is not valid JSON"
        if not isinstance(manifest, dict):
            return "refused:skill.json is not an object"
        named_id, named_version = manifest.get("skill_id"), manifest.get("version")
        # Only a manifest that NAMES something else is a contradiction. One that names
        # nothing (or only one of the two) cannot be compared; it is recorded as such,
        # never as "consistent".
        if named_id not in (None, skill_id) or named_version not in (None, version):
            return (f"refused:skill.json says {named_id!r}@{named_version!r}, "
                    f"installing as {skill_id}@{version}")
        return "consistent" if (named_id is not None and named_version is not None) else "incomplete"

    def _resolve_dependencies(self, deps: List[Dict], registry: Dict) -> List[str]:
        unmet = []
        for dep in deps:
            skill_id = dep.get("skill_id")
            if skill_id not in registry or not registry[skill_id]:
                unmet.append(f"{skill_id}({dep.get('version', '*')})")
        return unmet
    
    def _atomic_unzip(self, zip_path: Path, target_dir: Path) -> None:
        """Extract ZIP atomically (fixes C1: Path Traversal + C2: ZIP Bomb)."""
        temp_dir = target_dir.parent / f".{target_dir.name}_tmp"
        # A crash mid-extract leaves this dir behind; extracting into it would
        # merge the stale files into the new install.
        if temp_dir.exists():
            shutil.rmtree(temp_dir)
        temp_dir.mkdir(parents=True)
        try:
            with zipfile.ZipFile(zip_path, 'r') as zf:
                # FIX C1: Validate all ZIP entries (path traversal)
                self._validate_zip_entries(zf, temp_dir)

                # FIX C2: Validate uncompressed size (ZIP bomb)
                self._validate_zip_size(zf)

                # Safe extraction (after validation)
                zf.extractall(temp_dir)

            if target_dir.exists():
                old_dir = target_dir.parent / f".{target_dir.name}_prev"
                if old_dir.exists(): shutil.rmtree(old_dir)
                target_dir.rename(old_dir)
            temp_dir.rename(target_dir)
        except Exception as e:
            if temp_dir.exists(): shutil.rmtree(temp_dir, ignore_errors=True)
            raise

    def _validate_zip_entries(self, zf: zipfile.ZipFile, target_dir: Path) -> None:
        """Validate ZIP entries don't escape target_dir (C1: Path Traversal)."""
        target_dir = target_dir.resolve()
        for info in zf.infolist():
            # Check for path traversal attempts
            if ".." in info.filename or info.filename.startswith("/"):
                raise InstallationError(f"Unsafe path in ZIP: {info.filename}")

            # Check resolved path stays within target
            entry_path = (target_dir / info.filename).resolve()
            if not str(entry_path).startswith(str(target_dir)):
                raise InstallationError(f"Path traversal detected: {info.filename}")

    def _validate_zip_size(self, zf: zipfile.ZipFile, max_size: int = 100*1024*1024) -> None:
        """Validate uncompressed size (C2: ZIP Bomb)."""
        total_size = sum(info.file_size for info in zf.infolist())
        if total_size > max_size:
            raise InstallationError(f"ZIP too large: {total_size} > {max_size}")
    
    def _load_registry(self) -> Dict:
        if not self.registry_path.exists(): return {}
        with open(self.registry_path, "r") as f:
            return json.load(f)
    
    def _write_registry(self, registry: Dict) -> None:
        """Write registry atomically (fixes H2: File Permissions + H1: Registry Race)."""
        temp_path = self.registry_path.parent / f".{self.registry_path.name}.tmp"
        with open(temp_path, "w") as f:
            json.dump(registry, f, indent=2)

        # FIX H2: Set permissions before finalizing (owner read+write only)
        Path(temp_path).chmod(0o600)

        # FIX H1: Atomic rename (almost atomic on most filesystems)
        temp_path.replace(self.registry_path)
