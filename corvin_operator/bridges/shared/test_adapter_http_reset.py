#!/usr/bin/env python3
"""test_adapter_http_reset.py — E2E for the HTTP-transient reset path.

Closes the production gap observed in the journal:

    Mai 16-17, four bare-"400" errors landed in the adapter; the
    `should_reset` gate only matched "session", "stream idle",
    "idle timeout" — so a transient API failure left the `--continue`
    session half-broken and the next turn risked the same 400.

Per CLAUDE.md (`feedback_per_subtask_e2e`): no mocks — we spawn a real
Python subprocess that impersonates `claude -p --output-format stream-
json`. First invocation emits a result-error with api_error_status=400;
the second invocation (after the adapter wipes the session and retries)
emits a clean result. The test asserts that the adapter:

  1. Recognises the bare HTTP-400 as transient → resets + retries.
  2. The retry actually fires (final text is the second script's).
  3. The session-state marker (.session_started) is wiped before retry.
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import textwrap
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))


def _section(title: str) -> None:
    print(f"\n=== {title} ===")


def _fresh_adapter():
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


def _make_two_phase_claude(tmp: Path, *, naked: bool) -> Path:
    """Fake `claude` that emits api-error on first call, "ok" on second.

    `naked=True` → emit only `api_error_status: 400`, no body. That is
    the production failure mode where the CLI strips the error detail.
    `naked=False` → emit a structured error with a body, so the stderr-
    enrichment path can be observed too.

    State is persisted in a marker file so the adapter's retry actually
    sees a different response on the second invocation.
    """
    bin_dir = tmp / "fake-bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    state_file = tmp / "phase.txt"
    error_payload = (
        '{"type": "result", "is_error": true, '
        '"subtype": "error", "api_error_status": "400"}'
        if naked else
        '{"type": "result", "is_error": true, "subtype": "error", '
        '"result": "API Error: 400 messages.0.content: empty text block"}'
    )
    script = bin_dir / "claude"
    body = textwrap.dedent(f'''\
        #!/usr/bin/env python3
        import json, os, sys, time
        state = {str(state_file)!r}
        # Phase counter: phase 1 emits the error, phase 2 emits success.
        try:
            phase = int(open(state).read().strip())
        except (FileNotFoundError, ValueError):
            phase = 1
        with open(state, "w") as fh:
            fh.write(str(phase + 1))
        # On the error phase, also write to stderr so the engine's
        # tail-enricher has something to attach to a naked status.
        if phase == 1:
            sys.stderr.write("anthropic api: HTTP 400 — bad request body\\n")
            sys.stderr.flush()
            sys.stdout.write('{{"type":"system","subtype":"init"}}\\n')
            sys.stdout.write({error_payload!r} + "\\n")
            sys.stdout.flush()
            sys.exit(0)
        # Retry phase — clean success.
        sys.stdout.write('{{"type":"system","subtype":"init"}}\\n')
        sys.stdout.write(json.dumps({{
            "type": "result", "is_error": False,
            "subtype": "success", "result": "retry-ok"
        }}) + "\\n")
        sys.stdout.flush()
    ''')
    script.write_text(body)
    script.chmod(0o755)
    return bin_dir


_saved_env_for_teardown: dict = {}


def _common_setup(tmp: Path, bin_dir: Path) -> None:
    global _saved_env_for_teardown
    # ADR-0215 adversarial review (2026-07-24), CRITICAL finding (same bug
    # class fixed in test_adapter_engine_path.py's _pop_claude_bin_pins()):
    # helper_model.resolve_claude_bin() / agents.claude_code.
    # _configured_claude_bin() BOTH check CORVIN_CLAUDE_BIN (then
    # CLAUDE_BIN) BEFORE consulting PATH — a pin set in the ambient
    # environment (the CANONICAL production pin per house_rules.py's own
    # docstring) silently bypasses this file's fake `claude` entirely,
    # spawning the REAL CLI against synthetic 400/429/500-simulation input
    # instead. Also: PATH was set here but never restored in _teardown()
    # (only ever prepended-to across the whole test session, and
    # CORVIN_CLAUDE_BIN/CLAUDE_BIN — if genuinely set in the ambient
    # environment — must be RESTORED, not just popped, so other tests in
    # the same pytest process don't lose a real pin they depend on).
    _saved_env_for_teardown = {
        k: os.environ.get(k)
        for k in ("PATH", "CORVIN_CLAUDE_BIN", "CLAUDE_BIN")
    }
    os.environ["PATH"] = f"{bin_dir}{os.pathsep}{os.environ['PATH']}"
    os.environ.pop("CORVIN_CLAUDE_BIN", None)
    os.environ.pop("CLAUDE_BIN", None)
    os.environ["ADAPTER_STREAM_IDLE_TIMEOUT"] = "30"
    os.environ["ADAPTER_HEARTBEAT_INTERVAL"] = "0"
    os.environ["ADAPTER_INBOX"] = str(tmp / "inbox")
    os.environ["ADAPTER_OUTBOX"] = str(tmp / "outbox")
    os.environ["CORVIN_HOME"] = str(tmp / "corvinOSHome")
    os.environ["VOICE_AUDIT_PATH"] = str(tmp / "audit.jsonl")
    # Force the engine path on so the legacy direct-spawn doesn't get
    # exercised by mistake.
    os.environ["CORVIN_USE_ENGINE_LAYER"] = "1"


def _teardown() -> None:
    global _saved_env_for_teardown
    for k, v in _saved_env_for_teardown.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    _saved_env_for_teardown = {}
    for v in ("ADAPTER_STREAM_IDLE_TIMEOUT", "ADAPTER_HEARTBEAT_INTERVAL",
              "ADAPTER_INBOX", "ADAPTER_OUTBOX", "CORVIN_HOME",
              "VOICE_AUDIT_PATH", "CORVIN_USE_ENGINE_LAYER"):
        os.environ.pop(v, None)


def test_naked_http_400_triggers_reset_and_retry() -> None:
    _section("naked 400 on --continue triggers session reset + retry")
    tmp = Path(tempfile.mkdtemp(prefix="adapter-http-reset-"))
    try:
        bin_dir = _make_two_phase_claude(tmp, naked=True)
        _common_setup(tmp, bin_dir)
        adapter = _fresh_adapter()

        # Simulate an existing `--continue`-capable session.
        workdir = adapter._session_dir("discord", "http-reset-1")
        workdir.mkdir(parents=True, exist_ok=True)
        (workdir / ".session_started").touch()
        assert (workdir / ".session_started").exists()

        t0 = time.time()
        ans = adapter.call_claude_streaming(
            "trigger 400", channel="discord", chat_key="http-reset-1",
            mode="unrestricted", profile=None,
        )
        elapsed = time.time() - t0

        # Retry phase wrote "retry-ok". If reset+retry hadn't fired,
        # we'd get back "Der Claude-Aufruf ist fehlgeschlagen: 400…".
        assert "retry-ok" in ans, (
            f"reset+retry path didn't recover from bare 400; got: {ans!r}"
        )
        assert elapsed < 15, f"recovery took too long: {elapsed:.1f}s"
        print(f"PASS: bare-400 → reset → retry → 'retry-ok' "
              f"(took {elapsed:.1f}s)")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        _teardown()


def test_429_retry_preserves_session() -> None:
    _section("429 rate-limit triggers retry WITHOUT wiping session")
    tmp = Path(tempfile.mkdtemp(prefix="adapter-http-429-"))
    try:
        bin_dir = tmp / "fake-bin"
        bin_dir.mkdir()
        state_file = tmp / "phase.txt"
        script = bin_dir / "claude"
        script.write_text(textwrap.dedent(f'''\
            #!/usr/bin/env python3
            import json, sys
            state = {str(state_file)!r}
            try:
                phase = int(open(state).read().strip())
            except (FileNotFoundError, ValueError):
                phase = 1
            with open(state, "w") as fh:
                fh.write(str(phase + 1))
            if phase == 1:
                sys.stdout.write('{{"type":"system","subtype":"init"}}\\n')
                sys.stdout.write('{{"type":"result","is_error":true,'
                                 '"subtype":"error","api_error_status":"429"}}\\n')
                sys.stdout.flush()
                sys.exit(0)
            sys.stdout.write('{{"type":"system","subtype":"init"}}\\n')
            sys.stdout.write(json.dumps({{"type":"result","is_error":False,
                "subtype":"success","result":"after-429"}}) + "\\n")
            sys.stdout.flush()
        '''))
        script.chmod(0o755)
        _common_setup(tmp, bin_dir)
        adapter = _fresh_adapter()

        workdir = adapter._session_dir("discord", "http-429-1")
        workdir.mkdir(parents=True, exist_ok=True)
        (workdir / ".session_started").touch()

        t0 = time.time()
        ans = adapter.call_claude_streaming(
            "trigger 429", channel="discord", chat_key="http-429-1",
            mode="unrestricted", profile=None,
        )
        elapsed = time.time() - t0

        # Default Retry-After-fallback is 8s. With min_seconds=5 clamp,
        # the test must observe ≥5s elapsed if the backoff actually ran.
        assert "after-429" in ans, f"429 retry didn't recover: {ans!r}"
        assert elapsed >= 4.5, (
            f"expected Retry-After backoff (≥5s) before retry, "
            f"got {elapsed:.1f}s"
        )
        assert elapsed < 25, f"backoff overshoot: {elapsed:.1f}s"
        # Session marker must NOT be wiped: 429 is a pure rate-limit signal,
        # the local conversation state is still intact.
        assert (workdir / ".session_started").exists(), (
            "session marker was wiped on 429 — context loss bug regressed"
        )
        print(f"PASS: 429 → backoff → retry (session preserved) → 'after-429' "
              f"(took {elapsed:.1f}s)")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        _teardown()


def test_500_retry_preserves_session() -> None:
    _section("5xx upstream error triggers retry WITHOUT wiping session")
    tmp = Path(tempfile.mkdtemp(prefix="adapter-http-500-"))
    try:
        bin_dir = tmp / "fake-bin"
        bin_dir.mkdir()
        state_file = tmp / "phase.txt"
        script = bin_dir / "claude"
        script.write_text(textwrap.dedent(f'''\
            #!/usr/bin/env python3
            import json, sys
            state = {str(state_file)!r}
            try:
                phase = int(open(state).read().strip())
            except (FileNotFoundError, ValueError):
                phase = 1
            with open(state, "w") as fh:
                fh.write(str(phase + 1))
            if phase == 1:
                sys.stdout.write('{{"type":"system","subtype":"init"}}\\n')
                sys.stdout.write('{{"type":"result","is_error":true,'
                                 '"subtype":"error","result":"500 internal_server_error"}}\\n')
                sys.stdout.flush()
                sys.exit(0)
            sys.stdout.write('{{"type":"system","subtype":"init"}}\\n')
            sys.stdout.write(json.dumps({{"type":"result","is_error":False,
                "subtype":"success","result":"after-500"}}) + "\\n")
            sys.stdout.flush()
        '''))
        script.chmod(0o755)
        _common_setup(tmp, bin_dir)
        adapter = _fresh_adapter()

        workdir = adapter._session_dir("discord", "http-500-1")
        workdir.mkdir(parents=True, exist_ok=True)
        (workdir / ".session_started").touch()

        ans = adapter.call_claude_streaming(
            "trigger 500", channel="discord", chat_key="http-500-1",
            mode="unrestricted", profile=None,
        )
        assert "after-500" in ans, f"5xx retry didn't recover: {ans!r}"
        assert (workdir / ".session_started").exists(), (
            "session marker was wiped on 5xx — context loss bug regressed"
        )
        print("PASS: 500 → retry (session preserved) → 'after-500'")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        _teardown()


def test_idle_path_still_works() -> None:
    """Regression: the previous reset triggers (stream idle, session)
    must keep firing — we ADDED to the gate, not replaced it.
    """
    _section("stream-idle path still triggers reset (regression)")
    tmp = Path(tempfile.mkdtemp(prefix="adapter-idle-still-"))
    try:
        bin_dir = tmp / "fake-bin"
        bin_dir.mkdir()
        state_file = tmp / "phase.txt"
        script = bin_dir / "claude"
        script.write_text(textwrap.dedent(f'''\
            #!/usr/bin/env python3
            import json, sys
            state = {str(state_file)!r}
            try:
                phase = int(open(state).read().strip())
            except (FileNotFoundError, ValueError):
                phase = 1
            with open(state, "w") as fh:
                fh.write(str(phase + 1))
            if phase == 1:
                # Idle-timeout-shaped error, no HTTP code.
                sys.stdout.write('{{"type":"system","subtype":"init"}}\\n')
                sys.stdout.write('{{"type":"result","is_error":true,'
                                 '"subtype":"error","result":"Stream idle timeout — Claude lieferte 300s lang keine Events"}}\\n')
                sys.stdout.flush()
                sys.exit(0)
            sys.stdout.write('{{"type":"system","subtype":"init"}}\\n')
            sys.stdout.write(json.dumps({{"type":"result","is_error":False,
                "subtype":"success","result":"after-idle"}}) + "\\n")
            sys.stdout.flush()
        '''))
        script.chmod(0o755)
        _common_setup(tmp, bin_dir)
        adapter = _fresh_adapter()

        workdir = adapter._session_dir("discord", "http-idle-1")
        workdir.mkdir(parents=True, exist_ok=True)
        (workdir / ".session_started").touch()

        ans = adapter.call_claude_streaming(
            "trigger idle", channel="discord", chat_key="http-idle-1",
            mode="unrestricted", profile=None,
        )
        assert "after-idle" in ans, (
            f"idle-timeout reset path regressed: {ans!r}"
        )
        print("PASS: idle-timeout reset path unchanged")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        _teardown()


if __name__ == "__main__":
    test_naked_http_400_triggers_reset_and_retry()
    test_500_retry_preserves_session()
    test_idle_path_still_works()
    test_429_retry_preserves_session()
    print("\nALL OK")
