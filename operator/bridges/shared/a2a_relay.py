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

import json
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
_MAX_MESSAGE_BYTES = 512 * 1024  # ciphertext + envelope overhead; generous
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


@dataclass
class _KidSlot:
    """One routing slot: the pinned auth credential, the live connection (if
    any), and a bounded queue for delivery while the owner is offline."""
    auth_key: str
    connection_id: str | None = None
    queue: deque[_QueuedMessage] = field(default_factory=lambda: deque(maxlen=_MAX_QUEUE_PER_KID))
    # monotonic timestamp of the last register/deliver touching this slot —
    # drives the idle-slot reaper (A2/A3).
    last_active: float = field(default_factory=time.monotonic)


class RelayState:
    """In-memory routing table. One instance per relay process — deliberately
    NOT persisted (see module docstring: a relay holds no durable state)."""

    def __init__(self) -> None:
        self._slots: dict[str, _KidSlot] = {}
        # connection_id -> {WebSocket, set of kids registered on it}
        self._connections: dict[str, tuple[WebSocket, set[str]]] = {}
        # running total of bytes held across every slot's queue — enforces the
        # global ceiling so bounded slots × 512 KB payloads cannot exhaust RAM.
        self._queued_bytes = 0

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
            if slot.connection_id is not None or slot.queue:
                continue
            # 3. evict an offline, drained slot once it is idle past its TTL.
            ttl = _REPLY_KID_IDLE_TTL_S if _is_reply_kid(kid) else _SLOT_IDLE_TTL_S
            if now - slot.last_active > ttl:
                del self._slots[kid]

    # ── registration ────────────────────────────────────────────────

    def register(self, connection_id: str, kid: str, auth_key: str) -> str | None:
        """Claim (or reclaim) the routing slot for ``kid`` on this connection.

        Returns None on success, or a fixed rejection-reason string:
          - "auth_key_mismatch" — a DIFFERENT credential was already pinned
            for this kid (TOFU pin conflict — the connecting client does not
            hold the same shared secret as whoever registered first).
          - "too_many_kids" — this connection already registered the max.
          - "relay_at_capacity" — the process-wide slot table is full (see
            _MAX_TOTAL_SLOTS); only applies to a BRAND NEW kid, never to one
            already tracked (re-registering/reconnecting always succeeds).
        """
        self._prune()  # reclaim dead slots before deciding we're at capacity
        slot = self._slots.get(kid)
        if slot is None:
            if len(self._slots) >= _MAX_TOTAL_SLOTS:
                return "relay_at_capacity"
            self._slots[kid] = _KidSlot(auth_key=auth_key, connection_id=connection_id)
            return None
        if slot.auth_key != auth_key:
            return "auth_key_mismatch"
        slot.connection_id = connection_id
        slot.last_active = time.monotonic()
        return None

    def flush_queue(self, kid: str) -> list[dict[str, Any]]:
        """Pop and return every non-expired queued message for ``kid``, in
        delivery order. Called right after a successful registration."""
        slot = self._slots.get(kid)
        if slot is None:
            return []
        now = time.monotonic()
        out: list[dict[str, Any]] = []
        while slot.queue:
            item = slot.queue.popleft()
            self._queued_bytes -= item.nbytes
            if item.expires_at >= now:
                out.append(item.payload)
        return out

    # ── delivery ────────────────────────────────────────────────────

    async def deliver(self, to_kid: str, payload: dict[str, Any]) -> str:
        """Forward ``payload`` to ``to_kid``'s live connection, or queue it.

        Returns "delivered", "queued", or "dropped" (queue full / unknown
        kid with no prior registration at all — nothing to queue against).
        """
        self._prune()  # keep expired items from counting against the byte budget
        slot = self._slots.get(to_kid)
        if slot is None:
            # No one has EVER registered this kid on this relay — queuing
            # would grow unbounded for kids that will never claim it.
            return "dropped"
        slot.last_active = time.monotonic()
        if slot.connection_id is not None:
            conn = self._connections.get(slot.connection_id)
            if conn is not None:
                ws, _kids = conn
                try:
                    await ws.send_text(json.dumps(payload))
                    return "delivered"
                except Exception:  # noqa: BLE001 — fall through to queue
                    pass
        if len(slot.queue) >= _MAX_QUEUE_PER_KID:
            return "dropped"
        # Global byte-budget: bounded slot COUNT is not enough on its own — a
        # cap of slots × 512 KB queued payloads would still be ~5 GB. Refuse to
        # queue once the process-wide ceiling is reached (A2).
        nbytes = len(json.dumps(payload))
        if self._queued_bytes + nbytes > _MAX_TOTAL_QUEUE_BYTES:
            return "dropped"
        slot.queue.append(_QueuedMessage(
            payload=payload, expires_at=time.monotonic() + _QUEUE_TTL_S, nbytes=nbytes))
        self._queued_bytes += nbytes
        return "queued"

    # ── connection lifecycle ────────────────────────────────────────

    def open_connection(self, ws: WebSocket) -> str:
        connection_id = uuid.uuid4().hex
        self._connections[connection_id] = (ws, set())
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
        _ws, kids = conn
        now = time.monotonic()
        for kid in kids:
            slot = self._slots.get(kid)
            if slot is not None and slot.connection_id == connection_id:
                # Un-claim the LIVE slot, but keep the pinned auth_key and
                # any already-queued messages — a reconnect with the same
                # credential resumes exactly where it left off.
                slot.connection_id = None
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
    if len(to_kid) > 128 or len(from_kid) > 128 or len(task_id) > 128:
        return None
    if len(ciphertext) > _MAX_MESSAGE_BYTES * 2:  # hex doubles byte length
        return None
    return {
        "type": "deliver", "to_kid": to_kid, "from_kid": from_kid,
        "nonce": nonce, "ciphertext": ciphertext, "task_id": task_id,
    }


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
                    await websocket.send_text(json.dumps(
                        {"type": "error", "reason": "message_too_large"}))
                    continue
                try:
                    msg = json.loads(raw)
                except (ValueError, TypeError):
                    await websocket.send_text(json.dumps(
                        {"type": "error", "reason": "invalid_json"}))
                    continue
                if not isinstance(msg, dict):
                    await websocket.send_text(json.dumps(
                        {"type": "error", "reason": "envelope_not_object"}))
                    continue

                msg_type = msg.get("type")
                if msg_type == "register":
                    if state.registered_kid_count(connection_id) >= _MAX_KIDS_PER_CONNECTION:
                        await websocket.send_text(json.dumps(
                            {"type": "register_rejected", "reason": "too_many_kids"}))
                        continue
                    parsed = _validate_register(msg)
                    if parsed is None:
                        await websocket.send_text(json.dumps(
                            {"type": "error", "reason": "invalid_register"}))
                        continue
                    kid, auth_key = parsed
                    rejection = state.register(connection_id, kid, auth_key)
                    if rejection is not None:
                        await websocket.send_text(json.dumps(
                            {"type": "register_rejected", "kid": kid, "reason": rejection}))
                        continue
                    state.note_registered_kid(connection_id, kid)
                    await websocket.send_text(json.dumps({"type": "registered", "kid": kid}))
                    for queued in state.flush_queue(kid):
                        await websocket.send_text(json.dumps(queued))

                elif msg_type == "deliver":
                    parsed_deliver = _validate_deliver(msg)
                    if parsed_deliver is None:
                        await websocket.send_text(json.dumps(
                            {"type": "error", "reason": "invalid_deliver"}))
                        continue
                    # to_kid stays IN the forwarded payload (unlike an
                    # earlier draft that stripped it) — a listener that
                    # registered multiple kids on one connection needs it to
                    # know which of ITS kids an inbound "deliver" is for.
                    to_kid = parsed_deliver["to_kid"]
                    outcome = await state.deliver(to_kid, parsed_deliver)
                    await websocket.send_text(json.dumps(
                        {"type": "deliver_ack", "task_id": parsed_deliver["task_id"], "outcome": outcome}))

                else:
                    await websocket.send_text(json.dumps(
                        {"type": "error", "reason": "unknown_message_type"}))
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
    consistent error shape regardless of which transport was attempted."""


async def relay_deliver_and_wait(
    *, relay_url: str, my_kid: str, my_relay_auth_key: str,
    to_kid: str, nonce_hex: str, ciphertext_hex: str, task_id: str,
    timeout_s: float,
) -> dict[str, str]:
    """Sender-side: deliver one already-encrypted envelope via the relay and
    wait for the correlating encrypted response.

    Returns ``{"nonce": ..., "ciphertext": ...}`` for the response payload.
    Raises :class:`RelayTransportError` on any failure — connection error,
    registration rejected (e.g. our OWN kid's auth_key mismatches what is
    already pinned — should not happen for a legitimate pairing, but a
    misconfigured/reused relay could hit this), delivery outcome "dropped",
    or timeout waiting for the response.
    """
    import asyncio as _asyncio
    import websockets  # noqa: PLC0415

    try:
        async with websockets.connect(relay_url, open_timeout=timeout_s) as ws:
            await ws.send(json.dumps(
                {"type": "register", "kid": my_kid, "relay_auth_key": my_relay_auth_key}))
            reg_resp = json.loads(await _asyncio.wait_for(ws.recv(), timeout=timeout_s))
            if reg_resp.get("type") != "registered":
                raise RelayTransportError(f"registration_failed:{reg_resp.get('reason', 'unknown')}")

            await ws.send(json.dumps({
                "type": "deliver", "to_kid": to_kid, "from_kid": my_kid,
                "nonce": nonce_hex, "ciphertext": ciphertext_hex, "task_id": task_id,
            }))

            _loop = _asyncio.get_running_loop()  # get_event_loop() is deprecated inside a coroutine
            deadline = _loop.time() + timeout_s
            while True:
                remaining = deadline - _loop.time()
                if remaining <= 0:
                    raise RelayTransportError("response_timeout")
                raw = await _asyncio.wait_for(ws.recv(), timeout=remaining)
                msg = json.loads(raw)
                if msg.get("type") == "deliver_ack" and msg.get("task_id") == task_id:
                    if msg.get("outcome") == "dropped":
                        raise RelayTransportError("delivery_dropped")
                    continue  # "delivered" or "queued" — keep waiting for the actual response
                if (msg.get("type") == "deliver" and msg.get("task_id") == task_id
                        and msg.get("to_kid") == my_kid and msg.get("from_kid") == to_kid):
                    r_nonce, r_ct = msg.get("nonce"), msg.get("ciphertext")
                    if not (isinstance(r_nonce, str) and isinstance(r_ct, str)):
                        raise RelayTransportError("malformed_response")
                    return {"nonce": r_nonce, "ciphertext": r_ct}
                # Anything else (a stale/unrelated message) — ignore and keep waiting.
    except RelayTransportError:
        raise
    except _asyncio.TimeoutError as exc:
        raise RelayTransportError("response_timeout") from exc
    except Exception as exc:  # noqa: BLE001 — connection refused, DNS, etc.
        raise RelayTransportError(f"connect_failed:{type(exc).__name__}") from exc


# ── Receiver-side client: persistent listener ───────────────────────────

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
    ) -> None:
        self._relay_url = relay_url
        self._receiver = receiver
        self._origins_dir = origins_dir
        self._pending_dir = pending_dir
        self._endpoints_dir = endpoints_dir
        self._stop = False

    def stop(self) -> None:
        self._stop = True

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
                if not cfg.get("enabled"):
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
            except Exception:  # noqa: BLE001 — reconnect, never crash the caller
                pass
            if self._stop:
                return
            import asyncio as _asyncio
            await _asyncio.sleep(reconnect_backoff_s)

    async def _connect_and_serve(self) -> None:
        import websockets  # noqa: PLC0415
        import a2a_friendship as _ft  # noqa: PLC0415

        kids = self._registrable_kids()
        if not kids:
            return  # nothing to listen for yet — try again next backoff cycle

        import logging as _logging  # noqa: PLC0415
        _log = _logging.getLogger("corvin.a2a.relay-listener")

        async with websockets.connect(self._relay_url) as ws:
            for kid, hmac_key in kids:
                auth_key = _ft.derive_relay_auth_key(hmac_key)
                await ws.send(json.dumps({"type": "register", "kid": kid, "relay_auth_key": auth_key}))
            kids_by_id = dict(kids)
            _registered: set[str] = set()
            async for raw in ws:
                if self._stop:
                    return
                try:
                    msg = json.loads(raw)
                except (ValueError, TypeError):
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
                    continue
                if mtype == "register_rejected":
                    _log.warning(
                        "relay rejected registration for kid=%s reason=%s — this "
                        "peer is UNREACHABLE via the relay until resolved",
                        str(msg.get("kid"))[:16], msg.get("reason"),
                    )
                    continue
                if mtype != "deliver":
                    continue
                await self._handle_deliver(ws, msg, kids_by_id)

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

        try:
            plaintext = _ft.decrypt_from_relay(my_hmac_key, nonce, ciphertext)
            payload = json.loads(plaintext.decode("utf-8"))
        except (_ft.RelayDecryptError, ValueError, UnicodeDecodeError):
            return  # tampered/corrupt — silently drop, exactly like a bad HMAC on the direct path

        try:
            from instance_identity import get_instance_id as _gid  # noqa: PLC0415
            _my_instance = _gid()
        except Exception:  # noqa: BLE001
            _my_instance = None

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
        import asyncio as _asyncio
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
                _status, response_dict = _ppr(payload, self._receiver)
            elif "peer_url" in payload and "kid" in payload and "task_id" not in payload:
                # Friendship-ack request (ADR-0257) — same self-delivery
                # reasoning as the ping branch above (ack requests carry no
                # sender_instance_id slot either).
                if _my_instance and payload.get("_relay_sender_instance_id") == _my_instance:
                    return
                if self._pending_dir is None or self._endpoints_dir is None:
                    return  # ack dispatch not configured on this listener — inert
                from a2a_friendship import process_friendship_ack_request as _pfar  # noqa: PLC0415
                _status, response_dict = _pfar(
                    payload, pending_dir=Path(self._pending_dir),
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
                # task we sent can never be a task we should run. (The
                # remaining routing ambiguity when both peers share a relay
                # degrades to a send-side timeout+retry, not a wrong
                # execution — a wire-level instance-scoped routing key is the
                # follow-up.)
                if _my_instance and payload.get("sender_instance_id") == _my_instance:
                    return  # our own task, routed back to us — never execute it
                response = await _asyncio.to_thread(self._receiver.receive, payload)
                response_dict = response.to_dict()

            resp_nonce, resp_ct = _ft.encrypt_for_relay(
                my_hmac_key, json.dumps(response_dict).encode("utf-8"))
            await ws.send(json.dumps({
                "type": "deliver", "to_kid": from_kid, "from_kid": to_kid,
                "nonce": resp_nonce, "ciphertext": resp_ct, "task_id": task_id,
            }))
        except Exception:  # noqa: BLE001 — one bad delivery must not drop the socket
            return


def build_relay_app() -> FastAPI:
    """Standalone relay app — ``uvicorn a2a_relay:build_relay_app --factory``
    or ``python -m a2a_relay``."""
    app = FastAPI(title="CorvinOS A2A Relay", docs_url=None, redoc_url=None)
    state = RelayState()
    app.include_router(build_relay_router(state))

    @app.get("/healthz")
    def _healthz() -> dict[str, Any]:
        return {"ok": True, "kids_registered": len(state._slots)}

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
    uvicorn.run(build_relay_app(), host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
