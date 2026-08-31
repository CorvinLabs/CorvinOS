"""Plugin modularization layer (Phase 4.5, ADR-0426).

Subprocess isolation, lifecycle management, health monitoring, and module contracts
for out-of-process plugins.

Four subsystems:
1. plugin_isolation: subprocess lifecycle (start/stop/restart/version discovery)
2. plugin_health_loop: health monitoring and auto-restart orchestration
3. subprocess_isolation: lightweight IPC communication (message protocol)
4. module_contracts: module interface enforcement and violation detection
"""

from .plugin_isolation import (
    PluginProcessState,
    PluginManifest,
    PluginProcessInfo,
    ProcessResourceLimits,
    PluginProcessManager,
)
from .plugin_health_loop import (
    HealthCheckState,
    HealthProbe,
    HealthCheckConfig,
    HealthCheckRegistry,
    PluginHealthMonitor,
)
from .subprocess_isolation import (
    IPCMessage,
    MessageType,
    SubprocessBridge,
    PluginWorkerPool,
    PluginWorkerProcess,
)
from .module_contracts import (
    ContractAnalyzer,
    ContractRegistry,
    ContractViolation,
    MethodSignature,
    ModuleContract,
    PrivateAPI,
    PublicAPI,
    SimpleModuleContract,
    ViolationDetector,
    get_global_registry,
    reset_global_registry,
)

__all__ = [
    # plugin_isolation
    "PluginProcessState",
    "PluginManifest",
    "PluginProcessInfo",
    "ProcessResourceLimits",
    "PluginProcessManager",
    # plugin_health_loop
    "HealthCheckState",
    "HealthProbe",
    "HealthCheckConfig",
    "HealthCheckRegistry",
    "PluginHealthMonitor",
    # subprocess_isolation
    "IPCMessage",
    "MessageType",
    "SubprocessBridge",
    "PluginWorkerPool",
    "PluginWorkerProcess",
    # module_contracts
    "PublicAPI",
    "PrivateAPI",
    "ModuleContract",
    "SimpleModuleContract",
    "MethodSignature",
    "ContractRegistry",
    "ContractAnalyzer",
    "ViolationDetector",
    "ContractViolation",
    "get_global_registry",
    "reset_global_registry",
]
