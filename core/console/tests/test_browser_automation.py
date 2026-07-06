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


# ── Sensitivity model v2 (ADR-0183 S1) ───────────────────────────────────────

def test_sensitive_v2_url_path_signal():
    """An ambiguously-labelled commit button ("Continue") is now sensitive when
    the CURRENT page is on a checkout/payment/delete/security/billing path,
    even though its own accessible name matches no v1 keyword."""
    assert is_sensitive("click", role="button", name="Continue",
                        url="https://shop.example.com/checkout")
    assert is_sensitive("click", role="button", name="OK",
                        url="https://example.com/settings/security")
    assert is_sensitive("click", role="button", name="Confirm",
                        url="https://example.com/billing/invoice/42")
    # no url signal, no keyword match → still not sensitive (unchanged v1 behavior)
    assert not is_sensitive("click", role="button", name="Continue",
                            url="https://example.com/help")
    assert not is_sensitive("click", role="button", name="Continue")  # url defaults to ""


def test_sensitive_v2_form_context_signal():
    """Any click/submit inside a form that itself contains a password/card
    field is sensitive regardless of the clicked element's own label."""
    assert is_sensitive("click", role="button", name="Continue",
                        form_has_sensitive_field=True)
    assert is_sensitive("submit", role="button", name="OK",
                        form_has_sensitive_field=True)
    # fill is still NEVER auto-sensitive, even with the form-context hint True —
    # typing is reversible; it is the eventual click/submit that commits.
    assert not is_sensitive("fill", role="textbox", name="anything",
                            form_has_sensitive_field=True)


def test_sensitive_v2_signature_backward_compatible():
    """Existing call sites that only pass action/role/name keep working —
    the new url/form_has_sensitive_field kwargs are optional and default to
    a no-signal state."""
    assert is_sensitive("click", role="button", name="Buy now")
    assert not is_sensitive("click", role="button", name="Read more")


def test_sensitive_v2_checkout_e2e():
    """E2E: a real page with an ambiguously-labelled 'Continue' button served
    at a /checkout path is classified sensitive by the session's own click()
    wiring (url signal), and a decline blocks the click."""
    import http.server as _h
    import socketserver as _s
    import threading as _t

    html = (b"<!doctype html><html><body>"
            b'<button id="c">Continue</button></body></html>')

    class _H(_h.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(html)

        def log_message(self, *a):
            pass

    _s.TCPServer.allow_reuse_address = True
    httpd = _s.TCPServer(("127.0.0.1", 0), _H)
    port = httpd.server_address[1]
    _t.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        async def run():
            from corvin_console.browser import BrowserSessionManager, BrowserActionError
            home = Path(tempfile.mkdtemp())
            mgr = BrowserSessionManager(home_resolver=lambda t: home / t,
                                        allowlist_resolver=lambda t: (None, None))
            sid = await mgr.create("_default", headless=True)
            s = mgr.session("_default", sid)
            obs = await s.navigate(f"http://127.0.0.1:{port}/checkout")
            continue_btn = next(m.index for m in obs.marks if m.name == "Continue")

            async def decline():
                for _ in range(60):
                    p = mgr.pending("_default", sid)
                    if p:
                        mgr.resolve_confirm("_default", sid, p[0]["id"], False)
                        return
                    await asyncio.sleep(0.05)

            task = asyncio.ensure_future(s.click(continue_btn))
            await decline()
            with pytest.raises(BrowserActionError):
                await task   # blocked: /checkout path made the ambiguous button sensitive
            await mgr.close("_default", sid)

        asyncio.run(run())
    finally:
        httpd.shutdown()


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


def test_agent_loop_drives_browser(server):
    """The browser-agent loop executes a planner's actions end-to-end and stops
    on 'done'. Uses a deterministic planner (no LLM) so it's fast + hermetic."""
    async def run():
        from corvin_console.browser import BrowserSession
        from corvin_console.browser.agent import BrowserAgent
        s = BrowserSession("ag", "_default", home=Path(tempfile.mkdtemp()), headless=True)
        await s.start()
        await s.navigate(server)
        events = []
        script = iter([
            {"action": "fill", "index": 0, "text": "hello", "reason": "type"},
            {"action": "done", "reason": "typed the query"},
        ])

        async def planner(task, obs, transcript):
            return next(script)

        agent = BrowserAgent(s, planner=planner, on_step=lambda r: events.append(r["action"]))
        result = await agent.run("type hello into the search box")
        await s.close()
        assert result["status"] == "done"
        assert "agent_start" in events and "agent_done" in events

    asyncio.run(run())


def test_agent_cross_host_navigate_requires_confirm(server):
    """Injection defense: with no allowlist, the agent's cross-host navigate is
    confirm-gated; a decline blocks it. Manual navigate (no flag) is never gated."""
    async def run():
        from corvin_console.browser import BrowserSession, BrowserActionError
        home = Path(tempfile.mkdtemp())
        declines = []

        async def confirm(**kw):
            declines.append(kw)
            return False   # decline the cross-host jump

        s = BrowserSession("xh", "_default", home=home, headless=True,
                           allowlist=None, confirm_fn=confirm)
        await s.start()
        await s.navigate(server)                      # first load (no gate)
        # a cross-host navigate via the agent flag → confirm asked → declined → blocked
        with pytest.raises(BrowserActionError):
            await s.navigate("https://example.com", confirm_cross_host=True)
        assert declines and declines[0]["action"] == "navigate"
        # manual navigate (no flag) is NOT gated
        await s.navigate("https://example.com")       # should succeed (no confirm)
        await s.close()

    asyncio.run(run())


def test_agent_action_parser():
    from corvin_console.browser.agent import _parse_action
    assert _parse_action('{"action":"click","index":3}')["action"] == "click"
    assert _parse_action('reasoning... {"action":"done","reason":"ok"} trailing')["action"] == "done"
    assert _parse_action("not json at all")["action"] == "done"   # fail-safe
    assert _parse_action("")["action"] == "done"


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


# ── Stale-mark self-healing (ADR-0183 S1) ────────────────────────────────────

def test_click_on_unchanged_mark_still_works(server):
    """Baseline: a click on a valid, unchanged mark works exactly as before —
    the freshness check must not false-positive on a stable page."""
    async def run():
        from corvin_console.browser import BrowserSession
        s = BrowserSession("fresh", "_default", home=Path(tempfile.mkdtemp()), headless=True)
        await s.start()
        obs = await s.navigate(server)
        help_btn = next(m.index for m in obs.marks if m.name.lower() == "read more")
        await s.click(help_btn)   # must not raise
        await s.close()

    asyncio.run(run())


def test_stale_mark_after_dom_mutation_raises(server):
    """Core S1 regression: if the DOM changes in place between observe() and
    the act (e.g. an SPA re-renders the same index under a different control),
    the action must raise StaleMarkError instead of silently acting on the
    now-wrong element."""
    async def run():
        from corvin_console.browser import BrowserSession
        from corvin_console.browser.session import StaleMarkError
        s = BrowserSession("stale", "_default", home=Path(tempfile.mkdtemp()), headless=True)
        await s.start()
        obs = await s.navigate(server)
        # "Read more" is NOT sensitive (unlike "Buy now") — isolates the
        # stale-mark check from the separate sensitive-click confirm gate.
        help_btn = next(m.index for m in obs.marks if m.name.lower() == "read more")

        # Mutate the DOM in place: swap the button's accessible name (its
        # aria-label) WITHOUT calling observe() again — simulates an SPA
        # in-place re-render that leaves the same data-corvin-mark index
        # pointing at what is now logically a different control.
        await s._require_page().evaluate(
            "(i) => document.querySelector(`[data-corvin-mark=\"${i}\"]`)"
            ".setAttribute('aria-label', 'Something completely different')",
            help_btn,
        )
        with pytest.raises(StaleMarkError):
            await s.click(help_btn)
        await s.close()

    asyncio.run(run())


def test_stale_mark_error_is_a_browser_action_error(server):
    """StaleMarkError must remain catchable by existing BrowserActionError
    handlers (routes/browser.py's ``_act`` maps it to HTTP 409) — it is a
    subtype, not a parallel/incompatible exception hierarchy."""
    from corvin_console.browser import BrowserActionError, StaleMarkError
    assert issubclass(StaleMarkError, BrowserActionError)


def test_mark_not_found_still_raised_for_removed_element(server):
    """The pre-existing 'mark not found' case (element removed entirely, not
    just relabelled) must still raise plain BrowserActionError, not
    StaleMarkError — the two failure modes stay distinguishable."""
    async def run():
        from corvin_console.browser import BrowserSession, BrowserActionError
        from corvin_console.browser.session import StaleMarkError
        s = BrowserSession("removed", "_default", home=Path(tempfile.mkdtemp()), headless=True)
        await s.start()
        obs = await s.navigate(server)
        help_btn = next(m.index for m in obs.marks if m.name.lower() == "read more")
        await s._require_page().evaluate(
            "(i) => document.querySelector(`[data-corvin-mark=\"${i}\"]`)?.remove()", help_btn)
        try:
            await s.click(help_btn)
            assert False, "expected BrowserActionError"
        except StaleMarkError:
            assert False, "removed element must raise plain BrowserActionError, not StaleMarkError"
        except BrowserActionError:
            pass
        await s.close()

    asyncio.run(run())


# ── Decoupled confirm channel (ADR-0183 S1) ──────────────────────────────────

def test_decoupled_confirm_channel_resolves_pending_click(server):
    """A sensitive click parks a pending confirm; resolving it via the NEW
    manager-level ``resolve_oldest_pending`` (the chat-command path, no
    live-view browser tab / pending-id knowledge required) lets the action
    proceed — proving the second approval channel actually unblocks the tool
    driver, not just that the API exists."""
    async def run():
        from corvin_console.browser import BrowserSessionManager
        home = Path(tempfile.mkdtemp())
        mgr = BrowserSessionManager(home_resolver=lambda t: home / t,
                                    allowlist_resolver=lambda t: (None, None))
        sid = await mgr.create("_default", headless=True)
        s = mgr.session("_default", sid)
        obs = await s.navigate(server)
        buy = next(m.index for m in obs.marks if m.name.lower() == "buy now")

        async def approve_via_chat_channel():
            for _ in range(60):
                if mgr.pending("_default", sid):
                    return mgr.resolve_oldest_pending("_default", sid, True)
                await asyncio.sleep(0.05)
            return False

        click_task = asyncio.ensure_future(s.click(buy))
        assert await approve_via_chat_channel()
        await click_task   # must NOT raise — the chat-channel approval unblocked it
        await mgr.close("_default", sid)

    asyncio.run(run())


def test_decoupled_confirm_channel_fails_closed_when_nothing_pending():
    """No pending confirm for a known session → resolve_oldest_pending returns
    False (never guesses / auto-approves)."""
    async def run():
        from corvin_console.browser import BrowserSessionManager
        home = Path(tempfile.mkdtemp())
        mgr = BrowserSessionManager(home_resolver=lambda t: home / t,
                                    allowlist_resolver=lambda t: (None, None))
        sid = await mgr.create("_default", headless=True)
        assert mgr.resolve_oldest_pending("_default", sid, True) is False
        await mgr.close("_default", sid)

    asyncio.run(run())


def test_decoupled_confirm_channel_fails_closed_for_foreign_session():
    """An unknown/foreign session id raises KeyError (fail-closed) — the chat
    command handler turns this into a clear user-facing error, never a guess
    at which session/tenant was meant."""
    from corvin_console.browser import BrowserSessionManager
    mgr = BrowserSessionManager(home_resolver=lambda t: Path("/tmp"))
    with pytest.raises(KeyError):
        mgr.resolve_oldest_pending("_default", "does-not-exist", True)


def test_chat_browser_confirm_command_regex_and_wiring():
    """Grep-level + import-level proof the chat command wires cleanly:
    `/browser confirm <sid> yes|no` is recognized and dispatches to the new
    handler without touching the existing task-agent path."""
    from corvin_console.routes import chat as chat_routes
    m = chat_routes._BROWSER_CONFIRM_CMD_RE.match("confirm b12-3456 yes")
    assert m and m.group(1) == "b12-3456" and m.group(2).lower() == "yes"
    m2 = chat_routes._BROWSER_CONFIRM_CMD_RE.match("confirm b12-3456 no")
    assert m2 and m2.group(2).lower() == "no"
    # a normal free-text task must NOT be mistaken for the confirm sub-command
    assert chat_routes._BROWSER_CONFIRM_CMD_RE.match("book a flight to Berlin") is None
    assert callable(chat_routes._handle_browser_confirm_command)
