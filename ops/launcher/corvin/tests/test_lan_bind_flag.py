"""`corvin serve`'s bind-host resolution — Settings -> Features ->
a2a_lan_bind (2026-08-04).

Found live debugging a real A2A pairing: the loopback-only default is a
genuine security boundary (the A2A receiver lives on the same port as the
Console API), so it must NOT become 0.0.0.0 by default. But an operator who
deliberately wants two LAN peers to reach each other had no way to switch
it short of hand-typing `--host 0.0.0.0` on every manual launch and
separately hand-editing the autostart service's command line. This adds a
single flag, off by default, that both paths now read:

  * `cli._default_bind_host()` — resolves 127.0.0.1 unless the flag is on
  * `cmd_serve` — an explicit `--host` always overrides the flag either way
  * best-effort: any failure to read the flag degrades to loopback-only,
    never to an accidentally-wide-open bind
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest import mock

import pytest

_HERE = Path(__file__).resolve().parent
_LAUNCHER = _HERE.parents[1]  # ops/launcher
_REPO = _HERE.parents[3]

if str(_LAUNCHER) not in sys.path:
    sys.path.insert(0, str(_LAUNCHER))
_CONSOLE = _REPO / "core" / "console"
if str(_CONSOLE) not in sys.path:
    sys.path.insert(0, str(_CONSOLE))

from corvin import cli  # noqa: E402, I001


class TestDefaultBindHost:
    def test_flag_off_defaults_to_loopback(self, monkeypatch):
        monkeypatch.setattr(
            "corvin_console.feature_flags.is_enabled",
            lambda flag_id, tenant_id="_default": False,
        )
        assert cli._default_bind_host() == "127.0.0.1"

    def test_flag_on_defaults_to_all_interfaces(self, monkeypatch):
        monkeypatch.setattr(
            "corvin_console.feature_flags.is_enabled",
            lambda flag_id, tenant_id="_default": flag_id == "a2a_lan_bind",
        )
        assert cli._default_bind_host() == "0.0.0.0"

    def test_flag_resolution_failure_degrades_to_loopback_not_wide_open(self, monkeypatch):
        # A broken/absent feature_flags module must never fail OPEN.
        def _raise(*a, **kw):
            raise RuntimeError("overlay corrupt")
        monkeypatch.setattr("corvin_console.feature_flags.is_enabled", _raise)
        assert cli._default_bind_host() == "127.0.0.1"


class TestCmdServeHostResolution:
    """cmd_serve must call serve_backend.start with the resolved host."""

    def _run(self, monkeypatch, *, cli_host, flag_on):
        captured = {}
        monkeypatch.setattr(
            cli, "_default_bind_host",
            lambda: "0.0.0.0" if flag_on else "127.0.0.1",
        )
        monkeypatch.setattr(cli.serve_backend, "maybe_pypi_autoupdate", lambda **kw: False)
        monkeypatch.setattr(cli.serve_backend, "is_available", lambda: True)
        monkeypatch.setattr(cli.serve_backend, "console_url", lambda port: "http://x:8765")
        monkeypatch.setattr(cli, "_onboarding_complete", lambda: True)
        monkeypatch.setattr(cli, "_print_hermes_status", lambda: None)

        def _fake_start(**kwargs):
            captured.update(kwargs)
            return 0

        monkeypatch.setattr(cli.serve_backend, "start", _fake_start)

        class _Args:
            port = 8765
            no_browser = True
            host = cli_host

        cli.cmd_serve(_Args())
        return captured["host"]

    def test_no_explicit_host_and_flag_off_uses_loopback(self, monkeypatch):
        assert self._run(monkeypatch, cli_host=None, flag_on=False) == "127.0.0.1"

    def test_no_explicit_host_and_flag_on_uses_all_interfaces(self, monkeypatch):
        assert self._run(monkeypatch, cli_host=None, flag_on=True) == "0.0.0.0"

    def test_explicit_host_overrides_flag_on(self, monkeypatch):
        assert self._run(monkeypatch, cli_host="10.0.0.5", flag_on=True) == "10.0.0.5"

    def test_explicit_host_overrides_flag_off(self, monkeypatch):
        assert self._run(monkeypatch, cli_host="10.0.0.5", flag_on=False) == "10.0.0.5"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
