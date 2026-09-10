"""
E2E Tests: Model Selection Routing (ADR-0641–0644)

End-to-end tests for task classification → provider routing → fallback.
Tests audit trail integration and cost tracking.
"""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from core.models.model_selection_routing import ModelSelectionRouter, route_task_e2e
from core.models.provider_interface import ModelResponse


class TestModelSelectionRoutingE2E:
    """End-to-end routing tests."""

    def setup_method(self):
        self.router = ModelSelectionRouter()

    @pytest.mark.asyncio
    async def test_route_simple_task_to_ollama(self):
        """Test routing simple task to Ollama."""
        task = "Translate hello to French"

        with patch("core.models.router.ModelRouter.invoke_with_fallback") as mock_invoke:
            mock_response = ModelResponse(
                content="Bonjour",
                model="mistral:7b",
                usage_tokens=10,
                cost_usd=0.0,
            )
            mock_invoke.return_value = mock_response

            result = await self.router.route_task(task, "_default")

            assert result.content == "Bonjour"
            assert result.model == "mistral:7b"
            # Verify Ollama was preferred
            assert mock_invoke.call_args[1]["model_preference"] == "ollama"

    @pytest.mark.asyncio
    async def test_route_medium_task_to_openrouter(self):
        """Test routing medium task to OpenRouter."""
        task = "Write a Python function to sort numbers"

        with patch("core.models.router.ModelRouter.invoke_with_fallback") as mock_invoke:
            mock_response = ModelResponse(
                content="def sort_nums(nums): return sorted(nums)",
                model="open-mistral-7b",
                usage_tokens=50,
                cost_usd=0.001,
            )
            mock_invoke.return_value = mock_response

            result = await self.router.route_task(task, "_default")

            assert result.content is not None
            # Verify OpenRouter was preferred
            assert mock_invoke.call_args[1]["model_preference"] == "openrouter"

    @pytest.mark.asyncio
    async def test_route_complex_task_to_anthropic(self):
        """Test routing complex task to Anthropic."""
        task = "Design a distributed consensus protocol " * 10

        with patch("core.models.router.ModelRouter.invoke_with_fallback") as mock_invoke:
            mock_response = ModelResponse(
                content="Raft protocol implementation...",
                model="claude-opus-5",
                usage_tokens=500,
                cost_usd=0.15,
            )
            mock_invoke.return_value = mock_response

            result = await self.router.route_task(task, "_default")

            assert result.content is not None
            # Verify Anthropic was preferred
            assert mock_invoke.call_args[1]["model_preference"] == "anthropic"

    @pytest.mark.asyncio
    async def test_route_with_fallback_chain(self):
        """Test that fallback chain is provided to router."""
        task = "Test task"

        with patch("core.models.router.ModelRouter.invoke_with_fallback") as mock_invoke:
            mock_response = ModelResponse(
                content="Response",
                model="test-model",
                usage_tokens=10,
                cost_usd=0.0,
            )
            mock_invoke.return_value = mock_response

            await self.router.route_task(task, "_default")

            # Verify fallback chain was passed
            call_kwargs = mock_invoke.call_args[1]
            assert "fallback_chain" in call_kwargs
            assert len(call_kwargs["fallback_chain"]) > 0

    @pytest.mark.asyncio
    async def test_route_with_custom_messages(self):
        """Test routing with custom message list."""
        task = "Test"
        custom_messages = [
            {"role": "system", "content": "You are helpful"},
            {"role": "user", "content": "Help me"},
        ]

        with patch("core.models.router.ModelRouter.invoke_with_fallback") as mock_invoke:
            mock_response = ModelResponse(
                content="Helping",
                model="test",
                usage_tokens=10,
                cost_usd=0.0,
            )
            mock_invoke.return_value = mock_response

            await self.router.route_task(task, "_default", messages=custom_messages)

            # Verify custom messages were used
            call_kwargs = mock_invoke.call_args[1]
            assert call_kwargs["messages"] == custom_messages

    @pytest.mark.asyncio
    async def test_route_fallback_on_primary_failure(self):
        """Test fallback to next provider when primary fails."""
        task = "Test task"

        with patch("core.models.router.ModelRouter.invoke_with_fallback") as mock_invoke:
            # First call (primary provider) fails, second (fallback) succeeds
            mock_response = ModelResponse(
                content="Fallback response",
                model="claude-sonnet-5",
                usage_tokens=20,
                cost_usd=0.01,
            )

            def side_effect(*args, **kwargs):
                if kwargs["model_preference"] != "anthropic":
                    raise Exception("Provider failed")
                return mock_response

            mock_invoke.side_effect = side_effect

            # Should eventually get response from fallback
            result = await self.router.route_task(task, "_default")

            assert result.content == "Fallback response"

    @pytest.mark.asyncio
    async def test_route_all_providers_failure(self):
        """Test error when all providers fail."""
        task = "Test task"

        with patch("core.models.router.ModelRouter.invoke_with_fallback") as mock_invoke:
            mock_invoke.side_effect = Exception("All providers failed")

            with pytest.raises(Exception):
                await self.router.route_task(task, "_default")

    @pytest.mark.asyncio
    async def test_audit_trail_on_success(self):
        """Test that audit events are emitted on success."""
        task = "Test task"

        with patch("core.models.router.ModelRouter.invoke_with_fallback") as mock_invoke:
            with patch("core.models.model_selection_routing.emit_skill_audit") as mock_audit:
                mock_response = ModelResponse(
                    content="Response",
                    model="test-model",
                    usage_tokens=10,
                    cost_usd=0.001,
                )
                mock_invoke.return_value = mock_response

                await self.router.route_task(task, "_default")

                # Verify audit was called multiple times (classification, route success)
                assert mock_audit.call_count >= 2

                # Check that audit events contain expected details
                calls = [call[1] for call in mock_audit.call_args_list]
                event_types = [call["event_type"] for call in calls]
                assert "skill.model_selector.classified" in event_types
                assert "skill.model_selector.invoked" in event_types

    @pytest.mark.asyncio
    async def test_audit_trail_on_failure(self):
        """Test that audit events are emitted on failure."""
        task = "Test task"

        with patch("core.models.router.ModelRouter.invoke_with_fallback") as mock_invoke:
            with patch("core.models.model_selection_routing.emit_skill_audit") as mock_audit:
                mock_invoke.side_effect = Exception("Provider error")

                with pytest.raises(Exception):
                    await self.router.route_task(task, "_default")

                # Verify failure was audited
                calls = [call[1] for call in mock_audit.call_args_list]
                event_types = [call["event_type"] for call in calls]
                assert "skill.model_selector.route_failed" in event_types or \
                       "skill.model_selector.all_providers_failed" in event_types

    @pytest.mark.asyncio
    async def test_cost_tracking(self):
        """Test cost tracking across tasks."""
        with patch("core.models.router.ModelRouter.invoke_with_fallback") as mock_invoke:
            mock_response1 = ModelResponse(
                content="Response 1",
                model="model-1",
                usage_tokens=10,
                cost_usd=0.001,
            )
            mock_response2 = ModelResponse(
                content="Response 2",
                model="model-2",
                usage_tokens=20,
                cost_usd=0.002,
            )

            mock_invoke.side_effect = [mock_response1, mock_response2]

            await self.router.route_task("Task 1", "_default")
            await self.router.route_task("Task 2", "_default")

            # Check cost tracking
            stats = self.router.get_stats()
            assert "costs" in stats

    @pytest.mark.asyncio
    async def test_tenant_isolation_in_audit(self):
        """Test that audit events respect tenant isolation."""
        with patch("core.models.router.ModelRouter.invoke_with_fallback") as mock_invoke:
            with patch("core.models.model_selection_routing.emit_skill_audit") as mock_audit:
                mock_response = ModelResponse(
                    content="Response",
                    model="test",
                    usage_tokens=10,
                    cost_usd=0.0,
                )
                mock_invoke.return_value = mock_response

                await self.router.route_task("Task", "tenant-1")
                await self.router.route_task("Task", "tenant-2")

                # Verify each audit call has correct tenant_id
                for call in mock_audit.call_args_list:
                    assert "tenant_id" in call[1]
                    assert call[1]["tenant_id"] in ["tenant-1", "tenant-2"]

    def test_statistics_aggregation(self):
        """Test statistics aggregation."""
        selector = self.router.selector

        selector.classify("Simple")
        selector.classify("Medium " * 10)
        selector.classify("Complex " * 50)

        stats = self.router.get_stats()

        assert "classifications" in stats
        assert stats["classifications"]["classifications"] == 3

    @pytest.mark.asyncio
    async def test_custom_kwargs_passed_through(self):
        """Test that custom kwargs are passed to invoke."""
        task = "Test"

        with patch("core.models.router.ModelRouter.invoke_with_fallback") as mock_invoke:
            mock_response = ModelResponse(
                content="Response",
                model="test",
                usage_tokens=10,
                cost_usd=0.0,
            )
            mock_invoke.return_value = mock_response

            await self.router.route_task(
                task,
                "_default",
                temperature=0.5,
                max_tokens=500,
            )

            # Verify kwargs were passed
            call_kwargs = mock_invoke.call_args[1]
            assert call_kwargs.get("temperature") == 0.5
            assert call_kwargs.get("max_tokens") == 500


class TestTopLevelRoutingFunction:
    """Test top-level route_task_e2e function."""

    @pytest.mark.asyncio
    async def test_route_task_e2e_simple_usage(self):
        """Test simple usage of top-level routing function."""
        with patch("core.models.router.ModelRouter.invoke_with_fallback") as mock_invoke:
            mock_response = ModelResponse(
                content="Response",
                model="test",
                usage_tokens=10,
                cost_usd=0.0,
            )
            mock_invoke.return_value = mock_response

            result = await route_task_e2e(
                task_input="Test task",
                tenant_id="_default",
            )

            assert result.content == "Response"

    @pytest.mark.asyncio
    async def test_route_task_e2e_with_messages(self):
        """Test routing with custom messages."""
        messages = [
            {"role": "system", "content": "You are helpful"},
            {"role": "user", "content": "Help me"},
        ]

        with patch("core.models.router.ModelRouter.invoke_with_fallback") as mock_invoke:
            mock_response = ModelResponse(
                content="Response",
                model="test",
                usage_tokens=10,
                cost_usd=0.0,
            )
            mock_invoke.return_value = mock_response

            result = await route_task_e2e(
                task_input="Test",
                tenant_id="_default",
                messages=messages,
            )

            assert result.content == "Response"


class TestErrorRecovery:
    """Test error recovery and resilience."""

    @pytest.mark.asyncio
    async def test_timeout_recovery(self):
        """Test recovery from provider timeout."""
        with patch("core.models.router.ModelRouter.invoke_with_fallback") as mock_invoke:
            # First call times out, second succeeds
            mock_response = ModelResponse(
                content="Recovered",
                model="fallback",
                usage_tokens=10,
                cost_usd=0.0,
            )

            call_count = 0

            def side_effect(*args, **kwargs):
                nonlocal call_count
                call_count += 1
                if call_count == 1:
                    raise asyncio.TimeoutError("Timeout")
                return mock_response

            mock_invoke.side_effect = side_effect

            result = await ModelSelectionRouter().route_task("Test", "_default")

            assert result.content == "Recovered"

    @pytest.mark.asyncio
    async def test_network_error_recovery(self):
        """Test recovery from network errors."""
        with patch("core.models.router.ModelRouter.invoke_with_fallback") as mock_invoke:
            mock_response = ModelResponse(
                content="Recovered",
                model="fallback",
                usage_tokens=10,
                cost_usd=0.0,
            )

            call_count = 0

            def side_effect(*args, **kwargs):
                nonlocal call_count
                call_count += 1
                if call_count == 1:
                    raise ConnectionError("Network failed")
                return mock_response

            mock_invoke.side_effect = side_effect

            result = await ModelSelectionRouter().route_task("Test", "_default")

            assert result.content == "Recovered"
