"""Shared E2E test fixtures and configuration for Playwright."""

import pytest
from pathlib import Path
from typing import Generator
import os


@pytest.fixture(scope="session")
def playwright_config():
    """Playwright configuration for all E2E tests."""
    return {
        "base_url": os.getenv("BASE_URL", "http://localhost:8765"),
        "headless": os.getenv("HEADLESS", "true").lower() == "true",
        "slow_mo": int(os.getenv("SLOW_MO", "0")),
        "timeout": int(os.getenv("TIMEOUT", "30000")),  # 30s default
    }


@pytest.fixture
def api_base_url():
    """Backend API base URL."""
    return os.getenv("API_BASE_URL", "http://localhost:8000")


@pytest.fixture
def test_user():
    """Test user credentials."""
    return {
        "email": "test@example.com",
        "password": "test123",
        "user_id": "test-user-001",
    }


@pytest.fixture
def test_task_data():
    """Sample test task data."""
    return {
        "simple_qa": {
            "input": "What is the capital of France?",
            "type": "qa",
            "expected_keywords": ["Paris", "capital"],
        },
        "analysis": {
            "input": "Analyze this dataset: ...",
            "type": "analysis",
            "expected_keywords": ["analyzed", "findings"],
        },
        "invalid": {
            "input": "",  # Empty input
            "type": "qa",
            "should_fail": True,
            "error_message": "Task input is required",
        },
    }


@pytest.fixture
def wait_for_element_timeout():
    """Default timeout for waiting for elements (ms)."""
    return 10000  # 10 seconds


class ScreenshotHelper:
    """Helper for taking screenshots on failure."""

    def __init__(self, page, test_name: str):
        self.page = page
        self.test_name = test_name
        self.screenshot_dir = Path(__file__).parent / "screenshots"
        self.screenshot_dir.mkdir(exist_ok=True)

    async def take_on_failure(self, step_name: str):
        """Take screenshot and save for debugging."""
        filename = self.screenshot_dir / f"{self.test_name}_{step_name}.png"
        await self.page.screenshot(path=str(filename))
        print(f"Screenshot saved: {filename}")


@pytest.fixture
def screenshot_helper(page, request):
    """Screenshot helper for debugging."""
    return ScreenshotHelper(page, request.node.name)


class APIHelper:
    """Helper for API calls during E2E tests."""

    def __init__(self, api_client, base_url: str):
        self.client = api_client
        self.base_url = base_url

    async def submit_task(self, input: str, task_type: str = "qa", **kwargs) -> dict:
        """Submit a task via API."""
        response = await self.client.post(
            f"{self.base_url}/api/v2/task/submit",
            json={
                "input": input,
                "task_type": task_type,
                **kwargs,
            },
        )
        assert response.status_code == 200, f"Failed to submit task: {response.text}"
        return response.json()

    async def get_task_status(self, task_id: str) -> dict:
        """Get task status."""
        response = await self.client.get(f"{self.base_url}/api/v2/task/{task_id}/status")
        assert response.status_code == 200
        return response.json()

    async def get_task_output(self, task_id: str) -> dict:
        """Get task output."""
        response = await self.client.get(f"{self.base_url}/api/v2/task/{task_id}/output")
        assert response.status_code == 200
        return response.json()

    async def cancel_task(self, task_id: str) -> dict:
        """Cancel a task."""
        response = await self.client.post(f"{self.base_url}/api/v2/task/{task_id}/cancel")
        assert response.status_code in [200, 202]  # 202 = async
        return response.json()


@pytest.fixture
async def api_helper(httpx_async_client, api_base_url):
    """API helper for E2E tests."""
    return APIHelper(httpx_async_client, api_base_url)


@pytest.fixture
def form_helper(page):
    """Helper for form interactions."""

    class FormHelper:
        async def fill_task_form(self, input_text: str, task_type: str = "qa"):
            """Fill task form with given data."""
            await page.fill('textarea[name="taskInput"]', input_text)
            await page.select_option('select[name="taskType"]', task_type)

        async def submit_form(self):
            """Click submit button and wait for response."""
            await page.click('button:has-text("Submit")')
            # Wait for success message or error
            try:
                await page.wait_for_selector('text=Task submitted', timeout=5000)
                return True
            except:
                # Check for error message
                error_msg = await page.text_content('div[role="alert"]')
                return error_msg

        async def clear_form(self):
            """Clear form fields."""
            await page.fill('textarea[name="taskInput"]', "")

    return FormHelper()
