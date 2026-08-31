#!/usr/bin/env python3
"""web_trust_gate.py — PreToolUse hook that classifies the source of every
``WebFetch`` / ``WebSearch`` call and surfaces the classification as
*additional context* on the model's stream. This is the structural
foundation of the **Quiet Dialectic Layer (QDL)**: no allowlist, no
blocklist, no gating — every URL works. What changes is the contextual
hint the LLM sees *before* it consumes the tool result, which lets a
persona (e.g. ``jarvis``) modulate its language without ever naming the
mechanism.

Three behaviours, one hook:

  1. **Classify**      — extract URL(s) from ``tool_input``, look the
                         domain up in ``source_trust_data.json``, fall
                         back on TLD heuristics for unknown domains.
  2. **Inject**        — emit a JSON object on stdout with
                         ``hookSpecificOutput.additionalContext`` so the
                         model sees a short tier-aware briefing alongside
                         the tool result. The block is in English (the
                         model translates as needed).
  3. **Audit**         — write a ``web.source_classified`` event to the
                         unified hash-chain at
                         ``<corvin_home>/global/forge/audit.jsonl`` with
                         the tool, URL, tier, and reason. Operator can
                         review patterns via ``voice-audit verify`` /
                         ``--last`` afterwards.

Hook protocol:

  - stdin:  JSON ``{tool_name, tool_input, ...}``
  - stdout: JSON ``{"hookSpecificOutput": {"hookEventName": "PreToolUse",
                     "additionalContext": "..."}}`` — *or empty* on
            silent allow.
  - exit 0: always (this hook never denies; QDL is a speech-modulator,
            not a gate).

Match-set (configured in hooks.json):

  - ``WebFetch`` (single ``url`` in tool_input)
  - ``WebSearch`` (a ``query`` plus optional ``allowed_domains`` /
                    ``blocked_domains``; classification of *the search
                    intent*, not yet of the result-URLs)

Why ``additionalContext`` over a chat-message: keeps the hook silent on
the user's read path. The user never sees the tier text; only the model
does.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.parse
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA_FILE = HERE / "source_trust_data.json"

# Cache the trust data + its mtime so the hook can hot-reload edits
# without restart but skip the disk read on every invocation when the
# file hasn't changed.
_cache: dict = {"mtime": 0.0, "data": None}


def _load_trust_data() -> dict:
    try:
        st = DATA_FILE.stat()
    except OSError:
        return {}
    if _cache["mtime"] == st.st_mtime and _cache["data"] is not None:
        return _cache["data"]
    try:
        data = json.loads(DATA_FILE.read_text("utf-8"))
    except (OSError, json.JSONDecodeError):
        return _cache["data"] or {}
    _cache["mtime"] = st.st_mtime
    _cache["data"] = data
    return data


def _domain_of(url: str) -> str:
    """Return the normalised host of an http(s) URL, lower-cased, with
    leading ``www.`` / ``amp.`` / ``m.`` stripped. Empty string for
    non-http URLs (file://, data:, mailto:, etc.).
    """
    if not isinstance(url, str) or "://" not in url:
        return ""
    try:
        parsed = urllib.parse.urlparse(url)
    except ValueError:
        return ""
    if parsed.scheme.lower() not in ("http", "https"):
        return ""
    host = (parsed.hostname or "").lower()
    for prefix in ("www.", "amp.", "m."):
        if host.startswith(prefix):
            host = host[len(prefix):]
            break
    return host


def classify_source(url: str) -> dict:
    """Return ``{tier, domain, reason, source}`` for a URL.

    Tier resolution order:
      1. exact host match on the static table
      2. suffix match (``foo.bbc.co.uk`` → ``bbc.co.uk``)
      3. TLD-hint match (``.gov``, ``.edu``, ``.europa.eu``, ...)
      4. fallback: tier=``unknown``, reason=``no entry``

    ``source`` records *which* lookup tier matched (``exact``, ``suffix``,
    ``tld``, ``unknown``) — useful for audit + future tuning.
    """
    domain = _domain_of(url)
    if not domain:
        return {"tier": "unknown", "domain": "", "reason": "non-http url",
                "source": "skipped"}
    data = _load_trust_data()
    domains = data.get("domains") or {}
    tld_hints = data.get("tld_hints") or {}

    if domain in domains:
        e = domains[domain]
        return {"tier": e.get("tier", "unknown"), "domain": domain,
                "reason": e.get("note", ""), "source": "exact"}

    # Suffix match — `foo.bbc.co.uk` should match `bbc.co.uk`.
    parts = domain.split(".")
    for i in range(1, len(parts)):
        candidate = ".".join(parts[i:])
        if candidate in domains:
            e = domains[candidate]
            return {"tier": e.get("tier", "unknown"), "domain": domain,
                    "reason": e.get("note", "") + f" (matched suffix {candidate})",
                    "source": "suffix"}

    # TLD-hint — ".gov", ".edu", ".gov.uk", ".europa.eu" ...
    for tld, e in tld_hints.items():
        if domain.endswith(tld):
            return {"tier": e.get("tier", "unknown"), "domain": domain,
                    "reason": e.get("note", "") + f" (TLD {tld})",
                    "source": "tld"}

    return {"tier": "unknown", "domain": domain,
            "reason": "no entry in tier table",
            "source": "unknown"}


# Tier → guidance line embedded in the additionalContext block. Each
# guidance is an instruction *to the model* about how to attribute the
# source when speaking. None of these texts ever surface to the user
# verbatim — they shape the LLM's wording.
_TIER_GUIDANCE = {
    "green": (
        "Quality-tier source. Direct attribution by name is fine "
        "('Reuters reports that ...'). Treat reported facts as reliable "
        "until contradicted; still differentiate fact from analysis."
    ),
    "wiki": (
        "Wikipedia / sister project. Strong on established facts, weaker "
        "on current events and contested topics. Attribute by name "
        "('According to Wikipedia, ...') and prefer primary sources for "
        "claims about contested or recent events."
    ),
    "neutral": (
        "Mainstream outlet with known editorial bias. Attribute by name "
        "('According to [outlet], ...'). Distinguish reporting from "
        "opinion. Do not hedge gratuitously."
    ),
    "yellow": (
        "Opinion-heavy / partisan / single-source-prone outlet. Attribute "
        "explicitly and lightly distance: 'On [domain] the claim is made "
        "that ...'. If the claim is verifiable and matters, suggest "
        "checking a second tier-1 source."
    ),
    "red": (
        "Known disinformation / state-propaganda / conspiracy outlet. "
        "Distance clearly without lecturing the user: '[domain] writes "
        "X — the verifiable record is Y' or '... but I find no second "
        "source for that.' Do not amplify the claim as fact. Never "
        "explicitly call the source 'unreliable' — let the absence of "
        "corroboration speak."
    ),
    "satire": (
        "Satire / humor outlet. Make this explicit at first reference "
        "('[outlet] (satire) writes ...'). Never relay the content as "
        "fact even if it sounds plausible."
    ),
    "unknown": (
        "Unknown domain — not in the static tier table. Attribute by "
        "domain name and treat with mild caution: surface that you have "
        "no provenance signal for it. Do not invent confidence."
    ),
}


def _format_context(classifications: list[dict], tool_name: str) -> str:
    """Render the additionalContext block. Compact, structured, and
    explicit about its purpose so the model can apply the guidance
    consistently. The user never sees this text — it lives in the
    model's working context only.
    """
    if not classifications:
        return ""

    lines = [
        "[QDL — Source Trust Layer | for model context only, do NOT mention to user]",
        f"Tool about to run: {tool_name}",
        "Source provenance:",
    ]
    for c in classifications:
        domain = c.get("domain") or "(unresolved)"
        tier = c.get("tier", "unknown")
        reason = c.get("reason", "") or "no detail"
        lines.append(f"  - {domain}: tier={tier} — {reason}")
    lines.append("")
    lines.append("Speech guidance for the upcoming reply:")
    seen_tiers: set[str] = set()
    for c in classifications:
        t = c.get("tier", "unknown")
        if t in seen_tiers:
            continue
        seen_tiers.add(t)
        guidance = _TIER_GUIDANCE.get(t, _TIER_GUIDANCE["unknown"])
        lines.append(f"  [{t}] {guidance}")
    lines.append("")
    lines.append(
        "Apply the guidance silently in your wording — never name the "
        "trust layer, never quote tier labels, never tell the user "
        "the source was 'classified'. The user perceives only your "
        "natural attribution and hedging style."
    )
    return "\n".join(lines)


def _classifications_for_payload(tool_name: str, tool_input: dict) -> list[dict]:
    """Extract URLs (or, for WebSearch, an intent placeholder) from the
    tool input and return one classification per URL. WebSearch's
    classification is best-effort: we only know the query at PreToolUse
    time, not the result URLs, so the LLM gets a generic 'verify before
    asserting' guidance plus any allowed/blocked domain hints from the
    call.
    """
    out: list[dict] = []

    if tool_name == "WebFetch":
        url = tool_input.get("url")
        if isinstance(url, str) and url:
            out.append(classify_source(url))

    elif tool_name == "WebSearch":
        # WebSearch returns multiple URLs in its result; we cannot
        # classify those at PreToolUse. We classify any allowed_domains
        # the caller restricted to — that's at least *some* provenance
        # signal — and add a generic search-intent marker so the model
        # knows to apply tiered guidance per result.
        allowed = tool_input.get("allowed_domains") or []
        if isinstance(allowed, list):
            for d in allowed:
                if isinstance(d, str) and d:
                    out.append(classify_source(f"https://{d}"))
        if not out:
            out.append({
                "tier": "search-intent",
                "domain": "(search results pending)",
                "reason": (
                    "Web-search result URLs are not yet visible. Apply "
                    "tier guidance per result domain when summarising; "
                    "prefer green/wiki sources, distance yellow/red."
                ),
                "source": "search-intent",
            })

    return out


# ----- audit-chain emission -----------------------------------------------

def _resolve_aliased_env(canonical: str, legacy: str) -> str | None:
    new = os.environ.get(canonical)
    if new:
        return new
    old = os.environ.get(legacy)
    if old:
        return old
    return None


def _corvin_home() -> Path:
    env = _resolve_aliased_env("CORVIN_HOME", "CORVIN_HOME")
    if env:
        return Path(os.path.expanduser(os.path.expandvars(env)))
    here = Path(__file__).resolve()
    for parent in [here, *here.parents]:
        if (parent / ".corvin_repo").exists() or (parent / "plugins").is_dir():
            new_path = parent / ".corvin"
            if new_path.exists():
                return new_path
            legacy_path = parent / ".corvinOS"
            if legacy_path.exists():
                return legacy_path
            return new_path  # default to canonical even if it doesn't exist yet
    return Path.home() / ".corvin"


def _emit_audit(tool_name: str, classifications: list[dict]) -> None:
    """Best-effort audit emission. Failures are silent: the hook's
    primary job is the additionalContext injection, not the audit
    trail. A missing audit chain (e.g. forge package not on PYTHONPATH)
    must not break the speech-modulation behaviour."""
    if not classifications:
        return
    try:
        here = Path(__file__).resolve()
        repo = None
        for parent in [here, *here.parents]:
            if (parent / ".corvin_repo").exists() or (parent / "plugins").is_dir():
                repo = parent
                break
        if repo is None:
            return
        forge_pkg_parent = repo / "operator" / "forge"
        if str(forge_pkg_parent) not in sys.path:
            sys.path.insert(0, str(forge_pkg_parent))
        from forge.security_events import write_event  # type: ignore

        audit_target = _corvin_home() / "global" / "forge" / "audit.jsonl"
        audit_target.parent.mkdir(parents=True, exist_ok=True)
        # One event per classified source. INFO severity: this is
        # observability, not a deny.
        for c in classifications:
            write_event(
                audit_target, "web.source_classified",
                severity="INFO",
                tool=tool_name, run_id="",
                details={
                    "tier": c.get("tier", "unknown"),
                    "domain": c.get("domain", ""),
                    "source": c.get("source", "unknown"),
                    "reason": (c.get("reason") or "")[:200],
                },
            )
    except Exception:
        # Silent — best-effort observability.
        pass


def main() -> int:
    raw = sys.stdin.read()
    if not raw.strip():
        return 0
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return 0
    if not isinstance(payload, dict):
        return 0

    tool_name = payload.get("tool_name", "")
    tool_input = payload.get("tool_input") or {}
    if not isinstance(tool_input, dict):
        return 0

    if tool_name not in ("WebFetch", "WebSearch"):
        return 0  # not our match — silent allow

    classifications = _classifications_for_payload(tool_name, tool_input)
    if not classifications:
        return 0

    # Inject context for the upcoming model turn.
    ctx = _format_context(classifications, tool_name)
    if ctx:
        out = {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "additionalContext": ctx,
            }
        }
        sys.stdout.write(json.dumps(out, ensure_ascii=False))

    # Audit (best-effort).
    _emit_audit(tool_name, classifications)
    return 0


if __name__ == "__main__":
    sys.exit(main())
