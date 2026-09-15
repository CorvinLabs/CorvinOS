#!/usr/bin/env python3
"""auth_elevation_gate.py — PreToolUse hook that gates destructive MCP
tools behind PIN-elevation (layer-16 v2 hardening, Phase 3 E).

Runs on the matcher
``mcp__forge__forge_promote|mcp__skill_forge__skill_promote``.
The gate is fail-OPEN by default in two cases:

  1. The bridge isn't the caller (no ``CORVIN_CHANNEL_ID`` in env).
     A bare ``claude`` CLI invocation by the operator stays unblocked —
     PIN-elevation is a bridge feature, not a global lock.
  2. The auth_elevation module is missing (e.g. forge-only deployment
     without voice). Same graceful-fallback policy as the cowork /
     skill-inject layers.

When the bridge IS the caller, ``CORVIN_CHANNEL_ID`` is set and the
chat is NOT elevated, the hook denies with exit 2 and an informative
deny message instructing the user to run ``/auth-up <pin>``.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

HOOK_DIR = Path(__file__).resolve().parent
# operator/voice/hooks → operator/voice → operator → operator/bridges/shared
SHARED_DIR = HOOK_DIR.parent.parent / "bridges" / "shared"


def _load_auth_elevation():
    if str(SHARED_DIR) not in sys.path:
        sys.path.insert(0, str(SHARED_DIR))
    try:
        import auth_elevation  # type: ignore
        return auth_elevation
    except Exception:
        return None


def check(payload: dict) -> tuple[bool, str]:
    """Return (allow, reason). allow=False denies with the stderr message."""
    tool = payload.get("tool_name", "")
    chat_key = os.environ.get("CORVIN_CHANNEL_ID") or ""

    # Fail-open: bare CLI invocation (no bridge channel context).
    if not chat_key:
        return True, ""

    auth = _load_auth_elevation()
    if auth is None:
        return True, "auth-elevation-module-unavailable"
    if not auth.needs_elevation(tool):
        return True, ""
    if auth.is_elevated(chat_key):
        return True, "elevated"

    # Audit + deny.
    try:
        auth._audit(  # noqa: SLF001 — internal best-effort audit helper
            "auth.elevation_required",
            channel="bridge",
            chat_key=chat_key,
            details={"tool": tool, "reason": "gate-blocked"},
        )
    except Exception:
        pass

    return False, (
        f"auth_elevation: {tool} requires PIN-elevation. Run "
        f"`/auth-up <pin>` in this chat first (10 minutes by default), "
        f"then retry the tool call. Use /auth-down to drop elevation early."
    )


def main() -> int:
    raw = sys.stdin.read()
    if not raw.strip():
        return 0
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return 0
    if not isinstance(payload, dict):
        return 0
    allow, reason = check(payload)
    if allow:
        return 0
    print(reason, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
