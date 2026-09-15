"""ADR-0156 M1 — Custom Layer Registry.

Manages user-defined custom layers that extend CorvinOS with Tier A (prompt),
Tier B (tools), or Tier C (MCP server) capabilities.

On-disk layout
==============
Custom layers live at::

    <corvin_home>/tenants/<tid>/custom-layers/<vendor>.<name>/
        layer.corvin.yaml   (REQUIRED)
        system_prompt.md    (Tier A)
        skills/*.md         (Tier A)
        tools/*.py|.sh      (Tier B)
        mcp_server.py       (Tier C)

Registry file
=============
Stored as JSON at::

    <corvin_home>/tenants/<tid>/global/custom_layers.json

Schema::

    {
      "layers": {
        "<name>": {
          "name": str,
          "display_name": str,
          "tier": "A"|"B"|"C",
          "version": str,
          "active": bool,
          "installed_at": ISO-8601 timestamp
        }
      }
    }

Namespace rules (ADR-0142 mirror)
==================================
- Must match ``r'^[a-z0-9][a-z0-9-]*\\.[a-z0-9][a-z0-9_-]*$'``
- Vendor segment CANNOT be: ``corvin``, ``system``

Audit events (M7)
==================
Five events on the L16 hash chain:

- ``custom_layer.installed``
- ``custom_layer.enabled``
- ``custom_layer.disabled``
- ``custom_layer.removed``
- ``custom_layer.boot_limit_exceeded``

Allowed detail keys: ``layer_name``, ``tier``, ``tenant_id``, ``channel``,
``reason``.  NEVER manifest contents, tool code, or secret values.

Constraints (load-bearing — CLAUDE.md / ADR-0156)
===================================================
* NO ``import anthropic`` (CI AST lint enforces).
* Namespace gate is CRITICAL: vendor ``corvin`` or ``system`` is rejected.
* Use ``fcntl.flock`` for registry file writes (cross-process safety).
* Fail-open on read errors (never block startup for a registry read failure).
* NEVER write to audit.jsonl directly — use the shared ``audit_event`` function.
"""
from __future__ import annotations

from _compat_fcntl import fcntl  # portable: real fcntl on POSIX, no-op flock on Windows
import json
import os
import re
import shutil
import sys
import tarfile
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ── namespace / name validation ───────────────────────────────────────────────
# Full name format: <vendor>.<layer_name>
# vendor: r'[a-z0-9][a-z0-9-]*'
# layer_name: r'[a-z0-9][a-z0-9_-]*'
_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]*\.[a-z0-9][a-z0-9_-]*$")
_FORBIDDEN_VENDORS = frozenset({"corvin", "system"})

# Valid tiers
VALID_TIERS = frozenset({"A", "B", "C"})

# Prompt position values allowed (never before_persona)
VALID_PROMPT_POSITIONS = frozenset({"after_persona", "last"})


class CustomLayerError(Exception):
    """Base error for custom layer operations."""


class CustomLayerNameError(CustomLayerError):
    """Raised when a name fails the namespace gate."""


class CustomLayerManifestError(CustomLayerError):
    """Raised when a layer.corvin.yaml fails validation."""


class CustomLayerNotFoundError(CustomLayerError):
    """Raised when the requested layer is not installed."""


# ── dataclass ─────────────────────────────────────────────────────────────────

@dataclass
class CustomLayerRecord:
    """Registry entry for one installed custom layer."""
    name: str
    display_name: str
    tier: str                   # "A", "B", or "C"
    version: str
    active: bool
    installed_at: str           # ISO-8601 UTC
    # Runtime-only (not persisted to registry JSON):
    root: Path | None = field(default=None, compare=False, repr=False)

    def as_dict(self) -> dict[str, Any]:
        """Serialisable form for custom_layers.json (omits runtime ``root``)."""
        return {
            "name": self.name,
            "display_name": self.display_name,
            "tier": self.tier,
            "version": self.version,
            "active": self.active,
            "installed_at": self.installed_at,
        }


# ── path helpers ──────────────────────────────────────────────────────────────

def _corvin_home() -> Path:
    try:
        from .paths import corvin_home  # type: ignore
    except ImportError:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from paths import corvin_home  # type: ignore
    return corvin_home()


def _tenant_home(tenant_id: str | None) -> Path:
    try:
        from .paths import tenant_home  # type: ignore
    except ImportError:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from paths import tenant_home  # type: ignore
    return tenant_home(tenant_id)


def _custom_layers_root(tenant_id: str | None = None) -> Path:
    """Base directory for installed custom layers: <tenant>/custom-layers/."""
    return _tenant_home(tenant_id) / "custom-layers"


def _registry_path(tenant_id: str | None = None) -> Path:
    """Registry JSON file: <tenant>/global/custom_layers.json."""
    return _tenant_home(tenant_id) / "global" / "custom_layers.json"


# ── audit helper ──────────────────────────────────────────────────────────────

def _audit(event: str, *, layer_name: str = "", tier: str = "",
           tenant_id: str = "", channel: str = "", reason: str = "") -> None:
    """Best-effort custom_layer.* audit emit.

    Routes through the shared audit.py → forge security_events chain so the
    events land on the unified hash chain.  Silent on all failures (audit is
    observability, not a hard dependency).
    """
    details: dict[str, Any] = {}
    if layer_name:
        details["layer_name"] = layer_name
    if tier:
        details["tier"] = tier
    if reason:
        details["reason"] = reason
    try:
        try:
            from . import audit as _audit_mod  # type: ignore
        except ImportError:
            sys.path.insert(0, str(Path(__file__).resolve().parent))
            import audit as _audit_mod  # type: ignore
        _audit_mod.audit_event(
            event,
            channel=channel,
            tenant_id=tenant_id,
            details=details,
        )
    except Exception:  # pragma: no cover — best-effort
        pass


# ── namespace validation ──────────────────────────────────────────────────────

def validate_name(name: str) -> str:
    """Validate a custom layer name against namespace rules.

    Returns *name* unchanged if valid.  Raises :exc:`CustomLayerNameError`
    with a descriptive message otherwise.
    """
    if not isinstance(name, str) or not name:
        raise CustomLayerNameError("custom layer name must be a non-empty string")
    if "." not in name:
        raise CustomLayerNameError(
            f"'{name}' must contain a '.' separator (vendor.layer convention)"
        )
    vendor = name.split(".", 1)[0]
    if vendor in _FORBIDDEN_VENDORS:
        raise CustomLayerNameError(
            f"'{name}' uses the reserved '{vendor}.' namespace — "
            "choose a different vendor prefix"
        )
    # F-06: block prefix matches too (corvin-labs.x, system-core.y etc.).
    # Exact match above covers "corvin" and "system"; prefix match closes
    # impersonation via "corvin-*" / "system-*" vendor names.
    if any(vendor.startswith(p) for p in ("corvin", "system")):
        raise CustomLayerNameError(
            f"'{name}' vendor prefix '{vendor}' is reserved — "
            "vendor cannot start with 'corvin' or 'system'"
        )
    if not _NAME_RE.match(name):
        raise CustomLayerNameError(
            f"'{name}' fails the naming rule "
            r"'^[a-z0-9][a-z0-9-]*\.[a-z0-9][a-z0-9_-]*$'"
        )
    return name


# ── manifest validation ───────────────────────────────────────────────────────

def validate_manifest(manifest: dict[str, Any]) -> None:
    """Validate the parsed content of a ``layer.corvin.yaml`` manifest.

    Raises :exc:`CustomLayerManifestError` for schema violations and
    :exc:`CustomLayerNameError` for namespace violations.  Does NOT check
    that referenced files exist on disk.
    """
    if not isinstance(manifest, dict):
        raise CustomLayerManifestError("manifest must be a YAML mapping")

    # Required fields
    for field_name in ("name", "display_name", "version", "tier"):
        if not manifest.get(field_name):
            raise CustomLayerManifestError(f"manifest missing required field: '{field_name}'")

    name = str(manifest["name"])
    validate_name(name)  # raises CustomLayerNameError on violation

    tier = str(manifest["tier"])
    if tier not in VALID_TIERS:
        raise CustomLayerManifestError(
            f"invalid tier '{tier}' — must be one of: {', '.join(sorted(VALID_TIERS))}"
        )

    version = str(manifest["version"])
    if not version:
        raise CustomLayerManifestError("version must be a non-empty string")

    # Tier A optional prompt.position check
    prompt = manifest.get("prompt")
    if prompt is not None:
        if not isinstance(prompt, dict):
            raise CustomLayerManifestError("'prompt' must be a mapping")
        position = prompt.get("position")
        if position is not None and position not in VALID_PROMPT_POSITIONS:
            raise CustomLayerManifestError(
                f"prompt.position '{position}' is not allowed — "
                f"use one of: {', '.join(sorted(VALID_PROMPT_POSITIONS))}. "
                "'before_persona' is structurally forbidden."
            )

    # Tier B tools sanity check
    tools = manifest.get("tools", [])
    if tools is not None and not isinstance(tools, list):
        raise CustomLayerManifestError("'tools' must be a list")

    # Tier C mcp_server sanity check
    mcp = manifest.get("mcp_server")
    if mcp is not None and not isinstance(mcp, dict):
        raise CustomLayerManifestError("'mcp_server' must be a mapping")

    # meta.secrets must be names only (not values)
    meta = manifest.get("meta", {})
    if meta and isinstance(meta, dict):
        secrets = meta.get("secrets", [])
        if secrets and not isinstance(secrets, list):
            raise CustomLayerManifestError("meta.secrets must be a list of env-var names")


# ── registry read/write ───────────────────────────────────────────────────────

def load_registry(tenant_id: str | None = None) -> dict[str, CustomLayerRecord]:
    """Load the registry from disk.  Fail-open: returns empty dict on any read
    error so startup is never blocked by a corrupt registry file."""
    path = _registry_path(tenant_id)
    try:
        if not path.is_file():
            return {}
        raw = json.loads(path.read_text(encoding="utf-8"))
        layers: dict[str, CustomLayerRecord] = {}
        for name, entry in raw.get("layers", {}).items():
            try:
                rec = CustomLayerRecord(
                    name=entry["name"],
                    display_name=entry.get("display_name", entry["name"]),
                    tier=entry.get("tier", "A"),
                    version=entry.get("version", "0.0.0"),
                    active=bool(entry.get("active", False)),
                    installed_at=entry.get("installed_at", ""),
                )
                # Resolve root path if the layer directory exists
                root = _custom_layers_root(tenant_id) / name
                if root.is_dir():
                    rec.root = root
                layers[name] = rec
            except (KeyError, TypeError):
                # Skip corrupt individual entries — fail-open
                continue
        return layers
    except Exception:  # pragma: no cover — fail-open on read error
        return {}


def save_registry(
    layers: dict[str, CustomLayerRecord],
    tenant_id: str | None = None,
) -> None:
    """Persist the registry to disk with fcntl.flock for cross-process safety."""
    path = _registry_path(tenant_id)
    path.parent.mkdir(parents=True, exist_ok=True)

    payload = json.dumps(
        {"layers": {name: rec.as_dict() for name, rec in layers.items()}},
        indent=2,
        ensure_ascii=False,
    ) + "\n"

    # Atomic write via temp file + rename, guarded by flock on the registry file.
    # We open (or create) the registry file first to get a stable fd for flock,
    # then write via a sibling temp file and rename (POSIX atomic).
    lock_path = path.with_suffix(".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "w") as lock_fd:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(payload, encoding="utf-8")
            tmp.replace(path)
        finally:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)


# ── install / remove / enable / disable ──────────────────────────────────────

def install_layer(
    path: str | Path,
    tenant_id: str | None = None,
    *,
    upgrade: bool = False,
    channel: str = "",
) -> CustomLayerRecord:
    """Install a custom layer from a directory or ``.tar.gz`` archive.

    The source is validated, namespace-gated, and copied to
    ``<tenant>/custom-layers/<name>/``.  The registry JSON is updated
    atomically.  The layer is installed in the **disabled** state by default
    (same default-off safety model as ADR-0142 extensions).

    Parameters
    ----------
    path:
        Source directory or ``.tar.gz`` file path.
    tenant_id:
        Tenant to install into (defaults to ``_default``).
    upgrade:
        If *True*, an already-installed layer of the same name is replaced.
        The active state is preserved across upgrade.
    channel:
        Bridge channel name for audit metadata.

    Returns
    -------
    CustomLayerRecord
        The newly installed (or upgraded) record, with ``active=False``
        (unless this was an upgrade of an already-active layer).

    Raises
    ------
    CustomLayerError
        Source path does not exist or ``layer.corvin.yaml`` is missing.
    CustomLayerNameError
        Name fails the namespace gate.
    CustomLayerManifestError
        Manifest schema is invalid.
    ValueError
        Already installed and *upgrade* is False.
    """
    src = Path(path)
    _tmpdir: tempfile.TemporaryDirectory | None = None

    try:
        # Support .tar.gz sources — unpack to a temporary directory.
        if src.is_file() and (src.name.endswith(".tar.gz") or src.name.endswith(".tgz")):
            _tmpdir = tempfile.TemporaryDirectory(prefix="corvin-cl-")
            tmp_root = Path(_tmpdir.name)
            with tarfile.open(src, "r:gz") as tf:
                tf.extractall(tmp_root, filter="data")  # prevent path-traversal (CVE-class)
            # The tarball should contain exactly one top-level directory.
            candidates = [p for p in tmp_root.iterdir() if p.is_dir()]
            if len(candidates) == 1:
                src = candidates[0]
            else:
                src = tmp_root  # flat tarball

        if not src.is_dir():
            raise CustomLayerError(f"'{path}' is not a directory or .tar.gz archive")

        manifest_path = src / "layer.corvin.yaml"
        if not manifest_path.is_file():
            raise CustomLayerError(f"no layer.corvin.yaml found in '{src}'")

        try:
            import yaml
            raw_manifest: dict[str, Any] = yaml.safe_load(
                manifest_path.read_text(encoding="utf-8")
            ) or {}
        except Exception as exc:
            raise CustomLayerManifestError(f"cannot parse layer.corvin.yaml: {exc}") from exc

        validate_manifest(raw_manifest)

        name = str(raw_manifest["name"])
        display_name = str(raw_manifest.get("display_name", name))
        tier = str(raw_manifest["tier"])
        version = str(raw_manifest["version"])

        layers = load_registry(tenant_id)
        dest = _custom_layers_root(tenant_id) / name

        was_active = False
        if name in layers:
            if not upgrade:
                raise ValueError(
                    f"'{name}' is already installed — use upgrade=True to replace it"
                )
            was_active = layers[name].active
            if dest.is_dir():
                shutil.rmtree(dest)

        # ADR-0156 M2 — license gate for Tier-B/C layers.
        # Count currently active Tier-B/C layers, excluding this layer if it's
        # already installed (upgrade path: its active state is being preserved,
        # not incremented).
        try:
            try:
                from .custom_layer_gate import check_layer_install, LayerLimitExceeded  # type: ignore
            except ImportError:
                sys.path.insert(0, str(Path(__file__).resolve().parent))
                from custom_layer_gate import check_layer_install, LayerLimitExceeded  # type: ignore
            existing_bc = sum(
                1 for n, r in layers.items()
                if r.active and r.tier in ("B", "C") and n != name
            )
            check_layer_install(tier, existing_bc)
        except LayerLimitExceeded:
            raise  # surface directly to caller as a hard refusal

        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(src, dest)

        now = datetime.now(timezone.utc).isoformat()
        rec = CustomLayerRecord(
            name=name,
            display_name=display_name,
            tier=tier,
            version=version,
            active=was_active,  # preserve state on upgrade; default-off for new installs
            installed_at=now,
            root=dest,
        )
        layers[name] = rec
        save_registry(layers, tenant_id)

        _audit(
            "custom_layer.installed",
            layer_name=name,
            tier=tier,
            tenant_id=tenant_id or "_default",
            channel=channel,
            reason="upgrade" if upgrade else "install",
        )
        return rec
    finally:
        if _tmpdir is not None:
            _tmpdir.cleanup()


def remove_layer(
    name: str,
    tenant_id: str | None = None,
    *,
    channel: str = "",
) -> CustomLayerRecord:
    """Remove an installed custom layer (disable + delete files + registry).

    Raises :exc:`CustomLayerNotFoundError` when *name* is not installed.
    """
    layers = load_registry(tenant_id)
    if name not in layers:
        raise CustomLayerNotFoundError(f"custom layer '{name}' is not installed")
    rec = layers.pop(name)
    dest = _custom_layers_root(tenant_id) / name
    if dest.is_dir():
        shutil.rmtree(dest)
    save_registry(layers, tenant_id)
    _audit(
        "custom_layer.removed",
        layer_name=name,
        tier=rec.tier,
        tenant_id=tenant_id or "_default",
        channel=channel,
        reason="cli",
    )
    return rec


def enable_layer(
    name: str,
    tenant_id: str | None = None,
    *,
    channel: str = "",
) -> CustomLayerRecord:
    """Enable an installed custom layer.

    Raises :exc:`CustomLayerNotFoundError` when *name* is not installed.
    Raises :exc:`~custom_layer_gate.LayerLimitExceeded` when enabling a
    Tier-B/C layer would exceed the license-tier limit (same gate as
    install_layer — closing the bypass where two inactive layers are
    installed and then both enabled, circumventing the install-time count).
    """
    layers = load_registry(tenant_id)
    if name not in layers:
        raise CustomLayerNotFoundError(f"custom layer '{name}' is not installed")
    rec = layers[name]
    if not rec.active and rec.tier in ("B", "C"):
        # Gate: applying the same license check as install_layer so that
        # "install inactive, install inactive, enable both" cannot bypass
        # the per-tier limit (ADR-0156 M2, CL-ENABLE-BYPASS-01).
        try:
            try:
                from .custom_layer_gate import check_layer_install, LayerLimitExceeded  # type: ignore
            except ImportError:
                sys.path.insert(0, str(Path(__file__).resolve().parent))
                from custom_layer_gate import check_layer_install, LayerLimitExceeded  # type: ignore
            existing_bc = sum(
                1 for n, r in layers.items()
                if r.active and r.tier in ("B", "C") and n != name
            )
            check_layer_install(rec.tier, existing_bc)
        except LayerLimitExceeded:
            raise  # surface directly to caller as a hard refusal
    rec.active = True
    save_registry(layers, tenant_id)
    _audit(
        "custom_layer.enabled",
        layer_name=name,
        tier=rec.tier,
        tenant_id=tenant_id or "_default",
        channel=channel,
    )
    return rec


def disable_layer(
    name: str,
    tenant_id: str | None = None,
    *,
    channel: str = "",
) -> CustomLayerRecord:
    """Disable an installed custom layer without removing it.

    Raises :exc:`CustomLayerNotFoundError` when *name* is not installed.
    """
    layers = load_registry(tenant_id)
    if name not in layers:
        raise CustomLayerNotFoundError(f"custom layer '{name}' is not installed")
    rec = layers[name]
    rec.active = False
    save_registry(layers, tenant_id)
    _audit(
        "custom_layer.disabled",
        layer_name=name,
        tier=rec.tier,
        tenant_id=tenant_id or "_default",
        channel=channel,
    )
    return rec


def list_layers(tenant_id: str | None = None) -> list[CustomLayerRecord]:
    """Return all installed custom layers for *tenant_id*, sorted by name.

    Always succeeds — fail-open on a corrupt registry.
    """
    return sorted(load_registry(tenant_id).values(), key=lambda r: r.name)


def export_layer(
    name: str,
    dest_path: str | Path | None = None,
    tenant_id: str | None = None,
) -> Path:
    """Export an installed custom layer as a ``.tar.gz`` archive.

    Parameters
    ----------
    name:
        The layer to export.
    dest_path:
        Output file path.  Defaults to ``<cwd>/<name>-<version>.tar.gz``.
    tenant_id:
        Tenant where the layer is installed.

    Returns
    -------
    Path
        The path of the created archive.

    Raises
    ------
    CustomLayerNotFoundError
        Layer is not installed or its directory is missing.
    """
    layers = load_registry(tenant_id)
    if name not in layers:
        raise CustomLayerNotFoundError(f"custom layer '{name}' is not installed")
    rec = layers[name]
    src = _custom_layers_root(tenant_id) / name
    if not src.is_dir():
        raise CustomLayerNotFoundError(
            f"custom layer '{name}' directory missing at {src}"
        )
    out = Path(dest_path) if dest_path else Path.cwd() / f"{name}-{rec.version}.tar.gz"
    with tarfile.open(out, "w:gz") as tf:
        for child in sorted(src.iterdir()):
            tf.add(child, arcname=f"{name}/{child.name}", recursive=True)
    return out


# ── boot limit guard (ADR-0156 M2 — delegates to custom_layer_gate) ─────────

def check_boot_limit(
    tenant_id: str | None = None,
    *,
    limit: int | None = None,
    channel: str = "",
) -> list[str]:
    """Enforce the Tier-B/C active-layer limit at adapter boot.

    Delegates to :func:`custom_layer_gate.check_layer_boot`, which reads the
    active license tier, disables excess layers (oldest first), and emits
    ``custom_layer.boot_limit_exceeded`` WARNING audit events.

    The *limit* parameter is accepted for backward compatibility but is ignored
    — the gate always reads the live license limit via ``active_tier()`` so a
    stale caller-supplied value cannot weaken the enforcement.

    Parameters
    ----------
    tenant_id:
        Tenant to check.
    limit:
        Ignored (ADR-0156 M2: live license limit is used instead).
    channel:
        Bridge channel name for audit metadata.

    Returns
    -------
    list[str]
        Names of layers that were disabled.  Empty list when no action was
        needed.
    """
    try:
        try:
            from .custom_layer_gate import check_layer_boot as _clb  # type: ignore
        except ImportError:
            sys.path.insert(0, str(Path(__file__).resolve().parent))
            from custom_layer_gate import check_layer_boot as _clb  # type: ignore
        return _clb(tenant_id, channel=channel)
    except Exception as exc:
        # Fail-open for the boot helper — a gate import failure must not crash
        # the adapter.  Emit a CRITICAL audit event so the failure is visible in
        # the hash chain (F-04: silent swallowing prevented a forensic trace).
        import logging as _logging
        _logging.getLogger("corvin.custom_layer_registry").error(
            "check_boot_limit: gate call failed (%s) — boot enforcement skipped",
            exc,
        )
        try:
            _ch = Path(
                __import__("os").environ.get("CORVIN_HOME")
                or Path.home() / ".corvin"
            )
            _tid = __import__("os").environ.get("CORVIN_TENANT_ID", "_default") if tenant_id is None else tenant_id
            _ap = _ch / "tenants" / _tid / "global" / "forge" / "audit.jsonl"
            try:
                from forge.security_events import write_event as _cbl_we  # type: ignore
            except ImportError:
                from security_events import write_event as _cbl_we  # type: ignore
            _cbl_we(_ap, "custom_layer.boot_limit_enforcement_failed",
                    severity="CRITICAL",
                    details={"reason": "gate_import_or_call_failed"})
        except Exception:  # noqa: BLE001 — best-effort audit
            pass
        return []
