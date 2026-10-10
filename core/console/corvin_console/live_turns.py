"""Live-turn hub: a running chat turn's events reach EVERY socket of its chat.

Why this exists: a web-chat turn used to stream only to the WebSocket that
started it. The turn outlives its socket (``routes/chat.py::_detach_turn``), but
a socket that connects while the turn runs — the same tab after a reload (the
``console_auto_reload`` redeploy reload, a manual F5), a reconnect after a
network blip, a second tab — received nothing until the turn ended, and the
end-of-turn history refresh was a length heuristic that could miss the answer
entirely. Measured 2026-10-09: a redeploy reloaded the operator's tab eight
seconds into a 95-second turn; the turn finished and was persisted, the tab
never showed it.

Contract:

* The turn's owner (``_run_turn``) calls :func:`begin`, :func:`publish` for
  every event it streams, and :func:`end` exactly once when the turn is over —
  regardless of whether its own socket is still alive.
* Every socket calls :func:`subscribe` when it connects. If a turn is live, the
  snapshot returned is ``[turn_attach, *buffered events]`` and must be sent
  BEFORE anything taken from the queue; both are produced under one lock, so no
  event is lost or duplicated between snapshot and queue.
* The owning socket is excluded from fan-out by its subscriber token — it
  already receives the events directly.
* Bounded: the replay buffer stops growing past :data:`MAX_BUFFER_BYTES` and a
  subscriber queue past :data:`MAX_QUEUE`; either sets ``truncated``. A view
  that attached to a turn (``turn_attach``) or fell behind always reconciles
  with the persisted record when the turn's ``done`` arrives (``attached:
  true``) — the replay is a live convenience, the turns file is the truth.

In-process by design: both console hosts (``corvin_console.standalone`` and
``corvin_gateway.app``) serve the chat WebSocket and run the turn in the same
process. Events are never written anywhere by this module — the turn's
persistence (``chat_runtime._append_turn``) and the session ledger are
unchanged.
"""
from __future__ import annotations

import asyncio
import itertools
import json
import secrets
import threading
import time
from dataclasses import dataclass, field
from typing import Any

#: Replay buffer cap per live turn. A turn's events are block-level (text
#: blocks, tool calls), so a normal turn is a few hundred KB at most; past the
#: cap the replay is truncated and the end-of-turn reconcile fills the gap.
MAX_BUFFER_BYTES = 4 * 1024 * 1024

#: Per-subscriber queue cap. A socket this far behind is not reading; it stops
#: receiving live events and reconciles at ``done``.
MAX_QUEUE = 4096

#: Event types that are delivery-only and never replayed.
_NOT_BUFFERED = frozenset({"pong", "ready", "done"})


@dataclass
class _Subscriber:
    token: str
    loop: asyncio.AbstractEventLoop
    queue: asyncio.Queue
    overflowed: bool = False


@dataclass
class _LiveTurn:
    turn_id: str
    owner_token: str
    started_at: float
    events: list[dict[str, Any]] = field(default_factory=list)
    size: int = 0
    truncated: bool = False
    cancel_requested: bool = False


_lock = threading.Lock()
_turns: dict[tuple[str, str], _LiveTurn] = {}
_subs: dict[tuple[str, str], list[_Subscriber]] = {}
_seq = itertools.count(1)


def new_token() -> str:
    """A subscriber token; one per WebSocket connection."""
    return f"ws-{next(_seq)}-{secrets.token_hex(4)}"


def _deliver(sub: _Subscriber, event: dict[str, Any]) -> None:
    """Put *event* on *sub*'s queue from any thread. Caller holds ``_lock``."""
    if sub.overflowed:
        return

    def _put() -> None:
        if sub.overflowed:
            return
        try:
            sub.queue.put_nowait(event)
        except asyncio.QueueFull:
            # Stop feeding a socket that is not reading; tell it once (the
            # sentinel bypasses the cap) so its view reconciles at done.
            # Make room for one sentinel (the event it displaces is covered
            # by the same reconcile) so a waiting get() is woken normally.
            sub.overflowed = True
            try:
                sub.queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
            sub.queue.put_nowait({"type": "turn_resync"})

    try:
        running = asyncio.get_running_loop()
    except RuntimeError:
        running = None
    if running is sub.loop:
        _put()
    else:
        try:
            sub.loop.call_soon_threadsafe(_put)
        except RuntimeError:  # loop closed — the socket is gone
            pass


def _attach_event(turn: _LiveTurn) -> dict[str, Any]:
    return {
        "type": "turn_attach",
        "turn_id": turn.turn_id,
        "started_at": turn.started_at,
        "truncated": turn.truncated,
    }


def subscribe(tenant_id: str, sid: str, token: str,
              ) -> tuple[list[dict[str, Any]], asyncio.Queue, Any]:
    """Register a socket for *sid*'s live turns.

    Returns ``(snapshot, queue, unsubscribe)``. ``snapshot`` is empty when no
    turn is live; otherwise ``[turn_attach, *buffered]`` — send it first, then
    drain ``queue``. ``unsubscribe`` is idempotent and MUST be called when the
    socket closes.
    """
    loop = asyncio.get_running_loop()
    q: asyncio.Queue = asyncio.Queue(maxsize=MAX_QUEUE)
    sub = _Subscriber(token=token, loop=loop, queue=q)
    key = (tenant_id, sid)
    with _lock:
        turn = _turns.get(key)
        snapshot: list[dict[str, Any]] = []
        if turn is not None and turn.owner_token != token:
            snapshot = [_attach_event(turn), *turn.events]
        _subs.setdefault(key, []).append(sub)

    def _unsubscribe() -> None:
        with _lock:
            subs = _subs.get(key)
            if not subs:
                return
            try:
                subs.remove(sub)
            except ValueError:
                return
            if not subs:
                _subs.pop(key, None)

    return snapshot, q, _unsubscribe


def begin(tenant_id: str, sid: str, owner_token: str) -> str:
    """Mark a turn live for *sid*; returns its turn id. Already-connected
    other sockets get ``turn_attach`` now."""
    turn = _LiveTurn(
        turn_id=f"t-{int(time.time() * 1000)}-{secrets.token_hex(3)}",
        owner_token=owner_token,
        started_at=time.time(),
    )
    key = (tenant_id, sid)
    with _lock:
        _turns[key] = turn
        attach = _attach_event(turn)
        for sub in list(_subs.get(key, ())):
            if sub.token != owner_token:
                _deliver(sub, attach)
    return turn.turn_id


def publish(tenant_id: str, sid: str, turn_id: str, event: dict[str, Any]) -> None:
    """Buffer *event* for late joiners and fan it out to every other socket.
    No-op when *turn_id* is not the live turn (a stale owner)."""
    etype = event.get("type")
    if etype == "done":
        return  # end() owns the terminal event
    key = (tenant_id, sid)
    with _lock:
        turn = _turns.get(key)
        if turn is None or turn.turn_id != turn_id:
            return
        if etype not in _NOT_BUFFERED and not turn.truncated:
            try:
                n = len(json.dumps(event, ensure_ascii=False, default=str))
            except (TypeError, ValueError):
                n = MAX_BUFFER_BYTES
            if turn.size + n > MAX_BUFFER_BYTES:
                turn.truncated = True
            else:
                turn.events.append(event)
                turn.size += n
        for sub in list(_subs.get(key, ())):
            if sub.token != turn.owner_token:
                _deliver(sub, event)


def end(tenant_id: str, sid: str, turn_id: str) -> bool:
    """The turn is over (finished, failed or cancelled). Every other socket
    gets ``done`` with ``attached: true`` so its view reconciles with the
    persisted record. Idempotent. Returns True when the turn was stopped by
    :func:`request_cancel` (an operator in another tab), so the owner can tell
    that apart from a chat deletion."""
    key = (tenant_id, sid)
    with _lock:
        turn = _turns.get(key)
        if turn is None or turn.turn_id != turn_id:
            return False
        _turns.pop(key, None)
        done = {"type": "done", "attached": True, "turn_id": turn_id}
        for sub in list(_subs.get(key, ())):
            if sub.token != turn.owner_token:
                _deliver(sub, done)
        return turn.cancel_requested


def live_turn_id(tenant_id: str, sid: str) -> str | None:
    with _lock:
        turn = _turns.get((tenant_id, sid))
        return turn.turn_id if turn else None


def request_cancel(tenant_id: str, sid: str) -> bool:
    """Record that an operator asked to stop *sid*'s live turn from a socket
    that does not own it. Returns False when no turn is live. The caller
    cancels the task (``chat_runtime.cancel_live_turn``)."""
    with _lock:
        turn = _turns.get((tenant_id, sid))
        if turn is None:
            return False
        turn.cancel_requested = True
        return True
