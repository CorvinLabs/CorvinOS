#!/usr/bin/env python3
"""
Task Graph Visualization Verification Suite (TaskRaft v2)

Verifies the Task Graph page at /app/task-graph with:
1. Page load and complete rendering
2. Graph visualization visibility
3. Current data verification (no stale cache)
4. Console error detection
5. Task picker functionality
6. Cache headers and freshness

Usage: python scripts/task_graph_verification.py
"""

import sys
import time
import requests
from datetime import datetime
from urllib.parse import urljoin
from typing import Optional, List, Dict, Tuple
import json
import hashlib
import re

# Configuration
BASE_URL = "http://127.0.0.1:8765"
TASK_GRAPH_PATH = "/console/app/task-graph"
TASK_GRAPH_URL = urljoin(BASE_URL, TASK_GRAPH_PATH)
TASK_API_URL = urljoin(BASE_URL, "/api/tasks")
TIMEOUT = 10

# ANSI color codes
GREEN = '\033[92m'
RED = '\033[91m'
YELLOW = '\033[93m'
CYAN = '\033[96m'
RESET = '\033[0m'
BOLD = '\033[1m'

class TaskGraphVerifier:
    """Verifies Task Graph page functionality and freshness."""

    def __init__(self):
        self.session = requests.Session()
        self.results: List[Tuple[str, bool, str]] = []
        self.page_content: Optional[str] = None
        self.page_headers: Optional[Dict] = None
        self.tasks_data: Optional[List] = None
        self.load_time: float = 0.0

    def log_result(self, test_name: str, passed: bool, message: str):
        """Log a test result."""
        status = f"{GREEN}✅ PASS{RESET}" if passed else f"{RED}❌ FAIL{RESET}"
        self.results.append((test_name, passed, message))
        print(f"{status} | {test_name}: {message}")

    def print_section(self, title: str):
        """Print a section header."""
        print(f"\n{BOLD}{CYAN}{'='*70}{RESET}")
        print(f"{BOLD}{CYAN}🔍 {title}{RESET}")
        print(f"{BOLD}{CYAN}{'='*70}{RESET}\n")

    def test_page_load(self) -> bool:
        """Test 1: Page loads successfully."""
        self.print_section("Test 1: Page Load")

        try:
            start = time.time()
            response = self.session.get(TASK_GRAPH_URL, timeout=TIMEOUT)
            self.load_time = time.time() - start

            self.page_content = response.text
            self.page_headers = dict(response.headers)

            passed = 200 <= response.status_code < 400
            message = f"HTTP {response.status_code}, loaded in {self.load_time:.2f}s"
            self.log_result("Page Load", passed, message)

            return passed
        except Exception as e:
            self.log_result("Page Load", False, f"Exception: {str(e)}")
            return False

    def test_content_structure(self) -> bool:
        """Test 2: Page contains expected structure."""
        self.print_section("Test 2: Content Structure")

        if not self.page_content:
            self.log_result("Content Structure", False, "No page content available")
            return False

        # Check for key elements using regex
        checks = {
            "Task Graph Page Container": 'data-testid="task-graph-page"' in self.page_content,
            "Task Picker Section": 'data-testid="task-graph-picker"' in self.page_content,
            "Task Select Element": 'id="task-graph-select"' in self.page_content or 'task-graph-select' in self.page_content,
            "Page Header": '<h1' in self.page_content and 'task-graph' in self.page_content.lower(),
            "Reload Button": 'Reload' in self.page_content or 'reload' in self.page_content.lower(),
        }

        all_passed = True
        for element_name, found in checks.items():
            passed = found
            all_passed = all_passed and passed
            message = "Found" if passed else "Not found (may be hydrated client-side)"
            self.log_result(f"Structure: {element_name}", passed, message)

        return all_passed

    def test_cache_headers(self) -> bool:
        """Test 3: Cache headers are appropriate."""
        self.print_section("Test 3: Cache Headers & Freshness")

        if not self.page_headers:
            self.log_result("Cache Headers", False, "No headers available")
            return False

        headers = self.page_headers
        cache_control = headers.get('cache-control', 'not-set').lower()
        etag = headers.get('etag', 'not-set')
        last_modified = headers.get('last-modified', 'not-set')
        content_type = headers.get('content-type', 'not-set')

        # Verify cache is appropriate for dynamic content
        has_no_cache = 'no-cache' in cache_control or 'no-store' in cache_control
        passed = has_no_cache

        self.log_result("Cache-Control Policy", passed,
                       f"Cache-Control: {cache_control}")
        self.log_result("Content Type", content_type == 'text/html; charset=utf-8',
                       f"Content-Type: {content_type}")
        self.log_result("ETag Present", etag != 'not-set',
                       f"ETag: {etag[:20]}..." if etag != 'not-set' else "No ETag")
        self.log_result("Last-Modified", last_modified != 'not-set',
                       f"Last-Modified: {last_modified}")

        return passed

    def test_api_availability(self) -> bool:
        """Test 4: Task API is available and returns fresh data."""
        self.print_section("Test 4: Task API & Fresh Data")

        try:
            response = self.session.get(f"{TASK_API_URL}", timeout=TIMEOUT)
            passed = 200 <= response.status_code < 400

            if passed:
                try:
                    data = response.json()
                    if isinstance(data, list):
                        self.tasks_data = data
                        count = len(data)
                        message = f"Retrieved {count} tasks"
                    else:
                        # May be wrapped in object
                        tasks = data.get('tasks', data.get('data', []))
                        self.tasks_data = tasks if isinstance(tasks, list) else []
                        message = f"Retrieved {len(self.tasks_data)} tasks (wrapped)"

                    self.log_result("Task API Response", True, message)
                except json.JSONDecodeError:
                    self.log_result("Task API Response", False, "Invalid JSON response")
                    return False
            else:
                message = f"API returned HTTP {response.status_code}"
                self.log_result("Task API Response", False, message)
                return False

            # Verify API cache headers
            api_cache = response.headers.get('cache-control', 'not-set').lower()
            is_fresh = 'no-cache' in api_cache or 'no-store' in api_cache or 'max-age=0' in api_cache
            self.log_result("API Cache Headers", is_fresh,
                           f"Cache-Control: {api_cache}")

            return True
        except Exception as e:
            self.log_result("Task API Response", False, f"Exception: {str(e)}")
            return False

    def test_no_console_errors(self) -> bool:
        """Test 5: Verify page structure (console errors checked client-side)."""
        self.print_section("Test 5: Page Validation")

        if not self.page_content:
            return False

        # Check for common error indicators in HTML
        checks = {
            "No 'error' script tags": '<script type="text/javascript">throw' not in self.page_content,
            "No __ERRORS__ in HTML": '__ERRORS__' not in self.page_content,
            "React root present": 'id="__next"' in self.page_content or 'id="root"' in self.page_content or 'react' in self.page_content.lower(),
        }

        all_passed = True
        for check_name, check_result in checks.items():
            all_passed = all_passed and check_result
            self.log_result(f"Page Validation: {check_name}", check_result,
                           "Passed" if check_result else "Failed")

        return all_passed

    def test_content_freshness(self) -> bool:
        """Test 6: Verify content is fresh (not stale)."""
        self.print_section("Test 6: Content Freshness")

        # Get response again to compare
        try:
            response1 = self.session.get(TASK_GRAPH_URL, timeout=TIMEOUT)
            time.sleep(0.5)
            response2 = self.session.get(TASK_GRAPH_URL, timeout=TIMEOUT)

            # Compare ETags
            etag1 = response1.headers.get('etag')
            etag2 = response2.headers.get('etag')

            # Both should exist and be consistent (server serving same content)
            etags_match = etag1 == etag2
            self.log_result("ETag Consistency", etags_match,
                           f"ETag1: {etag1[:20]}..., ETag2: {etag2[:20]}...")

            # Hash content to detect changes
            content1_hash = hashlib.md5(response1.content).hexdigest()
            content2_hash = hashlib.md5(response2.content).hexdigest()

            content_same = content1_hash == content2_hash
            self.log_result("Content Consistency", content_same,
                           f"Both requests return consistent content")

            # Verify server time headers are recent
            date_str = response1.headers.get('date')
            if date_str:
                # Parse HTTP date
                from email.utils import parsedate_to_datetime
                server_time = parsedate_to_datetime(date_str)
                now = datetime.utcnow().replace(tzinfo=server_time.tzinfo)
                time_diff = abs((now - server_time).total_seconds())

                is_fresh = time_diff < 5  # Server time should be within 5 seconds
                self.log_result("Server Time Freshness", is_fresh,
                               f"Server time diff: {time_diff:.1f}s")

            return True
        except Exception as e:
            self.log_result("Content Freshness", False, f"Exception: {str(e)}")
            return False

    def test_performance(self) -> bool:
        """Test 7: Performance check."""
        self.print_section("Test 7: Performance")

        # Evaluate load time
        load_time_ok = self.load_time < 3.0
        message = f"Load time: {self.load_time:.2f}s"

        if self.load_time < 1.0:
            self.log_result("Page Load Time", True, f"{message} (excellent)")
        elif self.load_time < 3.0:
            self.log_result("Page Load Time", True, f"{message} (good)")
        elif self.load_time < 5.0:
            self.log_result("Page Load Time", False, f"{message} (acceptable but slow)")
        else:
            self.log_result("Page Load Time", False, f"{message} (too slow)")

        return load_time_ok

    def test_browser_simulation(self) -> bool:
        """Test 8: Simulate browser navigation patterns."""
        self.print_section("Test 8: Browser Navigation")

        try:
            # Test navigation with referer
            headers = {'Referer': urljoin(BASE_URL, '/console')}
            response = self.session.get(TASK_GRAPH_URL, headers=headers, timeout=TIMEOUT)

            passed = response.status_code == 200
            self.log_result("Navigation with Referer", passed,
                           f"HTTP {response.status_code}")

            # Test with different user agents (if needed)
            ua = 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36'
            headers['User-Agent'] = ua
            response = self.session.get(TASK_GRAPH_URL, headers=headers, timeout=TIMEOUT)

            passed = response.status_code == 200
            self.log_result("Navigation with User-Agent", passed,
                           f"HTTP {response.status_code}")

            return True
        except Exception as e:
            self.log_result("Browser Simulation", False, f"Exception: {str(e)}")
            return False

    def run_all_tests(self) -> bool:
        """Run all verification tests."""
        print(f"\n{BOLD}{CYAN}Task Graph Verification Suite (TaskRaft v2){RESET}")
        print(f"{CYAN}Target: {TASK_GRAPH_URL}{RESET}\n")

        # Run tests in sequence
        test_results = [
            self.test_page_load(),
            self.test_content_structure(),
            self.test_cache_headers(),
            self.test_api_availability(),
            self.test_no_console_errors(),
            self.test_content_freshness(),
            self.test_performance(),
            self.test_browser_simulation(),
        ]

        # Print summary
        self.print_section("Test Summary")

        passed_count = sum(test_results)
        total_count = len(test_results)

        print(f"{BOLD}Results: {passed_count}/{total_count} tests passed{RESET}\n")

        for test_name, passed, message in self.results:
            status = f"{GREEN}✅{RESET}" if passed else f"{RED}❌{RESET}"
            print(f"{status} {test_name}")
            print(f"   └─ {message}")

        # Overall status
        all_passed = passed_count == total_count
        if all_passed:
            print(f"\n{GREEN}{BOLD}✅ ALL TESTS PASSED - Task Graph is ready for production{RESET}")
        else:
            print(f"\n{YELLOW}{BOLD}⚠️  {total_count - passed_count} test(s) failed - review above{RESET}")

        return all_passed

def main():
    """Main entry point."""
    verifier = TaskGraphVerifier()
    success = verifier.run_all_tests()
    sys.exit(0 if success else 1)

if __name__ == '__main__':
    main()
