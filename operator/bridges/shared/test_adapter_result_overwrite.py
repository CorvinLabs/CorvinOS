#!/usr/bin/env python3
"""test_adapter_result_overwrite.py — E2E-Test für das Result-Event-
Overwrite-Pattern.

Pro CLAUDE.md (`feedback_per_subtask_e2e`): kein Mock — wir starten einen
echten Python-Subprozess, der sich als `claude` ausgibt und das Pattern
"erst echtes Result, dann zweites Result mit leerem Payload" exakt so
in stream-json reproduziert, wie es bei der Discord-Voice-Session
movrmss0_323f01 (2026-05-07 19:34) auftrat:

  1. assistant/text "Echte Antwort A"  (1993-Zeichen-Pendant)
  2. result is_error=False result="Echte Antwort A"
  3. assistant/text "No response requested."  (Reaktion auf
     Monitor-DONE-Notification + UserPromptSubmit-Hook-Error)
  4. result is_error=False result=""

Erwartung: call_claude_streaming gibt "Echte Antwort A" zurück — der
Adapter-Filter darf den nicht-leeren ersten Result nicht durch den
leeren zweiten überschreiben.

Zwei Sub-Tests:
  1. result=non-empty → result=empty: das nicht-leere Result gewinnt.
  2. result=empty → result=non-empty: das nicht-leere Result gewinnt
     (zeigt: der Filter blockiert nicht das normale Auffüllen einer
     anfänglich leeren final_text, falls die CLI das je tut).

Mock-Disziplin: subprocess.Popen wird nicht gemockt — es feuert echte
Python-Subprozesse, die per #!/usr/bin/env python3 das stream-json an
stdout schreiben. Der einzige Trick: `subprocess.Popen` wird so wie es
ist verwendet, mit env-PATH der unsere fake-bin priorisiert.
"""
from __future__ import annotations

import importlib
import os
import shutil
import sys
import tempfile
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))


def _section(title: str) -> None:
    print(f"\n=== {title} ===")


def _make_fake_claude(tmp: Path, events: list[str]) -> Path:
    bin_dir = tmp / "fake-bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    script = bin_dir / "claude"
    payload = ",\n        ".join(repr(ev) for ev in events)
    body = textwrap.dedent(f'''\
        #!/usr/bin/env python3
        import sys
        for ev in [
            {payload}
        ]:
            sys.stdout.write(ev + "\\n")
            sys.stdout.flush()
    ''')
    script.write_text(body)
    script.chmod(0o755)
    return bin_dir


def _run_streaming(events: list[str], chat_key: str) -> str:
    tmp = Path(tempfile.mkdtemp(prefix="adapter-result-overwrite-"))
    saved_path = os.environ.get("PATH", "")
    try:
        bin_dir = _make_fake_claude(tmp, events)
        os.environ["PATH"] = f"{bin_dir}{os.pathsep}{saved_path}"
        os.environ["ADAPTER_STREAM_IDLE_TIMEOUT"] = "0"
        os.environ["ADAPTER_HEARTBEAT_INTERVAL"] = "0"
        os.environ["CORVIN_HOME"]  = str(tmp / "corvinOSHome")
        os.environ.pop("ADAPTER_FAKE_CLAUDE", None)

        # Force a clean import so adapter sees the env-driven settings.
        # Also clear agents.* — test_adapter_engine_path may have poisoned
        # agents.claude_code with a fake CLAUDE_BIN at module level.
        for _mod in list(sys.modules):
            if _mod == "adapter" or _mod == "agents" or _mod.startswith("agents."):
                sys.modules.pop(_mod, None)
        adapter = importlib.import_module("adapter")  # type: ignore
        # L44 (ADR-0143): stub the Tier-1 acceptable-use classifier to a benign
        # clear — this test fakes the `claude` binary and cannot answer the
        # gate's `claude -p` classifier call (would fail-closed before the
        # engine path). Gate coverage lives in test_house_rules.py.
        adapter._house_rules_classifier = lambda task, rules, auth, **_kw: ("", 1.0, "test-benign")

        out = adapter.call_claude_streaming(
            "demo prompt", channel="discord", chat_key=chat_key,
            mode="bypassPermissions", profile=None,
        )
        return out
    finally:
        os.environ["PATH"] = saved_path
        shutil.rmtree(tmp, ignore_errors=True)


def test_nonempty_then_empty_result_keeps_first() -> None:
    _section("non-empty result followed by empty result keeps non-empty")
    out = _run_streaming(
        events=[
            '{"type":"system","subtype":"init"}',
            '{"type":"assistant","message":{"role":"assistant","content":[{"type":"text","text":"Echte Antwort A"}]}}',
            '{"type":"result","is_error":false,"result":"Echte Antwort A"}',
            '{"type":"assistant","message":{"role":"assistant","content":[{"type":"text","text":"No response requested."}]}}',
            '{"type":"result","is_error":false,"result":""}',
        ],
        chat_key="result-overwrite-test-1",
    )
    assert out == "Echte Antwort A", (
        f"expected first non-empty result to win, got: {out!r}"
    )
    print("  OK — non-empty result preserved despite later empty result")


def test_empty_then_nonempty_result_takes_nonempty() -> None:
    _section("empty result followed by non-empty result takes non-empty")
    out = _run_streaming(
        events=[
            '{"type":"system","subtype":"init"}',
            '{"type":"result","is_error":false,"result":""}',
            '{"type":"assistant","message":{"role":"assistant","content":[{"type":"text","text":"Späte Antwort"}]}}',
            '{"type":"result","is_error":false,"result":"Späte Antwort"}',
        ],
        chat_key="result-overwrite-test-2",
    )
    assert out == "Späte Antwort", (
        f"expected non-empty result to fill in over empty, got: {out!r}"
    )
    print("  OK — empty result correctly replaced by later non-empty")


if __name__ == "__main__":
    test_nonempty_then_empty_result_keeps_first()
    test_empty_then_nonempty_result_takes_nonempty()
    print("\nALL OK")
