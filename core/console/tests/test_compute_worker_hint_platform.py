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


def _status(monkeypatch, tmp_path, reachable: bool, os_name: str):
    from types import SimpleNamespace
    monkeypatch.setattr(compute, "_compute_dir", lambda tid: tmp_path / "compute")
    monkeypatch.setattr(compute, "_runs_dir", lambda tid: tmp_path / "compute" / "runs")
    monkeypatch.setattr(compute, "_socket_path", lambda tid: tmp_path / "compute" / "worker.sock")
    monkeypatch.setattr(compute, "_probe_socket", lambda p, **kw: {
        "exists": reachable, "reachable": reachable, "error": None})
    monkeypatch.setattr(compute, "_read_system_resources", lambda: {})
    monkeypatch.setattr(compute.os, "name", os_name)
    return compute.compute_status(SimpleNamespace(tenant_id="acme", sid_fingerprint="x"))


def test_status_carries_platform_hint_when_worker_down(monkeypatch, tmp_path):
    out = _status(monkeypatch, tmp_path, reachable=False, os_name="nt")
    assert "systemctl" not in out["worker_hint"]
    assert "--tenant acme" in out["worker_hint"]


def test_status_hint_absent_when_worker_up(monkeypatch, tmp_path):
    assert _status(monkeypatch, tmp_path, reachable=True, os_name="nt")["worker_hint"] is None
