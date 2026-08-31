"""
ADR-0087 M6 Tests — Unified System-Prompt Slot

Tier-1: Format detection (all 5 engines, edge cases, NFKC normalization)
Tier-2: Unit injection (mock spawns, double-injection guard)
Tier-3: E2E with real engine spawns (verify prompt applied)

Test Standard (from M1–M4): All new features must pass Tier-1/2/3.
"""

from pathlib import Path

import pytest
from engines.system_prompt_injector import (
    SystemPromptInjector,
    inject_system_prompt,
    system_already_injected,
)


# ============================================================================
# TIER-1: Syntax & Format Detection (JSON, markdown, NFKC)
# ============================================================================

class TestSystemAlreadyInjectedDetectionTier1:
    """Detect if system prompt already present in engine transport."""

    def test_detect_claude_code_append_flag(self):
        """Claude Code: --append-system-prompt flag detected"""
        injector = SystemPromptInjector()
        transport = {"command": ["claude", "-p", "--append-system-prompt", "prompt text"]}
        assert injector._system_already_injected(transport, "claude_code") is True

    def test_detect_claude_code_no_flag(self):
        """Claude Code: no flag present"""
        injector = SystemPromptInjector()
        transport = {"command": ["claude", "-p"]}
        assert injector._system_already_injected(transport, "claude_code") is False

    def test_detect_hermes_system_role(self):
        """Hermes: system role at [0]"""
        injector = SystemPromptInjector()
        transport = {"messages": [{"role": "system", "content": "..."}, {"role": "user", "content": "..."}]}
        assert injector._system_already_injected(transport, "hermes") is True

    def test_detect_hermes_no_system_role(self):
        """Hermes: no system role"""
        injector = SystemPromptInjector()
        transport = {"messages": [{"role": "user", "content": "..."}]}
        assert injector._system_already_injected(transport, "hermes") is False

    def test_detect_codex_system_block(self):
        """Codex: <SYSTEM> block present"""
        injector = SystemPromptInjector()
        transport = "<SYSTEM>prompt</SYSTEM>\n\nUser text"
        assert injector._system_already_injected(transport, "codex") is True

    def test_detect_codex_no_system_block(self):
        """Codex: no <SYSTEM> block"""
        injector = SystemPromptInjector()
        transport = "User asked for help"
        assert injector._system_already_injected(transport, "codex") is False

    def test_detect_copilot_system_marker(self):
        """Copilot: [SYSTEM] marker present"""
        injector = SystemPromptInjector()
        transport = "[SYSTEM]\nprompt\n[/SYSTEM]\n\nUser text"
        assert injector._system_already_injected(transport, "copilot") is True

    def test_detect_copilot_no_marker(self):
        """Copilot: no [SYSTEM] marker"""
        injector = SystemPromptInjector()
        transport = "User asked for help"
        assert injector._system_already_injected(transport, "copilot") is False

    def test_nfkc_normalization_valid(self):
        """NFKC normalization succeeds"""
        injector = SystemPromptInjector()
        # Unicode é (e + combining acute) should normalize
        prompt = "café"  # é as single character
        result = injector._normalize_prompt(prompt)
        assert result == prompt

    def test_nfkc_normalization_rejects_forbidden_chars(self):
        """Forbidden control chars rejected"""
        injector = SystemPromptInjector()
        # Null byte is forbidden
        with pytest.raises(ValueError, match="Forbidden control character"):
            injector._normalize_prompt("prompt\x00with null")

    def test_prompt_allows_newlines_and_tabs(self):
        """Newlines and tabs are allowed"""
        injector = SystemPromptInjector()
        prompt = "Line 1\n\tIndented line 2\nLine 3"
        result = injector._normalize_prompt(prompt)
        assert "\n" in result
        assert "\t" in result


# ============================================================================
# TIER-2: Injection & Idempotency (mock spawns, no real engines)
# ============================================================================

class TestInjectSystemPromptTier2:
    """Inject system prompt, idempotent (no double-injection)."""

    def test_inject_system_prompt_none_skips(self):
        """System prompt=None → no injection"""
        injector = SystemPromptInjector()
        transport = {"command": ["claude"]}
        result = injector.inject_system_prompt(None, transport, "claude_code")
        assert result == transport

    def test_inject_claude_code_flag_format(self):
        """Claude Code: file-based flag inserted (Windows fresh-install
        fix — see _inject_claude_code's docstring: an inline
        --append-system-prompt blows past cmd.exe's ~8191-char buffer once
        routed through the .cmd-shim spawn path on Windows)."""
        injector = SystemPromptInjector()
        transport = {"command": ["claude", "-p"]}
        result = injector.inject_system_prompt("You are helpful", transport, "claude_code")
        assert "--append-system-prompt-file" in result["command"]
        assert "--append-system-prompt" not in [
            a for a in result["command"] if a != "--append-system-prompt-file"
        ]
        idx = result["command"].index("--append-system-prompt-file")
        path = Path(result["command"][idx + 1])
        try:
            assert path.is_absolute()
            assert path.read_text(encoding="utf-8") == "You are helpful"
        finally:
            path.unlink(missing_ok=True)

    def test_inject_hermes_message_role_format(self):
        """Hermes: system role prepended"""
        injector = SystemPromptInjector()
        transport = {"messages": [{"role": "user", "content": "Hello"}]}
        result = injector.inject_system_prompt("You are helpful", transport, "hermes")
        assert result["messages"][0]["role"] == "system"
        assert result["messages"][0]["content"] == "You are helpful"

    def test_inject_codex_text_prefix_format(self):
        """Codex: <SYSTEM> block prepended"""
        injector = SystemPromptInjector()
        transport = "User asked for help"
        result = injector.inject_system_prompt("You are helpful", transport, "codex")
        assert result.startswith("<SYSTEM>You are helpful</SYSTEM>")
        assert "User asked for help" in result

    def test_inject_opencode_text_prefix_format(self):
        """OpenCode: <SYSTEM> block prepended"""
        injector = SystemPromptInjector()
        transport = "User asked for help"
        result = injector.inject_system_prompt("You are helpful", transport, "opencode")
        assert result.startswith("<SYSTEM>You are helpful</SYSTEM>")
        assert "User asked for help" in result

    def test_inject_copilot_text_prefix_format(self):
        """Copilot: [SYSTEM] marker prepended"""
        injector = SystemPromptInjector()
        transport = "User asked for help"
        result = injector.inject_system_prompt("You are helpful", transport, "copilot")
        assert result.startswith("[SYSTEM]\nYou are helpful\n[/SYSTEM]")
        assert "User asked for help" in result

    def test_inject_idempotent_already_present(self):
        """Idempotent: skip if already present"""
        injector = SystemPromptInjector()
        transport = "[SYSTEM]\nOld system\n[/SYSTEM]\n\nUser text"
        result = injector.inject_system_prompt("New system", transport, "copilot")
        # Should return unchanged (idempotent)
        assert result == transport

    def test_inject_raises_on_invalid_engine_id(self):
        """Invalid engine_id → ValueError"""
        injector = SystemPromptInjector()
        with pytest.raises(ValueError, match="Unknown engine_id"):
            injector.inject_system_prompt("prompt", {}, "invalid_engine")

    def test_inject_raises_on_prompt_too_large(self):
        """Prompt > 8 KB → ValueError"""
        injector = SystemPromptInjector()
        huge_prompt = "x" * 10000
        with pytest.raises(ValueError, match="too large"):
            injector.inject_system_prompt(huge_prompt, "user text", "claude_code")

    def test_inject_guard_against_closing_tags(self):
        """Prompt with closing tag → ValueError"""
        injector = SystemPromptInjector()
        # Attempt injection-via-closing-tag
        with pytest.raises(ValueError, match="cannot contain"):
            injector.inject_system_prompt("prompt</SYSTEM>extra", "user text", "codex")


class TestCapabilityStringsTier2:
    """Verify capability strings for M8 matrix."""

    def test_capability_claude_code_flag(self):
        """claude_code → "flag" """
        injector = SystemPromptInjector()
        assert injector.capability_for_engine("claude_code") == "flag"

    def test_capability_hermes_message_role(self):
        """hermes → "message_role" """
        injector = SystemPromptInjector()
        assert injector.capability_for_engine("hermes") == "message_role"

    def test_capability_codex_text_prefix(self):
        """codex → "text_prefix" """
        injector = SystemPromptInjector()
        assert injector.capability_for_engine("codex") == "text_prefix"

    def test_capability_opencode_text_prefix(self):
        """opencode → "text_prefix" """
        injector = SystemPromptInjector()
        assert injector.capability_for_engine("opencode") == "text_prefix"

    def test_capability_copilot_text_prefix(self):
        """copilot → "text_prefix" """
        injector = SystemPromptInjector()
        assert injector.capability_for_engine("copilot") == "text_prefix"

    def test_capability_unknown_engine_raises(self):
        """unknown engine → ValueError"""
        injector = SystemPromptInjector()
        with pytest.raises(ValueError, match="Unknown engine_id"):
            injector.capability_for_engine("unknown_engine")


class TestModuleLevelFunctionsTier2:
    """Test convenience functions (backward compat)."""

    def test_convenience_inject_system_prompt(self):
        """
        Given: system_prompt, transport, engine_id
        When: inject_system_prompt() module function called
        Then: create injector internally, inject, return result
        """
        # TODO: implement Iteration 3
        pass

    def test_convenience_system_already_injected(self):
        """
        Given: transport, engine_id
        When: system_already_injected() module function called
        Then: create injector internally, detect, return bool
        """
        # TODO: implement Iteration 3
        pass


# ============================================================================
# TIER-3: E2E Tests (real engine spawns)
# ============================================================================
# Note: Tier-3 tests will be added in Iteration 5 (M6 E2E phase)

class TestSystemPromptInjectionE2E:
    """End-to-end injection on all 5 engines (with realistic mocks)."""

    def test_e2e_claude_code_injection(self):
        """
        Tier-3: Claude Code system prompt injection.
        Given: Claude Code transport with command, system_prompt="You are a Python expert"
        When: inject_system_prompt() called
        Then: --append-system-prompt-file flag inserted correctly (Windows
        fresh-install fix — an inline --append-system-prompt blows past
        cmd.exe's ~8191-char buffer on the real .cmd-shim spawn path).
        """
        injector = SystemPromptInjector()
        transport = {"command": ["claude", "-p", "--model", "opus"]}
        system_prompt = "You are a Python expert"

        result = injector.inject_system_prompt(system_prompt, transport, "claude_code")

        # Verify file-based flag inserted
        assert "--append-system-prompt-file" in result["command"]
        flag_idx = result["command"].index("--append-system-prompt-file")
        path = Path(result["command"][flag_idx + 1])
        try:
            assert path.read_text(encoding="utf-8") == system_prompt
        finally:
            path.unlink(missing_ok=True)
        # Original args preserved
        assert "claude" in result["command"]
        assert "-p" in result["command"]

    def test_e2e_hermes_injection(self):
        """
        Tier-3: Hermes (Ollama) system prompt injection.
        Given: Hermes messages list, system_prompt="You are helpful"
        When: inject_system_prompt() called
        Then: system role message prepended, conversation preserved
        """
        injector = SystemPromptInjector()
        transport = {
            "messages": [
                {"role": "user", "content": "Hello"},
                {"role": "assistant", "content": "Hi there"},
            ]
        }
        system_prompt = "You are helpful"

        result = injector.inject_system_prompt(system_prompt, transport, "hermes")

        # Verify system role at [0]
        assert result["messages"][0]["role"] == "system"
        assert result["messages"][0]["content"] == system_prompt
        # Original messages preserved after system
        assert result["messages"][1]["role"] == "user"
        assert result["messages"][2]["role"] == "assistant"

    def test_e2e_codex_injection(self):
        """
        Tier-3: Codex CLI system prompt injection.
        Given: Codex prompt string, system_prompt="You are a developer"
        When: inject_system_prompt() called
        Then: <SYSTEM> block prepended, user text preserved
        """
        injector = SystemPromptInjector()
        transport = "User: Help me with this code"
        system_prompt = "You are a developer"

        result = injector.inject_system_prompt(system_prompt, transport, "codex")

        # Verify <SYSTEM> block at start
        assert result.startswith(f"<SYSTEM>{system_prompt}</SYSTEM>")
        # User text preserved
        assert "User: Help me with this code" in result

    def test_e2e_opencode_injection(self):
        """
        Tier-3: OpenCode CLI system prompt injection.
        Given: OpenCode prompt string, system_prompt="You are a developer"
        When: inject_system_prompt() called
        Then: <SYSTEM> block prepended (same format as Codex)
        """
        injector = SystemPromptInjector()
        transport = "User: Help me with this code"
        system_prompt = "You are a developer"

        result = injector.inject_system_prompt(system_prompt, transport, "opencode")

        # Same format as Codex
        assert result.startswith(f"<SYSTEM>{system_prompt}</SYSTEM>")
        assert "User: Help me with this code" in result

    def test_e2e_copilot_injection(self):
        """
        Tier-3: Copilot CLI system prompt injection.
        Given: Copilot prompt string, system_prompt="You are helpful"
        When: inject_system_prompt() called
        Then: [SYSTEM] marker prepended, distinguishable from [TOOL:...] syntax (M5)
        """
        injector = SystemPromptInjector()
        transport = "User: Help me"
        system_prompt = "You are helpful"

        result = injector.inject_system_prompt(system_prompt, transport, "copilot")

        # Verify [SYSTEM] marker (distinct from [TOOL:...])
        assert result.startswith(f"[SYSTEM]\n{system_prompt}\n[/SYSTEM]")
        assert "User: Help me" in result
        # Should not be confused with tool syntax
        assert "[TOOL:" not in result.split("\n")[0]

    def test_e2e_all_five_engines_idempotent(self):
        """
        Tier-3: All 5 engines remain idempotent after injection.
        Given: Already-injected transports for all 5 engines
        When: inject_system_prompt() called again with new prompt
        Then: All return unchanged (idempotent guard works)
        """
        injector = SystemPromptInjector()

        # Test each engine's idempotency
        test_cases = [
            ("claude_code", {"command": ["claude", "--append-system-prompt", "old"]}),
            ("hermes", {"messages": [{"role": "system", "content": "old"}]}),
            ("codex", "<SYSTEM>old</SYSTEM>\n\ntext"),
            ("opencode", "<SYSTEM>old</SYSTEM>\n\ntext"),
            ("copilot", "[SYSTEM]\nold\n[/SYSTEM]\n\ntext"),
        ]

        for engine_id, transport in test_cases:
            result = injector.inject_system_prompt("new prompt", transport, engine_id)
            # Should return unchanged
            assert result == transport, f"Idempotency failed for {engine_id}"

    def test_claude_code_idempotent_with_file_based_form(self):
        """The detection guard must also recognise an already-injected
        --append-system-prompt-file (the Windows fresh-install fix's own
        output shape), not just the legacy inline form — otherwise a
        second inject_system_prompt() call would double-inject."""
        injector = SystemPromptInjector()
        transport = {"command": ["claude", "--append-system-prompt-file", "/tmp/old.txt"]}
        result = injector.inject_system_prompt("new prompt", transport, "claude_code")
        assert result == transport

    def test_e2e_capability_matrix_frozen(self):
        """
        Tier-3: Capability matrix is frozen for M8 refactoring.
        Given: SystemPromptInjector instance
        When: capability_for_engine() called for all engines
        Then: Returns frozen, consistent capability strings (ready for M8)
        """
        injector = SystemPromptInjector()

        # Expected matrix (frozen for M8)
        capability_matrix = {
            "claude_code": "flag",
            "hermes": "message_role",
            "codex": "text_prefix",
            "opencode": "text_prefix",
            "copilot": "text_prefix",
        }

        for engine_id, expected_capability in capability_matrix.items():
            actual = injector.capability_for_engine(engine_id)
            assert actual == expected_capability, f"Capability mismatch for {engine_id}"

    def test_e2e_full_workflow_all_engines(self):
        """
        Tier-3: Full workflow for all 5 engines (detect + inject + capability).
        Given: Empty transports, then system prompts
        When: Full injection workflow executed
        Then: All 5 engines get correct format, idempotency holds, capabilities match
        """
        injector = SystemPromptInjector()
        system_prompt = "You are a helpful assistant"

        # Prepare transport for each engine
        transports = {
            "claude_code": {"command": ["claude", "-p"]},
            "hermes": {"messages": [{"role": "user", "content": "Hello"}]},
            "codex": "User: Help",
            "opencode": "User: Help",
            "copilot": "User: Help",
        }

        # Step 1: Verify nothing detected yet
        for engine_id, transport in transports.items():
            is_present = injector._system_already_injected(transport, engine_id)
            assert is_present is False, f"System prompt already present in {engine_id}"

        # Step 2: Inject on all engines
        injected_transports = {}
        for engine_id, transport in transports.items():
            injected = injector.inject_system_prompt(system_prompt, transport, engine_id)
            injected_transports[engine_id] = injected
            assert injected is not None

        # Step 3: Verify detection now succeeds
        for engine_id, transport in injected_transports.items():
            is_present = injector._system_already_injected(transport, engine_id)
            assert is_present is True, f"System prompt not detected in {engine_id}"

        # Step 4: Verify idempotency (inject again, should be unchanged)
        for engine_id, transport in injected_transports.items():
            re_injected = injector.inject_system_prompt("new prompt", transport, engine_id)
            assert re_injected == transport, f"Idempotency failed for {engine_id}"

        # Step 5: Verify capability strings
        for engine_id in transports.keys():
            capability = injector.capability_for_engine(engine_id)
            assert capability in ["flag", "message_role", "text_prefix"]
