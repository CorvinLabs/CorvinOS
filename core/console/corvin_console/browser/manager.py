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
        self._counter = 0

    def _key(self, tenant_id: str, sid: str) -> str:
        return f"{tenant_id}:{sid}"

    async def create(self, tenant_id: str, *, headless: bool = True) -> str:
        # Bound concurrent browsers per tenant → no Chromium/PID exhaustion (DoS).
        live_count = sum(1 for k in self._sessions if k.startswith(f"{tenant_id}:"))
        if live_count >= _MAX_SESSIONS_PER_TENANT:
            raise RuntimeError(
                f"browser session limit reached ({_MAX_SESSIONS_PER_TENANT}); close one first")

        self._counter += 1
        sid = f"b{self._counter}-{int(self._now())}"
        allowlist, forbidden = (None, None)
        if self._allowlist_resolver is not None:
            try:
                allowlist, forbidden = self._allowlist_resolver(tenant_id)
            except Exception:  # noqa: BLE001
                allowlist, forbidden = (None, None)

        live: _Live = _Live(session=None, created=self._now())  # type: ignore[arg-type]
        pid_seq = 0

        def _on_action(rec: dict) -> None:
            live.append({**rec, "ts": self._now()})

        async def _confirm(*, action: str, host: str, role: str, name: str) -> bool:
            nonlocal pid_seq
            pid_seq += 1
            pid = f"c{pid_seq}"      # monotonic per-session → never collides
            fut: asyncio.Future[bool] = asyncio.get_event_loop().create_future()
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
        try:
            await session.start()

            def _on_frame(png: bytes) -> None:
                live.frame = png
            await session.start_screencast(_on_frame)
        except Exception:
            # never orphan a launched Chromium if wiring after start() fails
            try:
                await session.close()
            except Exception:  # noqa: BLE001
                pass
            raise

        self._sessions[self._key(tenant_id, sid)] = live
        return sid

    def get(self, tenant_id: str, sid: str) -> _Live:
        live = self._sessions.get(self._key(tenant_id, sid))
        if live is None:
            raise KeyError(f"no browser session {sid} for tenant {tenant_id}")
        return live

    def session(self, tenant_id: str, sid: str) -> BrowserSession:
        return self.get(tenant_id, sid).session

    def resolve_confirm(self, tenant_id: str, sid: str, pid: str, approved: bool) -> bool:
        live = self.get(tenant_id, sid)
        p = live.pending.get(pid)
        if p is None or p.future.done():
            return False
        p.future.set_result(approved)
        live.append({"action": "confirm_resolved", "id": pid,
                     "approved": approved, "ts": self._now()})
        return True

    def set_paused(self, tenant_id: str, sid: str, paused: bool) -> None:
        self.get(tenant_id, sid).session.paused = paused

    def start_agent(self, tenant_id: str, sid: str, task: str, *,
                    max_steps: int = 12, auto_close: bool = False) -> bool:
        """Run a natural-language browser-agent loop in the background. Steps flow
        into the action log (live view); sensitive actions park confirms as usual.
        Returns False if an agent is already running for this session.

        auto_close=True closes the session when the agent finishes — used for
        chat-initiated (`/browser`) sessions so they can't leak / wedge the cap."""
        from .agent import BrowserAgent
        live = self.get(tenant_id, sid)
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

    def stop_agent(self, tenant_id: str, sid: str) -> None:
        live = self.get(tenant_id, sid)
        if live.agent_task is not None and not live.agent_task.done():
            live.agent_task.cancel()
            live.append({"action": "agent_stopped", "ts": self._now()})

    def actions(self, tenant_id: str, sid: str, since: int = 0) -> list[dict]:
        """Return actions with absolute sequence >= ``since``. ``since`` is a
        monotonic emission counter (NOT a buffer index), so this keeps delivering
        new events correctly after the 200-event deque rolls over.
        Pair with next_seq() for the cursor."""
        live = self.get(tenant_id, sid)
        items = list(live.actions)
        base = live.emitted - len(items)      # absolute seq of items[0]
        offset = max(0, since - base)
        return items[offset:]

    def next_seq(self, tenant_id: str, sid: str) -> int:
        return self.get(tenant_id, sid).emitted

    def frame(self, tenant_id: str, sid: str) -> bytes | None:
        return self.get(tenant_id, sid).frame

    def pending(self, tenant_id: str, sid: str) -> list[dict[str, Any]]:
        live = self.get(tenant_id, sid)
        return [{"id": p.id, "action": p.action, "host": p.host, "role": p.role,
                 "name": p.name} for p in live.pending.values()]

    async def close(self, tenant_id: str, sid: str) -> None:
        key = self._key(tenant_id, sid)
        live = self._sessions.pop(key, None)
        if live and live.agent_task is not None and not live.agent_task.done():
            live.agent_task.cancel()
        if live and live.session:
            await live.session.close()

    async def close_all(self) -> None:
        for live in list(self._sessions.values()):
            try:
                if live.session:
                    await live.session.close()
            except Exception:  # noqa: BLE001
                pass
        self._sessions.clear()
