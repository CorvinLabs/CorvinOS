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

#: A model id as engines and providers spell it: ``claude-sonnet-5``, ``claude-haiku-4-5-20251001``,
#: ``eu.anthropic.claude-sonnet-5``, ``anthropic/claude-opus-5``, ``...-v2:0``, ``claude-opus-5[1m]``.
#: No whitespace, so a sentence can never pass as a model.
MODEL_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@\[\]-]{0,127}$")


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
    return isinstance(value, str) and bool(MODEL_ID_RE.match(value))


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
