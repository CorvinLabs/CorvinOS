"""conversation_recall.py — Layer 28.1 (ADR-0016).

Per-tenant FTS5-indexed conversation history with text-mode PII
redaction at the indexing boundary. Pure SQLite + regex; MUST NOT
import any LLM SDK (cost contract — mirror of ``user_style.py`` and
``dialectic.py``).

Storage:

    <tenant_home>/global/memory/recall.db   (mode 0o600, SQLite WAL)

Two tables:

    turns       — one row per indexed turn-pair, metadata + redacted text
    turns_fts   — virtual FTS5 table over (user_text, assistant_text)

Public API:

    index_turn(channel, chat_key, *, user_text, assistant_text, msg_id,
               persona, ts=None, run_id="", tenant_id=None) -> dict
    recall(query, *, channel=None, chat_key=None, since=None, until=None,
           limit=20, caller_persona="", tenant_id=None) -> list[Recall]
    forget(filter, *, tenant_id=None) -> int        # Phase 28.4 stub
    redact_text(text) -> tuple[str, dict[str, int]]

Persona-ACL: the optional ``memory_recall_enabled: true`` flag on the
caller's resolved chat_profile gates the MCP / slash-command surface.
The Python API is unguarded by design — adapter-internal call sites
(``process_one`` indexing, ``/forget``) need unconditional access.
The persona-ACL lives at the dispatcher edge (MCP server / JS
slash-command).
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
import threading
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Callable, Iterable

# ── Audit hash chain (best-effort import; mirror user_style.py pattern) ────
_audit_writer: Callable[..., Any] | None = None
try:
    _HERE = Path(__file__).resolve().parent
    _FORGE_TOP = _HERE.parent.parent / "forge"
    if _FORGE_TOP.is_dir() and str(_FORGE_TOP) not in sys.path:
        sys.path.insert(0, str(_FORGE_TOP))
    from forge.security_events import write_event as _audit_writer  # type: ignore  # noqa: E402
except Exception:  # noqa: BLE001
    _audit_writer = None

# ── Tenant-aware paths (best-effort, mirror user_style.py) ────────────────
_tenant_global_dir: Callable[..., Path] | None = None
try:
    from paths import tenant_global_dir as _tenant_global_dir  # type: ignore
except Exception:
    try:
        from forge.paths import tenant_global_dir as _tenant_global_dir  # type: ignore  # noqa: E402
    except Exception:
        _tenant_global_dir = None


# ── Text-mode PII redaction ────────────────────────────────────────────────
#
# Free-text equivalent of Layer 24's value-regex backend. We do NOT
# import pii_detector.py — that module is column-oriented (it inspects
# per-column samples), this one operates on free conversation text.
# The regex patterns are intentionally aligned so a future
# pii_detector text-mode API can absorb us.
#
# Known limitations:
#   - Email obfuscation variants beyond [at]/[dot] (e.g. "at sign") are
#     not matched; only the two most common obfuscation forms are covered.
#   - Credit card matching uses Luhn validation to reduce false positives,
#     but compact digit sequences in prose (phone numbers, order IDs) may
#     still trigger if they happen to pass Luhn.
#   - Phone regex is conservative; unusual national formats may be missed.
#   - IBAN coverage is regex-only (no checksum validation).
#   - SSN and AHV are anchored to known formats (US/CH only).

_PII_TEXT_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # email — standard format, case-insensitive, robust against trailing punctuation
    (re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", re.IGNORECASE), "email"),
    # email — [at]/[dot] obfuscated variants (e.g. user[at]example[dot]com)
    (re.compile(
        r"[A-Za-z0-9._%+-]+\s*[\[\(]at[\]\)]\s*[A-Za-z0-9.-]+"
        r"\s*[\[\(]dot[\]\)]\s*[A-Za-z]{2,}",
        re.IGNORECASE,
    ), "email"),
    # IBAN — 2 letters + 2 digits + up to 30 alnum, optional spaces every 4
    (re.compile(r"\b[A-Z]{2}\d{2}(?:[\s-]?[A-Z0-9]){11,30}\b", re.IGNORECASE), "iban"),
    # credit card — candidate regex; Luhn validation applied separately in
    # _redact_credit_cards() below. This pattern is NOT in the main loop.
    # us-ssn — XXX-XX-XXXX (avoid matching arbitrary 9-digit sequences)
    (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "us_ssn"),
    # ch-ahv — 756.XXXX.XXXX.XX
    (re.compile(r"\b756\.\d{4}\.\d{4}\.\d{2}\b"), "ch_ahv"),
    # phone — international (+49 30 ...) or local (030/...).
    # Anchored on leading + or word-boundary 0; quantifier enforces
    # 7-17 more digits with optional separators. Conservative enough
    # to skip stray digits like "Phase 0 done" (no digits follow).
    (re.compile(r"(?:\+[1-9]\d{0,2}|\b0)(?:[\s\-./]?\d){7,17}"), "phone"),
]

# Credit card: two-step — candidate regex then Luhn check.
_CC_CANDIDATE_RE = re.compile(r'\b(?:\d[ -]?){13,19}\b')


def _luhn_check(s: str) -> bool:
    """Luhn algorithm — returns True for valid card numbers."""
    digits = [int(c) for c in s if c.isdigit()]
    if len(digits) < 13 or len(digits) > 19:
        return False
    total = 0
    for i, d in enumerate(reversed(digits)):
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def _redact_credit_cards(text: str, counts: dict[str, int]) -> str:
    """Replace valid credit card numbers (Luhn-validated) with <redacted:card>."""
    def replace_if_valid(m: re.Match) -> str:
        candidate = m.group(0)
        if _luhn_check(candidate):
            counts["credit_card"] = counts.get("credit_card", 0) + 1
            return "<redacted:credit_card>"
        return candidate
    return _CC_CANDIDATE_RE.sub(replace_if_valid, text)


# When a match is found we replace with a class token. Operators can
# correlate redaction frequency via the audit chain without seeing the
# original value.
def _redact_token(pii_class: str) -> str:
    return f"<redacted:{pii_class}>"


def redact_text(text: str) -> tuple[str, dict[str, int]]:
    """Apply text-mode PII regex to *text*.

    Returns the redacted text plus a dict mapping pii_class to hit
    count. Order is deterministic (pattern declaration order). When
    *text* is empty or non-string, returns ('', {}).
    """
    if not isinstance(text, str) or not text:
        return "", {}
    counts: dict[str, int] = {}
    out = text
    for pattern, pii_class in _PII_TEXT_PATTERNS:
        def _replace(match: re.Match[str], _cls: str = pii_class) -> str:
            counts[_cls] = counts.get(_cls, 0) + 1
            return _redact_token(_cls)
        out = pattern.sub(_replace, out)
    # Credit card via Luhn-validated two-step (must run after other patterns
    # to avoid double-matching digit sequences already redacted).
    out = _redact_credit_cards(out, counts)
    return out, counts


# ── DB path resolution ─────────────────────────────────────────────────────

def _memory_dir(tenant_id: str | None = None) -> Path:
    """Return ``<tenant_home>/global/memory/`` for the active tenant.

    Falls back to ``~/.corvin/global/memory/`` when the tenant
    resolver is not importable (test-sandbox path).
    """
    if _tenant_global_dir is not None:
        try:
            return _tenant_global_dir(tenant_id) / "memory"
        except Exception:  # noqa: BLE001
            pass
    # Fallback: env-driven path (test sandboxes)
    env = os.environ.get("CORVIN_HOME") or os.environ.get("CORVIN_HOME")
    if env:
        base = Path(os.path.expanduser(os.path.expandvars(env)))
    else:
        base = Path.home() / ".corvin"
    return base / "global" / "memory"


def _db_path(tenant_id: str | None = None) -> Path:
    return _memory_dir(tenant_id) / "recall.db"


# ── DB lifecycle ────────────────────────────────────────────────────────────

_DDL_TURNS = """
CREATE TABLE IF NOT EXISTS turns (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    ts                REAL    NOT NULL,
    channel           TEXT    NOT NULL,
    chat_key          TEXT    NOT NULL,
    msg_id            TEXT,
    run_id            TEXT,
    persona           TEXT,
    user_chars        INTEGER NOT NULL,
    asst_chars        INTEGER NOT NULL,
    redacted_classes  TEXT    NOT NULL,
    user_text         TEXT    NOT NULL,
    asst_text         TEXT    NOT NULL
);
"""

_DDL_TURNS_FTS = """
CREATE VIRTUAL TABLE IF NOT EXISTS turns_fts USING fts5(
    user_text, asst_text,
    content='turns', content_rowid='id',
    tokenize='unicode61 remove_diacritics 2'
);
"""

_DDL_TRIGGERS = """
CREATE TRIGGER IF NOT EXISTS turns_ai AFTER INSERT ON turns BEGIN
    INSERT INTO turns_fts(rowid, user_text, asst_text)
      VALUES (new.id, new.user_text, new.asst_text);
END;
CREATE TRIGGER IF NOT EXISTS turns_ad AFTER DELETE ON turns BEGIN
    INSERT INTO turns_fts(turns_fts, rowid, user_text, asst_text)
      VALUES('delete', old.id, old.user_text, old.asst_text);
END;
"""

_DDL_INDEXES = """
CREATE INDEX IF NOT EXISTS idx_turns_channel_chat
    ON turns(channel, chat_key, ts);
CREATE INDEX IF NOT EXISTS idx_turns_ts ON turns(ts);
"""

_conn_lock = threading.Lock()
_conn_cache: dict[str, sqlite3.Connection] = {}


def _connect(tenant_id: str | None = None) -> sqlite3.Connection:
    """Return a per-process SQLite connection for the tenant's DB.

    Lazy mkdir on first connect; sets WAL mode + 0o600 file mode.
    Connections are cached per-process-per-DB-path (thread-safe via
    SQLite's own per-connection lock + a coarse module-level lock for
    the cache itself).
    """
    path = _db_path(tenant_id)
    key = str(path)
    with _conn_lock:
        conn = _conn_cache.get(key)
        if conn is not None:
            return conn
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(path.parent, 0o700)
        except OSError:
            pass
        conn = sqlite3.connect(
            str(path),
            check_same_thread=False,
            isolation_level=None,  # autocommit
            timeout=5.0,
        )
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        # `executescript` is the only way to pass a CREATE TRIGGER ...
        # BEGIN ... END block — naive split-by-semicolon breaks the
        # trigger body open at the END.
        conn.executescript(
            _DDL_TURNS + _DDL_TURNS_FTS + _DDL_TRIGGERS + _DDL_INDEXES
        )
        # Enforce 0o600 unconditionally — both on first creation AND on
        # every subsequent open. This closes the window where a DB file
        # is created with world-readable permissions (e.g. by a crash or
        # an external tool) and is only ever corrected on the next
        # process restart. The self_test CRITICAL check requires mode
        # 0o600; this block is the only write-path that can correct it.
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        _conn_cache[key] = conn
        return conn


def _close_all_connections() -> None:
    """Test-only hook — close every cached connection.

    Lets tempdir-sandboxed E2Es teardown cleanly between cases.
    """
    with _conn_lock:
        for conn in _conn_cache.values():
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass
        _conn_cache.clear()


# ── Audit emit ──────────────────────────────────────────────────────────────

def _audit_path(tenant_id: str | None = None) -> Path:
    """The unified hash-chain audit log for the active tenant."""
    if _tenant_global_dir is not None:
        try:
            return _tenant_global_dir(tenant_id) / "forge" / "audit.jsonl"
        except Exception:  # noqa: BLE001
            pass
    env = os.environ.get("CORVIN_HOME") or os.environ.get("CORVIN_HOME")
    if env:
        base = Path(os.path.expanduser(os.path.expandvars(env)))
    else:
        base = Path.home() / ".corvin"
    return base / "global" / "forge" / "audit.jsonl"


_AUDIT_ALLOWED_FIELDS: dict[str, set[str]] = {
    "memory.turn_indexed": {
        "channel", "chat_key", "msg_id", "persona",
        "user_chars", "asst_chars", "redacted_class_count",
        "redacted_classes",  # list of class names, never values
    },
    "memory.recall_query": {
        "channel", "chat_key", "query_chars", "result_count",
        "caller_persona", "since", "until",
    },
    "memory.indexing_failed": {
        "channel", "chat_key", "msg_id", "reason", "error",
    },
    # GDPR Art. 17 — right-to-erasure audit event for turn deletions.
    # Only metadata (scope + row count) — never text content.
    "memory.turns_forgotten": {
        "channel", "chat_key", "before_ts", "rows_deleted",
    },
}


def _emit_audit(event_type: str, details: dict, tenant_id: str | None) -> None:
    """Emit one audit event into the unified hash chain.

    Validates the detail-key allow-list — extra keys are silently
    dropped (no exception in the indexing hot-path). The whole
    function is wrapped in try/except so a write failure NEVER blocks
    the bridge turn.
    """
    if _audit_writer is None:
        return
    allow = _AUDIT_ALLOWED_FIELDS.get(event_type, set())
    safe_details = {k: v for k, v in details.items() if k in allow}
    try:
        _audit_writer(
            _audit_path(tenant_id),
            event_type,
            details=safe_details,
        )
    except Exception:  # noqa: BLE001
        pass


# ── Public API ──────────────────────────────────────────────────────────────

def index_turn(
    channel: str,
    chat_key: str,
    *,
    user_text: str,
    assistant_text: str,
    msg_id: str = "",
    persona: str = "",
    ts: float | None = None,
    run_id: str = "",
    tenant_id: str | None = None,
) -> dict[str, Any]:
    """Index a (user_text, assistant_text) turn-pair.

    The texts are redacted via :func:`redact_text` BEFORE landing in
    the FTS5 table. Original text never persists.

    Returns a summary dict with the inserted row's metadata. Indexing
    failures are caught, audited as ``memory.indexing_failed``, and
    return ``{"ok": False, "reason": ...}`` — they NEVER raise out of
    this function (the bridge turn must always complete).
    """
    if not channel or not chat_key:
        return {"ok": False, "reason": "missing-required"}
    ts = float(ts if ts is not None else time.time())
    user_chars = len(user_text or "")
    asst_chars = len(assistant_text or "")
    try:
        redacted_user, user_counts = redact_text(user_text or "")
        redacted_asst, asst_counts = redact_text(assistant_text or "")
    except Exception as e:  # noqa: BLE001
        _emit_audit(
            "memory.indexing_failed",
            {
                "channel": channel, "chat_key": chat_key, "msg_id": msg_id,
                "reason": "redact-failed",
                "error": str(e)[:200],
            },
            tenant_id,
        )
        return {"ok": False, "reason": "redact-failed"}
    merged_counts: dict[str, int] = {}
    for k, v in {**user_counts}.items():
        merged_counts[k] = merged_counts.get(k, 0) + v
    for k, v in asst_counts.items():
        merged_counts[k] = merged_counts.get(k, 0) + v
    redacted_classes = sorted(merged_counts.keys())
    try:
        conn = _connect(tenant_id)
        cur = conn.execute(
            "INSERT INTO turns(ts, channel, chat_key, msg_id, run_id, persona,"
            " user_chars, asst_chars, redacted_classes, user_text, asst_text)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                ts, channel, chat_key, msg_id or "", run_id or "", persona or "",
                user_chars, asst_chars,
                ",".join(redacted_classes),
                redacted_user, redacted_asst,
            ),
        )
        row_id = cur.lastrowid
    except sqlite3.OperationalError as e:
        reason = "db-locked" if "locked" in str(e).lower() else "db-error"
        _emit_audit(
            "memory.indexing_failed",
            {
                "channel": channel, "chat_key": chat_key, "msg_id": msg_id,
                "reason": reason, "error": str(e)[:200],
            },
            tenant_id,
        )
        return {"ok": False, "reason": reason}
    except Exception as e:  # noqa: BLE001
        _emit_audit(
            "memory.indexing_failed",
            {
                "channel": channel, "chat_key": chat_key, "msg_id": msg_id,
                "reason": "indexing-failed", "error": str(e)[:200],
            },
            tenant_id,
        )
        return {"ok": False, "reason": "indexing-failed"}
    _emit_audit(
        "memory.turn_indexed",
        {
            "channel": channel,
            "chat_key": chat_key,
            "msg_id": msg_id or "",
            "persona": persona or "",
            "user_chars": user_chars,
            "asst_chars": asst_chars,
            "redacted_class_count": len(redacted_classes),
            "redacted_classes": redacted_classes,
        },
        tenant_id,
    )
    return {
        "ok": True, "row_id": row_id, "ts": ts,
        "user_chars": user_chars, "asst_chars": asst_chars,
        "redacted_classes": redacted_classes,
    }


@dataclass
class Recall:
    """One result from a recall query — redacted text only."""
    ts:               float
    channel:          str
    chat_key:         str
    persona:          str
    msg_id:           str
    run_id:           str
    user_text:        str   # already redacted
    assistant_text:   str   # already redacted
    score:            float

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _escape_fts(query: str) -> str:
    """Make an FTS5 MATCH query safe.

    FTS5 has its own mini-DSL with operators AND / OR / NOT / NEAR
    plus syntactically meaningful quotes / parens. We wrap the
    entire query in double quotes and escape embedded quotes — this
    forces a phrase-style match and removes operator surprise. Power
    users who want raw FTS5 can call ``recall_raw`` (not exposed in
    Phase 28.1).
    """
    q = (query or "").strip()
    if not q:
        return '""'
    return '"' + q.replace('"', '""') + '"'


def recall(
    query: str,
    *,
    channel: str | None = None,
    chat_key: str | None = None,
    since: float | None = None,
    until: float | None = None,
    limit: int = 20,
    caller_persona: str = "",
    tenant_id: str | None = None,
) -> list[Recall]:
    """Query the FTS5 index for matching turn-pairs.

    Scope rules:
      - When *channel* or *chat_key* is None, the query is unscoped
        in that dimension. The caller is responsible for passing the
        right scope (MCP / slash-command pass the current chat).
      - Cross-tenant queries are structurally impossible — the
        ``tenant_id`` argument resolves the DB path BEFORE the
        connection opens.

    Emits ``memory.recall_query`` (INFO) into the audit chain with
    metadata only (query character count, NOT the query text).
    """
    limit = max(1, min(int(limit), 100))
    q = _escape_fts(query)
    sql = (
        "SELECT t.ts, t.channel, t.chat_key, t.persona, t.msg_id,"
        "       t.run_id, t.user_text, t.asst_text,"
        "       bm25(turns_fts) AS score"
        " FROM turns_fts JOIN turns t ON t.id = turns_fts.rowid"
        " WHERE turns_fts MATCH ?"
    )
    params: list[Any] = [q]
    if channel:
        sql += " AND t.channel = ?"
        params.append(channel)
    if chat_key:
        sql += " AND t.chat_key = ?"
        params.append(chat_key)
    if since is not None:
        sql += " AND t.ts >= ?"
        params.append(float(since))
    if until is not None:
        sql += " AND t.ts <= ?"
        params.append(float(until))
    sql += " ORDER BY score ASC LIMIT ?"
    params.append(limit)
    try:
        conn = _connect(tenant_id)
        rows = list(conn.execute(sql, params))
    except sqlite3.OperationalError:
        rows = []
    results = [
        Recall(
            ts=float(r[0]), channel=str(r[1]), chat_key=str(r[2]),
            persona=str(r[3] or ""), msg_id=str(r[4] or ""),
            run_id=str(r[5] or ""), user_text=str(r[6] or ""),
            assistant_text=str(r[7] or ""), score=float(r[8] or 0.0),
        ) for r in rows
    ]
    _emit_audit(
        "memory.recall_query",
        {
            "channel": channel or "",
            "chat_key": chat_key or "",
            "query_chars": len(query or ""),
            "result_count": len(results),
            "caller_persona": caller_persona,
            "since": since if since is not None else 0.0,
            "until": until if until is not None else 0.0,
        },
        tenant_id,
    )
    return results


def forget(
    *,
    channel: str | None = None,
    chat_key: str | None = None,
    before_ts: float | None = None,
    tenant_id: str | None = None,
) -> int:
    """Delete turns matching the filter; return row count purged.

    Phase 28.4 will expose this via ``/forget`` slash-command + daily
    retention sweep. Phase 28.1 ships the function so adapter-level
    `/reset` and tenant teardown can use it. The deletion cascades
    via the AFTER DELETE trigger which removes the matching FTS5
    rows.

    Emits ``memory.turns_forgotten`` (WARNING) into the unified audit
    chain after deletion — GDPR Art. 17 compliance. Metadata only:
    scope (channel, chat_key, before_ts) + row count. Never text.
    """
    sql = "DELETE FROM turns WHERE 1=1"
    params: list[Any] = []
    if channel:
        sql += " AND channel = ?"
        params.append(channel)
    if chat_key:
        sql += " AND chat_key = ?"
        params.append(chat_key)
    if before_ts is not None:
        sql += " AND ts < ?"
        params.append(float(before_ts))
    try:
        conn = _connect(tenant_id)
        cur = conn.execute(sql, params)
        rows_deleted = int(cur.rowcount or 0)
    except sqlite3.OperationalError:
        rows_deleted = 0
    _emit_audit(
        "memory.turns_forgotten",
        {
            "channel": channel or "",
            "chat_key": chat_key or "",
            "before_ts": before_ts if before_ts is not None else 0.0,
            "rows_deleted": rows_deleted,
        },
        tenant_id,
    )
    return rows_deleted


# ── Persona-ACL helper (for the MCP edge + slash-command dispatcher) ──────

def is_recall_permitted_for_persona(profile: dict | None) -> bool:
    """Return True iff the resolved chat_profile / persona dict
    enables recall access for the caller.

    The default is **False** — recall is opt-in per persona via
    ``memory_recall_enabled: true``. Bundle personas
    (``assistant`` / ``research`` / ``coder``) ship the flag in their
    persona JSON; everything else must be enabled explicitly.

    Adapter-internal callers (the indexing hook in ``process_one``)
    do NOT consult this — only the MCP / slash-command edges that
    serve potentially-untrusted recall queries.
    """
    if not isinstance(profile, dict):
        return False
    return bool(profile.get("memory_recall_enabled", False))
