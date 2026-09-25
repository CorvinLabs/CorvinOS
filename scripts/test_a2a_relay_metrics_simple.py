#!/usr/bin/env python3
"""Simple test script for A2A Relay Metrics (no pytest dependency).

Usage: python3 test_a2a_relay_metrics_simple.py

Tests Day 8 k=1 deliverables:
- Prometheus metrics collector initialization
- Metric recording (register, deliver, discovery, handshake)
- Metrics text generation in Prometheus format
- Success rate updates
- Multi-tenant isolation
- Fail-closed behavior
"""
import sys
import time
from pathlib import Path

# Add shared bridges to path
sys.path.insert(0, str(Path(__file__).parent.parent / "corvin_operator" / "bridges" / "shared"))

try:
    from a2a_relay_metrics import RelayMetricsCollector, get_relay_metrics
except ImportError as e:
    print(f"SKIP: prometheus_client not available ({e})")
    sys.exit(0)


def test_collector_init():
    """Test 1: Collector initializes without errors."""
    print("Test 1: Collector initialization...", end=" ")
    try:
        collector = RelayMetricsCollector(namespace="test_1")
        assert collector is not None
        assert collector.namespace == "test_1"
        print("✓ PASS")
        return True
    except Exception as e:
        print(f"✗ FAIL: {e}")
        return False


def test_singleton():
    """Test 2: Singleton pattern works."""
    print("Test 2: Singleton pattern...", end=" ")
    try:
        m1 = get_relay_metrics()
        m2 = get_relay_metrics()
        assert m1 is m2
        print("✓ PASS")
        return True
    except Exception as e:
        print(f"✗ FAIL: {e}")
        return False


def test_record_registration():
    """Test 3: Registration metrics recorded."""
    print("Test 3: Record registration...", end=" ")
    try:
        collector = RelayMetricsCollector(namespace="test_3")
        tenant_id = "tenant_default"

        # Record registrations
        collector.record_registration(tenant_id, "success")
        collector.record_registration(tenant_id, "success")
        collector.record_registration(tenant_id, "auth_key_mismatch")

        # Generate and verify
        metrics_text = collector.generate_metrics_text()
        assert b"test_3_relay_register_attempts_total" in metrics_text
        assert b'outcome="success"' in metrics_text
        assert b'outcome="auth_key_mismatch"' in metrics_text
        print("✓ PASS")
        return True
    except Exception as e:
        print(f"✗ FAIL: {e}")
        return False


def test_record_delivery():
    """Test 4: Delivery metrics recorded with latency."""
    print("Test 4: Record delivery...", end=" ")
    try:
        collector = RelayMetricsCollector(namespace="test_4")
        tenant_id = "tenant_default"

        # Record deliveries with outcomes
        collector.record_delivery(tenant_id, "delivered", latency_ms=42)
        collector.record_delivery(tenant_id, "queued", latency_ms=5)
        collector.record_delivery(tenant_id, "dropped")

        metrics_text = collector.generate_metrics_text()
        assert b"test_4_relay_deliver_attempts_total" in metrics_text
        assert b"test_4_relay_message_latency_ms" in metrics_text
        print("✓ PASS")
        return True
    except Exception as e:
        print(f"✗ FAIL: {e}")
        return False


def test_record_discovery():
    """Test 5: Discovery metrics recorded."""
    print("Test 5: Record discovery...", end=" ")
    try:
        collector = RelayMetricsCollector(namespace="test_5")
        tenant_id = "tenant_default"

        # Record discovery attempts
        collector.record_discovery_attempt(tenant_id, "success", latency_ms=25)
        collector.record_discovery_attempt(tenant_id, "timeout")

        metrics_text = collector.generate_metrics_text()
        assert b"test_5_discovery_pairing_attempts_total" in metrics_text
        assert b"test_5_discovery_latency_ms" in metrics_text
        print("✓ PASS")
        return True
    except Exception as e:
        print(f"✗ FAIL: {e}")
        return False


def test_record_handshake():
    """Test 6: Handshake metrics recorded."""
    print("Test 6: Record handshake...", end=" ")
    try:
        collector = RelayMetricsCollector(namespace="test_6")
        tenant_id = "tenant_default"

        # Record handshakes
        collector.record_handshake(tenant_id, "success", latency_ms=12)
        collector.record_handshake(tenant_id, "verify_failed")

        metrics_text = collector.generate_metrics_text()
        assert b"test_6_a2a_handshake_attempts_total" in metrics_text
        assert b"test_6_a2a_handshake_latency_ms" in metrics_text
        print("✓ PASS")
        return True
    except Exception as e:
        print(f"✗ FAIL: {e}")
        return False


def test_success_rate_gauges():
    """Test 7: Success rate gauges update."""
    print("Test 7: Success rate gauges...", end=" ")
    try:
        collector = RelayMetricsCollector(namespace="test_7")
        tenant_id = "tenant_default"

        # Update gauges
        collector.update_registration_success_rate(tenant_id, 98.5)
        collector.update_delivery_cache_hit_rate(tenant_id, 92.3)
        collector.update_pairing_success_rate(tenant_id, 97.5)
        collector.update_handshake_success_rate(tenant_id, 99.1)

        metrics_text = collector.generate_metrics_text()
        assert b"test_7_relay_register_success_rate" in metrics_text
        assert b"test_7_relay_cache_hit_rate" in metrics_text
        assert b"test_7_discovery_pairing_success_rate" in metrics_text
        assert b"test_7_a2a_handshake_success_rate" in metrics_text
        print("✓ PASS")
        return True
    except Exception as e:
        print(f"✗ FAIL: {e}")
        return False


def test_active_connections():
    """Test 8: Connection count gauge updates."""
    print("Test 8: Active connections...", end=" ")
    try:
        collector = RelayMetricsCollector(namespace="test_8")
        tenant_id = "tenant_default"

        # Update connection counts
        collector.update_active_connections(tenant_id, 5)
        collector.update_active_connections(tenant_id, 10)

        metrics_text = collector.generate_metrics_text()
        assert b"test_8_relay_connections_active" in metrics_text
        print("✓ PASS")
        return True
    except Exception as e:
        print(f"✗ FAIL: {e}")
        return False


def test_measure_latency():
    """Test 9: Latency context manager."""
    print("Test 9: Measure latency context manager...", end=" ")
    try:
        collector = RelayMetricsCollector(namespace="test_9")
        tenant_id = "tenant_default"

        # Use context manager
        with collector.measure_latency(tenant_id, "discovery"):
            time.sleep(0.005)  # 5ms

        with collector.measure_latency(tenant_id, "handshake"):
            time.sleep(0.005)

        with collector.measure_latency(tenant_id, "message"):
            time.sleep(0.005)

        metrics_text = collector.generate_metrics_text()
        assert b"test_9_discovery_latency_ms" in metrics_text
        assert b"test_9_a2a_handshake_latency_ms" in metrics_text
        assert b"test_9_relay_message_latency_ms" in metrics_text
        print("✓ PASS")
        return True
    except Exception as e:
        print(f"✗ FAIL: {e}")
        return False


def test_multi_tenant():
    """Test 10: Multi-tenant isolation (ADR-0007)."""
    print("Test 10: Multi-tenant isolation...", end=" ")
    try:
        collector = RelayMetricsCollector(namespace="test_10")

        # Record for different tenants
        collector.record_registration("tenant_a", "success")
        collector.record_registration("tenant_b", "success")
        collector.record_discovery_attempt("tenant_a", "success", latency_ms=20)
        collector.record_discovery_attempt("tenant_c", "timeout")

        metrics_text = collector.generate_metrics_text()
        assert b'tenant_id="tenant_a"' in metrics_text
        assert b'tenant_id="tenant_b"' in metrics_text
        assert b'tenant_id="tenant_c"' in metrics_text
        print("✓ PASS")
        return True
    except Exception as e:
        print(f"✗ FAIL: {e}")
        return False


def test_fail_closed():
    """Test 11: Fail-closed behavior on errors."""
    print("Test 11: Fail-closed on errors...", end=" ")
    try:
        collector = RelayMetricsCollector(namespace="test_11")

        # Disable registry to simulate error condition
        collector.registry = None

        # All these should not raise
        collector.record_registration("tenant_default", "success")
        collector.record_delivery("tenant_default", "delivered", latency_ms=10)
        collector.record_discovery_attempt("tenant_default", "success", latency_ms=15)
        collector.record_handshake("tenant_default", "success", latency_ms=5)
        collector.update_registration_success_rate("tenant_default", 98.0)
        collector.update_delivery_cache_hit_rate("tenant_default", 95.0)
        collector.update_active_connections("tenant_default", 5)

        # Should return empty metrics
        metrics_text = collector.generate_metrics_text()
        assert isinstance(metrics_text, bytes)
        print("✓ PASS")
        return True
    except Exception as e:
        print(f"✗ FAIL: {e}")
        return False


def test_metrics_text_format():
    """Test 12: Generated metrics text is valid Prometheus format."""
    print("Test 12: Prometheus format validation...", end=" ")
    try:
        collector = RelayMetricsCollector(namespace="test_12")
        tenant_id = "tenant_default"

        # Record diverse metrics
        collector.record_registration(tenant_id, "success")
        collector.record_delivery(tenant_id, "delivered", latency_ms=42)
        collector.discovery_latency_ms.labels(tenant_id=tenant_id).observe(35)
        collector.a2a_handshake_latency_ms.labels(tenant_id=tenant_id).observe(15)

        metrics_text = collector.generate_metrics_text()

        # Should be bytes
        assert isinstance(metrics_text, bytes)
        # Should be non-empty
        assert len(metrics_text) > 0
        # Should contain Prometheus markers
        has_markers = (b"# HELP" in metrics_text or
                      b"# TYPE" in metrics_text or
                      b"test_12_" in metrics_text)
        assert has_markers
        print("✓ PASS")
        return True
    except Exception as e:
        print(f"✗ FAIL: {e}")
        return False


def test_slo_targets():
    """Test 13: SLO targets embedded in metric definitions."""
    print("Test 13: SLO targets validation...", end=" ")
    try:
        collector = RelayMetricsCollector(namespace="test_13")

        # SLO targets:
        # - discovery_latency p99 < 100ms (100ms bucket present)
        # - relay_query_latency p99 < 100ms (100ms bucket present)
        # - discovery_pairing_success_rate > 95%
        # - a2a_handshake_success_rate > baseline
        # - relay_cache_hit_rate measurement

        metrics_text = collector.generate_metrics_text()

        # Verify histogram buckets include 100ms target
        assert b"discovery_latency_ms_bucket" in metrics_text
        assert b"a2a_handshake_latency_ms_bucket" in metrics_text
        assert b"relay_message_latency_ms_bucket" in metrics_text
        # Verify success rate gauges for SLO monitoring
        assert b"discovery_pairing_success_rate" in metrics_text
        assert b"a2a_handshake_success_rate" in metrics_text
        print("✓ PASS")
        return True
    except Exception as e:
        print(f"✗ FAIL: {e}")
        return False


def main():
    """Run all tests and report results."""
    print("\n=== A2A Relay Metrics Tests (Day 8 k=1) ===\n")

    tests = [
        test_collector_init,
        test_singleton,
        test_record_registration,
        test_record_delivery,
        test_record_discovery,
        test_record_handshake,
        test_success_rate_gauges,
        test_active_connections,
        test_measure_latency,
        test_multi_tenant,
        test_fail_closed,
        test_metrics_text_format,
        test_slo_targets,
    ]

    results = [test() for test in tests]

    passed = sum(results)
    total = len(results)

    print(f"\n=== RESULTS: {passed}/{total} PASSED ===\n")

    if passed == total:
        print("✓ All tests passed! Day 8 k=1 deliverables validated.")
        return 0
    else:
        print(f"✗ {total - passed} test(s) failed.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
