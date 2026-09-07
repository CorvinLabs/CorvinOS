"""R2-E1 / R2-E2 (adversarial review round 2, 2026-09-07) — bridge adapter.

`claude -p` expands a user message whose FIRST byte is `/` into a slash
command / skill on every transport (positional-after-`--` and stdin alike);
`/cost` leaked the operator's subscription usage, `/pwn` ran a scratch
`.claude/commands/pwn.md`. The adapter used to forward the raw prompt whenever
the CEL prefix was empty. Every spawn site now wraps the outbound text with
the shared `agents.claude_code.guard_prompt_head` sentinel line.

R2-E2: the legacy `call_claude()` fallback built argv WITH the prompt
(`prompt_via_stdin` default False). It now feeds the prompt on stdin.

Driven through the REAL boundaries — the argv builder the spawn uses and a
recording `claude` stand-in on PATH that `call_claude()` actually execs —
never through a patched Popen.

Run: python3 operator/bridges/shared/test_adapter_prompt_head.py  (or pytest)
"""
from __future__ import annotations

import importlib
import json
import os
import stat
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from agents.claude_code import PROMPT_HEAD_SENTINEL, guard_prompt_head  # noqa: E402

SLASH = "/pwn"
HEAD = PROMPT_HEAD_SENTINEL + "\n"

_FAKE_CLAUDE = r"""#!/usr/bin/env bash
# Recording `claude` stand-in: dumps argv + stdin, answers like `claude -p`.
out="${FAKE_CLAUDE_OUT:?}"
python3 - "$@" <<'PY'
import json, os, sys
json.dump(sys.argv[1:], open(os.path.join(os.environ["FAKE_CLAUDE_OUT"], "argv.json"), "w"))
PY
cat > "$out/stdin.txt"
printf '%s\n' 'fake answer'
exit 0
"""


def _sandbox():
    base = Path(tempfile.mkdtemp(prefix="adapter-prompt-head-"))
    for d in ("inbox", "outbox", "processed", "bridges", "home"):
        (base / d).mkdir()
    (base / "settings.json").write_text("{}", encoding="utf-8")
    fake_dir = base / "bin"
    fake_dir.mkdir()
    binary = fake_dir / "claude"
    binary.write_text(_FAKE_CLAUDE, encoding="utf-8")
    binary.chmod(binary.stat().st_mode | stat.S_IXUSR)
    out = base / "fake_out"
    out.mkdir()
    env = {
        "CORVIN_HOME": str(base / "home"),
        "ADAPTER_SETTINGS": str(base / "settings.json"),
        "ADAPTER_INBOX": str(base / "inbox"),
        "ADAPTER_OUTBOX": str(base / "outbox"),
        "ADAPTER_PROCESSED": str(base / "processed"),
        "ADAPTER_ROUTING_MODE": "off",
        "VOICE_AUDIT_PATH": str(base / "audit.jsonl"),
        "ADAPTER_BRIDGES_DIR": str(base / "bridges"),
        "FAKE_CLAUDE_OUT": str(out),
        "CORVIN_CLAUDE_BIN": str(binary),
        "CLAUDE_BIN": str(binary),
        "PATH": str(fake_dir) + os.pathsep + os.environ.get("PATH", ""),
    }
    os.environ.pop("ADAPTER_FAKE_CLAUDE", None)
    for k, v in env.items():
        os.environ[k] = v
    sys.modules.pop("adapter", None)
    adapter = importlib.import_module("adapter")
    return base, out, adapter


def test_guard_prompt_head_contract() -> None:
    assert not PROMPT_HEAD_SENTINEL.startswith("/") and "\n" not in PROMPT_HEAD_SENTINEL
    for text in (SLASH, " /init", "/cost", "", "hello", "User input: /x"):
        g = guard_prompt_head(text)
        assert g.startswith(HEAD) and not g.startswith("/")
        assert g == HEAD + text
        assert guard_prompt_head(g) == g, "idempotent"
    assert guard_prompt_head(None) == HEAD


def test_build_claude_args_positional_prompt_is_sentinel_guarded() -> None:
    """R2-E1: the legacy argv builder (positional prompt behind `--`) never
    emits a user text whose byte 0 is `/`, CEL prefix or not."""
    base, out, adapter = _sandbox()
    args = adapter._build_claude_args(SLASH, "unrestricted", None, None,
                                      channel="discord", chat_key="ph-1")
    assert args[-2] == "--", args[-3:]
    assert args[-1].startswith(HEAD), args[-1][:40]
    assert args[-1].endswith(SLASH)
    assert SLASH not in args[:-1]
    # spawn_prompt_out mirrors exactly what the spawn feeds
    captured: list[str] = []
    args2 = adapter._build_claude_args(SLASH, "unrestricted", None, None,
                                       channel="discord", chat_key="ph-1",
                                       prompt_via_stdin=True, spawn_prompt_out=captured)
    assert "--" not in args2 and SLASH not in args2, args2
    assert captured and captured[0].startswith(HEAD) and captured[0].endswith(SLASH)
    for a in (args, args2):
        for p in (a[a.index("--append-system-prompt-file") + 1],) if "--append-system-prompt-file" in a else ():
            Path(p).unlink(missing_ok=True)


def test_call_claude_legacy_feeds_prompt_on_stdin_behind_sentinel() -> None:
    """R2-E2 + R2-E1 through the REAL exec boundary: `call_claude()` spawns the
    recording binary; argv must carry no prompt at all and stdin must start
    with the sentinel line and end with the verbatim user text."""
    base, out, adapter = _sandbox()
    answer = adapter.call_claude(SLASH, channel="discord", chat_key="ph-legacy")
    assert answer.strip() == "fake answer", answer
    argv = json.loads((out / "argv.json").read_text(encoding="utf-8"))
    stdin_text = (out / "stdin.txt").read_text(encoding="utf-8")
    assert SLASH not in argv, argv
    assert "--" not in argv, argv
    assert "--input-format" not in argv, argv  # plain-text stdin, whole input is the message
    assert stdin_text.startswith(HEAD), stdin_text[:60]
    assert stdin_text.endswith(SLASH), stdin_text[-60:]


def main() -> int:
    fails = 0
    tests = (test_guard_prompt_head_contract,
             test_build_claude_args_positional_prompt_is_sentinel_guarded,
             test_call_claude_legacy_feeds_prompt_on_stdin_behind_sentinel)
    for fn in tests:
        try:
            fn()
            print(f"PASS: {fn.__name__}")
        except AssertionError as e:
            fails += 1
            print(f"FAIL: {fn.__name__}: {e}")
    print(f"\n{len(tests) - fails} passed, {fails} failed")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
