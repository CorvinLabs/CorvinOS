"""session_reset — wipe everything bound to one bridge chat.

Layer 8 of the voice/cowork/forge/skill-forge stack: a single, idempotent
operation that purges the four cleanup layers a chat owns:

  1. SkillForge skills in the **session** scope (canonical workspace
     SKILL.md/meta.json + plugin-slot mirror under
     ``operator/skill-forge/skills/dyn/``).
  2. Forge tools in the **session** scope (manifest + impl files).
  3. Forge session workspace dir at
     ``<corvin_home>/sessions/<channel>:<chat>/`` — defensive rmtree.
  4. Voice conversation state at
     ``<corvin_home>/voice/sessions/<safe_channel>/<safe_chat>/`` —
     ``.claude.json``, ``.claude/`` and any session-scoped Claude state.

The audit event lands FIRST so the on-disk action is always traceable
even if the rmtree later fails for any reason.

Public surface:
    reset_session(channel, chat_id, repo_root=None, reason='manual')
        Returns a dict; never raises on missing paths; idempotent.

CLI:
    python3 session_reset.py --channel <c> --chat-id <id> [--repo-root P]
                             [--reason manual|timeout]
    Prints a single JSON document; exit 0 always.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
from pathlib import Path
from typing import Any

# Make the forge + skill-forge top dirs importable without polluting the
# parent process's sys.path beyond this module — mirror the pattern used
# in operator/forge/scripts/forge_cleanup.py.
HERE = Path(__file__).resolve().parent
# bridges/shared → bridges → operator/. forge + skill-forge live here.
PLUGINS = HERE.parent.parent
_FORGE_TOP = PLUGINS / "forge"
_SKILL_FORGE_TOP = PLUGINS / "skill-forge"
for _p in (_FORGE_TOP, _SKILL_FORGE_TOP):
    if _p.is_dir() and str(_p) not in sys.path:
        sys.path.insert(0, str(_p))


# Optional dependencies — silent fallback when forge / skill-forge missing.
try:
    from forge.paths import corvin_home as _corvin_home  # type: ignore
except Exception:  # noqa: BLE001
    _corvin_home = None  # type: ignore[assignment]

try:
    from forge.scope import scope_root as _scope_root  # type: ignore
except Exception:  # noqa: BLE001
    _scope_root = None  # type: ignore[assignment]

try:
    from forge.registry import Registry as _ForgeRegistry  # type: ignore
except Exception:  # noqa: BLE001
    _ForgeRegistry = None  # type: ignore[assignment]

try:
    from forge.security_events import write_event as _write_event  # type: ignore
except Exception:  # noqa: BLE001
    _write_event = None  # type: ignore[assignment]

try:
    from skill_forge.multi_registry import MultiSkillRegistry as _MultiSkillRegistry  # type: ignore
except Exception:  # noqa: BLE001
    _MultiSkillRegistry = None  # type: ignore[assignment]

# ADR-0096 M4 — MCP Plugin Manager: session-scope activation purge.
# Silent best-effort: missing mcp_manager does not block the reset.
try:
    _MCP_MANAGER = HERE.parent.parent / "mcp_manager"
    if _MCP_MANAGER.is_dir() and str(_MCP_MANAGER) not in sys.path:
        sys.path.insert(0, str(_MCP_MANAGER))
    from mcp_manager.activate import clear_session_scope as _mcp_clear_session  # type: ignore
except Exception:  # noqa: BLE001
    _mcp_clear_session = None  # type: ignore[assignment]

# ADR-0099 — Anthropic Batch API: cancel open batch jobs on session reset.
# Silent best-effort: missing compute module does not block the reset.
try:
    _COMPUTE_TOP = HERE.parents[2] / "core" / "compute"
    if _COMPUTE_TOP.is_dir() and str(_COMPUTE_TOP) not in sys.path:
        sys.path.insert(0, str(_COMPUTE_TOP))
    from corvin_compute.engines.anthropic_batch import (  # type: ignore
        cancel_open_batches_for_session as _abp_cancel_session,
    )
except Exception:  # noqa: BLE001
    _abp_cancel_session = None  # type: ignore[assignment]


# Every shipped channel, from the one list (channels.py). This tuple used to be
# hand-written with five entries, which made `--channel signal` / `--channel
# teams` an argparse error — so `/new` and `/reset` failed on both bridges with
# "session reset failed: invalid choice" and the session was never reset.
from channels import BRIDGE_CHANNELS  # noqa: E402  — local module, HERE is on sys.path

VALID_CHANNELS = BRIDGE_CHANNELS
VALID_REASONS = ("manual", "timeout")


def _corvin_home_safe() -> Path:
    """Return the resolved CORVIN_HOME, falling back to env/path heuristics
    when forge.paths is missing."""
    if _corvin_home is not None:
        return _corvin_home()
    env = os.environ.get("CORVIN_HOME")
    if env:
        return Path(os.path.expanduser(os.path.expandvars(env)))
    here = Path(__file__).resolve()
    for parent in [here, *here.parents]:
        if (parent / ".corvin_repo").exists() or (parent / "plugins").is_dir():
            return parent / ".corvin"
    return Path.home() / ".corvin"


def _safe_id(s: str) -> str:
    """Same shape as adapter._safe_id — used for the **voice** session path."""
    return "".join(ch if ch.isalnum() else "_" for ch in str(s))[:64] or "anon"


def forge_channel_id(channel: str, chat_id: str) -> str:
    """Mirror adapter._build_spawn_env: '<bridge>:<sanitized chat_id>' where
    only forward and back slashes are replaced. Other characters
    (':', '-', alnum) pass through. This is the EXACT id forge.scope uses
    to derive the session-scope workspace dir."""
    safe = re.sub(r"[/\\]", "_", str(chat_id))
    return f"{channel}:{safe}"


def _audit_path_unified() -> Path:
    """Resolve the path of the unified bridge+forge hash-chain.

    Honours ``VOICE_AUDIT_PATH`` (tests use this when they want to assert on
    a specific file) and otherwise routes through
    ``bridges/shared/audit.audit_path``, which itself defaults to
    ``<corvin_home>/global/forge/audit.jsonl``."""
    env = os.environ.get("VOICE_AUDIT_PATH")
    if env:
        return Path(env)
    try:
        from .audit import audit_path  # type: ignore
    except ImportError:
        sys.path.insert(0, str(HERE))
        from audit import audit_path  # type: ignore
    return audit_path()


def _write_audit(*, channel: str, chat_id: str, reason: str,
                 forge_chan_id: str) -> tuple[str | None, str]:
    """Write the session.reset / session.timeout event FIRST. Returns
    (event_id, event_type). event_id is None on best-effort write failure."""
    event_type = "session.timeout" if reason == "timeout" else "session.reset"
    if _write_event is None:
        print(
            "CRITICAL session_reset: forge.security_events unavailable — "
            f"audit event '{event_type}' could not be written before reset "
            f"(channel={channel!r} chat_id={chat_id!r} reason={reason!r}). "
            "Proceeding with reset; operator must investigate missing audit entry.",
            file=sys.stderr,
        )
        return None, event_type
    path = _audit_path_unified()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        rec = _write_event(
            path, event_type,
            tool="",
            run_id="",
            details={
                "channel":   channel,
                "chat_key":  str(chat_id),
                "chan_id":   forge_chan_id,
                "reason":    reason,
            },
            hash_chain=True,
        )
        return rec.get("hash"), event_type
    except Exception:  # noqa: BLE001
        return None, event_type


def _purge_skills(*, forge_chan_id: str, repo_root: Path | None,
                  failures: list[str]) -> int:
    """Delete every session-scope skill (canonical + slot mirror).
    Returns count of skills deleted. Slot-mirror counted separately by
    caller via the returned (skills_removed, slot_removed) tuple."""
    if _MultiSkillRegistry is None:
        return 0
    try:
        mr = _MultiSkillRegistry(
            channel_id=forge_chan_id, project_root=repo_root,
        )
    except Exception as e:  # noqa: BLE001
        failures.append(f"skill-forge open: {e!s}")
        return 0
    try:
        reg = mr._registry("session")  # the session sub-registry
    except Exception as e:  # noqa: BLE001
        failures.append(f"skill-forge session-scope: {e!s}")
        return 0

    removed = 0
    try:
        names = [spec.name for spec in reg.list()]
    except Exception as e:  # noqa: BLE001
        failures.append(f"skill-forge list: {e!s}")
        return 0
    for name in names:
        try:
            if reg.delete(name, reason="session.reset", purge_slot=True):
                removed += 1
        except Exception as e:  # noqa: BLE001
            failures.append(f"skill-forge delete {name}: {e!s}")
    return removed


def _purge_forge_tools(*, forge_chan_id: str, failures: list[str],
                       tenant_id: str = "_default") -> int:
    """Delete every session-scope forge tool. Returns count."""
    if _ForgeRegistry is None or _scope_root is None:
        return 0
    try:
        root = _scope_root("session", tenant_id=tenant_id, channel_id=forge_chan_id)
    except Exception as e:  # noqa: BLE001
        failures.append(f"forge scope_root: {e!s}")
        return 0
    if not root.exists():
        return 0
    try:
        reg = _ForgeRegistry(root)
    except Exception as e:  # noqa: BLE001
        failures.append(f"forge registry open: {e!s}")
        return 0
    removed = 0
    try:
        names = [spec.name for spec in reg.list()]
    except Exception as e:  # noqa: BLE001
        failures.append(f"forge list: {e!s}")
        return 0
    for name in names:
        try:
            reg.delete(name)
            removed += 1
        except Exception as e:  # noqa: BLE001
            failures.append(f"forge delete {name}: {e!s}")
    return removed


def _purge_worker_sessions(*, forge_chan_id: str,
                           failures: list[str]) -> int:
    """ADR-0049 — purge all worker_sessions/*.session.json files for a chat.

    Audit-first per file (best-effort, a write failure MUST NOT block the
    subsequent rmtree of the parent directory).  Returns count of files
    removed.
    """
    home = _corvin_home_safe()
    ws_dir = home / "sessions" / forge_chan_id / "worker_sessions"
    if not ws_dir.is_dir():
        return 0

    removed = 0
    for p in sorted(ws_dir.glob("*.session.json")):
        scope_label = p.stem.replace(".session", "")
        # Best-effort audit before each deletion.
        try:
            if _write_event is not None:
                path = _audit_path_unified()
                path.parent.mkdir(parents=True, exist_ok=True)
                _write_event(
                    path, "worker_session.purged",
                    tool="", run_id="",
                    details={
                        "scope_label": scope_label,
                        "chat_key":    forge_chan_id,
                    },
                    hash_chain=True,
                )
        except Exception:  # noqa: BLE001
            pass
        try:
            p.unlink()
            removed += 1
        except Exception as e:  # noqa: BLE001
            failures.append(f"worker_session unlink {p}: {e!s}")
    return removed


def _wipe_forge_session_dir(*, forge_chan_id: str,
                            failures: list[str]) -> bool:
    """Defensive rmtree of <corvin_home>/sessions/<chan_id>/ — picks up
    leftovers (audit.jsonl, .lock, manifest) that the registry deletes
    above don't touch. Returns True iff a directory was removed."""
    home = _corvin_home_safe()
    target = home / "sessions" / forge_chan_id
    if not target.exists():
        return False
    try:
        shutil.rmtree(target, ignore_errors=False)
        return True
    except Exception as e:  # noqa: BLE001
        failures.append(f"rmtree {target}: {e!s}")
        return False


def _wipe_voice_state(*, channel: str, chat_id: str,
                      failures: list[str]) -> bool:
    """Remove the voice adapter's per-chat session directory. Mirrors the
    layout used by adapter._session_dir() — sessions/<safe_channel>/<safe_chat>/."""
    home = _corvin_home_safe()
    target = home / "voice" / "sessions" / _safe_id(channel) / _safe_id(chat_id)
    if not target.exists():
        return False
    try:
        shutil.rmtree(target, ignore_errors=False)
        return True
    except Exception as e:  # noqa: BLE001
        failures.append(f"rmtree {target}: {e!s}")
        return False


def reset_session(
    *,
    channel: str,
    chat_id: str,
    repo_root: Path | None = None,
    reason: str = "manual",
    tenant_id: str = "_default",
) -> dict[str, Any]:
    """Wipe everything bound to one bridge chat.

    Order of operations is fixed and audit-first so a partial failure
    leaves a traceable record:

      1. Write the audit event (session.reset or session.timeout).
      2. Purge SkillForge skills (canonical + plugin-slot mirror).
      3. Purge forge tools.
      4. Defensive rmtree of the forge session workspace dir.
      5. Defensive rmtree of the voice session-state dir.

    Returns a dict with per-layer counts, the audit event id, the
    event type, and a list of any failures encountered. Idempotent —
    a second call on the same chat returns counts of zero.

    ``tenant_id`` is propagated to sub-calls that require it
    (ADR-0099 batch cancel, ADR-0096 MCP session clear).
    Environment-variable fallback is explicitly forbidden per ADR-0007
    (console tenant routing must be session-bound only).
    """
    failures: list[str] = []
    forge_chan_id = forge_channel_id(channel, chat_id)
    # Layer-11 dialectic decision-point: high-value sessions raise heat
    # above the threshold so the operator gets at least an audit-trail
    # entry recording what's about to be lost. Best-effort — never blocks
    # the reset itself.
    try:
        _dialectic_session_reset(channel=channel, chat_id=chat_id,
                                 forge_chan_id=forge_chan_id, reason=reason)
    except Exception:  # noqa: BLE001
        pass
    audit_event_id, audit_event_type = _write_audit(
        channel=channel, chat_id=chat_id, reason=reason,
        forge_chan_id=forge_chan_id,
    )

    # Emit Brain graph event for session reset (ADR-0296, ADR-0298)
    _emit_session_reset_event(
        session_id=forge_chan_id,
        tenant_id=tenant_id,
        reason=reason,
        channel=channel,
        chat_id=chat_id,
    )

    if audit_event_id is None:
        if _write_event is None:
            failures.append(
                "session_reset: forge unavailable — "
                f"'{audit_event_type}' event could not be written before reset "
                f"(channel={channel!r} chat_id={chat_id!r} reason={reason!r})"
            )
        else:
            failures.append(
                "session_reset: audit write failure — "
                f"'{audit_event_type}' event could not be written before reset "
                f"(channel={channel!r} chat_id={chat_id!r} reason={reason!r})"
            )
        # Audit-first invariant: block destructive operations when the audit
        # event could not be written — applies to both forge-absent and
        # forge-write-failed cases so the gap is always surfaced.
        return {
            "voice_state_removed":      0,
            "forge_tools_removed":      0,
            "skills_removed":           0,
            "slot_mirrors_removed":     0,
            "artifacts_removed":        0,
            "worker_sessions_removed":  0,
            "audit_event_id":           None,
            "audit_event_type":         audit_event_type,
            "reason":                   reason,
            "channel":                  channel,
            "chat_id":                  str(chat_id),
            "failures":                 failures,
        }

    # ADR-0099 — cancel open Anthropic Batch API jobs BEFORE any rmtree.
    # Audit-first invariant: compute.batch_cancelled events are written here
    # (inside _abp_cancel_session) while open_batches.json still exists.
    # Best-effort — failure must never block the rest of the reset.
    if _abp_cancel_session is not None:
        try:
            session_key = forge_channel_id(channel, chat_id)
            _abp_cancel_session(session_key, tenant_id)
        except Exception:  # noqa: BLE001
            pass

    # ADR-0096 M4 — purge ephemeral MCP session-scope activations.
    # Runs BEFORE skill / forge purge so the session file is gone before
    # the session workspace dir is rmtree'd. Best-effort, never blocks reset.
    if _mcp_clear_session is not None:
        try:
            _mcp_clear_session(tenant_id, forge_chan_id)
        except Exception:  # noqa: BLE001
            pass

    # Skills first so the slot mirror is purged before we drop the
    # forge session dir (registry walks files, the rmtree below is the
    # leftover-cleanup pass).
    skills_removed = _purge_skills(
        forge_chan_id=forge_chan_id, repo_root=repo_root,
        failures=failures,
    )
    forge_tools_removed = _purge_forge_tools(
        forge_chan_id=forge_chan_id, failures=failures,
        tenant_id=tenant_id,
    )
    # ADR-0049 — purge worker session files BEFORE the rmtree below
    # so per-file audit events land while the directory is still intact.
    worker_sessions_removed = _purge_worker_sessions(
        forge_chan_id=forge_chan_id, failures=failures,
    )
    # Layer 33 — purge session-scope artifacts BEFORE the rmtree below
    # so the audit event `artifact.session_purged` lands while the
    # manifest is still readable (audit-first rule). Pinned artifacts
    # live in <global>/artifacts/ and are never touched here.
    artifacts_removed = _purge_session_artifacts(
        channel=channel, chat_id=chat_id, failures=failures,
        tenant_id=tenant_id,
    )
    _ = _wipe_forge_session_dir(
        forge_chan_id=forge_chan_id, failures=failures,
    )
    voice_state_removed = _wipe_voice_state(
        channel=channel, chat_id=chat_id, failures=failures,
    )

    # The slot mirror is purged inline by SkillRegistry.delete(); the
    # registry doesn't return per-skill slot counts, so we report it as
    # equal to skills_removed to keep the surface simple.
    slot_mirrors_removed = skills_removed

    return {
        "voice_state_removed":      voice_state_removed,
        "forge_tools_removed":      forge_tools_removed,
        "skills_removed":           skills_removed,
        "slot_mirrors_removed":     slot_mirrors_removed,
        "artifacts_removed":        artifacts_removed,
        "worker_sessions_removed":  worker_sessions_removed,
        "audit_event_id":           audit_event_id,
        "audit_event_type":         audit_event_type,
        "reason":                   reason,
        "channel":                  channel,
        "chat_id":                  str(chat_id),
        "failures":                 failures,
    }


def _purge_session_artifacts(*, channel: str, chat_id: str,
                             failures: list[str],
                             tenant_id: str = "_default") -> int:
    """Layer 33 — purge unpinned artifacts for the given session.

    Audit-first: ``forge.artifacts.purge_session`` writes the CRITICAL
    ``artifact.session_purged`` event before the rmtree. Returns the
    count of removed artifacts (0 if no manifest exists). Failure to
    import the forge package or resolve the path is non-fatal — the
    rest of the reset proceeds.

    ``tenant_id`` is threaded into ``session_artifacts_dir`` so the
    purge targets the SAME tenant the writer used (ADR-0007). Without
    it a non-default tenant's reset would read/purge the ``_default``
    tenant's artifact dir (reader != writer divergence).
    """
    try:
        from forge import artifacts as _art  # type: ignore
        session_key = f"{channel}:{chat_id}"
        root = _art.session_artifacts_dir(session_key, tenant_id=tenant_id)
        if not root.exists():
            return 0
        return _art.purge_session(root)
    except Exception as e:  # noqa: BLE001
        failures.append(f"artifact-purge {channel}:{chat_id}: {e!s}")
        return 0


def collect_unpinned_artifacts(
    channel: str, chat_id: str, tenant_id: str = "_default",
) -> list[dict[str, object]]:
    """Layer 33 — list unpinned artifacts for ``/reset`` pre-warn.

    Returns ``[{name, mime, size, ts}, ...]`` sorted by ``ts`` desc.
    Empty list when no manifest exists or forge is not on path —
    pre-warn callers treat the empty list as "no pending artifacts,
    safe to reset". ``tenant_id`` must match the writer's tenant or the
    pre-warn list comes up empty on non-default tenants (reader!=writer).
    """
    try:
        from forge import artifacts as _art  # type: ignore
        session_key = f"{channel}:{chat_id}"
        root = _art.session_artifacts_dir(session_key, tenant_id=tenant_id)
        return [
            {"name": e.name, "mime": e.mime, "size": e.size, "ts": e.ts}
            for e in _art.list_active(root, limit=50)
        ]
    except Exception:  # noqa: BLE001
        return []


def _dialectic_session_reset(*, channel: str, chat_id: str,
                              forge_chan_id: str, reason: str) -> None:
    """Best-effort dialectic decision-point for session-reset.

    Heat is raised by skill-grade-count and tool-count in the session
    workspace — a session with 5 promoted skills and 12 tools is a
    high-value target where the operator should at least see an audit
    entry recording the opportunity-cost.
    """
    try:
        import sys as _sys
        here = Path(__file__).resolve().parent
        if str(here) not in _sys.path:
            _sys.path.insert(0, str(here))
        import dialectic as _dialectic  # type: ignore
    except Exception:
        return
    # Probe the session workspace for skills + tools (best-effort, no raises).
    n_skills = 0
    n_tools = 0
    try:
        sessions_root = _corvin_home_safe() / "sessions" / forge_chan_id
        skills_dir = sessions_root / "skill-forge" / "skills"
        if skills_dir.is_dir():
            n_skills = sum(1 for _ in skills_dir.iterdir() if _.is_dir())
        tools_manifest = sessions_root / "forge" / "tools" / "manifest.json"
        if tools_manifest.is_file():
            try:
                n_tools = len(json.loads(tools_manifest.read_text()) or {})
            except (OSError, json.JSONDecodeError):
                n_tools = 0
    except Exception:
        n_skills = n_tools = 0
    # Heat-Score: 0 skills + few tools → low heat → no dialectic.
    # 3+ skills or 10+ tools → above threshold.
    consequence = 0.1 + min(0.7, n_skills * 0.15 + n_tools * 0.04)
    uncertainty = 0.1 + (0.3 if reason == "timeout" else 0.0)
    scope_n = 1 + min(2, n_skills // 2)
    _dialectic.decide(
        site="session_reset",
        thesis={"action": "reset", "channel": channel,
                "chat_id": str(chat_id), "reason": reason,
                "n_skills": n_skills, "n_tools": n_tools},
        antithesis={"reason": "session-may-have-promotion-candidates",
                    "n_skills": n_skills, "n_tools": n_tools},
        consequence=consequence,
        uncertainty=uncertainty,
        scope=scope_n,
        channel_id=forge_chan_id,
    )


# ── CLI ─────────────────────────────────────────────────────────────────────


def _cli(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="session_reset")
    ap.add_argument("--channel", required=True, choices=VALID_CHANNELS)
    ap.add_argument("--chat-id", required=True)
    ap.add_argument("--repo-root", default=None,
                    help="optional git repo root for project-scope resolution")
    ap.add_argument("--reason", default="manual", choices=VALID_REASONS)
    args = ap.parse_args(argv)

    repo_root = Path(args.repo_root) if args.repo_root else None
    result = reset_session(
        channel=args.channel,
        chat_id=args.chat_id,
        repo_root=repo_root,
        reason=args.reason,
    )
    print(json.dumps(result, ensure_ascii=False))
    return 0


def _emit_session_reset_event(
    *,
    session_id: str,
    tenant_id: str,
    reason: str,
    channel: str,
    chat_id: str,
) -> None:
    """Emit session_reset event to Brain graph (best-effort).

    Allows Brain subsystems to track session lifecycle and update
    session state in the graph. Non-fatal: event emission failure
    does NOT block session reset.

    ADR-0296/0298: Session design in Brain.
    """
    try:
        # Try to access ContextBus (if available in this process)
        from core.context_engineering.context_bus import ContextBus  # type: ignore
        bus = ContextBus.get_instance()
        if bus:
            bus.publish("session_reset", {
                "session_id": session_id,
                "tenant_id": tenant_id,
                "reason": reason,
                "channel": channel,
                "chat_id": str(chat_id),
                "timestamp": datetime.now().isoformat(),
            })
    except Exception:  # noqa: BLE001
        # Best-effort: session reset proceeds even if event emission fails
        pass


if __name__ == "__main__":
    raise SystemExit(_cli())
