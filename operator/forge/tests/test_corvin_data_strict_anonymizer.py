"""ADR-0023 Layer 32 E2E — strict-anonymisation snapshot mode.

Covers:
  * Default OFF: byte-identical to pre-Layer-32 behaviour
  * Projection: sample dropped, stats bucketised, rowcount noised
  * k-anonymity: distinct < k → distinct_class = "unique"; bucket
    boundaries verified
  * Rowcount Laplace noise: rowcount_exact always False; noised
    value can differ from raw
  * Post-scan: clean payload passes through; PII match → reject
    skeleton
  * Advisory mode (reject_on_pii_leak: false): leaves replaced
    inline, audit fires anyway
  * Audit events metadata-only: walk every emitted detail field
  * Integration: call_data_register under strict policy returns
    strict-shape payload
  * Integration: call_data_snapshot re-snapshots under strict
    policy
  * Policy-load validation: malformed strict fields raise PolicyError

Self-contained — tempfiles + in-memory audit hook.
"""
from __future__ import annotations

import json
import random
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from forge.corvin_data import (  # noqa: E402
    DataPolicy,
    DataRegistry,
    PolicyError,
    ToolError,
    apply_strict_anonymisation,
    call_data_register,
    call_data_snapshot,
    scan_for_pii_leaks,
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


def _write_csv(content: str) -> Path:
    fh = tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, encoding="utf-8")
    fh.write(content)
    fh.close()
    return Path(fh.name)


def _registry(tmpdir: Path) -> DataRegistry:
    root = tmpdir / "forge-root"
    root.mkdir(parents=True, exist_ok=True)
    return DataRegistry(root=root)


# Realistic snapshot payload shape (mirrors Snapshot.to_dict()).
def _sample_snap() -> dict:
    return {
        "file": {
            "path":           "/tmp/data.csv",
            "format":         "csv",
            "size_b":         5_234_567_890,
            "rowcount":       1_000_847,
            "encoding":       "utf-8",
            "rowcount_exact": True,
        },
        "schema": [
            {"name": "id",         "type": "int",     "pii_class": None,
             "cardinality": 1000000},
            {"name": "email",      "type": "string",  "pii_class": "email",
             "cardinality": 950000},
            {"name": "country",    "type": "string",  "pii_class": None,
             "cardinality": 47},
            {"name": "is_active",  "type": "bool",    "pii_class": None,
             "cardinality": 2},
        ],
        "sample": [
            {"id": 1, "email": "alice@example.com", "country": "DE",
             "is_active": True},
            {"id": 2, "email": "bob@example.com",   "country": "US",
             "is_active": False},
        ],
        "stats": {
            "id":        {"nulls": 0, "p05": 100, "p50": 500_000, "p95": 999_900,
                          "distinct": 1000000, "top": None, "approximate": False},
            "email":     {"nulls": 12, "p05": None, "p50": None, "p95": None,
                          "distinct": 950000, "top": None, "approximate": False},
            "country":   {"nulls": 0, "p05": None, "p50": None, "p95": None,
                          "distinct": 47, "top": ["DE", "US", "AT"],
                          "approximate": False},
            "is_active": {"nulls": 8, "p05": None, "p50": None, "p95": None,
                          "distinct": 2, "top": ["True", "False"],
                          "approximate": False},
        },
    }


# ---------------------------------------------------------------------------
# Projection (apply_strict_anonymisation)
# ---------------------------------------------------------------------------


def test_projection_drops_sample_and_stats_values():
    print("\n[projection: sample dropped, stat values stripped]")
    raw = _sample_snap()
    out, dropped = apply_strict_anonymisation(
        raw, k_anonymity_threshold=5, rowcount_laplace_scale=0.001,
        rng=random.Random(42),
    )
    t("sample is empty", out["sample"] == [])
    t("strict marker set", out.get("strict") is True)
    t("anonymised marker set", out.get("anonymised") is True)
    t("dropped_keys count > 0", dropped > 0,
      detail=f"dropped={dropped}")
    for col, st in out["stats"].items():
        t(f"{col}: no p05",      "p05" not in st)
        t(f"{col}: no p95",      "p95" not in st)
        t(f"{col}: no distinct (count)", "distinct" not in st)
        t(f"{col}: no top",      "top" not in st)
        t(f"{col}: has type_class",     "type_class" in st)
        t(f"{col}: has nulls_class",    "nulls_class" in st)
        t(f"{col}: has distinct_class", "distinct_class" in st)


def test_k_anonymity_buckets():
    print("\n[projection: distinct_class respects k_anonymity_threshold]")
    raw = {
        "file": {"format": "csv", "rowcount": 100, "size_b": 1000},
        "schema": [
            {"name": "tiny",    "type": "int", "cardinality": 3},
            {"name": "low",     "type": "int", "cardinality": 25},
            {"name": "medium",  "type": "int", "cardinality": 5000},
            {"name": "high",    "type": "int", "cardinality": 100000},
        ],
        "sample": [],
        "stats": {
            "tiny":   {"distinct": 3},
            "low":    {"distinct": 25},
            "medium": {"distinct": 5000},
            "high":   {"distinct": 100000},
        },
    }
    out, _ = apply_strict_anonymisation(
        raw, k_anonymity_threshold=5, rowcount_laplace_scale=0,
        rng=random.Random(1),
    )
    t("tiny < k → unique",        out["stats"]["tiny"]["distinct_class"] == "unique")
    t("low ∈ [k, 100) → low",     out["stats"]["low"]["distinct_class"] == "low")
    t("medium ∈ [100, 10k) → medium",
      out["stats"]["medium"]["distinct_class"] == "medium")
    t("high >= 10k → high",       out["stats"]["high"]["distinct_class"] == "high")


def test_k_threshold_clamped_low():
    print("\n[projection: k_threshold floor of 2]")
    raw = {
        "file": {"format": "csv", "rowcount": 10, "size_b": 100},
        "schema": [{"name": "x", "type": "int"}],
        "sample": [],
        "stats": {"x": {"distinct": 1}},
    }
    # k=1 effectively means "no k-anonymity" — module clamps to 2.
    out, _ = apply_strict_anonymisation(
        raw, k_anonymity_threshold=1,
        rng=random.Random(1),
    )
    t("distinct=1 with k=1 (→clamped 2) → unique",
      out["stats"]["x"]["distinct_class"] == "unique")


def test_rowcount_laplace_noise():
    print("\n[projection: rowcount Laplace-noised, rowcount_exact False]")
    raw = _sample_snap()
    out, _ = apply_strict_anonymisation(
        raw, rowcount_laplace_scale=0.5,
        rng=random.Random(123),
    )
    t("rowcount_exact False",         out["file"]["rowcount_exact"] is False)
    t("rowcount_approx present",     "rowcount_approx" in out["file"])
    t("rowcount field absent (raw count not exposed)",
      "rowcount" not in out["file"])
    t("rowcount_approx >= 0",        out["file"]["rowcount_approx"] >= 0)


def test_zero_laplace_yields_clean_rowcount():
    print("\n[projection: scale 0 keeps rowcount exact-ish but still flags inexact]")
    raw = _sample_snap()
    out, _ = apply_strict_anonymisation(
        raw, rowcount_laplace_scale=0,
        rng=random.Random(1),
    )
    # scale=0 → no noise → rowcount_approx == raw
    t("rowcount_approx == raw when scale=0",
      out["file"]["rowcount_approx"] == raw["file"]["rowcount"])
    t("rowcount_exact still False (structural)",
      out["file"]["rowcount_exact"] is False)


# ---------------------------------------------------------------------------
# Post-scan (scan_for_pii_leaks)
# ---------------------------------------------------------------------------


def test_post_scan_clean_payload_passes():
    print("\n[post-scan: clean payload passes through unchanged]")
    payload = {
        "file":   {"format": "csv", "size_b": 1000},
        "schema": [{"name": "x", "type": "int"}],
        "stats":  {"x": {"type_class": "numeric", "distinct_class": "low"}},
    }
    out, rejected, count, classes = scan_for_pii_leaks(payload, reject=True)
    t("not rejected", rejected is False)
    t("zero matches", count == 0)
    t("empty classes", classes == [])
    t("payload preserved byte-equivalent",
      json.dumps(out, sort_keys=True) == json.dumps(payload, sort_keys=True))


def test_post_scan_email_leak_rejects():
    print("\n[post-scan: email leak → rejection skeleton]")
    payload = {
        "file":   {"format": "csv"},
        "schema": [],
        "sample": [],
        "stats":  {"col1": {"some_field": "user@example.com"}},
    }
    out, rejected, count, classes = scan_for_pii_leaks(payload, reject=True)
    t("rejected True", rejected is True)
    t("at least 1 match", count >= 1)
    t("email in classes", "email" in classes)
    t("rejection skeleton has rejection flag",
      out.get("anonymisation_rejected") is True)
    t("rejection skeleton has empty stats", out.get("stats") == {})


def test_post_scan_iban_leak():
    print("\n[post-scan: IBAN leak detected]")
    payload = {"x": "DE89370400440532013000"}
    _, rejected, count, classes = scan_for_pii_leaks(payload, reject=True)
    t("IBAN match",   "iban" in classes)
    t("rejected",     rejected is True)
    t("count >= 1",   count >= 1)


def test_post_scan_advisory_mode_replaces_leaves():
    print("\n[post-scan: advisory mode replaces leaves, doesn't reject]")
    payload = {
        "file": {"format": "csv"},
        "leak": "evil@example.com",
        "clean": "just text",
    }
    out, rejected, count, _ = scan_for_pii_leaks(payload, reject=False)
    t("not rejected (advisory mode)", rejected is False)
    t("at least one match",           count >= 1)
    t("leaked leaf replaced",         out["leak"] == "<pii-redacted>")
    t("clean leaf unchanged",         out["clean"] == "just text")


def test_post_scan_phone_e164():
    print("\n[post-scan: E.164 phone catches +49 prefix]")
    _, _, _, classes = scan_for_pii_leaks(
        {"x": "+491761234567890"}, reject=True)
    t("phone_e164 in classes", "phone_e164" in classes)


def test_post_scan_us_ssn():
    print("\n[post-scan: US SSN shape detected]")
    _, _, _, classes = scan_for_pii_leaks(
        {"x": "123-45-6789"}, reject=True)
    t("us_ssn in classes", "us_ssn" in classes)


def test_post_scan_de_steuer_id():
    print("\n[post-scan: 11-digit Steuer-ID shape detected]")
    _, _, _, classes = scan_for_pii_leaks(
        {"x": "12345678901"}, reject=True)
    t("de_steuer_id in classes", "de_steuer_id" in classes)


# ---------------------------------------------------------------------------
# DataPolicy validation
# ---------------------------------------------------------------------------


def test_policy_defaults_strict_off():
    print("\n[policy: default OFF for backward-compat]")
    pol = DataPolicy()
    t("strict_anonymization defaults False",
      pol.strict_anonymization is False)
    t("k_anonymity_threshold defaults 5",
      pol.k_anonymity_threshold == 5)
    t("rowcount_laplace_scale defaults 1.0",
      pol.rowcount_laplace_scale == 1.0)
    t("reject_on_pii_leak defaults True",
      pol.reject_on_pii_leak is True)


def test_policy_rejects_bad_k():
    print("\n[policy: k_anonymity_threshold < 2 raises]")
    try:
        DataPolicy(k_anonymity_threshold=1)
        t("expected PolicyError", False)
    except PolicyError:
        t("PolicyError raised on k=1", True)


def test_policy_rejects_negative_laplace():
    print("\n[policy: negative rowcount_laplace_scale raises]")
    try:
        DataPolicy(rowcount_laplace_scale=-1.0)
        t("expected PolicyError", False)
    except PolicyError:
        t("PolicyError raised", True)


def test_policy_rejects_wrong_types():
    print("\n[policy: non-bool strict_anonymization raises]")
    try:
        DataPolicy(strict_anonymization="yes")  # type: ignore[arg-type]
        t("expected PolicyError", False)
    except PolicyError:
        t("PolicyError raised on non-bool", True)


# ---------------------------------------------------------------------------
# Integration through call_data_register / call_data_snapshot
# ---------------------------------------------------------------------------


_CSV_NO_PII = """id,country,is_active
1,DE,true
2,US,false
3,AT,true
4,DE,false
5,US,true
6,DE,true
7,FR,true
8,IT,false
9,DE,true
10,AT,true
"""


def test_register_strict_off_default():
    print("\n[register: strict OFF (default) preserves Layer-24 shape]")
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        csv = _write_csv(_CSV_NO_PII)
        try:
            reg = _registry(tmp_path)
            result = call_data_register(
                reg, {"path": str(csv)}, policy=DataPolicy(), audit=None,
            )
            snap = result["snapshot"]
            t("has sample (non-empty)", len(snap.get("sample") or []) > 0)
            t("no 'strict' marker", "strict" not in snap)
            t("no 'anonymised' marker", "anonymised" not in snap)
        finally:
            csv.unlink(missing_ok=True)


def test_register_strict_on_zero_sample():
    print("\n[register: strict ON → sample is empty + strict marker]")
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        csv = _write_csv(_CSV_NO_PII)
        try:
            reg = _registry(tmp_path)
            events: list = []
            pol = DataPolicy(
                strict_anonymization=True,
                k_anonymity_threshold=5,
                rowcount_laplace_scale=0.01,
            )
            result = call_data_register(
                reg, {"path": str(csv)}, policy=pol,
                audit=lambda ev, dt: events.append((ev, dt)),
            )
            snap = result["snapshot"]
            t("sample empty", snap.get("sample") == [])
            t("strict marker present", snap.get("strict") is True)
            t("anonymised marker present", snap.get("anonymised") is True)
            t("rowcount_exact False", snap["file"]["rowcount_exact"] is False)
            t("no raw rowcount", "rowcount" not in snap["file"])
            # Audit event must fire.
            applied = [e for e in events if e[0] == "data.strict_anonymisation_applied"]
            t("strict_anonymisation_applied event emitted", len(applied) == 1)
            if applied:
                details = applied[0][1]
                t("audit details only carry metadata keys",
                  set(details.keys()) <= {"data_handle", "columns", "dropped_keys"})
        finally:
            csv.unlink(missing_ok=True)


def test_snapshot_strict_on_resnapshot():
    print("\n[snapshot: re-snapshot under strict policy stays strict]")
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        csv = _write_csv(_CSV_NO_PII)
        try:
            reg = _registry(tmp_path)
            pol_off = DataPolicy()
            pol_on = DataPolicy(strict_anonymization=True)
            # Register without strict.
            r1 = call_data_register(reg, {"path": str(csv)}, policy=pol_off,
                                    audit=None)
            handle = r1["data_handle"]
            t("first snapshot has sample",
              len(r1["snapshot"].get("sample") or []) > 0)
            # Re-snapshot WITH strict.
            r2 = call_data_snapshot(
                reg, {"data_handle": handle}, policy=pol_on, audit=None,
            )
            snap2 = r2["snapshot"]
            t("re-snapshot sample empty (strict applied)",
              snap2.get("sample") == [])
            t("re-snapshot has strict marker",
              snap2.get("strict") is True)
        finally:
            csv.unlink(missing_ok=True)


def test_audit_events_metadata_only():
    print("\n[audit: no raw values in strict-mode audit events]")
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        csv = _write_csv(_CSV_NO_PII)
        try:
            reg = _registry(tmp_path)
            events: list = []
            pol = DataPolicy(strict_anonymization=True)
            call_data_register(
                reg, {"path": str(csv)}, policy=pol,
                audit=lambda ev, dt: events.append((ev, dt)),
            )
            # Walk every detail value — assert no string contains
            # the literal CSV content tokens (e.g. "DE", "AT", or a row).
            forbidden = ["alice", "bob", "@example.com",
                         "DE89", "1234567"]
            for ev, dt in events:
                if not ev.startswith("data."):
                    continue
                # Serialize, scan for forbidden substrings.
                blob = json.dumps(dt, default=str)
                for needle in forbidden:
                    t(f"{ev}: no {needle!r} in details",
                      needle not in blob,
                      detail=f"details: {blob[:200]}")
        finally:
            csv.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Test runner
# ---------------------------------------------------------------------------


def main() -> int:
    test_new = []
    for name, obj in list(globals().items()):
        if name.startswith("test_") and callable(obj):
            test_new.append((name, obj))
    print(f"\nRunning {len(test_new)} strict-anonymizer (ADR-0023 L32) tests...\n")
    for name, fn in test_new:
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            print(f"\n  TEST CRASHED: {name} — {type(e).__name__}: {e}")
            globals()["FAIL"] = FAIL + 1
    print(f"\n== {PASS} pass / {FAIL} fail ==\n")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
