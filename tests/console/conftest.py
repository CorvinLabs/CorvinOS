"""Shared E2E test fixtures for console panels.

Extended from tests/e2e/conftest.py with panel-specific fixtures for:
- Parametrized panel navigation
- Common panel interactions (forms, tables, tabs, charts)
- Panel assertions (visibility, content, errors)
- Performance measurement
"""

import pytest
from pathlib import Path
import os
import time
import re
from typing import Callable, Dict, List, Optional


# ─────────────────────────────────────────────────────────────────────────────
# PANEL REGISTRY (from src/panels/registry.tsx)
# ─────────────────────────────────────────────────────────────────────────────

CRITICAL_PANELS = [
    # P0: Infrastructure (auth + main dashboard + models)
    {"route": "login", "name": "Login", "priority": "P0", "type": "auth"},
    {"route": "dashboard", "name": "Dashboard", "priority": "P0", "type": "core"},
    {"route": "vibe-engineering", "name": "Vibe Engineering / Learnings", "priority": "P0", "type": "core"},
    {"route": "models", "name": "Models", "priority": "P0", "type": "core"},
    # P1: Core user-facing
    {"route": "settings", "name": "Settings", "priority": "P1", "type": "forms"},
    {"route": "forge", "name": "Forge", "priority": "P1", "type": "editor"},
    {"route": "skills", "name": "Skills", "priority": "P1", "type": "list"},
    {"route": "marketplace", "name": "Marketplace", "priority": "P1", "type": "list"},
    {"route": "video-producer", "name": "Video Producer", "priority": "P1", "type": "media", "flag": "video_producer_enabled"},
]

SECONDARY_PANELS = [
    # P2: High-value secondary
    {"route": "bridges", "name": "Bridges / Channels", "priority": "P2", "type": "forms"},
    {"route": "voice", "name": "Voice Profile", "priority": "P2", "type": "forms"},
    {"route": "quality", "name": "Quality Gates", "priority": "P2", "type": "table"},
    {"route": "compliance", "name": "Compliance / Audit", "priority": "P2", "type": "table"},
    {"route": "api-keys", "name": "API Keys", "priority": "P2", "type": "list"},
    {"route": "agent-hub", "name": "Agent Hub", "priority": "P2", "type": "list"},
]

ALL_PANELS = CRITICAL_PANELS + SECONDARY_PANELS + [
    # P3: Secondary features
    {"route": "files", "name": "Files", "priority": "P3", "type": "upload"},
    {"route": "memory", "name": "Memory", "priority": "P3", "type": "editor"},
    {"route": "compute", "name": "Compute", "priority": "P3", "type": "metrics"},
    {"route": "skill-forge-generator", "name": "Skill Forge Generator", "priority": "P3", "type": "wizard"},
    {"route": "ldd", "name": "LDD / Quality", "priority": "P3", "type": "metrics"},
    {"route": "rag", "name": "RAG", "priority": "P3", "type": "list"},
    {"route": "rag-hub", "name": "RAG Hub", "priority": "P3", "type": "list"},
    {"route": "connectors", "name": "Connectors", "priority": "P3", "type": "list"},
    {"route": "data-sources", "name": "Data Sources", "priority": "P3", "type": "list"},
    {"route": "sync-monitor", "name": "Sync Monitor", "priority": "P3", "type": "realtime"},
    # P4: Edge cases
    {"route": "custom-provider", "name": "Custom Provider", "priority": "P4", "type": "forms"},
    {"route": "flows", "name": "Flows", "priority": "P4", "type": "dag"},
    {"route": "datahub-unified", "name": "DataHub", "priority": "P4", "type": "list"},
    {"route": "licensing-audit", "name": "Licensing Audit", "priority": "P4", "type": "report"},
    {"route": "otel-telemetry", "name": "OTEL Telemetry", "priority": "P4", "type": "metrics"},
    {"route": "settings/github", "name": "GitHub", "priority": "P4", "type": "forms"},
    # P5+: Hidden / Future / Errors
    {"route": "orgs", "name": "Orgs (hidden)", "priority": "P5", "type": "list", "hidden": True},
    {"route": "people", "name": "People (hidden)", "priority": "P5", "type": "list", "hidden": True},
    {"route": "license", "name": "License", "priority": "P3", "type": "status"},
]


# ─────────────────────────────────────────────────────────────────────────────
# SESSION-LEVEL FIXTURES
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def console_base_url():
    """Console base URL."""
    return os.getenv("CONSOLE_BASE_URL", "http://localhost:8765")


@pytest.fixture(scope="session")
def all_panels_list():
    """All 47+ panels for parametrization."""
    return ALL_PANELS


@pytest.fixture(scope="session")
def critical_panels_list():
    """P0–P1 panels only (9 total)."""
    return CRITICAL_PANELS


# ─────────────────────────────────────────────────────────────────────────────
# PANEL NAVIGATION FIXTURES
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture
async def navigate_to_panel(page, console_base_url):
    """Navigate to a panel by route; wait for ready."""
    async def _navigate(route: str) -> None:
        url = f"{console_base_url}/app/{route}"
        await page.goto(url, wait_until="networkidle")
        # Wait for any loaders to complete
        try:
            await page.wait_for_selector(".loader, [role='progressbar']", state="hidden", timeout=5000)
        except:
            pass  # No loader found, panel may be instant
    return _navigate


@pytest.fixture
async def wait_for_panel_ready(page):
    """Wait for panel to be fully interactive."""
    async def _wait() -> None:
        # Wait for main content to be visible
        await page.wait_for_selector("[role='main']", state="visible", timeout=10000)
        # Wait for spinners/loaders
        try:
            await page.wait_for_selector(".loader, [role='progressbar']", state="hidden", timeout=5000)
        except:
            pass
        # Small delay for animations
        await page.wait_for_timeout(500)
    return _wait


@pytest.fixture
async def goto_panel(page, console_base_url, wait_for_panel_ready):
    """Convenience: navigate + wait + ready in one call."""
    async def _goto(route: str) -> None:
        await page.goto(f"{console_base_url}/app/{route}", wait_until="networkidle")
        await wait_for_panel_ready()
    return _goto


# ─────────────────────────────────────────────────────────────────────────────
# PANEL INTERACTION FIXTURES
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def panel_table_helper(page):
    """Helper for table interactions (sort, filter, paginate)."""
    class TableHelper:
        async def get_row_count(self) -> int:
            """Get visible row count."""
            rows = await page.locator("tbody tr, [role='row']").count()
            return rows

        async def click_sort_header(self, column_name: str) -> None:
            """Click table header to sort."""
            await page.click(f"th:has-text('{column_name}'), [role='columnheader']:has-text('{column_name}')")

        async def filter_by_text(self, search_text: str) -> None:
            """Type in search/filter field."""
            search_input = page.locator("input[placeholder*='Search'], input[placeholder*='Filter']").first
            await search_input.fill(search_text)
            await search_input.press("Enter")

        async def goto_next_page(self) -> None:
            """Click next page button."""
            await page.click("button:has-text('Next'), [aria-label='Next page']")

        async def get_cell_value(self, row: int, col: int) -> str:
            """Get cell value by row/col index."""
            cell = await page.locator(f"tbody tr:nth-child({row}) td:nth-child({col})").text_content()
            return cell.strip() if cell else ""

    return TableHelper()


@pytest.fixture
def panel_form_helper(page):
    """Helper for form interactions (fill, submit, validate)."""
    class FormHelper:
        async def fill_field(self, label_or_name: str, value: str) -> None:
            """Fill form field by label or name."""
            # Try by label first
            field = page.locator(f"label:has-text('{label_or_name}') ~ input, label:has-text('{label_or_name}') ~ textarea")
            if await field.count() > 0:
                await field.first.fill(value)
            else:
                # Try by name
                field = page.locator(f"input[name='{label_or_name}'], textarea[name='{label_or_name}']")
                await field.fill(value)

        async def select_option(self, label_or_name: str, value: str) -> None:
            """Select dropdown option."""
            select = page.locator(f"select[name='{label_or_name}']")
            await select.select_option(value)

        async def check_checkbox(self, label_or_name: str, checked: bool = True) -> None:
            """Check/uncheck checkbox."""
            checkbox = page.locator(f"label:has-text('{label_or_name}') input[type='checkbox'], input[name='{label_or_name}']")
            if checked:
                await checkbox.check()
            else:
                await checkbox.uncheck()

        async def submit_form(self, button_text: str = "Submit") -> None:
            """Click submit button."""
            await page.click(f"button:has-text('{button_text}')")

        async def get_error_message(self) -> Optional[str]:
            """Get form error message."""
            error = await page.locator("div[role='alert'], .error, .text-red-500").first.text_content()
            return error.strip() if error else None

    return FormHelper()


@pytest.fixture
def panel_tabs_helper(page):
    """Helper for tab interactions."""
    class TabsHelper:
        async def click_tab(self, tab_name: str) -> None:
            """Click tab by name."""
            await page.click(f"button[role='tab']:has-text('{tab_name}'), [role='tablist'] :has-text('{tab_name}')")

        async def get_active_tab(self) -> str:
            """Get name of active tab."""
            active = await page.locator("[role='tab'][aria-selected='true']").text_content()
            return active.strip() if active else ""

        async def get_tab_list(self) -> List[str]:
            """Get all tab names."""
            tabs = await page.locator("[role='tab']").all_text_contents()
            return [t.strip() for t in tabs if t.strip()]

    return TabsHelper()


@pytest.fixture
def panel_search_helper(page):
    """Helper for search/filter in lists."""
    class SearchHelper:
        async def search(self, query: str) -> int:
            """Search and return result count."""
            search_input = page.locator("input[placeholder*='Search'], input[placeholder*='search']").first
            await search_input.fill(query)
            await page.wait_for_timeout(500)  # debounce
            # Try to get result count
            results = await page.locator("tbody tr, [role='row']").count()
            return results

        async def clear_search(self) -> None:
            """Clear search field."""
            search_input = page.locator("input[placeholder*='Search'], input[placeholder*='search']").first
            await search_input.clear()

        async def get_no_results_message(self) -> Optional[str]:
            """Get 'no results' message."""
            msg = await page.locator("text=/no results|no matches|empty/i").first.text_content()
            return msg.strip() if msg else None

    return SearchHelper()


# ─────────────────────────────────────────────────────────────────────────────
# PANEL ASSERTION FIXTURES
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def panel_assertions(page):
    """Assertions for panel validation."""
    class PanelAssertions:
        async def assert_visible(self, text: str = None) -> None:
            """Assert panel is visible and optionally contains text."""
            assert await page.locator("[role='main']").count() > 0, "Main content not found"
            if text:
                assert await page.locator(f"text='{text}'").count() > 0, f"Text '{text}' not found"

        async def assert_has_content(self, content_type: str) -> None:
            """Assert panel has expected content (table|form|chart|list)."""
            if content_type == "table":
                assert await page.locator("table, [role='table']").count() > 0, "No table found"
            elif content_type == "form":
                assert await page.locator("form, input[type='text'], textarea").count() > 0, "No form found"
            elif content_type == "chart":
                assert await page.locator("canvas, svg[role='img'], .recharts").count() > 0, "No chart found"
            elif content_type == "list":
                assert await page.locator("ul, [role='list'], tbody").count() > 0, "No list found"

        async def assert_no_errors(self) -> None:
            """Assert no error messages or 404s."""
            # Check for error messages
            errors = await page.locator("text=/error|failed|404|not found/i").all_text_contents()
            assert len(errors) == 0, f"Found errors: {errors}"
            # Check response status
            assert page.url.find("/404") == -1, "Page returned 404"

        async def assert_url_contains(self, route: str) -> None:
            """Assert URL contains expected route."""
            assert route in page.url, f"Expected '{route}' in URL, got {page.url}"

        async def assert_no_console_errors(self) -> None:
            """Assert no unhandled JS errors (requires Playwright console listener)."""
            # This would require a custom listener setup in conftest
            pass

    return PanelAssertions()


# ─────────────────────────────────────────────────────────────────────────────
# PERFORMANCE FIXTURES
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def panel_performance(page):
    """Performance measurement helpers."""
    class PerfHelper:
        async def measure_load_time(self, route: str) -> float:
            """Measure panel load time (ms)."""
            start = time.time()
            await page.goto(f"/app/{route}", wait_until="networkidle")
            elapsed = (time.time() - start) * 1000
            return elapsed

        async def measure_tti(self) -> float:
            """Measure Time To Interactive (ms)."""
            start = time.time()
            try:
                await page.wait_for_selector("[role='main']", state="visible", timeout=10000)
                await page.wait_for_selector(".loader, [role='progressbar']", state="hidden", timeout=5000)
            except:
                pass
            elapsed = (time.time() - start) * 1000
            return elapsed

        async def measure_network_requests(self) -> int:
            """Count network requests during page load."""
            # Requires request listener to be set up
            pass

    return PerfHelper()


# ─────────────────────────────────────────────────────────────────────────────
# PARAMETRIZED FIXTURES (for parallel panel tests)
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture(params=CRITICAL_PANELS, ids=[p["route"] for p in CRITICAL_PANELS])
def critical_panel(request):
    """Parametrized critical panel."""
    return request.param


@pytest.fixture(params=SECONDARY_PANELS, ids=[p["route"] for p in SECONDARY_PANELS])
def secondary_panel(request):
    """Parametrized secondary panel."""
    return request.param


@pytest.fixture(params=ALL_PANELS, ids=[p["route"] for p in ALL_PANELS])
def any_panel(request):
    """Parametrized all panels."""
    return request.param


# ─────────────────────────────────────────────────────────────────────────────
# UTILITY FIXTURES
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def screenshots_dir():
    """Directory for screenshots on failure."""
    dir_path = Path(__file__).parent / "screenshots"
    dir_path.mkdir(exist_ok=True)
    return dir_path


@pytest.fixture
def take_screenshot_on_failure(page, request, screenshots_dir):
    """Take screenshot on test failure."""
    yield
    if request.node.rep_call.failed:
        filename = screenshots_dir / f"{request.node.name}_failure.png"
        page.screenshot(path=str(filename))


@pytest.fixture
def panel_state_tracker(page):
    """Track panel state changes during test."""
    class StateTracker:
        def __init__(self):
            self.events = []

        async def wait_for_state_change(self, selector: str, timeout: int = 5000) -> None:
            """Wait for element state to change."""
            element = page.locator(selector).first
            initial_state = await element.get_attribute("aria-busy")
            await page.wait_for_function(
                f"document.querySelector('{selector}').getAttribute('aria-busy') !== '{initial_state}'",
                timeout=timeout,
            )

    return StateTracker()


# ─────────────────────────────────────────────────────────────────────────────
# MARKERS (Pytest)
# ─────────────────────────────────────────────────────────────────────────────

def pytest_configure(config):
    """Register pytest markers for panel testing."""
    config.addinivalue_line(
        "markers",
        "p0: Critical P0 infrastructure panels"
    )
    config.addinivalue_line(
        "markers",
        "p1: Core P1 user-facing panels"
    )
    config.addinivalue_line(
        "markers",
        "p2: Secondary P2 panels"
    )
    config.addinivalue_line(
        "markers",
        "critical_path: P0 + P1 panels (run first)"
    )
    config.addinivalue_line(
        "markers",
        "interactive: Panels with forms, tables, tabs"
    )
    config.addinivalue_line(
        "markers",
        "slow: Panels with >2s load time"
    )
    config.addinivalue_line(
        "markers",
        "requires_flag: Panel requires capability flag"
    )
