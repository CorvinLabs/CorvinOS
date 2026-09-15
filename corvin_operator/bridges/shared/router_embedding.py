"""router_embedding.py — auto-routing via OpenAI text embeddings.

The motivation: Max-subscription users have an OPENAI_API_KEY (used for
TTS + Whisper) but typically not an ANTHROPIC_API_KEY. The `claude -p`
CLI fallback is technically usable but takes ~12 s per request — too
slow to be the default routing path.

`text-embedding-3-small` solves both problems:
  - Latency 100-300 ms.
  - Cost ~$0.02 per 1M tokens; an average user message (~30 tokens)
    is ~$0.0000006 per routing decision.
  - Multilingual out of the box (DE/EN match the same anchors).

How it works
------------
1. Each persona's JSON file declares `routing_anchors` — 5-8 short
   example phrases that are typical for the role. We embed them once
   and cache to disk; re-embed only when a persona file's mtime changes.
2. For every incoming message we embed the text, compute the cosine
   similarity against each persona's anchor embeddings (max over the
   anchor set), and return the top persona if the score clears the
   threshold (default 0.35).
3. Personas with `routing_exclude: true` (e.g. `assistant`) are kept
   out of the pool — they're only reachable via explicit pin.

This module is dependency-light: only `openai` (already a hard dep
because the rest of the bridge needs it for TTS/Whisper) and stdlib.
"""
from __future__ import annotations

import json
import math
import os
import sys
from pathlib import Path
from typing import Any

try:
    from .paths import voice_dir  # type: ignore
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from paths import voice_dir  # type: ignore


def _router_cache_dir() -> Path:
    """Honour ``XDG_CACHE_HOME`` (legacy override) or root under ``voice_dir()``.

    The router cache stores embedding vectors for persona anchors, so it
    is conceptually closer to "build artefact" than "config" — we keep
    XDG_CACHE_HOME priority for users who pin all caches to one root.
    """
    xdg = os.environ.get("XDG_CACHE_HOME")
    if xdg:
        return Path(xdg) / "corvin-voice"
    return voice_dir() / "router-cache"


# Where the anchor embeddings get cached.
_CACHE_DIR = _router_cache_dir()
_CACHE_FILE = _CACHE_DIR / "routing-anchors.json"

# OpenAI's smallest, cheapest, fastest embedding model.
_MODEL = os.environ.get("ROUTER_EMBED_MODEL", "text-embedding-3-small")

# Score threshold below which we say "no confident pick" and let the
# adapter fall through to the fallback persona.
DEFAULT_THRESHOLD = 0.35

# Test hook: ROUTER_EMBED_FAKE=1 + ROUTER_EMBED_FAKE_TEXT_VECTOR / ANCHORS_VECTOR
# bypass any real OpenAI call. Used by test_router_embedding.py.
_FAKE_FLAG = "ROUTER_EMBED_FAKE"


# ─── primitives ──────────────────────────────────────────────────────────

def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    return dot / (na * nb) if na and nb else 0.0


def _embed_real(text: str) -> list[float]:
    """Real OpenAI call. Lazy-imports the SDK so the module is loadable
    on systems without it (only fails when actually invoked)."""
    from openai import OpenAI  # noqa
    client = OpenAI()
    r = client.embeddings.create(input=text, model=_MODEL)
    return r.data[0].embedding


def _embed_fake(text: str) -> list[float]:
    """Deterministic fake embedding: hash-derived 8-dim vector."""
    import hashlib
    h = hashlib.sha256(text.encode("utf-8", errors="replace")).digest()
    # 8 floats in [-1, 1]
    out = [(b - 128) / 128.0 for b in h[:8]]
    # Normalise to unit length so cosine similarity is well-behaved.
    n = math.sqrt(sum(x * x for x in out)) or 1.0
    return [x / n for x in out]


def _embed(text: str) -> list[float]:
    if os.environ.get(_FAKE_FLAG) == "1":
        return _embed_fake(text)
    return _embed_real(text)


# ─── cache ───────────────────────────────────────────────────────────────

def _personas_signature(personas: list[dict[str, Any]]) -> dict[str, Any]:
    """Identity of the (anchor sets that matter for) routing. We use the
    persona names + the anchors themselves; if either changes, the cache
    is rebuilt."""
    sig: dict[str, list[str]] = {}
    for p in personas:
        if p.get("routing_exclude"):
            continue
        anchors = p.get("routing_anchors") or []
        if anchors:
            sig[p["name"]] = list(anchors)
    return sig


def _load_cache() -> dict[str, Any] | None:
    try:
        return json.loads(_CACHE_FILE.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def _save_cache(payload: dict[str, Any]) -> None:
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = _CACHE_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False))
    tmp.replace(_CACHE_FILE)


def _ensure_anchors(personas: list[dict[str, Any]]) -> dict[str, list[list[float]]]:
    """Return {persona_name: [embedding, embedding, ...]}. Cached on disk.

    Cache invalidation: signature compares persona names + anchor texts.
    Any change → re-embed the changed personas (others stay).
    """
    want_sig = _personas_signature(personas)
    if not want_sig:
        return {}
    cached = _load_cache() or {}
    cached_sig = cached.get("signature", {})
    cached_embs: dict[str, list[list[float]]] = cached.get("embeddings", {})

    if cached_sig == want_sig and all(name in cached_embs for name in want_sig):
        return cached_embs

    # Re-embed only the changed/new ones; keep the rest from cache.
    out: dict[str, list[list[float]]] = {}
    for name, anchors in want_sig.items():
        if cached_sig.get(name) == anchors and name in cached_embs:
            out[name] = cached_embs[name]
            continue
        out[name] = [_embed(a) for a in anchors]
    _save_cache({"signature": want_sig, "embeddings": out})
    return out


# ─── public API ──────────────────────────────────────────────────────────

def route(text: str, personas: list[dict[str, Any]], *,
          threshold: float = DEFAULT_THRESHOLD) -> dict[str, Any] | None:
    """Embedding-based router. Returns `{persona, confidence, why}` or None.

    None is returned when:
      - text is empty,
      - no persona declares any `routing_anchors`,
      - the top similarity score is below `threshold`,
      - or the embedding call itself fails (logged via raise → caller decides).

    Embedding failures bubble up as exceptions; the typical caller wraps
    `route()` in try/except and falls back to heuristic / default.
    """
    if not text or not text.strip():
        return None
    pool = [p for p in (personas or []) if not p.get("routing_exclude")]
    if not pool:
        return None

    anchors = _ensure_anchors(pool)
    if not anchors:
        return None

    msg_emb = _embed(text)

    scores: dict[str, float] = {}
    for name, anchor_embs in anchors.items():
        if not anchor_embs:
            continue
        scores[name] = max(_cosine(msg_emb, a) for a in anchor_embs)
    if not scores:
        return None

    best = max(scores, key=scores.get)
    if scores[best] < threshold:
        return None

    runners = sorted(scores.items(), key=lambda kv: -kv[1])[1:3]
    raw_sim = scores[best]
    # Cosine sim is a [0,1] score on a different distribution than the
    # LLM-router's "confidence" (which is calibrated to be ≥ 0.5 for
    # confident picks). Map [threshold, 1.0] linearly to [0.5, 1.0] so the
    # outer router's min_confidence=0.5 filter doesn't drop legitimate
    # picks that "only" scored 0.4 cosine.
    span = max(1.0 - threshold, 1e-6)
    confidence = 0.5 + (raw_sim - threshold) / span * 0.5
    confidence = max(0.0, min(1.0, confidence))

    why = f"top-sim={raw_sim:.2f}"
    if runners:
        why += "; runners-up: " + ", ".join(f"{k}:{v:.2f}" for k, v in runners)
    return {"persona": best, "confidence": confidence, "why": why}
