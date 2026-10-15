"""Mandatory user notice for every system-caused interruption, resume and ending (PLAN-0958 P2).

CONCEPT-0112: when a task is cut off the user must be told what happened, whether it
continues, what was done and how many attempts are left - in the chat, readable after a
reload. A notice is a fixed template with a closed field set. It never carries ids, paths,
stack traces or model ids; the only free text is a short label made of the user's own first
words. A rendered text that still contains an id-shaped token fails closed (``NoticeError``)
and the caller falls back to a notice without the label.

Persistence: the notice is appended to the chat's turn log as an assistant turn flagged
``notice`` (UI record only - ``session_ledger.records_from_turn_log`` skips it, so a worker is
never handed its own bookkeeping) and to the task's event log as ``task.notice``.
"""
from __future__ import annotations

import logging
import re
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

LABEL_MAX = 60
KINDS = ("interrupted_resuming", "recovered", "gave_up", "needs_user")

# Closed vocabulary - the only values a template may interpolate.
_CLASS_LABEL = {
    "en": {
        "C1": "a service restart", "C2": "a disconnected client", "C3": "your cancel request",
        "C4": "a worker timeout", "C5": "a stalled worker", "C6": "an exhausted budget",
        "C7": "a policy gate", "C8": "a permanent error", "C9": "an unexpected stop",
    },
    "de": {
        "C1": "einen Neustart des Dienstes", "C2": "eine getrennte Verbindung", "C3": "deine Abbruch-Anfrage",
        "C4": "eine Zeitüberschreitung", "C5": "einen hängenden Worker", "C6": "ein aufgebrauchtes Budget",
        "C7": "ein Regel-Gate", "C8": "einen dauerhaften Fehler", "C9": "einen unerwarteten Abbruch",
    },
}
_TEMPLATES = {
    "en": {
        "interrupted_resuming": "Your task '{task_label}' was interrupted by {class_label}. It continues automatically "
                                "(attempt {attempt} of {max_attempts}, {remaining_attempts} left after this one). "
                                "You will get a final message when it completes or stops.",
        "recovered": "Your task '{task_label}' was interrupted by {class_label} and has now finished after an automatic "
                     "restart (attempt {attempt} of {max_attempts}).",
        "gave_up": "Your task '{task_label}' was interrupted by {class_label}. It ends here: {max_attempts} automatic "
                   "attempts produced no answer (0 left). Send the request again to retry it.",
        "needs_user": "Your task '{task_label}' was interrupted by {class_label}. It does NOT continue on its own: "
                      "a step may already have had an effect. Tell me whether to run it again.",
    },
    "de": {
        "interrupted_resuming": "Deine Aufgabe '{task_label}' wurde durch {class_label} unterbrochen. Sie läuft automatisch "
                                "weiter (Versuch {attempt} von {max_attempts}, danach noch {remaining_attempts}). "
                                "Du bekommst eine letzte Nachricht, sobald sie fertig ist oder endet.",
        "recovered": "Deine Aufgabe '{task_label}' wurde durch {class_label} unterbrochen und ist nach einem automatischen "
                     "Neustart jetzt fertig (Versuch {attempt} von {max_attempts}).",
        "gave_up": "Deine Aufgabe '{task_label}' wurde durch {class_label} unterbrochen. Sie endet hier: {max_attempts} "
                   "automatische Versuche lieferten keine Antwort (0 übrig). Sende die Anfrage erneut, um es noch einmal zu versuchen.",
        "needs_user": "Deine Aufgabe '{task_label}' wurde durch {class_label} unterbrochen. Sie läuft NICHT von selbst weiter: "
                      "ein Schritt kann schon Wirkung gehabt haben. Sag mir, ob ich sie erneut ausführen soll.",
    },
}

# Id-shaped tokens a notice must never contain: uuids, long hex runs, long digit runs (chat ids).
_ID_SHAPED = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
    r"|\b[0-9a-fA-F]{16,}\b|\b\d{10,}\b|[A-Za-z0-9_-]{22,}"
)


class NoticeError(ValueError):
    """The rendered notice would carry an id-shaped token or an unknown kind/class."""


def task_label(instruction: str) -> str:
    """Short label the user can recognise: their own first words, whitespace collapsed,
    id-shaped tokens and quotes removed, at most :data:`LABEL_MAX` characters."""
    text = _ID_SHAPED.sub("…", " ".join(str(instruction or "").split())).replace("'", "")
    if not text:
        return "your request"
    return (text[: LABEL_MAX - 1] + "…") if len(text) > LABEL_MAX else text


def render(kind: str, *, lang: str = "en", failure_class: str = "C9", label: str = "",
           attempt: int = 1, max_attempts: int = 2) -> str:
    """Fixed template + closed fields. Raises :class:`NoticeError` instead of leaking an id."""
    lang = lang if lang in _TEMPLATES else "en"
    if kind not in KINDS:
        raise NoticeError(f"unknown notice kind: {kind!r}")
    class_label = _CLASS_LABEL[lang].get(failure_class)
    if class_label is None:
        raise NoticeError(f"unknown failure class: {failure_class!r}")
    text = _TEMPLATES[lang][kind].format(
        task_label=task_label(label), class_label=class_label, attempt=int(attempt),
        max_attempts=int(max_attempts), remaining_attempts=max(0, int(max_attempts) - int(attempt)))
    if _ID_SHAPED.search(text):
        raise NoticeError("rendered notice contains an id-shaped token")
    return text


def session_lang(sess: Any) -> str:
    ctx = getattr(sess, "language_context", None)
    lang = str(ctx.get("lang") or "en").lower()[:2] if isinstance(ctx, dict) else "en"
    return lang if lang in _TEMPLATES else "en"


def emit(sess: Any, kind: str, *, task_id: str, failure_class: str, instruction: str,
         attempt: int, max_attempts: int) -> str | None:
    """Render, persist in the chat and in the task's event log, audit. Never raises: a notice
    that cannot be written must not stop the recovery it describes."""
    try:
        try:
            text = render(kind, lang=session_lang(sess), failure_class=failure_class, label=instruction,
                          attempt=attempt, max_attempts=max_attempts)
        except NoticeError:
            text = render(kind, lang=session_lang(sess), failure_class=failure_class, label="",
                          attempt=attempt, max_attempts=max_attempts)
        from . import chat_runtime
        chat_runtime._append_turn(sess, "assistant", [{"kind": "text", "text": text}], notice=kind)
        from . import task_manager as tm_module
        tm_module.TaskManager(Path(sess.workdir) / "tasks").record_event(
            task_id, {"event": "task.notice", "notice_kind": kind, "failure_class": failure_class,
                      "attempt": int(attempt), "max_attempts": int(max_attempts), "text": text,
                      "at": time.time()})
        _audit(sess, task_id, kind)
        return text
    except Exception as exc:  # noqa: BLE001
        logger.warning("task notice %s not delivered: %s", kind, type(exc).__name__)
        return None


def _audit(sess: Any, task_id: str, kind: str) -> None:
    try:
        from . import audit as console_audit
        console_audit.system_event(
            tenant_id=sess.tenant_id, event="task.notice_sent",
            details={"tenant_id": sess.tenant_id, "task_id": task_id, "notice_kind": kind, "channel": "web"})
    except Exception as exc:  # noqa: BLE001
        logger.error("task.notice_sent audit not written: %s", exc)
