"""Personal knowledge base console routes (ADR-2325, ADR-2326, ADR-2327; PLAN-0973 section 5.1).

Every route needs a console session; every change needs the CSRF token. Every route except
``status``, ``owner`` (create), ``unlock`` and ``unlock/recovery`` also needs the unlock token: a
random value in an HttpOnly, SameSite=Strict cookie that is never written to disk and is bound to
the session id. The owner is never taken from a request field. Key derivation, store loading and
export run off the event loop. Error bodies are generic where a specific answer would reveal
another owner. Responses carry ``Cache-Control: no-store``.
"""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import threading
import time
from typing import Annotated, Any

from fastapi import APIRouter, Body, Cookie, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from .. import auth as session_auth
from ..deps import require_csrf, require_session, require_session_csrf_on_mutation

COOKIE = "corvin_personal"

router = APIRouter(prefix="/personal", tags=["console-personal-kb"],
                   dependencies=[Depends(require_session_csrf_on_mutation)])

_stores: dict[str, Any] = {}
_stores_lock = threading.Lock()
_export_busy: set[str] = set()


def _no_store(resp: Response) -> None:
    resp.headers["Cache-Control"] = "no-store"


def _set_cookie(resp: Response, token: str, request: Request) -> None:
    # Secure follows the request scheme exactly as the console session cookie does (the token must never travel
    # over plain HTTP where the host also serves HTTPS).
    resp.set_cookie(COOKIE, token, httponly=True, samesite="strict", path="/",
                    secure=request.url.scheme == "https")


def _clear_cookie(resp: Response, request: Request) -> None:
    resp.delete_cookie(COOKIE, path="/", secure=request.url.scheme == "https", httponly=True, samesite="strict")


def _store_for(entry: Any, token: str):
    from core.personal_kb.store import PersonalStore

    key = hashlib.sha256(token.encode()).hexdigest()
    with _stores_lock:
        s = _stores.get(key)
    if s is not None and s.owner_id == entry.owner_id and s.keys.epoch == entry.keys.epoch:
        s.reload_if_changed()
        return s
    s = PersonalStore.open(entry.tenant_id, entry.owner_id, entry.keys)
    with _stores_lock:
        _stores[key] = s
    return s


def _drop_store(token: str | None) -> None:
    if token:
        with _stores_lock:
            _stores.pop(hashlib.sha256(token.encode()).hexdigest(), None)


class Unlocked:
    def __init__(self, rec: Any, entry: Any, token: str) -> None:
        self.rec, self.entry, self.token = rec, entry, token


def _evict_dead() -> None:
    """Drop every cached decrypted store whose unlock is gone (expired, locked, erased elsewhere)."""
    from core.personal_kb import session

    live = session.live_token_hashes()
    with _stores_lock:
        for k in [k for k in _stores if k not in live]:
            _stores.pop(k, None)


def _open_default(rec: Any, request: Request, response: Response) -> tuple[Any, str]:
    """Open the tenant's default space for this console session (no login): created on first use, unwrapped
    with the device key. Returns (entry, token); the token travels in the same httponly cookie as before."""
    from core.personal_kb import erasure, owner, session

    erasure.resume_pending(rec.tenant_id)
    oid, keys = owner.unlock_default(rec.tenant_id, surface="console")
    token = session.put_console(rec.tenant_id, oid, keys, rec.sid)
    _set_cookie(response, token, request)
    entry = session.get_console(token, tenant_id=rec.tenant_id, sid=rec.sid)
    if entry is None:
        raise HTTPException(status_code=423, detail="personal space is not available")
    return entry, token


def entry_or_default(rec: Any, request: Request, response: Response, token: str | None) -> tuple[Any, str]:
    """The open personal space of this console session: the one its cookie names, else the tenant's default
    space (opened here, no login). ``(None, "")`` when it cannot be opened. Used by the readers outside this
    router (Tasks "Only for me", the Knowledge Graph's personal scope), so none of them reports "locked"
    just because the personal panel was not the first thing the user opened."""
    from core.personal_kb import owner, session

    entry = session.get_console(token, tenant_id=rec.tenant_id, sid=rec.sid)
    if entry is not None:
        return entry, token or ""
    try:
        return _open_default(rec, request, response)
    except HTTPException:
        return None, ""
    except (owner.OwnerError, OSError):
        return None, ""


def _unlocked(rec: Annotated[session_auth.SessionRecord, Depends(require_session)], request: Request,
              response: Response, corvin_personal: Annotated[str | None, Cookie()] = None) -> Unlocked:
    from core.personal_kb import owner, session

    entry = session.get_console(corvin_personal, tenant_id=rec.tenant_id, sid=rec.sid)
    _evict_dead()
    if entry is not None:
        return Unlocked(rec, entry, corvin_personal or "")
    try:
        entry, token = _open_default(rec, request, response)
    except HTTPException:
        raise
    except (owner.OwnerError, OSError):
        raise HTTPException(status_code=423, detail="personal space is not available") from None
    return Unlocked(rec, entry, token)


async def _store(u: Unlocked):
    from core.personal_kb.store import Revoked

    try:
        return await asyncio.to_thread(_store_for, u.entry, u.token)
    except Revoked as exc:
        raise HTTPException(status_code=423, detail="personal space is locked") from exc


def _err(exc: Exception) -> HTTPException:
    from core.personal_kb import decoder, limits, model, owner, policy
    from core.personal_kb.store import NotFound, QuotaExceeded, Revoked, VersionConflict

    if isinstance(exc, limits.Busy):
        return HTTPException(status_code=429, detail="busy, try again shortly")
    if isinstance(exc, limits.TooSoon):
        return HTTPException(status_code=429, detail="try again later")
    if isinstance(exc, (owner.UnlockFailed,)):
        # 403, never 401: the console treats 401 as "console session gone" and signs the user out.
        return HTTPException(status_code=403, detail=str(exc))
    if isinstance(exc, (owner.OwnerError, policy.PolicyRefused, decoder.DecoderError, model.ValidationError,
                        QuotaExceeded, ValueError)):
        return HTTPException(status_code=400, detail=str(exc))
    if isinstance(exc, (NotFound, KeyError)):
        return HTTPException(status_code=404, detail="not available")
    if isinstance(exc, VersionConflict):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, Revoked):
        return HTTPException(status_code=423, detail="personal space is locked")
    return HTTPException(status_code=500, detail="not available")


async def _run(fn, *args, **kwargs):
    try:
        return await asyncio.to_thread(fn, *args, **kwargs)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise _err(exc) from exc


# ---- bodies -------------------------------------------------------------------------------------

class CreateBody(BaseModel):
    handle: str = Field(max_length=32)
    passphrase: str = Field(max_length=512)
    consent: bool
    unlock_mode: str = Field(default="convenient", max_length=16)


class UnlockBody(BaseModel):
    handle: str = Field(max_length=32)
    passphrase: str = Field(max_length=512)


class RecoveryBody(BaseModel):
    handle: str = Field(max_length=32)
    recovery_code: str = Field(max_length=128)
    new_passphrase: str = Field(max_length=512)


class PassphraseBody(BaseModel):
    old: str = Field(max_length=512)
    new: str = Field(max_length=512)


class EntityBody(BaseModel):
    type: str = Field(max_length=16)
    title: str = Field(max_length=200)
    body: str = Field(default="", max_length=8000)
    key: str | None = Field(default=None, max_length=80)
    labels: list[str] = Field(default_factory=list, max_length=12)


class EntityPatch(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    body: str | None = Field(default=None, max_length=8000)
    status: str | None = Field(default=None, max_length=16)
    labels: list[str] | None = Field(default=None, max_length=12)
    expected_version: int | None = None


class ReviewBody(BaseModel):
    action: str = Field(max_length=10)
    new_title: str | None = Field(default=None, max_length=200)


class TransitionBody(BaseModel):
    to: str = Field(max_length=16)
    reason: str | None = Field(default=None, max_length=500)
    dod: str | None = Field(default=None, max_length=2000)
    revisit_category: str | None = Field(default=None, max_length=32)


class PromoteBody(BaseModel):
    summary: str = Field(min_length=3, max_length=200)
    body: str = Field(default="", max_length=4000)
    target: str = Field(default="idea", max_length=8)


class ObjectionBody(BaseModel):
    index: int
    action: str = Field(max_length=10)
    reason: str = Field(default="", max_length=300)


class PolicyEdit(BaseModel):
    changes: dict[str, Any]


class EraseBody(BaseModel):
    passphrase: str = Field(default="", max_length=512)   # empty is fine for a space that has no passphrase
    also_chat_history: bool = False


# ---- status, create, unlock ---------------------------------------------------------------------

@router.get("/status")
async def status(response: Response, request: Request,
                 rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
                 corvin_personal: Annotated[str | None, Cookie()] = None) -> dict[str, Any]:
    from core.personal_kb import capabilities, owner, session

    _no_store(response)
    caps = await asyncio.to_thread(capabilities.probe)
    entry = await asyncio.to_thread(session.get_console, corvin_personal, tenant_id=rec.tenant_id, sid=rec.sid)
    token = corvin_personal or ""
    reason = ""
    if entry is None:   # no login: the tenant's default space opens for any authenticated console session
        try:
            entry, token = await asyncio.to_thread(_open_default, rec, request, response)
        except HTTPException:
            reason = "not available"
        except (owner.OwnerError, OSError) as exc:
            reason = str(exc) or "not available"
    out: dict[str, Any] = {"available": True, "secure_device_key": caps.get("secure_files", False),
                           "unlocked": entry is not None}
    if reason:
        out["reason"] = reason
    if entry is not None:
        s = await _store(Unlocked(rec, entry, token))
        out.update({"auto_capture": bool(s.meta.get("auto_capture")), "frozen": bool(s.meta.get("frozen")),
                    "review_pending": len(s.list(review="pending")), "entries": len(s.entries()),
                    "problems": len(s.problems), "policy_pending": bool(s.meta.get("policy_pending")),
                    "bindings": [{"channel": b["channel"], "display": b.get("display", "")}
                                 for b in s.meta.get("bindings", [])],
                    **(lambda i: {"mode": i["unlock_mode"], "has_passphrase": i["has_passphrase"],
                                  "default": i["default"]})(await asyncio.to_thread(_owner_info, entry))})
    return out


def _owner_info(entry: Any) -> dict[str, Any]:
    from core.personal_kb import owner

    return owner.owner_info(entry.tenant_id, entry.owner_id)


@router.post("/owner")
async def create_owner(body: CreateBody, response: Response, request: Request,
                       rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)]) -> dict[str, Any]:
    from core.personal_kb import owner, session

    _no_store(response)
    created = await _run(owner.create_owner, rec.tenant_id, body.handle, body.passphrase, consent=body.consent,
                         unlock_mode=body.unlock_mode)
    token = session.put_console(rec.tenant_id, created.owner_id, created.keys, rec.sid)
    _set_cookie(response, token, request)
    return {"created": True, "recovery_code": created.recovery_code}


@router.post("/unlock")
async def unlock(body: UnlockBody, response: Response, request: Request,
                 rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)]) -> dict[str, Any]:
    from core.personal_kb import erasure, owner, session

    _no_store(response)
    await asyncio.to_thread(erasure.resume_pending, rec.tenant_id)
    oid, keys = await _run(owner.unlock_with_passphrase, rec.tenant_id, body.handle, body.passphrase,
                           session_key=rec.sid, surface="console")
    _set_cookie(response, session.put_console(rec.tenant_id, oid, keys, rec.sid), request)
    return {"unlocked": True}


@router.post("/unlock/recovery")
async def unlock_recovery(body: RecoveryBody, response: Response, request: Request,
                          rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)]) -> dict[str, Any]:
    from core.personal_kb import owner, session

    _no_store(response)
    oid, keys = await _run(owner.unlock_with_recovery, rec.tenant_id, body.handle, body.recovery_code,
                           body.new_passphrase, session_key=rec.sid)
    _set_cookie(response, session.put_console(rec.tenant_id, oid, keys, rec.sid), request)
    return {"unlocked": True}


@router.post("/repair")
async def repair(response: Response, u: Annotated[Unlocked, Depends(_unlocked)],
                 _c: Annotated[Any, Depends(require_csrf)]) -> dict[str, Any]:
    """Remove the entries that are missing or unreadable, then finish an interrupted key change."""
    from core.personal_kb import owner

    _no_store(response)
    s = await _store(u)

    def _do() -> int:
        n = s.drop_unreadable()
        with contextlib.suppress(Exception):
            owner.resume_rekey_if_pending(u.entry.tenant_id, u.entry.owner_id, u.entry.keys)
        return n
    return {"removed": await _run(_do)}


@router.post("/lock")
async def lock(response: Response, request: Request, rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
               corvin_personal: Annotated[str | None, Cookie()] = None) -> dict[str, Any]:
    from core.personal_kb import audit, session

    _no_store(response)
    e = session.drop_console(corvin_personal)
    _drop_store(corvin_personal)
    _clear_cookie(response, request)
    if e is not None:
        await _run(audit.emit, "personal_kb.locked", tenant_id=e.tenant_id, owner_hash=audit.owner_hash(e.owner_id),
                   reason="manual")
    return {"unlocked": False}


@router.post("/passphrase")
async def change_passphrase(body: PassphraseBody, response: Response, u: Annotated[Unlocked, Depends(_unlocked)],
                            _c: Annotated[Any, Depends(require_csrf)]) -> dict[str, Any]:
    from core.personal_kb import owner

    _no_store(response)
    await _run(owner.change_passphrase, u.entry.tenant_id, u.entry.owner_id, body.old, body.new,
               session_key=u.rec.sid)
    return {"changed": True}


@router.post("/rekey")
async def rekey(response: Response, u: Annotated[Unlocked, Depends(_unlocked)],
                _c: Annotated[Any, Depends(require_csrf)], passphrase: str = Body(embed=True, max_length=512)
                ) -> dict[str, Any]:
    from core.personal_kb import owner, session

    _no_store(response)
    tid, oid = u.entry.tenant_id, u.entry.owner_id
    new, code = await _run(owner.rekey, tid, oid, u.entry.keys, passphrase, session_key=u.rec.sid, sweep=False,
                           on_switched=lambda k: session.replace_keys(tid, oid, k))
    _drop_store(u.token)

    def _sweep() -> None:   # re-encryption in the background: the new recovery code reaches the owner at once
        with contextlib.suppress(Exception):
            owner.resume_rekey_if_pending(tid, oid, new)
    # The pre-warmed share pool, not a new thread on the event loop (CLAUDE.md: the loop never starts threads).
    from .. import loop_health  # noqa: PLC0415
    loop_health.share_pool().submit(_sweep)
    return {"rekeyed": True, "recovery_code": code}


# ---- entities -----------------------------------------------------------------------------------

@router.get("/entities")
async def list_entities(response: Response, u: Annotated[Unlocked, Depends(_unlocked)], type: str | None = None,
                        q: str | None = None, review: str | None = None) -> dict[str, Any]:
    from core.personal_kb import views

    _no_store(response)
    s = await _store(u)
    items = [views.entity_public(e) for e in s.list(type=type, q=q, review=review)]
    return {"entities": items, "problems": [{"uid": k, "problem": v} for k, v in s.problems.items()]}


@router.post("/entities", status_code=201)
async def create_entity(body: EntityBody, response: Response, u: Annotated[Unlocked, Depends(_unlocked)],
                        _c: Annotated[Any, Depends(require_csrf)]) -> dict[str, Any]:
    from core.personal_kb import model, views

    _no_store(response)
    s = await _store(u)

    def _do():
        e = model.new_entity(body.type, body.title, body=body.body, origin="user", key=body.key, labels=body.labels)
        return s.put(e, manual=True)
    return views.entity_public(await _run(_do))


@router.get("/entities/{uid}")
async def get_entity(uid: str, response: Response, u: Annotated[Unlocked, Depends(_unlocked)]) -> dict[str, Any]:
    from core.personal_kb import views

    _no_store(response)
    s = await _store(u)
    e = s.get(uid)
    if e is None:
        raise HTTPException(status_code=404, detail="not available")
    return {**views.entity_public(e), "versions": s.versions(uid)}


@router.patch("/entities/{uid}")
async def patch_entity(uid: str, body: EntityPatch, response: Response, u: Annotated[Unlocked, Depends(_unlocked)],
                       _c: Annotated[Any, Depends(require_csrf)]) -> dict[str, Any]:
    from core.personal_kb import capture, views

    _no_store(response)
    s = await _store(u)

    def _do():
        e = s.get(uid)
        if e is None:
            raise KeyError(uid)
        if e["type"] == "task" and body.status is not None and body.status != e["status"]:
            raise ValueError("change a task's state with its transition, not by editing it")
        upd = dict(e)
        for f in ("title", "body", "status", "labels"):
            v = getattr(body, f)
            if v is not None:
                upd[f] = v
        if e.get("origin") in ("auto", "derived"):
            upd["origin"], upd["review"], upd["expires_at"] = "confirmed", "accepted", None
        out = s.put(upd, expected_version=body.expected_version if body.expected_version is not None else e["version"])
        if upd.get("title") != e.get("title") or upd.get("body") != e.get("body"):
            capture.note_owner_action(s, e, "edit")
        return out
    return views.entity_public(await _run(_do))


@router.delete("/entities/{uid}")
async def forget_entity(uid: str, response: Response, u: Annotated[Unlocked, Depends(_unlocked)],
                        _c: Annotated[Any, Depends(require_csrf)]) -> dict[str, Any]:
    from core.personal_kb import capture

    _no_store(response)
    s = await _store(u)

    def _do():
        e = s.get(uid)
        if e is None:
            raise KeyError(uid)
        s.forget(uid, fingerprint=(f"{e.get('key')} {e['title']}" if e.get("key") else e["title"]))
        capture.note_owner_action(s, e, "forget")
    await _run(_do)
    return {"forgotten": True}


@router.post("/entities/{uid}/restore")
async def restore_entity(uid: str, response: Response, u: Annotated[Unlocked, Depends(_unlocked)],
                         _c: Annotated[Any, Depends(require_csrf)], version: int = Body(embed=True)) -> dict[str, Any]:
    from core.personal_kb import views

    _no_store(response)
    s = await _store(u)
    return views.entity_public(await _run(s.restore, uid, version))


@router.post("/entities/{uid}/review")
async def review_entity(uid: str, body: ReviewBody, response: Response, u: Annotated[Unlocked, Depends(_unlocked)],
                        _c: Annotated[Any, Depends(require_csrf)]) -> dict[str, Any]:
    from core.personal_kb import capture, views

    _no_store(response)
    s = await _store(u)
    action = {"reject": "forget"}.get(body.action, body.action)
    out = await _run(capture.review, s, uid, action, new_title=body.new_title)
    return {"entity": views.entity_public(out) if out else None}


@router.post("/entities/{uid}/promote")
async def promote_entity(uid: str, body: PromoteBody, response: Response, u: Annotated[Unlocked, Depends(_unlocked)],
                         _c: Annotated[Any, Depends(require_csrf)]) -> dict[str, Any]:
    _no_store(response)
    s = await _store(u)
    if body.target not in ("idea", "plan"):
        raise HTTPException(status_code=400, detail="target must be idea or plan")
    lk = _PROMOTE_LOCKS.setdefault((s.tenant_id, s.owner_id, uid), asyncio.Lock())
    if lk.locked():
        raise HTTPException(status_code=409, detail="this idea is being promoted")
    async with lk:
        try:
            return await _promote_locked(s, u, uid, body)
        finally:
            _PROMOTE_LOCKS.pop((s.tenant_id, s.owner_id, uid), None)


_PROMOTE_LOCKS: dict[tuple[str, str, str], asyncio.Lock] = {}


async def _promote_locked(s: Any, u: Any, uid: str, body: Any) -> dict[str, Any]:
    """One promotion per idea at a time in this process: a double click must not create two project entries."""
    from core.personal_kb import audit
    from core.personal_kb.store import VersionConflict

    from .. import kb_projection

    e = s.get(uid)
    if e is None or e["type"] != "idea":
        raise HTTPException(status_code=404, detail="not available")
    if e["status"] == "promoted" or e.get("promoted_to"):
        raise HTTPException(status_code=409, detail="this idea was already promoted")
    try:
        res = await asyncio.to_thread(kb_projection.create_entity, u.rec.tenant_id, body.target, body.summary, body.body)
    except Exception as exc:  # noqa: BLE001 - no bound KB, a refused entry: the owner gets a reason, never a 500
        raise HTTPException(status_code=400, detail=str(exc)[:200] or "the project knowledge base refused it") from exc
    project_uid = res.get("uid")

    def _mark():
        audit.emit("personal_kb.promoted", tenant_id=s.tenant_id, owner_hash=s.owner_hash, uid=uid,
                   project_uid=project_uid)
        for _attempt in range(3):   # another process may touch the idea meanwhile: re-read, never overwrite
            cur = s.get(uid)
            if cur is None:
                raise KeyError(uid)
            try:
                return s.put(dict(cur, status="promoted", promoted_to=project_uid,
                                  links=cur["links"] + [{"rel": "promoted_to", "to": f"kb:{project_uid}"}]),
                             expected_version=cur["version"])
            except VersionConflict:
                s.reload_if_changed()
        raise VersionConflict("the idea keeps changing")
    await _run(_mark)
    return {"promoted": True, "project_id": res.get("id"), "project_uid": project_uid}


# ---- tasks and decoder --------------------------------------------------------------------------

@router.post("/tasks/{uid}/transition")
async def transition_task(uid: str, body: TransitionBody, response: Response,
                          u: Annotated[Unlocked, Depends(_unlocked)], _c: Annotated[Any, Depends(require_csrf)]
                          ) -> dict[str, Any]:
    from core.personal_kb import decoder, model, views

    _no_store(response)
    s = await _store(u)

    def _do():
        e = s.get(uid)
        if e is None or e["type"] != "task":
            raise KeyError(uid)
        out = s.put(model.transition(e, body.to, reason=body.reason, dod=body.dod), expected_version=e["version"])
        idea = next((ln["to"] for ln in out.get("links", []) if ln["rel"] == "derived_from"), None)
        if idea:
            decoder.refresh_done(s, idea)
        return out
    return views.entity_public(await _run(_do))


@router.get("/ideas/{uid}/decoder")
async def decoder_state(uid: str, response: Response, u: Annotated[Unlocked, Depends(_unlocked)]) -> dict[str, Any]:
    from core.personal_kb import decoder, views

    _no_store(response)
    s = await _store(u)
    e = s.get(uid)
    if e is None or e["type"] != "idea":
        raise HTTPException(status_code=404, detail="not available")
    st = views.entity_public(e).get("decoder") or {"stage": "seed", "loss": 0.0, "iterations": 0, "gates": {},
                                                   "objections": [], "children": {}}
    return {"idea": views.entity_public(e), "decoder": st, "next_stage": decoder.next_stage(e)}


def _result(r: Any) -> dict[str, Any]:
    return {"stage": r.stage, "gate_ok": r.gate_ok, "failures": r.failures, "loss": r.loss,
            "objections": r.objections, "created": r.created, "duplicates": r.duplicates}


@router.post("/ideas/{uid}/unfold")
async def unfold(uid: str, response: Response, u: Annotated[Unlocked, Depends(_unlocked)],
                 _c: Annotated[Any, Depends(require_csrf)], stage: str | None = Body(default=None, embed=True)
                 ) -> dict[str, Any]:
    from core.personal_kb import decoder

    _no_store(response)
    s = await _store(u)
    return _result(await _run(decoder.unfold, s, uid, stage=stage))


@router.post("/ideas/{uid}/decoder/objection")
async def objection(uid: str, body: ObjectionBody, response: Response, u: Annotated[Unlocked, Depends(_unlocked)],
                    _c: Annotated[Any, Depends(require_csrf)]) -> dict[str, Any]:
    from core.personal_kb import decoder

    _no_store(response)
    s = await _store(u)
    r = await _run(decoder.resolve_objection, s, uid, body.index, body.action, reason=body.reason)
    return _result(r) if r is not None else {"dismissed": True}


@router.post("/ideas/{uid}/decoder/revisit")
async def revisit(uid: str, response: Response, u: Annotated[Unlocked, Depends(_unlocked)],
                  _c: Annotated[Any, Depends(require_csrf)], task_uid: str = Body(embed=True, max_length=40),
                  category: str = Body(embed=True, max_length=32)) -> dict[str, Any]:
    from core.personal_kb import decoder

    _no_store(response)
    s = await _store(u)
    r = await _run(decoder.revisit, s, task_uid, category)
    return _result(r) if r is not None else {"revisit": None}


# ---- feed and policy ----------------------------------------------------------------------------

@router.get("/feed")
async def feed(response: Response, u: Annotated[Unlocked, Depends(_unlocked)]) -> dict[str, Any]:
    from core.personal_kb import views

    _no_store(response)
    s = await _store(u)
    items = sorted((e for e in s.list() if e.get("review") == "pending"), key=lambda e: -float(e.get("updated", 0)))
    return {"entries": [views.entity_public(e) for e in items[:200]]}


@router.get("/policy")
async def get_policy(response: Response, u: Annotated[Unlocked, Depends(_unlocked)]) -> dict[str, Any]:
    from core.personal_kb import policy

    _no_store(response)
    s = await _store(u)
    pend = s.meta.get("policy_pending")
    return {"current": await _run(policy.current, s), "version": policy.current_version(s),
            "versions": int(s.manifest.get("policy_versions", 0)), "frozen": bool(s.meta.get("frozen")),
            "pending": ({"change_id": pend["change_id"], "target": pend["target"], "cause": pend.get("cause")}
                        if pend else None),
            "allowlist": {k: {"kind": v.kind, "default": v.default, "min": v.lo, "max": v.hi,
                              "choices": list(v.choices)} for k, v in policy.ALLOWLIST.items()}}


@router.put("/policy")
async def edit_policy(body: PolicyEdit, response: Response, u: Annotated[Unlocked, Depends(_unlocked)],
                      _c: Annotated[Any, Depends(require_csrf)]) -> dict[str, Any]:
    from core.personal_kb import policy

    _no_store(response)
    s = await _store(u)
    d = await _run(policy.owner_edit, s, body.changes)
    return {"outcome": d.outcome, "version": d.version}


@router.post("/policy/rollback")
async def rollback_policy(response: Response, u: Annotated[Unlocked, Depends(_unlocked)],
                          _c: Annotated[Any, Depends(require_csrf)], to_version: int = Body(embed=True)
                          ) -> dict[str, Any]:
    from core.personal_kb import policy

    _no_store(response)
    s = await _store(u)
    d = await _run(policy.rollback, s, to_version, automatic=False)
    return {"outcome": d.outcome, "version": d.version}


@router.post("/policy/confirm")
async def confirm_policy(response: Response, u: Annotated[Unlocked, Depends(_unlocked)],
                         _c: Annotated[Any, Depends(require_csrf)], change_id: str = Body(embed=True, max_length=32),
                         accept: bool = Body(default=True, embed=True)) -> dict[str, Any]:
    from core.personal_kb import policy

    _no_store(response)
    s = await _store(u)
    if not accept:
        await _run(policy.dismiss, s, change_id)
        return {"outcome": "dismissed"}
    d = await _run(policy.confirm, s, change_id)
    return {"outcome": d.outcome, "version": d.version}


@router.post("/policy/unfreeze")
async def unfreeze_policy(response: Response, u: Annotated[Unlocked, Depends(_unlocked)],
                          _c: Annotated[Any, Depends(require_csrf)]) -> dict[str, Any]:
    from core.personal_kb import policy

    _no_store(response)
    s = await _store(u)
    await _run(policy.unfreeze, s)
    return {"frozen": False}


@router.post("/settings")
async def settings(response: Response, u: Annotated[Unlocked, Depends(_unlocked)],
                   _c: Annotated[Any, Depends(require_csrf)], auto_capture: bool = Body(embed=True)) -> dict[str, Any]:
    from core.personal_kb import owner

    _no_store(response)
    s = await _store(u)

    def _set(m: dict[str, Any]) -> None:
        m["auto_capture"] = bool(auto_capture)
        # Switching learning on is the consent act (a default space starts with none recorded).
        if auto_capture and (m.get("consent") or {}).get("version") != owner.CONSENT_VERSION:
            m["consent"] = {"version": owner.CONSENT_VERSION, "at": time.time()}
    await _run(s.update_meta, _set)
    return {"auto_capture": bool(auto_capture)}


# ---- pairing ------------------------------------------------------------------------------------

@router.post("/pair")
async def issue_pair(response: Response, u: Annotated[Unlocked, Depends(_unlocked)],
                     _c: Annotated[Any, Depends(require_csrf)]) -> dict[str, Any]:
    from core.personal_kb import owner

    _no_store(response)
    code = await _run(owner.issue_pair_code, u.entry.tenant_id, u.entry.owner_id)
    return {"code": code, "expires_in_s": owner.PAIR_TTL_S}


@router.get("/pair/pending")
async def pending_pairs(response: Response, u: Annotated[Unlocked, Depends(_unlocked)]) -> dict[str, Any]:
    from core.personal_kb import owner

    _no_store(response)
    s = await _store(u)
    return {"pending": await _run(owner.pending_pairs, u.entry.tenant_id, s)}


@router.post("/pair/confirm")
async def confirm_pair(response: Response, u: Annotated[Unlocked, Depends(_unlocked)],
                       _c: Annotated[Any, Depends(require_csrf)], pair_id: str = Body(embed=True, max_length=32)
                       ) -> dict[str, Any]:
    from core.personal_kb import migrate_user_model, owner

    _no_store(response)
    s = await _store(u)
    res = await _run(owner.confirm_pair, u.entry.tenant_id, s, pair_id)
    chat_key = res.pop("_chat_key", "")
    migrated = 0
    if chat_key:
        migrated = await _run(migrate_user_model.migrate, s, channel=res["channel"], chat_key=chat_key)
    return {"linked": res, "migrated": migrated}


@router.post("/identity/unbind")
async def unbind(response: Response, u: Annotated[Unlocked, Depends(_unlocked)],
                 _c: Annotated[Any, Depends(require_csrf)], index: int = Body(embed=True)) -> dict[str, Any]:
    from core.personal_kb import owner, session

    _no_store(response)
    s = await _store(u)
    binds = s.meta.get("bindings", [])
    if not (0 <= index < len(binds)):
        raise HTTPException(status_code=404, detail="not available")
    b = binds[index]
    ok = await _run(owner.unbind, u.entry.tenant_id, s, b["channel"], b["uid"])
    session.drop_owner_bridge(u.entry.tenant_id, u.entry.owner_id)
    return {"unbound": bool(ok)}


# ---- export and erase ---------------------------------------------------------------------------

@router.get("/export")
async def export(u: Annotated[Unlocked, Depends(_unlocked)]) -> Response:
    key = u.entry.owner_id
    if key in _export_busy:
        raise HTTPException(status_code=429, detail="an export is already running")
    _export_busy.add(key)
    try:
        s = await _store(u)
        data = await _run(s.export_bytes)
    finally:
        _export_busy.discard(key)
    return Response(content=data, media_type="application/zip",
                    headers={"Cache-Control": "no-store",
                             "Content-Disposition": 'attachment; filename="personal-knowledge.zip"'})


@router.post("/erase")
async def erase(body: EraseBody, response: Response, request: Request, u: Annotated[Unlocked, Depends(_unlocked)],
                _c: Annotated[Any, Depends(require_csrf)]) -> dict[str, Any]:
    from core.personal_kb import erasure, owner, session

    _no_store(response)
    info = await _run(owner.owner_info, u.entry.tenant_id, u.entry.owner_id)
    if info["has_passphrase"]:   # a space without a passphrase is erased by its open, CSRF-checked console session
        handle = (await _run(owner.read_owner_handle, u.entry.tenant_id, u.entry.owner_id))
        await _run(owner.unlock_with_passphrase, u.entry.tenant_id, handle, body.passphrase,
                   session_key=u.rec.sid, surface="console")
    s = await _store(u)
    bindings = list(s.meta.get("bindings", []))
    # The space first: the history purge runs the whole Art. 17 chain, whose personal-KB handler would
    # otherwise erase the space a second time and leave this call counting 0 entries.
    n = await _run(erasure.erase_owner, u.entry.tenant_id, u.entry.owner_id)
    purged = 0
    if body.also_chat_history:
        purged = await _run(_purge_history, u.rec.tenant_id, bindings)
    session.drop_console(u.token)
    _drop_store(u.token)
    _clear_cookie(response, request)
    return {"erased": True, "entries": n, "chat_histories": purged}


def _purge_history(tenant_id: str, bindings: list[dict[str, str]]) -> int:
    """One orchestrator request per bound identity, through the console's real erasure wiring
    (recorded with that chat id, as every erasure request is; the owner was told so)."""
    from ..chat_router import _execute_erasure

    import re  # noqa: PLC0415

    n = 0
    for b in bindings:
        uid = str(b.get("uid") or "")
        if b.get("channel") == "teams":
            # bound to the AAD object id, while a Teams chat history is keyed by the UPN: nothing to name here
            continue
        # The orchestrator accepts [A-Za-z0-9.:_-] only; a uid outside it (Signal "+49...", Teams UPN) cannot
        # be named there, so its history is left for an operator request and the space is still erased.
        if not uid or not re.fullmatch(r"[A-Za-z0-9.:_\-]{1,128}", uid):
            continue
        try:
            res = _execute_erasure(tenant_id, uid)
            # counted only when every layer applied or skipped: a partial run is not a purged history
            if str(getattr(getattr(res, "overall_status", None), "value", "")) == "completed":
                n += 1
        except Exception:  # noqa: BLE001 - one failing history purge never stops the erasure of the space
            continue
    return n
