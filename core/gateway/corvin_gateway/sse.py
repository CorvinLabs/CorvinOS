"""Per-run event buffer + SSE generator for live run streaming.

ADR-0007 Phase 2.5.

The dispatcher consumes engine events in a worker thread to build the
final-text / usage payload. Phase 2.5 adds a parallel tap: each event
is appended to a per-run in-memory buffer that the SSE endpoint
subscribes to. HTTP clients see normalised events as they happen
(text_delta, tool_call, turn_completed, error) plus a final
``run.<status>`` event when the dispatcher reaches a terminal state.

What this module does NOT do
----------------------------

* It does not persist events to disk. A gateway-process restart
  loses any active stream; the operator polls
  ``GET /v1/tenants/{tid}/runs/{run_id}`` (Phase 2.2) for the
  terminal state instead. Phase 7 may add durable buffers when
  rate-limiting + multi-process workers land.
* It does not impose a TTL on buffers. Long-lived Gateway processes
  accumulate per-run buffers indefinitely; the operator restarts
  the process or Phase 7 wires in eviction. For Phase 2's traffic
  envelope this is acceptable — every buffer is a small list of
  dicts.
* It does not multiplex across runs in a single SSE stream. One
  SSE connection follows exactly one run. A future
  ``GET /v1/tenants/{tid}/events`` aggregate stream is a Phase 7+
  decision.

Threading model
---------------

The dispatcher's ``_spawn_collect`` runs in a worker thread (via
``asyncio.to_thread``). Engine events arrive there; the worker
calls :meth:`RunEventBuffer.append` which forwards to subscriber
queues via ``loop.call_soon_threadsafe`` — the documented
thread-safe way to schedule work on an asyncio loop from outside.
The SSE endpoint subscribes from inside the loop and consumes the
queue with native asyncio.
"""
from __future__ import annotations

import asyncio
import threading
import time
from typing import Any, AsyncIterator


# ── Per-run buffer ───────────────────────────────────────────────────


class RunEventBuffer:
    """Thread-safe pub/sub for one run's stream.

    Producers (the dispatcher worker thread) call :meth:`append`
    for each engine event and :meth:`close` when the run reaches a
    terminal state. The close payload is a single ``run.<status>``
    event that gets delivered to every subscriber before the stream
    ends.

    Consumers (the SSE endpoint) call :meth:`subscribe` to get an
    async iterator. The iterator yields the full history first
    (so a late subscriber sees everything), then live events, and
    finally the terminal event before completing.
    """

    def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop
        self._lock = threading.Lock()
        self._events: list[dict[str, Any]] = []
        self._terminal_event: dict[str, Any] | None = None
        self._closed = False
        self._closed_at: float | None = None
        self._subscribers: list[asyncio.Queue] = []

    @property
    def closed_at(self) -> float | None:
        """Epoch when :meth:`close` last fired, or None while in-flight."""
        return self._closed_at

    # ---- Producer side ----------------------------------------------

    def append(self, event: dict[str, Any]) -> None:
        """Append a stream event. Thread-safe."""
        with self._lock:
            if self._closed:
                # Race window — engine emitted an event after the
                # dispatcher already closed the buffer (rare; the
                # current dispatcher only closes after the iterator
                # is fully drained). Drop the event silently.
                return
            self._events.append(event)
            subs = list(self._subscribers)
        for q in subs:
            self._schedule_put(q, event)

    def close(self, terminal_event: dict[str, Any] | None = None) -> None:
        """Close the buffer; final ``terminal_event`` is delivered
        to every active subscriber before the stream ends."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._closed_at = time.time()
            self._terminal_event = terminal_event
            subs = list(self._subscribers)
            self._subscribers.clear()
        for q in subs:
            if terminal_event is not None:
                self._schedule_put(q, terminal_event)
            self._schedule_put(q, None)  # sentinel

    # ---- Consumer side ----------------------------------------------

    async def subscribe(self) -> AsyncIterator[dict[str, Any]]:
        """Async iterator yielding events. Replays history, then
        tap-streams new events, then yields the terminal event
        before completing."""
        # Snapshot history under the lock so a late append can't
        # double-deliver into the queue + history replay.
        with self._lock:
            history = list(self._events)
            terminal = self._terminal_event
            closed = self._closed
            q: asyncio.Queue | None = None
            if not closed:
                q = asyncio.Queue()
                self._subscribers.append(q)

        for event in history:
            yield event

        if closed:
            if terminal is not None:
                yield terminal
            return

        assert q is not None
        try:
            while True:
                event = await q.get()
                if event is None:
                    return
                yield event
        finally:
            # Late unsubscribe; close() may have already cleared subs.
            with self._lock:
                if q in self._subscribers:
                    self._subscribers.remove(q)

    # ---- Internals --------------------------------------------------

    def _schedule_put(self, q: asyncio.Queue, event: dict[str, Any] | None) -> None:
        """Forward a put_nowait to the loop's thread. Safe to call
        from any thread."""
        try:
            self._loop.call_soon_threadsafe(q.put_nowait, event)
        except RuntimeError:
            # Loop is closed — most likely the process is shutting
            # down. The subscriber's async iterator will exit when
            # it next yields control; nothing more we can do here.
            pass


# ── Registry (per Gateway process) ───────────────────────────────────


class EventBufferRegistry:
    """Keyed by ``(tenant_id, run_id)`` → :class:`RunEventBuffer`.

    Lives on the :class:`RunDispatcher`. One instance per Gateway
    process; one buffer per run.

    Phase 7 follow-up: ``sweep_expired(now=None, ttl_s=...)``
    evicts buffers that closed more than ``ttl_s`` ago — the
    long-running-gateway memory growth the Phase 2.5 narrative
    flagged as acceptable for the Phase-2 envelope. The dispatcher
    schedules this periodically once the operator opts in via
    ``CORVIN_SSE_BUFFER_TTL_S``.
    """

    def __init__(self) -> None:
        self._buffers: dict[tuple[str, str], RunEventBuffer] = {}
        self._lock = threading.Lock()

    def get_or_create(
        self,
        tenant_id: str,
        run_id: str,
        loop: asyncio.AbstractEventLoop,
    ) -> RunEventBuffer:
        key = (tenant_id, run_id)
        with self._lock:
            buf = self._buffers.get(key)
            if buf is None:
                buf = RunEventBuffer(loop)
                self._buffers[key] = buf
            return buf

    def get(self, tenant_id: str, run_id: str) -> RunEventBuffer | None:
        return self._buffers.get((tenant_id, run_id))

    def evict(self, tenant_id: str, run_id: str) -> None:
        """Remove the buffer. Production callers normally use
        :meth:`sweep_expired` instead, which only evicts buffers
        that already closed (so late-subscriber replay stays
        intact for in-flight runs)."""
        with self._lock:
            self._buffers.pop((tenant_id, run_id), None)

    def sweep_expired(
        self,
        *,
        now: float | None = None,
        ttl_s: float = 1800.0,
    ) -> int:
        """Drop closed buffers whose terminal event landed more
        than ``ttl_s`` seconds ago. Returns the number evicted.

        Open (not-yet-closed) buffers are NEVER evicted — that
        would orphan an in-flight SSE stream. Operators tune the
        TTL via ``CORVIN_SSE_BUFFER_TTL_S``; the default 30
        minutes keeps a generous replay window for clients that
        reconnect after a hiccup.
        """
        cutoff = (now if now is not None else time.time()) - ttl_s
        dropped = 0
        with self._lock:
            for key in list(self._buffers.keys()):
                buf = self._buffers[key]
                if buf.closed_at is None:
                    continue  # in-flight; keep
                if buf.closed_at < cutoff:
                    del self._buffers[key]
                    dropped += 1
        return dropped

    def __len__(self) -> int:
        return len(self._buffers)


# ── Helpers ──────────────────────────────────────────────────────────


def stream_event_to_dict(event: Any) -> dict[str, Any]:
    """Project an ``agents.StreamEvent`` to a JSON-safe dict.

    The dispatcher worker calls this before passing to
    :meth:`RunEventBuffer.append` — the buffer never holds
    references to engine dataclasses, only plain dicts.
    """
    return {
        "type":   getattr(event, "type", ""),
        "text":   getattr(event, "text", "") or "",
        "usage":  getattr(event, "usage", None) or {},
        "error":  getattr(event, "error", None),
    }


def format_sse_frame(event: dict[str, Any]) -> str:
    """Format a dict as an SSE frame.

    The frame uses the ``event:`` field (set to the event ``type``)
    and the ``data:`` field (JSON-serialised event). Clients that
    parse via ``EventSource`` get distinct events by type; raw HTTP
    clients see a stream of ``event:`` / ``data:`` pairs separated
    by blank lines (the SSE protocol).
    """
    import json
    event_type = str(event.get("type") or event.get("event") or "message")
    payload = json.dumps(event, sort_keys=True)
    return f"event: {event_type}\ndata: {payload}\n\n"
