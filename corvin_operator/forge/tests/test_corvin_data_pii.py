"""Phase 12.2 E2E — PII detection (header + value-regex).

Covers:
  * header heuristics — name patterns map to PII classes
  * value regex     — email / phone / IBAN / SSN / CC / Steuer-ID / AHV
  * conflict resolution — value beats header on disagreement
  * threshold gating — below-threshold hit-rate produces no class
  * operator overrides — per-column directive bypasses detection
  * apply_pii_detection on a full Snapshot
  * detection_summary returns class counts only (no PII content)

No external deps. Builds in-memory Snapshot objects for the
``apply_pii_detection`` exercise (no temp files).
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from forge.corvin_data import (  # noqa: E402
    apply_pii_detection,
    detect_column_pii,
    detection_summary,
    generate_snapshot,
    PII_CLASSES,
    Snapshot,
    SnapshotOptions,
)
from forge.corvin_data.snapshot import (  # noqa: E402
    ColumnSchema,
    ColumnStats,
    FileMeta,
)


PASS = 0
FAIL = 0


def t(label: str, ok: bool, *, detail: str = "") -> None:
    global PASS, FAIL
    print(f"  {'PASS' if ok else 'FAIL'}  {label}{(' — ' + detail) if detail else ''}")
    if ok:
        PASS += 1
    else:
        FAIL += 1


# ---------------------------------------------------------------------------
# Header heuristics
# ---------------------------------------------------------------------------

def test_header_email():
    print("\n[header: email/mail variants]")
    for n in ["email", "Email", "EMAIL", "e_mail", "user_email", "MAIL"]:
        r = detect_column_pii(n, [])
        t(f"{n} → email", r.pii_class == "email", detail=str(r.pii_class))


def test_header_phone():
    print("\n[header: phone variants]")
    for n in ["phone", "tel", "telephone", "mobile", "user_phone"]:
        r = detect_column_pii(n, [])
        t(f"{n} → phone", r.pii_class == "phone", detail=str(r.pii_class))


def test_header_iban():
    print("\n[header: iban / bic]")
    r1 = detect_column_pii("iban", [])
    r2 = detect_column_pii("BIC", [])
    t("iban → iban", r1.pii_class == "iban")
    t("BIC → iban", r2.pii_class == "iban")


def test_header_name():
    print("\n[header: name patterns]")
    for n in ["name", "firstname", "surname", "lastname", "fullname",
              "first_name", "last_name", "vorname", "nachname"]:
        r = detect_column_pii(n, [])
        t(f"{n} → name", r.pii_class == "name", detail=str(r.pii_class))


def test_header_address():
    print("\n[header: address-shaped fields]")
    for n in ["address", "street", "zip", "postcode", "PLZ", "city"]:
        r = detect_column_pii(n, [])
        t(f"{n} → address", r.pii_class == "address", detail=str(r.pii_class))


def test_header_unknown_no_match():
    print("\n[header: random column names don't fire]")
    for n in ["price", "amount", "quantity", "product_id"]:
        r = detect_column_pii(n, [])
        t(f"{n} no class", r.pii_class is None, detail=str(r.pii_class))


def test_header_low_confidence():
    print("\n[header: confidence is 0.6 when only header matches]")
    r = detect_column_pii("email", [])
    t("confidence 0.6 (header only)", r.confidence == 0.6,
      detail=str(r.confidence))


# ---------------------------------------------------------------------------
# Value regex
# ---------------------------------------------------------------------------

def test_value_email():
    print("\n[value: emails detected without header signal]")
    r = detect_column_pii("contact_field", [
        "alice@example.com",
        "bob@example.org",
        "carol@test.de",
        "x@y.co",
        "trailing.dot.skipped",
    ])
    t("class = email", r.pii_class == "email", detail=str(r.pii_class))
    t("source has value-regex", "value-regex" in r.source, detail=r.source)


def test_value_iban():
    print("\n[value: IBAN-shaped strings]")
    r = detect_column_pii("acct", [
        "DE89370400440532013000",
        "AT611904300234573201",
        "CH9300762011623852957",
    ])
    t("class = iban", r.pii_class == "iban", detail=str(r.pii_class))


def test_value_us_ssn():
    print("\n[value: US SSN format]")
    r = detect_column_pii("identifier", [
        "123-45-6789",
        "987-65-4321",
        "555-00-1111",
        "111-22-3333",
    ])
    t("class = us_ssn", r.pii_class == "us_ssn", detail=str(r.pii_class))


def test_value_ch_ahv():
    print("\n[value: Swiss AHV format]")
    r = detect_column_pii("nummer", [
        "756.1234.5678.97",
        "756.9876.5432.10",
        "756.0001.0002.03",
    ])
    t("class = ch_ahv", r.pii_class == "ch_ahv", detail=str(r.pii_class))


def test_value_de_steuer_id():
    print("\n[value: German 11-digit Steuer-ID]")
    r = detect_column_pii("identifier", [
        "12345678901",
        "98765432109",
        "11122233344",
        "55566677788",
    ])
    t("class = de_steuer_id", r.pii_class == "de_steuer_id", detail=str(r.pii_class))


def test_value_credit_card():
    print("\n[value: credit-card digit run]")
    r = detect_column_pii("payment_token", [
        "4111-1111-1111-1111",
        "5500 0000 0000 0004",
        "3400 000000 00009",
        "6011 0000 0000 0004",
        "4012-8888-8888-1881",
    ])
    t("class = credit_card", r.pii_class == "credit_card", detail=str(r.pii_class))


def test_value_phone():
    print("\n[value: phone with international prefix]")
    r = detect_column_pii("contact", [
        "+49 30 12345678",
        "+1 555 123 4567",
        "+44 20 7946 0958",
        "+33 1 23 45 67 89",
        "+41 44 668 18 00",
    ])
    t("class = phone", r.pii_class == "phone", detail=str(r.pii_class))


def test_value_below_threshold_no_class():
    print("\n[value: 1 email in 10 random strings → no class]")
    r = detect_column_pii("free_text", [
        "alice@example.com",
        "some random text",
        "more text without email",
        "anything goes here",
        "another line",
        "lorem ipsum",
        "dolor sit amet",
        "consectetur",
        "adipiscing elit",
        "sed do eiusmod",
    ])
    t("below threshold → None", r.pii_class is None, detail=str(r.pii_class))


def test_value_high_confidence_alone():
    print("\n[value: confidence 0.90 when only value-regex fires]")
    r = detect_column_pii("opaque_field", [
        "alice@example.com",
        "bob@example.org",
        "carol@test.de",
    ])
    t("confidence 0.90", r.confidence == 0.90, detail=str(r.confidence))


# ---------------------------------------------------------------------------
# Conflict resolution
# ---------------------------------------------------------------------------

def test_conflict_value_overrides_header():
    print("\n[conflict: column called 'phone' but values are emails → email wins]")
    r = detect_column_pii("phone", [
        "alice@example.com",
        "bob@example.com",
        "carol@example.com",
        "dave@example.com",
    ])
    t("class = email (not phone from header)",
      r.pii_class == "email", detail=str(r.pii_class))
    t("source notes override", "overrode header" in r.source, detail=r.source)


def test_agreement_boosts_confidence():
    print("\n[conflict: header + value agree → confidence 0.95]")
    r = detect_column_pii("email", [
        "alice@example.com",
        "bob@example.org",
    ])
    t("class = email", r.pii_class == "email")
    t("confidence 0.95", r.confidence == 0.95, detail=str(r.confidence))


# ---------------------------------------------------------------------------
# Operator overrides
# ---------------------------------------------------------------------------

def test_override_forces_class():
    print("\n[override: per-column override forces class]")
    r = detect_column_pii("notes", [
        "random text 1",
        "random text 2",
    ], overrides={"notes": "name"})
    t("override forces name", r.pii_class == "name", detail=str(r.pii_class))
    t("source = override", r.source == "override")
    t("confidence 1.0", r.confidence == 1.0)


def test_override_only_applies_to_named_column():
    print("\n[override: scope limited to the named column]")
    r = detect_column_pii("other_column", [], overrides={"notes": "name"})
    t("other column unchanged", r.pii_class is None, detail=str(r.pii_class))


# ---------------------------------------------------------------------------
# apply_pii_detection on a full snapshot
# ---------------------------------------------------------------------------

def _make_snapshot_from_inline(rows: list[dict]) -> Snapshot:
    """Build a minimal Snapshot directly from in-memory rows for testing."""
    # Derive schema from row union
    keys: list[str] = []
    seen = set()
    for r in rows:
        for k in r.keys():
            if k not in seen:
                seen.add(k)
                keys.append(k)
    schema = [ColumnSchema(name=k, type="string") for k in keys]
    stats = {k: ColumnStats(nulls=0) for k in keys}
    return Snapshot(
        file=FileMeta(path="<inline>", format="csv", size_b=0, rowcount=len(rows)),
        schema=schema,
        sample=rows,
        stats=stats,
    )


def test_apply_to_snapshot_populates_pii_class():
    print("\n[apply: snapshot.schema gets pii_class set]")
    snap = _make_snapshot_from_inline([
        {"customer_email": "alice@example.com", "amount": "10.5", "country": "DE"},
        {"customer_email": "bob@example.com",   "amount": "20.0", "country": "AT"},
        {"customer_email": "c@example.com",     "amount": "30.5", "country": "CH"},
    ])
    apply_pii_detection(snap)
    cols = {c.name: c for c in snap.schema}
    t("email column tagged", cols["customer_email"].pii_class == "email")
    t("amount untagged", cols["amount"].pii_class is None)
    t("country untagged", cols["country"].pii_class is None)


def test_apply_with_overrides():
    print("\n[apply: operator overrides folded in]")
    snap = _make_snapshot_from_inline([
        {"notes": "harmless text"},
        {"notes": "more text"},
    ])
    apply_pii_detection(snap, overrides={"notes": "name"})
    t("notes forced to name",
      snap.schema[0].pii_class == "name", detail=str(snap.schema[0].pii_class))


# ---------------------------------------------------------------------------
# detection_summary
# ---------------------------------------------------------------------------

def test_detection_summary_counts_only():
    print("\n[summary: returns class counts (no values, no column names)]")
    snap = _make_snapshot_from_inline([
        {"email_a": "alice@example.com", "email_b": "bob@example.com",
         "phone_x": "+49 30 12345678", "amount": "10"},
        {"email_a": "carol@example.com", "email_b": "dave@example.com",
         "phone_x": "+49 30 87654321", "amount": "20"},
    ])
    apply_pii_detection(snap)
    s = detection_summary(snap)
    t("email count 2", s.get("email") == 2, detail=str(s))
    t("phone count 1", s.get("phone") == 1, detail=str(s))
    t("no_pii count 1", s.get("<no_pii>") == 1, detail=str(s))
    # No value content in the summary
    t("summary has only str keys",
      all(isinstance(k, str) for k in s.keys()))


# ---------------------------------------------------------------------------
# End-to-end: snapshot + detection chained
# ---------------------------------------------------------------------------

def test_e2e_csv_to_pii():
    print("\n[e2e: generate_snapshot + apply_pii_detection]")
    fh = tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, encoding="utf-8")
    fh.write("customer_email,amount,country,phone\n")
    fh.write("alice@example.com,10.5,DE,+49 30 12345678\n")
    fh.write("bob@example.com,20.0,AT,+43 1 234567\n")
    fh.write("carol@example.com,30.5,CH,+41 44 6681800\n")
    fh.close()
    p = Path(fh.name)
    try:
        snap = generate_snapshot(p, options=SnapshotOptions(seed=0))
        apply_pii_detection(snap)
        cols = {c.name: c for c in snap.schema}
        t("email detected via header+value",
          cols["customer_email"].pii_class == "email")
        t("phone detected via header (+value if regex hits)",
          cols["phone"].pii_class == "phone")
        t("amount untagged", cols["amount"].pii_class is None)
        t("country untagged", cols["country"].pii_class is None)
        summary = detection_summary(snap)
        t("summary has email", summary.get("email") == 1, detail=str(summary))
    finally:
        p.unlink()


# ---------------------------------------------------------------------------
# Invariants
# ---------------------------------------------------------------------------

def test_known_classes_are_curated():
    print("\n[invariant: PII_CLASSES is curated, not extensible at runtime]")
    expected = {
        "email", "phone", "iban", "credit_card",
        "us_ssn", "ch_ahv", "de_steuer_id",
        "name", "date_of_birth", "address",
        "opaque_id", "national_id",
    }
    t("matches curated set", set(PII_CLASSES) == expected,
      detail=str(set(PII_CLASSES) - expected))


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main() -> int:
    test_header_email()
    test_header_phone()
    test_header_iban()
    test_header_name()
    test_header_address()
    test_header_unknown_no_match()
    test_header_low_confidence()

    test_value_email()
    test_value_iban()
    test_value_us_ssn()
    test_value_ch_ahv()
    test_value_de_steuer_id()
    test_value_credit_card()
    test_value_phone()
    test_value_below_threshold_no_class()
    test_value_high_confidence_alone()

    test_conflict_value_overrides_header()
    test_agreement_boosts_confidence()

    test_override_forces_class()
    test_override_only_applies_to_named_column()

    test_apply_to_snapshot_populates_pii_class()
    test_apply_with_overrides()

    test_detection_summary_counts_only()
    test_e2e_csv_to_pii()
    test_known_classes_are_curated()

    print(f"\n{'=' * 50}")
    print(f"PASS={PASS}  FAIL={FAIL}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
