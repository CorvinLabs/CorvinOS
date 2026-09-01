"""bg_monitor.py — background-task session wakeup monitor.

The Claude Code SDK queues task-notifications from background agents
(Agent tool with run_in_background=True) for delivery on the next turn.
If a session goes idle past the adapter's stream_idle_to window (default
300 s), those notifications wait indefinitely until the user sends the
next message.

This script is invoked by a systemd timer every 60 seconds.  It reads
the bg_watch state (CORVIN_HOME/bg_watch.json) written by the adapter
after every real user turn, and for sessions that have been idle longer
than BGW_IDLE_GRACE (default 480 s) injects a synthetic wakeup message
into the adapter INBOX.  On the next adapter poll tick (~1 s), the
adapter processes the wakeup, spawns a new Claude Code turn, and the
SDK delivers any pending task-notifications automatically.

State schema (one entry per session key = "channel:chat_id_or_sender"):
    {
        "channel":       "discord",
        "from":          "112233445566778899",   # sender UID
        "chat_id":       "987654321",            # channel/chat routing id
        "last_activity": 1750000000.0,           # unix ts of last real turn
        "notified_at":   0.0                     # 0 = not yet notified this cycle
    }

The adapter calls bg_monitor.touch() after every real user turn.  The
next call to touch() for the same session always resets notified_at to 0
so a new activity cycle re-arms the wakeup.

Env vars:
    BGW_IDLE_GRACE   seconds idle before wakeup injection (default 480)
    BGW_MAX_AGE      seconds before entry is pruned entirely (default 86400)
    ADAPTER_INBOX    override INBOX dir (used by tests; mirrors adapter.py)
    CORVIN_HOME      override corvin home directory
"""
from __future__ import annotations

import json
import os
import secrets
import sys
import time
from pathlib import Path

BGW_IDLE_GRACE = float(os.environ.get("BGW_IDLE_GRACE", "480"))
BGW_MAX_AGE = float(os.environ.get("BGW_MAX_AGE", "86400"))

# Legacy blind idle-wakeup injection (the "deliver pending SDK notifications
# now" synthetic turn). OFF by default: over one-shot `claude -p` turns it
# cannot actually carry a background agent's result forward and mostly emitted
# spurious "All caught up." messages. Real completions now flow through the
# durable completion queue (completion_notify). Set BGW_LEGACY_WAKEUP=1 to
# re-enable the old behaviour for an interactive/persistent-session deployment.
BGW_LEGACY_WAKEUP = os.environ.get("BGW_LEGACY_WAKEUP", "0").strip().lower() in (
    "1", "true", "yes", "on",
)

# Wakeup text delivered to Claude Code when injecting a synthetic turn.
# Chosen to be neutral: if the SDK has pending task-notifications they are
# delivered automatically at turn start regardless of the text.  If there
# are none, the response is kept brief by the trailing instruction.
_WAKEUP_TEXT = (
    "Background monitor check: please deliver any pending background task "
    "notifications or completed agent results now. "
    "If there are none, reply with just 'All caught up.' — nothing more."
)


# ─── path helpers ──────────────────────────────────────────────────────────


def _corvin_home() -> Path:
    v = os.environ.get("CORVIN_HOME")
    if v:
        # Expand ~ / vars so `CORVIN_HOME=~/x` resolves to the SAME tree as
        # completion_notify._corvin_home (which expands), else reader≠writer.
        return Path(os.path.expanduser(os.path.expandvars(v)))
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from paths import corvin_home as _ch  # type: ignore
        return _ch()
    except Exception:  # noqa: BLE001
        return Path.home() / ".corvin"


def _bg_watch_path() -> Path:
    return _corvin_home() / "bg_watch.json"


def _inbox_path() -> Path:
    """Mirror the INBOX resolution in adapter.py (ROOT / 'inbox')."""
    env_inbox = os.environ.get("ADAPTER_INBOX")
    if env_inbox:
        return Path(env_inbox)
    return Path(__file__).resolve().parent / "inbox"


def _outbox_path() -> Path:
    """Mirror the OUTBOX resolution in adapter.py — the dir the daemons poll."""
    env_outbox = os.environ.get("ADAPTER_OUTBOX")
    if env_outbox:
        return Path(env_outbox)
    return Path(__file__).resolve().parent / "outbox"


def _deliver_progress() -> int:
    """Backup poller for the durable INTERMEDIATE-update queue.

    Same relationship to task_progress that _deliver_completions has to
    completion_notify: the adapter main loop flushes it while the bridge polls,
    this runs from the systemd timer so a long autonomous run still reports in
    while the adapter is idle or restarting. Idempotent with the adapter
    (per-record O_EXCL lock), so double-delivery is impossible.
    """
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import task_progress as _tp  # type: ignore

        return _tp.deliver_progress(_outbox_path())
    except Exception as e:  # noqa: BLE001
        print(f"bg_monitor: progress delivery failed: {e}", file=sys.stderr)
        return 0


def _supervise_runs() -> int:
    """Heal stopped/wedged background runs (task_supervisor).

    THE reason this timer exists for long autonomous work: a worker that was
    killed, OOMed, wedged, or hit its own wall clock is otherwise never
    restarted, and the run dies wherever it stopped. One tick per timer
    interval reconciles every supervised run against reality and relaunches
    what needs relaunching, within its attempt and wall-clock budgets.
    """
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import task_supervisor as _sup  # type: ignore

        return _sup.supervise()
    except Exception as e:  # noqa: BLE001
        print(f"bg_monitor: supervision failed: {e}", file=sys.stderr)
        return 0


def _orphan_reaper_enabled() -> bool:
    """Resolve the ship-dark `bridge_orphan_task_reaper` flag, fail-to-OFF.

    Mirrors adapter._bg_flag's contract: an absent/unreadable console package
    means OFF, so this backup poller never reaps on a bridge-only install that
    never opted in.
    """
    try:
        from corvin_core import feature_flags as _ff  # type: ignore

        tid = os.environ.get("CORVIN_TENANT_ID") or "_default"
        return bool(_ff.is_enabled("bridge_orphan_task_reaper", tid))
    except Exception:  # noqa: BLE001 — console package absent → feature is off
        return False


def _reap_orphans() -> int:
    """Backup poller for the opt-in orphan reaper (bridge_orphan_task_reaper).

    Same relationship to completion_notify.reap_orphan_pending that
    _deliver_completions has to deliver_ready: the adapter main loop runs it
    while the bridge polls; this runs from the systemd timer so an abandoned
    task is still reported even when the adapter is idle/restarting. No-op when
    the flag is off. Idempotent with the adapter (state transition + O_EXCL
    delivery lock), never raises.
    """
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import completion_notify as _cn  # type: ignore

        return _cn.reap_orphan_pending(enabled=_orphan_reaper_enabled())
    except Exception as e:  # noqa: BLE001
        print(f"bg_monitor: orphan reap failed: {e}", file=sys.stderr)
        return 0


def _deliver_completions() -> int:
    """Backup poller for the durable completion queue.

    The adapter main loop delivers completions while the bridge polls; this runs
    from the systemd timer so a completion still reaches the messenger even when
    the adapter is idle/restarting. Idempotent with the adapter (per-record
    O_EXCL lock in completion_notify), so double-delivery is impossible.
    """
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import completion_notify as _cn  # type: ignore

        return _cn.deliver_ready(_outbox_path())
    except Exception as e:  # noqa: BLE001
        print(f"bg_monitor: completion delivery failed: {e}", file=sys.stderr)
        return 0


# ─── state I/O (atomic tmp+rename) ────────────────────────────────────────


def _load_state() -> dict:
    p = _bg_watch_path()
    try:
        return json.loads(p.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_state(state: dict) -> None:
    p = _bg_watch_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2))
    tmp.replace(p)


# ─── public API ───────────────────────────────────────────────────────────


def touch(channel: str, from_: str, chat_id: str | None,
          tenant_id: str = "_default") -> None:
    """Record/refresh a session after a real user turn.

    Resets notified_at to 0 so a new activity cycle re-arms the wakeup.
    Uses read-modify-write with atomic save; benign under concurrent
    adapter workers (last writer wins per key, state is small).

    `tenant_id` is captured so a wakeup turn runs in the RIGHT tenant's
    session/persona/consent context (multi-tenant fix — previously the wakeup
    envelope carried no tenant and fell back to `_default`).
    """
    key = f"{channel}:{chat_id or from_}"
    state = _load_state()
    entry = state.get(key, {})
    entry.update(
        {
            "channel": channel,
            "from": from_,
            "chat_id": chat_id,
            "tenant_id": tenant_id or "_default",
            "last_activity": time.time(),
            "notified_at": 0.0,
        }
    )
    state[key] = entry
    _save_state(state)


def purge_user(uid: str) -> int:
    """Remove all bg_watch.json entries whose 'from' field matches *uid*.

    Called by the GDPR Art. 17 erasure path to honour Right-to-Erasure for
    the bg-monitor's personal-data store (Discord UIDs / chat routing keys).
    Returns the number of entries removed.
    """
    state = _load_state()
    to_remove = [k for k, v in state.items() if v.get("from") == uid]
    for k in to_remove:
        del state[k]
    if to_remove:
        try:
            _save_state(state)
        except OSError as e:
            print(f"bg_monitor: purge_user save failed: {e}", file=sys.stderr)
    return len(to_remove)


def run_once() -> int:
    """Supervise running background tasks, deliver their notifications, and
    (legacy, opt-in) inject idle wakeups.

    Primary jobs: (a) heal supervised runs whose worker stopped or wedged, so a
    long autonomous task is carried through to completion instead of dying
    wherever it stopped; (b) flush the durable completion AND progress queues to
    the outbox so a finished — or still-running — background task reaches the
    messenger even while the adapter is idle.

    Legacy job (only when BGW_LEGACY_WAKEUP=1): inject a synthetic wakeup turn
    for sessions idle past BGW_IDLE_GRACE so the SDK can flush pending
    notifications. Off by default — see BGW_LEGACY_WAKEUP.

    Returns the count of delivered completions + progress updates +
    injected wakeups.
    Called by the systemd timer and importable for tests.
    """
    now = time.time()

    # 1) Heal first, deliver second. Supervision can mark_done a run whose
    #    budget just ran out, and doing it BEFORE the delivery pass means that
    #    verdict reaches the user in this tick instead of waiting 60 s for the
    #    next one. It can also emit a resume notice, same reasoning.
    _supervise_runs()

    # 1b) Reap abandoned pending records into loud failures BEFORE delivery, so
    #     the abort notice ships in this same tick. Ship-dark: no-op unless the
    #     bridge_orphan_task_reaper flag is on.
    _reap_orphans()

    # 2) Always deliver ready durable completions (idempotent backup poller).
    delivered = _deliver_completions()

    # 3) …and the intermediate updates of still-running autonomous work.
    delivered += _deliver_progress()

    state = _load_state()
    if not state:
        return delivered

    if not BGW_LEGACY_WAKEUP:
        # Prune stale entries so bg_watch.json stays bounded even without the
        # wakeup path, then stop — no spurious wakeup injection.
        stale = [
            k for k, v in state.items()
            if now - float(v.get("last_activity", 0) or 0) > BGW_MAX_AGE
        ]
        if stale:
            for k in stale:
                state.pop(k, None)
            try:
                _save_state(state)
            except OSError:
                pass
        return delivered

    inbox = _inbox_path()
    inbox.mkdir(parents=True, exist_ok=True)

    changed = False
    injected = 0
    to_delete: list[str] = []

    for key, entry in state.items():
        try:
            last = float(entry.get("last_activity", 0))
            notified_at = float(entry.get("notified_at", 0))
        except (TypeError, ValueError):
            to_delete.append(key)
            changed = True
            continue

        age = now - last

        # Prune stale entries
        if age > BGW_MAX_AGE:
            to_delete.append(key)
            changed = True
            continue

        # Session too recent — not idle yet
        if age < BGW_IDLE_GRACE:
            continue

        # H3: once notified, never re-fire until touch() explicitly resets
        # notified_at to 0 (i.e., a real user turn arrives). The old
        # time-based re-arm caused spam every BGW_IDLE_GRACE seconds.
        if notified_at > 0:
            continue

        channel = entry.get("channel", "discord")
        from_ = entry.get("from", "")
        chat_id = entry.get("chat_id")
        tenant_id = entry.get("tenant_id", "_default")

        # M1: "zz_bgw_" sorts after "mp*" filenames lexicographically so the
        # adapter processes real user messages before wakeup injections in the
        # same poll tick (adapter uses sorted(INBOX.glob("*.json"))).
        msg_id = f"zz_bgw_{secrets.token_hex(6)}"
        envelope: dict = {
            "id": msg_id,
            "channel": channel,
            "from": from_,
            "chat_id": chat_id,
            "tenant_id": tenant_id,
            "text": _WAKEUP_TEXT,
            "ts": int(now * 1000),
            "_bg_wakeup": True,
        }
        try:
            (inbox / f"{msg_id}.json").write_text(
                json.dumps(envelope, ensure_ascii=False, indent=2)
            )
            entry["notified_at"] = now
            changed = True
            injected += 1
        except OSError as e:
            print(
                f"bg_monitor: inbox write failed for {key}: {e}",
                file=sys.stderr,
            )

    for key in to_delete:
        del state[key]

    if changed:
        try:
            _save_state(state)
        except OSError as e:
            print(f"bg_monitor: state save failed (notified_at not persisted): {e}", file=sys.stderr)

    return delivered + injected


if __name__ == "__main__":
    n = run_once()
    if n:
        # The count now covers completions + progress updates delivered as well
        # as legacy wakeups injected, so say "action(s)" rather than the old
        # "wakeup(s) injected", which was true when delivery was the only job.
        print(f"bg_monitor: {n} action(s)")
    else:
        print("bg_monitor: nothing to do")
