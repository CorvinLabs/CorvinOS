"""Per-launch record: the cowork resolver vouches for a wildcard skill-forge server (RL-5).

The skill-forge MCP server reads its caller persona from its own environment. Until
2026-11-05 any NAMED persona without a ``persona_namespaces`` entry was the operator's
wildcard (force-promote, project/user create, organic grades), so ``delegate``, every
operator-created persona, and a server a worker started itself from Bash with
``CORVIN_CALLER_PERSONA=skill-forge`` all acted as the operator (refutation 2026-11-05,
RL-5/RL-6). Now:

* only the explicit launcher names in :data:`WILDCARD_PERSONAS` can be a wildcard, and
  only for a server whose ``CORVIN_SKILL_FORGE_LAUNCH`` nonce matches a record the
  resolver wrote under ``<tenant>/skill-forge/launches/`` before the spawn (0600; the
  server removes it at exit, the resolver prunes records older than :data:`TTL_S`);
* every other persona - ``delegate``, custom personas, a wildcard name without a record -
  is namespaced to its own name (``<persona>.``).

**Boundary (stated, not hidden).** This is ATTRIBUTION, not isolation. The nonce travels
in the MCP config the CLI receives and in the server's environment, and the record is a
file owned by the same OS user the worker runs as: a worker that deliberately reads the
nonce (``/proc/<pid>/environ``, the config file) or writes a record can still present
itself as the operator. ``path_gate`` denies the naive forms (starting the server from
Bash, writing under ``skill-forge/launches``); integrity against a deliberate same-user
attacker needs OS-level isolation of the worker (ADR-0241/0238).
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import time
from pathlib import Path
from typing import Callable, Optional

#: The env var carrying ``<tenant_id>:<nonce>`` on the resolver-launched server.
LAUNCH_ENV = "CORVIN_SKILL_FORGE_LAUNCH"
#: The only persona names that can be the operator's wildcard - and only with a record.
WILDCARD_PERSONAS = frozenset({"skill-forge", "forge"})
#: A record older than this no longer vouches for anything (and is pruned).
TTL_S = 24 * 3600.0


def launches_dir(tenant_home: Path) -> Path:
    return Path(tenant_home) / "skill-forge" / "launches"


def _record_path(tenant_home: Path, nonce: str) -> Path:
    key = hashlib.sha256(nonce.encode("utf-8")).hexdigest()[:32]
    return launches_dir(tenant_home) / f"{key}.json"


def prune(tenant_home: Path, *, now: Optional[float] = None) -> int:
    """Remove records older than :data:`TTL_S`; return how many."""
    d = launches_dir(tenant_home)
    if not d.is_dir():
        return 0
    now = time.time() if now is None else now
    removed = 0
    for rec in d.glob("*.json"):
        try:
            created = json.loads(rec.read_text(encoding="utf-8")).get("created")
        except FileNotFoundError:
            continue
        except (ValueError, AttributeError):
            created = None
        if not isinstance(created, (int, float)) or now - created > TTL_S:
            rec.unlink(missing_ok=True)
            removed += 1
    return removed


def issue(tenant_home: Path, tenant_id: str, persona: str, *, now: Optional[float] = None) -> str:
    """Write a launch record for ``persona`` and return the env value for :data:`LAUNCH_ENV`.

    Called by the resolver BEFORE the server is spawned. ``O_EXCL`` + 0600."""
    now = time.time() if now is None else now
    nonce = secrets.token_hex(16)
    d = launches_dir(tenant_home)
    d.mkdir(parents=True, exist_ok=True)
    prune(tenant_home, now=now)
    path = _record_path(tenant_home, nonce)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump({"persona": persona, "tenant_id": tenant_id, "created": now}, fh)
    return f"{tenant_id}:{nonce}"


def verify(value: str, persona: str, tenant_home_of: Callable[[str], Path], *,
           now: Optional[float] = None) -> Optional[Path]:
    """The record ``value`` names, when it vouches for ``persona``; else ``None``.

    ``tenant_home_of`` validates the tenant id (it raises on an invalid one). A record
    that is missing, unreadable, for another persona or tenant, or older than
    :data:`TTL_S` vouches for nothing."""
    if persona not in WILDCARD_PERSONAS or not isinstance(value, str) or ":" not in value:
        return None
    tenant_id, nonce = value.split(":", 1)
    if not nonce:
        return None
    try:
        home = Path(tenant_home_of(tenant_id))
    except (ValueError, TypeError):
        return None
    path = _record_path(home, nonce)
    try:
        rec = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(rec, dict):
        return None
    created = rec.get("created")
    now = time.time() if now is None else now
    if (rec.get("persona") != persona or rec.get("tenant_id") != tenant_id
            or not isinstance(created, (int, float)) or now - created > TTL_S):
        return None
    return path


def release(path: Optional[Path]) -> None:
    """Remove a record at server exit (a missing one is fine)."""
    if path is not None:
        Path(path).unlink(missing_ok=True)
