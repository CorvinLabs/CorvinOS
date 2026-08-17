"""corvin-console — owner-self-service web UI for Corvin.

Owner-tier (single-tenant, semantic / "what is my machine doing").

Mounted onto the gateway's ASGI app under:
  * /v1/console/* (REST API)
  * /console/*    (React SPA — web-next/dist)

See README.md for the architecture summary and the
``outputs/corvin-console-konzept.md`` document for the full
design rationale.
"""
from __future__ import annotations


# The operator-dependency bootstrap (wincompat shim → operator/ subtrees on
# sys.path → source-tree injection) moved to corvin_core (ADR-0352 P2.3) — it is an
# OS-kernel concern, not a Console one, and headless mode needs it without the
# Console. Importing corvin_core runs it, in the same critical order, and MUST be
# the first import here so forge/etc. resolve before any submodule loads.
import corvin_core as _core  # noqa: F401

# Backward-compat: existing route modules still do `from .._bootstrap import
# forge_paths` / `from .._wincompat import …`. Alias those names to the real
# corvin_core modules so they keep resolving through corvin_console.*.
import sys as _sys  # noqa: E402
from corvin_core import (  # noqa: E402
    _operator_bootstrap as _ob, _wincompat as _wc, _bootstrap as _bs,
)
_sys.modules[__name__ + "._operator_bootstrap"] = _ob
_sys.modules[__name__ + "._wincompat"] = _wc
_sys.modules[__name__ + "._bootstrap"] = _bs

__version__ = "0.1.4"
