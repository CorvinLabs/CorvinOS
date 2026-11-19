"""Resolve a registered DSI v1 connection into driver environment variables.

ONE implementation shared by every consumer of a connection's credentials:

* the compute runner (``corvin_compute.cli`` - a forged tool run with ``datasources``), and
* the chat tool ``datasource_query`` (forge MCP server).

Credentials: a connection manifest stores only the NAMES of its secrets
(``secrets: ["PGUSER", "PGPASSWORD"]``). The values live in the TENANT's forge
secret vault (``forge.secret_vault.tenant_vault_path``), which is where the
Databases form's ``POST /data-sources/_secrets/{NAME}`` writes them. A secret
is released only to the tenant that stored it and only for the target (host)
it is bound to.

Fail-CLOSED: a declared connection that cannot be resolved (bad name, missing or
malformed file, missing secret) raises ``ValueError`` - callers must never run
with a half-built environment.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

# A DSI connection name is a single path segment - no traversal, no
# separators, no leading dot. Mirrors the L38 attachment-name guard.
DATASOURCE_NAME_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9._-]{0,127}$")


def connections_dir(corvin_home: Any, tenant_id: str) -> Path:
    return (Path(corvin_home) / "tenants" / tenant_id / "datasource_connections").resolve()


def load_connection(corvin_home: Any, tenant_id: str, name: str) -> dict[str, Any]:
    """The raw manifest JSON of connection *name* (strict name + containment check)."""
    if (not isinstance(name, str) or not DATASOURCE_NAME_RE.match(name)
            or ".." in name or "/" in name or "\\" in name):
        raise ValueError(f"invalid datasource name: {name!r}")
    conn_dir = connections_dir(corvin_home, tenant_id)
    path = (conn_dir / f"{name}.json").resolve()
    if conn_dir not in path.parents:
        raise ValueError(f"datasource path escaped connections dir: {name!r}")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"declared datasource {name!r} not resolvable: {exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError(f"declared datasource {name!r} not resolvable: manifest is not an object")
    return raw


# Names a manifest may never have injected into a driver process, whatever the
# vault holds: Corvin's own credentials and the process environment. The console
# refuses to STORE these; a manifest written by hand or by ``datasource_connect``
# could still NAME them (review 2026-11-19, R3-NEW-3).
_RESERVED_EXACT = frozenset({
    "PATH", "HOME", "USER", "LOGNAME", "SHELL", "PWD", "TERM", "LANG", "TMPDIR", "DISPLAY",
    "IFS", "ENV", "EDITOR", "PAGER", "HOSTALIASES",
})
_RESERVED_PREFIX = (
    "ANTHROPIC", "OPENAI", "OPENROUTER", "OLLAMA", "CORVIN", "CLAUDE", "FORGE_", "XDG_",
    "LD_", "PYTHON", "DYLD_", "NODE_", "BASH_", "GIT_", "SSH_", "LC_",
)
_RESERVED_PARTS = ("AUDIT", "VAULT", "POLICY", "LICENSE")
_SECRET_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]{1,63}$")


def secret_name_reserved(name: str) -> bool:
    """True when *name* must never be injected as a datasource credential."""
    upper = str(name).upper()
    return (
        not _SECRET_NAME_RE.match(str(name))
        or upper in _RESERVED_EXACT
        or upper.startswith(_RESERVED_PREFIX)
        or any(part in upper for part in _RESERVED_PARTS)
    )


def binding_target(raw: dict[str, Any]) -> str:
    """The target a connection's secrets are bound to: its host, else its adapter."""
    cfg = raw.get("config") if isinstance(raw.get("config"), dict) else {}
    for key in ("host", "hostname", "server", "endpoint_url", "endpoint", "account", "url"):
        value = cfg.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip().lower().rstrip(".")
    return "adapter:" + str(raw.get("adapter", "")).strip().lower()


def env_for_connection(
    raw: dict[str, Any], name: str = "", *, tenant_id: str | None = None,
) -> dict[str, str]:
    """Driver env for an already-loaded manifest (credentials from the vault).

    Secrets come from *tenant_id*'s own vault only (default: the process tenant),
    never from another tenant's, and only for the target the secret is bound to.
    """
    try:
        from forge import secret_vault as _vault  # type: ignore[import]
    except Exception:  # noqa: BLE001
        _vault = None  # type: ignore[assignment]

    env: dict[str, str] = {}
    adapter = str(raw.get("adapter", "")).lower()
    cfg = raw.get("config") or {}
    if adapter in ("postgresql", "postgres"):
        if cfg.get("host"):
            env["PGHOST"] = str(cfg["host"])
        if cfg.get("port"):
            env["PGPORT"] = str(cfg["port"])
        db = cfg.get("dbname") or cfg.get("database")
        if db:
            env["PGDATABASE"] = str(db)
        if cfg.get("user"):
            env["PGUSER"] = str(cfg["user"])
    elif adapter in ("mysql", "mariadb"):
        if cfg.get("host"):
            env["MYSQL_HOST"] = str(cfg["host"])
        if cfg.get("port"):
            # The adapter reads MYSQL_PORT, the mysql client MYSQL_TCP_PORT: set both.
            env["MYSQL_PORT"] = str(cfg["port"])
            env["MYSQL_TCP_PORT"] = str(cfg["port"])
        db = cfg.get("dbname") or cfg.get("database")
        if db:
            env["MYSQL_DATABASE"] = str(db)
        if cfg.get("user"):
            env["MYSQL_USER"] = str(cfg["user"])
    # Vault-sourced secrets (passwords / tokens): NAMES only on disk.
    declared = raw.get("secrets") or []
    if not isinstance(declared, list) or any(not isinstance(s, str) for s in declared):
        raise ValueError(f"datasource {name!r}: 'secrets' must be a list of names")
    secrets = list(dict.fromkeys(declared))
    if secrets:
        reserved = [s for s in secrets if secret_name_reserved(s)]
        if reserved:
            raise ValueError(
                f"datasource {name!r} names reserved secret(s) {reserved}: process and "
                "Corvin credentials are never injected into a driver")
        if _vault is None:
            raise ValueError(
                f"datasource {name!r} declares secrets {secrets} but the forge vault is unavailable")
        try:
            tid = tenant_id if tenant_id is not None else _vault.process_tenant_id()
            resolved, missing = _vault.resolve_secrets(
                secrets, tenant_id=tid, bind_to=binding_target(raw))
        except _vault.VaultError as exc:  # includes SecretBindingError
            raise ValueError(f"datasource {name!r} secret(s) not released: {exc}") from exc
        if missing:
            raise ValueError(f"datasource {name!r} secret(s) missing from vault: {missing}")
        env.update({k: v for k, v in resolved.items() if isinstance(v, str)})
    return env


def resolve_connection_env(corvin_home: Any, tenant_id: str, names: list[str]) -> dict[str, str]:
    """Merged driver env for each of *names* (the compute runner's entry point)."""
    env: dict[str, str] = {}
    for name in names:
        raw = load_connection(corvin_home, tenant_id, name)
        env.update(env_for_connection(raw, name, tenant_id=tenant_id))
    return env
