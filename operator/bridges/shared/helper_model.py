"""Resolve which Claude model to use for OS-side helper subprocesses.

Layer-29.5 cost-split: the bridge OS-turn + real engineering work keep
the user's default Claude model (Opus / Sonnet). The "around-the-task"
helpers — voice summaries, dialectic judges, user-style learner,
user-model distiller, delegate output-judge, router auto-mode — flip
to Haiku, which is cheaper and fast enough for these short, narrow
prompts.

Resolution order per call:

1. ``CORVIN_HELPER_MODEL_<SITE_UPPER>`` env (per-site pin)
2. ``CORVIN_HELPER_MODEL`` env (global helper default)
3. ``DEFAULT_HELPER_MODEL`` (``claude-haiku-4-5-20251001``)

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

DEFAULT_HELPER_MODEL: Final[str] = "claude-haiku-4-5-20251001"

# Known install locations probed when the CLI is not on PATH. The dominant
# failure mode is the adapter running under systemd with a stripped PATH that
# lacks ``~/.local/bin`` (where Claude Code installs the CLI) — a bare
# ``"claude"`` spawn then raises FileNotFoundError. The WorkerEngine path
# already survives this via ``agents.claude_code._resolve_claude_bin``; this
# list mirrors it so EVERY helper ``claude -p`` spawn survives the same
# environment. Override with ``CORVIN_CLAUDE_BIN_FALLBACKS=p1:p2:…``.
_CLAUDE_BIN_FALLBACKS: Final[tuple[str, ...]] = (
    "~/.local/bin/claude",
    "/usr/local/bin/claude",
    "/usr/bin/claude",
    "/opt/homebrew/bin/claude",
)


def resolve_claude_bin() -> str:
    """Resolve the claude CLI path for helper ``claude -p`` subprocess spawns.

    Resolution order — ``CORVIN_CLAUDE_BIN`` (explicit pin) → ``PATH``
    (``shutil.which``) → known-location fallbacks → bare ``"claude"`` (lets
    ``Popen`` raise a clear ``FileNotFoundError``).

    Helper spawns must NOT rely on the bare name alone: the bridge runs under
    systemd with a stripped PATH, so a bare ``"claude"`` raises
    ``FileNotFoundError`` — and for the fail-closed L44 house-rules gate that
    error escalated EVERY request to operator approval. Kept dependency-free
    here (``helper_model`` must not import the engine) but semantically the
    same resolver the WorkerEngine uses. Best-effort + never raises."""
    pinned = os.environ.get("CORVIN_CLAUDE_BIN", "").strip()
    if pinned and (os.sep in pinned or "/" in pinned):
        return pinned  # explicit absolute/relative pin — honour as-is
    name = pinned or "claude"
    try:
        found = shutil.which(name)
        if found:
            return found
        extra = os.environ.get("CORVIN_CLAUDE_BIN_FALLBACKS", "")
        candidates = tuple(p for p in extra.split(os.pathsep) if p) + _CLAUDE_BIN_FALLBACKS
        for cand in candidates:
            expanded = os.path.expanduser(cand)
            if os.path.isfile(expanded) and os.access(expanded, os.X_OK):
                return expanded
    except Exception:  # noqa: BLE001 — resolution is best-effort; fall back to the name
        pass
    return name


def is_hermes_available() -> bool:
    """Check if Hermes (Ollama fallback engine) is reachable.

    Used before degrading from Claude to Hermes to ensure the fallback
    actually works. Never raises — best-effort check only."""
    try:
        import urllib.request
        with urllib.request.urlopen("http://localhost:11434/api/tags", timeout=2.0) as resp:
            return getattr(resp, "status", 200) == 200
    except Exception:  # noqa: BLE001
        return False

# Curated site identifiers. Each helper passes one of these as ``site``;
# unknown sites fall through the resolver harmlessly (return default).
SITE_VOICE_SUMMARY: Final[str] = "voice_summary"
SITE_DIALECTIC_CLI: Final[str] = "dialectic_cli"
SITE_DIALECTIC_SUMMARY_JUDGE: Final[str] = "dialectic_summary_judge"
SITE_USER_STYLE_JUDGE: Final[str] = "user_style_judge"
SITE_USER_MODEL_DISTILL: Final[str] = "user_model_distill"
SITE_DELEGATE_OUTPUT_JUDGE: Final[str] = "delegate_output_judge"
SITE_ROUTER_CLI: Final[str] = "router_cli"
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

ALL_SITES: Final[tuple[str, ...]] = (
    SITE_VOICE_SUMMARY,
    SITE_DIALECTIC_CLI,
    SITE_DIALECTIC_SUMMARY_JUDGE,
    SITE_USER_STYLE_JUDGE,
    SITE_USER_MODEL_DISTILL,
    SITE_DELEGATE_OUTPUT_JUDGE,
    SITE_ROUTER_CLI,
    SITE_OS_TURN,
    SITE_HOUSE_RULES,
    SITE_ACS_CLASSIFY,
    SITE_ULO_COMPLIANCE,
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

    Resolution: per-site env > global env > built-in default.
    """
    per_site = os.environ.get(_per_site_env_var(site))
    if per_site is not None:
        return None if per_site.strip().lower() in _OPT_OUT_VALUES else per_site.strip()
    glob = os.environ.get("CORVIN_HELPER_MODEL")
    if glob is not None:
        return None if glob.strip().lower() in _OPT_OUT_VALUES else glob.strip()
    return DEFAULT_HELPER_MODEL


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
