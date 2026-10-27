"""The engine-attested usage block of a signed A2A response (ADR-2292).

A peer's engine bills the peer; the requester used to see an answer and a duration and nothing about what
the run consumed. The receiver already measures it for its own worker span (ADR-0759: the model the ENGINE
reported via ``agents.attested_model`` and the four-way token split via ``engine_span.usage_split``). This
module turns that measurement into one closed, signable object and back:

    {"model_id": str, "input_tokens": int, "output_tokens": int,
     "cache_read_tokens": int, "cache_write_tokens": int, "source": "engine_attested"}

Rules (both sides):

* Built only when the engine reported a model. No model = no block (unpriced is honest; a guessed id
  prices the run at the wrong rate). The response is then byte-identical to the pre-ADR-2292 shape.
* Counts are the engine's report, never a price. There is no dollar field and none is ever accepted: the
  requester prices the tokens with ITS OWN rate card, or shows tokens only for a model it does not know.
* Closed keys. On the requester side an unknown key is dropped; a count that is not a non-negative int
  (a bool is not an int) or exceeds :data:`MAX_TOKENS`, a ``model_id`` that is not model-id shaped (free
  text), or a ``source`` other than ``engine_attested`` drops the WHOLE block — a partly valid split would
  misprice the run.
* The block is attributed, not verified: it is what the peer's engine said. The HMAC proves the PEER sent
  it, not that the numbers are true.

Nothing here selects or ranks agents: selection ignores the block (ADR-2292 §4).
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

SOURCE = "engine_attested"

#: The four billed token columns — the same names ``engine_span.END_FIELDS`` carries.
COUNT_FIELDS: tuple[str, ...] = ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens")
BLOCK_KEYS = frozenset({"model_id", "source", *COUNT_FIELDS})

#: The scalars an audit record carries for a block (no ``source``: it is a constant).
AUDIT_FIELDS: tuple[str, ...] = ("model_id", *COUNT_FIELDS)

#: Upper bound of one count. Ten billion tokens is orders of magnitude above any single run; a larger
#: value is a forged or corrupted block, not a measurement.
MAX_TOKENS = 10 ** 10

#: The Claude naming scheme (the same one ``model_lineage._ID_RE`` parses), as engines and providers spell it:
#: ``claude-sonnet-5``, ``claude-haiku-4-5-20251001``, ``anthropic/claude-opus-5``,
#: ``eu.anthropic.claude-sonnet-5-v2:0``, ``claude-sonnet-5@20250929`` (Vertex), ``claude-opus-5[1m]``, and the
#: legacy Claude 3 order ``claude-3-5-sonnet-20241022`` / ``anthropic/claude-3.7-sonnet``.
_CLAUDE_ID_RE = re.compile(
    r"(?:anthropic/|(?:(?:eu|us|apac|global|au|jp)\.)?anthropic\.)?"
    r"claude-(?:(?:haiku|sonnet|opus|fable|mythos)-\d{1,2}(?:[-.]\d{1,2})?|\d(?:[-.]\d)?-(?:haiku|sonnet|opus))"
    r"(?:-(?P<d1>\d{8})|-latest)?(?:-v\d{1,2}(?::\d{1,2})?|@(?P<d2>\d{8}))?(?:\[1m\])?"
)

#: Third-party families: a CLOSED list of the leading word. A model id is peer-signed text that lands in the
#: tenant audit chain; free text cannot be told from a model name by shape alone, so after the family every
#: token must be a version, a size, a quantisation, ONE plausible release date, or a word from
#: :data:`_QUALIFIERS`. An id of a family not listed drops the block - unpriced is honest.
_FAMILIES = frozenset({
    "gpt", "chatgpt", "codex", "o", "gemini", "gemma", "llama", "codellama", "tinyllama", "mistral", "mixtral",
    "codestral", "ministral", "pixtral", "magistral", "devstral", "qwen", "qwq", "deepseek", "grok", "command",
    "phi", "kimi", "glm", "sonar", "nova", "titan", "zephyr", "yi", "dolphin", "vicuna", "falcon", "olmo",
    "smollm", "starcoder", "granite", "llava", "nemotron", "hermes", "orca", "solar", "jamba", "ernie",
    "open", "nomic", "mxbai", "bge", "snowflake", "openchat", "wizardlm", "neural", "stable", "tulu", "internlm", "minicpm",
})
#: Vendor prefixes (``vendor/model``) and Bedrock third-party prefixes (``[us.]vendor.model``).
_VENDORS = frozenset({
    "openai", "google", "meta", "meta-llama", "mistral", "mistralai", "deepseek", "deepseek-ai", "qwen", "xai",
    "x-ai", "cohere", "moonshot", "moonshotai", "perplexity", "amazon", "nvidia", "microsoft", "ai21", "zai-org",
    "z-ai",
})
_QUALIFIERS = frozenset({
    "latest", "preview", "instruct", "chat", "mini", "nano", "small", "medium", "large", "lite", "flash", "pro",
    "max", "turbo", "codex", "coder", "audio", "vision", "thinking", "cloud", "haiku", "sonnet", "opus", "oss",
    "base", "code", "reasoner", "versatile", "embed", "text", "express", "premier", "plus", "r", "exp",
    "experimental", "it", "qat", "air", "mistral", "image", "search", "dense", "nemo", "moe", "mixtral", "next", "vl", "guard", "fp16", "embedding", "realtime", "transcribe", "tts", "ultra", "fast", "reasoning",
})
_BEDROCK_RE = re.compile(r"(?:(?:eu|us|apac|global|au|jp)\.)?(" + "|".join(
    sorted(re.escape(v) for v in _VENDORS if "-" not in v)) + r")\.")
_FAMILY_TOKEN_RE = re.compile(r"([a-z]+)(\d[\d.]*)?")
_VERSION_RE = re.compile(r"v?\d{1,3}(?:\.\d{1,3}){0,3}[a-z]?")
_SIZE_RE = re.compile(r"(?:a|\d{1,2}x)?\d{1,4}(?:\.\d)?[bkmt]")
_QUANT_RE = re.compile(r"[qQ]\d(?:_[KkMmSsLl0-9])*")
_VARIANT_RE = re.compile(r"[a-z]\d{1,2}")  # k2, r1, v3 as in kimi-k2, deepseek-r1
_YEAR_LO, _YEAR_HI = 2019, 2030
_GENERIC_MAX_LEN = 64
_GENERIC_MAX_TOKENS = 10
_GENERIC_MAX_NUMBERS = 4
_GENERIC_MAX_WORDS = 4
_GENERIC_MAX_SHAPES = 2  # size / variant / quantisation tokens together
_GENERIC_MAX_DIGITS = 10  # digit characters in the whole id
_GENERIC_MAX_BARE_INTS = 2  # plain integer tokens; ONE when a date is present (a split date is a date)

#: Kept for importers that want a cheap pre-filter; the authority is :func:`valid_model_id`.
MODEL_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@\[\]-]{0,127}$")


def _ymd_ok(y: int, m: int, d: int) -> bool:
    return _YEAR_LO <= y <= _YEAR_HI and 1 <= m <= 12 and 1 <= d <= 31


def _date8_ok(run: str) -> bool:
    return len(run) == 8 and run.isdigit() and _ymd_ok(int(run[:4]), int(run[4:6]), int(run[6:]))


def _is2(t: str) -> bool:
    return len(t) == 2 and t.isdigit()


def _generic_ok(value: str) -> bool:
    if len(value) > _GENERIC_MAX_LEN or not value.isascii():
        return False
    body = value
    if "/" in body:
        vendor, _, body = body.partition("/")
        if vendor not in _VENDORS or "/" in body:
            return False
    else:
        m = _BEDROCK_RE.match(body)
        if m:
            body = body[m.end():]
    if body.count(":") > 1 or not body:
        return False
    toks = body.replace(":", "-").split("-")
    if len(toks) > _GENERIC_MAX_TOKENS or any(not t for t in toks):
        return False
    fam = _FAMILY_TOKEN_RE.fullmatch(toks[0])
    if not fam or fam.group(1) not in _FAMILIES or (fam.group(1) == "o" and not fam.group(2)):
        return False
    dates = numbers = words = shapes = bare = 0
    i = 1
    while i < len(toks):
        t = toks[i]
        step = 1
        if t.isdigit() and len(t) == 8:
            if not _date8_ok(t):
                return False
            dates += 1
        elif t.isdigit() and len(t) == 4:
            if i + 2 < len(toks) + 0 and _is2(toks[i + 1]) and _is2(toks[i + 2]) \
                    and _ymd_ok(int(t), int(toks[i + 1]), int(toks[i + 2])):
                step = 3
                dates += 1
            elif _YEAR_LO % 100 <= int(t[:2]) <= _YEAR_HI % 100 and 1 <= int(t[2:]) <= 12:  # YYMM
                dates += 1
            elif 1 <= int(t[:2]) <= 12 and 1 <= int(t[2:]) <= 31:  # MMDD
                dates += 1
            else:
                return False
        elif _is2(t) and i + 1 < len(toks) and _is2(toks[i + 1]) \
                and 1 <= int(t) <= 12 and 1 <= int(toks[i + 1]) <= 31:
            step = 2
            dates += 1
        elif t in _QUALIFIERS:
            words += 1
        elif _SIZE_RE.fullmatch(t) or _QUANT_RE.fullmatch(t) or _VARIANT_RE.fullmatch(t):
            shapes += 1
        elif _VERSION_RE.fullmatch(t):
            numbers += 1
            bare += t.isdigit()
        else:
            return False
        i += step
    return (dates <= 1 and numbers <= _GENERIC_MAX_NUMBERS and words <= _GENERIC_MAX_WORDS
            and shapes <= _GENERIC_MAX_SHAPES and sum(c.isdigit() for c in value) <= _GENERIC_MAX_DIGITS
            and bare <= (1 if dates else _GENERIC_MAX_BARE_INTS))


def _claude_ok(value: str) -> bool:
    m = _CLAUDE_ID_RE.fullmatch(value)
    if not m:
        return False
    return all(_date8_ok(d) for d in (m.group("d1"), m.group("d2")) if d)


def _usage_split(usage: Any) -> dict[str, int]:
    """``engine_span.usage_split`` — THE normaliser (ADR-0759). Never a private copy."""
    try:
        import engine_span  # type: ignore[import-not-found]
    except ImportError:
        import sys

        shared = Path(__file__).resolve().parent
        if str(shared) not in sys.path:
            sys.path.insert(0, str(shared))
        import engine_span  # type: ignore[import-not-found]
    return engine_span.usage_split(usage)


def valid_model_id(value: object) -> bool:
    """Closed grammar: a known Claude spelling, or a known third-party family in a tight shape.

    Anything else is free text from a peer and is refused - it would otherwise reach the tenant audit chain.
    """
    if not isinstance(value, str) or not value.isascii() or len(value) > 128:
        return False
    return _claude_ok(value) or _generic_ok(value)


def _valid_count(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= MAX_TOKENS


def build_usage_block(model_id: object, usage: Any) -> dict | None:
    """Receiver side: the block for a drained spawn, or ``None`` when the engine reported no model.

    ``model_id`` is ``agents.attested_model(result)``; ``usage`` the engine's raw usage object.
    """
    if not valid_model_id(model_id):
        return None
    split = _usage_split(usage)
    if not all(_valid_count(split.get(k)) for k in COUNT_FIELDS):
        return None
    return {"model_id": model_id, **{k: int(split[k]) for k in COUNT_FIELDS}, "source": SOURCE}


def parse_usage_block(raw: object) -> dict:
    """Requester side: the closed block from a VERIFIED response, ``{}`` when absent or malformed."""
    if not isinstance(raw, dict) or len(raw) > 4 * len(BLOCK_KEYS):
        return {}
    if raw.get("source") != SOURCE or not valid_model_id(raw.get("model_id")):
        return {}
    if not all(_valid_count(raw.get(k)) for k in COUNT_FIELDS):
        return {}
    return {"model_id": raw["model_id"], **{k: raw[k] for k in COUNT_FIELDS}, "source": SOURCE}


def audit_fields(block: object) -> dict:
    """The scalars an audit record carries for a block; ``{}`` for none (no field is emitted at all)."""
    if not isinstance(block, dict) or not block:
        return {}
    return {k: block[k] for k in AUDIT_FIELDS if k in block}


__all__ = [
    "AUDIT_FIELDS", "BLOCK_KEYS", "COUNT_FIELDS", "MAX_TOKENS", "MODEL_ID_RE", "SOURCE",
    "audit_fields", "build_usage_block", "parse_usage_block", "valid_model_id",
]
