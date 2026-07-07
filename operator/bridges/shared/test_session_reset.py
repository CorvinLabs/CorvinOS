#!/usr/bin/env python3
"""End-to-End test: reset_session purges every chat-bound layer.

Eight cases:
  1. Reset clears canonical skill SKILL.md AND slot mirror.
  2. Reset clears forge tool registry + impl files.
  3. Reset clears voice conversation state directory.
  4. Idempotent — second call returns counts of 0.
  5. Reset on never-existed session — counts all 0, no error,
     audit event still written.
  6. After multiple resets, verify_chain(audit_path) returns valid.
  7. Timeout sweep: two sessions (fresh + 8d-stale) — only stale purged,
     audit event_type is session.timeout.
  8. Engine-E2E (opt-in SKILL_FORGE_ENGINE_E2E=1) — see docstring at
     case_engine_e2e for the full flow.

Run: python3 operator/bridges/shared/test_session_reset.py
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SESSION_RESET_PY = ROOT / "session_reset.py"
TIMEOUT_SWEEP_PY = ROOT.parent.parent / "voice" / "scripts" / "session_timeout_sweep.py"
REPO = ROOT.parent.parent.parent  # ADR-0035: operator/bridges/shared/ -> repo
SKILL_FORGE_PKG = REPO / "operator" / "skill-forge"
FORGE_PKG = REPO / "operator" / "forge"


# ── PASS/FAIL counter ───────────────────────────────────────────────────────
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


# ── helpers ─────────────────────────────────────────────────────────────────


def _make_sandbox(prefix: str) -> Path:
    return Path(tempfile.mkdtemp(prefix=f"session-reset-{prefix}-"))


def _child_env(home: Path, slot: Path) -> dict:
    env = os.environ.copy()
    env["CORVIN_HOME"] = str(home)
    env["CORVIN_PLUGIN_SLOT_DIR"] = str(slot)
    env["CORVIN_FORCE_SCOPE"] = "session"
    return env


def _run_helper(home: Path, slot: Path, code: str, *args: str) -> str:
    """Run a child process with CORVIN_HOME redirected. Returns stdout."""
    env = _child_env(home, slot)
    full = (
        "import sys, os\n"
        f"sys.path.insert(0, {str(FORGE_PKG)!r})\n"
        f"sys.path.insert(0, {str(SKILL_FORGE_PKG)!r})\n"
        f"sys.path.insert(0, {str(ROOT)!r})\n"
        + code
    )
    r = subprocess.run(
        [sys.executable, "-c", full, *args],
        env=env, capture_output=True, text=True, check=True,
    )
    return r.stdout


def _create_skill(home: Path, slot: Path, *, channel_id: str,
                  name: str, body: str, description: str,
                  grade: float | None) -> None:
    code = (
        "import sys, os, json\n"
        "from skill_forge.multi_registry import MultiSkillRegistry\n"
        "os.environ['CORVIN_CHANNEL_ID'] = sys.argv[1]\n"
        "payload = json.loads(sys.argv[2])\n"
        "mr = MultiSkillRegistry(channel_id=sys.argv[1])\n"
        "mr.create(name=payload['name'], type='domain',\n"
        "          body_md=payload['body'],\n"
        "          description=payload['description'],\n"
        "          claim={'predicted_delta_loss': 0.1}, scope='session')\n"
        "if payload.get('grade') is not None:\n"
        "    mr.grade(payload['name'], 'run-1', float(payload['grade']))\n"
    )
    payload = {"name": name, "body": body, "description": description,
               "grade": grade}
    _run_helper(home, slot, code, channel_id, json.dumps(payload))


def _create_forge_tool(home: Path, slot: Path, *, channel_id: str,
                       name: str) -> None:
    code = (
        "import sys, os\n"
        "from forge.scope import scope_root\n"
        "from forge.registry import Registry\n"
        "os.environ['CORVIN_CHANNEL_ID'] = sys.argv[1]\n"
        "root = scope_root('session', channel_id=sys.argv[1])\n"
        "root.mkdir(parents=True, exist_ok=True)\n"
        "reg = Registry(root)\n"
        "reg.create(name=sys.argv[2], description='demo',\n"
        "           input_schema={'type':'object'},\n"
        "           impl='print(\"hi\")', runtime='python', scope='session')\n"
    )
    _run_helper(home, slot, code, channel_id, name)


def _call_reset(home: Path, slot: Path, *, channel: str,
                chat_id: str, reason: str = "manual") -> dict:
    env = _child_env(home, slot)
    r = subprocess.run(
        [sys.executable, str(SESSION_RESET_PY),
         "--channel", channel, "--chat-id", chat_id, "--reason", reason],
        env=env, capture_output=True, text=True, check=True,
    )
    return json.loads(r.stdout.strip())


def _slot_dir(slot: Path, name: str) -> Path:
    return slot / name.replace(".", "_")


def _forge_chan(channel: str, chat_id: str) -> str:
    import re as _re
    # NB: a backslash in an f-string EXPRESSION is a SyntaxError on Python < 3.12,
    # and pyproject declares requires-python >=3.10 — keep the sub() out of the
    # f-string so this module imports on 3.10/3.11 (path-audit 2026-07-07).
    safe_chat = _re.sub(r'[/\\\\]', '_', chat_id)
    return f"{channel}:{safe_chat}"


def _voice_state_dir(home: Path, channel: str, chat_id: str) -> Path:
    safe_channel = "".join(c if c.isalnum() else "_" for c in channel)[:64] or "anon"
    safe_chat = "".join(c if c.isalnum() else "_" for c in chat_id)[:64] or "anon"
    return home / "voice" / "sessions" / safe_channel / safe_chat


def _seed_voice_state(home: Path, channel: str, chat_id: str) -> Path:
    d = _voice_state_dir(home, channel, chat_id)
    d.mkdir(parents=True, exist_ok=True)
    (d / ".claude.json").write_text('{"sessionId":"abc"}')
    (d / ".session_started").touch()
    sub = d / ".claude"
    sub.mkdir(exist_ok=True)
    (sub / "history.jsonl").write_text('{"role":"user"}\n')
    return d


def _audit_path(home: Path) -> Path:
    return home / "global" / "forge" / "audit.jsonl"


def _verify_chain(home: Path) -> tuple[bool, list]:
    """Walk the unified audit chain via forge.security_events.verify_chain."""
    code = (
        "import sys, json\n"
        "from forge.security_events import verify_chain\n"
        "from pathlib import Path\n"
        "ok, problems = verify_chain(Path(sys.argv[1]))\n"
        "print(json.dumps({'ok': ok, 'problems': problems}))\n"
    )
    out = _run_helper(home, home / "slot-x", code, str(_audit_path(home)))
    j = json.loads(out.strip().splitlines()[-1])
    return j["ok"], j["problems"]


# ── case 1: skill canonical + slot mirror cleared ──────────────────────────


def case_01_skill_clear() -> None:
    print("\n=== case 1: reset clears canonical skill (no slot at session scope) ===")
    sandbox = _make_sandbox("01")
    home = sandbox / "home"
    slot = sandbox / "slot"
    chat = "chatA"
    cid = _forge_chan("discord", chat)
    body = "# demo.alpha\n\nReply with literal 'CASE1-MARK'.\n"
    _create_skill(home, slot, channel_id=cid,
                  name="demo.alpha", body=body,
                  description="case 1 skill", grade=0.8)
    canonical = (home / "sessions" / cid / "skill-forge"
                 / "skills" / "demo.alpha" / "SKILL.md")
    slot_md = _slot_dir(slot, "demo.alpha") / "SKILL.md"
    eq(canonical.exists(), True, "canonical SKILL.md created")
    # Layer-16 v2 scope-gate: session-scope skills do NOT land in the
    # plugin slot. The session_reset cleanup must therefore verify only
    # the canonical removal — the slot was never created at this scope.
    eq(slot_md.exists(), False, "session-scope skill has no slot mirror (scope-gate)")

    out = _call_reset(home, slot, channel="discord", chat_id=chat)
    eq(out["skills_removed"], 1, "reset reports 1 skill removed")
    eq(canonical.exists(), False, "canonical SKILL.md gone")
    eq(slot_md.exists(), False, "slot mirror SKILL.md gone")
    shutil.rmtree(sandbox, ignore_errors=True)


# ── case 2: forge tool cleared ─────────────────────────────────────────────


def case_02_forge_tool_clear() -> None:
    print("\n=== case 2: reset clears forge tool + impl ===")
    sandbox = _make_sandbox("02")
    home = sandbox / "home"
    slot = sandbox / "slot"
    chat = "chatB"
    cid = _forge_chan("discord", chat)
    _create_forge_tool(home, slot, channel_id=cid, name="case2.tool")
    forge_root = home / "sessions" / cid / "forge"
    impl = forge_root / "tools" / "case2.tool.py"
    eq(impl.exists(), True, "forge tool impl created")

    out = _call_reset(home, slot, channel="discord", chat_id=chat)
    eq(out["forge_tools_removed"], 1, "reset reports 1 forge tool removed")
    # Either the impl was unlinked OR the whole session dir was rmtree'd.
    eq(impl.exists(), False, "forge tool impl gone")
    shutil.rmtree(sandbox, ignore_errors=True)


# ── case 3: voice state cleared ────────────────────────────────────────────


def case_03_voice_state_clear() -> None:
    print("\n=== case 3: reset clears voice conversation state dir ===")
    sandbox = _make_sandbox("03")
    home = sandbox / "home"
    slot = sandbox / "slot"
    chat = "chatC"
    vs = _seed_voice_state(home, "telegram", chat)
    eq((vs / ".claude.json").exists(), True, "voice .claude.json seeded")

    out = _call_reset(home, slot, channel="telegram", chat_id=chat)
    eq(out["voice_state_removed"], True, "reset reports voice state removed")
    eq(vs.exists(), False, "voice session dir gone")
    shutil.rmtree(sandbox, ignore_errors=True)


# ── case 4: idempotent ─────────────────────────────────────────────────────


def case_04_idempotent() -> None:
    print("\n=== case 4: second call returns zeros ===")
    sandbox = _make_sandbox("04")
    home = sandbox / "home"
    slot = sandbox / "slot"
    chat = "chatD"
    cid = _forge_chan("discord", chat)
    _create_skill(home, slot, channel_id=cid,
                  name="demo.beta",
                  body="# demo.beta\n\nMARKER-D.\n",
                  description="case 4", grade=0.7)
    _create_forge_tool(home, slot, channel_id=cid, name="case4.tool")
    _seed_voice_state(home, "discord", chat)

    first = _call_reset(home, slot, channel="discord", chat_id=chat)
    second = _call_reset(home, slot, channel="discord", chat_id=chat)
    eq(first["skills_removed"], 1, "first call: 1 skill")
    eq(first["forge_tools_removed"], 1, "first call: 1 forge tool")
    eq(first["voice_state_removed"], True, "first call: voice removed")
    eq(second["skills_removed"], 0, "second call: 0 skills")
    eq(second["forge_tools_removed"], 0, "second call: 0 forge tools")
    eq(second["voice_state_removed"], False, "second call: voice already gone")
    shutil.rmtree(sandbox, ignore_errors=True)


# ── case 5: never-existed session — audit still written ───────────────────


def case_05_never_existed() -> None:
    print("\n=== case 5: reset on never-existed session — counts 0, audit written ===")
    sandbox = _make_sandbox("05")
    home = sandbox / "home"
    slot = sandbox / "slot"

    out = _call_reset(home, slot, channel="discord", chat_id="ghost")
    eq(out["skills_removed"], 0, "no skills")
    eq(out["forge_tools_removed"], 0, "no forge tools")
    eq(out["voice_state_removed"], False, "no voice state")
    eq(bool(out["audit_event_id"]), True, "audit event id present")
    eq(out["audit_event_type"], "session.reset", "event_type=session.reset")
    eq(_audit_path(home).exists(), True, "audit.jsonl created")
    shutil.rmtree(sandbox, ignore_errors=True)


# ── case 6: hash chain valid after multiple resets ────────────────────────


def case_06_chain_valid() -> None:
    print("\n=== case 6: chain valid after multiple resets ===")
    sandbox = _make_sandbox("06")
    home = sandbox / "home"
    slot = sandbox / "slot"
    for chat in ("c1", "c2", "c3"):
        cid = _forge_chan("discord", chat)
        _create_skill(home, slot, channel_id=cid,
                      name=f"demo.{chat}",
                      body=f"# demo.{chat}\n\nMARKER-{chat}.\n",
                      description=f"case 6 {chat}", grade=0.6)
        _call_reset(home, slot, channel="discord", chat_id=chat)
    chain_ok, problems = _verify_chain(home)
    eq(chain_ok, True, f"chain valid (problems={problems[:2]})")
    shutil.rmtree(sandbox, ignore_errors=True)


# ── case 7: timeout sweep ──────────────────────────────────────────────────


def case_07_timeout_sweep() -> None:
    print("\n=== case 7: timeout sweep purges only stale session ===")
    sandbox = _make_sandbox("07")
    home = sandbox / "home"
    slot = sandbox / "slot"

    fresh_chat = "freshchat"
    stale_chat = "stalechat"
    fresh_cid = _forge_chan("discord", fresh_chat)
    stale_cid = _forge_chan("discord", stale_chat)
    _create_skill(home, slot, channel_id=fresh_cid,
                  name="demo.fresh",
                  body="# demo.fresh\n\nFRESH-MARK.\n",
                  description="fresh", grade=0.7)
    _create_skill(home, slot, channel_id=stale_cid,
                  name="demo.stale",
                  body="# demo.stale\n\nSTALE-MARK.\n",
                  description="stale", grade=0.7)

    fresh_dir = home / "sessions" / fresh_cid
    stale_dir = home / "sessions" / stale_cid
    eq(fresh_dir.exists(), True, "fresh session dir exists pre-sweep")
    eq(stale_dir.exists(), True, "stale session dir exists pre-sweep")

    # Backdate the stale session by 8 days (recursively).
    stale_age = time.time() - 8 * 86400
    for p in [stale_dir, *stale_dir.rglob("*")]:
        try:
            os.utime(p, (stale_age, stale_age))
        except OSError:
            pass

    env = _child_env(home, slot)
    r = subprocess.run(
        [sys.executable, str(TIMEOUT_SWEEP_PY), "--ttl-days", "7"],
        env=env, capture_output=True, text=True, check=True,
    )
    out = r.stdout.strip()
    eq(stale_dir.exists(), False, "stale session purged")
    eq(fresh_dir.exists(), True, "fresh session retained")
    if "session.timeout" not in out:
        bad(f"sweep stdout missing session.timeout marker: {out[-300:]}")
    else:
        ok("sweep emitted session.timeout marker")
    shutil.rmtree(sandbox, ignore_errors=True)


# ── case 8 (opt-in): engine E2E ────────────────────────────────────────────


def case_08_engine_e2e() -> None:
    print("\n=== case 8: engine E2E (SKILL_FORGE_ENGINE_E2E) ===")
    if os.environ.get("SKILL_FORGE_ENGINE_E2E") != "1":
        print("SKIPPED: SKILL_FORGE_ENGINE_E2E not set")
        return

    if shutil.which("claude") is None:
        print("SKIPPED: claude binary not on PATH")
        return

    sandbox = _make_sandbox("08")
    home = sandbox / "home"
    slot = sandbox / "slot"
    chat = "reset-test-XYZ"
    cid = _forge_chan("discord", chat)

    body = (
        "# trading.reset_proof\n\n"
        "You know about RESET-PROOF-XYZ. When asked about RESET-PROOF-XYZ, "
        "reply with literal 'CLEARED-OK' and nothing else.\n"
    )
    _create_skill(home, slot, channel_id=cid,
                  name="trading.reset_proof", body=body,
                  description="reset-proof skill", grade=0.9)

    # Build append-system via skill_inject.collect_active_skills(...)
    code = (
        "import sys, json\n"
        "sys.path.insert(0, sys.argv[1])\n"
        "from skill_inject import collect_active_skills\n"
        "block = collect_active_skills(channel_id=sys.argv[2], profile={})\n"
        "print(json.dumps({'block': block}))\n"
    )
    out = _run_helper(home, slot, code, str(ROOT), cid)
    pre_block = json.loads(out.strip().splitlines()[-1])["block"]
    if not pre_block or "CLEARED-OK" not in pre_block:
        bad(f"pre-reset skill block missing CLEARED-OK: {pre_block!r}")
        return

    # Spawn claude -p with the constructed --append-system-prompt.
    pre_run = subprocess.run(
        ["claude", "-p", "What is RESET-PROOF-XYZ?",
         "--append-system-prompt", pre_block,
         "--permission-mode", "default"],
        capture_output=True, text=True, timeout=300,
    )
    pre_stdout = (pre_run.stdout or "")
    print(f"PRE-RESET stdout: {pre_stdout[:200]!r}")
    if "CLEARED-OK" not in pre_stdout:
        bad("pre-reset claude run did not include CLEARED-OK")
        return
    ok("pre-reset claude run honored skill (CLEARED-OK present)")

    _call_reset(home, slot, channel="discord", chat_id=chat)

    # collect_active_skills should now return None.
    out2 = _run_helper(home, slot, code, str(ROOT), cid)
    post_block = json.loads(out2.strip().splitlines()[-1])["block"]
    eq(post_block, None, "post-reset collect_active_skills() returns None")

    # Re-run claude with empty append-system → CLEARED-OK absent.
    post_run = subprocess.run(
        ["claude", "-p", "What is RESET-PROOF-XYZ?",
         "--append-system-prompt", "",
         "--permission-mode", "default"],
        capture_output=True, text=True, timeout=300,
    )
    post_stdout = (post_run.stdout or "")
    print(f"POST-RESET stdout: {post_stdout[:200]!r}")
    if "CLEARED-OK" in post_stdout:
        bad("post-reset claude STILL emitted CLEARED-OK")
    else:
        ok("post-reset claude no longer emits CLEARED-OK")
    shutil.rmtree(sandbox, ignore_errors=True)


# ── main ───────────────────────────────────────────────────────────────────


def main() -> int:
    case_01_skill_clear()
    case_02_forge_tool_clear()
    case_03_voice_state_clear()
    case_04_idempotent()
    case_05_never_existed()
    case_06_chain_valid()
    case_07_timeout_sweep()
    case_08_engine_e2e()
    print(f"\n{PASS} pass, {FAIL} fail")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
