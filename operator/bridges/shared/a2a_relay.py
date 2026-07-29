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
from typing import Any

from fastapi import APIRouter, FastAPI, WebSocket, WebSocketDisconnect

# ── Tunables ─────────────────────────────────────────────────────────────

_MAX_QUEUE_PER_KID = 32          # bounded — a liveness bridge, not a mailbox
_QUEUE_TTL_S = 300.0             # 5 min — long enough for a brief reconnect
_MAX_MESSAGE_BYTES = 512 * 1024  # ciphertext + envelope overhead; generous
_MAX_KIDS_PER_CONNECTION = 64    # one operator process may pair with many peers


class RelayError(Exception):
    """Base for relay protocol errors — the reason is never sent verbatim
    to the client beyond a closed, fixed set of reason strings (mirrors the
    ADR-0197 closed-template discipline used elsewhere in the A2A stack)."""


@dataclass
class _QueuedMessage:
    payload: dict[str, Any]
    expires_at: float


@dataclass
class _KidSlot:
    """One routing slot: the pinned auth credential, the live connection (if
    any), and a bounded queue for delivery while the owner is offline."""
    auth_key: str
    connection_id: str | None = None
    queue: deque[_QueuedMessage] = field(default_factory=lambda: deque(maxlen=_MAX_QUEUE_PER_KID))


class RelayState:
    """In-memory routing table. One instance per relay process — deliberately
    NOT persisted (see module docstring: a relay holds no durable state)."""

    def __init__(self) -> None:
        self._slots: dict[str, _KidSlot] = {}
        # connection_id -> {WebSocket, set of kids registered on it}
        self._connections: dict[str, tuple[WebSocket, set[str]]] = {}

    # ── registration ────────────────────────────────────────────────

    def register(self, connection_id: str, kid: str, auth_key: str) -> str | None:
        """Claim (or reclaim) the routing slot for ``kid`` on this connection.

        Returns None on success, or a fixed rejection-reason string:
          - "auth_key_mismatch" — a DIFFERENT credential was already pinned
            for this kid (TOFU pin conflict — the connecting client does not
            hold the same shared secret as whoever registered first).
          - "too_many_kids" — this connection already registered the max.
        """
        slot = self._slots.get(kid)
        if slot is None:
            self._slots[kid] = _KidSlot(auth_key=auth_key, connection_id=connection_id)
            return None
        if slot.auth_key != auth_key:
            return "auth_key_mismatch"
        slot.connection_id = connection_id
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
            if item.expires_at >= now:
                out.append(item.payload)
        return out

    # ── delivery ────────────────────────────────────────────────────

    async def deliver(self, to_kid: str, payload: dict[str, Any]) -> str:
        """Forward ``payload`` to ``to_kid``'s live connection, or queue it.

        Returns "delivered", "queued", or "dropped" (queue full / unknown
        kid with no prior registration at all — nothing to queue against).
        """
        slot = self._slots.get(to_kid)
        if slot is None:
            # No one has EVER registered this kid on this relay — queuing
            # would grow unbounded for kids that will never claim it.
            return "dropped"
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
        slot.queue.append(_QueuedMessage(payload=payload, expires_at=time.monotonic() + _QUEUE_TTL_S))
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
        for kid in kids:
            slot = self._slots.get(kid)
            if slot is not None and slot.connection_id == connection_id:
                # Un-claim the LIVE slot, but keep the pinned auth_key and
                # any already-queued messages — a reconnect with the same
                # credential resumes exactly where it left off.
                slot.connection_id = None


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

            deadline = _asyncio.get_event_loop().time() + timeout_s
            while True:
                remaining = deadline - _asyncio.get_event_loop().time()
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
                    return {"nonce": msg["nonce"], "ciphertext": msg["ciphertext"]}
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
    listens for inbound "deliver" messages, decrypts+dispatches each
    through the SAME ``RemoteTriggerReceiver.receive()`` pipeline a direct
    HTTP POST to /v1/a2a/receive would use, and relays the signed response
    back. Reconnects with backoff on any drop — this is a best-effort
    liveness bridge, not a guaranteed-delivery channel; a peer that only
    reaches us via the relay simply retries at the send() layer like any
    other transient failure.

    Intended lifecycle: one instance constructed and run as a background
    asyncio task from an app's lifespan (see corvin_console.standalone),
    started only when a2a_relay_fallback is enabled AND a relay URL is
    configured — inert (never constructed) otherwise.
    """

    def __init__(self, *, relay_url: str, receiver: Any, origins_dir: "Any") -> None:
        self._relay_url = relay_url
        self._receiver = receiver
        self._origins_dir = origins_dir
        self._stop = False

    def stop(self) -> None:
        self._stop = True

    def _registrable_kids(self) -> list[tuple[str, str]]:
        """(kid, hmac_key) for every ``_friendship`` origin record — the
        hmac_key is what enc_key/relay_auth_key are derived from (see
        a2a_friendship._derive_enc_key)."""
        import json as _json
        from pathlib import Path as _Path

        out: list[tuple[str, str]] = []
        d = _Path(self._origins_dir)
        if not d.exists():
            return out
        for p in sorted(d.glob("*.json")):
            try:
                cfg = _json.loads(p.read_text("utf-8"))
            except (OSError, ValueError):
                continue
            if not cfg.get("_friendship") or not cfg.get("enabled"):
                continue
            hmac_key = cfg.get("hmac_key")
            if isinstance(hmac_key, str) and len(hmac_key) == 64:
                out.append((p.stem, hmac_key))
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

        async with websockets.connect(self._relay_url) as ws:
            for kid, hmac_key in kids:
                auth_key = _ft.derive_relay_auth_key(hmac_key)
                await ws.send(json.dumps({"type": "register", "kid": kid, "relay_auth_key": auth_key}))
            kids_by_id = dict(kids)
            async for raw in ws:
                if self._stop:
                    return
                try:
                    msg = json.loads(raw)
                except (ValueError, TypeError):
                    continue
                if msg.get("type") != "deliver":
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
            envelope = json.loads(plaintext.decode("utf-8"))
        except (_ft.RelayDecryptError, ValueError, UnicodeDecodeError):
            return  # tampered/corrupt — silently drop, exactly like a bad HMAC on the direct path

        response = self._receiver.receive(envelope)
        response_dict = response.to_dict()
        resp_nonce, resp_ct = _ft.encrypt_for_relay(
            my_hmac_key, json.dumps(response_dict).encode("utf-8"))
        await ws.send(json.dumps({
            "type": "deliver", "to_kid": from_kid, "from_kid": to_kid,
            "nonce": resp_nonce, "ciphertext": resp_ct, "task_id": task_id,
        }))


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
