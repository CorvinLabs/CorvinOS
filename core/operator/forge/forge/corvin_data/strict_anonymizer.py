"""strict_anonymizer.py — Layer 32 (ADR-0023) zero-value snapshot projection.

Structural anonymisation layer on top of Layer 24's PII detection +
redaction stack. When the operator sets
``data_policy.spec.strict_anonymization: true``, this module rewrites
every snapshot payload that would otherwise leak raw values,
quantiles, or below-k-anonymity distinct counts into the LLM context.

Two stages:

  1. **Projection** — sample rows dropped, stats reduced to type/null/
     distinct buckets, quantiles + top-N + exact-counts stripped,
     rowcount Laplace-noised. The LLM-visible payload becomes a
     zero-value structural shape.
  2. **Post-scan** — walk the FINAL payload (after projection,
     depth-bounded) and apply a curated PII regex set to every
     string leaf. On match → fail-closed (reject + audit) by
     default; advisory mode replaces leaves with ``<pii-redacted>``.

Both stages are opt-in via the operator-only path-gate-protected
``data_policy.yaml`` — no agent-controllable parameter can bypass
or weaken the anonymisation once enabled.

Public API:

  apply_strict_anonymisation(payload, *, policy)
    -> tuple[anonymised_payload, applied: bool, dropped_keys: int]

  scan_for_pii_leaks(payload, *, reject: bool)
    -> tuple[scanned_payload, rejected: bool, match_count: int]
"""

from __future__ import annotations

import math
import random
import re
from typing import Any


# ---------------------------------------------------------------------------
# Post-scan PII patterns — curated, conservative.
#
# Each regex catches the "obvious shape" of a high-confidence PII class.
# False positives are acceptable (the redaction is structural, not
# user-facing); false negatives are the worry. The set deliberately
# avoids generic-number patterns that would false-positive on every
# rowcount / quantile / bucket count.
# ---------------------------------------------------------------------------

_PII_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("email",     re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")),
    # IBAN — 2 letters + 2 digits + 4–30 alphanumeric body
    ("iban",      re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{8,30}\b")),
    # Credit card: 13–19 digit runs separated by space/dash, Luhn not
    # validated here (regex-only — Luhn would belong in the value-side
    # PII detector). False-positive risk is low because we run AFTER
    # projection (which already stripped values), so any match is by
    # definition a leak.
    ("credit_card", re.compile(r"\b(?:\d[ -]?){13,19}\b")),
    # E.164-shaped phone with international + prefix.
    ("phone_e164", re.compile(r"\+\d{8,15}\b")),
    # US Social Security Number XXX-XX-XXXX
    ("us_ssn",    re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    # German Steuer-Identifikationsnummer — 11-digit run with word
    # boundary; we intentionally don't validate the checksum to keep
    # the scan light. The regex won't false-positive on a 10- or
    # 12-digit count thanks to the word-boundary anchors.
    ("de_steuer_id", re.compile(r"\b\d{11}\b")),
)


# Max recursion depth when walking the payload for the post-scan.
# Snapshot payloads are 3 layers deep at most (file/schema/stats);
# 16 is generous and rules out pathological deep-nested input.
_POST_SCAN_MAX_DEPTH = 16

# Sentinel that replaces a leaked leaf in advisory mode.
_REDACTED_LEAF = "<pii-redacted>"


# ---------------------------------------------------------------------------
# k-anonymity bucketisation
# ---------------------------------------------------------------------------


def _distinct_class(
    distinct: int | None,
    *,
    k_threshold: int,
) -> str | None:
    """Project an exact distinct count to a four-bucket categorical.

    None passes through (column wasn't enumerated). Below k →
    ``"unique"`` regardless of true count — an attacker cannot
    distinguish 1 / 2 / 3 / 4 distinct values when k = 5.
    """
    if distinct is None:
        return None
    if distinct < k_threshold:
        return "unique"
    if distinct < 100:
        return "low"
    if distinct < 10_000:
        return "medium"
    return "high"


def _nulls_class(nulls: int, total: int) -> str:
    """Bucket null-count rather than exposing the raw integer."""
    if total <= 0 or nulls == 0:
        return "none"
    ratio = nulls / total
    if ratio < 0.05:
        return "few"
    if ratio < 0.5:
        return "some"
    return "many"


def _type_class(col_type: str) -> str:
    """Project the schema's column-type to a coarse class."""
    t = (col_type or "").lower()
    if t in {"int", "float", "number", "numeric"}:
        return "numeric"
    if t in {"bool", "boolean"}:
        return "boolean"
    if t in {"date", "datetime", "timestamp", "time"}:
        return "temporal"
    return "categorical"


# ---------------------------------------------------------------------------
# Laplace noise for rowcount
# ---------------------------------------------------------------------------


def _laplace_noise(scale: float, rng: random.Random) -> float:
    """Sample from Laplace(0, scale) using inverse-CDF on Uniform(0,1).

    ``scale`` is the b parameter; expected absolute deviation is b * ln(2).
    Returns a real-valued shift the caller adds to the true count.
    """
    if scale <= 0:
        return 0.0
    u = rng.random() - 0.5
    return -scale * math.copysign(math.log1p(-2 * abs(u)), u)


def _noised_rowcount(rowcount: int, *, scale_ratio: float, rng: random.Random) -> int:
    """Add proportional Laplace noise to rowcount, clamp to >= 0.

    ``scale_ratio`` is multiplied by max(rowcount, 1) so the noise
    scales with the order of magnitude (1% noise on a million rows
    means ±10 000-ish; the absolute value isn't sensitive when the
    true count is huge).

    Below 100 rows the existing Layer-24 jitter has already moved
    the count; this layer adds Laplace on top.
    """
    if rowcount < 0:
        return 0
    base_scale = max(rowcount, 1) * float(scale_ratio)
    noised = rowcount + _laplace_noise(base_scale, rng)
    return max(0, int(round(noised)))


# ---------------------------------------------------------------------------
# Public API — projection
# ---------------------------------------------------------------------------


def apply_strict_anonymisation(
    payload: dict[str, Any],
    *,
    k_anonymity_threshold: int = 5,
    rowcount_laplace_scale: float = 1.0,
    rng: random.Random | None = None,
) -> tuple[dict[str, Any], int]:
    """Rewrite a snapshot payload into the zero-value strict projection.

    The input is the dict returned by ``Snapshot.to_dict()`` — i.e.::

        {"file": {...}, "schema": [...], "sample": [...], "stats": {...}}

    The output is the same shape but with:
      * ``sample`` always ``[]``
      * ``stats[col]`` reduced to ``{type_class, nulls_class, distinct_class}``
      * ``file.rowcount`` replaced by ``rowcount_approx`` with Laplace noise
      * ``file.rowcount_exact`` always ``False``
      * Top-level ``strict: True`` and ``anonymised: True`` markers added

    Returns ``(rewritten_payload, dropped_keys_count)``. The
    dropped-keys count is the number of stat fields that were
    structurally removed (audit-event detail).
    """
    if rng is None:
        rng = random.Random()
    k = max(2, int(k_anonymity_threshold))

    out: dict[str, Any] = {}

    # ── file ──────────────────────────────────────────────────────────
    raw_file = dict(payload.get("file") or {})
    rowcount = int(raw_file.get("rowcount", 0) or 0)
    noised = _noised_rowcount(
        rowcount,
        scale_ratio=float(rowcount_laplace_scale),
        rng=rng,
    )
    out["file"] = {
        "path":           raw_file.get("path"),
        "format":         raw_file.get("format"),
        "size_b":         raw_file.get("size_b"),
        "rowcount_approx": noised,
        "rowcount_exact": False,
        "encoding":       raw_file.get("encoding", "utf-8"),
    }

    # ── schema ────────────────────────────────────────────────────────
    # Keep name + type + pii_class. Drop cardinality (would leak
    # distinct-count even after the stats-side bucket).
    raw_schema = payload.get("schema") or []
    out["schema"] = []
    for col in raw_schema:
        if not isinstance(col, dict):
            continue
        out["schema"].append({
            "name":      col.get("name"),
            "type":      col.get("type"),
            "pii_class": col.get("pii_class"),
        })

    # ── sample ────────────────────────────────────────────────────────
    out["sample"] = []                          # ALWAYS empty in strict mode

    # ── stats ─────────────────────────────────────────────────────────
    raw_stats = payload.get("stats") or {}
    dropped = 0
    rewritten_stats: dict[str, dict[str, Any]] = {}
    # Total for nulls_class denominator — use noised rowcount so we
    # don't leak the exact count via ratio reverse-engineering.
    total_for_ratio = noised if noised > 0 else 1
    # Build a name→type lookup for type_class.
    type_by_name = {
        col.get("name"): col.get("type")
        for col in raw_schema if isinstance(col, dict)
    }
    for col_name, st in raw_stats.items():
        if not isinstance(st, dict):
            continue
        # Stripped fields: nulls (count), p05, p50, p95, distinct (count),
        # top (list), approximate flag. Counted for audit.
        stripped_here = sum(
            1 for k_ in ("nulls", "p05", "p50", "p95", "distinct", "top",
                         "approximate")
            if k_ in st
        )
        dropped += stripped_here
        rewritten_stats[col_name] = {
            "type_class":     _type_class(type_by_name.get(col_name, "")),
            "nulls_class":    _nulls_class(int(st.get("nulls", 0) or 0),
                                            total_for_ratio),
            "distinct_class": _distinct_class(st.get("distinct"),
                                              k_threshold=k),
        }
    out["stats"] = rewritten_stats

    out["strict"] = True
    out["anonymised"] = True

    return out, dropped


# ---------------------------------------------------------------------------
# Public API — post-scan
# ---------------------------------------------------------------------------


def scan_for_pii_leaks(
    payload: dict[str, Any],
    *,
    reject: bool = True,
) -> tuple[dict[str, Any], bool, int, list[str]]:
    """Walk the payload and apply the PII regex set to every string leaf.

    Returns ``(payload, rejected, match_count, matched_classes)``.

    * ``rejected=True``: ``reject=True`` and at least one match.
      Payload is replaced by a rejection skeleton:
      ``{"file": file, "schema": [], "sample": [], "stats": {},
         "anonymisation_rejected": True, "reason": "post-scan-pii-leak"}``.
    * ``rejected=False``: payload kept. In advisory mode (``reject=False``)
      matched leaves are replaced by ``"<pii-redacted>"`` inline.
    * ``match_count`` always reflects the total number of leaf-level
      matches encountered (independent of reject vs advisory mode).
    * ``matched_classes`` is the list of distinct PII class names that
      fired, e.g. ``["email", "iban"]``. Useful for the audit detail
      (the audit emitter still applies the metadata-only rule and
      records counts not values).
    """
    matches: list[str] = []

    def _walk(value: Any, depth: int = 0) -> Any:
        if depth > _POST_SCAN_MAX_DEPTH:
            return value
        if isinstance(value, str):
            hit_classes = []
            for cls, pat in _PII_PATTERNS:
                if pat.search(value):
                    hit_classes.append(cls)
                    matches.append(cls)
            if hit_classes:
                return _REDACTED_LEAF if not reject else value
            return value
        if isinstance(value, dict):
            return {k: _walk(v, depth + 1) for k, v in value.items()}
        if isinstance(value, list):
            return [_walk(v, depth + 1) for v in value]
        return value

    walked = _walk(payload)
    match_count = len(matches)
    matched_classes = sorted(set(matches))

    if reject and match_count > 0:
        skeleton = {
            "file":   (payload.get("file") if isinstance(payload, dict) else None),
            "schema": [],
            "sample": [],
            "stats":  {},
            "anonymisation_rejected": True,
            "reason": "post-scan-pii-leak",
        }
        return skeleton, True, match_count, matched_classes

    return walked, False, match_count, matched_classes


__all__ = [
    "apply_strict_anonymisation",
    "scan_for_pii_leaks",
]
