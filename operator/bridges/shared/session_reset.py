"""session_reset — wipe everything bound to one bridge chat.

Layer 8 of the voice/cowork/forge/skill-forge stack: a single, idempotent
operation that purges the four cleanup layers a chat owns:

  1. SkillForge skills in the **session** scope (canonical workspace
     SKILL.md/meta.json + plugin-slot mirror under
     ``operator/skill-forge/skills/dyn/``).
  2. Forge tools in the **session** scope (manifest + impl files).
  3. Forge session workspace dir at
     ``<corvin_home>/tenants/<tid>/sessions/<channel>:<chat>/`` — defensive
     rmtree (also takes ``forge/memory.md`` and the worker-session files).
  4. Claude conversation state in the adapter's per-chat session dir,
     resolved through ``session_state`` (the SSOT the adapter itself uses):
     ``.main_session.json``, ``.session_started``, ``.claude.json`` and
     ``.claude/``. Only those — project files in the same directory are
     deliberately kept, which is what the ``/new`` reply promises.

Both path families are tenant-aware (ADR-0007 Phase 1.2). They were NOT until
2026-08-28: this module still hand-built the pre-migration
``<corvin_home>/sessions/`` and ``<corvin_home>/voice/sessions/`` layouts,
neither of which resolved to anything, so ``/new`` deleted nothing the next
turn cared about and the chat resumed its old Claude session verbatim. Path
resolution now goes through ``session_state`` so the two sides cannot drift
apart again.

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
from datetime import datetime
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

# ...and then put OUR OWN directory back in front of them.
#
# ``operator/forge/`` ships a legacy top-level ``paths.py`` (FORGE_ROOT /
# get_forge_home / …) that is unrelated to ``bridges/shared/paths.py`` and
# shadows it whenever it sits earlier in sys.path. Roughly 25 sibling modules
# do ``from paths import corvin_home`` (or tenant_global_dir / voice_dir) at
# import time and raise ImportError under the shadowed name. Two of them are
# load-bearing right here and were failing silently into their own
# ``except Exception`` handlers:
#   * ``context_budget``    → the Layer-20 quota was never reset, so /new kept
#                             reporting "token budget reset: no".
#   * ``instance_identity`` → the session.reset audit event shipped without its
#                             instance signature.
# Nothing in forge/ or skill-forge/ imports the bare name ``paths`` — they all
# go through the package-qualified ``forge.paths`` — so restoring our own
# precedence is safe and keeps both worlds importable.
if str(HERE) in sys.path:
    sys.path.remove(str(HERE))
sys.path.insert(0, str(HERE))


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

# Layer 20 — Context Budget: reset per-session token quota on reset.
# Silent best-effort: missing context_budget module does not block the reset.
try:
    from context_budget import (  # type: ignore
        unregister_session_budget as _unregister_budget,
    )
except Exception:  # noqa: BLE001
    _unregister_budget = None  # type: ignore[assignment]


# Every shipped channel, from the one list (channels.py). This tuple used to be
# hand-written with five entries, which made `--channel signal` / `--channel
# teams` an argparse error — so `/new` and `/reset` failed on both bridges with
# "session reset failed: invalid choice" and the session was never reset.
from channels import BRIDGE_CHANNELS  # noqa: E402  — local module, HERE is on sys.path

# SSOT for the adapter's session-state location + contents. Not optional in
# spirit — without it a reset cannot clear what the next turn resumes from —
# but imported defensively so a broken install still runs the other layers and
# surfaces the gap as an explicit failure entry rather than a traceback.
try:
    import session_state as _session_state  # type: ignore  # noqa: E402
except Exception:  # noqa: BLE001
    _session_state = None  # type: ignore[assignment]

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


def _sessions_root(tenant_id: str = "_default") -> Path:
    """``<corvin_home>/tenants/<tid>/sessions/`` — where forge session workspaces live.

    ADR-0007 Phase 1.2 moved these under the tenant home; this module kept
    building ``<corvin_home>/sessions/`` until 2026-08-28, so the forge tools,
    ``forge/memory.md`` and the worker-session files of a real chat were never
    reached by ``/new``. Falls back to the pre-migration layout only when the
    resolver is genuinely unavailable.
    """
    if _session_state is not None:
        root = _session_state.tenant_sessions_root(tenant_id)
        if root is not None:
            return root
    return _corvin_home_safe() / "sessions"


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
    # Always use absolute import to avoid conflicts with operator/forge/paths.py
    # when this module is loaded directly (not as part of a package).
    # Add HERE to sys.path first so local audit + paths imports work reliably.
    if str(HERE) not in sys.path:
        sys.path.insert(0, str(HERE))
    try:
        from audit import audit_path  # type: ignore
    except ImportError:
        # Fallback: use the same resolver that audit.py provides
        from paths import corvin_home as _ch  # type: ignore
        return _ch() / "global" / "forge" / "audit.jsonl"
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
                           failures: list[str],
                           tenant_id: str = "_default") -> int:
    """ADR-0049 — purge all worker_sessions/*.session.json files for a chat.

    Audit-first per file (best-effort, a write failure MUST NOT block the
    subsequent rmtree of the parent directory).  Returns count of files
    removed.
    """
    ws_dir = _sessions_root(tenant_id) / forge_chan_id / "worker_sessions"
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
                            failures: list[str],
                            tenant_id: str = "_default") -> bool:
    """Defensive rmtree of <tenant_sessions_root>/<chan_id>/ — picks up
    leftovers (audit.jsonl, .lock, manifest, forge/memory.md) that the
    registry deletes above don't touch. Returns True iff a directory was
    removed."""
    target = _sessions_root(tenant_id) / forge_chan_id
    if not target.exists():
        return False
    try:
        shutil.rmtree(target, ignore_errors=False)
        return True
    except Exception as e:  # noqa: BLE001
        failures.append(f"rmtree {target}: {e!s}")
        return False


def _wipe_voice_state(*, channel: str, chat_id: str,
                      failures: list[str],
                      tenant_id: str = "_default") -> bool:
    """Clear Claude's conversation state in the adapter's per-chat session dir.

    Resolution and deletion both go through ``session_state`` — the SSOT the
    adapter itself uses (see that module's docstring). Two properties matter:

      * The directory is the one ``adapter._session_dir()`` really resolves to.
        Until 2026-08-28 this function hand-built ``<home>/voice/sessions/...``,
        which matched nothing after the ADR-0007 Phase 1.2 migration, so
        ``.main_session.json`` survived every ``/new`` and the next turn
        resumed the old Claude session.
      * Only conversation state is deleted, never the whole directory. The
        reset reply promises the chat's project files are kept, and the same
        directory holds ``outputs/``, ``tasks/`` and the L37-retained
        ``cel-briefs/`` audit sidecars.

    Returns True iff at least one state entry was actually removed.
    """
    if _session_state is None:
        failures.append(
            "session_reset: session_state module unavailable — "
            "Claude conversation state could NOT be cleared "
            f"(channel={channel!r} chat_id={chat_id!r})"
        )
        return False

    candidates = _session_state.claude_session_dirs(
        channel, chat_id, tenant_id=tenant_id,
    )
    if not candidates:
        # The resolver produced nothing at all. That is never a legitimate
        # "nothing to do" — it means path resolution itself failed, which is
        # precisely how this layer stayed broken for weeks while reporting a
        # cheerful "voice state cleared: no".
        failures.append(
            "session_reset: could not resolve any session directory — "
            f"Claude conversation state NOT cleared (channel={channel!r} "
            f"chat_id={chat_id!r} tenant={tenant_id!r})"
        )
        return False

    removed_any = False
    for target in candidates:
        if not target.is_dir():
            continue
        try:
            removed = _session_state.reset_claude_session_state(target)
            if removed:
                removed_any = True
        except Exception as e:  # noqa: BLE001
            failures.append(f"reset session state {target}: {e!s}")
    return removed_any


def _reset_budget(*, chat_id: str, failures: list[str]) -> bool:
    """Layer 20 — reset the session's token budget quota.

    Unregisters the session from budgets.json so the next adapter turn
    will auto-register a fresh budget with 0 tokens used. Uses bare chat_id
    (not forge_channel_id) because adapter registers budgets with the raw
    chat_id as the session key (ADR-0180, Layer 20). Returns True iff the
    session had a registered budget. Best-effort — failure must never block
    the rest of the reset.
    """
    if _unregister_budget is None:
        # Surface it instead of returning a quiet False. This import failing
        # silently is exactly how the Layer-20 reset stayed broken while the
        # reply reported "token budget reset: no" as if that were normal.
        failures.append(
            "session_reset: context_budget unavailable — token budget quota "
            f"NOT reset (chat_id={chat_id!r})"
        )
        return False
    try:
        return _unregister_budget(str(chat_id))
    except Exception as e:  # noqa: BLE001
        failures.append(f"budget reset: {e!s}")
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
      6. Reset Layer-20 context budget quota (so next turn gets fresh budget).

    Returns a dict with per-layer counts (including budget_reset: bool),
    the audit event id, the event type, and a list of any failures
    encountered. Idempotent — a second call on the same chat returns
    counts of zero.

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
            "budget_reset":             False,
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
        tenant_id=tenant_id,
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
        tenant_id=tenant_id,
    )
    voice_state_removed = _wipe_voice_state(
        channel=channel, chat_id=chat_id, failures=failures,
        tenant_id=tenant_id,
    )
    # Layer 20 — reset the session's context budget quota so the next turn
    # starts with a fresh 100k tokens (or the operator's configured default).
    # Best-effort: budget unavailability must not block the reset.
    # Use bare chat_id (not forge_channel_id) because adapter registers budgets
    # with the raw chat_id as the session key.
    budget_reset = _reset_budget(
        chat_id=chat_id, failures=failures,
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
        "budget_reset":             budget_reset,
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
        sessions_root = _sessions_root(tenant_id) / forge_chan_id
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


def _clear_execution_context() -> None:
    """Clear ExecutionContext for session reset (best-effort).

    Resets the current ExecutionContext via ContextVar to allow a fresh
    context for the next session. Non-fatal: failure does NOT block reset.

    ADR-0296/0298: Session design in Brain.
    """
    try:
        from core.context_engineering.context_bus import (  # type: ignore
            set_execution_context,
        )
        # Clear the current ExecutionContext
        set_execution_context(None)
        import logging
        logging.getLogger(__name__).debug("ExecutionContext cleared for session reset")
    except Exception:  # noqa: BLE001
        # Best-effort: session reset proceeds even if context clearing fails
        pass


def _call_subsystem_resets(session_id: str | None = None) -> None:
    """Call clear_session_cache() on all Brain subsystems (best-effort).

    Allows subsystems to clear session-scoped state (caches, retry counts,
    decision history, etc.). Non-fatal: failure in one subsystem does NOT
    block other subsystems or the overall session reset.

    ``session_id`` names the chat being reset and is passed on to every
    handler that accepts it. One process serves many chats, so a handler
    that clears its whole cache would wipe unrelated conversations; a
    handler is called without the argument only if its signature predates
    this and takes none.

    ADR-0296/0298: Session design in Brain.
    """
    import inspect
    try:
        from core.context_engineering.context_bus import ContextBus  # type: ignore
        bus = ContextBus.get_instance()
        if bus and hasattr(bus, 'hub') and bus.hub:
            # Call clear_session_cache() on all subsystems
            hub = bus.hub
            for subsystem in hub.subsystems.values():
                try:
                    handler = getattr(subsystem, 'clear_session_cache', None)
                    if handler is None:
                        continue
                    try:
                        accepts_id = 'session_id' in inspect.signature(
                            handler).parameters
                    except (TypeError, ValueError):
                        accepts_id = False
                    if accepts_id:
                        handler(session_id=session_id)
                    else:
                        handler()
                except Exception as e:  # noqa: BLE001
                    import logging
                    logging.getLogger(__name__).warning(
                        f"Subsystem {getattr(subsystem, 'name', 'unknown')} "
                        f"clear_session_cache failed: {e}"
                    )
    except Exception:  # noqa: BLE001
        # Best-effort: session reset proceeds even if subsystem resets fail
        pass


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
    session state in the graph. Also triggers ExecutionContext clear
    and subsystem resets. Non-fatal: event emission failure
    does NOT block session reset.

    ADR-0296/0298: Session design in Brain.
    """
    try:
        # Try to access ContextBus (if available in this process)
        from core.context_engineering.context_bus import ContextBus  # type: ignore
        bus = ContextBus.get_instance()
        if bus:
            # Emit the session reset event
            import asyncio
            try:
                asyncio.create_task(bus.publish("session_reset", {
                    "session_id": session_id,
                    "tenant_id": tenant_id,
                    "reason": reason,
                    "channel": channel,
                    "chat_id": str(chat_id),
                    "timestamp": datetime.now().isoformat(),
                }))
            except RuntimeError:
                # No event loop running; skip async publish
                pass
    except Exception:  # noqa: BLE001
        # Best-effort: session reset proceeds even if event emission fails
        pass

    # Clear ExecutionContext (non-fatal)
    _clear_execution_context()

    # Call clear_session_cache() on all subsystems (non-fatal)
    _call_subsystem_resets(session_id)


if __name__ == "__main__":
    raise SystemExit(_cli())
