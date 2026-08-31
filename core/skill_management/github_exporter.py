"""Export skills to GitHub as tarball + manifest."""

import json
import shutil
import hashlib
from pathlib import Path
from typing import List, Optional
from datetime import datetime
from dataclasses import dataclass

from core.skill_management.github_sync import GitClient, GitPushResult
from core.skill_management.resolver import SkillDependencyResolver
from core.skill_management.tenant_validator import validate_tenant_id  # TENANT-002: Fixed import


@dataclass
class ExportResult:
    """Result of exporting skills to GitHub."""
    success: bool
    exported_skills: List[str]
    exported_tools: List[str]
    tarball_path: Optional[Path] = None
    manifest_path: Optional[Path] = None
    manifest_hash: Optional[str] = None
    git_commit_sha: Optional[str] = None
    error: Optional[str] = None


class GitHubExporter:
    """Export skills to GitHub."""

    def __init__(self, repo_url: str, branch: str = "main", tenant_id: str = "_default"):
        # TENANT-002 FIX: Validate tenant_id before using in path construction
        validate_tenant_id(tenant_id)

        self.repo_url = repo_url
        self.branch = branch
        self.tenant_id = tenant_id
        self.base_path = Path.home() / ".corvin" / "tenants" / tenant_id
        # CVE-TENANT-001 FIX: Pass tenant_id to GitClient for path scoping
        self.git_client = GitClient(repo_url, branch, tenant_id=tenant_id)

    def export_shared_skills(self, dry_run: bool = False) -> ExportResult:
        """Export all _shared/ skills to GitHub."""
        exported_skills = []
        exported_tools = []
        errors = []

        # Step 1: Prepare export package
        shared_dir = self.base_path / "_shared"
        if not shared_dir.exists():
            return ExportResult(
                success=False,
                exported_skills=[],
                exported_tools=[],
                error="No _shared/ directory found"
            )

        # List all skills
        skills_dir = shared_dir / "skills"
        if skills_dir.exists():
            for skill_dir in skills_dir.iterdir():
                if skill_dir.is_dir() and not skill_dir.name.startswith('.'):
                    exported_skills.append(skill_dir.name)

        # Step 2: Create tarball
        tarball_path = self.base_path / "exports" / f"skills_{datetime.now().strftime('%Y%m%d_%H%M%S')}.tar.gz"
        tarball_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            shutil.make_archive(
                str(tarball_path.with_suffix('')),
                'gztar',
                shared_dir.parent,
                "_shared"
            )
        except Exception as e:
            return ExportResult(
                success=False,
                exported_skills=[],
                exported_tools=[],
                error=f"Failed to create tarball: {str(e)}"
            )

        # Step 3: Generate manifest
        manifest = self._generate_manifest(exported_skills, tarball_path)
        manifest_path = self.base_path / "exports" / f"MANIFEST_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"

        try:
            with open(manifest_path, "w") as f:
                json.dump(manifest, f, indent=2)
        except Exception as e:
            return ExportResult(
                success=False,
                exported_skills=[],
                exported_tools=[],
                error=f"Failed to write manifest: {str(e)}"
            )

        # Step 4: Calculate hash
        manifest_hash = self._calculate_hash(manifest_path)

        # Step 5: Push to GitHub (if not dry-run)
        if not dry_run:
            if not self.git_client.clone_or_pull():
                return ExportResult(
                    success=False,
                    exported_skills=exported_skills,
                    exported_tools=exported_tools,
                    error="Failed to clone/pull GitHub repo"
                )

            # Copy files to repo
            try:
                self._copy_to_repo(exported_skills)
            except Exception as e:
                return ExportResult(
                    success=False,
                    exported_skills=exported_skills,
                    exported_tools=exported_tools,
                    error=f"Failed to copy to repo: {str(e)}"
                )

            # Push to GitHub
            push_result = self.git_client.push(
                f"Export: {len(exported_skills)} skills on {datetime.now().isoformat()}"
            )

            if not push_result.success:
                return ExportResult(
                    success=False,
                    exported_skills=exported_skills,
                    exported_tools=exported_tools,
                    error=push_result.error
                )

            return ExportResult(
                success=True,
                exported_skills=exported_skills,
                exported_tools=exported_tools,
                tarball_path=tarball_path,
                manifest_path=manifest_path,
                manifest_hash=manifest_hash,
                git_commit_sha=push_result.commit_sha
            )
        else:
            # Dry-run: just return success without pushing
            return ExportResult(
                success=True,
                exported_skills=exported_skills,
                exported_tools=exported_tools,
                tarball_path=tarball_path,
                manifest_path=manifest_path,
                manifest_hash=manifest_hash
            )

    def export_single_skill(self, skill_id: str, dry_run: bool = False) -> ExportResult:
        """Export a single skill + its dependencies."""
        resolver = SkillDependencyResolver(self.tenant_id)
        result = resolver.resolve(skill_id, "_shared")

        if result.error:
            return ExportResult(
                success=False,
                exported_skills=[],
                exported_tools=[],
                error=f"Cannot resolve dependencies: {result.error}"
            )

        # Create temp export dir with just these skills
        temp_export = self.base_path / "exports" / f"temp_{skill_id}"
        temp_export.mkdir(parents=True, exist_ok=True)

        exported_skills = []
        try:
            for skill in result.resolved_skills:
                src = self.base_path / "_shared" / "skills" / skill.id
                dst = temp_export / skill.id
                if src.exists():
                    shutil.copytree(src, dst)
                    exported_skills.append(skill.id)
        except Exception as e:
            return ExportResult(
                success=False,
                exported_skills=[],
                exported_tools=[],
                error=f"Failed to copy skills: {str(e)}"
            )

        # Create tarball
        tarball_path = self.base_path / "exports" / f"skill_{skill_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.tar.gz"

        try:
            shutil.make_archive(
                str(tarball_path.with_suffix('')),
                'gztar',
                temp_export.parent,
                temp_export.name
            )
        finally:
            shutil.rmtree(temp_export)

        return ExportResult(
            success=True,
            exported_skills=exported_skills,
            exported_tools=[],
            tarball_path=tarball_path
        )

    def _generate_manifest(self, skill_ids: List[str], tarball_path: Path) -> dict:
        """Generate export manifest."""
        manifest = {
            "version": "1.0",
            "tenant_id": self.tenant_id,
            "exported_at": datetime.now().isoformat(),
            "repo": self.repo_url,
            "branch": self.branch,
            "content": {
                "skills": len(skill_ids),
                "tools": 0
            },
            "skills": skill_ids,
            "integrity_hash": self._calculate_hash(tarball_path),
            "restore_instructions": "Extract tarball and run: corvinOS skill import --tarball skills.tar.gz"
        }
        return manifest

    def _calculate_hash(self, file_path: Path) -> str:
        """Calculate SHA256 hash of file."""
        sha256_hash = hashlib.sha256()
        with open(file_path, "rb") as f:
            for byte_block in iter(lambda: f.read(4096), b""):
                sha256_hash.update(byte_block)
        return f"sha256:{sha256_hash.hexdigest()}"

    def _copy_to_repo(self, skill_ids: List[str]):
        """Copy skills to cloned repo."""
        for skill_id in skill_ids:
            src = self.base_path / "_shared" / "skills" / skill_id
            dst = self.git_client.local_repo_path / "skills" / skill_id

            if src.exists():
                dst.parent.mkdir(parents=True, exist_ok=True)
                if dst.exists():
                    shutil.rmtree(dst)
                shutil.copytree(src, dst)

    def cleanup(self):
        """Cleanup temporary files."""
        self.git_client.cleanup()
