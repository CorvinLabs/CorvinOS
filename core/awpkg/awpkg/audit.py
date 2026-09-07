"""Audit-chain integration for AWPKG.

Wraps forge.security_events.write_event() when available; falls back to a
standalone append when the forge plugin is not on sys.path.  Either way the
output format is identical so verify_chain can read a mixed log.
"""
from __future__ import annotations

try:
    import fcntl
except ImportError:  # Windows — POSIX advisory locks unavailable; degrade to no-op
    import types as _types
    fcntl = _types.SimpleNamespace(  # type: ignore[assignment]
        LOCK_SH=1, LOCK_EX=2, LOCK_NB=4, LOCK_UN=8,
        flock=lambda *a, **k: None, lockf=lambda *a, **k: None,
    )
import hashlib
import json
import os
import sys
import time
from pathlib import Path
from typing import Any


def _audit_path(tenant_id: str = "_default") -> Path:
    """Get tenant-scoped audit trail file path.

    Args:
        tenant_id: Tenant identifier (default: "_default")

    Returns:
        Path to tenant audit.jsonl file

    Raises:
        ValueError: If tenant_id is invalid
    """
    # Explicit env override (for migration/testing)
    env = os.environ.get("VOICE_AUDIT_PATH") or os.environ.get("FORGE_AUDIT_PATH")
    if env:
        return Path(env)

    # THE tenant chain: <corvin_home>/tenants/<tid>/global/forge/audit.jsonl
    try:
        from core.paths import tenant_audit_chain
        return tenant_audit_chain(tenant_id)
    except (ImportError, ValueError):
        # Fallback for bootstrap or core.paths unavailable
        pass

    # Bootstrap fallback, before core.paths can be imported. R4: the tail is
    # ``global/forge/audit.jsonl`` — THE tenant chain — not ``<tenant>/audit.jsonl``,
    # which nothing verifies and no compliance report covers.
    _tail = ("tenants", tenant_id, "global", "forge", "audit.jsonl")
    corvin_home = os.environ.get("CORVIN_HOME")
    if corvin_home:
        return Path(corvin_home).joinpath(*_tail)
    here = Path(__file__).resolve()
    for parent in [here, *here.parents]:
        if (parent / ".corvin_repo").exists() or (parent / "plugins").is_dir():
            candidate = parent / ".corvin"
            return candidate.joinpath(*_tail)
    return Path.home().joinpath(".corvin", *_tail)


def _try_forge_write(event_type: str, *, tenant_id: str = "_default", **details: Any) -> bool:
    try:
        forge_root = Path(__file__).resolve().parents[3] / "forge" / "forge"
        if forge_root not in [Path(p) for p in sys.path]:
            sys.path.insert(0, str(forge_root.parent))
        from forge.security_events import write_event  # type: ignore[import]
        write_event(_audit_path(tenant_id), event_type, details=details)
        return True
    except Exception:
        return False


def _standalone_write(event_type: str, *, tenant_id: str = "_default", **details: Any) -> None:
    path = _audit_path(tenant_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    _SEVERITIES = {
        "package.installed": "INFO",
        "package.removed": "INFO",
        "package.install_denied": "WARNING",
        "package.inspect": "INFO",
    }
    severity = _SEVERITIES.get(event_type, "INFO")
    with open(path, "a+b") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            prev_hash = ""
            try:
                # FND-18: read the TRUE last chain record. The old 4 KB-tail read
                # missed the last line whenever the final record exceeded 4 KB →
                # a wrong/empty prev_hash → a broken chain link. Walk all lines
                # (the awpkg fallback chain is small) and keep the last valid hash.
                fh.seek(0)
                for line in fh.read().decode("utf-8", errors="replace").splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        prev_hash = json.loads(line).get("hash", prev_hash)
                    except Exception:
                        pass
            except Exception:
                pass
            # ADR-0129 — apply the metadata-only floor even on the forge-
            # absent fallback path, so this writer can't bypass it. If forge
            # is genuinely unimportable, inline a minimal denylist (fail-safe).
            _det = details
            try:
                from forge.security_events import filter_audit_details as _fad  # type: ignore
                _det, _ = _fad(details, event_type=event_type)
            except Exception:  # noqa: BLE001 — forge truly absent: minimal floor
                _bad = ("prompt", "output", "text", "secret", "password",
                        "token", "credential", "email", "body", "content")
                _det = {k: v for k, v in (details or {}).items()
                        if str(k).lower() not in _bad}
            record: dict[str, Any] = {
                "ts": time.time(),
                "event_type": str(event_type)[:128],
                "severity": severity,
                "run_id": "",
                "tool": "",
                "details": _det,
                "prev_hash": prev_hash,
            }
            canonical = json.dumps(record, sort_keys=True, separators=(",", ":"))
            record["hash"] = hashlib.sha256(
                (prev_hash + "\n" + canonical).encode()
            ).hexdigest()[:16]
            fh.seek(0, 2)
            fh.write((json.dumps(record, separators=(",", ":")) + "\n").encode("utf-8"))
            # FND-18: durability parity with the forge writer — fsync so a crash
            # after write() but before flush doesn't lose the event the unified
            # chain assumes is present.
            fh.flush()
            os.fsync(fh.fileno())
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def emit(event_type: str, *, tenant_id: str = "_default", **details: Any) -> None:
    """Emit an audit event into the tenant-scoped audit hash chain.

    Args:
        event_type: Type of audit event (e.g., "package.installed")
        tenant_id: Tenant identifier (default: "_default", keyword-only)
        **details: Additional event details (arbitrary key-value pairs)

    Raises:
        ValueError: If tenant_id is invalid
    """
    if not _try_forge_write(event_type, tenant_id=tenant_id, **details):
        _standalone_write(event_type, tenant_id=tenant_id, **details)
