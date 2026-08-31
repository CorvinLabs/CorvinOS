#!/usr/bin/env python3
"""router.py — persona router for the cowork layer.

Decides which persona best fits a user message. Returns None on any failure
so the caller can fall through to its default behavior.

Public API:
    route(text, personas, *,
          model="claude-haiku-4-5",
          min_confidence=0.5) -> dict | None

Where the returned dict has:
    {"persona": "<name>", "confidence": <0.0-1.0>, "why": "<short reason>"}

Backends, tried in order:
    1. ROUTER_FAKE=1 + ROUTER_FAKE_RESULT='<json>' — for tests, no LLM call.
    2. Heuristic keyword match (0 ms, no API key, no subscription needed).
    3. OpenAI text embeddings (~150 ms, uses the OPENAI_API_KEY that's
       already loaded for TTS / Whisper). This is what lets Max-subscription
       users get real semantic auto-routing without any extra key.
    4. claude CLI fallback — only when ROUTER_ALLOW_CLI=1 is set, because
       on Max-subscription setups the `claude -p` boot takes >12 s and
       times out every routing call. Without the env flag we skip it
       silently and the caller falls back to its default persona.

Backend 2 covers the obvious cases (open URL → browser, websearch → research)
in 0 ms with zero API cost.
Backend 3 (embeddings) catches everything the regex doesn't cover —
"such mir den günstigsten Zug nach München" ends up at `browser` because
its embedding lands close to the browser anchor "find me the cheapest
train". Multilingual out of the box; latency ~150 ms; cost ~$0.0000006
per request. Anchors live in each persona's JSON under `routing_anchors`,
embeddings are cached on disk.

Personas with `routing_exclude=true` are filtered out before the call —
the Allrounder shouldn't route to itself, it's the safety net for low-
confidence cases.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys

def _resolve_default_router_model() -> str:
    """Layer-29.5 cost-split: router LLM calls route through helper_model
    so an operator can pin them globally via CORVIN_HELPER_MODEL[_ROUTER_CLI].
    Falls back to canonical Haiku-4.5 when helper_model is unreachable."""
    try:
        from . import helper_model as _hm  # type: ignore
    except ImportError:
        try:
            import helper_model as _hm  # type: ignore
        except ImportError:
            return "claude-haiku-4-5-20251001"
    return _hm.resolve_helper_model(_hm.SITE_ROUTER_CLI) or "claude-haiku-4-5-20251001"


DEFAULT_MODEL = _resolve_default_router_model()
DEFAULT_TIMEOUT = 12.0


def _build_prompt(text: str, personas: list[dict]) -> tuple[str, str]:
    """Returns (system_prompt, user_message) for the routing LLM call."""
    persona_lines = []
    for p in personas:
        name = p.get("name") or ""
        desc = (p.get("description") or "").splitlines()[0][:240]
        persona_lines.append(f"- {name}: {desc}")
    persona_list = "\n".join(persona_lines) or "- (keine)"

    system = (
        "Du bist ein Persona-Router for einen KI-Agenten in einem Messenger. "
        "Wähle aus der Liste die Persona, die zu der User-request am besten "
        "passt.\n\n"
        f"Available Personas:\n{persona_list}\n\n"
        "replye AUSSCHLIEßLICH mit einem JSON-Objekt im Format:\n"
        '{"persona": "<name>", "confidence": <0.0-1.0>, "why": "<kurz, max 80 Zeichen>"}\n\n'
        "confidence: wie sicher du bist (1.0 = eindeutige Spezialaufgabe wie "
        "'open diese URL', 0.0 = pure Rate). Wenn keine Spezial-Persona klar "
        "passt (alltägliche Coding-/Misch-Aufgaben), gib eine niedrige "
        "confidence — der Caller fällt dann auf einen Allrounder back. "
        "Niemals mehr Text als das JSON ausgeben."
    )
    user = f"User-request:\n{text.strip()[:2000]}"
    return system, user


def _parse_json(s: str) -> dict | None:
    """Be tolerant: pull the first {...} block out and try to parse it."""
    if not s:
        return None
    m = re.search(r"\{[\s\S]*\}", s)
    if not m:
        return None
    try:
        d = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    if not isinstance(d, dict) or "persona" not in d:
        return None
    return d


# Keyword patterns per persona — all lowercased, regex-ready. The list is
# intentionally short: the heuristic must only fire on UNAMBIGUOUS cases.
# When in doubt, return None so the LLM (or the assistant fallback) gets to
# decide. Adding a new pattern: keep it specific enough that "I want to
# write some code that opens a URL" does NOT trigger the browser persona.
#
# Two-token rule for ambiguous verbs: a verb like "open" / "öffne" alone is
# too generic ("open the file", "open source"). We only fire the browser
# persona when both an activity verb AND a web-noun appear in the same
# message — encoded as `<verb>.*?<noun>` with non-greedy `.*?`.
_HEURISTIC_PATTERNS: dict[str, list[str]] = {
    "browser": [
        r"\böffne(?:n)?\b.*?\b(url|link|seite|website|webseite)\b",
        r"\bnavigier\w*\b.*?\b(url|link|seite|website|webseite)\b",
        r"\b(klick(?:e|en)?\s+auf|fülle?\s+(?:das\s+)?formular\s+aus)\b",
        r"\bopen\b.*?\b(url|link|page|website|site)\b",
        r"\bnavigate\s+to\b",
        r"\b(submit\s+the\s+form|fill\s+(?:in|out)\s+(?:the\s+)?form)\b",
        r"\b(playwright|browser\s+automation|automate\s+(?:the\s+)?browser)\b",
    ],
    "research": [
        r"\brecherchier\w*\b",
        r"\bsuch\w*\b.*?\b(internet|web|netz)\b",
        r"\b(im|nach\s+im)\s+(internet|web|netz)\s+(nach)?(schauen|suchen|recherchier\w*)\b",
        r"\bgoogle\s+(?:mal\s+)?(?:nach|für)\b",
        r"\b(websearch|web\s+search)\b",
        r"\bsearch\s+(?:the\s+)?web\b",
        r"\bresearch\s+(?:on|about|the)\b",
        r"\bfind\s+information\s+(?:about|on)\b",
        r"\blook\s+up\s+online\b",
    ],
}


def _call_heuristic(text: str, personas: list[dict]) -> dict | None:
    """Schlüsselwort-basierte Persona-Wahl ohne LLM-Call.

    Liefert nur dann ein Ergebnis zurück, wenn ein Pattern eindeutig matcht
    UND die Persona in der erlaubten Liste ist. Confidence ist konstant
    0.85 — hoch genug um den Default-`min_confidence=0.5` zu schlagen, aber
    niedrig genug damit ein expliziter LLM-Call (höhere Confidence) Vorrang
    behält, wenn beide aktiv sind.
    """
    if not text or not text.strip():
        return None
    valid = {p["name"] for p in personas if p.get("name")}
    needle = text.lower()
    for persona, patterns in _HEURISTIC_PATTERNS.items():
        if persona not in valid:
            continue
        for pat in patterns:
            if re.search(pat, needle):
                return {
                    "persona": persona,
                    "confidence": 0.85,
                    "why": f"heuristic: '{pat[:40]}'",
                }
    return None




def _call_cli(system: str, user: str, model: str, timeout: float) -> dict | None:
    """Fallback via `claude -p` — uses the user's existing Claude Code login.
    Opt-in only (ROUTER_ALLOW_CLI=1) because `claude -p` boots in >12 s."""
    try:
        from . import helper_model as _hm  # type: ignore
    except ImportError:
        try:
            import helper_model as _hm  # type: ignore
        except ImportError:
            _hm = None
    _bin = _hm.resolve_claude_bin() if _hm else "claude"
    cmd = [
        _bin, "-p", user,
        "--append-system-prompt", system,
        "--model", model,
        "--output-format", "text",
        "--dangerously-skip-permissions",
    ]
    try:
        r = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, check=False,
        )
        if r.returncode != 0:
            print(f"[router] CLI failed (rc={r.returncode}): {r.stderr.strip()[:200]}",
                  file=sys.stderr)
            return None
        return _parse_json(r.stdout)
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        print(f"[router] CLI exec failed: {e}", file=sys.stderr)
        return None


def _fake_result() -> dict | None:
    """Test hook: ROUTER_FAKE=1 + ROUTER_FAKE_RESULT='<json>' bypasses the LLM."""
    if os.environ.get("ROUTER_FAKE") != "1":
        return None
    raw = os.environ.get("ROUTER_FAKE_RESULT", "")
    if not raw:
        return None
    try:
        d = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return d if isinstance(d, dict) else None


def _call_embedding(text: str, personas: list[dict]) -> dict | None:
    """Try the embedding-based router. Returns None on any failure so the
    caller can fall through. Lazy-imports the helper module so a bridge
    without the openai package still loads."""
    try:
        from . import router_embedding as _re  # type: ignore
    except ImportError:
        try:
            import router_embedding as _re  # type: ignore
        except ImportError:
            return None
    try:
        return _re.route(text, personas)
    except Exception:
        # Network / API key / quota — silently fall through.
        return None


def route(text: str, personas: list[dict], *,
          model: str = DEFAULT_MODEL,
          min_confidence: float = 0.5,
          timeout: float = DEFAULT_TIMEOUT,
          mode: str = "auto") -> dict | None:
    """Pick the best-matching persona for `text`. Returns None if no
    confident pick can be made (caller should fall back to a default).

    `mode` decides which backends are tried:
        "off"         — never route, always None.
        "heuristic"   — only the keyword-matcher (no LLM, no API key).
        "embedding"   — heuristic first; if no match, OpenAI embeddings
                        (uses the OPENAI_API_KEY already loaded for TTS).
        "auto"        — heuristic → embedding → anthropic SDK (if key set);
                        CLI fallback only with ROUTER_ALLOW_CLI=1.
    """
    if mode == "off":
        return None
    if not text or not text.strip():
        return None
    pool = [p for p in (personas or []) if not p.get("routing_exclude")]
    if not pool:
        return None

    # Test path: ROUTER_FAKE=1 short-circuits every real LLM call.
    # ROUTER_FAKE_RESULT="" → simulate "router returned nothing" → None.
    if os.environ.get("ROUTER_FAKE") == "1":
        result = _fake_result()
    else:
        # 1. Heuristic first — 0 ms, no API key needed.
        result = _call_heuristic(text, pool)
        # 2. Embedding backend (uses OPENAI_API_KEY which the bridge
        #    already needs for TTS/Whisper). The embedding helper itself
        #    requires personas to declare `routing_anchors`; without
        #    anchors it returns None and we fall through.
        if result is None and mode in ("embedding", "auto"):
            result = _call_embedding(text, pool)
        # 3. CLI opt-in (Max-subscription): `claude -p` boots in >12 s —
        #    too slow for default routing. Enable via ROUTER_ALLOW_CLI=1.
        if result is None and mode == "auto" and os.environ.get("ROUTER_ALLOW_CLI") == "1":
            sys_p, user_p = _build_prompt(text, pool)
            result = _call_cli(sys_p, user_p, model, timeout)
    if not result:
        return None

    name = result.get("persona")
    try:
        conf = float(result.get("confidence") or 0)
    except (TypeError, ValueError):
        conf = 0.0
    why = str(result.get("why") or "")[:200]

    valid_names = {p["name"] for p in pool}
    if name not in valid_names:
        return None
    if conf < min_confidence:
        return None
    return {"persona": name, "confidence": conf, "why": why}
