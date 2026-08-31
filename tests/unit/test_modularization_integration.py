"""Integration tests for Phase 4.5 Modularization (ADR-0426).

Tests interaction between subprocess isolation, health monitoring, and module contracts.
"""

import asyncio
from core.modularization import (
    # subprocess_isolation
    IPCMessage,
    MessageType,
    PluginWorkerPool,
    PluginWorkerProcess,
    # plugin_isolation
    PluginProcessState,
    PluginManifest,
    PluginProcessInfo,
    ProcessResourceLimits,
    # plugin_health_loop
    HealthCheckState,
    HealthProbe,
    HealthCheckConfig,
    HealthCheckRegistry,
    # module_contracts
    PublicAPI,
    PrivateAPI,
    ContractRegistry,
    SimpleModuleContract,
)


# ─────────────────────────────────────────────────────────────────────────
# INTEGRATION TEST 1: MESSAGE PROTOCOL + WORKER PROCESS
# ─────────────────────────────────────────────────────────────────────────


def test_message_protocol_with_worker():
    """Test message protocol integration with worker process."""
    # Create a worker
    worker = PluginWorkerProcess()

    # Register a handler
    def add(x: int, y: int) -> int:
        return x + y

    worker.register_method("add", add)

    # Create a request message
    request = IPCMessage(
        message_type=MessageType.REQUEST,
        id="msg-1",
        method="add",
        params={"x": 5, "y": 3},
    )

    # Verify message serialization
    json_str = request.to_json()
    restored = IPCMessage.from_json(json_str)
    assert restored.method == "add"
    assert restored.params["x"] == 5


# ─────────────────────────────────────────────────────────────────────────
# INTEGRATION TEST 2: PLUGIN PROCESS STATE + HEALTH MONITORING
# ─────────────────────────────────────────────────────────────────────────


def test_plugin_state_with_health_registry():
    """Test plugin process state transitions with health registry."""
    # Create plugin manifest
    manifest = PluginManifest(
        plugin_id="test-plugin-1",
        version="1.0.0",
        api_version="1.0",
        origin="community",
        boot_layer="installed",
        supports_isolation=True,
    )

    # Create plugin process info
    info = PluginProcessInfo(
        plugin_id="test-plugin-1",
        state=PluginProcessState.STARTING,
        pid=12345,
    )

    # Create health check registry
    health_config = HealthCheckConfig(
        enabled=True,
        interval_sec=60,
        timeout_sec=10,
    )
    health_registry = HealthCheckRegistry(
        plugin_id="test-plugin-1",
        config=health_config,
    )

    # Simulate health probe
    probe = HealthProbe(
        plugin_id="test-plugin-1",
        state=HealthCheckState.HEALTHY,
        timestamp=None,  # Will use current time
        response_time_ms=50.0,
    )

    health_registry.add_probe(probe)
    assert health_registry.current_state == HealthCheckState.HEALTHY
    assert not health_registry.is_restart_needed()


# ─────────────────────────────────────────────────────────────────────────
# INTEGRATION TEST 3: MODULE CONTRACTS WITH PLUGIN MANIFEST
# ─────────────────────────────────────────────────────────────────────────


class PluginAPI:
    """Mock plugin with contract-defined API."""

    @PublicAPI
    def get_config(self):
        return {"enabled": True}

    @PublicAPI
    def set_config(self, config):
        pass

    @PrivateAPI
    def _internal_init(self):
        pass


def test_module_contract_enforcement():
    """Test that contracts can be enforced on plugin APIs."""
    # Create contract for plugin
    contract = SimpleModuleContract(
        module_name="plugin_api",
        version="1.0.0",
        public_methods=["get_config", "set_config"],
        private_methods=["_internal_init"],
    )

    # Create registry and register
    registry = ContractRegistry()
    registry.register_contract(contract)
    impl = PluginAPI()
    registry.register_implementation("plugin_api", impl)

    # Validate
    assert registry.validate_implementation("plugin_api")

    # Verify public methods are accessible
    assert contract.has_public_method("get_config")
    assert contract.has_public_method("set_config")
    assert contract.has_private_method("_internal_init")


# ─────────────────────────────────────────────────────────────────────────
# INTEGRATION TEST 4: PROCESS RESOURCE LIMITS + HEALTH CONFIG
# ─────────────────────────────────────────────────────────────────────────


def test_resource_limits_with_health_config():
    """Test that resource limits and health config work together."""
    # Resource limits for subprocess
    limits = ProcessResourceLimits(
        memory_mb=256,
        cpu_limit=0.5,
        timeout_sec=15,
        max_restarts=3,
        restart_cooldown_sec=5,
    )

    # Health check config for monitoring
    health_config = HealthCheckConfig(
        enabled=True,
        interval_sec=30,
        timeout_sec=10,
        consecutive_failures_threshold=2,
        degraded_threshold_ms=2000,
    )

    # Verify they're compatible
    assert limits.timeout_sec > health_config.timeout_sec
    assert limits.max_restarts > 0
    assert health_config.consecutive_failures_threshold > 1


# ─────────────────────────────────────────────────────────────────────────
# INTEGRATION TEST 5: MESSAGE TYPES COVERAGE
# ─────────────────────────────────────────────────────────────────────────


def test_all_message_types():
    """Test all message types used in IPC."""
    # Request
    req = IPCMessage(message_type=MessageType.REQUEST, method="test")
    assert req.message_type == MessageType.REQUEST

    # Response
    resp = IPCMessage(message_type=MessageType.RESPONSE, result="ok")
    assert resp.message_type == MessageType.RESPONSE

    # Error
    err = IPCMessage(message_type=MessageType.ERROR, error="failed")
    assert err.message_type == MessageType.ERROR

    # Notification
    notif = IPCMessage(message_type=MessageType.NOTIFICATION, method="log")
    assert notif.message_type == MessageType.NOTIFICATION

    # Handshake
    hs = IPCMessage(message_type=MessageType.HANDSHAKE)
    assert hs.message_type == MessageType.HANDSHAKE


# ─────────────────────────────────────────────────────────────────────────
# INTEGRATION TEST 6: PLUGIN MANIFEST METADATA
# ─────────────────────────────────────────────────────────────────────────


def test_plugin_manifest_metadata():
    """Test plugin manifest with all metadata."""
    manifest = PluginManifest(
        plugin_id="analytics-plugin",
        version="2.1.0",
        api_version="2.0",
        origin="vetted",
        boot_layer="bundled",
        supports_isolation=False,  # Bundled plugins don't isolate
        requires_ipc=False,
    )

    assert manifest.plugin_id == "analytics-plugin"
    assert manifest.origin == "vetted"
    assert not manifest.supports_isolation  # Bundled = no isolation


# ─────────────────────────────────────────────────────────────────────────
# INTEGRATION TEST 7: HEALTH STATE MACHINE
# ─────────────────────────────────────────────────────────────────────────


def test_health_state_machine():
    """Test health probe state machine."""
    config = HealthCheckConfig(
        enabled=True,
        consecutive_failures_threshold=3,
    )
    registry = HealthCheckRegistry(
        plugin_id="test",
        config=config,
    )

    # Start: UNKNOWN
    assert registry.current_state == HealthCheckState.UNKNOWN
    assert registry.consecutive_failures == 0

    # First healthy probe
    probe1 = HealthProbe(
        plugin_id="test",
        state=HealthCheckState.HEALTHY,
        timestamp=None,
        response_time_ms=100.0,
    )
    registry.add_probe(probe1)
    assert registry.current_state == HealthCheckState.HEALTHY
    assert registry.consecutive_failures == 0

    # Degraded probe (doesn't trigger restart)
    probe2 = HealthProbe(
        plugin_id="test",
        state=HealthCheckState.DEGRADED,
        timestamp=None,
        response_time_ms=5000.0,
    )
    registry.add_probe(probe2)
    assert registry.current_state == HealthCheckState.DEGRADED
    assert not registry.is_restart_needed()

    # Multiple unhealthy probes
    for i in range(3):
        probe_u = HealthProbe(
            plugin_id="test",
            state=HealthCheckState.UNHEALTHY,
            timestamp=None,
            response_time_ms=0.0,
            error_message=f"Attempt {i+1}",
        )
        registry.add_probe(probe_u)

    # After 3 unhealthy probes, should trigger restart
    assert registry.is_restart_needed()


# ─────────────────────────────────────────────────────────────────────────
# INTEGRATION TEST 8: CONTRACT VALIDATION WITH MULTIPLE MODULES
# ─────────────────────────────────────────────────────────────────────────


class ServiceA:
    @PublicAPI
    def fetch_data(self):
        return "data"


class ServiceB:
    @PublicAPI
    def process_data(self, data):
        return f"processed: {data}"


def test_multi_module_contracts():
    """Test contracts on multiple interdependent modules."""
    registry = ContractRegistry()

    # Register contracts
    contract_a = SimpleModuleContract(
        module_name="service_a",
        version="1.0.0",
        public_methods=["fetch_data"],
        private_methods=[],
    )
    contract_b = SimpleModuleContract(
        module_name="service_b",
        version="1.0.0",
        public_methods=["process_data"],
        private_methods=[],
    )

    registry.register_contract(contract_a)
    registry.register_contract(contract_b)

    registry.register_implementation("service_a", ServiceA())
    registry.register_implementation("service_b", ServiceB())

    # Validate both
    assert registry.validate_implementation("service_a")
    assert registry.validate_implementation("service_b")
    assert registry.validate_all()


# ─────────────────────────────────────────────────────────────────────────
# RUN TESTS (for manual execution without pytest)
# ─────────────────────────────────────────────────────────────────────────


def run_all_tests():
    """Run all integration tests."""
    tests = [
        test_message_protocol_with_worker,
        test_plugin_state_with_health_registry,
        test_module_contract_enforcement,
        test_resource_limits_with_health_config,
        test_all_message_types,
        test_plugin_manifest_metadata,
        test_health_state_machine,
        test_multi_module_contracts,
    ]

    passed = 0
    failed = 0

    for test in tests:
        try:
            test()
            print(f"✓ {test.__name__}")
            passed += 1
        except Exception as e:
            print(f"✗ {test.__name__}: {e}")
            failed += 1

    print(f"\n{passed} passed, {failed} failed out of {len(tests)} tests")
    return failed == 0


if __name__ == "__main__":
    success = run_all_tests()
    exit(0 if success else 1)
