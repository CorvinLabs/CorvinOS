"""Outcome-grounded skill grading for the console web chat.

The bridge has graded skills from the user's follow-up turn since Phase 1
(``adapter.process_one`` -> ``skill_inject.grade_from_user_followup``): when the
next message says "gut gemacht" / "nochmal" / repeats the question, the skills
active in the previous turn get an approval / rejection / rephrase grade. The
console had no such path, so praise typed into the web chat never reached a
skill. This module is that path for the console, and it reuses the bridge's
ONE detector and grader (``grade_from_user_followup``) — there is no second
phrase list here.

Three call sites, all thin:

* ``injected_skill_ids`` — ``chat_runtime._stream_turn_impl``, in the CEL step:
  the ids the skill block ACTUALLY carries (``render_skill_bindings`` drops a
  ref with an empty body — the Gate-2-denied case — renders a PII-gated body as
  a placeholder, and stops at ``MAX_BINDINGS``; none of those reached a worker).
* ``record_turn_skills`` — ``chat_runtime._stream_turn_impl``, right after the
  native ``claude`` subprocess started (``task.started`` with its pid) with a
  system prompt that carries that block. The CEL brief reaches ONLY that spawn;
  a turn answered on any other branch (``/debug-engine``, a gate refusal, an
  ACS / TDE delegation, any ``no_engine`` path) never records a snapshot.
* ``grade_followup`` — ``routes/chat.py::_run_turn``, inside the session turn
  lock and BEFORE the next turn starts, with the next prompt.

Gates (all fail-closed — no signal unless every one holds):

1. a snapshot exists for this chat, younger than ``SNAPSHOT_TTL_S`` (consumed on
   first use, like the bridge: one follow-up grades one turn);
2. the previous turn's task is ``COMPLETED`` (a failed or cancelled turn is not
   graded — mirrors ``chat_runtime._on_turn_closed``);
3. the operator holds an active ``learning_feedback`` consent (GDPR Art. 6,
   the same scope and the same checker as ``POST /v1/console/learning/feedback``);
   any error while checking it is a denial;
4. the tenant's ``spec.web_chat`` does not set ``inject_skills: false`` or
   ``outcome_grading: false`` — the same two switches the bridge reads from its
   chat profile, read by ``grade_from_user_followup`` itself;
5. only skills of the SESSION tenant's own scopes (``session`` / ``user``) are
   graded — never the shared repo ``project`` scope (``grade_from_user_followup``
   with an explicit ``tenant_id``);
6. one organic follow-up grade per skill and signal kind per rolling 24 h (console)
   (dedupe channel ``"console"``; only a day-bound hash is stored, in the grade note).

Privacy (data minimisation): the snapshot holds NO prompt text — only the
tenant, chat key, run id, skill ids and a timestamp, in memory. The console
therefore produces approval / rejection signals only, never a rephrase signal
(that one needs the previous prompt). Deleting the chat drops its snapshot
(``forget_chat``). What is recorded is the signal enum and the skill names:
``console.action_performed`` with ``action="skill.outcome_graded.<signal>"``,
plus the registry's own hash-chained ``skill.grade`` record (score + a note
naming the signal, the previous run id and the dedupe hash — no text), written
BEFORE the grade is persisted.
"""
from __future__ import annotations

import importlib
import logging
import sys
import threading
import time
from pathlib import Path
from typing import Any

_log = logging.getLogger(__name__)

#: Same default as the bridge's ``adapter.OUTCOME_SNAPSHOT_TTL`` (30 min): a
#: follow-up after that long is not a reaction to the previous answer.
SNAPSHOT_TTL_S = 30 * 60

#: Dedupe channel of every console follow-up grade. The console is one surface
#: per tenant (the registry is tenant-scoped), so opening a new chat does not
#: open a new allowance.
DEDUPE_CHANNEL = "console"

_snapshots: dict[tuple[str, str], dict[str, Any]] = {}
_guard = threading.Lock()


def _key(tenant_id: str, chat_key: str) -> tuple[str, str]:
    return (str(tenant_id or ""), str(chat_key or ""))


def injected_skill_ids(bundle: Any, block: str) -> list[str]:
    """The skill ids whose BODY is in ``block`` (the rendered skill block).

    Mirrors ``render_skill_bindings``: the first ``MAX_BINDINGS`` refs with a
    non-empty id and body, minus a body the PII gate replaced by the redaction
    placeholder. Each id is also checked against its heading in the block.
    Fail-closed: any error, or an empty block, is ``[]``."""
    if not block:
        return []
    try:
        b = importlib.import_module("context_engineering.stages.binding")
    except Exception:  # noqa: BLE001
        try:
            b = importlib.import_module("corvin_operator.context_engineering.stages.binding")
        except Exception:  # noqa: BLE001
            return []
    try:
        out: list[str] = []
        for s in list(getattr(bundle, "skills_to_bind", None) or [])[:b.MAX_BINDINGS]:
            sid = str(getattr(s, "skill_id", "") or "").strip()
            body = str(getattr(s, "body", "") or "").strip()
            if not sid or not body:
                continue
            head = f"### {sid[:b.MAX_SKILL_ID_CHARS]}\n"
            if head not in block or (head + b.SKILL_BODY_REDACTED) in block:
                continue
            out.append(sid)
        return list(dict.fromkeys(out))
    except Exception:  # noqa: BLE001
        return []


def record_turn_skills(tenant_id: str, chat_key: str, *, tasks_dir: Path, run_id: str,
                       skill_names: list[str]) -> None:
    """Remember which skills THIS turn's spawned engine received, for the next
    turn's follow-up.

    No-op without skills. Replaces any older snapshot for the chat. In memory
    only, and without any prompt text."""
    names = [n for n in dict.fromkeys(str(s) for s in (skill_names or [])) if n]
    if not names or not run_id:
        return
    with _guard:
        _reap_locked()
        _snapshots[_key(tenant_id, chat_key)] = {
            "tasks_dir": Path(tasks_dir), "run_id": str(run_id), "skills": names,
            "ts": time.time(),
        }


def pop_turn_skills(tenant_id: str, chat_key: str) -> dict[str, Any] | None:
    """Return AND clear the chat's snapshot; None when absent or stale."""
    with _guard:
        snap = _snapshots.pop(_key(tenant_id, chat_key), None)
    if snap is None or time.time() - float(snap.get("ts", 0.0)) > SNAPSHOT_TTL_S:
        return None
    return snap


def forget_chat(tenant_id: str, chat_key: str) -> None:
    """Drop the chat's snapshot (chat delete)."""
    with _guard:
        _snapshots.pop(_key(tenant_id, chat_key), None)


def _reap_locked() -> None:
    cutoff = time.time() - SNAPSHOT_TTL_S
    for k in [k for k, v in _snapshots.items() if float(v.get("ts", 0.0)) < cutoff]:
        _snapshots.pop(k, None)


def _previous_turn_completed(snap: dict[str, Any]) -> bool:
    try:
        from . import task_manager as _tm  # noqa: PLC0415
        task = _tm.TaskManager(snap["tasks_dir"]).get_task(snap["run_id"])
        return task is not None and task.status == _tm.TaskStatus.COMPLETED
    except Exception:  # noqa: BLE001 — unverifiable → not graded
        return False


async def _consent_granted(rec: Any) -> bool:
    """The ``learning_feedback`` consent, checked exactly like the feedback route.
    Any refusal or error is a denial (fail-closed)."""
    try:
        from core.compliance.consent import consent_required  # noqa: PLC0415
        try:
            from core.skills.os_skills.feedback_loop import (  # noqa: PLC0415
                LEARNING_FEEDBACK_CONSENT_SCOPE as scope)
        except ImportError:
            scope = "learning_feedback"
        await consent_required(scope)(rec)
        return True
    except Exception:  # noqa: BLE001 — HTTPException 403/503 or anything else
        return False


def _web_chat_profile(tenant_id: str) -> dict[str, Any]:
    """``spec.web_chat`` — the console's counterpart of the bridge chat profile."""
    try:
        from . import chat_runtime as _cr  # noqa: PLC0415
        wc = _cr._tenant_spec(tenant_id).get("web_chat") or {}
        return dict(wc) if isinstance(wc, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def _skill_inject():
    try:
        shared = Path(__file__).resolve().parents[3] / "corvin_operator" / "bridges" / "shared"
        if shared.is_dir() and str(shared) not in sys.path:
            sys.path.insert(0, str(shared))
        import skill_inject as _si  # type: ignore  # noqa: PLC0415
        return _si
    except Exception:  # noqa: BLE001 — stripped install: no grading, never an error
        return None


async def grade_followup(rec: Any, chat_key: str, prompt: str) -> list[dict[str, Any]]:
    """Grade the previous turn's skills from ``prompt``. Never raises.

    Returns the ``grade_from_user_followup`` result (``[]`` when any gate
    fails, the text carries no signal, or no grade could be committed)."""
    try:
        tenant_id = getattr(rec, "tenant_id", None)
        if not tenant_id:
            return []
        snap = pop_turn_skills(tenant_id, chat_key)
        if snap is None:
            return []
        if not await _consent_granted(rec):
            return []
        if not _previous_turn_completed(snap):
            return []
        si = _skill_inject()
        if si is None:
            return []
        import asyncio  # noqa: PLC0415
        graded = await asyncio.to_thread(
            si.grade_from_user_followup,
            channel_id=None,   # the CEL binds skills from the tenant registry, no channel scope
            profile=_web_chat_profile(tenant_id),
            user_text=prompt,
            prev_run_id=snap["run_id"],
            prev_skill_names=list(snap["skills"]),
            prev_user_text=None,   # no prompt text kept → no rephrase signal on the console
            tenant_id=tenant_id,   # tenant-owned scopes only (never the shared project scope)
            dedupe_channel=DEDUPE_CHANNEL,
        )
    except Exception:  # noqa: BLE001 — grading must never cost the user a turn
        _log.warning("console follow-up grading failed", exc_info=True)
        return []
    for g in graded or []:
        try:
            from . import audit as _audit  # noqa: PLC0415
            _audit.action_performed(
                tenant_id=tenant_id,
                sid_fingerprint=str(getattr(rec, "sid_fingerprint", "") or ""),
                action=f"skill.outcome_graded.{g.get('signal')}",
                target_kind="skill", target_id=str(g.get("name", "")),
                run_id=str(snap["run_id"]))
        except Exception:  # noqa: BLE001 — the registry's skill.grade record stands
            _log.debug("outcome_graded audit failed", exc_info=True)
    return graded or []
