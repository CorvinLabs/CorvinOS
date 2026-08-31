"""Per-tenant configuration: ``tenant.corvin.yaml``.

ADR-0007 Phase 3.1 — the data layer for tenant-level policy. Phase 3.2
wires the dispatcher; Phase 3.3 wires the data-residency zones. This
module defines the schema, the loader and the writer.

Schema sketch::

    apiVersion: corvin/v1
    kind: Tenant
    metadata:
      id: acme
      display_name: ACME Corporation
      created_at: "2026-05-11T00:00:00Z"
    spec:
      data_residency:
        zone: eu-west
        allowed_engines: [claude_code]
        forbid_engines: []
      budget:
        max_runs_per_day: 5000
        max_tokens_per_day: 10000000
        max_wall_clock_per_run_s: 300

Resolution contract
-------------------

* ``load(tenant_id) -> TenantConfig`` returns the validated config
  on success, raises :class:`TenantConfigMalformed` on a defective
  file (bad mode, bad YAML, schema violation).
* ``load_or_default(tenant_id) -> TenantConfig`` returns the
  validated config or, when the file is absent, a permissive
  default. This is what the dispatcher uses in 3.2.
* ``save(config)`` writes the YAML atomically with mode ``0o600``.

What this module does NOT do
----------------------------

* It does not enforce any policy. 3.2 owns the engine-allowlist
  gate; 3.3 owns the zone gate. 3.1 is the data layer.
* It does not create the tenant directory. The Phase 1.4 migration
  helper remains the sole owner of tenant-tree creation.
* It does not validate engine names against the engine registry.
  Phase 3.2 reads ``bridges/shared/agents/`` for that.
"""
from __future__ import annotations

import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

# Forge path so we can reuse tenant validation + path helpers.
_THIS_DIR = Path(__file__).resolve().parent
_REPO = _THIS_DIR.parents[3]
_FORGE_PATH = _REPO / "operator" / "forge"
if str(_FORGE_PATH) not in sys.path:
    sys.path.insert(0, str(_FORGE_PATH))

from forge.tenants import validate_tenant_id  # noqa: E402
from forge import paths as _forge_paths  # noqa: E402


# ── Constants ────────────────────────────────────────────────────────


CONFIG_FILENAME = "tenant.corvin.yaml"
_REQUIRED_MODE = 0o600

# Engine names that are recognised by Corvin today. Phase 3.2 will
# validate against the engine registry at runtime; here we just keep a
# permissive whitelist of "expected" entries so a yaml typo is caught
# early in CLI workflows.
_KNOWN_ENGINE_NAMES = frozenset({"claude_code", "codex_cli"})

# Zone identifiers. Free-form strings; convention is region-shape
# (eu-west, us-east, ap-south, on-prem). Phase 3.3 maps zones to
# engine endpoints. Phase 3.1 just stores the label.
_ZONE_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}$")


# ── Exceptions ───────────────────────────────────────────────────────


class TenantConfigMalformed(Exception):
    """Config file present but unreadable / wrong shape / mode > 0o600."""


# ── Pydantic schema ──────────────────────────────────────────────────


class DataResidency(BaseModel):
    """Engine + zone constraints (Phase 3.3 enforces)."""
    model_config = ConfigDict(extra="forbid")

    zone:             str | None = None
    allowed_engines:  list[str]  = Field(default_factory=list)
    forbid_engines:   list[str]  = Field(default_factory=list)


class Budget(BaseModel):
    """Per-tenant resource caps (Phase 7 enforces)."""
    model_config = ConfigDict(extra="forbid")

    max_runs_per_day:           int | None = Field(default=None, ge=1)
    max_tokens_per_day:         int | None = Field(default=None, ge=1)
    max_wall_clock_per_run_s:   int | None = Field(default=None, ge=1, le=3600)


class ComputeConfig(BaseModel):
    """ADR-0013 — per-tenant compute-worker policy. Default on."""
    model_config = ConfigDict(extra="forbid")

    enabled:                     bool = True
    max_parallel_iterations:     int = Field(default=4, ge=1, le=16)
    max_concurrent_runs:         int = Field(default=2, ge=1, le=8)
    max_iterations_per_run:      int = Field(default=200, ge=1, le=10000)
    max_wall_clock_per_run_s:    int = Field(default=600, ge=1, le=86400)
    top_k_size:                  int = Field(default=5, ge=1, le=10)
    disallow_llm_strategies:     bool = False
    strategies_allowed:          list[str] = Field(
        default_factory=lambda: ["grid", "random", "bayesian"],
    )
    # ADR-0099 — Anthropic Batch API backend (opt-in, operator-gated).
    # Written by the corvin-batch MCP Plugin Manager manifest on install.
    # Only active when engine="anthropic_batch" in ComputeSpec; existing
    # FlatEngine / PipelineEngine / HACEngine jobs are unaffected.
    batch_backend:               str | None = None   # "anthropic_batch" to enable
    batch_min_candidates:        int = Field(default=100, ge=1, le=10000)


class EngineTrustConfig(BaseModel):
    """ADR-0020 — per-tenant engine-trust policy.

    Phase 30.1 wires the tier-gate (``min_tier``); Phase 30.2 wires
    the canary-drift signal (``canary_alert_delta`` /
    ``canary_min_window_days`` / ``auto_block_on_drift``); Phase 30.3
    wires the per-persona ``output_sentinel`` opt-in.

    All fields are opt-in. The permissive default is ``min_tier:
    "low"``, which means any installed engine with a valid manifest
    passes — single-operator setups don't need to configure anything.
    """
    model_config = ConfigDict(extra="forbid")

    # Phase 30.1 — tier-gate
    min_tier:                    Literal["low", "medium", "high"] = "low"
    require_binary_pin:          bool = False  # for ext. engines, error on null binary_sha256

    # Phase 30.2 — canary drift signal (data-only here; loop ships in 30.2)
    canary_alert_delta:          float = Field(default=0.10, ge=0.0, le=1.0)
    canary_min_window_days:      int = Field(default=7, ge=1, le=90)
    auto_block_on_drift:         bool = False

    # Phase 30.3 — output-sentinel opt-in (data-only here; sentinel ships in 30.3)
    sentinel_personas:           list[str] = Field(default_factory=list)
    audit_passed_sentinel:       bool = False


class TenantSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    data_residency: DataResidency      = Field(default_factory=DataResidency)
    budget:         Budget             = Field(default_factory=Budget)
    compute:        ComputeConfig | None = None
    engine_trust:   EngineTrustConfig | None = None


class TenantMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id:           str
    display_name: str = ""
    created_at:   str = ""  # ISO-8601; opaque to the loader


class TenantConfig(BaseModel):
    """Top-level on-disk schema for ``tenant.corvin.yaml``."""
    model_config = ConfigDict(extra="forbid")

    apiVersion: Literal["corvin/v1"]
    kind:       Literal["Tenant"]
    metadata:   TenantMetadata
    spec:       TenantSpec = Field(default_factory=TenantSpec)

    # ---- Constructors --------------------------------------------------

    @classmethod
    def default(cls, tenant_id: str, *, display_name: str = "") -> "TenantConfig":
        """Permissive default: no engine restrictions, no zone, no caps.

        Used when ``tenant.corvin.yaml`` is absent — the tenant runs
        with full Phase-2 capabilities until the operator writes a
        config file.
        """
        validate_tenant_id(tenant_id)
        now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        return cls(
            apiVersion="corvin/v1",
            kind="Tenant",
            metadata=TenantMetadata(
                id=tenant_id,
                display_name=display_name,
                created_at=now_iso,
            ),
            spec=TenantSpec(),
        )

    # ---- Validation helpers --------------------------------------------

    def is_engine_allowed(self, engine_name: str) -> bool:
        """Phase 3.2 will call this from the dispatcher."""
        if not isinstance(engine_name, str) or not engine_name:
            return False
        rd = self.spec.data_residency
        if engine_name in rd.forbid_engines:
            return False
        if rd.allowed_engines:
            return engine_name in rd.allowed_engines
        # Empty allowlist = no restriction.
        return True


# ── Path helpers ─────────────────────────────────────────────────────


def _config_path(tenant_id: str) -> Path:
    return _forge_paths.tenant_global_dir(tenant_id) / CONFIG_FILENAME


# ── Loader / saver ───────────────────────────────────────────────────


def _validate_zone(zone: str | None) -> None:
    if zone is None:
        return
    if not isinstance(zone, str) or not _ZONE_RE.match(zone):
        raise TenantConfigMalformed(
            f"invalid zone {zone!r}; must match {_ZONE_RE.pattern}"
        )


def _validate_engine_list(field: str, names: list[str]) -> None:
    if not isinstance(names, list):
        raise TenantConfigMalformed(
            f"{field} must be a list, got {type(names).__name__}"
        )
    for n in names:
        if not isinstance(n, str) or not n:
            raise TenantConfigMalformed(
                f"{field} entries must be non-empty strings, got {n!r}"
            )


def load(tenant_id: str) -> TenantConfig:
    """Load + validate the tenant config. Raises on any defect."""
    validate_tenant_id(tenant_id)
    p = _config_path(tenant_id)
    if not p.exists():
        raise TenantConfigMalformed(f"no config for tenant {tenant_id!r}")
    try:
        st = p.stat()
    except OSError as e:
        raise TenantConfigMalformed(f"stat failed for {p}: {e}") from e
    mode = st.st_mode & 0o777
    if mode != _REQUIRED_MODE:
        raise TenantConfigMalformed(
            f"{p} has mode 0o{mode:o}, want 0o{_REQUIRED_MODE:o}"
        )
    try:
        raw = p.read_text(encoding="utf-8")
    except OSError as e:
        raise TenantConfigMalformed(f"read failed for {p}: {e}") from e
    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as e:
        raise TenantConfigMalformed(f"malformed YAML in {p}: {e}") from e
    if not isinstance(data, dict):
        raise TenantConfigMalformed(f"{p}: top-level must be a mapping")
    try:
        config = TenantConfig.model_validate(data)
    except Exception as e:
        raise TenantConfigMalformed(f"{p}: schema validation failed: {e}") from e
    # Defensive post-validation — Pydantic's extra='forbid' catches
    # unknown keys but engine-name + zone shape need explicit checks.
    rd = config.spec.data_residency
    _validate_zone(rd.zone)
    _validate_engine_list("allowed_engines", rd.allowed_engines)
    _validate_engine_list("forbid_engines",  rd.forbid_engines)
    # Cross-field: metadata.id MUST match the file's location
    if config.metadata.id != tenant_id:
        raise TenantConfigMalformed(
            f"{p}: metadata.id {config.metadata.id!r} != "
            f"on-disk tenant {tenant_id!r}"
        )
    return config


def load_or_default(tenant_id: str) -> TenantConfig:
    """Load the on-disk config, or return a permissive default if
    the file does not exist. Re-raises :class:`TenantConfigMalformed`
    when the file exists but is defective — silently falling back to
    a default would mask operator-config errors."""
    validate_tenant_id(tenant_id)
    if not _config_path(tenant_id).exists():
        return TenantConfig.default(tenant_id)
    return load(tenant_id)


def save(config: TenantConfig) -> Path:
    """Write *config* to ``<tenant_home>/global/tenant.corvin.yaml``
    with mode ``0o600``, atomically.

    Refuses to create the tenant tree — the Phase 1.4 migration
    helper is the only owner of that.
    """
    tenant_id = config.metadata.id
    validate_tenant_id(tenant_id)
    target = _config_path(tenant_id)
    if not target.parent.exists():
        raise TenantConfigMalformed(
            f"tenant directory does not exist: {target.parent}"
        )
    data = config.model_dump(mode="python")
    body = yaml.safe_dump(data, sort_keys=False, allow_unicode=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, _REQUIRED_MODE)
    try:
        os.write(fd, body.encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(tmp, target)
    os.chmod(target, _REQUIRED_MODE)
    return target


# ── Convenience: init a fresh config ──────────────────────────────────


def init(
    tenant_id: str,
    *,
    display_name: str = "",
    zone: str | None = None,
    allowed_engines: list[str] | None = None,
    forbid_engines: list[str] | None = None,
) -> TenantConfig:
    """Build + save a fresh :class:`TenantConfig` for *tenant_id*.

    Permissive by default; the operator narrows via the optional
    args or later via :func:`save`-and-edit.
    """
    config = TenantConfig.default(tenant_id, display_name=display_name)
    if zone is not None:
        _validate_zone(zone)
        config.spec.data_residency.zone = zone
    if allowed_engines is not None:
        _validate_engine_list("allowed_engines", allowed_engines)
        config.spec.data_residency.allowed_engines = list(allowed_engines)
    if forbid_engines is not None:
        _validate_engine_list("forbid_engines", forbid_engines)
        config.spec.data_residency.forbid_engines = list(forbid_engines)
    save(config)
    return config


def known_engine_names() -> frozenset[str]:
    """Exported so the CLI can warn on typos. Not authoritative —
    Phase 3.2 reads the engine registry at runtime."""
    return _KNOWN_ENGINE_NAMES
