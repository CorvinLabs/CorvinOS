"""Compute-worker socket discovery (ADR-0013 Phase 13.5).

The Forge MCP server polls this on every ``tools/list`` to decide
whether to advertise the four ``compute_*`` MCP tools.

Discovery semantics:
1. Resolve current tenant via :func:`forge.tenants.current_tenant`.
2. Compute expected socket path:
   ``<corvin_home>/tenants/<tid>/compute/worker.sock``.
3. Non-blocking connect with 100 ms timeout.
4. Cache the result for 5 s — scraping more often than that burns the
   connect syscall on every list-tools poll.
5. On the first miss per process: emit ``compute.worker_unreachable``
   (WARNING) into the unified hash chain. Subsequent misses within the
   process are silent.

The module is intentionally lightweight — only stdlib imports. It is
imported eagerly by ``mcp_server.py`` so the discovery path is in
place from boot, even when the plugin isn't bootstrapped.
"""
from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Callable

from .paths import corvin_home
from .tenants import current_tenant

log = logging.getLogger(__name__)


_CACHE_TTL_S = 5.0
_CONNECT_TIMEOUT_S = 0.1

# Per-(corvin_home, tenant_id, socket_path) cached probe result.
_cache: dict[tuple[str, str, str], tuple[float, bool]] = {}
_cache_lock = threading.Lock()

# Tenants we already audited a `compute.worker_unreachable` for in this
# process — one-shot dedup per ADR §G.
_audited_unreachable: set[tuple[str, str]] = set()


def socket_path_for(tenant_id: str | None = None) -> Path:
    """Return the canonical socket path for ``tenant_id``."""
    tid = tenant_id or current_tenant()
    return corvin_home() / "tenants" / tid / "compute" / "worker.sock"


def _probe(path: Path) -> bool:
    # ADR-0159 M4: use bridge_transport for cross-platform probe (AF_UNIX on
    # Linux/macOS, TCP loopback on Windows when worker.sock.port exists).
    try:
        import sys as _sys
        import os as _os
        # parents[2] is the `operator` dir (operator/forge/forge/_compute_discovery.py);
        # parents[3] overshot to the repo root and omitted `operator`, so this path
        # never existed in source OR wheel (path-audit 2026-06-25 #LOW4).
        _shared = str(Path(__file__).resolve().parents[2] / "bridges" / "shared")
        if _shared not in _sys.path:
            _sys.path.insert(0, _shared)
        from bridge_transport import probe_socket  # type: ignore
        return probe_socket(path, timeout=_CONNECT_TIMEOUT_S)
    except ImportError:
        # Fallback: Unix-only probe (pre-M4 behaviour on non-Windows).
        import socket as _socket
        if not path.exists():
            return False
        if not hasattr(_socket, "AF_UNIX"):
            return False
        s = _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM)  # type: ignore[attr-defined]
        s.settimeout(_CONNECT_TIMEOUT_S)
        try:
            s.connect(str(path))
            return True
        except (OSError, _socket.timeout):
            return False
        finally:
            try:
                s.close()
            except OSError:
                pass


def is_worker_reachable(
    tenant_id: str | None = None,
    *,
    audit_emit: Callable[..., None] | None = None,
    socket_path: Path | None = None,
) -> bool:
    """Return True iff a compute worker is listening for ``tenant_id``.

    Cached for :data:`_CACHE_TTL_S` seconds per (corvin_home, tenant,
    socket-path). ``audit_emit`` is optional — when present, the first
    miss per (tenant, socket) emits
    ``compute.worker_unreachable`` (WARNING).
    """
    tid = tenant_id or current_tenant()
    path = Path(socket_path) if socket_path else socket_path_for(tid)
    home_key = str(corvin_home())
    key = (home_key, tid, str(path))
    now = time.monotonic()
    with _cache_lock:
        entry = _cache.get(key)
        if entry is not None and (now - entry[0]) < _CACHE_TTL_S:
            return entry[1]
    reachable = _probe(path)
    with _cache_lock:
        _cache[key] = (now, reachable)
    if not reachable and audit_emit is not None:
        miss_key = (tid, str(path))
        with _cache_lock:
            already = miss_key in _audited_unreachable
            if not already:
                _audited_unreachable.add(miss_key)
        if not already:
            try:
                audit_emit(
                    "compute.worker_unreachable",
                    details={
                        "tenant_id":        tid,
                        "attempted_socket": str(path),
                    },
                )
            except Exception:  # noqa: BLE001
                log.exception("audit emit failed for compute.worker_unreachable")
    return reachable


def reset_caches_for_tests() -> None:
    """Test helper — clear discovery + audit-dedup caches."""
    with _cache_lock:
        _cache.clear()
        _audited_unreachable.clear()
