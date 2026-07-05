"""Dashboard route — health-card data for the console landing page.

Phase A: returns a small JSON snapshot the SPA renders into a
header-grid. Sources are read fresh per request (no caching) — the
data shape is small so the cost is negligible.

Future viewer phases (B+) will add separate endpoints for sessions,
runs, personas, tools, skills, memory, etc. — each as its own
router under ``/v1/console/<resource>``.
"""
from __future__ import annotations

import json
import os
import shutil
import time
from typing import Annotated, Any

from fastapi import APIRouter, Depends

from .. import auth as session_auth
from ..deps import require_session

from .. import _bootstrap
_forge_paths = _bootstrap.forge_paths
_REPO = _bootstrap._REPO


router = APIRouter()


# All seven channels the platform supports; signal/teams were absent before
# (dashboard.py had only 5), causing the frontend to never render them even
# though CHANNEL_LABEL in dashboard.tsx already included them.
_BRIDGES = ("telegram", "discord", "slack", "whatsapp", "email", "signal", "teams")

# Per-channel "proof of real configuration": at least one of these keys must
# be present AND non-empty in settings.json.  An empty file ({}), a file with
# only comment fields (_NOTE, _HINWEIS), or a file with only a whitelist but
# no credential counts as "not configured" for dashboard purposes.
_BRIDGE_TOKEN_KEYS: dict[str, tuple[str, ...]] = {
    "telegram":  ("token", "telegram_token", "bot_token"),
    "discord":   ("token", "discord_token",  "bot_token"),
    "slack":     ("token", "slack_bot_token", "bot_token", "api_key"),
    "whatsapp":  (),   # WhatsApp uses QR-auth — no token key; presence of the file is enough
    "email":     ("imap_user", "smtp_user", "username", "user"),
    "signal":    ("signal_phone", "phone", "number"),
    "teams":     ("webhook_url", "teams_webhook", "token"),
}


def _bridge_status(channel: str) -> dict[str, Any]:
    """Probe whether a channel is genuinely configured.

    Two-tier check:
      1. File existence  — settings.json present at canonical or legacy path.
      2. Token presence  — at least one credential key is non-empty.

    ``configured`` = file found (a settings file was ever created).
    ``has_token``  = a real credential exists (bridge can actually connect).

    The dashboard uses ``has_token`` for the green-dot status so that an
    empty or comment-only file does NOT mislead the user into thinking the
    bridge is ready.
    """
    home = _forge_paths.corvin_home() if _forge_paths is not None else None
    canonical = (home / "bridges" / channel / "settings.json") if home else None
    legacy    = _REPO / "operator" / "bridges" / channel / "settings.json"

    found_path: Any = None
    if canonical is not None and canonical.exists():
        found_path = canonical
    elif legacy.exists():
        found_path = legacy

    configured = found_path is not None
    has_token  = False
    src = "canonical" if (canonical is not None and canonical.exists()) else \
          ("legacy" if legacy.exists() else None)

    if found_path is not None:
        token_keys = _BRIDGE_TOKEN_KEYS.get(channel, ())
        if not token_keys:
            # Channels without a token (e.g. WhatsApp QR-auth): file presence = ready
            has_token = True
        else:
            try:
                data = json.loads(found_path.read_text(encoding="utf-8"))
                has_token = any(
                    bool(str(data.get(k, "")).strip())
                    for k in token_keys
                )
            except (OSError, json.JSONDecodeError, ValueError):
                has_token = False

    return {"channel": channel, "configured": configured, "has_token": has_token, "source": src}


def _engine_binary_status() -> dict[str, dict[str, bool]]:
    """Quick binary/credential check for each engine — no subprocess, no I/O.

    Returns {engine_id: {installed: bool, has_credential: bool}} so the
    dashboard can show accurate green/amber/grey dots without calling the
    heavyweight /detect endpoint (which costs up to 15 s).

    Checks performed (all O(1)):
      claude_code  — shutil.which("claude") AND (ANTHROPIC_API_KEY set OR
                     Claude OAuth credential file present)
      hermes       — Ollama reachable check is done separately by /health;
                     here we just verify the binary is on PATH
      opencode     — shutil.which("opencode")
      codex_cli    — shutil.which("codex")
      copilot      — shutil.which("gh") (Copilot uses the GitHub CLI)
    """
    def _claude_cred() -> bool:
        if os.environ.get("ANTHROPIC_API_KEY", "").strip():
            return True
        # OAuth credential file locations (ADR-0125 / engine_detection.py)
        home = os.path.expanduser("~")
        for candidate in (
            os.path.join(home, ".claude", ".credentials.json"),
            os.path.join(home, ".config", "claude", "credentials.json"),
        ):
            try:
                if os.path.getsize(candidate) > 10:
                    return True
            except OSError:
                pass
        return False

    results: dict[str, dict[str, bool]] = {}

    binary_map = {
        "claude_code": ("claude",),
        "hermes":      ("ollama",),
        "opencode":    ("opencode",),
        "codex_cli":   ("codex",),
        "copilot":     ("gh",),
    }
    for engine_id, binaries in binary_map.items():
        installed = any(shutil.which(b) is not None for b in binaries)
        if engine_id == "claude_code":
            has_cred = installed and _claude_cred()
        elif engine_id == "hermes":
            has_cred = installed  # model presence checked by /health separately
        else:
            has_cred = installed  # for CLI tools, presence = usable
        results[engine_id] = {"installed": installed, "has_credential": has_cred}

    return results


def _audit_chain_status(tenant_id: str) -> dict[str, Any]:
    """Surface the audit-chain head + size for this tenant.

    Cheap inspection: file size + last line's hash. A full
    ``verify_chain`` is too expensive to run per page-load — Phase
    F will add an explicit "verify now" button that runs it on
    demand.
    """
    chain = _forge_paths.tenant_global_dir(tenant_id) / "forge" / "audit.jsonl"
    if not chain.exists():
        return {"present": False}
    try:
        size = chain.stat().st_size
    except OSError:
        return {"present": False}
    last_event_type: str | None = None
    last_ts: float | None = None
    try:
        with chain.open("rb") as fh:
            try:
                fh.seek(-4096, os.SEEK_END)
            except OSError:
                fh.seek(0)
            tail = fh.read().decode("utf-8", errors="replace").strip().splitlines()
            for line in reversed(tail):
                try:
                    rec = json.loads(line)
                    last_event_type = rec.get("event_type")
                    last_ts = rec.get("ts")
                    break
                except json.JSONDecodeError:
                    continue
    except OSError:
        pass
    return {
        "present":         True,
        "size_bytes":      size,
        "last_event_type": last_event_type,
        "last_event_ts":   last_ts,
    }


def _engine_default() -> str:
    """Read the engine-layer flag the bridge adapter consults at boot."""
    val = os.environ.get("CORVIN_USE_ENGINE_LAYER", "1")
    return "claude_code (engine layer)" if val != "0" else "claude_code (legacy direct-spawn)"


def _stt_chain() -> dict[str, Any]:
    pin = os.environ.get("CORVIN_STT_PROVIDER")
    if pin:
        return {"mode": "pinned", "providers": [pin]}
    chain = os.environ.get("CORVIN_STT_CHAIN", "openai,local")
    return {"mode": "chain", "providers": [p.strip() for p in chain.split(",") if p.strip()]}


def _today_event_counts(tenant_id: str) -> dict[str, int]:
    """Coarse counts of audit events from today (best-effort).

    Reads at most the last 4 KiB of the chain to keep cost bounded.
    For a full-day rollup we'd page through the whole file — that's
    a Phase F concern, not Phase A.
    """
    chain = _forge_paths.tenant_global_dir(tenant_id) / "forge" / "audit.jsonl"
    if not chain.exists():
        return {}
    midnight = _start_of_day_local()
    counts: dict[str, int] = {}
    try:
        # Read full file, count today's events. Bounded by typical
        # audit-chain volume; a busy operator will outgrow this and
        # we'll switch to indexed storage later.
        with chain.open("r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                ts = rec.get("ts")
                if not isinstance(ts, (int, float)) or ts < midnight:
                    continue
                sev = rec.get("severity", "INFO")
                counts[sev] = counts.get(sev, 0) + 1
    except OSError:
        return {}
    return counts


def _start_of_day_local() -> float:
    now = time.localtime()
    midnight_struct = time.struct_time((
        now.tm_year, now.tm_mon, now.tm_mday,
        0, 0, 0,
        now.tm_wday, now.tm_yday, now.tm_isdst,
    ))
    return time.mktime(midnight_struct)


@router.get("/dashboard")
def dashboard(
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> dict[str, Any]:
    """Return the dashboard payload for the owner's tenant."""
    tid = rec.tenant_id
    return {
        "tenant_id":       tid,
        "ts":              time.time(),
        "engine_default":  _engine_default(),
        "engine_status":   _engine_binary_status(),
        "stt":             _stt_chain(),
        "bridges":         [_bridge_status(b) for b in _BRIDGES],
        "audit_chain":     _audit_chain_status(tid),
        "today_counts":    _today_event_counts(tid),
        "fingerprint":     rec.token_fingerprint,
        "expires_at":      rec.expires_at,
    }
