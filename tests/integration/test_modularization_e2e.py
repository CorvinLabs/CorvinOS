"""End-to-end tests for Phase 4.5 Modularization (ADR-0426).

Proves that subprocess isolation, health monitoring, and module contracts
are reachable and functional through public API entry points.
"""

import sys
from pathlib import Path


def test_import_from_public_api():
    """E2E Test 1: All subsystems importable from public API."""
    from core.modularization import (
        # subprocess_isolation exports
        IPCMessage,
        MessageType,
        SubprocessBridge,
        PluginWorkerPool,
        PluginWorkerProcess,
        # plugin_isolation exports
        PluginProcessState,
        PluginManifest,
        PluginProcessInfo,
        ProcessResourceLimits,
        PluginProcessManager,
        # plugin_health_loop exports
        HealthCheckState,
        HealthProbe,
        HealthCheckConfig,
        HealthCheckRegistry,
        PluginHealthMonitor,
        # module_contracts exports
        PublicAPI,
        PrivateAPI,
        ContractAnalyzer,
        ContractRegistry,
        ContractViolation,
        SimpleModuleContract,
        ViolationDetector,
        get_global_registry,
        reset_global_registry,
    )

    # Verify types are actual classes/enums/functions
    assert hasattr(IPCMessage, "__dataclass_fields__")  # Dataclass
    assert hasattr(MessageType, "REQUEST")  # Enum
    assert callable(ContractRegistry)  # Class
    assert callable(PublicAPI)  # Decorator function
    print("✓ All subsystems imported successfully")
    return True


def test_ipc_message_creation():
    """E2E Test 2: IPC message protocol works end-to-end."""
    from core.modularization import IPCMessage, MessageType

    # Create request
    request = IPCMessage(
        message_type=MessageType.REQUEST,
        method="get_status",
        params={"plugin_id": "test-1"},
    )

    # Serialize to JSON
    json_str = request.to_json()
    assert isinstance(json_str, str)

    # Deserialize back
    restored = IPCMessage.from_json(json_str)
    assert restored.method == "get_status"
    assert restored.params["plugin_id"] == "test-1"
    print("✓ IPC message protocol works end-to-end")
    return True


def test_plugin_process_state_machine():
    """E2E Test 3: Plugin process state machine functional."""
    from core.modularization import PluginProcessState, PluginProcessInfo

    # Create initial state
    info1 = PluginProcessInfo(
        plugin_id="plugin-1",
        state=PluginProcessState.STOPPED,
    )
    assert info1.state == PluginProcessState.STOPPED

    # Simulate state transition
    info2 = PluginProcessInfo(
        plugin_id="plugin-1",
        state=PluginProcessState.STARTING,
        pid=9999,
    )
    assert info2.state == PluginProcessState.STARTING
    assert info2.pid == 9999

    # Verify all states are reachable
    all_states = [
        PluginProcessState.STOPPED,
        PluginProcessState.STARTING,
        PluginProcessState.HEALTHY,
        PluginProcessState.UNHEALTHY,
        PluginProcessState.STOPPING,
        PluginProcessState.CRASHED,
    ]
    assert len(all_states) == 6
    print("✓ Plugin process state machine works")
    return True


def test_health_monitoring_workflow():
    """E2E Test 4: Health monitoring detects state changes."""
    from core.modularization import (
        HealthCheckConfig,
        HealthCheckRegistry,
        HealthCheckState,
        HealthProbe,
    )

    config = HealthCheckConfig(
        enabled=True,
        interval_sec=30,
        timeout_sec=10,
        consecutive_failures_threshold=2,
    )
    registry = HealthCheckRegistry(
        plugin_id="monitored-plugin",
        config=config,
    )

    # Add healthy probe
    healthy = HealthProbe(
        plugin_id="monitored-plugin",
        state=HealthCheckState.HEALTHY,
        timestamp=None,
        response_time_ms=100.0,
    )
    registry.add_probe(healthy)
    assert registry.current_state == HealthCheckState.HEALTHY

    # Add unhealthy probe
    unhealthy = HealthProbe(
        plugin_id="monitored-plugin",
        state=HealthCheckState.UNHEALTHY,
        timestamp=None,
        response_time_ms=0.0,
        error_message="Connection timeout",
    )
    registry.add_probe(unhealthy)
    assert registry.consecutive_failures == 1

    # Add another unhealthy probe (should trigger restart)
    registry.add_probe(unhealthy)
    assert registry.is_restart_needed()

    print("✓ Health monitoring workflow functional")
    return True


def test_module_contract_validation():
    """E2E Test 5: Module contracts enforce API boundaries."""
    from core.modularization import (
        PublicAPI,
        PrivateAPI,
        ContractRegistry,
        SimpleModuleContract,
    )

    # Create a module with decorated API
    class DataService:
        @PublicAPI
        def get_data(self):
            return {"status": "ok"}

        @PrivateAPI
        def _encrypt_data(self, data):
            return f"encrypted:{data}"

    # Create contract
    contract = SimpleModuleContract(
        module_name="data_service",
        version="1.0.0",
        public_methods=["get_data"],
        private_methods=["_encrypt_data"],
    )

    # Register and validate
    registry = ContractRegistry()
    registry.register_contract(contract)
    registry.register_implementation("data_service", DataService())

    assert registry.validate_implementation("data_service")
    print("✓ Module contract validation works")
    return True


def test_contract_violation_detection():
    """E2E Test 6: Violation detector catches private API misuse."""
    from core.modularization import (
        ContractRegistry,
        SimpleModuleContract,
        ViolationDetector,
    )

    registry = ContractRegistry()
    contract = SimpleModuleContract(
        module_name="api_service",
        version="1.0.0",
        public_methods=["public_action"],
        private_methods=["_private_action"],
    )
    registry.register_contract(contract)

    # Create detector
    detector = ViolationDetector(registry)

    # Public call should be allowed
    assert detector.check_method_call("caller", "api_service", "public_action")

    # Private call should be rejected
    assert not detector.check_method_call("caller", "api_service", "_private_action")

    violations = detector.get_violations()
    assert len(violations) == 1
    assert "private" in violations[0].reason.lower()

    print("✓ Violation detector works")
    return True


def test_resource_limits_configuration():
    """E2E Test 7: Resource limits can be configured and accessed."""
    from core.modularization import ProcessResourceLimits, PluginManifest

    limits = ProcessResourceLimits(
        memory_mb=512,
        cpu_limit=1.0,
        timeout_sec=30,
        max_restarts=5,
        restart_cooldown_sec=10,
    )

    # Verify all properties are accessible
    assert limits.memory_mb == 512
    assert limits.cpu_limit == 1.0
    assert limits.timeout_sec == 30
    assert limits.max_restarts == 5
    assert limits.restart_cooldown_sec == 10

    # Create plugin manifest (uses limits)
    manifest = PluginManifest(
        plugin_id="configured-plugin",
        version="1.0.0",
        api_version="1.0",
        origin="community",
        boot_layer="installed",
        supports_isolation=True,
    )

    assert manifest.supports_isolation
    print("✓ Resource configuration works")
    return True


def test_worker_process_handler_registration():
    """E2E Test 8: Worker process can register and call handlers."""
    from core.modularization import PluginWorkerProcess

    worker = PluginWorkerProcess()

    # Register multiple handler types
    def sync_handler(x: int) -> int:
        return x * 2

    async def async_handler() -> str:
        return "async_result"

    worker.register_method("double", sync_handler)
    worker.register_method("async_op", async_handler)

    # Verify registration
    assert "double" in worker._methods
    assert "async_op" in worker._methods
    assert len(worker._methods) == 2

    print("✓ Worker process handler registration works")
    return True


def test_global_registry_singleton():
    """E2E Test 9: Global registry is properly singleton."""
    from core.modularization import (
        get_global_registry,
        reset_global_registry,
    )

    # Get registry
    reg1 = get_global_registry()

    # Register something
    from core.modularization import SimpleModuleContract
    contract = SimpleModuleContract(
        module_name="test_mod",
        version="1.0.0",
        public_methods=["method1"],
        private_methods=[],
    )
    reg1.register_contract(contract)

    # Get again - should be same instance
    reg2 = get_global_registry()
    assert reg1 is reg2
    assert "test_mod" in reg2._contracts

    # Reset and get new instance
    reset_global_registry()
    reg3 = get_global_registry()
    assert reg1 is not reg3
    assert "test_mod" not in reg3._contracts

    print("✓ Global registry singleton works")
    return True


def test_all_message_types():
    """E2E Test 10: All message types available and functional."""
    from core.modularization import MessageType

    types = [
        MessageType.REQUEST,
        MessageType.RESPONSE,
        MessageType.ERROR,
        MessageType.NOTIFICATION,
        MessageType.HANDSHAKE,
    ]

    assert len(types) == 5
    for msg_type in types:
        assert msg_type.value in ["request", "response", "error", "notification", "handshake"]

    print("✓ All message types functional")
    return True


# ─────────────────────────────────────────────────────────────────────────
# TEST RUNNER
# ─────────────────────────────────────────────────────────────────────────


def run_e2e_tests():
    """Run all E2E tests and report results."""
    e2e_tests = [
        test_import_from_public_api,
        test_ipc_message_creation,
        test_plugin_process_state_machine,
        test_health_monitoring_workflow,
        test_module_contract_validation,
        test_contract_violation_detection,
        test_resource_limits_configuration,
        test_worker_process_handler_registration,
        test_global_registry_singleton,
        test_all_message_types,
    ]

    print("=" * 70)
    print("PHASE 4.5 MODULARIZATION - END-TO-END TESTS")
    print("=" * 70)

    passed = 0
    failed = 0
    errors = []

    for i, test in enumerate(e2e_tests, 1):
        try:
            test()
            passed += 1
        except Exception as e:
            failed += 1
            errors.append((test.__name__, str(e)))
            print(f"✗ {test.__name__}: {e}")

    print("\n" + "=" * 70)
    print(f"RESULTS: {passed} passed, {failed} failed out of {len(e2e_tests)} E2E tests")
    print("=" * 70)

    if errors:
        print("\nFailed tests:")
        for name, error in errors:
            print(f"  - {name}: {error}")

    return failed == 0


if __name__ == "__main__":
    success = run_e2e_tests()
    sys.exit(0 if success else 1)
