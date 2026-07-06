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

from . import compliance as _cmp
from .marks import (
    _COLLECT_JS, _FINGERPRINT_JS, _FORM_SENSITIVE_JS, _PAINT_JS, _UNPAINT_JS,
    MAX_MARKS, Mark, Observation,
)

logger = logging.getLogger("corvin.browser.session")

# Type aliases for the injected compliance hooks.
AuditFn = Callable[..., None]
VaultResolve = Callable[[str], Optional[str]]           # vault_key -> secret value
ConfirmFn = Callable[..., Awaitable[bool]]              # (action, host, role, name) -> approved?
OnAction = Callable[[dict], None]                        # live action-log sink
OnFrame = Callable[[bytes], None]                        # screencast JPEG sink


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

    # ── lifecycle ────────────────────────────────────────────────────────────
    async def _ensure_started(self) -> None:
        """Lazily launch Chromium on the first action — double-checked lock."""
        if self._pw is not None and self._context is not None:
            return
        async with self._start_lock:
            if self._pw is None or self._context is None:
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

    async def close(self) -> None:
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
                approved = await self._confirm(action="navigate", host=decision.host,
                                               role="navigation", name=url[:120])
                if not approved:
                    _cmp.audit_action(self._audit, tenant_id=self.tenant_id, session_id=self.session_id,
                                      action="navigate", host=decision.host, ok=False,
                                      extra={"reason": "user_declined_cross_host"})
                    self._emit("navigate", host=decision.host, ok=False, reason="cross-host declined")
                    raise BrowserActionError(f"cross-host navigation to {decision.host} declined")
        async with self._page_lock:
            page = self._require_page()
            self._last_marks = []       # stamps from the old page are gone
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
        page = self._require_page()
        data = await page.evaluate(_COLLECT_JS, MAX_MARKS)
        marks = [Mark(**m) for m in data.get("marks", [])]
        self._last_marks = marks
        obs = Observation(url=data.get("url", ""), title=data.get("title", ""), marks=marks)
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
        """
        page = self._require_page()
        el = await page.query_selector(f'[data-corvin-mark="{index}"]')
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
                el = await page.query_selector(f'[data-corvin-mark="{index}"]')
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
        # screencast keeps updating while the user decides.
        if _cmp.is_sensitive("click", role=role, name=name, url=url,
                             form_has_sensitive_field=form_sensitive):
            # Fail-CLOSED: a sensitive click with NO confirm broker wired is blocked,
            # never auto-approved.
            if self._confirm is None:
                _cmp.audit_action(self._audit, tenant_id=self.tenant_id, session_id=self.session_id,
                                  action="click", host=host, role=role, index=index, ok=False,
                                  extra={"reason": "no_confirm_broker"})
                self._emit("click", index=index, role=role, name=name, ok=False,
                           reason="no_confirm_broker")
                raise BrowserActionError(
                    f"sensitive click on '{name}' blocked: no confirmation channel")
            approved = await self._confirm(action="click", host=host, role=role, name=name)
            if not approved:
                _cmp.audit_action(self._audit, tenant_id=self.tenant_id, session_id=self.session_id,
                                  action="click", host=host, role=role, index=index, ok=False,
                                  extra={"reason": "user_declined_sensitive"})
                self._emit("click", index=index, role=role, name=name, ok=False,
                           reason="user_declined_sensitive")
                raise BrowserActionError(f"sensitive click on '{name}' declined by user")
        self._guard_active("click")   # re-check: user may have paused during confirm
        async with self._page_lock:
            el = await self._resolve(index)
            await el.click(timeout=self._nav_timeout)
            self._last_marks = []      # a click may have navigated — force re-observe
            # C1 egress guard: a click can navigate anywhere (e.g. an <a href> to an
            # off-allowlist host). Re-validate the LANDING host, fail-closed —
            # a denied destination is parked on about:blank and the click refused.
            page = self._require_page()
            fdec = _cmp.check_egress(page.url, allowlist=self._allowlist, forbidden=self._forbidden)
            if not fdec.allowed:
                try:
                    await page.goto("about:blank")
                except Exception:  # noqa: BLE001
                    pass
                _cmp.audit_action(self._audit, tenant_id=self.tenant_id, session_id=self.session_id,
                                  action="click", host=fdec.host, role=role, index=index, ok=False,
                                  extra={"reason": "nav_" + fdec.reason})
                self._emit("click", index=index, role=role, name=name, ok=False,
                           reason="navigation blocked")
                raise BrowserActionError(
                    f"click navigated to disallowed host {fdec.host}: {fdec.reason}")
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
            await page.go_back(wait_until="domcontentloaded")
            self._emit("back")
            return await self._observe_locked()

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
