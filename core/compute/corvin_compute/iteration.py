"""Iteration record + history helpers (ADR-0013 Phase 13.2).

A single iteration is a (params -> loss) evaluation plus bookkeeping.
The :class:`IterRecord` is the immutable on-disk shape under
``compute/runs/<run_id>/iterations/<n>.json``.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import time
from typing import Any, Mapping


@dataclasses.dataclass(frozen=True)
class IterRecord:
    """One iteration's outcome — written once, never modified."""

    iter: int
    params: Mapping[str, Any]
    loss: float | None
    wall_ms: int
    ts: float
    cache_hit: bool = False
    param_fingerprint: str = ""
    error: str | None = None

    def to_json(self) -> dict[str, Any]:
        d = dataclasses.asdict(self)
        d["params"] = dict(self.params)
        return d

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> "IterRecord":
        return cls(
            iter=int(data["iter"]),
            params=dict(data.get("params", {})),
            loss=None if data.get("loss") is None else float(data["loss"]),
            wall_ms=int(data.get("wall_ms", 0)),
            ts=float(data.get("ts", 0.0)),
            cache_hit=bool(data.get("cache_hit", False)),
            param_fingerprint=str(data.get("param_fingerprint", "")),
            error=data.get("error"),
        )


def canonical_params(params: Mapping[str, Any]) -> str:
    """Deterministic JSON encoding (sorted keys, compact)."""
    return json.dumps(params, sort_keys=True, separators=(",", ":"))


def param_fingerprint(params: Mapping[str, Any], length: int = 16) -> str:
    """Return ``sha256:<hex[:length]>`` of the canonical-JSON params.

    Mirror of L23 voice-transcribe metadata rule — fingerprint is the
    only LLM-visible representation of the params.
    """
    digest = hashlib.sha256(canonical_params(params).encode("utf-8")).hexdigest()
    return f"sha256:{digest[:length]}"


def best_iter(history: list[IterRecord], minimise: bool = True) -> IterRecord | None:
    """Return the best record (lowest / highest loss), or None on empty."""
    valid = [h for h in history if h.loss is not None]
    if not valid:
        return None
    return min(valid, key=lambda r: r.loss) if minimise else max(valid, key=lambda r: r.loss)


def now_ts() -> float:
    return time.time()
