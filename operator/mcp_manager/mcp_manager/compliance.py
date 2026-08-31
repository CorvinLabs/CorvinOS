"""MCP Plugin Manager — L34/L35 compliance checks (ADR-0096 M2).

Two check sites:
  1. Activation-time  — called from activate(); raises ComplianceError on hard violation.
  2. Spawn-time       — called from get_active_mcp_servers(); emits mcp_plugin.spawn_blocked
                        and removes the tool from the servers dict (fail-closed).

L34 (data classification locality):
  Tool declares compliance.locality (local | eu_cloud | us_cloud | unknown).
  Checked against the tenant's data-classification matrix: the tool's locality
  must be allowed for at least INTERNAL data. If the tenant restricts to local-only
  a us_cloud tool is blocked.

L35 (egress gate):
  Tool declares compliance.hosts (list of hostnames it connects to).
  If declared, each host is validated against the tenant EgressGate.
  If network_egress=="required" but no hosts are declared, a WARNING is emitted
  but activation is not blocked (the specific host is unknown).
"""
from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path
from typing import Any


class ComplianceError(Exception):
    pass


# Locality → set of DataClassification levels that may flow to that locality.
# Intentionally STRICTER than the live DEFAULT_MATRIX in
# data_classification.py (permissive since ADR-0173): this dict is only the
# fail-closed fallback used when the tenant matrix cannot be loaded at all.
_DEFAULT_LOCALITY_ALLOWED_LEVELS: dict[str, frozenset[str]] = {
    "local":    frozenset({"PUBLIC", "INTERNAL", "CONFIDENTIAL", "SECRET"}),
    "eu_cloud": frozenset({"PUBLIC", "INTERNAL"}),
    "us_cloud": frozenset({"PUBLIC"}),
    "unknown":  frozenset({"PUBLIC"}),
}


def _load_tenant_matrix(tid: str) -> dict[str, frozenset[str]]:
    """Load per-tenant locality matrix from tenant.corvin.yaml.

    Falls back to the default matrix if config is absent or unreadable.
    """
    try:
        _bridges_shared = Path(__file__).resolve().parents[2] / "bridges" / "shared"
        if str(_bridges_shared) not in sys.path:
            sys.path.insert(0, str(_bridges_shared))
        from data_classification import (  # type: ignore[import-not-found]
            DataClassification, DataFlowGuard,
        )
        from adapter import _tenant_yaml_path  # type: ignore[import-not-found]
        import yaml  # type: ignore[import-not-found]

        yaml_path = _tenant_yaml_path(tid)
        tenant_cfg: dict[str, Any] = {}
        if yaml_path.is_file():
            with open(yaml_path, encoding="utf-8") as fh:
                tenant_cfg = yaml.safe_load(fh) or {}

        guard = DataFlowGuard.from_tenant_config(tenant_cfg)
        matrix: dict[str, frozenset[str]] = {}
        for locality in ("local", "eu_cloud", "us_cloud", "unknown"):
            allowed: set[str] = set()
            for cls in DataClassification:
                if locality in guard.matrix.get(cls, frozenset()):
                    allowed.add(cls.name)
            matrix[locality] = frozenset(allowed)
        return matrix
    except Exception:
        return dict(_DEFAULT_LOCALITY_ALLOWED_LEVELS)


def _load_egress_gate(tid: str):
    """Return an EgressGate loaded from tenant config, or None if unavailable."""
    try:
        _bridges_shared = Path(__file__).resolve().parents[2] / "bridges" / "shared"
        if str(_bridges_shared) not in sys.path:
            sys.path.insert(0, str(_bridges_shared))
        from egress_gate import EgressGate  # type: ignore[import-not-found]
        from adapter import _tenant_yaml_path  # type: ignore[import-not-found]
        import yaml  # type: ignore[import-not-found]

        yaml_path = _tenant_yaml_path(tid)
        tenant_cfg: dict[str, Any] = {}
        if yaml_path.is_file():
            with open(yaml_path, encoding="utf-8") as fh:
                tenant_cfg = yaml.safe_load(fh) or {}
        return EgressGate.from_tenant_config(tenant_cfg)
    except Exception:
        return None


def _emit_spawn_blocked(tool_id: str, reason: str, tid: str) -> None:
    try:
        from . import catalog as _cat  # noqa: PLC0415 (lazy — avoid circular)
        _forge = Path(__file__).resolve().parents[2] / "forge"
        if str(_forge) not in sys.path:
            sys.path.insert(0, str(_forge))
        from forge.security_events import write_event  # type: ignore[import-not-found]
        _home_str = os.environ.get("CORVIN_HOME")
        _home = Path(_home_str).expanduser() if _home_str else (Path.home() / ".corvin")
        audit_path = _home / "tenants" / tid / "global" / "audit.jsonl"
        write_event(audit_path, "mcp_plugin.spawn_blocked", details={
            "tool_id": tool_id,
            "tenant_id": tid,
            "reason": reason,
        })
    except Exception:
        pass


# ── SHA256 verification ───────────────────────────────────────────────────────


def verify_sha256(tool_id: str, expected_sha256: str, tid: str) -> tuple[bool, str]:
    """Verify stored tarball SHA256. Returns (ok, reason)."""
    from . import catalog as _cat  # noqa: PLC0415
    tarball = _cat.catalog_dir(tid) / "installs" / f"{tool_id}.tar.gz"
    if not tarball.is_file():
        return False, f"tarball not found at {tarball}"
    sha = hashlib.sha256()
    with open(tarball, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            sha.update(chunk)
    actual = sha.hexdigest()
    if actual != expected_sha256:
        return False, f"sha256 mismatch: expected={expected_sha256[:16]}… got={actual[:16]}…"
    return True, ""


# ── Secret vault check ────────────────────────────────────────────────────────


def _load_vault() -> dict[str, str]:
    try:
        _forge = Path(__file__).resolve().parents[2] / "forge"
        if str(_forge) not in sys.path:
            sys.path.insert(0, str(_forge))
        from forge.secret_vault import load_vault  # type: ignore[import-not-found]
        return load_vault()
    except Exception:
        pass
    # Fallback: direct file read
    env = os.environ.get("CORVIN_SECRET_VAULT")
    if env:
        vault_path = Path(env).expanduser()
    else:
        cfg = os.environ.get("XDG_CONFIG_HOME") or "~/.config"
        vault_path = Path(cfg).expanduser() / "corvin-voice" / "secrets.json"
    if not vault_path.is_file():
        return {}
    try:
        import json
        return json.loads(vault_path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def check_secrets(tool_entry: dict[str, Any]) -> tuple[bool, str]:
    """Return (ok, reason) for required-secret availability.

    Checks the vault for required secrets. Returns (False, reason) if any
    required secret is missing.
    """
    secrets = tool_entry.get("secrets") or []
    required = [s for s in secrets if s.get("required") and s.get("name")]
    if not required:
        return True, ""
    vault = _load_vault()
    missing = [s["name"] for s in required if s["name"] not in vault]
    if missing:
        return False, f"missing_secret:{','.join(missing)}"
    return True, ""


# ── L34 locality check ────────────────────────────────────────────────────────


def check_locality(tool_entry: dict[str, Any], tid: str) -> tuple[bool, str]:
    """Return (ok, reason) for L34 locality compliance.

    The tool's declared locality must be allowed for INTERNAL data in the
    tenant's classification matrix. This ensures the tool can process at
    minimum the default data tier.
    """
    compliance = tool_entry.get("compliance") or {}
    locality = (compliance.get("locality") or "unknown").lower()
    matrix = _load_tenant_matrix(tid)
    allowed_levels = matrix.get(locality, frozenset())
    if "INTERNAL" not in allowed_levels:
        return False, f"l34_locality:{locality} not allowed for INTERNAL data"
    return True, ""


# ── L35 egress check ─────────────────────────────────────────────────────────


def check_egress(tool_entry: dict[str, Any], tid: str) -> tuple[bool, str]:
    """Return (ok, reason) for L35 egress compliance.

    Validates each host declared in compliance.hosts against the tenant
    EgressGate. If no hosts are declared, returns (True, "") — the caller
    should emit a WARNING separately.
    """
    compliance = tool_entry.get("compliance") or {}
    hosts = compliance.get("hosts") or []
    if not hosts:
        return True, ""

    gate = _load_egress_gate(tid)
    if gate is None:
        # Hosts ARE declared but the L35 gate could not be loaded — fail CLOSED.
        # Returning (True, "") here let an MCP plugin with declared egress hosts
        # activate ungated whenever egress_gate failed to import/parse, exactly
        # the fail-open the L35 mandate forbids (security review 2026-06-27).
        return False, "l35_egress:gate_unavailable (fail-closed)"

    for host in hosts:
        decision = gate.validate(host, engine_id="mcp_plugin")
        if not decision.allowed:
            return False, f"l35_egress:{host} denied ({decision.reason})"
    return True, ""


# ── Combined activation-time check ───────────────────────────────────────────


def check_activation_compliance(tool_entry: dict[str, Any], tid: str) -> None:
    """Raise ComplianceError if the tool violates L34 or L35 at activation time.

    Activation-time check is a hard gate: a non-compliant tool cannot be
    added to active.json. The caller is responsible for emitting the audit event.
    """
    ok, reason = check_locality(tool_entry, tid)
    if not ok:
        raise ComplianceError(
            f"Tool {tool_entry.get('id')!r} violates L34: {reason}. "
            "Adjust the tenant classification matrix or choose a different tool."
        )

    ok, reason = check_egress(tool_entry, tid)
    if not ok:
        raise ComplianceError(
            f"Tool {tool_entry.get('id')!r} violates L35: {reason}. "
            "Add the host to tenant allowed_hosts or adjust egress policy."
        )


# ── Spawn-time filter ─────────────────────────────────────────────────────────


def filter_compliant_servers(
    servers: dict[str, Any],
    tool_entries: dict[str, dict[str, Any]],
    tid: str,
) -> dict[str, Any]:
    """Remove non-compliant tools from *servers* dict (fail-closed).

    Emits mcp_plugin.spawn_blocked for each removed tool with the specific
    reason code. Called from get_active_mcp_servers().
    """
    result: dict[str, Any] = {}
    for tool_id, server_cfg in servers.items():
        entry = tool_entries.get(tool_id)
        if entry is None:
            result[tool_id] = server_cfg
            continue

        blocked = False

        # SHA256 verification (GitHub/binary installs only)
        expected_sha = entry.get("sha256")
        if expected_sha:
            ok, reason = verify_sha256(tool_id, expected_sha, tid)
            if not ok:
                _emit_spawn_blocked(tool_id, f"sha_mismatch:{reason}", tid)
                blocked = True

        # Docker digest verification (M4 — docker: sources only)
        if not blocked and (entry.get("source") or "").startswith("docker:"):
            from . import installer as _ins  # noqa: PLC0415 (lazy to avoid circular)
            if not _ins.verify_docker_digest(entry):
                _emit_spawn_blocked(tool_id, "docker_digest_mismatch", tid)
                blocked = True

        # Required secret availability
        if not blocked:
            ok, reason = check_secrets(entry)
            if not ok:
                _emit_spawn_blocked(tool_id, reason, tid)
                blocked = True

        # L34 locality
        if not blocked:
            ok, reason = check_locality(entry, tid)
            if not ok:
                _emit_spawn_blocked(tool_id, reason, tid)
                blocked = True

        # L35 egress
        if not blocked:
            ok, reason = check_egress(entry, tid)
            if not ok:
                _emit_spawn_blocked(tool_id, reason, tid)
                blocked = True

        if not blocked:
            result[tool_id] = server_cfg

    return result
