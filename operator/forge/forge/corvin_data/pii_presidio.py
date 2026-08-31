"""Optional Presidio NER backend for PII detection.

Phase 12.7. Activated only when the operator's data_policy.yaml
declares ``pii_backend: presidio``. Adds NER-based detection on top
of the regex+headers pass (Phase 12.2):

  * personal names (PERSON)
  * locations  (LOCATION)
  * organisations (ORG)
  * dates of birth (DATE_TIME inferred as DoB)

Without Presidio installed, calls to ``detect_with_presidio`` raise
``PresidioNotInstalled``. The pii_detector module catches this and
falls back to regex+headers; an audit-event records that the
operator-requested backend was unavailable.

Install (operator side):

    pip install presidio_analyzer presidio_anonymizer
    python -m spacy download en_core_web_lg
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


class PresidioNotInstalled(ImportError):
    """Raised when the operator requested Presidio but it's not in the venv."""


_PRESIDIO_TO_CLASS = {
    "PERSON":         "name",
    "EMAIL_ADDRESS":  "email",
    "PHONE_NUMBER":   "phone",
    "IBAN_CODE":      "iban",
    "CREDIT_CARD":    "credit_card",
    "US_SSN":         "us_ssn",
    "DATE_TIME":      "date_of_birth",
    "LOCATION":       "address",
    "IP_ADDRESS":     "opaque_id",
}


@dataclass
class PresidioResult:
    """Per-call summary, no values."""

    by_class: dict[str, int]
    inspected_count: int


def _try_import_analyzer() -> "object | None":
    try:
        from presidio_analyzer import AnalyzerEngine  # type: ignore[import-untyped]
        return AnalyzerEngine()
    except ImportError:
        return None


# Module-level singleton — Analyzer is expensive to construct (spaCy
# model load); cache it across calls. Lazy.
_ANALYZER = None
_ANALYZER_INITIALISED = False


def _get_analyzer() -> "object":
    global _ANALYZER, _ANALYZER_INITIALISED
    if not _ANALYZER_INITIALISED:
        _ANALYZER = _try_import_analyzer()
        _ANALYZER_INITIALISED = True
    if _ANALYZER is None:
        raise PresidioNotInstalled(
            "Presidio NER backend requested but not installed. Run "
            "`pip install presidio_analyzer presidio_anonymizer` "
            "and `python -m spacy download en_core_web_lg`, or set "
            "pii_backend: regex+headers in data_policy.yaml."
        )
    return _ANALYZER


def is_available() -> bool:
    """Check (without raising) whether Presidio is reachable."""
    if not _ANALYZER_INITIALISED:
        _get_analyzer_safe()
    return _ANALYZER is not None


def _get_analyzer_safe() -> "object | None":
    global _ANALYZER, _ANALYZER_INITIALISED
    if not _ANALYZER_INITIALISED:
        _ANALYZER = _try_import_analyzer()
        _ANALYZER_INITIALISED = True
    return _ANALYZER


def detect_with_presidio(
    values:    Iterable[str],
    *,
    language:  str = "en",
    inspect_n: int = 100,
) -> PresidioResult:
    """Run Presidio NER over up to *inspect_n* values; aggregate
    detected entity types into our PII-class taxonomy.

    Raises ``PresidioNotInstalled`` when the backend is absent.
    """
    analyzer = _get_analyzer()

    counts: dict[str, int] = {}
    inspected = 0
    for v in values:
        if inspected >= inspect_n:
            break
        if v is None or v == "":
            continue
        s = v if isinstance(v, str) else str(v)
        inspected += 1
        try:
            results = analyzer.analyze(  # type: ignore[attr-defined]
                text=s, language=language, entities=list(_PRESIDIO_TO_CLASS),
            )
        except Exception:
            continue
        for res in results:
            cls = _PRESIDIO_TO_CLASS.get(getattr(res, "entity_type", ""))
            if not cls:
                continue
            counts[cls] = counts.get(cls, 0) + 1

    return PresidioResult(by_class=counts, inspected_count=inspected)


def reset_analyzer_cache() -> None:
    """Test helper — drop the cached Analyzer so re-initialisation runs."""
    global _ANALYZER, _ANALYZER_INITIALISED
    _ANALYZER = None
    _ANALYZER_INITIALISED = False
