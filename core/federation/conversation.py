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
    "ask",  # /ask @mine (ADR-2235 Phase 2): the one-shot turn finished normally
})

# Test seam + deployment hook: the engine at the very end of the local turn.
# None = a2a_worker's own default (ClaudeCodeEngine).
_engine_factory: Callable[[], Any] | None = None

_LIVE: dict[str, threading.Event] = {}
_LIVE_LOCK = threading.Lock()

# Operator participation (agent_conversations plugin, CONCEPT-0001). The transcript keeps ONE
# writer — the moderator thread. Everything the operator does while a conversation runs is a
# *request* queued here under _LIVE_LOCK; the moderator drains it at a turn boundary and writes.
MAX_INTERJECTION_CHARS = 1000
MAX_PENDING_REQUESTS = 5
MAX_ROLE_NOTE_CHARS = 500
DEFAULT_SETTINGS: dict[str, Any] = {
    "max_words": 250, "pace_s": 0, "role_notes": {"local": "", "peer": ""},
}
_REQUESTS: dict[str, list[dict[str, Any]]] = {}
_PAUSED: set[str] = set()
_SETTINGS: dict[str, dict[str, Any]] = {}


class ConversationError(ValueError):
    """Invalid request, or the conversation cannot be started."""


def normalize_settings(raw: Any, *, base: dict[str, Any] | None = None) -> dict[str, Any]:
    """Validate operator-supplied conversation settings (fail-closed: unknown key = error)."""
    out = json.loads(json.dumps(base if base is not None else DEFAULT_SETTINGS))
    if raw is None:
        return out
    if not isinstance(raw, dict):
        raise ConversationError("settings must be an object")
    unknown = set(raw) - set(DEFAULT_SETTINGS)
    if unknown:
        raise ConversationError(f"unknown setting: {sorted(unknown)[0]}")
    for key, lo, hi in (("max_words", 50, 600), ("pace_s", 0, 60)):
        if key in raw:
            v = raw[key]
            if isinstance(v, bool) or not isinstance(v, int) or not lo <= v <= hi:
                raise ConversationError(f"{key} must be an integer {lo}..{hi}")
            out[key] = v
    if "role_notes" in raw:
        notes = raw["role_notes"]
        if not isinstance(notes, dict) or set(notes) - {"local", "peer"}:
            raise ConversationError("role_notes must be an object with local/peer")
        for side, text in notes.items():
            if not isinstance(text, str) or len(text) > MAX_ROLE_NOTE_CHARS:
                raise ConversationError(f"role_notes.{side} longer than {MAX_ROLE_NOTE_CHARS} characters")
            out["role_notes"][side] = text.strip()
    return out


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
        # ADR-2235 point 2: this prompt is generated by the moderator/framing
        # code, not typed by the operator — label it so the peer-thread view
        # never renders it as "You".
        thread_ref={"kind": "conversation", "id": conversation_id,
                   "author_role": "local_agent", "agent_id": local_agent_id},
    )
    return {"ok": res.ok, "status": res.status, "text": _text_of(res.data) if res.ok else "",
            "duration_ms": res.duration_ms, "error": None if res.ok else "peer_turn_failed"}


def _prompt(meta: dict, turns: list[dict], speaker: str, settings: dict | None = None) -> str:
    cfg = settings or DEFAULT_SETTINGS
    me = meta["local"] if speaker == "local" else meta["peer"]
    other = meta["peer"] if speaker == "local" else meta["local"]
    lines: list[str] = []
    budget = MAX_PROMPT_TRANSCRIPT_CHARS
    omitted = 0
    for t in reversed(turns[1:]):  # newest first; turns[0] is the opener
        text = t["text"]
        # An interjection addressed to the other agent is not shown to this one.
        if t.get("speaker") == "operator" and t.get("target") not in (None, speaker):
            text = "(the operator spoke privately to the other agent)"
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
    note = (cfg.get("role_notes") or {}).get(speaker, "")
    note_block = f"\n\nOperator's instruction for you: {note}" if note else ""
    return (
        f'You are agent "{me["agent_id"]}" ({me["address"]}) in a moderated conversation '
        f'with agent "{other["agent_id"]}" ({other["address"]}). The operator chose the topic '
        f'and may join in; lines from "operator" are instructions from the human running this '
        f"conversation. Write only your next message to {other['agent_id']}: no preamble, no "
        f"tool use unless the topic needs it, at most about {int(cfg.get('max_words', 250))} "
        f"words.{note_block}\n\n"
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
        "ask": bool(meta.get("ask", False)),
        # The start record's settings, then every later `settings` event (live changes).
        "settings": next((r["settings"] for r in reversed(records)
                          if r.get("kind") == "event" and r.get("event") == "settings"),
                         normalize_settings(meta.get("settings"))),
        "paused": conversation_id in _PAUSED and status == "running",
        "pending": len(_REQUESTS.get(conversation_id, ())) if status == "running" else 0,
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


def _forget(conversation_id: str) -> None:
    with _LIVE_LOCK:
        _LIVE.pop(conversation_id, None)
        _REQUESTS.pop(conversation_id, None)
        _SETTINGS.pop(conversation_id, None)
        _PAUSED.discard(conversation_id)


def start(
    tenant_id: str, *, local_agent_id: str, endpoint_id: str, peer_agent_id: str,
    opener: str, max_turns: int = 6, first_speaker: str = "local",
    settings: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate, audit-first, write the opener, and start the moderator thread."""
    tenant_id = validate_tenant_id(tenant_id)
    settings = normalize_settings(settings)
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
        _REQUESTS[conversation_id] = []
        _SETTINGS[conversation_id] = settings
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
                       "first_speaker": first_speaker, "settings": settings, **meta}, create=True)
        _append(path, {"seq": 1, "kind": "turn", "ts": time.time(), "speaker": "operator",
                       "agent_id": "operator", "address": None, "task_id": None,
                       "status": "ok", "text": opener, "duration_ms": 0})
    except Exception:
        _forget(conversation_id)
        raise
    _prune(tenant_id)
    threading.Thread(
        target=_moderate, name=f"agent-conversation-{conversation_id[:8]}", daemon=True,
        args=(tenant_id, conversation_id, meta, max_turns, first_speaker, stop),
    ).start()
    return {"conversation_id": conversation_id, "status": "running", **meta}


def ask_mine(
    tenant_id: str, *, local_agent_id: str, endpoint_id: str, instruction: str,
) -> dict[str, Any]:
    """One-shot `/ask @mine` from a peer thread (ADR-2235 Phase 2): a single
    local turn through the same gates a conversation turn gets
    (``a2a_worker.spawn_a2a_worker`` — sanitizer, L34, L35, L44), never sent
    to the peer. Stored as a one-turn record (``ask: True`` in the start
    meta) in this same conversation store, so the existing transcript reader
    and ``erase_endpoint`` cover it without a second store.
    """
    tenant_id = validate_tenant_id(tenant_id)
    instruction = (instruction or "").strip()
    if not instruction:
        raise ConversationError("instruction must not be empty")
    if len(instruction) > MAX_OPENER_CHARS:
        raise ConversationError(f"instruction longer than {MAX_OPENER_CHARS} characters")

    local = LocalAgentRegistry(tenant_id).get(local_agent_id)
    if local is None:
        raise ConversationError("unknown local agent")
    if local.engine_type != "claude_code":
        raise ConversationError("only claude_code agents can answer /ask @mine")

    conversation_id = uuid.uuid4().hex
    meta = {
        "ask": True,
        "local": {"agent_id": local.agent_id, "model": local.model,
                  "address": agent_address(_local_instance_id(), local.agent_id)},
        "peer": {"agent_id": None, "model": None, "address": None,
                 "endpoint_id": endpoint_id, "peer_instance_id": None},
    }
    path = _path(tenant_id, conversation_id)
    _append(path, {"seq": 0, "kind": "start", "ts": time.time(), "max_turns": 1,
                   "first_speaker": "local", **meta}, create=True)
    task_id = str(uuid.uuid4())
    out = _run_local_turn(instruction=instruction, task_id=task_id,
                          model=local.model, origin_id=endpoint_id)
    text = (out["text"] or "")[:MAX_TURN_TEXT_CHARS]
    status = "completed" if (out["ok"] and text) else "failed"
    reason = "ask" if status == "completed" else (out["error"] or "empty_reply")
    # Audit before persisting the turn text (same order _moderate uses): a
    # chain-write failure here leaves the file with only its start record —
    # it reads as "interrupted" and is pruned like any other, the answer is
    # never shown to the operator unaudited.
    _audit("federation.local_ask", tenant_id, conversation_id=conversation_id,
          agent_id=local.agent_id, endpoint_id=endpoint_id, status=status,
          duration_ms=out["duration_ms"], task_id=task_id)
    _append(path, {"seq": 1, "kind": "turn", "ts": time.time(), "speaker": "local",
                   "agent_id": local.agent_id, "address": meta["local"]["address"],
                   "task_id": task_id, "status": out["status"] if out["ok"] else "error",
                   "text": text, "duration_ms": out["duration_ms"], "error": out["error"]})
    _append(path, {"seq": 2, "kind": "end", "ts": time.time(), "status": status,
                   "reason": reason, "turns": 1})
    _prune(tenant_id)
    return {"conversation_id": conversation_id, "status": status, "reason": reason,
            "agent_id": local.agent_id, "text": text, "task_id": task_id,
            "duration_ms": out["duration_ms"]}


def _drain(tenant_id: str, conversation_id: str, path: Path, seq: int,
           turns: list[dict]) -> int:
    """Write every queued operator request (moderator thread only — the one writer)."""
    with _LIVE_LOCK:
        batch, _REQUESTS[conversation_id] = _REQUESTS.get(conversation_id, []), []
    for req in batch:
        seq += 1
        if req["type"] == "say":
            target = req["target"]
            rec = {"seq": seq, "kind": "turn", "ts": time.time(), "speaker": "operator",
                   "agent_id": "operator", "address": None, "task_id": None, "status": "ok",
                   "text": req["text"], "duration_ms": 0, "target": target}
            _audit("federation.conversation_operator_message", tenant_id,
                   conversation_id=conversation_id, seq=seq, target=target or "both",
                   text_chars=len(req["text"]))
            turns.append(rec)
        else:  # settings
            with _LIVE_LOCK:
                merged = normalize_settings(req["changes"], base=_SETTINGS.get(conversation_id))
                _SETTINGS[conversation_id] = merged
            rec = {"seq": seq, "kind": "event", "ts": time.time(), "event": "settings",
                   "settings": merged}
            _audit("federation.conversation_settings_changed", tenant_id,
                   conversation_id=conversation_id, seq=seq,
                   changed=",".join(sorted(req["changes"])))
        _append(path, rec)
    return seq


def _wait_boundary(tenant_id: str, conversation_id: str, path: Path, seq: int,
                   turns: list[dict], stop: threading.Event, pace_s: int) -> int:
    """Between turns: honour pause and the pacing window; stay responsive to stop and to requests."""
    deadline = time.monotonic() + pace_s
    announced = False
    while not stop.is_set():
        with _LIVE_LOCK:
            paused = conversation_id in _PAUSED
            pending = bool(_REQUESTS.get(conversation_id))
        if pending:
            seq = _drain(tenant_id, conversation_id, path, seq, turns)
            continue
        if paused and not announced:
            seq += 1
            _audit("federation.conversation_paused", tenant_id, conversation_id=conversation_id, seq=seq)
            _append(path, {"seq": seq, "kind": "event", "ts": time.time(), "event": "paused"})
            announced = True
        if not paused:
            if announced:
                seq += 1
                _audit("federation.conversation_resumed", tenant_id,
                       conversation_id=conversation_id, seq=seq)
                _append(path, {"seq": seq, "kind": "event", "ts": time.time(), "event": "resumed"})
            if time.monotonic() >= deadline:
                break
        stop.wait(0.2)
    return seq


def _moderate(tenant_id: str, conversation_id: str, meta: dict, max_turns: int,
              first_speaker: str, stop: threading.Event) -> None:
    path = _path(tenant_id, conversation_id)
    seq = 1
    agent_turns = 0
    turns = [r for r in _read(path) if r.get("kind") == "turn"]
    speaker = first_speaker
    status, reason = "completed", "max_turns"
    try:
        while agent_turns < max_turns:
            if stop.is_set():
                status, reason = "stopped", "operator_stop"
                break
            with _LIVE_LOCK:
                cfg = dict(_SETTINGS.get(conversation_id) or DEFAULT_SETTINGS)
            if agent_turns:
                seq = _wait_boundary(tenant_id, conversation_id, path, seq, turns, stop,
                                     int(cfg.get("pace_s", 0)))
            else:
                seq = _drain(tenant_id, conversation_id, path, seq, turns)
            if stop.is_set():
                status, reason = "stopped", "operator_stop"
                break
            with _LIVE_LOCK:
                cfg = dict(_SETTINGS.get(conversation_id) or DEFAULT_SETTINGS)
            prompt = _prompt(meta, turns, speaker, cfg)
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
            agent_turns += 1
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
            # A message queued after the last turn is still written (unanswered) — never dropped.
            seq = _drain(tenant_id, conversation_id, path, seq, turns)
            _append(path, {"seq": seq + 1, "kind": "end", "ts": time.time(),
                           "status": status, "reason": reason, "turns": agent_turns})
            _audit("federation.conversation_ended", tenant_id, conversation_id=conversation_id,
                   status=status, reason=reason, turns=agent_turns)
        except Exception:  # noqa: BLE001 — the transcript reads "interrupted" then
            pass
        _forget(conversation_id)


# ── read / control ───────────────────────────────────────────────────────

def get(tenant_id: str, conversation_id: str, *, after_seq: int = -1) -> dict[str, Any]:
    records = _read(_path(tenant_id, conversation_id))
    if not records:
        raise KeyError(conversation_id)
    out = _summary(records, conversation_id)
    out["messages"] = [r for r in records if r.get("kind") == "turn" and r["seq"] > after_seq]
    out["events"] = [r for r in records if r.get("kind") == "event" and r["seq"] > after_seq]
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


def _live_or_raise(tenant_id: str, conversation_id: str) -> None:
    _path(tenant_id, conversation_id)
    with _LIVE_LOCK:
        if conversation_id not in _LIVE:
            raise ConversationError("conversation is not running")


def post(tenant_id: str, conversation_id: str, text: str, target: str | None = None) -> dict[str, Any]:
    """Queue an operator interjection; the moderator writes it before the next turn."""
    text = (text or "").strip()
    if not text:
        raise ConversationError("message must not be empty")
    if len(text) > MAX_INTERJECTION_CHARS:
        raise ConversationError(f"message longer than {MAX_INTERJECTION_CHARS} characters")
    if target not in (None, "local", "peer"):
        raise ConversationError("target must be 'local', 'peer' or omitted")
    _live_or_raise(tenant_id, conversation_id)
    with _LIVE_LOCK:
        queue = _REQUESTS.setdefault(conversation_id, [])
        if len(queue) >= MAX_PENDING_REQUESTS:
            raise ConversationError("too many pending messages — wait for the next turn")
        queue.append({"type": "say", "text": text, "target": target})
        return {"queued": len(queue)}


def configure(tenant_id: str, conversation_id: str, changes: dict[str, Any]) -> dict[str, Any]:
    """Change settings of a running conversation (applies from the next turn)."""
    if not isinstance(changes, dict) or not changes:
        raise ConversationError("no settings given")
    _live_or_raise(tenant_id, conversation_id)
    with _LIVE_LOCK:
        base = _SETTINGS.get(conversation_id)
    normalize_settings(changes, base=base)  # validate before queueing
    with _LIVE_LOCK:
        queue = _REQUESTS.setdefault(conversation_id, [])
        if len(queue) >= MAX_PENDING_REQUESTS:
            raise ConversationError("too many pending requests — wait for the next turn")
        queue.append({"type": "settings", "changes": changes})
        return {"queued": len(queue)}


def set_paused(tenant_id: str, conversation_id: str, paused: bool) -> dict[str, Any]:
    """Pause after the current turn / resume. Idempotent."""
    _live_or_raise(tenant_id, conversation_id)
    with _LIVE_LOCK:
        (_PAUSED.add if paused else _PAUSED.discard)(conversation_id)
    return {"paused": paused}


# Slash grammar for the composer — parsed HERE, never in the client (ADR-2235: a client copy of
# the grammar drifts, and a command not parsed server-side cannot be refused fail-closed).
COMMANDS: tuple[dict[str, str], ...] = (
    {"cmd": "/pause", "args": "", "desc": "Pause after the current turn"},
    {"cmd": "/resume", "args": "", "desc": "Continue a paused conversation"},
    {"cmd": "/stop", "args": "", "desc": "End the conversation after the current turn"},
    {"cmd": "/words", "args": "<50-600>", "desc": "Limit how long each agent's reply is (words)"},
    {"cmd": "/pace", "args": "<0-60>", "desc": "Seconds to wait between turns"},
)


def run_command(tenant_id: str, conversation_id: str, line: str) -> dict[str, Any]:
    """Run one composer ``/`` line. Anything unrecognised is refused, never sent as text."""
    parts = (line or "").strip().split()
    if not parts or not parts[0].startswith("/"):
        raise ConversationError("not a command")
    cmd, args = parts[0].lower(), parts[1:]
    if cmd in ("/pause", "/resume", "/stop") and args:
        raise ConversationError(f"{cmd} takes no argument")
    if cmd == "/pause":
        set_paused(tenant_id, conversation_id, True)
        return {"notice": "Paused after the current turn."}
    if cmd == "/resume":
        set_paused(tenant_id, conversation_id, False)
        return {"notice": "Resumed."}
    if cmd == "/stop":
        if not stop(tenant_id, conversation_id):
            raise ConversationError("conversation is not running")
        return {"notice": "Stopping after the current turn."}
    if cmd in ("/words", "/pace"):
        if len(args) != 1 or not args[0].isdigit():
            raise ConversationError(f"usage: {cmd} <number>")
        key = "max_words" if cmd == "/words" else "pace_s"
        configure(tenant_id, conversation_id, {key: int(args[0])})
        return {"notice": f"{key} set to {int(args[0])} from the next turn."}
    raise ConversationError(f"unknown command {cmd} — type / to see the list")


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
