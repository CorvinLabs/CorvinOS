"""Per-subtask E2E for the WorkerEngine layer (ADR-0001, Phase 1).

Spawns real `claude` and real `codex` binaries with a deterministic
single-word prompt and asserts:

  - both engines satisfy the WorkerEngine Protocol
  - both yield session_started + text_delta + turn_completed
  - final_text contains the expected single word
  - capability flags differ correctly between engines
  - the normalised event shape is the same regardless of backend

Live tests run by default. Set CORVIN_AGENTS_SKIP_LIVE=1 (or the legacy
CORVIN_AGENTS_SKIP_LIVE=1, deprecated, removed in Phase 7) to skip the
two real-subprocess tests (useful in CI without API quota).

Run:

    python3 operator/bridges/shared/agents/test_engines_e2e.py
"""

from __future__ import annotations

import os
import shutil
import sys
import time
import unittest
from pathlib import Path

# Ensure the package is importable when run as a script
HERE = Path(__file__).resolve().parent
SHARED = HERE.parent
sys.path.insert(0, str(SHARED))

from agents import StreamEvent, WorkerEngine, collect, parse_jsonl_line  # noqa: E402
from agents.claude_code import ClaudeCodeEngine  # noqa: E402
from agents.codex_cli import CodexCliEngine  # noqa: E402


def _resolve_skip_live() -> bool:
    """Phase-1 Corvin rebrand: CORVIN_AGENTS_SKIP_LIVE canonical;
    CORVIN_AGENTS_SKIP_LIVE legacy alias with one-shot deprecation log
    to stderr per process. Either set to '1' enables the skip; both will
    be removed in Phase 7."""
    if os.environ.get("CORVIN_AGENTS_SKIP_LIVE") == "1":
        return True
    if os.environ.get("CORVIN_AGENTS_SKIP_LIVE") == "1":
        try:
            print(
                "[deprecation] CORVIN_AGENTS_SKIP_LIVE is deprecated; "
                "use CORVIN_AGENTS_SKIP_LIVE instead. "
                "Will be removed in Phase 7.",
                file=sys.stderr, flush=True,
            )
        except Exception:
            pass
        return True
    return False


SKIP_LIVE = _resolve_skip_live()
PROMPT_PONG = "Reply with exactly the single word: pong"


def _codex_in_path() -> bool:
    if shutil.which("codex"):
        return True
    nvm = Path.home() / ".nvm/versions/node/v22.22.0/bin/codex"
    return nvm.exists()


# ---------------------------------------------------------------------------
# Fast unit tests (always run)
# ---------------------------------------------------------------------------


class ProtocolContractTests(unittest.TestCase):

    def test_claude_engine_satisfies_protocol(self) -> None:
        engine = ClaudeCodeEngine()
        self.assertIsInstance(engine, WorkerEngine)
        self.assertEqual(engine.name, "claude_code")

    def test_codex_engine_satisfies_protocol(self) -> None:
        engine = CodexCliEngine()
        self.assertIsInstance(engine, WorkerEngine)
        self.assertEqual(engine.name, "codex_cli")


class CapabilityFlagTests(unittest.TestCase):

    def test_claude_has_skills_tool(self) -> None:
        self.assertTrue(ClaudeCodeEngine.capabilities["skills_tool"])
        self.assertTrue(ClaudeCodeEngine.capabilities["mid_stream_inject"])
        self.assertTrue(ClaudeCodeEngine.capabilities["hooks"])

    def test_codex_lacks_skills_tool_and_mid_stream(self) -> None:
        # skills_tool remains False for codex — no first-class Skill tool API.
        # mid_stream_inject is "buffered" (ECI ADR-0069 M2) and hooks is
        # "teb_brokered" (ECI M4) — both are non-False truthy strings to signal
        # the capability exists but in a degraded/brokered form.
        self.assertFalse(CodexCliEngine.capabilities["skills_tool"])
        self.assertEqual(CodexCliEngine.capabilities["mid_stream_inject"], "buffered")
        self.assertEqual(CodexCliEngine.capabilities["hooks"], "teb_brokered")

    def test_both_support_mcp_and_stream_json(self) -> None:
        for engine_cls in (ClaudeCodeEngine, CodexCliEngine):
            with self.subTest(engine=engine_cls.name):
                self.assertTrue(engine_cls.capabilities["mcp"])
                self.assertTrue(engine_cls.capabilities["stream_json"])

    def test_capability_keys_match(self) -> None:
        # Both engines must declare the same set of capability keys so
        # adapter logic can rely on a consistent schema.
        a = set(ClaudeCodeEngine.capabilities.keys())
        b = set(CodexCliEngine.capabilities.keys())
        self.assertEqual(a, b, f"capability key drift: claude={a-b}, codex={b-a}")


class CollectHelperTests(unittest.TestCase):

    def test_collect_with_streaming_text_deltas(self) -> None:
        events = iter([
            StreamEvent(type="session_started"),
            StreamEvent(type="text_delta", text="hel"),
            StreamEvent(type="text_delta", text="lo"),
            StreamEvent(type="turn_completed", text="hello",
                        usage={"input_tokens": 10, "output_tokens": 1}),
        ])
        result = collect(events)
        self.assertEqual(result.final_text, "hello")
        self.assertEqual(result.usage["output_tokens"], 1)
        self.assertEqual(len(result.events), 4)
        self.assertIsNone(result.error)

    def test_collect_with_completion_only_text(self) -> None:
        # Codex pattern: text only on turn_completed
        events = iter([
            StreamEvent(type="session_started"),
            StreamEvent(type="turn_completed", text="pong",
                        usage={"input_tokens": 5}),
        ])
        result = collect(events)
        self.assertEqual(result.final_text, "pong")

    def test_collect_with_error(self) -> None:
        events = iter([
            StreamEvent(type="session_started"),
            StreamEvent(type="error", error="quota exceeded"),
        ])
        result = collect(events)
        self.assertEqual(result.error, "quota exceeded")
        self.assertEqual(result.final_text, "")


class ParseJsonlLineTests(unittest.TestCase):

    def test_valid_jsonl(self) -> None:
        self.assertEqual(parse_jsonl_line(b'{"type":"x"}\n'), {"type": "x"})

    def test_garbage_returns_none(self) -> None:
        self.assertIsNone(parse_jsonl_line(b"not json\n"))
        self.assertIsNone(parse_jsonl_line(b""))
        self.assertIsNone(parse_jsonl_line(b"   "))

    def test_partial_object_returns_none(self) -> None:
        self.assertIsNone(parse_jsonl_line(b'{"type":'))


class NormalisationTests(unittest.TestCase):
    """Pure-function tests of the per-engine event normaliser."""

    def test_claude_init_event_normalises_to_session_started(self) -> None:
        evt = ClaudeCodeEngine._normalise({
            "type": "system", "subtype": "init", "session_id": "abc",
        })
        self.assertIsNotNone(evt)
        assert evt is not None
        self.assertEqual(evt.type, "session_started")

    def test_claude_assistant_text_normalises_to_text_delta(self) -> None:
        evt = ClaudeCodeEngine._normalise({
            "type": "assistant",
            "message": {"content": [{"type": "text", "text": "pong"}]},
        })
        self.assertIsNotNone(evt)
        assert evt is not None
        self.assertEqual(evt.type, "text_delta")
        self.assertEqual(evt.text, "pong")

    def test_claude_result_success_normalises_to_turn_completed(self) -> None:
        evt = ClaudeCodeEngine._normalise({
            "type": "result", "subtype": "success", "is_error": False,
            "result": "pong", "usage": {"input_tokens": 5},
        })
        self.assertIsNotNone(evt)
        assert evt is not None
        self.assertEqual(evt.type, "turn_completed")
        self.assertEqual(evt.text, "pong")
        self.assertEqual(evt.usage, {"input_tokens": 5})

    def test_claude_result_error_normalises_to_error(self) -> None:
        evt = ClaudeCodeEngine._normalise({
            "type": "result", "subtype": "error_during_execution",
            "is_error": True, "api_error_status": "rate_limited",
        })
        self.assertIsNotNone(evt)
        assert evt is not None
        self.assertEqual(evt.type, "error")
        self.assertEqual(evt.error, "rate_limited")

    def test_codex_thread_started_normalises_to_session_started(self) -> None:
        evt = CodexCliEngine._normalise({"type": "thread.started"}, [])
        self.assertIsNotNone(evt)
        assert evt is not None
        self.assertEqual(evt.type, "session_started")

    def test_codex_agent_message_normalises_to_text_delta(self) -> None:
        acc: list[str] = []
        evt = CodexCliEngine._normalise({
            "type": "item.completed",
            "item": {"id": "i_0", "type": "agent_message", "text": "pong"},
        }, acc)
        self.assertIsNotNone(evt)
        assert evt is not None
        self.assertEqual(evt.type, "text_delta")
        self.assertEqual(evt.text, "pong")
        self.assertEqual(acc, ["pong"])

    def test_codex_turn_completed_uses_accumulated_text(self) -> None:
        acc = ["pong"]
        evt = CodexCliEngine._normalise({
            "type": "turn.completed",
            "usage": {"input_tokens": 100, "output_tokens": 1},
        }, acc)
        self.assertIsNotNone(evt)
        assert evt is not None
        self.assertEqual(evt.type, "turn_completed")
        self.assertEqual(evt.text, "pong")
        self.assertEqual(evt.usage["output_tokens"], 1)


# ---------------------------------------------------------------------------
# Live E2E tests (real subprocess)
# ---------------------------------------------------------------------------


class BuildArgsTests(unittest.TestCase):
    """Phase 2.1 — golden-snapshot of `ClaudeCodeEngine._build_args`.

    The engine owns argv composition. These cases pin the byte-shape so
    a refactor can never silently drift from the historical
    `adapter.py::_build_claude_args` output.
    """

    def test_minimal_unrestricted_legacy_shape(self) -> None:
        # Default: no profile fields → --dangerously-skip-permissions
        # and the prompt as positional arg right after `-p`.
        args = ClaudeCodeEngine._build_args("hello")
        self.assertEqual(
            args,
            ["claude", "-p", "hello", "--dangerously-skip-permissions"],
        )

    def test_with_system_prompt(self) -> None:
        args = ClaudeCodeEngine._build_args(
            "hello", system="be terse",
        )
        self.assertEqual(args, [
            "claude", "-p", "hello",
            "--append-system-prompt", "be terse",
            "--dangerously-skip-permissions",
        ])

    def test_permission_mode_plan_with_tools(self) -> None:
        args = ClaudeCodeEngine._build_args(
            "do thing",
            permission_mode="plan",
            allowed_tools=["Read", "Grep"],
            disallowed_tools=["Bash"],
        )
        self.assertIn("--permission-mode", args)
        self.assertEqual(args[args.index("--permission-mode") + 1], "plan")
        self.assertNotIn("--dangerously-skip-permissions", args)
        self.assertIn("--allowedTools", args)
        self.assertEqual(args[args.index("--allowedTools") + 1], "Read Grep")
        self.assertIn("--disallowedTools", args)
        self.assertEqual(args[args.index("--disallowedTools") + 1], "Bash")

    def test_media_mode_read_overrides_profile(self) -> None:
        # mode=read forces --allowedTools Read and skips the profile
        # tool caps + permission-mode fallback.
        args = ClaudeCodeEngine._build_args(
            "do thing", mode="read",
            permission_mode="plan",
            allowed_tools=["Edit"],  # ignored
        )
        self.assertEqual(
            args.count("--allowedTools"), 1,
            f"expected exactly one --allowedTools: {args!r}",
        )
        self.assertEqual(args[args.index("--allowedTools") + 1], "Read")
        self.assertNotIn("--permission-mode", args)
        self.assertNotIn("--dangerously-skip-permissions", args)

    def test_media_mode_restricted(self) -> None:
        args = ClaudeCodeEngine._build_args("x", mode="restricted")
        self.assertIn("--disallowedTools", args)
        self.assertEqual(args[args.index("--disallowedTools") + 1], "*")

    def test_mcp_config_and_add_dirs(self) -> None:
        args = ClaudeCodeEngine._build_args(
            "x",
            mcp_config_path="/tmp/mcp.json",
            add_dirs=["/a", "/b"],
            add_dir="/c",
        )
        self.assertEqual(args[args.index("--mcp-config") + 1], "/tmp/mcp.json")
        idxs = [i for i, a in enumerate(args) if a == "--add-dir"]
        self.assertEqual(len(idxs), 3)
        self.assertEqual(
            [args[i + 1] for i in idxs],
            ["/a", "/b", "/c"],
        )

    def test_continue_session_inserts_continue_before_p(self) -> None:
        args = ClaudeCodeEngine._build_args("x", continue_session=True)
        # Same shape as adapter's slice insertion: claude --continue -p ...
        self.assertEqual(args[0], "claude")
        self.assertEqual(args[1], "--continue")
        self.assertEqual(args[2], "-p")

    def test_streaming_with_stdin_prompt(self) -> None:
        args = ClaudeCodeEngine._build_args(
            "ignored",
            prompt_via_stdin=True,
            streaming=True,
        )
        # No positional prompt arg
        self.assertNotIn("ignored", args)
        # --input-format stream-json appears (because stdin path)
        self.assertIn("--input-format", args)
        self.assertEqual(args[args.index("--input-format") + 1], "stream-json")
        self.assertIn("--output-format", args)
        self.assertEqual(args[args.index("--output-format") + 1], "stream-json")
        self.assertIn("--verbose", args)

    def test_streaming_without_stdin_no_input_format(self) -> None:
        # Non-stdin path: --output-format added, --input-format NOT added.
        args = ClaudeCodeEngine._build_args(
            "x", prompt_via_stdin=False, streaming=True,
        )
        self.assertNotIn("--input-format", args)
        self.assertIn("--output-format", args)

    def test_model_flag(self) -> None:
        args = ClaudeCodeEngine._build_args("x", model="claude-haiku-4-5")
        self.assertEqual(args[args.index("--model") + 1], "claude-haiku-4-5")

    def test_extra_args_appended_after_flags_before_streaming(self) -> None:
        args = ClaudeCodeEngine._build_args(
            "x", extra_args=["--debug"], streaming=False,
        )
        # extra_args appears, but no streaming suffix
        self.assertIn("--debug", args)
        self.assertNotIn("--output-format", args)

    def test_dangerously_skip_explicit_false_keeps_permission_mode(self) -> None:
        # Caller explicitly says: don't add --dangerously-skip even
        # though permission_mode is bypassPermissions.
        args = ClaudeCodeEngine._build_args(
            "x",
            permission_mode="bypassPermissions",
            dangerously_skip_permissions=False,
        )
        self.assertNotIn("--dangerously-skip-permissions", args)
        self.assertIn("--permission-mode", args)
        self.assertEqual(
            args[args.index("--permission-mode") + 1], "bypassPermissions",
        )


class FakeClaudeStreamTests(unittest.TestCase):
    """Phase 2.1 — `ADAPTER_FAKE_CLAUDE=1` short-circuits the spawn and
    emits a synthetic event sequence so the existing test-fixture path
    keeps working when the adapter delegates to engine.spawn().
    """

    def setUp(self) -> None:
        self._saved = {}
        for k in ("ADAPTER_FAKE_CLAUDE", "ADAPTER_FAKE_DELAY",
                  "ADAPTER_FAKE_ARGS_DUMP"):
            self._saved[k] = os.environ.get(k)
        os.environ["ADAPTER_FAKE_CLAUDE"] = "1"
        os.environ["ADAPTER_FAKE_DELAY"] = "0.01"

    def tearDown(self) -> None:
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_fake_stream_emits_session_and_completion(self) -> None:
        engine = ClaudeCodeEngine()
        result = collect(engine.spawn(
            "ping", channel="ut", chat_key="x1", streaming=True,
        ))
        self.assertIsNone(result.error)
        self.assertIn("ping", result.final_text)
        types = [e.type for e in result.events]
        self.assertEqual(types[0], "session_started")
        self.assertEqual(types[-1], "turn_completed")

    def test_fake_stream_dumps_args(self) -> None:
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            dump = Path(td) / "args.jsonl"
            os.environ["ADAPTER_FAKE_ARGS_DUMP"] = str(dump)
            engine = ClaudeCodeEngine()
            # NOTE: spawn() now (Windows cmd.exe 8191-char fix) always writes
            # a non-empty `system` prompt to a temp file and appends
            # `--append-system-prompt-file <path>` instead of the inline
            # `--append-system-prompt <text>` form — see claude_code.py's
            # spawn() docstring. That temp file is unlinked in a `finally`
            # the instant this generator is fully drained (spawn()'s
            # `_cleanup_system_prompt_tmp_file`), which is exactly what
            # `collect()`'s draining `for` loop triggers. So the content
            # must be captured DURING iteration, before the generator is
            # exhausted — reading it after `collect()` returns would hit a
            # FileNotFoundError (the same file collect() would drain past).
            events: list[StreamEvent] = []
            sys_prompt_content: str | None = None
            for ev in engine.spawn(
                "hello world",
                system="terse",
                channel="ch", chat_key="ck",
                permission_mode="plan",
                allowed_tools=["Read"],
                streaming=True,
                prompt_via_stdin=True,
            ):
                events.append(ev)
                if sys_prompt_content is None and engine._system_prompt_tmp_path:
                    sys_prompt_content = Path(
                        engine._system_prompt_tmp_path
                    ).read_text(encoding="utf-8")
                if ev.type in ("turn_completed", "error"):
                    break
            error = next((e.error for e in events if e.type == "error"), None)
            self.assertIsNone(error)
            with open(dump) as fh:
                lines = [ln for ln in fh.read().splitlines() if ln.strip()]
            self.assertEqual(len(lines), 1)
            import json as _json
            payload = _json.loads(lines[0])
            self.assertEqual(payload["channel"], "ch")
            self.assertEqual(payload["chat_key"], "ck")
            self.assertEqual(payload["engine"], "claude_code")
            args = payload["args"]
            if "--append-system-prompt-file" in args:
                self.assertIsNotNone(
                    sys_prompt_content,
                    "system-prompt temp file was already cleaned up before "
                    "its content could be captured mid-stream",
                )
                self.assertEqual(sys_prompt_content, "terse")
            else:
                self.assertIn("--append-system-prompt", args)
                self.assertEqual(
                    args[args.index("--append-system-prompt") + 1], "terse",
                )
            self.assertIn("--permission-mode", args)
            self.assertEqual(args[args.index("--permission-mode") + 1], "plan")
            self.assertIn("--input-format", args)
            self.assertEqual(args[args.index("--input-format") + 1], "stream-json")
            self.assertIn("--output-format", args)
            self.assertIn("--verbose", args)


@unittest.skipIf(SKIP_LIVE, "CORVIN_AGENTS_SKIP_LIVE=1 (or legacy CORVIN_AGENTS_SKIP_LIVE=1) — live tests skipped")
class ClaudeCodeLiveE2E(unittest.TestCase):

    @unittest.skipUnless(shutil.which("claude"), "claude binary not in PATH")
    def test_claude_pong(self) -> None:
        engine = ClaudeCodeEngine()
        start = time.time()
        result = collect(engine.spawn(PROMPT_PONG, timeout=180.0))
        duration = time.time() - start

        self.assertIsNone(result.error,
                          f"claude engine returned error: {result.error}")
        self.assertIn("pong", result.final_text.lower(),
                      f"expected 'pong' in {result.final_text!r}")
        self.assertGreater(len(result.events), 0)
        # Pflicht-Events
        types = [e.type for e in result.events]
        self.assertIn("session_started", types)
        self.assertIn("turn_completed", types)
        # Usage shape
        self.assertIn("input_tokens", result.usage,
                      f"missing input_tokens in {result.usage!r}")
        print(f"  [claude] {duration:.1f}s  text={result.final_text!r}  "
              f"usage={result.usage}")


@unittest.skipIf(SKIP_LIVE, "CORVIN_AGENTS_SKIP_LIVE=1 (or legacy CORVIN_AGENTS_SKIP_LIVE=1) — live tests skipped")
class CodexCliLiveE2E(unittest.TestCase):

    @unittest.skipUnless(_codex_in_path(), "codex binary not found")
    def test_codex_pong(self) -> None:
        engine = CodexCliEngine()
        start = time.time()
        result = collect(engine.spawn(PROMPT_PONG, timeout=180.0))
        duration = time.time() - start

        self.assertIsNone(result.error,
                          f"codex engine returned error: {result.error}")
        self.assertIn("pong", result.final_text.lower(),
                      f"expected 'pong' in {result.final_text!r}")
        self.assertGreater(len(result.events), 0)
        types = [e.type for e in result.events]
        self.assertIn("session_started", types)
        self.assertIn("turn_completed", types)
        self.assertIn("input_tokens", result.usage,
                      f"missing input_tokens in {result.usage!r}")
        print(f"  [codex]  {duration:.1f}s  text={result.final_text!r}  "
              f"usage={result.usage}")


@unittest.skipIf(SKIP_LIVE, "CORVIN_AGENTS_SKIP_LIVE=1 (or legacy CORVIN_AGENTS_SKIP_LIVE=1) — live tests skipped")
class EngineParityLiveE2E(unittest.TestCase):
    """The load-bearing test: same prompt → same normalized event shape."""

    @unittest.skipUnless(shutil.which("claude") and _codex_in_path(),
                         "need both claude and codex binaries")
    def test_both_engines_return_same_event_types(self) -> None:
        prompt = "Reply with exactly: ack"
        claude_result = collect(ClaudeCodeEngine().spawn(prompt, timeout=180.0))
        codex_result = collect(CodexCliEngine().spawn(prompt, timeout=180.0))

        # Both must succeed
        self.assertIsNone(claude_result.error,
                          f"claude error: {claude_result.error}")
        self.assertIsNone(codex_result.error,
                          f"codex error: {codex_result.error}")

        # Both must have produced the same set of normalised event types
        # for the success path (session_started + turn_completed at least).
        claude_types = {e.type for e in claude_result.events
                        if e.type in ("session_started", "turn_completed",
                                      "text_delta")}
        codex_types = {e.type for e in codex_result.events
                       if e.type in ("session_started", "turn_completed",
                                     "text_delta")}
        # Both must contain at least session_started + turn_completed
        for must in ("session_started", "turn_completed"):
            self.assertIn(must, claude_types)
            self.assertIn(must, codex_types)

        # Both must have produced "ack"-containing final text
        self.assertIn("ack", claude_result.final_text.lower())
        self.assertIn("ack", codex_result.final_text.lower())

        print(f"  [parity] claude_types={claude_types}, "
              f"codex_types={codex_types}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
