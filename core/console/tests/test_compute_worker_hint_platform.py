"""The 503 'compute worker is not running' hint must name a start path that exists
on this platform: systemd on Linux, the CLI on Windows (no service is installed)."""
from __future__ import annotations

from corvin_console.routes import compute


def test_posix_hint_unchanged(monkeypatch):
    monkeypatch.setattr(compute.os, "name", "posix")
    assert compute._worker_not_running_detail("_default") == (
        "compute worker is not running — start it via Settings "
        "(systemctl --user start corvin-compute@<tenant>)")


def test_windows_hint_has_no_systemctl(monkeypatch):
    monkeypatch.setattr(compute.os, "name", "nt")
    msg = compute._worker_not_running_detail("acme")
    assert "systemctl" not in msg
    assert "-m corvin_compute serve --tenant acme" in msg
