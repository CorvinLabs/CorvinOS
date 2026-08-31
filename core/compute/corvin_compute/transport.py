"""Length-prefixed JSON framing over a stream (ADR-0013 Phase 13.4).

Wire format: ``<4-byte big-endian length><JSON UTF-8 bytes>``.

Frame size cap: 4 MiB (mirrors the Forge stdout cap). Larger payloads
indicate misuse — the operator should keep run-submission small (the
param_grid spec, not the data itself).
"""
from __future__ import annotations

import asyncio
import json
import socket
import struct

# 4 MiB hard cap. Submission payloads (param_grid spec) should never
# come close; abnormally large frames are rejected fail-closed.
MAX_FRAME_BYTES = 4 * 1024 * 1024


class TransportError(IOError):
    """Raised on framing / length-cap / partial-read failures."""


# ----- sync (blocking) -----------------------------------------------------

def send_frame_sync(sock: socket.socket, payload: dict) -> None:
    body = json.dumps(payload).encode("utf-8")
    if len(body) > MAX_FRAME_BYTES:
        raise TransportError(f"frame too large: {len(body)} > {MAX_FRAME_BYTES}")
    sock.sendall(struct.pack(">I", len(body)) + body)


def recv_frame_sync(sock: socket.socket) -> dict:
    header = _recv_exact_sync(sock, 4)
    (length,) = struct.unpack(">I", header)
    if length > MAX_FRAME_BYTES:
        raise TransportError(f"declared frame too large: {length}")
    body = _recv_exact_sync(sock, length)
    return json.loads(body.decode("utf-8"))


def _recv_exact_sync(sock: socket.socket, n: int) -> bytes:
    chunks: list[bytes] = []
    remaining = n
    while remaining > 0:
        chunk = sock.recv(remaining)
        if not chunk:
            raise TransportError("connection closed mid-frame")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


# ----- async (asyncio) -----------------------------------------------------

async def send_frame(writer: asyncio.StreamWriter, payload: dict) -> None:
    body = json.dumps(payload).encode("utf-8")
    if len(body) > MAX_FRAME_BYTES:
        raise TransportError(f"frame too large: {len(body)} > {MAX_FRAME_BYTES}")
    writer.write(struct.pack(">I", len(body)) + body)
    await writer.drain()


async def recv_frame(reader: asyncio.StreamReader) -> dict | None:
    """Read one frame; return ``None`` on clean EOF."""
    try:
        header = await reader.readexactly(4)
    except asyncio.IncompleteReadError as exc:
        if not exc.partial:
            return None
        raise TransportError("incomplete header") from exc
    (length,) = struct.unpack(">I", header)
    if length > MAX_FRAME_BYTES:
        raise TransportError(f"declared frame too large: {length}")
    body = await reader.readexactly(length)
    return json.loads(body.decode("utf-8"))
