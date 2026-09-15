#!/usr/bin/env python3
"""test_adapter_progress.py — TodoWrite progress-update dedup.

User-reported pain: every TodoWrite status flip (pending → in_progress
→ completed) re-posted the entire plan into the messenger chat. The
fix dedupes "once-only" tools (TodoWrite, ExitPlanMode) so the plan
ships exactly once per turn.

This suite locks both directions:
  - default (progress_plan_repeat=false): only one TodoWrite status
    file lands in the outbox even when call_claude_streaming fires
    on_status three times.
  - opt-in (progress_plan_repeat=true): the legacy behaviour comes
    back — every TodoWrite produces its own status file.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))


def _section(title: str) -> None:
    print(f"\n=== {title} ===")


def _fresh_adapter(env_overrides: dict | None = None):
    if env_overrides:
        for k, v in env_overrides.items():
            os.environ[k] = v
    for mod in list(sys.modules):
        if mod == "adapter":
            del sys.modules[mod]
    import adapter  # type: ignore
    return adapter


def _setup_sandbox() -> tuple[Path, Path, Path]:
    base = Path(tempfile.mkdtemp(prefix="adapter-progress-"))
    inbox = base / "inbox"
    outbox = base / "outbox"
    processed = base / "processed"
    for p in (inbox, outbox, processed):
        p.mkdir()
    return inbox, outbox, processed


def _make_streaming_stub(adapter, plan_events: int, final_text: str = "fertig."):
    """Build a fake call_claude_streaming that fires on_status `plan_events`
    times with tool_name='TodoWrite' (different status_text each call so
    the consecutive-text dedup wouldn't catch them)."""
    def fake_streaming(prompt, channel, chat_key, mode="unrestricted",
                       add_dir=None, on_status=None, status_mode="compact",
                       profile=None, _retry_count=0, **media_kwargs):
        if on_status is not None:
            for i in range(plan_events):
                on_status(
                    f"📋 *Plan:*\nitem-{i} updated",
                    tool_name="TodoWrite",
                )
        return final_text
    return fake_streaming


def _run_one_text_message(adapter, sandbox_dirs, settings_overrides=None):
    inbox, outbox, processed = sandbox_dirs
    # Use a sandbox channel name so Layer 16 inbox-revalidation hits
    # "no-settings" → fail-open, regardless of any operator-edited
    # bridges/<channel>/settings.json on disk.
    env = {
        "id": "msg-progress-1",
        "channel": "sandbox-progress",
        "from": "u_p",
        "chat_id": "chat_p",
        "text": "do something",
        "ts": time.time(),
    }
    in_file = inbox / "msg-progress-1.json"
    in_file.write_text(json.dumps(env))
    base_settings = {"whitelist": ["u_p"], "progress_updates": True}
    if settings_overrides:
        base_settings.update(settings_overrides)
    adapter.process_one(in_file, settings=base_settings)


def _count_status_files(outbox: Path) -> int:
    """msg-progress-1_s00.json, _s01.json, … — only the streamed status
    chunks. The final answer envelope uses a different suffix (_00.json)."""
    return len(list(outbox.glob("msg-progress-1_s*.json")))


def test_default_dedupes_todowrite_to_one_status() -> None:
    _section("default: TodoWrite collapsed to one status emit")
    inbox, outbox, processed = _setup_sandbox()
    try:
        adapter = _fresh_adapter({
            "ADAPTER_INBOX":     str(inbox),
            "ADAPTER_OUTBOX":    str(outbox),
            "ADAPTER_PROCESSED": str(processed),
            "ADAPTER_FAKE_CLAUDE": "0",  # we monkey-patch instead
            "ADAPTER_ROUTING_MODE": "off",
        })
        # Monkey-patch the streaming function to fire 3 TodoWrite events.
        adapter.call_claude_streaming = _make_streaming_stub(adapter, plan_events=3)

        _run_one_text_message(adapter, (inbox, outbox, processed))

        n = _count_status_files(outbox)
        assert n == 1, (
            f"expected exactly 1 TodoWrite status file with default dedup, got {n}"
        )
        # Verify the one we got really is the plan, not something stripped.
        status_file = list(outbox.glob("msg-progress-1_s*.json"))[0]
        body = json.loads(status_file.read_text())
        assert body.get("_progress") is True
        assert "Plan" in body.get("text", "")
        print(f"PASS: 3 TodoWrite events → 1 status file ({status_file.name})")
    finally:
        shutil.rmtree(inbox.parent, ignore_errors=True)


def test_opt_in_progress_plan_repeat_restores_old_behaviour() -> None:
    _section("opt-in: progress_plan_repeat=true → every TodoWrite fires")
    inbox, outbox, processed = _setup_sandbox()
    try:
        adapter = _fresh_adapter({
            "ADAPTER_INBOX":     str(inbox),
            "ADAPTER_OUTBOX":    str(outbox),
            "ADAPTER_PROCESSED": str(processed),
            "ADAPTER_ROUTING_MODE": "off",
        })
        adapter.call_claude_streaming = _make_streaming_stub(adapter, plan_events=3)

        _run_one_text_message(
            adapter, (inbox, outbox, processed),
            settings_overrides={"progress_plan_repeat": True},
        )

        n = _count_status_files(outbox)
        assert n == 3, (
            f"expected 3 TodoWrite status files when plan_repeat is on, got {n}"
        )
        print(f"PASS: 3 TodoWrite events → 3 status files (legacy behaviour intact)")
    finally:
        shutil.rmtree(inbox.parent, ignore_errors=True)


def test_consecutive_identical_text_still_deduped() -> None:
    _section("default: consecutive identical text dedup still active")
    inbox, outbox, processed = _setup_sandbox()
    try:
        adapter = _fresh_adapter({
            "ADAPTER_INBOX":     str(inbox),
            "ADAPTER_OUTBOX":    str(outbox),
            "ADAPTER_PROCESSED": str(processed),
            "ADAPTER_ROUTING_MODE": "off",
        })

        # Two Bash events with the SAME formatted text — must collapse to 1.
        def fake_streaming(prompt, channel, chat_key, mode="unrestricted",
                           add_dir=None, on_status=None, status_mode="compact",
                           profile=None, _retry_count=0, **media_kwargs):
            if on_status is not None:
                on_status("💻 Bash `ls`", tool_name="Bash")
                on_status("💻 Bash `ls`", tool_name="Bash")
            return "fertig."
        adapter.call_claude_streaming = fake_streaming

        _run_one_text_message(adapter, (inbox, outbox, processed))

        n = _count_status_files(outbox)
        assert n == 1, f"expected 1 (text-dedup), got {n}"
        print("PASS: identical-text dedup still works for non-plan tools")
    finally:
        shutil.rmtree(inbox.parent, ignore_errors=True)


def test_progress_off_still_routes_through_gated_dispatcher() -> None:
    """C1 regression (path-audit 2026-07-06): progress_updates=false must NOT
    bypass the pre-spawn gates. The non-progress branch used to call the ungated
    legacy call_claude(); it must now go through the engine-agnostic
    call_claude_streaming() dispatcher (which runs L34/L35/CLAG/capability/L44 +
    charges the turn), with progress suppressed (on_status=None)."""
    _section("progress_updates=false → gated call_claude_streaming, not call_claude")
    inbox, outbox, processed = _setup_sandbox()
    try:
        adapter = _fresh_adapter({
            "ADAPTER_INBOX":     str(inbox),
            "ADAPTER_OUTBOX":    str(outbox),
            "ADAPTER_PROCESSED": str(processed),
            "ADAPTER_FAKE_CLAUDE": "0",
            "ADAPTER_ROUTING_MODE": "off",
        })
        seen = {"streaming": 0, "legacy": 0, "on_status_none": None}

        def fake_streaming(prompt, channel, chat_key, mode="unrestricted",
                           add_dir=None, on_status=None, status_mode="compact",
                           profile=None, _retry_count=0, **media_kwargs):
            seen["streaming"] += 1
            seen["on_status_none"] = on_status is None
            return "fertig."

        def fake_legacy(prompt, channel="whatsapp", chat_key="anon", **kwargs):
            seen["legacy"] += 1
            return "LEGACY-UNGATED"

        adapter.call_claude_streaming = fake_streaming
        adapter.call_claude = fake_legacy
        # C1 is a routing decision at the dispatch site; stub the downstream
        # voice-summary builder (it shells out to a real summariser subprocess,
        # unrelated to this assertion) so the test stays fast and hermetic.
        adapter.build_voice_summary = lambda text, *a, **k: text

        _run_one_text_message(
            adapter, (inbox, outbox, processed),
            settings_overrides={"progress_updates": False},
        )

        assert seen["streaming"] == 1, (
            "progress_updates=false did NOT route through the gated "
            f"call_claude_streaming dispatcher (calls={seen['streaming']})"
        )
        assert seen["legacy"] == 0, (
            "progress_updates=false still called the ungated legacy call_claude "
            "— C1 gate-bypass regressed"
        )
        assert seen["on_status_none"] is True, (
            "non-progress path must pass on_status=None (progress suppressed)"
        )
        print("PASS: progress off routes through gated dispatcher, legacy path unused")
    finally:
        shutil.rmtree(inbox.parent, ignore_errors=True)


def main() -> int:
    tests = [
        test_default_dedupes_todowrite_to_one_status,
        test_opt_in_progress_plan_repeat_restores_old_behaviour,
        test_consecutive_identical_text_still_deduped,
        test_progress_off_still_routes_through_gated_dispatcher,
    ]
    failed = 0
    for t in tests:
        try:
            t()
        except AssertionError as exc:
            failed += 1
            print(f"FAIL  {t.__name__}: {exc}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"ERROR {t.__name__}: {type(exc).__name__}: {exc}")
            import traceback
            traceback.print_exc()
    print()
    if failed:
        print(f"{failed} test(s) failed")
        return 1
    print(f"All {len(tests)} tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
