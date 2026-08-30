"""Form → API Contract Tests (Playwright)

Tests that the Console form correctly serializes to API payload.
This covers the critical gap: Form validation → API call.
"""

import pytest
from typing import Dict, Any
import json


@pytest.mark.e2e
@pytest.mark.high_risk
class TestFormAPIContract:
    """Contract tests between Console form and backend API."""

    async def test_valid_form_submission(self, page, test_task_data, api_helper):
        """Valid form → successful API call with correct payload."""
        task_input = test_task_data["simple_qa"]["input"]

        # Step 1: Navigate to tasks panel
        await page.goto("http://localhost:8765/console/app/tasks")
        await page.wait_for_selector('textarea[name="taskInput"]', timeout=5000)

        # Step 2: Fill and submit form
        await page.fill('textarea[name="taskInput"]', task_input)
        await page.select_option('select[name="taskType"]', "qa")
        await page.click('button:has-text("Submit")')

        # Step 3: Intercept API request
        request_data = None

        async def handle_request(route):
            request_data = route.request.post_data_json
            await route.continue_()

        await page.route("**/api/v2/task/submit", handle_request)

        # Step 4: Verify API was called with correct payload
        await page.wait_for_response(
            lambda r: "/api/v2/task/submit" in r.url and r.status == 200, timeout=5000
        )

        # Assert request payload
        assert request_data is not None, "API request was not captured"
        assert request_data["input"] == task_input
        assert request_data["task_type"] == "qa"
        assert "user_id" in request_data  # Backend should provide this

    async def test_empty_input_validation(self, page, form_helper):
        """Empty input → form validation error (before API call)."""
        await page.goto("http://localhost:8765/console/app/tasks")

        # Try to submit empty form
        await form_helper.clear_form()
        await form_helper.submit_form()

        # Verify validation error shown
        await page.wait_for_selector('text=Task input is required', timeout=5000)

        # Verify API was NOT called
        intercepted_requests = []

        async def track_request(route):
            intercepted_requests.append(route.request)
            await route.continue_()

        await page.route("**/api/v2/task/submit", track_request)
        await form_helper.submit_form()

        assert len(intercepted_requests) == 0, "API should not be called on validation error"

    async def test_form_payload_schema(self, page, api_base_url):
        """Form payload matches expected API schema."""
        await page.goto("http://localhost:8765/console/app/tasks")

        # Intercept and validate request schema
        async def validate_schema(route):
            request = route.request
            data = request.post_data_json

            # Validate required fields
            assert "input" in data, "Missing 'input' field"
            assert "task_type" in data, "Missing 'task_type' field"
            assert isinstance(data["input"], str), "'input' must be string"
            assert isinstance(data["task_type"], str), "'task_type' must be string"

            # Validate optional fields
            if "context" in data:
                assert isinstance(data["context"], dict), "'context' must be dict"

            await route.continue_()

        await page.route("**/api/v2/task/submit", validate_schema)

        # Submit valid form
        await page.fill('textarea[name="taskInput"]', "Test input")
        await page.select_option('select[name="taskType"]', "qa")
        await page.click('button:has-text("Submit")')

    async def test_form_special_characters(self, page, form_helper):
        """Form correctly encodes special characters in payload."""
        special_input = 'Test with "quotes", \n newlines, & ampersands, <html> tags'

        await page.goto("http://localhost:8765/console/app/tasks")
        await form_helper.fill_task_form(special_input)

        # Capture request
        captured_request = None

        async def capture_request(route):
            nonlocal captured_request
            captured_request = route.request.post_data_json
            await route.continue_()

        await page.route("**/api/v2/task/submit", capture_request)
        await page.click('button:has-text("Submit")')

        # Verify special characters preserved
        assert captured_request["input"] == special_input

    async def test_form_max_input_length(self, page, form_helper):
        """Form enforces max input length."""
        max_length = 10000  # Typical limit
        too_long_input = "x" * (max_length + 1)

        await page.goto("http://localhost:8765/console/app/tasks")

        # Fill with text longer than max
        await page.fill('textarea[name="taskInput"]', too_long_input)

        # Should show error or truncate
        error_shown = await page.query_selector('text=exceeds maximum')
        input_value = await page.input_value('textarea[name="taskInput"]')

        # Either error shown OR input truncated
        assert error_shown is not None or len(input_value) <= max_length

    async def test_form_select_dropdown_options(self, page):
        """Form dropdown has expected options."""
        await page.goto("http://localhost:8765/console/app/tasks")

        # Get all options from dropdown
        options = await page.query_selector_all(
            'select[name="taskType"] > option'
        )
        option_values = [
            await opt.get_attribute("value") for opt in options
        ]

        # Verify expected options exist
        expected_options = ["qa", "analysis", "design", "implementation"]
        for expected in expected_options:
            assert expected in option_values, f"Missing option: {expected}"

    async def test_form_accessibility_labels(self, page):
        """Form has proper ARIA labels for accessibility."""
        await page.goto("http://localhost:8765/console/app/tasks")

        # Check textarea has label
        textarea = await page.query_selector('textarea[name="taskInput"]')
        aria_label = await textarea.get_attribute("aria-label")
        assert aria_label is not None, "Textarea missing aria-label"

        # Check select has label
        select = await page.query_selector('select[name="taskType"]')
        aria_label = await select.get_attribute("aria-label")
        assert aria_label is not None, "Select missing aria-label"

    async def test_form_loading_state(self, page, screenshot_helper):
        """Form shows loading indicator during submission."""
        await page.goto("http://localhost:8765/console/app/tasks")

        # Slow down network to observe loading state
        await page.route("**/api/v2/task/submit", lambda route: None)  # Stall request

        await page.fill('textarea[name="taskInput"]', "Test input")
        await page.click('button:has-text("Submit")')

        # Loading spinner should be visible
        spinner = await page.query_selector('spinner, [aria-busy="true"]')
        assert spinner is not None, "No loading indicator shown"

        # Submit button should be disabled
        submit_button = await page.query_selector('button:has-text("Submit")')
        is_disabled = await submit_button.is_disabled()
        assert is_disabled is True, "Submit button should be disabled during submission"

        await screenshot_helper.take_on_failure("loading_state")

    async def test_form_error_message_display(self, page):
        """Form displays API errors clearly."""
        await page.goto("http://localhost:8765/console/app/tasks")

        # Mock API to return error
        async def mock_error(route):
            await route.abort("failed")

        await page.route("**/api/v2/task/submit", mock_error)

        # Submit form
        await page.fill('textarea[name="taskInput"]', "Test")
        await page.click('button:has-text("Submit")')

        # Error message should appear
        await page.wait_for_selector('role=alert', timeout=5000)
        error_text = await page.text_content('role=alert')
        assert error_text is not None
        assert len(error_text) > 0
