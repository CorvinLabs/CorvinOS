"""dialectic.py — native LDD-style decision points for corvinOS (Layer 11).

Provides ``decide(...)`` for a small, curated set of decision SITES where
thesis/antithesis/synthesis adds real value. Cheap sites stay cheap (mode
``off`` or ``fast``); high-consequence sites can run in ``skill`` (the
calling Claude builds the synthesis itself, kostenneutral) or ``cli``
(spawned ``claude -p`` subprocess, Max-Abo).

Cost contract — IMPORTANT
-------------------------
This module MUST NOT import the Anthropic SDK. All LLM-mode work is
either a local Claude in ``skill`` mode (the synthesis is built in the
caller's own turn, nothing extra spawned) or a ``claude -p`` subprocess
in ``cli`` mode (authenticated via the user's Claude login → Max-Abo).
A repo-level CI lint rejects ``import anthropic`` in this file; do not
add it.

Heat-Score gate
---------------
For every potential decision a single value is computed:

    heat = 0.4 * consequence + 0.3 * uncertainty + 0.3 * (scope / 5)

When ``heat`` is below the per-site threshold, the call returns the
thesis as-is (no antithesis built, no audit overhead). Thresholds were
calibrated against 13 fictive tasks (see operator/bridges/shared/
test_dialectic_lib.py for the calibration table). Default 0.5 — except
``path_gate`` which uses 0.6 because false-positive denies are costly.

Components — definitions
- consequence: 0.1 reversible/local · 0.5 session-bound · 1.0 cross-session
- uncertainty: 1 - confidence (or 1 - (top_score - second_score) for
                categorical picks)
- scope:       1 (one decision) · 3 (session-bound) · 5 (user-scope,
                affects all future chats)

Configuration & toggle
----------------------
Settings live at ``<scope_root>/global/dialectic.json``, read fresh per
call (mtime-cached). Default-on. Five slash-commands toggle behaviour:
``/dialectic-on``, ``/dialectic-off``, ``/dialectic-status``,
``/dialectic-set <site> <mode>``, ``/dialectic-show``.

Per-chat override: ``chat_profile.dialectic_enabled = false`` disables
dialectic decisions for that one chat regardless of global state.
"""
from __future__ import annotations

import json
import os
import subprocess
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

# ── Audit hash chain (best-effort import) ──────────────────────────────────
#
# Mirror the optional-dep pattern used by skill_inject / cowork: we want
# the unified hash chain when forge is on the path, but we never break the
# bridge if it isn't.
_audit_writer: Callable[..., None] | None = None
try:
    import sys as _sys
    _HERE = Path(__file__).resolve().parent
    _FORGE_TOP = _HERE.parent.parent / "forge"
    if _FORGE_TOP.is_dir() and str(_FORGE_TOP) not in _sys.path:
        _sys.path.insert(0, str(_FORGE_TOP))
    from forge.security_events import write_event as _audit_writer  # type: ignore # noqa: E402
except Exception:  # noqa: BLE001
    _audit_writer = None

# Optional Layer-14 LDD-toggle library — when the dialectical_reasoning
# layer is off (globally or per-chat), every dialectic site degrades to
# mode=off. The import is best-effort; without ldd.py the gate is a no-op
# and dialectic behaves exactly as before Layer 14.
try:
    import ldd as _ldd  # type: ignore # noqa: E402
except Exception:  # noqa: BLE001
    _ldd = None  # type: ignore


# ── Site registry ──────────────────────────────────────────────────────────

# Per-site default mode + Heat-Score threshold.
# These were calibrated by running 13 fictive tasks through the score
# formula; see test_dialectic_lib.py for the table.
SITES: dict[str, dict[str, Any]] = {
    "skill_promotion": {"mode": "skill", "threshold": 0.5},
    "forge_creation":  {"mode": "skill", "threshold": 0.5},
    "auto_routing":    {"mode": "fast",  "threshold": 0.5},
    "path_gate":       {"mode": "fast",  "threshold": 0.6},
    "session_reset":   {"mode": "cli",   "threshold": 0.5},
    # voice_summary — faithfulness check on the read-aloud summary BEFORE
    # delivery. Default mode=off because it adds 5-15 s latency on every
    # voice reply (separate `claude -p` validation pass). User opts in via
    # `/dialectic-set voice_summary cli`. The site is consulted from
    # summarize.py:summarize() through judge_summary() — when off, that
    # call is a no-op so the wiring is harmless when disabled.
    "voice_summary":   {"mode": "off",   "threshold": 0.5},
}

VALID_MODES = ("off", "fast", "skill", "cli")


# ── Decision dataclass ─────────────────────────────────────────────────────

@dataclass
class Decision:
    """The audit-bare result of a (potentially) dialectic decision.

    For below-threshold or mode=off cases, ``antithesis`` is None and
    ``synthesis`` is just the thesis stringified. For mode=skill the
    ``synthesis`` field carries the placeholder marker that the caller
    is expected to fill in via the LLM turn (see ``skill_block_for``).
    """
    site: str
    choice: Any
    synthesis: str
    thesis: Any
    antithesis: Any
    why: str
    mode: str
    heat: float
    decision_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    ts: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ── Heat-Score formula ─────────────────────────────────────────────────────

def heat_score(consequence: float, uncertainty: float, scope: float) -> float:
    """Calibrated additive-weighted formula. See module docstring."""
    consequence = max(0.0, min(1.0, float(consequence)))
    uncertainty = max(0.0, min(1.0, float(uncertainty)))
    # scope is on a 1..5 ordinal — normalize to 0..1.
    scope_norm = max(0.0, min(1.0, float(scope) / 5.0))
    return 0.4 * consequence + 0.3 * uncertainty + 0.3 * scope_norm


# ── Configuration with mtime-based hot reload ──────────────────────────────

_CONFIG_LOCK = threading.RLock()
_CONFIG_CACHE: dict[str, Any] = {}
_CONFIG_MTIME: float = 0.0


def _config_path(*, tenant_id: str | None = None) -> Path:
    """Resolve <scope_root>/global/dialectic.json. Falls back to a per-user
    location when forge.paths is unimportable.

    ADR-0007 Phase 1.3: optional tenant_id kwarg places the config under
    <scope_root>/tenants/<tid>/global/dialectic.json. Default None
    preserves the legacy single-operator path.
    """
    middle = ("tenants", tenant_id, "global") if tenant_id else ("global",)
    try:
        from forge.paths import corvin_home  # type: ignore  # noqa: PLC0415
        return Path(corvin_home()).joinpath(*middle, "dialectic.json")
    except Exception:  # noqa: BLE001
        # Bridge environment without forge — fall back to repo-relative.
        env = os.environ.get("CORVIN_HOME") or os.environ.get("CORVIN_HOME")
        if env:
            return Path(env).joinpath(*middle, "dialectic.json")
        return Path.home().joinpath(".corvin", *middle, "dialectic.json")


def _default_config() -> dict[str, Any]:
    return {
        "enabled": True,
        "modes": {site: spec["mode"] for site, spec in SITES.items()},
        "thresholds": {site: spec["threshold"] for site, spec in SITES.items()},
        "show_in_reply": False,
        "telemetry": False,
    }


def load_config() -> dict[str, Any]:
    """Hot-reload aware config getter. Writes the default file on first
    call if missing."""
    global _CONFIG_CACHE, _CONFIG_MTIME
    p = _config_path()
    with _CONFIG_LOCK:
        if not p.exists():
            try:
                p.parent.mkdir(parents=True, exist_ok=True)
                cfg = _default_config()
                p.write_text(json.dumps(cfg, indent=2))
                _CONFIG_CACHE = cfg
                _CONFIG_MTIME = p.stat().st_mtime
                return dict(cfg)
            except OSError:
                # Read-only FS or sandboxing — return ephemeral defaults.
                return _default_config()
        try:
            mtime = p.stat().st_mtime
        except OSError:
            return dict(_CONFIG_CACHE) if _CONFIG_CACHE else _default_config()
        if not _CONFIG_CACHE or mtime != _CONFIG_MTIME:
            try:
                _CONFIG_CACHE = json.loads(p.read_text())
                _CONFIG_MTIME = mtime
            except (OSError, json.JSONDecodeError):
                # Corrupt config — fall back to defaults but DON'T overwrite
                # the on-disk file (the operator may want to fix it).
                _CONFIG_CACHE = _default_config()
        return dict(_CONFIG_CACHE)


def save_config(cfg: dict[str, Any]) -> None:
    """Atomic write + cache bust."""
    global _CONFIG_CACHE, _CONFIG_MTIME
    p = _config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    with _CONFIG_LOCK:
        tmp.write_text(json.dumps(cfg, indent=2))
        tmp.replace(p)
        _CONFIG_CACHE = dict(cfg)
        _CONFIG_MTIME = p.stat().st_mtime


# ── Mode resolution ────────────────────────────────────────────────────────

def resolve_mode(*, site: str, profile: dict | None = None,
                 explicit: str | None = None) -> str:
    """Determine the active mode for ``site``. Order of precedence:

    1. Explicit kwarg (test override).
    2. ``profile.dialectic_mode_<site>`` (per-chat override).
    3. ``profile.dialectic_enabled = False`` → ``off`` for all sites.
    4. ``cfg.enabled = False`` → ``off`` for all sites.
    5. ``cfg.modes[site]``.
    6. Bundle default (``SITES[site]["mode"]``).
    """
    if explicit and explicit in VALID_MODES:
        return explicit
    profile = profile or {}
    pm = profile.get(f"dialectic_mode_{site}")
    if isinstance(pm, str) and pm in VALID_MODES:
        return pm
    if profile.get("dialectic_enabled") is False:
        return "off"
    # Layer-14 master gate: when the dialectical_reasoning LDD layer is
    # off (globally OR for this profile), every site degrades to off.
    # Explicit per-site/per-profile dialectic modes already returned
    # above, so the LDD gate cannot silently override an explicit op-in.
    if _ldd is not None and not _ldd.is_layer_active(
        "dialectical_reasoning", profile=profile,
    ):
        return "off"
    cfg = load_config()
    if not cfg.get("enabled", True):
        return "off"
    mode = cfg.get("modes", {}).get(site)
    if isinstance(mode, str) and mode in VALID_MODES:
        return mode
    return SITES.get(site, {}).get("mode", "off")


def resolve_threshold(*, site: str, profile: dict | None = None) -> float:
    profile = profile or {}
    th = profile.get(f"dialectic_threshold_{site}")
    if isinstance(th, (int, float)):
        return float(th)
    cfg = load_config()
    th = cfg.get("thresholds", {}).get(site)
    if isinstance(th, (int, float)):
        return float(th)
    return float(SITES.get(site, {}).get("threshold", 0.5))


# ── Recursion-guard — max depth 1 ──────────────────────────────────────────

_DEPTH = threading.local()


def _enter() -> int:
    cur = getattr(_DEPTH, "value", 0)
    _DEPTH.value = cur + 1
    return cur


def _leave() -> None:
    _DEPTH.value = max(0, getattr(_DEPTH, "value", 0) - 1)


# ── Roadmap J — Rate-Limit für teure Modi ─────────────────────────────────
#
# Only ``skill`` and ``cli`` modes count against the limit. ``fast`` and
# ``off`` are sub-millisecond and never need throttling. The window is a
# 60-second sliding list of timestamps per site, kept in-memory (resets at
# bridge restart) and protected by a single lock — sites are independent.
#
# Default budgets are conservative; the operator can override via
# ``cfg.rate_limits[<site>]`` (calls-per-60s int) without touching code.
_RATE_WINDOW_SECONDS = 60.0
_RATE_DEFAULTS: dict[str, int] = {
    "skill_promotion": 6,
    "forge_creation":  6,
    "auto_routing":    12,
    "path_gate":       30,
    "session_reset":   6,
}
_RATE_LOCK = threading.Lock()
_RATE_RECENT: dict[str, list[float]] = {site: [] for site in SITES}


def _rate_limit_for(site: str) -> int:
    cfg = load_config()
    limits = cfg.get("rate_limits") or {}
    val = limits.get(site)
    if isinstance(val, int) and val >= 0:
        return val
    return _RATE_DEFAULTS.get(site, 12)


def _rate_limit_check(site: str, *, now: float | None = None) -> bool:
    """Return True when the call is within budget. Records the timestamp
    on success; the bookkeeping is ATOMIC under the lock so concurrent
    callers cannot both slip in over the boundary."""
    if site not in _RATE_RECENT:
        return True
    cap = _rate_limit_for(site)
    if cap <= 0:  # 0 = explicit "disabled by operator" → always permit
        return True
    t = now if now is not None else time.time()
    cutoff = t - _RATE_WINDOW_SECONDS
    with _RATE_LOCK:
        recent = _RATE_RECENT[site]
        # Drop expired timestamps in-place — bounded list, O(window-size).
        while recent and recent[0] < cutoff:
            recent.pop(0)
        if len(recent) >= cap:
            return False
        recent.append(t)
        return True


def _rate_limit_reset() -> None:
    """Drop every site's timestamp list. Used by tests; safe to call any
    time, the only effect is the next call gets a full window again."""
    with _RATE_LOCK:
        for site in _RATE_RECENT:
            _RATE_RECENT[site] = []


# ── Skill mode: prompt-block builder ───────────────────────────────────────

def skill_block_for(*, site: str, thesis: Any, antithesis: Any) -> str:
    """Return the markup the caller embeds into its system-prompt /
    next-step reasoning. The local Claude is then expected to author
    the synthesis as part of its normal output."""
    return (
        f'<dialectic site="{site}">\n'
        f'  <thesis>{_json_safe(thesis)}</thesis>\n'
        f'  <antithesis>{_json_safe(antithesis)}</antithesis>\n'
        f'  <synthesis>{{fill in the chosen action and a one-sentence why}}</synthesis>\n'
        f'</dialectic>'
    )


def _json_safe(v: Any) -> str:
    if isinstance(v, str):
        return v
    try:
        return json.dumps(v, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(v)


# ── CLI mode: claude -p sub-subprocess ─────────────────────────────────────

_CLI_TIMEOUT_SECONDS = 15.0


def _claude_authenticated() -> bool:
    """Cheap, subprocess-free Claude Code auth probe — the ungated sibling of
    the 41c174e summarize.py gate. Without it, a fresh install with `claude` on
    PATH but not yet logged in burns the full judge timeout (15–20 s) on EVERY
    research/forge voice summary before falling back. Mirrors
    summarize.py::_claude_authenticated (ANTHROPIC_API_KEY or an OAuth session
    in ~/.claude/.credentials.json). Fail-OPEN on read error so a transient
    glitch never disables a genuinely-logged-in user's judge.
    """
    import os as _os
    from pathlib import Path as _Path
    if _os.environ.get("ANTHROPIC_API_KEY"):
        return True
    try:
        creds_path = _Path.home() / ".claude" / ".credentials.json"
        if not creds_path.exists():
            return False
        import json as _json
        creds = _json.loads(creds_path.read_text(encoding="utf-8"))
        return bool(creds.get("claudeAiOauth") or creds.get("accessToken"))
    except Exception:  # noqa: BLE001
        return True  # fail-open: don't disable a possibly-authenticated judge


def _run_cli_judge(*, site: str, thesis: Any, antithesis: Any) -> str:
    """Spawn ``claude -p --max-turns 1 --no-tools`` to author a one-line
    synthesis. Returns the choice string. On timeout / failure, returns a
    deterministic fallback so the calling site never blocks indefinitely."""
    prompt = (
        f"Site: {site}\n"
        f"Thesis (option A): {_json_safe(thesis)}\n"
        f"Antithesis (option B): {_json_safe(antithesis)}\n\n"
        "Pick the better option. Reply with EXACTLY one line in the form:\n"
        "  A | B | <one-sentence why>\n"
        "Use A if the thesis wins, B if the antithesis wins. Be terse."
    )
    # Auth short-circuit (F3): avoid burning the full CLI timeout when `claude`
    # is present but not logged in. Binary ABSENCE is handled by the spawn's
    # FileNotFoundError catch below — do NOT gate on which("claude") here, which
    # false-negatives under a stripped PATH (engine-binary-resolution guard).
    if not _claude_authenticated():
        return "A | (cli unavailable, falling back to thesis)"
    try:
        from . import helper_model as _hm  # type: ignore
    except ImportError:
        try:
            import helper_model as _hm  # type: ignore
        except ImportError:
            _hm = None
    model_args = _hm.claude_args(_hm.SITE_DIALECTIC_CLI) if _hm else []
    _bin = _hm.resolve_claude_bin() if _hm else "claude"
    try:
        proc = subprocess.run(
            [_bin, "-p", "--max-turns", "1", "--tools", "",
             *model_args,
             "--output-format", "text", prompt],
            capture_output=True, text=True, timeout=_CLI_TIMEOUT_SECONDS,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return "A | (cli unavailable, falling back to thesis)"
    out = (proc.stdout or "").strip()
    if not out:
        return "A | (cli returned empty, falling back to thesis)"
    return out.splitlines()[-1].strip()


# ── voice_summary site: faithfulness judge ─────────────────────────────────
#
# The generic A|B prompt in `_run_cli_judge` doesn't fit a faithfulness check
# (there's no real "option B" — the antithesis is whatever the judge spots
# as a faithfulness defect). This judge has its own prompt shape and
# returns a verdict tag plus the final text.

# Cap on the source text we embed in the judge prompt — keeps the CLI
# round-trip fast for long assistant replies. The head+tail strategy
# preserves the faithfulness signal at both ends; truncation in the middle
# is the load-bearing pragmatic limit (a 50 KB diff dump cannot be
# fully judged in a 15 s budget anyway).
_SUMMARY_JUDGE_SRC_CAP = 4000
_SUMMARY_JUDGE_TIMEOUT_SECONDS = 20.0


def _truncate_for_judge(text: str, cap: int = _SUMMARY_JUDGE_SRC_CAP) -> str:
    if len(text) <= cap:
        return text
    head = text[: cap * 2 // 3]
    tail = text[-(cap // 3):]
    return f"{head}\n\n[…truncated for judge — {len(text) - cap} chars omitted…]\n\n{tail}"


def _summary_judge_prompt(source: str, candidate: str, lang: str) -> str:
    src_capped = _truncate_for_judge(source)
    return (
        "You are a faithfulness checker for a voice-summary that will be "
        "read aloud to a user. Your job is to decide whether the candidate "
        "summary accurately represents the source.\n\n"
        f"SOURCE (the original assistant reply, language={lang}):\n"
        f"<<<\n{src_capped}\n>>>\n\n"
        f"CANDIDATE SUMMARY (language={lang}):\n"
        f"<<<\n{candidate}\n>>>\n\n"
        "Check whether the summary is FAITHFUL to the source:\n"
        "- Does it omit any load-bearing fact (numbers, names, decisions, "
        "deadlines, error messages)?\n"
        "- Does it invent any fact that is not in the source?\n"
        "- Does it misrepresent the source's stance or recommendation?\n\n"
        "Reply with EXACTLY ONE LINE in one of these forms:\n"
        f"  FAITHFUL | <one short {lang} sentence saying why it's faithful>\n"
        f"  CORRECTED | <revised summary, in {lang}, on the SAME line, no newlines>\n\n"
        "Rules:\n"
        "- The whole response must be ONE line. No markdown. No code fences.\n"
        f"- Pick CORRECTED only if there is a clear faithfulness defect; the "
        "revised summary must keep the same length budget as the candidate "
        "and the same speaking-style.\n"
        "- Pick FAITHFUL when the candidate is acceptable — minor stylistic "
        "differences are NOT a faithfulness defect.\n"
    )


def _run_summary_judge(source: str, candidate: str, lang: str) -> str:
    """Spawn ``claude -p --max-turns 1 --no-tools`` with the faithfulness
    prompt. Returns the raw single-line verdict. On timeout / failure,
    returns a deterministic ``FAITHFUL | (cli unavailable)`` so the caller
    never blocks indefinitely AND defaults to shipping the candidate."""
    prompt = _summary_judge_prompt(source, candidate, lang)
    # Auth short-circuit (F3): see _run_cli_judge. Binary absence is caught at
    # the spawn; do not gate on which("claude") (stripped-PATH false-negative).
    if not _claude_authenticated():
        return "FAITHFUL | (cli unavailable, shipping candidate)"
    try:
        from . import helper_model as _hm  # type: ignore
    except ImportError:
        try:
            import helper_model as _hm  # type: ignore
        except ImportError:
            _hm = None
    model_args = _hm.claude_args(_hm.SITE_DIALECTIC_SUMMARY_JUDGE) if _hm else []
    _bin = _hm.resolve_claude_bin() if _hm else "claude"
    try:
        proc = subprocess.run(
            [_bin, "-p", "--max-turns", "1", "--tools", "",
             *model_args,
             "--output-format", "text", prompt],
            capture_output=True, text=True,
            timeout=_SUMMARY_JUDGE_TIMEOUT_SECONDS,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return "FAITHFUL | (cli unavailable, shipping candidate)"
    out = (proc.stdout or "").strip()
    if not out:
        return "FAITHFUL | (cli returned empty, shipping candidate)"
    return out.splitlines()[-1].strip()


def judge_summary(*,
                  source: str,
                  candidate: str,
                  lang: str = "de",
                  persona: str = "",
                  channel_id: str = "",
                  mode: str | None = None) -> tuple[str, str, str]:
    """Run the dialectic faithfulness check on a voice summary.

    Returns ``(final_text, verdict, why)`` where:
      * ``final_text`` is either the candidate (verdict == "faithful" or
        skipped) or a corrected version (verdict == "corrected").
      * ``verdict`` ∈ ``{"faithful", "corrected", "skipped",
        "rate-limited", "below-threshold"}``.
      * ``why`` is the judge's one-sentence rationale (empty when skipped).

    When the site mode resolves to ``off``, this is a zero-cost no-op:
    the candidate is returned unchanged and no CLI is spawned. That's the
    default state — the operator must opt in via
    ``/dialectic-set voice_summary cli`` for the check to fire.

    The audit chain receives a ``decision.dialectical`` event with the
    site-specific synthesis line for traceability.
    """
    site = "voice_summary"

    # Resolve active mode (op-in via /dialectic-set or chat profile).
    active = resolve_mode(site=site, explicit=mode)
    if active == "off":
        return candidate, "skipped", ""
    if active == "fast":
        # No fast synthesizer registered — fast falls through as skipped
        # (the only real mode that does work here is cli; faithfulness
        # cannot be settled by deterministic local rules).
        return candidate, "skipped", "fast-mode-no-synth"

    # Recursion + rate-limit gates: piggyback on decide()'s machinery so
    # the same `/dialectic-set voice_summary off` AND
    # `cfg.rate_limits.voice_summary` knobs apply.
    depth = _enter()
    try:
        if depth >= 1:
            return candidate, "skipped", "recursion-guard"
        if active in ("skill", "cli") and not _rate_limit_check(site):
            cap = _rate_limit_for(site)
            if _audit_writer is not None:
                ap = _audit_chain_path()
                if ap is not None:
                    try:
                        _audit_writer(
                            ap, "dialectic.rate_limited", tool=site,
                            details={
                                "site": site, "active_mode": active,
                                "cap": cap,
                                "window_s": int(_RATE_WINDOW_SECONDS),
                                "persona": persona, "channel_id": channel_id,
                            },
                        )
                    except Exception:  # noqa: BLE001
                        pass
            return candidate, "rate-limited", f"rate-limit cap={cap}"

        if active == "skill":
            # Skill mode for voice_summary is not currently implemented:
            # the summarizer's own LLM call could in principle fold the
            # check inline, but that fundamentally changes the system-
            # prompt shape. For now skill degrades to skip.
            return candidate, "skipped", "skill-not-implemented"

        if active != "cli":
            return candidate, "skipped", f"unknown-mode={active!r}"

        line = _run_summary_judge(source, candidate, lang)
        verdict_token = ""
        rest = ""
        if "|" in line:
            head, _, rest = line.partition("|")
            verdict_token = head.strip().upper()
            rest = rest.strip()
        else:
            verdict_token = line.strip().upper()

        if verdict_token == "CORRECTED" and rest:
            final_text = rest
            verdict = "corrected"
            why = "judge replaced candidate with corrected text"
        else:
            # Default-faithful on any unparseable / empty / FAITHFUL output —
            # the candidate ships. This is the safe path: a confused judge
            # never silently mangles the user's voice reply.
            final_text = candidate
            verdict = "faithful"
            why = rest or "no-defect-found"

        d = Decision(
            site=site, choice=verdict, synthesis=line,
            thesis=candidate, antithesis=None,
            why=why, mode="cli", heat=1.0,
        )
        _audit(d, persona=persona, channel_id=channel_id)
        return final_text, verdict, why
    finally:
        _leave()


# ── Fast-mode synthesizer registry ─────────────────────────────────────────
#
# Each entry returns (choice, synthesis_str, why). Fast synthesizers must
# be deterministic and fast (< 1 ms). Sites without a registered fast
# synthesizer fall back to the thesis when mode resolves to ``fast``.
_FAST_SYNTHS: dict[str, Callable[..., tuple[Any, str, str]]] = {}


def register_fast_synth(site: str):
    def deco(fn: Callable[..., tuple[Any, str, str]]):
        _FAST_SYNTHS[site] = fn
        return fn
    return deco


# Built-in fast synthesizers — deterministic rules per site. These are
# intentionally simple; richer synthesis lives in mode=skill / mode=cli.

@register_fast_synth("auto_routing")
def _fast_auto_routing(thesis, antithesis, ctx):
    # thesis/antithesis carry (persona, confidence). Highest confidence
    # wins; ties go to thesis (persona-router's first pick).
    t_conf = (thesis or {}).get("confidence", 0.0) if isinstance(thesis, dict) else 0.0
    a_conf = (antithesis or {}).get("confidence", 0.0) if isinstance(antithesis, dict) else 0.0
    if a_conf > t_conf:
        return antithesis, _json_safe(antithesis), "antithesis-confidence-higher"
    return thesis, _json_safe(thesis), "thesis-wins-or-tie"


@register_fast_synth("path_gate")
def _fast_path_gate(thesis, antithesis, ctx):
    # path-gate is fail-closed: thesis ("deny") always wins. Antithesis
    # exists only to record the false-positive risk in the audit trail.
    return thesis, _json_safe(thesis), "fail-closed: deny stands"


# ── Audit ──────────────────────────────────────────────────────────────────

def _audit_chain_path() -> Path | None:
    """Resolve the unified audit chain path. Returns None when forge.paths
    is unimportable — caller treats that as "no audit available, skip"."""
    try:
        from forge.paths import corvin_home  # type: ignore  # noqa: PLC0415
        return Path(corvin_home()) / "global" / "forge" / "audit.jsonl"
    except Exception:  # noqa: BLE001
        env = os.environ.get("CORVIN_HOME") or os.environ.get("CORVIN_HOME")
        if env:
            return Path(env) / "global" / "forge" / "audit.jsonl"
        return None


def _emit_oversight_override(
    *,
    site: str,
    profile: dict,
    explicit: str | None,
    persona: str,
    channel_id: str,
) -> None:
    """Emit human_oversight.override when mode=off is an operator-level
    override of a site whose bundle default is NOT off.

    Conditions that do NOT qualify (not an operator override):
    - The site's bundle default is already "off" (voice_summary default)
    - The override came from the LDD layer-gate (system-level feature flag)
    - The override came from the test-explicit kwarg (test harness)
    """
    if explicit is not None:
        return  # test-explicit, not human override
    site_default = SITES.get(site, {}).get("mode", "off")
    if site_default == "off":
        return  # default is already off — nothing was overridden
    # Check if it's the LDD gate (system-level, not operator)
    if _ldd is not None and not _ldd.is_layer_active(
        "dialectical_reasoning", profile=profile,
    ):
        return  # LDD layer gate, not an operator-level human override
    # Determine the override source for the audit record
    if profile.get(f"dialectic_mode_{site}") == "off":
        source = "profile_site"
    elif profile.get("dialectic_enabled") is False:
        source = "profile_global"
    else:
        cfg = load_config()
        if not cfg.get("enabled", True):
            source = "config_global"
        elif cfg.get("modes", {}).get(site) == "off":
            source = "config_site"
        else:
            return  # couldn't determine source — do not emit (avoid false positives)
    if _audit_writer is None:
        return
    ap = _audit_chain_path()
    if ap is None:
        return
    try:
        _audit_writer(
            ap,
            "human_oversight.override",
            details={
                "site":            site,
                "override_source": source,
                "persona":         persona,
                "channel_id":      channel_id,
            },
        )
    except Exception:  # noqa: BLE001
        pass  # audit-write failures must never break the calling site


def _audit(decision: Decision, *, persona: str = "", channel_id: str = "") -> None:
    """Best-effort write of a ``decision.dialectical`` event into the
    unified hash chain. Never raises — audit is observability, not
    enforcement."""
    if _audit_writer is None:
        return
    audit_path = _audit_chain_path()
    if audit_path is None:
        return
    try:
        _audit_writer(
            audit_path,
            "decision.dialectical",
            tool=decision.site,
            details={
                "decision_id": decision.decision_id,
                "site":        decision.site,
                "mode":        decision.mode,
                "heat":        decision.heat,
                "choice":      _json_safe(decision.choice)[:200],
                "synthesis":   decision.synthesis[:300],
                "why":         decision.why[:200],
                "persona":     persona,
                "channel_id":  channel_id,
            },
        )
    except Exception:  # noqa: BLE001
        # Audit-write failures must not break the calling site.
        pass


# ── Public API: decide() ───────────────────────────────────────────────────

def decide(*,
           site: str,
           thesis: Any,
           antithesis: Any = None,
           ctx: dict | None = None,
           profile: dict | None = None,
           mode: str | None = None,
           heat: float | None = None,
           consequence: float | None = None,
           uncertainty: float | None = None,
           scope: float | None = None,
           persona: str = "",
           channel_id: str = "") -> Decision:
    """Run a dialectic decision at ``site``.

    The Heat-Score gate filters trivial decisions out: when
    ``heat < threshold`` the call returns the thesis as-is, mode=off,
    no antithesis built, no audit overhead.

    Mode lookup: explicit kwarg → profile override → cfg → bundle default.

    Recursion guard: max depth 1 — a fast/skill synthesizer that calls
    ``decide()`` again degrades the inner call to ``off``.
    """
    if site not in SITES:
        raise ValueError(f"unknown dialectic site: {site!r}")
    ctx = ctx or {}

    # Recursion guard.
    depth = _enter()
    try:
        # Heat-Score gate: derive heat if not passed explicitly.
        if heat is None:
            if (consequence is not None
                    and uncertainty is not None
                    and scope is not None):
                heat = heat_score(consequence, uncertainty, scope)
            else:
                heat = 1.0  # caller did not provide → run as if always-on
        threshold = resolve_threshold(site=site, profile=profile)

        # Below threshold → thesis-only short-circuit.
        if heat < threshold:
            d = Decision(
                site=site, choice=thesis, synthesis=_json_safe(thesis),
                thesis=thesis, antithesis=None,
                why=f"below-threshold (heat={heat:.2f} < {threshold:.2f})",
                mode="off", heat=heat,
            )
            cfg = load_config()
            if cfg.get("telemetry"):
                _audit(d, persona=persona, channel_id=channel_id)
            return d

        # Recursion ≥ 1 → degrade to off (no nested judges).
        if depth >= 1:
            d = Decision(
                site=site, choice=thesis, synthesis=_json_safe(thesis),
                thesis=thesis, antithesis=antithesis,
                why="recursion-guard: depth>=1 → off",
                mode="off", heat=heat,
            )
            return d

        # Resolve mode.
        active = resolve_mode(site=site, profile=profile, explicit=mode)

        if active == "off":
            d = Decision(
                site=site, choice=thesis, synthesis=_json_safe(thesis),
                thesis=thesis, antithesis=None,
                why="mode=off (toggle)", mode="off", heat=heat,
            )
            _audit(d, persona=persona, channel_id=channel_id)
            # EU AI Act Art. 14 — emit human_oversight.override when an
            # operator explicitly disabled AI deliberation for a site whose
            # bundle default is NOT off (i.e., it was actively overridden,
            # not just left at its default). Best-effort.
            _emit_oversight_override(
                site=site, profile=profile or {}, explicit=mode,
                persona=persona, channel_id=channel_id,
            )
            return d

        if active == "fast":
            synth = _FAST_SYNTHS.get(site)
            if synth is None:
                # No registered fast-rule — same semantics as off but
                # tagged so the audit knows fast was picked.
                d = Decision(
                    site=site, choice=thesis, synthesis=_json_safe(thesis),
                    thesis=thesis, antithesis=antithesis,
                    why="fast: no synthesizer registered",
                    mode="fast", heat=heat,
                )
            else:
                choice, synthesis, why = synth(thesis, antithesis, ctx)
                d = Decision(
                    site=site, choice=choice, synthesis=synthesis,
                    thesis=thesis, antithesis=antithesis,
                    why=why, mode="fast", heat=heat,
                )
            _audit(d, persona=persona, channel_id=channel_id)
            return d

        # Roadmap J — Rate-Limit gate. Only the expensive modes (skill,
        # cli) count against the per-site sliding-window budget. When the
        # window is full, degrade to thesis-only / mode=off and emit a
        # `dialectic.rate_limited` audit event so operators can see the
        # throttle is firing without blocking the call site.
        if active in ("skill", "cli") and not _rate_limit_check(site):
            cap = _rate_limit_for(site)
            d = Decision(
                site=site, choice=thesis, synthesis=_json_safe(thesis),
                thesis=thesis, antithesis=antithesis,
                why=(f"rate-limit: {site} exceeded {cap} calls / "
                     f"{int(_RATE_WINDOW_SECONDS)}s — degraded to thesis"),
                mode="off", heat=heat,
            )
            # Out-of-line audit event so operators can correlate throttles
            # with site activity bursts. We reuse _audit_writer directly
            # rather than _audit() so the event_type is distinct from
            # `decision.dialectical`.
            if _audit_writer is not None:
                ap = _audit_chain_path()
                if ap is not None:
                    try:
                        _audit_writer(
                            ap,
                            "dialectic.rate_limited",
                            tool=site,
                            details={
                                "site":         site,
                                "active_mode":  active,
                                "cap":          cap,
                                "window_s":     int(_RATE_WINDOW_SECONDS),
                                "persona":      persona,
                                "channel_id":   channel_id,
                            },
                        )
                    except Exception:  # noqa: BLE001
                        pass
            return d

        if active == "skill":
            block = skill_block_for(site=site, thesis=thesis,
                                    antithesis=antithesis)
            d = Decision(
                site=site, choice="<DIALECTIC_PLACEHOLDER>",
                synthesis=block,
                thesis=thesis, antithesis=antithesis,
                why="skill: caller's Claude completes the synthesis in its turn",
                mode="skill", heat=heat,
            )
            _audit(d, persona=persona, channel_id=channel_id)
            return d

        if active == "cli":
            line = _run_cli_judge(site=site, thesis=thesis,
                                  antithesis=antithesis)
            # Parse "A | B | <why>" — first token decides.
            parts = [p.strip() for p in line.split("|", 2)]
            choice = thesis
            why = "cli: parse-fallback"
            if parts and parts[0].upper().startswith("B"):
                choice = antithesis
            if len(parts) >= 3:
                why = f"cli: {parts[2]}"
            elif len(parts) >= 2:
                why = f"cli: {parts[1]}"
            d = Decision(
                site=site, choice=choice, synthesis=line,
                thesis=thesis, antithesis=antithesis,
                why=why, mode="cli", heat=heat,
            )
            _audit(d, persona=persona, channel_id=channel_id)
            return d

        # Fall-through (shouldn't happen given VALID_MODES gate).
        return Decision(
            site=site, choice=thesis, synthesis=_json_safe(thesis),
            thesis=thesis, antithesis=antithesis,
            why=f"unknown-mode={active!r} → thesis fallback",
            mode="off", heat=heat,
        )
    finally:
        _leave()


# ── Reply-footer helper (consumed by adapter) ──────────────────────────────

# ── CLI entrypoint (used by slash-commands) ────────────────────────────────


def _cli_status() -> int:
    cfg = load_config()
    print(f"dialectic: enabled={cfg.get('enabled', True)}  "
          f"show_in_reply={cfg.get('show_in_reply', False)}  "
          f"telemetry={cfg.get('telemetry', False)}")
    print("modes:")
    for site, default in SITES.items():
        active = cfg.get("modes", {}).get(site, default["mode"])
        threshold = cfg.get("thresholds", {}).get(site, default["threshold"])
        print(f"  {site:18s} mode={active:5s} threshold={threshold:.2f}")
    print(f"\nconfig: {_config_path()}")
    return 0


def _cli_on() -> int:
    cfg = load_config()
    cfg["enabled"] = True
    save_config(cfg)
    print("dialectic: ENABLED globally (default-on state)")
    return 0


def _cli_off() -> int:
    cfg = load_config()
    cfg["enabled"] = False
    save_config(cfg)
    print("dialectic: DISABLED globally — all sites now mode=off")
    return 0


def _cli_show(arg: str = "") -> int:
    cfg = load_config()
    if arg.lower() in ("on", "true", "1", "yes"):
        cfg["show_in_reply"] = True
    elif arg.lower() in ("off", "false", "0", "no"):
        cfg["show_in_reply"] = False
    else:
        # Toggle.
        cfg["show_in_reply"] = not cfg.get("show_in_reply", False)
    save_config(cfg)
    print(f"dialectic: show_in_reply={cfg['show_in_reply']}")
    return 0


def _cli_set(site: str, mode: str) -> int:
    if site not in SITES:
        print(f"unknown site: {site!r}")
        print(f"valid: {sorted(SITES.keys())}")
        return 1
    if mode not in VALID_MODES:
        print(f"unknown mode: {mode!r}")
        print(f"valid: {list(VALID_MODES)}")
        return 1
    cfg = load_config()
    cfg.setdefault("modes", {})[site] = mode
    save_config(cfg)
    print(f"dialectic: {site} -> {mode}")
    return 0


def _cli_main(argv: list[str]) -> int:
    if not argv or argv[0] in ("status", "-h", "--help", "help"):
        return _cli_status()
    sub = argv[0].lower()
    if sub == "on":
        return _cli_on()
    if sub == "off":
        return _cli_off()
    if sub == "show":
        return _cli_show(argv[1] if len(argv) > 1 else "")
    if sub == "set":
        if len(argv) < 3:
            print("usage: dialectic.py set <site> <mode>")
            return 1
        return _cli_set(argv[1], argv[2])
    print(f"unknown command: {sub!r}")
    return _cli_status()


# ── Reply-footer helper (consumed by adapter) ──────────────────────────────


def format_reply_footer(decisions: list[Decision]) -> str:
    """Render the optional ``[decision: ...]`` reply footer when
    ``cfg.show_in_reply`` is true. Returns "" when no decisions or
    show-toggle is off."""
    if not decisions:
        return ""
    cfg = load_config()
    if not cfg.get("show_in_reply"):
        return ""
    parts = []
    for d in decisions:
        # Keep each line short — voice/TTS path strips this anyway, but
        # the text channel renders it.
        parts.append(
            f"[decision/{d.site} mode={d.mode} heat={d.heat:.2f} "
            f"choice={_json_safe(d.choice)[:60]} — {d.why}]"
        )
    return "\n\n" + "\n".join(parts)


if __name__ == "__main__":
    import sys as _sys
    raise SystemExit(_cli_main(_sys.argv[1:]))
