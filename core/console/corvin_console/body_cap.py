"""Request-body cap shared by both console hosts (standalone and the gateway mount).

The cap is enforced on Content-Length BEFORE Starlette parses (and spools to disk) a multipart
body, and counted as it arrives for a chunked body. One mechanism: the standalone host applies it
to every route, the gateway host to the paths that legitimately take an upload.
"""
from __future__ import annotations

from typing import Awaitable, Callable, Optional

from fastapi import Request
from fastapi.responses import JSONResponse

MIB = 1024 * 1024

# Video Producer styles: the import route allows a 25 MiB deck (+ multipart framing);
# the JSON routes carry two base64 PNGs and are capped at 8 MiB by their own reader.
STYLE_BODY_CAPS = {
    "/v1/console/video/styles/import": 25 * MIB + 256 * 1024,
    "/v1/console/video/styles": 8 * MIB,
    "/v1/console/video/styles/preview": 8 * MIB,
}


BODY_CAP_DEFAULT = 2 * MIB  # far above any JSON body the SPA sends
BODY_CAPS = {
    "/v1/console/voice/transcribe": 25 * MIB,
    "/v1/console/files/upload": 512 * MIB,
    "/v1/console/license/upload": 8 * MIB,
    "/v1/console/packages/upload": 256 * MIB,
    "/v1/console/workflows/import": 64 * MIB,
    **STYLE_BODY_CAPS,
}
BODY_CAP_PREFIXES = {
    # POST /chat/sessions/{sid}/attachments
    "/v1/console/chat/sessions/": 512 * MIB,
}


def standalone_cap_for(path: str) -> int:
    """Standalone host: every route is capped; unlisted ones get BODY_CAP_DEFAULT."""
    cap = BODY_CAPS.get(path)
    if cap is not None:
        return cap
    for prefix, pcap in BODY_CAP_PREFIXES.items():
        if path.startswith(prefix) and path.endswith("/attachments"):
            return pcap
    return BODY_CAP_DEFAULT


class BodyTooLarge(Exception):
    """Raised by the streaming body guard when a chunked body exceeds its cap."""

    def __init__(self, cap: int) -> None:
        super().__init__(f"request body exceeds {cap} bytes")
        self.cap = cap


def gateway_cap_for(path: str) -> Optional[int]:
    """Gateway host: only the upload paths named here are capped (everything else is untouched)."""
    return STYLE_BODY_CAPS.get(path)


def make_body_cap_middleware(cap_for: Callable[[str], Optional[int]]):
    """An ``@app.middleware("http")`` function; ``cap_for`` returns None to leave a path alone."""

    async def _cap_request_body(request: Request, call_next: Callable[[Request], Awaitable]):
        cap = cap_for(request.url.path)
        if cap is None:
            return await call_next(request)
        raw_len = request.headers.get("content-length")
        if raw_len:
            try:
                if int(raw_len) > cap:
                    return JSONResponse(status_code=413, content={"detail": f"request body exceeds {cap} bytes"})
            except ValueError:
                pass  # unparseable - the streaming guard below still applies
        # A body sent without Content-Length (chunked) is counted as it arrives and cut off at the same budget.
        received = 0
        original_receive = request.receive

        async def _limited_receive():  # noqa: ANN202
            nonlocal received
            message = await original_receive()
            if message.get("type") == "http.request":
                received += len(message.get("body", b""))
                if received > cap:
                    raise BodyTooLarge(cap)
            return message

        request._receive = _limited_receive  # noqa: SLF001 - Starlette's own hook
        try:
            response = await call_next(request)
        except BodyTooLarge as exc:
            return JSONResponse(status_code=413, content={"detail": f"request body exceeds {exc.cap} bytes"})
        if received > cap:
            # FastAPI turns ANY error while reading a body into a 400; the byte count is the truth.
            return JSONResponse(status_code=413, content={"detail": f"request body exceeds {cap} bytes"})
        return response

    return _cap_request_body
