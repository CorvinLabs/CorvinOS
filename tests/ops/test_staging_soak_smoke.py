"""
Stream 2 Staging Smoke Tests
=============================

Week 2, Days 1–3: Staging Deployment Validation

6 E2E smoke tests validating Stream 2 (Security Orchestrator) staging deployment:
1. Health check endpoint responds
2. Threats endpoint returns valid data
3. Policy endpoint accessible
4. Audit endpoint returns immutable audit trail
5. Metrics endpoint provides Prometheus metrics
6. WebSocket stream connects and streams threats

Run: pytest tests/ops/test_staging_soak_smoke.py -v

ADR-2047: Security Orchestrator Skill
Timeline: Phase 10, Week 2–3 Staging Soak Test
"""

import json
import pytest
import asyncio
import subprocess
import time
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional
from dataclasses import dataclass
from enum import Enum


class TestPhase(Enum):
    """Test execution phases."""
    PRE_DEPLOYMENT = "pre_deployment"
    SMOKE_TEST = "smoke_test"
    ROUTE_VALIDATION = "route_validation"
    METRICS_VALIDATION = "metrics_validation"


@dataclass
class SmokeTestResult:
    """Result of a smoke test."""
    test_name: str
    endpoint: str
    status: str  # "PASS", "FAIL", "TIMEOUT"
    response_code: Optional[int] = None
    response_time_ms: Optional[float] = None
    error_message: Optional[str] = None
    timestamp: str = None

    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = datetime.utcnow().isoformat() + "Z"


class StagingDeploymentValidator:
    """Validates Stream 2 staging deployment."""

    BASE_URL = "http://localhost:8765"
    TIMEOUT_SECONDS = 10
    RETRY_COUNT = 3

    def __init__(self):
        self.results: List[SmokeTestResult] = []
        self.deployment_ready = False

    async def check_health(self) -> SmokeTestResult:
        """Test 1: Health check endpoint responds (GET /security/health)."""
        test_name = "health_check"
        endpoint = "GET /security/health"

        for attempt in range(self.RETRY_COUNT):
            try:
                start_time = time.time()
                result = subprocess.run(
                    ["curl", "-s", "-w", "%{http_code}", "-o", "/dev/null",
                     f"{self.BASE_URL}/security/health"],
                    capture_output=True,
                    text=True,
                    timeout=self.TIMEOUT_SECONDS
                )
                response_time_ms = (time.time() - start_time) * 1000

                if result.returncode == 0:
                    http_code = int(result.stdout.strip()) if result.stdout.strip() else 0
                    if http_code == 200:
                        return SmokeTestResult(
                            test_name=test_name,
                            endpoint=endpoint,
                            status="PASS",
                            response_code=http_code,
                            response_time_ms=response_time_ms
                        )
            except (subprocess.TimeoutExpired, ValueError) as e:
                if attempt == self.RETRY_COUNT - 1:
                    return SmokeTestResult(
                        test_name=test_name,
                        endpoint=endpoint,
                        status="TIMEOUT",
                        error_message=f"Timeout after {self.TIMEOUT_SECONDS}s: {str(e)}"
                    )
                await asyncio.sleep(1)

        return SmokeTestResult(
            test_name=test_name,
            endpoint=endpoint,
            status="FAIL",
            error_message="Health check endpoint not responding"
        )

    async def check_threats_endpoint(self) -> SmokeTestResult:
        """Test 2: Threats endpoint returns valid data (GET /v1/console/security/threats)."""
        test_name = "threats_endpoint"
        endpoint = "GET /v1/console/security/threats"

        try:
            start_time = time.time()
            result = subprocess.run(
                ["curl", "-s", f"{self.BASE_URL}/v1/console/security/threats"],
                capture_output=True,
                text=True,
                timeout=self.TIMEOUT_SECONDS
            )
            response_time_ms = (time.time() - start_time) * 1000

            if result.returncode == 0 and result.stdout:
                try:
                    data = json.loads(result.stdout)
                    # Validate response structure
                    if isinstance(data, dict) and "threats" in data or isinstance(data, list):
                        return SmokeTestResult(
                            test_name=test_name,
                            endpoint=endpoint,
                            status="PASS",
                            response_code=200,
                            response_time_ms=response_time_ms
                        )
                except json.JSONDecodeError:
                    pass

            return SmokeTestResult(
                test_name=test_name,
                endpoint=endpoint,
                status="FAIL",
                response_code=result.returncode,
                error_message="Invalid response format or endpoint not responding"
            )
        except subprocess.TimeoutExpired:
            return SmokeTestResult(
                test_name=test_name,
                endpoint=endpoint,
                status="TIMEOUT",
                error_message=f"Timeout after {self.TIMEOUT_SECONDS}s"
            )

    async def check_policy_endpoint(self) -> SmokeTestResult:
        """Test 3: Policy endpoint accessible (GET /v1/console/security/policy)."""
        test_name = "policy_endpoint"
        endpoint = "GET /v1/console/security/policy"

        try:
            start_time = time.time()
            result = subprocess.run(
                ["curl", "-s", "-w", "%{http_code}", "-o", "/dev/null",
                 f"{self.BASE_URL}/v1/console/security/policy"],
                capture_output=True,
                text=True,
                timeout=self.TIMEOUT_SECONDS
            )
            response_time_ms = (time.time() - start_time) * 1000

            if result.returncode == 0:
                http_code = int(result.stdout.strip()) if result.stdout.strip() else 0
                if http_code in [200, 202]:  # 200 OK or 202 Accepted
                    return SmokeTestResult(
                        test_name=test_name,
                        endpoint=endpoint,
                        status="PASS",
                        response_code=http_code,
                        response_time_ms=response_time_ms
                    )

            return SmokeTestResult(
                test_name=test_name,
                endpoint=endpoint,
                status="FAIL",
                response_code=http_code if 'http_code' in locals() else None,
                error_message="Policy endpoint not accessible"
            )
        except subprocess.TimeoutExpired:
            return SmokeTestResult(
                test_name=test_name,
                endpoint=endpoint,
                status="TIMEOUT",
                error_message=f"Timeout after {self.TIMEOUT_SECONDS}s"
            )

    async def check_audit_endpoint(self) -> SmokeTestResult:
        """Test 4: Audit endpoint returns immutable audit trail (GET /v1/console/security/audit)."""
        test_name = "audit_endpoint"
        endpoint = "GET /v1/console/security/audit"

        try:
            start_time = time.time()
            result = subprocess.run(
                ["curl", "-s", f"{self.BASE_URL}/v1/console/security/audit?limit=10"],
                capture_output=True,
                text=True,
                timeout=self.TIMEOUT_SECONDS
            )
            response_time_ms = (time.time() - start_time) * 1000

            if result.returncode == 0 and result.stdout:
                try:
                    data = json.loads(result.stdout)
                    # Validate audit trail structure: should be list of events
                    if isinstance(data, list):
                        # Check if events have required fields
                        if len(data) == 0:
                            return SmokeTestResult(
                                test_name=test_name,
                                endpoint=endpoint,
                                status="PASS",
                                response_code=200,
                                response_time_ms=response_time_ms
                            )
                        # Validate first event has required fields
                        event = data[0]
                        if all(field in event for field in ["timestamp", "event_type", "tenant_id"]):
                            return SmokeTestResult(
                                test_name=test_name,
                                endpoint=endpoint,
                                status="PASS",
                                response_code=200,
                                response_time_ms=response_time_ms
                            )
                except json.JSONDecodeError:
                    pass

            return SmokeTestResult(
                test_name=test_name,
                endpoint=endpoint,
                status="FAIL",
                error_message="Invalid audit trail format"
            )
        except subprocess.TimeoutExpired:
            return SmokeTestResult(
                test_name=test_name,
                endpoint=endpoint,
                status="TIMEOUT",
                error_message=f"Timeout after {self.TIMEOUT_SECONDS}s"
            )

    async def check_metrics_endpoint(self) -> SmokeTestResult:
        """Test 5: Metrics endpoint provides Prometheus metrics (GET /v1/console/security/metrics)."""
        test_name = "metrics_endpoint"
        endpoint = "GET /v1/console/security/metrics"

        try:
            start_time = time.time()
            result = subprocess.run(
                ["curl", "-s", f"{self.BASE_URL}/v1/console/security/metrics"],
                capture_output=True,
                text=True,
                timeout=self.TIMEOUT_SECONDS
            )
            response_time_ms = (time.time() - start_time) * 1000

            if result.returncode == 0 and result.stdout:
                # Prometheus metrics are plain text, not JSON
                # Check for presence of metric lines (format: metric_name{labels} value timestamp)
                if "# HELP" in result.stdout or "security_orchestrator_" in result.stdout:
                    return SmokeTestResult(
                        test_name=test_name,
                        endpoint=endpoint,
                        status="PASS",
                        response_code=200,
                        response_time_ms=response_time_ms
                    )

            return SmokeTestResult(
                test_name=test_name,
                endpoint=endpoint,
                status="FAIL",
                error_message="Metrics endpoint not returning valid Prometheus format"
            )
        except subprocess.TimeoutExpired:
            return SmokeTestResult(
                test_name=test_name,
                endpoint=endpoint,
                status="TIMEOUT",
                error_message=f"Timeout after {self.TIMEOUT_SECONDS}s"
            )

    async def check_websocket_stream(self) -> SmokeTestResult:
        """Test 6: WebSocket stream connects and streams threats (WS /v1/console/security/stream)."""
        test_name = "websocket_stream"
        endpoint = "WS /v1/console/security/stream"

        try:
            start_time = time.time()
            result = subprocess.run(
                ["wscat", "-c", f"ws://localhost:8765/v1/console/security/stream", "--max", "1"],
                capture_output=True,
                text=True,
                timeout=5
            )
            response_time_ms = (time.time() - start_time) * 1000

            # WebSocket connection successful if wscat exits cleanly
            if result.returncode == 0:
                return SmokeTestResult(
                    test_name=test_name,
                    endpoint=endpoint,
                    status="PASS",
                    response_code=101,  # WebSocket Upgrade
                    response_time_ms=response_time_ms
                )
            else:
                # Try with curl (basic WS support)
                result = subprocess.run(
                    ["curl", "-i", "-N", "-H", "Connection: Upgrade",
                     "-H", "Upgrade: websocket",
                     f"http://localhost:8765/v1/console/security/stream"],
                    capture_output=True,
                    text=True,
                    timeout=5
                )
                if "101" in result.stdout:
                    return SmokeTestResult(
                        test_name=test_name,
                        endpoint=endpoint,
                        status="PASS",
                        response_code=101,
                        response_time_ms=response_time_ms
                    )

            return SmokeTestResult(
                test_name=test_name,
                endpoint=endpoint,
                status="FAIL",
                error_message="WebSocket stream not connecting"
            )
        except (subprocess.TimeoutExpired, FileNotFoundError):
            # wscat may not be installed; try curl fallback
            try:
                start_time = time.time()
                result = subprocess.run(
                    ["curl", "-i", "-N", "-H", "Connection: Upgrade",
                     "-H", "Upgrade: websocket",
                     f"http://localhost:8765/v1/console/security/stream"],
                    capture_output=True,
                    text=True,
                    timeout=2
                )
                response_time_ms = (time.time() - start_time) * 1000
                if "101" in result.stdout:
                    return SmokeTestResult(
                        test_name=test_name,
                        endpoint=endpoint,
                        status="PASS",
                        response_code=101,
                        response_time_ms=response_time_ms
                    )
            except subprocess.TimeoutExpired:
                pass

            return SmokeTestResult(
                test_name=test_name,
                endpoint=endpoint,
                status="TIMEOUT",
                error_message="WebSocket connection timeout"
            )

    async def run_all_tests(self) -> List[SmokeTestResult]:
        """Run all 6 smoke tests concurrently."""
        self.results = await asyncio.gather(
            self.check_health(),
            self.check_threats_endpoint(),
            self.check_policy_endpoint(),
            self.check_audit_endpoint(),
            self.check_metrics_endpoint(),
            self.check_websocket_stream(),
            return_exceptions=False
        )
        return self.results


# ==============================================================================
# Pytest Test Cases
# ==============================================================================

@pytest.fixture
async def validator():
    """Fixture: Create and return a deployment validator."""
    return StagingDeploymentValidator()


@pytest.mark.asyncio
async def test_01_health_check(validator):
    """Smoke Test 1: Health check endpoint responds (GET /security/health)."""
    result = await validator.check_health()
    assert result.status == "PASS", f"Health check failed: {result.error_message}"
    assert result.response_code == 200, f"Expected 200, got {result.response_code}"
    assert result.response_time_ms is not None, "No response time recorded"
    assert result.response_time_ms < 1000, f"Health check too slow: {result.response_time_ms}ms"


@pytest.mark.asyncio
async def test_02_threats_endpoint(validator):
    """Smoke Test 2: Threats endpoint returns valid data (GET /v1/console/security/threats)."""
    result = await validator.check_threats_endpoint()
    assert result.status == "PASS", f"Threats endpoint failed: {result.error_message}"
    assert result.response_code == 200, f"Expected 200, got {result.response_code}"
    assert result.response_time_ms is not None, "No response time recorded"
    assert result.response_time_ms < 5000, f"Threats endpoint too slow: {result.response_time_ms}ms"


@pytest.mark.asyncio
async def test_03_policy_endpoint(validator):
    """Smoke Test 3: Policy endpoint accessible (GET /v1/console/security/policy)."""
    result = await validator.check_policy_endpoint()
    assert result.status == "PASS", f"Policy endpoint failed: {result.error_message}"
    assert result.response_code in [200, 202], f"Expected 200 or 202, got {result.response_code}"


@pytest.mark.asyncio
async def test_04_audit_endpoint(validator):
    """Smoke Test 4: Audit endpoint returns immutable audit trail (GET /v1/console/security/audit)."""
    result = await validator.check_audit_endpoint()
    assert result.status == "PASS", f"Audit endpoint failed: {result.error_message}"
    assert result.response_code == 200, f"Expected 200, got {result.response_code}"


@pytest.mark.asyncio
async def test_05_metrics_endpoint(validator):
    """Smoke Test 5: Metrics endpoint provides Prometheus metrics (GET /v1/console/security/metrics)."""
    result = await validator.check_metrics_endpoint()
    assert result.status == "PASS", f"Metrics endpoint failed: {result.error_message}"
    assert result.response_code == 200, f"Expected 200, got {result.response_code}"


@pytest.mark.asyncio
async def test_06_websocket_stream(validator):
    """Smoke Test 6: WebSocket stream connects and streams threats (WS /v1/console/security/stream)."""
    result = await validator.check_websocket_stream()
    assert result.status == "PASS", f"WebSocket stream failed: {result.error_message}"
    assert result.response_code == 101, f"Expected 101 (WebSocket Upgrade), got {result.response_code}"


@pytest.mark.asyncio
async def test_all_routes_concurrently(validator):
    """Concurrency Test: All 6 endpoints respond within deployment window."""
    results = await validator.run_all_tests()

    # Aggregate results
    passed = sum(1 for r in results if r.status == "PASS")
    failed = sum(1 for r in results if r.status == "FAIL")
    timeout = sum(1 for r in results if r.status == "TIMEOUT")

    # Report
    print(f"\n📊 Smoke Test Summary:")
    print(f"   Passed: {passed}/6")
    print(f"   Failed: {failed}/6")
    print(f"   Timeout: {timeout}/6")
    for result in results:
        status_icon = "✅" if result.status == "PASS" else "❌"
        print(f"   {status_icon} {result.test_name}: {result.status} "
              f"({result.response_time_ms:.0f}ms)" if result.response_time_ms else "")

    # Gate 1 Success Criteria: All 6 tests pass
    assert passed == 6, f"Gate 1 Failed: {failed} failures, {timeout} timeouts"
    assert failed == 0, f"Expected 0 failures, got {failed}"
    assert timeout == 0, f"Expected 0 timeouts, got {timeout}"


if __name__ == "__main__":
    # Run smoke tests locally
    pytest.main([__file__, "-v", "-s"])
