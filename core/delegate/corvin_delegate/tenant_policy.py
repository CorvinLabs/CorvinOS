"""Layer 29.4a — Tenant-Policy + Engine-Zone-Routing for delegation.

Closes the data-residency bypass gap: ADR-0007 Phase 3.2 / 3.3 gates
engine choice and zone routing for the **gateway** (REST API). Until
this layer landed, ``run_delegate`` did NOT consult any of that —
so a tenant pinned to ``zone: eu-west`` could route through Codex
(OpenAI, US) via ``mcp__corvin_delegate__delegate_codex`` and the
operator wouldn't notice.

Design contract:

* The policy file at ``<corvin_home>/global/tenant.corvin.{yaml,json}``
  (or per-tenant ``tenants/<tid>/global/tenant.corvin.{yaml,json}``) is
  operator-managed. The Layer-10 path-gate protects it from LLM-side
  Write/Edit/Bash so it functions as a true security floor.
* No pyyaml dependency — JSON is the lingua franca; YAML is read via
  pyyaml *if available*, else the file must be ``.json``.
* Loader is best-effort lazy: missing file → ``None`` → no enforcement
  (preserves the single-operator zero-config path).
* Engine zone is resolved per (engine_id, model) tuple. Operators can
  override the per-engine default via env vars
  (``CORVIN_DELEGATE_<ENGINE>_ZONE``) — useful for Anthropic's EU
  endpoint, OpenAI's regional rollout, etc.
* Local execution (``ollama/...`` model on OpenCode) maps to zone
  ``"local"`` which is universally compatible.

The policy enforcement landing site is ``run_delegate`` in
``delegation.py``: the gate fires AFTER caller-side validation and
BEFORE engine factory construction, so a denied call burns no
subprocess resources.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Filesystem resolution
# ---------------------------------------------------------------------------


def _corvin_home() -> Path:
    """Mirror of forge.paths.corvin_home — strangler-fig env handling."""
    env = os.environ.get("CORVIN_HOME")
    if env:
        return Path(env).expanduser()
    return Path.home() / ".corvin"


def _candidate_paths(tenant_id: str) -> list[Path]:
    """Where to look for the tenant policy, in priority order.

    Per-tenant location wins over single-operator fallback. Both YAML
    and JSON shapes are accepted; first hit wins.
    """
    home = _corvin_home()
    return [
        home / "tenants" / tenant_id / "global" / "tenant.corvin.yaml",
        home / "tenants" / tenant_id / "global" / "tenant.corvin.yml",
        home / "tenants" / tenant_id / "global" / "tenant.corvin.json",
        home / "global" / "tenant.corvin.yaml",
        home / "global" / "tenant.corvin.yml",
        home / "global" / "tenant.corvin.json",
    ]


# ---------------------------------------------------------------------------
# Policy schema (subset of ADR-0007 Phase 3.1 schema)
# ---------------------------------------------------------------------------


@dataclass
class TenantPolicy:
    """Curated subset of ``tenant.corvin.yaml::spec.data_residency``.

    Only the three fields ``run_delegate`` enforces are surfaced —
    we deliberately do NOT mirror the full ADR-0007 schema here so
    a future schema bump doesn't require corvin-delegate updates.
    """
    tenant_id: str
    zone: str | None = None
    allowed_engines: list[str] = field(default_factory=list)
    forbid_engines: list[str] = field(default_factory=list)

    def is_engine_allowed(self, engine_id: str) -> bool:
        """Forbid > allowlist > default. Empty allowlist = no restriction."""
        if engine_id in self.forbid_engines:
            return False
        if not self.allowed_engines:
            return True
        return engine_id in self.allowed_engines


class PolicyMalformed(Exception):
    """Raised when a config file exists but cannot be parsed."""


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------


def load_policy(tenant_id: str | None = None) -> TenantPolicy | None:
    """Load tenant policy from disk. Returns None when no file exists.

    Resolution: explicit ``tenant_id`` arg > ``CORVIN_TENANT_ID`` env
    > ``"_default"``. Returns ``None`` for every tenant that has no
    policy file — the caller treats that as "no enforcement".

    Raises ``PolicyMalformed`` when the file exists but is broken.
    Fail-loud is correct here: a syntactically broken policy file
    silently treated as "no enforcement" is exactly the silent-bypass
    pattern we are avoiding.
    """
    tid = (tenant_id
           or os.environ.get("CORVIN_TENANT_ID")
           or "_default").strip()
    for path in _candidate_paths(tid):
        if path.exists():
            return _load_from_path(path, tid)
    return None


def _load_from_path(path: Path, tenant_id: str) -> TenantPolicy:
    """Parse a policy file. JSON is always supported; YAML needs pyyaml."""
    raw = path.read_text(encoding="utf-8")
    if path.suffix == ".json":
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            raise PolicyMalformed(f"{path}: invalid JSON: {e}") from e
    else:
        # YAML branch — only attempts the import lazily.
        try:
            import yaml  # type: ignore  # noqa: PLC0415
        except ImportError as e:
            raise PolicyMalformed(
                f"{path}: pyyaml not installed; convert to "
                f"tenant.corvin.json instead"
            ) from e
        try:
            data = yaml.safe_load(raw) or {}
        except Exception as e:  # noqa: BLE001  # pyyaml raises various
            raise PolicyMalformed(f"{path}: invalid YAML: {e}") from e
    if not isinstance(data, dict):
        raise PolicyMalformed(f"{path}: top-level must be a mapping")
    spec = data.get("spec") or {}
    if not isinstance(spec, dict):
        raise PolicyMalformed(f"{path}: spec must be a mapping")
    dr = spec.get("data_residency") or {}
    if not isinstance(dr, dict):
        raise PolicyMalformed(f"{path}: spec.data_residency must be a mapping")
    return TenantPolicy(
        tenant_id=tenant_id,
        zone=_validate_zone(dr.get("zone"), path),
        allowed_engines=_validate_engine_list(
            dr.get("allowed_engines"), "allowed_engines", path),
        forbid_engines=_validate_engine_list(
            dr.get("forbid_engines"), "forbid_engines", path),
    )


def _validate_zone(value: Any, path: Path) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise PolicyMalformed(f"{path}: data_residency.zone must be a string")
    return value.strip().lower()


def _validate_engine_list(value: Any, field_name: str, path: Path) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise PolicyMalformed(
            f"{path}: data_residency.{field_name} must be a list of strings"
        )
    out: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise PolicyMalformed(
                f"{path}: data_residency.{field_name} entries must be "
                f"non-empty strings"
            )
        out.append(item.strip())
    return out


# ---------------------------------------------------------------------------
# Engine zone resolution
# ---------------------------------------------------------------------------


# Default zones for each engine. These are the conservative defaults;
# operators with regional endpoints (Anthropic EU, etc.) override via
# the per-engine CORVIN_DELEGATE_<ENGINE>_ZONE env var.
_DEFAULT_ENGINE_ZONES: dict[str, str] = {
    "claude_code": "us",   # Anthropic API — default endpoint is US
    "codex_cli":   "us",   # OpenAI API — default endpoint is US
    "opencode":    "us",   # provider-agnostic; cloud default is US,
                           # local/cloud distinction handled by model
                           # prefix detection below.
    # hermes and copilot were previously ABSENT here, so
    # resolve_engine_zone() fell through to "unknown" for both — and
    # is_zone_compatible() denies "unknown" whenever a tenant has any zone
    # constraint set. hermes is the fully-local, zero-egress engine this
    # project explicitly recommends for CONFIDENTIAL-classified data (see
    # mcp_server.py's own marketing text) — treating it as an unrecognised/
    # foreign zone silently blocked it for any tenant with a residency
    # policy, unless the operator remembered CORVIN_DELEGATE_HERMES_ZONE=
    # local manually (adversarial review finding). copilot is genuinely
    # cloud (GitHub Copilot API), given its own explicit default here.
    "hermes":      "local",
    "copilot":     "us",
}


def resolve_engine_zone(engine_id: str, model: str | None = None) -> str:
    """Map ``(engine_id, model)`` to a compliance zone string.

    For OpenCode the model prefix takes precedence over the env
    default — ``ollama/...`` and ``local/...`` always map to ``local``
    regardless of operator config (data physically does not leave the
    machine). ``ollama-cloud/...`` maps to the cloud default.

    Returns ``"unknown"`` for unrecognised engines so the gate
    fail-closes when a tenant zone is set.
    """
    engine_id = (engine_id or "").strip()
    if not engine_id:
        return "unknown"

    # Per-engine env override — operator-set, not LLM-controllable.
    env_key = f"CORVIN_DELEGATE_{engine_id.upper()}_ZONE"
    env_value = os.environ.get(env_key)
    if env_value and env_value.strip():
        operator_default = env_value.strip().lower()
    else:
        operator_default = _DEFAULT_ENGINE_ZONES.get(engine_id)

    # OpenCode's provider routing is determined by the model prefix.
    if engine_id == "opencode" and model:
        m = model.strip().lower()
        if m.startswith("ollama/") or m.startswith("local/"):
            return "local"
        if m.startswith("ollama-cloud/"):
            return operator_default or "us"
        # other providers (anthropic/, openai/, openrouter/, ...)
        # fall through to operator default
        return operator_default or "us"

    if operator_default:
        return operator_default

    return "unknown"


def is_zone_compatible(
    tenant_zone: str | None,
    engine_zone: str,
) -> tuple[bool, str]:
    """Return ``(allowed, reason)`` for the (tenant, engine) pair.

    Decision matrix:

    * tenant_zone is None         → allow (no constraint)
    * engine_zone == "local"      → allow (data never leaves the host)
    * engine_zone == "global"     → allow (engine declares any-zone)
    * tenant_zone == engine_zone  → allow (zone match)
    * engine_zone == "unknown"    → deny (fail-closed; we cannot prove safety)
    * otherwise                   → deny (zone mismatch)
    """
    if not tenant_zone:
        return True, "no-tenant-constraint"
    tenant_zone = tenant_zone.strip().lower()
    engine_zone = (engine_zone or "").strip().lower()
    if engine_zone == "local":
        return True, "local-execution"
    if engine_zone == "global":
        return True, "global-engine"
    if engine_zone == tenant_zone:
        return True, "zone-match"
    if engine_zone == "unknown":
        return False, "unknown-engine-zone"
    return False, f"zone-mismatch:tenant={tenant_zone}:engine={engine_zone}"


__all__ = [
    "PolicyMalformed",
    "TenantPolicy",
    "is_zone_compatible",
    "load_policy",
    "resolve_engine_zone",
]
