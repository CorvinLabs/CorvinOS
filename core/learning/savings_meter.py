"""Savings meter — measured saving of the automatic model tiers (ADR-2259, PLAN-0955 S2).

What it answers: "this UTC month, how much did the models that actually ran cost
compared with running the SAME tokens on the default tier model?"

    counterfactual = tokens priced at the default tier model
    actual         = tokens priced at the model that ran
    saved          = counterfactual - actual          (net: a turn on a dearer model subtracts)
    ratio          = saved / counterfactual

Every figure derives from real ``os_turn.completed`` records on the tenant's
hash-chained audit chain, priced with the ONE rate card held in
``model_selection_learner`` — there is no constant "saving" anywhere (the dead
``TokenSavingsTracker`` this replaces had ``simple 100 -> 80``). A turn on an
unrecognised model, or with no token counts, is counted in ``total_turns`` and
left out of the money, never estimated.

Why per-model TOKEN SUMS and not dollar sums: cost is linear in tokens, so the
counterfactual for ANY default model is recomputed exactly from the same sums.
Changing the default tier in the Console never forces a rescan.

Tamper-evidence, not a lock: the number comes from the chain, so deleting a
counter file resets nothing; deleting chain history breaks the ADR-0232 boot
tripwire. The aggregate is derivable — a missing, shrunk or replaced chain is
rebuilt from scratch, never trusted over the chain.

Cost: the first read per process scans the month (byte-prefiltered, ~0.3 s for a
200 MB chain); later reads are served from memory for ``max_age_s`` and then
advance from a byte offset, so a turn never pays for a rescan.
"""
from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from core.learning.model_selection_learner import (
    _price_for_model,
    _turn_cost_usd,
)

_NEEDLE = b'"os_turn.completed"'
_DEFAULT_MAX_AGE_S = 20.0


@dataclass(frozen=True)
class MeterReading:
    month: str                    # "YYYY-MM" (UTC)
    counterfactual_usd: float
    actual_usd: float
    saved_usd: float
    counted_turns: int            # priced, with token counts
    total_turns: int              # every completed OS turn in the window
    ratio: Optional[float]        # saved / counterfactual, None when not measurable
    default_model: str
    priced: bool                  # False -> the default model is not in the rate card


class _Acc:
    __slots__ = ("month", "epoch", "path", "inode", "offset", "by_model",
                 "total_turns", "loaded_at")

    def __init__(self, month: str, epoch: float, path: Path) -> None:
        self.month = month
        self.epoch = epoch
        self.path = path
        self.inode = -1
        self.offset = 0
        # model -> [input, output, cache_write, cache_read, turns]
        self.by_model: dict[str, list[int]] = {}
        self.total_turns = 0
        self.loaded_at = 0.0


_LOCK = threading.Lock()
_CACHE: dict[tuple[str, str], _Acc] = {}


def reset_cache() -> None:
    """Drop every aggregate (tests; a counting-epoch change also self-heals)."""
    with _LOCK:
        _CACHE.clear()


def _epoch_ts(tenant_id: str) -> float:
    """ADR-0760: the SAME counting epoch every other cost reader applies."""
    try:
        from core.console.corvin_console import usage_epoch  # noqa: PLC0415

        return float(usage_epoch.epoch_ts(tenant_id))
    except Exception:  # noqa: BLE001 — no console in this process: count everything
        return 0.0


def _chain_path(tenant_id: str) -> Path:
    from core.paths.tenant import tenant_audit_chain  # noqa: PLC0415

    return tenant_audit_chain(tenant_id)


def _month_key(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m")


def _ingest(raw: bytes, acc: _Acc) -> None:
    try:
        rec = json.loads(raw.decode("utf-8", errors="replace"))
    except json.JSONDecodeError:
        return
    if not isinstance(rec, dict) or rec.get("event_type") != "os_turn.completed":
        return
    try:
        ts = float(rec.get("ts"))
    except (TypeError, ValueError):
        return
    if (acc.epoch and ts < acc.epoch) or _month_key(ts) != acc.month:
        return
    det = rec.get("details") or {}
    acc.total_turns += 1
    model = str(det.get("model") or "")
    tok = [int(det.get("input_tokens") or 0), int(det.get("output_tokens") or 0),
           int(det.get("cache_creation_input_tokens") or 0),
           int(det.get("cache_read_input_tokens") or 0)]
    if not model or not any(tok) or _price_for_model(model) is None:
        return
    slot = acc.by_model.setdefault(model, [0, 0, 0, 0, 0])
    for n in range(4):
        slot[n] += tok[n]
    slot[4] += 1


def _scan(path: Path, start: int, acc: _Acc) -> int:
    """Feed complete lines from ``start``; return the offset after the last one.

    A trailing line without ``\\n`` is a record still being written — it is left
    for the next read, never half-parsed.
    """
    offset = start
    try:
        with path.open("rb") as fh:
            fh.seek(start)
            while True:
                raw = fh.readline()
                if not raw or not raw.endswith(b"\n"):
                    break
                offset += len(raw)
                if _NEEDLE in raw:
                    _ingest(raw, acc)
    except OSError:
        pass
    return offset


def _rebuild(acc: _Acc) -> None:
    acc.by_model.clear()
    acc.total_turns = 0
    acc.offset = 0
    try:
        st = acc.path.stat()
    except OSError:
        acc.inode = -1
        return
    acc.inode = st.st_ino
    # ADR-2058: seam-linked HISTORY files first (a chain loss restarts the
    # canonical file empty), then the chain itself, whose offset we keep.
    try:
        from core.paths.chain_history import chain_history_files  # noqa: PLC0415

        for hist in chain_history_files(acc.path):
            _scan(Path(hist), 0, acc)
    except Exception:  # noqa: BLE001 — history is additive, never fatal
        pass
    acc.offset = _scan(acc.path, 0, acc)


def _advance(acc: _Acc) -> None:
    try:
        st = acc.path.stat()
    except OSError:
        acc.by_model.clear()
        acc.total_turns = 0
        acc.offset = 0
        acc.inode = -1
        return
    if st.st_ino != acc.inode or st.st_size < acc.offset:
        _rebuild(acc)          # replaced, rotated or truncated: never trust the old offset
    elif st.st_size > acc.offset:
        acc.offset = _scan(acc.path, acc.offset, acc)


def _reading(acc: _Acc, default_model: str) -> MeterReading:
    d_price = _price_for_model(default_model)
    actual = cf = 0.0
    counted = 0
    for model, (i, o, cw, cr, n) in acc.by_model.items():
        price = _price_for_model(model)
        if price is None:
            continue
        counted += n
        actual += _turn_cost_usd(price, i, o, cw, cr, model)
        if d_price is not None:
            cf += _turn_cost_usd(d_price, i, o, cw, cr, default_model)
    priced = d_price is not None
    saved = cf - actual if priced else 0.0
    ratio = (saved / cf) if priced and cf > 0 else None
    return MeterReading(
        month=acc.month,
        counterfactual_usd=cf if priced else 0.0,
        actual_usd=actual,
        saved_usd=saved,
        counted_turns=counted,
        total_turns=acc.total_turns,
        ratio=ratio,
        default_model=default_model,
        priced=priced,
    )


_LOADING: set[tuple[str, str]] = set()


def _load_in_background(key: tuple[str, str], month: str, epoch: float, path: Path) -> None:
    try:
        acc = _Acc(month, epoch, path)
        _rebuild(acc)
        acc.loaded_at = time.monotonic()
        with _LOCK:
            _CACHE[key] = acc
    except Exception:  # noqa: BLE001 — a failed warm-up just means "not measured yet"
        pass
    finally:
        with _LOCK:
            _LOADING.discard(key)


def read(
    tenant_id: str,
    default_model: str,
    *,
    now: Optional[float] = None,
    chain_path: Optional[Path] = None,
    max_age_s: float = _DEFAULT_MAX_AGE_S,
    block: bool = True,
) -> Optional[MeterReading]:
    """The current UTC month's measured saving against ``default_model``.

    ``block=False`` is for the request path: when no aggregate for this month is
    in memory yet it starts the (~0.3 s) build on a daemon thread and returns
    ``None`` ("not measured yet") instead of stalling the caller's event loop.
    """
    clock = time.time() if now is None else float(now)
    month = _month_key(clock)
    path = Path(chain_path) if chain_path is not None else _chain_path(tenant_id)
    epoch = _epoch_ts(tenant_id)
    key = (tenant_id, str(path))
    with _LOCK:
        acc = _CACHE.get(key)
        if acc is None or acc.month != month or acc.epoch != epoch:
            if not block:
                if key not in _LOADING:
                    _LOADING.add(key)
                    threading.Thread(
                        target=_load_in_background, args=(key, month, epoch, path),
                        name="savings-meter-warmup", daemon=True,
                    ).start()
                return None
            acc = _Acc(month, epoch, path)
            _CACHE[key] = acc
            _rebuild(acc)
            acc.loaded_at = time.monotonic()
        elif time.monotonic() - acc.loaded_at >= max_age_s:
            _advance(acc)
            acc.loaded_at = time.monotonic()
        return _reading(acc, default_model)
