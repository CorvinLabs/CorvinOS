"""MCP Plugin Manager — catalog storage (ADR-0096 M1).

Catalog location: <corvin_home>/tenants/<tid>/global/mcp-tools/catalog.json

Format::

    {
      "version": 1,
      "tools": {
        "brave-search": {
          "id": "brave-search",
          "source": "npm:@modelcontextprotocol/server-brave-search@0.6.2",
          "installed_at": "2026-06-07T10:23:00Z",
          "runtime": {"command": "npx", "args": ["-y", "..."]},
          "secrets": [{"name": "BRAVE_API_KEY", "vault_key": "brave_api_key",
                        "required": true}],
          "compliance": {"locality": "us_cloud", "network_egress": "required"}
        }
      }
    }
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
import json
import os
from pathlib import Path
from typing import Any


def _corvin_home() -> Path:
    h = os.environ.get("CORVIN_HOME")
    return Path(h).expanduser() if h else (Path.home() / ".corvin")


def catalog_dir(tid: str = "_default") -> Path:
    return _corvin_home() / "tenants" / tid / "global" / "mcp-tools"


def catalog_path(tid: str = "_default") -> Path:
    return catalog_dir(tid) / "catalog.json"


def load_catalog(tid: str = "_default") -> dict[str, Any]:
    path = catalog_path(tid)
    if not path.is_file():
        return {"version": 1, "tools": {}}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            fcntl.flock(fh, fcntl.LOCK_SH)
            try:
                data = json.load(fh)
            finally:
                fcntl.flock(fh, fcntl.LOCK_UN)
        if not isinstance(data, dict) or "tools" not in data:
            return {"version": 1, "tools": {}}
        return data
    except (json.JSONDecodeError, OSError):
        return {"version": 1, "tools": {}}


def save_catalog(tid: str, data: dict[str, Any]) -> None:
    d = catalog_dir(tid)
    d.mkdir(parents=True, exist_ok=True)
    path = catalog_path(tid)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            json.dump(data, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)
    tmp.replace(path)


def add_tool(tid: str, entry: dict[str, Any]) -> None:
    tool_id = entry["id"]
    data = load_catalog(tid)
    data["tools"][tool_id] = entry
    save_catalog(tid, data)


def remove_tool(tid: str, tool_id: str) -> bool:
    data = load_catalog(tid)
    if tool_id not in data["tools"]:
        return False
    del data["tools"][tool_id]
    save_catalog(tid, data)
    return True


def get_tool(tid: str, tool_id: str) -> dict[str, Any] | None:
    return load_catalog(tid)["tools"].get(tool_id)


def list_tools(tid: str = "_default") -> list[dict[str, Any]]:
    return list(load_catalog(tid)["tools"].values())
