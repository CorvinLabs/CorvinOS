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
from typing import Any, AsyncIterator, Callable

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


# Voice-annotation latency budget. The LERN-ZUGABE / METAPHER suffix spawns
# `claude -p` (Haiku) once per requested mode. On a COLD / fresh install that
# call burns its full internal timeout + Hermes fallback (~50s each) — and,
# because it used to sit on the critical path BEFORE the turn's `done` event,
# it froze the composer + mic (`disabled={streaming}`) for 1-2 minutes after
# EVERY turn. Symptom (verified via live browser E2E): turn 1 is spoken, then
# the UI appears stuck and no further turn can be sent/spoken. We now HARD-CAP
# each subprocess and skip the (secondary) metaphor once the budget is spent,
# so a slow machine degrades to no-annotation-this-turn instead of a frozen UI.
# A healthy machine (fast Haiku ~3s/call) stays well under budget and is
# unaffected. See the annotation call sites in the claude / hermes turn paths.
_ANN_CALL_TIMEOUT_S = 8   # per subprocess.run — hard-killed past this
_ANN_TOTAL_BUDGET_S = 5   # skip any remaining call once elapsed exceeds this


def _annotation_enabled() -> bool:
    """Cheap, spawn-free "could _compute_web_annotation_suffix produce anything?".

    Mirrors that function's own gates (chat-render opt-in + at least one of
    learning/metaphors) WITHOUT running the LLM passes. Exists so the stream can
    tell the client, at the FIRST result event, that a second one is coming:
    the client speaks every result event it sees, so an annotated turn used to
    fire TWO full /voice/tts syntheses — the playback of the first is superseded
    client-side, but the server-side synthesis has already run to completion (and
    archived an orphan file). Cancelling the request would not help either: the
    route is a sync `def`, so a client disconnect does not stop the subprocess.
    """
    if _voice_profile is None:
        return False
    try:
        raw: dict[str, Any] = _voice_profile.load(force=True) or {}
        if not _voice_profile.chat_render_enabled():
            return False
    except Exception:  # noqa: BLE001
        return False
    return (int(raw.get("voice_audience_learning") or 0) > 0
            or raw.get("voice_audience_metaphors") == "on")


async def _compute_web_annotation_suffix(text: str, tenant_id: str) -> str:
    """Append LERN-ZUGABE and/or METAPHER suffix mirroring the voice pipeline.

    Reads voice_audience_* from the tenant voice profile.  Returns the raw
    suffix string (no leading separator) or "" when annotations are not
    requested or any step fails.  Never raises.

    Latency-bounded: each spawned ``claude -p`` is hard-capped at
    ``_ANN_CALL_TIMEOUT_S`` and the metaphor pass is skipped once the running
    turn has already spent ``_ANN_TOTAL_BUDGET_S`` on annotation, so a cold
    engine never freezes the chat composer waiting on the annotation.
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
    _t0 = time.monotonic()

    if want_appendix:
        try:
            _in = annotated
            out = await asyncio.to_thread(
                lambda: subprocess.run(
                    [sys.executable, str(summarizer), "--lang", "de", "--appendix-mode"],
                    input=_in, capture_output=True, text=True,
                    env=env, timeout=_ANN_CALL_TIMEOUT_S, check=True,
                )
            )
            if out.stdout.strip():
                annotated = out.stdout.strip()
        except Exception:  # noqa: BLE001
            pass

    # Skip the (secondary) metaphor pass once the turn has already spent the
    # annotation budget — a cold appendix call must not chain into a second slow
    # spawn and double the composer freeze. The learning appendix is primary.
    if want_metapher and (time.monotonic() - _t0) < _ANN_TOTAL_BUDGET_S:
        tail = annotated[-300:] if len(annotated) > 300 else annotated
        if not any(m in tail for m in _METAPHER_MARKERS):
            try:
                _in = annotated
                # Cap this call's OWN timeout to what's left of a single
                # _ANN_CALL_TIMEOUT_S-sized window since turn start, not a
                # fresh full _ANN_CALL_TIMEOUT_S on top of whatever the
                # appendix call already spent — otherwise a 4.9s appendix call
                # (just under the 5s gate above) plus a full 8s metaphor
                # timeout could still freeze the composer for ~13s, well past
                # what "total budget" implies.
                _remaining = max(1.0, _ANN_CALL_TIMEOUT_S - (time.monotonic() - _t0))
                out = await asyncio.to_thread(
                    lambda: subprocess.run(
                        [sys.executable, str(summarizer), "--lang", "de", "--metapher-mode"],
                        input=_in, capture_output=True, text=True,
                        env=env, timeout=_remaining, check=True,
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


# ── ADR-0194 Phase 1 — per-turn voice artifact ───────────────────────────────
# The spoken audio for a turn already exists server-side: /voice/tts summarises
# the reply, shells out to say.py (which WRITES an audio file), returns the bytes
# — and then deletes the file. Keeping that file inside the session workdir turns
# it into an ordinary chat artifact for free: _artifact_mime() classifies audio/*,
# the workdir route serves it inline, ArtifactCard renders a real <audio> player,
# and it is erased with the session (Layer 33/36). No extra synthesis and — because
# /voice/tts runs AFTER the turn's `done` — no added turn latency, so the composer
# never stalls waiting for TTS (the freeze class fixed in 0.10.36).
_VOICE_SUBDIR = "voice"


def voice_key(text: str) -> str:
    """Stable key for a turn's spoken audio, derived from the turn TEXT.

    Writer (/voice/tts, which synthesises when the user actually hears the reply)
    and reader (history rehydrate) never share a turn id — a persisted turn has no
    identifier of its own. A hash of the NORMALISED text is what lets them meet:
    the streamed `result` text and the persisted `combined_text` differ in
    whitespace/trailing newlines, which must NOT yield a different key.
    """
    import hashlib  # noqa: PLC0415 — local: only this helper needs it
    norm = re.sub(r"\s+", " ", (text or "")).strip()
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()[:16]


def voice_dir(tenant_id: str, sid: str) -> Path:
    """Session-scoped directory holding this chat's per-turn voice audio."""
    return _workdir(tenant_id, sid) / _VOICE_SUBDIR


# ── ADR-0194 Phase 3 — full read-aloud segmentation ──────────────────────────
# The automatic voice is a SUMMARY by construction (/voice/tts runs the reply
# through summarize.py, ≤400 chars). Phase 3 adds the other rendering: speak the
# WHOLE answer, split into segments a TTS provider will accept (OpenAI TTS-1 caps
# at 4096 chars) and a listener can follow.
#
# The load-bearing invariant is COVERAGE: concatenating the segments must
# reproduce every word of the input, in order. A splitter that silently drops a
# tail would reintroduce exactly the defect this phase exists to remove — "a big
# part is never actually read aloud".
_VOICE_SEGMENT_MAX_CHARS = 1800


# Scripts written without spaces between words. A long run in one of these is
# NOT a "token" in the sense the oversized-token rule protects (a URL, an
# identifier) — it is ordinary prose that simply has no space to break at, so
# slicing it by length cuts no word in half. Deliberately narrow: CJK
# ideographs + kana + Hangul + Thai. Latin/Cyrillic/Greek/Arabic runs are NOT
# listed — an oversized token there really is one token and must stay whole.
_SPACE_FREE_SCRIPT_RANGES = (
    (0x3000, 0x303F),   # CJK punctuation (。、，！？ …) — part of the prose
    (0x3040, 0x30FF),   # Hiragana + Katakana
    (0x3400, 0x4DBF),   # CJK Unified Ideographs Extension A
    (0x4E00, 0x9FFF),   # CJK Unified Ideographs
    (0xAC00, 0xD7AF),   # Hangul syllables
    (0xF900, 0xFAFF),   # CJK Compatibility Ideographs
    (0xFF00, 0xFFEF),   # Fullwidth forms (！？，：ABC …)
    (0x0E00, 0x0E7F),   # Thai
)

# A token that must never be sliced: a URL / path / bare identifier is ONE atom,
# and half of it is unspeakable noise. Deliberately narrow — everything else
# oversized gets sliced, because respecting the provider cap matters more.
_URLISH_RE = re.compile(r"^[A-Za-z0-9._~:/?#\[\]@!$&'()*+,;=%-]+$")


def _is_space_free_script(token: str) -> bool:
    """True iff slicing *token* by length is safe — i.e. it is prose in a script
    with no word spaces, rather than a single unsplittable atom.

    The test is NOT "is it majority CJK". That was the first attempt and it was
    wrong in the direction that hurts: a Chinese TECHNICAL answer (CJK prose with
    Latin API names, no spaces, and CJK punctuation — which the ranges did not
    even count) fell under 50% and was left as one 8000-char segment, which the
    route then clamped to 4000 and dropped HALF the answer silently. Worse than
    the loud failure it replaced.

    So: slice anything that carries real space-free-script content and is not
    URL-ish. A URL never contains an ideograph, so the two never collide.
    """
    if not token:
        return False
    if _URLISH_RE.match(token):
        return False        # a URL/path/identifier is one atom — never cut it
    return any(
        any(lo <= ord(ch) <= hi for lo, hi in _SPACE_FREE_SCRIPT_RANGES)
        for ch in token
    )


def split_for_speech(text: str, max_chars: int = _VOICE_SEGMENT_MAX_CHARS) -> list[str]:
    """Split *text* into speakable segments of at most *max_chars*.

    Boundary preference: paragraph → sentence → word. A single "word" longer than
    max_chars (a URL, a base64 blob, a hash) is emitted WHOLE rather than cut: a
    mid-token cut is unspeakable anyway, and honouring the cap there would corrupt
    the only thing the cap protects — the provider's input.

    Coverage is the contract, not a nice-to-have: every word of the input appears
    exactly once, in order, across the returned segments.
    """
    text = (text or "").strip()
    if not text:
        return []
    if len(text) <= max_chars:
        return [text]

    # Sentence-ish units, each keeping its own terminator; blank lines are
    # paragraph breaks and therefore natural segment boundaries too.
    units: list[str] = []
    for para in re.split(r"\n\s*\n", text):
        para = para.strip()
        if not para:
            continue
        # ASCII terminators split ONLY when followed by whitespace. A bare
        # [.!?] class also fires inside tokens — "example.com", "3.14", "z.B." —
        # tearing a URL apart into unspeakable fragments (caught by
        # test_oversized_single_token_is_emitted_whole_not_cut).
        #
        # CJK terminators (。！？) split with NO whitespace requirement, because
        # Chinese/Japanese don't put a space after the full stop — and they're
        # unambiguous: unlike '.', they never occur inside a URL or a decimal.
        # Without this the ASCII-only rule matched nothing in CJK text, the
        # space-based word splitter below made no progress either, and a whole
        # answer came back as ONE oversized segment (reproduced: 2800 chars for
        # Chinese WITH full stops, 5000 without). Past the provider's 4096-char
        # cap say.py exits non-zero, /voice/segment returns 204, and playFull
        # reads 204 as end-of-playlist — so a CJK answer got no read-aloud at
        # all, silently. That is the exact defect Phase 3 exists to remove.
        for part in re.split(r"(?<=[.!?])\s+|(?<=[。！？])", para):
            part = part.strip()
            if part:
                units.append(part)

    segments: list[str] = []
    cur = ""

    def _flush() -> None:
        nonlocal cur
        if cur:
            segments.append(cur)
            cur = ""

    for unit in units:
        if len(unit) > max_chars:
            # One sentence bigger than a whole segment — pack it word-wise.
            _flush()
            buf = ""
            for word in unit.split():
                if len(word) > max_chars and _is_space_free_script(word):
                    # A single "word" over the cap that is CJK/Thai-like: the
                    # space-based packing above cannot shrink it (the script has
                    # no word spaces), so emitting it whole would blow the
                    # provider limit and silently kill the whole read-aloud.
                    # Slicing mid-run is safe here precisely BECAUSE the script
                    # has no spaces — there is no word to cut in half. An
                    # oversized LATIN token (a long URL) still falls through and
                    # is emitted whole: cutting that IS destructive, and an
                    # unspeakable URL fragment is worse than an oversized one.
                    if buf:
                        segments.append(buf)
                        buf = ""
                    for i in range(0, len(word), max_chars):
                        chunk = word[i:i + max_chars]
                        if len(chunk) == max_chars:
                            segments.append(chunk)
                        else:
                            buf = chunk  # remainder keeps packing
                    continue
                if buf and len(buf) + 1 + len(word) > max_chars:
                    segments.append(buf)
                    buf = word
                else:
                    buf = f"{buf} {word}" if buf else word
            cur = buf
            continue
        if cur and len(cur) + 1 + len(unit) > max_chars:
            _flush()
        cur = f"{cur} {unit}" if cur else unit
    _flush()
    return segments


def find_turn_voice(tenant_id: str, sid: str, text: str,
                    key: str | None = None) -> Path | None:
    """Return the persisted voice file for a turn's *text*, or None.

    *key* pins the voice_key explicitly (the turn's persisted ``voice_key``
    hint); *text* is only hashed as the legacy fallback when it is absent.

    Extension is provider-dependent (say.py emits OGG-Opus / MP3 / WAV and does
    NOT transcode), so match on the key and take whatever extension landed.
    """
    vdir = voice_dir(tenant_id, sid)
    k = key or (voice_key(text) if text and text.strip() else None)
    if not k:
        return None
    try:
        for p in sorted(vdir.glob(f"{k}.*")):
            # A stale `<key>.<ext>.tmp` (crash / ENOSPC mid-write) also matches
            # `{k}.*`, and would otherwise be served as if it were finished audio.
            if p.suffix == ".tmp" or not p.is_file() or p.stat().st_size <= 0:
                continue
            return p
    except OSError:
        pass
    return None


def find_turn_voice_segments(tenant_id: str, sid: str, text: str,
                             key: str | None = None) -> list[Path]:
    """Archived full-read-aloud segments for a turn's text, in PLAYBACK order.

    Named `<key>-f<NN>.<ext>` by /voice/segment; the zero-padded index is what makes
    a plain lexical sort the correct playback order (`-f09` before `-f10`). The
    summary lives at `<key>.<ext>` and is deliberately NOT matched here — the two
    renderings are separate archives of the same turn.
    """
    k = key or (voice_key(text) if text and text.strip() else None)
    if not k:
        return []
    try:
        return sorted(
            p for p in voice_dir(tenant_id, sid).glob(f"{k}-f*.*")
            # A stale `-fNN.<ext>.tmp` matches this glob too, and would both be
            # served as finished audio and inflate the "voice i/N" labels.
            if p.suffix != ".tmp" and p.is_file() and p.stat().st_size > 0
        )
    except OSError:
        return []


# Per-session ceiling for the ADR-0194 voice archive. There was no cap of any
# kind: _MAX_VOICE_SEGMENTS bounds segments PER TURN and _MAX_SESSIONS_PER_TENANT
# bounds session COUNT, but a single long-lived chat accumulated speech audio for
# every turn forever (~80 KB per summary here — 500 turns is ~40 MB before
# read-aloud segments). Beyond storage that is a GDPR Art. 5(1)(e)
# storage-limitation problem: audio of replies from months ago kept with no
# purpose and no expiry.
_VOICE_ARCHIVE_MAX_BYTES = int(
    os.environ.get("CORVIN_VOICE_ARCHIVE_MAX_BYTES", str(64 * 1024 * 1024)))
# Idle window before a voice group may be evicted. Sized to PLAYBACK cadence,
# not synthesis time: the client prefetches only segment i+1 while i is
# playing, so consecutive archive writes of one live playlist are up to a full
# segment's spoken duration apart — a 1800-char segment
# (_VOICE_SEGMENT_MAX_CHARS) is ~200 s of audio (refutation finding
# 2026-07-17; the first cut used 120 s ≈ 5× say.py synthesis and a live
# playlist could look idle between writes). See prune_voice_archive.
_VOICE_PRUNE_ACTIVE_GRACE_S = int(
    os.environ.get("CORVIN_VOICE_PRUNE_GRACE_S", "300"))


def prune_voice_archive(tenant_id: str, sid: str,
                        max_bytes: int = _VOICE_ARCHIVE_MAX_BYTES,
                        keep: str | None = None) -> int:
    """Evict oldest-first until the session's voice dir fits *max_bytes*.

    Eviction is per TURN (all files sharing a voice_key), never per file. A
    turn's read-aloud is an ORDERED playlist of `<key>-fNN` files, and evicting
    them one at a time produced something strictly worse than losing the audio:
    the survivors were renumbered by attach_voice_artifacts, so the user got a
    player labelled "voice 1/3" that actually started at segment 3 of 5 — the
    first 40% of the answer silently missing, with nothing to signal it. Whole
    groups keep the only degradation the design tolerates: no player at all,
    re-synthesised on demand by the existing Replay / read-aloud controls.

    *keep* names one file (the turn's freshly written audio); its whole group is
    exempt — as is every group younger than the active-write grace window (see
    _VOICE_PRUNE_ACTIVE_GRACE_S), so the cap may be exceeded transiently by the
    current turn plus whatever was written within the grace window. Without it a
    cap smaller than a single file made every turn synthesise its audio and
    immediately delete it again — no player, ever, and the TTS spend burned
    silently on every turn. Same compromise split_for_speech already makes for
    an oversized token: honouring the cap is not worth destroying the one thing
    the cap exists to manage.

    Returns the number of files removed. Best-effort: a failure here must never
    break TTS — the archive is a convenience, the audio was already streamed to
    the caller.

    Eviction is safe by construction: a turn whose audio is gone simply renders
    without a player (find_turn_voice returns None), and the user's existing
    Replay / read-aloud controls re-synthesise it on demand. That is also why
    this needs no index — which matters, because the files are keyed by a hash
    of the text with no back-reference, so "delete the audio for turn X" is not
    expressible; oldest-first over mtime is.
    """
    if max_bytes <= 0:
        return 0
    try:
        vdir = voice_dir(tenant_id, sid)
        if not vdir.is_dir():
            return 0
        # Group by voice_key: `<key>.<ext>` (summary) and `<key>-fNN.<ext>`
        # (read-aloud segments) are all one turn's audio and live or die together.
        groups: dict[str, dict[str, Any]] = {}
        total = 0
        keep_key: str | None = None
        for p in vdir.iterdir():
            # Skip another writer's in-flight temp file: unlinking it makes that
            # writer's replace() raise FileNotFoundError and its archive is
            # silently lost. Both finders already skip .tmp; this must too.
            if not p.is_file() or p.suffix == ".tmp":
                continue
            key = p.name.split(".", 1)[0].split("-f", 1)[0]
            st = p.stat()
            g = groups.setdefault(key, {"mtime": st.st_mtime, "size": 0, "paths": []})
            # A group is as young as its NEWEST file: a playlist written now must
            # not look old just because its first segment was.
            g["mtime"] = max(g["mtime"], st.st_mtime)
            g["size"] += st.st_size
            g["paths"].append(p)
            total += st.st_size
            if keep and p.name == keep:
                keep_key = key
        if total <= max_bytes:
            return 0
        removed = 0
        # Never evict a group that is still being WRITTEN: `keep` only exempts
        # the current writer's own group, so a concurrent turn (second tab,
        # next turn racing a long read-aloud) pruning over the cap could evict
        # a playlist mid-write — the still-arriving `-fNN` segments then formed
        # a partial group that attach_voice_artifacts renumbered from 1, which
        # is exactly the renumbering defect group-eviction exists to prevent.
        # A live playlist touches its group at least once per say.py call, so
        # "newest file older than the grace window" is a safe idle signal; the
        # cap may be exceeded transiently, which the docstring already accepts
        # for the keep-group.
        _now = time.time()
        for key, g in sorted(groups.items(), key=lambda kv: kv[1]["mtime"]):
            if total <= max_bytes:
                break
            if key == keep_key:
                continue
            if _now - g["mtime"] < _VOICE_PRUNE_ACTIVE_GRACE_S:
                continue
            for path in g["paths"]:
                try:
                    path.unlink()
                except OSError:
                    continue
                removed += 1
            total -= g["size"]
        if removed:
            _log.info("voice archive for %s: evicted %d oldest file(s) to stay "
                      "under %d bytes", sid, removed, max_bytes)
        return removed
    except OSError:
        return 0


def _voice_artifact_part(path: Path, label: str) -> dict[str, Any] | None:
    """Build the artifact dict for one persisted voice file, or None if its mime
    doesn't clear the inline-artifact gate. Single source of truth for the
    path/mime/size shape — shared by history hydration (attach_voice_artifacts)
    and the live WS push (publish_voice_event) so the two can never drift."""
    mime = _artifact_mime(path)
    if not mime:
        return None
    return {
        "kind": "artifact",
        "name": path.name,
        "path": f"{_VOICE_SUBDIR}/{path.name}",
        "mime": mime,
        "size": path.stat().st_size,
        "label": label,
    }


# ── Live voice stream event (out-of-band; ADR-0194 live-replay) ─────────────
# /voice/tts and /voice/segment persist the turn's audio via _persist_turn_voice
# and return — until now the archived file only ever became a visible player on
# the NEXT page load, because attach_voice_artifacts() runs solely from the GET
# .../turns route. This is a tiny per-sid fanout so those routes can push one
# event onto the SAME session's open chat WebSocket the moment the archive
# write lands, letting the frontend attach the player to the just-finished
# message live. Deliberately NOT the CCCPubSub (ccc_pubsub.py) pattern —
# that one fans out per-TENANT for cross-tab entity events; this is scoped to
# the single tab actually viewing this chat, and a missing/slow subscriber (tab
# closed, WS mid-reconnect) must never block the REST response that owns it.
_voice_live_subs: dict[str, list[asyncio.Queue]] = {}


def subscribe_voice_live(sid: str) -> tuple[asyncio.Queue, Callable[[], None]]:
    """Subscribe to live voice-attach events for one chat session.

    Returns the queue to await events from and an idempotent unsubscribe
    callback the caller MUST invoke when the connection ends.
    """
    q: asyncio.Queue = asyncio.Queue(maxsize=16)
    _voice_live_subs.setdefault(sid, []).append(q)

    def _unsubscribe() -> None:
        subs = _voice_live_subs.get(sid)
        if not subs:
            return
        try:
            subs.remove(q)
        except ValueError:
            pass
        if not subs:
            _voice_live_subs.pop(sid, None)

    return q, _unsubscribe


def publish_voice_event(sid: str, path: Path, label: str) -> None:
    """Push a live 'voice' stream event for *path* to every open subscriber of
    *sid*. Best-effort: no subscriber (no open tab, or the WS hasn't
    (re)connected yet) is the common case and must stay silent — the reload
    path (attach_voice_artifacts) still picks the file up on next load either
    way, so this is purely an additive live-attach shortcut.
    """
    subs = _voice_live_subs.get(sid)
    if not subs:
        return
    part = _voice_artifact_part(path, label)
    if part is None:
        return
    event = {
        "type": "voice",
        "name": part["name"],
        "path": part["path"],
        "mime": part["mime"],
        "size": part["size"],
        "label": part["label"],
    }
    for q in list(subs):
        try:
            q.put_nowait(event)
        except asyncio.QueueFull:
            continue


def _turn_text(turn: dict[str, Any]) -> str:
    """Concatenate a persisted turn's text parts — the key the audio is filed under."""
    out: list[str] = []
    for p in turn.get("parts") or []:
        if isinstance(p, dict) and p.get("kind") == "text" and p.get("text"):
            out.append(str(p["text"]))
    return "".join(out)


def attach_voice_artifacts(tenant_id: str, sid: str,
                           turns: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Append each assistant turn's archived voice file as an artifact part.

    The audio is written by /voice/tts AFTER the turn was already persisted —
    that ordering is deliberate (it keeps TTS off the turn's critical path, so
    the composer never stalls on synthesis). It therefore cannot be inside
    turns.jsonl, and rewriting history after the fact would be a second writer
    for the same record. Resolving it at READ time instead keeps turns.jsonl the
    single source of truth for the turn and lets the archive be purely additive:
    a turn whose audio was never generated (voice toggled off, TTS unavailable)
    simply gets no player, and one generated later starts appearing with no
    migration. Never raises — history must render even if the archive is gone.
    """
    for turn in turns:
        try:
            if turn.get("role") != "assistant":
                continue
            parts = turn.get("parts")
            if not isinstance(parts, list):
                continue
            if any(isinstance(p, dict)
                   and str(p.get("label") or "").startswith("voice") for p in parts):
                continue  # already carries its player(s)
            text = _turn_text(turn)
            # Prefer the key the writer pinned at persist time; hashing the
            # persisted text is only correct when the turn had a single text
            # block (see _append_turn's voice_key_hint). Legacy turns written
            # before the hint existed carry no key and keep the old behaviour.
            hint = turn.get("voice_key")
            key = hint if isinstance(hint, str) and hint else None

            vf = find_turn_voice(tenant_id, sid, text, key=key)
            if vf is not None:
                art = _voice_artifact_part(vf, "voice")
                if art:
                    parts.append(art)
            # Phase 3: the full read-aloud, in playback order, each segment its own
            # player. Only present for turns the user actually asked to hear in full.
            segs = find_turn_voice_segments(tenant_id, sid, text, key=key)
            for i, seg in enumerate(segs, start=1):
                art = _voice_artifact_part(seg, f"voice {i}/{len(segs)}")
                if art:
                    parts.append(art)
        except Exception:  # noqa: BLE001 — a broken archive must not break history
            continue
    return turns


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


def _console_base_url() -> str:
    """This console's own loopback base URL — for the ADR-0193 corvin-browser
    MCP tool subprocess to call back into the SAME running console process.
    ``CORVIN_CONSOLE_BASE_URL`` overrides wholesale (e.g. non-default test
    ports); otherwise defaults to the systemd/dev.sh-documented 127.0.0.1:8765,
    optionally overridden on just the port via ``CORVIN_CONSOLE_PORT``. Always
    127.0.0.1 — this console binds only to loopback, never a public interface."""
    override = os.environ.get("CORVIN_CONSOLE_BASE_URL", "").strip()
    if override:
        return override.rstrip("/")
    port = os.environ.get("CORVIN_CONSOLE_PORT", "8765").strip() or "8765"
    return f"http://127.0.0.1:{port}"


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
    # Binary present but NOT authenticated (OAuth session / API key absent): the
    # wizard installs the claude binary but login is skippable and commonly
    # deferred, so spawning it would fail every turn with a raw CLI auth error
    # while a fully-provisioned Hermes sits unused — the "positive first run"
    # killer. Fall back to Hermes here too, using the SAME credential signal the
    # rest of the product uses (~/.claude/.credentials.json + ANTHROPIC_API_KEY;
    # no macOS keychain path is used anywhere, so this introduces no new
    # false-negative). The user can `claude auth login` and switch back any time.
    if not _claude_authenticated():
        return "hermes"
    return engine


def _claude_authenticated() -> bool:
    """Cheap, subprocess-free Claude Code auth probe — mirrors the credential
    signal used by engine_detection.probe_claude_code() without the `claude
    --version` spawn (this runs on the per-turn engine-selection path).

    Authenticated iff an OAuth session exists in ~/.claude/.credentials.json OR
    ANTHROPIC_API_KEY is set. Fail-OPEN (returns True) on an unexpected read error
    so a transient glitch never silently reroutes a genuinely-logged-in user off
    Claude — the reroute only fires on a clearly-absent credential.
    """
    if os.environ.get("ANTHROPIC_API_KEY"):
        return True
    try:
        creds_path = Path.home() / ".claude" / ".credentials.json"
        if not creds_path.exists():
            return False
        creds = json.loads(creds_path.read_text(encoding="utf-8"))
        return bool(creds.get("claudeAiOauth") or creds.get("accessToken"))
    except Exception:  # noqa: BLE001
        return True  # fail-open: don't reroute a possibly-authenticated user


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


def will_delegate(sess: "WebChatSession", prompt: str) -> bool:
    """Public mirror of stream_turn's delegation decision (chat_runtime ~L2131).

    The WebSocket handler uses this to skip the engine-unavailable guard for
    turns that will take the ACS-delegation path — delegation runs on
    engine-independent workers, so refusing it because the *direct* OS engine
    (opencode/codex/copilot, or a missing claude) isn't drivable is wrong AND
    the refusal text itself points the user at delegation. Kept in lock-step
    with the runtime decision so gate and execution never disagree.
    """
    if not _delegation_enabled(sess.tenant_id):
        return False
    try:
        from .aco.repair import is_acs_throttled as _is_acs_throttled
        if _is_acs_throttled(sess.workdir):
            return False
    except Exception:  # noqa: BLE001 — repair module unavailable → no throttle
        pass
    return _should_delegate(prompt)


_LANGUAGE_RULE_AUTODETECT = (
    "LANGUAGE: Detect the user's language automatically and reply in the same language. "
    "German message → German reply. English message → English reply. "
    "Never switch languages unless the user explicitly requests it."
)

_WEB_CHAT_SYSTEM_PROMPT = (
    "When saving any output files (images, PDFs, data files, SVGs, code) during this session, "
    "always write them to the CURRENT WORKING DIRECTORY using relative paths "
    "(e.g. ./dog.svg, ./output.png, ./report.pdf). "
    "Do NOT write to the playground repository or any absolute path outside the current directory. "
    "Files saved in the current directory are automatically detected and displayed in the web chat.\n\n"
    # The LANGUAGE paragraph is the AUTO-DETECT default; _web_chat_system_prompt()
    # swaps it for the pinned-language rule when the operator set a Display
    # Language in Settings → Profile (see _language_rule()). `+` — not implicit
    # concatenation: this is a NAME, not a literal.
    + _LANGUAGE_RULE_AUTODETECT
)


def _language_rule() -> str:
    """The LANGUAGE paragraph, chosen by whether the operator pinned one.

    An explicit Settings → Profile language must WIN. The base prompt's
    auto-detect rule ("German message → German reply, English message → English
    reply") directly contradicted the profile line appended later, and the model
    followed the base rule — so an operator with Display Language = Deutsch kept
    getting English replies to English-looking input (reported 2026-07-20, and
    reproduced end-to-end: an English question got an English answer even with
    the profile block already saying "ALWAYS answer in de"). Emitting only ONE
    of the two rules removes the contradiction instead of hoping the later line
    wins.
    """
    lang = ""
    if _voice_profile is not None:
        try:
            lang = (_voice_profile.load() or {}).get("display_language") or ""
        except Exception:  # noqa: BLE001
            lang = ""
    if not str(lang).strip():
        return _LANGUAGE_RULE_AUTODETECT
    return (
        f"LANGUAGE: ALWAYS reply in {lang}. This is the operator's explicit "
        f"setting and OVERRIDES the language of the incoming message — reply in "
        f"{lang} even when the user writes in another language, and never switch "
        f"because a quoted snippet, a code sample or a proper noun is in another "
        f"language."
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


def _persona_mcp_config(tenant_id: str = "_default", workdir: "Path | None" = None,
                        *, browser_token: str | None = None) -> str | None:
    """Materialize the web-chat persona's MCP servers into an ``--mcp-config``
    file path, mirroring the bridge adapter's spawn wiring (adapter.py
    ``_resolve_spawn_inputs``): resolver-injected servers (forge, skill_forge,
    corvin_orchestration, corvin_delegate, ...) merged over the mcp_manager
    catalog's tenant-activated tools (imagegen-zero-config, ...), persona
    winning on key conflicts.

    Adversarial-review CRITICAL (ADR-0190 pass, 2026-07-12): the console
    web-chat — the designated command center — injected the full capability
    map ("you can call workflow_run / a2a_send / ...") into the system prompt
    but attached NO MCP servers to the ``claude -p`` subprocess, so every
    advertised capability was a confidently-wrong claim on this path. The
    messenger path was wired correctly all along; this brings the console to
    parity.

    Never raises; returns None when nothing is available (fail-safe)."""
    if _cowork is None:
        return None
    try:
        merged = _cowork.resolve(_WEB_CHAT_PERSONA, overrides={})
        if not isinstance(merged, dict):
            return None
        mcp = merged.get("mcp_servers")

        # mcp_manager catalog tools (ADR-0096/0191), persona wins on conflict.
        try:
            import mcp_manager.activate as _mcp_activate  # type: ignore
        except ImportError:
            _mcp_activate = None
        if _mcp_activate is not None:
            try:
                catalog_mcp = _mcp_activate.get_active_mcp_servers(
                    tenant_id,
                    image_outdir=str(workdir / "outputs") if workdir else None,
                    browser_token=browser_token,
                    browser_base_url=_console_base_url() if browser_token else None,
                )
                if catalog_mcp:
                    allowed_plugins = merged.get("mcp_plugins_allowed")
                    if isinstance(allowed_plugins, list):
                        catalog_mcp = {k: v for k, v in catalog_mcp.items()
                                       if k in allowed_plugins}
                    combined = dict(catalog_mcp)
                    if isinstance(mcp, dict):
                        combined.update(mcp)
                    mcp = combined
            except Exception:  # noqa: BLE001
                pass
        if not isinstance(mcp, dict) or not mcp:
            return None
        return _cowork.materialize_mcp({"mcp_servers": mcp, **{
            k: merged[k] for k in ("allowed_forged_tools",) if k in merged
        }})
    except Exception:  # noqa: BLE001
        return None


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


def _acs_directive_block(task_text: str) -> str:
    """Bridge-parity ACS-X directive for the console OS-turn (ADR-0203).

    The messenger bridges have injected the ``<acs_directive>`` block
    (ADR-0155) on every turn since ACS-X shipped; the console never did — so
    a console user asking for a recurring monitor, a persistent goal, or a
    data-compute job got no primitive guidance at all. Classify with the
    SHARED heuristic (same table as the bridges; heuristic stage only — the
    per-turn path must not spawn a Haiku subprocess) and render the same
    block. Fail-open: any failure returns "" and the turn proceeds without
    the directive, exactly like the adapter's try/except contract.
    """
    if not task_text.strip():
        return ""
    try:
        _shared = Path(__file__).resolve().parents[3] / "operator" / "bridges" / "shared"
        if str(_shared) not in sys.path:
            sys.path.insert(0, str(_shared))
        from acs_classify import heuristic_classify, render_directive_block  # type: ignore  # noqa: PLC0415
        _bp = _recurrence_supplement(heuristic_classify(task_text), task_text)
        # Review F9: this block is injected ONLY on the DIRECT OS-turn (the ACS
        # fan-out path builds its own manager prompt). The WORKFLOW directive
        # says "Use the Workflow tool for parallel multi-agent execution" —
        # which routes through run_acs_workflow and CHARGES compute_units. On a
        # turn the triage already decided to keep direct + un-metered, that is a
        # contradictory instruction. Suppress it: the direct turn uses Claude
        # Code's own built-in Task tool for any sub-delegation it needs.
        if _bp.primitive == "WORKFLOW":
            return ""
        block = render_directive_block(_bp, persona="assistant")
        return ("\n\n" + block) if block else ""
    except Exception:  # noqa: BLE001 — advisory layer, never break the turn
        return ""


# ── ADR-0213 — ACS delegation result → OS engine transcript sync ────────
# See ADR-0114's follow-up (ADR-0213) for the root cause: the delegation
# branch below never invokes `claude -p` for the OS role, so the CLI's own
# on-disk transcript never advances even though `touch(increment_turn=True)`
# tells the NEXT turn's `resume = turn_count > 0` check that it did.

_CONTEXT_SYNC_SYSTEM = (
    "You are receiving a background note about a task you already delegated "
    "to ACS workers earlier in this same conversation. The delegation and "
    "its result already happened outside this turn — you are not being "
    "asked to do anything now, and no tools are available in this turn. "
    "Read the note, then reply with a short (one sentence) acknowledgement "
    "in the note's language. Do not start new work, do not ask follow-up "
    "questions, and do not repeat the note verbatim."
)

# Hard character caps on both the echoed task and the result text (ADR-0213
# open question #1): an uncompressed worker result echoed into a `claude -p`
# prompt is an unbounded prompt-injection surface with model authority — a
# manipulated sub-task's output would otherwise ride straight back into the
# OS engine's own transcript. Mirrors the `final[:250]` pattern already used
# for user-facing error messages above, just with more headroom since this
# note is never shown to the user.
_CONTEXT_SYNC_TASK_CAP = 200
_CONTEXT_SYNC_RESULT_CAP = 1200
_CONTEXT_SYNC_TIMEOUT = 90.0  # seconds — tool-less, --max-turns 1, should be fast
_CONTEXT_SYNC_OUTPUT_CAP = 20_000  # chars retained from the ack subprocess's own stdout/stderr


def _compress_acs_result_for_context(res: Any, task_text: str, run_id: str,
                                     engine_label: str = "ACS workers") -> str:
    """Build the compressed, tool-less acknowledgement note for the
    context-sync call (ADR-0213). Truncates both the echoed task and the
    ACS result to hard character caps — see the caps' docstring above for
    why this must never pass the raw ``result.json`` through.

    ``ACSResult.final_output`` is a ``dict`` (structured output), not a
    string like ``summary``/``error`` — a plain ``or`` chain across all
    three picks whichever is truthy first and would hand a dict straight
    to ``.strip()`` the moment ``summary`` is empty on an otherwise
    successful, structured-output-only run (adversarial review finding).
    """
    task = task_text.strip()
    task_preview = task[:_CONTEXT_SYNC_TASK_CAP]
    if len(task) > _CONTEXT_SYNC_TASK_CAP:
        task_preview += "…"
    body = (getattr(res, "summary", "") or "").strip()
    if not body:
        final_output = getattr(res, "final_output", None)
        if final_output:
            try:
                body = json.dumps(final_output, ensure_ascii=False, default=str)
            except (TypeError, ValueError):
                body = str(final_output)
    if not body:
        body = (getattr(res, "error", "") or "").strip()
    body_preview = body[:_CONTEXT_SYNC_RESULT_CAP]
    if len(body) > _CONTEXT_SYNC_RESULT_CAP:
        body_preview += "…"
    return (
        f"[Background note — a task you delegated to {engine_label} has "
        "finished. This note is informational only; no action is "
        "requested.]\n\n"
        f"Delegated task: {task_preview}\n"
        f"Run ID: {run_id}\n"
        f"Status: {getattr(res, 'status', 'unknown')}\n"
        f"Result:\n{body_preview or '(no result text)'}\n"
    )


class _ContextSyncProcHolder:
    """Mutable holder so an awaiting caller can kill the sync subprocess
    spawned inside ``_sync_acs_result_to_transcript``'s ``to_thread`` call
    if the enclosing turn is cancelled (client disconnect, server
    shutdown). Mirrors ``acs_runtime._WorkerProcessHolder``, which exists
    for the identical reason: ``asyncio.to_thread()`` does not itself
    interrupt a blocking ``subprocess.Popen``/``communicate()`` call
    already running in the executor thread — without this, a cancelled
    turn leaves the tool-less sync `claude -p` process running for up to
    ``_CONTEXT_SYNC_TIMEOUT`` seconds after the turn already ended."""

    def __init__(self) -> None:
        self.popen: "subprocess.Popen | None" = None
        self.lock = threading.Lock()

    def kill(self) -> None:
        with self.lock:
            proc = self.popen
        if proc is not None and proc.poll() is None:
            try:
                proc.kill()
            except Exception:  # noqa: BLE001 — best-effort, never raise from cleanup
                pass


def _sync_acs_result_to_transcript(sess: WebChatSession, res: Any, run_id: str,
                                    task_text: str, *, model: str | None,
                                    resume: bool,
                                    proc_holder: "_ContextSyncProcHolder | None" = None,
                                    engine_label: str = "ACS workers",
                                    ) -> bool:
    """ADR-0213 — write a compressed acknowledgement of an ACS delegation
    result into the REAL claude CLI transcript via a tool-less
    ``claude -p [--continue] --max-turns 1 --disallowedTools "*"`` call, so
    the next ``--continue`` turn (the normal spawn path a few hundred lines
    below) actually knows the delegation happened.

    Synchronous and blocking by design — callers MUST run this via
    ``asyncio.to_thread`` (mirrors ``_render_acs_graph``'s use of the same
    pattern for the same reason: do not block the event loop). Pass a
    ``proc_holder`` so a cancelled caller can kill the subprocess (see
    ``_ContextSyncProcHolder``).

    The acknowledgement note is compressed BEFORE the subprocess spawns —
    a failure in ``_compress_acs_result_for_context`` must never leave an
    already-spawned, never-communicated `claude -p` process orphaned with
    an open stdin pipe (adversarial review finding: the previous ordering
    computed the note only after `Popen`, so a compression bug crashed
    into the catch-all below without ever calling ``communicate``/kill).

    Returns True only when the subprocess exits 0 — i.e. the transcript
    provably advanced. Callers must treat False as "no transcript write
    happened" and apply the C1 fallback (``touch(increment_turn=False)``),
    exactly like a first-turn-ever spawn failure. Never raises."""
    try:
        note = _compress_acs_result_for_context(res, task_text, run_id, engine_label)
        import acs_runtime as _acs  # type: ignore  # noqa: PLC0415 — path already on sys.path by the caller
        args = _build_args(sess, resume=resume, model=model,
                            task_text=task_text, purpose="context_sync")
        is_win_shim = (sys.platform == "win32" and args
                       and str(args[0]).lower().endswith((".cmd", ".bat")))
        if is_win_shim:
            from agents._win_shim import windows_shim_command  # noqa: PLC0415
            argv = windows_shim_command(args)
        else:
            argv = args
        proc = subprocess.Popen(
            argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, cwd=str(sess.workdir),
            text=True, encoding="utf-8", errors="replace",
        )
        if proc_holder is not None:
            with proc_holder.lock:
                proc_holder.popen = proc
        try:
            _acs._communicate_capped(  # noqa: SLF001 — shared, already-hardened helper
                proc, input_text=note, timeout=_CONTEXT_SYNC_TIMEOUT,
                cap_chars=_CONTEXT_SYNC_OUTPUT_CAP,
            )
        except subprocess.TimeoutExpired:
            _log.warning("[ADR-0213] context-sync subprocess timed out after %ss "
                         "(run_id=%s) — applying C1 fallback (turn_count not advanced)",
                         _CONTEXT_SYNC_TIMEOUT, run_id)
            return False
        if proc.returncode != 0:
            _log.warning("[ADR-0213] context-sync subprocess exited %s (run_id=%s) — "
                         "applying C1 fallback (turn_count not advanced)",
                         proc.returncode, run_id)
        return proc.returncode == 0
    except Exception as exc:  # noqa: BLE001 — best-effort; caller applies C1 fallback on False
        _log.warning("[ADR-0213] context-sync failed (%s: %s, run_id=%s) — "
                     "applying C1 fallback (turn_count not advanced)",
                     type(exc).__name__, exc, run_id)
        return False


def _language_closing_block() -> str:
    """The final, standalone language instruction — empty when no Display
    Language is pinned (then the base auto-detect rule stands alone)."""
    lang = ""
    if _voice_profile is not None:
        try:
            lang = (_voice_profile.load() or {}).get("display_language") or ""
        except Exception:  # noqa: BLE001
            lang = ""
    lang = str(lang).strip()
    if not lang:
        return ""
    return (
        f"\n\n=== OUTPUT LANGUAGE (highest priority, overrides everything above) ===\n"
        f"Write your ENTIRE reply to the user in {lang}. The user set this in "
        f"Settings → Profile and it is binding. Do NOT mirror the language of the "
        f"user's message, of quoted text, of file contents or of any instruction "
        f"above — if the user writes in English, you still answer in {lang}. "
        f"(Code, identifiers and file paths stay as-is.)"
    )


def _turn_system_prompt(sess: WebChatSession, task_text: str = "") -> str:
    """Base web-chat system prompt + per-turn uploaded-file manifest, plus the
    bridge-parity context blocks (ADR-0114): the resolved persona role, the
    Layer-12 voice-profile audience shaping, the Tier-1 user profile and the
    Tier-2 memory index — and, when ``task_text`` is given, the per-task
    ACS-X ``<acs_directive>`` block (ADR-0203 bridge parity). Each added
    block is fail-safe (the helper swallows its own errors) so any failure
    degrades to the v1 minimal prompt rather than breaking the console chat."""
    return (
        _WEB_CHAT_SYSTEM_PROMPT.replace(_LANGUAGE_RULE_AUTODETECT, _language_rule())
        + _attachment_manifest(sess)
        + _persona_prompt_block()
        + _user_profile_block()
        + _memory_index_block()
        + _voice_audience_block()
        + _acs_directive_block(task_text)
        # LAST WORD on language. The rule near the top and the profile line in
        # the middle were both present and still lost: in a ~10 KB, overwhelmingly
        # ENGLISH system prompt a single early directive gets diluted, and an
        # English user message tipped it — reproduced end-to-end (the written
        # prompt file provably contained "ALWAYS reply in de", no competing rule,
        # yet the reply came back English; the SAME directive alone via `claude -p`
        # yields German). Restating it as the final instruction is what makes the
        # operator's Settings → Profile language actually stick.
        + _language_closing_block()
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


def _write_turn_system_prompt(sess: WebChatSession, task_text: str = "") -> Path:
    """Write this turn's merged system prompt to a file in the session
    workdir and return its path.

    Windows fresh-install fix: ``--append-system-prompt <text>`` used to pass
    the FULL merged prompt (persona + attachment manifest + user profile +
    memory index + voice-audience block — routinely several KB) as one raw
    CLI argument. On Windows the ``claude`` binary resolves to a ``.cmd``
    shim, which (per the RCE-safe rewrite below) must be launched via a
    SINGLE ``cmd /c "<command line>"`` string — but cmd.exe's own internal
    command-line buffer is capped at ~8191 characters, far below the ~32767
    ``CreateProcess`` allows for a direct ``.exe`` launch. A multi-KB system
    prompt blows past that every time, and every turn failed immediately
    with cmd.exe's own "command line too long" error before ``claude`` ever
    started — reported live on a fresh Windows 11 install, first turn.
    ``--append-system-prompt-file <path>`` (an alias documented in
    ``claude --help``: "--append-system-prompt[-file]") takes a short path
    instead, keeping every platform's argv small regardless of prompt size —
    strictly safer everywhere, not just a Windows workaround. A dot-prefixed
    filename keeps it out of every artifact-scan site in this module (all of
    which already skip ``name.startswith(".")``).
    """
    path = sess.workdir / ".corvin-system-prompt.txt"
    path.write_text(_turn_system_prompt(sess, task_text), encoding="utf-8")
    return path


def _build_args(sess: WebChatSession, *, resume: bool, model: str | None = None,
                 browser_token: str | None = None, task_text: str = "",
                 purpose: str = "turn") -> list[str]:
    """Build a ``claude -p`` invocation for this turn.

    Resume mode uses ``--continue`` so the per-workdir session state
    carries across turns. First turn falls back to a fresh subprocess.
    The --append-system-prompt-file ensures output files land in the session
    workdir (not the playground repo) so artifact detection works.

    Permission handling (the fresh-install hang fix): the web console has no
    interactive permission-prompt UI, so we must NOT run in the CLI's default
    (interactive) permission mode under ``-p``. We mirror the bridge/task-worker
    default — skip prompts unless the tenant opts into a stricter mode — and
    always register the session workdir (plus any configured workspace roots) as
    allowed ``--add-dir`` directories so the Bash/PowerShell working-directory
    sandbox agrees with the file-tool layer.

    ``purpose="context_sync"`` (ADR-0213) builds a minimal, TOOL-LESS
    ``claude -p [--continue] --max-turns 1 --disallowedTools "*"``
    invocation used only to hand an ACS delegation summary back into the OS
    engine's own CLI transcript. No MCP config, no persona/system-prompt
    blocks, no ``--add-dir``, no tenant allowed-tools merge — the sync call
    must never be able to execute a tool, independent of any tenant
    permission-mode configuration.
    """
    binary = _claude_binary()
    # On Windows, shutil.which() may resolve the npm-installed claude to a
    # .cmd shim (e.g. claude.cmd). asyncio.create_subprocess_exec cannot start
    # a .cmd directly, so it has to run through cmd.exe. We resolve the shim
    # path here but do NOT prepend ``cmd /c`` in the argv: untrusted argv
    # content (e.g. a memory/profile path) must never cross the cmd.exe
    # re-parse boundary as a list2cmdline-quoted argv element — cmd treats
    # the ``\"`` escape as a quote toggle, so ``" & powershell … & "`` would
    # break out and execute (BatBadBut host RCE). The spawn site instead
    # wraps a .cmd shim through _win_shim's cmd.exe-safe quoting. argv[0] stays
    # the resolved .cmd path so the spawn can detect the shim case.
    if sys.platform == "win32" and not os.path.isabs(binary):
        resolved = shutil.which(binary)
        args: list[str] = [resolved or binary]
    else:
        args = [binary]

    if purpose == "context_sync":
        sync_prompt_path = sess.workdir / ".corvin-context-sync-system-prompt.txt"
        sync_prompt_path.write_text(_CONTEXT_SYNC_SYSTEM, encoding="utf-8")
        args += ["-p",
                 "--output-format", "json",
                 "--append-system-prompt-file", str(sync_prompt_path),
                 "--disallowedTools", "*",
                 "--max-turns", "1"]
        if model:
            args += ["--model", model]
        if resume:
            args.append("--continue")
        return args

    args += ["-p",
             "--output-format", "stream-json",
             "--verbose",
             "--append-system-prompt-file", str(_write_turn_system_prompt(sess, task_text))]

    # MCP servers — the persona's resolver-injected servers + mcp_manager
    # catalog tools, exactly like the bridge adapter's spawn path. Without
    # this the console chat advertised capabilities (via the injected
    # capability map) that no attached server could actually serve.
    mcp_config_path = _persona_mcp_config(sess.tenant_id, sess.workdir,
                                          browser_token=browser_token)
    if mcp_config_path:
        args += ["--mcp-config", mcp_config_path]

    # Permission mode: None → skip prompts (default); else a real mode.
    perm_mode = _web_permission_mode(sess.tenant_id)
    if perm_mode is None or perm_mode == "bypassPermissions":
        args.append("--dangerously-skip-permissions")
    else:
        args += ["--permission-mode", perm_mode]
        # Under a prompting permission mode the persona's allowed_tools list
        # is what pre-approves the MCP tools (mirrors adapter.py/_build_args
        # in claude_code.py — space-joined single argv element).
        try:
            _merged = _cowork.resolve(_WEB_CHAT_PERSONA, overrides={}) if _cowork else None
            _allowed = (_merged or {}).get("allowed_tools") or []
            if _allowed:
                args += ["--allowedTools", " ".join(str(t) for t in _allowed)]
        except Exception:  # noqa: BLE001
            pass

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
    # Maintainer decision 2026-07-20 (supersedes 2026-07-16): defaults sit AT the
    # settings.py::_BUDGET_KEYS ceilings so a task never stops on an unconfigured
    # budget — a mid-task budget stop reads as a failure and kept aborting real
    # work. The ceilings themselves stay put and stay guarded: acs_validator
    # R32/R35/R36 still fail LOUDLY on anything above them (the a47c6d3 100x
    # inflation class), and the manager-LLM still cannot RAISE any per-call
    # bound (_worker_budget_for_spawn clamps). What this deliberately gives up
    # is the "one metered compute unit must not authorize the maximum fan-out"
    # guard the 2026-07-16 decision kept: a fresh free-tier install may now
    # spend its daily ACS run at full width/length. MUST stay aligned with
    # _BUDGET_KEYS (a test pins this).
    "max_loops": 100,          # planning rounds for the whole delegation loop
    "max_depth": 4,            # recursive worker-delegation depth (M4) — NOT a loop counter and NOT
                               # raised with the rest: depth is the fan-out EXPONENT (it multiplies
                               # the worker ceiling), and an exhausted depth never aborts a task —
                               # the worker just does the subtask itself instead of sub-delegating.
                               # acs_validator R32 caps this at 10; 4 matches the ACS runtime default.
    "max_total_workers": 64,   # worker subprocesses per delegated turn (= R35 ceiling)
    "max_wall_time": 86400,    # 24 h wall-clock ceiling for the whole delegation loop (= R36 ceiling)
    "timeout_seconds": 86400,  # 24 h per worker subprocess (= _WORKER_TIMEOUT_CEILING); the spawn is
                               # additionally deadlined against REMAINING wall time
    "max_worker_turns": 5000,  # per-worker turn cap (= settings ceiling); the old runtime default of 5
                               # killed workers mid-tool-use → error_max_turns → "unknown error"
}

# Plain-language names for the knobs a delegation can stop on. Keys match the
# BudgetEnvelope.check() breach strings ("max_loops=20 reached").
_BUDGET_LABELS = {
    "max_loops":         {"de": "die Anzahl der Planungsrunden",
                          "en": "the number of planning rounds"},
    "max_total_workers": {"de": "die Anzahl paralleler Worker",
                          "en": "the number of parallel workers"},
    "max_wall_time":     {"de": "die Laufzeit",
                          "en": "the wall-clock time"},
    "max_total_tokens":  {"de": "das Token-Budget",
                          "en": "the token budget"},
    "max_tool_calls":    {"de": "die Anzahl der Tool-Aufrufe",
                          "en": "the number of tool calls"},
}

# Word lists deliberately avoid every de/en-ambiguous token ("was", "in",
# "an", "die", single-letter "a") — the same trap that flipped the voice
# text-first language detection on short German answers (review 2026-07-17).
_DE_HINT_WORDS = frozenset((
    "der", "und", "nicht", "eine", "ist", "ich", "mit", "für", "auf",
    "bitte", "mach", "erstelle", "dann", "wenn", "noch", "auch", "aber",
    "kannst", "soll", "wird", "sind", "wie", "zu", "im", "den", "dem",
    "einen", "aus", "bei", "nach", "über", "alle", "diese", "dieser",
    "das", "ein", "mal", "mir", "es", "oder", "kann", "hier", "jetzt",
    "dass", "schon", "mehr", "neu", "komplett", "baue", "analysiere",
    "schreib", "zeig", "erklär",
))
_EN_HINT_WORDS = frozenset((
    # "will" (German modal verb), "file"/"files" (Denglisch tech prompts) are
    # deliberately absent — same ambiguity rule as above.
    "the", "and", "not", "is", "are", "please", "create", "then", "if",
    "also", "but", "can", "should", "how", "what", "it", "to",
    "of", "this", "that", "with", "for", "on", "you", "from", "all",
    "send", "email", "make", "give", "write", "run", "use", "add", "fix",
    "check", "show", "into", "about", "report", "new",
))


def _prompt_is_german(prompt: str) -> bool:
    """Pick the language for delegation status messages from the user's own
    prompt — the one text this user is guaranteed to read.

    Umlaut/ß presence is STRONG German evidence but CAPPED at +2 total, not
    a knockout and not per-word: "Send an email to Jürgen about the Q3
    report" is English with a German name in it, and "Can you email Jürgen
    Müller about the Zürich meeting?" must not out-vote five English
    stopwords just because it carries three umlaut proper nouns (two
    refutation rounds, 2026-07-17). Everything else is a count of
    unambiguous stopwords; German needs a strict majority, ties default to
    English (repo rule: user-facing runtime text defaults to English). A
    heuristic, not a detector — the failure cost is a status message in the
    wrong-but-readable language.
    """
    text = (prompt or "").lower()
    words = re.findall(r"[a-zäöüß]+", text)
    de = 2 if any(any(ch in w for ch in "äöüß") for w in words) else 0
    de += sum(1 for w in words if w in _DE_HINT_WORDS)
    en = sum(1 for w in words if w in _EN_HINT_WORDS)
    return de > en


def _budget_stop_message(breach: str, iterations: int | None,
                         workers: int | None, *, german: bool) -> str:
    """Explain a bounded stop instead of reporting it as a failure.

    Reaching a delegation budget is the system working as configured, but it used
    to surface as "Delegation fehlgeschlagen: ACS workflow failed with status
    'budget_exhausted' (N iteration(s))" — indistinguishable from a crash for
    anyone who has never seen these numbers, which is everyone on a fresh
    install. ACS already reports WHICH limit was met; name it, say what was done,
    and point at the one place it can be raised. Bilingual because the final
    result text is also SPOKEN by the voice pipeline — a hard-German message
    made the voice switch language mid-session for English users.
    """
    lang = "de" if german else "en"
    key = breach.split("=", 1)[0].strip() if breach else ""
    labels = _BUDGET_LABELS.get(key)
    did = []
    if german:
        what = labels["de"] if labels else "ein Budget-Limit"
        if iterations:
            did.append(f"{iterations} Runde(n)")
        if workers:
            did.append(f"{workers} Worker")
        did_str = f" Bis dahin: {', '.join(did)}." if did else ""
        return (
            f"Budget erreicht — ich habe hier gestoppt, weil {what} aufgebraucht "
            f"war.{did_str} Das ist kein Fehler: die Teilergebnisse oben bleiben "
            f"gültig. Wenn die Aufgabe mehr braucht, kannst du das Limit unter "
            f"Settings → Delegation Budget anheben ({key or 'Budget'}) und es "
            f"erneut versuchen."
        )
    what = labels["en"] if labels else "a budget limit"
    if iterations:
        did.append(f"{iterations} round(s)")
    if workers:
        did.append(f"{workers} worker(s)")
    did_str = f" Progress so far: {', '.join(did)}." if did else ""
    return (
        f"Budget reached — I stopped here because {what} was used up."
        f"{did_str} This is not an error: the partial results above remain "
        f"valid. If the task needs more, you can raise the limit under "
        f"Settings → Delegation Budget ({key or 'budget'}) and try again."
    )

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
    r"und dann|anschließend|danach|mehrere[nrm]?|parallel"
    r"|schritte|steps|multiple"
    r"| dann(?=\s|$)"
    r"|then\b"
    r")\b",
    re.IGNORECASE,
)

# ── ACS-suitability triage (2026-07-20 rework) ────────────────────────
# ACS is a manager/worker FAN-OUT: independent subtasks, each worker a fresh
# `claude -p` with only its subtask + ≤3 KB context state, spawned OUTSIDE the
# session workspace, results merged by a JSON-schema manager loop. That shape
# fits decomposable, read-mostly, parallel work — and structurally MISFITS
# coding: coding is sequential (explore → edit → test → fix), needs the shared
# session workspace + conversation context, and parallel workers editing the
# same files conflict. The direct Claude Code OS-turn does all of that natively
# (session-pinned workdir, its own Task-tool sub-delegation when parallelism
# helps) AND is un-metered — while every ACS turn burns one
# compute_units_per_day (free tier: 1/day). Routing a "fix this bug" into the
# fan-out spends the user's single daily unit on the worse tool.
#
# Coding-shaped prompts: a coding verb or code-context token routes the turn
# to the DIRECT path even when long. Word-boundary anchors as above.
_TRIAGE_CODING_RE = re.compile(
    r"\b("
    r"bug|bugs|fehler|error|exception|traceback|stack\s?trace"
    r"|debugge|debug|debugging"
    r"|refaktoriere|refactor|refactoring"
    r"|kompiliert?|compile[sd]?|build\s+(fails?|error)"
    r"|unit[\s\-]?tests?|failing\s+tests?|tests?\s+(schlagen|fails?|rot|red|grün|green)"
    r"|repo|repository|branch|commit|merge|pull[\s\-]?request|diff"
    r"|funktion|function|methode|method|klasse|class\b|modul|module"
    r"|endpoint|api|schnittstelle"
    r"|code|quellcode|source\s?file|skript|script"
    r"|implementiere|implement|programmiere"
    # Crash/freeze vocabulary (review F5): a bug report rarely names a code
    # token — "die App stürzt ab", "fix the crash", "hängt sich auf" — yet is
    # squarely coding work that must stay on the sequential direct turn.
    r"|crash|absturz|abstürzt|stürzt\s+ab|abgestürzt"
    r"|hängt(\s+sich)?(\s+auf)?|hangs?|freeze[sd]?|einfriert|eingefroren"
    r"|app|anwendung|programm|deployment|deploy"
    r")\b"
    r"|\.(py|js|ts|tsx|jsx|go|rs|java|c|cpp|h|cs|rb|php|sh|ps1|yaml|yml|json|toml|sql|md)\b"
    r"|```",
    re.IGNORECASE,
)
# EXPLICIT parallelism — the user named workers / parallelism / fan-out. This
# is unambiguous intent and outranks the ACS-X blueprint gate (review F2/F3/F4):
# a product-noun collision (Apple *Watch*, 4K *Monitor*, *mit Hermes* the parcel
# carrier) must never hijack an explicit worker request to the direct path.
# Morphology: `parallel\w*` catches "parallele/parallelen"; `worker[ns]?`
# catches "Workern" (review F4).
_EXPLICIT_PARALLEL_RE = re.compile(
    r"\b(parallel\w*|gleichzeitig|worker[ns]?|fan[\s\-]?out)\b",
    re.IGNORECASE,
)
# An EXPLICIT worker / fan-out demand — the ONLY parallelism signal strong
# enough to override the LOOP/GOAL/COMPUTE blueprint or an incidental coding
# token at rule 1b (D6 refutation 2026-07-20). Deliberately narrower than
# _EXPLICIT_PARALLEL_RE: a bare adverb ("parallel"/"gleichzeitig") is excluded
# — "überwache die Dashboards parallel" is genuine monitoring (LOOP → DIRECT),
# not a fan-out request — and so is a bare "worker(s)" noun, which collides
# with "celery worker crashes" (a coding task). A count/quantifier or the
# "parallele Worker" / "fan-out" phrasing is required, matching every pinned
# F2/F3/F4 fan-out prompt ("mit mehreren Workern", "3 workern", "mehrere
# parallele Worker").
_EXPLICIT_WORKER_RE = re.compile(
    r"\b("
    r"fan[\s\-]?out"
    r"|parallele[nrms]?\s+worker[ns]?"
    r"|(\d+|mehrere[nrm]?|zwei|drei|vier|fünf|sechs|several|multiple)"
    r"\s+(parallele[nrms]?\s+)?worker[ns]?"
    # "parallele <plural work-noun>" — the inflected adjective + a plural
    # fan-out noun ("drei parallele Recherchen") is an explicit fan-out; the
    # bare adverb "parallel" ("… Dashboards parallel") is NOT and is excluded.
    r"|parallele[nrms]?\s+(recherchen|analysen|durchläufe|läufe|abfragen|suchen)"
    r")\b",
    re.IGNORECASE,
)
# A real worker-engine name, required before a DELEGATE-shaped prompt routes to
# the direct path (review F2): bare "delegiere" (no engine) and "mit Hermes"
# the parcel carrier must not silently steer a task off the fan-out.
_NAMED_ENGINE_RE = re.compile(
    r"\b(hermes|copilot|codex|opencode|claude[\s\-]?code)\b",
    re.IGNORECASE,
)
# Fan-out-shaped prompts: explicit parallelism, multi-source research,
# per-item bulk work, multi-perspective review — the shapes where N
# independent workers genuinely beat one sequential turn. `parallel\w*` and
# `recherche[n]?` cover the inflected/noun German forms (review F4).
_TRIAGE_FANOUT_RE = re.compile(
    r"\b("
    r"parallel\w*|gleichzeitig|worker[ns]?|fan[\s\-]?out"
    r"|unabhängig\s+voneinander|independently"
    # `mehrere[nrm]?` covers the German flexion forms — the dative "aus
    # mehreren Quellen" matched neither bare `mehrere` nor `mehrere\s+quellen`
    # (adversarial review D7).
    r"|mehrere[nrm]?\s+(quellen|perspektiven|varianten|ansätze|kandidaten|recherchen)"
    r"|multiple\s+(sources|perspectives|variants|approaches|candidates)"
    r"|aus\s+\w+\s+perspektiven|from\s+\w+\s+perspectives"
    r"|für\s+jede[ns]?\b|for\s+each\b|je\s+eine?n?\b"
    r"|recherchiere|recherchen|research"
    r"|vergleiche|compare"
    r"|sammle|collect|crawle"
    r")\b",
    re.IGNORECASE,
)

# German recurrence "alle N Minuten/Stunden/Tage" (adversarial review D6): the
# shared acs_classify heuristic only knows "jede[rn] …" and English
# "every N …", so this equally common form classified as DIRECT — and its
# frequent companion word "parallel" ("prüfe alle 10 Minuten parallel …") then
# hijacked the scheduler task into the quota-burning ACS fan-out via rule 1b.
# Console-side supplement (acs_classify is a shared bridge module; the fix
# lives in the routing layer that needs it — known-gap §7 of
# delegation-routing.md tracks folding the tables together).
_RECURRENCE_DE_RE = re.compile(
    r"\balle\s+(\d+|zwei|drei|vier|fünf|zehn|paar)\s*"
    r"(minuten?|stunden?|sekunden?|tage[n]?)\b",
    re.IGNORECASE,
)


def _recurrence_supplement(bp, task_text: str):
    """Upgrade a weak/DIRECT blueprint to LOOP on a German 'alle N <unit>'
    recurrence. Mirrors the 0.90 weight of the shared `jede[rn]…` signal.
    Never downgrades a strong (>=0.90) non-DIRECT classification; fail-open."""
    try:
        if (bp is not None
                and bp.primitive != "LOOP"
                and (bp.primitive == "DIRECT" or bp.confidence < 0.90)
                and _RECURRENCE_DE_RE.search(task_text)):
            from acs_classify import ACSBlueprint  # type: ignore  # noqa: PLC0415
            return ACSBlueprint(
                primitive="LOOP", confidence=0.90, path="heuristic",
                reason="console supplement: German recurrence 'alle N <unit>'")
    except Exception:  # noqa: BLE001 — advisory layer, never break the turn
        pass
    return bp

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


def _acs_x_blueprint(prompt: str):
    """Classify the task with the shared ACS-X heuristic (bridge parity).

    Returns an ``acs_classify.ACSBlueprint`` or ``None`` when the shared
    module is unavailable (fail-open — the console triage then falls back to
    its own regex rules alone). Heuristic stage ONLY: the triage path must
    stay 0 ms / no-subprocess, so the Haiku fallback stage is never invoked
    here. Import is lazy + path-inserted because chat_runtime lives in
    core/console while acs_classify lives in operator/bridges/shared.
    """
    try:
        _shared = Path(__file__).resolve().parents[3] / "operator" / "bridges" / "shared"
        if str(_shared) not in sys.path:
            sys.path.insert(0, str(_shared))
        from acs_classify import heuristic_classify  # type: ignore  # noqa: PLC0415
        return _recurrence_supplement(heuristic_classify(prompt), prompt)
    except Exception as _exc:  # noqa: BLE001 — advisory layer, never break the turn
        # Log ONCE per process (review observation): if acs_classify ever fails
        # to import in a deployment, rule 2 silently vanishes and every
        # LOOP/GOAL/COMPUTE/DELEGATE task reverts to quota-burning ACS with zero
        # signal. A single warning turns that from invisible into diagnosable.
        global _ACS_X_IMPORT_WARNED
        if not _ACS_X_IMPORT_WARNED:
            _ACS_X_IMPORT_WARNED = True
            _log.warning("[delegation] acs_classify unavailable (%s) — routing "
                         "rule 2 (LOOP/GOAL/COMPUTE/DELEGATE→direct) disabled; "
                         "tasks may over-route to the ACS fan-out", _exc)
        return None


_ACS_X_IMPORT_WARNED = False


# ACS-X primitives that must NEVER route into the ACS fan-out: their correct
# execution mechanism is something else entirely (scheduler//loop iteration,
# session goal, L25 compute, a single engine delegation) and every ACS turn
# burns one compute_units_per_day. Part of the ADR-0203 priority ladder.
_NON_FANOUT_PRIMITIVES = frozenset({"LOOP", "GOAL", "COMPUTE", "DELEGATE"})


def _should_delegate(prompt: str) -> bool:
    """Heuristic triage: does THIS task fit the ACS fan-out? (ADR-0202/0203)

    True → ACS manager/worker fan-out. False → the normal direct Claude Code
    OS-turn, which is NOT "no delegation": Claude Code does its own built-in
    Task-tool sub-delegation there, in the shared session workspace, un-metered
    — and the OS-turn system prompt carries the ACS-X ``<acs_directive>``
    (bridge parity, ADR-0203) steering LOOP/GOAL/COMPUTE/DELEGATE-shaped tasks
    to their correct mechanism.

    Priority ladder (deterministic, 0 ms, no API):
      1. ``/delegate`` prefix → ACS (explicit user override, unchanged).
      1b. An EXPLICIT worker/fan-out demand ("mehreren Workern", "3 workers",
         "fan-out", "parallele Recherchen") → ACS, checked BEFORE the blueprint
         and coding gates: the user literally named workers, and a product-noun
         collision (Apple *Watch*, 4K *Monitor*, *mit Hermes*) or an incidental
         coding token ("API") must not hijack that to DIRECT (F2/F3/F4 + D6(a)).
         A BARE parallel adverb ("parallel"/"gleichzeitig") is deliberately NOT
         enough here (D6 refutation): it is too weak to force the quota-burning
         fan-out — "überwache die Dashboards parallel" is monitoring (LOOP),
         "prüfe alle 10 Minuten parallel" is a scheduler task — so a bare adverb
         falls through to rule 2 (blueprint → DIRECT) and rule 3 (fan-out shape),
         which already require a substantive multi-source shape.
      2. ACS-X shape LOOP/GOAL/COMPUTE at ANY confidence → DIRECT: a
         recurring/monitoring task belongs to the scheduler or a /loop
         iteration, a persistent objective to the goal system, data
         processing to L25 compute — never a quota-burning one-shot fan-out.
         "Any confidence" is the F1 fix: "stündlich"/"täglich" weigh 0.60-
         0.65, below the old 0.70 gate, yet must still not burn quota. The
         DELEGATE primitive routes DIRECT only when a real engine is NAMED
         (F2: bare "delegiere"/"mit Hermes" must not steer off the fan-out).
         Checked BEFORE the fan-out shape so "recherchiere jede Stunde ..."
         does not mis-route into ACS on its research wording.
      3. Fan-out-shaped (multi-source research, per-item bulk work,
         multi-perspective) → ACS — N independent workers beat one turn.
      4. Coding-shaped (bug/fix/refactor/implement/test/crash with code
         context) → DIRECT, even when long: coding is sequential, needs the
         shared workspace + conversation context, and each ACS turn burns one
         compute_units_per_day. Pre-2026-07-20 the strong-verb list sent
         every coding task into the fan-out — the historical error classes
         (error_max_turns, worker parse failures, "Delegation
         fehlgeschlagen: unknown error") almost all came from that mismatch.
      5. Remaining substantive work (strong verbs, long or multi-step weak
         verbs, ≥400 chars) → ACS, as before.
    Regex anchors prevent false-positives: "latest" ≁ test, "prefix" ≁ fix.
    """
    p = prompt.strip()
    # Word-boundary match, in lockstep with stream_turn's `_force_delegate`
    # (2026-07-24 refutation: a bare `startswith` here made "/delegatex …"
    # delegate while stream_turn treated it as a plain prompt — two parsers
    # for one grammar, the exact divergence ADR-0215 F3 removed).
    _pl = p.lower()
    if _pl == _DELEGATE_PREFIX or _pl.startswith(_DELEGATE_PREFIX + " "):
        return True
    # D6 (adversarial review 2026-07-20): the coding triage and the
    # LOOP/GOAL/COMPUTE blueprint are evaluated BEFORE rule 1b fires. Rule 1b
    # used to sit unconditionally above both, so incidental parallel
    # vocabulary ("celery worker crashes", "zwei Nutzer gleichzeitig",
    # "alle 10 Minuten parallel") hijacked coding and scheduler tasks into
    # the quota-burning fan-out — violating the §6 invariants of
    # delegation-routing.md ("Coding never routes into the ACS fan-out",
    # "LOOP … never route into the ACS fan-out"). DELEGATE deliberately stays
    # BELOW rule 1b: "mit Hermes" the parcel carrier + explicit workers must
    # keep fanning out (F2/F3/F4).
    _coding = bool(_TRIAGE_CODING_RE.search(p))
    _bp = _acs_x_blueprint(p)
    # Rule 1b — an EXPLICIT worker/fan-out demand wins over the classifier's
    # product-noun collisions (Apple *Watch* → LOOP 0.85, 4K *Monitor*, *mit
    # Hermes*) AND over an incidental coding token: the user literally named
    # workers, so honour it (F2/F3/F4, + D6(a) refutation — an "API" token must
    # not cancel "mit mehreren Workern"). The earlier fix suppressed this on a
    # 0.90 confidence threshold, which let the whole 0.60–0.85 LOOP band
    # ("überwache … parallel") slip through on a bare adverb. The real
    # discriminator is signal STRENGTH, not confidence: only an explicit
    # worker/fan-out phrase reaches here. A bare "parallel"/"gleichzeitig"
    # adverb falls through to rule 2 (the LOOP/GOAL/COMPUTE blueprint routes it
    # DIRECT) and rule 3 (which demands a substantive multi-source shape).
    if _EXPLICIT_WORKER_RE.search(p):
        return True
    # Rule 1c (ADR-0217) — big-data shapes are delegation-AFFIRMATIVE: they
    # route into the delegated branch, where _delegation_engine_target sends
    # them to the ACS manager/worker fan-out ("ACS only for big data",
    # maintainer decision 2026-07-24). Without this, the COMPUTE blueprint at
    # rule 2 swallowed e.g. "Analysiere 500 GB Serverlogs … vergleiche die
    # Regionen" into a DIRECT turn and the big-data→ACS mapping never fired
    # (found by the ADR-0217 real-E2E run). Two carve-outs keep their
    # structurally cheaper mechanism: recurrence/goal shapes (scheduler /
    # goal system — a DAILY big-data scan is still a scheduler task) and a
    # NAMED worker engine (the direct delegate_* path).
    if _is_big_data_task(p):
        _named_delegate = bool(
            _bp is not None and _bp.primitive == "DELEGATE"
            and _NAMED_ENGINE_RE.search(p)
        )
        if not _named_delegate and (
            _bp is None or _bp.primitive not in ("LOOP", "GOAL")
        ):
            return True
    # Rule 2 — non-fan-out ACS-X primitives route to their correct mechanism.
    if _bp is not None and _bp.primitive in _NON_FANOUT_PRIMITIVES:
        if _bp.primitive == "DELEGATE":
            # Only a NAMED engine is an unambiguous delegate intent.
            if _NAMED_ENGINE_RE.search(p):
                return False
        elif _bp.confidence >= 0.50:  # LOOP/GOAL/COMPUTE at any real signal
            return False
    if _TRIAGE_FANOUT_RE.search(p):
        has_verb = bool(_TRIAGE_VERB_RE.search(p) or _TRIAGE_STRONG_RE.search(p))
        has_multi = bool(_TRIAGE_MULTI_RE.search(p))
        # Fan-out markers on smalltalk ("wie vergleiche ich?") still need a
        # substantive shape: a verb plus multi-step/length. (Explicit
        # parallel/worker words already returned True at rule 1b.)
        if has_verb and (has_multi or len(p) >= 160):
            return True
    if _coding:
        return False
    if len(p) >= 400:
        return True
    if _TRIAGE_STRONG_RE.search(p):
        return True
    has_verb = bool(_TRIAGE_VERB_RE.search(p))
    has_multi = bool(_TRIAGE_MULTI_RE.search(p))
    return has_verb and (has_multi or len(p) >= 160)


# ── ADR-0217 — TDE-first delegation: big-data discriminator ───────────────────
# Maintainer decision 2026-07-24: within the delegated branch, TDE (ADR-0214)
# is the DEFAULT engine; the ACS manager/worker fan-out remains ONLY for
# (a) the explicit `/delegate` override and (b) big-data-shaped tasks, where
# the manager/worker pattern's per-worker context isolation genuinely beats
# TDE's full-context steps. Deterministic, 0 ms, no API — same contract as the
# rest of the triage (§6 invariant: the triage path never spawns a subprocess).
# Big-data detection (ADR-0217). REBUILT 2026-07-24 (round-2 refutation): the
# earlier single mega-regex (bounded volume/count token + "[^.!?]{0,30}" window
# + data noun) had catastrophic O(n²) backtracking — a pasted digit blob froze
# the whole console event loop for tens of seconds. This version instead uses
# small, individually non-backtracking token regexes and does the "is a data
# noun nearby?" proximity test in Python against the CLAUSE the token sits in
# (bounded by . ! ? ; , : and newlines). No regex ever combines a variable-
# length run with a trailing window, so there is no backtracking blowup, and
# clause-scoping stops "3 GB RAM, welche Dateien?" from binding "Dateien"
# across the comma (the round-2 false-positive class).

# A data noun — the thing a big-data volume/count is ABOUT. Two tiers:
#  (a) an anchored regex (\b…\b) for the short / English / ambiguous nouns
#      where a compound-suffix match would be a false positive
#      (blogs→"logs", arrows→"rows", profiles→"files");
#  (b) plain substring checks for the German data HEADS that legitimately form
#      compounds (Kundentransaktionen, Verkaufsdaten, Messwerte) — done with
#      `in` on the lowercased clause, which is linear and cannot backtrack
#      (a "\b\w*(head)\b" regex would reintroduce the O(n²) blowup).
_DATA_NOUN_RE = re.compile(
    r"\b(?:logs?|logfiles?|logdatei\w*|serverlogs?|clickstreams?|"
    r"records?|rows?|zeilen|eintr[äa]ge?n?|entries|events?|"
    r"dokumente?n?|documents?|dateien|files?|exporte?\w*|dumps?|"
    r"datasets?|corpus|korpus|transactions|measurements|"
    r"backups?|buckets?|s3)\b",
    re.IGNORECASE,
)
_DATA_SUBSTR = (
    # Compound-prone data HEADS. "messung" is deliberately EXCLUDED — it is a
    # substring of the unrelated "Vermessung" (surveying), a false-positive
    # that would burn a quota unit; the more data-specific "messwert" is kept.
    "daten", "transaktion", "messwert",
    "datensatz", "datensätz", "datenbank", "datenmeng",
)
# Common words that END in "…daten" but are NOT data (Kandidaten, Mandaten,
# Soldaten, Sedaten). Stripped before the "daten" substring test so
# "5 Millionen Kandidaten" is not mis-read as a big-data task (2026-07-24
# round-3 refutation, "daten" false-friend class).
_DATA_FALSE_FRIENDS_RE = re.compile(
    r"kandidaten|mandaten|soldaten|sedaten|pedanten", re.IGNORECASE)


def _clause_has_data_noun(clause: str) -> bool:
    if _DATA_NOUN_RE.search(clause):
        return True
    low = _DATA_FALSE_FRIENDS_RE.sub("", clause.lower())
    return any(s in low for s in _DATA_SUBSTR)
# Hardware nouns that make a volume NOT about data ("2 TB SSD", "3 GB RAM").
_HW_NOUN_RE = re.compile(
    r"\b(?:ram|arbeitsspeicher|vram|ssds?|hdds?|festplatten?|disks?|drives?|"
    r"speicher)\b",
    re.IGNORECASE,
)
_BIGDATA_VOCAB_RE = re.compile(
    r"\bbig[\s\-]?data\b|\bdata[\s\-]*lakes?\b|\bdata[\s\-]*warehouses?\b|"
    r"\b(?:riesige[nrms]?|gewaltige[nrms]?|huge|massive|large[\s\-]*scale)\s+"
    r"(?:datenmengen?|datens[äa]tze?n?|datasets?|logfiles?|corpus|korpus)\b",
    re.IGNORECASE,
)
# TB/PB volume token — bounded digits, no trailing window (no backtracking).
_TBPB_RE = re.compile(
    r"\b\d{1,6}(?:[.,]\d{1,3})?\s?(?:tb|tib|pb|pib|terabytes?|petabytes?)\b",
    re.IGNORECASE,
)
_GB_RE = re.compile(
    r"\b\d{1,7}(?:[.,]\d{1,3})?\s?(?:gb|gib|gigabytes?)\b",
    re.IGNORECASE,
)
# A "big count" token: magnitude words, grouped ≥1e6, bare ≥7-digit run, or a
# k/m magnitude suffix (NOT followed by a letter, so "km"/"3m fertig" that is
# a unit/word is excluded). All bounded → each is linear-time.
_BIG_COUNT_RE = re.compile(
    r"\b(?:million(?:en)?|mio\.?|mrd\.?|milliarden?|billions?|millions?)\b"
    r"|\b\d{1,3}(?:[.,]\d{3}){2,6}\b"
    r"|\b\d{7,15}\b"
    r"|\b\d{1,6}(?:[.,]\d{1,3})?[km](?![a-z])",
    re.IGNORECASE,
)
_CLAUSE_DELIMS = ".!?;,:\n\r"


def _clause_bounds(text: str, start: int, end: int) -> tuple[int, int]:
    """[lo, hi) of the clause containing [start, end): extend to the nearest
    clause delimiter on each side."""
    lo = start
    while lo > 0 and text[lo - 1] not in _CLAUSE_DELIMS:
        lo -= 1
    hi = end
    n = len(text)
    while hi < n and text[hi] not in _CLAUSE_DELIMS:
        hi += 1
    return lo, hi


def _clause_around(text: str, start: int, end: int) -> str:
    lo, hi = _clause_bounds(text, start, end)
    return text[lo:hi]


_ABBREV_DOT_RE = re.compile(r"\b(mio|mrd)\.", re.IGNORECASE)
# The big-data signal (a volume/count + a nearby data noun) is a property of the
# TASK DESCRIPTION, which comes first; anything past this is pasted data, which
# needn't be scanned to know the task is big-data-shaped. Capping the scanned
# length bounds the routine to O(cap): without it, a delimiter-free numeric blob
# with many count tokens made the per-match clause scans O(n²) overall — ~2 min
# CPU on a 128 KB paste, on the async event loop (2026-07-24 round-3 refutation).
_BIG_DATA_MAX_SCAN = 2000


def _is_big_data_task(prompt: str) -> bool:
    """Deterministic big-data signal for the TDE-vs-ACS split (ADR-0217).

    Bounded: only the first _BIG_DATA_MAX_SCAN chars are scanned, so the whole
    routine (including the per-match clause scans) is O(_BIG_DATA_MAX_SCAN²)
    worst case (~a few hundred K ops), constant in the real prompt length — no
    O(n²) blowup on a pasted numeric blob (2026-07-24 round-3 refutation)."""
    prompt = prompt[:_BIG_DATA_MAX_SCAN]
    # Drop the period in "Mio."/"Mrd." so it isn't read as a clause boundary
    # that would split the magnitude word off its data noun.
    prompt = _ABBREV_DOT_RE.sub(r"\1", prompt)
    if _BIGDATA_VOCAB_RE.search(prompt):
        return True
    # TB/PB: big data unless the clause names hardware (SSD/HDD/…).
    for m in _TBPB_RE.finditer(prompt):
        lo, hi = _clause_bounds(prompt, m.start(), m.end())
        if not _HW_NOUN_RE.search(prompt[lo:hi]):
            return True
    # GB and big counts: require a data noun in the SAME clause. Dedup by
    # clause: once a clause has been checked and lacked a data noun, skip the
    # other volume/count tokens inside it — this makes a delimiter-free numeric
    # blob (all tokens in one clause) O(n) instead of O(n²) even before the
    # length cap (round-3 refutation).
    for rx in (_GB_RE, _BIG_COUNT_RE):
        _checked_hi = -1
        for m in rx.finditer(prompt):
            if m.start() < _checked_hi:
                continue  # same clause as a previous no-data-noun match
            lo, hi = _clause_bounds(prompt, m.start(), m.end())
            if _clause_has_data_noun(prompt[lo:hi]):
                return True
            _checked_hi = hi
    return False


def _tde_available() -> bool:
    """Availability probe for the ADR-0217 auto-route: can THIS install actually
    RUN a TDE turn? Two conditions, both required (2026-07-24 review):

    1. The full TDE module set imports. Covers the source tree (repo-relative
       injection) and wheel installs (vendored `_vendor/operator/orchestration`
       — now on sys.path via `_operator_bootstrap._OPERATOR_SUBTREES`).
    2. The `claude` CLI resolves. `_stream_tde_turn`'s very first action is a
       real `claude -p` InitialAnalysis call (analysis_runner → helper_model.
       resolve_claude_bin), which raises `AnalysisUnavailable` when the binary
       is absent. On a Hermes-only / no-API-key install that would make every
       auto-delegated turn a guaranteed terminal failure — the TDE path has no
       degrade ladder of its own — so if the CLI is missing we report
       unavailable and let the ACS branch (which pins a local worker model)
       handle delegation instead.

    Import cost is paid once — subsequent calls hit sys.modules."""
    try:
        _orch = Path(__file__).resolve().parents[3] / "operator" / "orchestration"
        if _orch.is_dir() and str(_orch) not in sys.path:
            sys.path.insert(0, str(_orch))
        import tde.analysis_runner  # noqa: F401, PLC0415
        import tde.engine_registry  # noqa: F401, PLC0415
        import tde.send_integration  # noqa: F401, PLC0415
        import tde.worker_ipc  # noqa: F401, PLC0415
        import helper_model  # noqa: PLC0415

        # resolve_claude_bin never raises and falls back to the bare name
        # "claude" — so a truthy return does NOT prove the CLI exists. Verify
        # the resolved value is an actual executable (absolute path on disk, or
        # resolvable on PATH).
        import os as _os  # noqa: PLC0415
        import shutil as _shutil  # noqa: PLC0415
        # expanduser: resolve_claude_bin returns a "~/…" pin verbatim, so a
        # tilde pin set in a non-shell context (systemd Environment=, .env)
        # would otherwise hit the relative-with-sep branch and report TDE
        # unavailable (2026-07-24 round-3 refutation).
        _bin = _os.path.expanduser(helper_model.resolve_claude_bin())
        if _os.path.isabs(_bin):
            _resolved = _os.path.isfile(_bin) and _os.access(_bin, _os.X_OK)
        elif _os.sep in _bin or "/" in _bin:
            # A RELATIVE pin with a separator ("./bin/claude") resolves against
            # the process cwd — but the worker spawns with cwd=tempdir
            # (worker_ipc.run_one_shot), so a console-cwd isfile() check would
            # be a false positive that then fails at spawn with a terminal
            # error instead of degrading to ACS. Treat it as unavailable
            # (fail-safe → ACS degrade), 2026-07-24 refutation.
            _resolved = False
        else:
            _resolved = bool(_shutil.which(_bin))
        return _resolved
    except Exception:  # noqa: BLE001 — any import/resolution defect → not available
        return False


def _tde_quota_peek_ok() -> bool:
    """Non-charging peek at the shared agentic-compute pool (ADR-0216).

    The AUTHORITATIVE charge stays inside TieredDelegationEngine.execute's
    `_enforce_tde_compute_quota` chokepoint — this peek only steers the
    auto-route AWAY from TDE when the pool is already exhausted, so the turn
    lands in the ACS branch below whose `_cq_inc` denial feeds the hardened
    ADR-0201 degrade ladder (single direct turn + notice) instead of TDE's
    terminal quota error. Fail-closed: a missing/broken license module returns
    False → the ACS branch repeats its own fail-closed check and surfaces the
    canonical 402."""
    try:
        _op_root = str(Path(__file__).resolve().parents[3] / "operator")
        if _op_root not in sys.path:
            sys.path.insert(0, _op_root)
        from license.compute_quota import get_today_count as _peek_count  # type: ignore  # noqa: PLC0415
        from license.validator import (  # type: ignore  # noqa: PLC0415
            get_limit as _peek_limit,
            load_license_from_env as _peek_load,
        )
        _peek_load()
        limit = _peek_limit("compute_units_per_day")
        if limit is None:
            return True
        return _peek_count(_forge_paths.corvin_home()) < int(limit)
    except Exception:  # noqa: BLE001 — fail-closed toward the ACS branch's own gate
        return False


def _delegation_engine_target(
    prompt: str,
    *,
    force_delegate: bool,
    tde_available: bool,
    quota_ok: bool,
) -> str:
    """ADR-0217 engine choice WITHIN the delegated branch: "tde" | "acs".

    Pure + deterministic so the routing matrix is unit-testable:
      1. `/delegate` (force_delegate) → ACS — explicit user commands beat
         every classifier (§6 invariant, unchanged).
      2. Big-data shape → ACS — the ONLY auto-routed ACS trigger left.
      3. TDE unavailable or pool exhausted (peek) → ACS — its branch owns the
         hardened ADR-0201 degrade ladder.
      4. Everything else → TDE (the default delegation engine).
    """
    if force_delegate:
        return "acs"
    if _is_big_data_task(prompt):
        return "acs"
    if not (tde_available and quota_ok):
        return "acs"
    return "tde"


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
        "state": {"initial": {"task": task}},
    }


def _audit_path(tenant_id: str) -> Path:
    return _store_dir(tenant_id) / _WEB_AUDIT_LOG_NAME


def _turns_path(tenant_id: str, sid: str) -> Path:
    """Per-session message log used by the SPA to re-hydrate a chat on
    re-open. One JSON object per line, append-only:
        {"role": "user" | "assistant", "ts": <epoch_s>, "parts": [...]}
    """
    return _store_dir(tenant_id) / f"{sid}.turns.jsonl"


def _append_turn(sess: "WebChatSession", role: str, parts: list[dict[str, Any]],
                 voice_key_hint: str | None = None, tde_progress: dict[str, Any] | None = None) -> None:
    """Append one turn (user or assistant) to the session's turns log.

    ``voice_key_hint`` (ADR-0194 Phase 1) pins the voice_key of the text this
    turn will actually be SPOKEN as, which is not always derivable from the
    persisted parts — see the comment at the call site. Omitted for user turns
    and for legacy records, where the reader falls back to hashing the turn text.

    ``tde_progress`` (ADR-0214 k=8): TDE delegation metrics (steps, counts, L34 status).
    Attached by _stream_tde_turn() before persisting, so audit graph survives reload.
    Omitted for non-TDE turns (backward-compatible; optional field in turns.jsonl).

    Best-effort: a failed write does not break the stream — the user
    message is still in the WebSocket history client-side, and the
    assistant's reply was already streamed back."""
    path = _turns_path(sess.tenant_id, sess.sid)
    payload = {"role": role, "ts": time.time(), "parts": parts}
    if voice_key_hint:
        payload["voice_key"] = voice_key_hint
    if tde_progress:
        payload["tde_progress"] = tde_progress
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

    system_prompt = _turn_system_prompt(sess, prompt)

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
    # annotation_pending tells the client "a second, final result event is
    # coming — render this text but do NOT speak it yet". Without it the
    # client spoke both events and paid for two full server-side syntheses
    # per annotated turn (the exact bug this field was introduced to fix for
    # the claude_code path below — the Hermes path never got the same field
    # on its own result events, so it silently reopened the double-speak
    # regression for every tenant on the Hermes engine, found 2026-07-16).
    _ann_pending = bool(final_text.strip()) and _annotation_enabled()
    yield {"type": "result", "text": final_text, "usage": last_usage,
           "annotation_pending": _ann_pending}

    # Voice annotation suffix (LERN-ZUGABE + METAPHER), mirroring the claude path.
    # Gated on _ann_pending, not just final_text: _annotation_enabled() here and
    # _compute_web_annotation_suffix's own gates read the profile seconds apart,
    # so a mid-turn toggle could produce a suffix the client was never told to
    # wait for — it would land in the persisted history and the voice_key but
    # never in the stream, orphaning the turn's archived audio.
    _ann_suffix = ""
    if final_text and _ann_pending:
        _ann_suffix = await _compute_web_annotation_suffix(final_text, sess.tenant_id)
    # Emit the FINAL result whenever the first one was flagged
    # annotation_pending — including when the annotation came back empty
    # (LLM skipped it, budget spent, both backends down). The client is
    # holding its voice waiting for exactly this event; skipping it on the
    # empty path would leave the turn permanently unspoken.
    if _ann_pending:
        if _ann_suffix:
            yield {"type": "delta", "text": "\n\n" + _ann_suffix}
        yield {"type": "result",
               "text": (final_text + "\n\n" + _ann_suffix) if _ann_suffix else final_text,
               "usage": last_usage, "annotation_pending": False}

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
    # ADR-0194: pin the voice_key of what the client will actually SPEAK — the
    # last result event's text. It is not `combined`: that one is .strip()ed
    # while the result event is not, so a reply with edge whitespace hashed
    # differently and the archived audio was orphaned (no player, ever). Same
    # class as the tool-using-turn divergence on the claude path; this path was
    # missed the first time round.
    _spoken = (final_text + "\n\n" + _ann_suffix) if _ann_suffix else final_text
    _append_turn(sess, "assistant",
                 [{"kind": "text", "text": combined or ""}],
                 voice_key_hint=voice_key(_spoken) if _spoken.strip() else None)
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


async def _stream_tde_turn(
    sess: "WebChatSession",
    task_text: str,
    tm: Any,
    task_id: str,
    *,
    os_audit: Any,
    audit_emit: Any,
    emit_completed: Any,
    os_model: str,
    resume: bool,
) -> AsyncIterator[dict[str, Any]]:
    """ADR-0214 — explicit TDE opt-in turn (`/use-engine tiered_delegation`).

    Runs the Tiered Delegation Engine: one REAL InitialAnalysis LM call,
    then parallel step execution with three-gate delegation (L34 → budget →
    loss) via SubprocessWorkerIPC. Yields the same event shapes as the other
    turn paths, plus the `engine` event that feeds the per-turn badge.

    Reached two ways since ADR-0217 (maintainer decision 2026-07-24): the
    explicit `/use-engine tiered_delegation` opt-in (the original ADR-0214
    canary path), and the ADR-0114 auto-delegation branch, where TDE is now
    the DEFAULT delegation engine (`_delegation_engine_target`; ACS keeps
    only /delegate, big-data shapes, and the unavailable/exhausted degrade).

    Attribution note: the ADR-0171 engine span emitted via os_audit
    ("os_turn.started") is attributed to the configured OS engine — which is
    accurate for the ADR-0213 context-sync `claude -p` call this turn makes;
    the TDE execution itself is attributed via the task event
    (engine="tiered_delegation") and the tde.* audit chain.
    """
    import types as _types

    tm.record_event(task_id, {
        "event": "task.started", "engine": "tiered_delegation",
        "turn": sess.turn_count,
    })
    os_audit("os_turn.started", {"model": "tde/helper"})

    rc = 1
    final = ""
    # Unique run id — second-granularity time.time() collides across
    # concurrent sessions (round-2 finding).
    run_id = f"tde-{int(time.time())}-{secrets.token_hex(4)}"

    def _close_books(rc_val: int, *, reason: "str | None" = None,
                     error: "str | None" = None) -> None:
        """Pair os_turn.started/task.started exactly once (round-3: every
        yield is a cancellation window — books must close BEFORE yields)."""
        kw: dict[str, Any] = {"tde_run_id": run_id}
        if reason:
            kw["reason"] = reason
        audit_emit(sess, "web.turn.completed", rc=rc_val,
                   result_chars=len(final), usage=None, **kw)
        ev: dict[str, Any] = {
            "event": "task.completed" if rc_val == 0 else "task.failed",
            "exit_code": rc_val,
        }
        if error:
            ev["error"] = error
        else:
            ev["summary"] = f"TDE run {run_id}: {len(final)} chars output"
        tm.record_event(task_id, ev)
        emit_completed(rc_val)

    books_closed = False
    _reply_persisted = False
    try:
        # tde_run_id (ADR-0214 audit-graph endpoint): the frontend has no other
        # way to learn this turn's correlation id — it never appears as a
        # structured field elsewhere, only buried in the free-text summary of
        # the closing task.completed event. Stamping it on the "engine" event
        # lets chat-registry.ts's existing engine-stamping case attach it to
        # the ChatMessage so the Audit panel's TDE Graph tab can find it.
        yield {"type": "engine", "engine": "tiered_delegation",
               "label": "TDE (Tiered Delegation Engine)", "tde_run_id": run_id}
        yield {"type": "delta",
               "text": "⚙ TDE (Tiered Delegation Engine, ADR-0214) gestartet — "
                       "Initial-Analyse läuft…\n"}

        # orchestration dir → sys.path (repo-relative pattern, bridges/shared)
        _orch = Path(__file__).resolve().parents[3] / "operator" / "orchestration"
        if _orch.is_dir() and str(_orch) not in sys.path:
            sys.path.insert(0, str(_orch))

        # Import pre-check BEFORE spending anything: on a wheel install
        # without the orchestration tree, fail the turn cleanly and skip the
        # context-sync subprocess (syncing a known-dead error would cost a
        # real `claude -p`).
        try:
            from tde.analysis_runner import run_initial_analysis_sync  # noqa: PLC0415
            from tde.engine_registry import EngineRegistry  # noqa: PLC0415
            from tde.send_integration import SendIntegration  # noqa: PLC0415
            from tde.worker_ipc import ProcHolder  # noqa: PLC0415
        except ImportError as _imp_err:
            final = (f"TDE ist auf dieser Installation nicht verfügbar "
                     f"(Modul fehlt: {_imp_err}).")
            _close_books(1, reason="tde_unavailable",
                         error="tde modules unavailable")
            books_closed = True
            yield {"type": "delta", "text": final}
            yield {"type": "result", "text": final, "usage": None}
            touch(sess, increment_turn=False)
            _append_turn(sess, "assistant", [{"kind": "text", "text": final}])
            yield {"type": "done"}
            return

        # Round-4 finding: unlike the ADR-0213 context-sync call below
        # (_ContextSyncProcHolder), this subprocess had no cancellation
        # holder — a client disconnect mid-analysis left the `claude -p`
        # one-shot running for up to _ANALYSIS_TIMEOUT_S (180s) after the
        # turn already ended. Killed from the outer except below.
        _analysis_holder = ProcHolder()
        # k=8: initialized BEFORE the try — the failure branches below (analysis
        # timeout, CLI missing, malformed plan) reach _append_turn(...,
        # tde_progress=tde_progress_dict) too; binding it inside the try made
        # every degraded TDE turn die with UnboundLocalError instead of
        # persisting the assistant turn (adversarial review 2026-07-24).
        tde_progress_dict: dict[str, Any] | None = None
        try:
            context: dict[str, Any] = {
                "statement": {"task": task_text},
                "task_text": task_text,
            }
            analysis = await asyncio.to_thread(
                run_initial_analysis_sync, task_text, context,
                proc_holder=_analysis_holder,
            )
            plan = analysis.global_plan
            yield {"type": "delta", "text": (
                f"⚙ Analyse: {analysis.classification.task_type} / "
                f"{analysis.classification.complexity} — {len(plan.steps)} Steps, "
                f"parallele Ausführung startet…\n"
            )}

            integration = SendIntegration(
                registry=EngineRegistry(real_ipc=True),
                # ADR-0215 F4: scope this turn's loss-tracker evidence to
                # this (tenant, session) — was previously a single
                # process-wide singleton shared (and silently mixed) across
                # every concurrent tenant/session.
                session_key=f"{sess.tenant_id}:{sess.sid}",
                # ADR-0007: stamp every tde.* chain event with the
                # authenticated tenant so the audit-graph endpoint can scope
                # runs per tenant (adversarial review 2026-07-24).
                tenant_id=sess.tenant_id,
            )
            engine_name, result = await integration.select_engine_and_execute(
                "/use-engine tiered_delegation\n" + task_text, context, analysis,
                run_id=run_id,
            )

            summary = result.get("summary") or {}
            selection = result.get("engine_selection") or {}

            # TDE-Progress: Emit structured progress summary (mirrors ACS's worker-completion tracking).
            # Frontend uses this for decision-tree visualization in audit panel + inline badge.
            succeeded = summary.get('succeeded', 0)
            step_count = summary.get('step_count', 0)
            delegated = summary.get('delegated', 0)
            local_count = summary.get('local', 0)
            l34_forced = selection.get("l34_forced", False)

            # k=8: Construct TdeProgress for backend persistence (ADR-0214).
            # Will be attached to ChatMessage via _append_turn so audit graph survives reload.
            tde_progress_dict = {
                "run_id": run_id,
                "total_steps": step_count,
                "completed_steps": succeeded,
                "delegated_count": delegated,
                "local_count": local_count,
                "l34_forced": l34_forced,
                "latency_delta_pct": summary.get("latency_delta_pct"),
                "token_savings_pct": summary.get("token_savings_pct"),
                "token_usage_instrumented": summary.get("token_usage_instrumented", False),
                # ADR-0216 badge fields (2026-07-24 round-4 review): the badge's
                # quota + classification lines were dead because these were never
                # forwarded from summary. quota_* are None on an unmetered run;
                # the badge omits the chip when quota_limit is None.
                "quota_used_today": summary.get("quota_used_today"),
                "quota_limit": summary.get("quota_limit"),
                "task_type": summary.get("task_type"),
                "complexity": summary.get("complexity"),
            }

            # GDPR Art. 30 Audit: Log L34 gate decision (compliance-load-bearing
            # per CLAUDE.md). Required whether delegation happened or not — the
            # gate decision is itself auditable. Emitted via tde_audit (the
            # HASH-CHAINED canonical path), NOT audit_emit: the latter writes a
            # separate, unchained per-tenant web log, which violated the
            # "every audit event must hash-chain" baseline (adversarial review
            # 2026-07-24). Key names must be in tde_audit._ALLOWED_KEYS —
            # delegated_count/local_count/l34_forced are; bare delegated/local
            # would be silently scrubbed.
            try:
                from tde import tde_audit as _tde_audit  # noqa: PLC0415
                _tde_audit.emit("l34_prescan",
                                l34_forced=l34_forced,
                                delegated_count=delegated,
                                local_count=local_count,
                                tde_run_id=run_id,
                                tenant_id=sess.tenant_id)
            except Exception:  # noqa: BLE001 — audit is best-effort by contract
                _log.warning("tde.l34_prescan audit emit failed", exc_info=True)

            if step_count > 0:
                # ADR-0215 (2026-07-24): completes the "TODO: TDE-Engine must
                # calculate actual savings" note from commit fcb6aaf, which
                # removed a `token_savings_pct` field that was always 0 (no
                # real token-usage instrumentation exists — worker_ipc.
                # run_one_shot uses --output-format text, not json). Rather
                # than re-add a fabricated token number, this surfaces what
                # tde_engine._summarize() now genuinely measures: real
                # wall-clock latency, delegated vs. local. `token_savings_pct`
                # stays explicitly None (never a silently-defaulted 0 that
                # looks like a real measurement) until real token
                # instrumentation lands.
                yield {"type": "engine_progress",
                       "engine": "tiered_delegation",
                       "run_id": run_id,
                       "total_steps": step_count,
                       "completed_steps": succeeded,
                       "delegated_count": delegated,
                       "local_count": local_count,
                       "l34_forced": l34_forced,
                       "latency_delta_pct": summary.get("latency_delta_pct"),
                       "token_savings_pct": summary.get("token_savings_pct"),
                       "token_usage_instrumented": summary.get("token_usage_instrumented", False),
                       "quota_used_today": summary.get("quota_used_today"),
                       "quota_limit": summary.get("quota_limit"),
                       "task_type": summary.get("task_type"),
                       "complexity": summary.get("complexity")}

            if engine_name != "tiered_delegation":
                # Round-4 finding: the FIRST "engine" stream event above is
                # emitted before anything ran and is hardcoded to
                # "tiered_delegation" (this whole function only runs for the
                # explicit opt-in). When L34's prescan forces claude_code
                # (selection["l34_forced"]), the turn actually executed
                # entirely locally — a consumer that keys off the structured
                # `engine` event (the documented purpose of this event, per
                # the per-turn engine badge) rather than the German badge
                # text kept showing "tiered_delegation" for a turn that never
                # delegated anything. Emit a corrective event with the engine
                # that actually ran.
                yield {"type": "engine", "engine": engine_name,
                       "label": f"{engine_name} (L34-Pre-Gate erzwungen)"
                                if selection.get("l34_forced") else engine_name,
                       "tde_run_id": run_id}
            # Peek/charge TOCTOU (2026-07-24 round-5 review): the auto-route's
            # non-charging `_tde_quota_peek_ok` can pass, then a parallel channel
            # consumes the last shared-pool unit before this run's authoritative
            # charge — TDE returns reason="quota_exhausted". Surface the SAME
            # friendly upgrade notice the ACS branch shows instead of the raw
            # "compute_units_per_day exceeded" error, honouring the ADR-0201
            # "quota degrades, never hard-fails" invariant on the TDE path too.
            if result.get("reason") == "quota_exhausted":
                final = (
                    "Dein tägliches Agentic-Compute-Kontingent ist ausgeschöpft "
                    "(geteilter Pool für TDE-, ACS- und Compute-Runs im "
                    "Free-Tier). Bitte versuche es morgen erneut oder erhöhe "
                    "dein Kontingent: "
                    "[Member-Upgrade](https://corvin-labs.com/pricing)"
                )
                _close_books(1, reason="tde_quota_exhausted")
                books_closed = True
                yield {"type": "notice", "subtype": "quota_fallback", "message": final}
                yield {"type": "delta", "text": final}
                yield {"type": "result", "text": final, "usage": None}
                touch(sess, increment_turn=False)
                _append_turn(sess, "assistant", [{"kind": "text", "text": final}])
                yield {"type": "done"}
                return
            parts: list[str] = []
            for r in result.get("results", []) or []:
                out = getattr(r, "output", None)
                if getattr(r, "success", False) and out:
                    parts.append(str(out))
            final = ("\n\n".join(parts)).strip() or str(result.get("error") or "")
            ok = bool(result.get("success"))
            rc = 0 if ok else 1

            badge = (
                f"\n\n—\n⚙ Engine: {engine_name} · Steps: "
                f"{summary.get('succeeded', 0)}/{summary.get('step_count', 0)} ok · "
                f"delegiert: {summary.get('delegated', 0)} · lokal: "
                f"{summary.get('local', 0)}"
            )
            if selection.get("l34_forced"):
                badge += " · L34-Pre-Gate: Delegation blockiert (claude_code erzwungen)"
            final = (final + badge).strip()
        except Exception as e:  # noqa: BLE001 — surface, never crash the socket
            final = f"TDE-Turn fehlgeschlagen: {e}"
            rc = 1
            # tde_progress_dict stays None (initialized above) on exception

        # Bookkeeping BEFORE the result yields: a disconnect on any yield
        # below must not leave the task RUNNING / audit unpaired.
        _close_books(rc)
        books_closed = True

        # Persist the assistant turn BEFORE the result yields too (2026-07-24
        # round-5 review, Area #5): _close_books already marked the task
        # completed, so a cancel on either yield below must not leave a
        # completed task with no persisted reply. Guarded by _reply_persisted
        # so the post-try block does not double-append.
        _append_turn(sess, "assistant", [{"kind": "text", "text": final}],
                     tde_progress=tde_progress_dict)
        _reply_persisted = True

        yield {"type": "delta", "text": "\n" + final + "\n"}
        yield {"type": "result", "text": final, "usage": None}
    except (asyncio.CancelledError, GeneratorExit):
        # Client disconnect / server shutdown mid-TDE: close the audit pair
        # before propagating (os_turn.started must not stay unmatched —
        # mirrors the Hermes/ACS paths). The InitialAnalysis one-shot is
        # killed explicitly (round-4 finding, see _analysis_holder above);
        # the per-step delegated/local worker one-shots inside
        # select_engine_and_execute() are still only bounded by their own
        # timeouts + process-group kill (run_one_shot) — killing those on
        # mid-batch cancellation is a larger, tracked follow-up (would need
        # holder plumbing through AdaptiveDelegationExecutor's parallel
        # batches, not a single subprocess).
        try:
            _analysis_holder.kill()  # no-op if never started / already done
        except NameError:
            pass
        if not books_closed:
            audit_emit(sess, "web.turn.cancelled", tde_run_id=run_id)
            tm.record_event(task_id, {"event": "task.failed", "exit_code": -1,
                                      "error": "cancelled mid-TDE"})
            emit_completed(-1)
        raise

    # The reply is normally already persisted before the result yields (Area #5
    # above); this is the fallback for any path that reached here without
    # setting _reply_persisted. k=8: tdeProgress attached for backend
    # persistence so the audit graph survives reload.
    if not _reply_persisted:
        _append_turn(sess, "assistant", [{"kind": "text", "text": final}],
                     tde_progress=tde_progress_dict)

    # ADR-0213 context-sync: advance the claude CLI transcript so the next
    # `--continue` sees this turn. Run it ONLY for a successful turn (rc == 0):
    # a failed / quota-denied TDE turn produced no assistant content worth
    # carrying forward, and the sync is a real `claude -p --continue` on the
    # EXPENSIVE user OS model (Opus/Sonnet) that is NOT metered against the
    # compute pool — spending it on a dead turn is pure waste (2026-07-24
    # review, Area #2; mirrors the import-failure path that already skips it).
    _sync_ok = False
    if rc == 0:
        _shim = _types.SimpleNamespace(
            summary=final[:_CONTEXT_SYNC_RESULT_CAP],
            final_output=None, error=None, status="completed",
        )
        _sync_holder = _ContextSyncProcHolder()
        try:
            _sync_ok = await asyncio.to_thread(
                _sync_acs_result_to_transcript, sess, _shim, run_id,
                task_text, model=os_model, resume=resume,
                proc_holder=_sync_holder,
                engine_label="the Tiered Delegation Engine (TDE)",
            )
        except (asyncio.CancelledError, GeneratorExit):
            _sync_holder.kill()
            raise
        except Exception:  # noqa: BLE001 — best-effort, C1 fallback below
            _sync_ok = False
        os_audit("os_turn.context_sync", {"delegated_run_id": run_id, "synced": _sync_ok})
    touch(sess, increment_turn=_sync_ok)
    yield {"type": "done"}


async def stream_turn(
    sess: WebChatSession,
    prompt: str,
    *,
    sid_fingerprint: str = "",
) -> AsyncIterator[dict[str, Any]]:
    """Run one turn against claude and yield normalised events.

    ``sid_fingerprint`` (the caller's login SessionRecord.sid_fingerprint, when
    known) is used ONLY to mint the ADR-0193 internal browser-tool token below
    — it never gates or scopes anything about the turn itself.

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
    # ADR-0193 — mint a short-lived internal bearer token so the native
    # corvin-browser MCP tool (a SEPARATE subprocess --mcp-config spawns,
    # which does not reliably inherit env from the claude process that
    # spawns it — see get_active_mcp_servers()'s image_outdir precedent)
    # can call this console's OWN /v1/console/browser/* REST API over
    # loopback and reach the SAME live sessions the SPA live-view watches.
    # Threaded through _build_args -> _persona_mcp_config -> the catalog's
    # per-tool env, exactly like image_outdir. Cheap even if the turn never
    # touches the browser tool — no browser session is created until the MCP
    # tool's first HTTP call.
    from .browser import internal_auth as _browser_internal_auth  # noqa: PLC0415
    _browser_token = _browser_internal_auth.mint(sess.tenant_id, sid_fingerprint)
    args = _build_args(sess, resume=resume, model=_os_model,
                       browser_token=_browser_token, task_text=prompt)

    # First-turn auto-title: derive a readable label from the prompt so the
    # sidebar shows "Wie groß ist die Wahrscheinlichkeit, dass …" instead of
    # the 22-char hash. Manual renames win — the heuristic only fires when
    # the user has not picked a title yet. Persisted before the subprocess
    # spawns so a crashed turn still gets a useful sidebar entry.
    # /delegate is a routing directive, not part of the task — strip it for
    # the title AND reuse the flag in the delegation branch below.
    # Word-boundary guard (2026-07-24 review, LOW): require the prefix to be
    # the whole token, so "/delegatex foo" is a plain prompt, not a command
    # that turns "x foo" into the task.
    _p_stripped = prompt.strip()
    _pl = _p_stripped.lower()
    _force_delegate = _pl == _DELEGATE_PREFIX or _pl.startswith(_DELEGATE_PREFIX + " ")
    _task_text = (_p_stripped[len(_DELEGATE_PREFIX):].strip()
                  if _force_delegate else prompt)
    # Strip the directive from the OS prompt too: the delegation branch can
    # degrade to the normal direct turn (quota/import/uncreatable-dir), which
    # must never hand the raw "/delegate …" command text to the LLM — the same
    # class the `/use-engine acs` branch already fixed (2026-07-24 review).
    # Use the stripped text even when empty (bare "/delegate"): `or prompt`
    # would restore the raw command and leak it to the LLM on the fallback
    # turn (2026-07-24 refutation).
    if _force_delegate:
        prompt = _task_text
    # ADR-0214 — explicit engine override (`/use-engine <engine> <task>`,
    # `/engine-auto <task>`, `/debug-engine <task>`).
    #
    # ADR-0215 F3: this used to be a second, hand-rolled regex
    # (`re.IGNORECASE` + `.lower()`) living side-by-side with
    # `tde.slash_command_parser.SlashCommandParser` (no `IGNORECASE`, no
    # `.lower()`) — two parsers for the same grammar that could silently
    # diverge, plus `/engine-auto` and `/debug-engine` were parseable by
    # SlashCommandParser but never reachable here, so both advertised
    # commands (`slash_command_parser.format_help()`) were dead in the
    # console. Fixed: this is now the single call site that parses
    # `/use-engine` et al.; `_stream_tde_turn`'s call into `SendIntegration`
    # re-parses the same grammar internally (that duplication is intentional
    # — `SendIntegration` is also the standalone CLI/bridge entry point per
    # its own module docstring — but both now share one implementation, not
    # two divergent regexes).
    _tde_force = False
    _force_direct = False  # ADR-0217 HIGH-1: explicit `/use-engine claude_code`
                           # must win over the auto-delegation heuristic.
    _debug_engine = False
    _ue_unknown: "str | None" = None
    try:
        _orch_dir = Path(__file__).resolve().parents[3] / "operator" / "orchestration"
        if _orch_dir.is_dir() and str(_orch_dir) not in sys.path:
            sys.path.insert(0, str(_orch_dir))
        from tde.slash_command_parser import SlashCommandParser as _SlashCommandParser  # noqa: PLC0415

        _parsed = _SlashCommandParser().parse(prompt.strip())
    except ImportError:
        _parsed = None  # TDE modules unavailable (e.g. wheel install without
                         # the orchestration tree) — fall through as a plain
                         # prompt, same degrade-gracefully contract as the
                         # `_stream_tde_turn` import guard below.
    except ValueError as _parse_exc:
        # Unknown /use-engine target.
        _parsed = None
        _ue_unknown = str(_parse_exc).split(":", 1)[-1].split(".", 1)[0].strip() or "?"

    if _parsed is not None:
        _ue_task = _parsed.task_text
        if _parsed.debug_mode:
            _debug_engine = True
            _task_text = _ue_task
        elif _parsed.engine_override == "tiered_delegation":
            _tde_force = True
            _task_text = _ue_task
        elif _parsed.engine_override == "acs":
            _force_delegate = True
            _task_text = _ue_task
            # Strip the directive from the OS prompt too — the delegation
            # branch can fall back to the normal turn (quota/import), which
            # must not hand the raw command text to the LLM (round-3 finding).
            prompt = _ue_task or prompt
        elif _parsed.engine_override == "claude_code":
            # ADR-0217 HIGH-1/HIGH-2: an explicit sequential-engine choice must
            # beat the auto-delegation heuristic (§6: explicit user commands
            # beat every classifier) AND strip the directive from BOTH the OS
            # prompt and _task_text — otherwise the raw "/use-engine claude_code
            # <task>" string leaks into the delegated pipeline (worker prompts,
            # analysis, auto-title, pre-spawn gate).
            _force_direct = True
            # Use _ue_task even when empty (bare "/use-engine claude_code"):
            # `or _task_text`/`or prompt` would keep the raw command and leak
            # it to the LLM + auto-title + pre-spawn gate (2026-07-24 refutation).
            _task_text = _ue_task
            prompt = _ue_task
        elif _parsed.original_message.strip().lower().startswith("/engine-auto"):
            # Explicit auto-detect request: identical to a plain prompt —
            # just strip the directive and continue normally. _task_text is
            # cleaned too (HIGH-2) so the delegated path never sees the command.
            _task_text = _ue_task
            prompt = _ue_task

    # Resolve engine early so turn.start debug event can record it.
    # The full pre-spawn gate check (line ~1958) also uses this value.
    _os_engine = _effective_os_engine(sess.tenant_id)

    # ── DEBUG: turn.start ────────────────────────────────────────────────────
    _dbg_t0 = time.monotonic()
    _dbg(sess.workdir, "turn.start",
         sid=sess.sid, chat_key=sess.chat_key,
         tenant=sess.tenant_id,
         prompt_len=len(prompt),
         # No prompt_preview here — this project's own compliance baseline
         # requires audit/debug details stay metadata-only (a raw prompt
         # fragment routinely contains a user's name, email, or the start of
         # a pasted secret within 120 chars). chat_debug.jsonl also sits
         # outside the L16 hash-chain and outside the L36 erasure
         # orchestrator's coverage, so this was PII persisting with no
         # retention/erasure guarantee (adversarial review finding).
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
    # ACS-3: fold the Layer-5 repair throttle into the gate's engine
    # classification. The real delegation decision below (`_del_will_delegate`)
    # includes `not _del_throttled`; if the gate omits it, a throttled turn is
    # classified as ACS (engine=DELEGATION_ENGINE_ID) at the gate but actually
    # runs on the OS engine (claude_code / hermes), so the pre-spawn L34/L35
    # gate checks the wrong compliance row. Compute the throttle once here and
    # reuse it for the decision below so gate and runtime agree.
    try:
        from .aco.repair import is_acs_throttled as _is_acs_throttled
        _del_throttled = _is_acs_throttled(sess.workdir)
    except Exception:  # noqa: BLE001 — repair module unavailable → no throttle
        _del_throttled = False
    # `not _force_direct` MUST mirror `_del_will_delegate` below (2026-07-24
    # review, D2 class): if the gate classifies as DELEGATION_ENGINE_ID but the
    # turn actually runs on _os_engine (because `/use-engine claude_code`
    # suppressed delegation), the pre-spawn L34/L35 gate would check the wrong
    # compliance row. Keep the two `_will_delegate` computations in lockstep.
    # Read `_delegation_enabled` ONCE here and reuse it at the decision below —
    # the two sites straddle an `await _ccc_dispatch`, and re-reading it there
    # would let a mid-turn tenant.corvin.yaml mtime flip diverge the gate's
    # compliance row from the engine that actually spawns (round-4 review).
    _del_enabled = _delegation_enabled(sess.tenant_id)
    _will_delegate = (_del_enabled
                      and not _del_throttled
                      and not _force_direct
                      and (_force_delegate or _should_delegate(prompt)))
    # _os_engine already resolved above (before turn.start debug event)
    _gate_refusal = _spawn_gates.check_console_spawn_or_refusal(
        _task_text, tenant_id=sess.tenant_id, persona="assistant",
        channel=CHANNEL, chat_key=sess.chat_key,
        engine_id=(_spawn_gates.DELEGATION_ENGINE_ID
                   if (_will_delegate or _tde_force) else _os_engine),
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

    # ── ADR-0214 — /use-engine with unknown engine name ──────────────────
    if _ue_unknown is not None:
        _ue_msg = (f"Unbekannte Engine `{_ue_unknown}`. Verfügbar: "
                   "tiered_delegation, acs, claude_code.")
        _os_audit("os_turn.started", {"model": _os_model_used})
        tm.record_event(task_id, {
            "event": "task.failed", "exit_code": 1,
            "error": "unknown engine in /use-engine",
        })
        _audit_emit(sess, "web.turn.completed", rc=1,
                    result_chars=len(_ue_msg), usage=None,
                    reason="use_engine_unknown")
        _os_emit_completed(1)
        yield {"type": "delta", "text": _ue_msg}
        yield {"type": "result", "text": _ue_msg, "usage": None}
        touch(sess, increment_turn=False)
        _append_turn(sess, "assistant", [{"kind": "text", "text": _ue_msg}])
        yield {"type": "done"}
        return

    # ── ADR-0214/ADR-0215 — /debug-engine: show selection signals, run
    # nothing. Previously unreachable (see F3 note above) because this
    # command never made it past slash_commands.py's dispatcher; now that
    # it does, it runs Phase 1 (InitialAnalysis) + Phase 1.5
    # (RobustEngineDetector) and reports the signals as the assistant
    # reply — it never calls EngineRegistry.execute(), so no engine (TDE,
    # ACS, or claude_code) actually runs the task.
    if _debug_engine:
        if not _task_text:
            _dbg_hint = "Bitte Task angeben: /debug-engine <task>"
            _os_audit("os_turn.started", {"model": _os_model_used})
            tm.record_event(task_id, {
                "event": "task.failed", "exit_code": 1,
                "error": "debug-engine command without task text",
            })
            _audit_emit(sess, "web.turn.completed", rc=1,
                        result_chars=len(_dbg_hint), usage=None,
                        reason="debug_engine_empty_task")
            _os_emit_completed(1)
            yield {"type": "delta", "text": _dbg_hint}
            yield {"type": "result", "text": _dbg_hint, "usage": None}
            touch(sess, increment_turn=False)
            _append_turn(sess, "assistant", [{"kind": "text", "text": _dbg_hint}])
            yield {"type": "done"}
            return

        _os_audit("os_turn.started", {"model": _os_model_used})
        try:
            _orch_dir = Path(__file__).resolve().parents[3] / "operator" / "orchestration"
            if _orch_dir.is_dir() and str(_orch_dir) not in sys.path:
                sys.path.insert(0, str(_orch_dir))
            from tde.analysis_runner import run_initial_analysis_sync  # noqa: PLC0415
            from tde.robust_engine_detector import RobustEngineDetector  # noqa: PLC0415
            from tde.loss_profile_tracker import get_session_tracker  # noqa: PLC0415
            from tde.worker_ipc import ProcHolder  # noqa: PLC0415

            _dbg_holder = ProcHolder()
            _dbg_ctx: dict[str, Any] = {
                "statement": {"task": _task_text}, "task_text": _task_text,
            }
            try:
                _dbg_analysis = await asyncio.to_thread(
                    run_initial_analysis_sync, _task_text, _dbg_ctx,
                    proc_holder=_dbg_holder,
                )
            finally:
                # A cancelled to_thread does NOT stop the running `claude -p`
                # one-shot (ProcHolder's whole reason to exist); without this
                # kill a client disconnect mid-/debug-engine left the analysis
                # subprocess burning a real LM call for up to 180s
                # (adversarial review 2026-07-24; kill() is a no-op when the
                # process already exited).
                _dbg_holder.kill()
            _dbg_detector = RobustEngineDetector(
                loss_tracker=get_session_tracker(session_key=f"{sess.tenant_id}:{sess.sid}")
            )
            _dbg_engine, _dbg_conf, _dbg_signals = _dbg_detector.detect_engine(
                _task_text, _dbg_ctx, _dbg_analysis,
            )
            _dbg_lines = [
                f"**Engine-Auswahl (Debug):** `{_dbg_engine}` "
                f"({_dbg_conf:.1%} Konfidenz)",
                f"- Task-Typ: `{_dbg_analysis.classification.task_type}` "
                f"/ Komplexität: `{_dbg_analysis.classification.complexity}`",
                "- Signale:",
            ]
            for _sig_k, _sig_v in _dbg_signals.items():
                _dbg_lines.append(f"  - `{_sig_k}`: {_sig_v}")
            _dbg_lines.append(
                "\n_Kein Engine wurde ausgeführt — nur die Auswahl-Signale "
                "wurden berechnet. Mit `/use-engine <name> <task>` erzwingen._"
            )
            _dbg_msg = "\n".join(_dbg_lines)
        except ImportError as _dbg_imp_err:
            _dbg_msg = f"TDE ist auf dieser Installation nicht verfügbar (Modul fehlt: {_dbg_imp_err})."
        except Exception as _dbg_err:  # noqa: BLE001 — debug command must never 500 the turn
            _log.warning("[/debug-engine] Analyse fehlgeschlagen: %s", _dbg_err)
            _dbg_msg = f"Engine-Debug-Analyse fehlgeschlagen: {_dbg_err}"

        tm.record_event(task_id, {"event": "task.completed", "exit_code": 0})
        _audit_emit(sess, "web.turn.completed", rc=0,
                    result_chars=len(_dbg_msg), usage=None,
                    reason="debug_engine")
        _os_emit_completed(0)
        yield {"type": "delta", "text": _dbg_msg}
        yield {"type": "result", "text": _dbg_msg, "usage": None}
        touch(sess, increment_turn=True)
        _append_turn(sess, "assistant", [{"kind": "text", "text": _dbg_msg}])
        yield {"type": "done"}
        return

    # ── ADR-0214 — TDE explicit opt-in path ──────────────────────────────
    # Fires on the slash command. Since ADR-0217 the ADR-0114 delegation
    # branch below ALSO routes to TDE by default (`_delegation_engine_target`).
    if _tde_force:
        if not _task_text:
            _tde_hint = "Bitte Task angeben: /use-engine tiered_delegation <task>"
            # Full bookkeeping (mirrors the gate-refusal branch): without the
            # task.failed event the pre-created task stays PENDING forever and
            # counts against the per-chat quota (round-2 finding). started
            # MUST precede completed — an unpaired os_turn.completed is the
            # same audit defect in the other direction (round-3 finding).
            _os_audit("os_turn.started", {"model": _os_model_used})
            tm.record_event(task_id, {
                "event": "task.failed", "exit_code": 1,
                "error": "tde command without task text",
            })
            _audit_emit(sess, "web.turn.completed", rc=1,
                        result_chars=len(_tde_hint), usage=None,
                        reason="tde_empty_task")
            _os_emit_completed(1)
            yield {"type": "delta", "text": _tde_hint}
            yield {"type": "result", "text": _tde_hint, "usage": None}
            touch(sess, increment_turn=False)
            _append_turn(sess, "assistant", [{"kind": "text", "text": _tde_hint}])
            yield {"type": "done"}
            return
        async for _ev in _stream_tde_turn(
            sess, _task_text, tm, task_id,
            os_audit=_os_audit, audit_emit=_audit_emit,
            emit_completed=_os_emit_completed,
            os_model=_os_model, resume=resume,
        ):
            yield _ev
        return

    # ── ADR-0217 — bare `/use-engine claude_code` (no task) ──────────────
    # `_force_direct` with an empty task means the whole message was just the
    # directive; prompt is now "" (stripped, HIGH-2). Without this guard the
    # turn would run the OS engine on an empty prompt — a degenerate LLM call
    # that still burns a chat turn (2026-07-24 round-3 refutation). Give the
    # same hint the other empty-directive branches give, with paired
    # bookkeeping so the pre-created task never lingers PENDING.
    if _force_direct and not _task_text:
        _cc_hint = "Bitte Task angeben: /use-engine claude_code <task>"
        _os_audit("os_turn.started", {"model": _os_model_used})
        tm.record_event(task_id, {
            "event": "task.failed", "exit_code": 1,
            "error": "claude_code command without task text",
        })
        _audit_emit(sess, "web.turn.completed", rc=1,
                    result_chars=len(_cc_hint), usage=None,
                    reason="claude_code_empty_task")
        _os_emit_completed(1)
        yield {"type": "delta", "text": _cc_hint}
        yield {"type": "result", "text": _cc_hint, "usage": None}
        touch(sess, increment_turn=False)
        _append_turn(sess, "assistant", [{"kind": "text", "text": _cc_hint}])
        yield {"type": "done"}
        return

    # ── ADR-0114 M1/M2 — delegation path ─────────────────────────────────
    # Tenant opt-in + triage: substantive tasks run on ACS workers (which
    # inherit the user/tenant model per ADR-0112); the OS side only manages.
    # Reuse `_del_enabled` computed at the pre-spawn gate above — NOT a fresh
    # read — so a tenant.corvin.yaml mtime flip during the CCC await cannot
    # diverge the gate's compliance row from this decision (round-4 review).
    _del_heuristic = _should_delegate(prompt)
    # Layer 5 repair throttle (acs_error_rate anomaly) was already resolved into
    # `_del_throttled` above, where it also fed the pre-spawn gate's engine
    # classification (ACS-3). Reuse that single value so the gate and the real
    # decision cannot diverge.
    # `_force_direct` (explicit `/use-engine claude_code`) hard-suppresses
    # delegation — it is mutually exclusive with `_force_delegate`
    # (`/use-engine acs`), so the two flags never conflict (HIGH-1).
    _del_will_delegate = (_del_enabled and not _del_throttled and not _force_direct
                          and (_force_delegate or _del_heuristic))
    _dbg(sess.workdir, "delegation.decision",
         delegation_enabled=_del_enabled,
         force_delegate=_force_delegate,
         heuristic_match=_del_heuristic,
         repair_throttled=_del_throttled,
         will_delegate=_del_will_delegate,
         prompt_len=len(prompt),
    )
    if _del_will_delegate:
        # ── ADR-0217 — TDE-first delegation (maintainer decision 2026-07-24) ──
        # Within the delegated branch TDE is now the DEFAULT engine; ACS runs
        # only for the explicit /delegate override, big-data-shaped tasks, or
        # when TDE is unavailable / the shared pool is exhausted (peek) — the
        # ACS branch below then owns the hardened ADR-0201 degrade ladder.
        # The pre-spawn gate above already classified this turn as delegation
        # (DELEGATION_ENGINE_ID covers both engines' spawn class).
        _tde_target = _delegation_engine_target(
            prompt,
            force_delegate=_force_delegate,
            tde_available=_tde_available(),
            quota_ok=_tde_quota_peek_ok(),
        )
        _dbg(sess.workdir, "delegation.engine_choice",
             target=_tde_target,
             force_delegate=_force_delegate,
             big_data=_is_big_data_task(prompt))
        if _tde_target == "tde":
            async for _ev in _stream_tde_turn(
                sess, _task_text, tm, task_id,
                os_audit=_os_audit, audit_emit=_audit_emit,
                emit_completed=_os_emit_completed,
                os_model=_os_model, resume=resume,
            ):
                yield _ev
            return
        task_text = _task_text
        _acs = None
        # Fallback flag: any "no ACS run possible" condition — runtime import
        # failure, empty task, an un-creatable run dir (the Windows ':' path bug),
        # or an exhausted ACS quota below — routes this turn to the NORMAL Claude
        # Code OS-turn instead of failing it. The normal path does its own
        # Task-tool delegation, so the user still gets delegated work, just not
        # via the ACS fan-out. (User requirement: robust — no ACS → normal.)
        _quota_fallback = False
        try:
            # Ensure operator/bridges/shared is in path for spawn_gates and other deps
            # Path: core/console/corvin_console/chat_runtime.py → CorvinOS/operator/bridges/shared
            _bridge_shared = Path(__file__).resolve().parents[3] / "operator" / "bridges" / "shared"
            if str(_bridge_shared) not in sys.path:
                sys.path.insert(0, str(_bridge_shared))
            import acs_runtime as _acs  # type: ignore  # noqa: PLC0415
        except Exception as _import_err:  # noqa: BLE001
            _log.warning("[delegation] ACS runtime import failed (%s) — falling back to "
                         "normal Claude Code turn", _import_err)
            _acs = None
        if _acs is None or not task_text:
            reason = "empty task" if not task_text else "ACS runtime unavailable"
            _dbg(sess.workdir, "delegation.fallback_to_normal", reason=reason)
            # Do NOT fail the turn — skip the ACS fan-out and let the normal
            # Claude Code OS-turn below handle the prompt.
            _quota_fallback = True

        # ADR-0150 LIC-WEBCHAT-DELEGATE-COMPUTE-01: this branch fans out to ACS
        # workers (1 manager + up to 4 worker `claude -p`) but constructs ACSRuntime
        # DIRECTLY, bypassing run_acs_workflow's compute charge. Charge
        # compute_units_per_day HERE (orthogonal to the route's enforce_chat_turns,
        # which only covers the single OS-turn framing). Fail-CLOSED: a missing
        # license module or an over-quota both deny the fan-out before any spawn.
        # All ACS-specific setup is skipped when we already fell back above.
        _cq_inc = _CQErr = _cq_home = None
        if not _quota_fallback:
            try:
                from license.compute_quota import increment_and_check as _cq_inc  # type: ignore  # noqa: PLC0415
                from license.limits import LicenseLimitError as _CQErr  # type: ignore  # noqa: PLC0415
            except ImportError:
                # ADR-0215 adversarial review (2026-07-24): this early return
                # used to skip touch()/_append_turn() entirely — since the
                # user's prompt was already persisted earlier in this turn
                # (_append_turn for the user message runs before this
                # branch), that left an orphaned user turn with no assistant
                # reply in history and a stale sess.last_active_at. Mirrors
                # the bookkeeping the _gate_refusal branch above already
                # does for the same class of system-level (not user-input)
                # rejection.
                _cq_msg = "compute quota enforcement unavailable (fail-closed)"
                _os_audit("os_turn.started", {"model": _os_model_used})
                tm.record_event(task_id, {
                    "event": "task.failed", "exit_code": 1,
                    "error": "compute_quota module unavailable",
                })
                _audit_emit(sess, "web.turn.completed", rc=1,
                            result_chars=len(_cq_msg), usage=None,
                            reason="compute_quota_unavailable")
                _os_emit_completed(rc=1)
                yield {"type": "error", "code": 402, "message": _cq_msg}
                yield {"type": "result", "text": _cq_msg, "usage": None}
                touch(sess, increment_turn=True)
                _append_turn(sess, "assistant", [{"kind": "text", "text": _cq_msg}])
                yield {"type": "done"}
                return
            # Use the CANONICAL resolver (CORVIN_HOME → service.env pin → repo marker
            # → ~/.corvin), NOT a hand-rolled env-or-~/.corvin: the deny-by-default
            # compute gate (ADR-0094) writes the counter via forge.paths.corvin_home()
            # in _compute_license_gate / mcp_server. A direct-env resolver returns
            # ~/.corvin inside a repo where canonical returns <repo>/.corvin → the
            # reader (this counter) and the writer diverge → quota silently miscounted.
            _cq_home = _forge_paths.corvin_home()
            # Robustness pre-flight: the ACS run writes its tree under
            # sess.workdir/acs/runs. If that can't be created (filesystem /
            # permission — including the Windows ':' chat_key path, now fixed at
            # source in acs_runtime._run_dir), fall back to the normal Claude Code
            # OS-turn instead of failing the whole turn.
            try:
                (sess.workdir / "acs" / "runs").mkdir(parents=True, exist_ok=True)
            except OSError as _pf_err:
                _quota_fallback = True
                _log.warning("[delegation] ACS run tree not creatable (%s) — falling back "
                             "to normal Claude Code turn", _pf_err)
                _dbg(sess.workdir, "delegation.fallback_to_normal",
                     reason=f"acs_dir_uncreatable:{type(_pf_err).__name__}")
                yield {"type": "notice", "subtype": "acs_fallback",
                       "message": "ACS-Run nicht möglich — der Task läuft direkt über "
                                  "Claude Code.\n\n"}
        _fb_quota_exceeded = False
        try:
            if not _quota_fallback:
                _cq_inc(_cq_home, channel="web-chat-acs", chat_key=f"web:{sess.tenant_id}:{sess.sid}")
        except _CQErr:  # type: ignore[misc]
            _quota_fallback = True
            _fb_quota_exceeded = True
        except Exception:  # noqa: BLE001 — operational error swallowed by increment_and_check
            pass

        if _quota_fallback:
            # L34/L35 fix: re-gate with the ACTUAL fallback engine on EVERY
            # fallback branch (adversarial review D2 — previously only the
            # quota-exhausted branch re-gated; the "ACS runtime unavailable",
            # "empty task" and "acs dir uncreatable" branches flipped to the
            # direct engine ungated). The initial gate (above) was called with
            # engine_id="acs"; after ANY fallback the real engine is _os_engine
            # (claude_code / hermes / …). Without this second check,
            # CONFIDENTIAL data could bypass residency policy because the gate
            # never evaluated the engine that will actually spawn. Fail-closed:
            # a refusal ends the turn. The gate runs BEFORE the quota notice so
            # a blocked turn never announces a fallback it will not perform.
            _fb_gate = _spawn_gates.check_console_spawn_or_refusal(
                _task_text, tenant_id=sess.tenant_id, persona="assistant",
                channel=CHANNEL, chat_key=sess.chat_key,
                engine_id=_os_engine,
            )
            if _fb_gate is not None:
                _os_audit("os_turn.started", {"model": _os_model_used})
                tm.record_event(task_id, {
                    "event": "task.failed", "exit_code": 1,
                    "error": "fallback engine blocked by pre-spawn gate",
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
            if _fb_quota_exceeded:
                # ADR-0216/0217: the pool is compute_units_per_day (default 10
                # on free tier), SHARED across TDE + ACS + compute runs — not
                # "1 Delegation-Run/Tag". Since ADR-0217 this branch is the
                # central degrade target for ALL delegated traffic, so the text
                # must name the real pool semantics (2026-07-24 review).
                _quota_notice = (
                    "Dein tägliches Agentic-Compute-Kontingent ist ausgeschöpft "
                    "(geteilter Pool für TDE-, ACS- und Compute-Runs im Free-Tier). "
                    "Der Task wird über Claude Code ausgeführt — ohne parallele Worker.\n"
                    "Für ein höheres Kontingent: [Member-Upgrade](https://corvin-labs.com/pricing)\n\n"
                )
                yield {"type": "notice", "subtype": "quota_fallback",
                       "message": _quota_notice}
                yield {"type": "delta", "text": _quota_notice}

        if not _quota_fallback:
            run_id = f"acs-web-{int(time.time())}-{secrets.token_hex(3)}"
            run_dir = sess.workdir / "acs" / "runs" / run_id
            spec_dict = _build_delegation_spec(task_text, _delegation_budget(sess.tenant_id))
            _dbg(sess.workdir, "acs.run.start",
                 run_id=run_id, task_len=len(task_text),
                 # No task_preview — see the matching comment on turn.start
                 # above (adversarial review finding, PII in debug log).
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
            # Per-turn agentic-compute badge (frontend takes the LAST engine
            # event of a turn — fallback paths later re-stamp claude/hermes).
            yield {"type": "engine", "engine": "acs",
                   "label": "ACS (Agentic Compute Fan-out)"}
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
                        "path": _relpath.as_posix(), "mime": _mime, "size": _sz,
                    }
                    if _live_label:
                        _evt["label"] = _live_label
                    else:
                        _evt["label"] = "live"
                    results.append(_evt)
                    # Persist alongside the text turn so the artifact survives reload.
                    _persist: dict[str, Any] = {
                        "kind": "artifact", "name": _fp.name,
                        "path": _relpath.as_posix(), "mime": _mime, "size": _sz,
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
            # Reaching a budget is a BOUNDED STOP, not a failure — it gets its
            # own outcome end-to-end (chat text, audit rc, task event, artifact
            # scan) so the chat cannot say "not an error" while the activity
            # views record a crash.
            bounded_stop = res.status == "budget_exhausted"
            # Status messages follow the language of the user's own prompt —
            # the final result text is also SPOKEN by the voice pipeline, and a
            # hard-German message switched the voice language mid-session for
            # English users (review 2026-07-17).
            _msg_de = _prompt_is_german(prompt)
            final = (res.summary or "").strip()

            # Safety net: raw HTML error pages (Cloudflare 50x, nginx, …) that
            # slip through the ACS layer must never appear verbatim in the chat.
            if final and final.lstrip().startswith(("<!DOCTYPE", "<!doctype", "<html", "<HTML")):
                import re as _re_html
                _t = _re_html.search(r"<title[^>]*>([^<]{1,120})</title>",
                                     final, _re_html.IGNORECASE)
                _label = _t.group(1).strip() if _t else ("HTTP-Fehlerseite" if _msg_de
                                                         else "HTTP error page")
                final = (f"Fehler: Der Server hat \"{_label}\" zurückgegeben. Bitte versuche es erneut."
                         if _msg_de else
                         f"Error: the server returned \"{_label}\". Please try again.")
                ok = False

            if bounded_stop:
                # It used to fall through to "Delegation fehlgeschlagen: ACS
                # workflow failed with status 'budget_exhausted' (N iteration(s))",
                # which reads as a crash to someone who has never seen these
                # numbers. ACS already reports WHICH limit was met
                # (budget_breach); say so, and say where to change it. Placed
                # OUTSIDE the `if not final:` fallback so a future ACS summary
                # on budget stops cannot silently hide the explanation — the
                # note is appended to whatever summary exists.
                _stop_note = _budget_stop_message(
                    getattr(res, "budget_breach", "") or "",
                    getattr(res, "iterations", None),
                    getattr(res, "workers_spawned", None),
                    german=_msg_de,
                )
                final = f"{final}\n\n{_stop_note}" if final else _stop_note
            elif not final:
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

                if ok:
                    final = "Delegation abgeschlossen." if _msg_de else "Delegation completed."
                else:
                    final = ((f"Delegation fehlgeschlagen: {error_msg[:250]}") if _msg_de
                             else f"Delegation failed: {error_msg[:250]}")
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
            # bounded_stop included: "the partial results above remain valid"
            # (the budget-stop message) was a lie while this gate was ok-only —
            # files finished in the last poll window need TWO stable sightings
            # to be live-emitted, so an abrupt budget stop routinely left the
            # freshest artifacts undelivered and unpersisted.
            if (ok or bounded_stop) and _scan_root.is_dir():
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
                        "path": _rel.as_posix(),
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

            # bounded_stop records rc=0 + task.completed: the chat tells the
            # user "this is not an error", so the audit trail and activity
            # views must not contradict it with a failed-run record.
            _audit_emit(sess, "web.turn.completed", rc=0 if (ok or bounded_stop) else 1,
                        result_chars=len(final), usage=None, delegated_run_id=run_id,
                        artifacts=len(_live_artifact_parts) + len(_acs_artifact_parts))
            if ok:
                tm.record_event(task_id, {
                    "event": "task.completed", "exit_code": 0,
                    "summary": f"delegated to ACS run {run_id}: {len(final)} chars output",
                })
            elif bounded_stop:
                tm.record_event(task_id, {
                    "event": "task.completed", "exit_code": 0,
                    "summary": (f"delegated to ACS run {run_id}: bounded stop "
                                f"({getattr(res, 'budget_breach', '') or 'budget reached'})"),
                })
            else:
                tm.record_event(task_id, {"event": "task.failed", "exit_code": 1})

            # ADR-0213 — write the ACS result into the REAL claude CLI
            # transcript before advancing turn_count. This branch never
            # calls `claude -p` for the OS role (only ACSRuntime's own
            # manager/worker subprocesses), so without this the next
            # `--continue` turn would resume a transcript that never saw
            # this delegation — see ADR-0213 for the full root cause.
            _sync_holder = _ContextSyncProcHolder()
            try:
                _sync_ok = await asyncio.to_thread(
                    _sync_acs_result_to_transcript, sess, res, run_id,
                    task_text, model=_os_model, resume=resume,
                    proc_holder=_sync_holder,
                )
            except (asyncio.CancelledError, GeneratorExit):
                # Client gone mid-sync — kill the subprocess synchronously
                # (asyncio.to_thread cannot interrupt the blocking thread
                # itself) before propagating, same bug class the ACS
                # worker/manager calls already fixed via _WorkerProcessHolder.
                _sync_holder.kill()
                raise
            except Exception:  # noqa: BLE001 — best-effort; C1 fallback below covers it
                _sync_ok = False
            _os_audit("os_turn.context_sync", {
                "delegated_run_id": run_id, "synced": _sync_ok,
            })
            _os_emit_completed(0 if (ok or bounded_stop) else 1)
            # C1 fallback (ADR-0213): only advance turn_count when the
            # transcript actually advanced — otherwise the next turn's
            # `resume = turn_count > 0` would append --continue onto a
            # transcript that never recorded this turn, reproducing the
            # original bug (turn-count increment with no transcript write)
            # in the failure path.
            touch(sess, increment_turn=_sync_ok)
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
        yield {"type": "engine", "engine": "hermes", "label": "Hermes (lokal)"}
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
    _spawn_kwargs: dict[str, Any] = dict(
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(sess.workdir),
        env=_spawn_env,
        limit=8 * 1024 * 1024,  # 8 MB — default 64 KB is too small for large tool results
    )
    # Windows .cmd/.bat shim: spawn through cmd.exe with _win_shim's
    # cmd.exe-safe quoting (doubled inner quotes) instead of letting
    # create_subprocess_exec's list2cmdline hand untrusted argv to cmd's
    # re-parser. POSIX and direct .exe launches keep create_subprocess_exec
    # byte-for-byte (Linux/macOS behaviour unchanged).
    _is_win_cmd_shim = (
        sys.platform == "win32" and args
        and isinstance(args[0], str)
        and args[0].lower().endswith((".cmd", ".bat"))
    )
    try:
        if _is_win_cmd_shim:
            from agents._win_shim import cmd_quote  # noqa: PLC0415
            _cmd_line = " ".join(cmd_quote(a) for a in args)
            proc = await asyncio.create_subprocess_shell(_cmd_line, **_spawn_kwargs)
        else:
            proc = await asyncio.create_subprocess_exec(*args, **_spawn_kwargs)
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
    yield {"type": "engine", "engine": _os_engine or "claude_code",
           "label": "Claude Code (OS-Engine)"}

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
    # Set at the result event; must exist even if the turn produces none (error,
    # kill, no output), because the final-result emit below reads it.
    _ann_pending: bool = False
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
                # annotation_pending tells the client "a second, final result
                # event is coming — render this text but do NOT speak it yet".
                # Without it the client spoke both events and paid for two full
                # server-side syntheses per annotated turn. Whenever this is
                # True a final result event is GUARANTEED below, even if the
                # annotation comes back empty — otherwise the turn would never
                # be spoken at all.
                _ann_pending = bool(result_text.strip()) and _annotation_enabled()
                yield {
                    "type":   "result",
                    "text":   result_text,
                    "usage":  last_usage,
                    "annotation_pending": _ann_pending,
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
    # Gated on _ann_pending (same rationale as the Hermes path): a suffix the
    # client was never told to wait for would be persisted + voice_key'd but
    # never spoken, orphaning the turn's archived audio on a mid-turn toggle.
    _ann_suffix = ""
    if rc == 0 and result_text and _ann_pending:
        _ann_suffix = await _compute_web_annotation_suffix(result_text, sess.tenant_id)
    if _ann_suffix:
        yield {"type": "delta", "text": "\n\n" + _ann_suffix}
    # Emit the FINAL result whenever the first one was flagged annotation_pending
    # — including when the annotation came back empty (LLM skipped it, budget
    # spent, both backends down). The client is holding its voice waiting for
    # exactly this event; skipping it on the empty path would leave the turn
    # permanently unspoken.
    if _ann_pending:
        yield {"type": "result",
               "text": (result_text + "\n\n" + _ann_suffix) if _ann_suffix else result_text,
               "usage": last_usage,
               "annotation_pending": False}

    touch(sess, increment_turn=True)

    # Persist the assistant turn (combined text-delta-runs + any
    # tool-use cards). The frontend's `<MessageBubble>` consumes the
    # same shape on rehydrate.
    combined_text = "".join(final_text_parts).strip()
    if _ann_suffix:
        combined_text = (combined_text + "\n\n" + _ann_suffix).strip()
    # ADR-0194 Phase 1: the exact string the client will hand to /voice/tts —
    # i.e. the text of the LAST `result` event yielded above. It is NOT
    # combined_text: `result_text` is the CLI's final assistant message, while
    # combined_text concatenates EVERY assistant text block of the turn. On a
    # tool-using turn ("Ich schaue nach." -> tool_use -> "Die Datei enthält X.")
    # the two diverge, so the writer (/voice/tts, hashing what it was sent) and
    # the reader (rehydrate, hashing the persisted turn) landed on different
    # voice_keys and the archived audio was orphaned — no player, ever. In an
    # agentic console tool-using turns are the COMMON case, so the Phase-1
    # archive silently didn't rehydrate for most turns (reproduced against the
    # live archive: every single-block turn had a player, the one browser-tool
    # turn had none). Pinning the key at persist time — where the server holds
    # BOTH strings — removes the guess entirely instead of trying to keep two
    # derivations byte-identical forever.
    _spoken_text = (result_text + "\n\n" + _ann_suffix) if _ann_suffix else result_text
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
                # .as_posix(), not str() — the artifact-generating tool most
                # commonly nests one level deep (e.g. imagegen's outputs/,
                # ACS's acs/runs/<id>/output/), and str(Path) renders with the
                # OS-NATIVE separator. On a Windows-hosted console that embeds
                # a literal backslash in this JSON "path", which the frontend
                # (chat.tsx splits on "/") and the serving route's
                # _SAFE_SUBPATH regex (forward-slash only) both then reject —
                # the artifact card renders but its <img>/download URL 404s,
                # with no user-visible error beyond a broken-image icon.
                rel = fpath.relative_to(sess.workdir)
                artifact_event = {
                    "type": "artifact",
                    "name": fpath.name,
                    "path": rel.as_posix(),
                    "mime": mime,
                    "size": fpath.stat().st_size,
                }
                _artifact_parts_buf.append({
                    "kind": "artifact",
                    "name": fpath.name,
                    "path": rel.as_posix(),
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
    _append_turn(sess, "assistant", parts_persisted,
                 voice_key_hint=voice_key(_spoken_text) if _spoken_text.strip() else None)

    yield {"type": "done"}
