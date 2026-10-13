"""ADR-0258 Stage 3 — A2A relay: an encrypted store-and-forward dumb pipe.

Closes the case ADR-0198's proactive-reconnect and ADR-0258 Stage 1/2 cannot:
two peers with NO direct route to each other (mobile-carrier CGNAT, hotel/
airport WiFi client isolation, corporate firewalls, or simply two devices
each roaming on different networks). Both peers hold a persistent OUTBOUND
WebSocket connection to a relay — outbound connections are permitted almost
everywhere inbound ones are not — and the relay forwards opaque,
already-AEAD-encrypted envelopes between them.

Non-negotiable trust property: the relay must never be able to read a
message it routes, even if the relay operator is fully malicious or
compelled. This holds because the two peers already share a secret (the
friendship token's ``key``, ADR-0070) the relay never sees — every payload
is AES-256-GCM encrypted with a key derived from it
(:func:`a2a_friendship.encrypt_for_relay`) before it ever reaches this
module. This module transports ciphertext only and never attempts to
decrypt anything.

Registration credential (``relay_auth_key``): a FOURTH key derived from the
same shared secret (label ``"a2a-relay-auth-v1"``, alongside hmac_key/
recv_key/enc_key), used ONLY to claim a routing slot for a ``kid`` on this
relay. This is deliberately NOT a zero-knowledge proof — the relay learns
the raw credential value on first registration and pins it in memory for
that ``kid`` (trust-on-first-use, like SSH host keys). Leaking it to the
relay is safe: it is single-purpose (routing only) and grants neither
content confidentiality (no enc_key) nor forgery capability (no hmac_key/
recv_key) even to a fully malicious relay operator. The accepted residual
risk is a race at the VERY FIRST registration for a kid — whoever registers
first pins the slot for the relay's uptime. Mitigation is operator trust in
which relay they configure (self-hosted, or one they have reason to trust),
not a cryptographic guarantee; documented here rather than hidden.

Everything is in-memory only — no disk state, no persistence across a
restart. A relay is a liveness bridge, not a message-durability guarantee:
queued messages for an offline peer are bounded and TTL'd, dropped on
overflow rather than growing without limit.

CI lint: module MUST NOT import the anthropic SDK.
"""
from __future__ import annotations

import functools
import json
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fastapi import APIRouter, FastAPI, WebSocket, WebSocketDisconnect

# ── Tunables ─────────────────────────────────────────────────────────────

_MAX_QUEUE_PER_KID = 32          # bounded — a liveness bridge, not a mailbox
_QUEUE_TTL_S = 300.0             # 5 min — long enough for a brief reconnect
# 2026-09-25 size alignment. The old single 512 KB frame cap could not carry
# what the direct path carries: an A2A envelope may hold 1 MiB of attachments
# (a2a_attachments.MAX_ATTACHMENTS_TOTAL_BYTES), base64-expanded to ~1.4 MB of
# JSON, which encrypt_for_relay then HEX-encodes (x2) — ~2.8 MB on the wire.
# Worse, _validate_deliver allowed 1 MB of ciphertext while the raw-frame
# check had already refused anything above 512 KB, so the two limits
# disagreed and the effective cap was undocumented. One chain now, derived
# from the plaintext cap: the relay carries any payload the direct HTTP path
# accepts (a2a_http_server's 4 MiB inbound body cap).
_MAX_PLAINTEXT_BYTES = 4 * 1024 * 1024                  # == direct inbound cap
_MAX_CIPHERTEXT_HEX = 2 * (_MAX_PLAINTEXT_BYTES + 16)    # hex(ciphertext || GCM tag)
_FRAME_OVERHEAD_BYTES = 4096                             # type/kids/nonce/task_id/tags
_MAX_MESSAGE_BYTES = _MAX_CIPHERTEXT_HEX + _FRAME_OVERHEAD_BYTES
# websockets' client default max_size is 1 MiB: a listener or sender that
# kept it would be torn down (close 1009) by the first legitimate large
# frame. Every client connection in this module passes this explicitly.
_WS_CLIENT_MAX_SIZE = _MAX_MESSAGE_BYTES + _FRAME_OVERHEAD_BYTES
# One full-size frame per offline recipient (round 9); see RelayState._deliver.
_MAX_QUEUE_BYTES_PER_KID = _MAX_MESSAGE_BYTES
_MAX_KIDS_PER_CONNECTION = 64    # one operator process may pair with many peers
# 2026-07-30 — memory-exhaustion DoS fix: `register()` accepted any
# syntactically-valid kid from ANY unauthenticated caller (TOFU pinning is
# the point — there's no shared secret to check against on first contact),
# and _KidSlot entries were never evicted, only the per-slot message QUEUE
# was bounded. An attacker could open a connection, register up to
# _MAX_KIDS_PER_CONNECTION fake kids, disconnect, and repeat indefinitely —
# permanently growing self._slots with no ceiling. This caps the TOTAL
# number of distinct kids the process will track; once at capacity, a
# never-before-seen kid is rejected (an already-registered kid re-pinning
# its own slot still succeeds — this bounds growth, not legitimate re-use).
_MAX_TOTAL_SLOTS = 10_000

# 2026-07-30 relay redesign (A2/A3): the _MAX_TOTAL_SLOTS cap bounded the NUMBER
# of slots but not slots × payload, and nothing ever evicted a slot — TTL was
# only checked when a kid re-registered (which an attacker never does), and each
# fallback send leaked one permanent ephemeral `*:reply:*` slot, so a busy relay
# wedged itself after _MAX_TOTAL_SLOTS legitimate sends. These add a real reaper.
_MAX_TOTAL_QUEUE_BYTES = 64 * 1024 * 1024  # global ceiling across ALL queues
_SLOT_IDLE_TTL_S = 600.0                    # evict an offline, drained slot after 10 min
_REPLY_KID_IDLE_TTL_S = 60.0               # an offline ephemeral reply slot after 1 min

# 2026-09-24 shared-kid fan-out: a friendship kid is IDENTICAL on both peers
# (and so is its relay auth key), so both peers' listeners register the SAME
# slot. With one connection per slot, whoever registered last received both
# directions and the other peer became unreachable via relay — measured: an
# ack sent over the relay was routed straight back to its own sender. A slot
# now holds every live connection that proved the pinned credential and a
# delivery fans out to all of them; each listener drops its own traffic via
# the `_relay_sender_instance_id` / `sender_instance_id` self-delivery guard,
# so exactly the peer answers. Bounded so one credential cannot pile up
# unbounded stale sockets: the oldest is un-claimed beyond this.
_MAX_CONNECTIONS_PER_KID = 4

# Receiver-side listener concurrency (2026-09-25). The listener used to await
# each delivery inline, so one long worker run (bounded only by the envelope
# ttl, 60 s by default) blocked every other peer's ping and task on the same
# socket. Each delivery is now its own task; heavy dispatches (task envelopes,
# friendship acks) are bounded by a semaphore, pings are not (they are cheap
# and are exactly what must stay responsive). The in-flight cap bounds memory
# if a peer floods the socket faster than we answer.
_MAX_CONCURRENT_DELIVERIES = 16
_MAX_INFLIGHT_DELIVERIES = 256

async def _run_control(fn: Any, *args: Any, **kwargs: Any) -> Any:
    """Friendship acks / revoke notices: their own small executor, never
    behind worker runs (round 3)."""
    try:
        import remote_trigger_receiver as _rtr  # noqa: PLC0415
    except Exception:  # noqa: BLE001
        import asyncio as _aio  # noqa: PLC0415
        return await _aio.to_thread(fn, *args, **kwargs)
    return await _rtr.run_a2a_control(fn, *args, **kwargs)


async def _run_work(fn: Any, *args: Any, **kwargs: Any) -> Any:
    """Heavy relay deliveries on the shared A2A work executor (never the
    default pool pings use), with ContextVars carried like ``to_thread``."""
    try:
        import remote_trigger_receiver as _rtr  # noqa: PLC0415
    except Exception:  # noqa: BLE001 — standalone relay process without receiver
        import asyncio as _aio  # noqa: PLC0415
        return await _aio.to_thread(fn, *args, **kwargs)
    return await _rtr.run_a2a_work(fn, *args, **kwargs)


# Prometheus label for relay-side metrics. The relay is not tenant-scoped (it
# routes opaque ciphertext for any operator); the ADR-0007 default tenant is
# used so the series line up with the collector's label schema.
_METRICS_TENANT = "_default"

# Closed set of relay error reasons a client may surface (ADR-0197
# closed-template discipline: a relay-supplied string never flows verbatim).
_RELAY_ERROR_REASONS = frozenset({
    "message_too_large", "invalid_json", "envelope_not_object",
    "invalid_register", "invalid_deliver", "unknown_message_type",
})
_REGISTER_REJECT_REASONS = frozenset({
    "auth_key_mismatch", "too_many_kids", "relay_at_capacity",
})


def instance_tag(kid: str, instance_id: str) -> str:
    """Per-kid pseudonymous tag for one INSTANCE's traffic on a shared kid.

    A friendship kid (and its relay credential) is identical on both peers,
    so the relay cannot tell the two peers' listeners apart. Both sides stamp
    this tag — listeners on ``register`` (``instance_tag``), senders on
    ``deliver`` (``from_instance_tag``) — so the relay can (a) skip the
    sender's own listener and (b) QUEUE a delivery when the only live
    connection is that own listener (the peer is reconnecting), instead of
    reporting it "delivered" into a socket that drops it.

    Derived with a label + the kid so the relay never learns the raw
    instance_id and tags do not correlate across kids by value. Purely a
    routing hint: a peer that lies about it can only hurt its own delivery.
    Additive wire fields — an old relay ignores them, an old client omits
    them (the relay then behaves exactly as before).
    """
    import hashlib as _hl  # noqa: PLC0415
    return _hl.sha256(
        f"a2a-relay-instance-tag-v1|{kid}|{instance_id}".encode("utf-8")
    ).hexdigest()[:32]


def _valid_tag(value: Any) -> str | None:
    if isinstance(value, str) and 16 <= len(value) <= 64 and all(
            c in "0123456789abcdef" for c in value):
        return value
    return None


def _metrics_collector() -> Any:
    """The process-wide relay metrics collector, or None (best-effort: a
    metrics failure must never interrupt routing)."""
    try:
        import a2a_relay_metrics as _m  # type: ignore[import-not-found]  # noqa: PLC0415
        return _m.get_relay_metrics()
    except Exception:  # noqa: BLE001
        return None


def _extract_task_id(raw: str) -> str | None:
    """Best-effort task_id from an oversize frame WITHOUT parsing it.

    json.dumps emits keys in insertion order and every sender in this module
    puts ``task_id`` last, so it sits in the frame's tail; the head is checked
    too for other key orders. Bounded scan, never a full parse of a frame we
    are refusing for its size."""
    import re as _re  # noqa: PLC0415
    for part in (raw[-512:], raw[:512]):
        m = _re.search(r'"task_id"\s*:\s*"([A-Za-z0-9_\-:.]{1,128})"', part)
        if m:
            return m.group(1)
    return None


def _is_reply_kid(kid: str) -> bool:
    """Ephemeral per-send reply slot minted by remote_trigger_sender._relay_post
    (`f"{to_kid}:reply:{task_id}"`) — needed for exactly one round-trip, then
    dead weight. Reaped aggressively so a busy relay cannot fill up on them."""
    return ":reply:" in kid


class RelayError(Exception):
    """Base for relay protocol errors — the reason is never sent verbatim
    to the client beyond a closed, fixed set of reason strings (mirrors the
    ADR-0197 closed-template discipline used elsewhere in the A2A stack)."""


@dataclass
class _QueuedMessage:
    payload: dict[str, Any]
    expires_at: float
    nbytes: int = 0
    # instance_tag of the SENDER (from_instance_tag), when it declared one —
    # a flush never hands a message back to the instance that sent it.
    from_tag: str | None = None


@dataclass
class _KidSlot:
    """One routing slot: the pinned auth credential, the live connections (if
    any), and a bounded queue for delivery while the owner is offline."""
    auth_key: str
    # insertion-ordered live connection ids -> that connection's declared
    # instance_tag (None for a listener that predates tags) — see
    # _MAX_CONNECTIONS_PER_KID for why a slot is no longer single-owner.
    connection_ids: dict[str, str | None] = field(default_factory=dict)
    queue: deque[_QueuedMessage] = field(default_factory=lambda: deque(maxlen=_MAX_QUEUE_PER_KID))
    # monotonic timestamp of the last register/deliver touching this slot —
    # drives the idle-slot reaper (A2/A3).
    last_active: float = field(default_factory=time.monotonic)


class RelayState:
    """In-memory routing table. One instance per relay process — deliberately
    NOT persisted (see module docstring: a relay holds no durable state)."""

    def __init__(self, *, metrics: Any = None) -> None:
        self._slots: dict[str, _KidSlot] = {}
        # connection_id -> {WebSocket, set of kids registered on it}
        self._connections: dict[str, tuple[WebSocket, set[str]]] = {}
        # running total of bytes held across every slot's queue — enforces the
        # global ceiling so bounded slots × large payloads cannot exhaust RAM.
        self._queued_bytes = 0
        # Injected collector (tests) or the process-wide one (lazy). 2026-09-25:
        # record_registration/record_delivery had zero callers, so /metrics
        # exported constant zeros; they are now fed from here.
        self._metrics = metrics

    def metrics(self) -> Any:
        return self._metrics if self._metrics is not None else _metrics_collector()

    def _record(self, fn: str, *args: Any, **kwargs: Any) -> None:
        try:
            m = self.metrics()
            if m is not None:
                getattr(m, fn)(_METRICS_TENANT, *args, **kwargs)
        except Exception:  # noqa: BLE001 — metrics never break routing
            pass

    # ── reaper (A2/A3) ───────────────────────────────────────────────

    def _prune(self) -> None:
        """Evict dead weight. Called opportunistically from register/deliver
        (the relay app has no scheduler): drop expired queue items, then evict
        offline slots that are drained and idle past their TTL — aggressively
        for ephemeral reply slots. A slot with a LIVE connection, or one still
        holding non-expired queued messages for a peer that may reconnect, is
        never evicted."""
        now = time.monotonic()
        for kid in list(self._slots.keys()):
            slot = self._slots.get(kid)
            if slot is None:
                continue
            # 1. drop expired queued messages (this is the ONLY place TTL was
            #    ever enforced before if the kid never re-registered).
            while slot.queue and slot.queue[0].expires_at < now:
                stale = slot.queue.popleft()
                self._queued_bytes -= stale.nbytes
            # 2. never touch a live or still-queued slot.
            if slot.connection_ids or slot.queue:
                continue
            # 3. evict an offline, drained slot once it is idle past its TTL.
            ttl = _REPLY_KID_IDLE_TTL_S if _is_reply_kid(kid) else _SLOT_IDLE_TTL_S
            if now - slot.last_active > ttl:
                del self._slots[kid]

    # ── registration ────────────────────────────────────────────────

    def register(self, connection_id: str, kid: str, auth_key: str,
                 instance_tag: str | None = None) -> str | None:
        """Claim (or reclaim) the routing slot for ``kid`` on this connection.

        ``instance_tag`` (optional, see :func:`instance_tag`) identifies which
        instance this connection belongs to on a shared kid.

        Returns None on success, or a fixed rejection-reason string:
          - "auth_key_mismatch" — a DIFFERENT credential was already pinned
            for this kid (TOFU pin conflict — the connecting client does not
            hold the same shared secret as whoever registered first).
          - "too_many_kids" — this connection already registered the max.
          - "relay_at_capacity" — the process-wide slot table is full (see
            _MAX_TOTAL_SLOTS); only applies to a BRAND NEW kid, never to one
            already tracked (re-registering/reconnecting always succeeds).
        """
        outcome = self._register(connection_id, kid, auth_key, _valid_tag(instance_tag))
        self._record("record_registration", outcome or "success")
        return outcome

    def _register(self, connection_id: str, kid: str, auth_key: str,
                  tag: str | None) -> str | None:
        self._prune()  # reclaim dead slots before deciding we're at capacity
        slot = self._slots.get(kid)
        if slot is None:
            if len(self._slots) >= _MAX_TOTAL_SLOTS:
                return "relay_at_capacity"
            self._slots[kid] = _KidSlot(auth_key=auth_key, connection_ids={connection_id: tag})
            return None
        if slot.auth_key != auth_key:
            return "auth_key_mismatch"
        # Join the slot (re-registration moves this connection to the newest
        # position); evict the oldest beyond the cap.
        slot.connection_ids.pop(connection_id, None)
        slot.connection_ids[connection_id] = tag
        while len(slot.connection_ids) > _MAX_CONNECTIONS_PER_KID:
            oldest = next(iter(slot.connection_ids))
            del slot.connection_ids[oldest]
        slot.last_active = time.monotonic()
        return None

    def flush_queue(self, kid: str, instance_tag: str | None = None) -> list[dict[str, Any]]:
        """Pop and return every non-expired queued message for ``kid``, in
        delivery order. Called right after a successful registration.

        A message whose sender declared the SAME instance tag as the
        registering connection stays queued: handing a peer's queued task to
        the sender's own (reconnecting) listener would drop it — it is kept
        for the peer's registration instead."""
        slot = self._slots.get(kid)
        if slot is None:
            return []
        tag = _valid_tag(instance_tag)
        now = time.monotonic()
        out: list[dict[str, Any]] = []
        keep: list[_QueuedMessage] = []
        while slot.queue:
            item = slot.queue.popleft()
            if item.expires_at < now:
                self._queued_bytes -= item.nbytes
                continue
            if tag is not None and item.from_tag == tag:
                keep.append(item)
                continue
            self._queued_bytes -= item.nbytes
            out.append(item.payload)
        slot.queue.extend(keep)
        return out

    # ── delivery ────────────────────────────────────────────────────

    async def deliver(self, to_kid: str, payload: dict[str, Any], *,
                      from_instance_tag: str | None = None) -> str:
        """Forward ``payload`` to every live connection of ``to_kid``, or queue it.

        Returns "delivered", "queued", or "dropped" (queue full / unknown
        kid with no prior registration at all — nothing to queue against).

        ``from_instance_tag``: the sender's instance tag. A connection that
        declared the same tag is the SENDER'S OWN listener on this shared kid;
        it is skipped and never counts as a delivery. If it was the only live
        connection, the message is queued for the peer's reconnect (2026-09-25:
        it used to count as "delivered" and was silently dropped by that
        listener's self-delivery guard, so the peer never got it).
        """
        t0 = time.monotonic()
        outcome = await self._deliver(to_kid, payload, _valid_tag(from_instance_tag))
        self._record("record_delivery", outcome,
                     latency_ms=(time.monotonic() - t0) * 1000.0)
        return outcome

    async def _deliver(self, to_kid: str, payload: dict[str, Any],
                       from_tag: str | None) -> str:
        self._prune()  # keep expired items from counting against the byte budget
        slot = self._slots.get(to_kid)
        if slot is None:
            # No one has EVER registered this kid on this relay — queuing
            # would grow unbounded for kids that will never claim it.
            return "dropped"
        slot.last_active = time.monotonic()
        if slot.connection_ids:
            text = json.dumps(payload)
            delivered = False
            for cid, conn_tag in list(slot.connection_ids.items()):
                if from_tag is not None and conn_tag == from_tag:
                    continue  # the sender's own listener — not the recipient
                conn = self._connections.get(cid)
                if conn is None:
                    slot.connection_ids.pop(cid, None)
                    continue
                ws, _kids = conn
                try:
                    await ws.send_text(text)
                    delivered = True
                except Exception:  # noqa: BLE001 — try the other connections, then queue
                    continue
            if delivered:
                return "delivered"
        if len(slot.queue) >= _MAX_QUEUE_PER_KID:
            return "dropped"
        # Global byte-budget: bounded slot COUNT is not enough on its own — a
        # cap of slots × large queued payloads would still be many GB. Refuse
        # to queue once the process-wide ceiling is reached (A2).
        nbytes = len(json.dumps(payload))
        if self._queued_bytes + nbytes > _MAX_TOTAL_QUEUE_BYTES:
            return "dropped"
        # Per-slot budget (round 9): with frames up to _MAX_MESSAGE_BYTES, one
        # kid's 32 slots could hold several times the global ceiling, so a
        # single self-registered kid filled the whole budget and every other
        # offline peer's delivery was dropped for the queue TTL.
        if sum(q.nbytes for q in slot.queue) + nbytes > _MAX_QUEUE_BYTES_PER_KID:
            return "dropped"
        slot.queue.append(_QueuedMessage(
            payload=payload, expires_at=time.monotonic() + _QUEUE_TTL_S,
            nbytes=nbytes, from_tag=from_tag))
        self._queued_bytes += nbytes
        return "queued"

    # ── connection lifecycle ────────────────────────────────────────

    def open_connection(self, ws: WebSocket) -> str:
        connection_id = uuid.uuid4().hex
        self._connections[connection_id] = (ws, set())
        self._record("update_active_connections", len(self._connections))
        return connection_id

    def note_registered_kid(self, connection_id: str, kid: str) -> None:
        conn = self._connections.get(connection_id)
        if conn is not None:
            conn[1].add(kid)

    def registered_kid_count(self, connection_id: str) -> int:
        conn = self._connections.get(connection_id)
        return len(conn[1]) if conn is not None else 0

    def close_connection(self, connection_id: str) -> None:
        conn = self._connections.pop(connection_id, None)
        if conn is None:
            return
        self._record("update_active_connections", len(self._connections))
        _ws, kids = conn
        now = time.monotonic()
        for kid in kids:
            slot = self._slots.get(kid)
            if slot is not None and connection_id in slot.connection_ids:
                # Un-claim this connection, but keep the pinned auth_key and
                # any already-queued messages — a reconnect with the same
                # credential resumes exactly where it left off.
                del slot.connection_ids[connection_id]
                if slot.connection_ids:
                    continue  # the peer's connection still holds the slot
                # Reset the idle clock from the moment it went offline, so the
                # reaper's TTL measures how long it has been GONE (A2/A3). An
                # ephemeral reply slot that will never reconnect is now on the
                # short reply-kid TTL and gets reclaimed.
                slot.last_active = now
        self._prune()


# ── wire message validation ─────────────────────────────────────────────

def _validate_register(msg: dict[str, Any]) -> tuple[str, str] | None:
    kid = msg.get("kid")
    auth_key = msg.get("relay_auth_key")
    if not isinstance(kid, str) or not kid or len(kid) > 128:
        return None
    if not isinstance(auth_key, str) or len(auth_key) != 64:
        return None
    if not all(c in "0123456789abcdefABCDEF" for c in auth_key):
        return None
    return kid, auth_key


def _validate_deliver(msg: dict[str, Any]) -> dict[str, Any] | None:
    to_kid = msg.get("to_kid")
    from_kid = msg.get("from_kid")
    nonce = msg.get("nonce")
    ciphertext = msg.get("ciphertext")
    task_id = msg.get("task_id")
    if not all(isinstance(v, str) and v for v in (to_kid, from_kid, nonce, ciphertext, task_id)):
        return None
    if len(to_kid) > 128 or len(from_kid) > 128 or len(task_id) > 128 or len(nonce) > 64:
        return None
    # Same limit the sender pre-checks (relay_deliver_and_wait) — one chain,
    # derived from _MAX_PLAINTEXT_BYTES; see the tunables block.
    if len(ciphertext) > _MAX_CIPHERTEXT_HEX:
        return None
    # The forwarded payload is rebuilt from known keys only; transport hints
    # (from_instance_tag) are consumed by the relay, never forwarded.
    return {
        "type": "deliver", "to_kid": to_kid, "from_kid": from_kid,
        "nonce": nonce, "ciphertext": ciphertext, "task_id": task_id,
    }


def _error_frame(reason: str, task_id: Any = None) -> str:
    """Relay error frame. Echoes ``task_id`` when one is known (2026-09-25):
    the frame used to carry no correlation, so a sender whose deliver was
    refused (e.g. too large) ignored it and waited out its full timeout.
    Additive field — an old client ignores it."""
    frame: dict[str, Any] = {"type": "error", "reason": reason}
    if isinstance(task_id, str) and 0 < len(task_id) <= 128:
        frame["task_id"] = task_id
    return json.dumps(frame)


# ── FastAPI wiring ───────────────────────────────────────────────────────

def build_relay_router(state: RelayState) -> APIRouter:
    """Build the WebSocket route bound to ``state``. Mountable into an
    existing FastAPI app (e.g. corvin_console.standalone), or served by
    :func:`build_relay_app` for a standalone process."""
    router = APIRouter()

    @router.websocket("/v1/a2a/relay/connect")
    async def relay_connect(websocket: WebSocket) -> None:  # noqa: ANN001
        await websocket.accept()
        connection_id = state.open_connection(websocket)
        try:
            while True:
                raw = await websocket.receive_text()
                if len(raw) > _MAX_MESSAGE_BYTES:
                    await websocket.send_text(_error_frame(
                        "message_too_large", _extract_task_id(raw)))
                    continue
                try:
                    msg = json.loads(raw)
                except (ValueError, TypeError):
                    await websocket.send_text(_error_frame("invalid_json"))
                    continue
                if not isinstance(msg, dict):
                    await websocket.send_text(_error_frame("envelope_not_object"))
                    continue

                msg_type = msg.get("type")
                if msg_type == "register":
                    if state.registered_kid_count(connection_id) >= _MAX_KIDS_PER_CONNECTION:
                        await websocket.send_text(json.dumps(
                            {"type": "register_rejected", "reason": "too_many_kids"}))
                        continue
                    parsed = _validate_register(msg)
                    if parsed is None:
                        await websocket.send_text(_error_frame("invalid_register"))
                        continue
                    kid, auth_key = parsed
                    tag = _valid_tag(msg.get("instance_tag"))
                    rejection = state.register(connection_id, kid, auth_key, tag)
                    if rejection is not None:
                        await websocket.send_text(json.dumps(
                            {"type": "register_rejected", "kid": kid, "reason": rejection}))
                        continue
                    state.note_registered_kid(connection_id, kid)
                    await websocket.send_text(json.dumps({"type": "registered", "kid": kid}))
                    for queued in state.flush_queue(kid, tag):
                        await websocket.send_text(json.dumps(queued))

                elif msg_type == "deliver":
                    parsed_deliver = _validate_deliver(msg)
                    if parsed_deliver is None:
                        await websocket.send_text(_error_frame(
                            "invalid_deliver", msg.get("task_id")))
                        continue
                    # to_kid stays IN the forwarded payload (unlike an
                    # earlier draft that stripped it) — a listener that
                    # registered multiple kids on one connection needs it to
                    # know which of ITS kids an inbound "deliver" is for.
                    to_kid = parsed_deliver["to_kid"]
                    outcome = await state.deliver(
                        to_kid, parsed_deliver,
                        from_instance_tag=msg.get("from_instance_tag"))
                    await websocket.send_text(json.dumps(
                        {"type": "deliver_ack", "task_id": parsed_deliver["task_id"], "outcome": outcome}))

                else:
                    await websocket.send_text(_error_frame("unknown_message_type"))
        except WebSocketDisconnect:
            pass
        finally:
            state.close_connection(connection_id)

    return router


# ── Sender-side client: ephemeral round trip ────────────────────────────
#
# One short-lived WebSocket connection per relay-fallback attempt: connect,
# register OUR OWN kid (so the peer's response can route back to us),
# deliver the already-encrypted envelope, wait for the correlating response
# (same task_id), close. This mirrors the direct-HTTP path's request/
# response shape closely enough that remote_trigger_sender.py's EXISTING
# response-verification code (_verify_response) can be reused unchanged —
# see that module's _relay_post for how the two are stitched together.

class RelayTransportError(Exception):
    """Sender-side relay failure — connection refused, registration
    rejected, delivery dropped, or no response within the timeout. The
    caller (remote_trigger_sender.py) maps this into the SAME TransportError
    taxonomy the direct-HTTP path already uses, so callers see one
    consistent error shape regardless of which transport was attempted.

    ``maybe_delivered`` (2026-09-25): False only when the relay provably did
    NOT hand the envelope to the peer (connect/registration failed, the relay
    refused or dropped the frame, local size check). True once the deliver
    frame left this process and no refusal came back — the peer may be
    executing it, so a caller must NOT retry with a new task (duplicate
    execution) or re-send the same one (nonce replay).
    """

    def __init__(self, reason: str, *, maybe_delivered: bool = True) -> None:
        super().__init__(reason)
        self.reason = reason
        self.maybe_delivered = maybe_delivered


def relay_frame_size(*, to_kid: str, from_kid: str, nonce_hex: str,
                     ciphertext_hex: str, task_id: str) -> int:
    """Byte size of the deliver frame :func:`relay_deliver_and_wait` sends
    (without the optional instance tag, which the overhead budget covers)."""
    return len(json.dumps({
        "type": "deliver", "to_kid": to_kid, "from_kid": from_kid,
        "nonce": nonce_hex, "ciphertext": ciphertext_hex, "task_id": task_id,
    }))


async def relay_deliver_and_wait(
    *, relay_url: str, my_kid: str, my_relay_auth_key: str,
    to_kid: str, nonce_hex: str, ciphertext_hex: str, task_id: str,
    timeout_s: float, from_instance_tag: str | None = None,
) -> dict[str, str]:
    """Sender-side: deliver one already-encrypted envelope via the relay and
    wait for the correlating encrypted response.

    Returns ``{"nonce": ..., "ciphertext": ...}`` for the response payload.
    Raises :class:`RelayTransportError` on any failure — connection error,
    registration rejected (e.g. our OWN kid's auth_key mismatches what is
    already pinned — should not happen for a legitimate pairing, but a
    misconfigured/reused relay could hit this), delivery outcome "dropped",
    a relay error frame for our deliver (fails immediately — e.g.
    ``message_too_large`` from a relay with a smaller frame cap), or timeout
    waiting for the response. ``timeout_s`` bounds the WHOLE exchange.

    Reasons (closed set): ``message_too_large``, ``connect_failed:<Type>``,
    ``registration_timeout``, ``registration_failed:<reason>``,
    ``delivery_dropped``, ``relay_rejected:<reason>``, ``response_timeout``,
    ``connection_lost``, ``malformed_response``.
    """
    import asyncio as _asyncio
    import websockets  # noqa: PLC0415

    frame: dict[str, Any] = {
        "type": "deliver", "to_kid": to_kid, "from_kid": my_kid,
        "nonce": nonce_hex, "ciphertext": ciphertext_hex, "task_id": task_id,
    }
    tag = _valid_tag(from_instance_tag)
    if tag is not None:
        frame["from_instance_tag"] = tag  # additive; an old relay ignores it
    deliver_text = json.dumps(frame)
    # Local size gate BEFORE any network work: a frame the relay will refuse
    # fails here with a typed reason instead of burning a connection (and,
    # against an old relay that sent no task_id on its error frame, the whole
    # timeout).
    if len(ciphertext_hex) > _MAX_CIPHERTEXT_HEX or len(deliver_text) > _MAX_MESSAGE_BYTES:
        raise RelayTransportError("message_too_large", maybe_delivered=False)

    loop = _asyncio.get_running_loop()
    deadline = loop.time() + max(0.1, float(timeout_s))

    def _remaining() -> float:
        return deadline - loop.time()

    phase = "connect"
    try:
        async with websockets.connect(
            relay_url, open_timeout=max(0.1, _remaining()),
            max_size=_WS_CLIENT_MAX_SIZE,
        ) as ws:
            phase = "register"
            await ws.send(json.dumps(
                {"type": "register", "kid": my_kid, "relay_auth_key": my_relay_auth_key}))
            while True:
                if _remaining() <= 0:
                    raise RelayTransportError("registration_timeout", maybe_delivered=False)
                reg_resp = json.loads(await _asyncio.wait_for(ws.recv(), timeout=_remaining()))
                rtype = reg_resp.get("type") if isinstance(reg_resp, dict) else None
                if rtype == "registered":
                    break
                if rtype in ("register_rejected", "error"):
                    r = str(reg_resp.get("reason", ""))
                    r = r if r in (_REGISTER_REJECT_REASONS | _RELAY_ERROR_REASONS) else "unknown"
                    raise RelayTransportError(f"registration_failed:{r}", maybe_delivered=False)
                # anything else before our registration completes: ignore

            phase = "deliver"
            await ws.send(deliver_text)
            phase = "wait"
            while True:
                if _remaining() <= 0:
                    raise RelayTransportError("response_timeout")
                msg = json.loads(await _asyncio.wait_for(ws.recv(), timeout=_remaining()))
                if not isinstance(msg, dict):
                    continue
                mtype = msg.get("type")
                if mtype == "deliver_ack" and msg.get("task_id") == task_id:
                    if msg.get("outcome") == "dropped":
                        raise RelayTransportError("delivery_dropped", maybe_delivered=False)
                    continue  # "delivered" or "queued" — keep waiting for the actual response
                if mtype == "error" and msg.get("task_id") in (None, task_id):
                    # This short-lived connection carries exactly ONE deliver,
                    # so an error frame after it is about that deliver — also
                    # from an old relay that does not echo task_id. The relay
                    # refused the frame; the peer never saw it.
                    r = str(msg.get("reason", ""))
                    if r == "message_too_large":
                        raise RelayTransportError("message_too_large", maybe_delivered=False)
                    r = r if r in _RELAY_ERROR_REASONS else "unknown"
                    raise RelayTransportError(f"relay_rejected:{r}", maybe_delivered=False)
                if (mtype == "deliver" and msg.get("task_id") == task_id
                        and msg.get("to_kid") == my_kid and msg.get("from_kid") == to_kid):
                    r_nonce, r_ct = msg.get("nonce"), msg.get("ciphertext")
                    if not (isinstance(r_nonce, str) and isinstance(r_ct, str)):
                        raise RelayTransportError("malformed_response")
                    return {"nonce": r_nonce, "ciphertext": r_ct}
                # Anything else (a stale/unrelated message) — ignore and keep waiting.
    except RelayTransportError:
        raise
    except (_asyncio.TimeoutError, TimeoutError) as exc:
        if phase == "connect":
            raise RelayTransportError("connect_failed:TimeoutError", maybe_delivered=False) from exc
        if phase == "register":
            raise RelayTransportError("registration_timeout", maybe_delivered=False) from exc
        raise RelayTransportError("response_timeout") from exc
    except Exception as exc:  # noqa: BLE001 — connection refused, DNS, closed socket, bad JSON
        if phase in ("connect", "register"):
            raise RelayTransportError(
                f"connect_failed:{_safe_type_name(exc)}", maybe_delivered=False) from exc
        # "deliver": send() raised — the frame may or may not have left.
        # "wait": the socket died after the frame left. Either way: unknown.
        raise RelayTransportError("connection_lost") from exc


_SAFE_EXC_NAMES = frozenset({
    "OSError", "ConnectionError", "ConnectionRefusedError", "ConnectionResetError",
    "ConnectionAbortedError", "TimeoutError", "gaierror", "SSLError",
    "SSLCertVerificationError", "InvalidURI", "InvalidHandshake", "InvalidStatus",
    "InvalidMessage", "ConnectionClosed", "ConnectionClosedError",
    "ConnectionClosedOK", "ValueError", "JSONDecodeError",
})


def _safe_type_name(exc: BaseException) -> str:
    name = type(exc).__name__
    return name if name in _SAFE_EXC_NAMES else "internal_error"


# ── Receiver-side client: persistent listener ───────────────────────────

_REFRESH_INTERVAL_S = 15.0
_nudge_flag = threading.Event()


def nudge_listeners() -> None:
    """Ask every running RelayListener to re-read its connection files now.
    Thread-safe; the console calls it right after a pairing changes."""
    _nudge_flag.set()


async def _wait_for_nudge(timeout_s: float) -> None:
    import asyncio as _asyncio  # noqa: PLC0415
    loop = _asyncio.get_running_loop()
    deadline = loop.time() + timeout_s
    while loop.time() < deadline:
        if _nudge_flag.is_set():
            await _asyncio.sleep(0.2)  # let the writer finish its file
            _nudge_flag.clear()
            return
        await _asyncio.sleep(min(0.5, max(0.0, deadline - loop.time())))

class RelayListener:
    """Receiver-side persistent connection to a configured relay.

    Registers every ``kid`` this instance has an ACTIVE origin record for,
    plus every ``kid`` it has a live PENDING outbound-token record for when a
    ``pending_dir`` is configured (ADR-0257 first-bootstrap-over-relay: the
    issuer must claim a slot for a freshly-issued token's kid BEFORE the
    reciprocal ack arrives, since its own origin record is written only after
    that ack is processed — see :meth:`_registrable_kids`). It then
    listens for inbound "deliver" messages, decrypts+dispatches each to the
    right handler for its shape, and relays the signed response back.
    Reconnects with backoff on any drop — this is a best-effort liveness
    bridge, not a guaranteed-delivery channel; a peer that only reaches us
    via the relay simply retries at the send()/ping() layer like any other
    transient failure.

    Three disjoint payload shapes are dispatched (2026-08-02, closing the
    gap where only real task delivery had a relay path — ping/recheck and
    the friendship-ack handshake stayed direct-only, so the console's
    reachability status could never reflect a relay-only-reachable peer
    even once one was configured):

    - Task envelope (``task_id``+``instruction`` present) → the SAME
      ``RemoteTriggerReceiver.receive()`` pipeline a direct HTTP POST to
      ``/v1/a2a/receive`` would use (unchanged from before this date).
    - Ping request (``ping_id`` present, ADR-0199) →
      ``a2a_http_server.process_ping_request()``, the same shared core the
      direct ``POST /v1/a2a/ping`` route uses.
    - Friendship-ack request (``peer_url``+``kid`` present, no ``task_id``/
      ``ping_id``, ADR-0257) →
      ``a2a_friendship.process_friendship_ack_request()``, the same shared
      core the direct ``POST /v1/a2a/friendship-ack`` route uses. Requires
      ``pending_dir``/``endpoints_dir`` to be configured; a listener built
      without them (e.g. an older caller, or existing tests) silently
      drops ack deliveries instead of raising — inert, not broken.

    Intended lifecycle: one instance constructed and run as a background
    asyncio task from an app's lifespan (see corvin_console.standalone),
    started only when a2a_relay_fallback is enabled AND a relay URL is
    configured — inert (never constructed) otherwise.
    """

    def __init__(
        self, *, relay_url: str, receiver: Any, origins_dir: "Any",
        pending_dir: "Any | None" = None, endpoints_dir: "Any | None" = None,
        instance_id: "str | None" = None,
    ) -> None:
        self._relay_url = relay_url
        # This instance's id for the self-delivery guard and the relay
        # instance tag. None → the process identity (production); explicit
        # only where several instances share one process (tests, e2e hosts).
        self._instance_id = instance_id
        self._receiver = receiver
        self._origins_dir = origins_dir
        self._pending_dir = pending_dir
        self._endpoints_dir = endpoints_dir
        self._stop = False
        self._ws: Any = None
        # Deliveries run concurrently (see _MAX_CONCURRENT_DELIVERIES); every
        # in-flight task is tracked so it can outlive a reconnect and answer
        # on the NEW socket (responses are routed by the sender's per-task
        # reply kid, so any connection of ours can carry them).
        self._inflight: set[Any] = set()
        self._dispatch_sem: Any = None
        self._register_sent_at: dict[str, float] = {}  # handshake latency metric
        # Diagnostics snapshot (the console's A2A diagnostics route reads it).
        self.status: dict[str, Any] = {
            "relay_url": relay_url, "connected": False, "registered": 0,
            "rejected": 0, "last_error": None, "connected_since": None,
        }

    @property
    def relay_url(self) -> str:
        return self._relay_url

    def stop(self) -> None:
        self._stop = True
        ws = self._ws
        if ws is not None:
            try:
                import asyncio as _asyncio  # noqa: PLC0415
                _asyncio.get_running_loop().create_task(ws.close())
            except Exception:  # noqa: BLE001 — best-effort; run_forever exits on _stop
                pass

    def _registrable_kids(self) -> list[tuple[str, str]]:
        """(kid, hmac_key) for every kid this instance should claim a relay
        listener slot for. hmac_key is what enc_key/relay_auth_key are derived
        from (see a2a_friendship._derive_enc_key).

        Two sources are unioned, deduped by kid (an ACTIVE origin always wins):

        1. Every enabled ``_friendship`` origin record in ``origins_dir`` — an
           established pairing whose hmac_key is stored directly. Covers every
           reconnect (already worked before this fix).

        2. Every live PENDING friendship record in ``pending_dir`` — an
           OUTBOUND token this instance ISSUED (save_pending_friendship) whose
           reciprocal ack has not yet arrived (ADR-0257). This closes the
           first-bootstrap-over-relay gap: on a BRAND-NEW pairing the issuer
           writes its ``_friendship`` origin record only AFTER
           process_friendship_ack_request runs, so before the ack it had NO
           origin record to register — the relay had no slot for the kid, and
           the redeemer's first reciprocal ack was ``dropped`` (unknown kid),
           never delivered. The pairing then hung unless a LAN direct-connect
           dodged the relay. Registering a slot for the pending kid up front
           lets the issuer receive that first ack over the relay path.

           The pending record persists only the raw shared token ``key``; the
           hmac_key is derived from it exactly as BOTH peers derive it
           (_derive_channel_keys), so the slot registered here decrypts the
           redeemer's ack under the same key the redeemer signed+encrypted it
           with — no trust or signature check is weakened (the ack's own
           signature is still verified inside process_friendship_ack_request).
           Only honoured when ``pending_dir`` is configured; an older listener
           without one keeps the origin-only behavior (inert, not broken).
        """
        import json as _json
        from pathlib import Path as _Path

        out: list[tuple[str, str]] = []
        seen: set[str] = set()
        d = _Path(self._origins_dir)
        if d.exists():
            for p in sorted(d.glob("*.json")):
                try:
                    cfg = _json.loads(p.read_text("utf-8"))
                except (OSError, ValueError):
                    continue
                if not cfg.get("_friendship"):
                    continue
                if not cfg.get("enabled") and cfg.get("state") != "PENDING":
                    # (A PENDING connection — imported without a peer URL — is
                    # exactly the one that needs the relay to learn it.)
                    # A deliberately-disabled/revoked friendship must not be
                    # resurrected by a lingering pending record for the same kid:
                    # claim the kid in `seen` so the pending branch below skips it,
                    # but do NOT register a relay slot for it.
                    seen.add(p.stem)
                    continue
                hmac_key = cfg.get("hmac_key")
                if isinstance(hmac_key, str) and len(hmac_key) == 64 and p.stem not in seen:
                    seen.add(p.stem)
                    out.append((p.stem, hmac_key))

        if self._pending_dir is not None:
            import a2a_friendship as _ft  # noqa: PLC0415
            pd = _Path(self._pending_dir)
            if pd.exists():
                for p in sorted(pd.glob("*.json")):
                    kid = p.stem
                    if kid in seen:
                        continue  # an ACTIVE origin already covers this kid
                    rec = _ft.load_pending_friendship(kid, pending_dir=pd)
                    if rec is None:
                        continue  # absent or expired
                    key = rec.get("key")
                    if not (isinstance(key, str) and len(key) == 64):
                        continue
                    hmac_key, _recv_key = _ft._derive_channel_keys(key)
                    seen.add(kid)
                    out.append((kid, hmac_key))
        return out

    async def run_forever(self, *, reconnect_backoff_s: float = 10.0) -> None:
        while not self._stop:
            try:
                await self._connect_and_serve()
            except Exception as exc:  # noqa: BLE001 — reconnect, never crash the caller
                self.status["last_error"] = type(exc).__name__
            if self._stop:
                return
            # Wakes early when a pairing appears (first registration).
            await _wait_for_nudge(reconnect_backoff_s)

    async def _connect_and_serve(self) -> None:
        import asyncio as _asyncio  # noqa: PLC0415
        import websockets  # noqa: PLC0415
        import a2a_friendship as _ft  # noqa: PLC0415

        kids = self._registrable_kids()
        if not kids:
            return  # nothing to listen for yet — try again next backoff cycle

        import logging as _logging  # noqa: PLC0415
        _log = _logging.getLogger("corvin.a2a.relay-listener")

        my_instance = self._my_instance_id()

        async with websockets.connect(
            self._relay_url, open_timeout=15, max_size=_WS_CLIENT_MAX_SIZE,
        ) as ws:
            self._ws = ws
            kids_by_id: dict[str, str] = {}
            _registered: set[str] = set()
            self._register_sent_at = {}

            async def _register_new(current: list[tuple[str, str]]) -> None:
                for kid, hmac_key in current:
                    if kid in kids_by_id:
                        continue
                    kids_by_id[kid] = hmac_key
                    auth_key = _ft.derive_relay_auth_key(hmac_key)
                    reg: dict[str, Any] = {
                        "type": "register", "kid": kid, "relay_auth_key": auth_key}
                    if my_instance:
                        # Lets the relay tell OUR listener from the peer's on
                        # this shared kid (see instance_tag); ignored by an
                        # old relay.
                        reg["instance_tag"] = instance_tag(kid, my_instance)
                    self._register_sent_at[kid] = time.monotonic()
                    await ws.send(json.dumps(reg))
                # A revoked connection stops being served immediately; the
                # relay drops the slot on the next reconnect.
                live = {kid for kid, _h in current}
                for kid in [k for k in kids_by_id if k not in live]:
                    kids_by_id.pop(kid, None)
                    _registered.discard(kid)
                self.status["registered"] = len(_registered)

            await _register_new(kids)
            self.status.update(connected=True, last_error=None, connected_since=time.time())
            _log.warning("A2A relay listener connected: %s (%d kid(s))",
                         self._relay_url, len(kids_by_id))

            async def _refresh_loop() -> None:
                # A pairing made after connect must be reachable without a
                # restart (2026-09-24: registrations were computed once per
                # connection, so a friendship created after boot was never
                # registered until the socket happened to drop). The console
                # nudges this right after a create/import.
                while not self._stop:
                    await _wait_for_nudge(_REFRESH_INTERVAL_S)
                    try:
                        current = await _asyncio.to_thread(self._registrable_kids)
                        await _register_new(current)
                    except Exception:  # noqa: BLE001 — a dead socket ends the serve loop
                        return

            refresher = _asyncio.create_task(_refresh_loop())
            try:
                # drain=False: a delivery still running when this socket drops
                # keeps running and answers on the next connection (self._ws)
                # instead of holding the reconnect hostage for up to a ttl.
                await self._serve(ws, kids_by_id, _registered, _log, drain=False)
            finally:
                refresher.cancel()
                if self._ws is ws:
                    self._ws = None
                self.status.update(connected=False, registered=0)

    def _record_handshake(self, kid: Any, outcome: str) -> None:
        try:
            sent = getattr(self, "_register_sent_at", {}).pop(kid, None)
            m = _metrics_collector()
            if m is not None:
                latency = (time.monotonic() - sent) * 1000.0 if sent is not None else 0
                m.record_handshake(_METRICS_TENANT, outcome, latency_ms=latency)
        except Exception:  # noqa: BLE001 — metrics never break the listener
            pass

    async def _serve(self, ws: Any, kids_by_id: dict[str, str],
                     _registered: set[str], _log: Any, *, drain: bool = True) -> None:
        """Read frames and dispatch each "deliver" as its OWN task.

        2026-09-25: this used to ``await self._handle_deliver`` inline, so a
        60 s worker run for one peer stalled every other frame on the socket —
        pings timed out and the console marked healthy peers unreachable.
        Responses need no ordering: each carries the request's task_id and is
        addressed to that request's own reply kid (``<kid>:reply:<task_id>``),
        which the sender matches on (relay_deliver_and_wait).

        ``drain``: wait for in-flight deliveries before returning (tests and
        direct callers). The reconnect loop passes False — see there.
        """
        import asyncio as _asyncio  # noqa: PLC0415
        inflight: set[Any] = set()
        try:
            async for raw in ws:
                if self._stop:
                    return
                try:
                    msg = json.loads(raw)
                except (ValueError, TypeError):
                    continue
                if not isinstance(msg, dict):
                    continue
                mtype = msg.get("type")
                # A4 (2026-07-30 relay redesign): the old loop discarded every
                # non-"deliver" frame, so `registered` / `register_rejected` were
                # never read. A rejected registration (relay_at_capacity, >64
                # kids, auth_key_mismatch from a squatted slot) then failed
                # SILENTLY — the console believed it was listening and received
                # nothing, with no log, no audit, no health signal. Surface it.
                if mtype == "registered":
                    k = msg.get("kid")
                    if isinstance(k, str):
                        _registered.add(k)
                        self.status["registered"] = len(_registered)
                        self._record_handshake(k, "success")
                    continue
                if mtype == "register_rejected":
                    self.status["rejected"] = int(self.status.get("rejected") or 0) + 1
                    self._record_handshake(msg.get("kid"), "rejected")
                    _log.warning(
                        "relay rejected registration for kid=%s reason=%s — this "
                        "peer is UNREACHABLE via the relay until resolved",
                        str(msg.get("kid"))[:16], msg.get("reason"),
                    )
                    continue
                if mtype != "deliver":
                    continue
                if len(self._inflight) >= _MAX_INFLIGHT_DELIVERIES:
                    _log.warning("relay listener: %d deliveries in flight — dropping "
                                 "one (the sender times out and may retry)",
                                 len(self._inflight))
                    continue
                task = _asyncio.create_task(self._handle_deliver(ws, msg, kids_by_id))
                inflight.add(task)
                self._inflight.add(task)
                task.add_done_callback(inflight.discard)
                task.add_done_callback(self._inflight.discard)
        finally:
            if drain and inflight and not self._stop:
                await _asyncio.gather(*list(inflight), return_exceptions=True)

    async def _handle_deliver(self, ws: Any, msg: dict[str, Any], kids_by_id: dict[str, str]) -> None:
        import a2a_friendship as _ft  # noqa: PLC0415

        to_kid = msg.get("to_kid")
        from_kid = msg.get("from_kid")
        nonce = msg.get("nonce")
        ciphertext = msg.get("ciphertext")
        task_id = msg.get("task_id")
        if not all(isinstance(v, str) and v for v in (to_kid, from_kid, nonce, ciphertext, task_id)):
            return
        my_hmac_key = kids_by_id.get(to_kid)
        if my_hmac_key is None:
            return  # not one of ours (should not happen — relay routes by registration)

        import asyncio as _asyncio

        def _decrypt() -> Any:
            return json.loads(_ft.decrypt_from_relay(my_hmac_key, nonce, ciphertext).decode("utf-8"))

        try:
            # A max-size frame is MBs of hex: decrypt+parse it off the loop so
            # it cannot stall the other in-flight deliveries.
            payload = (await _asyncio.to_thread(_decrypt)
                       if len(ciphertext) > 256 * 1024 else _decrypt())
        except (_ft.RelayDecryptError, ValueError, UnicodeDecodeError):
            return  # tampered/corrupt — silently drop, exactly like a bad HMAC on the direct path
        except Exception:  # noqa: BLE001 — never raise out of a delivery task
            return
        if not isinstance(payload, dict):
            return

        _my_instance = self._my_instance_id() or None

        # Adversarial review round 2 (2026-07-29): two fixes, unchanged below.
        # (1) RemoteTriggerReceiver.receive() is SYNC and does signature verify +
        #     nonce-store DB I/O — run it off the event loop so one delivery can't
        #     stall the whole console process's asyncio loop (this listener runs as
        #     a lifespan background task there).
        # (2) Wrap receive/encrypt/send so ONE malformed or hostile delivery drops
        #     just that message instead of raising out of the `async for` — an
        #     unhandled raise there tears down the WebSocket and forces a reconnect,
        #     so a peer replaying bad deliveries could keep us in a reconnect storm
        #     and offline. A dead socket is still noticed by the next `ws.recv()`.
        try:
            if "ping_id" in payload:
                # Ping request (ADR-0199). No signed sender_instance_id slot
                # exists in ping_request's HMAC-covered canonical — adding
                # one would break the direct-HTTP path's backward
                # compatibility with older peers (the exact ADR-0198
                # precedent for additive signed fields). The relay SENDER
                # (remote_trigger_sender._relay_ping) instead stamps
                # `_relay_sender_instance_id` into the plaintext AFTER
                # signing — outside the signed contract, relay-transport-only,
                # silently ignored by process_ping_request's own canonical
                # reconstruction. Same A1 self-delivery protection as the
                # task-envelope path below, applied via that field instead.
                if _my_instance and payload.get("_relay_sender_instance_id") == _my_instance:
                    return
                from a2a_http_server import process_ping_request as _ppr  # noqa: PLC0415
                _status, response_dict = await _asyncio.to_thread(_ppr, payload, self._receiver)
            elif "peer_url" in payload and "kid" in payload and "task_id" not in payload:
                # Friendship-ack request (ADR-0257) — same self-delivery
                # reasoning as the ping branch above (ack requests carry no
                # sender_instance_id slot either).
                if _my_instance and payload.get("_relay_sender_instance_id") == _my_instance:
                    return
                if self._pending_dir is None or self._endpoints_dir is None:
                    return  # ack dispatch not configured on this listener — inert
                from a2a_friendship import process_friendship_ack_request as _pfar  # noqa: PLC0415
                # Off the event loop (2026-09-24): the ack core pings the
                # redeemer back (up to 5 s, blocking) and that ping's relay
                # fallback calls asyncio.run() — which raises inside a running
                # loop, so a relay-only redeemer could never be reported
                # reachable, and the blocking ping stalled every other delivery.
                # Own small control executor (bounded by its 4 threads) and NOT
                # the heavy-slot semaphore: a pairing ack must never wait
                # behind task envelopes' worker runs (round 3).
                _status, response_dict = await _run_control(
                    _pfar, payload, pending_dir=Path(self._pending_dir),
                    origins_dir=Path(self._origins_dir),
                    endpoints_dir=Path(self._endpoints_dir),
                )
            else:
                # Task envelope — unchanged trust path. A1 self-delivery guard
                # (2026-07-30 relay redesign): the pairing kid is SHARED and
                # identical on both peers, and derive_relay_auth_key(hmac_key)
                # is identical too — so if both peers connect to the same
                # relay, whoever registered the slot last receives BOTH
                # directions, and one side could be handed its OWN outbound
                # task back (same kid, same keys, verifies clean) and execute
                # it as if it came from the peer. Refuse any envelope whose
                # HMAC-covered sender_instance_id is our own local UUID: a
                # task we sent can never be a task we should run. Since the
                # 2026-09-24 relay fan-out both peers' listeners receive every
                # delivery for the shared kid, so this guard is what makes
                # exactly the peer answer.
                if _my_instance and payload.get("sender_instance_id") == _my_instance:
                    return  # our own task, routed back to us — never execute it
                async with self._heavy_slot():
                    response = await _run_work(self._receiver.receive, payload)
                response_dict = response.to_dict()

            resp_plain = json.dumps(response_dict).encode("utf-8")
            resp_nonce, resp_ct = await _asyncio.to_thread(
                _ft.encrypt_for_relay, my_hmac_key, resp_plain)
            frame = json.dumps({
                "type": "deliver", "to_kid": from_kid, "from_kid": to_kid,
                "nonce": resp_nonce, "ciphertext": resp_ct, "task_id": task_id,
            })
            if len(frame) > _MAX_MESSAGE_BYTES:
                import logging as _logging  # noqa: PLC0415
                _logging.getLogger("corvin.a2a.relay-listener").warning(
                    "relay listener: response for one delivery exceeds the relay "
                    "frame limit (%d bytes) — not sent; the sender times out", len(frame))
                return
            # Answer on the CURRENT connection: a long delivery may outlive the
            # socket it arrived on (reconnect), and the response is routed by
            # the sender's reply kid, not by which of our sockets carries it.
            target = self._ws if self._ws is not None else ws
            await target.send(frame)
        except Exception:  # noqa: BLE001 — one bad delivery must not drop the socket
            return

    def _my_instance_id(self) -> str:
        return self._instance_id or _local_instance_id()

    def _heavy_slot(self) -> Any:
        """Semaphore bounding concurrent heavy dispatches (task envelopes,
        friendship acks). Created lazily so it binds to the running loop."""
        import asyncio as _asyncio  # noqa: PLC0415
        if self._dispatch_sem is None:
            self._dispatch_sem = _asyncio.Semaphore(_MAX_CONCURRENT_DELIVERIES)
        return self._dispatch_sem


def _local_instance_id() -> str:
    try:
        from instance_identity import get_instance_id as _gid  # noqa: PLC0415
        return _gid() or ""
    except Exception:  # noqa: BLE001
        return ""


def build_relay_app() -> FastAPI:
    """Standalone relay app — ``uvicorn a2a_relay:build_relay_app --factory``
    or ``python -m a2a_relay``."""
    app = FastAPI(title="CorvinOS A2A Relay", docs_url=None, redoc_url=None)
    state = RelayState()
    app.include_router(build_relay_router(state))

    @app.get("/healthz")
    def _healthz() -> dict[str, Any]:
        return {"ok": True, "kids_registered": len(state._slots)}

    @app.get("/metrics")
    def _metrics() -> Any:
        # Prometheus text for THIS relay process (registrations, deliveries by
        # outcome, active connections) — fed by RelayState since 2026-09-25.
        from fastapi.responses import Response  # noqa: PLC0415
        m = state.metrics()
        body = (m.generate_metrics_text() if m is not None
                else b"# A2A relay metrics unavailable\n")
        return Response(content=body, media_type="text/plain; version=0.0.4; charset=utf-8")

    return app


def main(argv: list[str] | None = None) -> int:
    import argparse
    import uvicorn  # noqa: PLC0415

    parser = argparse.ArgumentParser(prog="a2a_relay", description=__doc__)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8090)
    args = parser.parse_args(argv)

    print(f"[a2a_relay] WARNING: this process routes A2A traffic for peers that "
          f"cannot reach each other directly. It cannot read message content "
          f"(AEAD-encrypted end-to-end), but it CAN see routing metadata "
          f"(which kid talks to which, timing, volume). Only point paired "
          f"instances at a relay you operate or trust.", flush=True)
    # ws_max_size above the relay's own frame cap, so an oversize frame gets a
    # correlated "message_too_large" error frame instead of a bare close 1009.
    uvicorn.run(build_relay_app(), host=args.host, port=args.port,
                ws_max_size=_WS_CLIENT_MAX_SIZE)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
