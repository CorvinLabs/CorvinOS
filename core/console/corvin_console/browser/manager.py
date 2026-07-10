"""BrowserSessionManager — per-tenant registry of live browser sessions plus the
plumbing the console live-view and the MCP tool surface share (ADR-0182 M3/M4).

Responsibilities:
  * create / look up / close ``BrowserSession`` instances, keyed by (tenant, sid);
  * hold a bounded action-log ring buffer per session (for the live panel);
  * hold the latest screencast frame per session (for the live <img> stream);
  * broker human-in-the-loop confirmations: a sensitive action parks a pending
    request that the console resolves via approve/decline;
  * resolve the compliance hooks (egress allowlist from tenant config, vault
    resolver, audit sink) once, in one place.

Everything is in-process and best-effort; a console restart drops live sessions
(the browsers are child processes and are cleaned up on close()).
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import secrets
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from .session import BrowserSession

logger = logging.getLogger("corvin.browser.manager")

_ACTION_LOG_CAP = 200
_CONFIRM_TIMEOUT_S = 120.0
_MAX_SESSIONS_PER_TENANT = 8       # bound concurrent browsers → no resource exhaustion


@dataclass
class _Pending:
    id: str
    action: str
    host: str
    role: str
    name: str
    created: float
    future: "asyncio.Future[bool]"


@dataclass
class _Live:
    session: BrowserSession
    owner_fingerprint: str = ""   # sid_fingerprint of the creating console session
    actions: deque = field(default_factory=lambda: deque(maxlen=_ACTION_LOG_CAP))
    frame: bytes | None = None
    pending: dict[str, _Pending] = field(default_factory=dict)
    created: float = field(default_factory=lambda: 0.0)
    emitted: int = 0          # monotonic total events ever appended (survives deque rollover)
    agent_task: "asyncio.Task | None" = None

    def append(self, rec: dict) -> None:
        self.emitted += 1
        self.actions.append(rec)


class BrowserSessionManager:
    def __init__(
        self,
        *,
        home_resolver,                 # (tenant_id) -> Path (tenant browser home)
        audit_fn=None,
        vault_resolver=None,           # (tenant_id, key) -> Optional[str]
        allowlist_resolver=None,       # (tenant_id) -> (allowlist, forbidden)
        now=None,                      # injectable clock (Date.now-free for tests)
    ) -> None:
        self._home_resolver = home_resolver
        self._audit_fn = audit_fn
        self._vault_resolver = vault_resolver
        self._allowlist_resolver = allowlist_resolver
        self._now = now or time.time
        self._sessions: dict[str, _Live] = {}

    def _key(self, tenant_id: str, sid: str) -> str:
        return f"{tenant_id}:{sid}"

    async def create(self, tenant_id: str, *, headless: bool = True,
                     owner_fingerprint: str = "") -> str:
        # Bound concurrent browsers per tenant → no Chromium/PID exhaustion (DoS).
        live_count = sum(1 for k in self._sessions if k.startswith(f"{tenant_id}:"))
        if live_count >= _MAX_SESSIONS_PER_TENANT:
            raise RuntimeError(
                f"browser session limit reached ({_MAX_SESSIONS_PER_TENANT}); close one first")

        # Cryptographically random SID — not guessable from a counter/timestamp,
        # which would let one tenant user enumerate and hijack another's session.
        sid = secrets.token_urlsafe(16)
        allowlist, forbidden = (None, None)
        if self._allowlist_resolver is not None:
            try:
                allowlist, forbidden = self._allowlist_resolver(tenant_id)
            except Exception as e:  # noqa: BLE001
                # Fail-closed: a broken/misconfigured L35 policy source must block
                # session creation, never silently fall back to "no policy" (which
                # would mean unrestricted egress).
                raise RuntimeError(f"browser egress policy unavailable: {e}") from e

        live: _Live = _Live(session=None, owner_fingerprint=owner_fingerprint,  # type: ignore[arg-type]
                            created=self._now())
        pid_seq = 0

        def _on_action(rec: dict) -> None:
            live.append({**rec, "ts": self._now()})

        async def _confirm(*, action: str, host: str, role: str, name: str) -> bool:
            nonlocal pid_seq
            pid_seq += 1
            pid = f"c{pid_seq}"      # monotonic per-session → never collides
            # get_running_loop() (review L1): we are inside a coroutine, so the
            # running loop is the correct — and non-deprecated — future factory.
            fut: asyncio.Future[bool] = asyncio.get_running_loop().create_future()
            live.pending[pid] = _Pending(pid, action, host, role, name, self._now(), fut)
            live.append({"action": "confirm_request", "id": pid, "host": host,
                         "role": role, "name": name, "ts": self._now()})
            try:
                return await asyncio.wait_for(fut, timeout=_CONFIRM_TIMEOUT_S)
            except asyncio.TimeoutError:
                return False        # no answer in time → fail-closed decline
            finally:
                live.pending.pop(pid, None)

        vault = None
        if self._vault_resolver is not None:
            vault = lambda key: self._vault_resolver(tenant_id, key)  # noqa: E731

        session = BrowserSession(
            sid, tenant_id,
            home=self._home_resolver(tenant_id),
            allowlist=allowlist, forbidden=forbidden,
            audit_fn=self._audit_fn, vault_resolve=vault,
            confirm_fn=_confirm, on_action=_on_action, headless=headless,
        )
        live.session = session
        # Lazy start: register the screencast callback but do NOT launch Chromium yet.
        # Chromium is launched on the first action (navigate/observe/click/…) via
        # BrowserSession._ensure_started(), which also wires _pending_on_frame at
        # that point.  This prevents a blank about:blank window from opening whenever
        # a session object is created before any task is given.
        def _on_frame(png: bytes) -> None:
            live.frame = png
        session._pending_on_frame = _on_frame

        self._sessions[self._key(tenant_id, sid)] = live
        return sid

    def get(self, tenant_id: str, sid: str, *, owner_fingerprint: str = "") -> _Live:
        live = self._sessions.get(self._key(tenant_id, sid))
        if live is None:
            raise KeyError(f"no browser session {sid} for tenant {tenant_id}")
        # Verify ownership when a fingerprint is provided — prevents one console
        # user from reading or controlling another user's browser session even
        # within the same tenant. Raises the SAME KeyError as "not found" so a
        # caller can't distinguish "wrong owner" from "doesn't exist" (no oracle).
        if owner_fingerprint and live.owner_fingerprint and live.owner_fingerprint != owner_fingerprint:
            raise KeyError(f"no browser session {sid} for tenant {tenant_id}")
        return live

    def session(self, tenant_id: str, sid: str, *, owner_fingerprint: str = "") -> BrowserSession:
        return self.get(tenant_id, sid, owner_fingerprint=owner_fingerprint).session

    def resolve_confirm(self, tenant_id: str, sid: str, pid: str, approved: bool, *,
                        owner_fingerprint: str = "") -> bool:
        live = self.get(tenant_id, sid, owner_fingerprint=owner_fingerprint)
        p = live.pending.get(pid)
        if p is None or p.future.done():
            return False
        p.future.set_result(approved)
        live.append({"action": "confirm_resolved", "id": pid,
                     "approved": approved, "ts": self._now()})
        return True

    def resolve_oldest_pending(self, tenant_id: str, sid: str, approved: bool) -> bool:
        """Resolve the OLDEST pending human-in-the-loop confirm for (tenant_id,
        sid) — the manager-level entry point for the decoupled confirm channel
        (ADR-0183 S1). The live-view browser tab resolves a specific pending id
        via ``resolve_confirm``; a second approver watching the main console
        chat (a different tab, not sharing that tab's tenant CSRF session)
        does not know the pending id, only the session id it is watching, so
        it resolves "whichever confirm is oldest" instead.

        This is ADDITIVE to (not a replacement for) the live-view confirm path
        — both read/write the same ``live.pending`` dict, so whichever
        approver acts first wins; the other's later call on the same pending
        id is a no-op (``resolve_confirm`` returns False once ``future.done()``).

        Fail-closed: ``self.get()`` raises ``KeyError`` for an unknown/foreign
        session id (never guesses which tenant it belongs to); returns False
        (not an error) when the session is known but has no pending confirm —
        callers should surface both cases as a clear, distinct error rather
        than silently approving/declining nothing.
        """
        live = self.get(tenant_id, sid)
        if not live.pending:
            return False
        oldest_pid = next(iter(live.pending))   # dict preserves insertion order
        return self.resolve_confirm(tenant_id, sid, oldest_pid, approved)

    def set_paused(self, tenant_id: str, sid: str, paused: bool, *,
                   owner_fingerprint: str = "") -> None:
        self.get(tenant_id, sid, owner_fingerprint=owner_fingerprint).session.paused = paused

    def start_agent(self, tenant_id: str, sid: str, task: str, *,
                    max_steps: int = 12, auto_close: bool = False,
                    owner_fingerprint: str = "") -> bool:
        """Run a natural-language browser-agent loop in the background. Steps flow
        into the action log (live view); sensitive actions park confirms as usual.
        Returns False if an agent is already running for this session.

        auto_close=True closes the session when the agent finishes — used for
        chat-initiated (`/browser`) sessions so they can't leak / wedge the cap."""
        from .agent import BrowserAgent
        live = self.get(tenant_id, sid, owner_fingerprint=owner_fingerprint)
        if live.agent_task is not None and not live.agent_task.done():
            return False

        agent = BrowserAgent(live.session, max_steps=max_steps,
                             on_step=lambda rec: live.append({**rec, "ts": self._now()}))

        async def _run() -> None:
            try:
                result = await agent.run(task)
                live.append({"action": "agent_finished", **result, "ts": self._now()})
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                live.append({"action": "agent_error", "error": str(e), "ts": self._now()})
            finally:
                if auto_close:
                    # Inline close — do NOT call self.close() here: it would cancel
                    # THIS still-finishing agent task. Just drop the session + shut
                    # the browser down.
                    self._sessions.pop(self._key(tenant_id, sid), None)
                    with contextlib.suppress(Exception):
                        await live.session.close()

        live.agent_task = asyncio.ensure_future(_run())
        return True

    def agent_running(self, tenant_id: str, sid: str) -> bool:
        live = self.get(tenant_id, sid)
        return live.agent_task is not None and not live.agent_task.done()

    def stop_agent(self, tenant_id: str, sid: str, *, owner_fingerprint: str = "") -> None:
        live = self.get(tenant_id, sid, owner_fingerprint=owner_fingerprint)
        if live.agent_task is not None and not live.agent_task.done():
            live.agent_task.cancel()
            live.append({"action": "agent_stopped", "ts": self._now()})

    def actions(self, tenant_id: str, sid: str, since: int = 0, *,
               owner_fingerprint: str = "") -> list[dict]:
        """Return actions with absolute sequence >= ``since``. ``since`` is a
        monotonic emission counter (NOT a buffer index), so this keeps delivering
        new events correctly after the 200-event deque rolls over.
        Pair with next_seq() for the cursor."""
        live = self.get(tenant_id, sid, owner_fingerprint=owner_fingerprint)
        items = list(live.actions)
        base = live.emitted - len(items)      # absolute seq of items[0]
        offset = max(0, since - base)
        return items[offset:]

    def next_seq(self, tenant_id: str, sid: str, *, owner_fingerprint: str = "") -> int:
        return self.get(tenant_id, sid, owner_fingerprint=owner_fingerprint).emitted

    def frame(self, tenant_id: str, sid: str, *, owner_fingerprint: str = "") -> bytes | None:
        return self.get(tenant_id, sid, owner_fingerprint=owner_fingerprint).frame

    def pending(self, tenant_id: str, sid: str, *, owner_fingerprint: str = "") -> list[dict[str, Any]]:
        live = self.get(tenant_id, sid, owner_fingerprint=owner_fingerprint)
        return [{"id": p.id, "action": p.action, "host": p.host, "role": p.role,
                 "name": p.name} for p in live.pending.values()]

    @staticmethod
    def _drain_pending(live: _Live) -> None:
        """Fail-closed-resolve every parked human-in-the-loop confirm (review M5).
        A REST-path click can park a confirm future with a 120s timeout; once the
        session is being torn down, nobody can ever resolve it via the (popped)
        _Live, so the click coroutine would hang for the full timeout. Resolve
        them False (declined) so those coroutines unwind immediately."""
        for p in list(live.pending.values()):
            if not p.future.done():
                p.future.set_result(False)
        live.pending.clear()

    @staticmethod
    async def _cancel_agent(live: _Live) -> None:
        """Cancel a running agent task and AWAIT it (review H4/L5): its `finally`
        (auto_close) may itself close the session and append to the _Live, so we
        let it fully unwind before we close — session.close() is idempotent, so
        the double close is a safe no-op rather than a driver-corrupting race."""
        if live.agent_task is not None and not live.agent_task.done():
            live.agent_task.cancel()
            with contextlib.suppress(Exception):
                await live.agent_task

    async def close(self, tenant_id: str, sid: str, *, owner_fingerprint: str = "") -> None:
        # Verify ownership BEFORE popping — a foreign caller must not be able to
        # tear down (or even detect the existence of) another user's session.
        self.get(tenant_id, sid, owner_fingerprint=owner_fingerprint)
        key = self._key(tenant_id, sid)
        live = self._sessions.pop(key, None)
        if live:
            self._drain_pending(live)
            await self._cancel_agent(live)
            if live.session:
                await live.session.close()

    async def close_all(self) -> None:
        for live in list(self._sessions.values()):
            try:
                self._drain_pending(live)
                await self._cancel_agent(live)   # review H2: was never cancelling agents
                if live.session:
                    await live.session.close()
            except Exception:  # noqa: BLE001
                pass
        self._sessions.clear()
