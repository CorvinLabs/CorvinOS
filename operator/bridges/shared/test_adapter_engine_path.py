#!/usr/bin/env python3
"""test_adapter_engine_path.py — Phase 2.2 (ADR-0002).

Drives `call_claude_streaming` with `CORVIN_USE_ENGINE_LAYER=1` against
a fake `claude` binary so we can assert the engine-driven path:

  1. Simple prompt → final_text matches the fake echo result.
  2. Prompt with tool_use events → on_status callback fires per tool_use.
  3. Mid-stream cancel via /cancel → call returns "" and proc terminated.

The fake binary is a stand-alone Python script on PATH (mirrors
test_adapter_btw's pattern). No real Anthropic API spend.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import textwrap
import threading
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
    # Drop adapter AND the agents.claude_code module so module-level
    # constants (CLAUDE_BIN read from env at import time) pick up
    # per-test overrides.
    for mod in list(sys.modules):
        if mod == "adapter" or mod == "agents" or mod.startswith("agents."):
            del sys.modules[mod]
    import adapter  # type: ignore
    # L44 (ADR-0143): stub the Tier-1 acceptable-use classifier to a benign
    # clear — these tests fake the `claude` binary and cannot answer the gate's
    # `claude -p` classifier call (would fail-closed before the engine path).
    # Gate coverage lives in test_house_rules.py; test-local double only.
    adapter._house_rules_classifier = lambda task, rules, auth, **_kw: ("", 1.0, "test-benign")
    return adapter


_FAKE_ECHO_SCRIPT = textwrap.dedent("""\
#!/usr/bin/env python3
# Phase 2.2 fake claude binary — reads stream-json `user` lines from
# stdin, emits a single assistant + result event per turn.
import json, sys, time
for raw in sys.stdin:
    line = raw.strip()
    if not line:
        continue
    try:
        msg = json.loads(line)
    except json.JSONDecodeError:
        continue
    if msg.get("type") != "user":
        continue
    text = (msg.get("message") or {}).get("content") or ""
    if isinstance(text, list):
        text = " ".join(b.get("text", "") for b in text if isinstance(b, dict))
    sys.stdout.write(json.dumps({
        "type": "assistant",
        "message": {"content": [{"type": "text", "text": f"echo: {text}"}]},
    }) + "\\n")
    sys.stdout.flush()
    time.sleep(0.1)
    sys.stdout.write(json.dumps({
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "result": f"final: {text}",
        "usage": {"input_tokens": 5, "output_tokens": 5},
    }) + "\\n")
    sys.stdout.flush()
""")


_FAKE_TOOL_SCRIPT = textwrap.dedent("""\
#!/usr/bin/env python3
# Phase 2.2 fake claude with tool_use — emits two assistant messages
# carrying tool_use blocks, then a result.
import json, sys, time
for raw in sys.stdin:
    line = raw.strip()
    if not line:
        continue
    try:
        msg = json.loads(line)
    except json.JSONDecodeError:
        continue
    if msg.get("type") != "user":
        continue
    sys.stdout.write(json.dumps({
        "type": "assistant",
        "message": {"content": [
            {"type": "tool_use", "id": "t1", "name": "TodoWrite",
             "input": {"todos": [
                 {"content": "Step 1", "status": "in_progress",
                  "activeForm": "Doing step 1"},
             ]}},
        ]},
    }) + "\\n")
    sys.stdout.flush()
    time.sleep(0.05)
    sys.stdout.write(json.dumps({
        "type": "assistant",
        "message": {"content": [
            {"type": "tool_use", "id": "t2", "name": "ExitPlanMode",
             "input": {"plan": "Plan ready."}},
        ]},
    }) + "\\n")
    sys.stdout.flush()
    time.sleep(0.05)
    sys.stdout.write(json.dumps({
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "result": "OK",
        "usage": {"input_tokens": 1, "output_tokens": 1},
    }) + "\\n")
    sys.stdout.flush()
    break
""")


_FAKE_HANG_SCRIPT = textwrap.dedent("""\
#!/usr/bin/env python3
# Phase 2.2 fake claude that hangs — never emits result. Used for
# the mid-stream cancel test: adapter SIGTERMs the proc; engine path
# returns "" because rc < 0 + SIGTERM + not timed_out.
import json, sys, time
for raw in sys.stdin:
    line = raw.strip()
    if not line:
        continue
    try:
        msg = json.loads(line)
    except json.JSONDecodeError:
        continue
    if msg.get("type") != "user":
        continue
    sys.stdout.write(json.dumps({
        "type": "assistant",
        "message": {"content": [{"type": "text", "text": "starting..."}]},
    }) + "\\n")
    sys.stdout.flush()
    # Hang forever
    time.sleep(60)
""")


def _pop_claude_bin_pins() -> dict:
    """Clear CORVIN_CLAUDE_BIN/CLAUDE_BIN and return their prior values.

    ADR-0215 adversarial review (2026-07-24), CRITICAL finding: every test
    below that fakes the `claude` binary via a PATH prepend alone — without
    also clearing these two pins — silently calls the REAL, locally
    installed `claude` CLI instead of the fake test double whenever
    CORVIN_CLAUDE_BIN happens to be set in the environment. That is not a
    hypothetical: `helper_model.resolve_claude_bin()` and
    `agents.claude_code._configured_claude_bin()` BOTH check
    CORVIN_CLAUDE_BIN (then CLAUDE_BIN) BEFORE ever consulting PATH — by
    design, so a real deployment's pin survives a stripped systemd PATH
    (see house_rules.py's docstring, "[Engine-Autodetect Stripped-PATH]").
    The same design makes an unset-CORVIN_CLAUDE_BIN assumption in a test
    silently wrong on any machine where that canonical production pin is
    exported — which is to say, on exactly the machines these tests exist
    to protect. Reproduced directly: with CORVIN_CLAUDE_BIN set, this
    file's simple-prompt test took 12s and failed on an idle-timeout
    instead of the fake script's instant echo; with it unset, 0.3s and
    green. Real cost/PII risk: a real `claude -p` subprocess receiving the
    fake test's synthetic stream-json input is a real, billable API call
    with unpredictable (and potentially personalized, real-account) output
    asserted against as if it were the deterministic fake.
    """
    saved = {k: os.environ.get(k) for k in ("CORVIN_CLAUDE_BIN", "CLAUDE_BIN")}
    os.environ.pop("CORVIN_CLAUDE_BIN", None)
    os.environ.pop("CLAUDE_BIN", None)
    return saved


def _restore_claude_bin_pins(saved: dict) -> None:
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


def _setup_fake(name: str, script: str) -> tuple[Path, Path]:
    work = Path(tempfile.mkdtemp(prefix=f"engine-path-{name}-"))
    fake_dir = work / "bin"
    fake_dir.mkdir()
    fake_claude = fake_dir / "claude"
    fake_claude.write_text(script)
    fake_claude.chmod(0o755)
    return work, fake_dir


# ---------------------------------------------------------------------------
# 1. Simple prompt
# ---------------------------------------------------------------------------


def test_engine_path_simple_prompt() -> None:
    _section("engine path — simple prompt → final_text echo")
    work, fake_dir = _setup_fake("simple", _FAKE_ECHO_SCRIPT)
    saved_path = os.environ.get("PATH", "")
    saved_home = os.environ.get("CORVIN_HOME")
    saved_claude_bin = _pop_claude_bin_pins()
    try:
        os.environ["PATH"] = f"{fake_dir}:{saved_path}"
        os.environ["CORVIN_HOME"] = str(work / "corvinos")
        os.environ["CORVIN_USE_ENGINE_LAYER"] = "1"
        os.environ["ADAPTER_STREAM_IDLE_TIMEOUT"] = "10"

        adapter = _fresh_adapter()
        result = adapter.call_claude_streaming(
            prompt="ping-engine",
            channel="test",
            chat_key="simple-chat",
            profile={"permission_mode": "bypassPermissions"},
        )
        assert result == "final: ping-engine", \
            f"unexpected final_text: {result!r}"
        print(f"PASS: engine path returned final_text={result!r}")
    finally:
        os.environ["PATH"] = saved_path
        if saved_home is not None:
            os.environ["CORVIN_HOME"] = saved_home
        else:
            os.environ.pop("CORVIN_HOME", None)
        os.environ.pop("CORVIN_USE_ENGINE_LAYER", None)
        os.environ.pop("ADAPTER_STREAM_IDLE_TIMEOUT", None)
        _restore_claude_bin_pins(saved_claude_bin)
        shutil.rmtree(work, ignore_errors=True)


# ---------------------------------------------------------------------------
# 2. tool_use → on_status fires
# ---------------------------------------------------------------------------


def test_engine_path_tool_use_status() -> None:
    _section("engine path — tool_use events fire on_status")
    work, fake_dir = _setup_fake("tools", _FAKE_TOOL_SCRIPT)
    saved_path = os.environ.get("PATH", "")
    saved_home = os.environ.get("CORVIN_HOME")
    saved_claude_bin = _pop_claude_bin_pins()
    try:
        os.environ["PATH"] = f"{fake_dir}:{saved_path}"
        os.environ["CORVIN_HOME"] = str(work / "corvinos")
        os.environ["CORVIN_USE_ENGINE_LAYER"] = "1"
        os.environ["ADAPTER_STREAM_IDLE_TIMEOUT"] = "10"

        adapter = _fresh_adapter()
        statuses: list[tuple[str, str]] = []

        def on_status(text: str, *, tool_name: str = ""):
            statuses.append((tool_name, text))

        result = adapter.call_claude_streaming(
            prompt="plan-please",
            channel="test",
            chat_key="tool-chat",
            profile={"permission_mode": "bypassPermissions"},
            on_status=on_status,
            status_mode="compact",
        )
        assert result == "OK", f"unexpected final_text: {result!r}"
        # compact mode emits status for TodoWrite + ExitPlanMode (both
        # plan-relevant). Other tools (Read/Edit/Bash) suppressed.
        tool_names = [s[0] for s in statuses]
        assert "TodoWrite" in tool_names, \
            f"TodoWrite status missing: {statuses!r}"
        assert "ExitPlanMode" in tool_names, \
            f"ExitPlanMode status missing: {statuses!r}"
        print(f"PASS: engine path fired on_status for "
              f"{[s[0] for s in statuses]}")
    finally:
        os.environ["PATH"] = saved_path
        if saved_home is not None:
            os.environ["CORVIN_HOME"] = saved_home
        else:
            os.environ.pop("CORVIN_HOME", None)
        os.environ.pop("CORVIN_USE_ENGINE_LAYER", None)
        os.environ.pop("ADAPTER_STREAM_IDLE_TIMEOUT", None)
        _restore_claude_bin_pins(saved_claude_bin)
        shutil.rmtree(work, ignore_errors=True)


# ---------------------------------------------------------------------------
# 3. Mid-stream cancel
# ---------------------------------------------------------------------------


def test_engine_path_mid_stream_cancel() -> None:
    _section("engine path — /cancel mid-stream returns empty string")
    work, fake_dir = _setup_fake("hang", _FAKE_HANG_SCRIPT)
    saved_path = os.environ.get("PATH", "")
    saved_home = os.environ.get("CORVIN_HOME")
    saved_claude_bin = _pop_claude_bin_pins()
    try:
        os.environ["PATH"] = f"{fake_dir}:{saved_path}"
        os.environ["CORVIN_HOME"] = str(work / "corvinos")
        os.environ["CORVIN_USE_ENGINE_LAYER"] = "1"
        # Watchdog generous so the timeout doesn't fire before cancel
        os.environ["ADAPTER_STREAM_IDLE_TIMEOUT"] = "30"

        adapter = _fresh_adapter()
        result_box: dict = {}
        chat_key = "cancel-chat"

        def runner():
            try:
                result_box["final"] = adapter.call_claude_streaming(
                    prompt="hang-prompt",
                    channel="test",
                    chat_key=chat_key,
                    profile={"permission_mode": "bypassPermissions"},
                )
            except Exception as e:  # noqa: BLE001
                result_box["error"] = repr(e)

        t = threading.Thread(target=runner, daemon=True)
        t.start()

        # Wait until adapter has registered the running subproc.
        deadline = time.time() + 5.0
        while time.time() < deadline:
            with adapter._running_subprocs_guard:
                if chat_key in adapter._running_subprocs:
                    break
            time.sleep(0.05)
        else:
            raise AssertionError(
                "adapter never registered a subproc for cancel-chat",
            )

        # Cancel.
        n = adapter._cancel_chat(chat_key)
        assert n >= 1, f"cancel signalled {n} processes"

        t.join(timeout=10)
        assert not t.is_alive(), "engine streaming did not unwind on cancel"
        assert result_box.get("error") is None, \
            f"streaming raised: {result_box.get('error')}"
        # Behaviour parity: legacy path also returns "" on user-cancel
        # (signal.SIGTERM not from the watchdog).
        assert result_box.get("final") == "", \
            f"expected empty on cancel, got {result_box.get('final')!r}"
        print("PASS: engine path returned '' on /cancel mid-stream")
    finally:
        os.environ["PATH"] = saved_path
        if saved_home is not None:
            os.environ["CORVIN_HOME"] = saved_home
        else:
            os.environ.pop("CORVIN_HOME", None)
        os.environ.pop("CORVIN_USE_ENGINE_LAYER", None)
        os.environ.pop("ADAPTER_STREAM_IDLE_TIMEOUT", None)
        _restore_claude_bin_pins(saved_claude_bin)
        shutil.rmtree(work, ignore_errors=True)


_FAKE_BTW_SCRIPT = textwrap.dedent("""\
#!/usr/bin/env python3
# Phase 2.3 fake claude — same as the /btw E2E in test_adapter_btw, but
# kept here so this file exercises the engine-path /btw routing in
# isolation. Each user message produces assistant + (1.5s sleep) + result.
import json, sys, time
for raw in sys.stdin:
    line = raw.strip()
    if not line:
        continue
    try:
        msg = json.loads(line)
    except json.JSONDecodeError:
        continue
    if msg.get("type") != "user":
        continue
    text = (msg.get("message") or {}).get("content") or ""
    if isinstance(text, list):
        text = " ".join(b.get("text", "") for b in text if isinstance(b, dict))
    sys.stdout.write(json.dumps({
        "type": "assistant",
        "message": {"content": [{"type": "text", "text": f"echo: {text}"}]},
    }) + "\\n")
    sys.stdout.flush()
    time.sleep(1.5)
    sys.stdout.write(json.dumps({
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "result": f"reply for: {text}",
    }) + "\\n")
    sys.stdout.flush()
""")


def test_engine_path_btw_routes_through_engine() -> None:
    _section("engine path — /btw routes through engine.inject()")
    work, fake_dir = _setup_fake("btw-route", _FAKE_BTW_SCRIPT)
    saved_path = os.environ.get("PATH", "")
    saved_home = os.environ.get("CORVIN_HOME")
    saved_claude_bin = _pop_claude_bin_pins()
    try:
        os.environ["PATH"] = f"{fake_dir}:{saved_path}"
        os.environ["CORVIN_HOME"] = str(work / "corvinos")
        os.environ["CORVIN_USE_ENGINE_LAYER"] = "1"
        os.environ["ADAPTER_STREAM_IDLE_TIMEOUT"] = "10"

        adapter = _fresh_adapter()
        result_box: dict = {}
        chat_key = "btw-engine-chat"

        def runner():
            try:
                result_box["final"] = adapter.call_claude_streaming(
                    prompt="prompt-A",
                    channel="test",
                    chat_key=chat_key,
                    profile={"permission_mode": "bypassPermissions"},
                )
            except Exception as e:  # noqa: BLE001
                result_box["error"] = repr(e)

        t = threading.Thread(target=runner, daemon=True)
        t.start()

        # Wait for engine registration (proves we're on the engine path).
        deadline = time.time() + 5.0
        while time.time() < deadline:
            with adapter._running_engines_guard:
                if chat_key in adapter._running_engines:
                    break
            time.sleep(0.05)
        else:
            raise AssertionError(
                "adapter never registered the engine for the chat",
            )

        # Spy on the engine's inject() so we can assert it was the
        # actual write path (not the legacy raw-stdin fallback).
        with adapter._running_engines_guard:
            engine = adapter._running_engines[chat_key]
        original_inject = engine.inject
        inject_calls: list[str] = []

        def spy_inject(text: str) -> bool:
            inject_calls.append(text)
            return original_inject(text)

        engine.inject = spy_inject  # type: ignore[method-assign]

        ok = adapter.inject_btw(chat_key, "btw-followup-engine")
        assert ok, "inject_btw should have written via engine.inject"
        assert inject_calls == ["btw-followup-engine"], \
            f"engine.inject was not called: {inject_calls!r}"

        t.join(timeout=15)
        assert not t.is_alive(), \
            "call_claude_streaming did not return in time"
        assert result_box.get("final") == "reply for: btw-followup-engine", \
            f"second reply did not win: {result_box.get('final')!r}"
        print("PASS: engine.inject was called once and produced "
              f"final_text={result_box.get('final')!r}")
    finally:
        os.environ["PATH"] = saved_path
        if saved_home is not None:
            os.environ["CORVIN_HOME"] = saved_home
        else:
            os.environ.pop("CORVIN_HOME", None)
        os.environ.pop("CORVIN_USE_ENGINE_LAYER", None)
        os.environ.pop("ADAPTER_STREAM_IDLE_TIMEOUT", None)
        _restore_claude_bin_pins(saved_claude_bin)
        shutil.rmtree(work, ignore_errors=True)


# ---------------------------------------------------------------------------
# 5. Binary-not-found → surfaces real error, not misleading timeout message
# ---------------------------------------------------------------------------


def test_engine_path_no_engine_reachable_surfaces_clear_notice() -> None:
    """ADR-0159 M1 auto-detect + "degradation is not silent".

    Historical context: this test originally drove the legacy claude-direct
    path with a missing 'claude' binary and asserted a FileNotFoundError-style
    message ('not found' / 'No such file'). ADR-0159 M1 changed the dispatch:
    when the claude CLI is absent from PATH, the adapter auto-detects and
    defaults the OS engine to ``hermes`` (local Ollama) so a fresh install with
    no Anthropic credentials still boots. That is the *intended* behaviour and
    the old assertion no longer describes the real path.

    The genuine UX gap that ADR-0159 M1 exposed: if BOTH the claude CLI AND
    Ollama are absent (the true brand-new-user state), hermes never streams,
    the idle watchdog fires, and the hermes path used to fall through to its
    success branch and return ``""`` — a silent empty reply after ~10s. ADR-0159
    itself states the degraded path "is not silent". This test now asserts the
    fixed invariant: when no engine is reachable at all, the user gets a CLEAR,
    NON-EMPTY notice (never a silent empty string, never the misleading
    'engine spawn timed out before producing a process').
    """
    _section("engine path — no engine reachable → clear non-empty notice")
    work = Path(tempfile.mkdtemp(prefix="engine-path-noengine-"))
    empty_dir = work / "bin"
    empty_dir.mkdir()
    saved = {
        k: os.environ.get(k)
        for k in (
            "PATH", "CLAUDE_BIN", "CORVIN_CLAUDE_BIN", "CORVIN_HOME",
            "CORVIN_CLAUDE_BIN_FALLBACKS",
            "CORVIN_USE_ENGINE_LAYER", "ADAPTER_STREAM_IDLE_TIMEOUT",
            "ADAPTER_ROUTING_MODE", "CORVIN_OS_ENGINE", "CORVIN_OLLAMA_BASE_URL",
        )
    }
    try:
        # Empty PATH so shutil.which("claude") is None → ADR-0159 M1 auto-detect
        # routes the OS turn to hermes.
        os.environ["PATH"] = str(empty_dir)
        os.environ.pop("CLAUDE_BIN", None)
        os.environ.pop("CORVIN_OS_ENGINE", None)  # let auto-detect run
        # Pin CORVIN_CLAUDE_BIN to a non-existent absolute path. The hardened
        # resolver the auto-detect now uses also probes the built-in fallback
        # locations (~/.local/bin/claude, /usr/local/bin/claude, …); on a dev/CI
        # box where claude IS installed there, an empty PATH alone no longer
        # simulates "claude absent". An absolute non-existent pin is honoured
        # as-is by the resolver and deterministically reports absent regardless
        # of what is installed on the host → the OS turn falls to hermes.
        os.environ["CORVIN_CLAUDE_BIN"] = str(empty_dir / "claude_NOT_INSTALLED")
        os.environ["CORVIN_CLAUDE_BIN_FALLBACKS"] = str(empty_dir / "claude_NOT_INSTALLED")
        os.environ["CORVIN_HOME"] = str(work / "corvinos")
        os.environ["CORVIN_USE_ENGINE_LAYER"] = "1"
        os.environ["ADAPTER_STREAM_IDLE_TIMEOUT"] = "5"
        os.environ["ADAPTER_ROUTING_MODE"] = "off"
        # Point Ollama at a closed loopback port so hermes is deterministically
        # unreachable (connection refused) without depending on whether a real
        # Ollama happens to be running on the dev/CI box.
        os.environ["CORVIN_OLLAMA_BASE_URL"] = "http://127.0.0.1:1"

        adapter = _fresh_adapter()
        result = adapter.call_claude_streaming(
            prompt="should-surface-a-notice",
            channel="test",
            chat_key="noengine-chat",
            profile={"permission_mode": "bypassPermissions"},
        )
        # 1. Auto-detect must NOT end with a silent empty reply.
        assert result.strip(), (
            f"no-engine-reachable returned a SILENT empty string (UX regression): {result!r}"
        )
        # 2. Must NOT be the misleading internal spawn-timeout message.
        assert "engine spawn timed out before producing a process" not in result, (
            f"adapter returned misleading internal timeout message: {result!r}"
        )
        # 3. Must clearly point at the engine/Ollama problem so a brand-new user
        #    knows what to do. Either deterministic outcome is acceptable:
        #      - connection refused → "Hermes/Ollama is unreachable" / "unavailable"
        #      - idle watchdog (no events) → "No engine reachable" / "engine spawn failed"
        lowered = result.lower()
        assert any(
            tok in lowered
            for tok in ("ollama", "hermes", "engine spawn failed", "no engine reachable")
        ), f"result does not clearly name the engine/Ollama problem: {result!r}"
        print(f"PASS: no engine reachable surfaced a clear notice: {result!r}")
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(work, ignore_errors=True)


# ---------------------------------------------------------------------------
# 6. Stripped PATH + off-PATH claude → auto-detect MUST pick claude_code
#    (regression for the "hermes connect error: timed out" Discord bug)
# ---------------------------------------------------------------------------


def test_engine_autodetect_offpath_claude_resolves_to_claude_code() -> None:
    """Regression: a stripped PATH must NOT silently downgrade to hermes when
    the claude CLI is installed off-PATH.

    Root cause (fixed): the ADR-0159 M1 auto-detect probed a bare
    ``shutil.which("claude")``. The adapter runs under systemd / bridge.sh with
    a stripped PATH that lacks ``~/.local/bin`` (where Claude Code installs the
    CLI), so ``which()`` returned ``None`` EVEN WHEN claude was installed. The
    OS turn was then silently routed to hermes → Ollama timeout
    ("hermes connect error: timed out") although claude was the intended engine.
    The fix probes through the same hardened resolver
    (``helper_model.resolve_claude_bin``: ``CORVIN_CLAUDE_BIN`` → PATH → known
    install locations) the WorkerEngine and every helper spawn already use —
    the identical false-negative commit 79de989 fixed for the L44 helper path,
    which had missed this auto-detect probe.

    Hermetic proof: a working fake ``claude`` is placed at an OFF-PATH location
    and registered via ``CORVIN_CLAUDE_BIN_FALLBACKS`` (production: the built-in
    ``~/.local/bin/claude``). With hermes pointed at a dead loopback port, ANY
    regression that re-introduces the bare ``which()`` probe falls to hermes and
    fails this test loudly.
    """
    _section(
        "engine autodetect — off-PATH claude resolves to claude_code, not hermes"
    )
    work = Path(tempfile.mkdtemp(prefix="engine-autodetect-offpath-"))
    empty_dir = work / "bin"        # on PATH, but contains NO claude
    empty_dir.mkdir()
    offpath_dir = work / "offpath"  # NOT on PATH — mirrors ~/.local/bin
    offpath_dir.mkdir()
    fake_claude = offpath_dir / "claude"
    # PATH is stripped to ``empty_dir`` only (no python3 on it), so pin the
    # fake binary's interpreter to an absolute path instead of relying on the
    # ``/usr/bin/env python3`` shebang resolving through PATH.
    _abs_shebang_script = _FAKE_ECHO_SCRIPT.replace(
        "#!/usr/bin/env python3", f"#!{sys.executable}", 1
    )
    fake_claude.write_text(_abs_shebang_script)
    fake_claude.chmod(0o755)
    saved = {
        k: os.environ.get(k)
        for k in (
            "PATH", "CLAUDE_BIN", "CORVIN_CLAUDE_BIN", "CORVIN_HOME",
            "CORVIN_CLAUDE_BIN_FALLBACKS", "CORVIN_USE_ENGINE_LAYER",
            "ADAPTER_STREAM_IDLE_TIMEOUT", "ADAPTER_ROUTING_MODE",
            "CORVIN_OS_ENGINE", "CORVIN_OLLAMA_BASE_URL",
        )
    }
    try:
        os.environ["PATH"] = str(empty_dir)            # claude NOT on PATH
        os.environ.pop("CLAUDE_BIN", None)
        os.environ.pop("CORVIN_CLAUDE_BIN", None)       # no explicit pin
        os.environ.pop("CORVIN_OS_ENGINE", None)        # let auto-detect run
        # The known-location fallback list the resolver probes. In production
        # this is the built-in ~/.local/bin/claude; here we point it at the
        # off-PATH fake so the test is hermetic and platform-independent.
        os.environ["CORVIN_CLAUDE_BIN_FALLBACKS"] = str(fake_claude)
        os.environ["CORVIN_HOME"] = str(work / "corvinos")
        os.environ["CORVIN_USE_ENGINE_LAYER"] = "1"
        os.environ["ADAPTER_STREAM_IDLE_TIMEOUT"] = "10"
        # Dead loopback port: if the fix regresses and the OS turn falls to
        # hermes, the call surfaces an Ollama/hermes error and the asserts below
        # fail — no accidental pass via a real Ollama on the dev/CI box.
        os.environ["CORVIN_OLLAMA_BASE_URL"] = "http://127.0.0.1:1"

        adapter = _fresh_adapter()
        result = adapter.call_claude_streaming(
            prompt="ping-autodetect",
            channel="test",
            chat_key="autodetect-offpath-chat",
            profile={"permission_mode": "bypassPermissions"},
        )
        # The fake claude echoes "final: <prompt>" — proof the OS turn ran on
        # claude_code, not hermes.
        assert result == "final: ping-autodetect", (
            "stripped-PATH auto-detect did not resolve to claude_code "
            f"(got {result!r}); a hermes downgrade would surface an "
            "Ollama/hermes error instead"
        )
        lowered = result.lower()
        assert "hermes" not in lowered and "ollama" not in lowered, (
            f"auto-detect leaked a hermes/Ollama path: {result!r}"
        )
        print(f"PASS: off-PATH claude resolved to claude_code: {result!r}")
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(work, ignore_errors=True)


def main() -> int:
    tests = [
        test_engine_path_simple_prompt,
        test_engine_path_tool_use_status,
        test_engine_path_mid_stream_cancel,
        test_engine_path_btw_routes_through_engine,
        test_engine_path_no_engine_reachable_surfaces_clear_notice,
        test_engine_autodetect_offpath_claude_resolves_to_claude_code,
    ]
    failures = 0
    for fn in tests:
        try:
            fn()
        except AssertionError as e:
            print(f"FAIL  {fn.__name__}: {e}")
            failures += 1
        except Exception as e:  # noqa: BLE001
            print(f"FAIL  {fn.__name__}: {type(e).__name__}: {e}")
            failures += 1
    print(f"\n{len(tests) - failures}/{len(tests)} passed")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
