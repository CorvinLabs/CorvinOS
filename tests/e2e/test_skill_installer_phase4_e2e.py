"""Phase 4 E2E: Skill Installation + Dependency Resolution (ADR-0680)"""
import pytest, tempfile, json, zipfile, hashlib
from pathlib import Path
from core.skills.skill_installer import SkillInstaller

class TestSkillInstallerPhase4E2E:
    def test_full_install_lifecycle(self):
        """Full install: ZIP → unzip → register → verify."""
        with tempfile.TemporaryDirectory() as tmpdir:
            installer = SkillInstaller(Path(tmpdir))
            skill_zip = Path(tmpdir) / "skill.zip"
            with zipfile.ZipFile(skill_zip, 'w') as zf:
                zf.writestr("skill.json", '{"skill_id":"test-skill","version":"1.0.0"}')
                zf.writestr("impl.py", "def run(): pass")
            
            sha256 = hashlib.sha256()
            with open(skill_zip, "rb") as f:
                sha256.update(f.read())
            
            success, msg = installer.install_skill(skill_zip, sha256.hexdigest(), 
                {"skill_id": "test-skill", "version": "1.0.0"})
            assert success, f"Failed: {msg}"
            
            registry = installer._load_registry()
            assert "test-skill" in registry
            assert any(s["version"] == "1.0.0" for s in registry["test-skill"])
    
    def test_version_conflict_detection(self):
        """Install same version twice → conflict."""
        with tempfile.TemporaryDirectory() as tmpdir:
            installer = SkillInstaller(Path(tmpdir))
            skill_zip = Path(tmpdir) / "skill.zip"
            with zipfile.ZipFile(skill_zip, 'w') as zf:
                zf.writestr("skill.json", '{}')
            
            sha256 = hashlib.sha256()
            with open(skill_zip, "rb") as f:
                sha256.update(f.read())
            
            installer.install_skill(skill_zip, sha256.hexdigest(), 
                {"skill_id": "test", "version": "1.0.0"})
            success2, msg = installer.install_skill(skill_zip, sha256.hexdigest(),
                {"skill_id": "test", "version": "1.0.0"})
            assert not success2
            assert "already installed" in msg
    
    def test_dependency_resolution(self):
        """Unmet dependencies → installation fails."""
        with tempfile.TemporaryDirectory() as tmpdir:
            installer = SkillInstaller(Path(tmpdir))
            skill_zip = Path(tmpdir) / "skill.zip"
            with zipfile.ZipFile(skill_zip, 'w') as zf:
                zf.writestr("skill.json", '{}')
            
            sha256 = hashlib.sha256()
            with open(skill_zip, "rb") as f:
                sha256.update(f.read())
            
            success, msg = installer.install_skill(skill_zip, sha256.hexdigest(), {
                "skill_id": "test", "version": "1.0.0",
                "dependencies": [{"skill_id": "missing-skill", "version": "1.0.0"}]
            })
            assert not success
            assert "Unmet dependencies" in msg
    
    def test_bad_checksum_rejected(self):
        """Checksum mismatch → installation fails."""
        with tempfile.TemporaryDirectory() as tmpdir:
            installer = SkillInstaller(Path(tmpdir))
            skill_zip = Path(tmpdir) / "skill.zip"
            with zipfile.ZipFile(skill_zip, 'w') as zf:
                zf.writestr("skill.json", '{}')
            
            success, msg = installer.install_skill(skill_zip, "wrong_hash_12345",
                {"skill_id": "test", "version": "1.0.0"})
            assert not success
            assert "Checksum mismatch" in msg
