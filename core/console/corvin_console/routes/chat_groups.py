"""Group chat routes (ADR-2216).

The first group-conversation surface in the console — see
``chat_group_store.py`` for the storage design rationale (no prior group
model existed, foreign A2A agents are a first-class participant kind from
the start).

Routes (all behind a live session + CSRF on mutation):

  GET    /chat/groups                                 list this tenant's groups
  POST   /chat/groups                                  create a group
  GET    /chat/groups/{id}                              group detail (incl. participants)
  POST   /chat/groups/{id}/participants                add a participant
  DELETE /chat/groups/{id}/participants/{pid}          remove a participant
  GET    /chat/groups/{id}/messages                     list messages
  POST   /chat/groups/{id}/messages                     send a message

Two-layer authorization (ADR-2216 Decision, conceptual level): a group's
OWN participant list is the authorization surface for "who sees this
conversation." The A2A origin/endpoint state is a SEPARATE layer gating
"may this peer talk to us at all" — ``require_friendship_active`` resolves
that live on every admit/send involving an ``a2a_peer`` participant, never
cached, so a revoked friendship immediately stops that peer's group
participation even though the two facts live in different stores.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .. import audit as console_audit
from .. import auth as session_auth
from ..deps import require_session_csrf_on_mutation

_THIS_DIR = Path(__file__).resolve().parent
_BRIDGES_SHARED = _THIS_DIR.parents[3] / "corvin_operator" / "bridges" / "shared"
if str(_BRIDGES_SHARED) not in sys.path:
    sys.path.insert(0, str(_BRIDGES_SHARED))

import paths as _a2a_paths  # type: ignore[import-not-found]

from .. import chat_group_store as _store
from . import a2a_feed as _feed_routes  # reuse _peers() for the friendship check

router = APIRouter(dependencies=[Depends(require_session_csrf_on_mutation)])

Session = Annotated[session_auth.SessionRecord, Depends(require_session_csrf_on_mutation)]

ParticipantKind = Literal["human", "agent", "a2a_peer"]


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

    msg = _store.append_message(
        tenant_dir, group_id,
        sender_participant_id=body.sender_participant_id,
        text=body.text,
        delivery="local",
    )
    console_audit.action_performed(
        tenant_id=rec.tenant_id, sid_fingerprint=rec.sid_fingerprint,
        action="chat.group.message_sent", target_kind="chat_group", target_id=group_id,
    )
    return MessageOut(**msg)
