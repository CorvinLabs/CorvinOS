"""
Seccomp rule generation and management for plugin sandbox isolation.

Implements allow-by-default syscall filtering with capability dropping,
converting plugin metadata into safe BPF bytecode profiles.

Security principles:
- Deny by default: only syscalls explicitly allowed are permitted
- Minimal surface: allow only what the plugin declared it needs
- Defense in depth: seccomp + chroot + cgroup + rlimit + capability drop
- Fail-closed: any unknown syscall kills the process
"""

from dataclasses import dataclass, field
from typing import List, Dict, Set, Optional, Literal
from enum import Enum
import json


class SyscallAction(Enum):
    """Seccomp filter action."""
    ALLOW = "SCMP_ACT_ALLOW"
    KILL_THREAD = "SCMP_ACT_KILL_THREAD"
    ERRNO = "SCMP_ACT_ERRNO"
    TRACE = "SCMP_ACT_TRACE"


# Standard syscalls safe for all plugins (read-only or essential)
BASE_SAFE_SYSCALLS = {
    # Process lifecycle
    "exit",
    "exit_group",

    # Memory management
    "brk",
    "mmap",
    "mmap2",
    "munmap",
    "mprotect",
    "mremap",
    "madvise",

    # File operations (read-only)
    "read",
    "readv",
    "pread64",
    "preadv",
    "readlink",
    "readlinkat",

    # File operations (write)
    "write",
    "writev",
    "pwrite64",
    "pwritev",

    # File descriptor management
    "open",
    "openat",
    "openat2",
    "close",
    "dup",
    "dup2",
    "dup3",
    "fcntl",
    "fcntl64",
    "ioctl",

    # File inquiry
    "stat",
    "stat64",
    "lstat",
    "lstat64",
    "fstat",
    "fstat64",
    "statx",
    "fstatat",
    "access",
    "faccessat",
    "faccessat2",

    # Directory operations
    "getcwd",
    "chdir",
    "fchdir",

    # Signal handling
    "rt_sigaction",
    "rt_sigprocmask",
    "rt_sigpending",
    "rt_sigtimedwait",
    "rt_sigqueueinfo",
    "sigaltstack",
    "signal",
    "sigprocmask",
    "sigpending",

    # Clock/timing (read-only)
    "time",
    "clock_gettime",
    "clock_gettime64",
    "clock_nanosleep",
    "clock_nanosleep_time64",
    "gettimeofday",
    "getitimer",
    "times",

    # Information queries
    "uname",
    "getuid",
    "geteuid",
    "getgid",
    "getegid",
    "getpid",
    "getppid",
    "getpgid",
    "getgroups",

    # Filesystem enumeration
    "getdents",
    "getdents64",

    # IPC (limited)
    "pipe",
    "pipe2",
    "eventfd",
    "eventfd2",
    "epoll_create",
    "epoll_create1",
    "epoll_ctl",
    "epoll_wait",
    "epoll_pwait",
    "select",
    "pselect6",
    "poll",
    "ppoll",

    # Futex (basic, for synchronization)
    "futex",
    "futex_time64",

    # Process tracing (read-only inspection)
    "prctl",
    "arch_prctl",

    # Capability queries
    "capget",
}

# Syscalls that MUST always be denied (privilege escalation, module loading, etc.)
HARD_DENY_SYSCALLS = {
    # Privilege escalation
    "setuid",
    "setgid",
    "setreuid",
    "setregid",
    "setresuid",
    "setresgid",
    "setfsgid",
    "setfsuid",
    "setgroups",
    "capset",

    # Module loading
    "init_module",
    "delete_module",
    "finit_module",

    # Kernel patching
    "kexec_load",
    "kexec_file_load",
    "bpf",

    # Process tracing/ptrace
    "ptrace",
    "process_vm_readv",
    "process_vm_writev",

    # Raw network access
    "socket",
    "socketpair",
    "bind",
    "connect",
    "listen",
    "accept",
    "accept4",
    "sendto",
    "recvfrom",
    "sendmsg",
    "recvmsg",
    "sendmmsg",
    "recvmmsg",

    # System control (sysctl, syslog, etc.)
    "sysctl",
    "_sysctl",
    "syslog",
    "sysfs",

    # Mount/unmount
    "mount",
    "umount",
    "umount2",
    "pivot_root",
    "chroot",

    # Key management
    "keyctl",
    "add_key",
    "request_key",

    # Namespace operations
    "clone",
    "clone3",
    "fork",
    "vfork",
    "unshare",
    "setns",

    # Container/seccomp manipulation
    "seccomp",

    # Advanced process control
    "wait4",
    "waitid",
    "waitpid",
    "kill",
    "tkill",
    "tgkill",
    "rt_sigkill",

    # Device operations
    "ioctl",  # Note: some ioctls are safe; conservative deny

    # Direct kernel memory access
    "open_by_handle_at",
    "perf_event_open",
    "kcmp",

    # Membarrier (memory synchronization primitives)
    "membarrier",
}


@dataclass(frozen=True)
class SeccompProfile:
    """Immutable seccomp filter profile for a plugin."""

    plugin_id: str
    allowed_syscalls: List[str] = field(default_factory=list)
    denied_syscalls: List[str] = field(default_factory=list)
    filesystem_rules: Dict[str, str] = field(default_factory=dict)  # path -> "r" | "rw"
    network_allowed: bool = False
    cpu_limit_percent: int = 20
    memory_limit_mb: int = 256
    timeout_seconds: int = 60

    def __post_init__(self):
        """Validate profile invariants."""
        if not (1 <= self.cpu_limit_percent <= 100):
            raise ValueError(f"CPU limit must be in [1..100], got {self.cpu_limit_percent}")
        if not (64 <= self.memory_limit_mb <= 1024):
            raise ValueError(f"Memory limit must be in [64..1024] MB, got {self.memory_limit_mb}")
        if not (5 <= self.timeout_seconds <= 3600):
            raise ValueError(f"Timeout must be in [5..3600]s, got {self.timeout_seconds}")

    @property
    def effective_allowed(self) -> Set[str]:
        """Set of syscalls allowed by this profile."""
        return set(self.allowed_syscalls)

    @property
    def effective_denied(self) -> Set[str]:
        """Set of syscalls denied by this profile (includes hard denies)."""
        return set(self.denied_syscalls) | HARD_DENY_SYSCALLS

    def to_json(self) -> str:
        """Serialize to JSON for seccomp daemon."""
        return json.dumps({
            "plugin_id": self.plugin_id,
            "allowed_syscalls": sorted(self.allowed_syscalls),
            "denied_syscalls": sorted(self.denied_syscalls),
            "filesystem_rules": self.filesystem_rules,
            "network_allowed": self.network_allowed,
            "cpu_limit_percent": self.cpu_limit_percent,
            "memory_limit_mb": self.memory_limit_mb,
            "timeout_seconds": self.timeout_seconds,
        }, indent=2)


def generate_profile(
    plugin_id: str,
    required_syscalls: Optional[List[str]] = None,
    filesystem_paths: Optional[Dict[str, str]] = None,
    network_allowed: bool = False,
    cpu_limit_percent: int = 20,
    memory_limit_mb: int = 256,
    timeout_seconds: int = 60,
) -> SeccompProfile:
    """
    Generate a seccomp profile from plugin requirements.

    Algorithm:
    1. Start with BASE_SAFE_SYSCALLS (deny-by-default foundation)
    2. Add declared required_syscalls (plugin-specific needs)
    3. Remove any syscalls in HARD_DENY list (fail-closed)
    4. Build denied list as everything else
    5. Clamp resource limits to safe ranges

    Args:
        plugin_id: Unique plugin identifier
        required_syscalls: Syscalls the plugin declares it needs
        filesystem_paths: Filesystem access rules {path: "r"|"rw"}
        network_allowed: Whether plugin may use network (default False)
        cpu_limit_percent: CPU quota [1..100] (default 20)
        memory_limit_mb: Memory quota [64..1024] MB (default 256)
        timeout_seconds: Execution timeout [5..3600]s (default 60)

    Returns:
        SeccompProfile: Immutable profile ready for sandbox

    Raises:
        ValueError: If any parameter violates constraints
    """
    if not plugin_id:
        raise ValueError("plugin_id is required")

    required = required_syscalls or []
    paths = filesystem_paths or {}

    # Validate resource limits
    if not (1 <= cpu_limit_percent <= 100):
        raise ValueError(f"CPU must be in [1..100], got {cpu_limit_percent}")
    if not (64 <= memory_limit_mb <= 1024):
        raise ValueError(f"Memory must be in [64..1024] MB, got {memory_limit_mb}")
    if not (5 <= timeout_seconds <= 3600):
        raise ValueError(f"Timeout must be in [5..3600]s, got {timeout_seconds}")

    # Build allowed set
    allowed = BASE_SAFE_SYSCALLS.copy()

    # Add filesystem-related syscalls if paths are declared
    if paths:
        allowed.update({
            "stat", "stat64", "lstat", "lstat64", "fstat", "fstat64",
            "statx", "fstatat", "access", "faccessat", "faccessat2",
            "listdir", "mkdir", "mkdirat", "rmdir", "unlink", "unlinkat",
            "rename", "renameat", "renameat2", "link", "linkat",
            "symlink", "symlinkat", "chmod", "fchmod", "fchmodat",
            "chown", "fchown", "fchownat", "lchown", "truncate", "ftruncate",
        })

    # Add declared syscalls (if not in hard-deny)
    for sc in required:
        if sc in HARD_DENY_SYSCALLS:
            raise ValueError(f"Plugin requested denied syscall '{sc}' (privilege escalation / kernel access)")
        allowed.add(sc)

    # Add network syscalls only if explicitly allowed
    if network_allowed:
        allowed.update({
            "socket", "socketpair", "connect", "bind", "listen",
            "accept", "accept4", "send", "sendto", "sendmsg", "sendmmsg",
            "recv", "recvfrom", "recvmsg", "recvmmsg",
            "setsockopt", "getsockopt", "shutdown",
            "getpeername", "getsockname", "getprotobyname", "getprotobynumber",
        })

    # Remove hard denies (defense in depth)
    allowed -= HARD_DENY_SYSCALLS

    # Build denied list
    denied = HARD_DENY_SYSCALLS.copy()

    return SeccompProfile(
        plugin_id=plugin_id,
        allowed_syscalls=sorted(list(allowed)),
        denied_syscalls=sorted(list(denied)),
        filesystem_rules=paths,
        network_allowed=network_allowed,
        cpu_limit_percent=cpu_limit_percent,
        memory_limit_mb=memory_limit_mb,
        timeout_seconds=timeout_seconds,
    )


def validate_profile(profile: SeccompProfile) -> List[str]:
    """
    Validate a seccomp profile for security issues.

    Returns:
        List of warning/error messages (empty if valid)
    """
    issues = []

    # Check for dangerous overlap
    allowed = set(profile.allowed_syscalls)
    denied = set(profile.denied_syscalls)

    # Hard denies should all be in denied list
    missing_denies = HARD_DENY_SYSCALLS - denied
    if missing_denies:
        issues.append(f"Hard-deny syscalls missing from denied list: {missing_denies}")

    # Overlap check
    overlap = allowed & HARD_DENY_SYSCALLS
    if overlap:
        issues.append(f"CRITICAL: Allowed syscalls contain hard-denies: {overlap}")

    # Resource limits sanity
    if profile.memory_limit_mb < 64:
        issues.append(f"Memory limit too low ({profile.memory_limit_mb}MB); minimum 64MB")
    if profile.timeout_seconds < 5:
        issues.append(f"Timeout too low ({profile.timeout_seconds}s); minimum 5s")

    return issues
