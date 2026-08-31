"""Privacy-hardening regressions for the telemetry channels.

Covers confirmed findings from the 2026-07-08 review:

  * F3 — an explicit opt-out (consent.json opted_in:false, or
    spec.telemetry.error_traces:false) must WIN over a legacy
    CORVIN_TELEMETRY_OPTIN=1 env opt-in.
  * F4 — a config file that EXISTS but is unparseable must fail toward the
    privacy-preserving state (opted OUT). Default-ON applies only to an ABSENT
    config, never a BROKEN one.
  * F8 — every authenticated telemetry POST enforces https:// and refuses to
    follow redirects.
  * F6 — provision_telemetry_tokens runs its paired write under the shared
    .ping.lock, and does NOT re-acquire it when the caller already holds it.
  * F11 — error_signature.to_repo_path anchors repo roots at a path boundary
    (no "encore/" -> "core/" substring smuggling).

None of these weaken the maintainer's default-ON / opt-out stance — they only
stop an opt-out being ignored or a gate failing OPEN.
"""
from __future__ import annotations

import json
import tempfile
import urllib.request
from pathlib import Path
from unittest.mock import patch

import pytest

from corvin_console.aco import htrace_consent as hc
from corvin_console.aco import telemetry as tele
from corvin_console.aco.error_signature import to_repo_path


@pytest.fixture()
def home(monkeypatch):
    monkeypatch.delenv("CORVIN_TENANT_ID", raising=False)
    monkeypatch.delenv("CORVIN_TELEMETRY_OPTIN", raising=False)
    with tempfile.TemporaryDirectory() as d:
        yield Path(d)


def _write_cfg(home: Path, text: str) -> None:
    p = home / "tenants" / "_default" / "global" / "tenant.corvin.yaml"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


# broken flow-mapping — yaml.safe_load raises on this.
_BROKEN_YAML = "spec:\n  telemetry: {ping_enabled: false\n"


# ── F3 — explicit opt-out wins over env opt-in ────────────────────────────────

class TestOptOutWinsOverEnv:
    def test_env_optin_alone_is_on(self, home, monkeypatch):
        monkeypatch.setenv("CORVIN_TELEMETRY_OPTIN", "1")
        assert tele.consent_granted(home) is True

    def test_consent_file_optout_beats_env_optin(self, home, monkeypatch):
        monkeypatch.setenv("CORVIN_TELEMETRY_OPTIN", "1")
        tele.consent_path(home).parent.mkdir(parents=True, exist_ok=True)
        tele.consent_path(home).write_text(json.dumps({"opted_in": False}), encoding="utf-8")
        assert tele.consent_granted(home) is False

    def test_yaml_error_traces_optout_beats_env_optin(self, home, monkeypatch):
        monkeypatch.setenv("CORVIN_TELEMETRY_OPTIN", "1")
        _write_cfg(home, "spec:\n  telemetry:\n    error_traces: false\n")
        assert tele.consent_granted(home) is False

    def test_default_stays_on_without_any_artifact(self, home):
        assert tele.consent_granted(home) is True

    def test_env_optout_still_disables(self, home, monkeypatch):
        monkeypatch.setenv("CORVIN_TELEMETRY_OPTIN", "false")
        assert tele.consent_granted(home) is False

    def test_consent_file_optin_stays_on(self, home):
        tele.consent_path(home).parent.mkdir(parents=True, exist_ok=True)
        tele.consent_path(home).write_text(json.dumps({"opted_in": True}), encoding="utf-8")
        assert tele.consent_granted(home) is True


# ── F4 — broken config fails toward privacy (opted OUT) ───────────────────────

class TestBrokenConfigFailsClosed:
    def test_absent_config_is_default_on(self, home):
        assert hc.ping_enabled(home) is True
        assert hc.healing_traces_enabled(home) is True
        assert tele.consent_granted(home) is True

    def test_broken_yaml_opts_out_ping(self, home):
        _write_cfg(home, _BROKEN_YAML)
        assert hc.ping_enabled(home) is False

    def test_broken_yaml_opts_out_healing(self, home):
        _write_cfg(home, _BROKEN_YAML)
        assert hc.healing_traces_enabled(home) is False

    def test_broken_yaml_opts_out_error_channel(self, home):
        _write_cfg(home, _BROKEN_YAML)
        assert tele._yaml_error_optout(home) is True
        assert tele.consent_granted(home) is False

    def test_non_mapping_config_opts_out(self, home):
        _write_cfg(home, "just a scalar string")
        assert hc.ping_enabled(home) is False
        assert hc.healing_traces_enabled(home) is False

    def test_read_flag_polarity(self, home):
        cfg = home / "tenants" / "_default" / "global" / "tenant.corvin.yaml"
        # absent → None
        assert hc._read_telemetry_flag(cfg, "ping_enabled") is None
        _write_cfg(home, "spec:\n  telemetry:\n    ping_enabled: true\n")
        assert hc._read_telemetry_flag(cfg, "ping_enabled") is True
        _write_cfg(home, "spec:\n  telemetry:\n    ping_enabled: false\n")
        assert hc._read_telemetry_flag(cfg, "ping_enabled") is False
        _write_cfg(home, _BROKEN_YAML)
        assert hc._read_telemetry_flag(cfg, "ping_enabled") is False  # broken → opted OUT


# ── F8 — https-only + no-redirect on every authenticated POST ─────────────────

class TestHttpsEnforced:
    def test_open_no_redirect_rejects_http(self):
        req = urllib.request.Request("http://example.com/x", data=b"{}", method="POST")
        with pytest.raises(ValueError, match="https"):
            hc._open_no_redirect(req, 5)

    def test_open_no_redirect_rejects_plain_host(self):
        req = urllib.request.Request("ftp://example.com/x", method="POST")
        with pytest.raises(ValueError, match="https"):
            hc._open_no_redirect(req, 5)

    def test_noredirect_handler_returns_none(self):
        h = hc._NoRedirect()
        assert h.redirect_request(None, None, None, None, None) is None

    def test_provision_over_http_endpoint_returns_false(self, home, monkeypatch):
        # A misconfigured http:// token endpoint must never send credentials.
        monkeypatch.setattr(hc, "_TOKEN_ENDPOINT", "http://insecure.example/v1/token")
        assert hc.provision_telemetry_tokens(home, "inst-1") is False

    def test_heartbeat_over_http_url_returns_false(self, home, monkeypatch):
        from corvin_console.aco import heartbeat as hb
        monkeypatch.setattr(hb, "_HEARTBEAT_URL", "http://insecure.example/v1/heartbeat")
        monkeypatch.setattr(hb, "_load_telemetry_token", lambda h: "tok")
        monkeypatch.setattr(hb, "_load_instance_token", lambda h: "itok")
        monkeypatch.setattr(hb, "load_or_create_instance_id", lambda h: "iid")
        assert hb.send_heartbeat(home) is False


# ── F6 — provision runs under the shared lock, re-entrancy-safe ───────────────

class TestProvisionLockReentrancy:
    def test_outer_locked_does_not_deadlock_when_lock_held(self, home):
        """Simulate ping_if_due already holding .ping.lock; provision with
        _outer_locked=True must proceed (not block/deadlock) and write the pair."""
        if not hc._HAS_FLOCK:
            pytest.skip("flock not available")
        import fcntl

        class _Resp:
            def getcode(self):
                return 200

            def read(self):
                return json.dumps({"instance_token": "i", "telemetry_token": "t"}).encode()

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        lock_path = hc._ping_lock_path(home)
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        holder = lock_path.open("w")
        fcntl.flock(holder, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            with patch("urllib.request.OpenerDirector.open", return_value=_Resp()):
                ok = hc.provision_telemetry_tokens(home, "inst-1", _outer_locked=True)
        finally:
            fcntl.flock(holder, fcntl.LOCK_UN)
            holder.close()

        assert ok is True
        assert hc._instance_token_path(home).read_text() == "i"
        assert hc._telemetry_token_path(home).read_text() == "t"


# ── F11 — repo-root anchoring (no substring smuggling) ────────────────────────

class TestRepoPathAnchoring:
    def test_encore_does_not_match_core(self):
        assert to_repo_path("/home/alice/encore/x.py") is None

    def test_real_core_prefix_is_localized(self):
        assert to_repo_path("/opt/app/core/console/x.py") == "core/console/x.py"

    def test_already_relative_core(self):
        assert to_repo_path("core/console/corvin_console/aco/x.py") == \
            "core/console/corvin_console/aco/x.py"

    def test_boundary_at_operator(self):
        assert to_repo_path("/x/operator/forge/y.py") == "operator/forge/y.py"

    def test_word_prefixed_ops_does_not_match(self):
        # "backops/" must not be read as "ops/"
        assert to_repo_path("/srv/backops/tool.py") is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
