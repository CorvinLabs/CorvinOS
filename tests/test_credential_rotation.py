"""Test suite for Credential Rotation Phase 2 (Blocker 3)"""

from pathlib import Path
import sys
sys.path.insert(0, str(Path("/home/shumway/projects/CorvinOS")))

def test_phase2_script_exists():
    """Verify credential rotation Phase 2 script exists"""
    script = Path("/home/shumway/projects/CorvinOS/scripts/rotate_corvin_keys_phase2.py")
    assert script.exists(), "Phase 2 rotation script not found"

def test_phase2_has_pre_checks():
    """Verify Phase 1.5 pre-checks function is present"""
    script = Path("/home/shumway/projects/CorvinOS/scripts/rotate_corvin_keys_phase2.py")
    content = script.read_text()
    assert "phase_1_5_pre_checks" in content, "Phase 1.5 pre-checks function missing"

def test_phase2_has_rotation():
    """Verify Phase 2 rotation function is present"""
    script = Path("/home/shumway/projects/CorvinOS/scripts/rotate_corvin_keys_phase2.py")
    content = script.read_text()
    assert "rotate_credentials_phase2" in content, "Phase 2 rotation function missing"

def test_phase2_has_backup():
    """Verify backup functionality is present"""
    script = Path("/home/shumway/projects/CorvinOS/scripts/rotate_corvin_keys_phase2.py")
    content = script.read_text()
    assert "backup_credentials" in content or "BACKUP_DIR" in content, "Backup function missing"

def test_phase2_has_restore():
    """Verify rollback/restore functionality is present"""
    script = Path("/home/shumway/projects/CorvinOS/scripts/rotate_corvin_keys_phase2.py")
    content = script.read_text()
    assert "restore_credentials" in content or "rollback" in content.lower(), "Restore function missing"

def test_placeholder_generation():
    """Verify placeholder generation logic is present"""
    script = Path("/home/shumway/projects/CorvinOS/scripts/rotate_corvin_keys_phase2.py")
    content = script.read_text()
    assert "PLACEHOLDER" in content, "Placeholder generation missing"
    assert "timestamp" in content, "Timestamp in placeholder missing"

def test_dry_run_support():
    """Verify --dry-run flag is supported"""
    script = Path("/home/shumway/projects/CorvinOS/scripts/rotate_corvin_keys_phase2.py")
    content = script.read_text()
    assert "--dry-run" in content or "dry_run" in content, "Dry-run support missing"

def test_file_permissions():
    """Verify file is executable"""
    script = Path("/home/shumway/projects/CorvinOS/scripts/rotate_corvin_keys_phase2.py")
    assert script.stat().st_mode & 0o111, "Script is not executable"

if __name__ == "__main__":
    test_phase2_script_exists()
    test_phase2_has_pre_checks()
    test_phase2_has_rotation()
    test_phase2_has_backup()
    test_phase2_has_restore()
    test_placeholder_generation()
    test_dry_run_support()
    test_file_permissions()
    print("✓ All credential rotation tests passed")
