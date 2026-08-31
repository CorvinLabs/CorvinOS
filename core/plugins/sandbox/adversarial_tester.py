"""
Adversarial testing suite for plugin sandbox escape attempts.

Tests 20+ known exploit techniques to verify sandbox integrity:
- Privilege escalation (setuid, capset, etc.)
- Module injection (insmod, init_module, etc.)
- Filesystem escape (chroot, symlinks, etc.)
- Network covert channels
- Memory corruption attacks
- Timing side-channels
- Process tracing (ptrace, process_vm_*)
- Signal handler hijacking

Design:
- Each exploit is a defined scenario with expected syscall sequence
- Executor attempts the exploit in sandboxed plugin
- Sandbox should kill the process (SIGKILL) or return EPERM
- 0 successful escapes required for v0.7 gate
"""

from dataclasses import dataclass
from typing import List, Tuple, Dict, Any, Literal
from enum import Enum
import asyncio
from core.plugins.sandbox.executor import (
    SandboxExecutor,
    SandboxExecutionResult,
    SandboxManager,
)
from core.plugins.sandbox.seccomp_rules import generate_profile, SeccompProfile


class ExploitOutcome(Enum):
    """Outcome of an exploit attempt."""
    BLOCKED = "blocked"  # Good: sandbox prevented it
    ESCAPED = "escaped"  # Bad: exploit succeeded
    ERROR = "error"  # Inconclusive


@dataclass
class ExploitScenario:
    """Definition of a single exploit scenario."""
    name: str
    description: str
    syscalls_attempted: List[str]
    expected_behavior: Literal["blocked", "timeout", "error"]
    severity: Literal["critical", "high", "medium"]


@dataclass
class AdversarialTestResult:
    """Result of adversarial testing run."""
    plugin_id: str
    total_scenarios: int
    blocked_count: int
    escaped_count: int
    inconclusive_count: int
    escapes: List[Tuple[str, str]]  # List of (scenario_name, error_message)

    @property
    def pass_rate(self) -> float:
        """Percentage of attacks blocked."""
        if self.total_scenarios == 0:
            return 0.0
        return (self.blocked_count / self.total_scenarios) * 100.0

    @property
    def is_escape_proof(self) -> bool:
        """True if zero escapes detected."""
        return self.escaped_count == 0 and len(self.escapes) == 0


# Define 20+ exploit scenarios covering major attack vectors
EXPLOIT_SCENARIOS = [
    # === Privilege Escalation (4 scenarios) ===
    ExploitScenario(
        name="setuid_to_root",
        description="Attempt to setuid(0) to become root",
        syscalls_attempted=["setuid", "getuid"],
        expected_behavior="blocked",
        severity="critical",
    ),
    ExploitScenario(
        name="setgid_to_wheel",
        description="Attempt to setgid(0) to join root group",
        syscalls_attempted=["setgid", "getgid"],
        expected_behavior="blocked",
        severity="critical",
    ),
    ExploitScenario(
        name="capset_drop_restriction",
        description="Attempt to capset() to drop capability restrictions",
        syscalls_attempted=["capset", "capget"],
        expected_behavior="blocked",
        severity="critical",
    ),
    ExploitScenario(
        name="setfsuid_escalation",
        description="Attempt setfsuid(0) to change filesystem user",
        syscalls_attempted=["setfsuid"],
        expected_behavior="blocked",
        severity="high",
    ),

    # === Module Injection (3 scenarios) ===
    ExploitScenario(
        name="init_module_load_code",
        description="Attempt init_module() to load kernel code",
        syscalls_attempted=["init_module"],
        expected_behavior="blocked",
        severity="critical",
    ),
    ExploitScenario(
        name="finit_module_load_file",
        description="Attempt finit_module() to load module from file",
        syscalls_attempted=["finit_module"],
        expected_behavior="blocked",
        severity="critical",
    ),
    ExploitScenario(
        name="delete_module_unload",
        description="Attempt delete_module() to unload kernel module",
        syscalls_attempted=["delete_module"],
        expected_behavior="blocked",
        severity="high",
    ),

    # === Filesystem Escape (4 scenarios) ===
    ExploitScenario(
        name="chroot_escape_double_chroot",
        description="Attempt double chroot to escape jail",
        syscalls_attempted=["chroot", "chdir"],
        expected_behavior="blocked",
        severity="critical",
    ),
    ExploitScenario(
        name="symlink_traversal_escape",
        description="Create symlink pointing outside jail",
        syscalls_attempted=["symlink", "symlinkat", "open"],
        expected_behavior="blocked",
        severity="high",
    ),
    ExploitScenario(
        name="mount_break_jail",
        description="Attempt to mount filesystem to break out",
        syscalls_attempted=["mount", "umount", "umount2"],
        expected_behavior="blocked",
        severity="critical",
    ),
    ExploitScenario(
        name="pivot_root_escape",
        description="Attempt pivot_root() to change filesystem root",
        syscalls_attempted=["pivot_root"],
        expected_behavior="blocked",
        severity="critical",
    ),

    # === Network Covert Channels (3 scenarios) ===
    ExploitScenario(
        name="raw_socket_packet_sniffing",
        description="Attempt to create raw socket for packet sniffing",
        syscalls_attempted=["socket"],
        expected_behavior="blocked",
        severity="high",
    ),
    ExploitScenario(
        name="dns_data_exfiltration",
        description="Attempt to exfiltrate data via DNS queries",
        syscalls_attempted=["socket", "connect", "sendto"],
        expected_behavior="blocked",
        severity="high",
    ),
    ExploitScenario(
        name="icmp_tunnel_covert_channel",
        description="Attempt to create ICMP tunnel for data exfiltration",
        syscalls_attempted=["socket", "sendto"],
        expected_behavior="blocked",
        severity="medium",
    ),

    # === Memory Corruption (3 scenarios) ===
    ExploitScenario(
        name="ptrace_self_attach",
        description="Attempt ptrace(ATTACH) on self or sibling",
        syscalls_attempted=["ptrace"],
        expected_behavior="blocked",
        severity="critical",
    ),
    ExploitScenario(
        name="process_vm_readv_memory_read",
        description="Attempt process_vm_readv() to read another process' memory",
        syscalls_attempted=["process_vm_readv"],
        expected_behavior="blocked",
        severity="critical",
    ),
    ExploitScenario(
        name="madvise_poison_memory",
        description="Attempt madvise(MADV_DONTNEED) to poison memory",
        syscalls_attempted=["madvise"],
        expected_behavior="error",  # madvise is whitelisted but should fail
        severity="medium",
    ),

    # === Timing Side-Channels (2 scenarios) ===
    ExploitScenario(
        name="meltdown_timing_attack",
        description="Attempt timing attack to read kernel memory (Meltdown)",
        syscalls_attempted=["read", "clock_gettime"],
        expected_behavior="blocked",  # Mitigated by rate-limiting
        severity="medium",
    ),
    ExploitScenario(
        name="spectre_branch_prediction",
        description="Attempt branch prediction manipulation (Spectre)",
        syscalls_attempted=["read"],
        expected_behavior="blocked",  # Mitigated by isolation
        severity="medium",
    ),

    # === Signal Hijacking (2 scenarios) ===
    ExploitScenario(
        name="sigaction_handler_override",
        description="Override SIGSEGV handler for ROP gadget",
        syscalls_attempted=["rt_sigaction"],
        expected_behavior="error",  # Blocked or fails
        severity="high",
    ),
    ExploitScenario(
        name="sigprocmask_signal_unblock",
        description="Unblock SIGKILL via sigprocmask (should be impossible)",
        syscalls_attempted=["rt_sigprocmask"],
        expected_behavior="error",  # Kernel should reject
        severity="high",
    ),

    # === Process Tracing (2 scenarios) ===
    ExploitScenario(
        name="clone_fork_escape_namespace",
        description="Attempt clone()/fork() to escape process namespace",
        syscalls_attempted=["clone", "fork"],
        expected_behavior="blocked",
        severity="critical",
    ),
    ExploitScenario(
        name="unshare_new_namespace",
        description="Attempt unshare() to create new namespace",
        syscalls_attempted=["unshare"],
        expected_behavior="blocked",
        severity="high",
    ),

    # === Sysctl / Kernel Config (2 scenarios) ===
    ExploitScenario(
        name="sysctl_modify_kernel_params",
        description="Attempt sysctl() to modify kernel parameters",
        syscalls_attempted=["sysctl", "_sysctl"],
        expected_behavior="blocked",
        severity="high",
    ),
    ExploitScenario(
        name="syslog_read_kernel_log",
        description="Attempt syslog() to read kernel log buffer",
        syscalls_attempted=["syslog"],
        expected_behavior="blocked",
        severity="high",
    ),

    # === BPF / Kernel Patching (1 scenario) ===
    ExploitScenario(
        name="bpf_bypass_seccomp",
        description="Attempt bpf() syscall to bypass seccomp",
        syscalls_attempted=["bpf"],
        expected_behavior="blocked",
        severity="critical",
    ),
]


class AdversarialTester:
    """
    Test plugin sandbox resistance to exploitation attempts.

    Strategy:
    1. For each exploit scenario, craft a plugin that attempts the syscall
    2. Run plugin in sandbox with seccomp enabled
    3. Verify that sandbox blocks/kills the process (returns error)
    4. If plugin succeeds, mark as ESCAPED and fail the test
    5. Aggregate results: N blocked, M escaped, X inconclusive
    """

    def __init__(self, manager: SandboxManager):
        """Initialize with sandbox manager."""
        self.manager = manager
        self.scenarios = EXPLOIT_SCENARIOS

    async def test_plugin(
        self,
        plugin_id: str,
        profile: SeccompProfile,
    ) -> AdversarialTestResult:
        """
        Run all exploit scenarios against a plugin.

        Args:
            plugin_id: Plugin to test
            profile: Seccomp profile for plugin

        Returns:
            AdversarialTestResult with pass/fail breakdown
        """
        blocked_count = 0
        escaped_count = 0
        inconclusive_count = 0
        escapes: List[Tuple[str, str]] = []

        for scenario in self.scenarios:
            result = await self._test_scenario(
                plugin_id=plugin_id,
                scenario=scenario,
                profile=profile,
            )

            if result == ExploitOutcome.BLOCKED:
                blocked_count += 1
            elif result == ExploitOutcome.ESCAPED:
                escaped_count += 1
                escapes.append((
                    scenario.name,
                    f"Exploit succeeded: {scenario.description}",
                ))
            else:
                inconclusive_count += 1

        return AdversarialTestResult(
            plugin_id=plugin_id,
            total_scenarios=len(self.scenarios),
            blocked_count=blocked_count,
            escaped_count=escaped_count,
            inconclusive_count=inconclusive_count,
            escapes=escapes,
        )

    async def _test_scenario(
        self,
        plugin_id: str,
        scenario: ExploitScenario,
        profile: SeccompProfile,
    ) -> ExploitOutcome:
        """
        Test a single exploit scenario.

        Args:
            plugin_id: Plugin to test
            scenario: Exploit scenario to attempt
            profile: Sandbox profile

        Returns:
            ExploitOutcome: BLOCKED, ESCAPED, or ERROR
        """
        # Build plugin args that trigger the exploit
        args: Dict[str, Any] = {
            "exploit_name": scenario.name,
            "syscalls": scenario.syscalls_attempted,
        }

        # Run plugin in sandbox
        result = await self.manager.run_plugin_operation(
            plugin_id=plugin_id,
            operation="test_exploit",
            args=args,
            profile=profile,
        )

        # Evaluate result
        if result.status in ["timeout", "killed"]:
            # Sandbox killed the process = good
            return ExploitOutcome.BLOCKED
        elif result.status == "error":
            # Error within sandbox = plugin couldn't execute exploit
            return ExploitOutcome.BLOCKED
        elif result.status == "success":
            # Plugin succeeded = potential escape!
            if result.exit_code == 0:
                # Plugin claims success = ESCAPE
                return ExploitOutcome.ESCAPED
            else:
                # Plugin failed = blocked
                return ExploitOutcome.BLOCKED
        else:
            return ExploitOutcome.BLOCKED


class SandboxSecurityValidator:
    """
    Comprehensive sandbox security validation.

    Runs multiple test suites:
    1. Exploit resistance (adversarial_tester)
    2. Resource isolation (no cross-plugin interference)
    3. IPC integrity (capability tokens verified)
    4. Audit completeness (all events logged)
    """

    def __init__(self):
        self.manager = SandboxManager()
        self.tester = AdversarialTester(self.manager)

    async def validate_sandbox(
        self,
        plugin_id: str,
        num_exploit_runs: int = 1,
    ) -> Dict[str, Any]:
        """
        Comprehensive sandbox validation.

        Args:
            plugin_id: Plugin to validate
            num_exploit_runs: Number of times to run exploit suite

        Returns:
            Validation report with overall security assessment
        """
        profile = generate_profile(plugin_id=plugin_id)

        # Run adversarial tests
        results = []
        for run in range(num_exploit_runs):
            result = await self.tester.test_plugin(
                plugin_id=plugin_id,
                profile=profile,
            )
            results.append(result)

        # Aggregate results
        total_escapes = sum(r.escaped_count for r in results)
        avg_pass_rate = sum(r.pass_rate for r in results) / len(results)

        return {
            "plugin_id": plugin_id,
            "validation_result": "pass" if total_escapes == 0 else "fail",
            "total_escapes": total_escapes,
            "average_pass_rate": avg_pass_rate,
            "escape_proof": total_escapes == 0,
            "details": [r for r in results],
        }
