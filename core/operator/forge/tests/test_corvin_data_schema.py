"""Phase 12.4 E2E — forge schema extension (x-data, x-snapshot).

Covers:
  * extract_data_fields walks input_schema and finds x-data fields
  * validate_data_field rejects:
      - unknown x-data value
      - x-data + x-redact (confused intent)
      - x-data + x-sensitive (confused intent)
      - x-data on non-string type
      - bad x-bind value
      - malformed x-snapshot block
  * snapshot options project from x-snapshot block (rows, strategy,
    include_*, caps, seed)
  * pii_overrides are extracted per-field
  * snapshot_options_from_field convenience function works
  * a schema with NO x-data field returns []  (legacy path stays clean)
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from forge.corvin_data import (  # noqa: E402
    DATA_KINDS,
    DataFieldSpec,
    SchemaExtensionError,
    extract_data_fields,
    snapshot_options_from_field,
    validate_data_field,
    validate_input_schema,
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
# extract_data_fields
# ---------------------------------------------------------------------------

def test_no_x_data_returns_empty():
    print("\n[extract: schema without x-data returns []]")
    schema = {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "amount": {"type": "number"},
        },
    }
    t("empty", extract_data_fields(schema) == [])


def test_single_x_data_field_extracted():
    print("\n[extract: single x-data field]")
    schema = {
        "type": "object",
        "properties": {
            "sales": {
                "type": "string",
                "x-bind": "ro",
                "x-data": "large_dataset",
                "x-snapshot": {"rows": 30, "rows_strategy": "head"},
            },
        },
    }
    fields = extract_data_fields(schema)
    t("one field", len(fields) == 1)
    f = fields[0]
    t("name", f.field_name == "sales")
    t("kind", f.data_kind == "large_dataset")
    t("bind", f.bind_mode == "ro")
    t("rows=30", f.snapshot_options.rows == 30)
    t("strategy=head", f.snapshot_options.rows_strategy == "head")


def test_multiple_x_data_fields():
    print("\n[extract: multiple x-data fields]")
    schema = {
        "properties": {
            "a": {"type": "string", "x-data": "large_dataset"},
            "regular": {"type": "string"},
            "b": {"type": "string", "x-data": "large_dataset"},
        }
    }
    fields = extract_data_fields(schema)
    t("two fields", len(fields) == 2)
    t("names", sorted(f.field_name for f in fields) == ["a", "b"])


def test_extract_handles_missing_properties_gracefully():
    print("\n[extract: schema without 'properties' returns []]")
    t("empty", extract_data_fields({"type": "object"}) == [])
    t("non-dict", extract_data_fields("not a schema") == [])  # type: ignore[arg-type]
    t("non-dict properties",
      extract_data_fields({"properties": "string"}) == [])  # type: ignore[dict-item]


# ---------------------------------------------------------------------------
# validate_data_field — rejection paths
# ---------------------------------------------------------------------------

def test_unknown_x_data_value_rejected():
    print("\n[validate: unknown x-data value rejected]")
    try:
        validate_data_field("f", {"type": "string", "x-data": "large_image"})
        t("rejected", False, detail="should have raised")
    except SchemaExtensionError as e:
        t("rejected", "large_image" in str(e) or "DATA_KINDS" in str(e) or "large_dataset" in str(e))


def test_x_data_plus_x_redact_rejected():
    print("\n[validate: x-data + x-redact:true rejected]")
    try:
        validate_data_field("f", {
            "type": "string",
            "x-data": "large_dataset",
            "x-redact": True,
        })
        t("rejected", False, detail="should have raised")
    except SchemaExtensionError as e:
        t("rejected", "x-redact" in str(e))


def test_x_data_plus_x_sensitive_rejected():
    print("\n[validate: x-data + x-sensitive:true rejected]")
    try:
        validate_data_field("f", {
            "type": "string",
            "x-data": "large_dataset",
            "x-sensitive": True,
        })
        t("rejected", False, detail="should have raised")
    except SchemaExtensionError as e:
        t("rejected", "x-sensitive" in str(e))


def test_x_data_on_non_string_rejected():
    print("\n[validate: x-data field must be type string]")
    try:
        validate_data_field("f", {
            "type": "number",
            "x-data": "large_dataset",
        })
        t("rejected", False, detail="should have raised")
    except SchemaExtensionError as e:
        t("rejected", "string" in str(e))


def test_bad_x_bind_rejected():
    print("\n[validate: x-bind must be 'ro' or 'rw']")
    try:
        validate_data_field("f", {
            "type": "string",
            "x-data": "large_dataset",
            "x-bind": "execute",
        })
        t("rejected", False, detail="should have raised")
    except SchemaExtensionError as e:
        t("rejected", "x-bind" in str(e))


def test_x_snapshot_not_object_rejected():
    print("\n[validate: x-snapshot must be an object]")
    try:
        validate_data_field("f", {
            "type": "string",
            "x-data": "large_dataset",
            "x-snapshot": "not an object",
        })
        t("rejected", False, detail="should have raised")
    except SchemaExtensionError as e:
        t("rejected", "object" in str(e) or "x-snapshot" in str(e))


def test_invalid_rows_strategy_rejected():
    print("\n[validate: rows_strategy must be in curated set]")
    try:
        validate_data_field("f", {
            "type": "string",
            "x-data": "large_dataset",
            "x-snapshot": {"rows_strategy": "magic"},
        })
        t("rejected", False, detail="should have raised")
    except SchemaExtensionError as e:
        t("rejected", "rows_strategy" in str(e))


def test_rows_negative_rejected():
    print("\n[validate: rows < 0 rejected]")
    try:
        validate_data_field("f", {
            "type": "string",
            "x-data": "large_dataset",
            "x-snapshot": {"rows": -1},
        })
        t("rejected", False, detail="should have raised")
    except SchemaExtensionError as e:
        t("rejected", "rows" in str(e))


def test_rows_too_high_rejected():
    print("\n[validate: rows > 1000 rejected]")
    try:
        validate_data_field("f", {
            "type": "string",
            "x-data": "large_dataset",
            "x-snapshot": {"rows": 50_000},
        })
        t("rejected", False, detail="should have raised")
    except SchemaExtensionError as e:
        t("rejected", "rows" in str(e))


# ---------------------------------------------------------------------------
# snapshot options projection
# ---------------------------------------------------------------------------

def test_snapshot_options_defaults():
    print("\n[snapshot: defaults from empty x-snapshot block]")
    spec = validate_data_field("f", {
        "type": "string",
        "x-data": "large_dataset",
    })
    t("rows default 20", spec.snapshot_options.rows == 20)
    t("strategy default", spec.snapshot_options.rows_strategy == "head+random+tail")
    t("quantiles on", spec.snapshot_options.include_quantiles is True)
    t("distinct on", spec.snapshot_options.include_distinct is True)
    t("top on", spec.snapshot_options.include_top is True)


def test_snapshot_options_overrides():
    print("\n[snapshot: x-snapshot block overrides defaults]")
    spec = validate_data_field("f", {
        "type": "string",
        "x-data": "large_dataset",
        "x-snapshot": {
            "rows": 100,
            "rows_strategy": "tail",
            "include_quantiles": False,
            "include_distinct": True,
            "include_top": False,
            "distinct_cap": 5000,
            "top_threshold": 25,
            "rowcount_jitter": 10,
            "seed": 42,
        },
    })
    t("rows=100", spec.snapshot_options.rows == 100)
    t("strategy=tail", spec.snapshot_options.rows_strategy == "tail")
    t("quantiles off", spec.snapshot_options.include_quantiles is False)
    t("distinct still on", spec.snapshot_options.include_distinct is True)
    t("top off", spec.snapshot_options.include_top is False)
    t("distinct_cap=5000", spec.snapshot_options.distinct_cap == 5000)
    t("seed=42", spec.snapshot_options.seed == 42)


def test_snapshot_options_from_field_convenience():
    print("\n[snapshot: snapshot_options_from_field works on bare dict]")
    opts = snapshot_options_from_field({
        "type": "string",
        "x-data": "large_dataset",
        "x-snapshot": {"rows": 5},
    })
    t("rows=5", opts.rows == 5)


def test_snapshot_options_from_field_no_x_snapshot():
    print("\n[snapshot: from_field without x-snapshot → defaults]")
    opts = snapshot_options_from_field({
        "type": "string",
        "x-data": "large_dataset",
    })
    t("defaults", opts.rows == 20)


# ---------------------------------------------------------------------------
# PII overrides
# ---------------------------------------------------------------------------

def test_pii_overrides_extracted():
    print("\n[pii_overrides: per-column overrides parsed]")
    spec = validate_data_field("sales", {
        "type": "string",
        "x-data": "large_dataset",
        "x-snapshot": {
            "pii_overrides": {
                "customer_email": "pseudonymize",
                "iban": "drop",
            },
        },
    })
    t("two overrides", len(spec.pii_overrides) == 2)
    t("email mapping", spec.pii_overrides["customer_email"] == "pseudonymize")
    t("iban mapping", spec.pii_overrides["iban"] == "drop")


def test_pii_overrides_must_be_strings():
    print("\n[pii_overrides: non-string value rejected]")
    try:
        validate_data_field("f", {
            "type": "string",
            "x-data": "large_dataset",
            "x-snapshot": {
                "pii_overrides": {"col": 123},
            },
        })
        t("rejected", False, detail="should have raised")
    except SchemaExtensionError as e:
        t("rejected", "pii_overrides" in str(e))


# ---------------------------------------------------------------------------
# validate_input_schema (top-level walk)
# ---------------------------------------------------------------------------

def test_validate_input_schema_walks_all_fields():
    print("\n[validate_input_schema: walks every x-data field]")
    schema = {
        "properties": {
            "a": {"type": "string", "x-data": "large_dataset"},
            "b": {"type": "string", "x-data": "large_dataset"},
            "c": {"type": "string"},  # ignored
        }
    }
    specs = validate_input_schema(schema)
    t("two valid specs", len(specs) == 2)


def test_validate_input_schema_first_failure_raises():
    print("\n[validate_input_schema: first bad field raises]")
    schema = {
        "properties": {
            "good": {"type": "string", "x-data": "large_dataset"},
            "bad": {"type": "number", "x-data": "large_dataset"},  # rejected
        }
    }
    try:
        validate_input_schema(schema)
        t("rejected", False, detail="should have raised")
    except SchemaExtensionError as e:
        t("rejected", "bad" in str(e))


# ---------------------------------------------------------------------------
# Invariants
# ---------------------------------------------------------------------------

def test_data_kinds_curated():
    print("\n[invariant: DATA_KINDS is curated]")
    t("currently only large_dataset", DATA_KINDS == ("large_dataset",),
      detail=str(DATA_KINDS))


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main() -> int:
    test_no_x_data_returns_empty()
    test_single_x_data_field_extracted()
    test_multiple_x_data_fields()
    test_extract_handles_missing_properties_gracefully()

    test_unknown_x_data_value_rejected()
    test_x_data_plus_x_redact_rejected()
    test_x_data_plus_x_sensitive_rejected()
    test_x_data_on_non_string_rejected()
    test_bad_x_bind_rejected()
    test_x_snapshot_not_object_rejected()
    test_invalid_rows_strategy_rejected()
    test_rows_negative_rejected()
    test_rows_too_high_rejected()

    test_snapshot_options_defaults()
    test_snapshot_options_overrides()
    test_snapshot_options_from_field_convenience()
    test_snapshot_options_from_field_no_x_snapshot()

    test_pii_overrides_extracted()
    test_pii_overrides_must_be_strings()

    test_validate_input_schema_walks_all_fields()
    test_validate_input_schema_first_failure_raises()

    test_data_kinds_curated()

    print(f"\n{'=' * 50}")
    print(f"PASS={PASS}  FAIL={FAIL}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
