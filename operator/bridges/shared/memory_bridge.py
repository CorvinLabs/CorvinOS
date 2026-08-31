"""ADR-0051 — Layer 29: Worker Memory Bridge.

Two public functions:

    build_context_block(scope, workdir, profile, engine_type,
                        instruction_hint='', max_chars=2000) -> str
        Returns <corvin_context>…</corvin_context> or ''.
        For ClaudeCodeEngine workers, also writes selected memory files
        to the session-scope .claude/memory/ directory (§1 native pass-through).

    harvest_worker_output(scope, instruction, output, engine_id,
                          chat_key, tenant_id) -> None
        Fire-and-forget post-turn harvest: extracts memory entries from
        the worker's output and writes them back into the OS memory pool.
        Runs in a daemon background thread (30 s wall-clock ceiling).

CI lint invariant: this module MUST NOT ``import anthropic``.
"""
from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path
from typing import Any

# ── Best-effort imports ───────────────────────────────────────────────────────

_log = logging.getLogger(__name__)

_shared = Path(__file__).resolve().parent


def _add_to_path(p: Path) -> None:
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)


_add_to_path(_shared)

# Tenant paths
_tenant_global_dir: Any = None
_tenant_sessions_dir: Any = None
try:
    from paths import tenant_global_dir as _tenant_global_dir  # type: ignore
    from paths import tenant_sessions_dir as _tenant_sessions_dir  # type: ignore
except Exception:  # noqa: BLE001
    try:
        from forge.paths import tenant_global_dir as _tenant_global_dir  # type: ignore
        from forge.paths import tenant_sessions_dir as _tenant_sessions_dir  # type: ignore
    except Exception:  # noqa: BLE001
        pass

# User model (L28.2)
_user_model_load: Any = None
_user_model_render: Any = None
try:
    from user_model import load as _user_model_load  # type: ignore
    from user_model import render_block as _user_model_render  # type: ignore
except Exception:  # noqa: BLE001
    pass

# Conversation recall (L28.1)
_recall_fn: Any = None
try:
    from conversation_recall import recall as _recall_fn  # type: ignore
except Exception:  # noqa: BLE001
    pass

# PII redaction
_redact_text: Any = None
try:
    from conversation_recall import redact_text as _redact_text  # type: ignore
except Exception:  # noqa: BLE001
    pass

# Audit writer
_audit_writer: Any = None
try:
    _forge_top = _shared.parent.parent / "forge"
    if _forge_top.is_dir():
        _add_to_path(_forge_top)
    from forge.security_events import write_event as _audit_writer  # type: ignore
except Exception:  # noqa: BLE001
    pass

# Helper model (Haiku site resolution)
_helper_model_args: Any = None
_helper_resolve_bin: Any = None
try:
    from helper_model import claude_args as _helper_model_args  # type: ignore
    from helper_model import resolve_claude_bin as _helper_resolve_bin  # type: ignore
except Exception:  # noqa: BLE001
    pass

# ── Constants ─────────────────────────────────────────────────────────────────

_DEFAULT_MAX_CHARS  = 2000
_CAP_USER_PROFILE   = 600
_CAP_PROJECT_FACTS  = 800
_CAP_SESSION_LEARN  = 600
_SELECTOR_TIMEOUT_S = 10.0
_HARVEST_TIMEOUT_S  = 30.0
_DEFAULT_CLAUDE_BIN = "claude"

_ENV_MAX_CHARS = "CORVIN_CONTEXT_MAX_CHARS"

# Audit
_EVENT_WORKER_HARVEST   = "memory.worker_harvest"
_HARVEST_ALLOWED_FIELDS = frozenset(
    {"channel", "chat_key", "tenant_id", "scope", "entry_count", "engine_id"}
)

_MODE = 0o600

# Harvest validation
_VALID_SCOPES = frozenset({"session", "project", "user"})
_VALID_TYPES  = frozenset({"feedback", "project", "user"})
_VALID_SLUG   = re.compile(r"^[a-z0-9][a-z0-9_-]{0,40}$")

# ── Internal helpers ──────────────────────────────────────────────────────────


def _resolve_max_chars(max_chars: int) -> int:
    try:
        return int(os.environ.get(_ENV_MAX_CHARS, max_chars))
    except (TypeError, ValueError):
        return max_chars


def _parse_scope(scope: str) -> tuple[str, str, str]:
    """Split 'tenant_id:channel:chat_key' into its three parts.

    Returns (tenant_id, channel, chat_key). Gracefully handles missing parts.
    """
    parts = scope.split(":", 2)
    if len(parts) == 3:
        return parts[0], parts[1], parts[2]
    if len(parts) == 2:
        return "", parts[0], parts[1]
    return "", "", scope


def _truncate(text: str, cap: int) -> str:
    if len(text) <= cap:
        return text
    return text[: cap - 1].rstrip() + "…"


def _pii_redact(text: str) -> str:
    if _redact_text is not None:
        try:
            redacted, _ = _redact_text(text)
            return redacted
        except Exception:  # noqa: BLE001
            pass
    return text


def _run_haiku(prompt: str, timeout_s: float, site: str = "") -> str:
    """Run ``claude -p --max-turns 1 --no-tools`` with *prompt* on stdin.

    Returns stdout string. On timeout, unavailability, or any error returns ''.
    Never raises.
    """
    bin_path = _helper_resolve_bin() if _helper_resolve_bin is not None \
        else os.environ.get("CORVIN_CLAUDE_BIN", _DEFAULT_CLAUDE_BIN)
    model_args: list[str] = []
    if _helper_model_args is not None and site:
        try:
            model_args = _helper_model_args(site)
        except Exception:  # noqa: BLE001
            pass
    try:
        proc = subprocess.run(
            [bin_path, "-p", "--max-turns", "1", "--tools", "", *model_args],
            input=prompt,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
        return proc.stdout or ""
    except subprocess.TimeoutExpired:
        return ""
    except FileNotFoundError:
        return ""
    except Exception:  # noqa: BLE001
        return ""


def _emit_audit(audit_path: Path | None, event_type: str, details: dict) -> None:
    if _audit_writer is None or audit_path is None:
        return
    safe = {k: v for k, v in details.items() if k in _HARVEST_ALLOWED_FIELDS}
    try:
        _audit_writer(audit_path, event_type, details=safe)
    except Exception:  # noqa: BLE001
        pass


def _audit_path_for(tenant_id: str | None) -> Path | None:
    if _tenant_global_dir is None:
        return None
    try:
        return _tenant_global_dir(tenant_id) / "forge" / "audit.jsonl"
    except Exception:  # noqa: BLE001
        return None


def _atomic_write(path: Path, content: str, mode: int = _MODE) -> None:
    """Write *content* atomically with a tmp-then-rename pattern."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp.write_text(content, encoding="utf-8")
        try:
            tmp.chmod(mode)
        except OSError:
            pass
        tmp.replace(path)
    except Exception:  # noqa: BLE001
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise


# ── §3 Source-collection helpers ──────────────────────────────────────────────


def _collect_user_profile(
    channel: str, chat_key: str, tenant_id: str | None, cap: int
) -> str:
    if _user_model_load is None or _user_model_render is None:
        return ""
    try:
        model = _user_model_load(channel, chat_key, tenant_id=tenant_id)
        if model is None:
            return ""
        rendered = _user_model_render(model)
        # render_block wraps in <user_context> tags; strip them.
        body = re.sub(r"</?user_context[^>]*>", "", rendered).strip()
        return _truncate(body, cap)
    except Exception:  # noqa: BLE001
        return ""


def _collect_project_facts(
    tenant_id: str | None, instruction_hint: str, cap: int
) -> str:
    if _tenant_global_dir is None:
        return ""
    try:
        global_dir = _tenant_global_dir(tenant_id)
    except Exception:  # noqa: BLE001
        return ""

    hint_words = (
        frozenset(re.findall(r"\w{4,}", instruction_hint.lower()))
        if instruction_hint
        else frozenset()
    )

    fragments: list[str] = []
    for mem_dir in (
        global_dir / "memory",
        global_dir / "memory" / "worker_harvest",
    ):
        if not mem_dir.is_dir():
            continue
        for p in sorted(mem_dir.glob("*.md")):
            if p.name == "MEMORY.md":
                continue
            try:
                text = p.read_text(encoding="utf-8", errors="replace")
                if hint_words:
                    file_words = frozenset(re.findall(r"\w{4,}", text.lower()))
                    if not hint_words & file_words:
                        continue
                fragments.append(text.strip())
            except Exception:  # noqa: BLE001
                continue

    if not fragments:
        return ""
    return _truncate("\n\n".join(fragments), cap)


def _collect_session_learnings(
    channel: str,
    chat_key: str,
    tenant_id: str | None,
    session_mem_dir: Path | None,
    cap: int,
) -> str:
    fragments: list[str] = []

    if session_mem_dir is not None and session_mem_dir.is_dir():
        for p in sorted(session_mem_dir.glob("*.md")):
            if p.name == "MEMORY.md":
                continue
            try:
                fragments.append(p.read_text(encoding="utf-8", errors="replace").strip())
            except Exception:  # noqa: BLE001
                continue

    if _recall_fn is not None:
        try:
            turns = _recall_fn(
                "",
                channel=channel,
                chat_key=chat_key,
                limit=5,
                tenant_id=tenant_id,
            )
            for t in turns:
                u = getattr(t, "user_text", "") or ""
                a = getattr(t, "assistant_text", "") or ""
                if u or a:
                    fragments.append(f"U: {u}\nA: {a}")
        except Exception:  # noqa: BLE001
            pass

    if not fragments:
        return ""
    return _truncate("\n\n".join(fragments), cap)


# ── §2 Haiku compressor ───────────────────────────────────────────────────────


def _compress_block(label: str, raw: str, cap: int) -> str:
    """Compress *raw* to fit in *cap* chars via Haiku; fallback = truncation."""
    if not raw.strip():
        return ""
    if len(raw) <= cap:
        return raw.strip()

    prompt = textwrap.dedent(f"""\
        You are a context compressor. Summarise the following {label} into at most \
{cap} characters of dense, factual prose. Strip all PII (emails, phone numbers, \
IBANs). Output ONLY the compressed text — no wrapper, no commentary.

        === BEGIN {label.upper()} ===
        {raw[:4000]}
        === END {label.upper()} ===
    """)
    compressed = _run_haiku(prompt, _SELECTOR_TIMEOUT_S, site="memory_bridge_compress")
    if compressed.strip():
        return _truncate(compressed.strip(), cap)
    return _truncate(raw.strip(), cap)


# ── §1 CC-worker native pass-through ─────────────────────────────────────────


class WorkerMemoryPathEscape(RuntimeError):
    """Raised when a worker memory write path escapes the session root.

    ADR-0052 F5 — cross-session data isolation. Any path that resolves
    outside the permitted session_mem_dir is rejected and logged CRITICAL.
    """


def _validate_memory_write_path(target: Path, allowed_root: Path) -> None:
    """Raise WorkerMemoryPathEscape if target is outside allowed_root.

    Uses Path.resolve() to defeat ``../`` traversal attempts. Both paths
    are resolved to absolute real paths before comparison.
    """
    try:
        resolved_target = target.resolve()
        resolved_root = allowed_root.resolve()
        resolved_target.relative_to(resolved_root)
    except ValueError:
        _emit_worker_memory_escape_audit(target, allowed_root)
        raise WorkerMemoryPathEscape(
            f"worker memory write path escapes session root: "
            f"{target} not under {allowed_root}"
        )
    except OSError:
        pass


def _emit_worker_memory_escape_audit(target: Path, allowed_root: Path) -> None:
    """Best-effort CRITICAL audit emit for path escape attempts."""
    try:
        import os as _os
        import sys as _sys
        _forge_pkg = Path(__file__).resolve().parents[2] / "forge"
        if str(_forge_pkg) not in _sys.path:
            _sys.path.insert(0, str(_forge_pkg))
        from forge.security_events import write_event as _we
        _corvin = Path(_os.environ.get("CORVIN_HOME")
                        or _os.environ.get("CORVIN_HOME")
                        or Path.home() / ".corvin")
        _ap = _corvin / "global" / "forge" / "audit.jsonl"
        _ap.parent.mkdir(parents=True, exist_ok=True)
        _we(_ap, "worker_memory.path_escape", severity="CRITICAL",
            details={"allowed_root": str(allowed_root)[:300]})
    except Exception:  # noqa: BLE001
        pass


def _write_cc_memory_files(
    session_mem_dir: Path,
    user_profile: str,
    project_facts: str,
    session_learnings: str,
) -> None:
    """Write context blocks as .claude/memory/ files for ClaudeCodeEngine."""
    session_mem_dir.mkdir(parents=True, mode=0o700, exist_ok=True)

    entries = {
        "corvin_user_profile.md": (
            "---\n"
            "name: corvin-user-profile\n"
            "description: User profile injected by OS memory bridge\n"
            "metadata:\n  type: user\n"
            "---\n\n" + user_profile
        ) if user_profile else None,
        "corvin_project_facts.md": (
            "---\n"
            "name: corvin-project-facts\n"
            "description: Project facts injected by OS memory bridge\n"
            "metadata:\n  type: project\n"
            "---\n\n" + project_facts
        ) if project_facts else None,
        "corvin_session_learnings.md": (
            "---\n"
            "name: corvin-session-learnings\n"
            "description: Session learnings injected by OS memory bridge\n"
            "metadata:\n  type: project\n"
            "---\n\n" + session_learnings
        ) if session_learnings else None,
    }

    for fname, content in entries.items():
        path = session_mem_dir / fname
        # ADR-0052 F5 — validate every write path stays inside session_mem_dir
        try:
            _validate_memory_write_path(path, session_mem_dir)
        except WorkerMemoryPathEscape:
            _log.error("memory_bridge: path escape blocked for %s", path)
            continue
        if content is None:
            path.unlink(missing_ok=True)
            continue
        try:
            _atomic_write(path, content)
        except Exception:  # noqa: BLE001
            _log.debug("memory_bridge: failed to write %s", path)


# ── Public API — build_context_block ─────────────────────────────────────────


def build_context_block(
    scope: str,
    workdir: Path,
    profile: dict,
    engine_type: str,
    instruction_hint: str = "",
    max_chars: int = _DEFAULT_MAX_CHARS,
) -> str:
    """Return ``<corvin_context>…</corvin_context>`` or empty string.

    For ``ClaudeCodeEngine`` workers, also writes selected memory files to
    the session-scope ``.claude/memory/`` directory (ADR-0051 §1 native
    pass-through). The returned XML block is additionally written into the
    task-scoped ``CLAUDE.md`` by the delegation layer.

    Args:
        scope:            ``"tenant_id:channel:chat_key"``
        workdir:          Current working directory (used for session-scope path)
        profile:          Resolved ``chat_profile`` dict (persona ACL)
        engine_type:      ``"claude_code"`` | ``"codex"`` | ``"opencode"`` | …
        instruction_hint: Task instruction; used for keyword-based file matching
        max_chars:        Total budget (default 2000, override via env)
    """
    max_chars = _resolve_max_chars(max_chars)
    tenant_id, channel, chat_key = _parse_scope(scope)

    # Proportional per-block caps
    ratio = max_chars / _DEFAULT_MAX_CHARS
    cap_up = int(_CAP_USER_PROFILE * ratio)
    cap_pf = int(_CAP_PROJECT_FACTS * ratio)
    cap_sl = int(_CAP_SESSION_LEARN * ratio)

    # Session-scope .claude/memory/ directory
    session_mem_dir: Path | None = None
    if _tenant_sessions_dir is not None:
        try:
            # chat_key may contain ":" (bridge:chat_id) — keep as-is for the dir name
            session_root = _tenant_sessions_dir(tenant_id or None) / f"{channel}:{chat_key}"
            session_mem_dir = session_root / ".claude" / "memory"
        except Exception:  # noqa: BLE001
            session_mem_dir = None

    # Collect raw material (4× the target cap before compression)
    user_profile_raw = _collect_user_profile(channel, chat_key, tenant_id or None, cap_up * 4)
    project_facts_raw = _collect_project_facts(tenant_id or None, instruction_hint, cap_pf * 4)
    session_learn_raw = _collect_session_learnings(
        channel, chat_key, tenant_id or None, session_mem_dir, cap_sl * 4
    )

    # PII-redact before handing to Haiku
    up_text = _compress_block("user profile", _pii_redact(user_profile_raw), cap_up)
    pf_text = _compress_block("project facts", _pii_redact(project_facts_raw), cap_pf)
    sl_text = _compress_block("session learnings", _pii_redact(session_learn_raw), cap_sl)

    if not any([up_text, pf_text, sl_text]):
        return ""

    # §3 Structured XML block
    parts: list[str] = ["<corvin_context>"]
    if up_text:
        parts.append(f"  <user_profile>{up_text}</user_profile>")
    if pf_text:
        parts.append(f"  <project_facts>{pf_text}</project_facts>")
    if sl_text:
        parts.append(f"  <session_learnings>{sl_text}</session_learnings>")
    parts.append("</corvin_context>")
    block = "\n".join(parts)

    # §1 CC-worker native pass-through
    if engine_type == "claude_code" and session_mem_dir is not None:
        _write_cc_memory_files(session_mem_dir, up_text, pf_text, sl_text)

    return block


# ── Public API — harvest_worker_output ───────────────────────────────────────

_HARVEST_PROMPT = textwrap.dedent("""\
    You are a memory extractor for an AI operating system. Given a task instruction
    and the output from a worker agent, extract factual, reusable memory entries.

    Rules:
    - Only extract clearly stated facts, decisions, user corrections, or codebase learnings.
    - Do NOT invent, infer, or hallucinate entries.
    - Strip all PII (names, emails, phone numbers, IBANs) from entry bodies.
    - Each entry must be self-contained and meaningful without the original context.
    - Assign scope: "session" (this chat only), "project" (reusable across sessions),
      or "user" (user preference, applies across projects).
    - Assign type: "feedback" | "project" | "user"
    - Max 10 entries. Output [] if nothing is worth extracting.

    Output ONLY a JSON array (no prose, no markdown fences):
    [
      {{"scope": "session|project|user", "type": "feedback|project|user", "body": "..."}},
      ...
    ]

    === TASK INSTRUCTION (truncated) ===
    {instruction}

    === WORKER OUTPUT (truncated) ===
    {output}
""")


def _make_slug(body: str, index: int) -> str:
    words = re.findall(r"[a-z0-9]+", body.lower())[:5]
    slug = "-".join(words) or f"entry-{index}"
    slug = slug[:40]
    if not _VALID_SLUG.match(slug):
        slug = f"entry-{index}"
    return slug


def _write_harvest_entry(
    entry: dict,
    index: int,
    session_dir: Path | None,
    tenant_id: str | None,
) -> bool:
    """Write one extracted entry to the appropriate scope directory."""
    scope = entry.get("scope", "")
    etype = entry.get("type", "project")
    body = (entry.get("body", "") or "").strip()

    if scope not in _VALID_SCOPES or etype not in _VALID_TYPES or not body:
        return False

    body = _pii_redact(body)
    if not body:
        return False

    slug = _make_slug(body, index)
    ts = int(time.time())
    filename = f"harvest_{ts}_{index:02d}_{slug}.md"
    content = (
        f"---\n"
        f"name: {slug}\n"
        f"description: Worker-harvested entry (auto, ADR-0051)\n"
        f"metadata:\n  type: {etype}\n"
        f"---\n\n"
        f"{body}\n"
    )

    target: Path | None = None

    if scope == "session":
        if session_dir is not None:
            target = session_dir / ".claude" / "memory" / filename
            # ADR-0052 F5 — path escape guard for session-scoped writes
            allowed_root = session_dir / ".claude" / "memory"
            try:
                _validate_memory_write_path(target, allowed_root)
            except WorkerMemoryPathEscape:
                return False
    elif scope == "project":
        if _tenant_global_dir is not None:
            try:
                target = (
                    _tenant_global_dir(tenant_id)
                    / "memory"
                    / "worker_harvest"
                    / filename
                )
            except Exception:  # noqa: BLE001
                pass
    elif scope == "user":
        # §4.3 user scope → ~/.claude/projects/<cwd-path>/memory/
        try:
            cwd_safe = re.sub(r"[^a-zA-Z0-9._-]", "-", str(Path.cwd())).lstrip("-")
            target = Path.home() / ".claude" / "projects" / cwd_safe / "memory" / filename
        except Exception:  # noqa: BLE001
            pass

    if target is None:
        return False

    try:
        _atomic_write(target, content)
        return True
    except Exception:  # noqa: BLE001
        return False


def _harvest_task(
    scope: str,
    instruction: str,
    output: str,
    engine_id: str,
    chat_key: str,
    tenant_id: str | None,
    cancel_event: threading.Event,
) -> None:
    """Background harvest worker — runs inside a daemon thread."""
    t_start = time.monotonic()

    tid_parsed, channel, ck_parsed = _parse_scope(scope)
    effective_tid = tenant_id or tid_parsed or None
    effective_ck  = chat_key or ck_parsed

    session_dir: Path | None = None
    if _tenant_sessions_dir is not None:
        try:
            session_dir = (
                _tenant_sessions_dir(effective_tid) / f"{channel}:{effective_ck}"
            )
        except Exception:  # noqa: BLE001
            pass

    prompt = _HARVEST_PROMPT.format(
        instruction=instruction[:2048],
        output=output[:4096],
    )

    remaining = _HARVEST_TIMEOUT_S - (time.monotonic() - t_start)
    if remaining <= 2:
        return

    raw = _run_haiku(prompt, min(remaining - 1, _HARVEST_TIMEOUT_S), site="memory_bridge_harvest")

    # Honour L8 cancellation before any writes
    if cancel_event.is_set():
        return

    entries: list[dict] = []
    if raw.strip():
        start = raw.find("[")
        if start >= 0:
            try:
                parsed = json.loads(raw[start:])
                if isinstance(parsed, list):
                    entries = [e for e in parsed if isinstance(e, dict)]
            except json.JSONDecodeError:
                pass

    written = 0
    for i, entry in enumerate(entries[:10]):
        if cancel_event.is_set():
            break
        if time.monotonic() - t_start > _HARVEST_TIMEOUT_S:
            break
        if _write_harvest_entry(entry, i, session_dir, effective_tid):
            written += 1

    _emit_audit(
        _audit_path_for(effective_tid),
        _EVENT_WORKER_HARVEST,
        {
            "channel":     channel,
            "chat_key":    effective_ck,
            "tenant_id":   effective_tid or "",
            "scope":       scope,
            "entry_count": written,
            "engine_id":   engine_id,
        },
    )


# ── Cancellation registry (L8 reset integration) ────────────────────────────

_cancel_events: dict[str, threading.Event] = {}
_cancel_lock = threading.Lock()


def get_cancel_event(chat_key: str) -> threading.Event:
    """Return (creating if needed) the cancellation event for *chat_key*.

    L8 session reset should call this and ``event.set()`` before any rmtree,
    so in-flight harvest tasks discard pending writes.
    """
    with _cancel_lock:
        if chat_key not in _cancel_events:
            _cancel_events[chat_key] = threading.Event()
        return _cancel_events[chat_key]


def clear_cancel_event(chat_key: str) -> None:
    """Remove the cancellation event after L8 reset completes."""
    with _cancel_lock:
        _cancel_events.pop(chat_key, None)


def harvest_worker_output(
    scope: str,
    instruction: str,
    output: str,
    engine_id: str,
    chat_key: str,
    tenant_id: str,
) -> None:
    """Fire-and-forget post-turn memory harvest (ADR-0051 §4).

    Extracts memory-worthy facts from the worker output and writes them back
    into the OS memory pool. Runs in a daemon background thread with a 30 s
    wall-clock ceiling. Never blocks the caller; never raises.

    Args:
        scope:       ``"tenant_id:channel:chat_key"``
        instruction: Original task instruction (truncated internally to 2 KB)
        output:      Worker ``final_text`` (truncated internally to 4 KB)
        engine_id:   Engine identifier (e.g. ``"claude_code"``, ``"codex"``)
        chat_key:    Current chat key — used for L8 cancellation gate
        tenant_id:   Tenant identifier
    """
    if not output or not output.strip():
        return

    cancel_event = get_cancel_event(chat_key)

    label_suffix = (chat_key or "unknown")[-16:]
    t = threading.Thread(
        target=_harvest_task,
        args=(scope, instruction, output, engine_id, chat_key, tenant_id or None, cancel_event),
        daemon=True,
        name=f"mem-harvest-{label_suffix}",
    )
    t.start()
