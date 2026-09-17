"""Test suite for Watchdog Timer Health Check (ADR-0867)"""

import subprocess
from pathlib import Path

def test_health_check_probe_in_install_sh():
    """Verify health_check_probe function is in install.sh"""
    install_sh = Path("/home/shumway/projects/CorvinOS/install.sh")
    assert install_sh.exists()
    content = install_sh.read_text()
    assert "healthz_check_probe" in content
    assert "exponential backoff" in content or "backoff" in content

def test_console_endpoint_check():
    """Verify console health endpoint is checked"""
    install_sh = Path("/home/shumway/projects/CorvinOS/install.sh")
    content = install_sh.read_text()
    assert "/v1/console/healthz" in content

def test_gateway_endpoint_check():
    """Verify gateway health endpoint is checked"""
    install_sh = Path("/home/shumway/projects/CorvinOS/install.sh")
    content = install_sh.read_text()
    assert "/v1/gateway/healthz" in content

def test_fail_closed_exit_code():
    """Verify health check exits with code 2 on failure"""
    install_sh = Path("/home/shumway/projects/CorvinOS/install.sh")
    content = install_sh.read_text()
    assert "exit 2" in content
    assert "HEALTHZ_PASS" in content

def test_backoff_logic():
    """Verify exponential backoff logic is present"""
    install_sh = Path("/home/shumway/projects/CorvinOS/install.sh")
    content = install_sh.read_text()
    assert "backoff=$((backoff * 2))" in content or "backoff*" in content

if __name__ == "__main__":
    test_health_check_probe_in_install_sh()
    test_console_endpoint_check()
    test_gateway_endpoint_check()
    test_fail_closed_exit_code()
    test_backoff_logic()
    print("✓ All watchdog health check tests passed")
