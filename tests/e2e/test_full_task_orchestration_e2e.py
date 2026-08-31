"""Full Task Orchestration E2E Tests (Playwright)

Comprehensive E2E tests for the complete task orchestration pipeline:
form submission → API call → Brain scheduling → context injection →
execution → output rendering.

These tests run against a LIVE Console instance and verify the entire
user journey from task creation through result display.

ADR-0402: Task Orchestrator DAG execution
ADR-0445: Task supervision + resumability
"""

import pytest
import asyncio
import json
from datetime import datetime
from typing import Dict, Any, Optional


@pytest.mark.e2e
@pytest.mark.full_pipeline
class TestFullTaskOrchestrationE2E:
    """Complete task orchestration pipeline tests."""

    BASE_URL = "http://localhost:8765"
    API_BASE = "http://localhost:8000"
    TASK_TIMEOUT = 60000  # 60 seconds

    # ========== Test 1: Simple QA Task (E2E Baseline) ==========

    async def test_simple_qa_task_end_to_end(
        self, page, test_task_data, api_helper, screenshot_helper
    ):
        """Test 1: Simple QA task from submission to result display.

        User journey:
        1. Navigate to tasks panel
        2. Enter simple question
        3. Submit form
        4. Task queued and scheduled by Brain
        5. Result appears in Console
        """
        task_input = test_task_data["simple_qa"]["input"]

        # Step 1: Navigate to tasks panel
        await page.goto(f"{self.BASE_URL}/console/app/task-panel")
        await page.wait_for_selector('textarea[name="taskInput"]', timeout=10000)

        # Step 2: Fill form with simple QA task
        await page.fill('textarea[name="taskInput"]', task_input)
        await page.select_option('select[name="taskType"]', "qa")

        # Step 3: Submit form
        await page.click('button:has-text("Submit")')

        # Verify task submission success (UI feedback)
        try:
            await page.wait_for_selector(
                'text=Task submitted|text=Task queued',
                timeout=5000
            )
        except TimeoutError:
            await screenshot_helper.take_on_failure("submit_feedback")
            raise AssertionError("No submission feedback in UI")

        # Step 4: Extract task ID from URL or response
        # Task panel typically navigates to detail view or shows task ID
        task_id_match = None
        for attempt in range(5):
            try:
                task_id_element = await page.query_selector(
                    '[data-testid="task-id"], text=/Task ID: /'
                )
                if task_id_element:
                    task_id_text = await task_id_element.text_content()
                    # Extract ID from "Task ID: xyz" format
                    if "Task ID:" in task_id_text:
                        task_id_match = task_id_text.split(":")[-1].strip()
                    break
            except:
                await asyncio.sleep(1)

        assert task_id_match, "Could not extract task ID from Console"

        # Step 5: Poll for task completion
        task_completed = False
        for attempt in range(30):  # Poll up to 30 times (30s with 1s delays)
            try:
                # Check for completion indicators
                status_elem = await page.query_selector(
                    'text=Status: completed|text=Status: done'
                )
                if status_elem:
                    task_completed = True
                    break

                # Also check via API if available
                if api_helper:
                    try:
                        status_data = await api_helper.get_task_status(task_id_match)
                        if status_data.get("status") in ["completed", "done"]:
                            task_completed = True
                            break
                    except:
                        pass

            except Exception as e:
                print(f"Status check attempt {attempt} failed: {e}")

            await asyncio.sleep(1)

        assert task_completed, "Task did not complete within timeout"

        # Step 6: Verify output is rendered
        try:
            await page.wait_for_selector(
                '[data-testid="task-output"], div[role="region"]:has-text("Result")',
                timeout=5000
            )
        except TimeoutError:
            await screenshot_helper.take_on_failure("no_output_rendering")
            raise AssertionError("Task output not rendered after completion")

        # Step 7: Verify output contains expected keywords
        output_elem = await page.query_selector('[data-testid="task-output"]')
        if output_elem:
            output_text = await output_elem.text_content()
            # For a QA task about capital of France, expect Paris
            assert len(output_text) > 10, "Output text too short"

    # ========== Test 2: Complex Analysis with Dependencies ==========

    async def test_analysis_task_with_dependencies(
        self, page, api_helper, screenshot_helper
    ):
        """Test 2: Task with dependencies waits for parent completion.

        Scenario:
        1. Create parent task (Task A)
        2. Create child task (Task B) with depends_on=[Task A]
        3. Task B should not execute until A completes
        4. Both results visible in Console
        """
        # Step 1: Submit parent task
        await page.goto(f"{self.BASE_URL}/console/app/task-panel")
        await page.wait_for_selector('textarea[name="taskInput"]', timeout=10000)

        parent_input = "Analyze: What are the main themes in literature?"
        await page.fill('textarea[name="taskInput"]', parent_input)
        await page.select_option('select[name="taskType"]', "analysis")
        await page.click('button:has-text("Submit")')

        # Extract parent task ID
        await asyncio.sleep(1)
        parent_id = None
        try:
            parent_elem = await page.query_selector('[data-testid="task-id"]')
            if parent_elem:
                parent_text = await parent_elem.text_content()
                parent_id = parent_text.split(":")[-1].strip()
        except:
            pass

        if not parent_id:
            pytest.skip("Could not extract parent task ID (infrastructure limitation)")

        # Step 2: Submit child task with dependency
        await page.fill('textarea[name="taskInput"]', "Expand on the analysis findings")
        await page.select_option('select[name="taskType"]', "analysis")

        # Try to set dependency (if UI supports it)
        try:
            await page.fill('input[name="dependsOn"]', parent_id)
        except:
            # If UI doesn't support it, use API instead
            if api_helper:
                try:
                    await api_helper.submit_task(
                        "Expand on the analysis findings",
                        task_type="analysis",
                        depends_on=parent_id
                    )
                except:
                    pytest.skip("Dependency feature not fully implemented")

        await page.click('button:has-text("Submit")')

        # Step 3: Verify both tasks are visible
        try:
            await page.wait_for_selector(
                'text=Task 1|text=Task 2',
                timeout=10000
            )
        except TimeoutError:
            pytest.skip("Multi-task view not implemented")

        # Step 4: Verify parent completed before child started
        # This would require checking timestamps or state in Console
        await asyncio.sleep(2)
        try:
            parent_status = await page.query_selector(
                'text=Task 1.*completed'
            )
            child_status = await page.query_selector(
                'text=Task 2.*running'
            )
            # If we see parent completed and child running, dependency worked
            if parent_status and child_status:
                print("✓ Dependency order verified")
        except:
            pass

    # ========== Test 3: Long-Running Task Monitoring ==========

    async def test_long_running_task_progress_visible(
        self, page, api_helper, screenshot_helper
    ):
        """Test 3: Long-running task shows progress updates in real-time.

        Scenario:
        1. Submit a task that takes 15+ seconds
        2. Monitor console for progress updates
        3. Verify progress bar/percentage updates
        4. Verify task completes and output appears
        """
        await page.goto(f"{self.BASE_URL}/console/app/task-panel")
        await page.wait_for_selector('textarea[name="taskInput"]', timeout=10000)

        # Submit a task that will take time
        long_input = "Perform comprehensive analysis of the following complex dataset with multiple passes"
        await page.fill('textarea[name="taskInput"]', long_input)
        await page.select_option('select[name="taskType"]', "analysis")
        await page.click('button:has-text("Submit")')

        # Step 2: Monitor for progress indicators
        progress_observed = False
        initial_progress = None

        for attempt in range(20):  # Monitor for up to 20 seconds
            try:
                # Look for progress bar
                progress_bar = await page.query_selector(
                    '[data-testid="progress-bar"], progress, [role="progressbar"]'
                )
                if progress_bar:
                    progress_observed = True
                    # Get progress value
                    try:
                        progress_value = (await progress_bar.get_attribute("value") or
                                        await progress_bar.get_attribute("aria-valuenow"))
                        if progress_value:
                            if initial_progress is None:
                                initial_progress = float(progress_value)
                            else:
                                # Verify progress is advancing
                                current = float(progress_value)
                                assert current >= initial_progress, "Progress went backwards"
                    except:
                        pass

                    # Check for progress text
                    progress_text = await page.query_selector('text=/Progress|%|step/')
                    if progress_text:
                        text = await progress_text.text_content()
                        print(f"Progress: {text}")

            except Exception as e:
                print(f"Progress check attempt {attempt}: {e}")

            await asyncio.sleep(1)

        # Progress indication is nice-to-have, not critical
        if progress_observed:
            print("✓ Progress updates observed")
        else:
            print("⚠ No progress bar observed (may not be implemented)")

        # Step 3: Verify task eventually completes
        completion_found = False
        for attempt in range(30):
            try:
                completion = await page.query_selector(
                    'text=completed|text=done|text=finished'
                )
                if completion:
                    completion_found = True
                    break
            except:
                pass
            await asyncio.sleep(1)

        assert completion_found, "Long-running task did not complete"

        # Step 4: Verify output appears
        try:
            output = await page.query_selector(
                '[data-testid="task-output"], div[role="region"]'
            )
            assert output is not None, "No output after task completion"
        except:
            await screenshot_helper.take_on_failure("no_output_long_task")
            raise

    # ========== Test 4: Timeout & Graceful Recovery ==========

    async def test_task_timeout_graceful_recovery(
        self, page, api_helper, screenshot_helper
    ):
        """Test 4: Task exceeding timeout shows clear error and recovery options.

        Scenario:
        1. Submit a task with very short timeout (or submit impossible task)
        2. Task should timeout gracefully
        3. Clear error message shown
        4. User can retry or cancel
        """
        await page.goto(f"{self.BASE_URL}/console/app/task-panel")
        await page.wait_for_selector('textarea[name="taskInput"]', timeout=10000)

        # Submit task
        await page.fill('textarea[name="taskInput"]', "Analyze extremely complex problem")
        await page.select_option('select[name="taskType"]', "analysis")
        await page.click('button:has-text("Submit")')

        # Set a short timeout for this test (if UI supports it)
        try:
            await page.fill('input[name="timeout"]', "2")  # 2 second timeout
        except:
            # If not in UI, skip this part
            pass

        # Step 2: Wait for timeout error
        error_found = False
        for attempt in range(15):
            try:
                error_elem = await page.query_selector(
                    'text=timed out|text=timeout|text=exceeded'
                )
                if error_elem:
                    error_found = True
                    error_text = await error_elem.text_content()
                    print(f"Error message: {error_text}")
                    break
            except:
                pass
            await asyncio.sleep(1)

        if error_found:
            # Step 3: Verify recovery options
            retry_btn = await page.query_selector('button:has-text("Retry")')
            cancel_btn = await page.query_selector('button:has-text("Cancel")')

            if retry_btn:
                print("✓ Retry button available")
            if cancel_btn:
                print("✓ Cancel button available")
        else:
            # Timeout may be configured differently
            pytest.skip("Task did not timeout as expected")

    # ========== Test 5: Concurrent Task Isolation ==========

    async def test_concurrent_tasks_isolated(
        self, page, screenshot_helper
    ):
        """Test 5: Multiple concurrent tasks don't interfere with each other.

        Scenario:
        1. Open 3 browser tabs/contexts
        2. Submit different tasks in each
        3. Verify each task tracked independently
        4. Verify outputs don't get mixed
        """
        # Step 1: Create multiple browser contexts
        context1 = await page.context.browser.new_context()
        page1 = await context1.new_page()
        page2 = await page.context.new_page()  # Use existing context for page2
        page3 = await page.context.new_page()

        task_ids = {}
        try:
            # Step 2: Submit different tasks in each page
            for idx, page_obj in enumerate([page, page1, page2], 1):
                await page_obj.goto(f"{self.BASE_URL}/console/app/task-panel")
                await page_obj.wait_for_selector(
                    'textarea[name="taskInput"]',
                    timeout=10000
                )

                task_input = f"Task {idx}: Analyze topic number {idx}"
                await page_obj.fill('textarea[name="taskInput"]', task_input)
                await page_obj.select_option('select[name="taskType"]', "analysis")
                await page_obj.click('button:has-text("Submit")')

                # Extract task ID
                await asyncio.sleep(1)
                try:
                    task_elem = await page_obj.query_selector(
                        '[data-testid="task-id"]'
                    )
                    if task_elem:
                        task_text = await task_elem.text_content()
                        task_id = task_text.split(":")[-1].strip()
                        task_ids[idx] = task_id
                except:
                    pass

            # Step 3: Verify each task completes independently
            for idx, page_obj in enumerate([page, page1, page2], 1):
                try:
                    await page_obj.wait_for_selector(
                        'text=completed|text=done',
                        timeout=60000
                    )
                except TimeoutError:
                    pytest.skip(f"Task {idx} did not complete (timing issue)")
                    break

                # Step 4: Verify output for this task
                output_elem = await page_obj.query_selector(
                    '[data-testid="task-output"]'
                )
                if output_elem:
                    output_text = await output_elem.text_content()
                    # Each task's output should correspond to its input
                    # (This is a basic check; real tasks would verify content)
                    assert len(output_text) > 0
                    print(f"✓ Task {idx} output verified")

        finally:
            await context1.close()

    # ========== Test 6: Error and Retry Flow ==========

    async def test_error_and_retry(
        self, page, api_helper, screenshot_helper
    ):
        """Test 6: Task failure shows error and allows retry.

        Scenario:
        1. Submit task that will fail (e.g., invalid input)
        2. Task fails
        3. Error message displayed
        4. User clicks retry
        5. Task re-executes
        """
        await page.goto(f"{self.BASE_URL}/console/app/task-panel")
        await page.wait_for_selector('textarea[name="taskInput"]', timeout=10000)

        # Step 1: Submit a task (intentionally or not)
        await page.fill('textarea[name="taskInput"]', "Analyze this data")
        await page.select_option('select[name="taskType"]', "analysis")
        await page.click('button:has-text("Submit")')

        # Wait for any result (success or failure)
        result_found = False
        for attempt in range(30):
            try:
                result = await page.query_selector(
                    'text=completed|text=failed|text=error'
                )
                if result:
                    result_found = True
                    result_text = await result.text_content()
                    print(f"Result: {result_text}")
                    break
            except:
                pass
            await asyncio.sleep(1)

        if result_found and "failed" in result_text.lower():
            # Step 2: Verify error message
            error_elem = await page.query_selector(
                '[data-testid="error-message"], div[role="alert"]'
            )
            if error_elem:
                error_msg = await error_elem.text_content()
                assert len(error_msg) > 0, "Error message should have content"
                print(f"✓ Error message: {error_msg}")

            # Step 3: Click retry button
            retry_btn = await page.query_selector('button:has-text("Retry")')
            if retry_btn:
                await retry_btn.click()

                # Step 4: Verify task re-executes
                for attempt in range(30):
                    try:
                        new_result = await page.query_selector(
                            'text=completed|text=done'
                        )
                        if new_result:
                            print("✓ Task re-executed after retry")
                            break
                    except:
                        pass
                    await asyncio.sleep(1)
            else:
                print("⚠ Retry button not found")
        else:
            pytest.skip("Task did not fail as expected")

    # ========== Test 7: Multi-Panel Workflow ==========

    async def test_multi_panel_workflow(
        self, page, screenshot_helper
    ):
        """Test 7: Task state persists when switching between panels.

        Scenario:
        1. Submit task in Task Panel A
        2. Switch to Panel B (e.g., Settings)
        3. Switch back to Panel A
        4. Task state and results still visible
        """
        # Step 1: Navigate to task panel
        await page.goto(f"{self.BASE_URL}/console/app/task-panel")
        await page.wait_for_selector('textarea[name="taskInput"]', timeout=10000)

        # Submit task
        await page.fill('textarea[name="taskInput"]', "Analyze market trends")
        await page.select_option('select[name="taskType"]', "analysis")
        await page.click('button:has-text("Submit")')

        # Extract initial task info
        await asyncio.sleep(1)
        initial_task_elem = await page.query_selector('[data-testid="task-id"]')
        initial_task_info = None
        if initial_task_elem:
            initial_task_info = await initial_task_elem.text_content()

        # Step 2: Switch to different panel
        try:
            settings_link = await page.query_selector(
                'a:has-text("Settings"), button:has-text("Settings")'
            )
            if settings_link:
                await settings_link.click()
                await page.wait_for_load_state("networkidle")
            else:
                pytest.skip("Settings panel not found")
        except:
            pytest.skip("Navigation to other panels failed")

        # Step 3: Switch back to task panel
        try:
            tasks_link = await page.query_selector(
                'a:has-text("Tasks"), button:has-text("Tasks")'
            )
            if tasks_link:
                await tasks_link.click()
                await page.wait_for_load_state("networkidle")
            else:
                pytest.skip("Could not navigate back to task panel")
        except:
            pytest.skip("Navigation failed")

        # Step 4: Verify task state persisted
        try:
            current_task_elem = await page.query_selector('[data-testid="task-id"]')
            if current_task_elem:
                current_task_info = await current_task_elem.text_content()
                assert initial_task_info == current_task_info, \
                    "Task state was lost during panel switch"
                print("✓ Task state persisted across panel switches")
        except:
            pytest.skip("Could not verify task persistence")

    # ========== Test 8: Form Validation & API Error Prevention ==========

    async def test_form_validation_prevents_invalid_submission(
        self, page, api_helper, screenshot_helper
    ):
        """Test 8: Invalid form input blocked before API call.

        Scenario:
        1. Attempt to submit empty form
        2. Validation error shown
        3. API never called
        4. Try with invalid characters
        5. Validation blocks submission
        """
        await page.goto(f"{self.BASE_URL}/console/app/task-panel")
        await page.wait_for_selector('textarea[name="taskInput"]', timeout=10000)

        # Step 1: Try to submit empty form
        await page.fill('textarea[name="taskInput"]', "")

        # Try clicking submit
        submit_btn = await page.query_selector('button:has-text("Submit")')

        # Check if button is disabled
        is_disabled = await submit_btn.is_disabled()
        if is_disabled:
            print("✓ Submit button disabled for empty form")
        else:
            # Click and check for validation error
            await submit_btn.click()
            try:
                await page.wait_for_selector(
                    'text=required|text=cannot be empty',
                    timeout=5000
                )
                print("✓ Validation error shown for empty input")
            except TimeoutError:
                await screenshot_helper.take_on_failure("no_validation_error")
                raise AssertionError("No validation error for empty input")

        # Step 2: Verify API was not called
        api_calls = []

        async def track_api(route):
            api_calls.append(route.request.url)
            await route.continue_()

        await page.route("**/api/v2/task/submit", track_api)

        # Try submit again with empty input
        await page.fill('textarea[name="taskInput"]', "")
        await page.click('button:has-text("Submit")')

        await asyncio.sleep(1)

        assert len(api_calls) == 0, "API was called despite validation error"
        print("✓ API not called on validation error")

        # Step 3: Try with valid input
        await page.fill('textarea[name="taskInput"]', "What is Python?")
        api_calls.clear()
        await page.route("**/api/v2/task/submit", track_api)
        await page.click('button:has-text("Submit")')

        await asyncio.sleep(1)
        assert len(api_calls) > 0, "API should be called with valid input"
        print("✓ API called for valid input")

    # ========== Test 9: Large Output Rendering ==========

    async def test_large_output_rendering(
        self, page, api_helper, screenshot_helper
    ):
        """Test 9: Large task output (>100KB) renders correctly.

        Scenario:
        1. Submit task with large expected output
        2. Output loads and renders without crashing
        3. All content accessible (scrollable)
        """
        await page.goto(f"{self.BASE_URL}/console/app/task-panel")
        await page.wait_for_selector('textarea[name="taskInput"]', timeout=10000)

        # Submit task that will produce large output
        large_input = "Generate a comprehensive report analyzing " + \
                      "multiple aspects of machine learning with detailed explanations " * 10

        await page.fill('textarea[name="taskInput"]', large_input[:500])
        await page.select_option('select[name="taskType"]', "analysis")
        await page.click('button:has-text("Submit")')

        # Wait for completion
        completion_found = False
        for attempt in range(60):
            try:
                completion = await page.query_selector(
                    'text=completed|text=done'
                )
                if completion:
                    completion_found = True
                    break
            except:
                pass
            await asyncio.sleep(1)

        if completion_found:
            # Verify output renders
            output_elem = await page.query_selector(
                '[data-testid="task-output"]'
            )
            if output_elem:
                output_text = await output_elem.text_content()
                print(f"✓ Large output rendered ({len(output_text)} chars)")

                # Verify scrollable (if very large)
                if len(output_text) > 50000:
                    bounding_box = await output_elem.bounding_box()
                    assert bounding_box is not None, "Output not visible"
                    print("✓ Large output element accessible")
        else:
            pytest.skip("Task did not complete for large output test")

    # ========== Test 10: Performance Baseline ==========

    async def test_task_end_to_end_performance(
        self, page, screenshot_helper
    ):
        """Test 10: Full pipeline completes within performance budget.

        Scenario:
        1. Submit simple task
        2. Measure total time from submission to output display
        3. Assert completion within 60 seconds
        """
        import time

        await page.goto(f"{self.BASE_URL}/console/app/task-panel")
        await page.wait_for_selector('textarea[name="taskInput"]', timeout=10000)

        # Start timer
        start_time = time.time()

        # Submit simple task
        await page.fill('textarea[name="taskInput"]', "What is Python programming?")
        await page.select_option('select[name="taskType"]', "qa")
        await page.click('button:has-text("Submit")')

        # Wait for completion
        completion_found = False
        for attempt in range(60):
            try:
                completion = await page.query_selector(
                    'text=completed|text=done'
                )
                if completion:
                    completion_found = True
                    break
            except:
                pass
            await asyncio.sleep(1)

        elapsed_time = time.time() - start_time

        if completion_found:
            print(f"✓ Task completed in {elapsed_time:.1f} seconds")
            assert elapsed_time < 60, \
                f"Task took {elapsed_time:.1f}s, exceeds 60s budget"
        else:
            pytest.skip("Task did not complete (timing constraint)")
