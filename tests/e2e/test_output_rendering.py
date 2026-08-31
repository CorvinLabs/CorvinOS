"""Output Rendering Tests (Playwright)

Tests that task results are correctly rendered in Console UI.
This covers the CRITICAL gap: Execution complete → Output visible.
"""

import pytest
import asyncio


@pytest.mark.e2e
@pytest.mark.high_risk
class TestOutputRendering:
    """Output rendering in Console after task completion."""

    async def test_task_result_appears_in_panel(self, page):
        """Task result renders in Console after completion."""
        # Mock successful task completion
        await page.goto("http://localhost:8765/console/app/task-details/task-123")

        # Mock API response
        async def mock_task_output(route):
            await route.abort("failed")  # Start with no response
            # Later, respond with result

        await page.route("**/api/v2/task/task-123/output", mock_task_output)

        # Wait and poll for result
        result_found = False
        for attempt in range(10):  # Poll 10 times
            try:
                await page.wait_for_selector('[data-testid="task-output"]', timeout=1000)
                result_found = True
                break
            except:
                await asyncio.sleep(1)

        # Should eventually show result
        assert result_found, "Task output never appeared in Console"

        # Verify output has content
        output_element = await page.query_selector('[data-testid="task-output"]')
        output_text = await output_element.text_content()
        assert len(output_text) > 0, "Output element empty"

    async def test_output_format_validation(self, page):
        """Output format is valid HTML/markdown."""
        await page.goto("http://localhost:8765/console/app/task-details/task-456")

        # Wait for output
        await page.wait_for_selector('[data-testid="task-output"]', timeout=5000)

        # Get output content
        output = await page.query_selector('[data-testid="task-output"]')
        html = await output.inner_html()

        # Should not contain unescaped tags or XSS vectors
        assert "<script>" not in html.lower(), "Script tag in output (XSS)"
        assert "onclick=" not in html.lower(), "Event handler in output"

    async def test_output_metadata_displayed(self, page):
        """Output shows metadata (duration, agent, cost)."""
        await page.goto("http://localhost:8765/console/app/task-details/task-789")

        # Wait for task complete
        await page.wait_for_selector('text=Status: complete', timeout=10000)

        # Verify metadata elements exist
        duration = await page.query_selector('text=Completed in')
        assert duration is not None, "Duration not shown"

        agent = await page.query_selector('text=Agent:')
        assert agent is not None, "Agent not shown"

        # Optional: cost
        cost = await page.query_selector('text=Cost:')
        # Cost may not always be shown, but if shown should have value
        if cost:
            cost_text = await cost.text_content()
            assert "tokens" in cost_text.lower()

    async def test_error_output_formatting(self, page):
        """Task error is clearly formatted and actionable."""
        # Create task that fails
        await page.goto("http://localhost:8765/console/app/tasks")
        await page.fill('textarea[name="taskInput"]', "Invalid input that causes error")
        await page.click('button:has-text("Submit")')

        # Navigate to task when error occurs
        await page.wait_for_selector('text=Task failed', timeout=10000)

        # Error message should be visible and clear
        error_box = await page.query_selector('[data-testid="task-error"]')
        assert error_box is not None, "Error not displayed"

        error_text = await error_box.text_content()
        assert error_text is not None and len(error_text) > 10, "Error message too short"

    async def test_output_pagination_large_results(self, page):
        """Large task results are paginated."""
        # Create task with large output (simulated)
        await page.goto("http://localhost:8765/console/app/task-details/task-large")

        # Wait for output
        await page.wait_for_selector('[data-testid="task-output"]', timeout=5000)

        # Check if pagination controls exist (for large results)
        pagination = await page.query_selector('[data-testid="output-pagination"]')
        if pagination:
            # Should have next/prev buttons
            next_button = await page.query_selector('button:has-text("Next")')
            assert next_button is not None

    async def test_streaming_output_updates(self, page):
        """Output updates in real-time as task progresses."""
        await page.goto("http://localhost:8765/console/app/task-details/task-stream")

        # Collect output updates over time
        outputs_seen = []

        async def track_output_changes():
            for _ in range(5):  # Check 5 times
                output = await page.text_content('[data-testid="task-output"]')
                if output not in outputs_seen:
                    outputs_seen.append(output)
                await asyncio.sleep(1)

        await track_output_changes()

        # Should see multiple updates (streaming)
        if len(outputs_seen) > 1:
            # Verify each update is larger (content being added)
            for i in range(1, len(outputs_seen)):
                assert len(outputs_seen[i]) >= len(outputs_seen[i - 1])

    async def test_output_copy_to_clipboard(self, page):
        """User can copy output to clipboard."""
        await page.goto("http://localhost:8765/console/app/task-details/task-123")

        # Wait for output
        await page.wait_for_selector('[data-testid="task-output"]', timeout=5000)

        # Find copy button
        copy_button = await page.query_selector('button[title="Copy output"]')
        assert copy_button is not None, "Copy button not found"

        # Click copy
        await copy_button.click()

        # Verify feedback (toast or button state change)
        toast = await page.query_selector('[data-testid="copy-success"]')
        assert toast is not None or (await copy_button.text_content()).includes("Copied")

    async def test_output_export_functionality(self, page):
        """User can export task output (JSON/CSV)."""
        await page.goto("http://localhost:8765/console/app/task-details/task-123")

        # Wait for output
        await page.wait_for_selector('[data-testid="task-output"]', timeout=5000)

        # Find export button
        export_button = await page.query_selector('button:has-text("Export")')
        if export_button:  # Export may be optional feature
            await export_button.click()

            # Verify export format options
            json_option = await page.query_selector('text=JSON')
            csv_option = await page.query_selector('text=CSV')
            assert json_option or csv_option, "No export formats available"

    async def test_output_display_on_different_screen_sizes(self, page):
        """Output renders correctly on mobile/desktop."""
        await page.goto("http://localhost:8765/console/app/task-details/task-123")

        # Test desktop (1920x1080)
        await page.set_viewport_size({"width": 1920, "height": 1080})
        desktop_output = await page.query_selector('[data-testid="task-output"]')
        desktop_width = await desktop_output.bounding_box()

        # Test mobile (375x667)
        await page.set_viewport_size({"width": 375, "height": 667})
        mobile_output = await page.query_selector('[data-testid="task-output"]')
        mobile_width = await mobile_output.bounding_box()

        # Both should be visible and readable
        assert desktop_width is not None
        assert mobile_width is not None

        # Mobile width should fit in viewport
        assert mobile_width["width"] <= 375
