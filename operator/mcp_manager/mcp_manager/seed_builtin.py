"""seed_builtin.py — one-time, idempotent catalog seeding for tools that ship
WITH CorvinOS itself, rather than being installed by a user via `corvin-mcp
install <source>` (ADR-0191).

The generic `local:` installer (installer.py::_install_local) reads
mcp-tool.yaml and only rewrites `runtime.command` to an absolute path when
it's not a system command — it does NOT rewrite entries inside
`runtime.args`, so a manifest-declared relative arg like `main.py` breaks the
moment the spawning `claude` process's cwd isn't this tool's own directory
(which it usually isn't — cwd is the chat session's working directory).
Builtin, first-class tools therefore register their catalog entry directly
here, with an absolute `args` path resolved from THIS module's own on-disk
location — portable across any install location (git checkout, editable
install, or a packaged site-packages copy), unlike a path baked into a
static YAML file.

Command resolution (found the hard way, live-testing ADR-0191): a bare
``"python3"`` command is resolved via the SPAWNING process's own PATH, not
this process's — ``claude``'s child-process environment does not
necessarily prioritize the venv this package is actually installed into
(confirmed: it resolved to the system ``/usr/bin/python3``, which lacks the
``mcp``/``httpx`` dependencies, and the MCP connection failed silently at
subprocess-import time with no useful diagnostic surfaced to the chat).
``sys.executable`` — THIS process's own interpreter, guaranteed to have
every base dependency installed since it's the same package install — is
the portable fix, mirroring why forge/skill_forge's catalog entries use an
absolute interpreter path rather than a bare command name.

No ``secrets`` entry (found the same way): ``get_active_mcp_servers()``
unconditionally turns any declared secret into ``env: {"NAME": "${NAME}"}``
in the materialized mcp-config — but the ``claude`` CLI does NOT resolve
``${VAR}`` templates in MCP server env values (confirmed live: the
pre-existing persona-hardcoded `imagegen` server hits this same wall, its
own OpenAI call failing with the literal string
``${OPENAI*****KEY}`` as the bearer token). Declaring the secret here would
inject that literal unresolved string as `OPENAI_API_KEY` into THIS
server's own environment, which `provider_keys.resolve_key()` would then
treat as a genuinely-configured key (truthy, non-empty) and try to use for
Tier 1 — always failing with 401 instead of correctly falling back to
Tier 0. Unlike the third-party `imagegen` package (which only knows how to
read env vars), this server is first-party Python and calls
`provider_keys.resolve_key("openai_api_key")` directly, which already
reads `service.env` from disk — no env-var passthrough needed at all.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from . import activate as _activate
from . import catalog as _catalog
from . import compliance as _compliance

_SERVERS_DIR = Path(__file__).resolve().parents[1] / "servers"

# Bump when the seeded entry's SHAPE changes in a way that must be re-applied
# to existing installs (new compliance hosts, new env contract, ...). A pure
# path change (new venv after upgrade) is handled separately below and does
# NOT need a bump.
_SEED_VERSION = 2


def _marker_path(tid: str) -> Path:
    return _catalog.catalog_dir(tid) / "builtin-seeded.json"


def _load_marker(tid: str) -> dict:
    try:
        data = json.loads(_marker_path(tid).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _write_marker(tid: str, tool_id: str) -> None:
    marker = _load_marker(tid)
    marker[tool_id] = {"seed_version": _SEED_VERSION}
    path = _marker_path(tid)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(marker), encoding="utf-8")
    tmp.replace(path)


def ensure_imagegen_zero_config(tid: str = "_default") -> dict:
    """Idempotently ensure the ADR-0191 zero-config image-generation tool
    is installed and tenant-active — while RESPECTING operator intent:

    - First-ever seed (no marker): install + tenant-activate + write marker.
    - Marker present, entry present: refresh ONLY the runtime command/args/env
      (they embed absolute interpreter/venv paths that go stale on upgrade)
      unless an operator edited them; never touch activation state — a user
      who ran ``corvin-mcp deactivate`` stays deactivated (adversarial-review
      finding: the previous unconditional add_tool+activate silently undid
      both user deactivation and operator catalog edits on every boot).
    - Marker present, entry deleted: the operator uninstalled it — do nothing.
    - Marker seed_version older than _SEED_VERSION: re-apply the full entry
      (but still never force re-activation of a deactivated tool).

    Returns a dict with keys: installed (bool), activated (bool), error
    (str | None) — never raises; a compliance/activation failure (e.g. a
    tenant whose egress policy now forbids these hosts) is reported, not
    thrown, since this runs on ordinary startup paths.
    """
    tool_id = "imagegen-zero-config"
    main_py = _SERVERS_DIR / tool_id / "main.py"
    runtime = {
        "command": sys.executable,
        "args": [str(main_py)],
        # Threaded through explicitly: an MCP subprocess is not guaranteed to
        # inherit these, and both disclosure state and L44 tenant overlays
        # resolve through them (reader≠writer class otherwise).
        "env": {
            "CORVIN_HOME": str(_catalog._corvin_home()),
            "CORVIN_TENANT_ID": tid,
        },
    }
    entry = {
        "id": tool_id,
        "source": f"builtin:{main_py}",
        "runtime": runtime,
        "secrets": [],
        "compliance": {
            "locality": "us_cloud",
            "network_egress": "required",
            "hosts": ["image.pollinations.ai", "api.openai.com"],
        },
    }

    marker = _load_marker(tid).get(tool_id)
    existing = _catalog.get_tool(tid, tool_id)

    if marker and existing is None:
        # Seeded once, later uninstalled by the operator — respect that.
        return {"installed": False, "activated": False, "error": None}

    if marker and existing is not None and marker.get("seed_version") == _SEED_VERSION:
        # Already seeded at this shape: refresh stale interpreter/venv paths
        # (upgrade case) but preserve every operator edit and the activation
        # state. Only rewrite when the recorded interpreter/script no longer
        # exists — an operator who pointed the entry elsewhere on purpose
        # keeps their edit.
        rt = existing.get("runtime") or {}
        cmd = rt.get("command", "")
        args = rt.get("args") or []
        stale = (not os.path.exists(cmd)) or any(
            isinstance(a, str) and a.endswith("main.py") and not os.path.exists(a)
            for a in args
        )
        if stale:
            existing["runtime"] = runtime
            _catalog.add_tool(tid, existing)
        return {"installed": True, "activated": False, "error": None}

    # First seed for this tenant, or seed-shape upgrade.
    if existing is not None and marker is None:
        # Pre-marker install (0.10.27 gateway seeding): adopt it without
        # re-activating; activation state is whatever the operator left.
        _catalog.add_tool(tid, entry)
        _write_marker(tid, tool_id)
        return {"installed": True, "activated": False, "error": None}

    _catalog.add_tool(tid, entry)
    activated = False
    error: str | None = None
    if marker is None:
        try:
            _activate.activate(tid, tool_id, scope="tenant")
            activated = True
        except _compliance.ComplianceError as e:
            error = str(e)
    _write_marker(tid, tool_id)
    return {"installed": True, "activated": activated, "error": error}
