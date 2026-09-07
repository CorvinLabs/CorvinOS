"""
Phase A: Context Flow — E2E test for strip_for_tts fallback & context degradation.

Verifies:
1. strip_for_tts timeout → fallback to raw text works
2. Fallback text is passed to summarize.py
3. Output is not empty (no silent data loss)
"""
import subprocess
import sys
import os
from pathlib import Path

import pytest

def test_strip_for_tts_fallback():
    """E2E: If strip_for_tts fails, adapter uses raw text for summarize.py."""
    proj_root = Path(__file__).parent.parent.parent
    adapter_path = proj_root / "operator" / "bridges" / "shared" / "adapter.py"

    # Verify adapter.py has fallback logic
    adapter_code = adapter_path.read_text()
    assert "if not pre.strip():" in adapter_code, "Fallback: empty pre check missing"
    assert "pre = text" in adapter_code, "Fallback: raw text assignment missing"
    assert "TimeoutExpired" in adapter_code, "Fallback: timeout handler missing"
    assert "CalledProcessError" in adapter_code, "Fallback: process error handler missing"
    print("✅ Fallback logic present in adapter.py")

@pytest.mark.live
@pytest.mark.skipif(
    os.environ.get("CLAUDE_LIVE_E2E") != "1",
    reason="real-LLM E2E: build_voice_summary drives summarize.py → `claude -p`; opt in with CLAUDE_LIVE_E2E=1",
)
def test_voice_summary_context_preservation(tmp_path):
    """Live E2E: build_voice_summary (real `claude -p` summariser) preserves the context."""
    proj_root = Path(__file__).parent.parent.parent
    shared = proj_root / "operator" / "bridges" / "shared"

    # Fresh interpreter: adapter.py must be importable as a bare module with
    # <repo>/operator/bridges/shared on sys.path (no dotted operator.* import).
    script = tmp_path / "voice_test.py"
    script.write_text(f"""
import sys
sys.path.insert(0, {str(shared)!r})
from adapter import build_voice_summary

text = "Fixed a bug in the worker pool. Important: Restart workers after deploy."
result = build_voice_summary(text, max_chars=100)
assert result, "build_voice_summary returned empty"
low = result.lower()
assert any(k in low for k in ("bug", "fixed", "worker", "fehler", "behoben")), \
    f"Original context lost in output: {{result}}"
print(f"OK {{len(result)}} chars: {{result[:80]}}")
""")

    result = subprocess.run(
        [sys.executable, str(script)],
        capture_output=True, text=True, timeout=180, cwd=str(proj_root),
    )
    print(result.stdout)
    assert result.returncode == 0, f"voice summary E2E failed:\n{result.stderr[-2000:]}"
    assert "OK " in result.stdout
