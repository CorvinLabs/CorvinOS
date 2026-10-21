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

The same gate describes identifiers instead of letting TTS spell them
(``describe_identifiers``): paths, file names, commit hashes, ticket / ADR ids,
versions and URLs are replaced by what they are. The reader has the exact token
in the chat text.
"""

from __future__ import annotations

import re

# Prompt-form tokens shared by the line-start rules (_STRONG_META,
# _PROTOCOL_HEADER) and the mid-line splitter (_PROTOCOL_BOUNDARY), so these
# tokens cannot drift apart. All uppercase-only. The boundary deliberately
# splits on a SUBSET: its tag list stays narrow and case-sensitive, and the
# line-start-only labels (AUSGABE-FORMAT, TRANSCRIPT START/END, LERN-ZUGABE:)
# are not split mid-line. A new token shared by both goes in a _TOK_* fragment.
_TOK_SECTION_TAG = r"\[\s*(?:TASK|ANSWER|ANTWORT|AUFGABE)\s*\]"
_TOK_ONLY_TASK = r"(?:DEINE\s+EINZIGE\s+AUFGABE|YOUR\s+ONLY\s+TASK)"
_TOK_OUTPUT_LABEL = r"OUTPUT\s+(?:SHAPE|LANGUAGE|FORMAT)"
_TOK_SELF_CHECK = r"(?:SELF-?CHECK|SELBSTPR[ÜU]FUNG)\s*(?::|—|–)"
_TOK_RULES_DASH = r"(?:REGELN|RULES)\s*—"

# Prompt/protocol LINES. A line counts as scaffolding only when it *starts* with
# a tag, a protocol label followed by its separator, or a model-directed
# imperative. A word that merely appears inside a sentence ("die Regeln für
# Rechnungen", "der Self-Check ist grün", "SELF-CHECK in der Pipeline") is
# content and is never matched: every alternative is anchored at line start
# and the protocol tokens are uppercase-only.
_STRONG_META = re.compile(
    r"^\s*(?:[-*•>]\s*)?(?:"
    + _TOK_SECTION_TAG
    + r"|" + _TOK_ONLY_TASK + r"\b"
    r"|(?:" + _TOK_OUTPUT_LABEL + r"|AUSGABE-?FORMAT|TRANSCRIPT\s+(?:START|END)"
    r"|LERN-ZUGABE|LEARNING\s+ANNEX)\s*:"
    r"|" + _TOK_SELF_CHECK +
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
    r"^\s*" + _TOK_RULES_DASH + r"[^.!?]{0,40}:\s*$",
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
# Uppercase-only and built from the same _TOK_* fragments as _STRONG_META, so
# prose never splits.
_PROTOCOL_BOUNDARY = re.compile(
    r"(?<=\S)[ \t]*(?=" + _TOK_RULES_DASH
    + r"|" + _TOK_SELF_CHECK
    + r"|" + _TOK_SECTION_TAG
    + r"|" + _TOK_OUTPUT_LABEL + r"\s*:|" + _TOK_ONLY_TASK
    + r"|</?\s*(?:system-reminder|system|instructions?)\s*>)"
)
_TAG = re.compile(r"</?[A-Za-z][A-Za-z0-9_:-]*(?:\s[^<>]{0,120})?/?>")
_PLACEHOLDER = re.compile(r"\{[A-Za-z_][A-Za-z0-9_]*\}|\$\{[^}]*\}|%\([A-Za-z_]+\)s|<[A-Z_]{3,}>")
# Bounded and newline-free: the unbounded form backtracked quadratically on "[" * 50000 (6.6 s).
_MD_LINK = re.compile(r"!?\[([^\]\n]{0,200})\]\([^)\n]{0,500}\)")
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
    return sanitize_spoken(text, lang) or neutral_announcement(lang)


# -- Identifiers are described, never spoken (2026-10-20) ---------------------
#
# A listener hears a path, a commit hash or a ticket number as a stream of
# meaningless syllables, and the same token is already in the chat text. The
# summarizer prompt asks the model to describe such things; this is the
# deterministic net under that request, and it also covers a `<voice>` block the
# assistant wrote itself (no LLM pass there). It only handles classes that are
# unambiguous in running prose. A generic description ("eine Python-Datei")
# is the fallback: the model's own, specific description is better, so this
# fires only for what slipped through.
#
# Each class maps to a key; a hit becomes a marker first so that runs of the same
# kind collapse ("mehrere Architekturentscheidungen") and the article can be fitted
# to the word in front of it before the final text is produced.

# key -> (gender, singular, plural, nouns that already name the thing)
_DESC_DE = {
    "py": ("f", "Python-Datei", "Python-Dateien", "f"), "ts": ("f", "TypeScript-Datei", "TypeScript-Dateien", "f"),
    "js": ("f", "JavaScript-Datei", "JavaScript-Dateien", "f"), "json": ("f", "JSON-Datei", "JSON-Dateien", "f"),
    "md": ("n", "Markdown-Dokument", "Markdown-Dokumente", "f"), "conf": ("f", "Konfigurationsdatei", "Konfigurationsdateien", "f"),
    "sh": ("n", "Shell-Skript", "Shell-Skripte", "f"), "txt": ("f", "Textdatei", "Textdateien", "f"),
    "css": ("f", "Stylesheet-Datei", "Stylesheet-Dateien", "f"), "html": ("f", "HTML-Seite", "HTML-Seiten", "f"),
    "img": ("f", "Bilddatei", "Bilddateien", "f"), "audio": ("f", "Audiodatei", "Audiodateien", "f"),
    "video": ("f", "Videodatei", "Videodateien", "f"), "pptx": ("f", "Präsentation", "Präsentationen", "f"),
    "docx": ("n", "Word-Dokument", "Word-Dokumente", "f"), "table": ("f", "Tabelle", "Tabellen", "f"),
    "pdf": ("n", "PDF-Dokument", "PDF-Dokumente", "f"), "sql": ("f", "SQL-Datei", "SQL-Dateien", "f"),
    "log": ("f", "Logdatei", "Logdateien", "f"), "file": ("f", "Datei", "Dateien", "f"),
    "dir": ("m", "Ordner", "Ordner", "d"), "url": ("f", "Webadresse", "Webadressen", "u"),
    "local": ("f", "lokale Adresse", "lokale Adressen", "u"), "ip": ("f", "IP-Adresse", "IP-Adressen", "u"),
    "commit": ("m", "Commit", "Commits", "c"), "adr": ("f", "Architekturentscheidung", "Architekturentscheidungen", "a"),
    "plan": ("m", "Umsetzungsplan", "Umsetzungspläne", "p"), "concept": ("n", "Konzept", "Konzepte", "k"),
    "review": ("n", "Review", "Reviews", "r"), "idea": ("f", "Idee", "Ideen", "i"),
    "note": ("f", "Notiz", "Notizen", "n"), "task": ("f", "Aufgabe", "Aufgaben", "t"),
    "epic": ("n", "Epic", "Epics", "e"), "init": ("f", "Initiative", "Initiativen", "x"),
    "version": ("f", "Version", "Versionen", "v"),
}
_DESC_EN = {
    "py": ("a", "Python file", "Python files"), "ts": ("a", "TypeScript file", "TypeScript files"),
    "js": ("a", "JavaScript file", "JavaScript files"), "json": ("a", "JSON file", "JSON files"),
    "md": ("a", "Markdown document", "Markdown documents"), "conf": ("a", "configuration file", "configuration files"),
    "sh": ("a", "shell script", "shell scripts"), "txt": ("a", "text file", "text files"),
    "css": ("a", "stylesheet", "stylesheets"), "html": ("an", "HTML page", "HTML pages"),
    "img": ("an", "image file", "image files"), "audio": ("an", "audio file", "audio files"),
    "video": ("a", "video file", "video files"), "pptx": ("a", "presentation", "presentations"),
    "docx": ("a", "Word document", "Word documents"), "table": ("a", "spreadsheet", "spreadsheets"),
    "pdf": ("a", "PDF document", "PDF documents"), "sql": ("an", "SQL file", "SQL files"),
    "log": ("a", "log file", "log files"), "file": ("a", "file", "files"),
    "dir": ("a", "folder", "folders"), "url": ("a", "web address", "web addresses"),
    "local": ("a", "local address", "local addresses"), "ip": ("an", "IP address", "IP addresses"),
    "commit": ("a", "commit", "commits"), "adr": ("an", "architecture decision", "architecture decisions"),
    "plan": ("an", "implementation plan", "implementation plans"), "concept": ("a", "concept", "concepts"),
    "review": ("a", "review", "reviews"), "idea": ("an", "idea", "ideas"),
    "note": ("a", "note", "notes"), "task": ("a", "task", "tasks"),
    "epic": ("an", "epic", "epics"), "init": ("an", "initiative", "initiatives"),
    "version": ("a", "version", "versions"),
}
# A word right before the token that already names the thing ("Commit c81daf2",
# "die Datei foo.py", "version 1.4.1"): the token is dropped, the noun stays.
_OWN_NOUNS = {
    "file": ("datei", "dateien", "skript", "modul", "pfad", "file", "files", "script", "module", "path"),
    "dir": ("ordner", "verzeichnis", "pfad", "folder", "directory", "path"),
    "url": ("link", "url", "webadresse", "adresse", "address", "website"),
    "local": ("adresse", "address"), "ip": ("ip", "adresse", "address"),
    "commit": ("commit", "commits", "sha", "hash"),
    "adr": ("adr", "entscheidung", "architekturentscheidung", "decision"),
    "plan": ("plan", "umsetzungsplan"), "concept": ("konzept", "concept"), "review": ("review",),
    "idea": ("idee", "idea"), "note": ("notiz", "note"), "task": ("aufgabe", "ticket", "task"),
    "epic": ("epic",), "init": ("initiative", "initiative"), "version": ("version", "release"),
}
_FILE_KEYS = frozenset(k for k, v in _DESC_DE.items() if v[3] == "f")
# "Die Datei heißt README.md", "the script called deploy.sh": the token is a name, the noun before it says what it is.
_NAMING_WORDS = frozenset(("heißt", "heisst", "namens", "genannt", "named", "called"))

_EXT_KEYS = {
    "py": "py", "pyi": "py", "ts": "ts", "tsx": "ts", "js": "js", "jsx": "js", "mjs": "js",
    "json": "json", "jsonl": "json", "md": "md", "yaml": "conf", "yml": "conf", "toml": "conf",
    "ini": "conf", "cfg": "conf", "env": "conf", "sh": "sh", "txt": "txt", "css": "css",
    "html": "html", "svg": "img", "png": "img", "jpg": "img", "jpeg": "img", "gif": "img",
    "mp3": "audio", "ogg": "audio", "wav": "audio", "mp4": "video", "pptx": "pptx", "potx": "pptx",
    "docx": "docx", "xlsx": "table", "csv": "table", "pdf": "pdf", "sql": "sql", "log": "log",
    "lock": "file",
}
# "Node.js" and friends are product names, not files.
_JS_PRODUCTS = frozenset({"node", "next", "vue", "react", "chart", "d3", "three", "express", "nuxt", "vite", "p5"})
_DIR_HEADS = frozenset({"core", "src", "docs", "tests", "test", "scripts", "home", "usr", "etc", "var", "tmp",
                        "opt", "lib", "bin", "app", "pages", "components", "corvin_operator", "node_modules"})

_URL = re.compile(r"https?://[^\s<>\"')\]]*[^\s<>\"')\].,;:!?]", re.IGNORECASE)
_LOCAL = re.compile(r"(?<![\w.])localhost:\d{2,5}(?:/[^\s<>\"')\]]*)?", re.IGNORECASE)
_SEG = r"\.?[\w\-@+]+(?:\.[\w\-@+]+)*"       # a path segment (".vite" allowed) never ends in the sentence's period
_FILE = re.compile(
    r"(?<![\w/.\-@+])(?:[A-Za-z]:)?(?:(?:~|\.{1,2})?/)?(?:[\w.\-@+]+/)*[\w@+][\w.\-@+]*\.("
    + "|".join(sorted(_EXT_KEYS, key=len, reverse=True)) + r")(?::\d+)?(?!\w)", re.IGNORECASE)
_DIR = re.compile(
    r"(?<![\w/.\-@+])(?:(?:~|\.{1,2})(?:/" + _SEG + r")+|(?:[A-Za-z]:)?(?:/" + _SEG + r"){2,}"
    r"|(?:[A-Za-z]:/)?" + _SEG + r"(?:/" + _SEG + r")+)/?")
_IPV4 = re.compile(r"(?<![\w.])\d{1,3}(?:\.\d{1,3}){3}(?::\d{2,5})?(?!\w|\.\d)")
_HASH = re.compile(r"(?<![\w/#.\-])(?=[0-9a-f]*\d)(?=[0-9a-f]*[a-f])[0-9a-f]{7,40}(?![\w\-])")
# A word before a short hex run that says it is NOT a commit: an order number, a voucher, a
# serial, a password, an id (REF-08). Without such a word a 7-11 hex run in an answer about
# code is a commit ("der Fix kam mit c81daf266 auf main"); 12+ hex characters always are.
_NOT_HASH_NOUNS = frozenset((
    "bestellnummer", "nummer", "nr", "code", "gutscheincode", "rabattcode", "passwort", "password",
    "seriennummer", "serial", "kennung", "id", "pin", "token", "key", "schlüssel", "order", "voucher",
    "tracking", "build", "block", "kundennummer", "rechnungsnummer", "ticket", "artikelnummer",
))
_WINPATH = re.compile(r'(?<![\w\\])[A-Za-z]:\\(?:[^\s\\/:*?"<>|]+\\)*[^\s\\/:*?"<>|]*')
_KB_ID = re.compile(r"\b(?:(ADR)|(PLAN)|(CONCEPT)|(REVIEW)|(IDEA)|(NOTE))-\d{1,5}\b|\b(?:(T)-\d{4}|(E)-\d{3}|(I)-\d{2})\b")
_KB_KEYS = ("adr", "plan", "concept", "review", "idea", "note", "task", "epic", "init")
_VERSION = re.compile(r"(?<![\w.\-])v?\d+\.\d+\.\d+(?:[-+][0-9A-Za-z][0-9A-Za-z.\-]*)?(?!\w|\.\d)")
_SNAKE = re.compile(r"\b[A-Za-z][A-Za-z0-9]*(?:_[A-Za-z0-9]+)+\b")

_DATE_OR_THOUSANDS = re.compile(r"\d{1,2}\.\d{1,2}\.\d{2,4}|\d{1,3}(?:\.\d{3})+")
_PREV_WORD = re.compile(r"([^\W\d_][\w\-]*)[\s:,(]*$")
_RUN = re.compile(r"\x01(\w+)\x01(?:\s*(?:,|und|and|sowie|&)\s*\x01\1\x01)+")
_MIXED_RUN = re.compile(r"\x01\w+\x01(?:\s*(?:,|und|and|sowie|&)\s*\x01\w+\x01)+")


def _mixed_files_cb(m: "re.Match[str]") -> str:
    """A run of files of different kinds ("a.py, b.ts, c.md") is "several files"."""
    keys = re.findall(r"\x01(\w+)\x01", m.group(0))
    if len(keys) >= 2 and all(k in _FILE_KEYS for k in keys):
        return "\x02file\x02"
    return m.group(0)
_MARK = re.compile(r"([\x01\x02])(\w+)\1")
_DE_HINT = re.compile(r"\b(?:der|die|das|und|nicht|ist|sind|ich|du|wir|mit|für|eine?[nm]?|den|dem|zu|auf|im|wurde|noch)\b", re.I)
_EN_HINT = re.compile(r"\b(?:the|and|is|are|was|were|with|for|this|that|of|to|in|on|it|you|we)\b", re.I)

# Words after which the bare noun is right (an article or contraction is there).
_DET_DE = frozenset("der die das dem den des ein eine einer einem einen eines dieser diese diesem diesen dieses "
                    "jener jene jenes seine seiner seinem ihre unsere meine deine im am zum zur vom beim".split())
_DET_EN = frozenset("the this that these those a an its their our my your his her".split())
_DAT_PREP_DE = frozenset("aus von mit bei nach zu in an auf unter vor neben zwischen gegenüber".split())


def guess_lang(text: str) -> str:
    """``"en"`` when *text* reads as English, ``"de"`` otherwise."""
    return "en" if len(_EN_HINT.findall(text)) > len(_DE_HINT.findall(text)) else "de"


def _prev_word(s: str, pos: int) -> str:
    m = _PREV_WORD.search(s[max(0, pos - 40):pos])
    return m.group(1).lower() if m else ""


def _mark(m: "re.Match[str]", key: str) -> str:
    """Marker for one hit. Whether the word before it already names the thing is decided in
    ``_resolve`` — after runs collapsed, so "in den Dateien a.py, b.py und c.py" drops the
    whole run and not just its first token."""
    return f"\x01{key}\x01"


def _file_cb(m: "re.Match[str]") -> str:
    ext = m.group(1).lower()
    stem = m.group(0).rsplit("/", 1)[-1].rsplit(".", 1)[0].lower()
    if ext == "js" and stem in _JS_PRODUCTS:
        return m.group(0)
    return _mark(m, _EXT_KEYS[ext])


def _dir_cb(m: "re.Match[str]") -> str:
    token = m.group(0)
    if token[0] in "/~." or re.match(r"[A-Za-z]:/", token):
        return _mark(m, "dir")
    segs = [x for x in token.split("/") if x]
    if all(x.isdigit() for x in segs):
        return token                       # "24/7", "1/2"
    if segs[0].lower() in _DIR_HEADS or any(x.startswith(".") for x in segs):
        return _mark(m, "dir")             # "docs/claude-ref/", "node_modules/.vite"
    if len(segs) >= 4 or (len(segs) >= 3 and any(re.search(r"[._\-@+]", x) for x in segs)):
        return _mark(m, "dir")
    return token                           # "und/oder", "Stadt/Land/Fluss"


_VERSION_NOUNS = frozenset(("version", "versionen", "release", "releases", "build", "tag", "v"))


def _hash_cb(m: "re.Match[str]") -> str:
    """A short hex run is a commit unless the word before it says otherwise — an order number,
    a voucher code or a serial number ("Seriennummer 9f8e7d6c") stays spoken (REF-08). Twelve or
    more hex characters are a hash whatever precedes them."""
    tok = m.group(0)
    if len(tok) >= 12 or _prev_word(m.string, m.start()) not in _NOT_HASH_NOUNS:
        return _mark(m, "commit")
    return tok


def _winpath_cb(m: "re.Match[str]") -> str:
    tok = m.group(0).rstrip(".,;:!?")
    tail = tok.rsplit("\\", 1)[-1]
    if tail and "." in tail and tail.rsplit(".", 1)[-1].lower() in _EXT_KEYS:
        key = _EXT_KEYS[tail.rsplit(".", 1)[-1].lower()]
    else:
        key = "dir"
    return _mark(m, key) + m.group(0)[len(tok):]


def _version_cb(m: "re.Match[str]") -> str:
    """Only a token that IS a version is described: a ``v`` prefix, a pre-release suffix, or a
    version noun in front of it. "Abschnitt 3.2.1", "Python 3.11.4" and dates stay spoken."""
    tok = m.group(0)
    if _DATE_OR_THOUSANDS.fullmatch(tok):
        return tok
    if tok[0] in "vV" or "-" in tok or "+" in tok or _prev_word(m.string, m.start()) in _VERSION_NOUNS:
        return _mark(m, "version")
    return tok


def _kb_cb(m: "re.Match[str]") -> str:
    idx = next(i for i, g in enumerate(m.groups()) if g)
    return _mark(m, _KB_KEYS[idx])


def _resolve(m: "re.Match[str]", lang: str) -> str:
    plural = m.group(1) == "\x02"
    key = m.group(2)
    prev = _prev_word(m.string, m.start())
    group = "file" if key in _FILE_KEYS else key
    if prev in _OWN_NOUNS.get(group, ()) or prev in _NAMING_WORDS:
        return ""                          # "Commit c81daf2" → "Commit", "Die Datei heißt README.md" → "Die Datei heißt"
    if lang == "en":
        art, one, many = _DESC_EN[key]
        if prev in _DET_EN:
            return many if plural else one
        return f"several {many}" if plural else f"{art} {one}"
    gender, one, many, _ = _DESC_DE[key]
    if prev in _DET_DE:
        return many if plural else one
    dative = prev in _DAT_PREP_DE
    if plural:
        many_d = many if many.endswith(("n", "s")) else many + "n"
        return f"mehreren {many_d}" if dative else f"mehrere {many}"
    art = ("einer" if gender == "f" else "einem") if dative else ("eine" if gender == "f" else "ein")
    return f"{art} {one}"


def describe_identifiers(text: str, lang: str | None = None) -> str:
    """Replace file paths, commit hashes, ticket/ADR ids, versions, URLs and
    snake_case names by a spoken description. Prose is left alone: only forms that
    cannot be ordinary words are touched (a lone slash, a date, "Node.js" and a
    plain number all survive). Never raises.

    *lang* is the resolved output language of the spoken text. Descriptions exist
    for German and English; for any other language the text is returned unchanged
    (the token is spoken as is) — never a German description inside a French
    sentence. Only when *lang* is omitted is the language guessed from the text."""
    if not text:
        return text
    if lang is not None and lang.split("-", 1)[0].lower() not in ("de", "en"):
        return text
    try:
        s = text.replace("\x01", "").replace("\x02", "")
        s = _URL.sub(lambda m: _mark(m, "url"), s)
        s = _LOCAL.sub(lambda m: _mark(m, "local"), s)
        s = _WINPATH.sub(_winpath_cb, s)
        s = _FILE.sub(_file_cb, s)
        s = _DIR.sub(_dir_cb, s)
        s = _IPV4.sub(lambda m: _mark(m, "ip"), s)
        s = _HASH.sub(_hash_cb, s)
        s = _KB_ID.sub(_kb_cb, s)
        s = _VERSION.sub(_version_cb, s)
        s = _SNAKE.sub(lambda m: m.group(0).replace("_", " "), s)
        s = _RUN.sub(lambda m: f"\x02{m.group(1)}\x02", s)
        s = _MIXED_RUN.sub(_mixed_files_cb, s)
        code = lang or guess_lang(s)
        code = "en" if code.split("-", 1)[0].lower() == "en" else "de"
        s = _MARK.sub(lambda m: _resolve(m, code), s)
        s = re.sub(r"\(\s*\)", "", s)
        s = re.sub(r"[ \t]{2,}", " ", s)
        s = re.sub(r"\s+([,.;:!?])", r"\1", s)
        s = re.sub(r":\s*([.,;!?])", r"\1", s)      # "2 commits:." → "2 commits."
        s = re.sub(r",\s*,", ",", s)
        s = re.sub(r"\s+([,.;:!?])", r"\1", s)
        return s.strip()
    except Exception:  # noqa: BLE001 — a failed rewrite must not silence the answer
        return text


def sanitize_spoken(text: str, lang: str | None = None) -> str:
    """Return *text* reduced to speakable prose, or ``""`` if it is unusable.

    Never raises. ``""`` means "do not speak this" — the caller must fall
    back, not substitute the raw input. Identifiers are described, not spoken
    (:func:`describe_identifiers`); *lang* picks the language of the description
    and is guessed from the text when omitted.
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
    if not kept or dropped / total > _MAX_DROPPED_RATIO:
        return ""

    s = "\n".join(kept)
    s = _TAG.sub(" ", s)
    s = _PLACEHOLDER.sub(" ", s)
    s = _MD_NOISE.sub("", s)
    s = _EMPHASIS.sub(r"\2", s)
    s = s.replace("`", "")
    s = _SPACES.sub(" ", re.sub(r"\s*\n\s*", " ", s)).strip()
    s = describe_identifiers(s, lang)
    # Nothing but symbols/digits left (e.g. "---", "…") is not speech.
    if len(s) < _MIN_SPEAKABLE_CHARS or not re.search(r"[^\W\d_]{2,}", s):
        return ""
    return s


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
