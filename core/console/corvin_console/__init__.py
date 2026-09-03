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
import sys as _sys  # noqa: E402

try:
    import corvin_core as _core  # noqa: F401
    from corvin_core import (  # noqa: E402
        _operator_bootstrap as _ob, _wincompat as _wc, _bootstrap as _bs,
    )
except ImportError as _e:
    # corvin_core not available in development environment; create stub modules
    # so backward-compat imports fail immediately (not silently) with ImportError
    raise ImportError(
        "corvin_core import failed; backward-compat aliases unavailable. "
        "Development environment must have corvin_core on path."
    ) from _e

# Backward-compat: existing route modules still do `from .._bootstrap import
# forge_paths` / `from .._wincompat import …`. Alias those names to the real
# corvin_core modules so they keep resolving through corvin_console.*.
_sys.modules[__name__ + "._operator_bootstrap"] = _ob
_sys.modules[__name__ + "._wincompat"] = _wc
_sys.modules[__name__ + "._bootstrap"] = _bs



def _resolve_version() -> str:
    """The installed distribution's version, or the checkout's pyproject one.

    A hardcoded literal here drifted to "0.1.4" while pyproject.toml said
    1.0.0, so /v1/console/healthz reported a version no release ever had
    (2026-09-03 review, F8). Wheel/editable installs carry dist metadata; a
    bare checkout on sys.path without an install falls back to parsing the
    `version = "..."` line of the repo's pyproject.toml.
    """
    try:
        from importlib.metadata import version as _dist_version

        return _dist_version("corvinos")
    except Exception:  # noqa: BLE001 — PackageNotFoundError or broken metadata
        pass
    try:
        import re as _re
        from pathlib import Path as _Path

        _pyproject = _Path(__file__).resolve().parents[3] / "pyproject.toml"
        _m = _re.search(r'^version\s*=\s*"([^"]+)"', _pyproject.read_text(encoding="utf-8"), _re.M)
        if _m:
            return _m.group(1)
    except Exception:  # noqa: BLE001
        pass
    return "0.0.0+unknown"


__version__ = _resolve_version()
