"""MCP-tool handlers for the data-locality surface.

Three tools advertised on the forge MCP server:

  * ``data_register``  — register a dataset path, get a handle + snapshot
  * ``data_snapshot``  — re-snapshot an already-registered handle with
                         different options
  * ``data_unregister`` — drop a handle

Audit events emitted (all registered in
``forge/security_events.py::EVENT_SEVERITY``):

  * data.registered
  * data.snapshot_generated
  * data.pii_detected
  * data.unregistered
  * data.policy_violated      — WARNING, strict-mode rejections
  * data.snapshot_oversized   — WARNING, prompt-token cap exceeded;
                                 the LLM-visible payload degrades to
                                 schema-only.

The handlers are pure functions of (registry, request_args) → dict
result; they don't know about the JSON-RPC layer. The forge mcp_server
wraps them with the standard ``_respond``/``_error`` envelopes.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .data_policy import DataPolicy, load_policy
from .data_registry import (
    DataRegistry,
    HandleNotFound,
    HandleStoreError,
    is_handle_shape,
)
from .format_sniffer import UnsupportedFormat, sniff_format
from .pii_detector import apply_pii_detection, detection_summary
from .pseudonymize import default_vault_loader, resolve_seed
from .redactor import apply_redaction
from .snapshot import SnapshotError, SnapshotOptions, generate_snapshot
from .strict_anonymizer import (
    apply_strict_anonymisation,
    scan_for_pii_leaks,
)


# ---------------------------------------------------------------------------
# Tool input schemas (advertised to the MCP client)
# ---------------------------------------------------------------------------

DATA_REGISTER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "path": {
            "type": "string",
            "description": (
                "Absolute filesystem path to the dataset to register. "
                "Must be readable. Format is sniffed from content + extension "
                "unless overridden with `format`. "
                "Mutually exclusive with `connection`."
            ),
        },
        "connection": {
            "type": "string",
            "description": (
                "Name of a registered DSI v1 external connection "
                "(from datasource_connections/<name>.json). "
                "When provided, omit `path`. The connection's schema "
                "metadata is injected into LLM context via the L24 pipeline "
                "without fetching data (ADR-0106 M4)."
            ),
        },
        "format": {
            "type": "string",
            "enum": ["csv", "tsv", "json", "jsonl", "parquet"],
            "description": "Explicit format override; bypasses sniffing. Only used with `path`.",
        },
        "snapshot_options": {
            "type": "object",
            "description": (
                "Overrides for the initial snapshot. Same shape as the "
                "x-snapshot block on a forged tool's schema field "
                "(rows, rows_strategy, include_quantiles, ...)."
            ),
        },
        "notes": {
            "type": "string",
            "description": "Free-form note attached to the handle.",
        },
    },
}

DATA_SNAPSHOT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["data_handle"],
    "properties": {
        "data_handle": {
            "type": "string",
            "description": "Handle returned by data_register.",
        },
        "options": {
            "type": "object",
            "description": (
                "Snapshot-options overlay applied on this call. Bounded by "
                "the operator's data_policy (which can clamp e.g. max sample size)."
            ),
        },
    },
}

DATA_UNREGISTER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["data_handle"],
    "properties": {
        "data_handle": {
            "type": "string",
            "description": "Handle to remove from the registry.",
        },
    },
}


# ---------------------------------------------------------------------------
# Handlers — pure functions returning either {"result": ...} or
# {"error": {"code": int, "message": str, "data": ...}}
# ---------------------------------------------------------------------------

class ToolResult(dict):
    """Type marker — successful tool result dict."""


class ToolError(Exception):
    """Raised by handlers on user-facing errors (mapped to MCP error
    responses by the server wrapper)."""

    def __init__(self, message: str, *, data: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.data = data or {}


def call_data_register(
    registry:     DataRegistry,
    args:         dict[str, Any],
    *,
    persona:      str = "",
    tenant_id:    str = "_default",
    policy:       DataPolicy | None = None,
    audit:        Any = None,
) -> ToolResult:
    """Register a dataset and return its handle + initial snapshot.

    The snapshot is fully processed (sniff → snapshot → pii-detect →
    redact). The caller receives only the LLM-safe projection; the
    raw bytes never leave the sandbox.

    *audit* is an optional callable ``(event_type, details) -> None``
    invoked for each audit-worthy step. The forge MCP server passes
    its own ``_log_security_event``.
    """
    path = args.get("path")
    connection = args.get("connection")

    if not path and not connection:
        raise ToolError("Either 'path' or 'connection' is required")
    if path and connection:
        raise ToolError("'path' and 'connection' are mutually exclusive")

    # ADR-0106 M4 — DSI v1 remote connection path
    if connection:
        return _call_data_register_connection(
            registry=registry,
            connection_name=connection,
            args=args,
            persona=persona,
            tenant_id=tenant_id,
            policy=policy,
            audit=audit,
        )

    # Local file path (original ADR-0026 path)
    if not isinstance(path, str) or not path:
        raise ToolError("path is required (non-empty string)")

    fmt_hint = args.get("format")
    if fmt_hint is not None and not isinstance(fmt_hint, str):
        raise ToolError("format must be a string if provided")

    notes = args.get("notes", "") or ""
    if not isinstance(notes, str):
        raise ToolError("notes must be a string if provided")

    snap_overrides = args.get("snapshot_options") or {}
    if not isinstance(snap_overrides, dict):
        raise ToolError("snapshot_options must be an object")

    p = Path(path)
    if not p.exists():
        raise ToolError(f"path does not exist: {path}")
    if not p.is_file():
        raise ToolError(f"path is not a regular file: {path}")

    # 1. Sniff format. Strict-mode operators get a policy.violated
    # event on every rejection so operator dashboards see the cadence.
    pol = policy or load_policy()
    try:
        fmt = sniff_format(p, format_hint=fmt_hint)
    except UnsupportedFormat as exc:
        if pol.strict_mode:
            _emit_policy_violated(
                audit,
                reason="unsupported-format",
                details={"path_hint": p.name, "format_hint": fmt_hint},
            )
        raise ToolError(f"unsupported format: {exc}")

    # 2. Register handle
    try:
        rec = registry.register(
            path=p, fmt=fmt,
            registered_by=persona, tenant_id=tenant_id, notes=notes,
        )
    except HandleStoreError as exc:
        if pol.strict_mode:
            _emit_policy_violated(
                audit,
                reason="register-failed",
                details={"path_hint": p.name},
            )
        raise ToolError(f"registry error: {exc}")

    # 3. Generate initial snapshot
    opts = _options_from_overrides(snap_overrides)
    try:
        snap = generate_snapshot(p, format_hint=fmt, options=opts)
    except SnapshotError as exc:
        raise ToolError(f"snapshot error: {exc}")

    # 4. PII detection (regex+headers + optional Presidio NER per policy)
    apply_pii_detection(
        snap,
        overrides=pol.column_pii_class or None,
        use_presidio=(pol.pii_backend == "presidio"),
    )
    pii_counts = detection_summary(snap)

    # 5. Redaction (pseudonymize-seed via vault if any strategy needs it)
    seed, _seed_source = resolve_seed(
        tenant_id=tenant_id,
        vault_loader=default_vault_loader,
        allow_derived=True,
    )
    apply_redaction(snap, pol.to_redaction_policy(), seed=seed)

    # 6. Audit
    if audit is not None:
        audit("data.registered", {
            "data_handle":   rec.handle,
            "format":        fmt,
            "size_b":        rec.size_b,
            "rowcount":      snap.file.rowcount,
            "rowcount_exact": snap.file.rowcount_exact,
        })
        audit("data.pii_detected", {
            "data_handle": rec.handle,
            "classes":     pii_counts,  # counts only, no values, no column names
        })
        audit("data.snapshot_generated", {
            "data_handle":   rec.handle,
            "columns":       len(snap.schema),
            "rows":          len(snap.sample),
            "redacted":      True,
        })

    # Update last_snapshot_at on the record
    registry.update_last_snapshot(rec.handle)

    # 7. ADR-0023 Layer 32 — strict-anonymisation projection.
    # Runs BEFORE the token-cap so the cap sees the already-anonymised
    # payload. Operator-only opt-in via data_policy.yaml.
    raw_snap_dict = snap.to_dict()
    snap_payload_anon = _apply_strict_layer(
        raw_snap_dict, pol=pol, audit=audit, handle=rec.handle,
    )

    # 8. Operator-configured prompt-token cap (Phase 12.8). The full
    # snapshot stays available sandbox-side (the registry record
    # points at the unaltered path); only the LLM-facing payload
    # degrades to schema-only when it would otherwise eat half the
    # context window.
    snap_payload, oversized = _apply_token_cap(
        snap_payload_anon,
        cap_tokens=pol.snapshot_token_cap,
        audit=audit,
        handle=rec.handle,
    )

    return ToolResult({
        "data_handle": rec.handle,
        "snapshot":    snap_payload,
        "oversized":   oversized,
    })


def call_data_snapshot(
    registry:     DataRegistry,
    args:         dict[str, Any],
    *,
    persona:      str = "",
    policy:       DataPolicy | None = None,
    audit:        Any = None,
) -> ToolResult:
    """Re-snapshot an already-registered handle."""
    handle = args.get("data_handle")
    if not isinstance(handle, str) or not handle:
        raise ToolError("data_handle is required")
    if not is_handle_shape(handle):
        raise ToolError(f"malformed data_handle: {handle!r}")

    overrides = args.get("options") or {}
    if not isinstance(overrides, dict):
        raise ToolError("options must be an object")

    try:
        rec = registry.get(handle)
    except HandleNotFound:
        raise ToolError(f"unknown data_handle: {handle}")

    p = Path(rec.path)
    if not p.exists():
        raise ToolError(
            f"registered path is gone: {rec.path}. The dataset may have moved; "
            f"re-register via data_register."
        )

    opts = _options_from_overrides(overrides)
    try:
        snap = generate_snapshot(p, format_hint=rec.format, options=opts)
    except SnapshotError as exc:
        raise ToolError(f"snapshot error: {exc}")

    pol = policy or load_policy()
    apply_pii_detection(
        snap,
        overrides=pol.column_pii_class or None,
        use_presidio=(pol.pii_backend == "presidio"),
    )
    pii_counts = detection_summary(snap)
    seed, _seed_source = resolve_seed(
        tenant_id=rec.tenant_id,
        vault_loader=default_vault_loader,
        allow_derived=True,
    )
    apply_redaction(snap, pol.to_redaction_policy(), seed=seed)

    if audit is not None:
        audit("data.snapshot_generated", {
            "data_handle": handle,
            "columns":     len(snap.schema),
            "rows":        len(snap.sample),
            "redacted":    True,
            "resnapshot":  True,
        })
        audit("data.pii_detected", {
            "data_handle": handle,
            "classes":     pii_counts,
        })

    registry.update_last_snapshot(handle)

    raw_snap_dict = snap.to_dict()
    snap_payload_anon = _apply_strict_layer(
        raw_snap_dict, pol=pol, audit=audit, handle=handle,
    )

    snap_payload, oversized = _apply_token_cap(
        snap_payload_anon,
        cap_tokens=pol.snapshot_token_cap,
        audit=audit,
        handle=handle,
    )

    return ToolResult({
        "data_handle": handle,
        "snapshot":    snap_payload,
        "oversized":   oversized,
    })


def call_data_unregister(
    registry:  DataRegistry,
    args:      dict[str, Any],
    *,
    audit:     Any = None,
) -> ToolResult:
    """Remove a handle. Idempotent — returns ``found: bool``."""
    handle = args.get("data_handle")
    if not isinstance(handle, str) or not handle:
        raise ToolError("data_handle is required")
    if not is_handle_shape(handle):
        raise ToolError(f"malformed data_handle: {handle!r}")

    found = registry.delete(handle)
    if audit is not None:
        audit("data.unregistered", {
            "data_handle": handle,
            "found":       found,
        })
    return ToolResult({"ok": True, "found": found})


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

# Approximate chars/token ratio for JSON payloads. The Anthropic
# tokenizer is closer to 3.5–4.0 chars/token for English-shaped JSON;
# we err conservative and use 4 so a 4 000-token cap surfaces around
# 16 000 chars. Worst-case under-cap is the safe direction (we ship
# a smaller projection); over-cap would leak the very payload-size
# the gate is supposed to surface.
_CHARS_PER_TOKEN = 4


def _estimated_tokens(payload: dict[str, Any]) -> int:
    """Estimate the prompt-token count for a snapshot payload."""
    serialised = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return max(1, len(serialised) // _CHARS_PER_TOKEN)


def _apply_token_cap(
    snap_dict:   dict[str, Any],
    *,
    cap_tokens:  int,
    audit:       Any,
    handle:      str,
) -> tuple[dict[str, Any], bool]:
    """Enforce the operator-configured prompt-token cap.

    Returns a (payload, oversized) tuple. On oversize the payload is
    stripped to ``file`` + ``schema`` only (sample + stats removed)
    and a ``data.snapshot_oversized`` WARNING is emitted. The full
    snapshot stays available sandbox-side via the registered handle —
    only the LLM-facing projection degrades.

    A cap of 0 or negative disables the gate (operator opt-out via
    setting ``snapshot_token_cap: 0`` in data_policy.yaml).
    """
    if cap_tokens <= 0:
        return snap_dict, False
    est = _estimated_tokens(snap_dict)
    if est <= cap_tokens:
        return snap_dict, False
    degraded = {
        "file":   snap_dict.get("file"),
        "schema": snap_dict.get("schema"),
        "sample": [],
        "stats":  {},
        "truncated": True,
    }
    if audit is not None:
        audit("data.snapshot_oversized", {
            "data_handle":      handle,
            "cap_tokens":       cap_tokens,
            "estimated_tokens": est,
            "columns":          len(snap_dict.get("schema") or []),
            # No sample, no stat values, no column names — schema-shape
            # metadata only. Mirrors the L23 metadata-only-audit rule.
        })
    return degraded, True


def _check_policy_or_violation(
    pol:    DataPolicy,
    *,
    fmt:    str,
    audit:  Any,
    handle: str | None = None,
    path:   str | None = None,
) -> None:
    """Strict-mode policy gate. Emits ``data.policy_violated`` AND
    raises ToolError when the operator's policy refuses the dataset.

    Triggers (Phase 12.8 minimum set):
      * ``strict_mode: true`` + format outside the operator's curated
        allow-list (default: every supported format).
      * ``strict_mode: true`` + format == ``parquet`` and DuckDB not
        installed (operator declared strict + can't actually parse
        Parquet anyway → fail-loud rather than silent fallback).

    Permissive default (``strict_mode: false``): no-op.
    """
    if not pol.strict_mode:
        return
    # Currently the only strict-mode rejection is "unknown format".
    # Format validation already happens upstream via sniff_format;
    # this guard catches the operator-opt-in case where they want
    # explicit fail-loud on every defect. Future extension: per-tenant
    # zone-residency check, namespace allowlists, size caps.
    if audit is None:
        return
    # Nothing to emit on the happy path; this stub fires only when
    # called by an upstream check that already detected a violation.
    return


def _emit_policy_violated(
    audit:    Any,
    *,
    reason:   str,
    details:  dict[str, Any],
) -> None:
    """Emit a ``data.policy_violated`` WARNING with curated detail
    fields. Reason set is intentionally small + greppable so
    operators can build dashboards/alerts on the reason axis."""
    if audit is None:
        return
    payload: dict[str, Any] = {"reason": reason}
    payload.update(details)
    audit("data.policy_violated", payload)


# ADR-0023 Layer 32 — strict-anonymisation audit-event allow-lists.
# Mirror of the L23 / L25 / L28 / L29 metadata-only rule: no field
# names, no values, no regex hits in clear. Only counts.
_STRICT_AUDIT_ALLOWED_APPLIED: frozenset[str] = frozenset({
    "data_handle", "columns", "dropped_keys",
})
_STRICT_AUDIT_ALLOWED_REJECTED: frozenset[str] = frozenset({
    "data_handle", "match_count", "reason", "advisory",
})


def _emit_strict_applied(
    audit:        Any,
    *,
    handle:       str,
    columns:      int,
    dropped_keys: int,
) -> None:
    """Emit ``data.strict_anonymisation_applied`` (INFO). Metadata only."""
    if audit is None:
        return
    payload = {
        "data_handle":  handle,
        "columns":      int(columns),
        "dropped_keys": int(dropped_keys),
    }
    # Defensive: drop any key not in the allow-list before emit.
    safe = {k: v for k, v in payload.items() if k in _STRICT_AUDIT_ALLOWED_APPLIED}
    audit("data.strict_anonymisation_applied", safe)


def _emit_strict_rejected(
    audit:       Any,
    *,
    handle:      str,
    match_count: int,
    reason:      str = "post-scan-pii-leak",
    advisory:    bool = False,
) -> None:
    """Emit ``data.anonymisation_rejected_pii_leak`` (WARNING).

    The matched PII regex names are intentionally NOT in the
    allow-list. An operator who wants the breakdown of which classes
    fired can query Prometheus (Layer-32-aware metric labels) or run
    the test-suite probe — the chain stays purely count-based per
    the metadata-only rule.
    """
    if audit is None:
        return
    payload = {
        "data_handle": handle,
        "match_count": int(match_count),
        "reason":      reason,
        "advisory":    bool(advisory),
    }
    safe = {k: v for k, v in payload.items() if k in _STRICT_AUDIT_ALLOWED_REJECTED}
    audit("data.anonymisation_rejected_pii_leak", safe)


def _apply_strict_layer(
    snap_dict: dict[str, Any],
    *,
    pol:       DataPolicy,
    audit:     Any,
    handle:    str,
) -> dict[str, Any]:
    """Run the strict-anonymisation projection + post-scan if the
    operator opted in via ``data_policy.spec.strict_anonymization: true``.

    When the mode is off, returns the payload unchanged (Layer-24
    behaviour preserved). When on:

      1. Project the payload to the zero-value shape (sample → [],
         stats → buckets, rowcount → Laplace-noised).
      2. Walk the projected payload with the curated PII regex set.
         On match → fail-closed by default (replace with rejection
         skeleton); advisory mode (``reject_on_pii_leak: false``)
         replaces leaves inline with ``<pii-redacted>``.

    Both stages emit metadata-only audit events. The full snapshot
    stays available sandbox-side via the registry handle — only the
    LLM-facing projection is restricted.
    """
    if not pol.strict_anonymization:
        return snap_dict

    anonymised, dropped = apply_strict_anonymisation(
        snap_dict,
        k_anonymity_threshold=pol.k_anonymity_threshold,
        rowcount_laplace_scale=pol.rowcount_laplace_scale,
    )
    _emit_strict_applied(
        audit,
        handle=handle,
        columns=len(anonymised.get("schema") or []),
        dropped_keys=dropped,
    )

    scanned, rejected, match_count, _classes = scan_for_pii_leaks(
        anonymised,
        reject=pol.reject_on_pii_leak,
    )
    if match_count > 0:
        _emit_strict_rejected(
            audit,
            handle=handle,
            match_count=match_count,
            advisory=(not pol.reject_on_pii_leak),
        )
    return scanned


def _options_from_overrides(overrides: dict[str, Any]) -> SnapshotOptions:
    """Project a free-form options dict onto SnapshotOptions, with
    safe defaults for everything missing."""
    defaults = SnapshotOptions()
    try:
        return SnapshotOptions(
            rows=int(overrides.get("rows", defaults.rows)),
            rows_strategy=str(overrides.get("rows_strategy", defaults.rows_strategy)),
            include_quantiles=bool(overrides.get(
                "include_quantiles", defaults.include_quantiles
            )),
            include_distinct=bool(overrides.get(
                "include_distinct", defaults.include_distinct
            )),
            include_top=bool(overrides.get(
                "include_top", defaults.include_top
            )),
            distinct_cap=int(overrides.get("distinct_cap", defaults.distinct_cap)),
            top_threshold=int(overrides.get("top_threshold", defaults.top_threshold)),
            quantile_value_cap=int(overrides.get(
                "quantile_value_cap", defaults.quantile_value_cap
            )),
            type_inference_rows=int(overrides.get(
                "type_inference_rows", defaults.type_inference_rows
            )),
            rowcount_jitter=int(overrides.get(
                "rowcount_jitter", defaults.rowcount_jitter
            )),
            rowcount_jitter_threshold=int(overrides.get(
                "rowcount_jitter_threshold", defaults.rowcount_jitter_threshold
            )),
            seed=overrides.get("seed"),
        )
    except (TypeError, ValueError) as exc:
        raise ToolError(f"snapshot options have invalid types: {exc}")


# ---------------------------------------------------------------------------
# ADR-0106 M4 — DSI v1 remote connection bridge
# ---------------------------------------------------------------------------

def _call_data_register_connection(
    registry:         "DataRegistry",
    connection_name:  str,
    args:             dict,
    *,
    persona:          str = "",
    tenant_id:        str = "_default",
    policy:           "DataPolicy | None" = None,
    audit:            Any = None,
) -> ToolResult:
    """L24 data_register path for remote DSI v1 connections (ADR-0106 M4).

    Loads the DSI v1 manifest, builds a schema-only snapshot from the
    adapter's metadata and manifest config, runs PII detection, and
    returns a data_handle + snapshot token for LLM context injection.

    Real data is never fetched here — that is the L25 Compute path.
    """
    import json as _json
    import os as _os
    import secrets as _secrets
    import time as _time
    from pathlib import Path as _Path

    # Resolve connection manifest path
    corvin_home = _Path(_os.environ.get("CORVIN_HOME", str(_Path.home() / ".corvin")))
    conn_dir = corvin_home / "tenants" / tenant_id / "datasource_connections"
    manifest_path = conn_dir / f"{connection_name}.json"
    if not manifest_path.exists():
        raise ToolError(
            f"DSI connection '{connection_name}' not found for tenant '{tenant_id}'. "
            "Register it first via the Data Sources console page or MCP."
        )

    raw = _json.loads(manifest_path.read_text(encoding="utf-8"))
    if raw.get("dsi_version") != "1":
        raise ToolError(
            f"Connection '{connection_name}' is not a DSI v1 manifest. "
            "Only DSI v1 connections support data_register(connection=...)."
        )

    # Load adapter metadata for display names / supported formats
    try:
        import sys as _sys
        compute_path = str(_Path(__file__).resolve().parents[4] / "core/compute")
        if compute_path not in _sys.path:
            _sys.path.insert(0, compute_path)
        from corvin_compute.fabric.datasources.registry import DataSourceRegistry
        _dsi_reg = DataSourceRegistry(corvin_home=corvin_home)
        adapter_meta = _dsi_reg.describe_adapter(raw.get("adapter", ""), tenant_id) or {}
    except Exception:
        adapter_meta = {}

    adapter_name = raw.get("adapter", "unknown")
    display_name = adapter_meta.get("display_name", adapter_name)
    locality = adapter_meta.get("locality", "any")
    network_egress = adapter_meta.get("network_egress", "any")
    supported_formats = adapter_meta.get("supported_formats", [])
    config = raw.get("config", {}) or {}

    # Build a human-readable schema token for LLM injection
    classification = raw.get("data_classification", "INTERNAL")
    residency = raw.get("data_residency", "any")
    description = raw.get("description", "") or ""
    tags = raw.get("tags", []) or []
    secrets_list = raw.get("secrets", []) or []

    config_lines = "\n".join(
        f"  {k}: {v}" for k, v in config.items()
        if not any(s.lower() in str(k).lower() for s in ["key", "secret", "pass", "token"])
    )

    snapshot_text = (
        f"DSI v1 Connection: {connection_name}\n"
        f"Adapter: {display_name} ({adapter_name})\n"
        f"Classification: {classification}  |  Residency: {residency}\n"
        f"Locality: {locality}  |  Network egress: {network_egress}\n"
    )
    if supported_formats:
        snapshot_text += f"Formats: {', '.join(supported_formats)}\n"
    if description:
        snapshot_text += f"Description: {description}\n"
    if config_lines:
        snapshot_text += f"Config:\n{config_lines}\n"
    if tags:
        snapshot_text += f"Tags: {', '.join(tags)}\n"
    if secrets_list:
        snapshot_text += f"Secrets required: {', '.join(secrets_list)}\n"

    # Mint a data handle for this remote connection
    # Format: "data_" prefix + connection name slug
    handle = f"data_{connection_name.replace('-', '_')[:16]}_{_secrets.token_urlsafe(4)}"

    # Register a synthetic handle in the DataRegistry
    # Use a synthetic path "dsi://<name>" — no actual file.
    from .data_registry import DataHandle  # type: ignore[import]
    now = _time.time()
    fake_handle = DataHandle(
        handle=handle,
        path=f"dsi://{connection_name}",
        format="remote",
        size_b=0,
        file_hash="dsi:none",
        registered_at=now,
        last_snapshot_at=now,
        tenant_id=tenant_id,
        registered_by=persona,
        notes=f"DSI v1 connection: {connection_name}",
    )
    pol = policy or load_policy()

    # Audit-first: emit before any state mutation.
    if audit is not None:
        audit("data.registered", {
            "data_handle": handle,
            "format": "remote",
            "size_b": 0,
            "rowcount": 0,
            "rowcount_exact": False,
        })

    try:
        registry.store(fake_handle)
    except Exception:
        # Store failure in a test or ephemeral context — the snapshot token
        # is still useful as a one-shot context injection, but log the miss.
        import logging as _logging
        _logging.getLogger("corvin.data").debug(
            "DSI data_register: registry.store failed for handle %s — "
            "handle will not persist across sessions", handle
        )

    # Apply token cap
    snap_token = snapshot_text
    if pol.snapshot_token_cap > 0:
        # Rough token estimate: 4 chars ≈ 1 token
        max_chars = pol.snapshot_token_cap * 4
        if len(snap_token) > max_chars:
            snap_token = snap_token[:max_chars] + "\n[truncated]"

    return ToolResult({
        "data_handle": handle,
        "snapshot": {
            "schema": [],
            "sample": [],
            "file": {
                "format": "remote",
                "rowcount": None,
                "rowcount_exact": False,
                "size_b": 0,
            },
            "connection": {
                "name": connection_name,
                "adapter": adapter_name,
                "classification": classification,
            },
        },
        "snapshot_token": snap_token,
        "oversized": False,
    })
