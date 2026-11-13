"""Per-chat cost and token-efficiency metrics, derived on read from the turn log (ADR-2324).

Nothing is stored here. Every figure is recomputed from ``<session>.turns.jsonl``, whose
assistant records already carry ``turn_usage`` (ADR-2263: confirmed model plus the CLI's
four-way token split) and the CLI's own bounded ``diff`` on file-tool parts (ADR-2241).
A second counter would be a second truth that can disagree with the record.

Honesty rules (each one has a test):

* **Matched sets.** A ratio divides figures from the SAME turns. ``measured`` turns are those
  whose four token counts were all reported; every token and cache figure uses only them.
  Dollar figures use the ``priced`` subset (model in the rate card). Line ratios use turns whose
  diff is fully known. Output tokens in a line ratio come from exactly those turns.
* **Unknown is not zero.** A count the CLI did not report is ``None`` in the log and stays out of
  every sum. A model outside the rate card is *unpriced*: counted and listed, never priced at a
  guess and never $0. A zero denominator yields ``None``, never 0 or infinity.
* **Prices come from one place**: ``core.learning.model_selection_learner`` (the rate card and
  ``_turn_cost_usd`` the savings meter also uses), so figures agree across panels.
* **Lines are what the CLI diff reported.** A truncated diff makes the count a lower bound
  (``lines_lower_bound``); a credential-withheld diff makes the turn's lines unknown and removes
  it from line ratios; a Bash change that names no file is not seen at all.
* **Delegated worker spend (ACS) is not in the turn log** and is not included
  (``includes_delegated_workers`` is always ``False``).

Pure functions, no I/O, never raises on malformed records.
"""
from __future__ import annotations

import math
from typing import Any, Iterable, Optional

SCHEMA = 1

#: Newest per-turn rows returned; aggregates always cover every turn.
MAX_TURN_ROWS = 200

PRICE_BASIS = ("First-party list price per model (input, output; cache reads and writes at the "
               "published multipliers). Models outside the rate card are shown as unpriced.")

_USAGE_KEYS = ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")

# A turn the user did not run: notices and mid-turn /btw notes carry no usage of their own.
_NON_TURN_FLAGS = ("notice", "btw")


def _count(value: Any) -> Optional[int]:
    """A reported non-negative integer count, else None. bool/NaN/negative are not counts."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, float) and math.isfinite(value) and value >= 0 and value == int(value):
        return int(value)
    return None


def _ratio(num: float, den: float) -> Optional[float]:
    return (num / den) if den and den > 0 else None


def _diff_lines(diff: Any) -> tuple[int, int]:
    """(added, removed) of one unified-diff text; ``@@`` headers are not changes."""
    if not isinstance(diff, str):
        return 0, 0
    added = removed = 0
    for line in diff.split("\n"):
        if line.startswith("@@"):
            continue
        if line.startswith("+"):
            added += 1
        elif line.startswith("-"):
            removed += 1
    return added, removed


def _turn_lines(parts: Any) -> tuple[Optional[int], Optional[int], bool]:
    """(added, removed, truncated) for one turn; (None, None, False) when any diff was withheld."""
    added = removed = 0
    truncated = False
    if not isinstance(parts, list):
        return 0, 0, False
    for part in parts:
        if not isinstance(part, dict):
            continue
        if part.get("diff_withheld"):
            return None, None, False
        if part.get("diff"):
            a, r = _diff_lines(part["diff"])
            added += a
            removed += r
            if part.get("diff_truncated"):
                truncated = True
    return added, removed, truncated


def _price(model: str) -> Optional[tuple[float, float]]:
    from core.learning.model_selection_learner import model_price_per_1k  # noqa: PLC0415
    return model_price_per_1k(model) if model else None


def _turn_cost(price: tuple[float, float], u: dict[str, int], model: str) -> float:
    from core.learning.model_selection_learner import _turn_cost_usd  # noqa: PLC0415
    return _turn_cost_usd(price, u["input_tokens"], u["output_tokens"],
                          u["cache_creation_input_tokens"], u["cache_read_input_tokens"], model)


def _is_assistant_turn(rec: Any) -> bool:
    return (isinstance(rec, dict) and rec.get("role") == "assistant"
            and not any(rec.get(f) for f in _NON_TURN_FLAGS))


def compute_chat_efficiency(turns: Iterable[Any], *, history_truncated: bool = False) -> dict[str, Any]:
    """Aggregate cost / token / cache / code-line metrics over one chat's persisted turns."""
    n_turns = n_measured = n_priced = n_no_usage = n_incomplete = 0
    n_withheld = n_truncated = 0
    tok = dict.fromkeys(_USAGE_KEYS, 0)
    cost_total = 0.0
    priced_out = 0
    cache_saving = 0.0
    # line set: measured turns with a fully known diff
    line_turns = lines_added = lines_removed = line_out = 0
    line_priced_cost = 0.0
    line_priced_changed = 0
    code_turns = 0
    lower_bound = False
    by_model: dict[str, dict[str, Any]] = {}
    unpriced: set[str] = set()
    rows: list[dict[str, Any]] = []
    first_ts = last_ts = None

    for rec in turns:
        if not _is_assistant_turn(rec):
            continue
        n_turns += 1
        ts = rec.get("ts") if isinstance(rec.get("ts"), (int, float)) and not isinstance(rec.get("ts"), bool) else None
        if ts is not None:
            first_ts = ts if first_ts is None else min(first_ts, ts)
            last_ts = ts if last_ts is None else max(last_ts, ts)
        tu = rec.get("turn_usage")
        raw = tu.get("usage") if isinstance(tu, dict) else None
        model = str(tu.get("model") or "") if isinstance(tu, dict) else ""
        added, removed, trunc = _turn_lines(rec.get("parts"))
        row: dict[str, Any] = {"ts": ts, "model": model or None, "cost_usd": None,
                               "lines_added": added, "lines_removed": removed,
                               "lines_lower_bound": trunc}
        if added is None:
            n_withheld += 1
        if not isinstance(raw, dict):
            n_no_usage += 1
            row.update({k: None for k in _USAGE_KEYS})
            rows.append(row)
            continue
        counts = {k: _count(raw.get(k)) for k in _USAGE_KEYS}
        row.update(counts)
        if any(v is None for v in counts.values()):
            n_incomplete += 1
            rows.append(row)
            continue
        u = {k: int(v) for k, v in counts.items()}  # type: ignore[arg-type]
        n_measured += 1
        for k in _USAGE_KEYS:
            tok[k] += u[k]
        bucket = by_model.setdefault(model or "(model not reported)",
                                     {"model": model or "(model not reported)", "turns": 0,
                                      "priced_turns": 0, "cost_usd": 0.0, "output_tokens": 0})
        bucket["turns"] += 1
        bucket["output_tokens"] += u["output_tokens"]
        price = _price(model)
        cost: Optional[float] = None
        if price is None:
            if model:
                unpriced.add(model)
        else:
            cost = _turn_cost(price, u, model)
            n_priced += 1
            cost_total += cost
            priced_out += u["output_tokens"]
            bucket["priced_turns"] += 1
            bucket["cost_usd"] += cost
            plain = ((u["input_tokens"] + u["cache_read_input_tokens"] + u["cache_creation_input_tokens"]) / 1000.0
                     * price[0] + u["output_tokens"] / 1000.0 * price[1])
            cache_saving += plain - cost
            row["cost_usd"] = cost
        if added is not None:
            line_turns += 1
            lines_added += added
            lines_removed += removed or 0
            line_out += u["output_tokens"]
            if added + (removed or 0) > 0:
                code_turns += 1
            if trunc:
                lower_bound = True
                n_truncated += 1
            if cost is not None:
                line_priced_changed += added + (removed or 0)
                line_priced_cost += cost
        rows.append(row)

    input_side = tok["input_tokens"] + tok["cache_read_input_tokens"] + tok["cache_creation_input_tokens"]
    changed = lines_added + lines_removed
    priced_ok = n_priced > 0
    models = sorted(by_model.values(), key=lambda b: (-b["cost_usd"], b["model"]))
    for b in models:
        b["cost_usd"] = b["cost_usd"] if b["priced_turns"] else None
    return {
        "schema": SCHEMA,
        "window": {"scope": "chat_lifetime", "first_ts": first_ts, "last_ts": last_ts,
                   "history_truncated": bool(history_truncated)},
        "coverage": {
            "turns": n_turns, "measured": n_measured, "priced": n_priced,
            "unpriced": n_measured - n_priced, "without_usage": n_no_usage,
            "incomplete_usage": n_incomplete, "diff_withheld": n_withheld,
            "diff_truncated": n_truncated, "line_turns": line_turns,
            "code_turns": code_turns,
        },
        "tokens": {
            "input": tok["input_tokens"], "output": tok["output_tokens"],
            "cache_read": tok["cache_read_input_tokens"],
            "cache_write": tok["cache_creation_input_tokens"],
        },
        "cost": {
            "total_usd": cost_total if priced_ok else None,
            "per_turn_usd": _ratio(cost_total, n_priced),
            "per_1k_output_tokens_usd": _ratio(cost_total, priced_out / 1000.0),
            "cache_saving_usd": cache_saving if priced_ok else None,
        },
        "cache": {"hit_rate": _ratio(tok["cache_read_input_tokens"], input_side)},
        "code": {
            "lines_added": lines_added, "lines_removed": lines_removed, "lines_changed": changed,
            "lines_lower_bound": lower_bound,
            "output_tokens_per_changed_line": _ratio(line_out, changed),
            "changed_lines_per_1k_output_tokens": _ratio(changed, line_out / 1000.0),
            "cost_per_changed_line_usd": _ratio(line_priced_cost, line_priced_changed),
        },
        "models": models,
        "unpriced_models": sorted(unpriced),
        "includes_delegated_workers": False,
        "price_basis": PRICE_BASIS,
        "rows": rows[-MAX_TURN_ROWS:],
    }
