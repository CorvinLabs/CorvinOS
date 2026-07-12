"""Per-subtask E2E for OpenCodeEngine (ADR-0001 third backend).

Layered like test_engines_e2e.py: protocol contract, capability parity
with the other engines, golden-snapshot argv composition, event
normalisation, fake-binary smoke, plus an opt-in live test against the
real opencode CLI talking to a local Ollama daemon.

The live test is gated behind CORVIN_OPENCODE_LIVE=1 because it
requires:

  - `opencode` on PATH (or ~/.opencode/bin/opencode)
  - `ollama serve` reachable on http://localhost:11434
  - at least one ollama model pulled (e.g. `qwen3:1.7b`)
  - `~/.config/opencode/opencode.json` declaring an `ollama` provider

It runs by default in run-all-tests.sh when all three are present;
operators without Ollama see a clear skip line.

Run:

    python3 operator/bridges/shared/agents/test_opencode_cli.py
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import textwrap
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SHARED = HERE.parent
sys.path.insert(0, str(SHARED))

from agents import StreamEvent, WorkerEngine, collect  # noqa: E402
from agents.claude_code import ClaudeCodeEngine  # noqa: E402
from agents.codex_cli import CodexCliEngine  # noqa: E402
from agents.opencode_cli import OpenCodeEngine  # noqa: E402


PROMPT_PINGOK = "Reply with exactly the single word: PINGOK"


def _opencode_in_path() -> bool:
    if shutil.which("opencode"):
        return True
    return (Path.home() / ".opencode" / "bin" / "opencode").exists()


def _ollama_reachable() -> bool:
    import socket
    s = socket.socket()
    s.settimeout(0.5)
    try:
        s.connect(("127.0.0.1", 11434))
        return True
    except OSError:
        return False
    finally:
        try:
            s.close()
        except Exception:
            pass


def _ollama_first_model() -> str | None:
    import json as _json
    import urllib.request
    try:
        with urllib.request.urlopen(
            "http://127.0.0.1:11434/api/tags", timeout=1
        ) as resp:
            data = _json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None
    models = data.get("models") or []
    if not models:
        return None
    # Prefer the smallest by reported size for fast tests.
    models = sorted(models, key=lambda m: m.get("size") or 0)
    return f"ollama/{models[0].get('name')}"


def _opencode_has_ollama_provider() -> bool:
    cfg = Path.home() / ".config" / "opencode" / "opencode.json"
    if not cfg.exists():
        return False
    try:
        data = json.loads(cfg.read_text())
    except Exception:
        return False
    return bool((data.get("provider") or {}).get("ollama"))


LIVE = (
    os.environ.get("CORVIN_OPENCODE_LIVE") == "1"
    and _opencode_in_path()
    and _ollama_reachable()
    and _opencode_has_ollama_provider()
)


# ---------------------------------------------------------------------------
# Protocol + capability parity
# ---------------------------------------------------------------------------


class ProtocolContractTests(unittest.TestCase):

    def test_engine_satisfies_protocol(self) -> None:
        engine = OpenCodeEngine()
        self.assertIsInstance(engine, WorkerEngine)
        self.assertEqual(engine.name, "opencode")

    def test_resolve_binary_env_override(self) -> None:
        original = os.environ.get("OPENCODE_BIN")
        try:
            os.environ["OPENCODE_BIN"] = "/usr/local/bin/opencode-custom"
            engine = OpenCodeEngine()
            self.assertEqual(engine.binary, "/usr/local/bin/opencode-custom")
        finally:
            if original is None:
                os.environ.pop("OPENCODE_BIN", None)
            else:
                os.environ["OPENCODE_BIN"] = original


class CapabilityFlagTests(unittest.TestCase):

    def test_opencode_lacks_claude_specific_features(self) -> None:
        # skills_tool and add_system_prompt remain False — no native Skill tool
        # API and no --append-system-prompt flag.
        # mid_stream_inject is "buffered" (ECI ADR-0069 M2) and hooks is
        # "teb_brokered" (ECI M4) — truthy strings signalling degraded support.
        self.assertFalse(OpenCodeEngine.capabilities["skills_tool"])
        self.assertFalse(OpenCodeEngine.capabilities["add_system_prompt"])
        self.assertEqual(OpenCodeEngine.capabilities["mid_stream_inject"], "buffered")
        self.assertEqual(OpenCodeEngine.capabilities["hooks"], "teb_brokered")

    def test_opencode_supports_mcp_and_stream_json(self) -> None:
        self.assertTrue(OpenCodeEngine.capabilities["mcp"])
        self.assertTrue(OpenCodeEngine.capabilities["stream_json"])

    def test_capability_keys_match_other_engines(self) -> None:
        a = set(ClaudeCodeEngine.capabilities.keys())
        b = set(CodexCliEngine.capabilities.keys())
        c = set(OpenCodeEngine.capabilities.keys())
        # All three engines must declare the same capability schema.
        self.assertEqual(
            a, c,
            f"capability key drift claude<->opencode: claude={a-c} opencode={c-a}",
        )
        self.assertEqual(
            b, c,
            f"capability key drift codex<->opencode: codex={b-c} opencode={c-b}",
        )

    def test_permission_modes_curated(self) -> None:
        modes = OpenCodeEngine.capabilities["permission_modes"]
        self.assertIn("bypassPermissions", modes)
        self.assertIn("default", modes)


# ---------------------------------------------------------------------------
# _build_args golden snapshots
# ---------------------------------------------------------------------------


class BuildArgsTests(unittest.TestCase):

    def test_minimal(self) -> None:
        # Windows fresh-install fix: the message is no longer a trailing
        # argv element (it goes via stdin instead — see spawn()) so the
        # golden argv shape now ends at the last flag, not the prompt text.
        argv = OpenCodeEngine._build_args(
            binary="opencode",
            prompt="hello",
        )
        self.assertEqual(argv, [
            "opencode", "run", "--format", "json",
            "--dangerously-skip-permissions",
        ])

    def test_with_model_and_dir(self) -> None:
        argv = OpenCodeEngine._build_args(
            binary="opencode",
            prompt="say hi",
            model="ollama/qwen3:8b",
            working_dir=Path("/tmp/sandbox"),
        )
        self.assertEqual(argv, [
            "opencode", "run", "--format", "json",
            "--dangerously-skip-permissions",
            "--model", "ollama/qwen3:8b",
            "--dir", "/tmp/sandbox",
        ])

    def test_with_agent_overrides_default(self) -> None:
        argv = OpenCodeEngine._build_args(
            binary="opencode",
            prompt="audit this",
            agent="build",
        )
        self.assertEqual(argv, [
            "opencode", "run", "--format", "json",
            "--dangerously-skip-permissions",
            "--agent", "build",
        ])

    def test_permission_mode_plan_uses_plan_agent(self) -> None:
        argv = OpenCodeEngine._build_args(
            binary="opencode",
            prompt="just look",
            permission_mode="plan",
        )
        # plan-mode opts out of --dangerously-skip-permissions AND
        # the explicit --agent build, using opencode's read-only `plan`
        # agent instead.
        self.assertNotIn("--dangerously-skip-permissions", argv)
        self.assertIn("--agent", argv)
        self.assertEqual(argv[argv.index("--agent") + 1], "plan")

    def test_permission_mode_acceptedits_no_bypass_flag(self) -> None:
        argv = OpenCodeEngine._build_args(
            binary="opencode",
            prompt="x",
            permission_mode="acceptEdits",
        )
        self.assertNotIn("--dangerously-skip-permissions", argv)

    def test_session_continue(self) -> None:
        argv = OpenCodeEngine._build_args(
            binary="opencode",
            prompt="more",
            continue_session=True,
        )
        self.assertIn("-c", argv)

    def test_session_id_with_fork(self) -> None:
        argv = OpenCodeEngine._build_args(
            binary="opencode",
            prompt="branch",
            session_id="ses_abc",
            fork=True,
        )
        # -s comes before --fork; both present.
        self.assertIn("-s", argv)
        self.assertEqual(argv[argv.index("-s") + 1], "ses_abc")
        self.assertIn("--fork", argv)

    def test_session_continue_wins_over_session_id(self) -> None:
        # continue_session and session_id are mutually exclusive; the
        # continue branch wins. Adapter callers must pick one.
        argv = OpenCodeEngine._build_args(
            binary="opencode",
            prompt="x",
            continue_session=True,
            session_id="ses_should_be_ignored",
        )
        self.assertIn("-c", argv)
        self.assertNotIn("-s", argv)

    def test_file_attachments(self) -> None:
        argv = OpenCodeEngine._build_args(
            binary="opencode",
            prompt="review",
            file_attachments=["/tmp/a.py", "/tmp/b.py"],
        )
        # Each -f gets its own flag (multi-attach via repeated flag).
        self.assertEqual(argv.count("-f"), 2)
        self.assertIn("/tmp/a.py", argv)
        self.assertIn("/tmp/b.py", argv)

    def test_extra_args_pass_through(self) -> None:
        argv = OpenCodeEngine._build_args(
            binary="opencode",
            prompt="x",
            extra_args=["--thinking"],
        )
        self.assertIn("--thinking", argv)

    def test_binary_is_first(self) -> None:
        # Argv MUST start with the resolved binary path so subprocess
        # respects PATH-overrides and custom installs.
        argv = OpenCodeEngine._build_args(
            binary="/custom/path/opencode",
            prompt="x",
        )
        self.assertEqual(argv[0], "/custom/path/opencode")

    def test_format_json_is_load_bearing(self) -> None:
        # Without --format json the stream parser would receive ANSI-
        # formatted human-readable output and yield zero events. Regression
        # gate: the flag MUST always appear.
        for kwargs in (
            {},
            {"model": "ollama/qwen3:1.7b"},
            {"continue_session": True},
            {"permission_mode": "plan"},
        ):
            argv = OpenCodeEngine._build_args(
                binary="opencode", prompt="x", **kwargs
            )
            self.assertIn("--format", argv,
                          f"--format missing for kwargs={kwargs}")
            self.assertEqual(argv[argv.index("--format") + 1], "json")


# ---------------------------------------------------------------------------
# Event normalisation
# ---------------------------------------------------------------------------


class NormalisationTests(unittest.TestCase):

    def test_first_step_start_becomes_session_started(self) -> None:
        accum: list[str] = []
        events = OpenCodeEngine._normalise_all(
            {"type": "step_start", "sessionID": "ses_1", "part": {}},
            accumulated_text=accum,
            saw_session_started=False,
        )
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].type, "session_started")

    def test_subsequent_step_start_dropped(self) -> None:
        accum: list[str] = []
        events = OpenCodeEngine._normalise_all(
            {"type": "step_start", "sessionID": "ses_1", "part": {}},
            accumulated_text=accum,
            saw_session_started=True,
        )
        self.assertEqual(events, [])

    def test_text_part_becomes_text_delta(self) -> None:
        accum: list[str] = []
        events = OpenCodeEngine._normalise_all(
            {
                "type": "text",
                "part": {"type": "text", "text": "PINGOK",
                         "time": {"start": 1, "end": 2}},
            },
            accumulated_text=accum,
            saw_session_started=True,
        )
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].type, "text_delta")
        self.assertEqual(events[0].text, "PINGOK")
        self.assertEqual(accum, ["PINGOK"])

    def test_empty_text_part_dropped(self) -> None:
        accum: list[str] = []
        events = OpenCodeEngine._normalise_all(
            {"type": "text", "part": {"type": "text", "text": "   "}},
            accumulated_text=accum,
            saw_session_started=True,
        )
        self.assertEqual(events, [])
        self.assertEqual(accum, [])

    def test_tool_use_becomes_tool_call(self) -> None:
        accum: list[str] = []
        events = OpenCodeEngine._normalise_all(
            {
                "type": "tool_use",
                "part": {"type": "tool", "tool": "read",
                         "state": {"status": "completed"}},
            },
            accumulated_text=accum,
            saw_session_started=True,
        )
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].type, "tool_call")

    def test_error_with_nested_message_extracted(self) -> None:
        accum: list[str] = []
        events = OpenCodeEngine._normalise_all(
            {
                "type": "error",
                "error": {
                    "name": "ProviderError",
                    "data": {"message": "rate limited"},
                },
            },
            accumulated_text=accum,
            saw_session_started=True,
        )
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].type, "error")
        self.assertEqual(events[0].error, "rate limited")

    def test_error_falls_back_to_name(self) -> None:
        accum: list[str] = []
        events = OpenCodeEngine._normalise_all(
            {"type": "error", "error": {"name": "Boom"}},
            accumulated_text=accum,
            saw_session_started=True,
        )
        self.assertEqual(events[0].error, "Boom")

    def test_unknown_event_dropped(self) -> None:
        accum: list[str] = []
        events = OpenCodeEngine._normalise_all(
            {"type": "reasoning", "part": {"text": "internal..."}},
            accumulated_text=accum,
            saw_session_started=True,
        )
        self.assertEqual(events, [])


# ---------------------------------------------------------------------------
# Fake-binary smoke (no opencode required)
# ---------------------------------------------------------------------------


class FakeOpencodeStreamTests(unittest.TestCase):
    """Drive the engine against a fake `opencode` binary that emits a
    canned JSON-event stream. Exercises the full subprocess + stream-
    parser + collect() round-trip without needing the real CLI.
    """

    def _make_fake_binary(self, tmp: Path, events_json: list[dict]) -> Path:
        """Create a shell-script that emits the given events on stdout.

        Shebang must be in bytes 1-2 of the file or the kernel rejects
        with ENOEXEC — keep this a flat string, not a dedented heredoc.
        """
        bin_path = tmp / "opencode"
        emit_lines = "\n".join(
            f"echo '{json.dumps(e)}'" for e in events_json
        )
        script = f"#!/usr/bin/env bash\nset -e\n{emit_lines}\n"
        bin_path.write_text(script)
        bin_path.chmod(0o755)
        return bin_path

    def test_happy_path(self) -> None:
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            fake = self._make_fake_binary(tmp, [
                {"type": "step_start", "sessionID": "ses_1",
                 "part": {"id": "stp_1", "type": "step-start"}},
                {"type": "text", "sessionID": "ses_1",
                 "part": {"id": "prt_1", "type": "text",
                          "text": "PINGOK",
                          "time": {"start": 1, "end": 2}}},
                {"type": "step_finish", "sessionID": "ses_1",
                 "part": {"id": "stp_1"}},
            ])
            engine = OpenCodeEngine(binary=str(fake))
            result = collect(engine.spawn("hi", timeout=5.0))
            self.assertIsNone(result.error)
            self.assertEqual(result.final_text, "PINGOK")
            kinds = [e.type for e in result.events]
            self.assertIn("session_started", kinds)
            self.assertIn("text_delta", kinds)
            self.assertEqual(kinds[-1], "turn_completed")

    def test_error_event_yields_error_terminal(self) -> None:
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            fake = self._make_fake_binary(tmp, [
                {"type": "step_start", "sessionID": "ses_1", "part": {}},
                {"type": "error", "sessionID": "ses_1",
                 "error": {"name": "ProviderError",
                           "data": {"message": "no model configured"}}},
            ])
            engine = OpenCodeEngine(binary=str(fake))
            result = collect(engine.spawn("x", timeout=5.0))
            self.assertEqual(result.error, "no model configured")
            self.assertEqual(result.final_text, "")

    def test_missing_binary_yields_error(self) -> None:
        engine = OpenCodeEngine(binary="/nonexistent/opencode-XYZ")
        result = collect(engine.spawn("x", timeout=2.0))
        self.assertIsNotNone(result.error)
        self.assertIn("not found", (result.error or "").lower())

    def test_prompt_delivered_via_stdin_not_argv(self) -> None:
        """Windows fresh-install fix regression: the message must reach the
        child via stdin, not as a trailing argv element (which blew past
        cmd.exe's ~8191-char buffer on Windows once routed through
        windows_shim_command for the .cmd shim). This fake binary actually
        reads stdin (unlike the canned-output scripts above) and echoes it
        back as the text event, proving delivery end-to-end."""
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            bin_path = tmp / "opencode"
            # Reads whatever spawn() wrote to stdin and echoes it back as
            # the session's text — proves the content actually arrived,
            # not just that SOME process ran.
            script = (
                "#!/usr/bin/env bash\n"
                "set -e\n"
                "msg=$(cat)\n"
                'echo \'{"type":"step_start","sessionID":"ses_1","part":{}}\'\n'
                'python3 -c "import json,sys; print(json.dumps('
                '{\'type\':\'text\',\'sessionID\':\'ses_1\','
                '\'part\':{\'type\':\'text\',\'text\':sys.argv[1],'
                '\'time\':{\'start\':1,\'end\':2}}}))" "$msg"\n'
                'echo \'{"type":"step_finish","sessionID":"ses_1","part":{}}\'\n'
            )
            bin_path.write_text(script)
            bin_path.chmod(0o755)

            engine = OpenCodeEngine(binary=str(bin_path))
            result = collect(engine.spawn(
                "what is the codeword", system="the codeword is XKCD-4242",
                timeout=5.0,
            ))
            self.assertIsNone(result.error)
            self.assertIn("XKCD-4242", result.final_text)
            self.assertIn("what is the codeword", result.final_text)
            # And prove it did NOT also leak into argv (defense in depth —
            # a regression that reintroduces the positional AND keeps the
            # stdin write would still "work" but reopen the Windows bug).
            argv = OpenCodeEngine._build_args(binary=str(bin_path),
                                              prompt="what is the codeword")
            self.assertNotIn("what is the codeword", argv)


# ---------------------------------------------------------------------------
# Live E2E (opt-in via CORVIN_OPENCODE_LIVE=1, gated on Ollama present)
# ---------------------------------------------------------------------------


@unittest.skipUnless(LIVE, "CORVIN_OPENCODE_LIVE=1 and ollama+opencode required")
class OpenCodeLiveE2E(unittest.TestCase):

    def test_pingok_via_local_ollama(self) -> None:
        model = _ollama_first_model()
        self.assertIsNotNone(model, "no ollama model pulled")
        engine = OpenCodeEngine()
        result = collect(engine.spawn(
            PROMPT_PINGOK,
            model=model,
            timeout=180.0,  # local LLM can be slow on CPU
        ))
        self.assertIsNone(
            result.error,
            f"opencode/{model} failed: {result.error}",
        )
        self.assertIn("PINGOK", result.final_text.upper(),
                      f"expected PINGOK in output, got: {result.final_text!r}")


# ---------------------------------------------------------------------------
# Cloud-backed live E2E (opt-in via CORVIN_OPENCODE_LIVE_CLOUD=1 + OLLAMA_API_KEY)
# ---------------------------------------------------------------------------


LIVE_CLOUD = (
    os.environ.get("CORVIN_OPENCODE_LIVE_CLOUD") == "1"
    and bool(os.environ.get("OLLAMA_API_KEY"))
    and _opencode_in_path()
)


@unittest.skipUnless(
    LIVE_CLOUD,
    "CORVIN_OPENCODE_LIVE_CLOUD=1 + OLLAMA_API_KEY + opencode required",
)
class OpenCodeLiveE2ECloud(unittest.TestCase):
    """End-to-end against the Ollama Cloud OpenAI-compatible endpoint.

    Requires `~/.config/opencode/opencode.json` to declare the
    `ollama-cloud` provider with `baseURL: https://ollama.com/v1` and
    `apiKey: "{env:OLLAMA_API_KEY}"`. The qwen3-coder-next model is the
    canonical smoke target — small enough for fast turn-around, large
    enough that a wrong-Bearer-token failure surfaces as a 401 rather
    than a model-output regression.
    """

    DEFAULT_MODEL = "ollama-cloud/qwen3-coder-next"

    def test_pingok_via_ollama_cloud(self) -> None:
        model = os.environ.get(
            "CORVIN_OPENCODE_LIVE_CLOUD_MODEL", self.DEFAULT_MODEL
        )
        engine = OpenCodeEngine()
        result = collect(engine.spawn(
            PROMPT_PINGOK,
            model=model,
            timeout=90.0,
        ))
        self.assertIsNone(
            result.error,
            f"opencode/{model} cloud spawn failed: {result.error}",
        )
        self.assertIn("PINGOK", result.final_text.upper(),
                      f"expected PINGOK in cloud output, got: {result.final_text!r}")
        kinds = [e.type for e in result.events]
        self.assertEqual(kinds[0], "session_started")
        self.assertEqual(kinds[-1], "turn_completed")


if __name__ == "__main__":
    unittest.main()
