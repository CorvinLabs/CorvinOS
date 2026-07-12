"""profile.py — bridge-wide user profile (Tier 1 of the memory layer).

Holds harmless, always-relevant preferences that should steer every Claude
reply across every bridge / chat / persona: the user's name, preferred
language, tone, timezone, voice-note length cap, and an `_extra` dict for
arbitrary custom fields.

This file lives at `~/.config/corvin-voice/profile.json` (chmod 600) and is:
  - read fresh per inbox message (mtime cache, like the other bridge state),
  - injected as a short paragraph into every system prompt the adapter builds,
  - editable both by the user (via `/profile set …`) and by Claude itself
    (via the same CLI), so the assistant can offer "should I remember this
    in your profile?" and persist the answer.

Tier 1 — plain JSON, no secrets. For credentials use the Vault (Tier 3).
For longer-form notes use Memory (Tier 2).
"""
from __future__ import annotations

import json
import os
import shutil
import threading
from pathlib import Path
from typing import Any


def _profile_path() -> Path:
    """Canonical voice-profile path: ``<XDG_CONFIG_HOME or ~/.config>/corvin-voice/profile.json``.

    XDG Base Directory spec: when ``XDG_CONFIG_HOME`` is unset/empty, the spec
    default is ``$HOME/.config`` — NOT ``voice_dir()``. The old fallback to
    ``voice_dir()`` made this path FLIP between the XDG file and the tenant-home
    file depending on whether the launching env happened to export
    ``XDG_CONFIG_HOME`` (interactive shells do; systemd --user services do not).
    The result: the console (XDG set) wrote ``~/.config/corvin-voice/profile.json``
    while the systemd bridges (XDG unset) read ``<corvin_home>/tenants/_default/
    voice/profile.json`` — reader != writer, so Learning/Metaphern set in the
    console never reached the runtime. CLAUDE.md pins ``~/.config/corvin-voice/``
    as canonical; resolve there unconditionally.

    VOICE_CONFIG_DIR (highest priority) mirrors forge.paths.voice_config_dir /
    say.py's SSOT — see vault.py::_vault_root (V-2 audit finding)."""
    override = os.environ.get("VOICE_CONFIG_DIR", "").strip()
    if override:
        return Path(os.path.expanduser(os.path.expandvars(override))) / "profile.json"
    xdg = os.environ.get("XDG_CONFIG_HOME", "").strip() or os.path.join(
        os.path.expanduser("~"), ".config"
    )
    return Path(os.path.expanduser(xdg)) / "corvin-voice" / "profile.json"


PROFILE_FILE = _profile_path()

# Keys we know about. Anything not in this list lands under `_extra` so the
# user can stash arbitrary key/value pairs without us policing the schema.
#
# `voice_audience_*` keys belong to the layer-12 TTS-audience pipeline:
# they steer how the voice summarizer translates technical replies for the
# *listener* (level, jargon tolerance, background, style, metaphors, domains
# where jargon may stay). They are kept separate from `for_system_prompt()`
# — they only enter the summarize.py system prompt via `for_tts_audience()`,
# so the cost (tokens) is paid only on the TTS path, not on every reply.
KNOWN_KEYS = {
    "name", "display_language", "tone", "timezone",
    "voice_note_max_sentences", "default_persona",
    "custom_instructions",
    "voice_audience_level",
    "voice_audience_jargon",
    "voice_audience_style",
    "voice_audience_background",
    "voice_audience_metaphors",
    "voice_audience_domains",
    "voice_audience_learning",
    "voice_audience_chat_render",
    "tts_voice",
    "tts_voice_de",
    "tts_voice_en",
    "tts_provider",
}

# Keys we deliberately don't expose via the system prompt — private notes.
HIDDEN_KEYS = {"_meta"}

# Validators for the layer-12 TTS-audience fields. Failure to validate is
# fail-open (silently dropped) — the voice path must never break because of
# a malformed profile entry.
_VOICE_AUDIENCE_LEVELS = {"novice", "intermediate", "expert"}
_VOICE_AUDIENCE_STYLES = {"concise", "verbose", "example-driven"}
_VOICE_AUDIENCE_METAPHORS = {"on", "off"}
_VOICE_AUDIENCE_BACKGROUND_MAX = 200
# Learning-mode is a fourth integer dial on the audience axis. 0 = off
# (current default — no additive content); 1 = brief gloss on first
# occurrence of a non-trivial term; 2 = active concept introduction
# ("teach"); 3 = teach plus a one-line recap. Values outside 0..3 are
# fail-open dropped. The render below appends a structurally-marked
# LEARNING-ANNEX clause that authorizes additive content AFTER the
# faithful summary — never inside or in replacement of it.
_VOICE_AUDIENCE_LEARNING_MAX = 3

# Routing toggle — the audience block is TTS-only by default. When this
# flag is set true, adapter.py also injects it into the bridge's main
# subprocess system prompt so the chat-text reply carries the LERN-ZUGABE
# annex too. Off by default; user opts in via /voice-user-set chat_render=on.
_VOICE_AUDIENCE_CHAT_RENDER_TRUTHY = {"true", "1", "on", "yes", "ja"}
_VOICE_AUDIENCE_CHAT_RENDER_FALSY = {"false", "0", "off", "no", "nein", ""}

# OpenAI TTS voices for validation
_VALID_TTS_VOICES = {"alloy", "echo", "fable", "onyx", "nova", "shimmer"}

# Defaults applied when no profile.json exists (fresh install). Kept minimal
# and universal — user-specific fields (background, domains, level) are left
# absent so they don't override personal choices set during onboarding.
_PROFILE_DEFAULTS: dict[str, Any] = {
    "voice_audience_metaphors": "on",
    "voice_audience_learning": 3,
}


def chat_render_enabled() -> bool:
    """True iff the layer-12 audience block should also land in the bridge's
    main subprocess system prompt (chat-text), not only in the TTS path.

    Off by default — the audience block is TTS-only by design (paying token
    cost on chat replies for content read with the eyes is waste). When the
    user wants to *see* the LERN-ZUGABE in chat too, they flip this on via
    `/voice-user-set chat_render=on`.
    """
    raw = load().get("voice_audience_chat_render")
    if isinstance(raw, bool):
        return raw
    if raw is None:
        return False
    s = str(raw).strip().lower()
    if s in _VOICE_AUDIENCE_CHAT_RENDER_TRUTHY:
        return True
    return False


# ─── load / save (atomic, mtime-aware) ─────────────────────────────────────

_cache: dict[str, Any] | None = None
_cache_mtime: float = 0.0
_cache_lock = threading.Lock()


def load(force: bool = False) -> dict[str, Any]:
    """Read the profile file. mtime-cached so repeat calls in the same poll
    tick are free; `force=True` re-reads unconditionally.

    Thread-safe: cache access is protected by a lock to prevent race conditions
    when multiple chats run in parallel (adapter uses ThreadPoolExecutor).
    """
    global _cache, _cache_mtime
    with _cache_lock:
        try:
            st = PROFILE_FILE.stat()
        except FileNotFoundError:
            _cache = dict(_PROFILE_DEFAULTS)
            _cache_mtime = 0.0
            return dict(_PROFILE_DEFAULTS)
        if not force and _cache is not None and st.st_mtime == _cache_mtime:
            return _cache
        try:
            _cache = json.loads(PROFILE_FILE.read_text())
        except json.JSONDecodeError:
            # Corrupt — keep last good copy if any, otherwise empty.
            _cache = _cache or {}
        else:
            _cache_mtime = st.st_mtime
        return _cache or {}


def save(data: dict[str, Any]) -> None:
    """Atomic write: tmp + rename. Bumps mtime which invalidates the cache."""
    PROFILE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = PROFILE_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    shutil.move(str(tmp), str(PROFILE_FILE))
    try:
        PROFILE_FILE.chmod(0o600)
    except OSError:
        pass


# ─── public API ────────────────────────────────────────────────────────────

def get(key: str) -> Any:
    d = load()
    if key in KNOWN_KEYS:
        return d.get(key)
    return (d.get("_extra") or {}).get(key)


def set_value(key: str, value: Any) -> dict[str, Any]:
    """Set a single key. Known keys go top-level; everything else under _extra.
    `value=None` removes the entry. Returns the full profile after the change."""
    d = load(force=True)
    if key in KNOWN_KEYS:
        if value is None:
            d.pop(key, None)
        else:
            d[key] = value
    else:
        extra = dict(d.get("_extra") or {})
        if value is None:
            extra.pop(key, None)
        else:
            extra[key] = value
        d["_extra"] = extra
    save(d)
    return d


def reset() -> None:
    """Wipe the profile. Removes the file."""
    global _cache, _cache_mtime
    with _cache_lock:
        try:
            PROFILE_FILE.unlink()
        except FileNotFoundError:
            pass
        _cache = {}
        _cache_mtime = 0.0


# ─── system-prompt formatting ──────────────────────────────────────────────

def for_system_prompt() -> str:
    """Render the profile as a short paragraph suitable for appending to the
    Claude system prompt. Returns "" when no profile is set, so the bridge
    pays no token cost on a fresh install."""
    d = load()
    if not d:
        return ""

    lines: list[str] = []
    if d.get("name"):
        lines.append(f"- Name: {d['name']}")
    if d.get("display_language"):
        lines.append(
            f"- Language: {d['display_language']} "
            f"(default; still match the user's actual writing language)"
        )
    if d.get("tone"):
        lines.append(f"- Tone: {d['tone']}")
    if d.get("timezone"):
        lines.append(f"- Timezone: {d['timezone']}")
    if d.get("voice_note_max_sentences"):
        lines.append(
            f"- Voice-note summary cap: {d['voice_note_max_sentences']} sentences"
        )
    if d.get("custom_instructions"):
        lines.append(f"- Custom instructions: {d['custom_instructions']}")
    extra = d.get("_extra") or {}
    if extra:
        custom = ", ".join(f"{k}={v}" for k, v in extra.items())
        lines.append(f"- Custom: {custom}")

    if not lines:
        return ""

    return (
        "\n\nAbout the user (always available across every chat / bridge / "
        "persona — keep these in mind):\n" + "\n".join(lines)
    )


def _validate_tts_voices(d: dict[str, Any]) -> None:
    """Validate tts_voice, tts_voice_de, tts_voice_en against OpenAI TTS voices.
    Raises ValueError if any voice is invalid. Silently allows None/missing keys."""
    for voice_key in ("tts_voice", "tts_voice_de", "tts_voice_en"):
        voice = d.get(voice_key)
        if voice is not None:
            voice_str = str(voice).strip().lower()
            if voice_str and voice_str not in _VALID_TTS_VOICES:
                raise ValueError(
                    f"Invalid TTS voice '{voice}' in {voice_key}. "
                    f"Valid options: {', '.join(sorted(_VALID_TTS_VOICES))}"
                )


def _sanitize_voice_audience(d: dict[str, Any]) -> dict[str, Any]:
    """Pick the layer-12 TTS-audience fields out of the raw profile and
    return only the ones that pass validation. Unknown / malformed values
    are dropped silently so a typo never breaks TTS."""
    out: dict[str, Any] = {}
    lvl = str(d.get("voice_audience_level", "")).strip().lower()
    if lvl in _VOICE_AUDIENCE_LEVELS:
        out["level"] = lvl
    jargon_raw = d.get("voice_audience_jargon")
    if jargon_raw is not None:
        try:
            j = int(jargon_raw)
            if 0 <= j <= 5:
                out["jargon"] = j
        except (TypeError, ValueError):
            pass
    style = str(d.get("voice_audience_style", "")).strip().lower()
    if style in _VOICE_AUDIENCE_STYLES:
        out["style"] = style
    bg = str(d.get("voice_audience_background", "")).strip()
    if bg and len(bg) <= _VOICE_AUDIENCE_BACKGROUND_MAX:
        out["background"] = bg
    meta = str(d.get("voice_audience_metaphors", "")).strip().lower()
    if meta in _VOICE_AUDIENCE_METAPHORS:
        out["metaphors"] = meta
    doms_raw = d.get("voice_audience_domains")
    if isinstance(doms_raw, str):
        doms = [x.strip() for x in doms_raw.split(",") if x.strip()]
    elif isinstance(doms_raw, list):
        doms = [str(x).strip() for x in doms_raw if str(x).strip()]
    else:
        doms = []
    if doms:
        out["domains"] = doms[:8]
    learning_raw = d.get("voice_audience_learning")
    if learning_raw is not None:
        try:
            l = int(learning_raw)
            if 0 <= l <= _VOICE_AUDIENCE_LEARNING_MAX:
                out["learning"] = l
        except (TypeError, ValueError):
            pass
    return out


def for_tts_audience(lang: str = "de") -> str:
    """Render the TTS-audience block (layer 12) for the voice summarizer.

    Returns "" when no audience fields are set so the summarizer behaves
    exactly like before — backward-compat by construction.

    The block is appended AFTER the persona-tone block in summarize.py and
    BEFORE the closing instructions; faithfulness and completeness rules
    in the base system prompt remain load-bearing — the audience block
    only steers the *how*, never the *what*.

    `lang` accepts any BCP-47 code. `de` keeps the German block (legacy
    behaviour). Every other code (en, zh-Hans, ja, ar, fr, ...) renders
    the English block — English is the LLM-pivot for the audience
    metadata; the OUTPUT-LANGUAGE directive elsewhere in the system
    prompt steers the actual reply language.
    """
    a = _sanitize_voice_audience(load())
    if not a:
        return ""

    # BCP-47 dispatch: only `de` keeps the German rendering. Any other
    # code (including the legacy `en`) hits the English block.
    base = lang.split("-", 1)[0].lower() if lang else "en"
    if base != "de":
        bits: list[str] = []
        if "level" in a:
            bits.append(f"comprehension level {a['level']}")
        if "background" in a:
            bits.append(f"background — {a['background']}")
        if "jargon" in a:
            bits.append(f"jargon tolerance {a['jargon']}/5")
        if "style" in a:
            bits.append(f"style preference {a['style']}")
        if "metaphors" in a:
            bits.append(
                "analogies welcome" if a["metaphors"] == "on"
                else "no analogies — stick to literal description"
            )
        if "domains" in a:
            bits.append(
                "domains where jargon may stay untranslated: "
                + ", ".join(a["domains"])
            )
        if "learning" in a:
            bits.append(f"learning mode {a['learning']}/3")
        if not bits:
            return ""
        low_jargon_clause = ""
        if a.get("jargon") is not None and a["jargon"] <= 1:
            low_jargon_clause = (
                " For low jargon tolerance you ARE permitted to render "
                "code identifiers, CLI commands and API names in plain "
                "everyday language — that is translation, not invention. "
                "Signal where a term was code (\"the … setting\", \"the "
                "… command\") instead of speaking the literal token."
            )
        learning_clause = ""
        learning = a.get("learning")
        if learning is not None and learning >= 1:
            depth = {
                1: "Add ONE short half-sentence gloss the first time a "
                   "non-trivial term appears in the source. No new term "
                   "introduction beyond what the source itself names.",
                2: "Actively introduce ONE underlying concept the listener "
                   "probably does not already have — pick the most "
                   "load-bearing one in the source. One or two sentences.",
                3: "Actively introduce the most load-bearing underlying "
                   "concept (one or two sentences) AND close with a "
                   "one-sentence recap that pins the new vocabulary so it "
                   "sticks.",
            }[learning]
            learning_clause = (
                " LEARNING ANNEX (MANDATORY, NOT OPTIONAL) — your output "
                "MUST end with a teaching addition that begins with "
                "exactly one of these markers: \"And to give you "
                "context,\" or \"Worth knowing,\". The marker is the "
                "verification anchor — without it you have not "
                "completed the task. " + depth +
                " The annex is purely ADDITIVE: the summary itself stays "
                "faithful and complete — never trade source content for "
                "didactics, never reorder or shorten the summary to make "
                "room. The annex must be spoken aloud as part of the "
                "output, not silently appended."
            )
        metaphor_clause = ""
        if a.get("metaphors") == "on":
            if learning_clause:
                # Tie metaphor to the learning annex when both are active.
                metaphor_clause = (
                    " METAPHOR BRIDGE (follows the learning annex) — directly "
                    "after the learning annex, add exactly ONE sentence that "
                    "begins with \"As a picture,\" or \"Think of it like\". "
                    "Map the concept from the annex onto something from everyday "
                    "life — one concrete analogy, no new information."
                )
            else:
                # Standalone metaphor appendix (no learning annex active).
                metaphor_clause = (
                    " METAPHOR APPENDIX (MANDATORY, NOT OPTIONAL) — end your "
                    "output with one to two sentences that begin with \"As a "
                    "picture,\" or \"Think of it like\". Map the core topic of "
                    "this answer onto something from everyday life — one or two "
                    "concrete analogies, no new information. The marker is the "
                    "verification anchor — without it you have not completed the "
                    "task. Purely ADDITIVE: never shorten or trade content for "
                    "the metaphor."
                )
        return (
            "AUDIENCE — translate the cryptic-text parts (stack traces, CLI "
            "output, code) so this listener can follow without a screen. "
            "Listener profile: " + "; ".join(bits) + "."
            + low_jargon_clause
            + learning_clause
            + metaphor_clause
            + " This block tunes the *how*, never the *what* — "
            "faithfulness and completeness still rule, never drop or invent content."
        )

    # default: German
    bits = []
    if "level" in a:
        de_lvl = {"novice": "Anfänger", "intermediate": "mittel",
                  "expert": "Experte"}[a["level"]]
        bits.append(f"Verständnis-Niveau {de_lvl}")
    if "background" in a:
        bits.append(f"Hintergrund — {a['background']}")
    if "jargon" in a:
        bits.append(f"Jargon-Toleranz {a['jargon']}/5")
    if "style" in a:
        de_style = {"concise": "knapp", "verbose": "ausführlich",
                    "example-driven": "Beispiel-getrieben"}[a["style"]]
        bits.append(f"Stil-Präferenz {de_style}")
    if "metaphors" in a:
        bits.append(
            "Analogien erwünscht" if a["metaphors"] == "on"
            else "keine Analogien — wörtlich beschreiben"
        )
    if "domains" in a:
        bits.append(
            "Domänen, in denen Jargon stehen bleiben darf: "
            + ", ".join(a["domains"])
        )
    if "learning" in a:
        bits.append(f"Lern-Modus {a['learning']}/3")
    if not bits:
        return ""
    low_jargon_clause = ""
    if a.get("jargon") is not None and a["jargon"] <= 1:
        low_jargon_clause = (
            " Bei niedriger Jargon-Toleranz darfst du Code-Tokens, "
            "CLI-Befehle und API-Namen in Alltagssprache übertragen — "
            "das ist Übersetzen, kein Erfinden. Markiere die Stelle als "
            "Code (\"die … -Einstellung\", \"der … -Befehl\"), statt das "
            "Token wörtlich auszusprechen."
        )
    learning_clause = ""
    learning = a.get("learning")
    if learning is not None and learning >= 1:
        depth = {
            1: "Ergänze EINEN kurzen Halbsatz als Erläuterung beim ersten "
               "Auftreten eines nicht-trivialen Begriffs aus dem Quelltext. "
               "Keine neuen Begriffe einführen, die im Quelltext nicht "
               "schon vorkommen.",
            2: "Führe aktiv EIN zugrundeliegendes Konzept ein, das der "
               "Hörer wahrscheinlich noch nicht hat — wähle das im Quelltext "
               "wichtigste. Ein bis zwei Sätze.",
            3: "Führe aktiv das wichtigste zugrundeliegende Konzept ein "
               "(ein bis zwei Sätze) UND schließe mit einem Ein-Satz-Recap "
               "ab, der die neue Vokabel verankert, damit sie hängen bleibt.",
        }[learning]
        learning_clause = (
            " LERN-ZUGABE (PFLICHT, NICHT OPTIONAL) — deine Ausgabe MUSS "
            "am Ende eine Lehr-Ergänzung enthalten, die mit genau einem "
            "dieser Marker beginnt: \"Und zur Einordnung,\" oder "
            "\"Wissenswert dazu,\". Der Marker ist der "
            "Verifikations-Anker — ohne ihn hast du die Aufgabe nicht "
            "erfüllt. " + depth +
            " Die Zugabe ist rein ADDITIV: die Zusammenfassung selbst bleibt "
            "treu und vollständig — niemals Quellinhalt gegen Didaktik "
            "tauschen, niemals die Zusammenfassung umstellen oder kürzen, "
            "um Platz zu schaffen. Die Zugabe muss als Teil der "
            "Sprachausgabe vorgelesen werden, nicht stumm angehängt."
        )
    metapher_clause = ""
    if a.get("metaphors") == "on":
        if learning_clause:
            # Metapher-Brücke direkt nach der LERN-ZUGABE, wenn beide aktiv sind.
            metapher_clause = (
                " METAPHER-BRÜCKE (folgt auf die LERN-ZUGABE) — direkt nach "
                "der LERN-ZUGABE, füge genau EINEN Satz an, der mit "
                "\"Als Bild gesprochen,\" oder \"Bildlich gesprochen,\" beginnt. "
                "Bilde das Konzept aus der LERN-ZUGABE auf etwas aus dem Alltag "
                "ab — eine einzige konkrete Analogie, kein neues Wissen."
            )
        else:
            # Standalone-Metapher-Zugabe (kein Lern-Modus aktiv).
            metapher_clause = (
                " METAPHER-ZUGABE (PFLICHT, NICHT OPTIONAL) — beende deine "
                "Ausgabe mit ein bis zwei Sätzen als Metapher oder Analogie, "
                "die das Kernthema auf etwas aus dem Alltag überträgt. Die "
                "Sätze MÜSSEN mit \"Als Bild gesprochen,\" oder \"Bildlich "
                "gesprochen,\" beginnen. Der Marker ist der Verifikations-Anker "
                "— ohne ihn hast du die Aufgabe nicht erfüllt. Rein ADDITIV: "
                "niemals Quellinhalt kürzen oder gegen die Metapher tauschen."
            )
    return (
        "HÖRER-PROFIL — übersetze die kryptischen Stellen (Stack-Traces, "
        "CLI-Ausgaben, Code) so, dass dieser Hörer ohne Bildschirm folgen "
        "kann. Profil: " + "; ".join(bits) + "."
        + low_jargon_clause
        + learning_clause
        + metapher_clause
        + " Dieser Block steuert nur das WIE, niemals das WAS — "
        "Treue und Vollständigkeit bleiben oberste Regel, nichts weglassen oder erfinden."
    )


def humanize() -> str:
    """One-shot pretty print for `/profile show`."""
    d = load()
    if not d:
        return "(no profile set yet — try `/profile set name=YourName` to start)"
    out = ["Profile:"]
    # Identity / general fields first.
    for k in ("name", "display_language", "tone", "timezone",
              "voice_note_max_sentences", "default_persona"):
        v = d.get(k)
        if v is not None:
            out.append(f"  {k}: {v}")
    # Layer-12 TTS-audience fields. Listed under their canonical keys so
    # the user can see exactly what `/profile rm` would touch.
    for k in ("voice_audience_level", "voice_audience_jargon",
              "voice_audience_style", "voice_audience_background",
              "voice_audience_metaphors", "voice_audience_domains",
              "voice_audience_learning", "voice_audience_chat_render"):
        v = d.get(k)
        if v is not None:
            out.append(f"  {k}: {v}")
    extra = d.get("_extra") or {}
    for k, v in extra.items():
        out.append(f"  {k}: {v}  (custom)")
    if len(out) == 1:
        return "(profile file exists but is empty)"
    return "\n".join(out)


# ─── value parsing helper ──────────────────────────────────────────────────

def parse_value(raw: str) -> Any:
    """Best-effort parse of a CLI-supplied value. Bare strings stay strings;
    numbers and booleans get coerced; "null" / "none" / empty deletes."""
    s = raw.strip()
    low = s.lower()
    if low in ("null", "none", ""):
        return None
    if low in ("true", "yes", "on"):
        return True
    if low in ("false", "no", "off"):
        return False
    try:
        if "." in s:
            return float(s)
        return int(s)
    except ValueError:
        # Strip surrounding quotes if the user supplied any.
        if len(s) >= 2 and s[0] == s[-1] and s[0] in ("'", '"'):
            return s[1:-1]
        return s
