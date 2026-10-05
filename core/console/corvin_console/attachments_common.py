"""Shared file-attachment upload helper for server-stored chat surfaces.

Session chat (``routes/chat.py``) and group chat (``routes/chat_groups.py``)
both let an operator attach local files to a message: the browser POSTs
multipart files, the server stores them under a per-conversation
``attachments/`` directory, and the frontend embeds the returned relative
paths as a text line in the next message (see ``pages/chat.tsx``'s
``sendUser`` for the session-chat pattern that group chat mirrors) — the
engine (or, for a human-only group, another participant) reads the file
from disk via that path.

Peer/A2A chat does NOT use this helper. An A2A message is a binary payload
inside an HMAC-signed envelope sent to a REMOTE instance, not a local file
reference — its attachment limits
(``corvin_operator/bridges/shared/a2a_attachments.py``,
``MAX_ATTACHMENTS_TOTAL_BYTES`` = 1 MiB, ``MAX_ATTACHMENTS_COUNT`` = 16) are a
DoS-prevention cap on the signed binary payload itself ("reject before HMAC
verification: an attacker who could send arbitrary-sized envelopes could DoS
receivers regardless of signature validity" — see that module's docstring),
not a UI convenience limit. Raising that cap to 50 MB to match this module
would let a single chat message DoS a receiving peer before signature
verification even runs, so peer-chat attachments deliberately keep their own,
much smaller, wire-protocol caps instead of adopting these.

Operator decision (2026-10-04): 50 MB per file, no extension/MIME whitelist.
The only thing that made an unrestricted file type unsafe to store was a
path-traversal filename, and that is blocked by ``safe_attach_name`` (it
strips path separators and unsafe characters) regardless of what kind of
file this is — not by rejecting a file type. Every stored file is served
back to the engine/operator as inert data (read from disk, never executed
by the console itself), so widening the type range carries no new risk.
"""
from __future__ import annotations

import errno
import mimetypes
import os
import re
import secrets
from pathlib import Path
from typing import Any

from fastapi import HTTPException, Request, UploadFile, status as http_status
from starlette.datastructures import UploadFile as StarletteUploadFile

# 50 MB per file (operator decision 2026-10-04, up from the prior 20 MB).
ATTACH_MAX_BYTES = 50 * 1024 * 1024
ATTACH_MAX_FILES = 10


def safe_attach_name(raw: str) -> str:
    """Return a sanitised attachment filename (ASCII-safe, no path separators).

    ``Path(raw).name`` strips any directory components (path traversal), and
    the regex below replaces anything that isn't a word character, dot,
    hyphen or space — this is the ONLY gate on what may be stored; there is
    no extension/MIME whitelist (operator decision above).
    """
    name = Path(raw).name
    clean = re.sub(r"[^\w.\- ]", "_", name).strip()
    return clean or "file"


# Multipart framing per part (boundary, headers) is small; this bounds the
# request before a single byte of it is parsed or spooled to disk.
_FORM_OVERHEAD_BYTES = 256 * 1024


async def read_upload_form(
    request: Request,
    *,
    max_bytes: int = ATTACH_MAX_BYTES,
    max_files: int = ATTACH_MAX_FILES,
    field: str = "files",
) -> tuple[list[UploadFile], Any]:
    """Parse an upload request only if its declared size can be valid.

    Declaring ``files: list[UploadFile]`` as a route parameter makes FastAPI
    parse (and spool to disk) the whole body — up to 1000 parts of any size —
    before the handler can check a single limit. Here the Content-Length is
    checked first (browsers always send it for FormData; a request without
    one is refused), then Starlette's parser runs with ``max_files``.
    Returns ``(files, form)``; the caller must ``await form.close()``.
    """
    raw_len = request.headers.get("content-length")
    try:
        declared = int(raw_len) if raw_len is not None else -1
    except ValueError:
        declared = -1
    if declared < 0:
        raise HTTPException(http_status.HTTP_411_LENGTH_REQUIRED, "Content-Length required")
    if declared > max_files * max_bytes + _FORM_OVERHEAD_BYTES:
        raise HTTPException(
            http_status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            f"Upload too large — max {max_files} files of {max_bytes // (1024 * 1024)} MB",
        )
    try:
        form = await request.form(max_files=max_files, max_fields=max_files + 10)
    except HTTPException:
        raise
    except Exception as exc:  # starlette raises its own HTTPException/MultiPartException
        raise HTTPException(http_status.HTTP_400_BAD_REQUEST, f"Invalid upload: {exc}") from exc
    files = [f for f in form.getlist(field) if isinstance(f, StarletteUploadFile)]
    if not files:
        await form.close()
        raise HTTPException(http_status.HTTP_400_BAD_REQUEST, "No files in upload")
    return files, form


def _open_exclusive(attach_dir: Path, safe_name: str) -> tuple[int, str]:
    """Create a NEW file in ``attach_dir`` and return ``(fd, name)``.

    ``O_EXCL`` never reuses a name (no overwrite, no race between two uploads
    of the same name) and ``O_NOFOLLOW`` refuses a planted symlink — the
    directory sits inside the worker's workdir, and the old exists()-then-
    write_bytes() followed a dangling link to wherever it pointed.
    """
    base, dot_ext = (safe_name.rsplit(".", 1) if "." in safe_name.lstrip(".")
                     else (safe_name, ""))
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    candidates = [safe_name] + [
        f"{base}_{i}.{dot_ext}" if dot_ext else f"{base}_{i}" for i in range(1, 100)
    ]
    for _ in range(5):
        tag = secrets.token_hex(4)
        candidates.append(f"{base}_{tag}.{dot_ext}" if dot_ext else f"{base}_{tag}")
    for name in candidates:
        try:
            return os.open(attach_dir / name, flags, 0o600), name
        except FileExistsError:
            continue
        except OSError as exc:
            if exc.errno == errno.ELOOP:  # a symlink sits at this name
                continue
            raise
    raise HTTPException(http_status.HTTP_409_CONFLICT, f"Could not store {safe_name!r}")


async def receive_uploaded_files(
    files: list[UploadFile],
    attach_dir: Path,
    *,
    max_bytes: int = ATTACH_MAX_BYTES,
    max_files: int = ATTACH_MAX_FILES,
    path_prefix: str = "attachments",
) -> list[dict[str, Any]]:
    """Validate, sanitise and store one or more uploaded files.

    Every file is checked before any is written, so a refused upload never
    leaves part of the batch behind. Returns ``{name, size, mime, path}``
    descriptors — ``path`` is relative to the directory ``attach_dir`` lives
    under (e.g. ``"attachments/report.csv"``).
    """
    if len(files) > max_files:
        raise HTTPException(
            http_status.HTTP_400_BAD_REQUEST,
            f"Too many files — max {max_files} per upload",
        )
    if attach_dir.is_symlink():
        raise HTTPException(http_status.HTTP_409_CONFLICT, "attachments directory is a symlink")
    payloads: list[tuple[UploadFile, bytes]] = []
    for upload in files:
        if upload.size is not None and upload.size > max_bytes:
            raise HTTPException(
                http_status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                f"File exceeds 50 MB limit: {upload.filename!r}",
            )
    for upload in files:
        data = await upload.read(max_bytes + 1)
        if len(data) > max_bytes:
            raise HTTPException(
                http_status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                f"File exceeds 50 MB limit: {upload.filename!r}",
            )
        payloads.append((upload, data))

    attach_dir.mkdir(parents=True, exist_ok=True)
    if attach_dir.is_symlink():
        raise HTTPException(http_status.HTTP_409_CONFLICT, "attachments directory is a symlink")
    results: list[dict[str, Any]] = []
    for upload, data in payloads:
        fd, safe_name = _open_exclusive(attach_dir, safe_attach_name(upload.filename or "file"))
        try:
            view = memoryview(data)
            while view:
                view = view[os.write(fd, view):]
        finally:
            os.close(fd)
        mime = upload.content_type or mimetypes.guess_type(safe_name)[0] or "application/octet-stream"
        results.append({
            "name": safe_name,
            "size": len(data),
            "mime": mime,
            "path": f"{path_prefix}/{safe_name}",
        })
    return results


# Served inline only when the browser cannot run script from it. HTML and SVG
# stay inline (the chat renders them in a sandboxed iframe / <img>) but carry
# a CSP sandbox; every other type — xhtml, xml/xsl, anything unknown — is a
# download. Uploads have no type whitelist, so this is the line that keeps an
# uploaded file from executing on the console origin.
_INLINE_SAFE_PREFIXES = ("image/", "audio/", "video/")
_INLINE_SAFE_TYPES = frozenset({
    "application/pdf", "text/plain", "text/csv", "text/markdown", "application/json",
})
_INLINE_SANDBOXED_TYPES = frozenset({"text/html", "image/svg+xml"})


def serve_headers(mime: str | None) -> tuple[str, dict[str, str]]:
    """``(content_disposition_type, headers)`` for serving a stored file."""
    headers = {"X-Content-Type-Options": "nosniff"}
    if mime in _INLINE_SANDBOXED_TYPES:
        headers["Content-Security-Policy"] = "sandbox"
        return "inline", headers
    if mime and (mime in _INLINE_SAFE_TYPES or mime.startswith(_INLINE_SAFE_PREFIXES)):
        return "inline", headers
    headers["Content-Security-Policy"] = "sandbox"
    return "attachment", headers
