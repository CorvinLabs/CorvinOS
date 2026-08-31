"""ResourceSlot + ResourceManager — cgroup-aware resource allocation (ADR-0026 §C).

Reads /proc/self/cgroup limits (cpu_quota, memory_limit) and the backend
manifest's resource_per_instance spec. Computes the maximum number of
concurrent Worker instances.

Fallback when /proc/self/cgroup is unavailable: os.cpu_count().

MUST NOT import anthropic / openai / google.cloud.aiplatform.
"""
from __future__ import annotations

import dataclasses
import logging
import os
from typing import Optional

log = logging.getLogger(__name__)

_DEFAULT_CPU_PER_SLOT = 1
_DEFAULT_MEM_MIB_PER_SLOT = 512


class FabricResourceDenied(RuntimeError):
    """Raised when a resource slot allocation is denied due to insufficient capacity."""


@dataclasses.dataclass
class ResourceSlot:
    """An allocated resource slot for one Worker instance."""
    slot_id: int
    cpu_cores: float
    mem_mib: int
    backend: str = ""
    run_id: str = ""


class ResourceManager:
    """Manages ResourceSlot allocation for inter-job parallel workers.

    Thread-safe for sync use; does NOT use asyncio locks (sync only).
    """

    def __init__(
        self,
        *,
        cpu_per_slot: float = _DEFAULT_CPU_PER_SLOT,
        mem_mib_per_slot: int = _DEFAULT_MEM_MIB_PER_SLOT,
        # Override total resource limits for testing
        total_cpu_override: Optional[float] = None,
        total_mem_mib_override: Optional[int] = None,
        # Hard cap from backend manifest
        manifest_max_instances: int = 64,
        emit_fn=None,
    ) -> None:
        self._cpu_per_slot = max(0.1, cpu_per_slot)
        self._mem_per_slot = max(64, mem_mib_per_slot)
        self._manifest_cap = max(1, manifest_max_instances)
        self._emit = emit_fn or _default_emit

        # Discover host limits
        total_cpu = total_cpu_override if total_cpu_override is not None else _read_cpu_limit()
        total_mem = total_mem_mib_override if total_mem_mib_override is not None else _read_mem_limit_mib()

        self._total_cpu = total_cpu
        self._total_mem = total_mem

        # Compute maximum allowed slots
        self._max_slots = self._compute_max_slots()
        log.debug(
            "ResourceManager: cpu=%.1f mem=%dMiB max_slots=%d",
            self._total_cpu, self._total_mem, self._max_slots,
        )

        # Track allocated slots
        self._allocated: list[ResourceSlot] = []
        self._next_slot_id: int = 0

    def max_slots_for(
        self,
        *,
        cpu_per_slot: Optional[float] = None,
        mem_mib_per_slot: Optional[int] = None,
        manifest_cap: Optional[int] = None,
    ) -> int:
        """Return the maximum slots given (optionally overridden) per-slot costs."""
        cpu = cpu_per_slot if cpu_per_slot is not None else self._cpu_per_slot
        mem = mem_mib_per_slot if mem_mib_per_slot is not None else self._mem_per_slot
        cap = manifest_cap if manifest_cap is not None else self._manifest_cap
        return self._compute_max_slots(cpu, mem, cap)

    def _compute_max_slots(
        self,
        cpu_per: Optional[float] = None,
        mem_per: Optional[int] = None,
        cap: Optional[int] = None,
    ) -> int:
        cpu_per = cpu_per if cpu_per is not None else self._cpu_per_slot
        mem_per = mem_per if mem_per is not None else self._mem_per_slot
        cap = cap if cap is not None else self._manifest_cap
        by_cpu = int(self._total_cpu / max(0.01, cpu_per))
        by_mem = int(self._total_mem / max(1, mem_per))
        return max(1, min(by_cpu, by_mem, cap))

    def available_slots(self) -> int:
        """Return the number of currently available (unallocated) slots."""
        return self._max_slots - len(self._allocated)

    def allocate(
        self,
        n: int,
        *,
        backend: str = "",
        run_id: str = "",
    ) -> list[ResourceSlot]:
        """Allocate n ResourceSlots.

        Raises FabricResourceDenied if n exceeds available capacity.
        Emits compute.resource_slot_denied on denial.
        """
        available = self.available_slots()
        if n > available:
            try:
                self._emit(
                    "compute.resource_slot_denied",
                    run_id=run_id,
                    backend=backend,
                    requested_slots=n,
                    available_slots=available,
                )
            except Exception:  # noqa: BLE001
                pass
            raise FabricResourceDenied(
                f"requested {n} slots but only {available} available "
                f"(max={self._max_slots})"
            )
        slots = []
        for _ in range(n):
            slot = ResourceSlot(
                slot_id=self._next_slot_id,
                cpu_cores=self._cpu_per_slot,
                mem_mib=self._mem_per_slot,
                backend=backend,
                run_id=run_id,
            )
            self._next_slot_id += 1
            self._allocated.append(slot)
            slots.append(slot)
        log.debug("allocated %d slots (total_allocated=%d)", n, len(self._allocated))
        return slots

    def release(self, slots: list[ResourceSlot]) -> None:
        """Return ResourceSlots to the pool."""
        ids_to_release = {s.slot_id for s in slots}
        before = len(self._allocated)
        self._allocated = [s for s in self._allocated if s.slot_id not in ids_to_release]
        released = before - len(self._allocated)
        log.debug("released %d slots (total_allocated=%d)", released, len(self._allocated))

    @property
    def total_cpu(self) -> float:
        return self._total_cpu

    @property
    def total_mem_mib(self) -> int:
        return self._total_mem

    @property
    def max_slots(self) -> int:
        return self._max_slots


# ---------------------------------------------------------------------------
# cgroup limit readers
# ---------------------------------------------------------------------------

def _read_cpu_limit() -> float:
    """Read the process CPU limit from cgroup v2 / v1.

    Falls back to os.cpu_count() if /proc/self/cgroup is unavailable.
    """
    try:
        return _read_cgroup_cpu()
    except Exception as exc:
        log.debug("cgroup cpu read failed (%s); using os.cpu_count()", exc)
        return float(os.cpu_count() or 1)


def _read_cgroup_cpu() -> float:
    """Try to read cpu.max (cgroup v2) or cpu.cfs_quota_us (cgroup v1)."""
    # cgroup v2
    cpu_max = _read_file_safe("/sys/fs/cgroup/cpu.max")
    if cpu_max:
        parts = cpu_max.strip().split()
        if len(parts) >= 2 and parts[0] != "max":
            try:
                quota = int(parts[0])
                period = int(parts[1])
                if period > 0 and quota > 0:
                    return quota / period
            except ValueError:
                pass

    # cgroup v1 — read from first cgroup entry with "cpu" subsystem
    cgroup_data = _read_file_safe("/proc/self/cgroup") or ""
    for line in cgroup_data.splitlines():
        parts_line = line.split(":", 2)
        if len(parts_line) == 3 and "cpu" in parts_line[1]:
            cgroup_path = parts_line[2].strip()
            prefix = f"/sys/fs/cgroup/cpu{cgroup_path}"
            quota_str = _read_file_safe(f"{prefix}/cpu.cfs_quota_us")
            period_str = _read_file_safe(f"{prefix}/cpu.cfs_period_us")
            if quota_str and period_str:
                try:
                    quota = int(quota_str.strip())
                    period = int(period_str.strip())
                    if quota > 0 and period > 0:
                        return quota / period
                except ValueError:
                    pass

    return float(os.cpu_count() or 1)


def _read_mem_limit_mib() -> int:
    """Try to read memory limit from cgroup v2 or v1; fallback to 4096 MiB."""
    try:
        return _read_cgroup_mem()
    except Exception as exc:
        log.debug("cgroup mem read failed (%s); using 4096 MiB default", exc)
        return 4096


def _read_cgroup_mem() -> int:
    """Read memory.max (v2) or memory.limit_in_bytes (v1)."""
    # cgroup v2
    mem_max = _read_file_safe("/sys/fs/cgroup/memory.max")
    if mem_max and mem_max.strip() != "max":
        try:
            return int(mem_max.strip()) // (1024 * 1024)
        except ValueError:
            pass

    # cgroup v1
    cgroup_data = _read_file_safe("/proc/self/cgroup") or ""
    for line in cgroup_data.splitlines():
        parts_line = line.split(":", 2)
        if len(parts_line) == 3 and "memory" in parts_line[1]:
            cgroup_path = parts_line[2].strip()
            prefix = f"/sys/fs/cgroup/memory{cgroup_path}"
            limit_str = _read_file_safe(f"{prefix}/memory.limit_in_bytes")
            if limit_str:
                try:
                    limit_bytes = int(limit_str.strip())
                    # 9223372036854771712 = effectively unlimited (no limit set)
                    if 0 < limit_bytes < 9_000_000_000_000_000:
                        return limit_bytes // (1024 * 1024)
                except ValueError:
                    pass

    return 4096


def _read_file_safe(path: str) -> Optional[str]:
    try:
        with open(path) as f:
            return f.read()
    except (OSError, IOError):
        return None


def _default_emit(event: str, **kwargs) -> None:
    log.debug("audit: %s %s", event, kwargs)


__all__ = [
    "FabricResourceDenied",
    "ResourceSlot",
    "ResourceManager",
]
