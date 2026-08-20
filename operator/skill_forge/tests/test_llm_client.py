"""Tests for the Claude-Code-backed LLM client (ADR-0363).

Covers the contract the Skill-Creator phases depend on: an Anthropic-SDK
shaped `messages.create` that never needs ANTHROPIC_API_KEY, and an
envelope parser that refuses to pass a CLI error off as model output.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_OPERATOR_DIR = Path(__file__).resolve().parents[2]
if str(_OPERATOR_DIR) not in sys.path:
    sys.path.insert(0, str(_OPERATOR_DIR))

from skill_forge.llm_client import (  # noqa: E402
    ClaudeCodeClient,
    ClaudeCodeUnavailable,
    engine_id_of,
    resolve_claude_bin,
    resolve_llm_client,
)

SUCCESS_ENVELOPE = (
    '{"type":"result","subtype":"success","is_error":false,'
    '"result":"PONG","usage":{"input_tokens":2,"output_tokens":5},'
    '"modelUsage":{"claude-opus-5":{"inputTokens":2}}}'
)


def _client() -> ClaudeCodeClient:
    with patch("skill_forge.llm_client.resolve_claude_bin", return_value="/bin/true"):
        return ClaudeCodeClient()


class TestEnvelopeParsing:
    def test_success_envelope_yields_sdk_shape(self):
        resp = ClaudeCodeClient._parse_envelope(SUCCESS_ENVELOPE, fallback_model="m")
        assert resp.content[0].text == "PONG"
        assert resp.model == "claude-opus-5"
        assert resp.usage["output_tokens"] == 5

    def test_error_envelope_raises_instead_of_returning_error_text(self):
        """rc=0 + is_error=true is a FAILED call, not a model answer.

        The CLI reports soft failures (max-turns, upstream API error) inside
        a zero-exit envelope. Returning `result` there would feed the error
        text into the next phase as if it were a skill spec.
        """
        envelope = ('{"type":"result","subtype":"error_max_turns","is_error":true,'
                    '"result":"hit the turn limit"}')
        with pytest.raises(ClaudeCodeUnavailable, match="error_max_turns"):
            ClaudeCodeClient._parse_envelope(envelope, fallback_model="m")

    def test_plain_text_reply_falls_back_to_raw_stdout(self):
        resp = ClaudeCodeClient._parse_envelope("just text", fallback_model="m")
        assert resp.content[0].text == "just text"

    def test_empty_output_raises(self):
        with pytest.raises(ClaudeCodeUnavailable, match="empty output"):
            ClaudeCodeClient._parse_envelope("   ", fallback_model="m")


class TestMessagesCreate:
    def test_create_returns_text_without_api_key(self, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        client = _client()

        completed = MagicMock(returncode=0, stdout=SUCCESS_ENVELOPE, stderr="")
        with patch("skill_forge.llm_client.subprocess.run", return_value=completed) as run:
            resp = client.messages.create(
                model="claude-opus-5", max_tokens=50,
                messages=[{"role": "user", "content": "ping"}],
            )

        assert resp.content[0].text == "PONG"
        argv = run.call_args[0][0]
        assert argv[1] == "-p" and argv[2] == "ping"
        # Tool-free, single-turn, JSON envelope — the safety envelope the
        # Skill-Creator relies on.
        assert "--max-turns" in argv and "--disallowedTools" in argv
        assert argv[argv.index("--output-format") + 1] == "json"

    def test_nonzero_exit_raises_with_stderr_tail(self):
        client = _client()
        completed = MagicMock(returncode=1, stdout="", stderr="boom")
        with patch("skill_forge.llm_client.subprocess.run", return_value=completed):
            with pytest.raises(ClaudeCodeUnavailable, match="boom"):
                client.messages.create(messages=[{"role": "user", "content": "x"}])

    def test_empty_prompt_rejected(self):
        client = _client()
        with pytest.raises(ValueError):
            client.messages.create(messages=[{"role": "user", "content": "  "}])

    def test_content_blocks_are_flattened(self):
        client = _client()
        completed = MagicMock(returncode=0, stdout=SUCCESS_ENVELOPE, stderr="")
        with patch("skill_forge.llm_client.subprocess.run", return_value=completed) as run:
            client.messages.create(messages=[{
                "role": "user",
                "content": [{"type": "text", "text": "block one"},
                            {"type": "text", "text": "block two"}],
            }])
        assert "block one\nblock two" in run.call_args[0][0][2]


class TestResolution:
    def test_explicit_client_wins(self):
        sentinel = object()
        assert resolve_llm_client(sentinel) is sentinel

    def test_default_prefers_claude_code_over_api_key(self, monkeypatch):
        """A Max-subscription install must NOT be pushed onto an API key."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-should-not-be-used")
        monkeypatch.delenv("CORVIN_SKILL_CREATOR_ENGINE", raising=False)
        with patch("skill_forge.llm_client.resolve_claude_bin", return_value="/bin/true"):
            client = resolve_llm_client()
        assert engine_id_of(client) == "claude_code"

    def test_local_engine_is_explicit_opt_in(self, monkeypatch):
        monkeypatch.setenv("CORVIN_SKILL_CREATOR_ENGINE", "local")
        assert resolve_llm_client() is None

    def test_degrades_to_none_when_cli_missing_and_no_key(self, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("CORVIN_SKILL_CREATOR_ENGINE", raising=False)
        with patch("skill_forge.llm_client.resolve_claude_bin",
                   side_effect=ClaudeCodeUnavailable("not found")):
            assert resolve_llm_client() is None

    def test_bin_env_override_is_honoured(self, monkeypatch):
        monkeypatch.setenv("CORVIN_CLAUDE_BIN", "/bin/true")
        assert resolve_claude_bin() == "/bin/true"

    def test_missing_binary_raises_actionable_error(self, monkeypatch):
        monkeypatch.setenv("CORVIN_CLAUDE_BIN", "/nonexistent/claude-xyz")
        with patch("skill_forge.llm_client.shutil.which", return_value=None), \
             patch("skill_forge.llm_client.os.access", return_value=False):
            with pytest.raises(ClaudeCodeUnavailable, match="CORVIN_CLAUDE_BIN"):
                resolve_claude_bin()
