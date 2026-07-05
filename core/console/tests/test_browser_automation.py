"""E2E + unit tests for the Browser Automation Layer (ADR-0182).

The E2E tests drive a REAL headless Chromium (Playwright) against a local
throwaway HTTP server — navigate, Set-of-Marks observe, fill, sensitive-click
with human-in-the-loop confirm, live screencast frame, pause/take-over — and
assert the compliance invariants (metadata-only audit, no value leak).

Skipped automatically if Playwright's Chromium is not installed.
"""
from __future__ import annotations

import asyncio
import http.server
import socketserver
import tempfile
import threading
from pathlib import Path

import pytest

from corvin_console.browser import check_egress
from corvin_console.browser.compliance import is_sensitive

# ── Set-of-Marks + compliance units (no browser needed) ──────────────────────

def test_egress_allowlist_semantics():
    assert check_egress("https://ex.com/x", allowlist=["ex.com"], forbidden=None).allowed
    assert check_egress("https://www.ex.com", allowlist=["ex.com"], forbidden=None).allowed  # suffix
    assert not check_egress("https://evil.com", allowlist=["ex.com"], forbidden=None).allowed
    assert not check_egress("https://bank.com", allowlist=None, forbidden=["bank.com"]).allowed
    assert not check_egress("file:///etc/passwd", allowlist=None, forbidden=None).allowed
    assert check_egress("https://anything.com", allowlist=None, forbidden=None).allowed


def test_sensitive_action_classification():
    assert is_sensitive("click", role="button", name="Buy now")
    assert is_sensitive("click", role="button", name="Sign in")
    assert is_sensitive("click", role="button", name="Delete account")
    assert not is_sensitive("click", role="button", name="Read more")
    assert not is_sensitive("fill", role="textbox", name="Buy now")   # typing is never sensitive


# ── live E2E with a real browser ─────────────────────────────────────────────

def _has_chromium() -> bool:
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            b = p.chromium.launch(headless=True)
            b.close()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _has_chromium(), reason="playwright chromium not installed")

_HTML = b"""<!doctype html><html><head><title>Shop</title></head><body>
<input id="q" name="q" placeholder="Search products">
<input id="pw" type="password" name="pw" placeholder="Password">
<button id="buy">Buy now</button>
<button id="help">Read more</button>
</body></html>"""


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(_HTML)

    def log_message(self, *a):
        pass


@pytest.fixture()
def server():
    socketserver.TCPServer.allow_reuse_address = True
    httpd = socketserver.TCPServer(("127.0.0.1", 0), _Handler)
    port = httpd.server_address[1]
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{port}/"
    httpd.shutdown()


def test_browser_e2e_full(server):
    audit: list[dict] = []

    async def run():
        from corvin_console.browser import BrowserSessionManager
        home = Path(tempfile.mkdtemp())
        mgr = BrowserSessionManager(
            home_resolver=lambda t: home / t,
            audit_fn=lambda **kw: audit.append(kw),
            vault_resolver=lambda t, k: "hunter2" if k == "pw" else None,
            allowlist_resolver=lambda t: (None, None),
        )
        sid = await mgr.create("_default", headless=True)
        s = mgr.session("_default", sid)

        obs = await s.navigate(server)
        names = {m.name.lower(): m.index for m in obs.marks}
        # password field must NOT be surfaced as a mark
        assert not any(m.role == "password" for m in obs.marks)
        assert "search products" in names

        await s.fill(names["search products"], "alice@example.com")

        # 'Buy now' is sensitive → click parks a confirm; approve it concurrently.
        buy = names["buy now"]

        async def approve():
            for _ in range(60):
                p = mgr.pending("_default", sid)
                if p:
                    mgr.resolve_confirm("_default", sid, p[0]["id"], True)
                    return True
                await asyncio.sleep(0.05)
            return False

        click_task = asyncio.ensure_future(s.click(buy))
        assert await approve()
        await click_task

        # live view: screencast frame + action log
        await asyncio.sleep(1.0)
        assert mgr.frame("_default", sid) is not None
        log = [a["action"] for a in mgr.actions("_default", sid)]
        assert "confirm_request" in log and "confirm_resolved" in log and "click" in log

        # pause / take-over blocks agent actions (fail-closed)
        mgr.set_paused("_default", sid, True)
        from corvin_console.browser import BrowserActionError
        with pytest.raises(BrowserActionError):
            await s.fill(names["search products"], "x")

        await mgr.close("_default", sid)

    asyncio.run(run())

    # metadata-only audit: the typed value + the vault secret never leak
    blob = str(audit)
    assert "alice@example.com" not in blob
    assert "hunter2" not in blob
    # but the action metadata IS present
    assert any(a.get("details", {}).get("action") == "fill" for a in audit)
    assert any(a.get("details", {}).get("action") == "navigate" for a in audit)


def test_fill_value_never_surfaces_in_marks(server):
    """C2/H1 regression: a typed value must NOT be echoed back as a mark name on
    the next observe (which would leak it into the model context)."""
    async def run():
        from corvin_console.browser import BrowserSessionManager
        home = Path(tempfile.mkdtemp())
        mgr = BrowserSessionManager(home_resolver=lambda t: home / t,
                                    allowlist_resolver=lambda t: (None, None))
        sid = await mgr.create("_default", headless=True)
        s = mgr.session("_default", sid)
        obs = await s.navigate(server)
        q = next(m.index for m in obs.marks if "search" in m.name.lower())
        await s.fill(q, "SUPERSECRETVALUE-123")
        obs2 = await s.observe()
        blob = " ".join(m.name for m in obs2.marks) + obs2.as_text()
        assert "SUPERSECRETVALUE" not in blob
        await mgr.close("_default", sid)
    asyncio.run(run())


def test_click_link_to_off_allowlist_host_is_blocked():
    """C1 regression: a click that navigates to an off-allowlist host is refused."""
    import http.server as _h, socketserver as _s, threading as _t

    html = b'<!doctype html><html><body><a id="x" href="http://localhost:%d/">go</a></body></html>'

    class _H(_h.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200); self.send_header("Content-Type", "text/html"); self.end_headers()
            self.wfile.write(html % self.server.server_address[1])
        def log_message(self, *a): pass

    _s.TCPServer.allow_reuse_address = True
    httpd = _s.TCPServer(("127.0.0.1", 0), _H)
    port = httpd.server_address[1]
    _t.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        async def run():
            from corvin_console.browser import BrowserSessionManager, BrowserActionError
            home = Path(tempfile.mkdtemp())
            # allow ONLY 127.0.0.1 — the link points at localhost (different host)
            mgr = BrowserSessionManager(home_resolver=lambda t: home / t,
                                        allowlist_resolver=lambda t: (["127.0.0.1"], None))
            sid = await mgr.create("_default", headless=True)
            s = mgr.session("_default", sid)
            obs = await s.navigate(f"http://127.0.0.1:{port}/")
            link = next(m.index for m in obs.marks if m.role == "link")
            with pytest.raises(BrowserActionError):
                await s.click(link)   # navigates to localhost → off-allowlist → blocked
            await mgr.close("_default", sid)
        asyncio.run(run())
    finally:
        httpd.shutdown()


def test_action_log_survives_deque_rollover():
    """HIGH regression: actions(since) keeps delivering after >200 events using a
    monotonic sequence cursor (not a buffer index)."""
    from corvin_console.browser.manager import _Live

    live = _Live(session=None)  # type: ignore[arg-type]
    for i in range(500):
        live.append({"action": "x", "i": i})
    assert live.emitted == 500

    # simulate a client cursor: it should never get stuck and never miss the tail
    from corvin_console.browser import BrowserSessionManager
    mgr = BrowserSessionManager(home_resolver=lambda t: Path("/tmp"))
    mgr._sessions["_default:s"] = live
    tail = mgr.actions("_default", "s", since=495)
    assert [r["i"] for r in tail] == [495, 496, 497, 498, 499]
    assert mgr.next_seq("_default", "s") == 500


def test_declined_sensitive_click_is_blocked(server):
    async def run():
        from corvin_console.browser import BrowserSessionManager, BrowserActionError
        home = Path(tempfile.mkdtemp())
        mgr = BrowserSessionManager(home_resolver=lambda t: home / t,
                                    allowlist_resolver=lambda t: (None, None))
        sid = await mgr.create("_default", headless=True)
        s = mgr.session("_default", sid)
        obs = await s.navigate(server)
        buy = next(m.index for m in obs.marks if m.name.lower() == "buy now")

        async def decline():
            for _ in range(60):
                p = mgr.pending("_default", sid)
                if p:
                    mgr.resolve_confirm("_default", sid, p[0]["id"], False)
                    return
                await asyncio.sleep(0.05)

        task = asyncio.ensure_future(s.click(buy))
        await decline()
        with pytest.raises(BrowserActionError):
            await task
        await mgr.close("_default", sid)

    asyncio.run(run())
