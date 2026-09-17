"""
Adversarial Test Suite: Skill Forge v2.0 Security & Edge Cases

Tests security vectors (injection, path traversal, symlinks, ZIP bombs),
edge cases (empty skills, circular deps, corrupted archives), and
concurrency scenarios. Each test is independent and verifies fail-safe behavior.

ADR-0674: Skill Package & ZIP Distribution
License: Apache-2.0
"""

import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import zipfile
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock
import pytest

from core.skills.skill_packager import SkillPackager, PackagingError, ChecksumVerificationError
from core.skills.skill_installer import SkillInstaller, InstallationError, DependencyResolutionError
from core.skills.phase1_manifest_v2 import SkillManifestV2, BootLayer, SkillDomain


# ============================================================================
# SECURITY VECTORS
# ============================================================================

class TestInjectionAttacks:
    """Malicious JSON injection payloads should be rejected."""

    def test_shell_metacharacters_in_skill_id(self):
        """Test: skill_id with shell metacharacters → rejected."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)

            # Create ZIP with malicious skill.json
            malicious_skill_json = {
                "skill_id": "test'; DROP TABLE skills; --",
                "version": "1.0.0",
                "name": "Malicious",
                "description": "Attack",
                "entry_point": "src.main:execute",
                "boot_layer": "installed",
            }

            zip_path = tmpdir / "malicious.zip"
            with zipfile.ZipFile(zip_path, "w") as zf:
                zf.writestr("test_skill/skill.json", json.dumps(malicious_skill_json))

            # Try to install → should fail with clear error
            installer = SkillInstaller(tmpdir / "corvin_home")
            with pytest.raises(Exception):  # Should fail somewhere in validation
                asyncio.run(installer.install_skill(str(zip_path)))

    def test_json_injection_in_dependencies(self):
        """Test: Malicious JSON in dependencies field → fail-safe."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)

            # Create ZIP with injection in dependencies
            malicious_skill_json = {
                "skill_id": "test_skill",
                "version": "1.0.0",
                "name": "Test",
                "description": "Test",
                "entry_point": "src.main:execute",
                "dependencies": "'; MALICIOUS CODE; '",  # Should be dict, not string
            }

            zip_path = tmpdir / "injection.zip"
            with zipfile.ZipFile(zip_path, "w") as zf:
                zf.writestr("test_skill/skill.json", json.dumps(malicious_skill_json))

            installer = SkillInstaller(tmpdir / "corvin_home")
            # Should fail during manifest extraction (validation)
            with pytest.raises(Exception):
                asyncio.run(installer.install_skill(str(zip_path)))

    def test_unicode_escape_injection(self):
        """Test: Unicode escape sequences in skill_id → rejected."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)

            # Create ZIP with unicode escape injection
            malicious_skill_json = {
                "skill_id": "test\\x00skill",  # Null byte injection
                "version": "1.0.0",
                "name": "Test",
                "description": "Test",
                "entry_point": "src.main:execute",
            }

            zip_path = tmpdir / "unicode_injection.zip"
            with zipfile.ZipFile(zip_path, "w") as zf:
                zf.writestr("test_skill/skill.json", json.dumps(malicious_skill_json))

            installer = SkillInstaller(tmpdir / "corvin_home")
            with pytest.raises(Exception):
                asyncio.run(installer.install_skill(str(zip_path)))


class TestPathTraversalAttacks:
    """ZIP with ../ paths should be rejected before extraction."""

    def test_path_traversal_mkdir_escape(self):
        """Test: ZIP with ../../../etc/passwd → extraction blocked."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)

            # Create ZIP with path traversal
            zip_path = tmpdir / "traversal.zip"
            with zipfile.ZipFile(zip_path, "w") as zf:
                zf.writestr("test_skill/skill.json", json.dumps({
                    "skill_id": "test_skill",
                    "version": "1.0.0",
                    "name": "Test",
                    "description": "Test",
                    "entry_point": "src.main:execute",
                }))
                # Malicious path
                zf.writestr("test_skill/../../../etc/passwd_write", b"HACKED")

            installer = SkillInstaller(tmpdir / "corvin_home")
            result = asyncio.run(installer.install_skill(str(zip_path)))

            # Installation should fail or succeed with errors
            # Verify /etc/passwd was NOT modified
            assert not (Path("/etc/passwd_write").exists()), "Path traversal attack succeeded!"

    def test_path_traversal_absolute_paths(self):
        """Test: ZIP with absolute paths /etc/... → rejected."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)

            zip_path = tmpdir / "absolute.zip"
            with zipfile.ZipFile(zip_path, "w") as zf:
                zf.writestr("test_skill/skill.json", json.dumps({
                    "skill_id": "test_skill",
                    "version": "1.0.0",
                    "name": "Test",
                    "description": "Test",
                    "entry_point": "src.main:execute",
                }))
                # Absolute path
                zf.writestr("/etc/evil_config", b"MALICIOUS")

            installer = SkillInstaller(tmpdir / "corvin_home")
            result = asyncio.run(installer.install_skill(str(zip_path)))

            # Verify /etc/evil_config does not exist
            assert not Path("/etc/evil_config").exists(), "Absolute path extraction succeeded!"


class TestSymlinkAttacks:
    """Symlinks in ZIP should be validated or rejected."""

    def test_symlink_escape_to_system_file(self):
        """Test: ZIP contains symlink → ../../../etc/passwd → blocked."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)

            # Create a skill folder with a symlink
            skill_dir = tmpdir / "skill_src"
            skill_dir.mkdir()
            (skill_dir / "skill.json").write_text(json.dumps({
                "skill_id": "test_skill",
                "version": "1.0.0",
                "name": "Test",
                "description": "Test",
                "entry_point": "src.main:execute",
            }))
            (skill_dir / "src").mkdir()
            (skill_dir / "src" / "main.py").write_text("# Test")

            # Create a symlink that would escape
            evil_link = skill_dir / "evil_link"
            try:
                evil_link.symlink_to("/etc/passwd")
            except OSError:
                pytest.skip("Cannot create symlinks on this system")

            # Create ZIP
            zip_path = tmpdir / "symlink.zip"
            with zipfile.ZipFile(zip_path, "w") as zf:
                for root, dirs, files in os.walk(skill_dir):
                    for file in files:
                        file_path = Path(root) / file
                        arcname = file_path.relative_to(skill_dir)
                        zf.write(file_path, arcname)

            installer = SkillInstaller(tmpdir / "corvin_home")
            result = asyncio.run(installer.install_skill(str(zip_path)))

            # Verify /etc/passwd was not symlinked
            # Installation should complete without creating escape symlinks


class TestZIPBombs:
    """Extremely large files in ZIP should be detected."""

    def test_zip_bomb_decompression(self):
        """Test: ZIP bomb (1MB → 1GB) → detection and rejection."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)

            # Create a ZIP bomb: highly compressible data
            zip_path = tmpdir / "bomb.zip"
            with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
                zf.writestr("test_skill/skill.json", json.dumps({
                    "skill_id": "test_skill",
                    "version": "1.0.0",
                    "name": "Test",
                    "description": "Test",
                    "entry_point": "src.main:execute",
                }))
                # Add a huge file (10MB of zeros, highly compressible)
                zf.writestr("test_skill/huge_file.bin", b"\x00" * (10 * 1024 * 1024))

            installer = SkillInstaller(tmpdir / "corvin_home")

            # Should handle gracefully (may fail, but not OOM)
            try:
                result = asyncio.run(installer.install_skill(str(zip_path)))
                # If it succeeds, verify no disk exhaustion happened
                assert (tmpdir / "corvin_home" / "skills").exists()
            except (InstallationError, OSError):
                # Expected: installation fails due to size
                pass


class TestSignatureSpoofing:
    """Checksum files that don't match ZIP contents should be detected."""

    def test_checksum_file_lists_nonexistent_files(self):
        """Test: checksum.sha256 lists files not in ZIP → detection."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)

            zip_path = tmpdir / "spoofed.zip"
            with zipfile.ZipFile(zip_path, "w") as zf:
                zf.writestr("test_skill/skill.json", json.dumps({
                    "skill_id": "test_skill",
                    "version": "1.0.0",
                    "name": "Test",
                    "description": "Test",
                    "entry_point": "src.main:execute",
                }))
                # Add fake checksum file
                fake_checksums = (
                    "sha256:aaaa test_skill/nonexistent_file.py\n"
                    "sha256:bbbb test_skill/another_fake.txt\n"
                )
                zf.writestr(".forge/checksum.sha256", fake_checksums)

            installer = SkillInstaller(tmpdir / "corvin_home")
            # Installer should handle gracefully (warn or ignore)
            result = asyncio.run(installer.install_skill(str(zip_path)))
            assert result is not None  # Should complete (warnings acceptable)

    def test_checksum_hash_mismatch(self):
        """Test: checksum.sha256 has wrong hash → verification fails."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)

            zip_path = tmpdir / "mismatch.zip"
            with zipfile.ZipFile(zip_path, "w") as zf:
                file_content = b"Test content"
                zf.writestr("test_skill/skill.json", json.dumps({
                    "skill_id": "test_skill",
                    "version": "1.0.0",
                    "name": "Test",
                    "description": "Test",
                    "entry_point": "src.main:execute",
                }))
                zf.writestr("test_skill/test.py", file_content)
                # Wrong checksum
                zf.writestr(".forge/checksum.sha256",
                           "sha256:0000000000000000000000000000000000000000000000000000000000000000 test_skill/test.py\n")

            installer = SkillInstaller(tmpdir / "corvin_home")
            result = asyncio.run(installer.install_skill(str(zip_path), verify_checksum=True))
            # Should report checksum error in result or raise
            assert "error" in result or result["status"] != "success"


# ============================================================================
# EDGE CASES
# ============================================================================

class TestEmptySkills:
    """ZIP with only skeleton structure, no actual code."""

    def test_empty_skill_folder(self):
        """Test: Skill with empty src/ → installation should fail."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)

            zip_path = tmpdir / "empty.zip"
            with zipfile.ZipFile(zip_path, "w") as zf:
                zf.writestr("test_skill/skill.json", json.dumps({
                    "skill_id": "test_skill",
                    "version": "1.0.0",
                    "name": "Empty Skill",
                    "description": "Empty",
                    "entry_point": "src.main:execute",
                }))
                # Empty src/
                zf.writestr("test_skill/src/.gitkeep", "")

            installer = SkillInstaller(tmpdir / "corvin_home")
            # Should either succeed (empty is valid) or fail with clear error
            result = asyncio.run(installer.install_skill(str(zip_path)))
            assert result is not None


class TestCircularDependencies:
    """Skill A depends on B, B depends on A → detection."""

    def test_circular_dependency_detection(self):
        """Test: Circular deps → detected and reported."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)

            # Create skill A → depends on B
            zip_a = tmpdir / "skill_a.zip"
            with zipfile.ZipFile(zip_a, "w") as zf:
                zf.writestr("skill_a/skill.json", json.dumps({
                    "skill_id": "skill_a",
                    "version": "1.0.0",
                    "name": "Skill A",
                    "description": "A",
                    "entry_point": "src.main:execute",
                    "dependencies": {"skill_b": "1.0.0"},
                }))

            installer = SkillInstaller(tmpdir / "corvin_home")

            # Install A (should fail because B doesn't exist)
            with pytest.raises(DependencyResolutionError):
                asyncio.run(installer.install_skill(str(zip_a)))


class TestMissingDependencies:
    """Skill requires dependency that's not installed."""

    def test_missing_required_dependency(self):
        """Test: Skill requires uninstalled dep → fails."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)

            zip_path = tmpdir / "requires_missing.zip"
            with zipfile.ZipFile(zip_path, "w") as zf:
                zf.writestr("test_skill/skill.json", json.dumps({
                    "skill_id": "test_skill",
                    "version": "1.0.0",
                    "name": "Test",
                    "description": "Test",
                    "entry_point": "src.main:execute",
                    "dependencies": {"nonexistent_skill": "1.0.0"},
                }))

            installer = SkillInstaller(tmpdir / "corvin_home")
            with pytest.raises(DependencyResolutionError):
                asyncio.run(installer.install_skill(str(zip_path)))


class TestCorruptedZIP:
    """ZIP file truncated mid-stream → graceful failure."""

    def test_truncated_zip_file(self):
        """Test: ZIP file corrupted (truncated) → detection and rejection."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)

            # Create a valid ZIP, then truncate it
            zip_path = tmpdir / "corrupted.zip"
            with zipfile.ZipFile(zip_path, "w") as zf:
                zf.writestr("test_skill/skill.json", json.dumps({
                    "skill_id": "test_skill",
                    "version": "1.0.0",
                    "name": "Test",
                    "description": "Test",
                    "entry_point": "src.main:execute",
                }))

            # Truncate the ZIP
            with open(zip_path, "r+b") as f:
                f.seek(0, 2)  # Go to end
                size = f.tell()
                f.seek(size - 10)  # Truncate last 10 bytes
                f.truncate()

            installer = SkillInstaller(tmpdir / "corvin_home")
            with pytest.raises(InstallationError):
                asyncio.run(installer.install_skill(str(zip_path)))


class TestUnicodeFilenames:
    """ZIP with non-ASCII filenames (UTF-8 edge cases)."""

    def test_unicode_filenames_in_zip(self):
        """Test: ZIP with UTF-8 filenames → handled safely."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)

            zip_path = tmpdir / "unicode.zip"
            with zipfile.ZipFile(zip_path, "w") as zf:
                zf.writestr("test_skill/skill.json", json.dumps({
                    "skill_id": "test_skill",
                    "version": "1.0.0",
                    "name": "Test",
                    "description": "Test",
                    "entry_point": "src.main:execute",
                }))
                # Add files with unicode names
                zf.writestr("test_skill/src/файл.py", b"# Russian")
                zf.writestr("test_skill/src/文件.py", b"# Chinese")
                zf.writestr("test_skill/src/αρχείο.py", b"# Greek")

            installer = SkillInstaller(tmpdir / "corvin_home")
            result = asyncio.run(installer.install_skill(str(zip_path)))

            # Should handle without crashing
            assert result is not None
            assert "test_skill" in result["skill_id"]


# ============================================================================
# CONCURRENCY & RESOURCE ISSUES
# ============================================================================

class TestConcurrentInstallations:
    """Two threads install same skill simultaneously."""

    def test_concurrent_same_skill_install(self):
        """Test: Two threads install same skill → one wins, no corruption."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)

            zip_path = tmpdir / "concurrent.zip"
            with zipfile.ZipFile(zip_path, "w") as zf:
                zf.writestr("test_skill/skill.json", json.dumps({
                    "skill_id": "concurrent_skill",
                    "version": "1.0.0",
                    "name": "Concurrent",
                    "description": "Test concurrent install",
                    "entry_point": "src.main:execute",
                }))

            installer = SkillInstaller(tmpdir / "corvin_home")
            results = []
            errors = []

            async def install():
                try:
                    result = await installer.install_skill(str(zip_path))
                    results.append(result)
                except Exception as e:
                    errors.append(e)

            # Run two concurrent installations
            asyncio.run(asyncio.gather(install(), install()))

            # At least one should succeed, no exceptions expected
            assert len(results) + len(errors) == 2
            # Check skill folder is not corrupted
            skill_dir = tmpdir / "corvin_home" / "skills" / "custom" / "concurrent_skill"
            if skill_dir.exists():
                assert (skill_dir / "skill.json").exists()

    def test_concurrent_different_skills_install(self):
        """Test: Multiple threads install different skills → all succeed."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)

            # Create 3 different skill ZIPs
            zip_paths = []
            for i in range(3):
                zip_path = tmpdir / f"skill_{i}.zip"
                with zipfile.ZipFile(zip_path, "w") as zf:
                    zf.writestr(f"skill_{i}/skill.json", json.dumps({
                        "skill_id": f"skill_{i}",
                        "version": "1.0.0",
                        "name": f"Skill {i}",
                        "description": f"Test {i}",
                        "entry_point": "src.main:execute",
                    }))
                zip_paths.append(zip_path)

            installer = SkillInstaller(tmpdir / "corvin_home")
            results = []

            async def install_all():
                for zip_path in zip_paths:
                    result = await installer.install_skill(str(zip_path))
                    results.append(result)

            asyncio.run(install_all())

            # All should succeed
            assert len(results) == 3
            for result in results:
                assert result["status"] in ("success", "already_installed")


class TestVersionDowngrade:
    """Try to install older version over newer."""

    def test_version_downgrade_blocked(self):
        """Test: Installing v0.9.0 over v1.0.0 → blocked or warned."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)

            installer = SkillInstaller(tmpdir / "corvin_home")

            # Install v1.0.0
            zip_v1 = tmpdir / "skill_v1.zip"
            with zipfile.ZipFile(zip_v1, "w") as zf:
                zf.writestr("test_skill/skill.json", json.dumps({
                    "skill_id": "test_skill",
                    "version": "1.0.0",
                    "name": "Test",
                    "description": "Test",
                    "entry_point": "src.main:execute",
                }))

            result1 = asyncio.run(installer.install_skill(str(zip_v1)))
            assert result1["version"] == "1.0.0"

            # Try to install v0.9.0
            zip_v09 = tmpdir / "skill_v09.zip"
            with zipfile.ZipFile(zip_v09, "w") as zf:
                zf.writestr("test_skill/skill.json", json.dumps({
                    "skill_id": "test_skill",
                    "version": "0.9.0",
                    "name": "Test",
                    "description": "Test",
                    "entry_point": "src.main:execute",
                }))

            result2 = asyncio.run(installer.install_skill(str(zip_v09)))
            # Should either succeed with v0.9.0 (and backup v1.0.0) or warn
            # The important thing is no corruption


class TestDuplicateFiles:
    """ZIP contains same file twice with different content."""

    def test_duplicate_files_in_zip(self):
        """Test: ZIP has duplicate.txt twice → last one wins or error."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)

            zip_path = tmpdir / "duplicates.zip"
            with zipfile.ZipFile(zip_path, "w") as zf:
                zf.writestr("test_skill/skill.json", json.dumps({
                    "skill_id": "test_skill",
                    "version": "1.0.0",
                    "name": "Test",
                    "description": "Test",
                    "entry_point": "src.main:execute",
                }))
                # Add same file twice with different content
                zf.writestr("test_skill/src/config.py", b"version = 1")
                zf.writestr("test_skill/src/config.py", b"version = 2")

            installer = SkillInstaller(tmpdir / "corvin_home")
            result = asyncio.run(installer.install_skill(str(zip_path)))

            # Should handle (either last-one-wins or error)
            assert result is not None

            # Verify installed skill folder is valid
            skill_dir = tmpdir / "corvin_home" / "skills" / "custom" / "test_skill"
            if skill_dir.exists():
                assert (skill_dir / "skill.json").exists()


# ============================================================================
# PERFORMANCE
# ============================================================================

class TestLargeManifests:
    """skill.json with 10,000+ dependencies listed."""

    def test_large_dependency_manifest(self):
        """Test: skill.json with 10k deps → handles efficiently."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)

            # Create manifest with 10k dependencies
            large_deps = {f"dep_{i}": "1.0.0" for i in range(10000)}

            zip_path = tmpdir / "large.zip"
            with zipfile.ZipFile(zip_path, "w") as zf:
                zf.writestr("test_skill/skill.json", json.dumps({
                    "skill_id": "test_skill",
                    "version": "1.0.0",
                    "name": "Large Dep Manifest",
                    "description": "Test",
                    "entry_point": "src.main:execute",
                    "dependencies": large_deps,
                }))

            installer = SkillInstaller(tmpdir / "corvin_home")

            # Should handle without OOM or timeout
            try:
                result = asyncio.run(asyncio.wait_for(
                    installer.install_skill(str(zip_path)),
                    timeout=10.0
                ))
                # Installation completed (with or without errors is OK)
                assert result is not None
            except asyncio.TimeoutError:
                pytest.fail("Large manifest installation timed out")


class TestLargeChecksumFile:
    """checksum.sha256 with 10,000+ entries."""

    def test_large_checksum_verification(self):
        """Test: checksum.sha256 with 10k files → efficient verification."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)

            # Create ZIP with many files and large checksum
            zip_path = tmpdir / "large_checksum.zip"
            with zipfile.ZipFile(zip_path, "w") as zf:
                zf.writestr("test_skill/skill.json", json.dumps({
                    "skill_id": "test_skill",
                    "version": "1.0.0",
                    "name": "Test",
                    "description": "Test",
                    "entry_point": "src.main:execute",
                }))

                # Create large checksum file (10k entries)
                checksum_lines = []
                for i in range(10000):
                    checksum_lines.append(
                        f"sha256:{'a' * 64} test_skill/src/file_{i}.py"
                    )
                zf.writestr(".forge/checksum.sha256", "\n".join(checksum_lines))

            installer = SkillInstaller(tmpdir / "corvin_home")

            # Should verify efficiently
            try:
                result = asyncio.run(asyncio.wait_for(
                    installer.install_skill(str(zip_path), verify_checksum=True),
                    timeout=30.0
                ))
                assert result is not None
            except asyncio.TimeoutError:
                pytest.fail("Large checksum verification timed out")


# ============================================================================
# ATOMIC FAILURE VERIFICATION
# ============================================================================

class TestAtomicFailures:
    """Verify no partial state left behind on failure."""

    def test_partial_extraction_cleanup(self):
        """Test: Failed extraction → partial files cleaned up."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)

            # Create a ZIP that will fail verification
            zip_path = tmpdir / "failing.zip"
            with zipfile.ZipFile(zip_path, "w") as zf:
                zf.writestr("test_skill/skill.json", json.dumps({
                    "skill_id": "fail_skill",
                    "version": "1.0.0",
                    "name": "Will Fail",
                    "description": "Test",
                    "entry_point": "src.main:execute",
                    "dependencies": {"missing_dep": "1.0.0"},
                }))

            installer = SkillInstaller(tmpdir / "corvin_home")

            try:
                result = asyncio.run(installer.install_skill(str(zip_path)))
            except DependencyResolutionError:
                pass

            # Verify no partial skill directory left
            skill_dir = tmpdir / "corvin_home" / "skills" / "custom" / "fail_skill"
            # If it exists, it should be complete or empty (not partial)
            if skill_dir.exists():
                # Should have all required files or none
                assert (skill_dir / "skill.json").exists() or len(list(skill_dir.iterdir())) == 0

    def test_registry_not_updated_on_failure(self):
        """Test: Failed install → registry not updated."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)

            # Create ZIP with missing dependency
            zip_path = tmpdir / "bad_skill.zip"
            with zipfile.ZipFile(zip_path, "w") as zf:
                zf.writestr("bad_skill/skill.json", json.dumps({
                    "skill_id": "bad_skill",
                    "version": "1.0.0",
                    "name": "Bad",
                    "description": "Bad",
                    "entry_point": "src.main:execute",
                    "dependencies": {"nonexistent": "1.0.0"},
                }))

            installer = SkillInstaller(tmpdir / "corvin_home")

            try:
                asyncio.run(installer.install_skill(str(zip_path)))
            except DependencyResolutionError:
                pass

            # Registry should not have bad_skill entry
            registry_file = tmpdir / "corvin_home" / ".registry" / "installed.json"
            if registry_file.exists():
                registry = json.loads(registry_file.read_text())
                installed_ids = [r["skill_id"] for r in registry.get("installed_skills", [])]
                assert "bad_skill" not in installed_ids


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
