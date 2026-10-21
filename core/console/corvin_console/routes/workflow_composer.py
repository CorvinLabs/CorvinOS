"""Workflow composer routes (CONCEPT-0115 / ADR-2270).

The workflow editor's design chat uses the SAME composer as the main chat
(attach, folder, drag-and-drop, chips, dictation, slash commands). This module
is the server side of what that composer needs that the chat routes do not
provide:

  GET    /workflow-design/commands                  the design chat's slash-command table
  POST   /workflows/{wid}/attachments               upload files into the workflow's attachment store
  GET    /workflows/{wid}/attachments/{att_id}      read one back (Content-Disposition per type, CSP sandbox)
  DELETE /workflows/{wid}/attachments/{att_id}      drop one before it is sent

Attachment store (CONCEPT-0115 / ADR-2270):
``<tenant_home>/workflows/<wid>/attachments/<att_id>/{meta.json,data/<safe_name>}`` — under the
SAME root the workflow files and the Art. 17 handler use, never in a worker's cwd. The tenant
comes ONLY from the session record, never from a request field. A client names an attachment by
``att_id`` (16 hex); the server re-checks existence, size and sha256 on every use. The data file
is write-once (``O_EXCL``, never modified in place); ``meta.json`` names the uploader twice:
``uploaded_by`` (the per-login session fingerprint) and ``uploaded_by_subject`` (the login's
durable key, ``SessionRecord.token_fingerprint`` — ``""`` for the credential-less local login,
which has none). The cap of ``MAX_ATTACHMENTS_PER_WORKFLOW`` is counted and admitted under the
per-workflow lock ``<wid>/attachments/.lock`` (T-0239). A run freezes the store for itself by
HARD LINK into ``<wid>/runs/<rid>/attachments/`` (T-0238), see :func:`snapshot_for_run`.

Slash commands are a SERVER table, not a client constant (the ADR-2235 lesson):
a command the server does not parse cannot be audited or refused fail-closed,
and a client copy of the grammar is how two dispatchers drift. The design
WebSocket (``routes/workflows.py::workflow_chat_ws``) parses every ``/`` line
through ``parse_design_command`` / ``run_design_command`` below and answers it
WITHOUT spawning an engine and WITHOUT writing it into the design transcript
(a command line is not a design instruction and must not feed later prompts).
"""
from __future__ import annotations

import asyncio
import errno
import hashlib
import json
import logging
import mimetypes
import os
import re
import secrets
import shutil
import sys
import time
from pathlib import Path
from typing import Annotated, Any, NamedTuple

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse

from .. import attachments_common as _attachments
from .. import audit as console_audit
from .. import auth as session_auth
from ..deps import require_session, require_session_csrf_on_mutation

_FORGE_PATH = Path(__file__).resolve().parents[3] / "corvin_operator" / "forge"
if str(_FORGE_PATH) not in sys.path:
    sys.path.insert(0, str(_FORGE_PATH))
from forge import paths as _forge_paths  # noqa: E402

_log = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(require_session_csrf_on_mutation)])

# One entry per command the WebSocket understands. `cmd` is what the palette
# inserts, `args` the placeholder, `desc` the one-line help.
DESIGN_COMMANDS: tuple[dict[str, str], ...] = (
    {"cmd": "/help", "args": "", "desc": "List the design chat commands"},
    {"cmd": "/phase", "args": "", "desc": "Show the current design phase"},
    {"cmd": "/nodes", "args": "", "desc": "List the steps currently on the canvas"},
    {"cmd": "/voice on", "args": "", "desc": "Speak the designer's replies aloud"},
    {"cmd": "/voice off", "args": "", "desc": "Stop spoken replies"},
)

# A command token is a bare word after the slash. A path such as `/home/x/y`
# contains another slash and is ordinary text, never a command.
_CMD_TOKEN = re.compile(r"^/[a-z][a-z0-9-]*$")


class CommandResult(NamedTuple):
    reply: str
    voice: bool | None  # new voice preference when the command changed it


def parse_design_command(text: str) -> tuple[str, str] | None:
    """Return ``(command, argument)`` for a slash line, else ``None``.

    A line is a command when its first whitespace-delimited token is a bare
    ``/word``. Unknown words are still returned (the caller answers "unknown
    command") so a mistyped command is never forwarded to the designer.
    """
    stripped = text.strip()
    if not stripped.startswith("/"):
        return None
    head, _, rest = stripped.partition(" ")
    head = head.lower()
    if not _CMD_TOKEN.match(head):
        return None
    return head, rest.strip()


def run_design_command(
    command: str,
    argument: str,
    *,
    phase: str,
    node_ids: list[str],
    voice_enabled: bool,
) -> CommandResult:
    """Execute one design command. Pure: no I/O, no spawn."""
    if command == "/help":
        lines = ["Design chat commands:"]
        for entry in DESIGN_COMMANDS:
            lines.append(f"{entry['cmd']} - {entry['desc']}")
        return CommandResult("\n".join(lines), None)
    if command == "/phase":
        return CommandResult(f"Current design phase: {phase}.", None)
    if command == "/nodes":
        if not node_ids:
            return CommandResult("The canvas has no steps yet.", None)
        return CommandResult("Steps on the canvas: " + " -> ".join(node_ids), None)
    if command == "/voice":
        arg = argument.lower()
        if arg in ("on", "off"):
            on = arg == "on"
            return CommandResult(f"Spoken replies are {'on' if on else 'off'}.", on)
        state = "on" if voice_enabled else "off"
        return CommandResult(f"Spoken replies are {state}. Use /voice on or /voice off.", None)
    return CommandResult(f"Unknown command {command} - not sent to the designer. Type /help.", None)


@router.get("/workflow-design/commands")
def design_commands(
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> dict[str, Any]:
    """The design chat's slash-command table (same table the WebSocket parses)."""
    return {"commands": list(DESIGN_COMMANDS)}


# ── Attachment store ───────────────────────────────────────────────────────

_ATT_ID = re.compile(r"^[0-9a-f]{16}$")
_MIME_SHAPE = re.compile(r"^[a-z0-9][a-z0-9.+-]{0,60}/[a-z0-9][a-z0-9.+-]{0,100}$")
MAX_ATTACHMENTS_PER_WORKFLOW = 100
MAX_ATTACHMENTS_PER_MESSAGE = 10
# What the design chat can READ: UTF-8 text. Anything else is storable (the chat accepts any
# type too) but is refused at send time, because an engine prompt is text and the L34 gate
# classifies exactly the text it is handed.
READABLE_EXTENSIONS = frozenset({
    ".txt", ".md", ".csv", ".tsv", ".json", ".yaml", ".yml", ".toml", ".xml", ".html", ".css",
    ".sql", ".py", ".js", ".ts", ".log", ".ini", ".cfg", ".sh",
})
MAX_INLINE_BYTES_PER_FILE = 24 * 1024
MAX_INLINE_BYTES_PER_MESSAGE = 64 * 1024


class AttachmentError(Exception):
    """A referenced attachment cannot be used. ``code`` is a closed audit reason."""

    def __init__(self, code: str, message: str, http_status: int = 409) -> None:
        super().__init__(message)
        self.code = code
        self.http_status = http_status


def attachments_root(tenant_id: str, wid: str) -> Path:
    """``<tenant_home>/workflows/<wid>/attachments`` — tenant from the session record only."""
    return _forge_paths.tenant_home(tenant_id) / "workflows" / wid / "attachments"


def valid_att_id(att_id: str) -> bool:
    return bool(_ATT_ID.match(att_id or ""))


def _closed_mime(raw: str | None, name: str) -> str:
    cand = (raw or "").split(";")[0].strip().lower() or (mimetypes.guess_type(name)[0] or "")
    return cand if _MIME_SHAPE.match(cand) else "application/octet-stream"


def _sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while chunk := fh.read(1024 * 1024):
            h.update(chunk)
    return h.hexdigest()


def _write_meta(att_dir: Path, meta: dict[str, Any]) -> None:
    tmp = att_dir / "meta.json.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(meta, fh)
    os.replace(tmp, att_dir / "meta.json")


def load_attachment(tenant_id: str, wid: str, att_id: str) -> tuple[dict[str, Any], Path]:
    """Return ``(meta, file_path)`` or raise ``AttachmentError``.

    Re-verifies on EVERY use that the file exists, is a regular non-symlink file directly in
    the attachment's own ``data/`` dir, and still has the recorded size and sha256 — so an
    attachment swapped on disk after upload is refused, not sent.
    """
    if not valid_att_id(att_id):
        raise AttachmentError("bad_id", "invalid attachment id", 404)
    att_dir = attachments_root(tenant_id, wid) / att_id
    meta_p = att_dir / "meta.json"
    if att_dir.is_symlink() or not meta_p.is_file() or meta_p.is_symlink():
        raise AttachmentError("missing", "attachment not found", 404)
    try:
        meta = json.loads(meta_p.read_text(encoding="utf-8"))
        name = str(meta["name"])
    except Exception as exc:  # noqa: BLE001
        raise AttachmentError("corrupt", "attachment record unreadable", 409) from exc
    path = att_dir / "data" / name
    if (name != _attachments.safe_attach_name(name) or path.is_symlink() or not path.is_file()
            or path.resolve().parent != (att_dir / "data").resolve()):
        raise AttachmentError("missing", "attachment file not found", 404)
    if path.stat().st_size != int(meta.get("size", -1)) or _sha256_of(path) != meta.get("sha256"):
        raise AttachmentError("changed", "attachment changed after upload", 409)
    return meta, path


def public_view(meta: dict[str, Any]) -> dict[str, Any]:
    """What the client may see: id, name, size, mime. Never a filesystem path."""
    return {"id": meta["att_id"], "name": meta["name"], "size": meta["size"], "mime": meta["mime"]}


def design_readable(meta: dict[str, Any]) -> bool:
    return Path(str(meta.get("name", ""))).suffix.lower() in READABLE_EXTENSIONS or \
        str(meta.get("mime", "")).startswith("text/")


def resolve_for_message(tenant_id: str, wid: str, att_ids: list[str],
                        sid_fingerprint: str = "") -> list[tuple[dict[str, Any], Path]]:
    """Validate every attachment a client references on one design message. The client is
    never the authority: count, existence, tenant (by path), size/hash and readability are
    all decided here; the first failure refuses the whole message (and is audited)."""
    if len(att_ids) > MAX_ATTACHMENTS_PER_MESSAGE:
        raise AttachmentError("too_many", f"at most {MAX_ATTACHMENTS_PER_MESSAGE} attachments per message", 413)
    out: list[tuple[dict[str, Any], Path]] = []
    seen: set[str] = set()
    for att_id in att_ids:
        att_id = str(att_id)
        if att_id in seen:
            continue
        seen.add(att_id)
        try:
            meta, path = load_attachment(tenant_id, wid, att_id)
            if not design_readable(meta):
                raise AttachmentError(
                    "not_readable",
                    "The designer can only read text-based attachments (txt, md, csv, json, yaml, …). "
                    f"Remove \"{meta['name']}\" or attach a text version.", 415)
        except AttachmentError as exc:
            console_audit.workflow_attachment_refused(
                tenant_id=tenant_id, wid=wid, att_id=att_id, reason_code=exc.code,
                sid_fingerprint=sid_fingerprint)
            raise
        out.append((meta, path))
    return out


def inline_block(items: list[tuple[dict[str, Any], Path]]) -> str:
    """The text the engine sees for the attachments of one message (capped, UTF-8 only).

    This block is appended to the prompt BEFORE the spawn gates run, so L44 / L34 / L35 classify
    the attachment text exactly as they classify the operator's own words."""
    parts: list[str] = []
    budget = MAX_INLINE_BYTES_PER_MESSAGE
    for meta, path in items:
        with path.open("rb") as fh:                      # read only the capped prefix, never 50 MiB
            raw = fh.read(min(MAX_INLINE_BYTES_PER_FILE, budget))
        budget -= len(raw)
        text = raw.decode("utf-8", errors="replace")
        trunc = " (truncated)" if meta["size"] > len(raw) else ""
        parts.append(f"[Attached file: {meta['name']}, {meta['size']} bytes{trunc}]\n{text}\n[End of attached file]")
        if budget <= 0:
            break
    return "\n\n".join(parts)


# ``os.link`` is refused on these for reasons that are the filesystem's, not the file's: another
# device (EXDEV), a filesystem or mount that has no hard links (EPERM, ENOTSUP, EOPNOTSUPP,
# ENOSYS), or the link limit of the inode (EMLINK). Only then is the snapshot a copy. Anything
# else (ENOSPC, EACCES, EEXIST, ...) is a real failure and refuses the run.
_LINK_FALLBACK_ERRNOS = frozenset(
    getattr(errno, name) for name in ("EXDEV", "EPERM", "EMLINK", "ENOTSUP", "EOPNOTSUPP", "ENOSYS")
    if hasattr(errno, name))


def _freeze_bytes(src: Path, dst: Path) -> str:
    """Give ``dst`` the bytes of ``src`` for the life of the run: a HARD LINK where the
    filesystem allows one, else a copy. Returns ``"link"`` or ``"copy"`` (audited).

    The store file is write-once (``O_EXCL`` at upload, never rewritten; ``DELETE`` unlinks the
    directory entry, a re-upload is a new ``att_id``), so both names see the same immutable
    bytes and the link freezes them at no cost — the copy used to add up to 100 x 50 MiB per
    run (T-0238 / WFE-13)."""
    try:
        os.link(src, dst)
        return "link"
    except (AttributeError, NotImplementedError):
        pass
    except OSError as exc:
        if exc.errno not in _LINK_FALLBACK_ERRNOS:
            raise
    shutil.copyfile(src, dst)
    return "copy"


def snapshot_for_run(tenant_id: str, wid: str, rid: str) -> list[dict[str, Any]]:
    """Freeze the workflow's attachments for one run (variant b of the concept).

    Hard-links (or, where the filesystem refuses a link, copies — :func:`_freeze_bytes`) each
    stored attachment into ``<wid>/runs/<rid>/attachments/<att_id>/`` after re-verifying size and
    hash, writes the per-run ``meta.json`` and audits ``workflow.attachment_snapshot`` per item
    (with ``method`` link/copy). A missing or changed attachment raises ``AttachmentError``: the
    run is refused, never silently run with different bytes. Returns the public records for the
    run meta."""
    root = attachments_root(tenant_id, wid)
    if not root.is_dir():
        return []
    snap: list[dict[str, Any]] = []
    for d in sorted(p for p in root.iterdir() if p.is_dir() and valid_att_id(p.name)):
        if not (d / "meta.json").is_file():
            # A reservation (upload in flight, or a leftover of a crashed one — swept by the
            # next reservation): not an attachment of the workflow yet, never a refusal.
            continue
        meta, path = load_attachment(tenant_id, wid, d.name)
        dest = root.parent / "runs" / rid / "attachments" / d.name
        (dest / "data").mkdir(parents=True, exist_ok=True)
        method = _freeze_bytes(path, dest / "data" / path.name)
        _write_meta(dest, meta)
        console_audit.workflow_attachment_snapshot(
            tenant_id=tenant_id, wid=wid, rid=rid, att_id=d.name,
            size_bytes=int(meta["size"]), sha256_prefix=str(meta["sha256"])[:16], method=method)
        snap.append({**public_view(meta), "sha256_prefix": str(meta["sha256"])[:16]})
    return snap


def _require_workflow(rec: session_auth.SessionRecord, wid: str) -> None:
    from . import workflows as _wf  # lazy: workflows.py imports this module
    _wf._validate_wid(wid)
    _wf._require_workflow(rec.tenant_id, wid)


# A reservation that still has no ``meta.json`` after this long is the leftover of an upload
# whose process died mid-stream (the all-or-nothing cleanup never ran). It would otherwise hold
# a slot of the cap forever; it is removed under the lock by the next reservation.
RESERVATION_TTL_S = 3600.0


def _sweep_dead_reservations(root: Path, *, now: float | None = None) -> int:
    """Remove attachment directories that never got their ``meta.json`` and are older than
    :data:`RESERVATION_TTL_S`. Caller holds the attachments lock. Returns the count removed."""
    now = time.time() if now is None else now
    removed = 0
    for d in root.iterdir():
        if not d.is_dir() or d.is_symlink() or (d / "meta.json").exists():
            continue
        try:
            if now - d.stat().st_mtime <= RESERVATION_TTL_S:
                continue
            shutil.rmtree(d)
            removed += 1
        except OSError:
            continue
    return removed


def _reserve_attachment_dirs(tenant_id: str, wid: str, n: int) -> list[Path]:
    """Count and admit ``n`` new attachments in ONE critical section (T-0239 / WFE-18).

    The cap used to be checked against a count taken BEFORE the upload body was awaited, so N
    concurrent uploads each saw room and together overshot it. Here the per-workflow lock
    ``<wid>/attachments/.lock`` (the same bounded ``flock`` the workflow routes use, so it holds
    across processes) covers the count AND the ``mkdir`` of every new attachment directory: a
    reserved directory is counted by the next uploader before a byte of its file is stored.
    The caller streams into the reservations and removes them on failure."""
    from . import workflows as _wf  # lazy: workflows.py imports this module
    root = attachments_root(tenant_id, wid)
    try:
        with _wf._bounded_flock(root / ".lock", f"workflow attachments {wid!r}"):
            _sweep_dead_reservations(root)
            existing = sum(1 for p in root.iterdir() if p.is_dir())
            if existing + n > MAX_ATTACHMENTS_PER_WORKFLOW:
                raise HTTPException(409, f"a workflow holds at most {MAX_ATTACHMENTS_PER_WORKFLOW} attachments")
            reserved: list[Path] = []
            for _ in range(n):
                d = root / secrets.token_hex(8)
                d.mkdir(exist_ok=False)
                reserved.append(d)
            return reserved
    except _wf.WorkflowLockBusy as exc:
        raise HTTPException(503, "the attachment store is busy - try again") from exc


@router.post("/workflows/{wid}/attachments")
async def upload_workflow_attachments(
    wid: str,
    request: Request,
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> dict[str, Any]:
    """Store one or more files (same limits as the chat: 10 files, 50 MiB each, no type list)."""
    _require_workflow(rec, wid)
    files, form = await _attachments.read_upload_form(request)
    created: list[Path] = []
    try:
        for f in files:  # sizes first: a refused batch stores nothing
            size = f.size
            if size is not None and size > _attachments.ATTACH_MAX_BYTES:
                raise HTTPException(413, f"File exceeds 50 MB limit: {f.filename!r}")
        # The cap: counted and admitted atomically, in a thread so the lock never stalls the loop.
        created = await asyncio.get_running_loop().run_in_executor(
            None, _reserve_attachment_dirs, rec.tenant_id, wid, len(files))
        records: list[dict[str, Any]] = []
        for f, att_dir in zip(files, created):
            att_id = att_dir.name
            stored = await _attachments.receive_uploaded_files([f], att_dir / "data", max_files=1)
            info = stored[0]
            file_path = att_dir / "data" / info["name"]
            meta = {
                "att_id": att_id, "name": info["name"], "size": info["size"],
                "mime": _closed_mime(info.get("mime"), info["name"]),
                "sha256": _sha256_of(file_path), "created_at": time.time(),
                # Art. 17 (ADR-2270 s7): the per-login session fingerprint AND the login's durable
                # key, so an erasure request can name either. The local login has no durable key.
                "uploaded_by": rec.sid_fingerprint,
                "uploaded_by_subject": rec.token_fingerprint or "",
            }
            _write_meta(att_dir, meta)
            records.append(public_view(meta))
            console_audit.workflow_attachment_uploaded(
                tenant_id=rec.tenant_id, wid=wid, att_id=att_id, size_bytes=int(meta["size"]),
                mime=meta["mime"], sha256_prefix=meta["sha256"][:16], sid_fingerprint=rec.sid_fingerprint)
    except BaseException:
        for d in created:  # all-or-nothing: the reservations go too
            shutil.rmtree(d, ignore_errors=True)
        raise
    finally:
        await form.close()
    return {"attachments": records}


@router.get("/workflows/{wid}/attachments/{att_id}")
def get_workflow_attachment(
    wid: str, att_id: str,
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> FileResponse:
    _require_workflow(rec, wid)
    try:
        meta, path = load_attachment(rec.tenant_id, wid, att_id)
    except AttachmentError as exc:
        raise HTTPException(exc.http_status if exc.http_status in (404, 409) else 404, str(exc)) from exc
    disposition, headers = _attachments.serve_headers(meta.get("mime"))
    return FileResponse(path=str(path), media_type=meta.get("mime") or "application/octet-stream",
                        filename=meta["name"], content_disposition_type=disposition, headers=headers)


@router.delete("/workflows/{wid}/attachments/{att_id}")
def delete_workflow_attachment(
    wid: str, att_id: str,
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> dict[str, Any]:
    _require_workflow(rec, wid)
    if not valid_att_id(att_id):
        raise HTTPException(404, "attachment not found")
    att_dir = attachments_root(rec.tenant_id, wid) / att_id
    if att_dir.is_symlink() or not att_dir.is_dir():
        raise HTTPException(404, "attachment not found")
    shutil.rmtree(att_dir)
    console_audit.workflow_attachment_deleted(
        tenant_id=rec.tenant_id, wid=wid, att_id=att_id, sid_fingerprint=rec.sid_fingerprint)
    return {"ok": True}
