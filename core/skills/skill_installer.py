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

            signature_status = self._check_package_signature(zip_path)
            if signature_status.startswith("refused:"):
                return False, f"Signature check failed: {signature_status[len('refused:'):]}"
            # verified means exactly this: a trusted operator key signed the manifest plus the
            # checksum list, every file in the ZIP matches that list, and the manifest names
            # the skill it is installed as. Anything less is recorded, never called verified.
            verified = signature_status == "valid" and manifest_status == "consistent"

            target_dir = self._target_dir(skill_id, version)
            self._atomic_unzip(zip_path, target_dir)

            if skill_id not in registry:
                registry[skill_id] = []
            registry[skill_id].append({
                "version": version, "boot_layer": "installed",
                "dependencies": metadata.get("dependencies", []),
                # The ZIP checksum comes from the same caller as the ZIP, so it proves nothing
                # about who built the package. Only a valid operator signature does (ADR-2252);
                # a flag that read True after the checksum alone told the Installed tab
                # "verified" for any ZIP.
                "verified": verified,
                "verification": {"checksum": "caller_supplied", "manifest": manifest_status,
                                 "signature": signature_status},
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

    _SIGNATURE_MAX_BYTES = 64 * 1024

    def _check_package_signature(self, zip_path: Path) -> str:
        """``absent`` | ``unverifiable`` | ``valid`` | ``refused:<reason>`` (ADR-2252).

        ``absent``: the package carries no ``skill.sig``. ``unverifiable``: it does, but this
        install has no operator key to check it against (never read from the ZIP itself).
        ``refused``: a signature that does not verify, or a file the signature does not cover.
        """
        from core.skills import package_signing as ps  # noqa: PLC0415

        with zipfile.ZipFile(zip_path, "r") as zf:
            infos = [i for i in zf.infolist() if not i.is_dir()]
            manifests = [i for i in infos if i.filename.split("/")[-1] == ps.MANIFEST_NAME
                         and i.filename.count("/") <= 1]
            if len(manifests) != 1:
                return "absent" if not any(i.filename.endswith(ps.SIGNATURE_NAME) for i in infos) \
                    else "refused:signature without exactly one skill.json"
            pref = manifests[0].filename[: -len(ps.MANIFEST_NAME)]
            sig_info = next((i for i in infos if i.filename == pref + ps.SIGNATURE_NAME), None)
            if sig_info is None:
                return "absent"
            keys = ps.trusted_public_keys()
            if not keys:
                return "unverifiable"
            self._validate_zip_size(zf)
            if sig_info.file_size > self._SIGNATURE_MAX_BYTES:
                return "refused:signature file too large"
            by_name = {i.filename: i for i in infos}
            checksum_info = by_name.get(pref + ps.CHECKSUM_REL)
            if checksum_info is None:
                return "refused:signed package has no checksum list"
            manifest_raw = zf.read(manifests[0])
            checksum_raw = zf.read(checksum_info)
            signature = zf.read(sig_info).decode("ascii", errors="replace")
            if not ps.verify(manifest_raw, checksum_raw, signature, keys):
                return "refused:signature does not verify against any trusted operator key"
            listed = ps.parse_checksums(checksum_raw)
            for rel, digest in listed.items():
                info = by_name.get(pref + rel)
                if info is None:
                    return f"refused:listed file missing: {rel}"
                if ps.sha256_tag(zf.read(info)) != digest:
                    return f"refused:file changed after signing: {rel}"
            for info in infos:
                rel = info.filename[len(pref):]
                if rel == ps.SIGNATURE_NAME or rel in ps.ALLOWED_FORGE_ENTRIES or rel in listed:
                    continue
                return f"refused:file not covered by the signature: {rel}"
        return "valid"

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
