"""Regression: a non-default tenant's ping opt-out must not be silently
ignored (adversarial review finding).

The anonymous instance-count ping identity (instance_id, last_ping stamp) is
ONE shared value per CORVIN_HOME, but `ping_enabled()` used to only ever
consult the single env-resolved tenant (`CORVIN_TENANT_ID`, default
`_default`). In a multi-tenant install, a non-default tenant explicitly
setting `spec.telemetry.ping_enabled: false` had zero effect on the shared
ping, which still fired regardless. Fail-closed fix: if ANY known tenant on
the install has opted out, the shared ping is suppressed for all of them.
"""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from corvin_console.aco import htrace_consent as hc


def _write_tenant_cfg(home: Path, tid: str, ping_enabled: bool | None) -> None:
    cfg_dir = home / "tenants" / tid / "global"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    if ping_enabled is None:
        (cfg_dir / "tenant.corvin.yaml").write_text("spec: {}\n", encoding="utf-8")
        return
    (cfg_dir / "tenant.corvin.yaml").write_text(
        textwrap.dedent(f"""\
            spec:
              telemetry:
                ping_enabled: {str(ping_enabled).lower()}
        """),
        encoding="utf-8",
    )


def test_default_tenant_opted_out_still_suppresses_ping(tmp_path):
    home = tmp_path / ".corvin"
    _write_tenant_cfg(home, "_default", False)
    assert hc.ping_enabled(home) is False


def test_non_default_tenant_optout_now_suppresses_the_shared_ping(tmp_path, monkeypatch):
    """The env-resolved tenant (_default, since CORVIN_TENANT_ID is unset)
    stays ON, but a SECOND, non-default tenant on the same install has
    explicitly opted out — the shared ping must still be suppressed."""
    home = tmp_path / ".corvin"
    _write_tenant_cfg(home, "_default", None)       # no override -> ON
    _write_tenant_cfg(home, "acme-corp", False)      # explicit opt-out

    assert hc.ping_enabled(home) is False, (
        "a non-default tenant's explicit opt-out must suppress the shared "
        "install-wide ping, not just its own tenant's view of it"
    )


def test_all_tenants_enabled_keeps_the_ping_on(tmp_path):
    home = tmp_path / ".corvin"
    _write_tenant_cfg(home, "_default", True)
    _write_tenant_cfg(home, "acme-corp", True)
    assert hc.ping_enabled(home) is True


def test_no_tenant_dirs_at_all_defaults_on(tmp_path):
    home = tmp_path / ".corvin"
    home.mkdir(parents=True, exist_ok=True)
    assert hc.ping_enabled(home) is True


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
