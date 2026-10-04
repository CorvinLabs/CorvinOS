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

import mimetypes
import re
from pathlib import Path
from typing import Any

from fastapi import HTTPException, UploadFile, status as http_status

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


async def receive_uploaded_files(
    files: list[UploadFile],
    attach_dir: Path,
    *,
    max_bytes: int = ATTACH_MAX_BYTES,
    max_files: int = ATTACH_MAX_FILES,
    path_prefix: str = "attachments",
) -> list[dict[str, Any]]:
    """Validate, sanitise and store one or more uploaded files.

    Returns a list of ``{name, size, mime, path}`` descriptors — ``path`` is
    relative to whatever directory the caller's ``attach_dir`` lives under
    (e.g. ``"attachments/report.csv"``), matching the frontend's
    ``AttachmentMeta`` shape so session- and group-chat uploads share one
    response contract.
    """
    if len(files) > max_files:
        raise HTTPException(
            http_status.HTTP_400_BAD_REQUEST,
            f"Too many files — max {max_files} per upload",
        )
    attach_dir.mkdir(parents=True, exist_ok=True)

    results: list[dict[str, Any]] = []
    for upload in files:
        data = await upload.read(max_bytes + 1)
        if len(data) > max_bytes:
            raise HTTPException(
                http_status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                f"File exceeds 50 MB limit: {upload.filename!r}",
            )
        safe_name = safe_attach_name(upload.filename or "file")
        dest = attach_dir / safe_name
        # Avoid overwrite by appending a counter suffix.
        if dest.exists():
            base, dot_ext = (safe_name.rsplit(".", 1) if "." in safe_name
                             else (safe_name, ""))
            for i in range(1, 100):
                candidate = attach_dir / (f"{base}_{i}.{dot_ext}" if dot_ext else f"{base}_{i}")
                if not candidate.exists():
                    dest = candidate
                    safe_name = dest.name
                    break
        dest.write_bytes(data)
        mime = upload.content_type or mimetypes.guess_type(safe_name)[0] or "application/octet-stream"
        results.append({
            "name": safe_name,
            "size": len(data),
            "mime": mime,
            "path": f"{path_prefix}/{safe_name}",
        })
    return results
