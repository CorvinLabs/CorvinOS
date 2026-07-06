"""Minimal Web-Chat runtime — ADR-0037 § "New bridge channel `web`".

⚠ Scope of this v1 (Iter 3a, intentionally minimal)
----------------------------------------------------
This is NOT the full bridge-adapter integration. The 5,300-line
``operator/bridges/shared/adapter.py`` owns:
  * persona resolution (bundle + user overrides + auto-routing)
  * compliance gates (disclosure, consent, quota, observer-transcript)
  * path-gate hook activation, audit-chain emissions
  * engine selection (claude / codex / opencode), helper-model split
  * mid-stream /btw inject, transient-HTTP reset, stream-idle watchdog
  * hot-reload of channel settings, MCP materialisation, add_dirs, etc.

Folding that path into a WebSocket without duplicating logic is a
multi-day refactor. ADR-0037 § "Iteration 3a" calls out that this v1
runs a direct ``claude -p --output-format stream-json`` subprocess and
emits a thin audit envelope. The full integration ("web is just another
bridge channel") is queued as an ADR-0037 amendment.

What IS in v1
-------------
* per-session subprocess (one ``claude`` per chat_key)
* ``--continue``-based session persistence across messages (the same
  contract bridges use), so multi-turn conversations work
* stream-json output parsed into normalised events:
    {type: "delta",  text: ...}
    {type: "tool_use", name: ..., input: ...}
    {type: "result", text: ..., usage: {...}}
    {type: "error",  message: ...}
* per-tenant chat workdir under ``<corvin_home>/sessions/web:<sid>/``
  (matches the adapter's chat_key naming convention so a future
  refactor can pick this up unchanged)
* session lifecycle: create / list / delete; chat_key='web:<sid>'

Beyond v1 (ADR-0114)
--------------------
* Delegation path: behind ``spec.web_chat.delegation_enabled`` (tenant
  opt-in, deny-by-default) substantive turns are triaged and dispatched
  to ``ACSRuntime(bridge="web", chat=<sid>)`` — the OS side manages,
  workers inherit the user/tenant model (ADR-0112). Worker progress is
  streamed into the chat WebSocket; the run lands in the session
  workdir so the Audit panel's ACS Workflow Graph renders it live.
  ``/delegate <task>`` forces delegation for one turn. Known M1 limit:
  worker-produced files are NOT yet auto-registered as chat artifacts
  (the artifact scan covers the subprocess path only — ADR-0114 M2.1).

Bridge-parity context (ADR-0114 amendment slice)
------------------------------------------------
The per-turn system prompt now resolves the SAME context the bridge adapter
injects, so a web-console turn behaves like a Discord/WhatsApp turn:
  * persona resolution via the cowork resolver (``_persona_prompt_block``)
  * Layer-12 voice-profile audience shaping, chat-render gated
    (``_voice_audience_block``)
  * Tier-1 user profile + Tier-2 memory index
    (``_user_profile_block`` / ``_memory_index_block``)
Every block is fail-safe: a resolution error degrades to the v1 minimal
prompt instead of breaking the chat.

What is NOT in v1
-----------------
* compliance gates other than authenticated session
* full audit hash-chain integration — the thin `web.turn.*` envelopes
  still go to a SEPARATE side-channel log. Exception (first slice of the
  queued amendment): `os_turn.started / tool_called / completed` ARE
  emitted into the canonical L16 chain per turn (EU AI Act Art. 12/13
  traceability — metadata only, mirrors the bridge adapter's event
  family, consumed by the console `/os-turns` route)
* mid-stream /btw inject (single-shot per request)

Engine routing (round-6 fix)
----------------------------
The console web-chat drives TWO OS engines for the direct (non-delegation)
path, resolved from ``spec.default_engine``:

* ``claude_code`` → the direct ``claude -p --output-format stream-json``
  subprocess path (the historical path; behaviour is byte-for-byte unchanged).
* ``hermes`` → the Layer-22 ``WorkerEngine`` path (``HermesEngine`` → local
  Ollama HTTP, no subprocess, no Anthropic API key). This is the zero-egress /
  NO-API-KEY path the README + first-run SetupGate promote. Before this fix the
  web-chat only drove ``claude_code`` and every Hermes turn hit a
  "switch to Claude Code" dead-end — the no-API-key onboarding produced a
  console that could not answer (round-6 HIGH blocker).

The blocking ``HermesEngine.spawn`` urllib generator runs in a worker thread;
events are pumped into a queue and drained from the asyncio loop via
``asyncio.to_thread`` so the event loop never blocks (mirrors the bridge
adapter's ``_call_hermes_streaming_via_engine``). The FOUR fail-closed
pre-spawn gates (L44/LIP/L34/L35, via ``_spawn_gates.check_console_spawn_or_refusal``)
run for BOTH engines — for the hermes path the gate classifies against
``engine_id=hermes`` so L34/L35 see locality=local / egress=none.

Other engines (opencode / codex / copilot) are still NOT drivable by the
web-chat and surface an honest up-front mismatch message naming the
configured engine.
"""
from __future__ import annotations

import asyncio
import json
import mimetypes
import os
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator

_THIS_DIR = Path(__file__).resolve().parent
_REPO = _THIS_DIR.parents[2]
_FORGE_PATH = _REPO / "operator" / "forge"
if str(_FORGE_PATH) not in sys.path:
    sys.path.insert(0, str(_FORGE_PATH))

from forge import paths as _forge_paths  # noqa: E402
from . import task_manager as _task_manager  # noqa: E402
from . import _spawn_gates  # noqa: E402  — shared fail-closed pre-spawn chokepoint

# Canonical bridge audit chain (L16) — os_turn.* traceability for web turns
# (EU AI Act Art. 12/13). Best-effort import mirroring the `_cowork is not
# None` guard style: the console must come up even when the bridge tree is
# absent. write_event() is flock-protected, so the console appending to the
# same chain as the adapter is safe cross-process.
_BRIDGES_SHARED = _REPO / "operator" / "bridges" / "shared"
_bridge_audit = None
try:
    if str(_BRIDGES_SHARED) not in sys.path:
        sys.path.insert(0, str(_BRIDGES_SHARED))
    import audit as _bridge_audit  # type: ignore  # noqa: E402
except Exception:  # noqa: BLE001
    _bridge_audit = None

# ADR-0171 — universal engine-span audit (role=os for console OS turns).
# Best-effort; a missing module must never break a turn (spans are additive).
try:
    if str(_BRIDGES_SHARED) not in sys.path:
        sys.path.insert(0, str(_BRIDGES_SHARED))
    import engine_span as _espan  # type: ignore  # noqa: E402
except Exception:  # noqa: BLE001
    _espan = None

# Voice-profile loader — MUST be the SAME resolver the console profile route
# (routes/profile.py) writes through and the Discord/WhatsApp pipeline reads,
# so the console-chat annotation pipeline (LERN-ZUGABE + METAPHER) actually sees
# the operator's saved voice_audience_* settings. The module is XDG-aware
# (~/.config/corvin-voice/profile.json when XDG_CONFIG_HOME is set, else
# voice_dir()); reading a hardcoded tenant_home/voice/profile.json here silently
# diverged from the writer (reader != writer) and killed both features.
_voice_profile = None
try:
    if str(_BRIDGES_SHARED) not in sys.path:
        sys.path.insert(0, str(_BRIDGES_SHARED))
    import profile as _voice_profile  # type: ignore  # noqa: E402
except Exception:  # noqa: BLE001
    _voice_profile = None

# Cowork persona resolver (optional on-top plugin) — the SAME resolver the
# bridge adapter uses (operator/cowork/lib/resolver.py) so the console web-chat
# resolves the SAME persona system-prompt the Discord/WhatsApp pipeline does
# instead of running a persona-less prompt (ADR-0114 parity slice). Best-effort,
# mirroring the other bridge-tree imports: absence degrades to "no persona
# block" (the prior v1 behaviour) rather than a crash.
_cowork = None
try:
    _cowork_lib = _REPO / "operator" / "cowork" / "lib"
    if not (_cowork_lib / "resolver.py").is_file():
        # Wheel layout: operator/ lives in the vendored copy, not repo-relative.
        try:
            from ._operator_bootstrap import vendor_operator_root  # noqa: PLC0415
            _vroot = vendor_operator_root()
        except Exception:  # noqa: BLE001
            _vroot = None
        if _vroot is not None:
            _cowork_lib = _vroot / "cowork" / "lib"
    if (_cowork_lib / "resolver.py").is_file():
        if str(_cowork_lib) not in sys.path:
            sys.path.insert(0, str(_cowork_lib))
        import resolver as _cowork  # type: ignore  # noqa: E402
except Exception:  # noqa: BLE001
    _cowork = None

# Tier-2 memory index loader — the SAME module the bridge adapter reads through
# _memory_index_block(). Global / XDG-canonical, like the voice profile. (The
# Tier-1 user-profile block reuses _voice_profile.for_system_prompt(); the
# `profile` module exposes both for_tts_audience() and for_system_prompt().)
_memory_mod = None
try:
    if str(_BRIDGES_SHARED) not in sys.path:
        sys.path.insert(0, str(_BRIDGES_SHARED))
    import memory as _memory_mod  # type: ignore  # noqa: E402
except Exception:  # noqa: BLE001
    _memory_mod = None

# Adaptive OS-engine model (ADR-0112 engine-model split: OS turns run
# Haiku/Sonnet by payload size; workers inherit the user model). Best-effort:
# without the module the subprocess falls back to the CLI default model.
_model_selector = None
try:
    import model_selector as _model_selector  # type: ignore  # noqa: E402
except Exception:  # noqa: BLE001
    _model_selector = None

# Layer-22 WorkerEngine layer (ADR-0001 / ADR-0066). The console web-chat routes
# the OS turn through the SAME engine machinery the bridge adapter uses when the
# tenant picked a non-claude OS engine in Setup. HermesEngine drives local Ollama
# over HTTP (no subprocess, no Anthropic API key) — the zero-egress path the
# README + first-run SetupGate promote. Best-effort import mirroring the other
# bridge-tree imports: absence degrades to the honest "engine not drivable"
# message rather than a crash.
_HermesEngine = None
try:
    if str(_BRIDGES_SHARED) not in sys.path:
        sys.path.insert(0, str(_BRIDGES_SHARED))
    from agents.hermes_engine import HermesEngine as _HermesEngine  # type: ignore  # noqa: E402
except Exception:  # noqa: BLE001
    _HermesEngine = None

import logging  # noqa: E402

_log = logging.getLogger(__name__)

# ── Per-session structured debug log ────────────────────────────────────────
# Writes to <workdir>/chat_debug.jsonl — independent of L16 audit chain.
# Never raises; debug logging must not break production turns.
_dbg_lock = threading.Lock()
_DBG_MAX_BYTES = 2 * 1024 * 1024  # 2 MB per file, then rotate


def _dbg(workdir: Path, event: str, **fields) -> None:
    """Append one debug event to <workdir>/chat_debug.jsonl."""
    path = workdir / "chat_debug.jsonl"
    rec: dict = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "event": event}
    for k, v in fields.items():
        try:
            json.dumps(v)
            rec[k] = v
        except (TypeError, ValueError):
            rec[k] = str(v)
    line = json.dumps(rec, ensure_ascii=False) + "\n"
    try:
        with _dbg_lock:
            if path.exists() and path.stat().st_size > _DBG_MAX_BYTES:
                p1 = path.with_suffix(".jsonl.1")
                if p1.exists():
                    p1.replace(path.with_suffix(".jsonl.2"))
                path.replace(p1)
            with path.open("a", encoding="utf-8") as f:
                f.write(line)
    except Exception:  # noqa: BLE001
        pass


CHANNEL = "web"
_SID_BYTES = 16  # → 22-char url-safe base64

# ── Voice annotation pipeline (LERN-ZUGABE + METAPHER) ────────────────

def _resolve_voice_scripts_dir() -> Path:
    """Locate operator/voice/scripts in source OR wheel layout. In a wheel the
    repo-relative path lands in site-packages (no operator/), so fall back to the
    vendored copy — else the LERN-ZUGABE / METAPHER voice annotation (summarize.py)
    silently no-ops on a pip install (the 'Konsolen-Learning schlug nicht durch'
    class). Mirrors personas.py / landing.py."""
    src = _REPO / "operator" / "voice" / "scripts"
    if src.is_dir():
        return src
    try:
        from ._operator_bootstrap import vendor_operator_root  # noqa: PLC0415
        vroot = vendor_operator_root()
    except Exception:  # noqa: BLE001
        vroot = None
    if vroot is not None:
        vendored = vroot / "voice" / "scripts"
        if vendored.is_dir():
            return vendored
    return src


_SCRIPTS_DIR = _resolve_voice_scripts_dir()
_METAPHER_MARKERS = (
    "Als Bild gesprochen,", "Bildlich gesprochen,",
    "As a picture,", "Think of it like",
)


async def _compute_web_annotation_suffix(text: str, tenant_id: str) -> str:
    """Append LERN-ZUGABE and/or METAPHER suffix mirroring the voice pipeline.

    Reads voice_audience_* from the tenant voice profile.  Returns the raw
    suffix string (no leading separator) or "" when annotations are not
    requested or any step fails.  Never raises.
    """
    if not text or not text.strip():
        return ""
    if _voice_profile is None:
        return ""
    try:
        # Canonical voice profile (global, XDG-aware) — the SAME file the console
        # profile editor writes and the adapter voice pipeline reads. force=True
        # bypasses the in-module cache so a just-saved Learning/Metaphern toggle
        # takes effect on the next turn. (tenant_id is intentionally unused: the
        # voice profile is global today, matching routes/profile.py's writer.)
        raw: dict[str, Any] = _voice_profile.load(force=True) or {}
    except Exception:  # noqa: BLE001
        return ""

    # Layer-12 chat-render gate: the LERN-ZUGABE / METAPHER annex is VOICE-ONLY
    # by default. It only belongs in the (text) chat bubble when the user opted
    # in via voice_audience_chat_render=on — mirroring adapter.py:2829, which
    # gates the bridge's main-reply audience-block injection on the same flag.
    # Without this, the console rendered the annex in text even with chat_render
    # off, diverging from the Discord/WhatsApp text reply.
    try:
        if not _voice_profile.chat_render_enabled():
            return ""
    except Exception:  # noqa: BLE001
        return ""

    want_appendix = int(raw.get("voice_audience_learning") or 0) > 0
    want_metapher = raw.get("voice_audience_metaphors") == "on"
    if not want_appendix and not want_metapher:
        return ""

    summarizer = _SCRIPTS_DIR / "summarize.py"
    if not summarizer.exists():
        return ""

    env = os.environ.copy()
    env["VOICE_HOOK_RECURSION"] = "1"
    annotated = text

    if want_appendix:
        try:
            _in = annotated
            out = await asyncio.to_thread(
                lambda: subprocess.run(
                    ["python3", str(summarizer), "--lang", "de", "--appendix-mode"],
                    input=_in, capture_output=True, text=True,
                    env=env, timeout=60, check=True,
                )
            )
            if out.stdout.strip():
                annotated = out.stdout.strip()
        except Exception:  # noqa: BLE001
            pass

    if want_metapher:
        tail = annotated[-300:] if len(annotated) > 300 else annotated
        if not any(m in tail for m in _METAPHER_MARKERS):
            try:
                _in = annotated
                out = await asyncio.to_thread(
                    lambda: subprocess.run(
                        ["python3", str(summarizer), "--lang", "de", "--metapher-mode"],
                        input=_in, capture_output=True, text=True,
                        env=env, timeout=60, check=True,
                    )
                )
                if out.stdout.strip():
                    annotated = out.stdout.strip()
            except Exception:  # noqa: BLE001
                pass

    suffix = annotated[len(text):]
    return suffix.lstrip() if suffix else ""
_MAX_SESSIONS_PER_TENANT = 50
_WEB_AUDIT_LOG_NAME = "web_chat.jsonl"  # SEPARATE from canonical chain
_TITLE_MAX_CHARS = 120
# Auto-title cap is shorter than the persisted max so the sidebar stays
# readable; manual renames may use the full 120.
_AUTO_TITLE_MAX_CHARS = 60  # kept for reference; word-limit takes precedence
_AUTO_TITLE_WORD_LIMIT = 4


@dataclass
class WebChatSession:
    sid: str
    tenant_id: str
    created_at: float
    last_active_at: float
    title: str = ""
    turn_count: int = 0
    workdir: Path = field(default_factory=Path)

    @property
    def chat_key(self) -> str:
        return f"{CHANNEL}:{self.sid}"


# ── On-disk session store ─────────────────────────────────────────────


def _store_dir(tenant_id: str) -> Path:
    return _forge_paths.tenant_global_dir(tenant_id) / "web_chat" / "sessions"


def _meta_path(tenant_id: str, sid: str) -> Path:
    return _store_dir(tenant_id) / f"{sid}.json"


def _workdir(tenant_id: str, sid: str) -> Path:
    # The dir name is the chat_key (``web:<sid>``). The ``:`` is legal on POSIX
    # but ILLEGAL in a Windows filename → on Windows this is sanitised to
    # ``web_<sid>`` so create_session's mkdir no longer raises WinError 267 (no
    # chat could be created on a fresh Windows install). safe_session_subdir is a
    # POSIX no-op + honours any pre-existing legacy dir, so Linux/macOS are byte-
    # identical (no migration, no reader≠writer drift — every reader calls here).
    return _forge_paths.safe_session_subdir(
        _forge_paths.tenant_sessions_dir(tenant_id), f"{CHANNEL}:{sid}")


def _read_meta(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _write_meta(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    # Open with 0o600 before writing so the file is never world-readable.
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        data = (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")
        os.write(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)
    tmp.replace(path)


def _session_from_meta(d: dict[str, Any], tenant_id: str) -> WebChatSession | None:
    try:
        return WebChatSession(
            sid=d["sid"],
            tenant_id=tenant_id,
            created_at=float(d["created_at"]),
            last_active_at=float(d["last_active_at"]),
            title=d.get("title", "") or "",
            turn_count=int(d.get("turn_count", 0)),
            workdir=Path(d.get("workdir") or _workdir(tenant_id, d["sid"])),
        )
    except (KeyError, ValueError, TypeError):
        return None


# ── Public API ────────────────────────────────────────────────────────


def list_sessions(tenant_id: str) -> list[WebChatSession]:
    d = _store_dir(tenant_id)
    if not d.exists():
        return []
    out: list[WebChatSession] = []
    for f in sorted(d.iterdir()):
        if f.suffix != ".json":
            continue
        meta = _read_meta(f)
        if not isinstance(meta, dict):
            continue
        sess = _session_from_meta(meta, tenant_id)
        if sess is not None:
            out.append(sess)
    out.sort(key=lambda s: s.last_active_at, reverse=True)
    return out


def get_session(tenant_id: str, sid: str) -> WebChatSession | None:
    path = _meta_path(tenant_id, sid)
    if not path.exists():
        return None
    meta = _read_meta(path)
    if not isinstance(meta, dict):
        return None
    return _session_from_meta(meta, tenant_id)


def create_session(tenant_id: str, title: str = "") -> WebChatSession:
    existing = list_sessions(tenant_id)
    if len(existing) >= _MAX_SESSIONS_PER_TENANT:
        # Drop the oldest to keep the working set bounded.
        oldest = min(existing, key=lambda s: s.last_active_at)
        delete_session(tenant_id, oldest.sid)
    sid = secrets.token_urlsafe(_SID_BYTES)
    now = time.time()
    wd = _workdir(tenant_id, sid)
    wd.mkdir(parents=True, exist_ok=True)
    sess = WebChatSession(
        sid=sid,
        tenant_id=tenant_id,
        created_at=now,
        last_active_at=now,
        title=title.strip()[:_TITLE_MAX_CHARS],
        workdir=wd,
    )
    _save(sess)
    return sess


def rename_session(tenant_id: str, sid: str, title: str) -> WebChatSession | None:
    """Set a human-readable title on an existing session.

    Empty / whitespace-only input clears the title (the sidebar then falls
    back to the auto-derived heuristic on the next user turn, and to the
    sid prefix in the meantime).
    """
    sess = get_session(tenant_id, sid)
    if sess is None:
        return None
    sess.title = (title or "").strip()[:_TITLE_MAX_CHARS]
    _save(sess)
    return sess


def _derive_auto_title(prompt: str) -> str:
    """Squeeze a short, sidebar-friendly title out of the first user turn.

    Takes the first _AUTO_TITLE_WORD_LIMIT words from the first non-empty line
    so the sidebar shows a compact topic label rather than a truncated sentence.
    Returns "" if nothing usable falls out — callers MUST handle that.
    """
    for raw_line in (prompt or "").splitlines():
        words = raw_line.split()
        if not words:
            continue
        title_words = words[:_AUTO_TITLE_WORD_LIMIT]
        title = " ".join(title_words).rstrip(" .,:;!?-—–")
        if len(words) > _AUTO_TITLE_WORD_LIMIT:
            title += "…"
        # Word limit alone cannot bound the length: a single pathological
        # token (URL, hash, "x"*200) is ONE word. Hard-cut as a backstop.
        if len(title) > _AUTO_TITLE_MAX_CHARS:
            title = title[:_AUTO_TITLE_MAX_CHARS].rstrip() + "…"
        return title
    return ""


def delete_session(tenant_id: str, sid: str) -> bool:
    path = _meta_path(tenant_id, sid)
    if not path.exists():
        return False
    try:
        path.unlink()
    except OSError:
        return False
    wd = _workdir(tenant_id, sid)
    if wd.exists():
        try:
            # M4: Clean up tasks before removing workdir
            from .task_manager import TaskManager
            tasks_dir = wd / "tasks"
            if tasks_dir.exists():
                tm = TaskManager(tasks_dir)
                tm.cleanup_tasks(f"web:{sid}")
        except Exception:
            pass  # Best-effort cleanup
        try:
            shutil.rmtree(wd)
        except OSError:
            pass
    delete_turns(tenant_id, sid)
    return True


def _save(sess: WebChatSession) -> None:
    payload = {
        "sid":             sess.sid,
        "tenant_id":       sess.tenant_id,
        "created_at":      sess.created_at,
        "last_active_at":  sess.last_active_at,
        "title":           sess.title,
        "turn_count":      sess.turn_count,
        "workdir":         str(sess.workdir),
    }
    _write_meta(_meta_path(sess.tenant_id, sess.sid), payload)


def touch(sess: WebChatSession, *, increment_turn: bool = False) -> None:
    sess.last_active_at = time.time()
    if increment_turn:
        sess.turn_count += 1
    _save(sess)


# ── Subprocess streaming ──────────────────────────────────────────────


def _claude_binary() -> str:
    return os.environ.get("CORVIN_CLAUDE_BIN") or "claude"


# Engine ids the console web-chat can actually drive for an OS turn.
#   * claude_code → the direct `claude -p --output-format stream-json` subprocess
#     path (below). This is the historical path; behaviour is byte-for-byte.
#   * hermes      → the Layer-22 WorkerEngine path (HermesEngine → Ollama HTTP).
#     This is the zero-egress / NO-API-KEY path the README + SetupGate promote;
#     wiring it here is what makes the recommended Hermes onboarding actually
#     answer in the web chat (round-6 blocker). HermesEngine drives Ollama's
#     local HTTP streaming API — no subprocess, no Anthropic credential.
# Any OTHER engine_id (opencode / codex_cli / copilot) is genuinely not yet
# drivable by the console and still gets the honest up-front mismatch message.
_DIRECT_OS_ENGINES = frozenset({"claude_code", "hermes"})

# Human-readable labels for the up-front engine-mismatch message. Mirrors
# routes/engine.py::_ENGINE_METADATA labels so the chat names the engine the
# operator picked in Setup. Unknown ids fall back to a titleised id.
_ENGINE_LABELS = {
    "claude_code": "Claude Code",
    "codex_cli": "Codex CLI",
    "opencode": "OpenCode",
    "hermes": "Hermes",
    "copilot": "GitHub Copilot",
}


def _engine_label(engine_id: str) -> str:
    return _ENGINE_LABELS.get(engine_id) or engine_id.replace("_", " ").title()


def _configured_os_engine(tenant_id: str) -> str:
    """Resolve the tenant's configured OS engine (spec.default_engine).

    Mirrors the adapter's resolution floor: tenant spec.default_engine →
    "claude_code". Returns the canonical engine_id. Empty / unset → claude_code,
    matching engine_pref.py and the legacy default-spawn contract.
    """
    val = _tenant_spec(tenant_id).get("default_engine")
    if isinstance(val, str) and val.strip():
        return val.strip()
    return "claude_code"


def _effective_os_engine(tenant_id: str) -> str:
    """Like _configured_os_engine but with automatic Hermes fallback.

    When the tenant has claude_code configured (or defaulted) but the
    claude binary is absent — typical on a fresh Windows install where
    Claude Code was not installed — and the HermesEngine module is
    available, we transparently route to Hermes instead of surfacing
    a raw "claude binary not found" error.  The user gets a working
    response; they can switch to Claude Code later via Settings → Engines.
    """
    engine = _configured_os_engine(tenant_id)
    if engine != "claude_code":
        return engine
    binary = _claude_binary()
    # For absolute paths (CORVIN_CLAUDE_BIN set explicitly) check file existence
    # and executability — shutil.which only searches PATH and skips absolute paths,
    # so a dangling absolute path would wrongly look "found".
    if os.path.isabs(binary):
        claude_missing = not (os.path.isfile(binary) and os.access(binary, os.X_OK))
    else:
        claude_missing = shutil.which(binary) is None
    if claude_missing:
        # Always fall back to hermes — even if _HermesEngine failed to import
        # (vendored path issue on wheel installs). The hermes dispatch path will
        # surface a clearer "Ollama not running" error if needed, which is far
        # more actionable than "claude binary not found".
        return "hermes"
    return engine


def _engine_unavailable_message(engine_id: str) -> str | None:
    """Up-front guard for the direct-subprocess path (#8).

    Returns a user-facing chat message (DE+EN) when this v1 runtime cannot
    drive the configured engine — either because the operator selected a
    non-claude OS engine in Setup, or because the `claude` binary is missing
    on PATH. Returns None when the claude path is good to go.

    This is the MINIMUM-acceptable fix per the runtime's documented scope:
    the console web-chat does NOT yet route through the WorkerEngine layer
    (folding that path in is the queued ADR-0037 amendment / ADR-0114 M3),
    so instead of a raw "claude binary not found" we name the configured
    engine and point the operator at the Engines page.
    """
    # 1. hermes selected → the Layer-22 WorkerEngine path drives it (no claude
    #    binary, no API key). Drivable iff the engine module imported. A missing
    #    module is an installation defect, not a "switch to Claude Code" nudge.
    if engine_id == "hermes":
        if _HermesEngine is None:
            return (
                "Die Engine **Hermes** ist ausgewählt, aber die WorkerEngine-"
                "Schicht konnte nicht geladen werden. Prüfe die Installation "
                "(operator/bridges/shared/agents) und die Engine-Einrichtung "
                "unter Einstellungen → Engines.\n\n"
                "The **Hermes** engine is selected, but the WorkerEngine layer "
                "could not be loaded. Check the installation "
                "(operator/bridges/shared/agents) and the engine setup on the "
                "Settings → Engines page."
            )
        return None  # Hermes is drivable — handled by the Hermes branch below.

    # 2. Genuinely-unsupported OS engine selected in Setup (opencode / codex /
    #    copilot) → the console cannot drive it yet. Name it honestly and point
    #    at the Engines page or the delegation / Agentic Compute paths.
    if engine_id not in _DIRECT_OS_ENGINES:
        label = _engine_label(engine_id)
        return (
            f"Die Web-Konsole ist auf die Engine **{label}** eingestellt "
            f"(`spec.default_engine = {engine_id}`), aber der Web-Chat führt "
            f"OS-Turns derzeit nur über Claude Code und Hermes aus. Wechsle die "
            f"Engine unter Einstellungen → Engines auf „Claude Code“ oder "
            f"„Hermes“, oder nutze für {label} die Delegations- bzw. "
            f"Agentic-Compute-Pfade.\n\n"
            f"The web console is configured to use the **{label}** engine "
            f"(`spec.default_engine = {engine_id}`), but the web chat currently "
            f"runs OS turns through Claude Code and Hermes only. Switch the "
            f"engine to “Claude Code” or “Hermes” on the Settings → Engines "
            f"page, or use the delegation / Agentic Compute paths for {label}."
        )
    # 3. claude selected but the binary is absent or not executable.
    binary = _claude_binary()
    if os.path.isabs(binary):
        _claude_bad = not (os.path.isfile(binary) and os.access(binary, os.X_OK))
    else:
        _claude_bad = shutil.which(binary) is None
    if _claude_bad:
        return (
            f"Die Engine **Claude Code** ist ausgewählt, aber das `{binary}` "
            f"CLI wurde nicht gefunden. Installiere die Claude CLI (oder setze "
            f"`CORVIN_CLAUDE_BIN`) und prüfe die Engine-Einrichtung unter "
            f"Einstellungen → Engines.\n\n"
            f"The **Claude Code** engine is selected, but the `{binary}` CLI "
            f"was not found. Install the Claude CLI (or set `CORVIN_CLAUDE_BIN`) "
            f"and check the engine setup on the Settings → Engines page."
        )
    return None


def get_engine_unavailable_message(tenant_id: str) -> str | None:
    """Public helper: return a user-facing message if the tenant's OS engine cannot
    be driven by the web chat, else None.  Used by the WebSocket handler to guard
    the quota charge — no turn should be billed when the engine isn't even set up.
    """
    return _engine_unavailable_message(_effective_os_engine(tenant_id))


_WEB_CHAT_SYSTEM_PROMPT = (
    "When saving any output files (images, PDFs, data files, SVGs, code) during this session, "
    "always write them to the CURRENT WORKING DIRECTORY using relative paths "
    "(e.g. ./dog.svg, ./output.png, ./report.pdf). "
    "Do NOT write to the playground repository or any absolute path outside the current directory. "
    "Files saved in the current directory are automatically detected and displayed in the web chat.\n\n"
    # Belt-and-suspenders: the global CLAUDE.md and the bridge system_prompt_for()
    # both now say "respond in the user's language", but we reinforce it here to
    # ensure the web chat always auto-detects correctly even if those change.
    "LANGUAGE: Detect the user's language automatically and reply in the same language. "
    "German message → German reply. English message → English reply. "
    "Never switch languages unless the user explicitly requests it."
)

# Cap how many uploaded files we enumerate in the system-prompt manifest so a
# session with many attachments cannot bloat the prompt unboundedly. Files past
# the cap are still on disk and summarised by a trailing "… and N more" line.
_ATTACH_MANIFEST_MAX = 50


def _attachment_manifest(sess: WebChatSession) -> str:
    """Build a system-prompt block listing the files the user uploaded into this
    session's ``attachments/`` directory, with ABSOLUTE paths.

    Sourced from disk at turn time (NOT from the frontend message text), so it is
    present on EVERY turn — including follow-up questions where the user does not
    re-attach — and is immune to any frontend change. Absolute paths make it
    independent of the subprocess cwd. Returns ``""`` when there are no uploads,
    so a normal chat turn is unaffected. This is the robust, load-bearing channel
    that tells the engine the uploaded files exist and are readable; the frontend
    text header is now only a UI affordance, not the functional path.
    """
    try:
        attach_dir = sess.workdir / "attachments"
        if not attach_dir.is_dir():
            return ""
        files = sorted(
            p for p in attach_dir.iterdir()
            if p.is_file() and not p.name.startswith(".")
        )
    except OSError:
        return ""
    if not files:
        return ""
    lines: list[str] = []
    for p in files[:_ATTACH_MANIFEST_MAX]:
        try:
            size = p.stat().st_size
        except OSError:
            continue
        mime = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
        lines.append(f"- {p} ({size / 1024:.1f} KB, {mime})")
    if not lines:
        return ""
    extra = ""
    if len(files) > _ATTACH_MANIFEST_MAX:
        extra = (f"\n- … and {len(files) - _ATTACH_MANIFEST_MAX} more file(s) in "
                 f"{attach_dir}")
    return (
        "\n\nUPLOADED FILES — the user has attached the following file(s) to this "
        "chat session. They exist on the local filesystem at the absolute paths "
        "below and you CAN open them. Whenever the user refers to \"the file\", "
        "\"the attachment\", \"this CSV/PDF/image\", \"the document\", or asks you "
        "to analyse, summarise, or plot uploaded data, READ the relevant path with "
        "the Read tool (or the Bash tool for large or binary files) BEFORE "
        "answering — never claim you cannot access an uploaded file.\n"
        + "\n".join(lines) + extra
    )


# Default OS-turn persona for the console web-chat. v1 hardcoded "assistant"
# with no resolution; the ADR-0114 parity slice resolves it through the SAME
# cowork resolver the bridge adapter uses so the console inherits the persona
# role, voice audience shaping and Tier-1/Tier-2 memory context.
_WEB_CHAT_PERSONA = "assistant"


def _persona_prompt_block() -> str:
    """Resolve the OS-turn persona via cowork and return its system-prompt text
    (``append_system``, with ``system_prompt`` as fallback) as an appendable
    block. Empty string when cowork is unavailable or the persona has no prompt.
    Never raises — a failure here must NOT break the console chat (fail-safe,
    not fail-closed)."""
    if _cowork is None:
        return ""
    try:
        merged = _cowork.resolve(_WEB_CHAT_PERSONA, overrides={})
        if not isinstance(merged, dict):
            return ""
        text = (merged.get("append_system")
                or merged.get("system_prompt") or "").strip()
        return ("\n\n" + text) if text else ""
    except Exception:  # noqa: BLE001
        return ""


def _voice_audience_block() -> str:
    """Layer-12 voice-profile audience block, mirroring adapter.py:2463-2470.
    TTS-only by default; only injected into the (text) console prompt when the
    user opted in via ``voice_audience_chat_render=on`` — the SAME gate the
    bridge applies. Never raises."""
    if _voice_profile is None:
        return ""
    try:
        if not _voice_profile.chat_render_enabled():
            return ""
        aud = _voice_profile.for_tts_audience("de")
        return ("\n\n" + aud) if aud else ""
    except Exception:  # noqa: BLE001
        return ""


def _user_profile_block() -> str:
    """Tier-1 user profile block (global / XDG-canonical) — the SAME module the
    bridge adapter renders via its own _user_profile_block(). The returned text
    already carries its own leading separator. Never raises."""
    if _voice_profile is None:
        return ""
    try:
        return _voice_profile.for_system_prompt() or ""
    except Exception:  # noqa: BLE001
        return ""


def _memory_index_block() -> str:
    """Tier-2 memory index block (topic files + one-line summaries) — the SAME
    module the bridge adapter renders via its own _memory_index_block(). The
    returned text already carries its own leading separator. Never raises."""
    if _memory_mod is None:
        return ""
    try:
        return _memory_mod.for_system_prompt() or ""
    except Exception:  # noqa: BLE001
        return ""


def _turn_system_prompt(sess: WebChatSession) -> str:
    """Base web-chat system prompt + per-turn uploaded-file manifest, plus the
    bridge-parity context blocks (ADR-0114): the resolved persona role, the
    Layer-12 voice-profile audience shaping, the Tier-1 user profile and the
    Tier-2 memory index. Each added block is fail-safe (the helper swallows its
    own errors) so any failure degrades to the v1 minimal prompt rather than
    breaking the console chat."""
    return (
        _WEB_CHAT_SYSTEM_PROMPT
        + _attachment_manifest(sess)
        + _persona_prompt_block()
        + _user_profile_block()
        + _memory_index_block()
        + _voice_audience_block()
    )


_VALID_WEB_PERMISSION_MODES = {"default", "plan", "acceptEdits", "bypassPermissions"}


def _web_permission_mode(tenant_id: str) -> str | None:
    """Resolve the web-chat OS-turn permission mode.

    Deny-by-default is the *wrong* default here: the web console has no
    interactive permission-prompt UI, so a real ``--permission-mode`` (default/
    plan/acceptEdits) leaves headless ``-p`` tool-permission requests with
    nothing to answer them and they hang — even for files inside the session's
    own cwd. Mirror the rest of the system (ClaudeCodeEngine's ``None`` default
    and task_worker_pool's ``permission_mode="bypassPermissions"``): skip prompts
    unless a tenant explicitly opts into a stricter mode via
    ``spec.web_chat.permission_mode``. corvinOS's real guardrails are the L10
    path-gate, L34/L35 flow/egress guards and the L44 house-rules — not the SDK
    prompt.
    """
    wc = _tenant_spec(tenant_id).get("web_chat") or {}
    mode = wc.get("permission_mode")
    if isinstance(mode, str) and mode in _VALID_WEB_PERMISSION_MODES:
        return mode
    return None  # → --dangerously-skip-permissions


def _web_workspace_roots(tenant_id: str) -> list[str]:
    """Extra directories the web-chat agent may touch, beyond the session cwd.

    Fix direction A from the permission bug report: configure a workspace root
    (or several) ONCE per tenant via ``spec.web_chat.workspace_roots`` and every
    new session inherits it as an allowed ``--add-dir`` — so access to e.g.
    ``C:\\Users\\<user>\\projects`` works reliably in this and future sessions
    without any interactive grant.
    """
    wc = _tenant_spec(tenant_id).get("web_chat") or {}
    roots = wc.get("workspace_roots") or wc.get("additional_dirs") or []
    if isinstance(roots, str):
        roots = [roots]
    out: list[str] = []
    for r in roots:
        if isinstance(r, str) and r.strip():
            out.append(os.path.expanduser(r.strip()))
    return out


def _build_args(sess: WebChatSession, *, resume: bool, model: str | None = None) -> list[str]:
    """Build a ``claude -p`` invocation for this turn.

    Resume mode uses ``--continue`` so the per-workdir session state
    carries across turns. First turn falls back to a fresh subprocess.
    The --append-system-prompt ensures output files land in the session
    workdir (not the playground repo) so artifact detection works.

    Permission handling (the fresh-install hang fix): the web console has no
    interactive permission-prompt UI, so we must NOT run in the CLI's default
    (interactive) permission mode under ``-p``. We mirror the bridge/task-worker
    default — skip prompts unless the tenant opts into a stricter mode — and
    always register the session workdir (plus any configured workspace roots) as
    allowed ``--add-dir`` directories so the Bash/PowerShell working-directory
    sandbox agrees with the file-tool layer.
    """
    binary = _claude_binary()
    # On Windows, shutil.which() may resolve the npm-installed claude to a
    # .cmd shim (e.g. claude.cmd). asyncio.create_subprocess_exec cannot
    # start .cmd files directly — they must be wrapped in `cmd /c`.
    if sys.platform == "win32" and not os.path.isabs(binary):
        resolved = shutil.which(binary)
        if resolved and resolved.lower().endswith((".cmd", ".bat")):
            args: list[str] = ["cmd", "/c", resolved]
        else:
            args = [resolved or binary]
    else:
        args = [binary]
    args += ["-p",
             "--output-format", "stream-json",
             "--verbose",
             "--append-system-prompt", _turn_system_prompt(sess)]

    # Permission mode: None → skip prompts (default); else a real mode.
    perm_mode = _web_permission_mode(sess.tenant_id)
    if perm_mode is None or perm_mode == "bypassPermissions":
        args.append("--dangerously-skip-permissions")
    else:
        args += ["--permission-mode", perm_mode]

    # Always allow the session's own working directory, plus any tenant-
    # configured workspace roots, for both the file-tool and Bash sandbox layers.
    args += ["--add-dir", str(sess.workdir)]
    for d in _web_workspace_roots(sess.tenant_id):
        args += ["--add-dir", d]

    if model:
        args.extend(["--model", model])
    if resume:
        args.append("--continue")
    return args


# ── ADR-0114 — Web-Chat Delegation Path ──────────────────────────────
# OS turn = management (triage, adaptive Haiku/Sonnet); substantive tasks
# are dispatched to ACS workers which inherit the user/tenant model
# (ADR-0112). Opt-in per tenant: spec.web_chat.delegation_enabled.

_DELEGATE_PREFIX = "/delegate"

_DELEGATION_BUDGET_DEFAULTS = {
    "max_loops": 500,         # 3 was too tight: 1 worker-timeout burn + 2 parse-error burns = budget gone
    "max_depth": 4,           # recursive worker-delegation depth (M4) — NOT a loop counter like the
                              # other fields; 200 was an accidental blanket-scale-up in a47c6d3 that
                              # broke every delegation (acs_validator R32 caps this at 10). 4 matches
                              # the ACS runtime's own built-in default for recursive delegation depth.
    "max_total_workers": 400,
    "max_wall_time": 360000,
    "timeout_seconds": 360000,  # 100 h — allows complex multi-file tasks to complete
    "max_worker_turns": 10000,  # acs_runtime default was 5 → workers hit max_turns mid-tool-use
                                # on explore/implement tasks → error_max_turns → confidence=0.0
                                # → "Delegation fehlgeschlagen: unknown error" in web console.
}

# Triage heuristic vocabulary (deterministic, 0 ms, no API — same rationale
# as auto-routing's default heuristic mode).
# M3 quality pass: split into STRONG (always delegate, even short) and WEAK
# (delegate only when long or multi-step). "review", "debug", "refactor",
# "test", "fix" are strong — even a 3-word command is substantive work.
# Triage heuristics (M3): regex patterns with word-boundary anchors to avoid
# false-positives like "latest" → "test", "prefix" → "fix", "contest" → "test".
# Strong verbs always delegate regardless of prompt length.
# Weak verbs delegate only when combined with length ≥160 or a multi-step marker.
_TRIAGE_STRONG_RE = re.compile(
    r"\b("
    r"überprüfe|review|reviewe|code[\s\-]?review"
    r"|debugge|debug"
    r"|refaktoriere|refactor"
    r"|teste|testen"            # German imperative only; bare "test" is too ambiguous
    r"|behebe|behebt|beheben|fix"
    r"|migriere|migrate"
    r"|deploye|deploy"
    r")\b",
    re.IGNORECASE,
)
_TRIAGE_VERB_RE = re.compile(
    r"\b("
    r"analysiere|analyze|analyse"
    r"|erstelle?|create"
    r"|baue|build"
    r"|implementiere|implement"
    r"|generiere|generate"
    r"|entwickle|develop"
    r"|recherchiere|research"
    r"|vergleiche|compare"
    r"|schreibe|write"
    r"|entwerfe|design"
    r"|erkläre|erklaere|explain"
    r"|fasse|summarize|summarise"
    r")\b",
    re.IGNORECASE,
)
_TRIAGE_MULTI_RE = re.compile(
    r"\b("
    r"und dann|anschließend|danach|mehrere|parallel"
    r"|schritte|steps|multiple"
    r"| dann(?=\s|$)"
    r"|then\b"
    r")\b",
    re.IGNORECASE,
)

# --- Console chat inline-artifact gate -------------------------------------
# A file Claude (or a delegated ACS run) writes is surfaced into the chat as an
# inline artifact iff the console frontend can render it. This MUST stay in sync
# with the render branches of `ArtifactCard` in
# `web-next/src/pages/chat.tsx` — anything renderable there must pass here, or
# the file is silently dropped before it ever reaches the browser. (Conversely,
# the gate is deliberately narrower than "every text/* file" so incidental
# source files Claude writes — .py/.js/.ts → text/x-* — do not spam the chat.)
_ARTIFACT_MIME_PREFIXES = ("image/", "audio/", "video/")
_ARTIFACT_MIME_EXACT = frozenset({
    "application/pdf", "application/json",
    "text/html", "text/csv", "text/plain", "text/markdown",
})
# Extension fallback for media/data types that mimetypes.guess_type() may not
# resolve on a given platform (e.g. .opus/.flac/.mkv/.md), mapped to the mime
# the frontend expects. Mirrors the ext lists in ArtifactCard.
_ARTIFACT_EXT_FALLBACK = {
    # images
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".gif": "image/gif", ".webp": "image/webp", ".svg": "image/svg+xml",
    ".bmp": "image/bmp",
    # audio
    ".mp3": "audio/mpeg", ".wav": "audio/wav", ".ogg": "audio/ogg",
    ".oga": "audio/ogg", ".m4a": "audio/mp4", ".flac": "audio/flac",
    ".aac": "audio/aac", ".opus": "audio/opus", ".weba": "audio/webm",
    # video
    ".mp4": "video/mp4", ".webm": "video/webm", ".mov": "video/quicktime",
    ".mkv": "video/x-matroska", ".m4v": "video/mp4", ".ogv": "video/ogg",
    # documents / data
    ".pdf": "application/pdf", ".html": "text/html", ".htm": "text/html",
    ".csv": "text/csv", ".json": "application/json",
    ".txt": "text/plain", ".md": "text/markdown", ".sql": "text/plain",
}


def _artifact_mime(fpath: Path) -> str | None:
    """Return the mime to surface ``fpath`` as an inline chat artifact, else None.

    Single source of truth for the artifact gate, shared by the direct
    subprocess path and the ACS-delegation path. Resolves the mime via
    ``mimetypes`` first; if that misses (or returns a non-renderable ``text/x-*``
    type), falls back to a known media/data extension so platform gaps in the
    mimetypes DB never drop a file the console can render.
    """
    mime, _ = mimetypes.guess_type(str(fpath))
    if mime and (mime.startswith(_ARTIFACT_MIME_PREFIXES) or mime in _ARTIFACT_MIME_EXACT):
        return mime
    return _ARTIFACT_EXT_FALLBACK.get(fpath.suffix.lower())


# ACS internal directories / root files that are never user artifacts.
# Used by both the M1 post-run scan and the M2 live-streaming poll.
_ACS_SKIP_DIRS = frozenset({
    "traces", "iterations", "workers", "gate_results", "subtasks",
})
_ACS_SKIP_ROOT_FILES = frozenset({"manifest.json", "result.json"})


def _acs_artifact_label(fpath: Path, scan_root: Path) -> str | None:
    """Return a short M5 provenance label for an ACS artifact, or None.

    The label is attached to the WebSocket artifact event so the frontend
    can display a small badge (e.g. "Graph", "live").
    """
    if fpath.name == "acs_delegation_graph.png":
        return "Graph"
    return None


def _render_acs_graph(scan_root: Path) -> Path | None:
    """Render the ACS delegation topology as a PNG (M3 — ADR-0170).

    Reads workers/, iterations/, and subtasks/ from ``scan_root``, draws a
    hierarchical tree with matplotlib (optional dependency), and saves the
    result to ``scan_root/output/acs_delegation_graph.png``.

    Returns the output path on success, None when matplotlib is unavailable
    or there is no worker data to visualise.
    """
    try:
        import matplotlib  # type: ignore[import]
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt  # type: ignore[import]
        import matplotlib.patches as mpatches  # type: ignore[import]
    except ImportError:
        return None

    workers_dir = scan_root / "workers"
    iterations_dir = scan_root / "iterations"
    subtasks_dir = scan_root / "subtasks"

    if not workers_dir.is_dir():
        return None

    # ── Iteration metadata ───────────────────────────────────────────────────
    iteration_data: dict[int, dict] = {}
    if iterations_dir.is_dir():
        for iter_file in sorted(iterations_dir.glob("iter_*.json")):
            try:
                data = json.loads(iter_file.read_text())
                n = int(iter_file.stem.split("_")[-1])
                iteration_data[n] = data
            except Exception:
                pass

    # ── Workers per iteration ────────────────────────────────────────────────
    workers_by_iter: dict[int, list[dict]] = {}

    def _iter_num_from_name(name: str) -> int:
        for part in name.split("_"):
            if part.startswith("it") and part[2:].isdigit():
                return int(part[2:])
            if part.startswith("iter") and part[4:].isdigit():
                return int(part[4:])
        return 0

    for entry in sorted(workers_dir.iterdir()):
        n = _iter_num_from_name(entry.name)
        if entry.is_file() and entry.suffix == ".json":
            try:
                data = json.loads(entry.read_text())
            except Exception:
                data = {}
            data.setdefault("worker_id", entry.stem)
            data.setdefault("type", "worker")
            workers_by_iter.setdefault(n, []).append(data)
        elif entry.is_dir():
            manifest_f = entry / "manifest.json"
            try:
                data = json.loads(manifest_f.read_text()) if manifest_f.exists() else {}
            except Exception:
                data = {}
            data.setdefault("worker_id", entry.name)
            data.setdefault("type", "sub_manager")
            workers_by_iter.setdefault(n, []).append(data)

    if not workers_by_iter:
        return None

    # ── Figure layout ────────────────────────────────────────────────────────
    num_iters = len(workers_by_iter)
    max_workers = max(len(ws) for ws in workers_by_iter.values())
    fig_w = max(10.0, max_workers * 2.5 + 2.0)
    fig_h = max(5.0, num_iters * 3.2 + 2.5)

    fig, ax = plt.subplots(figsize=(fig_w, fig_h))
    ax.set_xlim(0, fig_w)
    ax.set_ylim(0, fig_h)
    ax.axis("off")

    STATUS_COLOR = {"success": "#16a34a", "failed": "#dc2626", "error": "#ea580c"}
    GRAY = "#6b7280"

    def _box(x: float, y: float, w: float, h: float,
              label: str, sub: str = "", color: str = "#3b82f6") -> None:
        rect = mpatches.FancyBboxPatch(
            (x - w / 2, y - h / 2), w, h,
            boxstyle="round,pad=0.08", linewidth=1.2,
            edgecolor=color, facecolor=color + "28",
        )
        ax.add_patch(rect)
        ax.text(x, y + (0.12 if sub else 0.0), label,
                ha="center", va="center", fontsize=7.5, fontweight="bold", color=color)
        if sub:
            ax.text(x, y - 0.22, sub,
                    ha="center", va="center", fontsize=6.0, color=GRAY)

    cx = fig_w / 2
    mgr_y = fig_h - 0.9
    _box(cx, mgr_y, 3.0, 0.65, "ACS Manager", color="#7c3aed")

    for idx, (iter_n, workers) in enumerate(sorted(workers_by_iter.items())):
        iter_y = mgr_y - 1.4 - idx * 3.0
        idata = iteration_data.get(iter_n, {})
        decision = idata.get("decision", "DELEGATE")
        conf = idata.get("confidence", 0.0)
        iter_label = f"Iter {iter_n + 1}  [{decision} {int(conf * 100)}%]"
        _box(cx, iter_y, 3.6, 0.58, iter_label, color="#d97706")
        ax.plot([cx, cx], [mgr_y - 0.33, iter_y + 0.29],
                color=GRAY, lw=0.8, ls="--")

        n_w = len(workers)
        spacing = fig_w / (n_w + 1)
        worker_y = iter_y - 1.45
        for w_i, wdata in enumerate(workers):
            wx = spacing * (w_i + 1)
            wid = wdata.get("worker_id", f"w{w_i}")
            wstatus = wdata.get("status", "")
            wconf = wdata.get("confidence", 0.0)
            wtype = wdata.get("type", "worker")
            wlabel = (wid[:18] + "…") if len(wid) > 18 else wid
            wsub = f"{int(wconf * 100)}%" if wconf else ""
            if wstatus in STATUS_COLOR:
                wcolor = STATUS_COLOR[wstatus]
            elif wtype == "sub_manager":
                wcolor = "#7c3aed"
            else:
                wcolor = "#3b82f6"
            box_w = min(2.0, spacing - 0.3)
            _box(wx, worker_y, box_w, 0.60, wlabel, wsub, color=wcolor)
            ax.plot([cx, wx], [iter_y - 0.29, worker_y + 0.30],
                    color=GRAY, lw=0.7, ls=":")

    fig.suptitle("ACS Delegation Graph", fontsize=10, fontweight="bold", y=0.99)
    plt.tight_layout(rect=[0, 0, 1, 0.97])

    out_dir = scan_root / "output"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "acs_delegation_graph.png"
    try:
        plt.savefig(out_path, dpi=120, bbox_inches="tight")
    finally:
        plt.close(fig)

    return out_path


def compute_inbox_notify(
    workdir: Path,
    task_id: str,
    description: str,
    status: str,
    artifact_paths: list[str],
) -> None:
    """Write a compute-task completion notification to a session's inbox (M4).

    L24 / L25 compute layers call this on task completion.
    ``artifact_paths`` must be relative to ``workdir``.
    The notification is drained and surfaced in chat at the user's next turn.
    """
    inbox = workdir / "compute_inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    payload = {
        "task_id": task_id,
        "description": description,
        "status": status,
        "artifact_paths": artifact_paths,
        "completed_at": int(time.time()),
    }
    # Use atomic fsync+rename so _drain_compute_inbox never sees a partial
    # JSON from a process kill mid-write (partial files get stuck in inbox/).
    _write_meta(inbox / f"{task_id}_result.json", payload)


def _drain_compute_inbox(sess: "WebChatSession") -> list[dict[str, Any]]:
    """Return stream events for pending compute-task notifications (M4).

    Moves processed notification files to compute_inbox/processed/ so they
    are not re-delivered on subsequent turns.
    """
    inbox = sess.workdir / "compute_inbox"
    if not inbox.is_dir():
        return []
    processed = inbox / "processed"
    try:
        processed.mkdir(exist_ok=True)
    except OSError:
        # Cannot create processed/ (disk full, bad permissions, etc.).
        # Skip the drain rather than propagating an OSError before task_id
        # is bound, which would abort the entire turn.
        return []
    events: list[dict[str, Any]] = []
    for nf in sorted(inbox.glob("*_result.json")):
        try:
            data = json.loads(nf.read_text())
        except Exception:
            continue
        task_id = data.get("task_id", nf.stem)
        description = data.get("description", "compute task")
        status = data.get("status", "completed")
        artifact_paths: list[str] = data.get("artifact_paths", [])
        icon = "✓" if status == "completed" else "✗"
        events.append({
            "type": "delta",
            "text": f"{icon} Compute-Task `{task_id}` fertig: {description}\n",
        })
        for ap in artifact_paths:
            fpath = sess.workdir / ap
            if not fpath.is_file():
                continue
            mime = _artifact_mime(fpath)
            if mime is None:
                continue
            try:
                sz = fpath.stat().st_size
            except OSError:
                continue
            events.append({
                "type": "artifact",
                "name": fpath.name,
                "path": ap,
                "mime": mime,
                "size": sz,
                "label": "compute",
            })
        try:
            nf.rename(processed / nf.name)
        except OSError:
            pass
    return events


# mtime-keyed cache: the spec is read on EVERY turn (delegation flag) —
# re-parse only when the file actually changed, keep hot-reload semantics.
_tenant_spec_cache: dict[str, tuple[float, dict]] = {}
_TENANT_SPEC_LOCK = threading.Lock()


def _tenant_spec(tenant_id: str) -> dict:
    """Best-effort read of tenant.corvin.yaml::spec (mtime-cached)."""
    try:
        p = (_forge_paths.corvin_home() / "tenants" / tenant_id
             / "global" / "tenant.corvin.yaml")
        if not p.is_file():
            return {}
        mtime = p.stat().st_mtime
        with _TENANT_SPEC_LOCK:
            cached = _tenant_spec_cache.get(str(p))
            if cached and cached[0] == mtime:
                return cached[1]
        import yaml  # type: ignore[import-untyped]  # noqa: PLC0415
        raw = yaml.safe_load(p.read_text("utf-8")) or {}
        spec = raw.get("spec")
        spec = spec if isinstance(spec, dict) else {}
        with _TENANT_SPEC_LOCK:
            _tenant_spec_cache[str(p)] = (mtime, spec)
        return spec
    except Exception:  # noqa: BLE001
        return {}


def _delegation_enabled(tenant_id: str) -> bool:
    """ADR-0114: deny-by-default — delegation is an explicit tenant opt-in."""
    wc = _tenant_spec(tenant_id).get("web_chat") or {}
    return bool(wc.get("delegation_enabled", False))


def _delegation_budget(tenant_id: str) -> dict:
    """Budget envelope for delegated runs.

    Priority order (highest first):
      1. delegation_budget.json  — written by the console Settings UI
      2. spec.web_chat.budget    — tenant.corvin.yaml overrides
      3. _DELEGATION_BUDGET_DEFAULTS — module-level defaults
    """
    import json as _djson  # noqa: PLC0415
    out = dict(_DELEGATION_BUDGET_DEFAULTS)
    # Layer 2: tenant.corvin.yaml overrides
    wc = _tenant_spec(tenant_id).get("web_chat") or {}
    for key, val in (wc.get("budget") or {}).items():
        if key in out and isinstance(val, int) and val > 0:
            out[key] = val
    # Layer 1: delegation_budget.json (console Settings UI) overrides everything
    budget_path = _forge_paths.tenant_global_dir(tenant_id) / "delegation_budget.json"
    try:
        stored = _djson.loads(budget_path.read_text(encoding="utf-8"))
        for key, val in stored.items():
            if key in out and isinstance(val, int) and val > 0:
                out[key] = val
    except Exception:  # noqa: BLE001 — file absent, parse error, etc.
        pass
    return out


def _should_delegate(prompt: str) -> bool:
    """Heuristic triage: substantive work → delegate to workers.

    Strong verbs (review, debug, refactor, test, fix, migrate) always
    delegate regardless of length — even a short command is real work.
    Weak verbs (analyze, build, write, explain) delegate only when the
    prompt is long (≥160 chars) or explicitly multi-step.
    Regex anchors prevent false-positives: "latest" ≁ test, "prefix" ≁ fix.
    """
    p = prompt.strip()
    if p.lower().startswith(_DELEGATE_PREFIX):
        return True
    if len(p) >= 400:
        return True
    if _TRIAGE_STRONG_RE.search(p):
        return True
    has_verb = bool(_TRIAGE_VERB_RE.search(p))
    has_multi = bool(_TRIAGE_MULTI_RE.search(p))
    return has_verb and (has_multi or len(p) >= 160)


def _build_delegation_spec(task: str, budget: dict) -> dict:
    """Wrap a chat task into a minimal AWP delegation_loop workflow."""
    return {
        "awp": "1.0.0",
        "workflow": {
            "name": "web-chat-delegation",
            "description": task,
            "version": "1.0.0",
        },
        "orchestration": {
            "engine": "delegation_loop",
            "delegation_loop": {"budget": dict(budget)},
        },
        "state": {"initial": {"task": "web-chat delegated turn (ADR-0114)"}},
    }


def _audit_path(tenant_id: str) -> Path:
    return _store_dir(tenant_id) / _WEB_AUDIT_LOG_NAME


def _turns_path(tenant_id: str, sid: str) -> Path:
    """Per-session message log used by the SPA to re-hydrate a chat on
    re-open. One JSON object per line, append-only:
        {"role": "user" | "assistant", "ts": <epoch_s>, "parts": [...]}
    """
    return _store_dir(tenant_id) / f"{sid}.turns.jsonl"


def _append_turn(sess: "WebChatSession", role: str, parts: list[dict[str, Any]]) -> None:
    """Append one turn (user or assistant) to the session's turns log.

    Best-effort: a failed write does not break the stream — the user
    message is still in the WebSocket history client-side, and the
    assistant's reply was already streamed back."""
    path = _turns_path(sess.tenant_id, sess.sid)
    payload = {"role": role, "ts": time.time(), "parts": parts}
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except OSError:
        pass


def read_turns(tenant_id: str, sid: str, *, limit: int | None = None) -> list[dict[str, Any]]:
    """Read the session's persisted message history, optionally tail-limited.

    Returns oldest-first. Missing file → empty list (a session may have
    never produced a turn yet).
    """
    path = _turns_path(tenant_id, sid)
    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        return []
    if limit is not None and len(out) > limit:
        out = out[-limit:]
    return out


def delete_turns(tenant_id: str, sid: str) -> None:
    """Remove the persisted history for a session (called from
    delete_session)."""
    path = _turns_path(tenant_id, sid)
    try:
        path.unlink()
    except OSError:
        pass


def _audit_emit(sess: WebChatSession, event: str, **extra: Any) -> None:
    """Write a thin envelope to a SEPARATE log (NOT the canonical
    hash-chain). This is the load-bearing reminder that v1 is not yet
    chain-integrated; the file name itself signals 'side-channel'.
    """
    path = _audit_path(sess.tenant_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "ts":         time.time(),
        "event":      event,
        "channel":    CHANNEL,
        "chat_key":   sess.chat_key,
        "tenant_id":  sess.tenant_id,
        "turn":       sess.turn_count,
        **extra,
    }
    try:
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, sort_keys=True) + "\n")
    except OSError:
        pass


# Patterns applied in order to redact sensitive values from shell commands.
# Each entry: (compiled pattern, replacement).  The replacement uses *** as
# placeholder so the UI can show the structure without leaking the value.
_CMD_REDACT: list[tuple[re.Pattern[str], str]] = [
    # NAME=value / NAME="value" / NAME='value' with a sensitive variable name
    (re.compile(
        r"(?i)((?:password|passwd|token|secret|api[_-]?key|apikey|auth[_-]?key|"
        r"credential|private[_-]?key|access[_-]?key|client[_-]?secret|signing[_-]?key)"
        r"\s*=\s*)('[^']*'|\"[^\"]*\"|\S+)",
    ), r"\1***"),
    # Bearer <token> (HTTP Authorization headers)
    (re.compile(r"(?i)(bearer\s+)([A-Za-z0-9._\-+/]{8,}={0,2})"), r"\1***"),
    # URL with embedded credentials: https://[user[:pass]@]host
    (re.compile(r"(https?://)[^@\s]+@"), r"\1***@"),
    # --password=value / --password value / --token value / etc.
    (re.compile(
        r"(?i)(--(?:password|passwd|token|secret|api[-_]?key|auth(?:orization)?|credential)"
        r"(?:=|\s+))('[^']*'|\"[^\"]*\"|\S+)",
    ), r"\1***"),
    # -p value  (short password flag — mysql, psql, etc.)
    (re.compile(r"(?<!\w)(-p )(\S+)"), r"\1***"),
]


def _redact_cmd(cmd: str) -> str:
    """Redact known-sensitive patterns from a shell command for safe UI display."""
    for pattern, replacement in _CMD_REDACT:
        cmd = pattern.sub(replacement, cmd)
    return cmd


def _sanitize_tool_input(tool_name: str, full_input: dict[str, Any]) -> dict[str, Any]:
    """Extract safe, non-sensitive parameters for UI display (GDPR Art. 5).

    Returns a subset of tool_input with values that won't leak secrets.
    Secrets, file paths, and command bodies are stripped; only safe metadata shown.
    """
    safe = {}

    # Whitelist of (tool, param_name, sanitizer_func) tuples.
    # sanitizer_func: receives raw value, returns safe string or None to skip.
    SAFE_PARAMS = [
        # Bash: show full command with sensitive values redacted to ***
        ("bash", "command", lambda v: _redact_cmd(str(v)) if v and str(v).strip() else None),
        # File tools: show only the filename, not the full path (GDPR-safe)
        ("read", "file_path", lambda v: Path(v).name if v else None),
        ("edit", "file_path", lambda v: Path(v).name if v else None),
        ("write", "file_path", lambda v: Path(v).name if v else None),
        # URLs: safe to show (public endpoints)
        ("web_fetch", "url", lambda v: v if v else None),
        ("web_search", "query", lambda v: v if v else None),
        # Patterns: safe metadata
        ("bash", "pattern", lambda v: v if v and len(str(v)) < 50 else None),
        # Generic fallback for unknown tools: show key count only
    ]

    # Normalize: lowercase + strip underscores so "Bash"/"bash", "WebFetch"/"web_fetch" all match.
    tname_norm = tool_name.lower().replace("_", "")
    for tool, param, sanitizer in SAFE_PARAMS:
        if tool.replace("_", "") == tname_norm and param in full_input:
            try:
                sanitized = sanitizer(full_input[param])
                if sanitized is not None:
                    safe[param] = sanitized
            except Exception:
                pass  # silently skip on any error (e.g. Path() on non-string)

    return safe


# ── Hermes OS-turn (Layer-22 WorkerEngine path) ─────────────────────────────
#
# When the tenant selected Hermes as the OS engine (spec.default_engine=hermes),
# the console drives the SAME Layer-22 WorkerEngine the bridge adapter uses:
# HermesEngine streams from local Ollama over HTTP — no subprocess, no Anthropic
# API key. The blocking urllib generator runs in a worker thread; events are
# pumped into a queue and drained from the asyncio loop without blocking it
# (mirrors the adapter's _call_hermes_streaming_via_engine queue pattern).
#
# The pre-spawn gates (L44/LIP/L34/L35) run in stream_turn BEFORE this is
# called, with engine_id=hermes, so this path is reached only for a permitted
# turn. "Degradation is not silent" (ADR-0159): a turn that yields no usable
# output surfaces a clear notice, never an empty reply.

_HERMES_IDLE_TIMEOUT_S = 300.0  # wall-clock idle budget; matches adapter floor


def _configured_hermes_model(tenant_id: str) -> str | None:
    """spec.hermes_model from tenant.corvin.yaml, or None for the engine default
    (CORVIN_HERMES_MODEL env → qwen3:8b). Mirrors routes/engine.py's PUT writer."""
    val = _tenant_spec(tenant_id).get("hermes_model")
    if isinstance(val, str) and val.strip():
        return val.strip()
    return None


def _acs_local_pin_model(os_engine: str, os_model: "str | None",
                         tenant_id: str) -> "str | None":
    """The concrete LOCAL model ACS must use for BOTH manager and worker when the
    OS engine is local (Hermes/Ollama), or None for non-local engines.

    Returning a real local model (never None for hermes) is load-bearing: ACS's
    _resolve_worker_engine routes by model name, so a hermes model → the Hermes
    engine. Without a concrete model ACS uses its claude-sonnet default → routes
    to claude_code → the manager raises "claude CLI not found" on a fresh
    Hermes/Ollama install → workers_spawned=0 → EMPTY worker-engine graph. Cloud
    OS engines return None here to preserve their existing worker cost-tier
    fallback.
    """
    if os_engine != "hermes":
        return None
    model = os_model or _configured_hermes_model(tenant_id)
    if model:
        return model
    try:
        from agents.hermes_engine import _resolve_default_model as _rdm  # noqa: PLC0415
        return _rdm()
    except Exception:  # noqa: BLE001
        return "qwen3:8b"


async def _stream_hermes_turn(
    sess: "WebChatSession",
    prompt: str,
    tm: Any,
    task_id: str,
    *,
    os_audit: Any,
    audit_emit: Any,
    emit_completed: Any,
    os_turn_id: str,
) -> AsyncIterator[dict[str, Any]]:
    """Drive one OS turn through HermesEngine (Ollama HTTP) and yield normalised
    web-chat events. Same yielded shapes as the claude path:
        {type: "delta", text}  {type: "result", text, usage}
        {type: "tool_use", name, input}  {type: "error", message}  {type: "done"}
    """
    import queue as _queue  # local import keeps the module's top clean

    model = _configured_hermes_model(sess.tenant_id)
    engine = _HermesEngine(model=model)  # type: ignore[misc]
    ev_q: "_queue.Queue" = _queue.Queue()

    system_prompt = _turn_system_prompt(sess)

    def _stream_thread() -> None:
        try:
            for ev in engine.spawn(
                prompt,
                system=system_prompt,
                model=model,
                working_dir=sess.workdir,
                timeout=float("inf"),  # the async drain loop owns the idle watchdog
            ):
                ev_q.put(("event", ev))
        except Exception as e:  # noqa: BLE001
            ev_q.put(("error", str(e)))
        finally:
            ev_q.put(("eof", None))

    thread = threading.Thread(
        target=_stream_thread, daemon=True, name=f"hermes-web-{sess.sid}",
    )
    # task.started — no subprocess pid (HTTP, in-thread); the run is INLINE within
    # the live request, so if the console dies mid-turn it is a genuine orphan and
    # the boot reaper finalizes it (same rationale as the ACS delegation branch).
    tm.record_event(task_id, {
        "event": "task.started", "engine": "hermes", "turn": sess.turn_count,
    })
    os_audit("os_turn.started", {"model": engine.model})
    thread.start()

    accumulated: list[str] = []
    last_usage: dict[str, Any] | None = None
    error_text: str | None = None
    timed_out = False
    last_event = time.monotonic()
    _tools_called = 0
    _tool_seq = 0

    try:
        while True:
            try:
                kind, payload = await asyncio.to_thread(ev_q.get, True, 1.0)
            except _queue.Empty:
                if time.monotonic() - last_event > _HERMES_IDLE_TIMEOUT_S:
                    timed_out = True
                    try:
                        engine.cancel()
                    except Exception:  # noqa: BLE001
                        pass
                    break
                continue

            last_event = time.monotonic()
            if kind == "event":
                ev = payload
                if ev.type == "text_delta" and ev.text:
                    accumulated.append(ev.text)
                    tm.record_event(task_id, {"event": "stream_token", "chunk": ev.text})
                    yield {"type": "delta", "text": ev.text}
                elif ev.type == "tool_call":
                    _tools_called += 1
                    _tool_seq += 1
                    _tname = ev.text or ""
                    if not _tname and isinstance(ev.raw, dict):
                        _tname = ev.raw.get("name", "")
                    tm.record_event(task_id, {"event": "tool_use", "tool_name": _tname})
                    # GDPR Art. 5: tool name + seq only, never tool inputs.
                    os_audit("os_turn.tool_called", {"tool_name": _tname, "seq": _tool_seq})
                    yield {"type": "tool_use", "name": _tname, "input": {}}
                elif ev.type == "turn_completed":
                    if ev.text and not accumulated:
                        accumulated.append(ev.text)
                    if ev.usage:
                        last_usage = ev.usage
                    break
                elif ev.type == "error":
                    error_text = ev.error or "hermes error"
                    break
            elif kind == "error":
                error_text = str(payload)
                break
            elif kind == "eof":
                break
    except (asyncio.CancelledError, GeneratorExit):
        try:
            engine.cancel()
        except Exception:  # noqa: BLE001
            pass
        audit_emit(sess, "web.turn.cancelled")
        emit_completed(rc=-1)
        raise

    await asyncio.to_thread(thread.join, 5.0)
    final_text = "".join(accumulated).strip()

    # "Degradation is not silent" (ADR-0159): an idle-timeout or an Ollama error
    # with no usable text surfaces a clear notice rather than an empty reply.
    if not final_text and (error_text or timed_out):
        if error_text and "ollama" in error_text.lower():
            notice = (
                "Hermes/Ollama ist nicht erreichbar. Bitte starte `ollama serve` "
                "und stelle sicher, dass das Modell geladen ist.\n\n"
                "Hermes/Ollama is unreachable. Please start `ollama serve` and "
                "make sure the model is pulled."
            )
        elif timed_out:
            notice = (
                "Hermes hat innerhalb des Zeitfensters nicht geantwortet "
                "(Ollama-Idle-Timeout). Bitte erneut versuchen.\n\n"
                "Hermes did not respond within the time window (Ollama idle "
                "timeout). Please try again."
            )
        else:
            notice = (
                f"Hermes-Fehler: {error_text}.\n\n"
                f"Hermes error: {error_text}."
            )
        rc = 1
        tm.record_event(task_id, {"event": "task.failed", "exit_code": rc})
        audit_emit(sess, "web.turn.completed", rc=rc, result_chars=len(notice),
                   usage=None, reason="hermes_no_output")
        emit_completed(rc)
        yield {"type": "delta", "text": notice}
        yield {"type": "result", "text": notice, "usage": None}
        touch(sess, increment_turn=True)
        _append_turn(sess, "assistant", [{"kind": "text", "text": notice}])
        yield {"type": "done"}
        return

    rc = 0
    yield {"type": "result", "text": final_text, "usage": last_usage}

    # Voice annotation suffix (LERN-ZUGABE + METAPHER), mirroring the claude path.
    _ann_suffix = ""
    if final_text:
        _ann_suffix = await _compute_web_annotation_suffix(final_text, sess.tenant_id)
    if _ann_suffix:
        yield {"type": "delta", "text": "\n\n" + _ann_suffix}
        yield {"type": "result", "text": final_text + "\n\n" + _ann_suffix,
               "usage": last_usage}

    combined = final_text
    if _ann_suffix:
        combined = (final_text + "\n\n" + _ann_suffix).strip()

    audit_emit(sess, "web.turn.completed", rc=rc, result_chars=len(final_text),
               usage=last_usage)
    tm.record_event(task_id, {
        "event": "task.completed", "exit_code": 0,
        "summary": f"hermes: {len(final_text)} chars output",
    })
    emit_completed(rc)
    touch(sess, increment_turn=True)
    _append_turn(sess, "assistant",
                 [{"kind": "text", "text": combined or ""}])
    yield {"type": "done"}


# ── L44 + L-integrity + L34 + L35 pre-spawn gates (CRITICAL compliance) ───────
#
# The owner-console web-chat runs an OS turn either by spawning ``claude -p``
# directly (the v1 subprocess path) or by fanning out via ``ACSRuntime`` (the
# ADR-0114 delegation path). The bridge adapter runs FOUR fail-closed gates
# before EVERY OS-turn spawn: L44 acceptable-use (ADR-0143), ADR-0141 Tier-3
# capability presence, L34 data-classification (ADR-0042) and L35 egress
# (ADR-0043). All four now live in the shared ``_spawn_gates`` chokepoint
# (``check_console_spawn_or_refusal``) so EVERY authenticated console spawn
# surface runs the identical gate — an ungated authenticated LLM spawn path is
# a structural fail-open of a load-bearing EU-AI-Act-Art.5 mechanism. The
# round-3 house-rules + capability logic was lifted into that module verbatim;
# round-4 added L34/L35. The gate runs on the user's prompt before either spawn
# path in ``stream_turn``.


async def stream_turn(
    sess: WebChatSession,
    prompt: str,
) -> AsyncIterator[dict[str, Any]]:
    """Run one turn against claude and yield normalised events.

    Yielded shapes:
      {type: "delta",    text: str}
      {type: "tool_use", name: str, input: dict}
      {type: "result",   text: str, usage: dict | None}
      {type: "error",    message: str}
      {type: "done"}
    """
    if not prompt or not prompt.strip():
        yield {"type": "error", "message": "empty prompt"}
        yield {"type": "done"}
        return

    # M4 (ADR-0170) — drain compute-task inbox before processing the new turn
    # so background results surface at the next user interaction, not delayed.
    sess.workdir.mkdir(parents=True, exist_ok=True)
    for _inbox_evt in _drain_compute_inbox(sess):
        yield _inbox_evt

    # ADR-0080 M1 — task lifecycle (L16 audit alternative)
    sess.workdir.mkdir(parents=True, exist_ok=True)
    tasks_dir = sess.workdir / "tasks"
    tm = _task_manager.TaskManager(tasks_dir)
    task_id = tm.create_task(
        chat_key=sess.chat_key,
        instruction=prompt,
        persona="assistant",
        turn_number=sess.turn_count,
    )

    resume = sess.turn_count > 0
    # ADR-0112 engine-model split: OS turns run the adaptive Haiku/Sonnet
    # pair, distinct from ACS workers which inherit the user/tenant model.
    # Mirrors the adapter's resolution tiers: operator override env →
    # autoselect gate → payload-sized autoselect (prompt + web system
    # prompt + session history, not bare len(prompt)). Falls back to the
    # CLI default when the selector module is unavailable.
    _os_model: str | None = None
    if _model_selector is not None:
        try:
            _os_model = _model_selector.os_model_override()
            if not _os_model and _model_selector.autoselect_enabled():
                payload = _model_selector.estimate_os_turn_chars(
                    prompt, _WEB_CHAT_SYSTEM_PROMPT, session_dir=sess.workdir,
                )
                _os_model = _model_selector.autoselect_os_model(payload)
        except Exception:  # noqa: BLE001
            _os_model = None
    args = _build_args(sess, resume=resume, model=_os_model)

    # First-turn auto-title: derive a readable label from the prompt so the
    # sidebar shows "Wie groß ist die Wahrscheinlichkeit, dass …" instead of
    # the 22-char hash. Manual renames win — the heuristic only fires when
    # the user has not picked a title yet. Persisted before the subprocess
    # spawns so a crashed turn still gets a useful sidebar entry.
    # /delegate is a routing directive, not part of the task — strip it for
    # the title AND reuse the flag in the delegation branch below.
    _force_delegate = prompt.strip().lower().startswith(_DELEGATE_PREFIX)
    _task_text = (prompt.strip()[len(_DELEGATE_PREFIX):].strip()
                  if _force_delegate else prompt)

    # Resolve engine early so turn.start debug event can record it.
    # The full pre-spawn gate check (line ~1958) also uses this value.
    _os_engine = _effective_os_engine(sess.tenant_id)

    # ── DEBUG: turn.start ────────────────────────────────────────────────────
    _dbg_t0 = time.monotonic()
    _dbg(sess.workdir, "turn.start",
         sid=sess.sid, chat_key=sess.chat_key,
         tenant=sess.tenant_id,
         prompt_len=len(prompt),
         prompt_preview=prompt[:120],
         force_delegate=_force_delegate,
         resume=resume,
         os_engine=_os_engine,
         os_model=str(_os_model or ""),
    )

    title_event: dict[str, Any] | None = None
    if not resume and not sess.title.strip():
        auto = _derive_auto_title(_task_text)
        if auto:
            sess.title = auto
            _save(sess)
            title_event = {"type": "session_title", "title": auto}

    if title_event:
        yield title_event

    _audit_emit(sess, "web.turn.started", prompt_chars=len(prompt))
    # NOTE: the `task.started` event is recorded per-path AFTER the engine
    # process exists so it can carry the real subprocess pid — the boot
    # stale-task reaper's liveness gate (TaskManager._task_pid_alive) reaps a
    # RUNNING task ONLY when its recorded pid is gone; an event with no pid
    # makes every live console turn look like an orphan. Recording it here,
    # before any process exists, was the state-corruption root cause.

    # L16 chain: one os_turn per user interaction — metadata only, no prompt
    # text (GDPR Art. 5). Same event family the bridge adapter emits, so the
    # console's /os-turns route renders web and bridge channels alike.
    _os_turn_id = "ot_" + secrets.token_urlsafe(9)
    _os_turn_start = time.monotonic()
    # Wall-clock start (epoch s) — distinct from the monotonic timer above.
    # The ACS global-index manifest (#4) sorts by started_at in wall time, so
    # the delegated run must carry an epoch timestamp, not a monotonic offset.
    _os_turn_start_wall = time.time()
    _os_tools_called = 0
    _os_tool_seq = 0          # sequence counter for os_turn.tool_called events
    _os_completed_emitted = False
    # Requested model; overwritten with the subprocess-confirmed model from
    # the stream-json init event once it arrives.
    _os_model_used = _os_model or ""
    # ADR-0171 — one engine.span per OS turn (role=os), engine-agnostic (claude OR
    # hermes), dual-emitted on the SAME chain as os_turn.* so the console can build
    # the OS graph from spans uniformly. Paired by a stable per-turn span_id.
    _os_span_id = f"spn-os-{_os_turn_id}"
    _os_span_started = False

    def _os_audit(event: str, extra: dict[str, Any] | None = None) -> None:
        if _bridge_audit is None:
            return
        try:
            _bridge_audit.audit_event(
                event,
                channel=CHANNEL,
                chat_key=sess.chat_key,
                persona="assistant",
                details={"turn_id": _os_turn_id, **(extra or {})},
            )
        except Exception:  # noqa: BLE001
            pass  # audit is best-effort here; chain health is boot-checked
        # Emit the engine-span START exactly once, when the OS turn starts.
        if event == "os_turn.started" and _espan is not None:
            nonlocal _os_span_started
            if not _os_span_started:
                _os_span_started = True
                try:
                    _espan.emit_start(
                        _bridge_audit.audit_event,
                        span_id=_os_span_id, role="os",
                        engine_id=_os_engine, model_id=_os_model_used,
                        run_id=_os_turn_id, turn_id=_os_turn_id,
                        channel=CHANNEL, chat_key=sess.chat_key,
                    )
                except Exception:  # noqa: BLE001
                    pass

    def _os_emit_completed(rc: int) -> None:
        nonlocal _os_completed_emitted
        if _os_completed_emitted:
            return
        _os_completed_emitted = True
        _os_audit("os_turn.completed", {
            "duration_ms": int((time.monotonic() - _os_turn_start) * 1000),
            "tools_called": _os_tools_called,
            "exit_code": rc,
            "timed_out": False,
            "model": _os_model_used,
        })
        # ADR-0171 — engine-span END (paired with the start above). status from rc.
        if _espan is not None and _os_span_started:
            try:
                _espan.emit_end(
                    _bridge_audit.audit_event,
                    span_id=_os_span_id, role="os",
                    engine_id=_os_engine, model_id=_os_model_used,
                    run_id=_os_turn_id, turn_id=_os_turn_id,
                    status="ok" if rc == 0 else "error",
                    duration_ms=int((time.monotonic() - _os_turn_start) * 1000),
                    tool_call_count=_os_tools_called,
                )
            except Exception:  # noqa: BLE001
                pass

    # Persist the user-side of this turn immediately so a tab refresh
    # mid-turn still shows what the user said.
    _append_turn(sess, "user", [{"kind": "text", "text": prompt}])

    # ── ADR-0141 / Layer-Integrity + ADR-0143 / Layer 44 — pre-spawn gates ──
    # Mandatory, fail-closed acceptable-use + security-layer-presence checks that
    # run BEFORE either OS-turn spawn path (the direct `claude -p` subprocess AND
    # the ACS delegation fan-out). The bridge adapter runs these before every
    # OS-turn; the web-chat had neither, leaving an authenticated ungated LLM
    # spawn path — a structural fail-open of a load-bearing EU-AI-Act-Art.5
    # control (CLAUDE.md compliance baseline). We gate the substantive task text
    # (`_task_text` = prompt with any `/delegate` routing prefix stripped) so the
    # same instruction is classified regardless of which spawn path it takes.
    # A blocked turn reuses the engine-unavailable bookkeeping below: it emits
    # os_turn.started + task.failed + web.turn.completed(rc=1), streams the
    # refusal, then `done`. The gate's own house_rules.* / security.* audit event
    # is written to the per-tenant L16 chain INSIDE the check, before we yield.
    # Round-4: route through the shared console pre-spawn chokepoint so the
    # web-chat runs the SAME four fail-closed gates the bridge adapter runs —
    # L44 acceptable-use + ADR-0141 capability presence (round-3) AND now L34
    # data-classification + L35 egress (round-4 finding #3). One call, audit-first
    # on every deny.
    # Resolve the engine that will ACTUALLY run this turn so the gate classifies
    # against the right L34/L35 compliance row (hermes = locality=local /
    # egress=none; claude_code = us_cloud). Delegation fan-out is classified as
    # "acs"; otherwise the configured OS engine (claude_code | hermes | …).
    _will_delegate = (_delegation_enabled(sess.tenant_id)
                      and (_force_delegate or _should_delegate(prompt)))
    # _os_engine already resolved above (before turn.start debug event)
    _gate_refusal = _spawn_gates.check_console_spawn_or_refusal(
        _task_text, tenant_id=sess.tenant_id, persona="assistant",
        channel=CHANNEL, chat_key=sess.chat_key,
        engine_id=(_spawn_gates.DELEGATION_ENGINE_ID if _will_delegate else _os_engine),
    )
    if _gate_refusal is not None:
        _os_audit("os_turn.started", {"model": _os_model_used})
        tm.record_event(task_id, {
            "event": "task.failed", "exit_code": 1,
            "error": "blocked by pre-spawn acceptable-use / layer-integrity gate",
        })
        _audit_emit(sess, "web.turn.completed", rc=1,
                    result_chars=len(_gate_refusal), usage=None,
                    reason="pre_spawn_gate_blocked")
        _os_emit_completed(rc=1)
        yield {"type": "delta", "text": _gate_refusal}
        yield {"type": "result", "text": _gate_refusal, "usage": None}
        touch(sess, increment_turn=True)
        _append_turn(sess, "assistant", [{"kind": "text", "text": _gate_refusal}])
        yield {"type": "done"}
        return

    # ── ADR-0168 M1/M2 — CCC entity extraction + command routing ─────────
    # Enabled by default (opt-out: set CORVIN_CCC_M1_ENABLED=0 to disable).
    # Runs AFTER all pre-spawn gates pass, BEFORE engine spawn, so every
    # gate (L44, L34, L35) has already cleared this turn.
    # Yields a "ccc_action" event to the WebSocket; the LLM turn continues
    # normally — CCC is additive, not a bypass.
    import os as _os_ccc  # noqa: PLC0415 — local import to keep module-level clean
    if _os_ccc.environ.get("CORVIN_CCC_M1_ENABLED", "1") != "0":
        try:
            from entity_extract import extract as _ccc_extract  # type: ignore  # noqa: PLC0415
            from corvin_console.chat_router import dispatch as _ccc_dispatch  # noqa: PLC0415
            _ccc_plan = _ccc_extract(_task_text)
            # Audit: metadata only — entity_type + confidence, never prompt text.
            _os_audit("ccc.entity_extracted", {
                "entity_type": _ccc_plan.entity_type,
                "confidence":  round(_ccc_plan.confidence, 3),
                "forced":      _ccc_plan.forced,
            })
            if _ccc_plan.is_actionable:
                _ccc_tasks_dir = sess.workdir / "tasks"
                _ccc_result = await _ccc_dispatch(
                    _ccc_plan,
                    tenant_id=sess.tenant_id,
                    tasks_dir=_ccc_tasks_dir,
                )
                _os_audit("ccc.action_dispatched", {
                    "entity_type": _ccc_result.entity_type,
                    "action_id":   _ccc_result.action_id,
                    "entity_id":   _ccc_result.entity_id,
                    "status":      _ccc_result.status,
                })
                # L34 gate: strip payload for CONFIDENTIAL entity types before
                # emitting over WebSocket (mirrors ccc_pubsub._gate_payload).
                # SSOT: entity_extract.CONFIDENTIAL_ENTITY_TYPES, fail-CLOSED to
                # the full set on import error (security review 2026-06-27, C5).
                try:
                    from entity_extract import (
                        CONFIDENTIAL_ENTITY_TYPES as _CCC_CONFIDENTIAL,
                    )
                except Exception:  # noqa: BLE001 — fail closed with the complete set
                    _CCC_CONFIDENTIAL = frozenset(
                        {"erasure_request", "vault_entry", "a2a_session"}
                    )
                _ws_payload = (
                    {
                        "entity_id": _ccc_result.entity_id,
                        "status":    _ccc_result.status,
                    }
                    if _ccc_result.entity_type in _CCC_CONFIDENTIAL
                    else _ccc_result.payload
                )
                yield {
                    "type":        "ccc_action",
                    "action_id":   _ccc_result.action_id,
                    "entity_type": _ccc_result.entity_type,
                    "entity_id":   _ccc_result.entity_id,
                    "status":      _ccc_result.status,
                    "message":     _ccc_result.message,
                    "payload":     _ws_payload,
                }
        except ImportError:
            pass  # entity_extract not installed — skip CCC (degraded mode)
        except Exception as _ccc_err:  # noqa: BLE001
            _log.debug("CCC hook error (non-fatal): %s", _ccc_err)

    # ── ADR-0114 M1/M2 — delegation path ─────────────────────────────────
    # Tenant opt-in + triage: substantive tasks run on ACS workers (which
    # inherit the user/tenant model per ADR-0112); the OS side only manages.
    _del_enabled = _delegation_enabled(sess.tenant_id)
    _del_heuristic = _should_delegate(prompt)
    # Layer 5 repair: check if ACS throttle is active (written by repair.py
    # after an acs_error_rate anomaly — throttle expires after N turn.done events).
    try:
        from .aco.repair import is_acs_throttled as _is_acs_throttled
        _del_throttled = _is_acs_throttled(sess.workdir)
    except Exception:  # noqa: BLE001 — repair module unavailable → no throttle
        _del_throttled = False
    _del_will_delegate = _del_enabled and not _del_throttled and (_force_delegate or _del_heuristic)
    _dbg(sess.workdir, "delegation.decision",
         delegation_enabled=_del_enabled,
         force_delegate=_force_delegate,
         heuristic_match=_del_heuristic,
         repair_throttled=_del_throttled,
         will_delegate=_del_will_delegate,
         prompt_len=len(prompt),
    )
    if _del_will_delegate:
        task_text = _task_text
        _acs = None
        try:
            # Ensure operator/bridges/shared is in path for spawn_gates and other deps
            # Path: core/console/corvin_console/chat_runtime.py → CorvinOS/operator/bridges/shared
            _bridge_shared = Path(__file__).resolve().parents[3] / "operator" / "bridges" / "shared"
            if str(_bridge_shared) not in sys.path:
                sys.path.insert(0, str(_bridge_shared))
            import acs_runtime as _acs  # type: ignore  # noqa: PLC0415
        except Exception as _import_err:  # noqa: BLE001
            _log.warning("[delegation] Failed to import ACS runtime: %s", _import_err)
            _acs = None
        if _acs is None or not task_text:
            reason = "empty task" if not task_text else "ACS runtime unavailable"
            _dbg(sess.workdir, "delegation.skipped", reason=reason)
            yield {"type": "error", "message": f"delegation skipped: {reason}"}
            yield {"type": "done"}
            return

        # ADR-0150 LIC-WEBCHAT-DELEGATE-COMPUTE-01: this branch fans out to ACS
        # workers (1 manager + up to 4 worker `claude -p`) but constructs ACSRuntime
        # DIRECTLY, bypassing run_acs_workflow's compute charge. Charge
        # compute_units_per_day HERE (orthogonal to the route's enforce_chat_turns,
        # which only covers the single OS-turn framing). Fail-CLOSED: a missing
        # license module or an over-quota both deny the fan-out before any spawn.
        try:
            from license.compute_quota import increment_and_check as _cq_inc  # type: ignore  # noqa: PLC0415
            from license.limits import LicenseLimitError as _CQErr  # type: ignore  # noqa: PLC0415
        except ImportError:
            yield {"type": "error", "code": 402,
                   "message": "compute quota enforcement unavailable (fail-closed)"}
            yield {"type": "done"}
            return
        # Use the CANONICAL resolver (CORVIN_HOME → service.env pin → repo marker
        # → ~/.corvin), NOT a hand-rolled env-or-~/.corvin: the deny-by-default
        # compute gate (ADR-0094) writes the counter via forge.paths.corvin_home()
        # in _compute_license_gate / mcp_server. A direct-env resolver returns
        # ~/.corvin inside a repo where canonical returns <repo>/.corvin → the
        # reader (this counter) and the writer diverge → quota silently miscounted.
        _cq_home = _forge_paths.corvin_home()
        _quota_fallback = False
        try:
            _cq_inc(_cq_home, channel="web-chat-acs", chat_key=f"web:{sess.tenant_id}:{sess.sid}")
        except _CQErr:  # type: ignore[misc]
            _quota_fallback = True
            # L34/L35 fix: re-gate with the ACTUAL fallback engine.  The initial
            # gate (above) was called with engine_id="acs"; after quota fallback the
            # real engine is _os_engine (claude_code / hermes / …).  Without this
            # second check, CONFIDENTIAL data could bypass residency policy because
            # the gate never evaluated the engine that will actually spawn.
            _fb_gate = _spawn_gates.check_console_spawn_or_refusal(
                _task_text, tenant_id=sess.tenant_id, persona="assistant",
                channel=CHANNEL, chat_key=sess.chat_key,
                engine_id=_os_engine,
            )
            if _fb_gate is not None:
                _os_audit("os_turn.started", {"model": _os_model_used})
                tm.record_event(task_id, {
                    "event": "task.failed", "exit_code": 1,
                    "error": "quota-fallback engine blocked by pre-spawn gate",
                })
                _audit_emit(sess, "web.turn.completed", rc=1,
                            result_chars=len(_fb_gate), usage=None,
                            reason="pre_spawn_gate_blocked")
                _os_emit_completed(rc=1)
                yield {"type": "delta", "text": _fb_gate}
                yield {"type": "result", "text": _fb_gate, "usage": None}
                touch(sess, increment_turn=True)
                _append_turn(sess, "assistant", [{"kind": "text", "text": _fb_gate}])
                yield {"type": "done"}
                return
            _quota_notice = (
                "Dein tägliches ACS-Kontingent ist ausgeschöpft "
                "(1 Delegation-Run/Tag im Free-Tier). "
                "Der Task wird über Claude Code ausgeführt — ohne parallele Worker.\n"
                "Für unbegrenzte ACS-Runs: [Member-Upgrade](https://corvin-labs.com/pricing)\n\n"
            )
            yield {"type": "notice", "subtype": "quota_fallback",
                   "message": _quota_notice}
            yield {"type": "delta", "text": _quota_notice}
        except Exception:  # noqa: BLE001 — operational error swallowed by increment_and_check
            pass

        if not _quota_fallback:
            run_id = f"acs-web-{int(time.time())}-{secrets.token_hex(3)}"
            run_dir = sess.workdir / "acs" / "runs" / run_id
            spec_dict = _build_delegation_spec(task_text, _delegation_budget(sess.tenant_id))
            _dbg(sess.workdir, "acs.run.start",
                 run_id=run_id, task_len=len(task_text), task_preview=task_text[:120],
                 budget=_delegation_budget(sess.tenant_id))
            rt_kwargs: dict[str, Any] = {
                "tenant_id": sess.tenant_id, "bridge": CHANNEL, "chat": sess.sid,
            }
            if _os_model:  # manager = OS role → adaptive model (ADR-0112)
                rt_kwargs["manager_model"] = _os_model
            # When the OS engine is LOCAL (Hermes/Ollama), pin BOTH manager and
            # worker model to a concrete local model (see _acs_local_pin_model) so
            # ACS never falls back to cloud-Claude and dies with "claude CLI not
            # found" → 0 workers → empty worker-engine graph on a fresh local install.
            _pin_model = _acs_local_pin_model(_os_engine, _os_model, sess.tenant_id)
            if _pin_model:
                rt_kwargs["manager_model"] = _pin_model
                rt_kwargs["worker_model"] = _pin_model
            # Pass session workdir so ACSRuntime writes acs.worker.* events into
            # chat_debug.jsonl — enables ACO Layer 3 to correlate worker errors.
            rt_kwargs["session_debug_log"] = sess.workdir
            runtime = _acs.ACSRuntime(**rt_kwargs)

            # Lifecycle marker for the delegation path. This turn runs INLINE
            # within the live request (awaited below) and fans out to ACS workers
            # rather than a single tracked `claude` subprocess, so no engine pid is
            # recorded here: if the console dies mid-delegation the task is a
            # genuine orphan and the boot reaper correctly finalizes it.
            tm.record_event(task_id, {
                "event": "task.started", "engine": "acs-delegation",
                "turn": sess.turn_count,
            })
            _os_audit("os_turn.started", {"model": _os_model_used})
            yield {"type": "delta",
                   "text": f"⚙ Delegation an ACS-Worker gestartet (run {run_id})…\n"}

            run_task = asyncio.create_task(runtime.run(spec_dict, run_id=run_id))
            seen_traces: set[str] = set()

            def _new_worker_traces() -> list[str]:
                traces_dir = run_dir / "traces"
                if not traces_dir.is_dir():
                    return []
                fresh = [tf.stem for tf in sorted(traces_dir.glob("*.json"))
                         if tf.name not in seen_traces]
                for name in fresh:
                    seen_traces.add(name + ".json")
                return fresh

            # M2 (ADR-0170) — live artifact streaming.
            # Track file sizes between polls; only emit once the size is stable
            # (unchanged from the previous poll) so partially-written files are
            # never surfaced. ``_live_emitted`` is read by the M1 post-run scan
            # to skip files that were already delivered during the run.
            _live_prev_sizes: dict[Path, int] = {}
            _live_emitted: set[str] = set()
            # M2 persistence: mirror of yielded live artifacts in the "kind" format
            # so they can be added to _turn_parts and persisted to turns.jsonl.
            # Without this, live-delivered artifacts are absent from session history.
            _live_artifact_parts: list[dict[str, Any]] = []

            def _new_live_artifacts() -> list[dict[str, Any]]:
                if not run_dir.is_dir():
                    return []
                results: list[dict[str, Any]] = []
                for _fp in sorted(run_dir.rglob("*")):
                    if not _fp.is_file() or _fp.name.startswith("."):
                        continue
                    if _fp.suffix == ".jsonl":
                        continue
                    try:
                        _rel_parts = _fp.relative_to(run_dir).parts
                    except ValueError:
                        continue
                    if _rel_parts and _rel_parts[0] in _ACS_SKIP_DIRS:
                        continue
                    if _fp.parent == run_dir and _fp.name in _ACS_SKIP_ROOT_FILES:
                        continue
                    _key = str(_fp)
                    if _key in _live_emitted:
                        continue
                    try:
                        _sz = _fp.stat().st_size
                    except OSError:
                        continue
                    _prev = _live_prev_sizes.get(_fp)
                    if _prev is None:
                        _live_prev_sizes[_fp] = _sz  # first sighting, wait one poll
                        continue
                    if _prev != _sz:
                        _live_prev_sizes[_fp] = _sz  # still growing, wait
                        continue
                    # Size stable — file is fully written
                    _mime = _artifact_mime(_fp)
                    if _mime is None:
                        continue
                    try:
                        _relpath = _fp.relative_to(sess.workdir)
                    except ValueError:
                        _relpath = _fp.relative_to(run_dir)
                    _live_emitted.add(_key)
                    _live_label = _acs_artifact_label(_fp, run_dir)
                    _evt: dict[str, Any] = {
                        "type": "artifact", "name": _fp.name,
                        "path": str(_relpath), "mime": _mime, "size": _sz,
                    }
                    if _live_label:
                        _evt["label"] = _live_label
                    else:
                        _evt["label"] = "live"
                    results.append(_evt)
                    # Persist alongside the text turn so the artifact survives reload.
                    _persist: dict[str, Any] = {
                        "kind": "artifact", "name": _fp.name,
                        "path": str(_relpath), "mime": _mime, "size": _sz,
                    }
                    if _live_label:
                        _persist["label"] = _live_label
                    else:
                        _persist["label"] = "live"
                    _live_artifact_parts.append(_persist)
                return results

            res = None
            try:
                while not run_task.done():
                    await asyncio.sleep(2.0)
                    for worker in _new_worker_traces():
                        yield {"type": "delta",
                               "text": f"✓ Worker {worker} abgeschlossen\n"}
                    for _la in _new_live_artifacts():
                        yield _la
                # Final poll — catch workers/artifacts that landed in the last window.
                for worker in _new_worker_traces():
                    yield {"type": "delta",
                           "text": f"✓ Worker {worker} abgeschlossen\n"}
                for _la in _new_live_artifacts():
                    yield _la
                # Await the result — may raise if ACSRuntime encountered an error
                res = await run_task
            except (asyncio.CancelledError, GeneratorExit):
                # Client gone mid-run — mirror v1 semantics (no orphaned work).
                # No await after GeneratorExit; retrieve the task's outcome via
                # callback so asyncio doesn't log "exception was never retrieved".
                run_task.cancel()
                run_task.add_done_callback(
                    lambda t: None if t.cancelled() else t.exception())
                _audit_emit(sess, "web.turn.cancelled", delegated_run_id=run_id)
                _os_emit_completed(rc=-1)
                raise
            except Exception as exc:
                # ACS runtime or other unexpected error — capture and return as failed result
                import traceback
                error_msg = f"{type(exc).__name__}: {str(exc)[:200]}"
                tb_lines = traceback.format_exc().split('\n')[-4:-1]  # Last 3 lines of traceback
                error_detail = " | ".join(line.strip() for line in tb_lines if line.strip())
                _log.exception("[delegation] Unexpected error in ACS run: %s", error_msg)
                res = _acs.ACSResult(
                    run_id=run_id, workflow_id="unknown", status="failed",
                    error=error_msg, summary=f"Unexpected error: {error_detail}",
                    run_dir=run_dir,
                )

            if res is None:
                # Fallback — should not happen but guard against empty result
                res = _acs.ACSResult(
                    run_id=run_id, workflow_id="unknown", status="failed",
                    error="No result returned from ACS runtime",
                    run_dir=run_dir,
                )
            ok = res.status == "success"
            final = (res.summary or "").strip()

            # Safety net: raw HTML error pages (Cloudflare 50x, nginx, …) that
            # slip through the ACS layer must never appear verbatim in the chat.
            if final and final.lstrip().startswith(("<!DOCTYPE", "<!doctype", "<html", "<HTML")):
                import re as _re_html
                _t = _re_html.search(r"<title[^>]*>([^<]{1,120})</title>",
                                     final, _re_html.IGNORECASE)
                _label = _t.group(1).strip() if _t else "HTTP-Fehlerseite"
                final = f"Fehler: Der Server hat \"{_label}\" zurückgegeben. Bitte versuche es erneut."
                ok = False

            if not final:
                # Debug: log the actual result state
                _log.debug(
                    "[delegation] Final result: status=%s, error=%s, summary=%s",
                    res.status, repr(res.error), repr(res.summary)
                )
                # Use best available error message (prefer error, then summary, then construct from status)
                if res.error:
                    error_msg = res.error
                elif res.summary:
                    error_msg = res.summary
                else:
                    # Fallback: construct message from status and iterations/workers
                    details = []
                    if hasattr(res, "iterations") and res.iterations:
                        details.append(f"{res.iterations} iteration(s)")
                    if hasattr(res, "workers_spawned") and res.workers_spawned:
                        details.append(f"{res.workers_spawned} worker(s)")
                    detail_str = f" ({', '.join(details)})" if details else ""
                    error_msg = f"ACS workflow failed with status '{res.status}'{detail_str}"

                final = ("Delegation abgeschlossen." if ok
                         else f"Delegation fehlgeschlagen: {error_msg[:250]}")
            _dbg(sess.workdir, "acs.run.done",
                 run_id=getattr(res, "run_id", run_id),
                 status=res.status,
                 ok=ok,
                 elapsed_s=getattr(res, "elapsed_s", None),
                 iterations=getattr(res, "iterations", None),
                 workers_spawned=getattr(res, "workers_spawned", None),
                 budget_breach=getattr(res, "budget_breach", None),
                 error=getattr(res, "error", None),
                 summary_len=len(final),
                 elapsed_total_ms=int((time.monotonic() - _dbg_t0) * 1000),
            )

            # M3 (ADR-0170) — render delegation topology graph.
            # Run in a thread pool so matplotlib file I/O + PNG encoding do not
            # block the asyncio event loop (confirmed blocking bug: code-review
            # 2026-06-27). Best-effort: any error is silently suppressed.
            _scan_root_m3 = Path(res.run_dir) if res.run_dir else run_dir
            try:
                await asyncio.to_thread(_render_acs_graph, _scan_root_m3)
            except Exception:  # noqa: BLE001
                pass

            # #4 — surface the chat-triggered ACS run under Agentic Compute.
            # ACSRuntime writes its run data (manifest, iterations, workers,
            # gate_results, output) into a SESSION-scoped run_dir; the console's
            # list_acs_runs/get_acs_run (acs_engine_adapter.py) scan the
            # TENANT-GLOBAL index at <tenant>/global/acs/runs/<run_id>/manifest.json
            # and follow its "run_dir" pointer to the session data. This branch
            # builds ACSRuntime directly (compute-charge bypass — kept intact, charged
            # above), so run_acs_workflow's global-index write never fires and the
            # run stays invisible. Mirror that thin manifest here — index write ONLY,
            # no second compute charge. Path matches _acs_runs_dir() exactly so the
            # reader finds it. Best-effort: a failed index write never breaks the chat.
            _acs_actual_run_dir = Path(res.run_dir) if res.run_dir else run_dir
            try:
                _acs_global_index = (
                    _forge_paths.tenant_global_dir(sess.tenant_id)
                    / "acs" / "runs" / res.run_id
                )
                _acs_manifest = {
                    "run_id": res.run_id,
                    "workflow_id": res.workflow_id,
                    "status": res.status,
                    "engine": "acs",
                    "started_at": _os_turn_start_wall,
                    "completed_at": time.time(),
                    "duration_s": round(res.elapsed_s, 3),
                    "iterations": res.iterations,
                    "workers_spawned": res.workers_spawned,
                    "budget_breach": res.budget_breach,
                    "run_dir": str(_acs_actual_run_dir),
                    "source": "web-chat-delegation",
                }
                _acs_global_index.mkdir(parents=True, exist_ok=True)
                _acs_idx_tmp = _acs_global_index / "manifest.json.tmp"
                _acs_idx_fd = os.open(
                    _acs_idx_tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600
                )
                try:
                    os.write(
                        _acs_idx_fd,
                        (json.dumps(_acs_manifest, indent=2, ensure_ascii=False) + "\n").encode("utf-8"),
                    )
                    os.fsync(_acs_idx_fd)
                finally:
                    os.close(_acs_idx_fd)
                _acs_idx_tmp.replace(_acs_global_index / "manifest.json")

                # Also drop a result.json beside the session run data so the
                # console detail view (get_acs_run follows run_dir → result.json)
                # renders the summary instead of an empty body. ACSRuntime itself
                # does not write result.json — only run_acs_workflow does.
                if _acs_actual_run_dir.is_dir():
                    _acs_result = {
                        "run_id": res.run_id,
                        "workflow_id": res.workflow_id,
                        "status": res.status,
                        "summary": res.summary,
                        "final_output": res.final_output,
                        "error": res.error,
                        "iterations": res.iterations,
                        "workers_spawned": res.workers_spawned,
                        "budget_breach": res.budget_breach,
                        "elapsed_s": res.elapsed_s,
                    }
                    _acs_res_tmp = _acs_actual_run_dir / "result.json.tmp"
                    _acs_res_fd = os.open(
                        _acs_res_tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600
                    )
                    try:
                        os.write(
                            _acs_res_fd,
                            (json.dumps(_acs_result, indent=2, ensure_ascii=False) + "\n").encode("utf-8"),
                        )
                        os.fsync(_acs_res_fd)
                    finally:
                        os.close(_acs_res_fd)
                    _acs_res_tmp.replace(_acs_actual_run_dir / "result.json")
            except OSError:
                pass  # index surfacing is best-effort; the chat reply already streamed

            # M1 post-run artifact scan (ADR-0114 M2.1 + ADR-0170).
            # Covers every qualifying file in run_dir that was NOT already surfaced
            # by the M2 live poll (_live_emitted deduplication).
            # _ACS_SKIP_DIRS / _ACS_SKIP_ROOT_FILES are module-level constants.
            _acs_artifact_parts: list[dict[str, Any]] = []
            _scan_root = Path(res.run_dir) if res.run_dir else run_dir
            if ok and _scan_root.is_dir():
                for _fpath in sorted(_scan_root.rglob("*")):
                    if not _fpath.is_file() or _fpath.name.startswith("."):
                        continue
                    if _fpath.suffix == ".jsonl":
                        continue
                    try:
                        _parts = _fpath.relative_to(_scan_root).parts
                    except ValueError:
                        continue
                    if _parts and _parts[0] in _ACS_SKIP_DIRS:
                        continue
                    if _fpath.parent == _scan_root and _fpath.name in _ACS_SKIP_ROOT_FILES:
                        continue
                    # M2 dedup: skip files already delivered live during the run.
                    if str(_fpath) in _live_emitted:
                        continue
                    _mime = _artifact_mime(_fpath)
                    if _mime is None:
                        continue
                    try:
                        _rel = _fpath.relative_to(sess.workdir)
                    except ValueError:
                        _rel = _fpath.relative_to(_scan_root)
                    try:
                        _size = _fpath.stat().st_size
                    except OSError:
                        continue
                    # M5 provenance label
                    _label = _acs_artifact_label(_fpath, _scan_root)
                    _part: dict[str, Any] = {
                        "kind": "artifact",
                        "name": _fpath.name,
                        "path": str(_rel),
                        "mime": _mime,
                        "size": _size,
                    }
                    if _label:
                        _part["label"] = _label
                    _acs_artifact_parts.append(_part)

            yield {"type": "result", "text": final, "usage": None}

            for _ap in _acs_artifact_parts:
                _ae: dict[str, Any] = {
                    "type": "artifact", "name": _ap["name"], "path": _ap["path"],
                    "mime": _ap["mime"], "size": _ap["size"],
                }
                if "label" in _ap:
                    _ae["label"] = _ap["label"]
                yield _ae

            _turn_parts: list[dict[str, Any]] = [{"kind": "text", "text": final}]
            # M2 live-delivered artifacts must be persisted so they survive reload.
            # Without this, _live_emitted dedup removes them from _acs_artifact_parts
            # and _append_turn would write a history entry with no artifacts.
            _turn_parts.extend(_live_artifact_parts)
            _turn_parts.extend(_acs_artifact_parts)

            _audit_emit(sess, "web.turn.completed", rc=0 if ok else 1,
                        result_chars=len(final), usage=None, delegated_run_id=run_id,
                        artifacts=len(_live_artifact_parts) + len(_acs_artifact_parts))
            if ok:
                tm.record_event(task_id, {
                    "event": "task.completed", "exit_code": 0,
                    "summary": f"delegated to ACS run {run_id}: {len(final)} chars output",
                })
            else:
                tm.record_event(task_id, {"event": "task.failed", "exit_code": 1})
            _os_emit_completed(0 if ok else 1)
            touch(sess, increment_turn=True)
            _append_turn(sess, "assistant", _turn_parts)
            yield {"type": "done"}
            return

    # #8 — engine-respect guard.
    # The console web-chat drives two OS engines: claude_code (the direct
    # `claude -p` subprocess path below) and hermes (the Layer-22 WorkerEngine
    # path → local Ollama HTTP). If the tenant selected a different engine in
    # Setup (opencode / codex / copilot), or the claude binary is missing for a
    # claude_code tenant, surface a clear chat message naming the configured
    # engine and pointing to the Engines page — never a raw "claude binary not
    # found". The delegation branch above is engine-independent (ACS workers
    # inherit the user/tenant model, ADR-0112) and is intentionally NOT gated.
    _engine_msg = _engine_unavailable_message(_os_engine)
    if _engine_msg is not None:
        _os_audit("os_turn.started", {"model": _os_model_used})
        tm.record_event(task_id, {
            "event": "task.failed", "exit_code": 1,
            "error": "configured engine not drivable by web-chat",
        })
        _audit_emit(sess, "web.turn.completed", rc=1, result_chars=len(_engine_msg),
                    usage=None, reason="engine_not_drivable")
        _os_emit_completed(rc=1)
        yield {"type": "delta", "text": _engine_msg}
        yield {"type": "result", "text": _engine_msg, "usage": None}
        touch(sess, increment_turn=True)
        _append_turn(sess, "assistant", [{"kind": "text", "text": _engine_msg}])
        yield {"type": "done"}
        return

    # ── Hermes OS-turn (Layer-22 WorkerEngine path) ──────────────────────────
    # The pre-spawn gates (L44/LIP/L34/L35) ALREADY ran above with
    # engine_id=hermes, so this branch is reached only for a permitted turn.
    # HermesEngine drives local Ollama over HTTP — no subprocess, no Anthropic
    # API key. This is the zero-egress / NO-API-KEY path the SetupGate promotes;
    # routing it here is what makes the recommended Hermes onboarding actually
    # answer in the web chat (round-6 blocker).
    if _os_engine == "hermes":
        async for _ev in _stream_hermes_turn(
            sess, prompt, tm, task_id,
            os_audit=_os_audit, audit_emit=_audit_emit,
            emit_completed=_os_emit_completed,
            os_turn_id=_os_turn_id,
        ):
            yield _ev
        return

    # Snapshot workdir before subprocess so we can detect new output files.
    _before_files: set[Path] = set(sess.workdir.rglob("*")) if sess.workdir.exists() else set()
    # Inject CORVIN_SESSION_DIR so the delegation MCP server can locate the
    # session workdir and write WDAT run directories for the Audit graph.
    # Per-subprocess env copy keeps concurrent sessions isolated.
    _spawn_env = {**os.environ, "CORVIN_SESSION_DIR": str(sess.workdir)}
    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(sess.workdir),
            env=_spawn_env,
            limit=8 * 1024 * 1024,  # 8 MB — default 64 KB is too small for large tool results
        )
    except FileNotFoundError as e:
        binary = _claude_binary()
        if e.filename and str(e.filename) != binary and binary not in str(e.filename or ""):
            msg = f"workdir missing: {sess.workdir}"
        else:
            msg = (
                f"Claude Code CLI not found ({binary!r}). "
                "To fix: install it from https://claude.ai/code, then restart the server. "
                "Or switch to Hermes (local, no API key needed) on Settings → Engines."
            )
        yield {"type": "error", "message": msg}
        yield {"type": "done"}
        return
    except OSError as e:
        yield {"type": "error", "message": f"subprocess spawn failed: {e}"}
        yield {"type": "done"}
        return

    # Subprocess materialized → the turn is real; paired completed is
    # guaranteed via _os_emit_completed on every exit path below. Carries
    # the requested model so a RUNNING turn is already attributable
    # (EU AI Act Art. 12); completed overwrites with the subprocess-
    # confirmed model.
    # Record task.started ONLY now, carrying the real `claude` subprocess pid:
    # the boot stale-task reaper (TaskManager._task_pid_alive) probes this pid
    # with os.kill(pid, 0) and confirms it is a `claude` engine via
    # /proc/<pid>/cmdline. Without a recorded pid the reaper treated every live
    # console turn as an orphan and could falsely finalize a running turn.
    tm.record_event(task_id, {
        "event": "task.started", "engine": "claude",
        "turn": sess.turn_count, "pid": proc.pid,
    })
    _os_audit("os_turn.started", {"model": _os_model_used})

    # Feed the prompt + close stdin so claude knows we're done.
    assert proc.stdin is not None
    try:
        proc.stdin.write(prompt.encode("utf-8"))
        await proc.stdin.drain()
        proc.stdin.close()
    except (OSError, BrokenPipeError):
        pass

    assert proc.stdout is not None
    final_text_parts: list[str] = []
    # Mirror of the parts we yield to the client, in the same shape the
    # frontend's MessagePart union expects — so the turns.jsonl can be
    # replayed verbatim on re-open.
    assistant_parts: list[dict[str, Any]] = []
    last_usage: dict[str, Any] | None = None
    result_text: str = ""
    saw_any_event = False

    # Flag: True only when stdout is fully drained normally. Used in the
    # finally block to decide whether to kill the subprocess — we must not
    # kill on normal completion, but must kill on CancelledError, GeneratorExit,
    # or any other abnormal exit (prevents orphaned subprocesses when the
    # WebSocket client disconnects mid-turn).
    _stdout_drained_normally = False
    try:
        async for raw in proc.stdout:
            saw_any_event = True
            line = raw.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            try:
                evt = json.loads(line)
            except json.JSONDecodeError:
                continue
            etype = evt.get("type")
            if etype == "system" and evt.get("subtype") == "init":
                # Subprocess-confirmed model — authoritative over the
                # requested one (CLI may alias/upgrade model ids).
                if evt.get("model"):
                    _os_model_used = str(evt["model"])
            elif etype == "assistant":
                # Stream-json assistant event — extract text deltas.
                msg = evt.get("message") or {}
                for block in (msg.get("content") or []):
                    if isinstance(block, dict):
                        btype = block.get("type")
                        if btype == "text" and block.get("text"):
                            text = str(block["text"])
                            final_text_parts.append(text)
                            tm.record_event(task_id, {"event": "stream_token", "chunk": text})
                            yield {"type": "delta", "text": text}
                        elif btype == "tool_use":
                            tname = block.get("name") or ""
                            tinput = block.get("input") or {}
                            # Sanitize tool input for UI display + persistence: extract only safe,
                            # non-sensitive parameters (GDPR Art. 5 data-minimisation).
                            # Full input never leaves the server. Safe params only: cmd name,
                            # file name (not full path), URLs, patterns — no secrets exposed.
                            safe_input = _sanitize_tool_input(tname, tinput)
                            assistant_parts.append({
                                "kind": "tool", "name": tname, "input": safe_input,
                            })
                            # GDPR Art. 5 data-minimisation: record tool name only,
                            # never tool input (may contain paths, vault secrets).
                            tm.record_event(task_id, {
                                "event": "tool_use",
                                "tool_name": tname,
                            })
                            _os_tools_called += 1
                            _os_tool_seq += 1
                            # Chain: tool name + seq only, never inputs (GDPR Art. 5)
                            _os_audit("os_turn.tool_called", {
                                "tool_name": tname, "seq": _os_tool_seq,
                            })
                            yield {
                                "type": "tool_use",
                                "name": tname,
                                "input": safe_input,
                            }
            elif etype == "result":
                result_text = evt.get("result") or "".join(final_text_parts)
                last_usage = evt.get("usage") or {}
                yield {
                    "type":   "result",
                    "text":   result_text,
                    "usage":  last_usage,
                }
        _stdout_drained_normally = True
    except (asyncio.CancelledError, GeneratorExit):
        # GeneratorExit (consumer aclose() on a client mid-turn disconnect) is a
        # BaseException sibling of CancelledError and was NOT caught here — so the
        # claude OS path orphaned its engine.span.start / os_turn.started with no
        # matching end (ADR-0171 pairing invariant; the delegation + hermes paths
        # already catch both). Emit the paired completion before re-raising.
        _audit_emit(sess, "web.turn.cancelled")
        _os_emit_completed(rc=-1)
        raise
    finally:
        if not _stdout_drained_normally:
            # Abnormal exit (CancelledError, GeneratorExit from aclose(), or
            # any other exception). Kill the subprocess so it does not become
            # an orphan that blocks on a full stdout pipe.
            try:
                proc.kill()
            except ProcessLookupError:
                pass

    rc = await proc.wait()
    _os_emit_completed(rc)
    if rc != 0 and not saw_any_event:
        stderr_bytes = await (proc.stderr.read() if proc.stderr else asyncio.sleep(0, b""))
        msg = (stderr_bytes or b"").decode("utf-8", errors="replace")[-400:]
        yield {"type": "error", "message": f"claude exited {rc}: {msg.strip() or 'no stderr'}"}

    # Voice annotation suffix: LERN-ZUGABE + METAPHER, mirroring the
    # adapter.py voice pipeline used by Discord/WhatsApp.  Appended as a
    # delta so the chat bubble grows naturally; a second result event
    # updates latestResultText so TTS speaks the annotated version.
    _ann_suffix = ""
    if rc == 0 and result_text:
        _ann_suffix = await _compute_web_annotation_suffix(result_text, sess.tenant_id)
    if _ann_suffix:
        yield {"type": "delta", "text": "\n\n" + _ann_suffix}
        yield {"type": "result", "text": result_text + "\n\n" + _ann_suffix,
               "usage": last_usage}

    touch(sess, increment_turn=True)

    # Persist the assistant turn (combined text-delta-runs + any
    # tool-use cards). The frontend's `<MessageBubble>` consumes the
    # same shape on rehydrate.
    combined_text = "".join(final_text_parts).strip()
    if _ann_suffix:
        combined_text = (combined_text + "\n\n" + _ann_suffix).strip()
    parts_persisted: list[dict[str, Any]] = []
    if combined_text:
        parts_persisted.append({"kind": "text", "text": combined_text})
    parts_persisted.extend(p for p in assistant_parts if p.get("kind") == "tool")
    # Artifact parts are added after the subprocess scan below; collect them here
    # and append to the turn after emitting the artifact events.
    _artifact_parts_buf: list[dict[str, Any]] = []

    _audit_emit(
        sess,
        "web.turn.completed",
        rc=rc,
        result_chars=sum(len(p) for p in final_text_parts),
        usage=last_usage,
    )
    _dbg(sess.workdir, "turn.done",
         rc=rc,
         result_chars=sum(len(p) for p in final_text_parts),
         elapsed_ms=int((time.monotonic() - _dbg_t0) * 1000),
         usage=last_usage,
         session_id=_captured_session_id if "_captured_session_id" in dir() else None,
    )

    # ADR-0080 M1 — record task completion
    if rc == 0:
        tm.record_event(task_id, {
            "event": "task.completed",
            "exit_code": 0,
            "summary": f"{sum(len(p) for p in final_text_parts)} chars output",
        })
    else:
        tm.record_event(task_id, {
            "event": "task.failed",
            "exit_code": rc,
        })

    # Emit artifact events for files Claude created during this turn.
    if sess.workdir.exists():
        after_files = set(sess.workdir.rglob("*"))
        new_files = sorted(
            f for f in (after_files - _before_files)
            if f.is_file() and not f.name.startswith(".")
        )
        for fpath in new_files:
            mime = _artifact_mime(fpath)
            if mime:
                rel = fpath.relative_to(sess.workdir)
                artifact_event = {
                    "type": "artifact",
                    "name": fpath.name,
                    "path": str(rel),
                    "mime": mime,
                    "size": fpath.stat().st_size,
                }
                _artifact_parts_buf.append({
                    "kind": "artifact",
                    "name": fpath.name,
                    "path": str(rel),
                    "mime": mime,
                    "size": fpath.stat().st_size,
                })
                yield artifact_event

    # Persist turn including artifact parts.
    parts_persisted.extend(_artifact_parts_buf)
    # Always write at least a placeholder so the turn appears in history.
    # An empty parts list would silently drop the turn, causing chat history
    # to lose context on revisit (tool-only or image-only responses).
    if not parts_persisted:
        parts_persisted = [{"kind": "text", "text": ""}]
    _append_turn(sess, "assistant", parts_persisted)

    yield {"type": "done"}
