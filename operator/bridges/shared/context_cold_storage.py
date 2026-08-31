"""context_cold_storage.py — Layer 20 Phase-2: cold storage for paged-out turns.

When ``context_budget.evict()`` drops a turn from the working set, the
content is gone. Cold storage adds a second tier: dropped turns are
embedded into a vector, stored on disk with metadata, and can be
retrieved later via similarity search when a future turn references
something the agent should remember.

Architecture:

    working set (hot, in context window)
         ↓ evict()
    cold storage (vector-embedded on disk)
         ↑ page_in(query, top_k)

The embedding provider is **pluggable**. The MVP ships a deterministic
hash-based pseudo-embedding (``HashEmbeddingProvider``) for testing
and zero-network smoke runs. Production deployments would swap in
``OpenAIEmbeddingProvider`` (text-embedding-3-small) or a local
sentence-transformer. The cold-storage code never imports embedding
backends directly — callers inject the provider.

Storage layout::

    <corvin_home>/run/cold_storage/<session_id>/
        pages.jsonl    — one record per paged-out turn

Each record::

    {
      "stored_id":    "p_a1b2c3",
      "session_id":   "s_xyz",
      "turn_id":      "t_42",
      "tokens":       1500,
      "content":      "<original turn text>",
      "content_hash": "sha256(content)",
      "embedding":    [0.12, -0.05, ...],       # provider-specific dim
      "embedded_with": "hash-128" | "openai-text-embedding-3-small",
      "page_out_ts":  "2026-05-08T15:00:00Z",
      "metadata":     {...optional...}
    }

This module is pure bookkeeping + cosine-similarity arithmetic. It
does not call ``context_budget`` or any other layer; the integration
"evict from budget AND page out to cold storage" lives in the adapter
pre-flight gate, which is the deferred follow-up slice.
"""
from __future__ import annotations

import contextlib
from _compat_fcntl import fcntl  # portable: real fcntl on POSIX, no-op flock on Windows
import hashlib
import json
import math
import os
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Protocol

from paths import corvin_home  # type: ignore


# --------------------------------------------------------------------- abstract

class EmbeddingProvider(Protocol):
    """Pluggable embedding interface."""

    name: str         # used in stored records for traceability
    dim: int          # vector dimensionality

    def embed(self, text: str) -> List[float]:
        """Return a vector embedding of the given text."""


class HashEmbeddingProvider:
    """Deterministic hash-based embedding for tests + zero-network runs.

    NOT a real embedding — relevance ranking will be poor compared to
    a learned model. The point is that the cold-storage *machinery*
    works end-to-end without an external API call. Production MUST
    swap this out for a real embedder.

    **Production caveat — silent cliff on embedder swap.** The
    ``embedded_with`` field is the cross-provider safety key: pages
    embedded with provider A are skipped when querying with provider B
    (registry.py line ~280). When you swap from this provider to a real
    one (OpenAI / sentence-transformers), every existing page in cold
    storage becomes invisible to queries until you re-embed. Two ways
    to handle the transition:

      1. Purge cold storage on provider swap: ``ColdStorage.purge_session()``
         per session, then accept the lost history.
      2. Re-embed: walk every page, run the new provider's ``embed()``
         on the stored ``content``, write back the new ``embedding`` +
         ``embedded_with``. (Helper not yet in this module — Phase-4.)

    **Production caveat — similarity thresholds don't transfer.** A
    ``min_similarity=0.7`` threshold tuned against this provider will
    behave very differently against OpenAI's text-embedding-3-small.
    Re-tune after switching.

    Construction is parameterised by ``dim`` so tests can verify
    different sizes; the default 128 is plenty for the hash-based
    distribution.
    """

    def __init__(self, dim: int = 128) -> None:
        self.name = f"hash-{dim}"
        self.dim = dim

    def embed(self, text: str) -> List[float]:
        # Hash text into bytes, expand to dim floats by chunking,
        # normalize to unit length so cosine similarity is meaningful.
        seed = hashlib.sha256(text.encode("utf-8")).digest()
        vec: List[float] = []
        i = 0
        while len(vec) < self.dim:
            chunk = hashlib.sha256(seed + i.to_bytes(4, "big")).digest()
            for b in chunk:
                if len(vec) >= self.dim:
                    break
                # Map [0, 255] to [-1, 1]
                vec.append((b / 127.5) - 1.0)
            i += 1
        # Normalize to unit length so cosine similarity is correct
        norm = math.sqrt(sum(x * x for x in vec))
        if norm > 0:
            vec = [x / norm for x in vec]
        return vec


# --------------------------------------------------------------------- math

def cosine(a: List[float], b: List[float]) -> float:
    if len(a) != len(b):
        raise ValueError(f"vector dims differ: {len(a)} vs {len(b)}")
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


# --------------------------------------------------------------------- paths

def _root_dir() -> Path:
    d = corvin_home() / "run" / "cold_storage"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _session_dir(session_id: str) -> Path:
    # Reject path traversal AND null bytes (which truncate the
    # filename on some filesystems, allowing 'foo\x00bar' to land
    # at 'foo' silently). Conservative whitelist of name shapes;
    # callers using opaque ids (secrets.token_hex, uuids) are
    # unaffected.
    if (
        not session_id
        or "/" in session_id
        or ".." in session_id
        or "\x00" in session_id
    ):
        raise ValueError(f"invalid session_id: {session_id!r}")
    d = _root_dir() / session_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def _pages_path(session_id: str) -> Path:
    return _session_dir(session_id) / "pages.jsonl"


def _lock_path(session_id: str) -> Path:
    return _session_dir(session_id) / "pages.lock"


@contextlib.contextmanager
def _exclusive_lock(session_id: str) -> Iterator[None]:
    fd = os.open(str(_lock_path(session_id)), os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


# --------------------------------------------------------------------- io

def _now_iso() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def _read_pages(session_id: str) -> List[Dict[str, Any]]:
    p = _pages_path(session_id)
    if not p.exists():
        return []
    out: List[Dict[str, Any]] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def _write_pages(session_id: str, pages: List[Dict[str, Any]]) -> None:
    p = _pages_path(session_id)
    tmp = p.with_suffix(p.suffix + ".tmp")
    payload = "\n".join(
        json.dumps(p, separators=(",", ":"), sort_keys=True) for p in pages
    )
    if payload:
        payload += "\n"
    tmp.write_text(payload, encoding="utf-8")
    os.replace(tmp, p)


# --------------------------------------------------------------------- api

class ColdStorage:
    """Per-session cold storage.

    Construction is cheap; instances do not hold open files. Each call
    re-reads pages.jsonl under the session lock — fine for chat-scale
    cardinality (dozens of pages per session, not millions).
    """

    def __init__(
        self,
        session_id: str,
        provider: Optional[EmbeddingProvider] = None,
    ) -> None:
        if not session_id:
            raise ValueError("session_id must be non-empty")
        self.session_id = session_id
        self.provider: EmbeddingProvider = provider or HashEmbeddingProvider()

    def page_out(
        self,
        turn_id: str,
        content: str,
        tokens: int,
        *,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Embed and persist a turn. Returns the stored_id."""
        if tokens < 0:
            raise ValueError(f"tokens must be non-negative, got {tokens}")
        vec = self.provider.embed(content)
        if len(vec) != self.provider.dim:
            raise ValueError(
                f"provider {self.provider.name} returned vector of "
                f"length {len(vec)}, expected {self.provider.dim}"
            )
        stored_id = "p_" + secrets.token_hex(4)
        record = {
            "stored_id": stored_id,
            "session_id": self.session_id,
            "turn_id": turn_id,
            "tokens": int(tokens),
            "content": content,
            "content_hash": hashlib.sha256(
                content.encode("utf-8")
            ).hexdigest(),
            "embedding": vec,
            "embedded_with": self.provider.name,
            "page_out_ts": _now_iso(),
            "metadata": metadata or {},
        }
        with _exclusive_lock(self.session_id):
            pages = _read_pages(self.session_id)
            pages.append(record)
            _write_pages(self.session_id, pages)
        return stored_id

    def page_in(
        self,
        query: str,
        top_k: int = 3,
        *,
        min_similarity: float = 0.0,
    ) -> List[Dict[str, Any]]:
        """Return the top_k pages most similar to the query.

        Cosine similarity. Pages stored with a different
        ``embedded_with`` provider than the current one are skipped —
        embeddings from different models are not directly comparable.
        """
        if top_k <= 0:
            raise ValueError("top_k must be positive")
        pages = _read_pages(self.session_id)
        if not pages:
            return []
        q_vec = self.provider.embed(query)
        scored: List[tuple] = []
        for p in pages:
            if p.get("embedded_with") != self.provider.name:
                continue  # incompatible embedding
            sim = cosine(q_vec, p["embedding"])
            if sim >= min_similarity:
                scored.append((sim, p))
        scored.sort(key=lambda x: -x[0])
        out = []
        for sim, p in scored[:top_k]:
            r = dict(p)
            r["similarity"] = round(sim, 6)
            out.append(r)
        return out

    def list_paged(self) -> List[Dict[str, Any]]:
        """All paged-out records for this session, oldest first.

        The embedding is omitted from the result (it would dominate
        log output and isn't useful at this layer); callers who need
        it can use ``get(stored_id)`` instead.
        """
        out = []
        for p in _read_pages(self.session_id):
            r = {k: v for k, v in p.items() if k != "embedding"}
            out.append(r)
        return out

    def get(self, stored_id: str) -> Optional[Dict[str, Any]]:
        for p in _read_pages(self.session_id):
            if p["stored_id"] == stored_id:
                return p
        return None

    def restore_one(self, stored_id: str) -> Optional[Dict[str, Any]]:
        """Pop a single page and return it (read-and-remove).

        Use when the caller wants to bring a specific turn back into
        the working set. Returns None if the stored_id isn't found.
        """
        with _exclusive_lock(self.session_id):
            pages = _read_pages(self.session_id)
            for i, p in enumerate(pages):
                if p["stored_id"] == stored_id:
                    del pages[i]
                    _write_pages(self.session_id, pages)
                    return p
            return None

    def purge_session(self) -> int:
        """Delete every page for this session. Returns count removed."""
        with _exclusive_lock(self.session_id):
            pages = _read_pages(self.session_id)
            n = len(pages)
            _pages_path(self.session_id).unlink(missing_ok=True)
        # remove lock file outside the locked region
        _lock_path(self.session_id).unlink(missing_ok=True)
        try:
            _session_dir(self.session_id).rmdir()
        except OSError:
            pass  # not empty (lock file race) — fine
        return n

    def page_count(self) -> int:
        return len(_read_pages(self.session_id))

    def total_tokens(self) -> int:
        return sum(int(p.get("tokens", 0)) for p in _read_pages(self.session_id))


# --------------------------------------------------------------------- helpers

def list_sessions_with_pages() -> List[str]:
    """Return all session_ids that have paged content."""
    root = _root_dir()
    out = []
    for d in sorted(root.iterdir()):
        if d.is_dir() and (d / "pages.jsonl").exists():
            out.append(d.name)
    return out
