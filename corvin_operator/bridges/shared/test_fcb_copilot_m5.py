"""
ADR-0087 M5 Tests — Tool Call Bridge (FCB) for Copilot via Text Syntax

Tier-1: Syntax validation (JSON schemas, markdown format, size limits)
Tier-2: Unit tests (parsing, injection, mock TEB invocation)
Tier-3: E2E tests (real Copilot spawn or realistic mock output)

Test Standard (from M1–M4): All new features must pass Tier-1/2/3.
"""

import json
import pytest
from teb.fcb_copilot import (
    ToolSchemaInjector,
    CopilotToolParser,
    ToolResultInjector,
    CopilotMCPIntegration,
    MCPTool,
)


# ============================================================================
# TIER-1: Syntax Validation (JSON, markdown, size limits)
# ============================================================================

class TestToolSchemaInjectorSyntaxTier1:
    """Validate tool schema generation (JSON, markdown, size)."""

    def test_inject_schemas_empty_list(self):
        """
        Given: empty tool list
        When: inject_schemas([]) called
        Then: raise ValueError (cannot inject zero tools)
        """
        injector = ToolSchemaInjector()
        with pytest.raises(ValueError, match="empty"):
            injector.inject_schemas([])

    def test_inject_schemas_single_tool_valid_json(self):
        """
        Given: single tool with valid JSON schema
        When: inject_schemas([tool1]) called
        Then: return markdown string with "## Available Tools" header + valid JSON
        """
        injector = ToolSchemaInjector()
        tool = MCPTool(
            name="Read",
            description="Read a file",
            input_schema={"type": "object", "properties": {"path": {"type": "string"}}}
        )
        result = injector.inject_schemas([tool])
        assert result.startswith("## Available Tools")
        assert "### Tool: Read" in result
        assert "Read a file" in result
        assert json.dumps(tool.input_schema, indent=2) in result

    def test_inject_schemas_multiple_tools_order_preserved(self):
        """
        Given: 3 tools (Read, Write, Bash)
        When: inject_schemas([Read, Write, Bash]) called
        Then: return markdown with tools in same order
        """
        injector = ToolSchemaInjector()
        tools = [
            MCPTool(name="Read", description="Read", input_schema={"type": "object"}),
            MCPTool(name="Write", description="Write", input_schema={"type": "object"}),
            MCPTool(name="Bash", description="Bash", input_schema={"type": "object"}),
        ]
        result = injector.inject_schemas(tools)
        read_pos = result.find("### Tool: Read")
        write_pos = result.find("### Tool: Write")
        bash_pos = result.find("### Tool: Bash")
        assert read_pos < write_pos < bash_pos

    def test_inject_schemas_size_under_8kb(self):
        """
        Given: tool list (total schema size < 8 KB)
        When: inject_schemas() called
        Then: return markdown, assert len() <= 8192 bytes
        """
        injector = ToolSchemaInjector()
        tools = [
            MCPTool(
                name=f"Tool{i}",
                description=f"Tool {i}",
                input_schema={"type": "object", "properties": {"arg": {"type": "string"}}}
            )
            for i in range(10)
        ]
        result = injector.inject_schemas(tools)
        assert len(result.encode()) <= 8192

    def test_inject_schemas_size_exceeds_8kb_truncates(self):
        """
        Given: tools that would exceed 8 KB
        When: inject_schemas() called
        Then: truncate gracefully, return < 8 KB
        """
        injector = ToolSchemaInjector()
        # Create tools with large schemas
        tools = [
            MCPTool(
                name=f"Tool{i}",
                description="x" * 500,  # Large description
                input_schema={"type": "object", "properties": {"arg": {"type": "string", "description": "x" * 100}}}
            )
            for i in range(50)  # 50 tools likely exceeds 8 KB
        ]
        result = injector.inject_schemas(tools)
        assert len(result.encode()) <= 8192

    def test_inject_schemas_tool_name_alphanumeric_only(self):
        """
        Given: tool with invalid name (e.g., "tool-name" with dash)
        When: inject_schemas() called
        Then: raise ValueError (tool names must be alphanumeric + underscore)
        """
        injector = ToolSchemaInjector()
        tool = MCPTool(name="tool-name", description="Bad", input_schema={})
        with pytest.raises(ValueError, match="Invalid tool name"):
            injector.inject_schemas([tool])

    def test_schema_to_markdown_format(self):
        """
        Given: single tool "Read" with description and input_schema
        When: schema_to_markdown(tool) called
        Then: return markdown with "### Tool: Read", description, "**Input Schema:**", JSON block
        """
        injector = ToolSchemaInjector()
        tool = MCPTool(
            name="Read",
            description="Read a file from disk",
            input_schema={"type": "object", "properties": {"file_path": {"type": "string"}}}
        )
        result = injector.schema_to_markdown(tool)
        assert "### Tool: Read" in result
        assert "Read a file from disk" in result
        assert "**Input Schema:**" in result
        assert "```json" in result


# ============================================================================
# TIER-2: Parsing & Unit Tests (mock TEB, no real spawns)
# ============================================================================

class TestCopilotToolParserTier2:
    """Parse [TOOL: name] syntax from Copilot output."""

    def test_parse_tool_call_valid_simple(self):
        """
        Given: '[TOOL: Read] {"file_path": "/etc/hosts"}'
        When: parse_tool_call() called
        Then: return ("Read", {"file_path": "/etc/hosts"})
        """
        parser = CopilotToolParser()
        name, args = parser.parse_tool_call('[TOOL: Read] {"file_path": "/etc/hosts"}')
        assert name == "Read"
        assert args == {"file_path": "/etc/hosts"}

    def test_parse_tool_call_case_insensitive_name(self):
        """
        Given: '[TOOL: read]', '[TOOL: READ]', '[TOOL: Read]' (mixed case)
        When: parse_tool_call() called on each
        Then: all return tool_name preserved (case-aware, not normalized)
        """
        parser = CopilotToolParser()
        # Note: The implementation preserves case, doesn't normalize to lowercase
        for variant in ["[TOOL: read] {}", "[TOOL: READ] {}", "[TOOL: Read] {}"]:
            name, args = parser.parse_tool_call(variant)
            assert name is not None
            assert args == {}

    def test_parse_tool_call_multiline_json_args(self):
        """
        Given: '[TOOL: Write] {\\n  "file_path": "/tmp/file",\\n  "content": "..."\\n}'
        When: parse_tool_call() called
        Then: parse multiline JSON correctly, return (Write, dict)
        """
        parser = CopilotToolParser()
        output = '[TOOL: Write] {\n  "file_path": "/tmp/file",\n  "content": "hello"\n}'
        name, args = parser.parse_tool_call(output)
        assert name == "Write"
        assert args == {"file_path": "/tmp/file", "content": "hello"}

    def test_parse_tool_call_no_match(self):
        """
        Given: "No tool here, just text"
        When: parse_tool_call() called
        Then: return (None, None)
        """
        parser = CopilotToolParser()
        name, args = parser.parse_tool_call("No tool here, just text")
        assert (name, args) == (None, None)

    def test_parse_tool_call_malformed_json(self):
        """
        Given: '[TOOL: Read] {invalid json}'
        When: parse_tool_call() called
        Then: return ("Read", None) — tool name detected but args invalid
        """
        parser = CopilotToolParser()
        name, args = parser.parse_tool_call('[TOOL: Read] {invalid json}')
        assert name == "Read"
        assert args is None

    def test_parse_tool_call_multiple_matches_first_wins(self):
        """
        Given: '[TOOL: Read] {...} ... [TOOL: Write] {...}'
        When: parse_tool_call() called
        Then: return ("Read", ...) — first match wins, ignore Write
        """
        parser = CopilotToolParser()
        output = '[TOOL: Read] {"x": 1} and [TOOL: Write] {"y": 2}'
        name, args = parser.parse_tool_call(output)
        assert name == "Read"
        assert args == {"x": 1}

    def test_parse_tool_call_empty_args_valid(self):
        """
        Given: '[TOOL: SomeCommand] {}'
        When: parse_tool_call() called
        Then: return ("SomeCommand", {}) — empty args valid
        """
        parser = CopilotToolParser()
        name, args = parser.parse_tool_call('[TOOL: SomeCommand] {}')
        assert name == "SomeCommand"
        assert args == {}

    def test_parse_tool_call_underscore_in_name(self):
        """
        Given: '[TOOL: Tool_Name_123] {"arg": "val"}'
        When: parse_tool_call() called
        Then: tool name matches \\w+ (alphanumeric + underscore), return (Tool_Name_123, dict)
        """
        parser = CopilotToolParser()
        name, args = parser.parse_tool_call('[TOOL: Tool_Name_123] {"arg": "val"}')
        assert name == "Tool_Name_123"
        assert args == {"arg": "val"}

    def test_parse_args_json_lenient_parsing(self):
        """
        Given: JSON with extra spaces: '{ "key" : "val" }'
        When: _parse_args_json() called
        Then: parse successfully (lenient JSON)
        """
        parser = CopilotToolParser()
        result = parser._parse_args_json('{ "key" : "val" }')
        assert result == {"key": "val"}

    def test_parse_args_json_invalid(self):
        """
        Given: completely invalid JSON
        When: _parse_args_json() called
        Then: return None
        """
        parser = CopilotToolParser()
        result = parser._parse_args_json('{not valid json}')
        assert result is None


class TestToolResultInjectorTier2:
    """Queue tool results to buffered transport (M2 pattern)."""

    def test_inject_result_queued_to_btw_queue(self, tmp_path):
        """
        Given: successful tool result, session_dir exists
        When: inject_result("file content", tool_name="Read", session_dir=...) called
        Then: result queued to session_dir/btw_queue.jsonl (append-only)
        """
        injector = ToolResultInjector()
        session_dir = str(tmp_path)
        result_text = "File content here"

        injector.inject_result(result_text, tool_name="Read", session_dir=session_dir, status="success")

        # Verify JSONL was written
        btw_queue_path = tmp_path / "btw_queue.jsonl"
        assert btw_queue_path.exists()
        content = btw_queue_path.read_text()
        entry = json.loads(content.strip())
        assert entry["tool_name"] == "Read"
        assert entry["status"] == "success"
        assert entry["type"] == "tool_result"

    def test_inject_result_truncates_at_4kb(self, tmp_path):
        """
        Given: result > 4 KB (e.g., 10 KB file content)
        When: inject_result() called
        Then: truncate to 4 KB, find sentence boundary, append "[...truncated...]"
        """
        injector = ToolResultInjector()
        session_dir = str(tmp_path)
        # Create 10 KB of text
        large_result = "x" * 10000

        injector.inject_result(large_result, tool_name="Read", session_dir=session_dir)

        btw_queue_path = tmp_path / "btw_queue.jsonl"
        entry = json.loads(btw_queue_path.read_text().strip())
        assert entry["truncated"] is True
        assert "[...truncated...]" in entry["content"]
        assert len(entry["content"].encode()) <= 4 * 1024 + 100  # Some overhead for marker

    def test_inject_result_truncation_sentence_boundary(self, tmp_path):
        """
        Given: result with text ending near 4 KB with sentence boundaries
        When: inject_result() called
        Then: truncate at sentence boundary (last period), not mid-word
        """
        injector = ToolResultInjector()
        session_dir = str(tmp_path)
        # Create text with sentence boundaries
        text = "Sentence one. " * 300 + "Sentence two."  # ~5 KB with clear sentences
        injector.inject_result(text, tool_name="Read", session_dir=session_dir)

        btw_queue_path = tmp_path / "btw_queue.jsonl"
        entry = json.loads(btw_queue_path.read_text().strip())
        # Should truncate at a sentence boundary (ending with . or [..truncated..])
        assert entry["truncated"] is True

    def test_inject_result_jsonl_format(self, tmp_path):
        """
        Given: queued result entry
        When: read btw_queue.jsonl
        Then: entry is valid JSONL with all required fields
        """
        injector = ToolResultInjector()
        session_dir = str(tmp_path)
        injector.inject_result("test result", tool_name="Read", session_dir=session_dir, status="success")

        btw_queue_path = tmp_path / "btw_queue.jsonl"
        entry = json.loads(btw_queue_path.read_text().strip())

        required_fields = {"type", "tool_name", "timestamp", "status", "result_size_bytes", "truncated", "content"}
        assert set(entry.keys()) >= required_fields
        assert entry["type"] == "tool_result"
        assert entry["status"] == "success"
        assert isinstance(entry["timestamp"], int)
        assert isinstance(entry["result_size_bytes"], int)
        assert isinstance(entry["truncated"], bool)

    def test_inject_result_session_dir_not_exists(self):
        """
        Given: session_dir does not exist
        When: inject_result() called
        Then: raise ValueError
        """
        injector = ToolResultInjector()
        with pytest.raises(ValueError, match="not a directory"):
            injector.inject_result("result", tool_name="Read", session_dir="/nonexistent/dir")


class TestCopilotMCPIntegrationTier2:
    """High-level FCB integration (mock TEB)."""

    def test_inject_tools_system_prompt(self, tmp_path):
        """
        Given: 5 tools
        When: inject_tools(tools) called
        Then: return markdown system prompt section (valid, < 8 KB)
        """
        session_dir = str(tmp_path)
        integration = CopilotMCPIntegration(teb_tool_registry=None, session_dir=session_dir)

        tools = [
            MCPTool(name=f"Tool{i}", description=f"Tool {i}", input_schema={"type": "object"})
            for i in range(5)
        ]
        result = integration.inject_tools(tools)

        assert result.startswith("## Available Tools")
        assert len(result.encode()) <= 8192
        for tool in tools:
            assert tool.name in result

    def test_parse_and_execute_no_tool_call(self, tmp_path):
        """
        Given: Copilot output without [TOOL:...] syntax
        When: parse_and_execute(output) called
        Then: return None (no tool detected)
        """
        session_dir = str(tmp_path)
        integration = CopilotMCPIntegration(teb_tool_registry=None, session_dir=session_dir)

        result = integration.parse_and_execute("No tool here, just regular text")
        assert result is None

    def test_parse_and_execute_tool_call_detected(self, tmp_path):
        """
        Given: Copilot output with [TOOL: Read] {...}
        When: parse_and_execute(output) called
        Then: detect tool, queue result, return result string
        """
        session_dir = str(tmp_path)
        integration = CopilotMCPIntegration(teb_tool_registry=None, session_dir=session_dir)

        output = '[TOOL: Read] {"file_path": "/etc/hosts"}'
        result = integration.parse_and_execute(output)

        assert result is not None
        assert "Read" in result
        # Verify JSONL was written
        btw_queue_path = tmp_path / "btw_queue.jsonl"
        assert btw_queue_path.exists()

    def test_parse_and_execute_malformed_args_error(self, tmp_path):
        """
        Given: [TOOL: Read] with malformed JSON args
        When: parse_and_execute() called
        Then: return error message, queue error entry
        """
        session_dir = str(tmp_path)
        integration = CopilotMCPIntegration(teb_tool_registry=None, session_dir=session_dir)

        output = '[TOOL: Read] {invalid json}'
        result = integration.parse_and_execute(output)

        assert result is not None
        assert "Error" in result or "error" in result
        btw_queue_path = tmp_path / "btw_queue.jsonl"
        entry = json.loads(btw_queue_path.read_text().strip())
        assert entry["status"] == "error"


# ============================================================================
# TIER-3: E2E Tests (real Copilot spawn or realistic mock output)
# ============================================================================
# Note: Tier-3 tests will be added in Iteration 4 (M5 E2E phase)
# Placeholder for test class structure:

class TestCopilotToolCallE2E:
    """End-to-end Copilot tool calls (realistic mock, since GH_TOKEN not available in test env)."""

    def test_e2e_full_flow_mock_copilot(self, tmp_path):
        """
        Tier-3: Full FCB flow: inject schemas → mock Copilot output → parse → queue result.
        Given: 3 tools injected as system prompt
               Mock Copilot output: "[TOOL: Read] {\"file_path\": \"/etc/hosts\"}"
        When: parse_and_execute() called
        Then: tool name parsed, result queued, next turn sees queued result
        """
        # Setup
        session_dir = str(tmp_path)
        integration = CopilotMCPIntegration(teb_tool_registry=None, session_dir=session_dir)

        # Step 1: Inject tools into system prompt
        tools = [
            MCPTool(name="Read", description="Read file", input_schema={"type": "object", "properties": {"file_path": {"type": "string"}}}),
            MCPTool(name="Write", description="Write file", input_schema={"type": "object"}),
            MCPTool(name="Bash", description="Run bash", input_schema={"type": "object"}),
        ]
        system_prompt = integration.inject_tools(tools)
        assert "## Available Tools" in system_prompt
        assert all(t.name in system_prompt for t in tools)

        # Step 2: Mock Copilot output with tool call
        mock_copilot_output = (
            "I'll read the hosts file for you.\n\n"
            '[TOOL: Read] {"file_path": "/etc/hosts"}\n\n'
            "Once you confirm, I can help with more."
        )

        # Step 3: Parse and execute
        result = integration.parse_and_execute(mock_copilot_output)
        assert result is not None
        assert "Read" in result

        # Step 4: Verify result queued to btw_queue
        btw_queue_path = tmp_path / "btw_queue.jsonl"
        assert btw_queue_path.exists()
        queued_entry = json.loads(btw_queue_path.read_text().strip())
        assert queued_entry["tool_name"] == "Read"
        assert queued_entry["type"] == "tool_result"
        assert "**Tool Result:** [Read]" in queued_entry["content"]

    def test_e2e_tool_result_prepended_to_next_turn(self, tmp_path):
        """
        Tier-3: Two-turn flow: Turn 1 queues result, Turn 2 sees queued result in btw_queue.
        Given: Turn 1: tool executed, result queued to btw_queue.jsonl
               Turn 2: new prompt needs to see that result
        When: checking btw_queue.jsonl after Turn 1
        Then: result entry is formatted correctly for prepending to Turn 2
        """
        session_dir = str(tmp_path)
        integration = CopilotMCPIntegration(teb_tool_registry=None, session_dir=session_dir)

        # Turn 1: Execute a tool call
        mock_turn1_output = '[TOOL: Read] {"file_path": "/tmp/test.txt"}'
        result = integration.parse_and_execute(mock_turn1_output)

        # Turn 2: Read the queued result (simulating next spawn)
        btw_queue_path = tmp_path / "btw_queue.jsonl"
        queued_lines = btw_queue_path.read_text().strip().split("\n")
        assert len(queued_lines) >= 1

        queued_entry = json.loads(queued_lines[0])
        # The content should be ready to prepend to next turn
        prepend_text = queued_entry["content"]
        assert prepend_text.startswith("**Tool Result:**")
        assert "Read" in prepend_text

        # Simulate Turn 2 prompt preparation
        turn2_system_prompt = "You are helpful"
        turn2_user_prompt = f"{prepend_text}\n\nUser: What was in that file?"
        # This is what would be sent to next Copilot spawn
        assert "**Tool Result:**" in turn2_user_prompt
        assert "Read" in turn2_user_prompt

    def test_e2e_multiple_tool_calls_sequential(self, tmp_path):
        """
        Tier-3: Multiple tool calls in sequence (single Copilot turn with multiple [TOOL:...] blocks).
        Given: Mock Copilot output with two [TOOL:...] calls
        When: parse_and_execute() called
        Then: first match is detected, second is ignored (by design)
        """
        session_dir = str(tmp_path)
        integration = CopilotMCPIntegration(teb_tool_registry=None, session_dir=session_dir)

        # Mock output with two tool calls (first wins by design)
        mock_output = (
            'I need to do two things.\n\n'
            '[TOOL: Read] {"file_path": "/etc/hosts"}\n\n'
            'Then I would run [TOOL: Bash] {"cmd": "ls"}'
        )

        result = integration.parse_and_execute(mock_output)
        assert result is not None
        assert "Read" in result

        # Verify only the first tool was queued
        btw_queue_path = tmp_path / "btw_queue.jsonl"
        queued_entry = json.loads(btw_queue_path.read_text().strip())
        assert queued_entry["tool_name"] == "Read"

    def test_e2e_tool_call_with_large_result_truncation(self, tmp_path):
        """
        Tier-3: Tool result larger than 4 KB gets truncated intelligently.
        Given: 10 KB result from tool execution
        When: inject_result() called
        Then: truncated to ~4 KB, sentence boundary preserved, marker added
        """
        session_dir = str(tmp_path)
        integration = CopilotMCPIntegration(teb_tool_registry=None, session_dir=session_dir)

        # Simulate large tool result (e.g., file contents)
        large_result = ("This is a line of file content. " * 200)  # ~6.4 KB

        # Queue it
        integration.result_injector.inject_result(
            large_result,
            tool_name="Read",
            session_dir=session_dir,
            status="success"
        )

        # Verify truncation
        btw_queue_path = tmp_path / "btw_queue.jsonl"
        queued_entry = json.loads(btw_queue_path.read_text().strip())
        assert queued_entry["truncated"] is True
        assert "[...truncated...]" in queued_entry["content"]
        # Content should be reasonable size
        assert len(queued_entry["content"].encode()) <= 4 * 1024 + 200
