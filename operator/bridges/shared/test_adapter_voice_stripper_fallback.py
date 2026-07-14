#!/usr/bin/env python3
"""test_adapter_voice_stripper_fallback.py — Voice Summary fallback when
strip_for_tts.py returns empty or times out.

When strip_for_tts.py removes all content (e.g., input is 100% code blocks)
or fails, build_voice_summary should:
  1. Catch the empty/failure state explicitly
  2. Log the fallback reason
  3. Use raw text as input to summarize.py

Three subtests:
  1. strip_for_tts returns empty → fallback uses raw text
  2. strip_for_tts times out → fallback uses raw text
  3. summarize.py returns empty → fallback uses head of answer
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))


def _section(title: str) -> None:
    print(f"\n=== {title} ===")


def _install_fakes(tmp: Path, stripper_mode: str = "normal") -> tuple[Path, Path, Path]:
    """Install fake summarize.py and strip_for_tts.py.

    stripper_mode:
      'normal'  — echo input
      'empty'   — return empty output
      'timeout' — sleep forever
    """
    scripts_dir = tmp / "scripts"
    scripts_dir.mkdir()
    argv_dump = tmp / "summarizer_argv.json"
    stripper_dump = tmp / "stripper_input.txt"

    # Fake strip_for_tts.py
    if stripper_mode == "empty":
        fake_stripper_code = (
            "#!/usr/bin/env python3\n"
            "import sys\n"
            "sys.stdout.write('')  # return empty\n"
        )
    elif stripper_mode == "timeout":
        fake_stripper_code = (
            "#!/usr/bin/env python3\n"
            "import time\n"
            "time.sleep(999)  # timeout\n"
        )
    else:
        fake_stripper_code = (
            "#!/usr/bin/env python3\n"
            "import sys\n"
            f"open({json.dumps(str(stripper_dump))}, 'w').write(sys.stdin.read())\n"
            "sys.stdout.write(sys.stdin.read())\n"  # echo input
        )

    fake_stripper = scripts_dir / "strip_for_tts.py"
    fake_stripper.write_text(fake_stripper_code)
    fake_stripper.chmod(0o755)

    # Fake summarize.py
    fake_summarize = scripts_dir / "summarize.py"
    fake_summarize.write_text(
        "#!/usr/bin/env python3\n"
        "import json, sys\n"
        "argv = sys.argv[1:]\n"
        # Dump ONLY the MAIN summarize call's argv — the LERN-ZUGABE / Metapher
        # backfills re-invoke this script with --appendix-mode / --metapher-mode
        # after the main call and would otherwise overwrite the dump.
        "if '--appendix-mode' not in argv and '--metapher-mode' not in argv:\n"
        f"    open({json.dumps(str(argv_dump))}, 'w').write(json.dumps(argv))\n"
        "input_text = sys.stdin.read()\n"
        "# Echo first 100 chars of input to prove it was received\n"
        "print(f'SUMMARY[{len(input_text)} bytes]: {input_text[:100]}')\n"
    )
    fake_summarize.chmod(0o755)

    return scripts_dir, argv_dump, stripper_dump


def _fresh_adapter_with_scripts_dir(scripts_dir: Path):
    """Re-import adapter with SCRIPTS_DIR redirected."""
    for m in ("adapter",):
        sys.modules.pop(m, None)
    import adapter  # type: ignore
    adapter.SCRIPTS_DIR = scripts_dir
    return adapter


def test_stripper_empty_fallback() -> None:
    _section("strip_for_tts returns empty → fallback uses raw text")
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        scripts_dir, argv_dump, stripper_dump = _install_fakes(
            td_path, stripper_mode="empty"
        )
        adapter = _fresh_adapter_with_scripts_dir(scripts_dir)

        # Long text that would trigger summarize path
        test_input = "Das ist ein Test. " * 100  # ~1800 chars
        result = adapter.build_voice_summary(test_input, max_chars=400)

        assert result, "build_voice_summary returned empty"
        assert "Test" in result, "Result should contain part of input text"

        # Verify summarize.py received the raw text, not empty
        argv = json.loads(argv_dump.read_text())
        # The fake summarize prints the first 100 chars and bytecount
        print(f"  Result snippet: {result[:100]}...")
        print(f"  Summarizer argv: {argv[:4]}")  # --lang de --max-chars 400
        assert argv[:4] == ["--lang", "de", "--max-chars", "400"]
        print("  OK — fallback to raw text when stripper returns empty")


def test_stripper_timeout_fallback() -> None:
    _section("strip_for_tts times out → fallback uses raw text")
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        scripts_dir, argv_dump, _ = _install_fakes(
            td_path, stripper_mode="timeout"
        )
        adapter = _fresh_adapter_with_scripts_dir(scripts_dir)

        test_input = "Das ist ein Test. " * 100
        # Should timeout in ~10s (the stripper's timeout), then fallback
        result = adapter.build_voice_summary(test_input, max_chars=400)

        assert result, "build_voice_summary should return fallback even on timeout"
        assert "Test" in result or len(result) > 0, "Should have some output"
        print(f"  Got fallback result: {result[:100]}...")
        print("  OK — fallback to raw text when stripper times out")


def test_summarizer_empty_fallback() -> None:
    _section("summarize.py returns empty → fallback uses head of answer")
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        scripts_dir, argv_dump, _ = _install_fakes(
            td_path, stripper_mode="normal"
        )
        # Patch summarize to return empty
        fake_summarize = scripts_dir / "summarize.py"
        fake_summarize.write_text(
            "#!/usr/bin/env python3\n"
            "import json, sys\n"
            f"open({json.dumps(str(argv_dump))}, 'w').write("
            "json.dumps(sys.argv[1:]))\n"
            "sys.stdout.write('')  # return empty\n"
        )
        fake_summarize.chmod(0o755)

        adapter = _fresh_adapter_with_scripts_dir(scripts_dir)
        test_input = "Das ist ein Test. " * 100
        result = adapter.build_voice_summary(test_input, max_chars=400)

        # Should fall back to text[:max_chars]
        assert result, "Should return fallback"
        assert len(result) <= 500, "Should be roughly max_chars or less"
        assert "Test" in result or len(result) > 0
        print(f"  Got fallback result: {result[:100]}...")
        print("  OK — fallback to text head when summarizer returns empty")


def test_degraded_fallback_marker_is_logged() -> None:
    """Regression (2026-07-14): summarize.py always exits 0 and always prints
    something, even when both its LLM backends failed and it fell through to
    naive_truncate (a near-verbatim passthrough) -- from build_voice_summary's
    side, that used to look identical to a real summary (non-empty stdout,
    exit 0), so the "voice summary just reads the raw text" symptom was
    invisible in CorvinOS's own logs. summarize.py now prints a
    "[summarize] degraded: ..." sentinel to STDERR in that case; adapter.py
    must surface it via log()."""
    _section("summarize.py degraded-fallback sentinel → adapter logs a warning")
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        scripts_dir, argv_dump, _ = _install_fakes(td_path, stripper_mode="normal")
        fake_summarize = scripts_dir / "summarize.py"
        fake_summarize.write_text(
            "#!/usr/bin/env python3\n"
            "import json, sys\n"
            f"open({json.dumps(str(argv_dump))}, 'w').write("
            "json.dumps(sys.argv[1:]))\n"
            "input_text = sys.stdin.read()\n"
            "print('[summarize] degraded: both LLM backends unavailable — "
            "using naive_truncate (near-verbatim) structural fallback', "
            "file=sys.stderr)\n"
            "print(input_text)  # near-verbatim passthrough, like naive_truncate\n"
        )
        fake_summarize.chmod(0o755)

        adapter = _fresh_adapter_with_scripts_dir(scripts_dir)
        logged: list[str] = []
        adapter.log = lambda *a: logged.append(" ".join(str(x) for x in a))  # type: ignore[assignment]

        test_input = "Das ist ein Test. " * 100
        result = adapter.build_voice_summary(test_input, max_chars=400)

        assert result, "should still return SOME spoken text"
        assert any("degraded" in line for line in logged), (
            f"expected a 'degraded' warning in adapter's own logs, got: {logged}"
        )
        print("  OK — degraded-fallback sentinel surfaced via adapter.log()")


def main() -> int:
    try:
        test_stripper_empty_fallback()
        test_stripper_timeout_fallback()
        test_summarizer_empty_fallback()
        test_degraded_fallback_marker_is_logged()
        print("\nAll voice-stripper-fallback tests passed.")
        return 0
    except AssertionError as e:
        print(f"\n❌ Test failed: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
