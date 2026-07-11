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

import sys
from pathlib import Path

from . import activate as _activate
from . import catalog as _catalog
from . import compliance as _compliance

_SERVERS_DIR = Path(__file__).resolve().parents[1] / "servers"


def ensure_imagegen_zero_config(tid: str = "_default") -> dict:
    """Idempotently ensure the ADR-0191 zero-config image-generation tool
    is installed and tenant-active. Safe to call on every startup — a
    second call is a no-op (add_tool overwrites with identical content,
    activate() is already idempotent per tool_id).

    Returns a dict with keys: installed (bool), activated (bool), error
    (str | None) — never raises; a compliance/activation failure (e.g. a
    tenant whose egress policy now forbids these hosts) is reported, not
    thrown, since this runs on ordinary startup paths.
    """
    main_py = _SERVERS_DIR / "imagegen-zero-config" / "main.py"
    entry = {
        "id": "imagegen-zero-config",
        "source": f"builtin:{main_py}",
        "runtime": {"command": sys.executable, "args": [str(main_py)]},
        "secrets": [],
        "compliance": {
            "locality": "us_cloud",
            "network_egress": "required",
            "hosts": ["image.pollinations.ai", "api.openai.com"],
        },
    }
    _catalog.add_tool(tid, entry)
    try:
        _activate.activate(tid, "imagegen-zero-config", scope="tenant")
        return {"installed": True, "activated": True, "error": None}
    except _compliance.ComplianceError as e:
        return {"installed": True, "activated": False, "error": str(e)}
