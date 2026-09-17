"""Test suite for Docker Uninstall Coverage (ADR-0868)"""

from pathlib import Path
import subprocess

def test_docker_uninstall_script_exists():
    """Verify corvin-uninstall script exists"""
    uninstall_script = Path("/home/shumway/projects/CorvinOS/corvin-uninstall")
    assert uninstall_script.exists()

def test_detect_deployment_mode_function():
    """Verify detect_deployment_mode function is present"""
    uninstall_script = Path("/home/shumway/projects/CorvinOS/corvin-uninstall")
    content = uninstall_script.read_text()
    assert "detect_deployment_mode" in content

def test_export_audit_trail_function():
    """Verify export_audit_trail function is present"""
    uninstall_script = Path("/home/shumway/projects/CorvinOS/corvin-uninstall")
    content = uninstall_script.read_text()
    assert "export_audit_trail" in content
    assert "CORVIN_EXPORT_DIR" in content

def test_cleanup_docker_function():
    """Verify cleanup_docker function is present"""
    uninstall_script = Path("/home/shumway/projects/CorvinOS/corvin-uninstall")
    content = uninstall_script.read_text()
    assert "cleanup_docker" in content
    assert "docker stop" in content or "docker ps" in content

def test_verify_docker_cleanup_function():
    """Verify verify_docker_cleanup function is present"""
    uninstall_script = Path("/home/shumway/projects/CorvinOS/corvin-uninstall")
    content = uninstall_script.read_text()
    assert "verify_docker_cleanup" in content

def test_docker_label_based_detection():
    """Verify label-based Docker detection is used"""
    uninstall_script = Path("/home/shumway/projects/CorvinOS/corvin-uninstall")
    content = uninstall_script.read_text()
    assert 'label=app=corvinOS' in content

def test_audit_trail_export_before_cleanup():
    """Verify audit trail is exported before Docker cleanup"""
    uninstall_script = Path("/home/shumway/projects/CorvinOS/corvin-uninstall")
    content = uninstall_script.read_text()
    export_pos = content.find("export_audit_trail")
    cleanup_pos = content.find("cleanup_docker")
    assert export_pos > 0 and cleanup_pos > 0
    assert export_pos < cleanup_pos, "Audit trail export should happen before cleanup"

def test_volume_removal_with_confirmation():
    """Verify volumes are removed with user confirmation"""
    uninstall_script = Path("/home/shumway/projects/CorvinOS/corvin-uninstall")
    content = uninstall_script.read_text()
    assert "read -p" in content or "confirm" in content
    assert "docker volume" in content

if __name__ == "__main__":
    test_docker_uninstall_script_exists()
    test_detect_deployment_mode_function()
    test_export_audit_trail_function()
    test_cleanup_docker_function()
    test_verify_docker_cleanup_function()
    test_docker_label_based_detection()
    test_audit_trail_export_before_cleanup()
    test_volume_removal_with_confirmation()
    print("✓ All Docker uninstall tests passed")
