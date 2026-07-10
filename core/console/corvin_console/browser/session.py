"""BrowserSession — the agent-driven browser (ADR-0182 Pillar B).

An async Playwright-managed Chromium with a compliant action surface:
``navigate / observe / click / fill / fill_secret / read / scroll / back /
screenshot``. Every action routes through the compliance gates in
``compliance.py`` (egress allowlist, metadata-only audit, human-in-the-loop
confirmation for sensitive actions) and never lets a typed value reach the audit
trail or the model context.

Perception is Set-of-Marks (``marks.py``): each ``observe`` stamps interactive
elements with ``data-corvin-mark=<index>`` and returns the numbered list, so a
subsequent ``click(index)`` resolves back to the exact node without index drift.

The session is isolated: its own user-data dir + downloads dir under the tenant
browser home; nothing is shared with other sessions or the host profile.
"""
from __future__ import annotations

import asyncio
import base64
import contextlib
import logging
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional
from urllib.parse import urlparse

from . import compliance as _cmp
from .marks import (
    _ACTIVE_FORM_SENSITIVE_JS, _COLLECT_JS, _EXTRACT_FORMS_JS, _EXTRACT_TABLE_JS,
    _FINGERPRINT_JS, _FORM_SENSITIVE_JS, _PAINT_JS, _UNPAINT_JS,
    MAX_MARKS, Mark, Observation,
)

logger = logging.getLogger("corvin.browser.session")

# Type aliases for the injected compliance hooks.
AuditFn = Callable[..., None]
VaultResolve = Callable[[str], Optional[str]]           # vault_key -> secret value
ConfirmFn = Callable[..., Awaitable[bool]]              # (action, host, role, name) -> approved?
OnAction = Callable[[dict], None]                        # live action-log sink
OnFrame = Callable[[bytes], None]                        # screencast JPEG sink

# key() allowlist (ADR-0183 S2): only well-known, harmless navigation/editing
# keys may be pressed by name. Deliberately excludes modifier combinations
# (Ctrl/Alt/Meta/Shift+X) — those can trigger OS/browser-level shortcuts
# (devtools, paste-from-clipboard, "select all" on an unrelated field) that
# were never vetted for this action surface. Reject anything not on this list
# rather than passing an arbitrary string straight to Playwright's keyboard.
ALLOWED_KEYS = frozenset({
    "Enter", "Tab", "Escape", "Backspace", "Delete", "Space",
    "ArrowDown", "ArrowUp", "ArrowLeft", "ArrowRight",
    "Home", "End", "PageUp", "PageDown",
    "F1", "F2", "F3", "F4", "F5", "F6", "F7", "F8", "F9", "F10", "F11", "F12",
})

# Keys that can COMMIT the focused control — submit a form / activate a button —
# without any click() ever happening. A press of one of these must go through the
# SAME human-in-the-loop sensitivity gate + landing-egress recheck as a click,
# otherwise `fill(user); fill(pw); key("Enter")` would log in / pay / delete
# entirely un-confirmed (ADR-0183 S1 hardening — the "submit" sensitivity branch
# was previously dead code, reachable by nothing).
_COMMIT_KEYS = frozenset({"Enter", "Space"})

# Structured extraction bounds (ADR-0183 S2) — keep the model's context bounded
# regardless of how large the live page's table/forms are.
_MAX_EXTRACT_ROWS = 200

# Hard cap on concurrently-open tabs per session (review H3): a page that
# window.open()s in a loop must not spawn unbounded tabs + 30s guard tasks.
_MAX_TABS = 12


class BrowserActionError(RuntimeError):
    """Raised when an action cannot be completed (bad index, blocked, timeout)."""


class StaleMarkError(BrowserActionError):
    """Raised when the live element at ``[index]`` no longer matches the
    ``Mark`` captured at the last ``observe()`` (ADR-0183 S1 stale-mark
    self-healing) — an in-place SPA re-render changed the element under the
    index between observe() and the act. Distinguishable from the plain
    "mark not found" case (element removed entirely) so a caller (e.g. the
    agent loop) can specifically prompt a re-observe instead of retrying
    blindly or surfacing a generic error."""


class BrowserSession:
    def __init__(
        self,
        session_id: str,
        tenant_id: str,
        *,
        home: Path,
        allowlist: list[str] | None = None,
        forbidden: list[str] | None = None,
        audit_fn: AuditFn | None = None,
        vault_resolve: VaultResolve | None = None,
        confirm_fn: ConfirmFn | None = None,
        on_action: OnAction | None = None,
        headless: bool = True,
        nav_timeout_ms: int = 30_000,
    ) -> None:
        self.session_id = session_id
        self.tenant_id = tenant_id
        self._home = home
        self._allowlist = allowlist
        self._forbidden = forbidden
        self._audit = audit_fn
        self._vault = vault_resolve
        self._confirm = confirm_fn
        self._on_action = on_action
        self._headless = headless
        self._nav_timeout = nav_timeout_ms

        self._pw = None
        self._browser = None
        self._context = None
        self._page = None
        self._last_marks: list[Mark] = []
        # ADR-0183 S2 iframe traversal: which Frame (or Page, for the main
        # document) a given global mark index was collected from, so
        # ``_resolve()`` queries the CORRECT frame instead of always the
        # top-level page. Absent entries default to the current page (fully
        # backward compatible with pre-S2 single-frame pages).
        self._mark_frame: dict[int, Any] = {}
        self._screencast_task: asyncio.Task | None = None
        self.paused = False          # take-over: agent actions are refused while paused
        # Playwright Page is NOT safe for concurrent operations. This lock
        # serializes every page-touching call (actions AND the screencast poll)
        # so a screenshot can never interleave with a click/navigate. Public
        # methods acquire it; internal helpers (``*_locked``) assume it is held to
        # avoid re-entrant deadlock (navigate → observe).
        self._page_lock = asyncio.Lock()
        # Lazy-start: Chromium is not launched at construction time — only when the
        # first action (typically navigate) is called.  _pending_on_frame is set by
        # the manager and consumed by _ensure_started() to wire the screencast after
        # the browser is up.
        self._start_lock = asyncio.Lock()
        self._pending_on_frame: "OnFrame | None" = None
        # Terminal-state flag (concurrency review H1): close() sets it FIRST so a
        # concurrent in-flight action cannot re-launch Chromium after teardown
        # (close() nulls _pw/_context, which _ensure_started would otherwise read
        # as "never started" and relaunch → a leaked, unreachable zombie browser).
        self._closed = False
        # Fire-and-forget new-tab egress guards (review H3): the event loop keeps
        # only a WEAK ref to a bare ensure_future task, so it can be GC'd mid-wait
        # and silently skip the egress check (fail-closed → fail-open). Keep a hard
        # ref here; cancel them all on close().
        self._guard_tasks: set[asyncio.Task] = set()

    # ── lifecycle ────────────────────────────────────────────────────────────
    async def _ensure_started(self) -> None:
        """Lazily launch Chromium on the first action — double-checked lock."""
        if self._closed:
            raise BrowserActionError("session closed")
        # Fast path requires a live PAGE too (review M4): a persistent context can
        # briefly exist with _pw/_context set but _page still None during start(),
        # and the invariant "fast path ⇒ fully usable" must hold.
        if self._pw is not None and self._context is not None and self._page is not None:
            return
        async with self._start_lock:
            if self._closed:
                raise BrowserActionError("session closed")
            if self._pw is None or self._context is None or self._page is None:
                await self.start()
                if self._pending_on_frame is not None:
                    await self.start_screencast(self._pending_on_frame)
                    self._pending_on_frame = None

    async def start(self) -> None:
        import os
        from playwright.async_api import async_playwright
        user_data = self._home / "sessions" / self.session_id
        user_data.mkdir(parents=True, exist_ok=True)
        self._user_data = user_data
        self._pw = await async_playwright().start()
        # Renderer sandbox stays ON — this browser loads untrusted third-party
        # pages, so a renderer exploit must NOT reach the host. Only disable it
        # when the deploy environment genuinely can't sandbox (e.g. unprivileged
        # container as root) via an explicit opt-in, and log the downgrade.
        args = ["--disable-dev-shm-usage"]
        if os.environ.get("CORVIN_BROWSER_NO_SANDBOX") == "1":
            args.append("--no-sandbox")
            logger.warning("browser: renderer sandbox DISABLED (CORVIN_BROWSER_NO_SANDBOX=1)")
        try:
            self._context = await self._pw.chromium.launch_persistent_context(
                user_data_dir=str(user_data),
                headless=self._headless,
                accept_downloads=False,     # downloads gated separately (L10) — off by default
                args=args,
                viewport={"width": 1280, "height": 800},
            )
        except Exception:
            # A failed launch must not leak the Playwright driver subprocess nor
            # leave _pw truthy — that would make _ensure_started() think the
            # session is already up on the next call, permanently wedging it.
            with contextlib.suppress(Exception):
                await self._pw.stop()
            self._pw = None
            raise
        self._page = self._context.pages[0] if self._context.pages else await self._context.new_page()
        self._page.set_default_timeout(self._nav_timeout)
        # Multi-tab awareness (ADR-0183 S2): a target="_blank" click or a
        # window.open() creates a brand-new Page OUTSIDE the normal
        # navigate()/click() control flow — without this hook it could reach
        # an off-allowlist host without ever going through check_egress().
        # Wired once, at the context level, so it covers every tab for the
        # life of the session (registered after context.pages[0] already
        # exists, which is fine: that first tab is only ever driven through
        # our own navigate(), which already egress-checks explicitly).
        self._context.on("page", self._on_new_page)
        # Network-layer egress enforcement (review HIGH-1). check_egress() gates
        # top-level NAVIGATION, but a loaded page can still fetch()/XHR/beacon/
        # <img>/WebSocket to ANY host — the real indirect-prompt-injection
        # exfil vector — with nothing stopping it. Route EVERY request through
        # the same policy and abort a disallowed one. This also blocks a
        # subresource fetch to a cloud-metadata endpoint even when no allowlist
        # is configured (check_egress blocks metadata unconditionally).
        await self._context.route("**/*", self._route_egress)

    async def _route_egress(self, route) -> None:
        """Per-request egress gate. In-page pseudo-schemes (data:/blob:/about:)
        make no network egress and are always allowed; everything else must pass
        check_egress or is aborted. Fail-closed: any handler error aborts the
        single request rather than letting it through."""
        try:
            url = route.request.url
            scheme = (urlparse(url).scheme or "").lower()
            if scheme in ("data", "blob", "about", "javascript", "filesystem"):
                await route.continue_()
                return
            if _cmp.check_egress(url, allowlist=self._allowlist,
                                 forbidden=self._forbidden).allowed:
                await route.continue_()
            else:
                await route.abort()
        except Exception:  # noqa: BLE001 — fail-closed on this one request
            with contextlib.suppress(Exception):
                await route.abort()

    def _on_new_page(self, new_page) -> None:
        """Sync 'page' event callback (Playwright fires this synchronously) —
        just schedules the actual async egress check/audit. Keep a HARD ref to
        the task (review H3) so the loop's weak-ref bookkeeping can't GC it
        mid-wait and silently skip the guard."""
        task = asyncio.ensure_future(self._guard_new_page(new_page))
        self._guard_tasks.add(task)
        task.add_done_callback(self._guard_tasks.discard)

    async def _guard_new_page(self, new_page) -> None:
        """Fail-closed egress gate for a newly-opened tab/popup.

        Best-effort: waits for the tab's first load so ``new_page.url``
        reflects its real destination (covers the common target="_blank" /
        window.open(url) case); a script that opens a blank tab and navigates
        it later via a timer is a known limitation of this one-shot check —
        the same fail-closed re-check that ``navigate()``/``click()`` already
        do for the PRIMARY tab does not yet run repeatedly on secondary tabs.
        Any error here (including the egress check itself) closes the tab
        rather than leaving an unchecked page open.
        """
        host = ""
        try:
            # Tab-flood cap (review H3): a page that window.open()s in a loop must
            # not spawn unbounded tabs+guards. Over the cap, close the newcomer.
            if self._context is not None and len(self._context.pages) > _MAX_TABS:
                await self._safe_close_tab(new_page)
                self._emit("new_tab", host="", ok=False, reason="tab_limit")
                return
            with contextlib.suppress(Exception):
                await new_page.wait_for_load_state("load", timeout=self._nav_timeout)
            new_page.set_default_timeout(self._nav_timeout)
            url = new_page.url
            decision = _cmp.check_egress(url, allowlist=self._allowlist, forbidden=self._forbidden)
            host = decision.host
            if not decision.allowed and url not in ("about:blank", ""):
                await self._safe_close_tab(new_page)
                _cmp.audit_action(self._audit, tenant_id=self.tenant_id, session_id=self.session_id,
                                  action="new_tab", host=host, ok=False,
                                  extra={"reason": decision.reason})
                self._emit("new_tab", host=host, ok=False, reason=decision.reason)
                return
            _cmp.audit_action(self._audit, tenant_id=self.tenant_id, session_id=self.session_id,
                              action="new_tab", host=host, ok=True)
            self._emit("new_tab", host=host, ok=True)
        except Exception:  # noqa: BLE001 — a hook failure must never crash the session;
            # fail-closed: an unexpected error means we could NOT confirm the new
            # tab is safe, so close it rather than leave it open unchecked.
            await self._safe_close_tab(new_page)
            logger.debug("new-tab egress guard failed for %s", host, exc_info=True)

    async def _safe_close_tab(self, page) -> None:
        """Close a popup/secondary tab under the page lock (review M2: the guard
        must not race an in-flight locked action on the same Page). NEVER closes
        the tab that is currently the active ``self._page`` — switch_tab() may
        have promoted this popup to primary while we were waiting for its load;
        closing it would leave every later action raising a raw 'Target closed'.
        """
        if page is self._page:
            return
        async with self._page_lock:
            if page is self._page:      # re-check under the lock
                return
            with contextlib.suppress(Exception):
                await page.close()

    async def close(self) -> None:
        """Idempotent, self-serializing teardown (review H1/H3/H4/M3).

        Sets ``_closed`` FIRST so any queued ``_ensure_started`` bails instead of
        relaunching Chromium onto a popped session (the resurrection-zombie bug),
        then serializes with ``_start_lock`` so a close racing an in-flight
        ``start()`` cannot stop the driver mid-launch. Safe to call twice (the
        chat auto_close finally + an explicit REST close can race): the second
        call sees ``_pw is None`` and no-ops rather than double-stopping the
        Playwright driver."""
        already = self._closed
        self._closed = True
        # Cancel the fire-and-forget new-tab guards up front (review H3).
        for t in list(self._guard_tasks):
            t.cancel()
        self._guard_tasks.clear()
        async with self._start_lock:
            if already and self._pw is None:
                return                       # a concurrent close() already tore down
            if self._screencast_task:
                self._screencast_task.cancel()
                try:
                    await self._screencast_task     # drain the in-flight frame cleanly
                except (asyncio.CancelledError, Exception):  # noqa: BLE001
                    pass
                self._screencast_task = None
            try:
                if self._context:
                    await self._context.close()
            finally:
                if self._pw:
                    await self._pw.stop()
                self._pw = self._browser = self._context = self._page = None
                # L3: wipe the persistent profile (cookies/localStorage/auth) — the
                # ephemeral managed session must not leave credentials on disk.
                try:
                    import shutil
                    if getattr(self, "_user_data", None) and self._user_data.exists():
                        shutil.rmtree(self._user_data, ignore_errors=True)
                except Exception:  # noqa: BLE001
                    pass

    def _require_page(self):
        if self._page is None:
            raise BrowserActionError("session not started")
        return self._page

    def _emit(self, action: str, **kw: Any) -> None:
        rec = {"action": action, "session": self.session_id, **kw}
        if self._on_action is not None:
            try:
                self._on_action(rec)
            except Exception:  # noqa: BLE001
                pass

    def _guard_active(self, action: str) -> None:
        if self.paused:
            raise BrowserActionError(
                f"blocked: session is paused / under user take-over ({action})")

    # ── shared sensitivity + egress gates (used by every commit-capable action) ─
    async def _confirm_sensitive_or_raise(
        self, action: str, *, host: str, role: str = "", name: str = "",
        url: str = "", form_sensitive: bool = False, index: int | None = None,
    ) -> None:
        """Human-in-the-loop gate shared by click / key / select_option / drag.

        MUST be called with the page lock NOT held — the confirm can block for up
        to the broker timeout and we want the live screencast to keep updating
        while the user decides. Fail-closed: a sensitive action with no confirm
        broker wired is blocked (never auto-approved); a declined one raises."""
        if not _cmp.is_sensitive(action, role=role, name=name, url=url,
                                 form_has_sensitive_field=form_sensitive):
            return
        if self._confirm is None:
            _cmp.audit_action(self._audit, tenant_id=self.tenant_id, session_id=self.session_id,
                              action=action, host=host, role=role, index=index, ok=False,
                              extra={"reason": "no_confirm_broker"})
            self._emit(action, index=index, role=role, name=name, ok=False,
                       reason="no_confirm_broker")
            raise BrowserActionError(
                f"sensitive {action} on '{name}' blocked: no confirmation channel")
        approved = await self._confirm(action=action, host=host, role=role, name=name)
        if not approved:
            _cmp.audit_action(self._audit, tenant_id=self.tenant_id, session_id=self.session_id,
                              action=action, host=host, role=role, index=index, ok=False,
                              extra={"reason": "user_declined_sensitive"})
            self._emit(action, index=index, role=role, name=name, ok=False,
                       reason="user_declined_sensitive")
            raise BrowserActionError(f"sensitive {action} on '{name}' declined by user")

    async def _recheck_landing_egress_locked(
        self, action: str, *, role: str = "", index: int | None = None,
    ) -> None:
        """Re-validate the CURRENT landing host after an act that may have
        navigated (a click's <a href>, a key('Enter') form submit, a
        <select onchange=location=…>, a drag-to-confirm). Assumes ``_page_lock``
        is held. Fail-closed: a denied destination is parked on about:blank and
        the action refused, exactly like navigate()'s redirect guard."""
        page = self._require_page()
        fdec = _cmp.check_egress(page.url, allowlist=self._allowlist, forbidden=self._forbidden)
        if not fdec.allowed:
            try:
                await page.goto("about:blank")
            except Exception:  # noqa: BLE001
                pass
            _cmp.audit_action(self._audit, tenant_id=self.tenant_id, session_id=self.session_id,
                              action=action, host=fdec.host, role=role, index=index, ok=False,
                              extra={"reason": "nav_" + fdec.reason})
            self._emit(action, index=index, role=role, ok=False, reason="navigation blocked")
            raise BrowserActionError(
                f"{action} navigated to disallowed host {fdec.host}: {fdec.reason}")

    async def _act_and_settle(self, page, do: Callable[[], Awaitable[Any]]) -> None:
        """Run the page-mutating coroutine ``do`` and, when an egress policy is
        configured, wait (bounded) for any navigation it triggers to SETTLE
        before returning — so a subsequent ``_recheck_landing_egress_locked``
        sees the real destination, not a stale pre-navigation url.

        Needed because ``page.keyboard.press`` / ``select_option`` / a drag do
        NOT auto-wait for a navigation the way ``ElementHandle.click`` does — a
        form submitted via Enter starts navigating asynchronously, so an
        immediate ``page.url`` read still shows the old host and the egress
        recheck would be a no-op (fail-OPEN). When no allowlist/forbidden is set
        the recheck is a no-op anyway, so we skip the wait entirely to keep the
        common no-policy path fast. Assumes ``_page_lock`` is held."""
        if self._allowlist is None and not self._forbidden:
            await do()
            return
        from playwright.async_api import Error as PWError, TimeoutError as PWTimeout
        try:
            async with page.expect_navigation(timeout=3000):
                await do()
        except PWTimeout:
            pass   # the action did not navigate — that is fine, recheck the current url
        except PWError:
            # The navigation was aborted at the network layer — the per-request
            # egress route (HIGH-1) refuses an off-allowlist / metadata document
            # request, surfacing as net::ERR_FAILED/ERR_ABORTED here. That IS the
            # block working: the page stays on the current (allowed) host, so the
            # landing recheck below is a no-op. Swallow it rather than leaking a
            # raw Playwright error out of a commit action.
            pass

    # ── actions ──────────────────────────────────────────────────────────────
    async def navigate(self, url: str, *, confirm_cross_host: bool = False) -> Observation:
        self._guard_active("navigate")
        await self._ensure_started()
        decision = _cmp.check_egress(url, allowlist=self._allowlist, forbidden=self._forbidden)
        if not decision.allowed:
            _cmp.audit_action(self._audit, tenant_id=self.tenant_id, session_id=self.session_id,
                              action="navigate", host=decision.host, ok=False,
                              extra={"reason": decision.reason})
            self._emit("navigate", host=decision.host, ok=False, reason=decision.reason)
            raise BrowserActionError(f"egress denied for {decision.host}: {decision.reason}")
        # Injection defense for the AUTONOMOUS agent: when no egress allowlist is
        # configured, a cross-host navigation (the classic indirect-prompt-
        # injection → beacon vector) requires human confirmation. Manual operator
        # navigation (confirm_cross_host=False) is never gated.
        if confirm_cross_host and self._allowlist is None and self._confirm is not None:
            cur = _cmp._host(self._require_page().url)
            # A falsy `cur` (fresh session on about:blank, no host yet) must NOT
            # skip the confirm — that would let the agent's very FIRST hop of a
            # session go unconfirmed regardless of destination.
            if decision.host and (not cur or decision.host != cur):
                # SECURITY: pass the HOST only, never the full URL — the confirm
                # `name` is written verbatim into the live action-log ring buffer
                # and the pending() payload, and a full URL can carry a
                # ?token=/reset secret. The audit trail is already host-only
                # (below); the live view must not leak more than the audit trail.
                approved = await self._confirm(action="navigate", host=decision.host,
                                               role="navigation", name=decision.host)
                if not approved:
                    _cmp.audit_action(self._audit, tenant_id=self.tenant_id, session_id=self.session_id,
                                      action="navigate", host=decision.host, ok=False,
                                      extra={"reason": "user_declined_cross_host"})
                    self._emit("navigate", host=decision.host, ok=False, reason="cross-host declined")
                    raise BrowserActionError(f"cross-host navigation to {decision.host} declined")
        async with self._page_lock:
            page = self._require_page()
            self._last_marks = []       # stamps from the old page are gone
            self._mark_frame = {}       # old frames are gone/detached too
            await page.goto(url, wait_until="domcontentloaded")
            # Redirect guard: the server may 3xx to another host. Re-check the
            # FINAL landing url against the same policy; a denied redirect is
            # parked on about:blank and refused (fail-closed).
            final = page.url
            fdec = _cmp.check_egress(final, allowlist=self._allowlist, forbidden=self._forbidden)
            if not fdec.allowed:
                try:
                    await page.goto("about:blank")
                except Exception:  # noqa: BLE001
                    pass
                _cmp.audit_action(self._audit, tenant_id=self.tenant_id, session_id=self.session_id,
                                  action="navigate", host=fdec.host, ok=False,
                                  extra={"reason": "redirect_" + fdec.reason})
                self._emit("navigate", host=fdec.host, ok=False, reason="redirect blocked")
                raise BrowserActionError(f"egress denied after redirect to {fdec.host}: {fdec.reason}")
            _cmp.audit_action(self._audit, tenant_id=self.tenant_id, session_id=self.session_id,
                              action="navigate", host=fdec.host, ok=True)
            # L1: action log carries HOST only, never the full URL (which could
            # hold ?token=/reset links). The full url stays local to the browser.
            self._emit("navigate", host=fdec.host, ok=True)
            return await self._observe_locked()

    async def observe(self) -> Observation:
        self._guard_active("observe")
        await self._ensure_started()
        async with self._page_lock:
            return await self._observe_locked()

    async def _observe_locked(self) -> Observation:
        """Collect Set-of-Marks from the main document AND every same-page
        iframe (ADR-0183 S2) — same-origin or cross-origin: Playwright's
        ``Frame.evaluate`` reaches iframe content regardless of origin, which
        is exactly what makes a payment widget (Stripe/PayPal) visible to the
        agent instead of an invisible black box. All frames share ONE global,
        MAX_MARKS-bounded index space; ``self._mark_frame`` remembers which
        frame produced which index so ``_resolve()`` can query the right one.
        Backward compatible: a page with no iframes collects exactly as
        before (single frame, offset 0).
        """
        page = self._require_page()
        main = page.main_frame
        try:
            frames = list(page.frames)
        except Exception:  # noqa: BLE001
            frames = [main]
        ordered = [main] + [f for f in frames if f is not main]

        marks: list[Mark] = []
        mark_frame: dict[int, Any] = {}
        url = page.url
        title = ""
        for frame in ordered:
            remaining = MAX_MARKS - len(marks)
            if remaining <= 0:
                break
            try:
                data = await frame.evaluate(_COLLECT_JS, {"maxMarks": remaining, "offset": len(marks)})
            except Exception:  # noqa: BLE001 — detached/navigating/restricted frame: skip it
                continue
            if frame is main:
                url = data.get("url", url)
                title = data.get("title", "")
            for m in data.get("marks", []):
                mark = Mark(**m)
                marks.append(mark)
                mark_frame[mark.index] = frame

        self._last_marks = marks
        self._mark_frame = mark_frame
        obs = Observation(url=url, title=title, marks=marks)
        host = _cmp._host(obs.url)
        _cmp.audit_action(self._audit, tenant_id=self.tenant_id, session_id=self.session_id,
                          action="observe", host=host, ok=True, extra={"count": len(marks)})
        self._emit("observe", host=host, count=len(marks))    # host only, not full url
        return obs

    async def _resolve(self, index: int, *, verify_fresh: bool = True):
        """Resolve mark ``index`` to a live element handle.

        ADR-0183 S1 stale-mark self-healing: before handing the element back
        to an actor (click/fill/fill_secret/read), re-derive its accessible-name
        fingerprint (same priority order as ``accName()`` in marks.py, never
        ``el.value``) and compare it against the ``Mark.name`` captured at the
        last ``observe()``. A mismatch means the page re-rendered in place
        since the last observe (the index now points at a DIFFERENT logical
        control) — raise ``StaleMarkError`` instead of silently acting on a
        possibly-wrong element. The check only fires when BOTH names are
        non-empty (an empty name carries no signal either way).

        ADR-0183 S2 iframe traversal: resolves against the FRAME that
        actually produced this index (``self._mark_frame``), not always the
        top-level page — a Playwright ``Frame`` exposes the same
        ``query_selector``/``evaluate`` surface as ``Page``, so this stays a
        drop-in. Indices with no recorded frame (pages with no iframes,
        pre-S2 behavior) fall back to the current page.
        """
        page = self._require_page()
        frame = self._mark_frame.get(index, page)
        el = await frame.query_selector(f'[data-corvin-mark="{index}"]')
        if el is None:
            raise BrowserActionError(
                f"mark [{index}] not found — the page changed; call observe() again")
        if verify_fresh:
            mark = self._mark(index)
            if mark is not None and mark.name:
                try:
                    live_name = await el.evaluate(_FINGERPRINT_JS)
                except Exception:  # noqa: BLE001 — resolution hiccup, not proof of staleness
                    live_name = None
                if isinstance(live_name, str) and live_name.strip() and live_name.strip() != mark.name:
                    raise StaleMarkError(
                        f"stale mark [{index}]: page changed since last observe() — "
                        f"call observe() again")
        return el

    async def _form_sensitive_hint(self, index: int) -> bool:
        """Best-effort: does the <form> enclosing mark ``index`` contain a
        password or card-number field? (Sensitivity model v2, ADR-0183 S1.)

        Never raises — any resolution/eval failure defaults to False. This is
        only a RECALL-raising hint for ``is_sensitive()``; it never replaces
        the fail-closed backstop in ``_resolve()`` (missing/stale mark) that
        runs on the actual action right before it executes.
        """
        try:
            async with self._page_lock:
                page = self._require_page()
                frame = self._mark_frame.get(index, page)
                el = await frame.query_selector(f'[data-corvin-mark="{index}"]')
                if el is None:
                    return False
                return bool(await el.evaluate(_FORM_SENSITIVE_JS))
        except Exception:  # noqa: BLE001
            return False

    def _mark(self, index: int) -> Mark | None:
        for m in self._last_marks:
            if m.index == index:
                return m
        return None

    async def click(self, index: int) -> None:
        self._guard_active("click")
        await self._ensure_started()
        mark = self._mark(index)
        role = mark.role if mark else ""
        name = mark.name if mark else ""
        url = self._require_page().url
        host = _cmp._host(url)
        # Sensitivity model v2 (ADR-0183 S1): URL-path + form-context signals,
        # additive to the v1 name-keyword match. Best-effort — a resolution
        # failure here defaults form_has_sensitive_field=False rather than
        # raising; the later _resolve() staleness/missing-mark check remains
        # the fail-closed backstop for the actual click.
        form_sensitive = await self._form_sensitive_hint(index)
        # Human-in-the-loop confirmation happens OUTSIDE the page lock so the live
        # screencast keeps updating while the user decides. Fail-closed inside the
        # shared helper (no broker → blocked; declined → raised).
        await self._confirm_sensitive_or_raise(
            "click", host=host, role=role, name=name, url=url,
            form_sensitive=form_sensitive, index=index)
        self._guard_active("click")   # re-check: user may have paused during confirm
        async with self._page_lock:
            el = await self._resolve(index)
            # TOCTOU re-check (review M1): the sensitivity decision above was made
            # on pre-lock state, and there are await boundaries (up to the full
            # confirm timeout) before we actually click. If the page swapped the
            # form under this element (benign "Continue" → payment form) or
            # pushState'd onto a /checkout path in that window, re-evaluate on the
            # LIVE element/url; a benign→sensitive transition that we never
            # confirmed must refuse, not click through unconfirmed.
            try:
                live_url = self._require_page().url
                live_form_sensitive = bool(await el.evaluate(_FORM_SENSITIVE_JS))
            except Exception:  # noqa: BLE001
                live_url, live_form_sensitive = url, form_sensitive
            if (_cmp.is_sensitive("click", role=role, name=name, url=live_url,
                                  form_has_sensitive_field=live_form_sensitive)
                    and not _cmp.is_sensitive("click", role=role, name=name, url=url,
                                              form_has_sensitive_field=form_sensitive)):
                raise StaleMarkError(
                    f"click target [{index}] became sensitive since the confirm "
                    f"decision — call observe() again and retry")
            await el.click(timeout=self._nav_timeout)
            self._last_marks = []      # a click may have navigated — force re-observe
            self._mark_frame = {}      # old frames (if any) are gone/detached too
            # C1 egress guard: a click can navigate anywhere (e.g. an <a href> to an
            # off-allowlist host). Re-validate the LANDING host, fail-closed.
            await self._recheck_landing_egress_locked("click", role=role, index=index)
        _cmp.audit_action(self._audit, tenant_id=self.tenant_id, session_id=self.session_id,
                          action="click", host=host, role=role, index=index, ok=True)
        self._emit("click", index=index, role=role, name=name, ok=True)

    async def fill(self, index: int, text: str) -> None:
        """Type a value into a field. The value is NEVER audited or logged."""
        self._guard_active("fill")
        await self._ensure_started()
        mark = self._mark(index)
        role = mark.role if mark else ""
        # Sensitivity model v2 (ADR-0183 S1): fill itself stays never-auto-
        # sensitive (typing is reversible — is_sensitive() short-circuits for
        # action="fill" regardless of these signals; see compliance.py), but
        # the form-context hint is still computed and recorded as metadata so
        # a fill into a password/card-number-bearing form is visible in the
        # audit trail even though the confirm gate only fires on the eventual
        # submit/click that commits it.
        form_sensitive = await self._form_sensitive_hint(index)
        async with self._page_lock:
            host = _cmp._host(self._require_page().url)
            el = await self._resolve(index)
            await el.fill(text)
        _cmp.audit_action(self._audit, tenant_id=self.tenant_id, session_id=self.session_id,
                          action="fill", host=host, role=role, index=index, ok=True,
                          extra={"chars": len(text),          # length only, never the value
                                 "form_sensitive_context": form_sensitive})
        self._emit("fill", index=index, role=role, ok=True, chars=len(text))

    async def fill_secret(self, index: int, vault_key: str) -> None:
        """Type a secret resolved from the vault. The value never enters the model
        context, the action log, or the audit trail — only the vault key name."""
        self._guard_active("fill_secret")
        await self._ensure_started()
        if self._vault is None:
            raise BrowserActionError("no vault resolver configured")
        value = self._vault(vault_key)
        if not value:
            raise BrowserActionError(f"vault key '{vault_key}' not found")
        async with self._page_lock:
            host = _cmp._host(self._require_page().url)
            el = await self._resolve(index)
            await el.fill(value)
        del value      # drop the secret from this frame promptly
        _cmp.audit_action(self._audit, tenant_id=self.tenant_id, session_id=self.session_id,
                          action="fill_secret", host=host, index=index, ok=True,
                          extra={"vault_key": vault_key})   # key name only, never the value
        self._emit("fill_secret", index=index, ok=True, vault_key=vault_key)

    async def read(self, index: int | None = None, *, max_chars: int = 4000) -> str:
        self._guard_active("read")
        await self._ensure_started()
        async with self._page_lock:
            page = self._require_page()
            host = _cmp._host(page.url)
            if index is not None:
                el = await self._resolve(index)
                txt = (await el.inner_text()) or ""
            else:
                txt = await page.evaluate(
                    "() => document.body ? (document.body.innerText || '') : ''")
        n = min(len(txt), max_chars)
        _cmp.audit_action(self._audit, tenant_id=self.tenant_id, session_id=self.session_id,
                          action="read", host=host, index=index, ok=True, extra={"chars": n})
        self._emit("read", index=index, chars=n)
        return txt[:max_chars]

    async def scroll(self, direction: str = "down") -> None:
        self._guard_active("scroll")
        await self._ensure_started()
        dy ={"down": 600, "up": -600, "top": -100000, "bottom": 100000}.get(direction, 600)
        async with self._page_lock:
            await self._require_page().evaluate("(dy) => window.scrollBy(0, dy)", dy)
        self._emit("scroll", direction=direction)

    async def back(self) -> Observation:
        self._guard_active("back")
        await self._ensure_started()
        async with self._page_lock:
            page = self._require_page()
            self._last_marks = []
            self._mark_frame = {}
            await page.go_back(wait_until="domcontentloaded")
            self._emit("back")
            return await self._observe_locked()

    # ── ADR-0183 S2: expanded action surface ────────────────────────────────
    async def hover(self, index: int) -> None:
        """Hover the element at ``index`` (e.g. to reveal a hover-only menu)
        without clicking it. Goes through the same stale-mark ``_resolve()``
        check as every other action."""
        self._guard_active("hover")
        await self._ensure_started()
        mark = self._mark(index)
        role = mark.role if mark else ""
        async with self._page_lock:
            host = _cmp._host(self._require_page().url)
            el = await self._resolve(index)
            await el.hover(timeout=self._nav_timeout)
        _cmp.audit_action(self._audit, tenant_id=self.tenant_id, session_id=self.session_id,
                          action="hover", host=host, role=role, index=index, ok=True)
        self._emit("hover", index=index, role=role, ok=True)

    async def key(self, key: str) -> None:
        """Press a single named key (Enter/Tab/Escape/Arrow*/…) on the page.

        SECURITY: ``key`` must be on ``ALLOWED_KEYS`` — an arbitrary string
        (or a modifier combo like "Control+A") is rejected rather than passed
        straight to Playwright's keyboard, since some combos trigger browser/
        OS-level behavior (devtools, paste, select-all) never vetted for this
        surface. Fail-closed: an unknown key raises, nothing is pressed.
        """
        self._guard_active("key")
        await self._ensure_started()
        if key not in ALLOWED_KEYS:
            raise BrowserActionError(
                f"key '{key}' is not in the allowed key set ({sorted(ALLOWED_KEYS)})")
        url = self._require_page().url
        host = _cmp._host(url)
        # A commit key (Enter/Space) can submit the focused form without any
        # click — gate it through the SAME sensitivity confirm as a click, using
        # the current page path + whether the FOCUSED element's form carries a
        # password/card field. Closes the "Enter bypasses the sensitivity gate"
        # hole (the previously-dead is_sensitive("submit", …) branch).
        if key in _COMMIT_KEYS:
            active_sensitive = False
            try:
                async with self._page_lock:
                    active_sensitive = bool(
                        await self._require_page().evaluate(_ACTIVE_FORM_SENSITIVE_JS))
            except Exception:  # noqa: BLE001 — best-effort hint, never blocks on eval error
                active_sensitive = False
            await self._confirm_sensitive_or_raise(
                "submit", host=host, name=key, url=url, form_sensitive=active_sensitive)
            self._guard_active("key")   # re-check: user may have paused during confirm
        async with self._page_lock:
            page = self._require_page()
            # A committed form submit can navigate anywhere — wait for the nav to
            # settle, then re-validate the landing host fail-closed (same as click).
            if key in _COMMIT_KEYS:
                self._last_marks = []
                self._mark_frame = {}
                await self._act_and_settle(page, lambda: page.keyboard.press(key))
                await self._recheck_landing_egress_locked("key")
            else:
                await page.keyboard.press(key)
        # The key NAME itself ("Enter") is not sensitive content — it is
        # metadata about the action, not typed text — so it is safe to audit,
        # unlike a fill() value.
        _cmp.audit_action(self._audit, tenant_id=self.tenant_id, session_id=self.session_id,
                          action="key", host=host, ok=True, extra={"key": key})
        self._emit("key", key=key, ok=True)

    async def select_option(self, index: int, value: str) -> None:
        """Choose an option (by its ``value`` attribute) in the <select> at
        ``index``. Like ``fill()``, the chosen value is never audited/logged
        — only its length — since a selected option can itself carry
        sensitive context (e.g. a country/insurance-plan choice)."""
        self._guard_active("select_option")
        await self._ensure_started()
        mark = self._mark(index)
        role = mark.role if mark else ""
        url = self._require_page().url
        host = _cmp._host(url)
        # A <select onchange="location=…"> commits + navigates like a click —
        # gate it through the same sensitivity confirm (url path + enclosing-form
        # context) and re-check the landing host afterwards.
        form_sensitive = await self._form_sensitive_hint(index)
        await self._confirm_sensitive_or_raise(
            "click", host=host, role=role, name=mark.name if mark else "", url=url,
            form_sensitive=form_sensitive, index=index)
        self._guard_active("select_option")
        async with self._page_lock:
            page = self._require_page()
            el = await self._resolve(index)
            self._last_marks = []
            self._mark_frame = {}
            await self._act_and_settle(page, lambda: el.select_option(value=value))
            await self._recheck_landing_egress_locked("select_option", role=role, index=index)
        _cmp.audit_action(self._audit, tenant_id=self.tenant_id, session_id=self.session_id,
                          action="select_option", host=host, role=role, index=index, ok=True,
                          extra={"chars": len(value)})   # length only, never the value
        self._emit("select_option", index=index, role=role, ok=True)

    async def upload_file(self, index: int, filename: str) -> None:
        """Attach a file to the file-input at ``index``.

        SECURITY: ``filename`` is NOT an arbitrary host path. Accepting one
        would let an untrusted page/agent read arbitrary files off the
        operator's disk (path traversal / LFI) via a file-input's
        ``set_input_files``. Instead, a file may only be attached if it
        ALREADY exists under this session's dedicated uploads directory —
        ``<tenant browser home>/sessions/<session_id>/uploads/`` — created
        lazily on first use. Any ``..`` path component or an absolute path is
        rejected outright; the final resolved path is then re-verified to
        still be inside the uploads dir before Playwright ever touches it
        (fail-closed against normalization/symlink tricks). An operator (or a
        prior, explicitly-approved step) must place the file there first —
        this method never fetches or writes file content itself.
        """
        self._guard_active("upload_file")
        await self._ensure_started()
        uploads_dir = self._home / "sessions" / self.session_id / "uploads"
        uploads_dir.mkdir(parents=True, exist_ok=True)
        raw = (filename or "").strip()
        if not raw or Path(raw).is_absolute() or ".." in Path(raw).parts:
            raise BrowserActionError(f"invalid upload filename: {filename!r}")
        uploads_resolved = uploads_dir.resolve()
        candidate = (uploads_dir / raw).resolve()
        if candidate != uploads_resolved and uploads_resolved not in candidate.parents:
            raise BrowserActionError(f"upload path escapes the session uploads dir: {filename!r}")
        if not candidate.is_file():
            raise BrowserActionError(
                f"upload file not found: {filename!r} (place it under {uploads_dir})")
        mark = self._mark(index)
        role = mark.role if mark else ""
        async with self._page_lock:
            host = _cmp._host(self._require_page().url)
            el = await self._resolve(index)
            await el.set_input_files(str(candidate))
        _cmp.audit_action(self._audit, tenant_id=self.tenant_id, session_id=self.session_id,
                          action="upload_file", host=host, role=role, index=index, ok=True,
                          extra={"filename": raw})   # filename only — never file content
        self._emit("upload_file", index=index, role=role, ok=True, filename=raw)

    async def drag(self, from_index: int, to_index: int) -> None:
        """Drag the element at ``from_index`` onto the element at
        ``to_index`` via a manual hover + mouse down/move/up sequence (more
        reliable than selector-string ``page.drag_and_drop`` for elements
        resolved through Set-of-Marks / possibly inside an iframe — an
        ElementHandle's ``bounding_box()`` is always reported relative to the
        main frame's viewport, so ``page.mouse`` coordinates work regardless
        of which frame either endpoint lives in). Both endpoints go through
        the normal stale-mark ``_resolve()`` check first.
        """
        self._guard_active("drag")
        await self._ensure_started()
        async with self._page_lock:
            page = self._require_page()
            host = _cmp._host(page.url)
            src = await self._resolve(from_index)
            dst = await self._resolve(to_index)
            src_box = await src.bounding_box()
            dst_box = await dst.bounding_box()
            if src_box is None or dst_box is None:
                raise BrowserActionError(
                    f"drag: source [{from_index}] or target [{to_index}] has no bounding box "
                    "(not visible)")
            sx = src_box["x"] + src_box["width"] / 2
            sy = src_box["y"] + src_box["height"] / 2
            tx = dst_box["x"] + dst_box["width"] / 2
            ty = dst_box["y"] + dst_box["height"] / 2
            async def _do_drag():
                await page.mouse.move(sx, sy)
                await page.mouse.down()
                await page.mouse.move(tx, ty, steps=10)
                await page.mouse.up()
            # A drag-to-confirm / slide-to-pay control can navigate — settle any
            # nav, then re-validate the landing host fail-closed (like click/key).
            self._last_marks = []
            self._mark_frame = {}
            await self._act_and_settle(page, _do_drag)
            await self._recheck_landing_egress_locked("drag")
        _cmp.audit_action(self._audit, tenant_id=self.tenant_id, session_id=self.session_id,
                          action="drag", host=host, ok=True,
                          extra={"from_index": from_index, "to_index": to_index})
        self._emit("drag", from_index=from_index, to_index=to_index, ok=True)

    # ── multi-tab awareness (ADR-0183 S2) ───────────────────────────────────
    async def tabs(self) -> list[dict[str, Any]]:
        """List every open tab/page in this session's browser context —
        including ones opened by a target="_blank" click or window.open()
        that the agent has not yet switched to."""
        self._guard_active("tabs")
        await self._ensure_started()
        async with self._page_lock:
            pages = list(self._context.pages) if self._context else []
            out = []
            for i, pg in enumerate(pages):
                try:
                    title = await pg.title()
                except Exception:  # noqa: BLE001
                    title = ""
                out.append({"index": i, "url": pg.url, "title": title})
        _cmp.audit_action(self._audit, tenant_id=self.tenant_id, session_id=self.session_id,
                          action="tabs", ok=True, extra={"count": len(out)})
        self._emit("tabs", count=len(out))
        return out

    async def switch_tab(self, index: int) -> Observation:
        """Make tab ``index`` (as reported by ``tabs()``) the active page for
        all subsequent actions, and return a fresh Set-of-Marks observation
        of it. The context-level egress guard (wired once in ``start()``,
        see ``_guard_new_page``) already covers every tab for the life of the
        session, so switching does not need to re-wire anything per-page —
        it only needs to make sure the newly-active page has the same
        default timeout as the rest of the session.
        """
        self._guard_active("switch_tab")
        await self._ensure_started()
        async with self._page_lock:
            pages = list(self._context.pages) if self._context else []
            if index < 0 or index >= len(pages):
                raise BrowserActionError(f"no tab at index {index}")
            self._page = pages[index]
            self._page.set_default_timeout(self._nav_timeout)
            self._last_marks = []
            self._mark_frame = {}
            obs = await self._observe_locked()
        host = _cmp._host(obs.url)
        _cmp.audit_action(self._audit, tenant_id=self.tenant_id, session_id=self.session_id,
                          action="switch_tab", host=host, ok=True, extra={"tab_index": index})
        self._emit("switch_tab", tab_index=index, host=host, ok=True)
        return obs

    # ── structured extraction (ADR-0183 S2) ─────────────────────────────────
    async def extract_table(self, index: int) -> dict[str, Any]:
        """Parse the element at ``index`` — a <table>, or a container that
        wraps/represents one (role="table"/"grid") — into
        ``{"headers": [...], "rows": [[...], ...]}``. Bounded at
        ``_MAX_EXTRACT_ROWS`` rows so a huge table can't blow the model's
        context. Goes through the normal stale-mark ``_resolve()`` first."""
        self._guard_active("extract_table")
        await self._ensure_started()
        async with self._page_lock:
            host = _cmp._host(self._require_page().url)
            el = await self._resolve(index)
            data = await el.evaluate(_EXTRACT_TABLE_JS, _MAX_EXTRACT_ROWS)
        headers = data.get("headers", []) if isinstance(data, dict) else []
        rows = data.get("rows", []) if isinstance(data, dict) else []
        _cmp.audit_action(self._audit, tenant_id=self.tenant_id, session_id=self.session_id,
                          action="extract_table", host=host, index=index, ok=True,
                          extra={"count": len(rows)})
        self._emit("extract_table", index=index, count=len(rows), ok=True)
        return {"headers": headers, "rows": rows}

    async def extract_form_schema(self) -> list[dict[str, Any]]:
        """Describe every <form> on the CURRENT top-level document — action,
        method, and one entry per field (name/type/required/label). NEVER
        includes a field's current value (only its static label/attributes),
        so an in-progress password/PII entry can never leak through this
        path. Scoped to the top-level document only (does not descend into
        iframes — use ``extract_table`` or a per-frame ``observe()`` for
        iframe-embedded forms)."""
        self._guard_active("extract_form_schema")
        await self._ensure_started()
        async with self._page_lock:
            page = self._require_page()
            host = _cmp._host(page.url)
            forms = await page.evaluate(_EXTRACT_FORMS_JS)
        forms = forms if isinstance(forms, list) else []
        _cmp.audit_action(self._audit, tenant_id=self.tenant_id, session_id=self.session_id,
                          action="extract_form_schema", host=host, ok=True,
                          extra={"count": len(forms)})
        self._emit("extract_form_schema", count=len(forms), ok=True)
        return forms

    async def screenshot(self, *, marks: bool = True) -> bytes:
        await self._ensure_started()
        async with self._page_lock:
            return await self._screenshot_locked(marks=marks)

    async def _screenshot_locked(self, *, marks: bool = True) -> bytes:
        page = self._require_page()
        if marks and self._last_marks:
            try:
                await page.evaluate(_PAINT_JS)
            except Exception:  # noqa: BLE001
                pass
        png = await page.screenshot(type="jpeg", quality=60, full_page=False)
        if marks and self._last_marks:
            try:
                await page.evaluate(_UNPAINT_JS)
            except Exception:  # noqa: BLE001
                pass
        return png

    def screenshot_data_url(self, png: bytes) -> str:
        return "data:image/jpeg;base64," + base64.b64encode(png).decode("ascii")

    # ── live view (screencast) ────────────────────────────────────────────────
    async def start_screencast(self, on_frame: OnFrame, *, fps: float = 1.5) -> None:
        """Poll screenshots at ``fps`` and push JPEG frames to ``on_frame``.
        Simple + cross-page (survives navigations). Cancelled on close()."""
        interval = 1.0 / max(0.5, fps)

        async def _loop() -> None:
            while True:
                try:
                    png = await self.screenshot(marks=True)
                    on_frame(png)
                except asyncio.CancelledError:
                    raise
                except Exception:  # noqa: BLE001 — a transient nav shouldn't kill the cast
                    pass
                await asyncio.sleep(interval)

        if self._screencast_task:
            self._screencast_task.cancel()
        self._screencast_task = asyncio.ensure_future(_loop())
