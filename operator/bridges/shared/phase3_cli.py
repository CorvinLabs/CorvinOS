#!/usr/bin/env python3
"""phase3_cli.py — unified CLI wrapper for the Phase-3 slash commands.

Single entry point that the bridge dispatcher (in_chat_commands.js)
shells out to. Subcommand-style — first arg picks the layer, rest of
args are passed through to that layer's CLI surface.

Usage:
    python3 phase3_cli.py ps [-a]                      # Layer 17
    python3 phase3_cli.py pipe list                    # Layer 18
    python3 phase3_cli.py pipe create <name> [<type>]
    python3 phase3_cli.py pipe write <name> <payload>
    python3 phase3_cli.py pipe read <name>
    python3 phase3_cli.py pipe rm <name>
    python3 phase3_cli.py svc list                     # Layer 19
    python3 phase3_cli.py svc deps <name>
    python3 phase3_cli.py budget show [<session_id>]   # Layer 20
    python3 phase3_cli.py budget policy <session> <evict|compress|reject>
    python3 phase3_cli.py debug status                 # /debug self-test channel
    python3 phase3_cli.py debug send <text>
    python3 phase3_cli.py kill [-9] <session_id>       # Layer 17 Phase-4.1
    python3 phase3_cli.py nice <session_id> <±N>
    python3 phase3_cli.py help

Output is plain text formatted for chat (≤ ~80 cols where possible).
Exit code 0 on success, 1 on user error, 2 on internal error.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent.parent.parent / "core" / "init"))


def _err(msg: str, code: int = 1) -> int:
    print(f"error: {msg}", file=sys.stderr)
    return code


def _ps(argv: list[str]) -> int:
    import process_table  # type: ignore
    include_terminated = "-a" in argv or "--all" in argv
    sessions = process_table.list_sessions(include_terminated=include_terminated)
    print(process_table.format_ps_table(sessions))
    return 0


def _pipe(argv: list[str]) -> int:
    if not argv:
        return _err("Usage: pipe <list|create|write|read|rm|meta> [...]")
    import pipe_registry  # type: ignore
    sub = argv[0]
    rest = argv[1:]
    try:
        if sub == "list":
            pipes = pipe_registry.list_pipes()
            if not pipes:
                print("(no pipes)")
                return 0
            for p in pipes:
                seq = p.get("next_seq", 0)
                wc = p.get("write_count", 0)
                rc = p.get("read_count", 0)
                print(f"  {p['name']:20s} {p['type']:10s} "
                      f"next_seq={seq:<5d} writes={wc} reads={rc}")
            return 0
        if sub == "create":
            if not rest:
                return _err("Usage: pipe create <name> [<type>]")
            name = rest[0]
            ptype = rest[1] if len(rest) > 1 else "named"
            meta = pipe_registry.create_pipe(name, ptype)
            print(f"created pipe {meta['name']!r} type={meta['type']}")
            return 0
        if sub == "write":
            if len(rest) < 2:
                return _err("Usage: pipe write <name> <payload>")
            name = rest[0]
            payload_text = " ".join(rest[1:])
            try:
                payload = json.loads(payload_text)
            except json.JSONDecodeError:
                payload = payload_text  # fall back to raw string
            seq = pipe_registry.write(name, payload, writer="cli")
            print(f"wrote seq={seq}")
            return 0
        if sub == "read":
            if not rest:
                return _err("Usage: pipe read <name> [<subscriber_id>]")
            name = rest[0]
            sid = rest[1] if len(rest) > 1 else None
            msgs = pipe_registry.read(name, subscriber_id=sid)
            if not msgs:
                print("(no messages)")
                return 0
            for m in msgs:
                p = m.get("payload")
                if isinstance(p, (dict, list)):
                    p = json.dumps(p)
                print(f"  seq={m['seq']:<4d} {m['ts']}  {p}")
            return 0
        if sub == "rm":
            if not rest:
                return _err("Usage: pipe rm <name>")
            existed = pipe_registry.remove_pipe(rest[0])
            print("removed" if existed else "(not found)")
            return 0 if existed else 1
        if sub == "meta":
            if not rest:
                return _err("Usage: pipe meta <name>")
            meta = pipe_registry.get_meta(rest[0])
            if meta is None:
                return _err(f"pipe {rest[0]!r} does not exist")
            for k, v in sorted(meta.items()):
                print(f"  {k}: {v}")
            return 0
        return _err(f"unknown pipe subcommand: {sub}")
    except (FileExistsError, FileNotFoundError, KeyError, ValueError) as exc:
        return _err(f"{type(exc).__name__}: {exc}")


def _svc(argv: list[str]) -> int:
    if not argv:
        return _err("Usage: svc <list|deps|start|stop|restart|status|journal> [...]")
    import init  # type: ignore
    sub = argv[0]
    rest = argv[1:]

    # Phase 4.2 — start/stop/restart/status/journal/reload need the live
    # daemon; list/deps fall back to direct manifest read so the CLI is
    # still useful when the daemon isn't running.
    daemon_only = {"start", "stop", "restart", "status", "journal", "reload"}
    if sub in daemon_only:
        return _svc_daemon_call(sub, rest)

    # Discover services across operator/ for the read-only commands
    # bridges/shared → bridges → operator/. Service manifests live in
    # operator/{forge,voice,skill-forge,bridges/*}/*.service.yaml.
    plugins_dir = HERE.parent.parent
    try:
        services = init.discover_services([plugins_dir])
    except ValueError as exc:
        return _err(f"manifest discovery failed: {exc}")
    try:
        if sub == "list":
            # Try daemon first for live status; fall back to manifest-only
            from init import daemon_call as _dc  # type: ignore
            reply = _dc("list")
            if reply.get("ok"):
                rows = reply.get("services") or []
                print(f"  {'NAME':25s} {'STATUS':10s} {'PID':>7s} "
                      f"RESTARTS")
                print("  " + "-" * 60)
                for r in rows:
                    pid = r.get("pid") or "-"
                    print(f"  {r['name']:25s} {r['status']:10s} "
                          f"{str(pid):>7s} {r.get('restart_count', 0)}")
                return 0
            # Daemon not running — fall back to manifest-only listing
            order = init.topological_order(services)
            print(f"  {'NAME':25s} {'TYPE':10s} {'REQUIRES':30s} WANTS")
            print("  " + "-" * 85)
            for name in order:
                sd = services[name]
                req = ",".join(sd.requires) or "-"
                want = ",".join(sd.wants) or "-"
                print(f"  {name:25s} {sd.type:10s} {req:30s} {want}")
            print()
            print(f"  (daemon not running: {reply.get('error')!r} — "
                  f"showing manifest-only)")
            return 0
        if sub == "deps":
            if not rest:
                return _err("Usage: svc deps <name>")
            name = rest[0]
            if name not in services:
                return _err(f"unknown service: {name}")
            sd = services[name]
            print(f"service: {name}")
            print(f"  type:       {sd.type}")
            print(f"  exec_start: {sd.exec_start}")
            print(f"  restart:    {sd.restart} ({sd.backoff} backoff, "
                  f"max={sd.max_restarts})")
            print(f"  requires:   {sd.requires or '(none)'}")
            print(f"  wants:      {sd.wants or '(none)'}")
            if sd.hot_reload:
                print(f"  hot_reload: {sd.hot_reload}")
            return 0
        return _err(f"unknown svc subcommand: {sub}")
    except ValueError as exc:
        return _err(str(exc))


def _svc_daemon_call(sub: str, rest: list[str]) -> int:
    """Phase-4.2 — issue a daemon-only svc command via Unix socket."""
    from init import daemon_call as _dc  # type: ignore
    if sub == "journal":
        if not rest:
            return _err("Usage: svc journal <name> [N]")
        name = rest[0]
        n = "50"
        if len(rest) >= 2:
            n = rest[1]
        reply = _dc("journal", name, n)
    elif sub in ("start", "stop", "restart", "status", "reload"):
        if not rest:
            return _err(f"Usage: svc {sub} <name>")
        reply = _dc(sub, rest[0])
    else:
        return _err(f"unknown svc subcommand: {sub}")

    if not reply.get("ok"):
        return _err(reply.get("error") or "unknown error")
    if sub == "journal":
        for line in reply.get("lines") or []:
            print(line)
        return 0
    if sub == "status":
        st = reply.get("status") or {}
        for k, v in st.items():
            print(f"  {k}: {v}")
        return 0
    print(f"ok ({sub} {rest[0]})")
    return 0


def _budget(argv: list[str]) -> int:
    if not argv:
        return _err("Usage: budget <show|policy> [...]")
    import context_budget  # type: ignore
    sub = argv[0]
    rest = argv[1:]
    try:
        if sub == "show":
            if rest:
                rec = context_budget.get_budget(rest[0])
                if rec is None:
                    return _err(f"no budget for session {rest[0]!r}")
                print(context_budget.format_budget_table([rec]))
                return 0
            print(context_budget.format_budget_table(
                context_budget.list_budgets()
            ))
            return 0
        if sub == "policy":
            if len(rest) < 2:
                return _err("Usage: budget policy <session_id> <evict|compress|reject>")
            rec = context_budget.set_oom_policy(rest[0], rest[1])
            print(f"set policy={rec['oom_policy']} for {rec['session_id']}")
            return 0
        return _err(f"unknown budget subcommand: {sub}")
    except (KeyError, ValueError) as exc:
        return _err(f"{type(exc).__name__}: {exc}")


def _debug(argv: list[str]) -> int:
    """Layer-21 debug-channel CLI — let the agent self-test through the
    messenger when the chat has /debug on.

    Subcommands:
      status            — show whether debug is enabled for the current chat
                          (CORVIN_CHANNEL_ID env var)
      send <text>       — write an outbox envelope to the current chat
                          (denied unless debug-enabled + under rate limit)

    Loop / abuse guards:
      - chat must have ``debug: true`` in chat_profiles (set via /debug on)
      - per-chat rate limit (10 messages / 60 s sliding window)
      - audit: every send writes a ``bridge.debug_message`` event
      - depth guard via CORVIN_DEBUG_DEPTH env (refused at depth >= 3)
    """
    if not argv:
        return _err("Usage: debug <status|send> [...]")
    sub = argv[0]
    rest = argv[1:]

    chat_id = (os.environ.get("CORVIN_CHANNEL_ID") or os.environ.get("CORVIN_CHANNEL_ID", "")).strip()
    if not chat_id:
        return _err(
            "CORVIN_CHANNEL_ID env not set — debug commands only work "
            "from inside an adapter-spawned claude subprocess."
        )

    if sub == "status":
        enabled, reason = _debug_state_for_chat(chat_id)
        if enabled:
            print(f"debug enabled for chat {chat_id!r}")
        else:
            print(f"debug disabled for chat {chat_id!r} ({reason})")
        return 0

    if sub == "send":
        if not rest:
            return _err("Usage: debug send <text>")
        text = " ".join(rest)
        # Depth guard: prevent runaway echo loops
        try:
            depth = int(os.environ.get("CORVIN_DEBUG_DEPTH") or os.environ.get("CORVIN_DEBUG_DEPTH", "0"))
        except ValueError:
            depth = 0
        if depth >= 3:
            return _err(f"debug depth {depth} >= 3 — refusing to send")
        # State check: chat must be debug-enabled
        enabled, reason = _debug_state_for_chat(chat_id)
        if not enabled:
            return _err(f"debug not enabled for {chat_id!r} ({reason}); "
                        f"toggle via /debug on")
        # Rate limit
        if not _debug_rate_limit_ok(chat_id, limit=10, window_s=60):
            return _err(f"debug rate-limit exceeded for {chat_id!r} "
                        f"(>10 messages in last 60s)")
        # Write the outbox envelope
        try:
            _debug_send_outbox(chat_id, text)
        except Exception as exc:
            return _err(f"send failed: {exc}", code=2)
        # Audit
        try:
            _debug_audit(chat_id, text, depth)
        except Exception:
            pass  # best-effort
        print(f"sent ({len(text)} chars) to {chat_id}")
        return 0

    return _err(f"unknown debug subcommand: {sub}")


def _debug_state_for_chat(chat_id: str) -> tuple[bool, str]:
    """Returns (enabled, reason). Reads bridges/<channel>/settings.json fresh."""
    parts = chat_id.split(":", 1)
    if len(parts) != 2:
        return False, "invalid CORVIN_CHANNEL_ID format"
    channel, sanitised_chat = parts
    settings_path = (HERE.parent / channel / "settings.json")
    if not settings_path.exists():
        return False, f"no settings.json for channel {channel!r}"
    try:
        with settings_path.open("r", encoding="utf-8") as fh:
            settings = json.load(fh)
    except (json.JSONDecodeError, OSError) as exc:
        return False, f"settings load failed: {exc}"
    profiles = settings.get("chat_profiles") or {}
    # CORVIN_CHANNEL_ID has the chat_key sanitised, but settings is keyed
    # by the original chat_key. The adapter sets both; we accept whichever
    # match.
    profile = profiles.get(sanitised_chat)
    if profile is None:
        # Try without sanitisation — find any profile whose normalised key matches
        for k, v in profiles.items():
            if _sanitise_chat(k) == sanitised_chat:
                profile = v
                break
    if not isinstance(profile, dict):
        return False, "no chat_profile found"
    if profile.get("debug") is True:
        return True, "ok"
    return False, "debug flag not set on chat_profile"


def _sanitise_chat(chat_key: str) -> str:
    """Mirror adapter's _sanitise_chat_key — replace path-unsafe chars."""
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in chat_key)


# --- rate limit -------------------------------------------------------------

def _debug_state_dir() -> Path:
    from paths import corvin_home  # type: ignore
    d = corvin_home() / "run" / "debug"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _debug_rate_limit_ok(chat_id: str, *, limit: int, window_s: int) -> bool:
    """Sliding-window rate limit. Stores timestamps in a per-chat JSON file."""
    safe_id = _sanitise_chat(chat_id)
    state_path = _debug_state_dir() / f"{safe_id}.rate.json"
    now = time.time()
    history: list[float] = []
    if state_path.exists():
        try:
            history = json.loads(state_path.read_text(encoding="utf-8"))
            if not isinstance(history, list):
                history = []
        except (json.JSONDecodeError, OSError):
            history = []
    # prune entries outside window
    history = [t for t in history if now - t < window_s]
    if len(history) >= limit:
        return False
    history.append(now)
    try:
        state_path.write_text(
            json.dumps(history, separators=(",", ":")),
            encoding="utf-8",
        )
    except OSError:
        pass
    return True


# --- outbox write -----------------------------------------------------------

def _debug_send_outbox(chat_id: str, text: str) -> None:
    """Write an outbox envelope so the bridge daemon picks it up and
    pushes the message to the chat. Mirrors the adapter's normal
    outbox envelope shape."""
    from paths import corvin_home  # type: ignore  # noqa: F401
    parts = chat_id.split(":", 1)
    if len(parts) != 2:
        raise ValueError(f"invalid CORVIN_CHANNEL_ID: {chat_id}")
    channel, sanitised_chat = parts
    # Outbox lives under bridges/<channel>/outbox/ (same dir layout the
    # daemons watch). Allow override via ADAPTER_OUTBOX env (test path).
    outbox_root = os.environ.get("ADAPTER_OUTBOX")
    if outbox_root:
        outbox_dir = Path(outbox_root)
    else:
        outbox_dir = HERE.parent / channel / "outbox"
    outbox_dir.mkdir(parents=True, exist_ok=True)
    msg_id = f"debug_{int(time.time() * 1000)}_{os.urandom(2).hex()}"
    payload = {
        "msg_id": msg_id,
        "chat_id": sanitised_chat,
        "text": text,
        "channel": channel,
        "_debug": True,
        "ts": time.time(),
    }
    out_path = outbox_dir / f"{msg_id}.json"
    tmp = out_path.with_suffix(out_path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    tmp.replace(out_path)


def _debug_audit(chat_id: str, text: str, depth: int) -> None:
    """Append a bridge.debug_message event to the unified audit chain.
    Best-effort — never raises."""
    try:
        # Reuse the existing forge audit chain when forge is on the path.
        forge_root = HERE.parent.parent / "forge"
        if forge_root.is_dir():
            sys.path.insert(0, str(forge_root))
        from forge.security_events import write_event  # type: ignore
        write_event(
            event_type="bridge.debug_message",
            details={
                "chat_id": chat_id,
                "len": len(text),
                "snippet": text[:200],
                "truncated": len(text) > 200,
                "depth": depth,
            },
        )
    except Exception:
        pass  # forge unavailable — silent no-op


def _kill(argv: list[str]) -> int:
    """Layer-17 Phase-4.1 — terminate a running session by id.

    Usage:
        kill <session_id>          send SIGTERM to the process group
        kill -9 <session_id>       send SIGKILL (force)

    Looks up the session in the process table, resolves its pid (set
    by the adapter at register time), and signals the whole process
    group (`os.killpg`). The adapter's existing `finally` cleanup
    handles the deregister + workspace teardown when the subprocess
    exits — no extra adapter wiring needed.
    """
    import signal as sig_mod
    import process_table  # type: ignore

    if not argv:
        return _err("Usage: kill [-9] <session_id>")

    sig = sig_mod.SIGTERM
    args = list(argv)
    if args[0] == "-9":
        sig = sig_mod.SIGKILL
        args.pop(0)
    if not args:
        return _err("Usage: kill [-9] <session_id>")

    session_id = args[0]
    rec = process_table.get_session(session_id)
    if rec is None:
        return _err(f"unknown session: {session_id!r}")
    if rec.get("status") in ("exited", "killed"):
        return _err(
            f"session {session_id} already {rec['status']!r} "
            f"(rc={rec.get('exit_code')})"
        )
    pid = rec.get("pid")
    if not isinstance(pid, int) or pid <= 0:
        return _err(f"no pid recorded for session {session_id!r}; "
                    f"adapter may pre-date Phase-4.1")
    try:
        os.killpg(pid, sig)
    except ProcessLookupError:
        # Process already gone — registry will reflect the exit on
        # next adapter poll. Mark it as killed in the registry now so
        # /ps -a shows the operator-issued kill.
        try:
            process_table.deregister_session(
                session_id, exit_reason="killed", keep=True,
            )
        except Exception:
            pass
        return _err(f"process {pid} already gone (registry updated)")
    except PermissionError as exc:
        return _err(f"permission denied killing pid {pid}: {exc}")
    print(f"sent {sig.name} to pgid {pid} (session {session_id})")
    return 0


def _nice(argv: list[str]) -> int:
    """Layer-17 Phase-4.1 — adjust the nice value of a session.

    Usage:
        nice <session_id> <±N>

    Updates the session's nice value in the process table (range
    -20..+19, lower = higher priority). The value is observability-
    only today — actual scheduler-level nice() is Phase-4.2.
    """
    import process_table  # type: ignore

    if len(argv) < 2:
        return _err("Usage: nice <session_id> <±N>")
    session_id = argv[0]
    try:
        nice_val = int(argv[1])
    except ValueError:
        return _err(f"nice must be an integer, got {argv[1]!r}")
    if not (-20 <= nice_val <= 19):
        return _err(f"nice out of range [-20, 19]: {nice_val}")

    rec = process_table.get_session(session_id)
    if rec is None:
        return _err(f"unknown session: {session_id!r}")
    try:
        process_table.update_session(session_id, nice=nice_val)
    except Exception as exc:
        return _err(f"update failed: {exc}", code=2)
    print(f"set nice={nice_val} for session {session_id}")
    return 0


def _sig(argv: list[str]) -> int:
    """Layer-17 Phase-4.1.5 — send a custom signal to a session.

    Usage:
        sig <session_id> <KILL|PLAN|SUMMARIZE|CONTEXT_DROP|QUIET|RESUME>

    Resolves the session via the process table, finds its chat_key,
    and writes a side-channel _signal envelope to that channel's
    inbox. The adapter's process_one handles the envelope:

      - KILL                 SIGTERM the chat's running subprocess
                             (same effect as /cancel, addressable
                             cross-chat by session_id)
      - PLAN/SUMMARIZE/...   inject a "[CORVIN_SIGNAL: <name>]"
                             marker (combined with the legacy
                             "[CORVIN_SIGNAL: <name>]" alias on
                             the same line until rebrand-Phase 7)
                             via a magic-prefix stream-json user
                             message into the running subprocess;
                             the persona's append_system interprets
                             it. Unknown markers are treated as
                             ambient text by the model — graceful
                             no-op.

    Returns 0 if the envelope was written (delivery is confirmed
    asynchronously via the bot's reply ack); 1 on validation error.
    """
    import process_table  # type: ignore
    if len(argv) < 2:
        return _err("Usage: sig <session_id> <KILL|PLAN|SUMMARIZE|CONTEXT_DROP|QUIET|RESUME>")
    session_id = argv[0]
    signal_name = argv[1].upper()
    valid_signals = {
        "KILL", "PLAN", "SUMMARIZE", "CONTEXT_DROP", "QUIET", "RESUME",
    }
    if signal_name not in valid_signals:
        return _err(
            f"unknown signal {signal_name!r}; valid: {sorted(valid_signals)}"
        )
    rec = process_table.get_session(session_id)
    if rec is None:
        return _err(f"unknown session: {session_id!r}")
    chat_key = rec.get("chat_key", "")
    # Determine channel from chat_key shape (bridge:chat_id) or fall back
    # to the env CORVIN_CHANNEL_ID (legacy: CORVIN_CHANNEL_ID).
    if ":" in chat_key:
        channel, target_chat_id = chat_key.split(":", 1)
    else:
        env_id = os.environ.get("CORVIN_CHANNEL_ID") or os.environ.get("CORVIN_CHANNEL_ID", "")
        if ":" in env_id:
            channel = env_id.split(":", 1)[0]
            target_chat_id = chat_key
        else:
            return _err("could not resolve channel from chat_key or env")
    inbox_root = os.environ.get("ADAPTER_INBOX")
    if inbox_root:
        inbox_dir = Path(inbox_root)
    else:
        # Production layout: bridges/<channel>/inbox/
        inbox_dir = HERE.parent / channel / "inbox"
    inbox_dir.mkdir(parents=True, exist_ok=True)
    msg_id = f"sig_{int(time.time() * 1000)}_{os.urandom(2).hex()}"
    envelope = {
        "msg_id": msg_id,
        "channel": channel,
        "from": "_cli_sig",
        "chat_id": target_chat_id,
        "_signal": True,
        "session_id": session_id,
        "signal": signal_name,
        "ts": time.time(),
    }
    out_path = inbox_dir / f"{msg_id}.json"
    tmp = out_path.with_suffix(out_path.suffix + ".tmp")
    tmp.write_text(json.dumps(envelope, ensure_ascii=False), encoding="utf-8")
    tmp.replace(out_path)
    print(f"sent signal {signal_name} to session {session_id} (chat={chat_key})")
    return 0


def _help() -> int:
    print(__doc__)
    return 0


COMMANDS = {
    "ps": _ps,
    "pipe": _pipe,
    "svc": _svc,
    "budget": _budget,
    "debug": _debug,
    "kill": _kill,
    "nice": _nice,
    "sig": _sig,
    "help": lambda _argv: _help(),
    "-h": lambda _argv: _help(),
    "--help": lambda _argv: _help(),
}


def main(argv: list[str]) -> int:
    if not argv:
        return _help()
    head = argv[0]
    fn = COMMANDS.get(head)
    if fn is None:
        return _err(f"unknown command: {head}\nrun 'phase3_cli.py help' for usage")
    return fn(argv[1:])


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
