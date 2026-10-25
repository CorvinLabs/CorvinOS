"""Additive sensitive-data detector for the L34 classifier (ADR-2268 Decision 6, T-0220).

Heuristic, German and English, and it can only RAISE a class:

* personal data (e-mail address, phone number, IBAN with a valid checksum, payment card with a
  valid Luhn checksum, German tax id with a valid check digit and a context word, US social
  security number) -> at least ``CONFIDENTIAL``
* credential prefixes (``sk-ant-``, ``sk-proj-``, ``sk-``, ``ghp_``, ``AKIA`` ...) -> ``SECRET``,
  never ``CONFIDENTIAL``: a key sent to any provider is a credential leak, and ``SECRET`` is
  never rerouted.

It never lowers a class, never overrides the secret bank or an explicit marker (the caller takes
the maximum), and it returns CATEGORY NAMES only - no matched text - so nothing it reports can leak
into an audit record. A failure inside the detector raises :class:`DetectorError`; the caller
treats that as a refusal (fail-closed), never as ``PUBLIC``.

What it does NOT detect is stated in docs/claude-ref/layer-34-data-classification.md: names,
postal addresses, health or financial prose, obfuscated values ("d e 8 9 ..."), spelled-out
digits, and anything outside the categories above. The recall figures recorded in T-0220 are
measured on a small self-written corpus and are not a claim about real traffic.
"""
from __future__ import annotations

import re

from data_classification import DataClassification


class DetectorError(RuntimeError):
    """The detector could not judge the text. The caller must refuse, not assume PUBLIC."""


#: Categories that mean a credential (-> SECRET). Everything else detected is personal data.
CREDENTIAL_CATEGORIES = frozenset({"api_key"})

_EMAIL_RE = re.compile(r"(?<![\w.+-])[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.([A-Za-z]{2,24})(?![\w-])")
# Image / asset names such as "icon@2x.png" are not addresses.
_NOT_A_TLD = frozenset({"png", "jpg", "jpeg", "gif", "svg", "webp", "css", "js", "ico", "bmp", "avif"})

_IBAN_START_RE = re.compile(r"(?<![A-Za-z0-9])[A-Za-z]{2}\d{2}")
_CARD_RE = re.compile(r"(?<![\d.])(\d(?:[ -]?\d){12,18})(?![\d])")
# A number that starts in the MIDDLE of another digit group ("... 0044 0532 ...") is a piece of
# an account or card number, not a phone number: no digit + separator right before it.
_NOT_MID_NUMBER = r"(?<!\d[ .\-/])"
_PHONE_RES = (
    # +CC ... , 00CC ... (a country code never starts with 0)
    re.compile(r"(?<![\w+])" + _NOT_MID_NUMBER + r"(?:\+[1-9]|00[1-9])\d{0,2}[ .\-/]?(?:\(\d{1,5}\)[ .\-/]?)?\d{1,5}(?:[ .\-/]?\d{2,8}){1,4}(?!\d)"),
    # national trunk prefix 0 + area code + subscriber, e.g. 030 1234567, 0171/2345678, (030) 123456-78
    re.compile(r"(?<![\w.+-])" + _NOT_MID_NUMBER + r"\(?0\d{2,5}\)?[ .\-/]?\d{3,8}(?:[ .\-/]?\d{1,6}){0,3}(?![\d-])"),
    # North American, e.g. (415) 555-0132
    re.compile(r"(?<![\w.+-])\(\d{3}\)[ .\-]?\d{3}[ .\-]\d{4}(?!\d)"),
)
_TAX_DE_RE = re.compile(r"(?<!\d)(\d{2}[ ]?\d{3}[ ]?\d{3}[ ]?\d{3})(?!\d)")
_TAX_CONTEXT_RE = re.compile(
    r"steuer[- ]?id|steueridentifikationsnummer|identifikationsnummer|idnr|tax[- ]?id(?:entification)?|\bTIN\b",
    re.IGNORECASE)
_SSN_RE = re.compile(r"(?<![\d-])(\d{3})-(\d{2})-(\d{4})(?![\d-])")

_KEY_RES = tuple(re.compile(p) for p in (
    r"\bsk-ant-[A-Za-z0-9_-]{8,}",
    r"\bsk-proj-[A-Za-z0-9_-]{8,}",
    r"\bsk-[A-Za-z0-9]{20,}",
    r"\bghp_[A-Za-z0-9]{20,}",
    r"\bgithub_pat_[A-Za-z0-9_]{20,}",
    r"\bAKIA[0-9A-Z]{16}\b",
    r"\bxox[abprs]-[A-Za-z0-9-]{10,}",
    r"\bAIza[0-9A-Za-z_-]{30,}",
))


# Registered IBAN lengths per country (the common ones). A string of the wrong length for its
# country is not that country's IBAN, and trying every length would let a random prefix pass the
# mod-97 check about one time in a hundred. An unlisted country accepts 15..34.
_IBAN_LENGTH = {
    "AD": 24, "AE": 23, "AL": 28, "AT": 20, "BE": 16, "BG": 22, "CH": 21, "CY": 28, "CZ": 24,
    "DE": 22, "DK": 18, "EE": 20, "ES": 24, "FI": 18, "FR": 27, "GB": 22, "GR": 27, "HR": 21,
    "HU": 28, "IE": 22, "IS": 26, "IT": 27, "LI": 21, "LT": 20, "LU": 20, "LV": 21, "MC": 27,
    "MT": 31, "NL": 18, "NO": 15, "PL": 28, "PT": 25, "RO": 24, "SA": 24, "SE": 24, "SI": 19,
    "SK": 24, "SM": 27, "TR": 26,
}


def _iban_mod97(s: str) -> bool:
    expected = _IBAN_LENGTH.get(s[:2])
    if expected is not None and len(s) != expected:
        return False
    moved = s[4:] + s[:4]
    return int("".join(str(int(ch, 36)) for ch in moved)) % 97 == 1


def _iban_in(text: str) -> bool:
    """Is there a checksum-valid IBAN in *text*? Tries every length from a start, so words
    that follow the account number ("... 00 bis Monatsende") do not hide it."""
    for start in _IBAN_START_RE.finditer(text):
        norm = ""
        i = start.start()
        end = min(len(text), i + 60)
        while i < end:
            ch = text[i]
            if ch.isalnum() and ch.isascii():
                norm += ch.upper()
                nxt = text[i + 1] if i + 1 < len(text) else ""
                if 15 <= len(norm) <= 34 and not (nxt.isalnum() and nxt.isascii()) and _iban_mod97(norm):
                    return True
                if len(norm) >= 34:
                    break
            elif ch not in " -":
                break
            i += 1
    return False


def _luhn_ok(digits: str) -> bool:
    total, alt = 0, False
    for ch in reversed(digits):
        d = int(ch)
        if alt:
            d *= 2
            if d > 9:
                d -= 9
        total += d
        alt = not alt
    return total % 10 == 0


def _tax_id_ok(digits: str) -> bool:
    """German Steuer-ID: 11 digits, first non-zero, ISO 7064 MOD 11,10 check digit."""
    if len(digits) != 11 or digits[0] == "0":
        return False
    product = 10
    for ch in digits[:10]:
        total = (int(ch) + product) % 10
        if total == 0:
            total = 10
        product = (total * 2) % 11
    check = 11 - product
    if check == 10:
        check = 0
    return check == int(digits[10])


def _phone_digits_ok(match: str) -> bool:
    digits = re.sub(r"\D", "", match)
    if not (8 <= len(digits) <= 15):
        return False
    # An ISO date or a dotted version is not a number: 2026-10-26, 10.4.2.1
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", match.strip()) or re.fullmatch(r"\d+(?:\.\d+){2,}", match.strip()):
        return False
    return True


def detect(text: str) -> tuple[str, ...]:
    """Sorted, de-duplicated category names found in *text*. Never returns matched text."""
    if not isinstance(text, str):
        raise DetectorError("text is not a string")
    if not text:
        return ()
    try:
        found: set[str] = set()
        if any(r.search(text) for r in _KEY_RES):
            found.add("api_key")
        for m in _EMAIL_RE.finditer(text):
            if m.group(1).lower() not in _NOT_A_TLD:
                found.add("email")
                break
        if _iban_in(text):
            found.add("iban")
        for m in _CARD_RE.finditer(text):
            digits = re.sub(r"\D", "", m.group(1))
            if 13 <= len(digits) <= 19 and _luhn_ok(digits):
                found.add("payment_card")
                break
        for rx in _PHONE_RES:
            if any(_phone_digits_ok(m.group(0)) for m in rx.finditer(text)):
                found.add("phone")
                break
        for m in _TAX_DE_RE.finditer(text):
            digits = re.sub(r"\D", "", m.group(1))
            lo, hi = max(0, m.start() - 60), min(len(text), m.end() + 20)
            if _tax_id_ok(digits) and _TAX_CONTEXT_RE.search(text[lo:hi]):
                found.add("tax_id_de")
                break
        for m in _SSN_RE.finditer(text):
            area, group, serial = m.groups()
            if area not in ("000", "666") and not area.startswith("9") and group != "00" and serial != "0000":
                found.add("ssn_us")
                break
        return tuple(sorted(found))
    except DetectorError:
        raise
    except Exception as exc:  # noqa: BLE001 - any failure is a refusal upstream, not a PUBLIC turn
        raise DetectorError(type(exc).__name__) from exc


def detected_class(text: str) -> "DataClassification | None":
    """The class the detector alone would assign, or ``None`` when it found nothing."""
    cats = detect(text)
    if not cats:
        return None
    if CREDENTIAL_CATEGORIES & set(cats):
        return DataClassification.SECRET
    return DataClassification.CONFIDENTIAL


def raise_class(current: DataClassification, text: str) -> DataClassification:
    """``max(current, detector)``. Never lowers *current*; raises :class:`DetectorError` on failure."""
    hit = detected_class(text)
    return current if hit is None or hit <= current else hit
