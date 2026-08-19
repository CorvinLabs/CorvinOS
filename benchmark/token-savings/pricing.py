"""Cache-aware cost model for a token-savings benchmark — NO fabricated prices.

Raw token COUNT and COST diverge because the four token classes are priced very differently.
The class MULTIPLIERS relative to base input are Anthropic-official and fixed:

    fresh input          1.00 x base_input
    cache write (5-min)  1.25 x base_input
    cache write (1-hour) 2.00 x base_input
    cache read           0.10 x base_input
    output               base_output  (a separate per-model price)

The per-model BASE prices (base_input, base_output, in $ per 1M tokens) are NOT hardcoded here
— the repo has no trustworthy price table (the only one, claude_engine.py, is ~10x off with no
cache tiers). They come from `prices.json`, which the operator fills with their real, current
prices. Until real prices are supplied, `cost_usd()` returns None and the report shows raw
counts only — a cost figure is never invented.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

# Official, fixed multipliers vs. base input price. Not prices — ratios.
MULT_FRESH = 1.00
MULT_CACHE_WRITE_5M = 1.25
MULT_CACHE_WRITE_1H = 2.00
MULT_CACHE_READ = 0.10


def creation_split(usage: Any) -> tuple[int, int]:
    """(5-min, 1-hour) cache-creation tokens from the real usage, when the worker reports the
    ephemeral breakdown; else the whole cache_creation is treated as 5-min (the cheaper, safer
    assumption for a cost claim — never over-credit savings)."""
    if not isinstance(usage, dict):
        return 0, 0
    det = usage.get("cache_creation")
    if isinstance(det, dict):
        return (int(det.get("ephemeral_5m_input_tokens", 0) or 0),
                int(det.get("ephemeral_1h_input_tokens", 0) or 0))
    return int(usage.get("cache_creation_input_tokens", 0) or 0), 0


def load_prices(path: "str | Path | None" = None) -> dict:
    p = Path(path) if path else Path(__file__).resolve().parent / "prices.json"
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def _model_prices(prices: dict, model: str) -> "dict | None":
    m = (prices.get("models") or {}).get(model)
    if not isinstance(m, dict):
        return None
    bi, bo = m.get("base_input_per_mtok"), m.get("base_output_per_mtok")
    if not (isinstance(bi, (int, float)) and isinstance(bo, (int, float)) and bi > 0):
        return None            # placeholder / unfilled → no cost, never a fabricated number
    return {"bi": float(bi), "bo": float(bo)}


def cost_usd(usage: Any, model: str, prices: "dict | None" = None) -> "float | None":
    """Cache-weighted cost in USD for one turn's usage, or None if real prices are absent.
    Uses the true 5m/1h cache-creation split when available."""
    mp = _model_prices(prices or load_prices(), model)
    if mp is None:
        return None
    u = usage if isinstance(usage, dict) else {}
    fresh = int(u.get("input_tokens", 0) or 0)
    read = int(u.get("cache_read_input_tokens", 0) or 0)
    out = int(u.get("output_tokens", 0) or 0)
    c5, c1 = creation_split(u)
    bi = mp["bi"] / 1_000_000
    input_units = (MULT_FRESH * fresh + MULT_CACHE_WRITE_5M * c5
                   + MULT_CACHE_WRITE_1H * c1 + MULT_CACHE_READ * read)
    return input_units * bi + out * (mp["bo"] / 1_000_000)


def prices_available(model: str, prices: "dict | None" = None) -> bool:
    return _model_prices(prices or load_prices(), model) is not None
