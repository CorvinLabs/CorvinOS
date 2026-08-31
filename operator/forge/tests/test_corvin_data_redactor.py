"""Phase 12.3 E2E — redaction strategies + data_policy loader.

Covers:
  * each strategy applied to a single value (redact, pseudonymize,
    mask_partial, hash) with class-specific behaviour
  * pseudonymize determinism per (seed, value) + cross-seed unlinkability
  * mask_partial class-specific reveals (email: ``j****@***.com``, etc.)
  * apply_redaction on a full Snapshot — drop / aggregate_only / per-value
  * top values in stats are also redacted
  * pseudonymize-without-seed falls back to redact (no crash)
  * data_policy loader: JSON, YAML (when PyYAML available), env var,
    corvin-home discovery, validation errors
  * RedactionPolicy.strategy_for resolution: column > class > default

Builds in-memory Snapshot objects for the apply_redaction exercise
plus tempfiles for the policy loader.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from forge.corvin_data import (  # noqa: E402
    DataPolicy,
    NoiseConfig,
    PolicyError,
    RedactionError,
    RedactionPolicy,
    STRATEGIES,
    apply_redaction,
    hash_value,
    load_policy,
    mask_partial,
    pseudonymize,
    redact,
)
from forge.corvin_data.snapshot import (  # noqa: E402
    ColumnSchema,
    ColumnStats,
    FileMeta,
    Snapshot,
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
# Per-value strategies
# ---------------------------------------------------------------------------

def test_redact_value_uses_class_tag():
    print("\n[redact: value replaced with class tag]")
    t("email", redact("alice@example.com", "email") == "<email>")
    t("phone", redact("+49 30 12345678", "phone") == "<phone>")
    t("unknown class", redact("x", "weird_class") == "<weird_class>")


def test_pseudonymize_deterministic():
    print("\n[pseudonymize: same seed + same value → same token]")
    a = pseudonymize("alice@example.com", "email", seed="tenant-A-seed")
    b = pseudonymize("alice@example.com", "email", seed="tenant-A-seed")
    t("deterministic", a == b, detail=f"{a!r} vs {b!r}")
    t("format prefix", a.startswith("***pseudo:"))


def test_pseudonymize_cross_seed_unlinkable():
    print("\n[pseudonymize: different seeds → different tokens]")
    a = pseudonymize("alice@example.com", "email", seed="tenant-A")
    b = pseudonymize("alice@example.com", "email", seed="tenant-B")
    t("different tokens", a != b)


def test_pseudonymize_none_value():
    print("\n[pseudonymize: None falls back to class tag]")
    out = pseudonymize(None, "email", seed="x")
    t("None handled", out == "<email>", detail=out)


def test_mask_partial_email():
    print("\n[mask_partial: email partial reveal]")
    out = mask_partial("alice@example.com", "email")
    t("starts with a", out.startswith("a"), detail=out)
    t("contains @", "@" in out, detail=out)
    t("ends with .com", out.endswith(".com"), detail=out)


def test_mask_partial_phone_last4():
    print("\n[mask_partial: phone keeps last 4 digits]")
    out = mask_partial("+49 30 1234 5678", "phone")
    t("last 4 visible", out.endswith("5678"), detail=out)
    t("rest masked", out.count("*") > 0, detail=out)


def test_mask_partial_credit_card_last4():
    print("\n[mask_partial: credit card keeps last 4]")
    out = mask_partial("4111-1111-1111-1234", "credit_card")
    t("last 4 visible", out.endswith("1234"), detail=out)


def test_mask_partial_iban_keeps_first4_last4():
    print("\n[mask_partial: IBAN keeps first 4 + last 4]")
    out = mask_partial("DE89370400440532013000", "iban")
    t("first 4 visible", out.startswith("DE89"), detail=out)
    t("last 4 visible", out.endswith("3000"), detail=out)


def test_mask_partial_us_ssn():
    print("\n[mask_partial: SSN keeps last 4]")
    out = mask_partial("123-45-6789", "us_ssn")
    t("format ***-**-NNNN", out == "***-**-6789", detail=out)


def test_hash_value_stable():
    print("\n[hash: stable per value]")
    a = hash_value("alice")
    b = hash_value("alice")
    t("stable", a == b)
    t("format prefix", a.startswith("<hash:"))


def test_hash_value_diff_for_diff_inputs():
    print("\n[hash: different inputs → different hashes]")
    a = hash_value("alice")
    b = hash_value("bob")
    t("different", a != b)


# ---------------------------------------------------------------------------
# Snapshot-level apply_redaction
# ---------------------------------------------------------------------------

def _make_snapshot(rows: list[dict], schema_classes: dict[str, str | None]) -> Snapshot:
    keys = list(schema_classes.keys())
    schema = [
        ColumnSchema(name=k, type="string", pii_class=schema_classes[k])
        for k in keys
    ]
    stats = {k: ColumnStats(nulls=0) for k in keys}
    return Snapshot(
        file=FileMeta(path="<inline>", format="csv", size_b=0, rowcount=len(rows)),
        schema=schema,
        sample=rows,
        stats=stats,
    )


def test_apply_redact_default_strategy():
    print("\n[apply: default strategy = redact]")
    snap = _make_snapshot(
        [
            {"email": "alice@example.com", "amount": "10"},
            {"email": "bob@example.com",   "amount": "20"},
        ],
        {"email": "email", "amount": None},
    )
    apply_redaction(snap)
    t("email redacted", snap.sample[0]["email"] == "<email>")
    t("amount untouched", snap.sample[0]["amount"] == "10")


def test_apply_class_strategy_pseudonymize():
    print("\n[apply: class_strategies email→pseudonymize]")
    pol = RedactionPolicy(
        default_strategy="redact",
        class_strategies={"email": "pseudonymize"},
    )
    snap = _make_snapshot(
        [
            {"email": "alice@example.com"},
            {"email": "alice@example.com"},  # same value → same pseudo
            {"email": "bob@example.com"},
        ],
        {"email": "email"},
    )
    apply_redaction(snap, pol, seed="seed-1")
    t("alice consistent",
      snap.sample[0]["email"] == snap.sample[1]["email"])
    t("bob different",
      snap.sample[0]["email"] != snap.sample[2]["email"])
    t("starts pseudo:",
      snap.sample[0]["email"].startswith("***pseudo:"))


def test_apply_column_override_wins():
    print("\n[apply: column_overrides beats class default]")
    pol = RedactionPolicy(
        default_strategy="redact",
        class_strategies={"email": "redact"},
        column_overrides={"customer_email": "drop"},
    )
    snap = _make_snapshot(
        [
            {"customer_email": "alice@example.com", "phone": "+49 30 12345678"},
            {"customer_email": "bob@example.com",   "phone": "+49 30 87654321"},
        ],
        {"customer_email": "email", "phone": "phone"},
    )
    apply_redaction(snap, pol, seed="x")
    t("customer_email dropped from sample",
      "customer_email" not in snap.sample[0])
    t("customer_email dropped from schema",
      not any(c.name == "customer_email" for c in snap.schema))
    t("phone still present + redacted",
      snap.sample[0]["phone"] == "<phone>")


def test_apply_aggregate_only_keeps_stats():
    print("\n[apply: aggregate_only drops sample but keeps schema + stats]")
    pol = RedactionPolicy(
        default_strategy="aggregate_only",
    )
    snap = _make_snapshot(
        [
            {"email": "alice@example.com"},
            {"email": "bob@example.com"},
        ],
        {"email": "email"},
    )
    apply_redaction(snap, pol)
    t("sample empty for email", "email" not in snap.sample[0])
    t("schema kept", any(c.name == "email" for c in snap.schema))
    t("stats kept", "email" in snap.stats)


def test_apply_mask_partial_per_class():
    print("\n[apply: mask_partial applied per class]")
    pol = RedactionPolicy(default_strategy="mask_partial")
    snap = _make_snapshot(
        [
            {"email": "alice@example.com", "phone": "+49 30 12345678"},
        ],
        {"email": "email", "phone": "phone"},
    )
    apply_redaction(snap, pol)
    t("email partial reveal",
      "@" in snap.sample[0]["email"] and "*" in snap.sample[0]["email"])
    t("phone last-4 reveal",
      snap.sample[0]["phone"].endswith("5678"))


def test_apply_redacts_top_values():
    print("\n[apply: stats.top values get redacted too]")
    pol = RedactionPolicy(default_strategy="redact")
    snap = _make_snapshot(
        [{"email": "alice@example.com"}],
        {"email": "email"},
    )
    snap.stats["email"].top = ["alice@example.com", "bob@example.com"]
    apply_redaction(snap, pol)
    t("top values redacted",
      all(v == "<email>" for v in snap.stats["email"].top),
      detail=str(snap.stats["email"].top))


def test_pseudonymize_without_seed_falls_back():
    print("\n[apply: pseudonymize without seed → redact fallback (no crash)]")
    pol = RedactionPolicy(
        default_strategy="redact",
        class_strategies={"email": "pseudonymize"},
    )
    snap = _make_snapshot(
        [{"email": "alice@example.com"}],
        {"email": "email"},
    )
    apply_redaction(snap, pol)  # no seed kwarg
    t("fell back to redact",
      snap.sample[0]["email"] == "<email>")


def test_columns_without_pii_class_untouched():
    print("\n[apply: columns without pii_class stay untouched]")
    pol = RedactionPolicy(default_strategy="redact")
    snap = _make_snapshot(
        [{"amount": "10.5", "country": "DE"}],
        {"amount": None, "country": None},
    )
    apply_redaction(snap, pol)
    t("amount unchanged", snap.sample[0]["amount"] == "10.5")
    t("country unchanged", snap.sample[0]["country"] == "DE")


# ---------------------------------------------------------------------------
# RedactionPolicy.strategy_for resolution
# ---------------------------------------------------------------------------

def test_strategy_for_default():
    p = RedactionPolicy(default_strategy="redact")
    t("default applies", p.strategy_for("anything", "email") == "redact")


def test_strategy_for_class_overrides_default():
    p = RedactionPolicy(
        default_strategy="redact",
        class_strategies={"email": "pseudonymize"},
    )
    t("class wins over default",
      p.strategy_for("any_col", "email") == "pseudonymize")
    t("class only for matching pii_class",
      p.strategy_for("any_col", "phone") == "redact")


def test_strategy_for_column_overrides_all():
    p = RedactionPolicy(
        default_strategy="redact",
        class_strategies={"email": "pseudonymize"},
        column_overrides={"customer_email": "drop"},
    )
    t("column wins over class",
      p.strategy_for("customer_email", "email") == "drop")


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def test_unknown_strategy_rejected():
    print("\n[validate: unknown strategy raises]")
    try:
        RedactionPolicy(default_strategy="not_a_strategy")
        t("rejected", False, detail="should have raised")
    except RedactionError:
        t("rejected", True)


def test_class_strategy_validated():
    print("\n[validate: class_strategies value validated]")
    try:
        RedactionPolicy(
            default_strategy="redact",
            class_strategies={"email": "made_up"},
        )
        t("rejected", False, detail="should have raised")
    except RedactionError:
        t("rejected", True)


# ---------------------------------------------------------------------------
# Policy loader
# ---------------------------------------------------------------------------

def test_load_policy_json():
    print("\n[loader: JSON file]")
    fh = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
    json.dump({
        "apiVersion": "corvin/v1",
        "kind": "DataPolicy",
        "spec": {
            "pii_backend": "regex+headers",
            "default_strategy": "redact",
            "class_strategies": {"email": "pseudonymize"},
            "column_overrides": {"notes": "aggregate_only"},
        },
    }, fh)
    fh.close()
    p = Path(fh.name)
    try:
        pol = load_policy(p)
        t("default_strategy redact", pol.default_strategy == "redact")
        t("class_strategies email pseudo",
          pol.class_strategies.get("email") == "pseudonymize")
        t("column_overrides notes aggregate",
          pol.column_overrides.get("notes") == "aggregate_only")
        rpol = pol.to_redaction_policy()
        t("projected to RedactionPolicy",
          rpol.strategy_for("notes", "name") == "aggregate_only")
    finally:
        p.unlink()


def test_load_policy_yaml_when_available():
    print("\n[loader: YAML file (skipped if PyYAML missing)]")
    try:
        import yaml  # noqa: F401
    except ImportError:
        print("    SKIP (PyYAML not installed)")
        return
    fh = tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False)
    fh.write(
        "apiVersion: corvin/v1\n"
        "kind: DataPolicy\n"
        "spec:\n"
        "  pii_backend: regex+headers\n"
        "  default_strategy: pseudonymize\n"
        "  noise:\n"
        "    rowcount_jitter: 7\n"
        "    distinct_jitter: 2\n"
    )
    fh.close()
    p = Path(fh.name)
    try:
        pol = load_policy(p)
        t("default pseudonymize", pol.default_strategy == "pseudonymize")
        t("noise rowcount_jitter 7", pol.noise.rowcount_jitter == 7)
        t("noise distinct_jitter 2", pol.noise.distinct_jitter == 2)
    finally:
        p.unlink()


def test_load_policy_unknown_strategy_rejected():
    print("\n[loader: unknown strategy in policy rejected]")
    fh = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
    json.dump({
        "apiVersion": "corvin/v1",
        "kind": "DataPolicy",
        "spec": {"default_strategy": "made_up"},
    }, fh)
    fh.close()
    p = Path(fh.name)
    try:
        try:
            load_policy(p)
            t("rejected", False, detail="should have raised")
        except PolicyError:
            t("rejected", True)
    finally:
        p.unlink()


def test_load_policy_bad_apiversion_rejected():
    print("\n[loader: wrong apiVersion rejected]")
    fh = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
    json.dump({
        "apiVersion": "corvin/v999",
        "kind": "DataPolicy",
        "spec": {"default_strategy": "redact"},
    }, fh)
    fh.close()
    p = Path(fh.name)
    try:
        try:
            load_policy(p)
            t("rejected", False, detail="should have raised")
        except PolicyError as e:
            t("rejected", "apiVersion" in str(e))
    finally:
        p.unlink()


def test_load_policy_no_file_returns_default():
    print("\n[loader: no file → defaults]")
    # Save & clear env, then ensure no corvin_home present.
    saved = {
        k: os.environ.pop(k, None)
        for k in ("CORVIN_DATA_POLICY", "CORVIN_HOME")
    }
    try:
        pol = load_policy()
        t("defaults", pol.default_strategy == "redact")
        t("pii_backend default", pol.pii_backend == "regex+headers")
    finally:
        for k, v in saved.items():
            if v is not None:
                os.environ[k] = v


def test_load_policy_from_env_var():
    print("\n[loader: CORVIN_DATA_POLICY env var]")
    fh = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
    json.dump({
        "apiVersion": "corvin/v1",
        "kind": "DataPolicy",
        "spec": {"default_strategy": "drop"},
    }, fh)
    fh.close()
    p = Path(fh.name)
    saved = os.environ.get("CORVIN_DATA_POLICY")
    try:
        os.environ["CORVIN_DATA_POLICY"] = str(p)
        pol = load_policy()
        t("policy from env", pol.default_strategy == "drop")
    finally:
        if saved is None:
            os.environ.pop("CORVIN_DATA_POLICY", None)
        else:
            os.environ["CORVIN_DATA_POLICY"] = saved
        p.unlink()


# ---------------------------------------------------------------------------
# Invariants
# ---------------------------------------------------------------------------

def test_strategies_curated():
    print("\n[invariant: STRATEGIES is curated]")
    expected = {"drop", "redact", "pseudonymize", "mask_partial",
                "aggregate_only", "hash"}
    t("six strategies", set(STRATEGIES) == expected,
      detail=str(set(STRATEGIES) - expected))


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main() -> int:
    # per-value
    test_redact_value_uses_class_tag()
    test_pseudonymize_deterministic()
    test_pseudonymize_cross_seed_unlinkable()
    test_pseudonymize_none_value()
    test_mask_partial_email()
    test_mask_partial_phone_last4()
    test_mask_partial_credit_card_last4()
    test_mask_partial_iban_keeps_first4_last4()
    test_mask_partial_us_ssn()
    test_hash_value_stable()
    test_hash_value_diff_for_diff_inputs()
    # snapshot-level
    test_apply_redact_default_strategy()
    test_apply_class_strategy_pseudonymize()
    test_apply_column_override_wins()
    test_apply_aggregate_only_keeps_stats()
    test_apply_mask_partial_per_class()
    test_apply_redacts_top_values()
    test_pseudonymize_without_seed_falls_back()
    test_columns_without_pii_class_untouched()
    # resolution
    test_strategy_for_default()
    test_strategy_for_class_overrides_default()
    test_strategy_for_column_overrides_all()
    # validation
    test_unknown_strategy_rejected()
    test_class_strategy_validated()
    # loader
    test_load_policy_json()
    test_load_policy_yaml_when_available()
    test_load_policy_unknown_strategy_rejected()
    test_load_policy_bad_apiversion_rejected()
    test_load_policy_no_file_returns_default()
    test_load_policy_from_env_var()
    # invariants
    test_strategies_curated()

    print(f"\n{'=' * 50}")
    print(f"PASS={PASS}  FAIL={FAIL}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
