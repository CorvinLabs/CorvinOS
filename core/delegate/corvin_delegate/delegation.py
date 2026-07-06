"""Delegation core — wraps the WorkerEngine layer behind a single call.

Public API:

    result = run_delegate(
        engine="codex_cli" | "opencode" | "claude_code",
        prompt="...",
        model=None,                 # optional, engine-specific (e.g. "ollama/qwen3:8b")
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

# The WorkerEngine layer lives in the voice plugin's shared/agents/ tree.
# We resolve it lazily so this package stays importable even when the
# voice plugin is absent (e.g. CI runs that only need the delegation
# library or its MCP-server surface).
_PLUGIN_ROOT = Path(__file__).resolve().parents[2]
_AGENTS_DIR = _PLUGIN_ROOT / "voice" / "bridges" / "shared"


BUDGET_DEFAULT_S = 60
BUDGET_MIN_S = 10
BUDGET_MAX_S = 600

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
_INJECTION_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    # "ignore" / "disregard" — allow up to 3 filler words between the verb
    # and the target (covers "ignore the previous", "disregard all earlier").
    ("ignore_previous",      re.compile(r"\bignore\b(?:\W+\w+){0,3}\W+(?:previous|prior|earlier|above|instructions|rules|commands|directives|everything)\b", re.IGNORECASE)),
    ("disregard",            re.compile(r"\bdisregard\b(?:\W+\w+){0,3}\W+(?:previous|prior|earlier|above|instructions|rules|commands|directives|everything)\b", re.IGNORECASE)),
    ("system_tag_inject",    re.compile(r"<\s*/?\s*(?:SYSTEM|sys|system_prompt)\s*>", re.IGNORECASE)),
    ("role_switch",          re.compile(r"^\s*(?:assistant|user|system)\s*:", re.IGNORECASE | re.MULTILINE)),
    ("new_instructions",     re.compile(r"\b(?:new|updated|revised)\s+instructions?\s*:", re.IGNORECASE)),
    ("forget_everything",    re.compile(r"\bforget\b(?:\W+\w+){0,3}\W+(?:everything|all|previous|prior|instructions)\b", re.IGNORECASE)),
)
_INJECTION_SCAN_HEAD_CHARS = 8_192  # only scan first chunk; tail rarely carries injections

AVAILABLE_ENGINES: tuple[str, ...] = ("claude_code", "codex_cli", "opencode", "hermes", "copilot")


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
    if engine_id == "hermes":
        from agents.hermes_engine import HermesEngine  # type: ignore
        return HermesEngine()
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
            return {"extra_args": ["--sandbox", "workspace-write"]}
        return {}
    if engine_id == "hermes":
        # HermesEngine drives Ollama HTTP — no subprocess permission modes
        # and no filesystem writes. allow_write is a no-op for this engine.
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
    # common providers (Ollama Cloud, Anthropic, OpenAI). Custom
    # providers (e.g. OpenRouter) pass their key via env_extra.
    "opencode":    frozenset({
        "OLLAMA_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY",
    }),
    # HermesEngine resolves base_url and model at __init__ time (before
    # env scrubbing kicks in), so no API key or runtime env vars are
    # needed during spawn(). Ollama runs on localhost — no auth required.
    "hermes":      frozenset(),
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


def _env_allowlist_for(engine_id: str) -> frozenset[str]:
    """Compose the env-var allowlist for a given engine."""
    return _BASE_ENV_ALLOWLIST | _ENGINE_ENV_ADDITIONS.get(engine_id, frozenset())


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


def _clamp_budget(budget_s: Any) -> int:
    try:
        b = int(budget_s)
    except (TypeError, ValueError):
        b = BUDGET_DEFAULT_S
    if b < BUDGET_MIN_S:
        return BUDGET_MIN_S
    if b > BUDGET_MAX_S:
        return BUDGET_MAX_S
    return b


def _validate_working_dir(working_dir: Any) -> Path | None:
    if working_dir is None:
        return None
    if not isinstance(working_dir, (str, Path)):
        raise DelegateError("working_dir must be a string, Path, or None")
    p = Path(working_dir)
    if not p.is_absolute():
        raise DelegateError(f"working_dir must be absolute: {working_dir!r}")
    return p


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
    if engine not in AVAILABLE_ENGINES:
        raise DelegateError(
            f"unknown engine: {engine!r}; expected one of {AVAILABLE_ENGINES}"
        )
    prompt = _validate_prompt(prompt)
    model = _validate_model(model)
    budget = _clamp_budget(budget_s)
    cwd = _validate_working_dir(working_dir)
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
            _eng_op = str(Path(__file__).resolve().parents[3] / "operator")
            if _eng_op not in sys.path:
                sys.path.insert(0, _eng_op)
            from license.validator import assert_limit as _eng_assert  # type: ignore
            from license.limits import LicenseLimitError as _eng_lic_err  # type: ignore
            _eng_assert("engines_allowed", engine)
        except Exception as _eng_exc:  # noqa: BLE001 — fail-CLOSED on any license error
            _denied = _eng_lic_err is not None and isinstance(_eng_exc, _eng_lic_err)
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
    # Layer 29.5 — emit sandbox lifecycle audit BEFORE the env-scrub
    # context manager starts, so CORVIN_HOME is still in the env when
    # the audit writer resolves the chain path. Decision-only audit
    # (no spawn-side state needed at this point).
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
        elif sandbox_decision == "bwrap":
            _emit_audit_sandboxed(
                engine=engine,
                persona=persona_tag,
                mode=final_sandbox_mode,
                decision="bwrap",
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
        env_cm = _scrubbed_environ(_env_allowlist_for(engine))

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
            if sandbox_decision == "bwrap" and effective_cwd is not None:
                argv_prefix = _sandbox.build_bwrap_args(
                    engine_id=engine,
                    hermetic_dir=Path(effective_cwd),
                    allow_net=True,
                )
                spawn_kwargs["argv_prefix"] = argv_prefix
            # ADR-0049 — pass resume_session_id when session is pinned.
            if _pin_can and _pin_resume_sid:
                spawn_kwargs["resume_session_id"] = _pin_resume_sid
            events = worker.spawn(prompt, **spawn_kwargs)
            spawn_result = collect(events)

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

    if spawn_result.error:
        if audit:
            _emit_audit_failed(
                engine=engine,
                persona=persona_tag,
                reason="engine-error",
                duration_ms=duration_ms,
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

_FORGE_PATH_FOR_WDAT = str(Path(__file__).resolve().parents[3] / "operator" / "forge")


def _write_wdat_run_for_delegation(
    *,
    engine: str,
    model: str | None,
    duration_ms: int,
    start_wall: float,
    prompt: str,
    usage: dict,
) -> None:
    """Write an ACS-compatible run directory + L16 audit events for a delegation run.

    Best-effort — never raises. Called from run_delegate() after a successful
    spawn so the WDAT Audit panel in the console can render the delegation graph.

    Requires CORVIN_SESSION_DIR to be set in os.environ (chat_runtime.py injects
    it for each web-console subprocess). Without it this function is a no-op so
    CLI / Discord delegate calls are unaffected.

    Audit events written to the per-tenant L16 chain (metadata only per GDPR Art.5):
      acs.manager_decided  — one synthetic manager decision
      acs.worker_spawned   — the single worker (this engine)
      acs.engine_completed — timing/token metadata
      acs.worker_traced    — final status
    """
    session_dir_str = os.environ.get("CORVIN_SESSION_DIR", "").strip()
    if not session_dir_str:
        return

    session_dir = Path(session_dir_str)
    if not session_dir.is_absolute():
        return

    tenant_id = (os.environ.get("CORVIN_TENANT_ID") or "_default").strip() or "_default"
    corvin_home_str = os.environ.get("CORVIN_HOME", "").strip()
    if corvin_home_str:
        corvin_home = Path(corvin_home_str)
    else:
        try:
            if _FORGE_PATH_FOR_WDAT not in sys.path:
                sys.path.insert(0, _FORGE_PATH_FOR_WDAT)
            from forge.paths import corvin_home as _ch_fn  # type: ignore
            corvin_home = Path(str(_ch_fn()))
        except Exception:  # noqa: BLE001
            return

    run_id = f"acs-dlg-{int(start_wall)}-{secrets.token_hex(3)}"
    worker_id = "w0"
    spawn_nonce = secrets.token_hex(4)
    instruction_hash = hashlib.sha256(prompt.encode()).hexdigest()[:16]
    duration_s = duration_ms / 1000.0
    model_id = model or engine
    tokens_in  = int(usage.get("input_tokens",  usage.get("tokens_in",  0)) or 0)
    tokens_out = int(usage.get("output_tokens", usage.get("tokens_out", 0)) or 0)
    tokens_used = tokens_in + tokens_out

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
            "status":          "completed",
            "workers_spawned": 1,
            "iterations":      1,
            "elapsed_s":       round(duration_s, 3),
        }
        (run_dir / "result.json").write_text(
            json.dumps(result, ensure_ascii=False), encoding="utf-8",
        )
    except OSError:
        pass  # best-effort; run still shows as active in the panel

    # 3. Audit events → per-tenant L16 chain
    audit_path = corvin_home / "tenants" / tenant_id / "global" / "audit.jsonl"
    try:
        if _FORGE_PATH_FOR_WDAT not in sys.path:
            sys.path.insert(0, _FORGE_PATH_FOR_WDAT)
        from forge import security_events as _sec  # type: ignore

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
            "tokens_used": tokens_used,
        })
        _sec.write_event(audit_path, "acs.worker_traced", details={
            "run_id":     run_id,
            "worker_id":  worker_id,
            "status":     "completed",
            "duration_ms": duration_ms,
            "engine_attestation": {"engine": engine, "model_id": model_id},
        })
        # ADR-0171 — universal engine span (role=worker) on the SAME chain + IDs
        # as the acs.* events, so the console renders the delegation engine
        # engine-agnostically (any engine_id), not only via acs.* heuristics.
        try:
            _shared_dir = str(Path(__file__).resolve().parents[3]
                              / "operator" / "bridges" / "shared")
            if _shared_dir not in sys.path:
                sys.path.insert(0, _shared_dir)
            import engine_span as _espan  # type: ignore
            _span_id = f"spn-{run_id}-{worker_id}"
            _sec.write_event(audit_path, _espan.ENGINE_SPAN_START,
                             details=_espan.start_details(
                                 span_id=_span_id, role="worker",
                                 engine_id=engine, model_id=model_id or "",
                                 run_id=run_id))
            _sec.write_event(audit_path, _espan.ENGINE_SPAN_END,
                             details=_espan.end_details(
                                 span_id=_span_id, role="worker",
                                 engine_id=engine, model_id=model_id or "",
                                 run_id=run_id, status="ok",
                                 duration_ms=duration_ms, tokens_used=tokens_used))
        except Exception:  # noqa: BLE001
            pass
    except Exception:  # noqa: BLE001 — audit is observability, never enforcement
        pass


# ---------------------------------------------------------------------------
# Audit shim (lazy import to keep core module zero-dep)
# ---------------------------------------------------------------------------


def _emit_audit_invoked(**fields: Any) -> None:
    try:
        from .audit import emit_invoked
        emit_invoked(**fields)
    except Exception:  # noqa: BLE001 — audit is observability, never enforcement
        pass


def _emit_audit_completed(**fields: Any) -> None:
    try:
        from .audit import emit_completed
        emit_completed(**fields)
    except Exception:  # noqa: BLE001
        pass


def _emit_audit_failed(**fields: Any) -> None:
    try:
        from .audit import emit_failed
        emit_failed(**fields)
    except Exception:  # noqa: BLE001
        pass


def _emit_audit_output_judged(**fields: Any) -> None:
    try:
        from .audit import emit_output_judged
        emit_output_judged(**fields)
    except Exception:  # noqa: BLE001
        pass


def _emit_audit_engine_policy_denied(**fields: Any) -> None:
    try:
        from .audit import emit_engine_policy_denied
        emit_engine_policy_denied(**fields)
    except Exception:  # noqa: BLE001
        pass


def _emit_audit_zone_policy_denied(**fields: Any) -> None:
    try:
        from .audit import emit_zone_policy_denied
        emit_zone_policy_denied(**fields)
    except Exception:  # noqa: BLE001
        pass


def _emit_audit_sandboxed(**fields: Any) -> None:
    try:
        from .audit import emit_sandboxed
        emit_sandboxed(**fields)
    except Exception:  # noqa: BLE001
        pass


def _emit_audit_sandbox_unavailable(**fields: Any) -> None:
    try:
        from .audit import emit_sandbox_unavailable
        emit_sandbox_unavailable(**fields)
    except Exception:  # noqa: BLE001
        pass


def _emit_audit_prompt_classified(**fields: Any) -> None:
    try:
        from .audit import emit_prompt_classified
        emit_prompt_classified(**fields)
    except Exception:  # noqa: BLE001
        pass


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
    the verdict drives whether we proceed or block. Fail-safe: on
    classifier_error the delegation proceeds (with WARNING audit so
    the operator notices silent classifier-down conditions).
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

    blocked = (
        final_mode == "blocking"
        and result.verdict == "refuse"
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
            error="prompt-safety-refused: classifier marked outbound prompt as REFUSE",
            model=model,
            allow_write=allow_write,
        )
    return None


# ---------------------------------------------------------------------------
# Layer 29.4a — Tenant-Policy + Engine-Zone gate
# ---------------------------------------------------------------------------


def _check_data_classification(
    *,
    engine: str,
    prompt: str,
    model: str | None,
    persona_tag: str,
) -> "DelegateResult | None":
    """Layer 34 — data-classification × engine-egress gate for delegation.

    Closes the bypass found in the compliance review: the delegation path
    enforced only the orthogonal L29.4a tenant-zone gate, NOT L34. A
    SECRET/CONFIDENTIAL prompt delegated to a cloud engine was ungated on
    the sensitivity axis. Mirrors the adapter OS-turn gate exactly
    (classify_task → DataFlowGuard.validate), which emits its own
    ``data_flow.approved``/``blocked`` audit event. Fail-open only when the
    module is genuinely unavailable (adapter / tenant-policy parity).
    """
    try:
        from data_classification import load_guard_for_tenant, classify_task  # type: ignore
    except Exception:  # noqa: BLE001 — module not on path → no enforcement
        return None
    tenant_id = os.environ.get("CORVIN_TENANT_ID") or "_default"
    try:
        # Opt-in (adapter parity): no tenant data_classification config → allow.
        guard = load_guard_for_tenant(tenant_id)
        if guard is None:
            return None
        classification = classify_task(prompt or "", persona=persona_tag or None)
        decision = guard.validate(
            classification=classification,
            engine_id=engine,
            persona=persona_tag or None,
        )
    except Exception:  # noqa: BLE001 — internal error → fail-open (adapter parity)
        return None
    if decision.allowed:
        return None
    return DelegateResult(
        ok=False,
        engine=engine,
        error=(f"data-flow-denied: classification {classification.name} is not "
               f"allowed for engine {engine!r}: {decision.reason}")[:400],
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
    fails (best-effort; never blocks the spawn).
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
    except Exception:  # noqa: BLE001
        pass


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
        _bridge_shared = Path(__file__).resolve().parents[2] / "voice" / "bridges" / "shared"
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
    except Exception:  # noqa: BLE001
        pass
