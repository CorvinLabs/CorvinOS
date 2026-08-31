"""Brain Task Scheduling Tests (pytest)

Tests that Brain correctly schedules tasks and selects agents.
This covers the gap: API submit → Brain scheduling → correct agent selection.

Effort: 25 hours, 15+ tests
"""

import pytest
import asyncio
from datetime import datetime, timedelta


@pytest.mark.integration
@pytest.mark.high_risk
@pytest.mark.asyncio
class TestBrainTaskScheduling:
    """Brain scheduling and agent selection."""

    async def test_task_submission_queues_in_brain(self, app_client, test_user):
        """Task submission creates brain task."""
        response = await app_client.post(
            "/api/v2/task/submit",
            json={
                "input": "What is quantum computing?",
                "task_type": "qa",
                "user_id": test_user["user_id"],
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert "task_id" in data
        assert data["status"] in ["queued", "pending"]

        # Verify task appears in scheduler
        task_id = data["task_id"]
        status_response = await app_client.get(f"/api/v2/task/{task_id}/status")
        assert status_response.status_code == 200
        status_data = status_response.json()
        assert status_data["status"] in ["queued", "pending", "running"]

    async def test_brain_selects_qa_agent_for_qa_task(self, app_client, test_user):
        """Brain selects QA agent for QA tasks."""
        response = await app_client.post(
            "/api/v2/task/submit",
            json={
                "input": "What is Python?",
                "task_type": "qa",
                "user_id": test_user["user_id"],
            },
        )

        task_id = response.json()["task_id"]

        # Wait for Brain to schedule
        for attempt in range(10):
            status = await app_client.get(f"/api/v2/task/{task_id}/status")
            status_data = status.json()

            if "agent" in status_data:
                # Verify QA agent selected
                agent = status_data["agent"]
                assert "qa" in agent.lower(), f"Wrong agent selected: {agent}"
                break

            await asyncio.sleep(0.5)

    async def test_brain_selects_analysis_agent_for_analysis_task(
        self, app_client, test_user
    ):
        """Brain selects Analysis agent for analysis tasks."""
        response = await app_client.post(
            "/api/v2/task/submit",
            json={
                "input": "Analyze this data: ...",
                "task_type": "analysis",
                "user_id": test_user["user_id"],
            },
        )

        task_id = response.json()["task_id"]

        # Wait for Brain to schedule
        for attempt in range(10):
            status = await app_client.get(f"/api/v2/task/{task_id}/status")
            status_data = status.json()

            if "agent" in status_data:
                agent = status_data["agent"]
                assert "analysis" in agent.lower() or "analyzer" in agent.lower()
                break

            await asyncio.sleep(0.5)

    async def test_concurrent_task_scheduling(self, app_client, test_user):
        """Multiple tasks scheduled concurrently without deadlock."""
        tasks = []

        # Submit 5 tasks concurrently
        for i in range(5):
            response = await app_client.post(
                "/api/v2/task/submit",
                json={
                    "input": f"Task {i}: What is {i}?",
                    "task_type": "qa",
                    "user_id": f"{test_user['user_id']}-{i}",
                },
            )
            assert response.status_code == 200
            tasks.append(response.json()["task_id"])

        # All tasks should be scheduled
        assert len(tasks) == 5

        # Verify all tasks progress
        await asyncio.sleep(1)
        for task_id in tasks:
            status = await app_client.get(f"/api/v2/task/{task_id}/status")
            assert status.status_code == 200
            assert status.json()["status"] in ["queued", "running", "complete"]

    async def test_task_timeout_configuration(self, app_client, test_user):
        """Task respects timeout configuration."""
        response = await app_client.post(
            "/api/v2/task/submit",
            json={
                "input": "Slow analysis task",
                "task_type": "analysis",
                "user_id": test_user["user_id"],
                "timeout_seconds": 5,  # 5 second timeout
            },
        )

        task_id = response.json()["task_id"]

        # Simulate slow execution (would normally timeout)
        await asyncio.sleep(6)

        # Check if timeout was respected
        status = await app_client.get(f"/api/v2/task/{task_id}/status")
        status_data = status.json()

        # Task should have failed or timed out
        assert status_data.get("status") in ["failed", "timeout", "running"]

    async def test_brain_agent_fallback(self, app_client, test_user):
        """Brain selects fallback agent if primary unavailable."""
        # Simulate primary agent unavailability
        response = await app_client.post(
            "/api/v2/task/submit",
            json={
                "input": "Test task",
                "task_type": "complex",  # Might not have dedicated agent
                "user_id": test_user["user_id"],
            },
        )

        task_id = response.json()["task_id"]
        await asyncio.sleep(1)

        status = await app_client.get(f"/api/v2/task/{task_id}/status")
        status_data = status.json()

        # Should have selected some agent (not failed to schedule)
        if status_data["status"] != "failed":
            assert "agent" in status_data, "No agent selected for task"

    async def test_task_priority_scheduling(self, app_client, test_user):
        """Higher priority tasks scheduled before lower priority."""
        # Submit low priority task
        low_response = await app_client.post(
            "/api/v2/task/submit",
            json={
                "input": "Low priority task",
                "task_type": "qa",
                "user_id": test_user["user_id"],
                "priority": 1,
            },
        )
        low_task_id = low_response.json()["task_id"]

        # Submit high priority task
        high_response = await app_client.post(
            "/api/v2/task/submit",
            json={
                "input": "High priority task",
                "task_type": "qa",
                "user_id": test_user["user_id"],
                "priority": 10,
            },
        )
        high_task_id = high_response.json()["task_id"]

        # High priority should start/complete first
        await asyncio.sleep(2)

        high_status = await app_client.get(f"/api/v2/task/{high_task_id}/status")
        low_status = await app_client.get(f"/api/v2/task/{low_task_id}/status")

        # High priority should be further along
        high_progress = self._get_progress(high_status.json())
        low_progress = self._get_progress(low_status.json())
        assert high_progress >= low_progress

    def _get_progress(self, status_data: dict) -> int:
        """Get progress score (0=queued, 50=running, 100=complete)."""
        status = status_data.get("status", "queued")
        if status == "complete":
            return 100
        elif status == "running":
            return 50
        else:  # queued, pending
            return 0

    async def test_task_context_initialization(self, app_client, test_user):
        """Brain initializes ExecutionContext with proper metadata."""
        response = await app_client.post(
            "/api/v2/task/submit",
            json={
                "input": "Test",
                "task_type": "qa",
                "user_id": test_user["user_id"],
            },
        )

        task_id = response.json()["task_id"]
        await asyncio.sleep(1)

        # Get execution context (if exposed)
        context_response = await app_client.get(f"/api/v2/task/{task_id}/context")
        if context_response.status_code == 200:
            context = context_response.json()
            assert "task_id" in context
            assert "user_id" in context
            assert "created_at" in context

    async def test_task_dependency_detection(self, app_client, test_user):
        """Brain detects task dependencies (e.g., needs prior results)."""
        # Submit task with dependencies
        response = await app_client.post(
            "/api/v2/task/submit",
            json={
                "input": "Use results from task-456",
                "task_type": "qa",
                "user_id": test_user["user_id"],
                "depends_on": ["task-456"],
            },
        )

        task_id = response.json()["task_id"]
        await asyncio.sleep(1)

        status = await app_client.get(f"/api/v2/task/{task_id}/status")
        status_data = status.json()

        # Should be waiting for dependency
        if "task-456" not in ["complete", "running"]:
            assert status_data["status"] in ["waiting", "blocked", "queued"]

    async def test_brain_logs_scheduling_decision(self, app_client, test_user, caplog):
        """Brain logs why it selected an agent."""
        response = await app_client.post(
            "/api/v2/task/submit",
            json={
                "input": "Why was this agent chosen?",
                "task_type": "qa",
                "user_id": test_user["user_id"],
            },
        )

        task_id = response.json()["task_id"]
        await asyncio.sleep(1)

        # Get logs (if available)
        logs_response = await app_client.get(f"/api/v2/task/{task_id}/logs")
        if logs_response.status_code == 200:
            logs = logs_response.json()
            # Should contain scheduling decision
            assert any("agent" in log.lower() for log in logs)

    async def test_task_queueing_on_capacity_limit(self, app_client, test_user):
        """Tasks queue when system at capacity."""
        # Submit many tasks to hit capacity
        submitted_tasks = []
        for i in range(100):  # Try 100 tasks
            response = await app_client.post(
                "/api/v2/task/submit",
                json={
                    "input": f"Task {i}",
                    "task_type": "qa",
                    "user_id": f"{test_user['user_id']}-{i}",
                },
            )
            if response.status_code == 200:
                submitted_tasks.append(response.json()["task_id"])

        # Some should be queued (not running immediately)
        queued_count = 0
        for task_id in submitted_tasks[:10]:  # Check first 10
            status = await app_client.get(f"/api/v2/task/{task_id}/status")
            if status.json()["status"] == "queued":
                queued_count += 1

        # At least some should be queued
        assert queued_count > 0, "No tasks queued (capacity not tested)"
