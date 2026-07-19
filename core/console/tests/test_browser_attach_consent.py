"""ADR-0200 Phase 2 — real-chrome attach consent + audit tag."""
from __future__ import annotations

import time
from unittest import mock

import pytest

from corvin_console.browser import attach_consent as ac
from corvin_console.browser import compliance as cmp


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    monkeypatch.setattr(ac._forge_paths, "tenant_global_dir", lambda t: tmp_path)


def test_no_grant_means_inactive():
    assert ac.active("_default") is False
    assert ac.status("_default")["active"] is False


def test_grant_activates_then_expires():
    ac.grant("_default", ttl_s=60)
    assert ac.active("_default") is True
    st = ac.status("_default")
    assert st["active"] and st["remaining_s"] > 0


def test_expired_grant_is_inactive():
    ac.grant("_default", ttl_s=60)
    # move the clock past expiry
    with mock.patch.object(ac, "_now", return_value=time.time() + 3600):
        assert ac.active("_default") is False


def test_revoke_is_immediate():
    ac.grant("_default", ttl_s=3600)
    assert ac.active("_default") is True
    ac.revoke("_default")
    assert ac.active("_default") is False


def test_ttl_is_clamped():
    assert ac.clamp_ttl(1) == ac.MIN_TTL_S            # below floor
    assert ac.clamp_ttl(10**9) == ac.MAX_TTL_S        # above ceiling
    assert ac.clamp_ttl(None) == ac.DEFAULT_TTL_S
    assert ac.clamp_ttl("garbage") == ac.DEFAULT_TTL_S


def test_corrupt_store_is_failclosed(tmp_path):
    ac._path("_default").write_text("{not json")
    assert ac.active("_default") is False


def test_audit_carries_the_attach_tag():
    captured = {}
    def _sink(**kw):
        captured.update(kw)
    cmp.audit_action(_sink, tenant_id="_default", session_id="s1",
                     action="navigate", host="example.com", attach="real-chrome")
    assert captured["details"]["attach"] == "real-chrome"


def test_audit_omits_the_tag_for_launched_mode():
    captured = {}
    cmp.audit_action(lambda **kw: captured.update(kw), tenant_id="_default",
                     session_id="s1", action="navigate", host="example.com", attach="")
    assert "attach" not in captured["details"]
