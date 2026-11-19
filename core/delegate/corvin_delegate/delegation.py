"""Delegation core — wraps the WorkerEngine layer behind a single call.

Public API:

    result = run_delegate(
        engine="codex_cli" | "opencode" | "claude_code",
        prompt="...",
        model=None,                 # optional, engine-specific (e.g. "anthropic/claude-sonnet-5")
        budget_s=60,                # int, clamped to [BUDGET_MIN_S, BUDGET_MAX_S]
        working_dir=None,           # optional path; engine cwd
        env_extra=None,             # optional dict[str, str]; merged into spawn env
        engine_factory=None,        # optional callable for tests
        audit=True,                 # emit delegate.* audit events (default on)
        persona=None,               # optional caller-persona tag for audit
    )
    if result.ok:
        text = result.final_text
    else:
        text = result.error

The engine factory indirection (default = ``_default_engine_factory``)
exists so tests can pass in a fake engine without monkey-patching
import paths. Production callers should leave it None.

Audit events emitted (when ``audit=True``):
    delegate.invoked   — engine, persona, prompt_chars, budget_s, model
    delegate.completed — engine, persona, duration_ms, output_chars
    delegate.failed    — engine, persona, reason, duration_ms

NEVER the prompt text, NEVER the output text. Metadata only, per the
L23 / L25 / L28 precedent. The audit emitter in ``audit.py`` validates
the details payload against a per-event allow-list at write time.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
import re
import secrets
import shutil
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterator

_log = logging.getLogger("corvin_delegate")

# The WorkerEngine layer lives in corvin_operator/bridges/shared/agents/. We
# resolve it lazily so this package stays importable even when that tree
# is absent (e.g. CI runs that only need the delegation library or its
# MCP-server surface).
_REPO_ROOT = Path(__file__).resolve().parents[3]
_AGENTS_DIR = _REPO_ROOT / "corvin_operator" / "bridges" / "shared"


BUDGET_DEFAULT_S = 60
BUDGET_MIN_S = 10
# The normal caller-requestable maximum stays 600 s (10 min). Adversarial
# review F7: raising this global to 86400 (as ADR-0201 first did) let EVERY
# run_delegate caller — including the corvin_delegate MCP `delegate_*` tools,
# whose budget_s comes straight from (possibly prompt-injected) LLM tool args —
# hold an un-metered engine subprocess for 24 h. A 10-min interactive cap is
# right for those; only the ACS quota-fallback legitimately needs longer, and
# it now passes an explicit higher `budget_ceiling_s` (see BUDGET_FALLBACK_MAX_S).
BUDGET_MAX_S = 600
# The elevated ceiling the ACS daily-quota fallback may request: it runs a whole
# workflow goal as ONE delegate turn, so a 10-min cap aborts exactly the long
# tasks the fallback exists to save. Matches the ACS per-worker ceiling
# (_WORKER_TIMEOUT_CEILING) and routes/settings.py::_BUDGET_KEYS. Reachable ONLY
# via run_delegate(budget_ceiling_s=...), never from the MCP tool surface.
BUDGET_FALLBACK_MAX_S = 86400

PROMPT_MAX_CHARS = 64_000  # rejected hard above; protects engines from runaway input
MODEL_MAX_CHARS = 256

# Layer 29.1b — output size cap. Worker `final_text` longer than this is
# truncated and `output_truncated=True` is set in the result. Protects
# against runaway workers dumping env / context into the reply.
OUTPUT_CAP_DEFAULT_CHARS = 65_536
OUTPUT_CAP_MIN_CHARS = 1_024
OUTPUT_CAP_MAX_CHARS = 524_288  # 512 KB hard ceiling
OUTPUT_TRUNCATED_MARKER = "\n\n[…output truncated by corvin-delegate at {n} chars…]"

# Layer 29.1c — prompt-injection marker scan on worker output. When ANY
# marker matches, the framing-block in mcp_server.py wraps the worker's
# text so Claude OS treats it as ambient data, not as a directive.
#
# KNOWN, DOCUMENTED LIMITATION (adversarial review finding): this is a
# structural, keyword-anchored scan — it does not and cannot catch every
# injection phrasing (non-English wording beyond the German patterns added
# below, unicode homoglyphs, zero-width-char-split keywords, HTML-comment-
# hidden text, base64-encoded directives a downstream LLM might still
# decode-and-follow). False positives are acceptable (the framing-block
# downstream is non-disruptive); false negatives are the residual risk this
# scan reduces but does not eliminate. The patterns map to the well-known
# attack families documented in OWASP LLM01.
_INJECTION_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    # "ignore" / "disregard" — allow up to 3 filler words between the verb
    # and the target (covers "ignore the previous", "disregard all earlier").
    ("ignore_previous",      re.compile(r"\bignore\b(?:\W+\w+){0,3}\W+(?:previous|prior|earlier|above|instructions|rules|commands|directives|everything)\b", re.IGNORECASE)),
    ("disregard",            re.compile(r"\bdisregard\b(?:\W+\w+){0,3}\W+(?:previous|prior|earlier|above|instructions|rules|commands|directives|everything)\b", re.IGNORECASE)),
    ("system_tag_inject",    re.compile(r"<\s*/?\s*(?:SYSTEM|sys|system_prompt)\s*>", re.IGNORECASE)),
    ("html_comment_hidden",  re.compile(r"<!--.*?(?:ignore|instructions?|system|forget).*?-->", re.IGNORECASE | re.DOTALL)),
    ("role_switch",          re.compile(r"^\s*(?:assistant|user|system)\s*:", re.IGNORECASE | re.MULTILINE)),
    ("new_instructions",     re.compile(r"\b(?:new|updated|revised|real|actual)\s+instructions?\s*(?:is|are|now)?\s*:", re.IGNORECASE)),
    ("forget_everything",    re.compile(r"\bforget\b(?:\W+\w+){0,3}\W+(?:everything|all|previous|prior|instructions)\b", re.IGNORECASE)),
    ("indirect_authority",   re.compile(r"\b(?:the\s+user'?s?|your)\s+(?:real|actual|true)\s+instructions?\s+(?:is|are|now)\b", re.IGNORECASE)),
    # German equivalents of the two most common English patterns above —
    # the injection-scan literature notes non-English phrasing is one of
    # the most common evasions of English-only keyword scans.
    ("ignore_previous_de",   re.compile(r"\bignorier(?:e|en)\b(?:\W+\w+){0,3}\W+(?:vorherige[nr]?|vorig[e]?|obige[nr]?|bisherige[nr]?|anweisungen|regeln|befehle|alles)\b", re.IGNORECASE)),
    ("forget_everything_de", re.compile(r"\bvergiss\b(?:\W+\w+){0,3}\W+(?:alles|alle|vorherige[nr]?|anweisungen)\b", re.IGNORECASE)),
)
# Scan the FULL (already output-cap-bounded, max 512 KB) text rather than
# only a head slice. The head-only scan was a trivial evasion: placing the
# injection payload after the cutoff (well within the 64 KB default output
# cap) reached Claude OS byte-identically, unframed (adversarial review
# finding). A regex pass over up to 512 KB is still sub-millisecond-to-low-
# millisecond — the original "cost" rationale does not hold at this size.
_INJECTION_SCAN_HEAD_CHARS = OUTPUT_CAP_MAX_CHARS

# ADR-2091: ``hermes`` (local Ollama) was removed. A legacy id passed in is
# mapped to ``claude_code`` by ``_normalize_legacy_engine_id`` below.
AVAILABLE_ENGINES: tuple[str, ...] = ("claude_code", "codex_cli", "opencode", "copilot")


def _normalize_legacy_engine_id(engine_id: Any) -> Any:
    """ADR-2091: map a removed engine id (``hermes``, ``ollama`` ...) to
    ``claude_code``. SSOT is ``engine_registry.normalize_legacy_engine_id``
    in ``corvin_operator/bridges/shared``; this wrapper only puts that
    directory on ``sys.path`` first. Non-string values pass through unchanged
    so the caller-side validation below still rejects them."""
    if not isinstance(engine_id, str):
        return engine_id
    _ensure_agents_on_path()
    from engine_registry import normalize_legacy_engine_id  # type: ignore
    return normalize_legacy_engine_id(engine_id)


class DelegateError(Exception):
    """Raised for caller-side validation errors (bad engine, oversized prompt).

    Engine-side failures (binary missing, stream timeout, non-zero exit)
    are NOT raised — they land on ``DelegateResult.error`` with ``ok=False``
    so the OS-turn caller can format a graceful reply.
    """


@dataclass
class DelegateResult:
    ok: bool
    engine: str
    final_text: str = ""
    duration_ms: int = 0
    usage: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    model: str | None = None
    # Layer 29.1 hardening — surfaced to the MCP-server layer so it can
    # wrap the text content with a framing block when needed.
    output_truncated: bool = False
    output_total_chars: int = 0           # length of final_text BEFORE truncation
    injection_markers: list[str] = field(default_factory=list)
    allow_write: bool = False             # echo of the caller's opt-in
    # Layer 29.3a — faithfulness judge surface.
    output_judge_mode: str = "off"        # resolved mode (after max-strictness)
    output_judge_verdict: str = "skipped" # skipped | faithful | corrected | judge_error
    output_judge_notes: str | None = None
    output_judge_latency_ms: int = 0
    output_judge_replaced: bool = False   # True when enforcing mode used corrected text


# ---------------------------------------------------------------------------
# Engine factory
# ---------------------------------------------------------------------------


def _ensure_agents_on_path() -> None:
    """Add the agents-package parent to sys.path on first use."""
    p = str(_AGENTS_DIR)
    if p not in sys.path:
        sys.path.insert(0, p)


def _default_engine_factory(engine_id: str):
    """Build a concrete WorkerEngine instance for an engine_id.

    Imported lazily so a missing voice plugin doesn't break module-load.
    Returns the engine instance, or raises DelegateError for unknown ids.
    """
    if engine_id not in AVAILABLE_ENGINES:
        raise DelegateError(
            f"unknown engine: {engine_id!r}; expected one of {AVAILABLE_ENGINES}"
        )

    _ensure_agents_on_path()

    if engine_id == "claude_code":
        from agents.claude_code import ClaudeCodeEngine  # type: ignore
        return ClaudeCodeEngine()
    if engine_id == "codex_cli":
        from agents.codex_cli import CodexCliEngine  # type: ignore
        return CodexCliEngine()
    if engine_id == "opencode":
        from agents.opencode_cli import OpenCodeEngine  # type: ignore
        return OpenCodeEngine()
    if engine_id == "copilot":
        from agents.copilot_cli import CopilotCliEngine  # type: ignore
        return CopilotCliEngine()
    # Defence-in-depth — unreachable, but explicit.
    raise DelegateError(f"engine_id passed validation but no factory: {engine_id!r}")


# ---------------------------------------------------------------------------
# Layer 29.1a — engine-safe-defaults (per-engine spawn kwargs)
# ---------------------------------------------------------------------------


def _safe_spawn_kwargs(engine_id: str, allow_write: bool) -> dict[str, Any]:
    """Per-engine kwargs that lock down the worker subprocess.

    Defaults are RESTRICTIVE — workers cannot modify the filesystem
    unless the caller explicitly sets ``allow_write=True``. The
    delegation contract is "the OS-turn already has full Claude
    permissions; the worker is a sub-task that should not need them."
    Read access is permitted (so the worker can inspect files passed
    via ``working_dir``); write / execute is gated.

    Per engine:
      * ``claude_code`` — explicit ``permission_mode="default"`` AND
        ``dangerously_skip_permissions=False`` so the engine NEVER
        appends ``--dangerously-skip-permissions``. The default
        ``ClaudeCodeEngine`` would otherwise pick that flag when both
        kwargs are None.
      * ``opencode`` — ``permission_mode="plan"`` routes to
        ``--agent plan`` which is OpenCode's read-only-equivalent.
        Without this, OpenCode's engine module defaults to
        ``--dangerously-skip-permissions`` (see opencode_cli.py:115).
      * ``codex_cli`` — engine default is already
        ``--sandbox read-only``. Nothing to override on the safe path.

    ``allow_write=True`` widens to the legacy unrestricted mode:
      * Claude Code → permission_mode="bypassPermissions"
      * OpenCode    → permission_mode="bypassPermissions"
      * Codex       → extra_args adds ``--sandbox workspace-write``

    Operators / personas can opt in per delegation when a write-
    capable worker is genuinely needed (e.g. a Claude-Worker that
    runs a refactor and writes the patch to disk). Default OFF.
    """
    if engine_id == "claude_code":
        if allow_write:
            return {
                "permission_mode": "bypassPermissions",
                "dangerously_skip_permissions": True,
            }
        return {
            "permission_mode": "default",
            "dangerously_skip_permissions": False,
        }
    if engine_id == "opencode":
        if allow_write:
            return {"permission_mode": "bypassPermissions"}
        return {"permission_mode": "plan"}
    if engine_id == "codex_cli":
        if allow_write:
            # codex_cli.py resolves --sandbox from `permission_mode` FIRST
            # (defaulting to "read-only" whenever permission_mode is None),
            # then strips any --sandbox pair already present in `extra_args`
            # so its own resolution "wins" — so passing the widened sandbox
            # only via extra_args (the previous form here) was silently
            # discarded, and allow_write=True was a complete no-op for this
            # engine: it stayed read-only regardless of caller intent
            # (adversarial review finding). "acceptEdits" is the
            # permission_mode _SANDBOX_MAP key that maps to workspace-write.
            return {"permission_mode": "acceptEdits"}
        return {}
    if engine_id == "copilot":
        # CopilotCliEngine spawns `copilot -p` — no filesystem permission
        # modes exposed by the binary. allow_write is a no-op.
        return {}
    return {}


# ---------------------------------------------------------------------------
# Layer 29.1b — output cap (truncate runaway worker output)
# ---------------------------------------------------------------------------


def _clamp_output_cap(cap: Any) -> int:
    """Clamp the operator-provided output cap to a sane range."""
    try:
        c = int(cap)
    except (TypeError, ValueError):
        c = OUTPUT_CAP_DEFAULT_CHARS
    if c < OUTPUT_CAP_MIN_CHARS:
        return OUTPUT_CAP_MIN_CHARS
    if c > OUTPUT_CAP_MAX_CHARS:
        return OUTPUT_CAP_MAX_CHARS
    return c


def _apply_output_cap(text: str, cap_chars: int) -> tuple[str, bool, int]:
    """Truncate ``text`` to ``cap_chars`` if oversized.

    Returns ``(maybe_truncated_text, was_truncated, original_length)``.
    """
    if not isinstance(text, str):
        text = "" if text is None else str(text)
    total = len(text)
    if total <= cap_chars:
        return text, False, total
    marker = OUTPUT_TRUNCATED_MARKER.format(n=cap_chars)
    head = text[:cap_chars]
    return head + marker, True, total


# ---------------------------------------------------------------------------
# Layer 29.1c — prompt-injection marker scan
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Layer 29.2a — hermetic working_dir
# ---------------------------------------------------------------------------


# Minimum env keys every worker subprocess needs to function (PATH for
# binary resolution, HOME for ~ expansion, USER for tooling that reads
# whoami, LANG/LC_* for utf-8, TERM for stdout sniffing, TMP* for /tmp).
#
# Layer 30 (ADR-0022) adds three CORVIN_DELEGATE_* env-floor vars and
# CORVIN_HOME / CORVIN_TENANT_ID / CORVIN_CALLER_PERSONA / CORVIN_CHANNEL_ID
# so the per-spawn forge / skill_forge MCP-server children can resolve
# the right tenant tree + persona attribution. Without these the
# scrubbed env would force the forge MCP-server to fall back to
# user-scope and lose chat-binding.
_BASE_ENV_ALLOWLIST: frozenset[str] = frozenset({
    "PATH", "HOME", "USER", "LOGNAME", "SHELL",
    "LANG", "LC_ALL", "LC_CTYPE", "LC_MESSAGES",
    "TERM", "TMPDIR", "TEMP", "TMP",
    # Layer 29.2b — already used by the OS-side; needed for forge
    # tenant resolution from inside the worker.
    "CORVIN_HOME",
    "CORVIN_TENANT_ID",
    "CORVIN_CALLER_PERSONA",
    "CORVIN_CHANNEL_ID",
    # Layer 30 — Forge / SkillForge env-floor (ADR-0022).
    "CORVIN_DELEGATE_INJECT_SKILLS",
    "CORVIN_DELEGATE_INJECT_SKILLS_UNGRADED",
    "CORVIN_DELEGATE_MAX_SKILLS",
    "CORVIN_DELEGATE_FORGE_ENABLED",
    "CORVIN_DELEGATE_SKILL_FORGE_ENABLED",
    # ADR-0049 — session-pinning env-floor + session directory hint.
    "CORVIN_DELEGATE_WORKER_SESSION_PINNED",
    "CORVIN_SESSION_DIR",
})


# Per-engine API-key allowlist on top of the base. Conservative —
# operators that need additional vars (e.g. opencode targeting a custom
# provider) pass them via the ``env_extra`` parameter.
_ENGINE_ENV_ADDITIONS: dict[str, frozenset[str]] = {
    "claude_code": frozenset({"ANTHROPIC_API_KEY"}),
    "codex_cli":   frozenset({"OPENAI_API_KEY"}),
    # OpenCode is provider-agnostic. The three keys cover the
    # common providers (Ollama Cloud — a hosted API, Anthropic, OpenAI). Custom
    # providers (e.g. OpenRouter) pass their key via env_extra.
    "opencode":    frozenset({
        "OLLAMA_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY",
    }),
    # CopilotCliEngine reads auth from ~/.copilot/config.json. GH_TOKEN /
    # GITHUB_TOKEN / GH_HOST / GH_ENTERPRISE_TOKEN are honoured as env
    # fallbacks — injected via L16 vault→bwrap, never as CLI args.
    "copilot":     frozenset({
        "GH_TOKEN",
        "GH_ENTERPRISE_TOKEN",
        "GITHUB_TOKEN",
        "GH_HOST",
        "COPILOT_AGENT_ROOT",
    }),
}


_OPENCODE_PROVIDER_ENV: dict[str, str] = {
    "ollama":    "OLLAMA_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "openai":    "OPENAI_API_KEY",
}


def _env_allowlist_for(engine_id: str, model: str | None = None) -> frozenset[str]:
    """Compose the env-var allowlist for a given engine.

    ``model`` narrows the OpenCode allowlist to only the API key its
    ``"provider/model"`` string actually implies (e.g. ``"openai/gpt-5"``
    -> only ``OPENAI_API_KEY``) instead of unconditionally handing every
    OpenCode delegation ALL THREE providers' keys regardless of which one
    the task actually targets (adversarial review finding) — an
    ``"openai/..."`` call has no legitimate need for
    ``ANTHROPIC_API_KEY``/``OLLAMA_API_KEY``, and a curious or
    successfully-injected worker with shell/file tools has a plausible path
    to read and exfiltrate credentials it was never supposed to need for
    its assigned task. A missing/unrecognised model falls back to the full
    set (unchanged, conservative default) since the caller may rely on
    OpenCode's own default-model resolution.
    """
    additions = _ENGINE_ENV_ADDITIONS.get(engine_id, frozenset())
    if engine_id == "opencode" and model:
        provider = model.split("/", 1)[0].strip().lower()
        scoped = _OPENCODE_PROVIDER_ENV.get(provider)
        if scoped is not None:
            additions = frozenset({scoped})
        # Unrecognised provider (e.g. a custom OpenRouter model) -> no
        # implicit key at all; per the module's own convention such
        # providers pass their key via env_extra.
        elif provider not in ("", None):
            additions = frozenset()
    return _BASE_ENV_ALLOWLIST | additions


@contextlib.contextmanager
def _scrubbed_environ(allowlist: frozenset[str]) -> Iterator[None]:
    """Temporarily strip ``os.environ`` down to ``allowlist``.

    The engine modules currently base their spawn env on ``os.environ.copy()``
    (codex_cli.py:118-120 etc.) and OVERLAY any caller-provided env on top.
    To enforce a restrictive base env we mutate ``os.environ`` in place
    for the duration of the spawn, then restore it.

    Single-threaded contract: the MCP server dispatches one tools/call
    at a time (serial ``serve()`` loop). A future multi-threaded path
    would need a different mechanism (env-replace flag on each engine).
    """
    saved = dict(os.environ)
    try:
        for k in list(os.environ):
            if k not in allowlist:
                del os.environ[k]
        yield
    finally:
        # Restore atomically — clear current contents, repopulate from saved.
        os.environ.clear()
        os.environ.update(saved)


@contextlib.contextmanager
def _hermetic_tempdir(prefix: str = "corvin-delegate-") -> Iterator[Path]:
    """Create a private 0o700 tempdir, yield its Path, rmtree on exit."""
    path = Path(tempfile.mkdtemp(prefix=prefix))
    try:
        # mkdtemp creates 0o700 by default on POSIX, but be explicit.
        try:
            os.chmod(path, 0o700)
        except OSError:
            pass
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


def sweep_stale_hermetic_tempdirs(
    max_age_s: float = 3600, prefix: str = "corvin-delegate-",
) -> int:
    """Best-effort reaper for orphaned hermetic tempdirs.

    ``_hermetic_tempdir``'s try/finally only guarantees ``rmtree`` on normal
    Python-level exit (exception or completion) — a hard process kill
    (SIGKILL, OOM-killer, systemd hard-restart, host crash) during an active
    delegation leaves the 0o700 tempdir under the system tempdir permanently,
    potentially containing partial/sensitive intermediate worker output. No
    sweep existed anywhere for these directories (adversarial review
    finding), unlike the analogous stale-running-task reaper for a different
    subsystem. Called best-effort at MCP-server startup; fail-soft, never
    raises. Returns the number of directories removed.
    """
    removed = 0
    try:
        base = Path(tempfile.gettempdir())
        now = time.time()
        for entry in base.glob(f"{prefix}*"):
            try:
                if not entry.is_dir():
                    continue
                age = now - entry.stat().st_mtime
                if age > max_age_s:
                    shutil.rmtree(entry, ignore_errors=True)
                    removed += 1
            except OSError:
                continue
    except Exception:  # noqa: BLE001
        pass
    return removed


def _scan_injection_markers(text: str) -> list[str]:
    """Scan worker output for prompt-injection marker patterns.

    Returns a list of matched marker names (e.g. ``["ignore_previous",
    "system_tag_inject"]``). Only the first ``_INJECTION_SCAN_HEAD_CHARS``
    are scanned — injections in the wild are almost always near the
    start, and a full-document regex pass adds cost without value.

    Detection is structural and conservative: false positives are
    acceptable (the framing-block downstream is non-disruptive); false
    negatives are the worry. The patterns map to the well-known
    attack families documented in OWASP LLM01.
    """
    if not isinstance(text, str) or not text:
        return []
    head = text[:_INJECTION_SCAN_HEAD_CHARS]
    hits: list[str] = []
    for name, pat in _INJECTION_PATTERNS:
        if pat.search(head):
            hits.append(name)
    return hits


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def _validate_prompt(prompt: Any) -> str:
    if not isinstance(prompt, str):
        raise DelegateError("prompt must be a string")
    if not prompt.strip():
        raise DelegateError("prompt must not be empty")
    if len(prompt) > PROMPT_MAX_CHARS:
        raise DelegateError(
            f"prompt too long ({len(prompt)} chars; cap {PROMPT_MAX_CHARS})"
        )
    return prompt


def _validate_model(model: Any) -> str | None:
    if model is None:
        return None
    if not isinstance(model, str):
        raise DelegateError("model must be a string or None")
    model = model.strip()
    if not model:
        return None
    if len(model) > MODEL_MAX_CHARS:
        raise DelegateError(
            f"model id too long ({len(model)} chars; cap {MODEL_MAX_CHARS})"
        )
    return model


def _clamp_budget(budget_s: Any, ceiling_s: int = BUDGET_MAX_S) -> int:
    try:
        b = int(budget_s)
    except (TypeError, ValueError):
        b = BUDGET_DEFAULT_S
    if b < BUDGET_MIN_S:
        return BUDGET_MIN_S
    # ceiling_s defaults to the interactive cap (600 s); only the ACS quota
    # fallback passes the elevated BUDGET_FALLBACK_MAX_S (review F7). A caller
    # can never widen its OWN ceiling — the elevated value is a fixed constant,
    # not caller input.
    ceiling_s = max(BUDGET_MIN_S, min(int(ceiling_s), BUDGET_FALLBACK_MAX_S))
    if b > ceiling_s:
        return ceiling_s
    return b


#: Operator-declared extra workspace roots (``os.pathsep``-separated absolute
#: paths). Operator environment only - never a tool argument.
WORKSPACE_ROOTS_ENV = "CORVIN_DELEGATE_WORKSPACE_ROOTS"

# Directories directly under the user's home that hold credentials or the
# configuration of this very toolchain. A working dir inside one of them - or
# one that CONTAINS one of them - is refused.
#
# This list is the "contains" half only. For the "inside" half a list can never
# be complete (review R3-BR-4: ``~/.local/bin``, ``~/.password-store``,
# ``~/.cargo``, ``~/.mozilla``, ``~/.cache`` and ``~/.local/share/uv`` were all
# accepted): EVERY first-level dot directory of the home is refused, see
# ``_home_dot_dir``.
_HOME_PROTECTED = (
    ".ssh", ".claude", ".config", ".aws", ".azure", ".gnupg", ".kube", ".docker",
    ".codex", ".local/share/keyrings", ".netrc", ".npmrc", ".pypirc",
    ".git-credentials", ".corvin", ".password-store", ".mozilla", ".cargo",
    ".local/bin", ".local/share/uv", ".cache", ".thunderbird", ".kube",
)
_SYSTEM_DIRS_POSIX = (
    "/etc", "/usr", "/bin", "/sbin", "/lib", "/lib32", "/lib64", "/boot", "/dev",
    "/proc", "/sys", "/root", "/run", "/var", "/opt", "/srv", "/snap",
    "/System", "/Library", "/Applications", "/private/etc", "/private/var",
)


def _resolved(path: "str | Path") -> "Path | None":
    try:
        return Path(os.path.expandvars(str(path))).expanduser().resolve()
    except (OSError, RuntimeError, ValueError):
        return None


def _within(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def _delegate_corvin_home() -> "Path | None":
    """The runtime root, by the canonical resolver (``forge.paths``), else the
    documented rule ``$CORVIN_HOME`` / ``~/.corvin``."""
    try:
        from .audit import _forge_modules
        _sec, _fpaths = _forge_modules()
        return _resolved(_fpaths.corvin_home())
    except Exception:  # noqa: BLE001 — fall back to the documented rule
        raw = os.environ.get("CORVIN_HOME", "").strip()
        return _resolved(raw) if raw else _resolved(Path.home() / ".corvin")


def _corvin_home_workspace(resolved: Path, home: Path, tenant_id: str = "_default") -> bool:
    """True for the two worker workspaces that live INSIDE the runtime root, of
    THIS delegation's tenant only: a session directory
    (``tenants/<tid>/sessions/<...>``; legacy ``sessions/<...>`` for the default
    tenant) and an ACS run directory (``tenants/<tid>/global/acs/runs/<run>/...``).

    Until 2026-11-19 only the ``sessions`` / ``global/acs/runs`` components were
    compared, so a delegation of tenant ``_default`` was admitted into
    ``tenants/other/sessions/c1`` (review R3-BR-4)."""
    try:
        parts = resolved.relative_to(home).parts
    except ValueError:
        return False
    if len(parts) >= 2 and parts[0] == "sessions":
        return tenant_id == "_default"
    if len(parts) < 4 or parts[0] != "tenants" or parts[1] != tenant_id:
        return False
    if parts[2] == "sessions":
        return True
    return len(parts) >= 6 and parts[2:5] == ("global", "acs", "runs")


def _home_dot_dir(resolved: Path, user_home: "Path | None") -> bool:
    """True when *resolved* lies in a first-level dot directory of the user's home
    (``~/.ssh``, ``~/.local/...``, ``~/.cache/...``, any other ``~/.<name>``).
    Those hold credentials, toolchain configuration, executables on ``PATH`` and
    package caches that later runs execute; none is a project workspace."""
    if user_home is None:
        return False
    try:
        parts = resolved.relative_to(user_home).parts
    except ValueError:
        return False
    return bool(parts) and parts[0].startswith(".")


def _protected_dirs() -> "tuple[list[Path], set[Path]]":
    """``(protected, system)`` - every directory a delegated worker must never
    get as (part of) its writable root: the runtime root with its audit chain,
    credential and toolchain-config directories, and the system directories
    (the second value names which of them are system directories)."""
    out: list[Path] = []
    system: set[Path] = set()

    def _add(p: "str | Path | None", *, is_system: bool = False) -> None:
        r = _resolved(p) if p else None
        if r is None or r == Path(r.anchor):
            return
        if r not in out:
            out.append(r)
        if is_system:
            system.add(r)

    _add(_delegate_corvin_home())
    try:
        user_home = Path.home()
    except (RuntimeError, OSError):
        user_home = None
    if user_home is not None:
        for name in _HOME_PROTECTED:
            _add(user_home / name)
    _add(os.environ.get("XDG_CONFIG_HOME", "").strip() or None)
    _add(os.environ.get("CLAUDE_CONFIG_DIR", "").strip() or None)
    _add(os.environ.get("CODEX_HOME", "").strip() or None)
    if os.name == "nt":
        for var in ("SystemRoot", "ProgramFiles", "ProgramFiles(x86)", "ProgramData",
                    "APPDATA", "LOCALAPPDATA"):
            _add(os.environ.get(var, "").strip() or None, is_system=True)
    else:
        for d in _SYSTEM_DIRS_POSIX:
            # Resolved like the candidate: /var -> /private/var on macOS,
            # /bin -> /usr/bin on merged-usr hosts.
            _add(d, is_system=True)
    return out, system


def _workspace_roots() -> "tuple[list[Path], list[Path], list[Path]]":
    """``(operator_roots, session_roots, temp_roots)`` a working dir may live
    under. A temp root admits only directories strictly BELOW it: the shared
    temp directory itself as a worker's writable root would hand it every
    other process's temp files."""
    operator: list[Path] = []
    for raw in os.environ.get(WORKSPACE_ROOTS_ENV, "").split(os.pathsep):
        raw = raw.strip()
        r = _resolved(raw) if raw and os.path.isabs(os.path.expanduser(raw)) else None
        if r is not None and r != Path(r.anchor):
            operator.append(r)
    session: list[Path] = []
    raw = os.environ.get("CORVIN_SESSION_DIR", "").strip()
    r = _resolved(raw) if raw and os.path.isabs(raw) else None
    if r is not None and r != Path(r.anchor):
        session.append(r)
    temp: list[Path] = []
    for raw in (tempfile.gettempdir(),) + (("/var/tmp",) if os.name != "nt" else ()):
        r = _resolved(raw)
        if r is not None and r not in temp:
            temp.append(r)
    return operator, session, temp


def _validate_working_dir(working_dir: Any, tenant_id: "str | None" = None) -> Path | None:
    """Validate a caller-supplied worker workspace and return it RESOLVED.

    The working dir is the worker's writable root: the sandbox binds it
    read-write and an unsandboxed engine takes it as its cwd / workspace. Until
    2026-11-19 only a filesystem root was refused, so ``/home``, ``/etc``, the
    user's home, ``~/.ssh``, the runtime root and the audit-chain directory were
    all accepted (review R2-A2A-7). The rule is positive now, with a deny list
    as second line:

    * it must resolve under a workspace root - a session or ACS run directory
      of the runtime root THAT BELONGS TO ``tenant_id``, ``CORVIN_SESSION_DIR``,
      a directory below the temp directory, a directory below the user's home
      that is not in one of its first-level dot directories, or a root the
      OPERATOR declared in ``CORVIN_DELEGATE_WORKSPACE_ROOTS``;
    * it must not be, or lie inside, a protected directory (the runtime root,
      credential / toolchain-config directories, system directories);
    * it must not CONTAIN a protected directory (``/home/<user>`` contains
      ``~/.ssh``; a checkout contains its repo-local runtime root) - unless the
      operator declared that very tree as a workspace root.

    The RESOLVED path is returned and used for the spawn: handing back the
    caller's spelling let a symlink be re-pointed between this check and the
    spawn.
    """
    if working_dir is None:
        return None
    if not isinstance(working_dir, (str, Path)):
        raise DelegateError("working_dir must be a string, Path, or None")
    p = Path(working_dir)
    if not p.is_absolute():
        raise DelegateError(f"working_dir must be absolute: {working_dir!r}")
    try:
        resolved = p.resolve()
    except (OSError, RuntimeError) as exc:
        raise DelegateError(f"working_dir cannot be resolved: {working_dir!r}") from exc
    if resolved == Path(resolved.anchor) or resolved.parent == resolved:
        raise DelegateError("working_dir must not be a filesystem root")

    operator_roots, session_roots, temp_roots = _workspace_roots()
    corvin_home = _delegate_corvin_home()
    tenant = _resolve_tenant_id(tenant_id)
    in_home_workspace = (corvin_home is not None
                         and _corvin_home_workspace(resolved, corvin_home, tenant))
    try:
        user_home = Path.home().resolve()
    except (RuntimeError, OSError):
        user_home = None

    # The roots that ADMIT this path (positive rule).
    in_operator_root = any(_within(resolved, r) for r in operator_roots)
    admitting: list[Path] = [r for r in operator_roots if _within(resolved, r)]
    admitting += [r for r in session_roots if _within(resolved, r)]
    admitting += [r for r in temp_roots if r in resolved.parents]
    if in_home_workspace and corvin_home is not None:
        admitting.append(corvin_home)
    # R3-BR-4: the home admits a path only outside its first-level dot directories.
    # A dot directory is reachable through an explicit root alone (operator root,
    # the session directory, a workspace of the runtime root, the temp directory).
    in_dot_dir = _home_dot_dir(resolved, user_home)
    if user_home is not None and user_home in resolved.parents and not in_dot_dir:
        admitting.append(user_home)
    if in_dot_dir and not admitting:
        raise DelegateError(
            "working_dir is inside a dot directory of the user's home (credentials, "
            f"toolchain configuration, caches); declare it in {WORKSPACE_ROOTS_ENV} "
            "if this tree is meant to be a worker workspace")

    protected, system = _protected_dirs()
    for prot in protected:
        if not _within(resolved, prot):
            continue
        # A protected directory that merely CONTAINS an admitting root does not
        # apply inside that root: the temp dir lives under /var on macOS, a
        # root-run install has its home at /root, a session directory lies in
        # the runtime root. The root itself being the protected directory only
        # counts for a system directory (home == /root) and for the two
        # workspace subtrees of the runtime root.
        if any(_within(r, prot) and (r != prot or prot in system) for r in admitting):
            continue
        if in_home_workspace and prot == corvin_home:
            continue
        raise DelegateError(
            "working_dir is inside a protected directory (runtime root, "
            "credential/config or system directory)")
    if not in_operator_root:
        for prot in protected:
            if prot != resolved and _within(prot, resolved):
                raise DelegateError(
                    "working_dir contains a protected directory (runtime root or "
                    f"credential/config directory); declare it in {WORKSPACE_ROOTS_ENV} "
                    "if this tree is meant to be a worker workspace")
    if not admitting:
        raise DelegateError(
            "working_dir is not under a workspace root (session directory, temp "
            f"directory, the user's home, or {WORKSPACE_ROOTS_ENV})")
    return resolved


def _validate_env_extra(env_extra: Any) -> dict[str, str] | None:
    if env_extra is None:
        return None
    if not isinstance(env_extra, dict):
        raise DelegateError("env_extra must be a dict or None")
    out: dict[str, str] = {}
    for k, v in env_extra.items():
        if not isinstance(k, str) or not isinstance(v, str):
            raise DelegateError("env_extra keys and values must be strings")
        out[k] = v
    return out


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def run_delegate(
    *,
    engine: str,
    prompt: str,
    model: str | None = None,
    budget_s: int = BUDGET_DEFAULT_S,
    budget_ceiling_s: int = BUDGET_MAX_S,
    working_dir: str | Path | None = None,
    env_extra: dict[str, str] | None = None,
    engine_factory: Callable[[str], Any] | None = None,
    audit: bool = True,
    persona: str | None = None,
    allow_write: bool = False,
    output_cap_chars: int = OUTPUT_CAP_DEFAULT_CHARS,
    hermetic: bool = True,
    env_passthrough: bool = False,
    output_judge_mode: str | None = None,
    judge_runner: Any = None,
    sandbox_mode: str | None = None,
    prompt_safety_mode: str | None = None,
    safety_runner: Any = None,
    # Layer 30 (ADR-0022) — engine-agnostic Forge + SkillForge.
    inject_skills: bool | None = None,
    forge_enabled: bool | None = None,
    skill_forge_enabled: bool | None = None,
    # ADR-0049 — session pinning (claude_code engine only)
    pin_session: bool = False,
    scope_label: str = "",
    session_home: "Path | None" = None,
    tenant_id: "str | None" = None,
) -> DelegateResult:
    """Synchronous delegation. Returns DelegateResult (never raises engine errors).

    Caller-side validation failures (bad engine id, oversized prompt,
    non-absolute working_dir) raise ``DelegateError`` so the MCP-server
    layer can surface a proper JSON-RPC error. Engine-side failures
    (binary missing, stream timeout, non-zero exit) land on
    ``DelegateResult.error`` with ``ok=False``.

    Layer 29.1 hardenings layered on top of the v0.1 contract:

    * ``allow_write`` (default False) — gates the engine permission
      mode. False → Claude Code in ``permission_mode="default"``,
      OpenCode via ``--agent plan`` (read-only), Codex stays at
      its ``--sandbox read-only`` default. True → bypass mode.
    * ``output_cap_chars`` — clamps the worker's ``final_text``
      length. Default 64 KB; clamped to [1 KB, 512 KB]. Oversized
      output is truncated and the result carries
      ``output_truncated=True``.
    * The result also carries ``injection_markers`` — a list of
      well-known prompt-injection patterns that fired against the
      first 8 KB of worker output. The MCP-server layer uses this
      to wrap the text with a framing block before Claude OS sees it.
    * ``hermetic`` (default True, Layer 29.2a) — if no
      ``working_dir`` is passed, the worker runs in a fresh
      ``mktemp -d 0o700`` that is rmtree'd after the call. Setting
      ``working_dir`` explicitly bypasses the hermetic dir.
    * ``env_passthrough`` (default False, Layer 29.2b) — when
      False the worker's environment is scrubbed to a curated
      allowlist (PATH/HOME/USER/LANG/TERM + engine-specific API
      key). ``env_extra`` always adds on top. Set True to inherit
      the parent process's full env (legacy v0.1 behaviour).
    """
    engine = _normalize_legacy_engine_id(engine)
    if engine not in AVAILABLE_ENGINES:
        raise DelegateError(
            f"unknown engine: {engine!r}; expected one of {AVAILABLE_ENGINES}"
        )
    prompt = _validate_prompt(prompt)
    model = _validate_model(model)
    budget = _clamp_budget(budget_s, ceiling_s=budget_ceiling_s)
    cwd = _validate_working_dir(working_dir, tenant_id)
    env_overlay = _validate_env_extra(env_extra)
    cap_chars = _clamp_output_cap(output_cap_chars)
    allow_write = bool(allow_write)

    # ADR-0149 LIC-ENG-USE-01: license engines_allowed gate (fail-CLOSED). The
    # adapter OS-turn spawn path enforces engines_allowed via _lic_assert_limit, but
    # the delegate worker-engine USE path did not — a SesT carrying
    # limits.engines_allowed could spawn a forbidden worker engine here unchecked.
    # Latent today (every tier sets engines_allowed=None, so assert_limit is a
    # no-op) but closed before engines become a paid axis. The dual-env test bypass
    # mirrors adapter.py: BOTH vars are required, a single one must NOT disable it.
    if not (
        os.environ.get("CORVIN_AGENTS_SKIP_LIVE") == "1"
        and os.environ.get("CORVIN_INTEGRATION_TEST") == "1"
    ):
        _eng_lic_err: "type | None" = None
        try:
            _eng_op = str(Path(__file__).resolve().parents[3] / "corvin_operator")
            if _eng_op not in sys.path:
                sys.path.insert(0, _eng_op)
            from license.validator import assert_limit as _eng_assert  # type: ignore
            from license.limits import LicenseLimitError as _eng_lic_err  # type: ignore
            _eng_assert("engines_allowed", engine)
        except Exception as _eng_exc:  # noqa: BLE001 — fail-CLOSED on any license error
            _denied = _eng_lic_err is not None and isinstance(_eng_exc, _eng_lic_err)
            # A genuine LicenseLimitError already gets its own L16 event from
            # inside validator.assert_limit() (license.limit_exceeded) before
            # it raises. But any OTHER exception here (e.g. the `license.*`
            # import itself failing, or a corrupted/unverifiable token making
            # _verified_license() raise) denies the delegation with NO
            # corresponding audit event at all — a real gap given this
            # project's own load-bearing requirement that every enforcement
            # decision hash-chain into the audit log (adversarial review
            # finding). Emit one explicitly for exactly that non-denied case.
            if audit and not _denied:
                _emit_audit_failed(
                    engine=engine,
                    persona=(persona or os.environ.get("CORVIN_CALLER_PERSONA") or "").strip(),
                    reason=f"license-gate-error: {type(_eng_exc).__name__}",
                    duration_ms=0,
                )
            return DelegateResult(
                ok=False, engine=engine, duration_ms=0, model=model,
                error=(
                    f"engine-not-allowed-by-license: {engine}" if _denied
                    else f"license-gate-error (fail-closed): {type(_eng_exc).__name__}"
                ),
            )

        # ADR-0150 LIC-DELEGATE-MCP-COMPUTE-01 (superseded, 2026-07-06): this used
        # to charge compute_units_per_day here too, symmetric to the web-chat ACS
        # branch. Maintainer decision: normal engine delegation (this path) is not
        # a metered "big data / heavy compute" feature and must keep working even
        # when the ACS daily compute quota is exhausted — only ACS (chat_runtime.py
        # web-chat branch + acs_engine_adapter.run_acs_workflow) is quota-gated.
        # engines_allowed (above) remains the gate for this path.

    factory = engine_factory or _default_engine_factory
    persona_tag = (persona or os.environ.get("CORVIN_CALLER_PERSONA") or "").strip()
    # The tenant is resolved ONCE and handed to every gate / audit writer below.
    tenant = _resolve_tenant_id(tenant_id)

    # Layer 29.4a — tenant policy + engine-zone gate. Closes the
    # data-residency bypass: ADR-0007 Phase 3.2/3.3 gates the gateway
    # but until 29.4a, ``run_delegate`` did NOT consult any of that.
    # The gate fires AFTER caller-side validation but BEFORE the
    # engine factory runs, so a denied call burns no subprocess
    # resources. Audit event fires regardless of caller's audit flag
    # — operator visibility is non-negotiable for policy denies.
    policy_denial = _check_tenant_policy(
        engine=engine,
        model=model,
        persona_tag=persona_tag,
        audit=audit,
    )
    if policy_denial is not None:
        return policy_denial

    # Layer 34 — data-classification × engine-egress gate. Orthogonal to the
    # zone gate above (zone = "may this engine run here", L34 = "may THIS data
    # reach this engine's egress"). Both must pass. Fires before the factory
    # so a denied call spawns no subprocess.
    classification_denial = _check_data_classification(
        engine=engine,
        prompt=prompt,
        model=model,
        persona_tag=persona_tag,
        tenant_id=tenant,
    )
    if classification_denial is not None:
        return classification_denial

    # Layer 29.6 — pre-flight prompt-safety classifier. Asymmetric
    # resolution: env-floor wins over LLM tool-arg. Mode `off` is a
    # zero-cost no-op (no subprocess). Mode `blocking` + `REFUSE`
    # → deny the delegation BEFORE invoked-audit fires (a refused
    # call shouldn't show up as "we ran this", only as "we refused
    # to run it"). The classify metadata-audit always lands.
    safety_denial = _check_prompt_safety(
        engine=engine,
        prompt=prompt,
        persona_tag=persona_tag,
        mode=prompt_safety_mode,
        runner=safety_runner,
        audit=audit,
        model=model,
        allow_write=allow_write,
    )
    if safety_denial is not None:
        return safety_denial

    if audit:
        _emit_audit_invoked(
            engine=engine,
            persona=persona_tag,
            prompt_chars=len(prompt),
            budget_s=budget,
            model=model,
        )

    start = time.monotonic()
    start_wall = time.time()
    try:
        worker = factory(engine)
    except DelegateError:
        raise
    except Exception as e:  # noqa: BLE001
        duration_ms = int((time.monotonic() - start) * 1000)
        reason = f"engine-construct-failed: {type(e).__name__}"
        if audit:
            _emit_audit_failed(
                engine=engine,
                persona=persona_tag,
                reason=reason,
                duration_ms=duration_ms,
            )
        return DelegateResult(
            ok=False,
            engine=engine,
            duration_ms=duration_ms,
            error=f"{reason}: {e}"[:400],
            model=model,
        )

    try:
        # collect() drains the StreamEvent iterator into a SpawnResult.
        from agents import collect  # type: ignore  # noqa: PLC0415
    except Exception as e:  # noqa: BLE001 — agents package missing
        duration_ms = int((time.monotonic() - start) * 1000)
        reason = f"agents-import-failed: {type(e).__name__}"
        if audit:
            _emit_audit_failed(
                engine=engine,
                persona=persona_tag,
                reason=reason,
                duration_ms=duration_ms,
            )
        return DelegateResult(
            ok=False,
            engine=engine,
            duration_ms=duration_ms,
            error=f"{reason}: {e}"[:400],
            model=model,
        )

    # ADR-0049 — capability gate + session loading.
    _pin_can = False
    _pin_resume_sid: str | None = None
    _pin_ws_dir: "Path | None" = None
    if pin_session:
        caps = getattr(worker, "capabilities", {}) or {}
        if not caps.get("session_pinning"):
            duration_ms = int((time.monotonic() - start) * 1000)
            reason = f"capability-error: engine {engine!r} does not support session_pinning"
            if audit:
                _emit_audit_failed(
                    engine=engine,
                    persona=persona_tag,
                    reason=reason,
                    duration_ms=duration_ms,
                )
            return DelegateResult(
                ok=False,
                engine=engine,
                duration_ms=duration_ms,
                error=reason,
                model=model,
            )
        _pin_can = True
        if session_home is not None and scope_label:
            try:
                _ensure_agents_on_path()
                from worker_session_store import (  # type: ignore[import-not-found]
                    worker_sessions_dir as _wsd, load_session as _ls,
                )
                _pin_ws_dir = _wsd(session_home)
                _pin_resume_sid = _ls(_pin_ws_dir, scope_label)
            except Exception:  # noqa: BLE001
                pass

    safe_kwargs = _safe_spawn_kwargs(engine, allow_write)

    # Layer 29.5 — sandbox decision (asymmetric resolution).
    # The LLM-controllable tool-arg can only WIDEN strictness above
    # the operator-set env floor — same security-gate property as
    # 29.3a output-judge.
    from . import sandbox as _sandbox
    final_sandbox_mode = _sandbox.max_strictness(
        _sandbox.env_floor_mode(),
        sandbox_mode,
    )
    sandbox_decision = _sandbox.decide_sandbox(final_sandbox_mode)
    # A sandbox that was DECIDED is not yet a sandbox that is APPLIED. Two
    # things can still keep the bwrap argv from reaching the subprocess:
    #   * the engine has no launcher-prefix support (capability "argv_prefix");
    #     an engine that swallows unknown kwargs would run NATIVE while the
    #     chain said "sandboxed";
    #   * there is no directory to bind (hermetic=False and no working_dir).
    # ``enforcing`` then refuses the delegation; ``advisory`` runs native and
    # says so. ``delegate.sandboxed`` is written only after the prefix was
    # handed to the engine's spawn (see ``sandbox_applied`` below).
    sandbox_block = ""
    if sandbox_decision == "bwrap":
        _engine_caps = getattr(worker, "capabilities", None)
        if not (isinstance(_engine_caps, dict) and _engine_caps.get("argv_prefix") is True):
            sandbox_block = "engine-no-argv-prefix"
        elif cwd is None and not hermetic:
            sandbox_block = "no-working-dir-to-bind"
    # Lifecycle audit BEFORE the env-scrub context manager starts, so
    # CORVIN_HOME is still in the env when the audit writer resolves the chain.
    if audit:
        if sandbox_decision == "denied-no-bwrap":
            _emit_audit_sandbox_unavailable(
                engine=engine,
                persona=persona_tag,
                mode=final_sandbox_mode,
                reason="bwrap-binary-missing",
            )
        elif sandbox_decision == "fallback-no-bwrap":
            _emit_audit_sandbox_unavailable(
                engine=engine,
                persona=persona_tag,
                mode=final_sandbox_mode,
                reason="bwrap-binary-missing-fallback-native",
            )
        elif sandbox_block:
            _emit_audit_sandbox_unavailable(
                engine=engine,
                persona=persona_tag,
                mode=final_sandbox_mode,
                reason=(sandbox_block if final_sandbox_mode == "enforcing"
                        else f"{sandbox_block}-fallback-native"),
            )
    if sandbox_decision == "denied-no-bwrap":
        return DelegateResult(
            ok=False,
            engine=engine,
            error=("sandbox-denied: bwrap binary not found and "
                   "sandbox_mode=enforcing requires it"),
            model=model,
            allow_write=allow_write,
        )
    if sandbox_block:
        if final_sandbox_mode == "enforcing":
            return DelegateResult(
                ok=False,
                engine=engine,
                error=(f"sandbox-denied: sandbox_mode=enforcing but the sandbox "
                       f"cannot be applied to engine {engine!r} ({sandbox_block})"),
                model=model,
                allow_write=allow_write,
            )
        sandbox_decision = "fallback-unsupported"
    # ``requested``: the bwrap prefix was built and handed to spawn().
    # ``applied``: that spawn ran - the engine's event stream was drained
    # without raising. Only ``applied`` is recorded as ``delegate.sandboxed``
    # (review R2-A2A-10: the flag used to flip BEFORE worker.spawn, so a spawn
    # that raised left "decision: bwrap" in the chain for a sandbox that never
    # started).
    sandbox_requested = False
    sandbox_applied = False

    # Layer 29.2a — when no working_dir given AND hermetic mode is on
    # (default), provide a fresh 0o700 tempdir for the duration of the
    # spawn. Exiting the context manager rmtree's it.
    hermetic_active = hermetic and cwd is None
    # Layer 29.2b — env scrubbing. When env_passthrough is False
    # (default), the parent's os.environ is stripped to the curated
    # allowlist for the duration of the spawn. env_overlay still
    # adds on top via the engine's existing env= overlay.
    if env_passthrough:
        env_cm = contextlib.nullcontext()
    else:
        env_cm = _scrubbed_environ(_env_allowlist_for(engine, model))

    if hermetic_active:
        tempdir_cm = _hermetic_tempdir()
    else:
        tempdir_cm = contextlib.nullcontext(cwd)

    try:
        with tempdir_cm as effective_cwd, env_cm:
            # Layer 30 (ADR-0022) — skill-context block + MCP pass-through.
            # Both run inside the env-scrub context so the env-floor reads
            # see the operator-set CORVIN_DELEGATE_* vars consistently
            # (they are part of the curated allowlist; see _env_allowlist_for).
            #
            # Skill block: prepended to the prompt BEFORE handing it to
            # the engine. None when disabled (env or persona) or empty.
            skill_block = _build_skill_block_for_engine(
                persona=persona_tag,
                inject_skills=inject_skills,
                audit=audit,
                engine=engine,
            )
            if skill_block:
                prompt = f"{skill_block}\n\n{prompt}"

            # MCP pass-through: materialise per-spawn config in the
            # hermetic tempdir (or operator-supplied working_dir) and
            # merge the resulting spawn_kwargs / env_overlay so the
            # worker can call mcp__forge__* / mcp__skill_forge__* tools.
            mcp_extras = _wire_mcp_for_engine(
                engine=engine,
                persona=persona_tag,
                tempdir=Path(effective_cwd) if effective_cwd else None,
                working_dir=cwd,
                forge_enabled=forge_enabled,
                skill_forge_enabled=skill_forge_enabled,
                audit=audit,
            )

            spawn_kwargs: dict[str, Any] = {
                "model": model,
                "working_dir": effective_cwd,
                "timeout": float(budget),
                "env": _merge_env(env_overlay, mcp_extras["env_overlay"]),
            }
            spawn_kwargs.update(safe_kwargs)
            spawn_kwargs.update(mcp_extras["spawn_kwargs"])
            # Layer 29.5 — when sandbox decision is bwrap, build the
            # bwrap argv prefix and pass to the engine via argv_prefix.
            # The engine modules prepend this to their own args so the
            # subprocess starts inside a fresh PID/IPC/UTS namespace.
            # The lifecycle audit was emitted BEFORE entering env_cm
            # (so CORVIN_HOME is still readable); this block only
            # builds the argv-prefix that flows through to spawn().
            if sandbox_decision == "bwrap":
                if effective_cwd is None:
                    # Unreachable by the checks above; kept as a hard stop so a
                    # future code path cannot spawn native under a bwrap decision.
                    raise RuntimeError("sandbox decided but no directory to bind")
                argv_prefix = _sandbox.build_bwrap_args(
                    engine_id=engine,
                    hermetic_dir=Path(effective_cwd),
                    allow_net=True,
                )
                spawn_kwargs["argv_prefix"] = argv_prefix
                sandbox_requested = True
            # ADR-0049 — pass resume_session_id when session is pinned.
            if _pin_can and _pin_resume_sid:
                spawn_kwargs["resume_session_id"] = _pin_resume_sid
            # ADR-0648 amendment 2: `prompt` reaches here from the
            # `delegate_*` MCP tools (`mcp_server.py::_handle_tools_call`)
            # after `_validate_prompt`, which checks type and length only.
            # The neutraliser is applied inside `ClaudeCodeEngine.spawn()`,
            # so the worker cannot be started on an unguarded payload; keep
            # the engine abstraction rather than building argv here.
            events = worker.spawn(prompt, **spawn_kwargs)
            spawn_result = collect(events)
            sandbox_applied = sandbox_requested

            # ADR-0049 — stale-session eviction + one-shot re-spawn.
            if (_pin_can and _pin_resume_sid
                    and _is_stale_session_error(spawn_result.error)):
                if _pin_ws_dir and scope_label:
                    try:
                        from worker_session_store import delete_session as _ds  # type: ignore
                        _ds(_pin_ws_dir, scope_label)
                    except Exception:  # noqa: BLE001
                        pass
                _emit_worker_session_audit_delegate(
                    "worker_session.stale_evicted",
                    scope_label=scope_label, persona=persona_tag,
                )
                _pin_resume_sid = None
                spawn_kwargs.pop("resume_session_id", None)
                events2 = worker.spawn(prompt, **spawn_kwargs)
                spawn_result = collect(events2)
    except Exception as e:  # noqa: BLE001
        duration_ms = int((time.monotonic() - start) * 1000)
        reason = f"engine-spawn-failed: {type(e).__name__}"
        if type(e).__name__ == "UnsafeConfigTarget":
            # Raised by the MCP wiring BEFORE any spawn: no engine ran.
            reason = "unsafe-config-target"
        if audit and sandbox_applied:
            # A first spawn ran inside the sandbox; the failure came later
            # (the stale-session re-spawn).
            _emit_audit_sandboxed(engine=engine, persona=persona_tag,
                                  mode=final_sandbox_mode, decision="bwrap")
        elif audit and sandbox_requested:
            # The sandbox was decided and its launcher built, but the spawn
            # raised: nothing ran inside it, and the chain must not say it did.
            _emit_audit_sandbox_unavailable(
                engine=engine, persona=persona_tag, mode=final_sandbox_mode,
                reason="engine-spawn-failed")
        if audit:
            _emit_audit_failed(
                engine=engine,
                persona=persona_tag,
                reason=reason,
                duration_ms=duration_ms,
            )
        return DelegateResult(
            ok=False,
            engine=engine,
            duration_ms=duration_ms,
            error=f"{reason}: {e}"[:400],
            model=model,
            allow_write=allow_write,
        )

    duration_ms = int((time.monotonic() - start) * 1000)
    # ``delegate.sandboxed`` AFTER application: the bwrap prefix was handed to
    # an engine that declares it prepends it. (Written here, outside the
    # env-scrub context, so the chain path resolves.)
    if audit and sandbox_applied:
        _emit_audit_sandboxed(engine=engine, persona=persona_tag,
                              mode=final_sandbox_mode, decision="bwrap")

    if spawn_result.error:
        if audit:
            _emit_audit_failed(
                engine=engine,
                persona=persona_tag,
                reason="engine-error",
                duration_ms=duration_ms,
            )
        # A failed or timed-out run was still billed for what it consumed
        # (A2A-12): close a worker span on the model the engine REPORTED and
        # the usage it returned, status "error". Without it the run read $0.00.
        _write_wdat_run_for_delegation(
            engine=engine, model=model, duration_ms=duration_ms,
            start_wall=start_wall, prompt=prompt, usage=spawn_result.usage,
            reported_model=_attested_model_of(spawn_result),
            tenant_id=tenant, status="error",
        )
        return DelegateResult(
            ok=False,
            engine=engine,
            duration_ms=duration_ms,
            usage=spawn_result.usage,
            error=spawn_result.error[:400],
            model=model,
            allow_write=allow_write,
        )

    # ADR-0049 — persist / update session file after a successful spawn.
    if _pin_can and _pin_ws_dir and scope_label:
        new_sid = _extract_session_id_delegate(spawn_result)
        if new_sid:
            _save_worker_session_delegate(
                _pin_ws_dir, scope_label, new_sid,
                persona_tag, was_resume=bool(_pin_resume_sid),
            )

    # Layer 29.1b — cap oversized output BEFORE the injection scan so the
    # scan sees what the caller will actually receive.
    capped_text, truncated, total_chars = _apply_output_cap(
        spawn_result.final_text, cap_chars
    )
    # Layer 29.1c — structural prompt-injection marker scan.
    markers = _scan_injection_markers(capped_text)

    # Layer 29.3a — faithfulness judge. Mode resolution is the
    # asymmetric "max-strictness" rule: the LLM-controllable
    # tool-arg can WIDEN strictness but never weaken what the
    # operator set via the env floor. That makes the gate a
    # genuine security boundary, not just a preference.
    from . import output_judge as _judge_module
    final_judge_mode = _judge_module.max_strictness(
        _judge_module.env_floor_mode(),
        output_judge_mode,
    )
    judge_result = _judge_module.judge_output(
        prompt=prompt,
        worker_output=capped_text,
        mode=final_judge_mode,
        runner=judge_runner,
    )

    if final_judge_mode == "enforcing" and judge_result.verdict == "judge_error":
        # Enforcing means the output is released only with a verdict. A judge
        # that could not produce one (timeout, crash, unparseable reply) is a
        # denial, not a pass-through of the unjudged text (A2A-12).
        if audit:
            _emit_audit_output_judged(
                engine=engine, persona=persona_tag, mode=final_judge_mode,
                verdict=judge_result.verdict, latency_ms=judge_result.latency_ms,
                replaced=False,
            )
            _emit_audit_failed(engine=engine, persona=persona_tag,
                               reason="output-judge-error", duration_ms=duration_ms)
        _write_wdat_run_for_delegation(
            engine=engine, model=model, duration_ms=duration_ms,
            start_wall=start_wall, prompt=prompt, usage=spawn_result.usage,
            reported_model=_attested_model_of(spawn_result),
            tenant_id=tenant, status="error",
        )
        return DelegateResult(
            ok=False, engine=engine, duration_ms=duration_ms,
            usage=spawn_result.usage, model=model, allow_write=allow_write,
            error=("output-judge-error: output_judge_mode=enforcing and the judge "
                   "returned no verdict — output withheld (fail-closed)"),
            output_judge_mode=final_judge_mode,
            output_judge_verdict=judge_result.verdict,
            output_judge_notes=judge_result.notes,
            output_judge_latency_ms=judge_result.latency_ms,
        )

    judge_replaced = False
    final_text_after_judge = capped_text
    if final_judge_mode == "enforcing" and judge_result.verdict == "corrected":
        if judge_result.revised_text:
            final_text_after_judge = judge_result.revised_text
            judge_replaced = True

    if audit:
        _emit_audit_completed(
            engine=engine,
            persona=persona_tag,
            duration_ms=duration_ms,
            output_chars=len(final_text_after_judge),
        )
        if final_judge_mode != "off":
            _emit_audit_output_judged(
                engine=engine,
                persona=persona_tag,
                mode=final_judge_mode,
                verdict=judge_result.verdict,
                latency_ms=judge_result.latency_ms,
                replaced=judge_replaced,
            )

    _write_wdat_run_for_delegation(
        engine=engine,
        model=model,
        duration_ms=duration_ms,
        start_wall=start_wall,
        prompt=prompt,
        usage=spawn_result.usage,
        reported_model=_attested_model_of(spawn_result),
        tenant_id=tenant,
    )

    return DelegateResult(
        ok=True,
        engine=engine,
        final_text=final_text_after_judge,
        duration_ms=duration_ms,
        usage=spawn_result.usage,
        model=model,
        output_truncated=truncated,
        output_total_chars=total_chars,
        injection_markers=markers,
        allow_write=allow_write,
        output_judge_mode=final_judge_mode,
        output_judge_verdict=judge_result.verdict,
        output_judge_notes=judge_result.notes,
        output_judge_latency_ms=judge_result.latency_ms,
        output_judge_replaced=judge_replaced,
    )


# ---------------------------------------------------------------------------
# WDAT run directory writer — makes delegation runs visible in the Audit graph
# ---------------------------------------------------------------------------


def _write_wdat_run_for_delegation(
    *,
    engine: str,
    model: str | None,
    duration_ms: int,
    start_wall: float,
    prompt: str,
    usage: dict,
    reported_model: str = "",
    tenant_id: "str | None" = None,
    status: str = "ok",
) -> None:
    """Write an ACS-compatible run directory + L16 audit events for a delegation run.

    ``reported_model`` is the model the ENGINE reported (``agents.attested_model``).
    The ``engine.span.end`` record — and the live model-usage signal sent from it
    (ADR-2286) — carry only that, never the requested ``model`` or the engine
    name, so a span without a reported model stays unpriced and unsent (ADR-0759).

    Best-effort — never raises. Called from run_delegate() after a successful
    spawn so the WDAT Audit panel in the console can render the delegation graph.

    The run directory and its acs.* records need CORVIN_SESSION_DIR (chat_runtime.py
    injects it for each web-console subprocess). The engine.span start/end pair is
    written on EVERY surface — bridge, CLI and ACS-adapter delegations included —
    because it is the record that prices the run.

    Audit events written to the per-tenant L16 chain (metadata only per GDPR Art.5):
      acs.manager_decided  — one synthetic manager decision      (session dir only)
      acs.worker_spawned   — the single worker (this engine)     (session dir only)
      acs.engine_completed — timing/token metadata               (session dir only)
      acs.worker_traced    — final status                        (session dir only)
      engine.span.start / engine.span.end (role=worker)          (always)
    """
    # Without a console session dir there is no WDAT run directory to draw — but
    # the engine.span pair is still written: it is what prices the run, and a
    # bridge / CLI / ACS-adapter delegation is billed exactly like a console one
    # (review 2026-11-05 DX-4: those runs left no span and read as $0.00).
    session_dir_str = os.environ.get("CORVIN_SESSION_DIR", "").strip()
    session_dir: "Path | None" = Path(session_dir_str) if session_dir_str else None
    if session_dir is not None and not session_dir.is_absolute():
        session_dir = None

    tenant_id = _resolve_tenant_id(tenant_id)
    status = "ok" if status == "ok" else "error"
    # THE tenant chain via forge.paths (honours CORVIN_HOME, validates the
    # tenant id), resolved by the SAME resolver the delegate.* records use —
    # it repairs a namespace-bound ``forge`` and never binds a second copy
    # from another checkout (``corvin_operator.forge.forge.paths``).
    try:
        from .audit import _forge_modules
        _sec, _fpaths = _forge_modules()
        audit_path = Path(_fpaths.tenant_audit_chain(tenant_id))
    except Exception as exc:  # noqa: BLE001
        _log.error("ACS delegation audit skipped: chain unresolved: %s", exc)
        return

    run_id = f"acs-dlg-{int(start_wall)}-{secrets.token_hex(3)}"
    worker_id = "w0"
    spawn_nonce = secrets.token_hex(4)
    instruction_hash = hashlib.sha256(prompt.encode()).hexdigest()[:16]
    duration_s = duration_ms / 1000.0
    # The model the ENGINE reported (``agents.attested_model``), or "" — never
    # the requested ``model`` and never the engine NAME (A2A-8: ``model or
    # engine`` put "claude_code" / "codex_cli" into ``model_id`` of every
    # record this function writes; a reader prices on that field).
    model_id = reported_model or ""
    # The four-way split (input / output / cache read / cache write) comes from the ONE
    # normaliser every span emitter shares, ``engine_span.usage_split()`` (CLAUDE.md,
    # ADR-0759; review DLG-21 — a private copy here read `tokens_in` / only the raw
    # cache names, so the same engine report priced differently on this path). Cache
    # tokens dominate real spend on a cache-heavy delegation, so they are part of it.
    try:
        _shared_dir = str(Path(__file__).resolve().parents[3]
                          / "corvin_operator" / "bridges" / "shared")
        if _shared_dir not in sys.path:
            sys.path.insert(0, _shared_dir)
        import engine_span as _espan  # type: ignore
    except Exception as exc:  # noqa: BLE001 — no normaliser: nothing priceable to write
        _log.error("ACS delegation audit skipped: engine_span unavailable: %s", exc)
        return
    _split = _espan.usage_split(usage)
    tokens_in = _split["input_tokens"]
    tokens_out = _split["output_tokens"]
    tokens_used = tokens_in + tokens_out

    if session_dir is not None:
        _write_wdat_run_records(
            _sec, audit_path, session_dir=session_dir, run_id=run_id, worker_id=worker_id,
            spawn_nonce=spawn_nonce, instruction_hash=instruction_hash, engine=engine,
            model_id=model_id, tenant_id=tenant_id, start_wall=start_wall,
            duration_s=duration_s, duration_ms=duration_ms, tokens_used=tokens_used,
            status=status)

    # ADR-0171 — universal engine span (role=worker) on the SAME chain + IDs as
    # the acs.* records, so the console renders the delegation engine
    # engine-agnostically (any engine_id), not only via acs.* heuristics.
    try:
        _span_id = f"spn-{run_id}-{worker_id}"
        _sec.write_event(audit_path, _espan.ENGINE_SPAN_START,
                         details=_espan.start_details(
                             span_id=_span_id, role="worker",
                             engine_id=engine, model_id=model_id or "",
                             run_id=run_id))
        _end = _espan.end_details(
            span_id=_span_id, role="worker",
            engine_id=engine, model_id=reported_model or "",
            run_id=run_id, status=status,
            duration_ms=duration_ms, tokens_used=tokens_used, **_split)
        _sec.write_event(audit_path, _espan.ENGINE_SPAN_END, details=_end)
        # ADR-2286 — live model-usage signal from the same span details.
        _espan.send_live_model_usage(_end)
    except Exception as exc:  # noqa: BLE001 — audit is observability, never enforcement
        _log.error("ACS delegation engine span not written: %s", exc)


def _write_wdat_run_records(
    _sec: Any, audit_path: Path, *, session_dir: Path, run_id: str, worker_id: str,
    spawn_nonce: str, instruction_hash: str, engine: str, model_id: str, tenant_id: str,
    start_wall: float, duration_s: float, duration_ms: int, tokens_used: int,
    status: str = "ok",
) -> None:
    """The console's WDAT run directory + its acs.* records (needs a session dir)."""
    # 1. Run directory + manifest.json
    run_dir = session_dir / "acs" / "runs" / run_id
    try:
        run_dir.mkdir(parents=True, exist_ok=True)
        manifest = {
            "run_id":      run_id,
            "workflow_id": f"delegation:{engine}",
            "tenant_id":   tenant_id,
            "started_at":  start_wall,
            "budget":      {"max_wall_time": int(duration_s) + 10},
        }
        (run_dir / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False), encoding="utf-8",
        )
    except OSError:
        return  # cannot create run dir — skip entirely

    # 2. result.json (marks run as completed, not active)
    try:
        result = {
            "run_id":          run_id,
            "status":          "completed" if status == "ok" else "failed",
            "workers_spawned": 1,
            "iterations":      1,
            "elapsed_s":       round(duration_s, 3),
        }
        (run_dir / "result.json").write_text(
            json.dumps(result, ensure_ascii=False), encoding="utf-8",
        )
    except OSError:
        pass  # best-effort; run still shows as active in the panel

    # 3. Audit events → the tenant chain (writer resolved above)
    try:
        _sec.write_event(audit_path, "acs.manager_decided", details={
            "run_id":        run_id,
            "iteration":     0,
            "decision_type": "delegate",
            "spawn_nonce":   spawn_nonce,
        })
        _sec.write_event(audit_path, "acs.worker_spawned", details={
            "run_id":           run_id,
            "worker_id":        worker_id,
            "engine_id":        engine,
            "model_id":         model_id,
            "iteration":        0,
            "depth":            0,
            "spawn_nonce":      spawn_nonce,
            "instruction_hash": instruction_hash,
            "can_delegate":     False,
        })
        _sec.write_event(audit_path, "acs.engine_completed", details={
            "run_id":      run_id,
            "worker_id":   worker_id,
            "engine_id":   engine,
            "model_id":    model_id,
            "duration_ms": duration_ms,
            # Display total only (the console's run graph). The PRICEABLE
            # four-way split is carried by ONE record, the engine.span.end
            # written by the caller (A2A-8): both carried it, and
            # model_selection_learner reads both, so a console delegation was
            # priced twice. ``span_id`` names the span that holds the tokens.
            "tokens_used": tokens_used,
            "span_id":     f"spn-{run_id}-{worker_id}",
        })
        _sec.write_event(audit_path, "acs.worker_traced", details={
            "run_id":     run_id,
            "worker_id":  worker_id,
            "status":     "completed" if status == "ok" else "failed",
            "duration_ms": duration_ms,
            "engine_attestation": {"engine": engine, "model_id": model_id},
        })
    except Exception as exc:  # noqa: BLE001 — audit is observability, never enforcement
        _log.error("ACS delegation audit record lost: %s", exc)


# ---------------------------------------------------------------------------
# Audit shim (lazy import to keep core module zero-dep)
# ---------------------------------------------------------------------------


def _emit_audit_invoked(**fields: Any) -> None:
    try:
        from .audit import emit_invoked
        emit_invoked(**fields)
    except Exception as exc:  # noqa: BLE001 — audit never blocks delegation, but a lost record is never silent
        _log.error("delegate audit record lost (%s): %s", 'emit_invoked', exc)


def _emit_audit_completed(**fields: Any) -> None:
    try:
        from .audit import emit_completed
        emit_completed(**fields)
    except Exception as exc:  # noqa: BLE001 — audit never blocks delegation, but a lost record is never silent
        _log.error("delegate audit record lost (%s): %s", 'emit_completed', exc)


def _emit_audit_failed(**fields: Any) -> None:
    try:
        from .audit import emit_failed
        emit_failed(**fields)
    except Exception as exc:  # noqa: BLE001 — audit never blocks delegation, but a lost record is never silent
        _log.error("delegate audit record lost (%s): %s", 'emit_failed', exc)


def _emit_audit_output_judged(**fields: Any) -> None:
    try:
        from .audit import emit_output_judged
        emit_output_judged(**fields)
    except Exception as exc:  # noqa: BLE001 — audit never blocks delegation, but a lost record is never silent
        _log.error("delegate audit record lost (%s): %s", 'emit_output_judged', exc)


def _emit_audit_engine_policy_denied(**fields: Any) -> None:
    try:
        from .audit import emit_engine_policy_denied
        emit_engine_policy_denied(**fields)
    except Exception as exc:  # noqa: BLE001 — audit never blocks delegation, but a lost record is never silent
        _log.error("delegate audit record lost (%s): %s", 'emit_engine_policy_denied', exc)


def _emit_audit_zone_policy_denied(**fields: Any) -> None:
    try:
        from .audit import emit_zone_policy_denied
        emit_zone_policy_denied(**fields)
    except Exception as exc:  # noqa: BLE001 — audit never blocks delegation, but a lost record is never silent
        _log.error("delegate audit record lost (%s): %s", 'emit_zone_policy_denied', exc)


def _emit_audit_sandboxed(**fields: Any) -> None:
    try:
        from .audit import emit_sandboxed
        emit_sandboxed(**fields)
    except Exception as exc:  # noqa: BLE001 — audit never blocks delegation, but a lost record is never silent
        _log.error("delegate audit record lost (%s): %s", 'emit_sandboxed', exc)


def _emit_audit_sandbox_unavailable(**fields: Any) -> None:
    try:
        from .audit import emit_sandbox_unavailable
        emit_sandbox_unavailable(**fields)
    except Exception as exc:  # noqa: BLE001 — audit never blocks delegation, but a lost record is never silent
        _log.error("delegate audit record lost (%s): %s", 'emit_sandbox_unavailable', exc)


def _emit_audit_prompt_classified(**fields: Any) -> None:
    try:
        from .audit import emit_prompt_classified
        emit_prompt_classified(**fields)
    except Exception as exc:  # noqa: BLE001 — audit never blocks delegation, but a lost record is never silent
        _log.error("delegate audit record lost (%s): %s", 'emit_prompt_classified', exc)


# ---------------------------------------------------------------------------
# Layer 29.6 — Pre-flight prompt-safety check
# ---------------------------------------------------------------------------


def _check_prompt_safety(
    *,
    engine: str,
    prompt: str,
    persona_tag: str,
    mode: str | None,
    runner: Any,
    audit: bool,
    model: str | None,
    allow_write: bool,
) -> "DelegateResult | None":
    """Classify the outbound prompt. Returns deny-shaped DelegateResult
    on REFUSE in blocking mode, or None to proceed.

    Audit always fires when the classifier ran (i.e. mode != off);
    the verdict drives whether we proceed or block. In ``blocking`` mode a
    ``classifier_error`` is a denial (fail-closed); in ``advisory`` mode the
    delegation proceeds and the audit carries the error verdict.
    """
    from . import prompt_safety as _ps  # lazy
    final_mode = _ps.max_strictness(_ps.env_floor_mode(), mode)
    if final_mode == "off":
        return None

    result = _ps.classify_prompt(
        prompt=prompt,
        mode=final_mode,
        runner=runner,
    )

    # ``blocking`` releases a prompt only on a SAFE verdict. A classifier that
    # could not produce one (timeout, crash, unparseable reply) is a denial —
    # it used to be a pass, so taking the classifier down opened the gate
    # (A2A-12). ``advisory`` keeps proceeding on an error, with the audit.
    blocked = (
        final_mode == "blocking"
        and result.verdict in ("refuse", "classifier_error")
    )
    if audit:
        _emit_audit_prompt_classified(
            engine=engine,
            persona=persona_tag,
            mode=final_mode,
            verdict=result.verdict,
            latency_ms=result.latency_ms,
            blocked=blocked,
        )
    if blocked:
        return DelegateResult(
            ok=False,
            engine=engine,
            error=("prompt-safety-refused: classifier marked outbound prompt as REFUSE"
                   if result.verdict == "refuse" else
                   "prompt-safety-refused: prompt_safety_mode=blocking and the "
                   "classifier returned no verdict (fail-closed)"),
            model=model,
            allow_write=allow_write,
        )
    return None


# ---------------------------------------------------------------------------
# Layer 29.4a — Tenant-Policy + Engine-Zone gate
# ---------------------------------------------------------------------------


def _resolve_tenant_id(tenant_id: "str | None") -> str:
    """The tenant this delegation runs for.

    The caller states it (``run_delegate(tenant_id=...)``). The delegate MCP
    server is a per-tenant subprocess whose spawner sets ``CORVIN_TENANT_ID``,
    so that variable is the caller's statement on that surface; ``_default`` is
    the single-tenant install. The resolved id is passed EXPLICITLY to every
    gate below — no gate re-reads the environment on its own.
    """
    tid = (tenant_id or os.environ.get("CORVIN_TENANT_ID") or "_default").strip()
    return tid or "_default"


def _check_data_classification(
    *,
    engine: str,
    prompt: str,
    model: str | None,
    persona_tag: str,
    tenant_id: "str | None" = None,
) -> "DelegateResult | None":
    """Layer 34 — data-classification x engine-egress gate for delegation.

    This is ``spawn_gates.check_l34`` — the gate the OS turn and ACS use — not
    a copy of it. (The A2A worker is NOT on it: ``a2a_worker.spawn_a2a_worker``
    still carries its own L34 block — it classifies several texts, attachments
    included, and ignores a peer's ``[class:]`` marker — so "one gate" holds
    for OS turn, ACS and delegation only.) The copy that lived here failed OPEN on
    every internal error (an unimportable classifier, a present policy that
    could not be loaded, a classify/validate exception) while the shared gate
    refuses in each of those cases, so the same prompt was denied on an OS
    turn and waved through as a delegation.

    Fail-closed: a gate that cannot be loaded or that raises is a denial.
    ``check_l34`` itself keeps the documented opt-in default (no tenant policy
    => allow, except a SECRET prompt) and writes ``data_flow.approved`` /
    ``data_flow.blocked``.
    """
    tenant = _resolve_tenant_id(tenant_id)
    try:
        _ensure_agents_on_path()
        import spawn_gates as _gates  # type: ignore[import-not-found]
        refusal = _gates.check_l34(
            engine, tenant, prompt=prompt, persona=persona_tag or None,
        )
    except Exception as exc:  # noqa: BLE001 — no verdict is a denial
        _log.warning("delegate L34 gate unavailable (%s) — fail-closed",
                     type(exc).__name__)
        return DelegateResult(
            ok=False,
            engine=engine,
            error=(f"data-flow-denied: the data-classification gate could not be "
                   f"evaluated for engine {engine!r} "
                   f"(gate error: {type(exc).__name__}) — fail-closed"),
            model=model,
        )
    if refusal is None:
        return None
    return DelegateResult(
        ok=False,
        engine=engine,
        error=f"data-flow-denied: {refusal}"[:400],
        model=model,
    )


def _check_tenant_policy(
    *,
    engine: str,
    model: str | None,
    persona_tag: str,
    audit: bool,
) -> "DelegateResult | None":
    """Policy gate: returns a deny-shaped DelegateResult or None to allow.

    The policy file is operator-managed (path-gate-protected, see L10
    v3). When absent → no enforcement (single-operator zero-config
    default). When malformed → fail-loud with a curated error
    surfaced as ``policy-malformed``.
    """
    from . import tenant_policy as _tp  # lazy
    try:
        policy = _tp.load_policy()
    except _tp.PolicyMalformed as e:
        # Fail-loud: a broken policy file is silently ignored = silent
        # bypass = the very pattern this gate exists to close.
        if audit:
            _emit_audit_engine_policy_denied(
                engine=engine,
                persona=persona_tag,
                tenant_id=(os.environ.get("CORVIN_TENANT_ID") or "_default"),
                reason="policy-malformed",
            )
        return DelegateResult(
            ok=False,
            engine=engine,
            error=f"policy-malformed: {e}"[:400],
            model=model,
        )
    if policy is None:
        return None  # No policy → no enforcement

    # Engine allow / forbid lists.
    if not policy.is_engine_allowed(engine):
        if audit:
            _emit_audit_engine_policy_denied(
                engine=engine,
                persona=persona_tag,
                tenant_id=policy.tenant_id,
                reason="engine-not-allowed",
            )
        return DelegateResult(
            ok=False,
            engine=engine,
            error=f"engine-not-allowed-by-policy: {engine}",
            model=model,
        )

    # Zone routing.
    engine_zone = _tp.resolve_engine_zone(engine, model)
    zone_ok, zone_reason = _tp.is_zone_compatible(policy.zone, engine_zone)
    if not zone_ok:
        if audit:
            _emit_audit_zone_policy_denied(
                engine=engine,
                persona=persona_tag,
                tenant_id=policy.tenant_id,
                tenant_zone=policy.zone or "",
                engine_zone=engine_zone,
                reason=zone_reason,
            )
        return DelegateResult(
            ok=False,
            engine=engine,
            error=f"zone-policy-denied: {zone_reason}",
            model=model,
        )

    return None  # Allowed


# ---------------------------------------------------------------------------
# Layer 30 (ADR-0022) — Skill-Context + MCP-Pass-Through helpers
# ---------------------------------------------------------------------------


def _build_skill_block_for_engine(
    *,
    persona: str,
    inject_skills: bool | None,
    audit: bool,
    engine: str,
) -> str | None:
    """Build the optional skill-context block for a delegate spawn.

    Returns the block string (already wrapped) or None when injection
    is disabled / no skills are eligible / SkillForge is missing.
    Best-effort: any internal error returns None and logs nothing
    (skill injection is observability, never enforcement).
    """
    try:
        from . import skill_context as _sc
    except Exception:  # noqa: BLE001 — module missing or broken
        return None
    try:
        block = _sc.build_skill_context_block(
            persona=persona,
            inject_skills=inject_skills,
        )
    except Exception:  # noqa: BLE001
        return None
    if not block:
        return None
    # Audit: surface count + chars only. Skill bodies / names NEVER
    # land in the chain (mirror of L23/L25/L28 metadata-only rule).
    if audit:
        try:
            count = _sc.count_skills_in_block(block)
            _emit_audit_skill_injected(
                engine=engine,
                persona=persona,
                skill_count=count,
                skill_chars=len(block),
            )
        except Exception:  # noqa: BLE001
            pass
    return block


def _wire_mcp_for_engine(
    *,
    engine: str,
    persona: str,
    tempdir: Path | None,
    working_dir: Path | None,
    forge_enabled: bool | None,
    skill_forge_enabled: bool | None,
    audit: bool,
) -> dict[str, Any]:
    """Materialise per-spawn MCP-server config for the worker engine.

    Returns a dict with two stable keys:
      * ``spawn_kwargs`` — kwargs to merge into ``engine.spawn(...)``
        (e.g. ``mcp_config_path`` for Claude Code).
      * ``env_overlay``  — env-vars to merge into the worker's env
        overlay (e.g. ``CODEX_HOME`` for Codex).

    Both default to empty dicts when MCP wiring is disabled or
    fails (best-effort; never blocks the spawn) - with ONE exception:
    ``mcp_config_builder.UnsafeConfigTarget`` propagates and refuses the
    delegation.
    """
    empty = {"spawn_kwargs": {}, "env_overlay": {}, "mcp_servers": []}
    if tempdir is None:
        # Without a tempdir we have nowhere to write the per-spawn config.
        # Caller passed hermetic=False AND no working_dir — degraded mode.
        return empty
    try:
        from . import mcp_config_builder as _mcb
    except Exception:  # noqa: BLE001
        return empty

    # Asymmetric resolution: env-floor wins when stricter (False).
    final_forge = _mcb.resolve_capability(
        env_floor=_mcb.env_floor_forge_enabled(),
        tool_arg=forge_enabled,
        persona_default=False,
    )
    final_skill = _mcb.resolve_capability(
        env_floor=_mcb.env_floor_skill_forge_enabled(),
        tool_arg=skill_forge_enabled,
        persona_default=False,
    )
    if not (final_forge or final_skill):
        return empty

    try:
        specs = _mcb.build_mcp_specs(
            persona=persona,
            forge_enabled=final_forge,
            skill_forge_enabled=final_skill,
        )
    except Exception:  # noqa: BLE001
        return empty
    if not specs:
        return empty

    try:
        result = _mcb.materialise_for_engine(
            engine_id=engine,
            specs=specs,
            tempdir=tempdir,
            working_dir=working_dir,
        )
    except _mcb.UnsafeConfigTarget:
        # NOT best-effort (review R2-A2A-12). The config target in the worker's
        # directory is a symlink, a hard link or not a plain file: somebody
        # planted it. Swallowing this started the worker in that directory
        # anyway - for OpenCode with the planted ``opencode.json`` as its
        # config, i.e. MCP servers of the planter's choosing. The delegation is
        # refused; run_delegate records it (delegate.failed, reason
        # "unsafe-config-target").
        raise
    except Exception:  # noqa: BLE001
        return empty

    if audit and result.get("mcp_servers"):
        try:
            _emit_audit_mcp_wired(
                engine=engine,
                persona=persona,
                mcp_servers=list(result["mcp_servers"]),
            )
        except Exception:  # noqa: BLE001
            pass
    return result


def _merge_env(
    base: dict[str, str] | None,
    extra: dict[str, str] | None,
) -> dict[str, str] | None:
    """Merge two env overlay dicts. Returns None when both are empty
    so the engine's spawn-side ``if env: spawn_env.update(env)`` shortcut
    stays a no-op for MCP-less spawns."""
    if not base and not extra:
        return None
    out: dict[str, str] = {}
    if base:
        out.update(base)
    if extra:
        out.update(extra)
    return out or None


def _emit_audit_skill_injected(**fields: Any) -> None:
    try:
        from .audit import emit_skill_injected
        emit_skill_injected(**fields)
    except Exception as exc:  # noqa: BLE001 — audit never blocks delegation, but a lost record is never silent
        _log.error("delegate audit record lost (%s): %s", 'emit_skill_injected', exc)


# ---------------------------------------------------------------------------
# ADR-0049 — session-pinning helpers
# ---------------------------------------------------------------------------


def _is_stale_session_error(error: str | None) -> bool:
    """True when the engine error indicates an expired --resume session."""
    return bool(error and "session not found" in error.lower())


def _emit_worker_session_audit_delegate(event_type: str, **fields: Any) -> None:
    """Best-effort audit emit for worker_session.* events from delegation path."""
    try:
        _ensure_agents_on_path()
        import sys as _sys
        import os as _os
        _bridge_shared = _AGENTS_DIR
        _bp = str(_bridge_shared)
        if _bp not in _sys.path:
            _sys.path.insert(0, _bp)
        from audit import audit_event  # type: ignore[import-not-found]
        audit_event(event_type, details=fields)
    except Exception:  # noqa: BLE001
        pass


def _save_worker_session_delegate(
    ws_dir: "Path",
    scope_label: str,
    session_id: str,
    persona_tag: str,
    was_resume: bool,
) -> None:
    """Persist session file and emit audit after a successful delegate spawn."""
    try:
        _ensure_agents_on_path()
        from worker_session_store import save_session, read_session_record  # type: ignore[import-not-found]
        save_session(ws_dir, scope_label, session_id, persona_tag)
        if was_resume:
            rec = read_session_record(ws_dir, scope_label)
            resume_count = rec.get("resume_count", 0) if rec else 0
            _emit_worker_session_audit_delegate(
                "worker_session.resumed",
                scope_label=scope_label, persona=persona_tag,
                resume_count=resume_count,
            )
        else:
            _emit_worker_session_audit_delegate(
                "worker_session.created",
                scope_label=scope_label, persona=persona_tag,
            )
    except Exception:  # noqa: BLE001
        pass


def _attested_model_of(spawn_result: Any) -> str:
    """The engine-reported model of a delegated run, ``""`` when none (ADR-0759)."""
    try:
        from agents import attested_model  # type: ignore  # noqa: PLC0415
        return attested_model(spawn_result)
    except Exception:  # noqa: BLE001
        return ""


def _extract_session_id_delegate(spawn_result: Any) -> str | None:
    """Extract claude session_id from SpawnResult events."""
    for ev in getattr(spawn_result, "events", []):
        if getattr(ev, "type", None) == "session_started":
            raw = getattr(ev, "raw", None) or {}
            sid = raw.get("session_id")
            if isinstance(sid, str) and sid.strip():
                return sid.strip()
    return None


def _emit_audit_mcp_wired(**fields: Any) -> None:
    try:
        from .audit import emit_mcp_wired
        emit_mcp_wired(**fields)
    except Exception as exc:  # noqa: BLE001 — audit never blocks delegation, but a lost record is never silent
        _log.error("delegate audit record lost (%s): %s", 'emit_mcp_wired', exc)
