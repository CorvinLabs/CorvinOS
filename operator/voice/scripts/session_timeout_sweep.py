#!/usr/bin/env python3
"""session_timeout_sweep — daily cleanup of inactive bridge chats.

Walks ``<corvin_home>/tenants/<tid>/sessions/<bridge>:<chat>/`` and computes the
recursive newest mtime per session dir. Any session whose newest mtime
is older than ``--ttl-days`` (default 7, env override
``CORVIN_SESSION_TTL_DAYS``) is wiped via
``session_reset.reset_session(reason='timeout')``.

A second pass walks ``<corvin_home>/tenants/<tid>/sessions/voice/<channel>/<chat>/``
to catch voice-only orphans (chats with voice state but no forge dir of
the same safe-name).

Both roots are tenant-aware (ADR-0007 Phase 1.2). They were not until
2026-08-28: this script still walked the pre-migration layout, so the daily
timer fired, matched nothing and no chat was ever aged out.

User scope (``<corvin_home>/global/``) and project scope
(``<repo>/.corvin/``) are NEVER pruned.

Run by ``corvin-session-timeout.timer`` (daily at 03:30 local) or by
hand for a manual sweep:

  python3 operator/voice/scripts/session_timeout_sweep.py [--ttl-days N]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

# Wire the bridges/shared/ dir onto sys.path so we can import the reset
# module without copy-pasting its logic. Mirrors the layout the adapter
# uses to import its own siblings.
HERE = Path(__file__).resolve().parent
PLUGIN_ROOT = HERE.parent  # operator/voice/
# operator/bridges/shared/ lives next to operator/voice/, so step up once.
SHARED_DIR = PLUGIN_ROOT.parent / "bridges" / "shared"
sys.path.insert(0, str(SHARED_DIR))

from paths import corvin_home  # noqa: E402  (kept: used by the docstring's scope note)
from session_state import (  # noqa: E402
    has_claude_session_state, tenant_sessions_root,
)
from session_reset import (  # noqa: E402
    VALID_CHANNELS, reset_session,
)


def _forge_sessions_root() -> Path:
    """``<corvin_home>/tenants/<tid>/sessions/`` — the forge session workspaces.

    ADR-0007 Phase 1.2 moved these under the tenant home. This sweep kept
    walking the pre-migration ``<corvin_home>/sessions/`` until 2026-08-28,
    so the daily timer ran, found nothing, and no chat was ever aged out.
    """
    root = tenant_sessions_root(None)
    return root if root is not None else corvin_home() / "sessions"


def _voice_sessions_root() -> Path:
    """``<tenant_sessions>/voice/`` — the adapter's per-chat session dirs.

    Derived from the same SSOT the adapter and session_reset use, rather
    than the dead ``<corvin_home>/voice/sessions/`` this used to build.
    """
    return _forge_sessions_root() / "voice"


# Forge-side ``<channel>:<chat_id>`` syntax. Bridge name MUST start with a
# letter, then alnum / _ / - (matches the adapter's _build_spawn_env shape).
# The chat side is freer — anything goes after the first ':'.
_SESSION_RE = re.compile(r"^([a-zA-Z][a-zA-Z0-9_-]*):(.+)$")


def _newest_mtime(path: Path) -> float:
    """Return the recursive newest mtime under ``path``. Falls back to
    the stat of ``path`` itself when the dir is empty."""
    newest = 0.0
    try:
        newest = path.stat().st_mtime
    except FileNotFoundError:
        return 0.0
    try:
        for child in path.rglob("*"):
            try:
                m = child.stat().st_mtime
            except (FileNotFoundError, PermissionError):
                continue
            if m > newest:
                newest = m
    except (FileNotFoundError, PermissionError):
        pass
    return newest


def sweep_forge_sessions(*, ttl_seconds: float, dry_run: bool) -> list[dict]:
    """First pass: walk forge session workspaces."""
    sessions_root = _forge_sessions_root()
    purged: list[dict] = []
    if not sessions_root.is_dir():
        return purged
    now = time.time()
    for child in sorted(sessions_root.iterdir()):
        if not child.is_dir():
            continue
        m = _SESSION_RE.match(child.name)
        if not m:
            continue
        bridge, chat_id = m.group(1), m.group(2)
        if bridge not in VALID_CHANNELS:
            # Not a known bridge; leave it alone — could be a custom
            # integration we don't own.
            continue
        newest = _newest_mtime(child)
        age = now - newest if newest else 0.0
        if age <= ttl_seconds:
            continue
        info = {
            "bridge":   bridge,
            "chat_id":  chat_id,
            "age_days": round(age / 86400, 2),
            "session_dir": str(child),
        }
        if dry_run:
            info["dry_run"] = True
            purged.append(info)
            continue
        result = reset_session(
            channel=bridge, chat_id=chat_id, reason="timeout",
        )
        info["result"] = result
        # Audit-first invariant check: a None audit_event_id means the L16
        # hash-chain write failed before any rmtree was attempted.  This is a
        # CRITICAL failure — the reset may have proceeded without an audit
        # record, breaking tamper-evidence.
        if result.get("audit_event_id") is None:
            print(
                f"CRITICAL session_timeout_sweep: audit-first failed for "
                f"{bridge}:{chat_id} — reset_session returned no "
                f"audit_event_id; session reset may be unaudited",
                file=sys.stderr,
            )
            info["reset_failed"] = True
            info["reset_failure_reason"] = "audit_event_id_missing"
        # Surface any per-layer failures (skill-forge purge, forge purge,
        # voice-state purge, etc.) so the systemd journal captures them.
        failures = result.get("failures")
        if failures:
            print(
                f"WARNING session_timeout_sweep: partial failures resetting "
                f"{bridge}:{chat_id} — {failures}",
                file=sys.stderr,
            )
            info["reset_failed"] = True
            info["reset_failures"] = failures
        purged.append(info)
    return purged


def sweep_worker_sessions(*, ttl_seconds: float, dry_run: bool) -> list[dict]:
    """ADR-0049 third pass: prune stale worker_sessions/*.session.json files.

    Each file's age is determined by its ``last_resumed_at`` ISO-8601 field
    (falls back to file mtime when the field is absent or malformed).  A
    stale file is deleted individually; the parent session dir is NOT reset
    — that's the full-session sweep's job.  Emits ``worker_session.purged``
    audit events best-effort.
    """
    import json

    sessions_root = _forge_sessions_root()
    purged: list[dict] = []
    if not sessions_root.is_dir():
        return purged
    now = time.time()

    for child in sorted(sessions_root.iterdir()):
        if not child.is_dir():
            continue
        m = _SESSION_RE.match(child.name)
        if not m:
            continue
        bridge = m.group(1)
        if bridge not in VALID_CHANNELS:
            continue
        ws_dir = child / "worker_sessions"
        if not ws_dir.is_dir():
            continue

        for sf in sorted(ws_dir.glob("*.session.json")):
            scope_label = sf.stem.replace(".session", "")
            # Determine age from last_resumed_at field.
            last_resumed_ts = 0.0
            try:
                data = json.loads(sf.read_text("utf-8"))
                lr = data.get("last_resumed_at", "")
                if lr:
                    from datetime import datetime, timezone
                    dt = datetime.fromisoformat(lr)
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    last_resumed_ts = dt.timestamp()
            except Exception:  # noqa: BLE001
                pass
            if not last_resumed_ts:
                try:
                    last_resumed_ts = sf.stat().st_mtime
                except OSError:
                    continue
            age = now - last_resumed_ts
            if age <= ttl_seconds:
                continue

            info = {
                "scope_label": scope_label,
                "session_dir": str(child),
                "age_days":    round(age / 86400, 2),
                "session_file": str(sf),
            }
            if dry_run:
                info["dry_run"] = True
                purged.append(info)
                continue

            # Emit audit before delete (best-effort).
            try:
                sys.path.insert(0, str(SHARED_DIR))
                from audit import audit_event  # type: ignore[import-not-found]
                audit_event(
                    "worker_session.purged",
                    details={"scope_label": scope_label,
                             "chat_key": child.name},
                )
            except Exception:  # noqa: BLE001
                pass
            try:
                sf.unlink()
                info["removed"] = True
            except Exception as exc:  # noqa: BLE001
                info["error"] = str(exc)
            purged.append(info)

    return purged


def sweep_voice_orphans(
    *, ttl_seconds: float, dry_run: bool,
    purged_chans: set[str],
) -> list[dict]:
    """Second pass: voice-only orphans — voice state present, no matching
    forge session dir."""
    voice_root = _voice_sessions_root()
    purged: list[dict] = []
    if not voice_root.is_dir():
        return purged
    now = time.time()
    for chan_dir in sorted(voice_root.iterdir()):
        if not chan_dir.is_dir():
            continue
        bridge = chan_dir.name
        if bridge not in VALID_CHANNELS:
            continue
        for chat_dir in sorted(chan_dir.iterdir()):
            if not chat_dir.is_dir():
                continue
            chat_id = chat_dir.name
            tag = f"{bridge}:{chat_id}"
            if tag in purged_chans:
                # Already handled in pass 1 (we wiped voice state via
                # reset_session above).
                continue
            newest = _newest_mtime(chat_dir)
            age = now - newest if newest else 0.0
            if age <= ttl_seconds:
                continue
            # A reset clears the conversation state but deliberately keeps the
            # chat's project files, so this directory survives it. Without this
            # guard the sweep would re-purge the same aged-out chat every night
            # — its mtime stays old — and write a fresh session.timeout event
            # into the audit chain each time. Nothing left to resume from means
            # nothing left to time out.
            if not has_claude_session_state(chat_dir):
                continue
            info = {
                "bridge":  bridge,
                "chat_id": chat_id,
                "age_days": round(age / 86400, 2),
                "voice_dir": str(chat_dir),
            }
            if dry_run:
                info["dry_run"] = True
                purged.append(info)
                continue
            # No forge session dir — but we still emit the audit event
            # and remove the voice state. reset_session() handles the
            # missing-dirs case gracefully.
            result = reset_session(
                channel=bridge, chat_id=chat_id, reason="timeout",
            )
            info["result"] = result
            # Mirror the failure-checking logic from sweep_forge_sessions() so
            # audit-first failures are surfaced identically in both sweep paths.
            if result.get("audit_event_id") is None:
                import sys as _sys
                _failures = result.get("failures", [])
                print(
                    f"CRITICAL session_timeout_sweep: audit-first failure for "
                    f"voice orphan {bridge}:{chat_id} — "
                    f"reset blocked. failures={_failures}",
                    file=_sys.stderr, flush=True,
                )
                info["reset_failed"] = True
            purged.append(info)
    return purged


def _resolve_ttl_env() -> str | None:
    """Return CORVIN_SESSION_TTL_DAYS env override, or None if unset."""
    return os.environ.get("CORVIN_SESSION_TTL_DAYS")


def main(argv: list[str] | None = None) -> int:
    default_ttl = float(_resolve_ttl_env() or "7")
    ap = argparse.ArgumentParser(prog="session_timeout_sweep")
    ap.add_argument("--ttl-days", type=float, default=default_ttl,
                    help="age threshold (days). default=7, env "
                    "CORVIN_SESSION_TTL_DAYS overrides")
    ap.add_argument("--dry-run", action="store_true",
                    help="list what would be reset, no rmtree")
    args = ap.parse_args(argv)

    ttl_seconds = args.ttl_days * 86400
    forge_purged = sweep_forge_sessions(
        ttl_seconds=ttl_seconds, dry_run=args.dry_run,
    )
    purged_tags = {f"{p['bridge']}:{p['chat_id']}" for p in forge_purged}
    voice_purged = sweep_voice_orphans(
        ttl_seconds=ttl_seconds, dry_run=args.dry_run,
        purged_chans=purged_tags,
    )
    # ADR-0049: fine-grained worker_session prune by last_resumed_at.
    worker_session_purged = sweep_worker_sessions(
        ttl_seconds=ttl_seconds, dry_run=args.dry_run,
    )
    summary = {
        "ttl_days":             args.ttl_days,
        "dry_run":              args.dry_run,
        "forge_purged":         forge_purged,
        "voice_purged":         voice_purged,
        "worker_session_purged": worker_session_purged,
        "audit_event_type":     "session.timeout",
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
