"""Resolve which Claude model to use for OS-side helper subprocesses.

Layer-29.5 cost-split: the bridge OS-turn + real engineering work keep
the user's default Claude model (Opus / Sonnet). The "around-the-task"
helpers — voice summaries, dialectic judges, user-style learner,
user-model distiller, delegate output-judge, router auto-mode — flip
to Haiku, which is cheaper and fast enough for these short, narrow
prompts.

Resolution order per call:

0. an opt-out value in the env (``none``/``off``/empty) -> no ``--model``
1. the operator's Console choice for the ``corvinOS`` row (Models page)
2. ``CORVIN_HELPER_MODEL_<SITE_UPPER>`` env (per-site pin)
3. ``CORVIN_HELPER_MODEL`` env (global helper default)
4. :func:`default_helper_model` — the NEWEST available Haiku (ADR-2090 lineage);
   ``DEFAULT_HELPER_MODEL`` (``claude-haiku-4-5-20251001``) only when the
   registry offers none

Opt-out: setting the env value to ``""`` or ``"none"`` returns ``None``,
which causes ``claude_args(...)`` to emit no ``--model`` flag — the
helper then falls through to the CLI's own default model (whatever
the user's subscription resolves to). Operator escape hatch when a
specific helper is judged too weak on Haiku and needs to ride the
default.

The module is dependency-free (stdlib only) and MUST NOT
``import anthropic`` — same cost contract as ``dialectic.py``.
"""

from __future__ import annotations

import os
import shutil
import sys
import threading
from typing import Final

#: Fallback ONLY — used when the model registry cannot be read. The model a
#: helper actually gets is :func:`default_helper_model` (newest Haiku).
DEFAULT_HELPER_MODEL: Final[str] = "claude-haiku-4-5-20251001"


def default_helper_model(tenant_id: str | None = None) -> str:
    """The model a helper gets when nothing pins it: the operator's Console
    choice for the ``corvinOS`` row, else the NEWEST available Haiku
    (ADR-2090 lineage), else the curated fallback. Never raises: a helper must
    not fail because the registry or the config is unreadable."""
    try:
        try:
            from console_selection import helper_model as _console  # type: ignore  # noqa: PLC0415
        except ImportError:
            from .console_selection import helper_model as _console  # type: ignore  # noqa: PLC0415
        chosen = _console(tenant_id)
        if chosen:
            return chosen
    except Exception:  # noqa: BLE001
        pass
    try:
        try:
            from model_lineage import latest  # type: ignore  # noqa: PLC0415
        except ImportError:
            from .model_lineage import latest  # type: ignore  # noqa: PLC0415
        return latest("haiku") or DEFAULT_HELPER_MODEL
    except Exception:  # noqa: BLE001
        return DEFAULT_HELPER_MODEL


# Known install locations probed when the CLI is not on PATH. The dominant
# failure mode is a host running under systemd / launchd with a stripped PATH
# that lacks ``~/.local/bin`` (where the native Claude Code installer puts the
# CLI) — a bare ``"claude"`` spawn then raises FileNotFoundError, and the
# console web chat told the operator "the `claude` CLI was not found" on a
# machine where it is installed and works in every shell. Covers every
# supported install method: native installer (``~/.local/bin``), the legacy
# local install (``~/.claude/local``), npm with a user prefix / Volta / bun /
# pnpm, Homebrew, system packages and snap. nvm installs are version-specific
# and resolved dynamically (``_nvm_bin_dirs``). Extend with
# ``CORVIN_CLAUDE_BIN_FALLBACKS=p1:p2:…`` (os.pathsep-separated).
# ``agents.claude_code`` imports ``claude_bin_candidates()`` — ONE list.
_CLAUDE_BIN_FALLBACKS: Final[tuple[str, ...]] = (
    "~/.local/bin/claude",
    "~/.claude/local/claude",
    "~/.npm-global/bin/claude",
    "~/.volta/bin/claude",
    "~/.bun/bin/claude",
    "~/.local/share/pnpm/claude",
    "~/Library/pnpm/claude",
    "/usr/local/bin/claude",
    "/usr/bin/claude",
    "/opt/homebrew/bin/claude",
    "/snap/bin/claude",
)


def _nvm_bin_dirs() -> tuple[str, ...]:
    """``bin`` dirs of nvm-managed node versions, newest first. An npm global
    install under nvm lands in ``~/.nvm/versions/node/<ver>/bin`` — never on a
    service PATH, and version-specific, so it cannot be a literal fallback.
    Best-effort; never raises."""
    try:
        root = os.path.join(
            os.environ.get("NVM_DIR") or os.path.expanduser("~/.nvm"),
            "versions", "node",
        )
        if not os.path.isdir(root):
            return ()

        def _key(v: str) -> tuple[int, ...]:
            return tuple(int(p) if p.isdigit() else 0 for p in v.lstrip("v").split("."))

        versions = sorted(os.listdir(root), key=_key, reverse=True)
        return tuple(os.path.join(root, v, "bin") for v in versions)
    except Exception:  # noqa: BLE001
        return ()


def _windows_bin_fallbacks() -> tuple[str, ...]:
    """Windows-only fallback locations. %APPDATA%/%USERPROFILE% are per-user
    and not expressible as a ``~``-relative literal, so these are resolved
    from env vars rather than hardcoded."""
    candidates: list[str] = []
    appdata = os.environ.get("APPDATA")
    if appdata:
        candidates.append(os.path.join(appdata, "npm", "claude.cmd"))
        candidates.append(os.path.join(appdata, "npm", "claude.exe"))
    userprofile = os.environ.get("USERPROFILE")
    if userprofile:
        candidates.append(os.path.join(userprofile, ".local", "bin", "claude.exe"))
    return tuple(candidates)


def claude_bin_candidates() -> tuple[str, ...]:
    """Every off-PATH location probed for the claude CLI, in priority order:
    operator-supplied ``CORVIN_CLAUDE_BIN_FALLBACKS`` → Windows shims →
    static install locations → nvm version dirs (newest first). Entries may
    carry a leading ``~``; callers ``expanduser`` them."""
    extra = os.environ.get("CORVIN_CLAUDE_BIN_FALLBACKS", "")
    base: tuple[str, ...] = _CLAUDE_BIN_FALLBACKS
    if sys.platform.startswith("win"):
        base = _windows_bin_fallbacks() + base
    nvm = tuple(os.path.join(d, "claude") for d in _nvm_bin_dirs())
    return tuple(p for p in extra.split(os.pathsep) if p) + base + nvm


def _is_executable_file(path: str) -> bool:
    return os.path.isfile(path) and os.access(path, os.X_OK)


def resolve_claude_bin() -> str:
    """Resolve the claude CLI path for every ``claude`` subprocess spawn.

    Resolution order — ``CORVIN_CLAUDE_BIN`` (explicit pin) → ``PATH``
    (``shutil.which``) → ``claude_bin_candidates()`` → bare ``"claude"``
    (lets ``Popen`` raise a clear ``FileNotFoundError``).

    Spawns must NOT rely on the bare name alone: the bridge and the console
    run under systemd with a stripped PATH, so a bare ``"claude"`` raises
    ``FileNotFoundError`` — for the fail-closed L44 house-rules gate that
    escalated EVERY request to operator approval, and the console web chat
    reported the CLI missing on a machine where it is installed. Kept
    dependency-free (``helper_model`` must not import the engine); the
    WorkerEngine resolver shares ``claude_bin_candidates()``. Best-effort +
    never raises."""
    pinned = os.environ.get("CORVIN_CLAUDE_BIN", "").strip()
    if pinned and (os.sep in pinned or "/" in pinned):
        return pinned  # explicit absolute/relative pin — honour as-is
    name = pinned or "claude"
    try:
        found = shutil.which(name)
        if found:
            return found
        for cand in claude_bin_candidates():
            expanded = os.path.expanduser(cand)
            if _is_executable_file(expanded):
                return expanded
    except Exception:  # noqa: BLE001 — resolution is best-effort; fall back to the name
        pass
    return name


def harden_path() -> list[str]:
    """PATH repair for a service process: APPEND every existing install dir
    from ``claude_bin_candidates()`` that holds the ``claude`` CLI and is
    missing from ``PATH`` — the whole dir, so an npm-installed ``claude``
    also finds the ``node`` its ``#!/usr/bin/env node`` shebang needs (nvm,
    Volta and Homebrew keep both side by side). Appending, never prepending, so
    nothing the operator put on PATH is shadowed. Makes every bare
    ``shutil.which("claude")`` probe in the process (engine detection,
    dashboard status, healers, workflows) agree with ``resolve_claude_bin``
    without patching each call site. Idempotent; returns the dirs it added;
    never raises."""
    added: list[str] = []
    try:
        parts = [p for p in os.environ.get("PATH", "").split(os.pathsep) if p]
        seen = {os.path.normcase(os.path.normpath(p)) for p in parts}
        for cand in claude_bin_candidates():
            expanded = os.path.expanduser(cand)
            d = os.path.dirname(expanded)
            if not d or not _is_executable_file(expanded):
                continue
            key = os.path.normcase(os.path.normpath(d))
            if key in seen:
                continue
            seen.add(key)
            parts.append(d)
            added.append(d)
        if added:
            os.environ["PATH"] = os.pathsep.join(parts)
    except Exception:  # noqa: BLE001 — PATH repair is best-effort
        return []
    return added


# Curated site identifiers. Each helper passes one of these as ``site``;
# unknown sites fall through the resolver harmlessly (return default).
SITE_VOICE_SUMMARY: Final[str] = "voice_summary"
SITE_DIALECTIC_CLI: Final[str] = "dialectic_cli"
SITE_DIALECTIC_SUMMARY_JUDGE: Final[str] = "dialectic_summary_judge"
SITE_USER_STYLE_JUDGE: Final[str] = "user_style_judge"
SITE_USER_MODEL_DISTILL: Final[str] = "user_model_distill"
SITE_DELEGATE_OUTPUT_JUDGE: Final[str] = "delegate_output_judge"
# L44 (ADR-0143) — house-rules acceptable-use adjudicator. Decides whether a
# Tier-0 keyword hit is a genuine violation vs a false-positive / allowed
# exception. Haiku by default (cheap, only runs on a pattern hit).
SITE_HOUSE_RULES: Final[str] = "house_rules_adjudicator"

# Phase 2 — OS-turn site. The adapter consults this when the active
# persona declares `helper_model_default: true` AND no explicit
# `model:` field is set on persona / chat_profile. Used by the
# orchestrator-haiku persona to flip the bridge OS-turn from the
# subscription default (Opus / Sonnet) to Haiku, while delegated
# worker engines stay on their own model selection.
SITE_OS_TURN: Final[str] = "os_turn"

# ADR-0155 — ACS-X Autonomous Command Selector Extended. LLM fallback
# classifier fires only when heuristic confidence < 0.7. Haiku by default
# (cheap, single-call, no tools).
SITE_ACS_CLASSIFY: Final[str] = "acs_classify"

# ADR-0163 M2 — ULO post-turn compliance checker. Haiku by default
# (cheap, per-objective single call, no tools).
SITE_ULO_COMPLIANCE: Final[str] = "ulo_compliance"

# ADR-0210 Phase 1 / ADR-0214 — unified InitialAnalysis (classify + extract +
# plan in ONE call). Haiku by default (structured JSON, no tools).
SITE_INITIAL_ANALYSIS: Final[str] = "initial_analysis"

# ADR-0214 — TDE delegated-step worker (SubprocessWorkerIPC). Executes one
# L34-sanitized plan step as a tool-less one-shot. Haiku by default; pin a
# stronger model via CORVIN_HELPER_MODEL_TDE_WORKER when steps need it.
SITE_TDE_WORKER: Final[str] = "tde_worker"

ALL_SITES: Final[tuple[str, ...]] = (
    SITE_VOICE_SUMMARY,
    SITE_DIALECTIC_CLI,
    SITE_DIALECTIC_SUMMARY_JUDGE,
    SITE_USER_STYLE_JUDGE,
    SITE_USER_MODEL_DISTILL,
    SITE_DELEGATE_OUTPUT_JUDGE,
    SITE_OS_TURN,
    SITE_HOUSE_RULES,
    SITE_ACS_CLASSIFY,
    SITE_ULO_COMPLIANCE,
    SITE_INITIAL_ANALYSIS,
    SITE_TDE_WORKER,
)

_OPT_OUT_VALUES: Final[frozenset[str]] = frozenset({"", "none", "default", "off"})

_log_lock = threading.Lock()
_logged_sites: set[tuple[str, str | None]] = set()


def _per_site_env_var(site: str) -> str:
    """Map ``voice_summary`` → ``CORVIN_HELPER_MODEL_VOICE_SUMMARY``."""
    return "CORVIN_HELPER_MODEL_" + site.upper()


def resolve_helper_model(site: str) -> str | None:
    """Return the Claude model string for this helper site, or ``None``
    when the operator opted out (then the caller emits no ``--model``).

    Resolution: env opt-out > Console ``corvinOS`` choice > per-site env >
    global env > newest Haiku. The Console outranks a model-valued env pin -
    "the models selected in the Console are always the ones used" - while an
    explicit opt-out value (``none``/``off``/empty) stays the operator's escape
    hatch to the CLI default.
    """
    per_site = os.environ.get(_per_site_env_var(site))
    env_val = per_site if per_site is not None else os.environ.get("CORVIN_HELPER_MODEL")
    if env_val is not None and env_val.strip().lower() in _OPT_OUT_VALUES:
        return None
    try:
        try:
            from console_selection import helper_model as _console  # type: ignore  # noqa: PLC0415
        except ImportError:
            from .console_selection import helper_model as _console  # type: ignore  # noqa: PLC0415
        chosen = _console()
        if chosen:
            return chosen
    except Exception:  # noqa: BLE001
        pass
    if env_val is not None:
        return env_val.strip()
    return default_helper_model()


def env_only_helper_model(site: str) -> str | None:
    """The per-site environment model and nothing else, or ``None`` (ADR-2308).

    Unlike :func:`resolve_helper_model` this never falls back to the Console
    ``corvinOS`` row or to the newest Haiku: a site that is not configured must
    leave its caller's behaviour exactly as it was. The global
    ``CORVIN_HELPER_MODEL`` is not read either. An unset variable and every
    opt-out value (``""``, ``none``, ``default``, ``off``) count as "not set".
    A ``provider/`` prefix is stripped the way a pin is."""
    raw = os.environ.get(_per_site_env_var(site))
    if raw is None or raw.strip().lower() in _OPT_OUT_VALUES:
        return None
    value = raw.strip()
    try:
        try:
            from model_selector import normalise_pin as _norm  # type: ignore  # noqa: PLC0415
        except ImportError:
            from .model_selector import normalise_pin as _norm  # type: ignore  # noqa: PLC0415
        return _norm(value, "claude_code") or value
    except Exception:  # noqa: BLE001 - an unnormalised value beats no value
        return value


def claude_args(site: str) -> list[str]:
    """Return ``["--model", <model>]`` for argv composition, or ``[]``
    when the operator opted this site out (no model flag added).

    Side effect: the first call per (site, model) writes one stderr
    line so an operator inspecting bridge logs sees which model each
    helper is on. Idempotent + best-effort — never raises."""
    model = resolve_helper_model(site)
    announce(site)
    if model is None:
        return []
    return ["--model", model]


def announce(site: str, *, stream=sys.stderr) -> None:
    """Once-per-process stderr line for forensics. Idempotent per
    (site, model). Best-effort — never raises."""
    try:
        model = resolve_helper_model(site)
        key = (site, model)
        with _log_lock:
            if key in _logged_sites:
                return
            _logged_sites.add(key)
        stream.write(f"[helper_model] site={site} model={model or '<cli-default>'}\n")
        try:
            stream.flush()
        except Exception:  # noqa: BLE001
            pass
    except Exception:  # noqa: BLE001
        pass


# ── test hook ───────────────────────────────────────────────────────────────

def _reset_for_tests() -> None:
    """Clear the once-per-process announce cache. Test-only."""
    with _log_lock:
        _logged_sites.clear()
