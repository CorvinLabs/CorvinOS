"""E2E: session content survives compaction and unwanted resets (session ledger).

Drives the REAL entry points: the adapter process consumes inbox items
(``process_one``), records each turn in the chat's ledger and builds the next
spawn's argv (captured via ``ADAPTER_FAKE_ARGS_DUMP``); resets go through the
real ``session_reset.py`` CLI that ``/new`` and the inactivity sweep call. The
CLI transcript is written in the real Claude Code JSONL format, including a
``compact_boundary`` record shaped like the one measured on this install's
Discord bridge (197 886 → 12 287 tokens).

Scenario, one chat:
  1  ALPHA                      → no history block (nothing recorded yet)
  2  BRAVO, transcript holds A  → no history block (A is live)
  3  CHARLIE, transcript compacted after A,B → A and B re-supplied verbatim
  4  timeout reset, DELTA       → A, B, C re-supplied, reset labelled
  5  /new (manual), ECHO        → fresh start: nothing re-supplied
  6  FOXTROT                    → ECHO re-supplied, A–D not, fence noted
Then: the ledger holds all six turns verbatim plus three boundaries, and the
sandbox audit chain carries the session_ledger events.

Run: python3 corvin_operator/bridges/shared/test_session_ledger_e2e.py
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ADAPTER = ROOT / "adapter.py"
RESET = ROOT / "session_reset.py"
CHANNEL = "telegram"
CHAT = "ledger-e2e-chat"
SID = "5e55a1b2-0000-4000-8000-00000000e2e1"
HEADER = "## Chat history this session does not hold"

MSGS = {
    "A": "ALPHA-7781 which river runs through the old town?",
    "B": "BRAVO-4410 remember that my build number is 7720",
    "C": "CHARLIE-9902 and what did I say my build number was?",
    "D": "DELTA-3315 continue where we left off",
    "E": "ECHO-6620 new topic: plan a garden",
    "F": "FOXTROT-1180 what was the first thing in this new topic?",
}


def _env(sb: Path) -> dict:
    env = os.environ.copy()
    # Under pytest the bridge conftest exports VOICE_AUDIT_PATH (it outranks
    # FORGE_ROOT); the adapter must write THIS sandbox's chain, not that one.
    env.pop("VOICE_AUDIT_PATH", None)
    env.update({
        "ADAPTER_INBOX": str(sb / "inbox"), "ADAPTER_OUTBOX": str(sb / "outbox"),
        "ADAPTER_PROCESSED": str(sb / "processed"),
        "ADAPTER_FAKE_CLAUDE": "1", "ADAPTER_FAKE_DELAY": "0.05",
        "ADAPTER_FAKE_ARGS_DUMP": str(sb / "args.jsonl"),
        "ADAPTER_POLL_INTERVAL": "0.1", "ADAPTER_BRIDGES_DIR": str(sb / "bridges"),
        "ADAPTER_DISABLE_VOICE": "1", "BRIDGE_PROGRESS_UPDATES": "0",
        "ADAPTER_ROUTING_MODE": "off",
        "CORVIN_HOME": str(sb / "home"), "XDG_CONFIG_HOME": str(sb / "xdg"),
        "FORGE_ROOT": str(sb / "forge-root"),
        "CORVIN_AUDIT_ANCHOR_KEY": str(sb / "anchor.key"),
        "CORVIN_PLUGIN_SLOT_DIR": str(sb / "slot"), "CORVIN_PROJECT_ROOT": "",
        "COWORK_USER_DIR": str(sb / "cowork-user"), "COWORK_MCP_CACHE": str(sb / "mcp-cache"),
        "CLAUDE_CONFIG_DIR": str(sb / "claude-cfg"),
        "CORVIN_TENANT_ID": "_default",
    })
    return env


def _send(sb: Path, key: str, n: int) -> None:
    inbox = sb / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    item = {"id": f"m{n}-{key}", "channel": CHANNEL, "chat_id": CHAT,
            "from": CHAT, "text": MSGS[key]}
    (inbox / f"m{n}-{key}.json").write_text(json.dumps(item))
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        if len(list((sb / "processed").glob("*.json"))) >= n:
            return
        time.sleep(0.05)
    raise AssertionError(f"turn {n} ({key}) was not processed; see {sb / 'adapter.log'}")


def _system_prompt(sb: Path, n: int) -> str:
    calls = [json.loads(l) for l in (sb / "args.jsonl").read_text().splitlines() if l.strip()]
    args = calls[n - 1]["args"]
    if "--append-system-prompt" in args:
        return args[args.index("--append-system-prompt") + 1]
    return Path(args[args.index("--append-system-prompt-file") + 1]).read_text(encoding="utf-8")


def _workdir(sb: Path) -> Path:
    hits = list((sb / "home").rglob(".corvin-ledger/ledger.jsonl"))
    assert len(hits) == 1, hits
    return hits[0].parent.parent


def _write_transcript(sb: Path, wd: Path, entries: list[dict]) -> None:
    proj = sb / "claude-cfg" / "projects" / re.sub(r"[^A-Za-z0-9]", "-", str(wd.resolve()))
    proj.mkdir(parents=True, exist_ok=True)
    (proj / f"{SID}.jsonl").write_text("".join(json.dumps(e) + "\n" for e in entries))
    (wd / ".main_session.json").write_text(json.dumps({"session_id": SID}))
    (wd / ".session_started").touch()


def _user(text: str) -> dict:
    # Exactly how the engine frames a turn (agents/claude_code.guard_prompt_head).
    return {"type": "user", "message": {"role": "user", "content": "User input:\n" + text}}


def _reset(env: dict, reason: str) -> None:
    r = subprocess.run([sys.executable, str(RESET), "--channel", CHANNEL, "--chat-id", CHAT,
                        "--reason", reason], env=env, capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr


def main() -> int:
    sb = Path(tempfile.mkdtemp(prefix="ledger-e2e-"))
    env = _env(sb)
    (sb / "bridges" / CHANNEL).mkdir(parents=True)
    (sb / "bridges" / CHANNEL / "settings.json").write_text(json.dumps({"chat_profiles": {CHAT: {}}}))
    proc = subprocess.Popen([sys.executable, str(ADAPTER)], env=env,
                            stdout=open(sb / "adapter.log", "a"), stderr=subprocess.STDOUT)
    failures: list[str] = []

    def check(cond: bool, msg: str) -> None:
        print(("  ok   " if cond else "  FAIL ") + msg)
        if not cond:
            failures.append(msg)

    try:
        time.sleep(0.5)
        print("1 ALPHA — first turn")
        _send(sb, "A", 1)
        check(HEADER not in _system_prompt(sb, 1), "no history block before anything is recorded")
        wd = _workdir(sb)

        print("2 BRAVO — the live transcript holds ALPHA")
        _write_transcript(sb, wd, [_user(MSGS["A"])])
        _send(sb, "B", 2)
        check(HEADER not in _system_prompt(sb, 2), "a turn the transcript holds is not re-supplied")

        print("3 CHARLIE — the CLI compacted after ALPHA and BRAVO")
        _write_transcript(sb, wd, [
            _user(MSGS["A"]), _user(MSGS["B"]),
            {"type": "system", "subtype": "compact_boundary", "uuid": "cb-e2e-1",
             "timestamp": "2026-10-02T12:00:00Z",
             "compactMetadata": {"trigger": "auto", "preTokens": 197886, "postTokens": 12287}},
        ])
        sp = _system_prompt(sb, 2)  # sanity: the previous spawn stays unaffected
        _send(sb, "C", 3)
        sp = _system_prompt(sb, 3)
        check(MSGS["A"] in sp and MSGS["B"] in sp, "compacted turns ALPHA+BRAVO re-supplied verbatim")
        check("build number is 7720" in sp, "the fact from BRAVO reaches the next spawn")

        print("4 DELTA — after an inactivity-timeout reset (unwanted)")
        _reset(env, "timeout")
        check(not (wd / ".main_session.json").exists(), "the reset wiped the CLI session state")
        _send(sb, "D", 4)
        sp = _system_prompt(sb, 4)
        check(all(MSGS[k] in sp for k in "ABC"), "ALPHA, BRAVO, CHARLIE re-supplied into the fresh session")
        check("session reset (timeout)" in sp, "the reset is labelled in the view")
        check("compacted the conversation (197886 → 12287 tokens)" in sp, "the compaction is labelled")

        print("5 ECHO — after an explicit /new (manual)")
        (wd / ".session_started").touch()  # the session DELTA created
        _reset(env, "manual")
        _send(sb, "E", 5)
        sp5 = _system_prompt(sb, 5)
        check(HEADER not in sp5 and not any(MSGS[k] in sp5 for k in "ABCD"),
              "/new starts fresh: nothing re-supplied")
        check("before the operator's /new" in sp5, "/new leaves one pointer to the ledger file")

        print("6 FOXTROT — the new topic continues")
        _send(sb, "F", 6)
        sp = _system_prompt(sb, 6)
        check(MSGS["E"] in sp, "ECHO (since /new, not in a live transcript) re-supplied")
        check(not any(MSGS[k] in sp for k in "ABCD"), "turns before /new are not re-supplied")
        check("started over with /new" in sp and "4 turn(s) before that" in sp,
              "the fence and the ledger file are named")

        print("7 the record itself")
        recs = [json.loads(l) for l in (wd / ".corvin-ledger" / "ledger.jsonl").read_text().splitlines()]
        turns = [r for r in recs if r["kind"] == "turn"]
        check([r["n"] for r in turns] == [1, 2, 3, 4, 5, 6], "six turns, numbered 1..6")
        check([r["user"] for r in turns] == [MSGS[k] for k in "ABCDEF"], "every user text kept verbatim")
        check(all(r["assistant"].startswith("[fake") and MSGS[k][:20] in r["assistant"]
                  for r, k in zip(turns, "ABCDEF")), "every answer kept")
        bounds = [(r["boundary"], r["reason"]) for r in recs if r["kind"] == "boundary"]
        check(bounds == [("compaction", "auto"), ("reset", "timeout"), ("reset", "manual")],
              f"boundaries recorded in order: {bounds}")
        check(oct((wd / ".corvin-ledger" / "ledger.jsonl").stat().st_mode & 0o777) == "0o600",
              "ledger file is 0600")

        print("8 audit chain (sandbox)")
        # Tests redirect the chain via FORGE_ROOT (CLAUDE.md § Testing); the
        # adapter and session_reset processes may each resolve a sandbox file.
        chains = list(sb.rglob("audit.jsonl"))
        events = []
        for c in chains:
            for l in c.read_text().splitlines():
                try:
                    events.append(json.loads(l))
                except json.JSONDecodeError:
                    pass
        kinds = [e.get("event_type") or e.get("event") for e in events]
        check(kinds.count("session_ledger.boundary") == 3, "three session_ledger.boundary records")
        check("session_ledger.context_resupplied" in kinds, "context_resupplied recorded")
        ledger_events = [e for e in events
                         if str(e.get("event_type") or e.get("event")).startswith("session_ledger")]
        check(len(ledger_events) >= 4, f"session_ledger records present ({len(ledger_events)})")
        blob = json.dumps(ledger_events)
        check(bool(ledger_events) and not any(MSGS[k].split()[0] in blob for k in MSGS),
              "no turn text in the session_ledger audit records")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
    if failures:
        print(f"\nFAILED ({len(failures)}); sandbox kept at {sb}")
        return 1
    shutil.rmtree(sb, ignore_errors=True)
    print("\nPASS")
    return 0


def test_session_ledger_e2e():
    assert main() == 0


if __name__ == "__main__":
    sys.exit(main())
