"""Group chat routes (ADR-2216).

The first group-conversation surface in the console — see
``chat_group_store.py`` for the storage design rationale (no prior group
model existed, foreign A2A agents are a first-class participant kind from
the start).

Routes (all behind a live session + CSRF on mutation):

  GET    /chat/groups                                 list this tenant's groups
  POST   /chat/groups                                  create a group
  GET    /chat/groups/{id}                              group detail (incl. participants)
  DELETE /chat/groups/{id}                              delete the group on this instance
  POST   /chat/groups/{id}/participants                add a participant
  DELETE /chat/groups/{id}/participants/{pid}          remove a participant
  GET    /chat/groups/{id}/messages                     list messages
  POST   /chat/groups/{id}/messages                     send a message
  POST   /chat/groups/{id}/attachments                  upload files (see upload_group_attachments)

Two-layer authorization (ADR-2216 Decision, conceptual level): a group's
OWN participant list is the authorization surface for "who sees this
conversation." The A2A origin/endpoint state is a SEPARATE layer gating
"may this peer talk to us at all" — ``require_friendship_active`` resolves
that live on every admit/send involving an ``a2a_peer`` participant, never
cached, so a revoked friendship immediately stops that peer's group
participation even though the two facts live in different stores.
"""
from __future__ import annotations

import mimetypes
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .. import attachments_common as _attachments
from .. import audit as console_audit
from .. import auth as session_auth
from ..deps import require_session_csrf_on_mutation

_THIS_DIR = Path(__file__).resolve().parent
_BRIDGES_SHARED = _THIS_DIR.parents[3] / "corvin_operator" / "bridges" / "shared"
if str(_BRIDGES_SHARED) not in sys.path:
    sys.path.insert(0, str(_BRIDGES_SHARED))

import paths as _a2a_paths  # type: ignore[import-not-found]
from remote_trigger_sender import RemoteTriggerSender  # type: ignore[import-not-found]

from .. import chat_group_store as _store
from . import a2a_feed as _feed_routes  # reuse _peers() for the friendship check

router = APIRouter(dependencies=[Depends(require_session_csrf_on_mutation)])

Session = Annotated[session_auth.SessionRecord, Depends(require_session_csrf_on_mutation)]

ParticipantKind = Literal["human", "agent", "a2a_peer"]

# Outbound group delivery runs off the request thread: a peer send blocks for
# up to its timeout, and a group message must not hold the HTTP response.
_FANOUT_POOL = ThreadPoolExecutor(max_workers=4, thread_name_prefix="group-fanout")

# A group created by an inbound envelope carries this creator prefix. Only a
# group's ORIGIN instance relays peer messages onward; a mirror never does,
# which is what keeps hub-and-spoke delivery loop-free.
_MIRROR_CREATOR_PREFIX = "a2a:"
_LOCAL_OPERATOR_ID = "operator"


def _peer_label(peer_endpoint_id: str) -> str:
    peer = next((p for p in _feed_routes._peers() if p["peer_id"] == peer_endpoint_id), None)
    return str((peer or {}).get("label") or peer_endpoint_id)


def _deliver_to_peers(
    *, tenant_id: str, group_id: str, endpoint_ids: list[str], text: str,
) -> None:
    """Send one group message to each peer endpoint, carrying ``group_id``.
    Never raises; every outcome is audited."""
    for ep in endpoint_ids:
        try:
            result = RemoteTriggerSender().send(ep, text, purpose_id="group_message", group_id=group_id)
            ok = bool(result.ok)
        except Exception:  # noqa: BLE001 — a failing peer must not stop the others
            ok = False
        try:
            console_audit.action_performed(
                tenant_id=tenant_id, sid_fingerprint=f"a2a:{ep}",
                action="chat.group.message_sent_to_peer" if ok else "chat.group.message_send_to_peer_failed",
                target_kind="chat_group", target_id=group_id,
            )
        except Exception:  # noqa: BLE001
            pass


def _schedule_delivery(
    *, tenant_id: str, group: dict[str, Any], text: str, exclude: str | None = None,
) -> list[str]:
    endpoint_ids = [
        p["peer_endpoint_id"] for p in group["participants"]
        if p["kind"] == "a2a_peer" and p.get("peer_endpoint_id") and p["peer_endpoint_id"] != exclude
    ]
    if endpoint_ids:
        _FANOUT_POOL.submit(
            _deliver_to_peers, tenant_id=tenant_id, group_id=group["group_id"],
            endpoint_ids=endpoint_ids, text=text,
        )
    return endpoint_ids


def require_friendship_active(peer_endpoint_id: str) -> None:
    """Live gate — resolves the CURRENT A2A origin/endpoint state, never a
    cached "already a member" fact. Raises HTTPException(404) if the peer
    has no enabled endpoint, matching the same check
    ``a2a_feed.a2a_feed_send_confirm`` already uses before firing a send."""
    peer = next((p for p in _feed_routes._peers() if p["peer_id"] == peer_endpoint_id), None)
    if peer is None or not peer.get("can_send"):
        raise HTTPException(status_code=404, detail="no active friendship for this peer")


class GroupCreateRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=128)


class ParticipantOut(BaseModel):
    participant_id: str
    kind: ParticipantKind
    display_name: str
    peer_endpoint_id: str | None
    added_at: float
    added_by: str


class GroupOut(BaseModel):
    group_id: str
    title: str
    created_at: float
    created_by: str
    participants: list[ParticipantOut]


class AddParticipantRequest(BaseModel):
    participant_id: str = Field(..., min_length=1, max_length=128)
    kind: ParticipantKind
    display_name: str = Field(default="", max_length=64)
    peer_endpoint_id: str | None = Field(default=None, max_length=128)


class MessageOut(BaseModel):
    id: str
    group_id: str
    sender_participant_id: str
    ts: float
    text: str
    delivery: str


class SendMessageRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=16 * 1024)
    sender_participant_id: str = Field(..., min_length=1, max_length=128)


@router.get("/chat/groups")
def list_groups(rec: Session) -> list[GroupOut]:
    tenant_dir = _a2a_paths.tenant_global_dir(rec.tenant_id)
    groups = _store.list_groups(tenant_dir, rec.tenant_id)
    return [GroupOut(**g) for g in groups]


@router.post("/chat/groups")
def create_group(rec: Session, body: GroupCreateRequest) -> GroupOut:
    tenant_dir = _a2a_paths.tenant_global_dir(rec.tenant_id)
    rec_out = _store.create_group(
        tenant_dir,
        tenant_id=rec.tenant_id,
        title=body.title,
        created_by_participant_id=rec.sid_fingerprint,
        created_by_kind="human",
    )
    console_audit.action_performed(
        tenant_id=rec.tenant_id, sid_fingerprint=rec.sid_fingerprint,
        action="chat.group.created", target_kind="chat_group", target_id=rec_out["group_id"],
    )
    return GroupOut(**rec_out)


@router.get("/chat/groups/{group_id}")
def get_group(rec: Session, group_id: str) -> GroupOut:
    tenant_dir = _a2a_paths.tenant_global_dir(rec.tenant_id)
    g = _store.get_group(tenant_dir, group_id)
    if g is None or g.get("tenant_id") != rec.tenant_id:
        raise HTTPException(status_code=404, detail="group not found")
    return GroupOut(**g)


@router.delete("/chat/groups/{group_id}")
def delete_group(rec: Session, group_id: str) -> dict[str, Any]:
    """Delete a group and its messages on this instance; audited before it goes."""
    tenant_dir = _a2a_paths.tenant_global_dir(rec.tenant_id)
    g = _store.get_group(tenant_dir, group_id)
    if g is None or g.get("tenant_id") != rec.tenant_id:
        raise HTTPException(status_code=404, detail="group not found")
    console_audit.action_performed(
        tenant_id=rec.tenant_id, sid_fingerprint=rec.sid_fingerprint,
        action="chat.group.deleted", target_kind="chat_group", target_id=group_id,
    )
    _store.delete_group(tenant_dir, group_id)
    return {"deleted": True, "group_id": group_id}


@router.post("/chat/groups/{group_id}/participants")
def add_participant(rec: Session, group_id: str, body: AddParticipantRequest) -> GroupOut:
    tenant_dir = _a2a_paths.tenant_global_dir(rec.tenant_id)
    g = _store.get_group(tenant_dir, group_id)
    if g is None or g.get("tenant_id") != rec.tenant_id:
        raise HTTPException(status_code=404, detail="group not found")

    if body.kind == "a2a_peer":
        if not body.peer_endpoint_id:
            raise HTTPException(status_code=400, detail="peer_endpoint_id required for a2a_peer participants")
        # The live gate: a foreign agent may only join once it is an
        # ACTIVE friendship — never admitted on the strength of a token
        # having once been imported (ADR-2216 Decision, conceptual level).
        require_friendship_active(body.peer_endpoint_id)

    try:
        rec_out = _store.add_participant(
            tenant_dir, group_id,
            participant_id=body.participant_id,
            kind=body.kind,
            display_name=body.display_name or body.participant_id,
            added_by=rec.sid_fingerprint,
            peer_endpoint_id=body.peer_endpoint_id,
        )
    except _store.ChatGroupError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    console_audit.action_performed(
        tenant_id=rec.tenant_id, sid_fingerprint=rec.sid_fingerprint,
        action="chat.group.participant_added", target_kind="chat_group", target_id=group_id,
    )
    return GroupOut(**rec_out)


@router.delete("/chat/groups/{group_id}/participants/{participant_id}")
def remove_participant(rec: Session, group_id: str, participant_id: str) -> GroupOut:
    tenant_dir = _a2a_paths.tenant_global_dir(rec.tenant_id)
    g = _store.get_group(tenant_dir, group_id)
    if g is None or g.get("tenant_id") != rec.tenant_id:
        raise HTTPException(status_code=404, detail="group not found")
    try:
        rec_out = _store.remove_participant(tenant_dir, group_id, participant_id)
    except _store.ChatGroupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    console_audit.action_performed(
        tenant_id=rec.tenant_id, sid_fingerprint=rec.sid_fingerprint,
        action="chat.group.participant_removed", target_kind="chat_group", target_id=group_id,
    )
    return GroupOut(**rec_out)


@router.get("/chat/groups/{group_id}/messages")
def list_messages(rec: Session, group_id: str, limit: int = 200) -> list[MessageOut]:
    tenant_dir = _a2a_paths.tenant_global_dir(rec.tenant_id)
    g = _store.get_group(tenant_dir, group_id)
    if g is None or g.get("tenant_id") != rec.tenant_id:
        raise HTTPException(status_code=404, detail="group not found")
    return [MessageOut(**m) for m in _store.list_messages(tenant_dir, group_id, limit=limit)]


class SendToPeerRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=16 * 1024)
    sender_participant_id: str = Field(..., min_length=1, max_length=128)
    peer_id: str = Field(..., min_length=1, max_length=128)


class SendToPeerResponse(BaseModel):
    message_id: str
    status: str  # "sent_pending" | "error"
    detail: str | None = None


@router.post("/chat/groups/{group_id}/send-to-peer")
def send_message_to_peer(rec: Session, group_id: str, body: SendToPeerRequest) -> SendToPeerResponse:
    """ADR-2218 — Send a message FROM a group TO an a2a_peer participant.

    Constructs a TaskEnvelope with the group_id so the receiver can route
    the message to the group's message store (not a 1:1 channel) and replies
    come back to the group.
    """
    tenant_dir = _a2a_paths.tenant_global_dir(rec.tenant_id)
    g = _store.get_group(tenant_dir, group_id)
    if g is None or g.get("tenant_id") != rec.tenant_id:
        raise HTTPException(status_code=404, detail="group not found")
    if not _store.is_participant(tenant_dir, group_id, body.sender_participant_id):
        raise HTTPException(status_code=403, detail="sender is not a participant of this group")

    # Validate the peer_id is an a2a_peer in this group and has an active friendship.
    peer_participant = None
    for p in g["participants"]:
        if p["participant_id"] == body.peer_id and p["kind"] == "a2a_peer":
            peer_participant = p
            break
    if peer_participant is None:
        raise HTTPException(status_code=404, detail="peer is not an a2a_peer participant in this group")

    peer_endpoint_id = peer_participant.get("peer_endpoint_id")
    if not peer_endpoint_id:
        raise HTTPException(status_code=400, detail="peer has no endpoint_id configured")

    # Re-check friendship is active (not cached — must be live).
    require_friendship_active(peer_endpoint_id)

    # Append the message locally first (delivery="remote" to indicate cross-instance).
    msg = _store.append_message(
        tenant_dir, group_id,
        sender_participant_id=body.sender_participant_id,
        text=body.text,
        delivery="remote",
    )

    # Send the real TaskEnvelope to the peer, carrying group_id (ADR-2218)
    # so the receiving instance's RemoteTriggerReceiver routes the message
    # into its own group store instead of a 1:1 channel. ``instruction``
    # IS the message text — the receiver passes it straight through to the
    # group_message_handler callback (Phase 3).
    try:
        sender = RemoteTriggerSender()
        result = sender.send(
            peer_endpoint_id,
            body.text,
            purpose_id="group_message",
            group_id=group_id,
        )
        if not result.ok:
            console_audit.action_performed(
                tenant_id=rec.tenant_id, sid_fingerprint=rec.sid_fingerprint,
                action="chat.group.message_send_to_peer_failed", target_kind="chat_group", target_id=group_id,
            )
            return SendToPeerResponse(
                message_id=msg["id"], status="error",
                detail=result.error_detail or result.status,
            )
        console_audit.action_performed(
            tenant_id=rec.tenant_id, sid_fingerprint=rec.sid_fingerprint,
            action="chat.group.message_sent_to_peer", target_kind="chat_group", target_id=group_id,
        )
    except Exception as e:
        console_audit.action_performed(
            tenant_id=rec.tenant_id, sid_fingerprint=rec.sid_fingerprint,
            action="chat.group.message_send_to_peer_failed", target_kind="chat_group", target_id=group_id,
        )
        raise HTTPException(status_code=500, detail=f"Failed to send to peer: {str(e)}") from e

    return SendToPeerResponse(message_id=msg["id"], status="sent_pending")


@router.post("/chat/groups/{group_id}/messages")
def send_message(rec: Session, group_id: str, body: SendMessageRequest) -> MessageOut:
    """Append a message to the group. If the group has any ``a2a_peer``
    participants, their friendship is re-checked live (not cached) before
    the message is admitted — a revoked friendship must not let a stale
    membership keep accepting messages into the group."""
    tenant_dir = _a2a_paths.tenant_global_dir(rec.tenant_id)
    g = _store.get_group(tenant_dir, group_id)
    if g is None or g.get("tenant_id") != rec.tenant_id:
        raise HTTPException(status_code=404, detail="group not found")
    if not _store.is_participant(tenant_dir, group_id, body.sender_participant_id):
        raise HTTPException(status_code=403, detail="sender is not a participant of this group")

    for p in g["participants"]:
        if p["kind"] == "a2a_peer" and p.get("peer_endpoint_id"):
            require_friendship_active(p["peer_endpoint_id"])

    has_peers = any(p["kind"] == "a2a_peer" and p.get("peer_endpoint_id") for p in g["participants"])
    msg = _store.append_message(
        tenant_dir, group_id,
        sender_participant_id=body.sender_participant_id,
        text=body.text,
        delivery="fanout" if has_peers else "local",
    )
    console_audit.action_performed(
        tenant_id=rec.tenant_id, sid_fingerprint=rec.sid_fingerprint,
        action="chat.group.message_sent", target_kind="chat_group", target_id=group_id,
    )
    # A group message reaches every A2A peer in the group (ADR-2218); the
    # receiving instance stores it only as TEXT (``_deliver_to_peers`` sends
    # ``text`` alone, no attachments) — an uploaded file below is visible to
    # local participants of this group only, same as any other local-first
    # chat surface without an A2A workdir to share.
    _schedule_delivery(tenant_id=rec.tenant_id, group=g, text=body.text)
    return MessageOut(**msg)


@router.post("/chat/groups/{group_id}/attachments")
async def upload_group_attachments(
    rec: Session,
    group_id: str,
    request: Request,
) -> dict[str, Any]:
    """Upload one or more files into the group's own ``attachments/`` directory.

    Mirrors ``routes/chat.py::upload_attachments`` (same 50 MB/file limit, no
    extension/MIME whitelist, same ``chat_attachment`` audit shape) — see
    ``attachments_common.py`` for the shared validation/storage logic and why
    it's safe without a type whitelist. The frontend embeds the returned
    paths as a text line in the next group message, same pattern as session
    chat; a peer in the group only ever receives that text (see the comment
    on ``send_message`` above), never the file itself.
    """
    tenant_dir = _a2a_paths.tenant_global_dir(rec.tenant_id)
    g = _store.get_group(tenant_dir, group_id)
    if g is None or g.get("tenant_id") != rec.tenant_id:
        raise HTTPException(status_code=404, detail="group not found")

    attach_dir = _store.attachments_dir(tenant_dir, group_id)
    if attach_dir is None:
        raise HTTPException(status_code=404, detail="group not found")

    files, form = await _attachments.read_upload_form(request)
    try:
        results = await _attachments.receive_uploaded_files(files, attach_dir)
    finally:
        await form.close()

    console_audit.action_performed(
        tenant_id=rec.tenant_id, sid_fingerprint=rec.sid_fingerprint,
        action="upload", target_kind="chat_attachment", target_id=group_id,
    )
    return {"attachments": results}


@router.get("/chat/groups/{group_id}/attachments/{name}")
def get_group_attachment(rec: Session, group_id: str, name: str) -> FileResponse:
    """Serve one stored group attachment to a session of the group's tenant.

    Uploads existed without a way to read them back, so the paths a group
    message references pointed nowhere in the UI. ``name`` must be exactly a
    stored name (``safe_attach_name`` round-trips), the resolved file must
    sit directly in the group's attachments dir and must not be a symlink;
    the response uses the same per-type disposition/CSP as session chat.
    """
    tenant_dir = _a2a_paths.tenant_global_dir(rec.tenant_id)
    g = _store.get_group(tenant_dir, group_id)
    if g is None or g.get("tenant_id") != rec.tenant_id:
        raise HTTPException(status_code=404, detail="group not found")
    attach_dir = _store.attachments_dir(tenant_dir, group_id)
    if attach_dir is None or name != _attachments.safe_attach_name(name) or name in (".", ".."):
        raise HTTPException(status_code=404, detail="attachment not found")
    path = attach_dir / name
    if path.is_symlink() or not path.is_file() or path.resolve().parent != attach_dir.resolve():
        raise HTTPException(status_code=404, detail="attachment not found")
    mime = mimetypes.guess_type(name)[0]
    disposition, headers = _attachments.serve_headers(mime)
    return FileResponse(path=str(path), media_type=mime or "application/octet-stream",
                        filename=name, content_disposition_type=disposition, headers=headers)


# ── A2A inbound group routing (ADR-2218 Phase 3.5) ─────────────────────────
#
# Callback injected into RemoteTriggerReceiver (see a2a_http_server.py's
# ``build_server(group_message_handler=...)`` and whatever wires the console
# into the gateway's A2A listener). Called from the RECEIVE path, not an
# HTTP route — no Session/CSRF dependency here, that's the sender side's job.
#
# A2A is host-scoped (see a2a_feed.py::_a2a_tenant) — the receiver and its
# origins are not tenant-partitioned, so this always resolves the PROCESS
# tenant, never a tenant carried on the wire (there is none to carry).

def handle_inbound_group_message(
    *, group_id: str, sender_origin_id: str, instruction: str, task_id: str,
) -> dict[str, Any]:
    """ADR-2218 Phase 3.5: store an inbound A2A group message.

    ``instruction`` IS the message text (RemoteTriggerSender.send() passes
    the caller's instruction straight through — there is no separate
    payload field on TaskEnvelope). ``sender_origin_id`` is our own local
    name for the peer (``env.origin_id`` as resolved against our
    ``origins_dir``), matched against the group's ``peer_endpoint_id`` —
    the same field the OUTBOUND route (``send_message_to_peer``) checks
    friendship against, so admission is symmetric in both directions.

    Returns ``{"status": "accepted", "message_id": ...}`` on success or
    ``{"status": "error", "reason": ...}`` — never raises (this runs
    inside RemoteTriggerReceiver's never-raises contract).
    """
    import os as _os
    try:
        tenant_id = (_os.environ.get("CORVIN_TENANT_ID") or "_default").strip() or "_default"
        tenant_dir = _a2a_paths.tenant_global_dir(tenant_id)

        g = _store.get_group(tenant_dir, group_id)
        if g is None:
            # First message of a group that lives on the sender's instance:
            # mirror it here, as a chat app shows a group you were added to.
            # Only an ACTIVE friend may open one — the same gate that lets it
            # send us anything at all.
            require_friendship_active(sender_origin_id)
            label = _peer_label(sender_origin_id)
            try:
                _store.create_group(
                    tenant_dir, tenant_id=tenant_id, title=f"Group with {label}",
                    created_by_participant_id=_LOCAL_OPERATOR_ID, created_by_kind="human",
                    group_id=group_id, created_by=f"{_MIRROR_CREATOR_PREFIX}{sender_origin_id}",
                )
                g = _store.add_participant(
                    tenant_dir, group_id, participant_id=sender_origin_id, kind="a2a_peer",
                    display_name=label, added_by=f"{_MIRROR_CREATOR_PREFIX}{sender_origin_id}",
                    peer_endpoint_id=sender_origin_id,
                )
            except _store.ChatGroupError:
                return {"status": "error", "reason": "group_not_found"}
            console_audit.action_performed(
                tenant_id=tenant_id, sid_fingerprint=f"a2a:{sender_origin_id}",
                action="chat.group.mirror_created", target_kind="chat_group", target_id=group_id,
            )

        peer_participant = None
        for p in g["participants"]:
            if p["kind"] == "a2a_peer" and p.get("peer_endpoint_id") == sender_origin_id:
                peer_participant = p
                break
        if peer_participant is None:
            return {"status": "error", "reason": "sender_not_a_group_participant"}

        # Live gate, same as the outbound route: a revoked friendship must
        # not let a stale membership keep accepting messages into the group.
        require_friendship_active(sender_origin_id)

        msg = _store.append_message(
            tenant_dir, group_id,
            sender_participant_id=peer_participant["participant_id"],
            text=instruction,
            delivery="remote",
        )
        console_audit.action_performed(
            tenant_id=tenant_id, sid_fingerprint=f"a2a:{sender_origin_id}",
            action="chat.group.message_received_from_peer",
            target_kind="chat_group", target_id=group_id,
        )
        # Hub relay: the instance that OWNS the group forwards a peer's
        # message to the group's other peers, attributed in the text. A
        # mirror never relays, so delivery cannot loop.
        if not str(g.get("created_by", "")).startswith(_MIRROR_CREATOR_PREFIX):
            _schedule_delivery(
                tenant_id=tenant_id, group=g,
                text=f"[{peer_participant.get('display_name') or sender_origin_id}] {instruction}",
                exclude=sender_origin_id,
            )
        return {"status": "accepted", "message_id": msg["id"]}
    except HTTPException as exc:
        return {"status": "error", "reason": f"friendship_check_failed:{exc.status_code}"}
    except Exception as exc:  # noqa: BLE001 — never raise into the receiver
        return {"status": "error", "reason": f"handler_exception:{type(exc).__name__}"}
