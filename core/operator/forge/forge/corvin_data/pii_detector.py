"""Three-layer PII detection — header heuristics + value regex + (optional)
Presidio NER.

This module implements the Phase 12.2 detector. Presidio integration
lives in ``pii_presidio.py`` (Phase 12.7) and is consulted from here
when ``policy.pii_backend == "presidio"``.

Detection strategy:

  1. **Header heuristics** — match the column name against a curated
     pattern set (``email`` → email, ``phone`` → phone, ``iban`` →
     iban, etc.). Fast, no value scanning, but high-precision when
     header naming is clear.
  2. **Value regex** — match the first N sample values against
     curated regexes for the six load-bearing PII classes (email,
     phone, IBAN, credit_card, us_ssn, ch_ahv, de_steuer_id).
     Required when header naming is ambiguous (e.g. ``user_data``).
  3. **Presidio** (optional) — operator-installed NER backend for
     unstructured cases (free-text columns containing names, places,
     organisations). Off by default; covered in Phase 12.7.

Output: for each column, a single ``pii_class`` string (or None when
no class fits with sufficient confidence). The single-class output is
a deliberate simplification — a column is one shape of PII, not many.
For mixed columns the operator's data_policy.yaml can declare a
per-column override (Phase 12.3).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

from .snapshot import ColumnSchema, ColumnStats, Snapshot


# ---------------------------------------------------------------------------
# Public surface
# ---------------------------------------------------------------------------

# The canonical PII classes. Curated, not extensible at runtime — adding
# a new class needs a corresponding redactor strategy and policy entry.
PII_CLASSES = (
    "email",
    "phone",
    "iban",
    "credit_card",
    "us_ssn",
    "ch_ahv",
    "de_steuer_id",
    "name",
    "date_of_birth",
    "address",
    "opaque_id",
    "national_id",
)


@dataclass
class DetectionResult:
    """Single-column detection outcome."""

    column:        str
    pii_class:     str | None
    source:        str             # "header" | "value-regex" | "presidio" | "override" | "none"
    confidence:    float           # 0.0 .. 1.0
    sample_hits:   int = 0         # how many of the inspected sample values matched


# ---------------------------------------------------------------------------
# Header-heuristic patterns
# ---------------------------------------------------------------------------

# Tuples: (regex pattern matched against lower(column_name), pii_class).
# Order matters — first match wins.
_HEADER_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # email — match "email", "mail", "e_mail", but NOT "name" alone
    (re.compile(r"^(e[_-]?mail|email|mail)([_-].*)?$"),               "email"),
    (re.compile(r"^.*[_-](email|mail)([_-].*)?$"),                    "email"),
    # phone
    (re.compile(r"^(phone|tel|telephone|mobile|handy|fon)([_-].*)?$"), "phone"),
    (re.compile(r"^.*[_-](phone|tel|mobile)([_-].*)?$"),              "phone"),
    # IBAN / BIC
    (re.compile(r"^(iban|bic|swift)([_-].*)?$"),                      "iban"),
    (re.compile(r"^.*[_-](iban|bic)([_-].*)?$"),                      "iban"),
    # credit card
    (re.compile(r"^(cc|ccnum|cc_num|credit[_-]?card|card[_-]?number|kreditkarte)([_-].*)?$"),
     "credit_card"),
    # US SSN
    (re.compile(r"^(ssn|social[_-]?security)([_-].*)?$"),             "us_ssn"),
    # Swiss AHV
    (re.compile(r"^(ahv|ahv[_-]nr|ahv[_-]nummer)([_-].*)?$"),         "ch_ahv"),
    # German Steuer-ID
    (re.compile(r"^(steuer[_-]?id|tax[_-]?id|tin)([_-].*)?$"),        "de_steuer_id"),
    # Name (broad — risks false positives on e.g. "product_name"; we
    # only fire on prefix/exact match)
    (re.compile(r"^(name|firstname|first_name|surname|lastname|last_name|vorname|nachname|fullname|full_name)$"),
     "name"),
    # Date of birth
    (re.compile(r"^(dob|birthdate|birth_date|geburtsdatum|date_of_birth)([_-].*)?$"),
     "date_of_birth"),
    # Address
    (re.compile(r"^(address|addr|street|strasse|zip|postcode|postleitzahl|plz|city|stadt|ort)([_-].*)?$"),
     "address"),
    # Generic opaque ID (last resort)
    (re.compile(r"^(uuid|guid|hash|external[_-]?id|opaque[_-]?id)([_-].*)?$"),
     "opaque_id"),
]


# ---------------------------------------------------------------------------
# Value regex patterns
# ---------------------------------------------------------------------------

# Anchored at start to avoid matching across cell concatenation in
# CSV reads. Lenient about trailing whitespace.
#
# Email: RFC-5322-ish, NOT the full grammar (which is unboundedly weird).
# We accept what 99.9% of real emails look like.
_RE_EMAIL = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\s*$")

# Phone: international prefix optional, then 7-15 digits with optional
# spaces / dashes / parentheses.  Permissive to catch real-world formats.
_RE_PHONE = re.compile(
    r"^[\+]?[0-9][\d\s\-().]{6,20}\s*$"
)

# IBAN: 2-letter country + 2 check digits + up to 30 BBAN chars.
# Strict-mode would re-validate the checksum; not done here to keep
# detection cheap.
_RE_IBAN = re.compile(
    r"^[A-Z]{2}[0-9]{2}[A-Z0-9]{10,30}\s*$"
)

# Credit card: 13-19 digits with optional spaces/dashes.
_RE_CREDIT_CARD = re.compile(
    r"^(?:\d[ -]?){12,18}\d\s*$"
)

# US SSN: NNN-NN-NNNN with optional dashes.
_RE_US_SSN = re.compile(
    r"^\d{3}[-\s]?\d{2}[-\s]?\d{4}\s*$"
)

# Swiss AHV: 13 digits, often formatted 756.XXXX.XXXX.XX.
_RE_CH_AHV = re.compile(
    r"^756[.\s]?\d{4}[.\s]?\d{4}[.\s]?\d{2}\s*$"
)

# German Steuer-ID: 11 digits, often without separators.
_RE_DE_STEUER_ID = re.compile(
    r"^\d{11}\s*$"
)

# Value-pattern lookup, ordered by specificity (most-specific first).
_VALUE_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (_RE_EMAIL,         "email"),
    (_RE_IBAN,          "iban"),
    (_RE_CH_AHV,        "ch_ahv"),
    (_RE_US_SSN,        "us_ssn"),
    (_RE_DE_STEUER_ID,  "de_steuer_id"),
    (_RE_CREDIT_CARD,   "credit_card"),
    (_RE_PHONE,         "phone"),
]


# ---------------------------------------------------------------------------
# Per-class confidence thresholds
# ---------------------------------------------------------------------------

# Minimum fraction of sample values that must match a regex for the
# column to be classified as that PII class. Email + IBAN are highly
# specific so the bar is lower; phone + credit_card + ssn risk
# false positives on free-text columns, so the bar is higher.
_VALUE_REGEX_THRESHOLDS = {
    "email":        0.50,
    "iban":         0.50,
    "ch_ahv":       0.60,
    "us_ssn":       0.70,
    "de_steuer_id": 0.80,
    "credit_card":  0.80,
    "phone":        0.70,
}

# When BOTH header AND value-regex fire, value-regex wins on conflict
# (a column called ``id`` whose values look like e-mail addresses
# *is* an email column, regardless of the misleading header).
# When only header fires, we accept it with confidence 0.6.
# When only value-regex fires above threshold, confidence 0.9.
# Both → 0.95.


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def detect_column_pii(
    name:          str,
    sample_values: Iterable[str],
    *,
    inspect_n:     int = 100,
    overrides:     dict[str, str] | None = None,
) -> DetectionResult:
    """Classify a single column.

    *overrides* is the operator's per-column directive from
    ``data_policy.yaml`` — when set for *name*, it bypasses detection
    and forces the class. Use this for false-negatives the regex pass
    misses (free-text columns with embedded PII).
    """
    if overrides and name in overrides:
        return DetectionResult(
            column=name,
            pii_class=overrides[name],
            source="override",
            confidence=1.0,
        )

    header_class = _match_header(name)
    value_class, value_hits, value_inspected = _match_values(sample_values, inspect_n)

    # Conflict resolution
    if header_class and value_class:
        # Both agree? bump confidence.
        if header_class == value_class:
            return DetectionResult(
                column=name,
                pii_class=header_class,
                source="header+value-regex",
                confidence=0.95,
                sample_hits=value_hits,
            )
        # Disagree — value wins.
        return DetectionResult(
            column=name,
            pii_class=value_class,
            source="value-regex (overrode header)",
            confidence=0.90,
            sample_hits=value_hits,
        )

    if value_class:
        return DetectionResult(
            column=name,
            pii_class=value_class,
            source="value-regex",
            confidence=0.90,
            sample_hits=value_hits,
        )

    if header_class:
        return DetectionResult(
            column=name,
            pii_class=header_class,
            source="header",
            confidence=0.60,
        )

    return DetectionResult(
        column=name,
        pii_class=None,
        source="none",
        confidence=0.0,
    )


def apply_pii_detection(
    snapshot:    Snapshot,
    *,
    inspect_n:   int = 100,
    overrides:   dict[str, str] | None = None,
    use_presidio: bool = False,
) -> Snapshot:
    """Mutate *snapshot* in place — populate ``schema[i].pii_class``.

    Inspects the snapshot's own sample rows; works even when the
    underlying file is no longer reachable. Returns the same snapshot
    object for chaining.

    *use_presidio*: when True AND Presidio is installed, run the NER
    backend on each column whose regex+header pass yielded no class.
    Falls back silently to regex+headers when Presidio is missing.
    The operator opts in via data_policy.yaml.
    """
    overrides = overrides or {}
    for col in snapshot.schema:
        sample_values = _collect_column_values(snapshot.sample, col.name)
        result = detect_column_pii(
            col.name,
            sample_values,
            inspect_n=inspect_n,
            overrides=overrides,
        )
        col.pii_class = result.pii_class

        # Presidio NER fallback for unclassified columns
        if use_presidio and col.pii_class is None and col.name not in overrides:
            presidio_class = _try_presidio_column(sample_values, inspect_n=inspect_n)
            if presidio_class:
                col.pii_class = presidio_class
    return snapshot


def _try_presidio_column(values, *, inspect_n: int) -> str | None:
    """Best-effort Presidio call. Returns the most-frequent detected
    class for the column, or None when Presidio is unavailable / no
    hits."""
    try:
        from . import pii_presidio
        res = pii_presidio.detect_with_presidio(values, inspect_n=inspect_n)
    except Exception:
        return None
    if not res.by_class:
        return None
    # Pick the class with the most hits (≥ 50% of inspected).
    best_cls, best_hits = max(res.by_class.items(), key=lambda kv: kv[1])
    if res.inspected_count and best_hits / res.inspected_count >= 0.5:
        return best_cls
    return None


def detection_summary(snapshot: Snapshot) -> dict[str, int]:
    """Return a count-by-PII-class summary for audit-event details.

    Returns ``{"email": 1, "phone": 2, "<no_pii>": 4}`` — never
    column names, never values. The shape is the load-bearing
    audit-chain payload (cf. ADR-0012 §E).
    """
    counts: dict[str, int] = {}
    for col in snapshot.schema:
        key = col.pii_class if col.pii_class else "<no_pii>"
        counts[key] = counts.get(key, 0) + 1
    return counts


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

def _match_header(name: str) -> str | None:
    """Return the PII class for *name* by header heuristic, or None."""
    norm = name.strip().lower().replace(" ", "_")
    for pat, cls in _HEADER_PATTERNS:
        if pat.match(norm):
            return cls
    return None


def _match_values(
    values:    Iterable[str],
    inspect_n: int,
) -> tuple[str | None, int, int]:
    """Inspect up to *inspect_n* values; return (winning class, hits, inspected).

    Per-class tally: for each value, find the first matching pattern
    in ``_VALUE_PATTERNS`` (specificity-ordered) and increment that
    class's counter. After the inspection budget is exhausted, the
    class whose hit-fraction crosses its threshold wins. Ties broken
    by the per-class specificity ranking.
    """
    counters: dict[str, int] = {cls: 0 for _re, cls in _VALUE_PATTERNS}
    inspected = 0
    for v in values:
        if inspected >= inspect_n:
            break
        if v is None or v == "":
            continue
        # Coerce non-string scalars (int/float) to str for regex
        s = v if isinstance(v, str) else str(v)
        inspected += 1
        for pat, cls in _VALUE_PATTERNS:
            if pat.match(s):
                counters[cls] += 1
                break

    if inspected == 0:
        return None, 0, 0

    # Pick the strongest class above its threshold.
    best: tuple[str | None, int, float] = (None, 0, 0.0)
    for cls, hits in counters.items():
        threshold = _VALUE_REGEX_THRESHOLDS.get(cls, 0.7)
        fraction = hits / inspected if inspected else 0.0
        if fraction >= threshold:
            if fraction > best[2]:
                best = (cls, hits, fraction)

    return best[0], best[1], inspected


def _collect_column_values(sample: list[dict], column: str) -> list:
    """Pull the column's values out of the sample rows."""
    return [row.get(column) for row in sample if column in row]
