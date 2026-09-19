"""
Unit Tests for Worker Monitor (B2 Task)
Uses unittest instead of pytest for compatibility.
Tests health checking, alert triggering, metric aggregation, and E2E scenarios.
"""

import unittest
import threading
import time
from datetime import datetime, timedelta
from core.orchestrator.worker_monitor import (
    WorkerMonitor,
    WorkerRegistry,
    HealthChecker,
    MetricsCollector,
    AlertSystem,
    WorkerMetrics,
    Alert,
    AlertSeverity,
    WorkerStatus,
    get_monitor,
)


class TestWorkerRegistry(unittest.TestCase):
    """Test WorkerRegistry — track active workers"""

    def test_register_worker(self):
        """Test registering a worker"""
        registry = WorkerRegistry()
        metrics = WorkerMetrics(
            worker_id="w1",
            tenant_id="_default",
            timestamp=datetime.utcnow(),
        )

        registry.register(metrics)
        self.assertEqual(registry.count(), 1)
        self.assertEqual(registry.get("w1"), metrics)

    def test_deregister_worker(self):
        """Test deregistering a worker"""
        registry = WorkerRegistry()
        metrics = WorkerMetrics(
            worker_id="w1",
            tenant_id="_default",
            timestamp=datetime.utcnow(),
        )

        registry.register(metrics)
        self.assertEqual(registry.count(), 1)

        registry.deregister("w1")
        self.assertEqual(registry.count(), 0)
        self.assertIsNone(registry.get("w1"))

    def test_get_all_workers(self):
        """Test retrieving all workers"""
        registry = WorkerRegistry()

        for i in range(5):
            metrics = WorkerMetrics(
                worker_id=f"w{i}",
                tenant_id="_default",
                timestamp=datetime.utcnow(),
            )
            registry.register(metrics)

        workers = registry.get_all()
        self.assertEqual(len(workers), 5)

    def test_tenant_isolation(self):
        """Test tenant-scoped worker retrieval"""
        registry = WorkerRegistry()

        # Register workers in different tenants
        for tenant in ["tenant1", "tenant2"]:
            for i in range(3):
                metrics = WorkerMetrics(
                    worker_id=f"{tenant}-w{i}",
                    tenant_id=tenant,
                    timestamp=datetime.utcnow(),
                )
                registry.register(metrics)

        # Should only return workers from tenant1
        tenant1_workers = registry.get_all("tenant1")
        self.assertEqual(len(tenant1_workers), 3)
        self.assertTrue(all(w.tenant_id == "tenant1" for w in tenant1_workers))


class TestMetricsCollector(unittest.TestCase):
    """Test MetricsCollector — aggregate performance metrics"""

    def test_record_and_retrieve_latency(self):
        """Test latency recording and percentile calculation"""
        collector = MetricsCollector()

        # Record latencies: 10, 20, ..., 100 ms
        for i in range(1, 11):
            collector.record_latency("w1", float(i * 10))

        # p50 should be around 55 (median of 10-100)
        p50 = collector.get_latency_percentile("w1", 50.0)
        self.assertIsNotNone(p50)
        self.assertTrue(40 <= p50 <= 60)

        # p99 should be close to 100
        p99 = collector.get_latency_percentile("w1", 99.0)
        self.assertIsNotNone(p99)
        self.assertGreaterEqual(p99, 90)

    def test_record_and_retrieve_error_rate(self):
        """Test error rate tracking"""
        collector = MetricsCollector()

        # Record error rates: 0.01, 0.02, ..., 0.10
        for i in range(1, 11):
            collector.record_error("w1", float(i) / 100)

        avg = collector.get_avg_error_rate("w1")
        self.assertIsNotNone(avg)
        self.assertTrue(0.05 <= avg <= 0.06)

    def test_record_and_retrieve_throughput(self):
        """Test throughput tracking"""
        collector = MetricsCollector()

        # Record throughputs: 10, 20, ..., 100 tasks/min
        for i in range(1, 11):
            collector.record_throughput("w1", i * 10)

        avg = collector.get_avg_throughput("w1")
        self.assertIsNotNone(avg)
        self.assertTrue(50 <= avg <= 60)

    def test_sliding_window_size(self):
        """Test window size constraint"""
        collector = MetricsCollector(window_size=5)

        # Record 10 values; only last 5 should be kept
        for i in range(10):
            collector.record_latency("w1", float(i))

        # Percentile should be based on last 5 values (5-9)
        p50 = collector.get_latency_percentile("w1", 50.0)
        self.assertIsNotNone(p50)
        # Should be between 5 and 9
        self.assertTrue(5 <= p50 <= 9)

    def test_clear_metrics(self):
        """Test clearing metrics"""
        collector = MetricsCollector()

        collector.record_latency("w1", 100)
        collector.record_latency("w2", 200)

        # Clear w1
        collector.clear("w1")
        self.assertIsNone(collector.get_avg_latency("w1"))
        self.assertIsNotNone(collector.get_avg_latency("w2"))


class TestAlertSystem(unittest.TestCase):
    """Test AlertSystem — threshold violations and alert triggering"""

    def test_alert_on_critical_cpu(self):
        """Test alert when CPU exceeds critical threshold"""
        alert_system = AlertSystem()

        # CPU 85% > 80% critical threshold
        alerts = alert_system.check_and_alert(
            "w1",
            "_default",
            {"cpu_percent": 85.0, "memory_mb": 500.0, "latency_p99_ms": 1000.0, "error_rate": 0.01},
        )

        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0].severity, AlertSeverity.CRITICAL)
        self.assertEqual(alerts[0].metric_name, "cpu_percent")

    def test_alert_on_warning_cpu(self):
        """Test alert when CPU exceeds warning threshold"""
        alert_system = AlertSystem()

        # CPU 65% > 60% warning threshold, < 80% critical
        alerts = alert_system.check_and_alert(
            "w1",
            "_default",
            {"cpu_percent": 65.0, "memory_mb": 500.0, "latency_p99_ms": 1000.0, "error_rate": 0.01},
        )

        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0].severity, AlertSeverity.WARNING)

    def test_alert_on_multiple_metrics(self):
        """Test multiple alerts on same check"""
        alert_system = AlertSystem()

        # Both CPU and error rate exceed thresholds
        alerts = alert_system.check_and_alert(
            "w1",
            "_default",
            {"cpu_percent": 85.0, "memory_mb": 500.0, "latency_p99_ms": 1000.0, "error_rate": 0.15},
        )

        self.assertEqual(len(alerts), 2)
        metric_names = {a.metric_name for a in alerts}
        self.assertIn("cpu_percent", metric_names)
        self.assertIn("error_rate", metric_names)

    def test_no_alert_within_thresholds(self):
        """Test no alert when metrics are healthy"""
        alert_system = AlertSystem()

        alerts = alert_system.check_and_alert(
            "w1",
            "_default",
            {"cpu_percent": 30.0, "memory_mb": 300.0, "latency_p99_ms": 500.0, "error_rate": 0.01},
        )

        self.assertEqual(len(alerts), 0)

    def test_alert_history(self):
        """Test alert history retrieval"""
        alert_system = AlertSystem()

        # Emit 5 alerts
        for i in range(5):
            alert_system.check_and_alert(
                f"w{i}",
                "_default",
                {"cpu_percent": 85.0, "memory_mb": 500.0, "latency_p99_ms": 1000.0, "error_rate": 0.01},
            )

        # Retrieve all alerts
        alerts = alert_system.get_alerts()
        self.assertEqual(len(alerts), 5)

    def test_alert_callback(self):
        """Test alert callback invocation"""
        alert_system = AlertSystem()
        received_alerts = []

        def capture_alert(alert: Alert):
            received_alerts.append(alert)

        alert_system.add_callback(capture_alert)

        alert_system.check_and_alert(
            "w1",
            "_default",
            {"cpu_percent": 85.0, "memory_mb": 500.0, "latency_p99_ms": 1000.0, "error_rate": 0.01},
        )

        self.assertEqual(len(received_alerts), 1)
        self.assertEqual(received_alerts[0].severity, AlertSeverity.CRITICAL)

    def test_custom_thresholds(self):
        """Test overriding default thresholds"""
        alert_system = AlertSystem()

        # Set custom threshold: CPU critical at 50% (lower than default 80%)
        alert_system.set_thresholds({
            "cpu_percent": {"critical": 50.0, "warning": 35.0},
        })

        alerts = alert_system.check_and_alert(
            "w1",
            "_default",
            {"cpu_percent": 55.0, "memory_mb": 500.0, "latency_p99_ms": 1000.0, "error_rate": 0.01},
        )

        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0].severity, AlertSeverity.CRITICAL)


class TestHealthChecker(unittest.TestCase):
    """Test HealthChecker — periodic health probes"""

    def test_health_checker_start_stop(self):
        """Test starting and stopping health checker"""
        registry = WorkerRegistry()
        checker = HealthChecker(registry, check_interval_sec=0.1)

        # Should not be running initially
        self.assertFalse(checker._running)

        checker.start()
        self.assertTrue(checker._running)
        time.sleep(0.2)  # Let it run briefly

        checker.stop()
        self.assertFalse(checker._running)

    def test_health_checker_probes_workers(self):
        """Test that health checker probes registered workers"""
        registry = WorkerRegistry()
        checker = HealthChecker(registry, check_interval_sec=0.1)

        # Register a worker
        metrics = WorkerMetrics(
            worker_id="w1",
            tenant_id="_default",
            timestamp=datetime.utcnow(),
            last_heartbeat=datetime.utcnow() - timedelta(seconds=10),  # Old heartbeat
        )
        registry.register(metrics)

        # Set probe function
        probe_calls = []
        def mock_probe(worker_id):
            probe_calls.append(worker_id)
            return {"cpu": 50.0, "memory": 300.0, "alive": True}

        checker.set_probe_function(mock_probe)

        checker.start()
        time.sleep(0.3)  # Let it probe
        checker.stop()

        # Should have probed w1 at least once
        self.assertIn("w1", probe_calls)

        # Heartbeat should be updated
        updated = registry.get("w1")
        self.assertIsNotNone(updated)
        # Heartbeat should be recent
        age = (datetime.utcnow() - updated.last_heartbeat).total_seconds()
        self.assertLess(age, 1.0)


class TestWorkerMonitor(unittest.TestCase):
    """Test WorkerMonitor — unified monitoring orchestrator"""

    def test_register_and_monitor_worker(self):
        """Test registering a worker and monitoring it"""
        monitor = WorkerMonitor("_default")

        monitor.register_worker("w1")

        # Worker should be in registry
        self.assertEqual(monitor.registry.count(), 1)

        # Get worker status
        status = monitor.get_worker_status("w1")
        self.assertIsNotNone(status)
        self.assertEqual(status["worker_id"], "w1")
        self.assertEqual(status["status"], "healthy")

    def test_update_worker_metrics_triggers_alerts(self):
        """Test that updating metrics can trigger alerts"""
        monitor = WorkerMonitor("_default")

        monitor.register_worker("w1")

        # Update CPU to 85% (exceeds critical threshold)
        monitor.update_worker_metrics("w1", cpu_percent=85.0)

        # Should trigger critical alert
        alerts = monitor.alert_system.get_alerts("w1")
        self.assertGreater(len(alerts), 0)
        self.assertTrue(any(a.severity == AlertSeverity.CRITICAL for a in alerts))

    def test_worker_status_computation(self):
        """Test worker status computation based on metrics"""
        monitor = WorkerMonitor("_default")

        monitor.register_worker("w1")

        # Healthy: all metrics within thresholds
        monitor.update_worker_metrics("w1", cpu_percent=30.0, error_rate=0.01)
        status1 = monitor.get_worker_status("w1")
        self.assertEqual(status1["status"], "healthy")

        # Degraded: CPU above 60%
        monitor.update_worker_metrics("w1", cpu_percent=70.0)
        status2 = monitor.get_worker_status("w1")
        self.assertEqual(status2["status"], "degraded")

        # Unhealthy: CPU above 80%
        monitor.update_worker_metrics("w1", cpu_percent=90.0)
        status3 = monitor.get_worker_status("w1")
        self.assertEqual(status3["status"], "unhealthy")

    def test_cluster_status_aggregation(self):
        """Test aggregating cluster status across workers"""
        monitor = WorkerMonitor("_default")

        # Register 3 workers
        for i in range(3):
            monitor.register_worker(f"w{i}")
            monitor.update_worker_metrics(f"w{i}", cpu_percent=30.0 + i * 20)

        cluster = monitor.get_cluster_status()
        self.assertEqual(cluster["worker_count"], 3)
        self.assertEqual(cluster["healthy"] + cluster["degraded"] + cluster["unhealthy"], 3)

    def test_export_metrics_json(self):
        """Test exporting metrics as JSON"""
        monitor = WorkerMonitor("_default")

        monitor.register_worker("w1")
        monitor.update_worker_metrics("w1", cpu_percent=50.0, latency_ms=100.0)

        export = monitor.export_metrics()

        self.assertEqual(export["tenant_id"], "_default")
        self.assertIn("cluster_status", export)
        self.assertIn("workers", export)
        self.assertEqual(len(export["workers"]), 1)
        self.assertEqual(export["workers"][0]["worker_id"], "w1")

    def test_deregister_worker(self):
        """Test deregistering a worker"""
        monitor = WorkerMonitor("_default")

        monitor.register_worker("w1")
        self.assertEqual(monitor.registry.count(), 1)

        monitor.deregister_worker("w1")
        self.assertEqual(monitor.registry.count(), 0)

    def test_latency_percentiles(self):
        """Test latency percentile tracking"""
        monitor = WorkerMonitor("_default")

        monitor.register_worker("w1")

        # Record latencies
        for i in range(1, 11):
            monitor.update_worker_metrics("w1", latency_ms=float(i * 10))

        status = monitor.get_worker_status("w1")

        # Should have p50 and p99
        self.assertIsNotNone(status["latency_p50_ms"])
        self.assertIsNotNone(status["latency_p99_ms"])
        # p99 should be higher than p50
        self.assertGreaterEqual(status["latency_p99_ms"], status["latency_p50_ms"])


class TestWorkerSimulation(unittest.TestCase):
    """Test with simulated workers (configurable latency/error)"""

    def test_simulated_healthy_worker(self):
        """Test monitoring a healthy simulated worker"""
        monitor = WorkerMonitor("_default")
        monitor.register_worker("sim_w1")

        # Simulate healthy operation
        for i in range(10):
            monitor.update_worker_metrics(
                "sim_w1",
                cpu_percent=35.0,
                memory_mb=250.0,
                latency_ms=50.0 + i,
                error_rate=0.01,
            )

        status = monitor.get_worker_status("sim_w1")
        self.assertEqual(status["status"], "healthy")
        self.assertIsNotNone(status["latency_p50_ms"])
        self.assertTrue(50 <= status["latency_p50_ms"] <= 60)

    def test_simulated_degraded_worker(self):
        """Test monitoring a degraded simulated worker"""
        monitor = WorkerMonitor("_default")
        monitor.register_worker("sim_w2")

        # Simulate degraded operation (high CPU)
        for i in range(10):
            monitor.update_worker_metrics(
                "sim_w2",
                cpu_percent=70.0,
                memory_mb=350.0,
                latency_ms=150.0,
                error_rate=0.03,
            )

        status = monitor.get_worker_status("sim_w2")
        self.assertEqual(status["status"], "degraded")

    def test_simulated_failing_worker(self):
        """Test monitoring a failing simulated worker"""
        monitor = WorkerMonitor("_default")
        monitor.register_worker("sim_w3")

        # First, register with normal metrics
        monitor.update_worker_metrics("sim_w3", cpu_percent=30.0)

        # Simulate failure by not updating heartbeat for >5s
        metrics = monitor.registry.get("sim_w3")
        metrics.last_heartbeat = datetime.utcnow() - timedelta(seconds=6)
        monitor.registry.register(metrics)

        status = monitor.get_worker_status("sim_w3")
        self.assertEqual(status["status"], "unhealthy")


class TestGlobalMonitor(unittest.TestCase):
    """Test global monitor factory"""

    def test_get_monitor_singleton(self):
        """Test get_monitor returns singleton per tenant"""
        m1 = get_monitor("tenant1")
        m2 = get_monitor("tenant1")

        # Should be same instance
        self.assertIs(m1, m2)

    def test_tenant_isolation_in_global_factory(self):
        """Test that different tenants get different monitors"""
        m1 = get_monitor("tenant1")
        m2 = get_monitor("tenant2")

        # Should be different instances
        self.assertIsNot(m1, m2)

        # Register workers in different tenants
        m1.register_worker("w1")
        m2.register_worker("w2")

        # Each monitor should only see its own workers
        self.assertEqual(m1.registry.count("tenant1"), 1)
        self.assertEqual(m2.registry.count("tenant2"), 1)


class TestE2EWorkerMonitor(unittest.TestCase):
    """End-to-end test: simulate worker lifecycle"""

    def test_worker_lifecycle_e2e(self):
        """E2E test: worker registration, operation, health check, failure"""
        monitor = WorkerMonitor("_default")

        # 1. Register worker
        monitor.register_worker("e2e_w1")
        self.assertEqual(monitor.registry.count(), 1)

        # 2. Normal operation
        for i in range(5):
            monitor.update_worker_metrics(
                "e2e_w1",
                cpu_percent=40.0 + i,
                latency_ms=50.0,
                error_rate=0.01,
                tasks_completed=10 * (i + 1),
            )
            time.sleep(0.01)

        # 3. Check status is healthy
        status = monitor.get_worker_status("e2e_w1")
        self.assertEqual(status["status"], "healthy")
        self.assertEqual(status["metrics"]["tasks_completed"], 50)

        # 4. Simulate degradation
        for i in range(3):
            monitor.update_worker_metrics(
                "e2e_w1",
                cpu_percent=75.0,  # Degraded
                error_rate=0.04,
            )

        status = monitor.get_worker_status("e2e_w1")
        self.assertEqual(status["status"], "degraded")

        # 5. Deregister
        monitor.deregister_worker("e2e_w1")
        self.assertEqual(monitor.registry.count(), 0)

    def test_cluster_monitoring_e2e(self):
        """E2E test: monitor cluster of multiple workers"""
        monitor = WorkerMonitor("_default")

        # Simulate cluster: 10 workers
        for i in range(10):
            monitor.register_worker(f"cluster_w{i}")

        # 75% healthy, 25% degraded
        for i in range(10):
            cpu = 35.0 if i < 7 else 70.0
            monitor.update_worker_metrics(f"cluster_w{i}", cpu_percent=cpu)

        cluster = monitor.get_cluster_status()
        self.assertEqual(cluster["worker_count"], 10)
        self.assertEqual(cluster["healthy"], 7)
        self.assertGreaterEqual(cluster["degraded"], 2)


if __name__ == "__main__":
    # Run tests
    unittest.main(verbosity=2)
