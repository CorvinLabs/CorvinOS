"""
Skill Forge v2.0 Phase 3 Tests: ZIP Packaging & Distribution

Tests for:
- SkillPackager (ZIP creation, metadata, checksums)
- Distribution routes (download, list, metadata)
- E2E: skeleton → package → ZIP ready

ADR-0677: Skill Package & ZIP Distribution Format
License: Apache-2.0
"""

import pytest
import json
import tempfile
import zipfile
from pathlib import Path
from datetime import datetime

from core.skills.skill_packager import SkillPackager
from core.skills.phase1_manifest_v2 import SkillManifestV2, BootLayer, SkillDomain


@pytest.fixture
def sample_skill_folder():
    """Create a minimal Phase 1-2 output folder for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        skill_dir = Path(tmpdir) / "test_skill"
        skill_dir.mkdir()

        # Create required directories
        for subdir in ["src", "hooks", "tests", "scripts", "docs", "references"]:
            (skill_dir / subdir).mkdir()

        # Create required files
        (skill_dir / "README.md").write_text("# Test Skill\n\nA test skill for packaging.")

        # Create minimal skill.json
        (skill_dir / "skill.json").write_text(
            json.dumps({
                "skill_id": "test_skill",
                "name": "Test Skill",
                "version": "1.0.0",
                "description": "A test skill for packaging",
                "entry_point": "src.skill:TestSkill.execute",
                "boot_layer": "installed",
                "input_schema": {"input": "string"},
                "output_schema": {"output": "string"},
            })
        )

        # Create src/skill.py
        (skill_dir / "src" / "skill.py").write_text(
            """
class TestSkill:
    def execute(self, input_data: str) -> str:
        return f"processed: {input_data}"
"""
        )

        # Create minimal test file
        (skill_dir / "tests" / "test_skill.py").write_text(
            """
def test_skill_works():
    assert True
"""
        )

        yield skill_dir


class TestSkillPackager:
    """Test SkillPackager class."""

    def test_packager_init(self):
        """Initialize packager with default/custom output dir."""
        packager = SkillPackager()
        assert packager.output_dir.exists()
        assert "skills_packages" in str(packager.output_dir)

    def test_package_skill_basic(self, sample_skill_folder):
        """Package a skill and verify ZIP is created."""
        manifest_data = json.loads((sample_skill_folder / "skill.json").read_text())
        manifest = SkillManifestV2.from_dict(manifest_data)

        with tempfile.TemporaryDirectory() as tmpdir:
            packager = SkillPackager(Path(tmpdir))
            zip_path, zip_hash, metadata = packager.package(sample_skill_folder, manifest)

            # Verify ZIP exists
            assert zip_path.exists()
            assert zip_path.name == "test_skill_1.0.0.zip"

            # Verify hash format
            assert zip_hash.startswith("sha256:")
            assert len(zip_hash) > 10

            # Verify metadata
            assert metadata["skill_id"] == "test_skill"
            assert metadata["version"] == "1.0.0"
            assert metadata["checksum_count"] > 0

    def test_package_duplicate_error(self, sample_skill_folder):
        """Packaging same skill twice should raise FileExistsError."""
        manifest_data = json.loads((sample_skill_folder / "skill.json").read_text())
        manifest = SkillManifestV2.from_dict(manifest_data)

        with tempfile.TemporaryDirectory() as tmpdir:
            packager = SkillPackager(Path(tmpdir))

            # First package succeeds
            packager.package(sample_skill_folder, manifest)

            # Second package should fail
            with pytest.raises(FileExistsError):
                packager.package(sample_skill_folder, manifest)

    def test_package_missing_folder_error(self):
        """Packaging non-existent folder should raise ValueError."""
        manifest = SkillManifestV2(
            skill_id="nonexistent",
            name="Nonexistent",
            version="1.0.0",
            description="Test",
            entry_point="test:Test.execute",
            input_schema={},
            output_schema={},
        )

        packager = SkillPackager()
        with pytest.raises(ValueError):
            packager.package(Path("/nonexistent/path"), manifest)

    def test_zip_contents_valid(self, sample_skill_folder):
        """Verify ZIP contains all expected files and structure."""
        manifest_data = json.loads((sample_skill_folder / "skill.json").read_text())
        manifest = SkillManifestV2.from_dict(manifest_data)

        with tempfile.TemporaryDirectory() as tmpdir:
            packager = SkillPackager(Path(tmpdir))
            zip_path, _, _ = packager.package(sample_skill_folder, manifest)

            # Verify ZIP structure
            with zipfile.ZipFile(zip_path, "r") as zf:
                names = zf.namelist()

                # Should have files in subdirectories
                assert any("skill.json" in n for n in names)
                assert any("README.md" in n for n in names)
                assert any("src/" in n for n in names)
                assert any(".forge/" in n for n in names)

                # Verify metadata files exist
                assert any("generation_context.json" in n for n in names)
                assert any("audit_trail.jsonl" in n for n in names)
                assert any("checksum.sha256" in n for n in names)

    def test_metadata_files_content(self, sample_skill_folder):
        """Verify .forge metadata files have correct content."""
        manifest_data = json.loads((sample_skill_folder / "skill.json").read_text())
        manifest = SkillManifestV2.from_dict(manifest_data)

        with tempfile.TemporaryDirectory() as tmpdir:
            packager = SkillPackager(Path(tmpdir))
            zip_path, _, _ = packager.package(sample_skill_folder, manifest)

            with zipfile.ZipFile(zip_path, "r") as zf:
                # Parse generation_context.json
                context_json = zf.read("test_skill/.forge/generation_context.json").decode()
                context = json.loads(context_json)

                assert context["skill_id"] == "test_skill"
                assert context["version"] == "1.0.0"
                assert "phases_completed" in context

                # Parse audit_trail.jsonl
                audit_text = zf.read("test_skill/.forge/audit_trail.jsonl").decode()
                audit_lines = [json.loads(line) for line in audit_text.strip().split("\n") if line]

                # Should have at least the packaging event
                assert len(audit_lines) > 0
                assert any(e["event"] == "package_created" for e in audit_lines)

                # Parse checksum.sha256
                checksum_text = zf.read("test_skill/.forge/checksum.sha256").decode()
                checksum_lines = [line for line in checksum_text.strip().split("\n") if line]

                # Should have checksums for files
                assert len(checksum_lines) > 0
                assert all(line.startswith("sha256:") for line in checksum_lines)

    def test_checksum_validation(self, sample_skill_folder):
        """Verify checksums are computed and included."""
        manifest_data = json.loads((sample_skill_folder / "skill.json").read_text())
        manifest = SkillManifestV2.from_dict(manifest_data)

        with tempfile.TemporaryDirectory() as tmpdir:
            packager = SkillPackager(Path(tmpdir))
            zip_path, _, _ = packager.package(sample_skill_folder, manifest)

            with zipfile.ZipFile(zip_path, "r") as zf:
                checksum_text = zf.read("test_skill/.forge/checksum.sha256").decode()
                checksums = {}
                for line in checksum_text.strip().split("\n"):
                    if line:
                        parts = line.split(" ", 1)
                        if len(parts) == 2:
                            checksums[parts[1]] = parts[0]

                # Should have checksums for expected files
                assert any("skill.json" in path for path in checksums.keys())
                assert any("README.md" in path for path in checksums.keys())

                # All checksums should be valid SHA256 format
                for path, checksum in checksums.items():
                    assert checksum.startswith("sha256:")
                    assert len(checksum) == len("sha256:") + 64  # 64 hex chars


class TestPackageE2E:
    """End-to-end tests for packaging workflow."""

    def test_e2e_generate_package_extract(self, sample_skill_folder):
        """E2E: Create skill folder → Package → Extract → Validate."""
        manifest_data = json.loads((sample_skill_folder / "skill.json").read_text())
        manifest = SkillManifestV2.from_dict(manifest_data)

        with tempfile.TemporaryDirectory() as tmpdir:
            # Phase 3: Package
            packager = SkillPackager(Path(tmpdir))
            zip_path, zip_hash, metadata = packager.package(sample_skill_folder, manifest)

            assert zip_path.exists()

            # Extract and validate
            with tempfile.TemporaryDirectory() as extract_dir:
                with zipfile.ZipFile(zip_path, "r") as zf:
                    zf.extractall(extract_dir)

                # Verify structure after extraction
                extracted_skill = Path(extract_dir) / "test_skill"
                assert (extracted_skill / "skill.json").exists()
                assert (extracted_skill / "README.md").exists()
                assert (extracted_skill / ".forge" / "generation_context.json").exists()

                # Verify manifest is still valid
                extracted_manifest_data = json.loads(
                    (extracted_skill / "skill.json").read_text()
                )
                extracted_manifest = SkillManifestV2.from_dict(extracted_manifest_data)
                assert extracted_manifest.skill_id == "test_skill"
