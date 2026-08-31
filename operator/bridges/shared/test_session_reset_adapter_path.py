#!/usr/bin/env python3
"""Regression test: /new must clear the session state the ADAPTER actually reads.

Root cause this pins down (2026-08-28, Discord channel 1501315335750684803):
``session_reset._wipe_voice_state`` hand-built ``<corvin_home>/voice/sessions/
<channel>/<chat>``, while ``adapter._session_dir()`` resolves through the
tenant-aware SSOT ``paths.voice_session_dir()`` →
``<corvin_home>/tenants/<tid>/sessions/voice/<channel>/<chat>``.

The two paths never coincided under any configuration, so ``/new`` rmtree'd a
directory that did not exist and reported ``voice_state_removed: no``, while
``.main_session.json`` survived. The next turn read that file and spawned
``claude --resume <old session id>`` — the conversation continued verbatim.

Cases:
  1. The adapter's real session dir is cleared of Claude conversation state.
  2. Project files in that same dir SURVIVE (the reset reply promises this).
  3. Idempotent — a second reset on an already-clean chat is a no-op.
  4. The legacy (pre-ADR-0007) session dir is cleared too.

Run: python3 operator/bridges/shared/test_session_reset_adapter_path.py
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SESSION_RESET_PY = ROOT / "session_reset.py"

PASS = 0
FAIL = 0


def ok(msg: str) -> None:
    global PASS
    PASS += 1
    print(f"PASS: {msg}")


def bad(msg: str) -> None:
    global FAIL
    FAIL += 1
    print(f"FAIL: {msg}")


def eq(actual, expected, msg: str) -> None:
    if actual == expected:
        ok(msg)
    else:
        bad(f"{msg} — expected {expected!r}, got {actual!r}")


# ── helpers ────────────────────────────────────────────────────────────────

def _sandbox(tag: str) -> Path:
    d = Path(tempfile.mkdtemp(prefix=f"corvin-reset-path-{tag}-"))
    (d / "home").mkdir()
    return d


def _adapter_session_dir(home: Path, channel: str, chat_id: str) -> Path:
    """Resolve exactly like adapter._session_dir() does — via the SSOT."""
    code = (
        "import sys, os\n"
        f"sys.path.insert(0, {str(ROOT)!r})\n"
        "import paths\n"
        "safe = ''.join(c if c.isalnum() else '_' for c in sys.argv[2])[:64] or 'anon'\n"
        "print(paths.voice_session_dir(sys.argv[1], safe))\n"
    )
    env = dict(os.environ)
    env["CORVIN_HOME"] = str(home)
    env.pop("CORVIN_TENANT_ID", None)
    out = subprocess.run(
        [sys.executable, "-c", code, channel, chat_id],
        env=env, capture_output=True, text=True, check=True,
    ).stdout.strip()
    return Path(out)


def _seed_session(d: Path) -> None:
    """Seed what the adapter writes: conversation state + project files."""
    d.mkdir(parents=True, exist_ok=True)
    # Claude conversation state — MUST be removed by /new.
    (d / ".main_session.json").write_text(
        json.dumps({"session_id": "1e53620a-335b-4a5c-a0fa-c1d08c9d3d82",
                    "saved_at": "2026-08-28T06:55:48Z"})
    )
    (d / ".session_started").touch()
    (d / ".claude.json").write_text('{"sessionId":"abc"}')
    cdir = d / ".claude"
    cdir.mkdir(exist_ok=True)
    (cdir / "history.jsonl").write_text('{"role":"user"}\n')
    # Project files — MUST survive (the /new reply promises exactly this).
    (d / "notes.md").write_text("# my project notes\n")
    (d / "outputs").mkdir(exist_ok=True)
    (d / "outputs" / "chart.png").write_text("png")


def _has_claude_state(d: Path) -> bool:
    """Mirror adapter's own has_session probe + the --resume read."""
    return (
        any(d.glob(".claude*"))
        or (d / ".session_started").exists()
        or (d / ".main_session.json").exists()
    )


def _call_reset(home: Path, channel: str, chat_id: str) -> dict:
    env = dict(os.environ)
    env["CORVIN_HOME"] = str(home)
    env.pop("CORVIN_TENANT_ID", None)
    r = subprocess.run(
        [sys.executable, str(SESSION_RESET_PY),
         "--channel", channel, "--chat-id", chat_id, "--reason", "manual"],
        env=env, capture_output=True, text=True,
    )
    if r.returncode != 0:
        raise AssertionError(f"session_reset exited {r.returncode}: {r.stderr[:600]}")
    return json.loads(r.stdout.strip())


# ── case 1+2: adapter path cleared, project files survive ──────────────────

def case_01_adapter_path_cleared() -> None:
    print("\n=== case 1: /new clears the session dir the adapter actually reads ===")
    sb = _sandbox("01")
    home = sb / "home"
    chat = "1501315335750684803"
    d = _adapter_session_dir(home, "discord", chat)
    _seed_session(d)
    eq(_has_claude_state(d), True, "seeded: adapter would resume this session")

    out = _call_reset(home, "discord", chat)

    eq(out["voice_state_removed"], True, "reset reports voice state removed")
    eq((d / ".main_session.json").exists(), False,
       ".main_session.json gone (no --resume of the old session)")
    eq((d / ".session_started").exists(), False, ".session_started gone")
    eq((d / ".claude.json").exists(), False, ".claude.json gone")
    eq((d / ".claude").exists(), False, ".claude/ gone")
    eq(_has_claude_state(d), False,
       "adapter's has_session probe is now False — next turn starts fresh")

    print("\n=== case 2: project files in the same dir survive ===")
    eq((d / "notes.md").exists(), True, "notes.md kept (reset reply promises this)")
    eq((d / "outputs" / "chart.png").exists(), True, "outputs/chart.png kept")
    shutil.rmtree(sb, ignore_errors=True)


# ── case 3: idempotent ─────────────────────────────────────────────────────

def case_03_idempotent() -> None:
    print("\n=== case 3: second reset is a no-op ===")
    sb = _sandbox("03")
    home = sb / "home"
    chat = "chatIdem"
    d = _adapter_session_dir(home, "discord", chat)
    _seed_session(d)

    first = _call_reset(home, "discord", chat)
    eq(first["voice_state_removed"], True, "first call: state removed")
    second = _call_reset(home, "discord", chat)
    eq(second["voice_state_removed"], False, "second call: nothing left to remove")
    eq(second["failures"], [], "second call: no failures")
    eq((d / "notes.md").exists(), True, "project file still kept after 2 resets")
    shutil.rmtree(sb, ignore_errors=True)


# ── case 4: legacy pre-ADR-0007 layout ─────────────────────────────────────

def case_04_legacy_path_cleared() -> None:
    print("\n=== case 4: legacy (pre-ADR-0007) session dir is cleared too ===")
    sb = _sandbox("04")
    home = sb / "home"
    chat = "chatLegacy"
    # adapter._session_dir()'s migration branch: SESSIONS_ROOT = voice_dir()/sessions
    legacy = home / "tenants" / "_default" / "voice" / "sessions" / "discord" / chat
    _seed_session(legacy)

    out = _call_reset(home, "discord", chat)
    eq(out["voice_state_removed"], True, "reset reports legacy state removed")
    eq(_has_claude_state(legacy), False, "legacy dir has no Claude state left")
    eq((legacy / "notes.md").exists(), True, "legacy dir project file kept")
    shutil.rmtree(sb, ignore_errors=True)


def main() -> int:
    case_01_adapter_path_cleared()
    case_03_idempotent()
    case_04_legacy_path_cleared()
    print(f"\n{'=' * 60}\nPASS: {PASS}  FAIL: {FAIL}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
