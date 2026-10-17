#!/usr/bin/env python3
"""Last gate in front of TTS: only human-understandable prose may be spoken.

The summarizer (``summarize.py``) asks an LLM to rewrite an answer as spoken
text under a long system prompt. When that call misbehaves — the model echoes
its instructions, the appendix/metaphor marker lookup lands INSIDE the echoed
prompt, a template placeholder stays unrendered — the instruction text used to
travel unchecked to the synthesiser and was read aloud.

``sanitize_spoken`` is the structural answer: a deterministic, stdlib-only
filter that drops meta/instruction lines, strips markup remnants and internal
placeholders, and returns ``""`` when nothing speakable is left. A caller that
gets ``""`` speaks ``neutral_announcement()`` (via ``speak_or_announce``) — it
never falls back to the raw input text.

Twin consumers: ``summarize.py`` (every LLM output, all surfaces) and
``adapter.build_voice_summary`` (final gate, also for ``<voice>`` overrides).
Both load it by file path, so it must stay import-free of the repo.
"""

from __future__ import annotations

import re

# Prompt/protocol LINES. A line counts as scaffolding only when it *starts* with
# a tag, a protocol label followed by its separator, or a model-directed
# imperative. A word that merely appears inside a sentence ("die Regeln für
# Rechnungen", "der Self-Check ist grün", "SELF-CHECK in der Pipeline") is
# content and is never matched: every alternative is anchored at line start
# and the protocol tokens are uppercase-only.
_STRONG_META = re.compile(
    r"^\s*(?:[-*•>]\s*)?(?:"
    r"\[\s*(?:TASK|ANSWER|ANTWORT|AUFGABE)\s*\]"
    r"|(?:DEINE\s+EINZIGE\s+AUFGABE|YOUR\s+ONLY\s+TASK)\b"
    r"|(?:OUTPUT\s+(?:SHAPE|LANGUAGE|FORMAT)|AUSGABE-?FORMAT|TRANSCRIPT\s+(?:START|END)"
    r"|LERN-ZUGABE|LEARNING\s+ANNEX)\s*:"
    r"|(?:SELF-?CHECK|SELBSTPR[ÜU]FUNG)\s*(?::|—|–)"
    r"|</?\s*(?i:system-reminder|system|instructions?|voice|assistant|user|task|answer)\s*>"
    r"|<\?claude|<<[A-Za-z_ ]+>>|\[\[\s*[A-Za-z_]+\s*\]\]"
    r"|VOICE_HOOK_RECURSION\b"
    r")",
)

# Forms that are scaffolding wherever they sit in a line: a CLI flag with its
# argument (an echoed invocation, not prose) and a closing system-reminder tag
# that ends the line.
_INLINE_META = re.compile(
    r"--append-system-prompt(?:-file)?\s+\S"
    r"|</system-reminder>\s*$",
)

# Section headers of the summarizer prompt, e.g. "REGELN — load-bearing:".
# Only the prompt's own form: the uppercase label REGELN/RULES, an em dash and a
# short line that ends in a colon. Other uppercase labels with a dash ("NOTE —
# die Datei fehlt.", "WICHTIG — Wartung am Freitag") are ordinary speech.
_PROTOCOL_HEADER = re.compile(
    r"^\s*(?:REGELN|RULES)\s*—[^.!?]{0,40}:\s*$",
)

# Imperatives addressed to a model, at the start of a line (case-insensitive).
# Only forms that name the model's role or the speech output: "Antworte nur im
# Chat, nicht per Mail" is a sentence in an answer and survives. "You are a
# helpful assistant" is deliberately absent: it is a common line in prose about
# prompts, which this project writes about.
_META_START = re.compile(
    r"^\s*(?:[-*•>]\s*)?(?:"
    r"du\s+bist\s+(?:ein|eine)\s+(?:[\w-]+\s+)?(?:vorleser|vorlese\w*|zusammenfass\w*)\b"
    r"|you\s+are\s+(?:a|an)\s+(?:[\w-]+\s+)?(?:summari[sz]er|reader|translator)\b"
    r"|antworte\s+nur\s+mit\b"
    r"|(?:reply|respond|answer)\s+(?:only|exclusively)\s+with\b"
    r"|output\s+only\s+(?:the\s+)?(?:spoken|speech|summary)\b"
    r")",
    re.IGNORECASE,
)

_FENCE = re.compile(r"```[\s\S]*?(?:```|\Z)")
# Sentence ends count as line starts: the structural fallback and a model that
# answers on one line both collapse "Inhalt. SELF-CHECK: …" into one line, and
# the anchored patterns must still see the instruction at its own start.
_SENTENCE_END = re.compile(r"(?<=[.!?])[ \t]+")
# Prompt-form tokens that start a scaffolding line. The structural fallback joins
# a whole answer onto one line, so a header or tag can sit mid-line behind a
# content sentence ("… grün. REGELN — load-bearing: [TASK]"): a line break is
# put in front of each such token so the anchored patterns see it at its start.
# Uppercase-only and the same forms as _STRONG_META, so prose never splits.
_PROTOCOL_BOUNDARY = re.compile(
    r"(?<=\S)[ \t]*(?=(?:REGELN|RULES)\s*—"
    r"|SELF-?CHECK\s*(?::|—|–)|SELBSTPR[ÜU]FUNG\s*(?::|—|–)"
    r"|\[\s*(?:TASK|ANSWER|ANTWORT|AUFGABE)\s*\]"
    r"|OUTPUT\s+(?:SHAPE|LANGUAGE|FORMAT)\s*:|DEINE\s+EINZIGE\s+AUFGABE|YOUR\s+ONLY\s+TASK"
    r"|</?\s*(?:system-reminder|system|instructions?)\s*>)"
)
_TAG = re.compile(r"</?[A-Za-z][A-Za-z0-9_:-]*(?:\s[^<>]{0,120})?/?>")
_PLACEHOLDER = re.compile(r"\{[A-Za-z_][A-Za-z0-9_]*\}|\$\{[^}]*\}|%\([A-Za-z_]+\)s|<[A-Z_]{3,}>")
_MD_LINK = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")
_MD_NOISE = re.compile(r"(?m)^\s*(?:#{1,6}\s+|[-*+]\s+|>\s+|\d+[.)]\s+)")
_EMPHASIS = re.compile(r"(\*{1,3}|_{2,3}|~{2})([^*_~\n]+)\1")
_SPACES = re.compile(r"[ \t ]+")

# More than this share of dropped (non-empty) lines or sentences means the output
# was mostly scaffolding: the survivors are fragments of a leaked prompt, not a
# summary, so the whole result is "" and the caller speaks the neutral line. The
# rule is kept because the patterns above are narrow enough that a real answer
# does not reach it. Exactly half is not "mostly": one instruction line beside
# one content line keeps the content.
_MAX_DROPPED_RATIO = 0.5
_MIN_SPEAKABLE_CHARS = 3


def is_meta_line(line: str) -> bool:
    """True when *line* is instruction/protocol text rather than content."""
    return bool(_STRONG_META.match(line) or _INLINE_META.search(line)
                or _META_START.match(line) or _PROTOCOL_HEADER.match(line))


# The ONE spoken fallback for "nothing speakable". Fixed text per language: it
# never quotes the input, so an empty guard result degrades into a neutral
# announcement and never into the raw (possibly instruction-laden) text.
# summarize.py and adapter.py both route through neutral_announcement().
_NEUTRAL_ANNOUNCEMENT = {
    "de": "Die Antwort steht im Chat.",
    "en": "The answer is in the chat.",
}


def neutral_announcement(lang: str = "de") -> str:
    """Fixed, input-free spoken fallback for *lang* (``de`` unless ``en``)."""
    return _NEUTRAL_ANNOUNCEMENT["en" if lang == "en" else "de"]


def speak_or_announce(text: str, lang: str = "de") -> str:
    """Speakable form of *text*, or the neutral announcement — never the raw input."""
    return sanitize_spoken(text) or neutral_announcement(lang)


def sanitize_spoken(text: str) -> str:
    """Return *text* reduced to speakable prose, or ``""`` if it is unusable.

    Never raises. ``""`` means "do not speak this" — the caller must fall
    back, not substitute the raw input.
    """
    if not text or not text.strip():
        return ""
    s = _FENCE.sub(" ", text)
    s = _MD_LINK.sub(r"\1", s)
    s = _SENTENCE_END.sub("\n", s)
    s = _PROTOCOL_BOUNDARY.sub("\n", s)

    kept: list[str] = []
    dropped = 0
    total = 0
    for raw in s.splitlines():
        line = raw.strip()
        if not line:
            continue
        total += 1
        if is_meta_line(line):
            dropped += 1
            continue
        kept.append(line)
    if not kept or (total and dropped / total > _MAX_DROPPED_RATIO):
        return ""

    s = "\n".join(kept)
    s = _TAG.sub(" ", s)
    s = _PLACEHOLDER.sub(" ", s)
    s = _MD_NOISE.sub("", s)
    s = _EMPHASIS.sub(r"\2", s)
    s = s.replace("`", "")
    s = _SPACES.sub(" ", re.sub(r"\s*\n\s*", " ", s)).strip()
    # Nothing but symbols/digits left (e.g. "---", "…") is not speech.
    if len(s) < _MIN_SPEAKABLE_CHARS or not re.search(r"[^\W\d_]{2,}", s):
        return ""
    return s


def is_speakable(text: str) -> bool:
    """True when *text* would pass ``sanitize_spoken`` unchanged in substance."""
    return bool(sanitize_spoken(text))


_WORD = re.compile(r"[^\W_]+", re.UNICODE)
_ECHO_WINDOW = 7  # consecutive words shared with the prompt = echo, not coincidence


def echoes_prompt(output: str, prompt: str, window: int = _ECHO_WINDOW) -> bool:
    """True when *output* repeats a run of *window* consecutive words of *prompt*.

    Pattern lists cannot recognise an echoed prompt: it is ordinary prose. The
    only reliable test is to compare against the instructions actually sent.
    A summary of a user's text never shares a 7-word run with our own prompt.
    """
    if not output or not prompt:
        return False
    out_w = [w.lower() for w in _WORD.findall(output)]
    pr_w = [w.lower() for w in _WORD.findall(prompt)]
    if len(out_w) < window or len(pr_w) < window:
        return False
    grams = {tuple(pr_w[i:i + window]) for i in range(len(pr_w) - window + 1)}
    return any(tuple(out_w[i:i + window]) in grams
               for i in range(len(out_w) - window + 1))
