"""Entity model of the personal knowledge base (PLAN-0973 section 3, ADR-2327 items 3-4).

Five entity types. A preference or context that Corvin captures automatically must use a key
from the closed ``AUTO_KEYS`` taxonomy: enum keys take an enum member only, short-text keys at
most 60 characters that pass the value grammar. Free text exists only for entries the owner
wrote or confirmed. The task state machine is a copy of the project KB's ``TASK_FSM``; a parity
test pins the two together.
"""
from __future__ import annotations

import re
import time
from typing import Any

from .ids import is_ulid, new_ulid

TYPES = ("preference", "context", "idea", "task", "note")
STATUSES: dict[str, tuple[str, ...]] = {
    "preference": ("active", "retired"),
    "context": ("active", "retired"),
    "idea": ("inbox", "exploring", "realized", "promoted", "dropped"),
    "task": ("open", "in_progress", "blocked", "done", "cancelled"),
    "note": ("active", "archived"),
}
DEFAULT_STATUS = {"preference": "active", "context": "active", "idea": "inbox", "task": "open", "note": "active"}
ORIGINS = ("user", "auto", "confirmed", "derived")
REVIEWS = ("none", "pending", "accepted")
LINK_RELS = ("related", "derived_from", "promoted_to", "blocks", "regresses")
PREFIX = {"preference": "PREF", "context": "CTX", "idea": "IDEA", "task": "me/T", "note": "NOTE"}

MAX_TITLE = 200
MAX_BODY = 8000
MAX_KEY = 80
MAX_LABELS = 12
MAX_LABEL = 32
MAX_LINKS = 64
MAX_ENTITY_BYTES = 64 * 1024
AUTO_VALUE_MAX = 60

# Copy of Corvin-Knowledge scripts/kb.py TASK_FSM (tests/personal_kb/test_model.py pins parity).
TASK_FSM: dict[str, frozenset[str]] = {
    "open": frozenset({"in_progress", "cancelled"}),
    "in_progress": frozenset({"blocked", "done", "open", "cancelled"}),
    "blocked": frozenset({"in_progress", "open", "cancelled"}),
    "done": frozenset(),
    "cancelled": frozenset(),
}

# Closed key taxonomy for automatic capture (ADR-2327 item 3).
AUTO_ENUM_KEYS: dict[str, dict[str, tuple[str, ...]]] = {
    "preference": {
        "language": ("de", "en", "fr", "es", "it", "nl", "pl", "pt", "tr", "uk"),
        "verbosity": ("brief", "balanced", "detailed"),
        "tone": ("formal", "neutral", "casual", "warm", "humorous"),
        "format": ("prose", "bullets", "tables", "headings", "code_first"),
        "units": ("metric", "imperial"),
        "address_form": ("du", "sie", "first_name", "neutral"),
        "expertise_level": ("beginner", "intermediate", "expert"),
    },
    "context": {},
}
AUTO_TEXT_KEYS: dict[str, tuple[str, ...]] = {
    "preference": (),
    "context": ("role", "domain", "tools", "timezone", "working_hours"),
}

_KEY_RE = re.compile(r"^[a-z][a-z0-9_]{0,79}$")
_LABEL_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
_URI_RE = re.compile(r"^[a-z][a-z0-9+.-]{1,20}:[^\s]{1,300}$")

# Value grammar (ADR-2327 item 3): what an automatic value may never contain.
_UNSAFE = [
    re.compile(r"(?i)\b(?:https?|ftp|file)://"),
    re.compile(r"(?i)\bwww\.[a-z0-9-]+\."),
    re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"),
    re.compile(r"(?:^|\s)(?:[A-Za-z]:\\|~?/[\w.-]+/)"),
    re.compile(r"[`$|;&<>]|\$\(|&&|\|\|"),
    re.compile(r"(?i)\b(?:send|forward|upload|delete|remove|run|execute|install|email|mail|post|"
               r"transfer|copy|move|approve|ignore|disregard)\b.{0,40}\b(?:file|files|data|repo|"
               r"command|commands|script|instruction|instructions|key|keys|password|token|review|pr|prs)\b"),
    re.compile(r"(?i)\b(?:schick|sende|leite|lade|lösche|loesche|führe|fuehre|installiere|"
               r"ignoriere|genehmige)\w*\b.{0,40}\b(?:datei|dateien|daten|befehl|skript|anweisung|"
               r"schlüssel|passwort|token|review)\w*\b"),
]


class ValidationError(ValueError):
    """An entity or a value violates the model."""


def unsafe_value(text: str) -> bool:
    return any(p.search(text or "") for p in _UNSAFE)


def check_auto_value(etype: str, key: str, value: str) -> str | None:
    """None when an automatic (key, value) is admissible, else the closed reason code."""
    if etype not in ("preference", "context"):
        return "key_not_allowed"
    enum = AUTO_ENUM_KEYS.get(etype, {})
    text_keys = AUTO_TEXT_KEYS.get(etype, ())
    value = str(value or "").strip()
    if key in enum:
        return None if value in enum[key] else "key_not_allowed"
    if key in text_keys:
        if not value or len(value) > AUTO_VALUE_MAX:
            return "key_not_allowed"
        if unsafe_value(value):
            return "unsafe_content"
        return None
    return "key_not_allowed"


def new_entity(etype: str, title: str, *, body: str = "", origin: str = "user",
               review: str | None = None, key: str | None = None, status: str | None = None,
               links: list[dict[str, str]] | None = None, labels: list[str] | None = None,
               confidence: float | None = None, source: dict[str, Any] | None = None,
               expires_at: float | None = None, now: float | None = None) -> dict[str, Any]:
    ts = time.time() if now is None else float(now)
    ent = {
        "schema": 1, "uid": new_ulid(int(ts * 1000)), "type": etype, "title": title, "body": body,
        "status": status or DEFAULT_STATUS.get(etype, ""), "origin": origin,
        "review": review if review is not None else ("pending" if origin == "auto" else "none"),
        "key": key, "links": list(links or []), "labels": list(labels or []),
        "confidence": confidence, "source": source, "expires_at": expires_at,
        "created": ts, "updated": ts, "version": 0,
        "task": {"dod": None, "blocked_reason": None, "cancel_reason": None} if etype == "task" else None,
        "promoted_to": None,
        "decoder": None,
    }
    return validate(ent)


_SURROGATE = re.compile("[\ud800-\udfff]")


def _no_surrogates(value: Any) -> None:
    """A lone UTF-16 surrogate encodes to no UTF-8 at all: the block, the export and the system prompt would fail on
    every later turn. Refused at the one write path, so no stored entry can carry one."""
    if isinstance(value, str) and _SURROGATE.search(value):
        raise ValidationError("text contains a character that cannot be stored")
    if isinstance(value, (list, tuple)):
        for v in value:
            _no_surrogates(v)
    elif isinstance(value, dict):
        for k, v in value.items():
            _no_surrogates(k)
            _no_surrogates(v)


def validate(ent: dict[str, Any]) -> dict[str, Any]:
    """Return a normalised copy or raise :class:`ValidationError`."""
    if not isinstance(ent, dict):
        raise ValidationError("entity must be an object")
    _no_surrogates(ent)
    e = dict(ent)
    if e.get("schema") != 1:
        raise ValidationError("unknown schema")
    if not is_ulid(e.get("uid")):
        raise ValidationError("bad uid")
    t = e.get("type")
    if t not in TYPES:
        raise ValidationError("bad type")
    title = str(e.get("title") or "").strip()
    if not title or len(title) > MAX_TITLE:
        raise ValidationError(f"title must be 1-{MAX_TITLE} characters")
    e["title"] = title
    body = str(e.get("body") or "")
    if len(body) > MAX_BODY:
        raise ValidationError(f"body over {MAX_BODY} characters")
    e["body"] = body
    if e.get("status") not in STATUSES[t]:
        raise ValidationError("bad status")
    if e.get("origin") not in ORIGINS:
        raise ValidationError("bad origin")
    if e.get("review") not in REVIEWS:
        raise ValidationError("bad review")
    key = e.get("key")
    if key is not None:
        if t not in ("preference", "context") or not _KEY_RE.match(str(key)):
            raise ValidationError("bad key")
    if e.get("origin") == "auto":
        if t in ("preference", "context"):
            if key is None:
                raise ValidationError("automatic entry needs a key")
            why = check_auto_value(t, key, title)
            if why:
                raise ValidationError(why)
    links = e.get("links") or []
    if not isinstance(links, list) or len(links) > MAX_LINKS:
        raise ValidationError("bad links")
    clean_links = []
    for ln in links:
        if not isinstance(ln, dict) or ln.get("rel") not in LINK_RELS:
            raise ValidationError("bad link")
        to = str(ln.get("to") or "")
        if not (is_ulid(to) or _URI_RE.match(to)):
            raise ValidationError("bad link target")
        clean_links.append({"rel": ln["rel"], "to": to})
    e["links"] = clean_links
    if e.get("origin") == "derived" and not any(ln["rel"] == "derived_from" for ln in clean_links):
        raise ValidationError("a derived entry needs a derived_from link")
    labels = e.get("labels") or []
    if not isinstance(labels, list) or len(labels) > MAX_LABELS or not all(
            isinstance(x, str) and _LABEL_RE.match(x) for x in labels):
        raise ValidationError("bad labels")
    conf = e.get("confidence")
    if conf is not None and not (isinstance(conf, (int, float)) and 0.0 <= float(conf) <= 1.0):
        raise ValidationError("bad confidence")
    if t == "task":
        task = e.get("task") or {}
        if not isinstance(task, dict):
            raise ValidationError("bad task block")
        e["task"] = {k: (str(task.get(k))[:2000] if task.get(k) else None)
                     for k in ("dod", "blocked_reason", "cancel_reason")}
        if e["status"] == "done" and not e["task"]["dod"]:
            raise ValidationError("a done task needs a definition of done")
        if e["status"] == "blocked" and not e["task"]["blocked_reason"]:
            raise ValidationError("a blocked task needs a reason")
        if e["status"] == "cancelled" and not e["task"]["cancel_reason"]:
            raise ValidationError("a cancelled task needs a reason")
    else:
        e["task"] = None
    if not isinstance(e.get("version"), int) or e["version"] < 0:
        raise ValidationError("bad version")
    import json as _json
    if len(_json.dumps(e, default=str).encode("utf-8")) > MAX_ENTITY_BYTES:
        raise ValidationError("entry too large")
    return e


def transition(task: dict[str, Any], to: str, *, reason: str | None = None,
               dod: str | None = None) -> dict[str, Any]:
    """A new copy of *task* in state *to*, or :class:`ValidationError` (project KB rules)."""
    if task.get("type") != "task":
        raise ValidationError("not a task")
    cur = task.get("status")
    if to not in TASK_FSM.get(cur, frozenset()):
        raise ValidationError(f"{cur} -> {to} is not allowed")
    nxt = dict(task)
    block = dict(task.get("task") or {})
    if to == "blocked":
        if not reason:
            raise ValidationError("blocked needs a reason")
        block["blocked_reason"] = reason
    if to == "cancelled":
        if not reason:
            raise ValidationError("cancelled needs a reason")
        block["cancel_reason"] = reason
    if to == "done":
        dod = dod or block.get("dod")
        if not dod:
            raise ValidationError("done needs a definition of done")
        block["dod"] = dod
    if to in ("in_progress", "open"):
        block["blocked_reason"] = None
    nxt["task"] = block
    nxt["status"] = to
    return validate(nxt)
