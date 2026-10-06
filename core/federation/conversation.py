"""Agent-to-agent conversation across installations (ADR-2234, CONCEPT-0097).

One local agent (``LocalAgentRegistry``) and one peer agent (``PeerCatalog``)
take turns on a topic the operator opened. THIS installation moderates: it
owns the turn order, the transcript and the stop button. Nothing new goes on
the wire — every peer turn is an ordinary ADR-2232 delegation (signed
TaskEnvelope, receiver-side gates, both chain anchors, ``parent_task_id`` =
the conversation id so ``delegation.trace`` returns the whole exchange).

The local turn runs through ``a2a_worker.spawn_a2a_worker``, i.e. the same
sanitizer + L34 + L35 + L44 framing an inbound A2A task gets: its prompt
carries the peer agent's words, which are untrusted input here exactly as
they would be on the receiving side.

Observation: ``<tenant>/global/federation/conversations/<id>.jsonl`` —
append-only, one writer (the moderator thread), strictly increasing ``seq``.
That file is the readable transcript; the audit chain carries metadata only
(``federation.conversation_*``: ids, speaker, status, length — never text).
"""
from __future__ import annotations

import json
import os
import re
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable

from core.federation.local_agent import LocalAgentRegistry
from core.federation.peer_catalog import PeerCatalog, agent_address
from core.paths import tenant_home
from core.tenants.validation import validate_tenant_id

_DIR_RELATIVE_PATH = "global/federation/conversations"
_ID_RE = re.compile(r"^[0-9a-f]{32}$")

MAX_TURNS_CAP = 12
MAX_OPENER_CHARS = 2000
MAX_TURN_TEXT_CHARS = 4000        # stored per turn
MAX_TURN_PROMPT_CHARS = 1500      # per earlier turn inside the next prompt
MAX_PROMPT_TRANSCRIPT_CHARS = 9000  # keeps the prompt under the 16 KB A2A cap
MAX_CONVERSATIONS = 50            # per tenant; oldest FINISHED ones are dropped
MAX_RUNNING = 3                   # per process
LOCAL_TURN_TTL_S = 300
PEER_TURN_TIMEOUT_S = 300

# Closed vocabulary — these strings reach the chain, the transcript and the API.
END_REASONS = frozenset({
    "max_turns", "operator_stop", "local_turn_failed", "peer_turn_failed",
    "local_turn_refused", "empty_reply", "audit_failed", "interrupted", "internal_error",
})

# Test seam + deployment hook: the engine at the very end of the local turn.
# None = a2a_worker's own default (ClaudeCodeEngine).
_engine_factory: Callable[[], Any] | None = None

_LIVE: dict[str, threading.Event] = {}
_LIVE_LOCK = threading.Lock()


class ConversationError(ValueError):
    """Invalid request, or the conversation cannot be started."""


def _dir(tenant_id: str) -> Path:
    d = tenant_home(validate_tenant_id(tenant_id)) / _DIR_RELATIVE_PATH
    d.mkdir(parents=True, exist_ok=True, mode=0o700)
    return d


def _path(tenant_id: str, conversation_id: str) -> Path:
    if not isinstance(conversation_id, str) or not _ID_RE.match(conversation_id):
        raise ConversationError("invalid conversation id")
    return _dir(tenant_id) / f"{conversation_id}.jsonl"


def _append(path: Path, record: dict, *, create: bool = False) -> None:
    # Only start() creates the file: a transcript erased mid-run is not
    # re-created by the moderator's next append (it fails and ends the run).
    line = (json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
    flags = os.O_WRONLY | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0) | (os.O_CREAT | os.O_EXCL if create else 0)
    fd = os.open(path, flags, 0o600)
    try:
        os.write(fd, line)
    finally:
        os.close(fd)


def _read(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out: list[dict] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(rec, dict) and isinstance(rec.get("seq"), int):
                out.append(rec)
    return out


def _local_instance_id() -> str:
    from core.federation.delegation import _local_instance_id as _iid
    return _iid()


def _audit(event: str, tenant_id: str, **details: Any) -> None:
    from core.federation import audit as federation_audit
    federation_audit.emit(event, tenant_id=tenant_id, **details)


# ── turn execution ───────────────────────────────────────────────────────

def _shared_path() -> None:
    shared = Path(__file__).resolve().parents[2] / "corvin_operator" / "bridges" / "shared"
    if str(shared) not in sys.path:
        sys.path.insert(0, str(shared))


def _text_of(data: Any) -> str:
    if isinstance(data, dict):
        out = data.get("output")
        if isinstance(out, str):
            return out.strip()
        return json.dumps(data, ensure_ascii=False) if data else ""
    return str(data or "").strip()


def _run_local_turn(*, instruction: str, task_id: str, model: str, origin_id: str) -> dict:
    _shared_path()
    import a2a_worker  # type: ignore[import-not-found]

    try:
        res = a2a_worker.spawn_a2a_worker(
            instruction=instruction, origin_id=origin_id, task_id=task_id,
            persona="assistant", ttl_s=LOCAL_TURN_TTL_S,
            engine_factory=_engine_factory, model=model or None,
        )
    except a2a_worker.InjectionAttempt:
        return {"ok": False, "status": "rejected", "text": "", "duration_ms": 0,
                "error": "local_turn_refused"}
    ok = res.status == "ok"
    return {"ok": ok, "status": res.status, "text": _text_of(res.parsed_output) if ok else "",
            "duration_ms": int(res.duration_ms or 0),
            "error": None if ok else ("local_turn_refused" if res.status == "rejected"
                                      else "local_turn_failed")}


def _run_peer_turn(tenant_id: str, *, instruction: str, task_id: str, endpoint_id: str,
                   agent_id: str, local_agent_id: str, conversation_id: str) -> dict:
    from core.federation.delegation import delegate

    res = delegate(
        tenant_id, instruction=instruction, endpoint_id=endpoint_id, agent_id=agent_id,
        parent_task_id=conversation_id, origin_agent_id=local_agent_id, hop=0,
        task_id=task_id, timeout_s=PEER_TURN_TIMEOUT_S,
    )
    return {"ok": res.ok, "status": res.status, "text": _text_of(res.data) if res.ok else "",
            "duration_ms": res.duration_ms, "error": None if res.ok else "peer_turn_failed"}


def _prompt(meta: dict, turns: list[dict], speaker: str) -> str:
    me = meta["local"] if speaker == "local" else meta["peer"]
    other = meta["peer"] if speaker == "local" else meta["local"]
    lines: list[str] = []
    budget = MAX_PROMPT_TRANSCRIPT_CHARS
    omitted = 0
    for t in reversed(turns[1:]):  # newest first; turns[0] is the opener
        text = t["text"]
        if len(text) > MAX_TURN_PROMPT_CHARS:
            text = text[:MAX_TURN_PROMPT_CHARS] + " […]"
        entry = f"{t['agent_id']}: {text}"
        if len(entry) > budget:
            omitted += 1
            continue
        budget -= len(entry)
        lines.append(entry)
    lines.reverse()
    history = "\n\n".join(lines) if lines else "(nothing yet — you open the exchange)"
    if omitted:
        history = f"({omitted} earlier message(s) omitted)\n\n" + history
    return (
        f'You are agent "{me["agent_id"]}" ({me["address"]}) in a moderated conversation '
        f'with agent "{other["agent_id"]}" ({other["address"]}). The operator chose the topic. '
        f'Write only your next message to {other["agent_id"]}: no preamble, no tool use '
        f"unless the topic needs it, at most about 250 words.\n\n"
        f"Topic from the operator:\n{turns[0]['text']}\n\n"
        f"Conversation so far:\n{history}"
    )


# ── lifecycle ────────────────────────────────────────────────────────────

def _summary(records: list[dict], conversation_id: str) -> dict[str, Any]:
    meta = next((r for r in records if r.get("kind") == "start"), {})
    end = next((r for r in records if r.get("kind") == "end"), None)
    turns = [r for r in records if r.get("kind") == "turn"]
    if end is not None:
        status = end["status"]
    else:
        with _LIVE_LOCK:
            status = "running" if conversation_id in _LIVE else "interrupted"
    return {
        "conversation_id": conversation_id,
        "status": status,
        "reason": (end or {}).get("reason") or (None if status == "running" else "interrupted"),
        "local": meta.get("local"), "peer": meta.get("peer"),
        "max_turns": meta.get("max_turns"), "first_speaker": meta.get("first_speaker"),
        "started_at": meta.get("ts"), "ended_at": (end or {}).get("ts"),
        "turns": len([t for t in turns if t.get("speaker") != "operator"]),
        "topic": turns[0]["text"][:160] if turns else "",
    }


def _prune(tenant_id: str) -> None:
    files = sorted(_dir(tenant_id).glob("*.jsonl"), key=lambda p: p.stat().st_mtime)
    excess = len(files) - MAX_CONVERSATIONS
    for p in files:
        if excess <= 0:
            break
        cid = p.stem
        with _LIVE_LOCK:
            live = cid in _LIVE
        if not live:
            p.unlink(missing_ok=True)
            excess -= 1


def start(
    tenant_id: str, *, local_agent_id: str, endpoint_id: str, peer_agent_id: str,
    opener: str, max_turns: int = 6, first_speaker: str = "local",
) -> dict[str, Any]:
    """Validate, audit-first, write the opener, and start the moderator thread."""
    tenant_id = validate_tenant_id(tenant_id)
    opener = (opener or "").strip()
    if not opener:
        raise ConversationError("opener must not be empty")
    if len(opener) > MAX_OPENER_CHARS:
        raise ConversationError(f"opener longer than {MAX_OPENER_CHARS} characters")
    if isinstance(max_turns, bool) or not isinstance(max_turns, int) or not 1 <= max_turns <= MAX_TURNS_CAP:
        raise ConversationError(f"max_turns must be 1..{MAX_TURNS_CAP}")
    if first_speaker not in ("local", "peer"):
        raise ConversationError("first_speaker must be 'local' or 'peer'")

    local = LocalAgentRegistry(tenant_id).get(local_agent_id)
    if local is None:
        raise ConversationError("unknown local agent")
    if local.engine_type != "claude_code":
        raise ConversationError("only claude_code agents can take part in a conversation")
    peer = next((p for p in PeerCatalog(tenant_id).agents()
                 if p.endpoint_id == endpoint_id and p.agent_id == peer_agent_id), None)
    if peer is None:
        raise ConversationError("peer agent not in a fresh catalog — refresh the peer first")

    with _LIVE_LOCK:
        if len(_LIVE) >= MAX_RUNNING:
            raise ConversationError(f"{MAX_RUNNING} conversations are already running")
        conversation_id = uuid.uuid4().hex
        stop = threading.Event()
        _LIVE[conversation_id] = stop
    try:
        # Audit-first: no chained record → no conversation, nothing sent.
        _audit("federation.conversation_started", tenant_id, conversation_id=conversation_id,
               local_agent_id=local.agent_id, endpoint_id=endpoint_id,
               peer_instance_id=peer.peer_instance_id, peer_agent_id=peer.agent_id,
               max_turns=max_turns)
        meta = {
            "local": {"agent_id": local.agent_id, "model": local.model,
                      "address": agent_address(_local_instance_id(), local.agent_id)},
            "peer": {"agent_id": peer.agent_id, "model": peer.model, "address": peer.address,
                     "endpoint_id": endpoint_id, "peer_instance_id": peer.peer_instance_id},
        }
        path = _path(tenant_id, conversation_id)
        _append(path, {"seq": 0, "kind": "start", "ts": time.time(), "max_turns": max_turns,
                       "first_speaker": first_speaker, **meta}, create=True)
        _append(path, {"seq": 1, "kind": "turn", "ts": time.time(), "speaker": "operator",
                       "agent_id": "operator", "address": None, "task_id": None,
                       "status": "ok", "text": opener, "duration_ms": 0})
    except Exception:
        with _LIVE_LOCK:
            _LIVE.pop(conversation_id, None)
        raise
    _prune(tenant_id)
    threading.Thread(
        target=_moderate, name=f"agent-conversation-{conversation_id[:8]}", daemon=True,
        args=(tenant_id, conversation_id, meta, max_turns, first_speaker, stop),
    ).start()
    return {"conversation_id": conversation_id, "status": "running", **meta}


def _moderate(tenant_id: str, conversation_id: str, meta: dict, max_turns: int,
              first_speaker: str, stop: threading.Event) -> None:
    path = _path(tenant_id, conversation_id)
    seq = 1
    turns = [r for r in _read(path) if r.get("kind") == "turn"]
    speaker = first_speaker
    status, reason = "completed", "max_turns"
    try:
        for _ in range(max_turns):
            if stop.is_set():
                status, reason = "stopped", "operator_stop"
                break
            prompt = _prompt(meta, turns, speaker)
            task_id = str(uuid.uuid4())
            if speaker == "local":
                out = _run_local_turn(instruction=prompt, task_id=task_id,
                                      model=meta["local"]["model"],
                                      origin_id=meta["peer"]["endpoint_id"])
            else:
                out = _run_peer_turn(tenant_id, instruction=prompt, task_id=task_id,
                                     endpoint_id=meta["peer"]["endpoint_id"],
                                     agent_id=meta["peer"]["agent_id"],
                                     local_agent_id=meta["local"]["agent_id"],
                                     conversation_id=conversation_id)
            text = (out["text"] or "")[:MAX_TURN_TEXT_CHARS]
            seq += 1
            rec = {"seq": seq, "kind": "turn", "ts": time.time(), "speaker": speaker,
                   "agent_id": meta[speaker]["agent_id"], "address": meta[speaker]["address"],
                   "task_id": task_id, "status": out["status"] if out["ok"] else "error",
                   "text": text, "duration_ms": out["duration_ms"], "error": out["error"]}
            _audit("federation.conversation_turn", tenant_id, conversation_id=conversation_id,
                   seq=seq, speaker=speaker, agent_id=rec["agent_id"], task_id=task_id,
                   status=rec["status"], duration_ms=rec["duration_ms"], text_chars=len(text))
            _append(path, rec)
            if not out["ok"]:
                status, reason = "failed", out["error"]
                break
            if not text:
                status, reason = "failed", "empty_reply"
                break
            turns.append(rec)
            speaker = "peer" if speaker == "local" else "local"
    except Exception as exc:  # noqa: BLE001 — every exit writes an end record
        from core.federation.audit import FederationAuditError
        status = "failed"
        reason = "audit_failed" if isinstance(exc, FederationAuditError) else "internal_error"
    finally:
        try:
            _append(path, {"seq": seq + 1, "kind": "end", "ts": time.time(),
                           "status": status, "reason": reason, "turns": seq - 1})
            _audit("federation.conversation_ended", tenant_id, conversation_id=conversation_id,
                   status=status, reason=reason, turns=seq - 1)
        except Exception:  # noqa: BLE001 — the transcript reads "interrupted" then
            pass
        with _LIVE_LOCK:
            _LIVE.pop(conversation_id, None)


# ── read / control ───────────────────────────────────────────────────────

def get(tenant_id: str, conversation_id: str, *, after_seq: int = -1) -> dict[str, Any]:
    records = _read(_path(tenant_id, conversation_id))
    if not records:
        raise KeyError(conversation_id)
    out = _summary(records, conversation_id)
    out["messages"] = [r for r in records if r.get("kind") == "turn" and r["seq"] > after_seq]
    out["last_seq"] = records[-1]["seq"]
    return out


def list_conversations(tenant_id: str) -> list[dict[str, Any]]:
    out = []
    for p in _dir(tenant_id).glob("*.jsonl"):
        if _ID_RE.match(p.stem):
            records = _read(p)
            if records:
                out.append(_summary(records, p.stem))
    return sorted(out, key=lambda s: s.get("started_at") or 0, reverse=True)


def stop(tenant_id: str, conversation_id: str) -> bool:
    """Ask a running conversation to stop after the current turn."""
    _path(tenant_id, conversation_id)
    with _LIVE_LOCK:
        ev = _LIVE.get(conversation_id)
    if ev is None:
        return False
    ev.set()
    return True


def delete(tenant_id: str, conversation_id: str) -> bool:
    path = _path(tenant_id, conversation_id)
    with _LIVE_LOCK:
        if conversation_id in _LIVE:
            raise ConversationError("stop the conversation before deleting it")
    if not path.exists():
        return False
    path.unlink()
    return True


def erase_endpoint(tenant_id: str, endpoint_id: str) -> int:
    """Remove every finished transcript with this peer (GDPR Art. 17 hook)."""
    n = 0
    for p in _dir(tenant_id).glob("*.jsonl"):
        records = _read(p)
        meta = next((r for r in records if r.get("kind") == "start"), {})
        if (meta.get("peer") or {}).get("endpoint_id") == endpoint_id:
            with _LIVE_LOCK:
                ev = _LIVE.get(p.stem)
            if ev is not None:
                ev.set()
            p.unlink(missing_ok=True)
            n += 1
    return n
