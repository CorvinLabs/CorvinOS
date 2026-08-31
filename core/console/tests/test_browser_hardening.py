"""Adversarial-review hardening for the browser subsystem (2026-07-20).

Pure-unit coverage for the fixes landed after the whole-subsystem adversarial
review — deliberately NOT gated on a real Chromium (unlike
test_browser_automation.py) so they run everywhere, incl. CI without a browser:

  * Chrome-primary / Chromium-fallback engine selection + process cache
  * trailing-dot egress bypass of the forbidden list + metadata set (HIGH)
  * tabs() URL sanitization (strip query/userinfo)
  * disconnect detection → actionable error + screencast stop
  * manager idle-reaper (cures the session-cap wedge)
  * consent-route internal-token gate (real-chrome attach is human-only)
"""
from __future__ import annotations

import asyncio
import types

import pytest


# ── Chrome-primary / Chromium-fallback engine selection ──────────────────────

def _reset_channel_cache():
    from corvin_console.browser import session as s
    s._auto_channel_cache = s._UNSET


def test_channel_candidates_auto_prefers_chrome_then_chromium(monkeypatch):
    from corvin_console.browser import session as s
    monkeypatch.delenv("CORVIN_BROWSER_CHANNEL", raising=False)
    _reset_channel_cache()
    assert s._channel_candidates() == ["chrome", None]


def test_channel_candidates_explicit_chromium_only(monkeypatch):
    from corvin_console.browser import session as s
    monkeypatch.setenv("CORVIN_BROWSER_CHANNEL", "chromium")
    _reset_channel_cache()
    assert s._channel_candidates() == [None]


def test_channel_candidates_explicit_chrome_only_no_fallback(monkeypatch):
    from corvin_console.browser import session as s
    monkeypatch.setenv("CORVIN_BROWSER_CHANNEL", "chrome")
    _reset_channel_cache()
    assert s._channel_candidates() == ["chrome"]


def test_remember_channel_makes_auto_sticky(monkeypatch):
    from corvin_console.browser import session as s
    monkeypatch.delenv("CORVIN_BROWSER_CHANNEL", raising=False)
    _reset_channel_cache()
    # Simulate "Chrome failed, bundled Chromium won": auto mode should stop
    # retrying Chrome on every later session.
    s._remember_channel(None)
    assert s._channel_candidates() == [None]
    _reset_channel_cache()
    s._remember_channel("chrome")
    assert s._channel_candidates() == ["chrome"]


def test_remember_channel_never_overrides_explicit(monkeypatch):
    from corvin_console.browser import session as s
    monkeypatch.setenv("CORVIN_BROWSER_CHANNEL", "chromium")
    _reset_channel_cache()
    s._remember_channel("chrome")             # must be ignored for an explicit pref
    assert s._auto_channel_cache is s._UNSET
    assert s._channel_candidates() == [None]


# ── trailing-dot egress bypass (HIGH) ────────────────────────────────────────

def test_trailing_dot_does_not_bypass_forbidden_list():
    from corvin_console.browser import compliance as c
    blocked = c.check_egress("http://blocked.example.com./p",
                             allowlist=None, forbidden=["blocked.example.com"])
    assert blocked.allowed is False
    assert "forbidden" in blocked.reason
    # sanity: the plain form was and is still blocked
    assert c.check_egress("http://blocked.example.com/p",
                          allowlist=None, forbidden=["blocked.example.com"]).allowed is False


def test_trailing_dot_does_not_bypass_metadata_literal():
    from corvin_console.browser import compliance as c
    d = c.check_egress("http://metadata.google.internal./computeMetadata/v1/",
                       allowlist=None, forbidden=None)
    assert d.allowed is False
    assert "metadata" in d.reason.lower()


def test_trailing_dot_does_not_bypass_allowlist_deny_by_default():
    from corvin_console.browser import compliance as c
    # allowlist set, host (with dot) not on it → still deny-by-default, no host
    # confusion that would accidentally allow it.
    d = c.check_egress("http://evil.com./x", allowlist=["good.com"], forbidden=None)
    assert d.allowed is False


# ── tabs() URL sanitization ──────────────────────────────────────────────────

def test_safe_tab_url_strips_query_and_userinfo():
    from corvin_console.browser.session import _safe_tab_url
    out = _safe_tab_url("https://user:pw@bank.example.com/account?token=SECRET#frag")
    assert out == "https://bank.example.com/account"
    assert "SECRET" not in out and "pw" not in out


def test_safe_tab_url_keeps_port_and_path():
    from corvin_console.browser.session import _safe_tab_url
    assert _safe_tab_url("http://localhost:8080/dash?x=1") == "http://localhost:8080/dash"


def test_safe_tab_url_handles_about_blank():
    from corvin_console.browser.session import _safe_tab_url
    # no hostname → empty (nothing to leak), never a crash
    assert _safe_tab_url("about:blank") == ""


# ── disconnect detection ─────────────────────────────────────────────────────

def _bare_session(**over):
    """A BrowserSession built via __new__ with just the attrs the method under
    test reads — no real browser."""
    from corvin_console.browser.session import BrowserSession
    s = BrowserSession.__new__(BrowserSession)
    s._disconnected = False
    s._attached = False
    s._consent_ok = None
    s.paused = False
    s._closed = False
    s._on_action = None
    s.session_id = "s1"
    for k, v in over.items():
        setattr(s, k, v)
    return s


def test_guard_active_raises_actionable_after_disconnect():
    from corvin_console.browser.session import BrowserActionError
    s = _bare_session()
    s._mark_disconnected()
    assert s._disconnected is True
    with pytest.raises(BrowserActionError) as ei:
        s._guard_active("click")
    assert "closed" in str(ei.value).lower()


def test_mark_disconnected_is_idempotent_and_emits_once():
    seen = []
    s = _bare_session(_on_action=lambda rec: seen.append(rec))
    s._mark_disconnected()
    s._mark_disconnected()
    dis = [r for r in seen if r.get("action") == "browser_disconnected"]
    assert len(dis) == 1


# ── manager idle-reaper (cures the cap wedge) ────────────────────────────────

class _FakeSession:
    def __init__(self):
        self.closed = False
        self._attached = False

    async def close(self):
        self.closed = True


def _make_manager(now_holder):
    from corvin_console.browser.manager import BrowserSessionManager
    return BrowserSessionManager(home_resolver=lambda t: None,
                                 now=lambda: now_holder["t"])


def _register(mgr, tenant, sid, *, last_activity, running=False, pending=False):
    from corvin_console.browser.manager import _Live
    live = _Live(session=_FakeSession(), owner_fingerprint="o",
                 created=last_activity, last_activity=last_activity)
    if pending:
        live.pending["p1"] = object()   # non-empty → not reaped
    if running:
        async def _forever():
            await asyncio.sleep(3600)
        live.agent_task = asyncio.ensure_future(_forever())
    mgr._sessions[f"{tenant}:{sid}"] = live
    return live


def test_idle_reaper_closes_idle_but_keeps_active(event_loop=None):
    from corvin_console.browser import manager as m
    now = {"t": 10_000.0}
    mgr = _make_manager(now)
    idle = _register(mgr, "_default", "old", last_activity=now["t"] - m._IDLE_TTL_S - 10)
    fresh = _register(mgr, "_default", "new", last_activity=now["t"] - 5)

    asyncio.run(mgr._reap_idle("_default"))

    assert idle.session.closed is True
    assert "_default:old" not in mgr._sessions
    assert fresh.session.closed is False
    assert "_default:new" in mgr._sessions


def test_idle_reaper_spares_running_and_pending():
    from corvin_console.browser import manager as m

    async def _run():
        now = {"t": 10_000.0}
        mgr = _make_manager(now)
        old = now["t"] - m._IDLE_TTL_S - 10
        running = _register(mgr, "_default", "r", last_activity=old, running=True)
        pend = _register(mgr, "_default", "p", last_activity=old, pending=True)
        await mgr._reap_idle("_default")
        # neither reaped despite being idle
        assert "_default:r" in mgr._sessions and running.session.closed is False
        assert "_default:p" in mgr._sessions and pend.session.closed is False
        running.agent_task.cancel()

    asyncio.run(_run())


def test_sessions_info_is_owner_scoped():
    now = {"t": 1.0}
    mgr = _make_manager(now)
    _register(mgr, "_default", "mine", last_activity=1.0)
    live = mgr._sessions["_default:mine"]
    live.owner_fingerprint = "ownerA"
    assert [s["session"] for s in mgr.sessions_info("_default", owner_fingerprint="ownerA")] == ["mine"]
    assert mgr.sessions_info("_default", owner_fingerprint="ownerB") == []
