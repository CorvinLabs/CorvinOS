#!/usr/bin/env python3
"""test_adapter_engine_switch.py — Layer 22 OpenCode pre-dispatch.

Drives `adapter.call_claude_streaming` with a profile that pins
`default_engine: "opencode"` against a fake `opencode` binary on
PATH, and asserts:

  1. The opencode binary is invoked (not claude), final_text matches
     the fake echo result.
  2. A profile WITHOUT `default_engine="opencode"` continues to route
     through the Claude path (regression-gate that the new dispatch
     branch hasn't intercepted other personas).
  3. `inject_btw` against an engine with no `mid_stream_inject`
     capability falls through to the legacy stdin lookup (which is
     empty in this test setup), returning False — the user-facing
     ACK then becomes "kein Task läuft" instead of an
     AttributeError crash.

The fake opencode binary emits the documented JSON event shape
(step_start + text + EOF). No real cloud spend, no Anthropic API.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest.mock as mock
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

# L44 house-rules is MANDATORY + fail-closed (ADR-0143): adapter._check_house_
# rules_or_fail spawns the real `claude` CLI to classify every dispatched
# task, including these opencode/engine-switch dispatches. Without the CLI
# (CI, or any box that hasn't installed it) the spawn fails (spawn_missing)
# and the gate fail-closed-escalates, replacing the expected echoed
# final_text with an operator-approval message. These tests are about the
# L22 engine-switch dispatch path, not L44 compliance, so stub the gate to a
# fixed "allow" verdict — the sanctioned classifier test-double (see
# test_adapter_house_rules_binary.py::_run_escalate_message for the same
# pattern with an escalate verdict).
import house_rules as _hr  # type: ignore  # noqa: E402

_allow = _hr.HouseRulesDecision("allow", "", "clear", 1.0)


class _AllowGate:
    @classmethod
    def from_repo(cls, **kw):
        return cls()

    def classify(self, *a, **kw):
        return _allow


mock.patch.object(_hr, "HouseRulesGate", _AllowGate).start()


def _section(title: str) -> None:
    print(f"\n=== {title} ===")


def _fresh_adapter() -> object:
    for mod in list(sys.modules):
        if mod == "adapter":
            del sys.modules[mod]
    import adapter  # type: ignore
    return adapter


_FAKE_OPENCODE = (
    "#!/usr/bin/env python3\n"
    "# Layer 22 fake opencode binary — emits the documented JSON\n"
    "# event shape (step_start + text + EOF). The last positional\n"
    "# argv element is the prompt; we echo it back.\n"
    "import json, sys, time\n"
    "prompt = sys.argv[-1] if len(sys.argv) > 1 else 'noprompt'\n"
    "sys.stdout.write(json.dumps({\n"
    "    'type': 'step_start', 'timestamp': 1, 'sessionID': 'ses_X',\n"
    "    'part': {'id': 'stp_1', 'type': 'step-start'},\n"
    "}) + '\\n')\n"
    "sys.stdout.flush()\n"
    "time.sleep(0.05)\n"
    "sys.stdout.write(json.dumps({\n"
    "    'type': 'text', 'timestamp': 2, 'sessionID': 'ses_X',\n"
    "    'part': {'id': 'prt_1', 'type': 'text',\n"
    "             'text': f'opencode-echo: {prompt}',\n"
    "             'time': {'start': 1, 'end': 2}},\n"
    "}) + '\\n')\n"
    "sys.stdout.flush()\n"
)


def _setup_fake_opencode(name: str) -> tuple[Path, Path]:
    work = Path(tempfile.mkdtemp(prefix=f"engine-switch-{name}-"))
    bin_dir = work / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "opencode"
    fake.write_text(_FAKE_OPENCODE)
    fake.chmod(0o755)
    return work, bin_dir


def _local_coder_profile() -> dict:
    """Mirror the bundle persona's relevant fields. The adapter only
    inspects `default_engine`, `model`, `append_system` on the
    opencode path; the rest is for completeness."""
    return {
        "default_engine": "opencode",
        "model": "ollama-cloud/qwen3-coder-next",
        "permission_mode": "bypassPermissions",
        "allowed_tools": [],
        "disallowed_tools": [],
        "append_system": "be concise",
        "_persona": "local-coder",
    }


# ---------------------------------------------------------------------------
# 1. opencode pre-dispatch
# ---------------------------------------------------------------------------


def test_opencode_dispatch_runs_fake_binary() -> None:
    _section("opencode pre-dispatch — fake opencode → echo final_text")
    work, bin_dir = _setup_fake_opencode("dispatch")
    saved = {k: os.environ.get(k) for k in (
        "PATH", "OPENCODE_BIN", "CORVIN_HOME", "ADAPTER_STREAM_IDLE_TIMEOUT",
        "CORVIN_AGENTS_SKIP_LIVE", "CORVIN_INTEGRATION_TEST",
    )}
    try:
        os.environ["PATH"] = f"{bin_dir}:{saved['PATH'] or ''}"
        os.environ["OPENCODE_BIN"] = str(bin_dir / "opencode")
        os.environ["CORVIN_HOME"] = str(work / "corvinos")
        os.environ["ADAPTER_STREAM_IDLE_TIMEOUT"] = "10"
        # ADR-0150: this test mocks the engine — activate the dual-env license
        # bypass so the live chat_turns_per_day gate at the dispatcher is skipped.
        os.environ["CORVIN_AGENTS_SKIP_LIVE"] = "1"
        os.environ["CORVIN_INTEGRATION_TEST"] = "1"

        adapter = _fresh_adapter()
        result = adapter.call_claude_streaming(
            prompt="ping-opencode",
            channel="test",
            chat_key="opencode-chat",
            profile=_local_coder_profile(),
        )
        assert "opencode-echo" in result, \
            f"expected opencode-echo in final_text, got: {result!r}"
        assert "ping-opencode" in result, \
            f"expected prompt echo, got: {result!r}"
        print(f"PASS: opencode dispatch returned final_text={result!r}")
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(work, ignore_errors=True)


# ---------------------------------------------------------------------------
# 2. Regression — claude path stays default
# ---------------------------------------------------------------------------


def test_claude_path_unchanged_without_default_engine() -> None:
    _section("regression — profile without default_engine stays on Claude")
    work, bin_dir = _setup_fake_opencode("no-route")
    saved = {k: os.environ.get(k) for k in (
        "PATH", "OPENCODE_BIN", "CORVIN_HOME",
        "ADAPTER_FAKE_CLAUDE", "ADAPTER_STREAM_IDLE_TIMEOUT",
    )}
    try:
        # If the dispatch WRONGLY routed to opencode here, the fake
        # opencode binary would be invoked and we'd see "opencode-echo"
        # in the output. With ADAPTER_FAKE_CLAUDE=1 (the legacy
        # adapter fast-stub path), no claude binary is needed; the
        # adapter returns a "[fake-stream]" prefix string.
        os.environ["PATH"] = f"{bin_dir}:{saved['PATH'] or ''}"
        os.environ["OPENCODE_BIN"] = str(bin_dir / "opencode")
        os.environ["CORVIN_HOME"] = str(work / "corvinos")
        os.environ["ADAPTER_FAKE_CLAUDE"] = "1"
        os.environ["ADAPTER_STREAM_IDLE_TIMEOUT"] = "10"

        adapter = _fresh_adapter()
        result = adapter.call_claude_streaming(
            prompt="ping-claude",
            channel="test",
            chat_key="claude-chat",
            profile={"permission_mode": "bypassPermissions"},
        )
        assert "[fake-stream]" in result, \
            f"expected legacy [fake-stream] path, got: {result!r}"
        assert "opencode-echo" not in result, \
            f"WRONG: opencode dispatch fired without default_engine"
        print(f"PASS: claude path stayed default; result={result!r}")
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(work, ignore_errors=True)


# ---------------------------------------------------------------------------
# 3. inject_btw capability-gate
# ---------------------------------------------------------------------------


def test_inject_btw_on_engine_without_mid_stream_inject_returns_false() -> None:
    _section("inject_btw — engine without mid_stream_inject → False")
    adapter = _fresh_adapter()

    class FakeEngineNoInject:
        name = "fake_no_inject"
        capabilities = {"mid_stream_inject": False}
        # No inject() method on purpose — gate must prevent the call.

    fake = FakeEngineNoInject()
    chat_key = "no-inject-chat"
    try:
        with adapter._running_engines_guard:
            adapter._running_engines[chat_key] = fake  # type: ignore[assignment]
        delivered = adapter.inject_btw(chat_key, "hello mid-stream")
        assert delivered is False, \
            f"expected False (no mid_stream_inject), got {delivered!r}"
        print("PASS: capability-gate prevented engine.inject() on "
              "engine without mid_stream_inject")
    finally:
        with adapter._running_engines_guard:
            adapter._running_engines.pop(chat_key, None)


if __name__ == "__main__":
    failures = 0
    for case in (
        test_opencode_dispatch_runs_fake_binary,
        test_claude_path_unchanged_without_default_engine,
        test_inject_btw_on_engine_without_mid_stream_inject_returns_false,
    ):
        try:
            case()
        except AssertionError as e:
            failures += 1
            print(f"FAIL: {case.__name__}: {e}")
        except Exception as e:  # noqa: BLE001
            failures += 1
            print(f"ERROR: {case.__name__}: {type(e).__name__}: {e}")
    total = 3
    if failures:
        print(f"\n{failures}/{total} failed")
        sys.exit(1)
    print(f"\nAll {total} tests passed")
