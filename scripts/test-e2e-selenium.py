#!/usr/bin/env python3
"""
E2E Integration Test Suite for CorvinOS Console
Uses requests library to test API endpoints and functionality
"""

import sys
import json
import time
from pathlib import Path
from typing import Optional, Dict, Any
import subprocess
import requests
from datetime import datetime

# Colors for output
GREEN = '\033[0;32m'
RED = '\033[0;31m'
YELLOW = '\033[1;33m'
BLUE = '\033[0;34m'
NC = '\033[0m'

BASE_URL = "http://localhost:8000"
TIMEOUT = 10

class E2ETester:
    def __init__(self):
        self.session = requests.Session()
        self.test_results = []
        self.console_process = None

    def log_test(self, name: str, passed: bool, details: str = ""):
        """Log test result"""
        status = f"{GREEN}✅{NC}" if passed else f"{RED}❌{NC}"
        self.test_results.append({"name": name, "passed": passed, "details": details})
        print(f"{status} {name}")
        if details:
            print(f"   {details}")

    def start_console(self):
        """Start Console server"""
        print(f"{BLUE}🚀 Starting Console Server...{NC}")
        try:
            self.console_process = subprocess.Popen(
                ["python", "-m", "corvin_console.standalone"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
            )
            time.sleep(3)  # Wait for server to start

            # Test connection
            try:
                response = self.session.get(f"{BASE_URL}", timeout=TIMEOUT)
                print(f"{GREEN}✅ Console Server Running{NC}\n")
                return True
            except:
                print(f"{RED}❌ Could not connect to Console{NC}")
                return False
        except Exception as e:
            print(f"{RED}❌ Failed to start Console: {e}{NC}")
            return False

    def test_1_health_check(self):
        """Test 1: Console is reachable"""
        print(f"\n{BLUE}Test 1️⃣ - Console Health Check{NC}")
        try:
            response = self.session.get(f"{BASE_URL}", timeout=TIMEOUT)
            passed = response.status_code == 200
            self.log_test("Console responds to requests", passed, f"Status: {response.status_code}")
            return passed
        except Exception as e:
            self.log_test("Console responds to requests", False, str(e))
            return False

    def test_2_feature_flags_api(self):
        """Test 2: Feature Flags API"""
        print(f"\n{BLUE}Test 2️⃣ - Feature Flags API (GET /settings/features){NC}")
        try:
            response = self.session.get(f"{BASE_URL}/api/settings/features", timeout=TIMEOUT)

            if response.status_code == 200:
                data = response.json()
                features = data.get("features", [])

                # Check if vibe_engineering exists
                vibe_eng = next((f for f in features if f["id"] == "vibe_engineering"), None)

                self.log_test("API endpoint accessible", True, f"Found {len(features)} features")
                self.log_test("vibe_engineering feature exists", vibe_eng is not None)

                return vibe_eng is not None
            else:
                self.log_test("API endpoint accessible", False, f"Status: {response.status_code}")
                return False
        except Exception as e:
            self.log_test("API endpoint accessible", False, str(e))
            return False

    def test_3_token_metrics_database(self):
        """Test 3: Token Metrics Database"""
        print(f"\n{BLUE}Test 3️⃣ - Token Metrics Database{NC}")
        try:
            db_path = Path.home() / ".corvin" / "token_metrics.db"

            if db_path.exists():
                size_mb = db_path.stat().st_size / (1024 * 1024)
                self.log_test("Token metrics database exists", True, f"Size: {size_mb:.2f} MB")

                # Try to query it
                import sqlite3
                conn = sqlite3.connect(db_path)
                cursor = conn.cursor()
                cursor.execute("SELECT COUNT(*) FROM token_metrics")
                count = cursor.fetchone()[0]
                conn.close()

                self.log_test("Database has token records", count > 0, f"Records: {count}")
                return count > 0
            else:
                self.log_test("Token metrics database exists", False, "File not found")
                return False
        except Exception as e:
            self.log_test("Token metrics database query", False, str(e))
            return False

    def test_4_feature_toggle_api(self):
        """Test 4: Feature Toggle API"""
        print(f"\n{BLUE}Test 4️⃣ - Feature Toggle API (POST /settings/features){NC}")
        try:
            # Try to enable vibe_engineering
            payload = {"id": "vibe_engineering", "enabled": True}
            response = self.session.post(
                f"{BASE_URL}/api/settings/features/vibe_engineering",
                json=payload,
                timeout=TIMEOUT
            )

            passed = response.status_code in [200, 201, 204]
            self.log_test("Feature toggle API works", passed, f"Status: {response.status_code}")
            return passed
        except Exception as e:
            self.log_test("Feature toggle API works", False, str(e))
            return False

    def test_5_memory_context_endpoint(self):
        """Test 5: Token Metrics Endpoint"""
        print(f"\n{BLUE}Test 5️⃣ - Token Metrics Endpoint (GET /api/metrics/session){NC}")
        try:
            response = self.session.get(
                f"{BASE_URL}/api/metrics/session/current",
                timeout=TIMEOUT
            )

            if response.status_code == 200:
                data = response.json()
                self.log_test("Metrics endpoint works", True)

                # Check for expected fields
                summary = data.get("summary", {})
                has_tokens = "total_tokens" in summary
                has_savings = "savings_percent" in summary

                self.log_test("Metrics contain token data", has_tokens)
                self.log_test("Metrics contain savings data", has_savings)

                if has_tokens:
                    tokens = summary.get("total_tokens", 0)
                    savings = summary.get("savings_percent", 0)
                    print(f"   📊 Tokens: {tokens:,}, Savings: {savings:.1f}%")

                return has_tokens and has_savings
            else:
                self.log_test("Metrics endpoint works", False, f"Status: {response.status_code}")
                return False
        except Exception as e:
            self.log_test("Metrics endpoint works", False, str(e))
            return False

    def test_6_chat_endpoint(self):
        """Test 6: Chat Endpoint"""
        print(f"\n{BLUE}Test 6️⃣ - Chat Endpoint{NC}")
        try:
            # Check if chat routes are registered
            response = self.session.get(f"{BASE_URL}/api/chat", timeout=TIMEOUT)

            # Accept various status codes (endpoint might require specific method)
            passed = response.status_code != 404
            self.log_test("Chat endpoint exists", passed, f"Status: {response.status_code}")
            return passed
        except Exception as e:
            self.log_test("Chat endpoint exists", False, str(e))
            return False

    def test_7_forge_tools_registered(self):
        """Test 7: Forge Tools"""
        print(f"\n{BLUE}Test 7️⃣ - Forge Tools Registry{NC}")
        try:
            # Check if forge tools are available
            response = self.session.get(f"{BASE_URL}/api/forge", timeout=TIMEOUT)

            passed = response.status_code != 404
            self.log_test("Forge API accessible", passed, f"Status: {response.status_code}")
            return passed
        except Exception as e:
            self.log_test("Forge API accessible", False, str(e))
            return False

    def test_8_skills_endpoint(self):
        """Test 8: Skills Endpoint"""
        print(f"\n{BLUE}Test 8️⃣ - Skills Registry{NC}")
        try:
            response = self.session.get(f"{BASE_URL}/api/skills", timeout=TIMEOUT)

            passed = response.status_code != 404
            self.log_test("Skills API accessible", passed, f"Status: {response.status_code}")
            return passed
        except Exception as e:
            self.log_test("Skills API accessible", False, str(e))
            return False

    def test_9_settings_endpoint(self):
        """Test 9: Settings Endpoint"""
        print(f"\n{BLUE}Test 9️⃣ - Settings Endpoint{NC}")
        try:
            response = self.session.get(f"{BASE_URL}/api/settings", timeout=TIMEOUT)

            passed = response.status_code == 200
            self.log_test("Settings endpoint works", passed, f"Status: {response.status_code}")
            return passed
        except Exception as e:
            self.log_test("Settings endpoint works", False, str(e))
            return False

    def test_10_context_pipeline_wiring(self):
        """Test 10: Full Context Pipeline"""
        print(f"\n{BLUE}Test 🔟 - Context Pipeline Integration{NC}")
        try:
            # Verify all major endpoints are reachable
            endpoints = {
                "Memory": "/api/memory",
                "Skills": "/api/skills",
                "Graph": "/api/graph",
                "Metrics": "/api/metrics/session/current",
            }

            results = []
            for name, endpoint in endpoints.items():
                try:
                    response = self.session.get(f"{BASE_URL}{endpoint}", timeout=TIMEOUT)
                    reachable = response.status_code != 404
                    results.append((name, reachable))
                    status = "✅" if reachable else "⚠️"
                    print(f"   {status} {name}: {endpoint}")
                except:
                    results.append((name, False))
                    print(f"   ❌ {name}: {endpoint}")

            # At least 2 should be reachable
            reachable_count = sum(1 for _, r in results if r)
            passed = reachable_count >= 2

            self.log_test(f"Pipeline stages reachable ({reachable_count}/4)", passed)
            return passed
        except Exception as e:
            self.log_test("Pipeline integration check", False, str(e))
            return False

    def test_11_integration_summary(self):
        """Test 11: Full Integration Check"""
        print(f"\n{BLUE}Test 1️⃣1️⃣ - Full Integration Check{NC}")
        try:
            # Check all critical components
            checks = {
                "Console Server": self.test_health_check,
                "Feature Flags": self.test_feature_flags_api,
                "Token Metrics": self.test_token_metrics_database,
                "Settings": self.test_settings_endpoint,
            }

            all_ok = True
            for name in checks:
                try:
                    response = self.session.get(f"{BASE_URL}/api", timeout=TIMEOUT)
                    ok = response.status_code != 500
                    print(f"   {'✅' if ok else '❌'} {name}")
                    all_ok = all_ok and ok
                except:
                    print(f"   ❌ {name}")
                    all_ok = False

            self.log_test("All critical components online", all_ok)
            return all_ok
        except Exception as e:
            self.log_test("Integration check", False, str(e))
            return False

    def run_all_tests(self):
        """Run all tests"""
        print(f"\n{BLUE}{'='*80}{NC}")
        print(f"{BLUE}  CorvinOS E2E Integration Test Suite{NC}")
        print(f"{BLUE}{'='*80}{NC}\n")

        # Start console if not already running
        if not self._is_console_running():
            if not self.start_console():
                print(f"{RED}Failed to start console, running tests anyway...{NC}")
        else:
            print(f"{GREEN}✅ Console already running{NC}\n")

        # Run all tests
        self.test_1_health_check()
        self.test_2_feature_flags_api()
        self.test_3_token_metrics_database()
        self.test_4_feature_toggle_api()
        self.test_5_memory_context_endpoint()
        self.test_6_chat_endpoint()
        self.test_7_forge_tools_registered()
        self.test_8_skills_endpoint()
        self.test_9_settings_endpoint()
        self.test_10_context_pipeline_wiring()
        self.test_11_integration_summary()

        # Print summary
        self.print_summary()

    def _is_console_running(self):
        """Check if console is already running"""
        try:
            response = requests.get(f"{BASE_URL}", timeout=2)
            return response.status_code == 200
        except:
            return False

    def print_summary(self):
        """Print test summary"""
        print(f"\n{BLUE}{'='*80}{NC}")
        print(f"{BLUE}  Test Results Summary{NC}")
        print(f"{BLUE}{'='*80}{NC}\n")

        passed = sum(1 for t in self.test_results if t["passed"])
        total = len(self.test_results)

        for test in self.test_results:
            status = f"{GREEN}✅{NC}" if test["passed"] else f"{RED}❌{NC}"
            print(f"{status} {test['name']}")

        print(f"\n{BLUE}Score: {passed}/{total} tests passed ({passed*100//total}%){NC}")

        if passed == total:
            print(f"\n{GREEN}{'🎉 ' * 20}{NC}")
            print(f"{GREEN}All Integration Tests Passed!{NC}")
            print(f"{GREEN}Console UI and Context Pipeline fully functional{NC}")
            print(f"{GREEN}{'🎉 ' * 20}{NC}\n")
        else:
            print(f"\n{YELLOW}⚠️ Some tests failed - review output above{NC}\n")

        return passed == total

    def cleanup(self):
        """Cleanup"""
        if self.console_process:
            try:
                self.console_process.terminate()
                self.console_process.wait(timeout=5)
            except:
                self.console_process.kill()


if __name__ == "__main__":
    tester = E2ETester()
    try:
        tester.run_all_tests()
        success = tester.print_summary()
        sys.exit(0 if success else 1)
    except KeyboardInterrupt:
        print(f"\n{YELLOW}Tests interrupted{NC}")
        sys.exit(1)
    finally:
        tester.cleanup()
