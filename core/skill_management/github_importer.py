"""Import skills from GitHub tarball."""

import json
import shutil
import tarfile
from pathlib import Path, PurePosixPath
from typing import List, Optional, Dict
from dataclasses import dataclass
from enum import Enum

from core.skill_management.tenant_validator import validate_tenant_id  # TENANT-002


class UnsafeTarMember(ValueError):
    """A tarball member would escape the extraction directory or is not a plain file/dir.

    Raised BEFORE any extraction happens (D-F1 tar-slip fix): the importer
    never partially extracts a tarball that contains an unsafe member.
    """


def check_tar_members(tar: tarfile.TarFile) -> None:
    """Reject tar members that could escape or alias outside the target dir.

    Fail-closed pre-extraction gate (D-F1). Rejects, for every member:
    - absolute paths (``/etc/passwd``) and drive-rooted paths
    - any ``..`` path component
    - symlinks and hardlinks (their targets are outside our control)
    - device nodes and FIFOs (never legitimate skill content)

    ``tar.extractall(filter="data")`` is applied as a second, independent
    layer; this explicit check exists so a rejection is reported as a
    clear ``UnsafeTarMember`` and so nothing is extracted at all.
    """
    for member in tar.getmembers():
        name = member.name
        pure = PurePosixPath(name)
        if pure.is_absolute() or name.startswith(("/", "\\")) or (len(name) > 1 and name[1] == ":"):
            raise UnsafeTarMember(f"absolute path in tarball: {name!r}")
        if ".." in pure.parts:
            raise UnsafeTarMember(f"path traversal in tarball: {name!r}")
        if member.issym() or member.islnk():
            raise UnsafeTarMember(f"link member in tarball (not allowed): {name!r}")
        if member.isdev() or member.isfifo():
            raise UnsafeTarMember(f"device/fifo member in tarball (not allowed): {name!r}")
        if not (member.isfile() or member.isdir()):
            raise UnsafeTarMember(f"unsupported member type in tarball: {name!r}")


class ConflictResolution(Enum):
    """How to resolve skill conflicts during import."""
    OPERATOR_WINS = "operator_wins"  # Keep local version
    GITHUB_WINS = "github_wins"       # Use imported version
    MANUAL = "manual"                  # Prompt operator


@dataclass
class SkillConflict:
    """A skill that exists both locally and in import."""
    skill_id: str
    local_version: str
    imported_version: str
    local_modified: str
    imported_modified: str


@dataclass
class ImportResult:
    """Result of importing skills."""
    success: bool
    imported_skills: List[str]
    imported_tools: List[str]
    conflicts: List[SkillConflict]
    conflict_resolution: str  # How conflicts were resolved
    error: Optional[str] = None


class GitHubImporter:
    """Import skills from GitHub tarball."""

    def __init__(self, tenant_id: str = "_default", base_path: Optional[Path] = None):
        # TENANT-002 FIX: Validate tenant_id before using in path construction
        validate_tenant_id(tenant_id)

        self.tenant_id = tenant_id
        # ``base_path`` lets tests (and embedders) target a tmp root instead of
        # the live ~/.corvin tree. Default is unchanged.
        self.base_path = (
            Path(base_path) if base_path is not None
            else Path.home() / ".corvin" / "tenants" / tenant_id
        )

    def import_from_tarball(
        self,
        tarball_path: Path,
        conflict_resolution: ConflictResolution = ConflictResolution.OPERATOR_WINS,
        dry_run: bool = False
    ) -> ImportResult:
        """Import skills from tarball."""
        imported_skills = []
        imported_tools = []
        conflicts = []

        # Step 1: Extract tarball to temp
        temp_dir = self.base_path / "imports" / f"temp_{tarball_path.stem}"
        temp_dir.mkdir(parents=True, exist_ok=True)

        try:
            with tarfile.open(tarball_path, "r:gz") as tar:
                # D-F1 tar-slip fix: explicit member gate (fail-closed, nothing
                # extracted on rejection) + stdlib "data" filter as 2nd layer.
                check_tar_members(tar)
                tar.extractall(temp_dir, filter="data")
        except Exception as e:
            shutil.rmtree(temp_dir, ignore_errors=True)
            return ImportResult(
                success=False,
                imported_skills=[],
                imported_tools=[],
                conflicts=[],
                conflict_resolution=conflict_resolution.value,
                error=f"Failed to extract tarball: {str(e)}"
            )

        # Step 2: Find skills in extracted content
        import_skills_dir = None
        for item in temp_dir.iterdir():
            if item.is_dir() and item.name == "_shared":
                import_skills_dir = item / "skills"
                break

        if not import_skills_dir or not import_skills_dir.exists():
            return ImportResult(
                success=False,
                imported_skills=[],
                imported_tools=[],
                conflicts=[],
                conflict_resolution=conflict_resolution.value,
                error="No _shared/skills found in tarball"
            )

        # Step 3: Detect conflicts
        for skill_dir in import_skills_dir.iterdir():
            if not skill_dir.is_dir() or skill_dir.name.startswith('.'):
                continue

            skill_id = skill_dir.name
            local_skill = self.base_path / "_shared" / "skills" / skill_id

            if local_skill.exists():
                # Conflict
                import_meta = self._load_meta(skill_dir)
                local_meta = self._load_meta(local_skill)

                if import_meta and local_meta:
                    conflicts.append(SkillConflict(
                        skill_id=skill_id,
                        local_version=local_meta.get("version", "unknown"),
                        imported_version=import_meta.get("version", "unknown"),
                        local_modified=local_meta.get("last_modified", "unknown"),
                        imported_modified=import_meta.get("last_modified", "unknown")
                    ))

        # Step 4: Resolve conflicts
        resolution_map = self._resolve_conflicts(conflicts, conflict_resolution)

        # Step 5: Import skills (if not dry-run)
        if not dry_run:
            shared_skills_dir = self.base_path / "_shared" / "skills"
            shared_skills_dir.mkdir(parents=True, exist_ok=True)

            for skill_dir in import_skills_dir.iterdir():
                if not skill_dir.is_dir() or skill_dir.name.startswith('.'):
                    continue

                skill_id = skill_dir.name

                # Check if we should skip (operator_wins)
                if skill_id in resolution_map and resolution_map[skill_id] == "skip":
                    continue

                dst = shared_skills_dir / skill_id
                try:
                    if dst.exists():
                        shutil.rmtree(dst)
                    shutil.copytree(skill_dir, dst)
                    imported_skills.append(skill_id)
                except Exception as e:
                    return ImportResult(
                        success=False,
                        imported_skills=imported_skills,
                        imported_tools=imported_tools,
                        conflicts=conflicts,
                        conflict_resolution=conflict_resolution.value,
                        error=f"Failed to import skill {skill_id}: {str(e)}"
                    )

        # Cleanup
        try:
            shutil.rmtree(temp_dir)
        except:
            pass

        return ImportResult(
            success=True,
            imported_skills=imported_skills,
            imported_tools=imported_tools,
            conflicts=conflicts,
            conflict_resolution=conflict_resolution.value
        )

    def _load_meta(self, skill_dir: Path) -> Optional[Dict]:
        """Load meta.json from skill directory."""
        meta_path = skill_dir / "meta.json"
        if not meta_path.exists():
            return None

        try:
            with open(meta_path) as f:
                return json.load(f)
        except:
            return None

    def _resolve_conflicts(
        self,
        conflicts: List[SkillConflict],
        resolution: ConflictResolution
    ) -> Dict[str, str]:
        """Resolve conflicts based on strategy."""
        resolution_map = {}

        for conflict in conflicts:
            if resolution == ConflictResolution.OPERATOR_WINS:
                # Keep local, skip import
                resolution_map[conflict.skill_id] = "skip"
            elif resolution == ConflictResolution.GITHUB_WINS:
                # Import (overwrite local)
                resolution_map[conflict.skill_id] = "import"
            elif resolution == ConflictResolution.MANUAL:
                # For now, default to operator_wins
                # (real implementation would prompt user)
                resolution_map[conflict.skill_id] = "skip"

        return resolution_map
