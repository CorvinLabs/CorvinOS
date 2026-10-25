"""Helpers shared by the gap-closing proofs (security / settings wave).

* ``verify_tenant_chain`` runs the real ``security_events.verify_chain`` over a tenant's
  forge chain inside a sandbox child process.
* ``chain_events`` reads the tenant chain records (event_type + details).
* ``networked_server`` starts a console whose proxies are NOT dead, for the few proofs that
  must reach a real third party (GitHub). Everything else about the sandbox is unchanged.
"""
from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import Any

from .harness import Sandbox, Server

VERIFY_CHAIN = r"""
import json, sys
from pathlib import Path
from corvin_console import audit
ok, problems = audit._security_events.verify_chain(Path(sys.argv[1]))
print(json.dumps({"ok": bool(ok), "problems": [dict(p) for p in problems][:5]}))
"""


def tenant_chain(sb: Sandbox, tenant: str) -> Path:
    return sb.tenant_home(tenant) / "global" / "forge" / "audit.jsonl"


def verify_tenant_chain(sb: Sandbox, tenant: str) -> dict[str, Any]:
    chain = tenant_chain(sb, tenant)
    if not chain.exists():
        return {"ok": True, "problems": [], "empty": True}
    return json.loads(sb.run_child(VERIFY_CHAIN, str(chain)).strip().splitlines()[-1])


def chain_events(sb: Sandbox, tenant: str, event_type: str | None = None) -> list[dict[str, Any]]:
    chain = tenant_chain(sb, tenant)
    if not chain.exists():
        return []
    out = []
    for line in chain.read_text(encoding="utf-8").splitlines():
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if event_type is None or rec.get("event_type") == event_type:
            out.append(rec)
    return out


def action_records(sb: Sandbox, tenant: str, action: str) -> list[dict[str, Any]]:
    """Console action_* records of ``tenant`` whose ``details.action`` is ``action``."""
    return [r for r in chain_events(sb, tenant)
            if str(r.get("event_type", "")).startswith("console.action_")
            and (r.get("details") or {}).get("action") == action]


def networked_server(sb: Sandbox, name: str = "console-net") -> Server:
    """Console server with the sandbox's dead proxies removed (real outbound network)."""
    drop = {"HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"}
    env = {k: v for k, v in sb.env.items() if k not in drop}
    return Server(dataclasses.replace(sb, env=env), Server.CONSOLE, name)
