#!/usr/bin/env python3
"""Environment validation script for E2E console tests.

Checks:
1. Python version (3.11+)
2. Required dependencies (pytest, playwright, httpx, pydantic, fastapi)
3. Playwright chromium availability
4. Test directory structure
5. Base URL reachability

Usage:
    python3 tests/validate_environment.py [--verbose]
    python3 tests/validate_environment.py [--install-deps]
"""

import sys
import subprocess
import importlib
from pathlib import Path
from typing import Tuple, List
import os


# ANSI colors
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
BLUE = "\033[94m"
RESET = "\033[0m"
BOLD = "\033[1m"


def print_header(text: str) -> None:
    """Print section header."""
    print(f"\n{BOLD}{BLUE}{'─' * 70}{RESET}")
    print(f"{BOLD}{BLUE}{text:^70}{RESET}")
    print(f"{BOLD}{BLUE}{'─' * 70}{RESET}\n")


def print_success(text: str) -> None:
    """Print success message."""
    print(f"{GREEN}✅ {text}{RESET}")


def print_error(text: str) -> None:
    """Print error message."""
    print(f"{RED}❌ {text}{RESET}")


def print_warning(text: str) -> None:
    """Print warning message."""
    print(f"{YELLOW}⚠️  {text}{RESET}")


def print_info(text: str) -> None:
    """Print info message."""
    print(f"{BLUE}ℹ️  {text}{RESET}")


class EnvironmentValidator:
    """Validate E2E test environment."""

    def __init__(self):
        self.checks_passed = 0
        self.checks_failed = 0
        self.checks_warned = 0
        self.issues: List[str] = []

    def check_python_version(self) -> bool:
        """Check Python version (3.11+)."""
        print_header("1. Python Version Check")
        version = sys.version_info
        print(f"Detected: Python {version.major}.{version.minor}.{version.micro}")

        if version.major >= 3 and version.minor >= 11:
            print_success(f"Python {version.major}.{version.minor} meets minimum requirement (3.11+)")
            self.checks_passed += 1
            return True
        else:
            print_error(f"Python {version.major}.{version.minor} does not meet minimum requirement (3.11+)")
            self.checks_failed += 1
            self.issues.append(f"Python {version.major}.{version.minor} (need 3.11+)")
            return False

    def check_import(self, module_name: str, pip_name: str = None) -> bool:
        """Check if a module can be imported."""
        try:
            importlib.import_module(module_name)
            print_success(f"{module_name} is installed")
            return True
        except ImportError:
            pip_name = pip_name or module_name
            print_error(f"{module_name} not found (install: pip install {pip_name})")
            self.issues.append(f"{module_name} (pip: {pip_name})")
            return False

    def check_dependencies(self) -> bool:
        """Check required dependencies."""
        print_header("2. Required Dependencies")

        deps = [
            ("pytest", "pytest"),
            ("pytest_asyncio", "pytest-asyncio"),
            ("httpx", "httpx"),
            ("pydantic", "pydantic"),
            ("fastapi", "fastapi"),
            ("playwright", "playwright"),
        ]

        all_ok = True
        for module_name, pip_name in deps:
            if self.check_import(module_name, pip_name):
                self.checks_passed += 1
            else:
                self.checks_failed += 1
                all_ok = False

        return all_ok

    def check_playwright_chromium(self) -> bool:
        """Check if Playwright Chromium is installed."""
        print_header("3. Playwright Chromium Availability")

        try:
            from playwright.async_api import async_playwright
        except ImportError:
            print_error("Playwright not installed")
            self.checks_failed += 1
            self.issues.append("Playwright (pip install playwright)")
            return False

        chromium_path = self._get_chromium_path()
        if chromium_path and chromium_path.exists():
            print_success(f"Chromium found at: {chromium_path}")
            self.checks_passed += 1
            return True
        else:
            print_warning("Chromium not installed. Run: playwright install chromium")
            self.checks_warned += 1
            self.issues.append("Chromium (run: playwright install chromium)")
            return False

    def _get_chromium_path(self) -> Path:
        """Get expected Chromium binary path."""
        home = Path.home()
        # Typical locations
        paths = [
            home / ".cache" / "ms-playwright" / "chromium-XXXX" / "chrome-linux" / "chrome",
            home / ".cache" / "ms-playwright" / "chromium" / "chrome",
            Path("/usr/bin/chromium"),
            Path("/usr/bin/chromium-browser"),
        ]
        for p in paths:
            if p.exists() or p.parent.exists():
                return p
        return None

    def check_test_directories(self) -> bool:
        """Check test directory structure."""
        print_header("4. Test Directory Structure")

        required_dirs = [
            Path("tests"),
            Path("tests/console"),
            Path("tests/e2e"),
        ]

        all_ok = True
        for dir_path in required_dirs:
            if dir_path.exists():
                print_success(f"{dir_path}/ exists")
                self.checks_passed += 1
            else:
                print_warning(f"{dir_path}/ does not exist (create with: mkdir -p {dir_path})")
                self.checks_warned += 1
                all_ok = False

        required_files = [
            Path("tests/e2e/conftest.py"),
            Path("tests/console/conftest.py"),
            Path("tests/console/PANEL_INVENTORY.md"),
            Path("pytest.ini"),
        ]

        for file_path in required_files:
            if file_path.exists():
                print_success(f"{file_path} exists")
                self.checks_passed += 1
            else:
                print_warning(f"{file_path} does not exist")
                self.checks_warned += 1

        return all_ok

    def check_base_url_reachable(self) -> bool:
        """Check if console base URL is reachable."""
        print_header("5. Console Base URL Reachability")

        base_url = os.getenv("CONSOLE_BASE_URL", "http://localhost:8765")
        print(f"Testing: {base_url}")

        try:
            import httpx
            with httpx.Client(timeout=5) as client:
                response = client.get(base_url)
                if response.status_code == 200:
                    print_success(f"Console is reachable (HTTP {response.status_code})")
                    self.checks_passed += 1
                    return True
                else:
                    print_warning(f"Console returned HTTP {response.status_code} (expected 200)")
                    self.checks_warned += 1
                    return False
        except Exception as e:
            print_warning(f"Console not reachable: {e}")
            print_info(f"Start console with: corvin-console-watch or systemctl --user start corvin-webui")
            self.checks_warned += 1
            return False

    def check_pytest_ini(self) -> bool:
        """Check pytest.ini configuration."""
        print_header("6. Pytest Configuration")

        pytest_ini = Path("pytest.ini")
        if pytest_ini.exists():
            print_success("pytest.ini exists")
            with open(pytest_ini) as f:
                content = f.read()
                if "[pytest]" in content and "markers" in content:
                    print_success("pytest.ini has marker definitions")
                    self.checks_passed += 1
                    return True
                else:
                    print_warning("pytest.ini exists but may be incomplete")
                    self.checks_warned += 1
                    return False
        else:
            print_warning("pytest.ini not found (create from template)")
            self.checks_warned += 1
            return False

    def check_corvinOS_installation(self) -> bool:
        """Check if CorvinOS package is importable."""
        print_header("7. CorvinOS Package")

        try:
            # Try importing key modules
            from core.console import app
            print_success("core.console is importable")
            self.checks_passed += 1
            return True
        except ImportError as e:
            print_warning(f"core.console not fully importable: {e}")
            print_info("Run: pip install -e /home/shumway/projects/CorvinOS")
            self.checks_warned += 1
            return False

    def run_all_checks(self) -> bool:
        """Run all validation checks."""
        print(f"\n{BOLD}{BLUE}E2E Console Environment Validation{RESET}")
        print(f"{BOLD}{BLUE}{'=' * 70}{RESET}")

        self.check_python_version()
        self.check_dependencies()
        self.check_playwright_chromium()
        self.check_test_directories()
        self.check_base_url_reachable()
        self.check_pytest_ini()
        self.check_corvinOS_installation()

        self.print_summary()
        return self.checks_failed == 0

    def print_summary(self) -> None:
        """Print validation summary."""
        print_header("Summary")

        total = self.checks_passed + self.checks_failed + self.checks_warned
        print(f"Total checks: {total}")
        print(f"{GREEN}{self.checks_passed} passed{RESET}")
        if self.checks_warned > 0:
            print(f"{YELLOW}{self.checks_warned} warnings{RESET}")
        if self.checks_failed > 0:
            print(f"{RED}{self.checks_failed} failed{RESET}")

        if self.checks_failed > 0:
            print(f"\n{RED}❌ Environment validation FAILED{RESET}")
            print(f"\nIssues to fix:")
            for issue in self.issues:
                print(f"  • {issue}")
            return

        if self.checks_warned > 0:
            print(f"\n{YELLOW}⚠️  Environment validation PASSED with warnings{RESET}")
            print(f"\nRecommended fixes:")
            for issue in self.issues:
                print(f"  • {issue}")
            return

        print(f"\n{GREEN}✅ Environment validation PASSED{RESET}")
        print(f"\nReady to run tests:")
        print(f"  pytest tests/console/ -m critical_path -v")
        print(f"  pytest tests/console/test_panel_smoke.py -v")
        print(f"  pytest tests/console/ --co  # List all tests\n")

    def suggest_install_script(self) -> None:
        """Suggest installation script."""
        print_header("Suggested Installation Steps")

        print(f"{BOLD}1. Install pip (if missing){RESET}")
        print(f"  curl https://bootstrap.pypa.io/get-pip.py | python3\n")

        print(f"{BOLD}2. Install CorvinOS in development mode{RESET}")
        print(f"  pip install -e /home/shumway/projects/CorvinOS\n")

        print(f"{BOLD}3. Install test dependencies{RESET}")
        print(f"  pip install pytest pytest-asyncio httpx pydantic fastapi playwright\n")

        print(f"{BOLD}4. Install Playwright browsers{RESET}")
        print(f"  playwright install chromium\n")

        print(f"{BOLD}5. Verify environment{RESET}")
        print(f"  python3 tests/validate_environment.py\n")

        print(f"{BOLD}6. Start console (in background){RESET}")
        print(f"  systemctl --user start corvin-webui\n")

        print(f"{BOLD}7. Run tests{RESET}")
        print(f"  pytest tests/console/ -m critical_path -v --tb=short\n")


def main():
    """Main entry point."""
    validator = EnvironmentValidator()

    if "--install" in sys.argv or "--install-deps" in sys.argv:
        validator.suggest_install_script()
        sys.exit(0)

    success = validator.run_all_checks()
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
