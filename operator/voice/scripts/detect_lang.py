#!/usr/bin/env python3
"""Detect language in text without external dependencies.

Supports 20 languages: en, de, es, fr, it, pt, nl, pl, ru, ja, zh, ko, ar, tr, sv, da, no, fi, el, cs.

Usage:
    detect_lang.py [--default de] [--lang en,de,es,...] [text]
    echo "..." | detect_lang.py

Prints language code on stdout. Heuristic: count occurrences of common
function words for each language; whichever scores higher wins. Falls
back to --default (or "de") on a tie or empty input.

For special scripts (CJK, Cyrillic, Arabic, Greek), also detects by
Unicode ranges — but script membership alone NEVER produces a confident
verdict (a script is shared by many languages: Cyrillic ≠ Russian,
Arabic script ≠ Arabic, Kanji ≠ Chinese).

This is intentionally dependency-free — it does not need to be perfect,
only "good enough to pick a TTS voice." For mixed-language text, picks
the dominant language.

Scoring model (redesigned 2026-07-18 after adversarial review):
- A word that appears in SEVERAL languages' lists counts for ALL of them
  ("shared" evidence). Shared evidence raises absolute counts but can
  never create a margin — this generalises the old hand-maintained
  de/en BILINGUAL neutralisation to all 20 languages.
- A word unique to ONE list is "distinctive" evidence. Only distinctive
  evidence can win a margin, unlock script bonuses, or satisfy the
  one-sided bar.
- Script bonuses (umlauts→de, Cyrillic→ru, …) apply only when the
  language already has ≥2 distinctive word hits. An umlaut is not proof
  of German (Finnish/Swedish/Turkish use ä/ö/ü); Cyrillic is not proof
  of Russian (Ukrainian/Bulgarian).
- Script guards: Persian/Urdu-specific letters veto "ar", Ukrainian-
  specific letters veto "ru", kana vetoes "zh". These are one-way vetoes
  (never positive evidence) — the caller's fallback (profile pin /
  system locale) is the right answer for languages we cannot name.
"""

from __future__ import annotations

import argparse
import re
import sys

# Function words for each language (~30-50 per language).
# These are the most common words that strongly signal the language.
# RULES for these lists (learned the hard way, adversarial review
# 2026-07-18): (1) FUNCTION words only — no content words, and never
# words lifted from a demo/test sentence (the previous revision carried
# the Norwegian quick-brown-fox pangram and word-for-word translations
# of one test prompt in no/fi/cs, which overfit detection to those
# sentences and collided with English "late"). (2) A word may appear in
# several lists — the scorer treats it as shared evidence automatically.

EN = {
    "the", "and", "is", "of", "to", "in", "that", "it", "for", "with",
    "on", "as", "are", "was", "be", "this", "have", "has", "had", "not",
    "but", "or", "if", "when", "you", "we", "they", "i", "he", "she",
    "do", "does", "did", "an", "a", "by", "at", "from", "which", "what",
    "no", "yes", "very", "much", "many", "some", "any", "all", "none",
    "would", "could", "should", "will", "shall", "done", "my", "can",
    "more", "there", "their", "your", "them", "just", "now", "its", "only",
    "been", "get", "than", "other", "who", "go", "me", "up",
    "him", "out", "come", "make", "time", "way", "first", "want",
    "our", "about", "into", "then", "these", "those",
    "ok", "okay",
}

DE = {
    "und", "der", "die", "das", "ist", "nicht", "ein", "eine", "den", "dem",
    "des", "im", "mit", "auf", "von", "zu", "sich", "auch", "wie", "war",
    "sind", "werden", "wird", "haben", "hat", "kann", "noch", "nur", "aber",
    "oder", "wenn", "weil", "doch", "schon", "über", "für", "bei", "nach",
    "ich", "du", "er", "sie", "wir", "ihr", "es", "wurde", "worden", "ja",
    "nein", "sehr", "viel", "viele", "einen", "einer", "einem", "eines",
    "alle", "alles", "kein", "keine", "man", "mehr", "etwas", "nichts",
    "danke", "super", "prima", "gut", "ok", "okay", "fertig", "bereit",
    "möglich", "unmöglich", "richtig", "falsch", "wahr", "stimmt",
    "verstanden", "klar", "genau", "interessant", "wichtig", "wunderbar",
    "schön", "schick", "toll", "hervorragend", "perfekt", "bitte", "gerne",
    "ganz", "gar", "eben", "darum", "daher", "dann", "mal", "zwar", "sondern",
    "mein", "dein", "sein", "unser", "euer", "diesen", "jenen",
    "mir", "dir", "mich", "dich", "passt",
    # "so" is a high-frequency German particle ("weiter so!", "so ist es").
    # It also sits in the EN list — listing it on both sides makes it
    # SHARED evidence (absolute weight, no margin), same as "ja" below.
    "so",
}

ES = {
    "el", "la", "de", "que", "y", "a", "en", "un", "una", "los", "las",
    "es", "por", "con", "su", "se", "no", "le", "está", "están", "son",
    "sido", "siendo", "he", "ha", "han", "hemos", "habéis", "habían",
    "estoy", "estamos", "estáis", "estaban", "debo", "puedo", "quiero",
    "tengo", "voy", "vamos", "vienen", "viene", "fue", "eran", "sería",
    "iría", "vuelto", "ser", "estar", "hacer", "ir", "dar", "saber",
    "querer", "poder", "deber", "poner", "parecer", "yo", "tú", "él",
    "nosotros", "vosotros", "ellos", "ellas", "usted", "ustedes",
    "pero", "sino", "aunque", "porque", "si", "cuando", "donde", "como",
}

FR = {
    "le", "la", "les", "de", "des", "du", "un", "une", "et", "à", "au",
    "pas", "ne", "n", "qui", "que", "se", "s", "pour", "sur", "dans",
    "en", "avec", "avoir", "être", "je", "tu", "il", "elle", "nous",
    "vous", "elles", "eux", "me", "m", "te", "t", "lui", "son", "ses",
    "mon", "ma", "mes", "ton", "ta", "tes", "notre", "nos", "votre",
    "vos", "leur", "leurs", "celui", "celle", "ceux", "celles", "ici",
    "là", "où", "comment", "quand", "pourquoi", "combien", "quel",
    "quelle", "quels", "quelles",
    "est", "sont", "était", "étaient", "c", "ce", "cet", "cette", "ces",
    "ai", "as", "avez", "avons", "avaient", "aurais", "aurait", "ayant",
}

IT = {
    "il", "lo", "la", "i", "gli", "le", "di", "da", "che", "per", "in",
    "e", "a", "un", "una", "uno", "dei", "delle", "degli",
    "è", "sono", "sia", "siamo", "siate", "era", "eravamo",
    "eravate", "erano", "fossi", "fosse", "fossimo", "foste", "fossero",
    "sarò", "sarai", "sarà", "saremo", "sarete", "saranno", "sarei",
    "sarebbe", "saremmo", "sareste", "sarebbero", "ho", "hai", "ha",
    "abbiamo", "avete", "hanno", "avrei", "avresti", "avrebbe", "avremmo",
    "avreste", "avrebbero", "avessi", "avesse", "avessimo", "aveste",
    "avessero", "non", "anche", "come", "con", "molto", "più", "meno",
    "sempre", "ancora", "già", "appena", "solo", "mentre", "però",
}

PT = {
    "o", "a", "os", "as", "de", "do", "da", "dos", "das", "um", "uma",
    "uns", "umas", "em", "no", "na", "nos", "nas", "por", "para", "com",
    "sem", "até", "sobre", "durante", "depois", "antes", "e", "ou", "que",
    "se", "qual", "quais", "quanto", "quanta", "quantos", "quantas",
    "onde", "aonde", "como", "porque", "pois", "portanto", "então",
    "é", "são", "era", "eram", "foi", "foram", "seja", "sejam", "fosse",
    "fossem", "sou", "somos", "sois", "eras", "éramos", "éreis", "fora",
    "fôramos", "fôreis", "será", "serão", "serias",
    "seríamos", "seríeis", "seriam", "teria", "terias", "teríamos",
    "teríeis", "teriam", "tenho", "tem", "temos", "tendes", "têm",
    "tinha", "tinhas", "tínhamos", "tínheis", "tinham", "tive", "tiveste",
}

NL = {
    "de", "en", "van", "in", "te", "het", "is", "een", "dat", "op",
    "aan", "voor", "naar", "met", "of", "die", "om", "ook", "zijn",
    "had", "er", "kan", "als", "hoe", "wat", "waar", "wie", "wanneer",
    "waarom", "welk", "welke", "want", "maar", "dus", "toch", "echter",
    "ja", "nee", "nog", "heel", "zeer", "erg", "meer", "minder", "veel",
    "dit", "deze", "mijn", "jouw", "haar",
    "onze", "uw", "hun", "zich", "hier", "daar", "overal", "nergens",
    "nu", "altijd", "nooit", "ooit", "ben", "bent", "zal", "zou", "wil",
    "moet", "mag", "hoef", "durft", "wordt", "word", "werden",
    "heb", "hebt", "heeft", "hebben", "gehad", "gaat", "ging", "gegaan",
}

PL = {
    # NOTE: "ich" (Polish genitive "their") is deliberately ABSENT — it is
    # THE most frequent German word, and listing it here turned short
    # German answers ("Mach ich sofort.") into de/pl ties → None
    # (refutation round 2026-07-18). Polish keeps ~60 other markers.
    "i", "się", "w", "z", "na", "do", "nie", "a", "o", "to", "że",
    "je", "dla", "od", "czy", "jak", "ale", "mnie", "lub", "może",
    "ten", "ta", "ci", "tego", "tej", "tym", "tymi", "mi",
    "sobie", "sobą", "jego", "jej", "mój", "moja", "moje",
    "twój", "twoja", "twoje", "nasz", "nasza", "nasze", "wasz", "wasza",
    "przez", "przed", "po", "pod", "nad", "obok", "poza", "bez",
    "przy", "przeciwko", "ku", "wraz", "wobec", "zamiast", "zaraz",
    "jestem", "jesteś", "jest", "jesteśmy", "jesteście", "są", "byłem",
    "byłeś", "był", "była", "było", "były", "byłam",
    "będę", "będziesz", "będzie", "będziemy", "będziecie", "będą",
}

RU = {
    "и", "в", "во", "не", "что", "он", "на", "я", "с", "со", "а",
    "то", "все", "она", "так", "его", "но", "да", "ты", "к", "у",
    "же", "вы", "за", "бы", "по", "только", "ее", "можно", "при",
    "наконец", "два", "о", "другой", "хоть", "после", "над", "больше",
    "тот", "через", "эти", "нас", "про", "всех", "них", "какая",
    "ни", "быть", "были", "буду", "будем", "будет", "было", "от",
    "до", "вас", "эта", "это", "этот", "какой",
    "него", "ней", "ему", "ей", "нами", "ними", "мне", "тебе", "себе",
    "кто", "как", "где", "когда", "зачем", "почему", "куда", "откуда",
    "здесь", "там", "везде", "нигде", "всегда", "никогда", "иногда",
}

JA = {
    "の", "に", "は", "を", "た", "が", "で", "て", "と", "し",
    "れ", "さ", "ある", "いる", "も", "する", "から", "な", "こと",
    "として", "い", "や", "れる", "など", "なっ", "ない", "この",
    "ため", "その", "あっ", "よう", "また", "もの", "という", "あり",
    "まで", "られ", "なかっ", "ん", "ぐらい", "ゆく", "くん",
    "ちゃん", "さん", "さま", "ました", "ます", "ません", "なさい",
    "だ", "である", "です", "あります", "いません",
    "ませんでした", "ましょう", "ましょうか",
}

ZH = {
    "的", "一", "是", "不", "了", "人", "在", "他", "有", "这",
    "中", "来", "上", "大", "为", "和", "国", "地", "到", "以",
    "说", "多", "然", "作", "能", "下", "现", "出", "分", "生",
    "对", "进", "没", "把", "其", "年", "动", "同", "工", "也",
    "好", "就", "被", "开", "如", "从", "或", "实", "我", "你",
    "她", "它", "们", "那", "哪", "谁", "什么", "哪里",
    "怎样", "怎么", "会", "要", "应", "可", "让", "给", "向",
}

KO = {
    "이", "그", "저", "것", "수", "등", "들", "및", "있", "되",
    "하", "않", "말", "매우", "또는", "그리고", "에서", "나", "너",
    "우리", "그들", "이것", "그것", "저것", "무엇", "누가", "누구",
    "어디", "어떻게", "왜", "언제", "어느", "어떤", "무척", "정말",
    "꼭", "좋다", "나쁘다", "크다", "작다", "길다", "짧다",
    "내", "당신", "그녀", "너희",
    "여기", "저기", "거기", "이곳", "저곳", "지금", "어제", "내일",
    "은", "는", "가", "를", "을", "에", "로", "로부터",
}

AR = {
    "ال", "في", "من", "إلى", "هو", "هي", "أن", "على", "هذا",
    "كل", "لا", "ما", "هن", "قد", "كان", "كانت", "عن", "مع",
    "يكون", "كيف", "أين", "متى", "لماذا", "ماذا", "نعم", "أنا",
    "أنت", "نحن", "أنتم", "هم", "هذه", "ذلك", "تلك",
    "التي", "الذي", "واحد", "اثنين", "ثلاثة", "أربعة", "خمسة",
    "هنا", "هناك", "حيث", "هنالك", "و", "ف", "أو", "أم",
    "لكن", "ولكن", "لأن", "كي", "حتى", "بعد", "قبل",
    "يوم", "أمس", "غدا", "فقط", "أيضا",
}

TR = {
    "ve", "bir", "bu", "için", "ile", "en", "çok", "aynı", "olmak",
    "var", "bulunmak", "kendisi", "böyle", "olarak", "çünkü", "biraz",
    "hepsi", "sonra", "ben", "sen", "o", "biz", "siz", "onlar",
    "şu", "nedir", "neresi", "nasıl", "kimdir", "ne", "kaç",
    "hangi", "kimin", "kime", "nerede", "neden",
    "bana", "sana", "ona", "bize", "size", "onlara", "burada", "orada",
    "şurada", "nereden", "nereye", "başında", "sonunda", "ise", "da",
    # question particles are written separately in Turkish; the copula
    # suffixes (-dir/-dır/-midir) attach to the word and can never match a
    # standalone token — and "dir" collided with the German pronoun.
    "mi", "mı", "mu",
    "değil", "yok", "ama", "ancak", "fakat",
}

SV = {
    "och", "i", "att", "det", "som", "en", "av", "för", "till", "är",
    "på", "de", "om", "inte", "se", "kan", "han", "ska", "från", "eller",
    "var", "då", "detta", "ja", "än", "där", "här", "nu", "fram", "innan",
    "senare", "många", "andra", "samma", "även", "bara", "endast", "utan",
    "genom", "båda", "redan", "efter", "över", "under", "vi",
    "ni", "hon", "dem", "sina", "min", "din", "hans", "hennes", "vår",
    "er", "vilken", "vilka", "denna",
    "hade", "har", "skulle", "vill", "måste", "får", "behöva", "bli",
}

DA = {
    "og", "i", "at", "det", "som", "en", "af", "for", "til", "er",
    "på", "de", "om", "ikke", "kan", "han", "skal", "fra", "eller",
    "hvor", "der", "hvad", "når", "hvem", "hvornår", "hvorfor", "hvordan",
    "hvilken", "hvilket", "hvilke", "hende", "dine", "sine", "denne",
    "disse", "bort", "helt", "kun", "endnu", "slet", "netop", "således",
    "vi", "os", "jer", "hun", "dem", "min", "dit", "hans", "hendes",
    "vores", "jeres", "deres", "jeg",
    "havde", "har", "ville", "må", "blev",
}

NO = {
    # NOTE: "alle" (collides with core German) and "men" (collides with
    # English) are deliberately absent — they broke short de/en answers,
    # and Norwegian detection is structurally margin-less against Danish
    # anyway (the lists are ~90 % identical, both fall back safely).
    "og", "i", "at", "det", "som", "en", "av", "for", "til", "er",
    "på", "de", "om", "ikke", "se", "kan", "han", "skal", "fra", "eller",
    "hvor", "der", "hva", "når", "hvem", "hvorfor", "hvordan", "hvilken",
    "hvilke", "dette", "denne", "disse", "her", "nå", "da", "så",
    "mens", "også", "bare", "noe", "alt", "før", "etter", "over",
    "vi", "oss", "dere", "hun", "dem", "min", "din", "hans", "hennes",
    "vår", "deres", "noen", "ingen", "mange", "få", "jeg", "meg",
    "hadde", "har", "ville", "skulle", "må", "gjøre", "blir",
}

FI = {
    "ja", "että", "ole", "on", "se", "ei", "kun", "jonka", "jotka",
    "sekä", "nyt", "siis", "siinä", "jo", "niin", "tämä", "tuo",
    "kuka", "mikä", "missä", "milloin", "mihin", "mistä", "miten", "miksi",
    "millainen", "millaiset", "sellainen", "sellaisia", "tällainen",
    "tällaisia", "hän", "hänen", "hänet", "hänelle", "minä",
    "minun", "minut", "minulle", "sinä", "sinun", "sinut", "me",
    "meidän", "meitä", "meille", "te", "teidän", "teitä", "teille",
    "oli", "olivat", "ollut", "olette", "olemme", "olet", "eivät",
    "kuinka", "myös", "mutta", "vain", "koska", "sitten", "vielä",
}

EL = {
    "και", "ο", "η", "το", "στο", "στη", "στον", "στην", "από", "για",
    "να", "είναι", "που", "αν", "όπως", "κάθε", "ή", "όχι", "τι", "ποια",
    "ποιο", "ποιοι", "ποιες", "πόσο", "πόσα", "πού", "πότε", "πώς",
    "γιατί", "αυτός", "αυτή", "αυτό", "αυτοί", "αυτές", "εκείνος",
    "εκείνη", "εκείνο", "εκείνοι", "εκείνες", "έχει", "έχουν",
    "έχω", "έχεις", "έχουμε", "έχετε", "ήμουν",
    "ήσουν", "ήταν", "ήμαστε", "ήσασταν", "θα",
}

CS = {
    "a", "aby", "ačkoliv", "již", "ale", "ano",
    "aniž", "až", "bez", "byl", "byla", "bylo", "byli", "bych",
    "být", "co", "čeho", "čemu", "či", "čili", "což", "čím", "čímž",
    "daleko", "dle", "do", "dovolte", "dříve", "během",
    "ho", "jeho", "jejíž", "jej", "jemu", "když", "kde",
    "jsem", "jsi", "je", "jsme", "jste", "ten", "ta", "to",
    "tento", "tato", "toto", "těchto", "těm",
    "byly", "měl", "měla", "mělo",
    "můžeš", "také", "ještě", "pouze", "protože", "tedy", "nebo",
}

# Map language codes to their word sets
LANGUAGE_DICTS = {
    "en": EN,
    "de": DE,
    "es": ES,
    "fr": FR,
    "it": IT,
    "pt": PT,
    "nl": NL,
    "pl": PL,
    "ru": RU,
    "ja": JA,
    "zh": ZH,
    "ko": KO,
    "ar": AR,
    "tr": TR,
    "sv": SV,
    "da": DA,
    "no": NO,
    "fi": FI,
    "el": EL,
    "cs": CS,
}

# Words that occur as ordinary tokens in BOTH German and English even though
# only the EN list carries them: "was" (EN past tense / DE "what"), "in" and
# "an" (prepositions in both), one-letter "a" (EN article / a label like
# "Datei A"), "will" (EN auxiliary / DE "ich will"). Counting them one-sided
# flipped short German sentences to English (found 2026-07-17). The scorer
# treats them as shared de/en evidence.
BILINGUAL = {"was", "in", "an", "a", "will"}

# word → set of language codes whose list contains it (shared-evidence map).
# BILINGUAL words are injected into "de" so the historic de/en
# neutralisation is preserved without polluting the DE list itself.
_WORD_LANGS: dict[str, frozenset[str]] = {}


def _build_word_langs() -> None:
    tmp: dict[str, set[str]] = {}
    for lang, words in LANGUAGE_DICTS.items():
        for w in words:
            tmp.setdefault(w, set()).add(lang)
    for w in BILINGUAL:
        tmp.setdefault(w, set()).update({"de", "en"})
    _WORD_LANGS.clear()
    _WORD_LANGS.update({w: frozenset(ls) for w, ls in tmp.items()})


_build_word_langs()

# Extended regex to handle Latin with diacritics + more CJK/Scripts
WORD_RE = re.compile(
    r"[A-Za-zĀ-ſƀ-ɏ"  # Latin Extended A, B, and Basic
    r"ÄÖÜäöüßÁáÉéÍíÓóÚúÀàÈèÌìÒòÙùÂâÊêÎîÔôÛûÃãÑñÕõÇç"  # Common diacritics
    r"]+"
    r"|[぀-ゟ]+"  # Hiragana
    r"|[゠-ヿ]+"  # Katakana
    r"|[一-鿿]+"  # CJK Unified Ideographs (Kanji + Hanzi)
    r"|[가-힯]+"  # Hangul (match syllable blocks)
    r"|[Ѐ-ӿ]+"  # Cyrillic
    r"|[؀-ۿ]+"  # Arabic
    r"|[Ͱ-Ͽ]+"  # Greek
)

# Script-specific Unicode ranges for bonus/guard logic
_UMLAUT_RE = re.compile(r"[äöüÄÖÜß]")
_CYRILLIC_RE = re.compile(r"[Ѐ-ӿ]")
_CJK_RE = re.compile(r"[一-鿿぀-ゟ゠-ヿ]")
_KANA_RE = re.compile(r"[぀-ゟ゠-ヿ]")
_HANGUL_RE = re.compile(r"[가-힯]")
_ARABIC_RE = re.compile(r"[؀-ۿ]")
_GREEK_RE = re.compile(r"[Ͱ-Ͽ]")

# One-way script VETOES: codepoints that exist in a neighbour language but
# not in the language we would otherwise report. Without these, Ukrainian
# was reported as a confident "ru" and Persian/Urdu as a confident "ar" —
# and the summary pipeline then FORCE-TRANSLATED the reply into the wrong
# language (adversarial review 2026-07-18, F1).
_UKRAINIAN_ONLY_RE = re.compile(r"[іїєґІЇЄҐ]")
_PERSO_URDU_RE = re.compile(r"[پچژگیےٹڈڑہھۀە]")

# Confidence thresholds for detect_confident(): with MIXED evidence the
# winner must lead by ≥ _CONFIDENT_MARGIN distinctive hits AND have
# ≥ _CONFIDENT_MIN_HITS total evidence. With effectively ONE-SIDED
# evidence (every other language's hits are shared with the winner)
# ≥ _CONFIDENT_ONE_SIDED_MIN total hits suffice. For very short German
# text the one-sided bar drops to 1 ("Ja, fertig!" — calibrated
# 2026-07-17; kept de-only so single shared tokens in other Latin
# languages cannot misfire).
_CONFIDENT_MARGIN = 2
_CONFIDENT_MIN_HITS = 3
_CONFIDENT_ONE_SIDED_MIN = 2
_CONFIDENT_ONE_SIDED_MIN_SHORT = 1  # for de, text < 50 chars

# Markdown code carriers. Code is keyword soup (if/not/for/in/is/and/with…)
# Fenced blocks (``` ... ``` — dangling opener swallows to end-of-text,
# unterminated code must not be scored) and inline spans (`...`) are
# dropped before scoring. Replicated minimally so this module stays
# dependency-free.
_CODE_FENCE_RE = re.compile(r"```[\s\S]*?(?:```|\Z)")
_INLINE_CODE_RE = re.compile(r"`[^`\n]*`")


def _strip_code(text: str) -> str:
    text = _CODE_FENCE_RE.sub(" ", text)
    text = _INLINE_CODE_RE.sub(" ", text)
    return text


def _vetoed_langs(text: str) -> set[str]:
    """Languages that must not be reported for this text (script vetoes)."""
    veto: set[str] = set()
    if _UKRAINIAN_ONLY_RE.search(text):
        veto.add("ru")  # Ukrainian text is not Russian
    if _PERSO_URDU_RE.search(text):
        veto.add("ar")  # Persian/Urdu text is not Arabic
    if _KANA_RE.search(text):
        veto.add("zh")  # kana present → Japanese, never Chinese
    return veto


def _score_details(
    text: str, languages: list[str]
) -> tuple[dict[str, int], dict[str, int]]:
    """Return (total, distinctive) hit counts per language.

    total: every list containing the word gets a point (shared evidence).
    distinctive: only words unique to exactly one of the CANDIDATE
    languages count — this is the evidence that margins/bonuses/one-sided
    decisions are allowed to use.
    """
    lang_set = set(languages)
    total = {lang: 0 for lang in languages}
    distinctive = {lang: 0 for lang in languages}
    for word in WORD_RE.findall(text.lower()):
        owners = _WORD_LANGS.get(word)
        if owners:
            hit_langs = owners & lang_set
            for lang in hit_langs:
                total[lang] += 1
            if len(hit_langs) == 1:
                (only,) = hit_langs
                distinctive[only] += 1
            if owners & lang_set:
                continue
        # CJK/Hangul: word-level lookup missed → per-character lookup
        # (hanzi/kanji tokens concatenate; Korean particles attach to the
        # word). A char in exactly one list is distinctive.
        if any("一" <= c <= "鿿" or "぀" <= c <= "ヿ" or "가" <= c <= "힯" for c in word):
            for char in word:
                char_owners = {
                    lang for lang in ("zh", "ja", "ko")
                    if lang in lang_set and char in LANGUAGE_DICTS[lang]
                }
                for lang in char_owners:
                    total[lang] += 1
                if len(char_owners) == 1:
                    (only,) = char_owners
                    distinctive[only] += 1
            continue
        # Arabic script: clitics (ال، و، ف) attach to the word — substring
        # check, capped at ONE hit per word so single-letter entries cannot
        # inflate the count.
        if "ar" in lang_set and _ARABIC_RE.search(word):
            if any(ar_word in word for ar_word in AR):
                total["ar"] += 1
                distinctive["ar"] += 1
    return total, distinctive


def _script_bonus(text: str, lang: str, distinctive_hits: int) -> int:
    """Script-membership bonus — ONLY when the language already has ≥2
    distinctive word hits. Script alone is never evidence: umlauts are
    Finnish/Swedish/Turkish letters too, Cyrillic covers a dozen
    languages, kanji are shared with Chinese."""
    if distinctive_hits < 2:
        return 0
    if lang == "de" and _UMLAUT_RE.search(text):
        return 2
    if lang == "ru" and _CYRILLIC_RE.search(text):
        return 3
    if lang in ("ja", "zh") and _CJK_RE.search(text):
        return 3
    if lang == "ko" and _HANGUL_RE.search(text):
        return 3
    if lang == "ar" and _ARABIC_RE.search(text):
        return 3
    if lang == "el" and _GREEK_RE.search(text):
        return 2
    return 0


def score(
    text: str, languages: list[str] | None = None
) -> dict[str, int]:
    """Score text against function word sets for given languages.

    Args:
        text: Text to analyze
        languages: List of language codes to check. If None, check all available.

    Returns:
        Dict mapping language code to hit count (shared words count for
        every language whose list contains them).
    """
    if languages is None:
        languages = list(LANGUAGE_DICTS.keys())
    languages = [lang for lang in languages if lang in LANGUAGE_DICTS]
    total, _ = _score_details(text, languages)
    return total


def detect_confident(
    text: str, languages: list[str] | None = None
) -> str | None:
    """Language detection with confidence margin — None when unsure.

    Unlike :func:`detect` (which always answers, falling back to a default),
    this variant is for callers that have a BETTER fallback than a guess —
    e.g. adapter.py's per-turn voice-language override, whose fallback is the
    user's static profile pin.

    Behavior:
    1. Shared words (in several lists) add absolute evidence to every
       owner but can never create a margin on their own.
    2. Script bonuses require ≥2 distinctive word hits (script alone is
       never evidence — see _script_bonus).
    3. Script vetoes drop neighbour languages we would misname
       (Ukrainian≠ru, Persian/Urdu≠ar, kana≠zh).
    4. Markdown code (fences + inline spans) is stripped first.
    5. With MIXED distinctive evidence the winner must lead by
       ≥ _CONFIDENT_MARGIN AND have ≥ _CONFIDENT_MIN_HITS total.
       With effectively ONE-SIDED evidence (no other language has a
       distinctive hit) ≥ _CONFIDENT_ONE_SIDED_MIN total hits suffice
       (1 for short German text). Anything weaker returns None.

    Args:
        text: Text to analyze
        languages: List of language codes to check. If None, check all available.

    Returns:
        Language code if confident, None otherwise.
    """
    if languages is None:
        languages = list(LANGUAGE_DICTS.keys())
    languages = [lang for lang in languages if lang in LANGUAGE_DICTS]
    if not languages:
        return None

    text = _strip_code(text)
    veto = _vetoed_langs(text)
    candidates = [lang for lang in languages if lang not in veto]
    if not candidates:
        return None

    total, distinctive = _score_details(text, candidates)
    for lang in candidates:
        total[lang] += _script_bonus(text, lang, distinctive[lang])

    if all(v == 0 for v in total.values()):
        return None

    max_total = max(total.values())
    top = [lang for lang in candidates if total[lang] == max_total]
    if len(top) > 1:
        return None
    winner = top[0]

    # Effectively one-sided: no OTHER language has distinctive evidence —
    # whatever hits they have are words shared with the winner and cannot
    # contradict it.
    rival_distinctive = max(
        (distinctive[lang] for lang in candidates if lang != winner),
        default=0,
    )
    if rival_distinctive == 0:
        if distinctive[winner] == 0:
            return None  # shared/bonus-only evidence names no language
        min_hits = (
            _CONFIDENT_ONE_SIDED_MIN_SHORT
            if winner == "de" and len(text) < 50
            else _CONFIDENT_ONE_SIDED_MIN
        )
        return winner if total[winner] >= min_hits else None

    # Mixed evidence: margin on DISTINCTIVE hits, volume on total.
    runner_up_distinctive = rival_distinctive
    if (
        distinctive[winner] >= runner_up_distinctive + _CONFIDENT_MARGIN
        and total[winner] >= _CONFIDENT_MIN_HITS
    ):
        return winner
    return None


def detect(
    text: str, default: str = "de", languages: list[str] | None = None
) -> str:
    """Language detection that always returns a result.

    Args:
        text: Text to analyze
        default: Default language code if no hits or tie (default: "de",
            the historic contract of this module)
        languages: List of language codes to check. If None, check all available.

    Returns:
        Language code. Falls back to default if tie or no hits.
    """
    if languages is None:
        languages = list(LANGUAGE_DICTS.keys())
    languages = [lang for lang in languages if lang in LANGUAGE_DICTS]
    if not languages:
        return default

    veto = _vetoed_langs(text)
    candidates = [lang for lang in languages if lang not in veto] or languages

    total, distinctive = _score_details(text, candidates)
    if all(v == 0 for v in total.values()):
        return default

    for lang in candidates:
        total[lang] += _script_bonus(text, lang, distinctive[lang])

    max_total = max(total.values())
    winners = [lang for lang in candidates if total[lang] == max_total]
    if len(winners) == 1:
        return winners[0]
    if default in winners:
        return default
    return winners[0]


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Detect language in text (20 languages supported)"
    )
    ap.add_argument(
        "--default",
        default="de",
        choices=sorted(LANGUAGE_DICTS.keys()),
        help="Default language code if no hits or tie (default: de)",
    )
    ap.add_argument(
        "--lang",
        help="Comma-separated list of language codes to check (default: all available)",
    )
    ap.add_argument(
        "--list-langs",
        action="store_true",
        help="List all supported language codes and exit",
    )
    ap.add_argument(
        "text", nargs="*", help="Text to analyze; if omitted, read stdin"
    )
    args = ap.parse_args()

    if args.list_langs:
        print("Supported languages:")
        for code in sorted(LANGUAGE_DICTS.keys()):
            print(f"  {code}")
        return 0

    languages = None
    if args.lang:
        languages = [l.strip() for l in args.lang.split(",")]

    text = " ".join(args.text) if args.text else sys.stdin.read()
    print(detect(text, args.default, languages))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
