"""Engine-Trust-Härtung — ADR-0020 Layer 30 Phase 30.1.

Loader + Schema-Validation + Verdict-API für die per-Engine Trust-
Manifeste. Sub-Phasen 30.2 (Canary-Loop) und 30.3 (Output-Sentinel)
bauen darauf auf — dieses Modul liefert nur die Datenschicht und die
Tier-Gate-Logik.

Module-Surface
==============

* :func:`load_manifest(engine_id)` — bundle-default-Pfad,
  optional pro-Operator-Override unter ``<corvin_home>/global/
  engine_trust/<engine_id>.yaml``. Returns a frozen dataclass.
* :func:`evaluate_trust(engine_id, *, min_tier, current_binary_path,
  now)` — pure-Python Verdict-API, no side effects, no audit
  emission. Returns a :class:`TrustVerdict` that the dispatcher
  (Phase 30.1b) consumes to decide spawn vs. fail.
* :func:`emit_violation_event(verdict, *, audit_path)` —
  best-effort audit-event-emitter. Caller decides ob ein Verdict
  einen Audit-Event verdient (typischerweise: jeder
  ``passed=False``-Verdict).

Cost contract
=============

Pure file-IO + dictionary lookups + sha256. **NO LLM calls.** The
Phase 30.2/30.3 modules will spawn `claude -p` subprocesses; this
module never does.

Schema validation is hand-written (no pydantic dep) — the bridge
runtime is decoupled from the gateway venv, mirror of the
sister-module pattern (consent.py / roles.py / quota.py).
"""
from __future__ import annotations

import hashlib
import os
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml


# ---------------------------------------------------------------------------
# Constants + paths
# ---------------------------------------------------------------------------


_THIS = Path(__file__).resolve()
_BUNDLE_TRUST_DIR = _THIS.parent / "agents" / "trust"

# Tier-ordering: high > medium > low. Operators set min_tier; an
# engine whose effective tier is below it fails.
_TIER_ORDER: dict[str, int] = {"low": 0, "medium": 1, "high": 2}
_VALID_TIERS = frozenset(_TIER_ORDER.keys())

_ENGINE_ID_RE = re.compile(r"^[a-z0-9_][a-z0-9_-]{0,62}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class EngineTrustError(Exception):
    """Caller-side error: bad input."""


class EngineTrustManifestMissing(EngineTrustError):
    """No bundle manifest AND no operator override for the engine_id."""


class EngineTrustManifestMalformed(EngineTrustError):
    """Manifest exists but fails schema validation."""


class EngineTrustAuditFieldNotAllowed(Exception):
    """A caller tried to put a forbidden / off-allowlist field into the chain."""


# ---------------------------------------------------------------------------
# Manifest dataclass + validators
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Metadata:
    engine_id:         str
    trust_tier:        str   # "low" | "medium" | "high"
    evaluated_at:      str
    evaluated_against: str
    valid_until:       str


@dataclass(frozen=True)
class _Spec:
    binary_sha256:           str | None
    jailbreak_resistance:    float
    system_prompt_respect:   float
    tool_call_fidelity:      float
    tested_refusal_classes:  tuple[str, ...]
    notes:                   str


@dataclass(frozen=True)
class EngineTrustManifest:
    apiVersion: str
    kind:       str
    metadata:   _Metadata
    spec:       _Spec


_ALLOWED_TOPLEVEL = frozenset({"apiVersion", "kind", "metadata", "spec"})
_ALLOWED_METADATA = frozenset({
    "engine_id", "trust_tier", "evaluated_at",
    "evaluated_against", "valid_until",
})
_REQUIRED_METADATA = frozenset({
    "engine_id", "trust_tier", "evaluated_at",
    "evaluated_against", "valid_until",
})
_ALLOWED_SPEC = frozenset({
    "binary_sha256", "jailbreak_resistance", "system_prompt_respect",
    "tool_call_fidelity", "tested_refusal_classes", "notes",
})


def _require_keys_subset(actual: dict, allowed: frozenset, where: str) -> None:
    extra = set(actual.keys()) - allowed
    if extra:
        raise EngineTrustManifestMalformed(
            f"{where}: unknown keys {sorted(extra)}; allowed {sorted(allowed)}"
        )


def _require_required(actual: dict, required: frozenset, where: str) -> None:
    missing = required - set(actual.keys())
    if missing:
        raise EngineTrustManifestMalformed(
            f"{where}: missing required keys {sorted(missing)}"
        )


def _validate_float(v: Any, field: str, lo: float = 0.0, hi: float = 1.0) -> float:
    if not isinstance(v, (int, float)) or isinstance(v, bool):
        raise EngineTrustManifestMalformed(
            f"{field}: must be number, got {type(v).__name__}"
        )
    fv = float(v)
    if not (lo <= fv <= hi):
        raise EngineTrustManifestMalformed(
            f"{field}: must be in [{lo}, {hi}], got {fv}"
        )
    return fv


def _validate_binary_sha(v: Any) -> str | None:
    if v is None:
        return None
    if not isinstance(v, str):
        raise EngineTrustManifestMalformed(
            f"binary_sha256: must be str|null, got {type(v).__name__}"
        )
    s = v.strip().lower()
    if not _SHA256_RE.match(s):
        raise EngineTrustManifestMalformed(
            f"binary_sha256: must be 64 lower-hex chars, got {v!r}"
        )
    return s


def _validate_string_list(v: Any, field: str, *, max_items: int = 64) -> tuple[str, ...]:
    if not isinstance(v, list):
        raise EngineTrustManifestMalformed(
            f"{field}: must be list, got {type(v).__name__}"
        )
    if len(v) > max_items:
        raise EngineTrustManifestMalformed(
            f"{field}: too many items ({len(v)} > {max_items})"
        )
    out: list[str] = []
    for entry in v:
        if not isinstance(entry, str) or not entry:
            raise EngineTrustManifestMalformed(
                f"{field}: entries must be non-empty strings, got {entry!r}"
            )
        out.append(entry)
    return tuple(out)


def _parse_manifest(raw: Any, source_path: Path) -> EngineTrustManifest:
    if not isinstance(raw, dict):
        raise EngineTrustManifestMalformed(
            f"{source_path}: top-level must be mapping, got {type(raw).__name__}"
        )
    _require_keys_subset(raw, _ALLOWED_TOPLEVEL, str(source_path))

    if raw.get("apiVersion") != "corvin/v1":
        raise EngineTrustManifestMalformed(
            f"{source_path}: apiVersion must be 'corvin/v1'"
        )
    if raw.get("kind") != "EngineTrust":
        raise EngineTrustManifestMalformed(
            f"{source_path}: kind must be 'EngineTrust'"
        )

    md = raw.get("metadata")
    if not isinstance(md, dict):
        raise EngineTrustManifestMalformed(
            f"{source_path}: metadata must be mapping"
        )
    _require_keys_subset(md, _ALLOWED_METADATA, f"{source_path}: metadata")
    _require_required(md, _REQUIRED_METADATA, f"{source_path}: metadata")

    if not isinstance(md["engine_id"], str) or not md["engine_id"]:
        raise EngineTrustManifestMalformed(
            f"{source_path}: metadata.engine_id must be non-empty string"
        )

    tier = md["trust_tier"]
    if not isinstance(tier, str) or tier not in _VALID_TIERS:
        raise EngineTrustManifestMalformed(
            f"{source_path}: metadata.trust_tier must be one of "
            f"{sorted(_VALID_TIERS)}, got {tier!r}"
        )
    for ts_field in ("evaluated_at", "valid_until"):
        ts_val = md[ts_field]
        if not isinstance(ts_val, str) or not ts_val:
            raise EngineTrustManifestMalformed(
                f"{source_path}: metadata.{ts_field} must be non-empty ISO-8601 string"
            )
        # parse (defensive — same parser used in evaluate_trust)
        _parse_iso8601(ts_val, where=f"{source_path}: metadata.{ts_field}")

    if not isinstance(md["evaluated_against"], str):
        raise EngineTrustManifestMalformed(
            f"{source_path}: metadata.evaluated_against must be string"
        )

    metadata_obj = _Metadata(
        engine_id=md["engine_id"],
        trust_tier=tier,
        evaluated_at=md["evaluated_at"],
        evaluated_against=md["evaluated_against"],
        valid_until=md["valid_until"],
    )

    sp = raw.get("spec") or {}
    if not isinstance(sp, dict):
        raise EngineTrustManifestMalformed(
            f"{source_path}: spec must be mapping when present"
        )
    _require_keys_subset(sp, _ALLOWED_SPEC, f"{source_path}: spec")

    spec_obj = _Spec(
        binary_sha256=_validate_binary_sha(sp.get("binary_sha256")),
        jailbreak_resistance=_validate_float(
            sp.get("jailbreak_resistance", 0.0), "spec.jailbreak_resistance"),
        system_prompt_respect=_validate_float(
            sp.get("system_prompt_respect", 0.0), "spec.system_prompt_respect"),
        tool_call_fidelity=_validate_float(
            sp.get("tool_call_fidelity", 0.0), "spec.tool_call_fidelity"),
        tested_refusal_classes=_validate_string_list(
            sp.get("tested_refusal_classes", []), "spec.tested_refusal_classes"),
        notes=str(sp.get("notes", "")),
    )

    return EngineTrustManifest(
        apiVersion=raw["apiVersion"],
        kind=raw["kind"],
        metadata=metadata_obj,
        spec=spec_obj,
    )


# ---------------------------------------------------------------------------
# Verdict
# ---------------------------------------------------------------------------


@dataclass
class TrustVerdict:
    """Result of :func:`evaluate_trust`. Pure-data; caller acts on it."""
    engine_id:           str
    passed:              bool
    reason:              str = ""        # short tag for audit + diagnostic
    effective_tier:      str = "low"     # what the gate used (after expiry-downgrade)
    declared_tier:       str = "low"     # raw from manifest
    expired:             bool = False
    expired_at:          str = ""
    evaluated_at:        str = ""
    binary_check:        str = "skipped" # "skipped" | "matched" | "mismatch" | "binary-missing"
    expected_sha256:     str | None = None
    observed_sha256:     str | None = None
    detail:              dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Path + env helpers
# ---------------------------------------------------------------------------


def _corvin_home() -> Path:
    """Resolve CORVIN_HOME (with legacy CORVIN_HOME fallback)."""
    for var in ("CORVIN_HOME", "CORVIN_HOME"):
        v = os.environ.get(var)
        if v:
            return Path(v).expanduser()
    return Path.home() / ".corvin"


def _operator_override_path(engine_id: str) -> Path:
    return _corvin_home() / "global" / "engine_trust" / f"{engine_id}.yaml"


def _tenant_config_path(tenant_id: str) -> Path:
    """Lightweight lookup of <tenant_home>/global/tenant.corvin.yaml.

    Mirror of corvin_gateway/tenant_config.py path layout, but without
    pulling in pydantic (the bridge runtime stays decoupled from the
    gateway venv — same rule as L23 STT and L24 PII). We read the YAML
    plain and look up only the one field we care about.
    """
    return _corvin_home() / "tenants" / tenant_id / "global" / "tenant.corvin.yaml"


def _load_engine_trust_block(tenant_id: str) -> dict[str, Any]:
    """Lightweight read of ``spec.engine_trust`` from the tenant config.

    Returns ``{}`` (empty dict) on every error path. Mirror of the
    L23 / L24 lightweight-loader pattern: keep the bridge runtime
    decoupled from the gateway's pydantic dep.
    """
    p = _tenant_config_path(tenant_id)
    if not p.exists():
        return {}
    try:
        with p.open("r", encoding="utf-8") as fh:
            raw = yaml.safe_load(fh)
    except (yaml.YAMLError, OSError):
        return {}
    if not isinstance(raw, dict):
        return {}
    spec = raw.get("spec") or {}
    if not isinstance(spec, dict):
        return {}
    et = spec.get("engine_trust") or {}
    if not isinstance(et, dict):
        return {}
    return et


def load_min_tier_for_tenant(tenant_id: str = "_default") -> str:
    """Resolve ``spec.engine_trust.min_tier`` for a tenant.

    Returns the configured minimum tier or ``"low"`` (permissive
    default) when:
      * the tenant config file is absent
      * the file is malformed
      * the engine_trust block is absent or null
      * the field is unparseable

    Fail-open is the right default here: a misconfigured tenant
    should not lose every engine spawn. Operators see misconfig via
    the existing validate-config path; this loader is the read-only
    consumer.
    """
    et = _load_engine_trust_block(tenant_id)
    tier = et.get("min_tier")
    if isinstance(tier, str) and tier in _VALID_TIERS:
        return tier
    return "low"


def load_drift_policy_for_tenant(tenant_id: str = "_default") -> dict[str, Any]:
    """Resolve drift-related policy fields for a tenant.

    Returns a dict with keys:
      * ``auto_block_on_drift`` (bool, default False)
      * ``canary_alert_delta`` (float in [0.0, 1.0], default 0.10)
      * ``canary_min_window_days`` (int in [1, 90], default 7)

    All three default to the permissive baseline — a tenant without
    explicit `engine_trust` config gets `auto_block_on_drift=False`,
    so the spawn-time drift gate is a true opt-in. The daily canary
    timer still runs and emits its own drift events regardless of
    this flag.
    """
    et = _load_engine_trust_block(tenant_id)
    auto_block = bool(et.get("auto_block_on_drift", False))
    raw_delta = et.get("canary_alert_delta", 0.10)
    if isinstance(raw_delta, (int, float)) and 0.0 <= float(raw_delta) <= 1.0:
        alert_delta = float(raw_delta)
    else:
        alert_delta = 0.10
    raw_window = et.get("canary_min_window_days", 7)
    if isinstance(raw_window, int) and 1 <= raw_window <= 90:
        min_window_days = raw_window
    else:
        min_window_days = 7
    return {
        "auto_block_on_drift": auto_block,
        "canary_alert_delta":  alert_delta,
        "canary_min_window_days": min_window_days,
    }


@dataclass
class DriftSpawnVerdict:
    """Adapter-facing summary of the spawn-time drift gate.

    ``passed=True`` means the spawn proceeds (no drift OR drift not
    enforced). ``passed=False`` means the gate blocked because
    ``auto_block_on_drift`` is on AND at least one class drifted.
    """
    engine_id:        str
    passed:           bool
    enforced:         bool         # auto_block_on_drift effective for this tenant
    drifted_classes:  tuple[str, ...] = ()
    detail:           dict[str, Any] = field(default_factory=dict)


def evaluate_drift_for_spawn(
    engine_id: str,
    *,
    tenant_id: str = "_default",
) -> DriftSpawnVerdict:
    """Spawn-time drift check (Phase 30.2f).

    Always reads scores + computes drift; only ENFORCES (blocks) when
    the tenant has ``auto_block_on_drift: true`` in ``tenant.corvin
    .yaml::spec.engine_trust``. Either way the drift verdicts are
    written into the audit chain on every drifted class so an
    operator gets the spawn-time signal alongside the daily canary
    signal.

    The function fail-OPENs on every operational issue (missing
    score file, missing engine_canary module, schema drift). The
    drift-gate is best-effort defence; a broken score-loader must
    not silently brick the bridge.
    """
    policy = load_drift_policy_for_tenant(tenant_id)
    enforced = bool(policy["auto_block_on_drift"])

    # Lazy import — engine_canary is a sibling script under
    # operator/voice/scripts/, NOT a top-level adapter import.
    try:
        scripts_dir = _THIS.parent.parent / "voice" / "scripts"
        if str(scripts_dir) not in sys.path:
            sys.path.insert(0, str(scripts_dir))
        import engine_canary as _ec  # type: ignore
    except Exception as e:  # noqa: BLE001
        return DriftSpawnVerdict(
            engine_id=engine_id, passed=True, enforced=enforced,
            detail={"reason": f"engine_canary unavailable: {e!r}"},
        )

    try:
        scores = _ec.load_canary_scores(engine_id)
    except Exception as e:  # noqa: BLE001
        return DriftSpawnVerdict(
            engine_id=engine_id, passed=True, enforced=enforced,
            detail={"reason": f"score load failed: {e!r}"},
        )

    drifted_classes: list[str] = []
    for klass, records in scores.items():
        try:
            verdict = _ec.detect_drift(
                records,
                engine_id=engine_id, klass=klass,
                alert_delta=policy["canary_alert_delta"],
                min_window_days=policy["canary_min_window_days"],
            )
        except Exception:  # noqa: BLE001
            continue
        if verdict.drifted:
            drifted_classes.append(klass)
            try:
                _ec.emit_drift_event(verdict)
            except Exception:  # noqa: BLE001
                pass

    if not drifted_classes:
        return DriftSpawnVerdict(
            engine_id=engine_id, passed=True, enforced=enforced,
        )

    # Drifted; block iff enforcement is on.
    if enforced:
        return DriftSpawnVerdict(
            engine_id=engine_id, passed=False, enforced=True,
            drifted_classes=tuple(drifted_classes),
            detail={"reason": "auto-block-on-drift",
                    "alert_delta": policy["canary_alert_delta"]},
        )
    # Drift detected but not enforced — let spawn proceed; the audit
    # event already landed for operator forensics.
    return DriftSpawnVerdict(
        engine_id=engine_id, passed=True, enforced=False,
        drifted_classes=tuple(drifted_classes),
        detail={"reason": "drift-detected-but-not-enforced"},
    )


def _bundle_path(engine_id: str) -> Path:
    return _BUNDLE_TRUST_DIR / f"{engine_id}.yaml"


def _validate_engine_id(engine_id: str) -> None:
    if not isinstance(engine_id, str) or not engine_id:
        raise EngineTrustError(f"engine_id must be non-empty string, got {engine_id!r}")
    if not _ENGINE_ID_RE.match(engine_id):
        raise EngineTrustError(
            f"engine_id {engine_id!r} fails charset; "
            f"must match {_ENGINE_ID_RE.pattern}"
        )


def _parse_iso8601(s: str, *, where: str = "") -> datetime:
    """Parse an ISO-8601 timestamp; tolerate both Z-suffix and explicit offsets."""
    if not isinstance(s, str) or not s:
        raise EngineTrustManifestMalformed(
            f"{where or 'timestamp'}: must be ISO-8601 str, got {s!r}"
        )
    norm = s.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(norm)
    except ValueError as e:
        raise EngineTrustManifestMalformed(
            f"{where or 'timestamp'}: unparseable ISO-8601 {s!r}: {e}"
        ) from e
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------


def load_manifest(engine_id: str) -> EngineTrustManifest:
    """Load the trust manifest for an engine.

    Resolution order:
      1. Operator override at ``<corvin_home>/global/engine_trust/<engine_id>.yaml``
      2. Bundle default at ``agents/trust/<engine_id>.yaml``
    """
    _validate_engine_id(engine_id)
    candidates = [_operator_override_path(engine_id), _bundle_path(engine_id)]
    chosen: Path | None = None
    for p in candidates:
        if p.exists():
            chosen = p
            break
    if chosen is None:
        raise EngineTrustManifestMissing(
            f"no trust manifest for engine_id {engine_id!r}; "
            f"expected one of {[str(p) for p in candidates]}"
        )
    try:
        with chosen.open("r", encoding="utf-8") as fh:
            raw = yaml.safe_load(fh)
    except (yaml.YAMLError, OSError) as e:
        raise EngineTrustManifestMalformed(f"reading {chosen}: {e}") from e
    manifest = _parse_manifest(raw, chosen)
    if manifest.metadata.engine_id != engine_id:
        raise EngineTrustManifestMalformed(
            f"{chosen}: metadata.engine_id={manifest.metadata.engine_id!r} "
            f"does not match requested {engine_id!r}"
        )
    return manifest


# ---------------------------------------------------------------------------
# Hash helper
# ---------------------------------------------------------------------------


def compute_binary_sha256(path: str | Path, *, chunk: int = 65536) -> str:
    """Stream-hash a file. Returns 64-char lower-hex sha256."""
    p = Path(path)
    h = hashlib.sha256()
    with p.open("rb") as fh:
        while True:
            buf = fh.read(chunk)
            if not buf:
                break
            h.update(buf)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Verdict API
# ---------------------------------------------------------------------------


def evaluate_trust(
    engine_id: str,
    *,
    min_tier: str = "low",
    current_binary_path: str | Path | None = None,
    now: float | None = None,
) -> TrustVerdict:
    """Decide whether an engine may spawn under the operator's policy.

    Returns a :class:`TrustVerdict` with ``passed=True`` iff:
      * Manifest exists AND validates
      * Effective tier (after expiry-downgrade) ≥ ``min_tier``
      * If ``current_binary_path`` and ``binary_sha256`` are both set,
        the hashes match.

    On manifest-missing / malformed, ``passed=False`` and ``reason``
    carries the diagnostic. The verdict NEVER raises on engine-side
    issues; only caller-side errors (invalid ``min_tier``) raise.
    """
    if min_tier not in _VALID_TIERS:
        raise EngineTrustError(
            f"min_tier {min_tier!r} not in {sorted(_VALID_TIERS)}"
        )
    now_ts = now if now is not None else time.time()

    try:
        m = load_manifest(engine_id)
    except EngineTrustManifestMissing:
        return TrustVerdict(
            engine_id=engine_id,
            passed=False,
            reason="manifest-missing",
            effective_tier="low",
            declared_tier="low",
        )
    except EngineTrustManifestMalformed as e:
        return TrustVerdict(
            engine_id=engine_id,
            passed=False,
            reason="manifest-malformed",
            effective_tier="low",
            declared_tier="low",
            detail={"error": str(e)[:200]},
        )

    declared_tier = m.metadata.trust_tier
    valid_until_dt = _parse_iso8601(m.metadata.valid_until,
                                     where="metadata.valid_until")
    expired = now_ts > valid_until_dt.timestamp()
    effective_tier = "low" if expired else declared_tier

    if _TIER_ORDER[effective_tier] < _TIER_ORDER[min_tier]:
        return TrustVerdict(
            engine_id=engine_id,
            passed=False,
            reason="manifest-expired" if expired else "trust-tier-too-low",
            effective_tier=effective_tier,
            declared_tier=declared_tier,
            expired=expired,
            expired_at=m.metadata.valid_until if expired else "",
            evaluated_at=m.metadata.evaluated_at,
            detail={"min_tier": min_tier},
        )

    binary_check = "skipped"
    expected = m.spec.binary_sha256
    observed: str | None = None
    if expected is not None and current_binary_path is not None:
        binary_path = Path(current_binary_path)
        if not binary_path.exists():
            return TrustVerdict(
                engine_id=engine_id,
                passed=False,
                reason="binary-missing",
                effective_tier=effective_tier,
                declared_tier=declared_tier,
                expired=expired,
                evaluated_at=m.metadata.evaluated_at,
                expected_sha256=expected,
                binary_check="binary-missing",
                detail={"binary_path": str(binary_path)},
            )
        observed = compute_binary_sha256(binary_path)
        if observed != expected:
            return TrustVerdict(
                engine_id=engine_id,
                passed=False,
                reason="binary-hash-mismatch",
                effective_tier=effective_tier,
                declared_tier=declared_tier,
                expired=expired,
                evaluated_at=m.metadata.evaluated_at,
                expected_sha256=expected,
                observed_sha256=observed,
                binary_check="mismatch",
                detail={"binary_path": str(binary_path)},
            )
        binary_check = "matched"

    return TrustVerdict(
        engine_id=engine_id,
        passed=True,
        reason="ok",
        effective_tier=effective_tier,
        declared_tier=declared_tier,
        expired=expired,
        expired_at=m.metadata.valid_until if expired else "",
        evaluated_at=m.metadata.evaluated_at,
        binary_check=binary_check,
        expected_sha256=expected,
        observed_sha256=observed,
    )


# ---------------------------------------------------------------------------
# Audit emission (caller-driven)
# ---------------------------------------------------------------------------


# Per-event allow-list. Mirror der L23/L24/L25/L28/L29-Regel —
# nur Metadaten, keine Manifest-Inhalte oder Binary-Bytes.
_AUDIT_ALLOWED_FIELDS: dict[str, frozenset[str]] = {
    "engine.trust_tier_violated":    frozenset({
        "engine_id", "actual_tier", "min_tier",
    }),
    "engine.trust_manifest_expired": frozenset({
        "engine_id", "evaluated_at", "valid_until", "effective_tier",
    }),
    "engine.binary_hash_mismatch":   frozenset({
        "engine_id", "binary_path", "expected_sha256", "observed_sha256",
    }),
    "engine.trust_manifest_missing": frozenset({
        "engine_id", "reason",
    }),
}

_FORBIDDEN_FIELDS: frozenset[str] = frozenset({
    "manifest_body", "manifest_yaml", "binary_bytes", "private_key",
    "secret", "token", "key", "prompt", "output_text", "final_text",
})


def _validate_audit_details(event_type: str, details: dict[str, Any]) -> None:
    allowed = _AUDIT_ALLOWED_FIELDS.get(event_type)
    if allowed is None:
        raise EngineTrustAuditFieldNotAllowed(
            f"unknown event_type {event_type!r}"
        )
    for k in details.keys():
        if k in _FORBIDDEN_FIELDS:
            raise EngineTrustAuditFieldNotAllowed(
                f"field {k!r} is in _FORBIDDEN_FIELDS for {event_type}"
            )
        if k not in allowed:
            raise EngineTrustAuditFieldNotAllowed(
                f"field {k!r} not in allow-list for {event_type}; "
                f"allowed: {sorted(allowed)}"
            )


def emit_violation_event(
    verdict: TrustVerdict,
    *,
    audit_path: Path | None = None,
    binary_path: str | Path | None = None,
) -> str | None:
    """Emit a structured audit event for a failed verdict.

    Returns the event_type that was emitted, or ``None`` if the
    verdict passed (no event needed).
    """
    if verdict.passed:
        return None

    if audit_path is None:
        audit_path = (
            _corvin_home() / "tenants" / "_default" / "global" /
            "forge" / "audit.jsonl"
        )

    forge_path = _THIS.parent.parent.parent / "forge"
    if str(forge_path) not in sys.path:
        sys.path.insert(0, str(forge_path))
    try:
        from forge import security_events as _se
    except Exception as e:  # pragma: no cover — defensive
        raise EngineTrustError(
            f"cannot import forge.security_events: {e}"
        ) from e

    reason = verdict.reason
    details: dict[str, Any] = {"engine_id": verdict.engine_id}
    if reason == "trust-tier-too-low":
        event_type = "engine.trust_tier_violated"
        details["actual_tier"] = verdict.effective_tier
        details["min_tier"] = verdict.detail.get("min_tier", "")
    elif reason == "manifest-expired":
        event_type = "engine.trust_manifest_expired"
        details["evaluated_at"] = verdict.evaluated_at
        details["valid_until"] = verdict.expired_at
        details["effective_tier"] = verdict.effective_tier
    elif reason == "binary-hash-mismatch":
        event_type = "engine.binary_hash_mismatch"
        details["binary_path"] = str(binary_path or verdict.detail.get("binary_path", ""))
        details["expected_sha256"] = verdict.expected_sha256 or ""
        details["observed_sha256"] = verdict.observed_sha256 or ""
    elif reason == "binary-missing":
        event_type = "engine.binary_hash_mismatch"
        details["binary_path"] = str(binary_path or verdict.detail.get("binary_path", ""))
        details["expected_sha256"] = verdict.expected_sha256 or ""
        details["observed_sha256"] = ""
    elif reason in ("manifest-missing", "manifest-malformed"):
        event_type = "engine.trust_manifest_missing"
        details["reason"] = reason
    else:
        return None

    _validate_audit_details(event_type, details)

    audit_path.parent.mkdir(parents=True, exist_ok=True)
    _se.write_event(
        audit_path,
        event_type,
        details=details,
    )
    return event_type
