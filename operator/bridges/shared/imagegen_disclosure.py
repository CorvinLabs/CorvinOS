"""imagegen_disclosure.py — one-time, per-tenant disclosure gate for the
zero-config image-generation tool (ADR-0191).

Mirrors the STORAGE/AUDIT pattern of ``disclosure.py`` (Art. 50 bot-disclosure
card) but is deliberately a SEPARATE, simpler mechanism: the MCP tool call
that needs to disclose here only ever has a tenant_id (an MCP ``tools/call``
carries the arguments a model chose, not a messenger channel/chat/uid triple),
so this is tenant-scoped, not (channel, chat, uid)-scoped like the L19 card.
Reusing ``disclosure.py``'s store directly would conflate two different
consent records under one key space — kept apart on purpose.

Only Tier 0 (Pollinations.ai, a community-run third party with no contract)
triggers this — Tier 1 (the user's own configured OpenAI key) is a service
the user already explicitly opted into via BYOK, so it does not re-disclose.

Storage: one small JSON file per tenant at
``<corvin_home>/tenants/<tid>/global/imagegen-disclosure.json``::

    {"disclosed_at": 1783780250.5}

Lazy: nothing expires — this is a one-time-per-tenant contract, same shape
as the bot-disclosure card's one-time-per-uid contract.
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any

# Same shape contract as forge.tenants.validate_tenant_id (not importable
# from this MCP-subprocess context without sys.path surgery): tenant ids are
# path components here, so an unvalidated value would be a path-injection
# vector (and a ':'-in-path crasher on Windows).
_TENANT_ID_RE = re.compile(r"^[a-z0-9_][a-z0-9_-]{0,62}$")


def _safe_tenant(tenant_id: str) -> str:
    if not isinstance(tenant_id, str) or tenant_id.startswith("__") \
            or not _TENANT_ID_RE.match(tenant_id):
        return "_default"
    return tenant_id

# English per the repo language policy (user-facing runtime text defaults to
# English); the relaying assistant answers in the user's own language and
# will render this notice accordingly.
DISCLOSURE_TEXT = (
    "Image generation uses Pollinations.ai by default — a free, keyless "
    "community service. Your text prompt is sent to image.pollinations.ai "
    "for this. This notice appears only once. Configure your own OpenAI "
    "API key to use the paid OpenAI image service instead."
)


def _corvin_home() -> Path:
    """Same discovery order as disclosure.py's ``_corvin_home`` — env var
    first, then walk up from this file's own on-disk location looking for
    the repo marker. An MCP server subprocess is NOT guaranteed to inherit
    CORVIN_HOME from its spawning process (verified empirically: the `mcp`
    Python SDK's stdio client uses a curated default environment, not a
    full inherit-then-override — the `claude` CLI's own MCP spawn behavior
    is not something this module should have to assume either way), so the
    on-disk fallback is the primary mechanism, not a last resort."""
    env = os.environ.get("CORVIN_HOME")
    if env:
        return Path(os.path.expanduser(os.path.expandvars(env)))
    here = Path(__file__).resolve()
    for parent in [here, *here.parents]:
        if (parent / ".corvin_repo").exists() or (parent / "plugins").is_dir():
            return parent / ".corvin"
    return Path.home() / ".corvin"


def _store_path(tenant_id: str) -> Path:
    tenant_id = _safe_tenant(tenant_id)
    return _corvin_home() / "tenants" / tenant_id / "global" / "imagegen-disclosure.json"


def _audit_path(tenant_id: str) -> Path:
    tenant_id = _safe_tenant(tenant_id)
    return _corvin_home() / "tenants" / tenant_id / "global" / "forge" / "audit.jsonl"


def _load(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def has_disclosed(tenant_id: str) -> bool:
    """True iff Tier 0 has already been disclosed to this tenant once."""
    return "disclosed_at" in _load(_store_path(tenant_id))


def _audit(tenant_id: str) -> None:
    try:
        import sys
        here = Path(__file__).resolve()
        repo = None
        for parent in here.parents:
            if (parent / ".corvin_repo").exists() or (parent / "plugins").is_dir():
                repo = parent
                break
        if repo is not None:
            forge_pkg = repo / "operator" / "forge"
            if str(forge_pkg) not in sys.path:
                sys.path.insert(0, str(forge_pkg))
        from forge.security_events import write_event  # type: ignore
        write_event(_audit_path(tenant_id), "imagegen.disclosure_shown",
                    details={"tenant_id": tenant_id, "host": "image.pollinations.ai"})
    except Exception:  # noqa: BLE001 — best-effort, never blocks the tool call
        pass


def ensure_disclosed(tenant_id: str) -> str | None:
    """Return the disclosure text the FIRST time Tier 0 is used for this
    tenant, ``None`` on every call after that. Never raises — a storage
    failure degrades to "treat as not-yet-disclosed" (shows the text once
    more) rather than silently skipping disclosure forever."""
    path = _store_path(tenant_id)
    if has_disclosed(tenant_id):
        return None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"disclosed_at": time.time()}), encoding="utf-8")
        tmp.replace(path)
    except OSError:
        # Storage failure (read-only home, quota, ...) degrades to "shown
        # again next time" — the docstring's contract. Raising here would
        # fail the tool call AFTER work was done, over a bookkeeping write.
        pass
    _audit(tenant_id)
    return DISCLOSURE_TEXT
