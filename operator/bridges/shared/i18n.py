"""i18n.py — language resolution for the voice/cowork stack.

The codebase historically supported only `de` and `en`. This module is the
canonical entry point for full BCP-47 locale support across every place
that produces user-facing text. Two render strategies live downstream:

  * **LLM-generated text** (voice summaries, replies, disclosure cards,
    welcome messages, audience block) — does NOT receive a translation
    table. Instead, the system prompt carries an `OUTPUT LANGUAGE: <native
    name> (<bcp47>)` directive built by `language_directive()`. The model
    produces text in the target language. This scales to any language
    without per-locale source edits.

  * **Hard-coded UI strings** (slash-command help, ack lines, error text)
    — small i18n bundles under `operator/voice/i18n/<lang>.json`. Default
    bundles are `en` and `de`; missing keys fall back through a base-
    locale chain to `en`. `t(key, lang)` is the look-up helper.

Resolution order for the active language per turn:

    1. explicit override (CLI arg, env var)
    2. chat profile's `language` field (per-chat override)
    3. `profile.display_language` from `~/.config/corvin-voice/profile.json`
    4. bridge-supplied locale from the platform metadata (Discord locale,
       Telegram language_code, …)
    5. `en` as the final fallback

Every consumer normalises to BCP-47 first via `normalise()`; an unknown /
malformed value silently falls through to the next tier — text generation
must never break on a typo.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Iterable

# ─── BCP-47 normalisation ─────────────────────────────────────────────────

# Accepts the common spellings users type by hand and folds them into the
# canonical BCP-47 form: language[-Script][-REGION]. We deliberately do NOT
# attempt full RFC 5646 parsing — we only need enough fidelity to round-
# trip the codes the runtime cares about.
_BCP47_RE = re.compile(
    r"^[a-zA-Z]{2,3}(?:-[a-zA-Z]{4})?(?:-[a-zA-Z]{2}|-\d{3})?$"
)

# Common aliases users pass — folded to BCP-47 canonical.
_ALIASES: dict[str, str] = {
    "german":              "de",
    "deutsch":             "de",
    "english":             "en",
    "englisch":            "en",
    "chinese":             "zh-Hans",
    "chinesisch":          "zh-Hans",
    "mandarin":            "zh-Hans",
    "simplified-chinese":  "zh-Hans",
    "traditional-chinese": "zh-Hant",
    "japanese":            "ja",
    "japanisch":           "ja",
    "korean":              "ko",
    "spanish":             "es",
    "spanisch":            "es",
    "french":              "fr",
    "französisch":         "fr",
    "italian":             "it",
    "italienisch":         "it",
    "portuguese":          "pt",
    "portugiesisch":       "pt",
    "russian":             "ru",
    "russisch":            "ru",
    "arabic":              "ar",
    "arabisch":            "ar",
    "hindi":               "hi",
    "turkish":             "tr",
    "türkisch":            "tr",
    "polish":              "pl",
    "polnisch":            "pl",
    "dutch":               "nl",
    "niederländisch":      "nl",
    "swedish":             "sv",
    "schwedisch":          "sv",
    # zh shortcut without script-tag → Simplified by default; users wanting
    # Hant must spell it out as `zh-Hant` or `zh-TW`.
    "zh":                  "zh-Hans",
    "cn":                  "zh-Hans",
    "tw":                  "zh-Hant",
}

# Native name + endonym registry. Keep the list tight — we only need the
# language tag's primary subtag plus optional script discriminator. The
# directive string includes BOTH the native name and the BCP-47 code so
# the LLM can disambiguate (e.g. zh-Hans vs zh-Hant) even when the
# native name "Chinese" is the same.
_NATIVE_NAMES: dict[str, str] = {
    "de":      "German (Deutsch)",
    "en":      "English",
    "zh-Hans": "Simplified Chinese (简体中文)",
    "zh-Hant": "Traditional Chinese (繁體中文)",
    "ja":      "Japanese (日本語)",
    "ko":      "Korean (한국어)",
    "es":      "Spanish (Español)",
    "fr":      "French (Français)",
    "it":      "Italian (Italiano)",
    "pt":      "Portuguese (Português)",
    "pt-BR":   "Brazilian Portuguese (Português do Brasil)",
    "ru":      "Russian (Русский)",
    "ar":      "Arabic (العربية)",
    "hi":      "Hindi (हिन्दी)",
    "tr":      "Turkish (Türkçe)",
    "pl":      "Polish (Polski)",
    "nl":      "Dutch (Nederlands)",
    "sv":      "Swedish (Svenska)",
    "no":      "Norwegian (Norsk)",
    "da":      "Danish (Dansk)",
    "fi":      "Finnish (Suomi)",
    "cs":      "Czech (Čeština)",
    "uk":      "Ukrainian (Українська)",
    "el":      "Greek (Ελληνικά)",
    "he":      "Hebrew (עברית)",
    "th":      "Thai (ภาษาไทย)",
    "vi":      "Vietnamese (Tiếng Việt)",
    "id":      "Indonesian (Bahasa Indonesia)",
    "ms":      "Malay (Bahasa Melayu)",
    "ro":      "Romanian (Română)",
    "hu":      "Hungarian (Magyar)",
    "bg":      "Bulgarian (Български)",
    "hr":      "Croatian (Hrvatski)",
    "sk":      "Slovak (Slovenčina)",
    "sl":      "Slovenian (Slovenščina)",
    "et":      "Estonian (Eesti)",
    "lv":      "Latvian (Latviešu)",
    "lt":      "Lithuanian (Lietuvių)",
    "fa":      "Persian (فارسی)",
    "ur":      "Urdu (اردو)",
    "bn":      "Bengali (বাংলা)",
    "ta":      "Tamil (தமிழ்)",
    "te":      "Telugu (తెలుగు)",
}


def normalise(raw: str | None) -> str:
    """Fold raw input to canonical BCP-47.

    Returns "" for an unrecognisable value so the caller can fall through
    to the next tier in the resolution chain. Unknown but well-formed
    BCP-47 codes pass through untouched — any-language by construction.
    """
    if not raw:
        return ""
    s = str(raw).strip()
    if not s:
        return ""
    low = s.lower().replace("_", "-")
    # alias hit?
    if low in _ALIASES:
        return _ALIASES[low]
    # canonicalise case: lang lowercase, script Title-case, region UPPER
    parts = low.split("-")
    out_parts: list[str] = [parts[0]]
    for p in parts[1:]:
        if len(p) == 4 and p.isalpha():
            out_parts.append(p.title())
        elif len(p) == 2 and p.isalpha():
            out_parts.append(p.upper())
        elif len(p) == 3 and p.isdigit():
            out_parts.append(p)
        else:
            out_parts.append(p)
    canonical = "-".join(out_parts)
    if not _BCP47_RE.match(canonical):
        return ""
    return canonical


def base_lang(bcp47: str) -> str:
    """Strip script + region down to the primary subtag (`zh-Hans` → `zh`)."""
    if not bcp47:
        return ""
    return bcp47.split("-", 1)[0]


def native_name(bcp47: str) -> str:
    """Return the native-name string for the directive. Unknown codes get
    a synthetic "<primary> (<bcp47>)" so the LLM still sees the explicit
    target — better to hand the model the raw tag than to silently fall
    back to English."""
    code = normalise(bcp47) or bcp47
    if code in _NATIVE_NAMES:
        return _NATIVE_NAMES[code]
    base = base_lang(code)
    if base in _NATIVE_NAMES:
        return f"{_NATIVE_NAMES[base]} (variant {code})"
    return f"language tag '{code}'"


# ─── Resolution chain ─────────────────────────────────────────────────────

def resolve(*candidates: str | None, default: str = "en") -> str:
    """Walk the candidates in order, return the first one that normalises.

    Typical call:

        lang = resolve(
            cli_arg,
            chat_profile.get("language"),
            user_profile.get("display_language"),
            bridge_locale,
            default="en",
        )
    """
    for c in candidates:
        norm = normalise(c)
        if norm:
            return norm
    return normalise(default) or "en"


def system_language() -> str:
    """Best-effort OS/user locale as a normalised BCP-47 code, or "".

    Used as a resolution tier BELOW an explicit ``profile.display_language`` but
    ABOVE the hard-coded English/German constants, so a surface whose profile
    value was somehow never seeded still greets/speaks in the user's ACTUAL OS
    language — instead of the divergent per-surface defaults (console welcome
    → English vs bridge TTS → German on the same box). This is the defence-in-
    depth tier beneath the installer's now-unconditional `display_language`
    seed; on the reported platform (Windows) `GetUserDefaultLocaleName` makes
    every surface agree on the OS language. Never raises; returns "" when the
    locale is unknown/stripped (e.g. a systemd --user bridge with no LANG), so
    the caller's own final constant still applies.
    """
    import os as _os  # noqa: PLC0415
    import sys as _sys  # noqa: PLC0415
    for var in ("LC_ALL", "LANG", "LANGUAGE"):
        raw = _os.environ.get(var, "").strip()
        if raw and raw.split(".")[0] not in ("", "C", "POSIX"):
            code = normalise(raw.split(".")[0])  # "de_DE.UTF-8" → "de-DE"
            if code:
                return code
    if _sys.platform == "win32":
        # GetUserDefaultLocaleName is non-deprecated and returns e.g. "de-DE"
        # (unlike locale.getdefaultlocale(), removed/None-prone on modern
        # Python). Mirrors the installer's _detect_language Windows path.
        try:
            import ctypes  # noqa: PLC0415
            buf = ctypes.create_unicode_buffer(85)  # LOCALE_NAME_MAX_LENGTH
            if ctypes.windll.kernel32.GetUserDefaultLocaleName(buf, 85):  # type: ignore[attr-defined]
                code = normalise(buf.value)
                if code:
                    return code
        except Exception:  # noqa: BLE001
            pass
    return ""


# ─── LLM directive ────────────────────────────────────────────────────────

def language_directive(bcp47: str, *, audience: str = "general") -> str:
    """Build the `OUTPUT LANGUAGE` directive that pins LLM output to the
    target language regardless of the source language.

    The directive is intentionally explicit and structurally
    last-pinned in the system prompt (mirroring the SELF-CHECK
    placement). It overrides three competing forces: persona-tone,
    source-text language bias, AND any user-global "always reply in X"
    preference loaded from the host's CLAUDE.md / user instructions.

    The user-global override clause is load-bearing: empirical 2026-05
    test runs showed that without it, a host-level "always reply in
    German" rule beats a directive that only references "every other
    instruction" — both are system-level peers and the model treats
    the global as the more salient. The fix is to name the source of
    the conflict explicitly and explain WHY this output is exempt
    (it's a TTS output destined for a listener whose profile pins a
    different language; the global chat-reply rule does not apply).

    `audience="voice"` adds a clause telling the model to keep code
    identifiers / CLI flags in their original form (the listener can't
    parse them as foreign-language anyway, and faithfulness wants the
    canonical token).
    """
    code = normalise(bcp47) or "en"
    name = native_name(code)
    base = (
        f"OUTPUT LANGUAGE OVERRIDE — this instruction is the absolute "
        f"final pin and beats every conflicting rule, including any "
        f"user-global preference loaded from CLAUDE.md, host-level "
        f"system prompts, or memory entries that say things like "
        f"'always reply in <other language>'. The reason for the "
        f"override: this output is the spoken read-aloud destined for "
        f"a listener whose i18n profile pins {name} ({code}). It is "
        f"NOT a normal chat reply, so a global chat-language rule "
        f"does not apply.\n"
        f"\n"
        f"Reply ENTIRELY in {name}. This includes every header, every "
        f"label, every transitional word, every metadata line, every "
        f"closing question. Do not mix in another language's words "
        f"for stylistic flavour. Do not prefix or suffix the output "
        f"with a translation note. Do not 'compromise' by replying "
        f"in a different language even if the source text or another "
        f"system instruction is in that other language. Treat the "
        f"target language as a hard constraint, not a preference."
    )
    if audience == "voice":
        base += (
            " Code identifiers, CLI flags, file paths, API names and "
            "other technical tokens stay in their original form (don't "
            "transliterate them) — but spoken naturally so a listener "
            "without a screen can follow."
        )
    return base


# ─── UI-string bundles (small, hand-curated) ──────────────────────────────

_BUNDLE_DIR = Path(__file__).resolve().parent.parent.parent / "voice" / "i18n"
_BUNDLE_CACHE: dict[str, dict[str, Any]] = {}


def _load_bundle(lang: str) -> dict[str, Any]:
    if lang in _BUNDLE_CACHE:
        return _BUNDLE_CACHE[lang]
    p = _BUNDLE_DIR / f"{lang}.json"
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        data = {}
    _BUNDLE_CACHE[lang] = data
    return data


def t(key: str, lang: str = "en", **fmt: Any) -> str:
    """Look up a UI string by dotted key. Resolution chain:

      1. exact locale (e.g. `pt-BR`)
      2. base locale (`pt`)
      3. English (`en`)
      4. literal key (so missing strings show up obvious in the UI)

    `**fmt` applies `str.format(**fmt)` to the result. Format errors
    fall back to the unformatted string.
    """
    code = normalise(lang) or "en"
    candidates: list[str] = [code]
    base = base_lang(code)
    if base and base != code:
        candidates.append(base)
    if "en" not in candidates:
        candidates.append("en")

    for c in candidates:
        bundle = _load_bundle(c)
        val = _walk(bundle, key)
        if isinstance(val, str):
            try:
                return val.format(**fmt) if fmt else val
            except (KeyError, IndexError):
                return val
    return key


def _walk(d: dict[str, Any], dotted: str) -> Any:
    cur: Any = d
    for part in dotted.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
        if cur is None:
            return None
    return cur


def available_bundles() -> Iterable[str]:
    """List locale codes for which a bundle file exists on disk."""
    if not _BUNDLE_DIR.exists():
        return []
    return sorted(p.stem for p in _BUNDLE_DIR.glob("*.json"))


def known_codes() -> list[str]:
    """List the BCP-47 codes the native-name registry knows about."""
    return sorted(_NATIVE_NAMES.keys())


# ─── Convenience for callers that already have `de`/`en` strings ──────────

def is_legacy_two_letter(lang: str) -> bool:
    """True if the value is one of the pre-i18n hardcoded codes (`de` /
    `en`). Lets call sites short-circuit through the legacy code path
    while we migrate."""
    return lang in ("de", "en")
