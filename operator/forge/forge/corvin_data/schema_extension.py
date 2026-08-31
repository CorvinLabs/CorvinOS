"""Forge schema extension — ``x-data`` and ``x-snapshot`` annotations.

When a forged tool declares a field as a large-dataset input, the
agent should not pass raw file contents but a *handle* obtained via
``data_register``. The schema extension is purely DECLARATIVE: the
runner still ro-binds the path; this module gives the snapshot
pipeline + MCP layer the metadata to know which fields are
data-handles and what snapshot options to use.

Two new keys on schema field definitions:

```jsonc
{
  "input_schema": {
    "properties": {
      "sales_data": {
        "type": "string",
        "x-bind": "ro",
        "x-data": "large_dataset",
        "x-snapshot": {
          "rows": 20,
          "rows_strategy": "head+random+tail",
          "include_quantiles": true,
          "include_distinct": true,
          "include_top": true,
          "pii_overrides": {
            "customer_email": "pseudonymize"
          }
        }
      }
    }
  }
}
```

This module provides:
  * ``DataFieldSpec`` — projection of a single field's data-related
    annotations
  * ``extract_data_fields(schema)`` — find all ``x-data`` fields in a
    Forge tool's input_schema
  * ``validate_data_field(name, field_def)`` — strict per-field check
    (raises on confused intent, e.g. x-data + x-redact)
  * ``validate_input_schema(schema)`` — top-level walk over all fields
  * ``snapshot_options_from_field(field_def)`` — projection onto
    ``SnapshotOptions`` for the snapshot pipeline
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .snapshot import SnapshotOptions


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Currently the only x-data value we recognise. Future formats (e.g.
# `large_image`, `large_audio`) would add entries here; adding a new
# value REQUIRES a matching snapshot/redaction pipeline in
# corvin_data.
DATA_KINDS = ("large_dataset",)


class SchemaExtensionError(ValueError):
    """Raised on confused intent in x-data / x-snapshot annotations."""


# ---------------------------------------------------------------------------
# Per-field projection
# ---------------------------------------------------------------------------

@dataclass
class DataFieldSpec:
    """The data-related annotations of a single schema field, after
    validation."""

    field_name:      str
    data_kind:       str                    # currently always "large_dataset"
    bind_mode:       str | None              # "ro" / "rw" / None
    snapshot_options: SnapshotOptions
    pii_overrides:   dict[str, str] = field(default_factory=dict)
    # `pii_overrides` here are per-COLUMN of the dataset (operator-tagged
    # at schema-declaration time). They project into the redaction
    # policy's column_overrides at snapshot time.


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def extract_data_fields(input_schema: dict[str, Any]) -> list[DataFieldSpec]:
    """Walk *input_schema*, return one DataFieldSpec per x-data field.

    Returns an empty list when the schema declares no data fields —
    that's the legacy / non-big-data path and is the most common
    case.
    """
    if not isinstance(input_schema, dict):
        return []
    props = input_schema.get("properties", {})
    if not isinstance(props, dict):
        return []

    specs: list[DataFieldSpec] = []
    for fname, fdef in props.items():
        if not isinstance(fdef, dict):
            continue
        if "x-data" not in fdef:
            continue
        spec = validate_data_field(fname, fdef)
        specs.append(spec)
    return specs


def validate_data_field(name: str, field_def: dict[str, Any]) -> DataFieldSpec:
    """Strictly validate a single field's data-related annotations.

    Raises ``SchemaExtensionError`` on:
      * unknown x-data value
      * x-data combined with x-redact: true (confused intent — the
        snapshot pipeline IS the redaction layer; declaring x-redact
        on top doubles up incoherently)
      * x-data combined with x-sensitive: true (same reason)
      * non-string type (large_dataset fields must accept a path
        string or a data_handle string)
      * malformed x-snapshot block
    """
    if not isinstance(field_def, dict):
        raise SchemaExtensionError(f"field {name!r} definition is not an object")

    data_kind = field_def.get("x-data")
    if data_kind not in DATA_KINDS:
        raise SchemaExtensionError(
            f"field {name!r}: x-data must be one of {DATA_KINDS}; "
            f"got {data_kind!r}"
        )

    # Confused-intent gates
    if field_def.get("x-redact") is True:
        raise SchemaExtensionError(
            f"field {name!r}: x-data + x-redact:true is incoherent. "
            f"The data-handle pattern IS the redaction; choose one."
        )
    if field_def.get("x-sensitive") is True:
        raise SchemaExtensionError(
            f"field {name!r}: x-data + x-sensitive:true is incoherent. "
            f"Use x-data alone; the snapshot redacts."
        )

    # Type check — must accept a string (path OR data_handle).
    type_val = field_def.get("type")
    if type_val not in ("string", None):
        raise SchemaExtensionError(
            f"field {name!r}: x-data field must be type string "
            f"(path or data_handle); got {type_val!r}"
        )

    # Bind mode hint (optional, runner-level)
    bind_mode = field_def.get("x-bind")
    if bind_mode is not None and bind_mode not in ("ro", "rw"):
        raise SchemaExtensionError(
            f"field {name!r}: x-bind must be 'ro' or 'rw'; got {bind_mode!r}"
        )

    # x-snapshot projection
    raw_snap = field_def.get("x-snapshot", {})
    if not isinstance(raw_snap, dict):
        raise SchemaExtensionError(
            f"field {name!r}: x-snapshot must be an object; got {type(raw_snap).__name__}"
        )

    opts, pii_overrides = _parse_snapshot_block(name, raw_snap)

    return DataFieldSpec(
        field_name=name,
        data_kind=data_kind,
        bind_mode=bind_mode,
        snapshot_options=opts,
        pii_overrides=pii_overrides,
    )


def validate_input_schema(input_schema: dict[str, Any]) -> list[DataFieldSpec]:
    """Walk + validate every x-data field in the schema.

    Raises ``SchemaExtensionError`` on the first malformed field.
    Returns the list of valid DataFieldSpec entries (which the caller
    can ignore if it only wants the validation side effect).
    """
    return extract_data_fields(input_schema)


def snapshot_options_from_field(field_def: dict[str, Any]) -> SnapshotOptions:
    """Convenience: build SnapshotOptions from a field definition's
    ``x-snapshot`` block without going through DataFieldSpec.

    Used by the MCP ``data_snapshot`` tool when re-snapshotting an
    already-registered handle with new options.
    """
    raw_snap = field_def.get("x-snapshot", {}) if isinstance(field_def, dict) else {}
    opts, _ = _parse_snapshot_block("<inline>", raw_snap)
    return opts


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

def _parse_snapshot_block(
    field_name: str,
    raw: dict[str, Any],
) -> tuple[SnapshotOptions, dict[str, str]]:
    """Project an x-snapshot dict onto SnapshotOptions + per-column
    PII overrides."""
    if not isinstance(raw, dict):
        raise SchemaExtensionError(
            f"field {field_name!r}: x-snapshot must be an object"
        )

    # Per-column PII overrides — operator-tagged at schema time.
    pii_overrides_raw = raw.get("pii_overrides", {})
    if not isinstance(pii_overrides_raw, dict):
        raise SchemaExtensionError(
            f"field {field_name!r}: x-snapshot.pii_overrides must be an object"
        )
    pii_overrides: dict[str, str] = {}
    for k, v in pii_overrides_raw.items():
        if not isinstance(v, str):
            raise SchemaExtensionError(
                f"field {field_name!r}: x-snapshot.pii_overrides[{k!r}] "
                f"must be a string (strategy or pii_class); got {type(v).__name__}"
            )
        pii_overrides[k] = v

    # Type-safe extraction with defaults
    try:
        opts = SnapshotOptions(
            rows=int(raw.get("rows", 20)),
            rows_strategy=str(raw.get("rows_strategy", "head+random+tail")),
            include_quantiles=bool(raw.get("include_quantiles", True)),
            include_distinct=bool(raw.get("include_distinct", True)),
            include_top=bool(raw.get("include_top", True)),
            distinct_cap=int(raw.get("distinct_cap", 10_000)),
            top_threshold=int(raw.get("top_threshold", 50)),
            quantile_value_cap=int(raw.get("quantile_value_cap", 100_000)),
            type_inference_rows=int(raw.get("type_inference_rows", 1_000)),
            rowcount_jitter=int(raw.get("rowcount_jitter", 5)),
            rowcount_jitter_threshold=int(raw.get("rowcount_jitter_threshold", 100)),
            seed=raw.get("seed"),
        )
    except (TypeError, ValueError) as exc:
        raise SchemaExtensionError(
            f"field {field_name!r}: x-snapshot has invalid types: {exc}"
        ) from exc

    # Bound checks
    if opts.rows < 0 or opts.rows > 1000:
        raise SchemaExtensionError(
            f"field {field_name!r}: x-snapshot.rows must be in [0, 1000]; got {opts.rows}"
        )
    if opts.rows_strategy not in ("head", "tail", "random", "head+random+tail"):
        raise SchemaExtensionError(
            f"field {field_name!r}: x-snapshot.rows_strategy must be one of "
            f"['head', 'tail', 'random', 'head+random+tail']; got {opts.rows_strategy!r}"
        )

    return opts, pii_overrides
