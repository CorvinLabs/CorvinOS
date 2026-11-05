"""The 503 'compute worker is not running' hint must name a start path that exists
on this platform: systemd on Linux, the CLI on Windows (no service is installed).

The platform is flipped through ``compute._on_windows``, never through the global
``os.name``: patching that turns every ``pathlib.Path()`` into a ``WindowsPath`` and
pytest's own reporting then dies with INTERNALERROR on Linux (review SRF-19)."""
from __future__ import annotations

from corvin_console.routes import compute


def test_posix_hint_unchanged(monkeypatch):
    monkeypatch.setattr(compute, "_on_windows", lambda: False)
    assert compute._worker_not_running_detail("_default") == (
        "compute worker is not running — start it via Settings "
        "(systemctl --user start corvin-compute@<tenant>)")


def test_windows_hint_has_no_systemctl(monkeypatch):
    monkeypatch.setattr(compute, "_on_windows", lambda: True)
    msg = compute._worker_not_running_detail("acme")
    assert "systemctl" not in msg
    assert "-m corvin_compute --corvin-home " in msg
    assert " serve --tenant acme" in msg


def _status(monkeypatch, tmp_path, reachable: bool, os_name: str):
    from types import SimpleNamespace
    monkeypatch.setattr(compute, "_compute_dir", lambda tid: tmp_path / "compute")
    monkeypatch.setattr(compute, "_runs_dir", lambda tid: tmp_path / "compute" / "runs")
    monkeypatch.setattr(compute, "_socket_path", lambda tid: tmp_path / "compute" / "worker.sock")
    monkeypatch.setattr(compute, "_probe_socket", lambda p, **kw: {
        "exists": reachable, "reachable": reachable, "error": None})
    monkeypatch.setattr(compute, "_read_system_resources", lambda: {})
    monkeypatch.setattr(compute, "_on_windows", lambda: os_name == "nt")
    return compute.compute_status(SimpleNamespace(tenant_id="acme", sid_fingerprint="x"))


def test_status_carries_platform_hint_when_worker_down(monkeypatch, tmp_path):
    out = _status(monkeypatch, tmp_path, reachable=False, os_name="nt")
    assert "systemctl" not in out["worker_hint"]
    assert "--tenant acme" in out["worker_hint"]


def test_status_hint_absent_when_worker_up(monkeypatch, tmp_path):
    assert _status(monkeypatch, tmp_path, reachable=True, os_name="nt")["worker_hint"] is None


def test_windows_hint_names_effective_corvin_home(monkeypatch, tmp_path):
    """A console on a non-default home must point the user at a worker on the
    SAME home (the CLI default could resolve elsewhere); --corvin-home is a
    top-level option so it precedes the ``serve`` subcommand."""
    home = tmp_path / "my home"
    monkeypatch.setenv("CORVIN_HOME", str(home))
    monkeypatch.setattr(compute, "_on_windows", lambda: True)
    msg = compute._worker_not_running_detail("acme")
    assert f'-m corvin_compute --corvin-home "{home}" serve --tenant acme' in msg
    # The suggested argv must parse with the real CLI parser.
    from corvin_compute.cli import build_parser
    args = build_parser().parse_args(
        ["--corvin-home", str(home), "serve", "--tenant", "acme"])
    assert args.corvin_home == str(home) and args.cmd == "serve"
