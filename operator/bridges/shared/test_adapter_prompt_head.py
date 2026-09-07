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

from agents.claude_code import (  # noqa: E402
    AT_NEUTRALISER,
    PROMPT_HEAD_SENTINEL,
    guard_prompt_head,
    neutralise_at_references,
)

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
        assert g == HEAD + text  # no `@` in these → text is byte-identical
        assert guard_prompt_head(g) == g, "idempotent"
    assert guard_prompt_head(None) == HEAD
    # R2-E1 also covers `!` (client-side shell) and `#` (memory-add) at byte 0:
    # the sentinel's own first byte is a letter, so nothing user-supplied can
    # ever sit at position 0.
    for text in ("!cat /etc/passwd", "#remember this", "/pwn"):
        assert guard_prompt_head(text)[0].isalpha(), text


def test_neutralise_at_references_contract() -> None:
    """R3-C2: `@<path>` is expanded client-side ANYWHERE in the message, so the
    byte-0 sentinel does not touch it. The neutraliser inserts one zero-width
    joiner after each `@` that is not inside an e-mail local part.

    Measured trigger (real CLI, 2026-09-07): only a token-start `@` expands —
    `x@canary.txt` and `(`/`<`/`"`/`,`/`:`/`=`/`[`/`/`/`-` before it do not. The
    neutraliser is deliberately WIDER than that (anything but an e-mail
    local-part char), so a CLI that widens its own rule stays covered.

    The joiner goes BEFORE the `@`, which keeps the user's `@token` contiguous.
    U+200B in that position was measured NOT to stop the expansion; U+2060
    does. See the module comment in `agents/claude_code.py` for the full matrix.
    """
    assert AT_NEUTRALISER == "\u2060" and len(AT_NEUTRALISER) == 1

    # the exfiltration vector, in every shape that reached production
    for vector in ("@/etc/hostname", "read @~/.corvin/audit.jsonl please",
                   "hi\n@.env\nbye", "@secret.txt", "\t@Makefile"):
        out = neutralise_at_references(vector)
        assert AT_NEUTRALISER + "@" in out, vector
        # nothing deleted: dropping the joiners restores the input byte-for-byte
        assert out.replace(AT_NEUTRALISER, "") == vector, vector
        # no `@` is left at a token start (the joiner precedes each one)
        for i, ch in enumerate(out):
            if ch == "@":
                assert out[i - 1: i] == AT_NEUTRALISER, (vector, i)

    # e-mail addresses survive untouched — the stated trade-off boundary
    for keep in ("silvio.jurk@googlemail.com", "a@b.co", "x_1+tag%q-z@host.example"):
        assert neutralise_at_references(keep) == keep, keep
    assert neutralise_at_references("write to ops@example.com now") == \
        "write to ops@example.com now"

    # idempotent, and a no-@ payload is returned unchanged (identity fast path)
    for text in ("", "no at sign", "@a", "user@host", "a @b c@d"):
        once = neutralise_at_references(text)
        assert neutralise_at_references(once) == once, text
    assert neutralise_at_references("plain") == "plain"

    # and the full guard composes both halves, idempotently
    g = guard_prompt_head("/pwn then @/etc/hostname")
    assert g == HEAD + "/pwn then " + AT_NEUTRALISER + "@/etc/hostname"
    assert guard_prompt_head(g) == g


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
             test_neutralise_at_references_contract,
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
