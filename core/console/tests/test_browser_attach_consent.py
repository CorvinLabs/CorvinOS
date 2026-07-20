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
    from corvin_console.browser import confirm_mode as _cm
    monkeypatch.setattr(_cm._forge_paths, "tenant_global_dir", lambda t: tmp_path)


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


# ── the REST surface (grant / revoke / status / launch-command) ──────────────

def test_launch_command_endpoint_shape():
    from corvin_console.browser.session import cdp_launch_command
    cmd = cdp_launch_command(9222, None)
    assert "9222" in cmd and "--user-data-dir=" in cmd


def test_grant_then_status_active_then_revoke(monkeypatch, tmp_path):
    # attach_consent already isolated to tmp_path by the autouse fixture.
    assert ac.active("_default") is False
    exp = ac.grant("_default", 3600)
    assert exp > time.time()
    assert ac.active("_default") is True
    ac.revoke("_default")
    assert ac.active("_default") is False


def test_attach_consent_reclamps_crafted_far_future_expiry(tmp_path):
    """Review F3: a tampered store with a far-future expires_at must not become a
    permanent real-login consent — the 12h ceiling is enforced on READ against
    granted_at, and a store with no valid granted_at is fail-closed."""
    import json, time as _t
    # far-future expiry but granted_at well past the 12h ceiling → dead.
    ac._path("_default").write_text(json.dumps(
        {"expires_at": 9e12, "granted_at": _t.time() - (13 * 3600), "revoked": False}))
    assert ac.active("_default") is False
    # no granted_at at all → cannot prove it is within the ceiling → fail-closed.
    ac._path("_default").write_text(json.dumps({"expires_at": 9e12, "revoked": False}))
    assert ac.active("_default") is False
    # a fresh grant with far-future expiry is capped but still active.
    ac._path("_default").write_text(json.dumps(
        {"expires_at": 9e12, "granted_at": _t.time(), "revoked": False}))
    st = ac.status("_default")
    assert st["active"] is True and 0 < st["remaining_s"] <= ac.MAX_TTL_S


# ── confirm-mode (Q3): watch-mode auto-approve, TTL-bounded, attach-only ──────

def test_confirm_mode_defaults_to_confirm_each():
    from corvin_console.browser import confirm_mode as cm
    assert cm.status("_default")["mode"] == "confirm-each"
    assert cm.should_auto_approve("_default") is False


def test_watch_mode_auto_approves_until_ttl():
    from corvin_console.browser import confirm_mode as cm
    cm.set_watch("_default", ttl_s=300)
    assert cm.should_auto_approve("_default") is True
    with mock.patch.object(cm, "_now", return_value=time.time() + 10_000):
        assert cm.should_auto_approve("_default") is False   # hard TTL


def test_watch_ttl_is_clamped():
    from corvin_console.browser import confirm_mode as cm
    assert cm._clamp(1) == cm.WATCH_MIN_TTL_S
    assert cm._clamp(10**9) == cm.WATCH_MAX_TTL_S            # 30m ceiling
    assert cm._clamp(None) == cm.WATCH_DEFAULT_TTL_S


def test_confirm_each_reverts_watch():
    from corvin_console.browser import confirm_mode as cm
    cm.set_watch("_default", ttl_s=300)
    assert cm.should_auto_approve("_default") is True
    cm.set_confirm_each("_default")
    assert cm.should_auto_approve("_default") is False


def test_corrupt_confirm_mode_store_is_failclosed_to_ask():
    from corvin_console.browser import confirm_mode as cm
    cm._path("_default").write_text("{bad")
    assert cm.should_auto_approve("_default") is False


# ── manager enforcement: watch-mode auto-approves ONLY attached sessions ─────

def _mk_manager(tmp_path):
    from corvin_console.browser.manager import BrowserSessionManager
    return BrowserSessionManager(home_resolver=lambda t: tmp_path,
                                 allowlist_resolver=lambda t: (None, None))


def test_manager_confirm_auto_approves_attached_watch(tmp_path, monkeypatch):
    import asyncio
    from corvin_console.browser import confirm_mode as cm
    ac.grant("_default", 3600)   # attach now requires active consent (review fix)
    mgr = _mk_manager(tmp_path)
    sid = asyncio.run(mgr.create("_default", cdp_endpoint="ws://x/y"))
    live = mgr._sessions[f"_default:{sid}"]
    assert live.session._attached is True
    cm.set_watch("_default", ttl_s=300)
    # the confirm broker is the session's confirm_fn (the manager closure)
    approved = asyncio.run(live.session._confirm(
        action="click", host="bank.example", role="button", name="Pay"))
    assert approved is True                      # watch-mode auto-approved
    # audited as an auto-approve, not a human confirm
    assert any(e.get("action") == "confirm_auto_watch" for e in live.actions)


def test_manager_confirm_does_NOT_auto_approve_launched_watch(tmp_path):
    import asyncio
    from corvin_console.browser import confirm_mode as cm
    mgr = _mk_manager(tmp_path)
    sid = asyncio.run(mgr.create("_default"))    # launched, NOT attached
    live = mgr._sessions[f"_default:{sid}"]
    assert live.session._attached is False
    cm.set_watch("_default", ttl_s=300)
    # a launched session must still ASK — the broker creates a pending future and
    # waits; with no answer it fail-closed declines (times out). Assert it does
    # NOT short-circuit to True: run with a tiny timeout via the pending path.
    async def _call():
        # patch the confirm timeout to near-zero so the "asks then declines" path
        # returns quickly instead of blocking the test.
        import corvin_console.browser.manager as m
        orig = m._CONFIRM_TIMEOUT_S
        m._CONFIRM_TIMEOUT_S = 0.05
        try:
            return await live.session._confirm(
                action="click", host="bank.example", role="button", name="Pay")
        finally:
            m._CONFIRM_TIMEOUT_S = orig
    assert asyncio.run(_call()) is False         # launched + watch → still asks (declines)
    assert not any(e.get("action") == "confirm_auto_watch" for e in live.actions)


# ── adversarial-review hardening fixes ───────────────────────────────────────

def test_confirm_mode_is_owner_scoped(tmp_path, monkeypatch):
    """Finding 3: user A's watch-mode must NOT auto-approve user B's session."""
    from corvin_console.browser import confirm_mode as cm
    cm.set_watch("_default", ttl_s=300, owner="userA")
    assert cm.should_auto_approve("_default", "userA") is True
    assert cm.should_auto_approve("_default", "userB") is False   # different owner
    assert cm.should_auto_approve("_default", "") is False        # tenant-wide legacy


def test_confirm_mode_crafted_far_future_without_set_at_is_failclosed(tmp_path):
    """Review F2: the previous reclamp capped only the REPORTED remaining — a
    crafted far-future expires_at then read as active-watch FOREVER. Now a store
    with no valid set_at (i.e. hand-crafted, never written by set_watch) is
    fail-closed to confirm-each: it cannot prove it is within the 30-min ceiling."""
    import json
    from corvin_console.browser import confirm_mode as cm
    cm._path("_default", "u").write_text(json.dumps({"mode": "watch", "expires_at": 9e12}))
    st = cm.status("_default", "u")
    assert st["mode"] == "confirm-each"
    assert st["remaining_s"] == 0
    assert cm.should_auto_approve("_default", "u") is False


def test_confirm_mode_reclamps_far_future_expiry_against_set_at(tmp_path):
    """Review F2: even WITH a set_at, a crafted far-future expires_at is bounded
    at set_at + WATCH_MAX_TTL_S — a stale watch (set long ago) is expired, not
    perpetually active."""
    import json, time as _t
    from corvin_console.browser import confirm_mode as cm
    # set_at an hour ago (> 30-min ceiling) + a far-future expiry → must be dead.
    cm._path("_default", "u").write_text(json.dumps(
        {"mode": "watch", "expires_at": 9e12, "set_at": _t.time() - 3600}))
    st = cm.status("_default", "u")
    assert st["mode"] == "confirm-each" and st["remaining_s"] == 0
    # A fresh set_at with far-future expiry is capped to the ceiling, still active.
    cm._path("_default", "u").write_text(json.dumps(
        {"mode": "watch", "expires_at": 9e12, "set_at": _t.time()}))
    st2 = cm.status("_default", "u")
    assert st2["mode"] == "watch"
    assert 0 < st2["remaining_s"] <= cm.WATCH_MAX_TTL_S


def test_confirm_mode_non_numeric_expiry_is_failclosed(tmp_path):
    import json
    from corvin_console.browser import confirm_mode as cm
    cm._path("_default", "u").write_text(json.dumps({"mode": "watch", "expires_at": "abc"}))
    assert cm.should_auto_approve("_default", "u") is False   # no longer raises


def test_loopback_cdp_validator():
    from corvin_console.routes.browser import _is_loopback_cdp as f
    assert f("ws://127.0.0.1:9222/devtools/browser/x") is True
    assert f("ws://localhost:9222/x") is True
    assert f("ws://[::1]:9222/x") is True
    assert f("ws://169.254.169.254:9222/x") is False   # metadata
    assert f("ws://attacker.com:9222/x") is False       # remote
    assert f("ws://10.0.0.5:9222/x") is False           # LAN
    assert f("wss://127.0.0.1:9222/x") is False          # wrong scheme
    assert f("http://127.0.0.1:9222/x") is False


def test_lapsed_consent_refuses_further_actions(tmp_path, monkeypatch):
    """Findings 1+2: an attached session must STOP acting when consent expires
    or is revoked — not just refuse NEW sessions."""
    import asyncio
    from corvin_console.browser.session import BrowserSession, BrowserActionError
    live = {"ok": True}
    s = BrowserSession("s", "_default", home=tmp_path,
                       cdp_endpoint="ws://127.0.0.1:9222/x",
                       consent_ok=lambda: live["ok"])
    s._guard_active("navigate")            # consent live → allowed
    live["ok"] = False                      # consent revoked/expired mid-session
    try:
        s._guard_active("click")
        raise AssertionError("a lapsed consent must refuse the action")
    except BrowserActionError as e:
        assert "consent expired" in str(e)


def test_screenshot_refuses_lapsed_consent_and_attach_pause(tmp_path):
    """B1 (adversarial review 2026-07-20): screenshot() was the ONLY action
    without a guard — a revoked/expired attach consent or an attach take-over
    pause kept serving live JPEGs of the user's real Chrome via the REST/MCP
    pull path, the exact leak class the F7 screencast hardening closed."""
    import asyncio
    from corvin_console.browser.session import BrowserSession, BrowserActionError
    live = {"ok": True}
    s = BrowserSession("s", "_default", home=tmp_path,
                       cdp_endpoint="ws://127.0.0.1:9222/x",
                       consent_ok=lambda: live["ok"])
    live["ok"] = False
    with pytest.raises(BrowserActionError, match="consent expired"):
        asyncio.run(s.screenshot())
    live["ok"] = True
    s.paused = True
    with pytest.raises(BrowserActionError, match="paused"):
        asyncio.run(s.screenshot())


def test_screenshot_still_serves_paused_managed_session(tmp_path):
    """Counterpart to B1: a paused MANAGED (launched) session must keep
    serving frames — the take-over live view depends on the screenshot pull.
    Only the attach mode refuses while paused (screencast-loop parity)."""
    import asyncio
    from corvin_console.browser.session import BrowserSession
    s = BrowserSession("s", "_default", home=tmp_path)
    assert s._attached is False
    s.paused = True

    async def _no_start():
        return None

    async def _fake_shot(*, marks=True):
        return b"jpeg"

    s._ensure_started = _no_start  # type: ignore[method-assign]
    s._screenshot_locked = _fake_shot  # type: ignore[method-assign]
    assert asyncio.run(s.screenshot()) == b"jpeg"


def test_ws_egress_gate_is_registered():
    """Finding 1: session wires a WebSocket egress route (context.route alone
    does NOT cover WS handshakes)."""
    import inspect
    from corvin_console.browser import session as _s
    src = inspect.getsource(_s.BrowserSession._finish_start)
    assert "route_web_socket" in src
    assert hasattr(_s.BrowserSession, "_route_web_socket")


class _FakeWs:
    def __init__(self, url):
        self.url = url
        self.connected = False
        self.closed = False

    def connect_to_server(self):
        self.connected = True

    def close(self):
        self.closed = True


def test_ws_egress_gate_behavior(tmp_path):
    """B2 (adversarial review 2026-07-20): the WS gate previously closed EVERY
    WebSocket — check_egress hard-rejects the ws/wss scheme — so the documented
    'Allowed → proxy to the real server' path was unreachable and any WS-using
    page (live chat, streaming) silently broke. The gate must apply the SAME
    host policy as HTTP: allowlisted host connects, everything else closes."""
    from corvin_console.browser.session import BrowserSession

    s = BrowserSession("s", "_default", home=tmp_path,
                       allowlist=["example.com"])
    ok = _FakeWs("wss://example.com/chat")
    s._route_web_socket(ok)
    assert ok.connected and not ok.closed

    for bad_url in (
        "wss://attacker.com/exfil",          # off-allowlist
        "ws://169.254.169.254/latest",        # metadata IP
        "ws://10.0.0.5:9222/x",               # private LAN
        "ftp://example.com/x",                # non-WS scheme stays rejected
    ):
        bad = _FakeWs(bad_url)
        s._route_web_socket(bad)
        assert bad.closed and not bad.connected, bad_url
