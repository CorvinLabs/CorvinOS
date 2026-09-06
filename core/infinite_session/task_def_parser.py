"""Phase A: Task Definition Parser (ADR-0540, Infinite Session Engine).

Parses JSON-LD task definitions and produces ExecutionPlans.
Validates schema, detects cycles, and produces topologically sorted phase order.

Compliance:
- Audit Trail: Parser execution emitted as audit event
- Schema Validation: Fail-closed on missing required fields
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Optional
from uuid import uuid4
import json

from collections import deque, defaultdict


class AutonomyLevel(str, Enum):
    """Autonomy levels for task execution (ADR-0540)."""
    LEVEL_1 = "1"  # Gates are informational
    LEVEL_2 = "2"  # Gates auto-pass/fail, user reviews phase
    LEVEL_3 = "3"  # Gates auto-pass/fail, proceed automatic
    LEVEL_4 = "4"  # Config auto-tuning + auto-proceed
    LEVEL_5 = "5"  # Code-review gates auto-pass (requires high confidence)


class GateType(str, Enum):
    """Gate types (validation after phase completion)."""
    FINDING_COUNT = "finding_count"  # e.g., <= 5 findings
    TEST_PASS_RATE = "test_pass_rate"  # e.g., >= 95% tests pass
    AUDIT_TRAIL_VERIFIED = "audit_trail_verified"  # Hash chain unbroken
    CUSTOM_CHECK = "custom_check"  # Custom gate (e.g., code review)


@dataclass
class Phase:
    """Single phase in task execution."""

    phase_id: str
    phase_name: str
    description: str
    dependencies: list[str] = field(default_factory=list)  # Phase IDs this depends on
    skills: list[str] = field(default_factory=list)  # Skills to run
    gates: list[Gate] = field(default_factory=list)  # Gates to evaluate
    timeout_seconds: int = 3600  # Phase timeout
    parallelizable: bool = False  # Can run in parallel with other phases
    max_retries: int = 3
    retry_delay_seconds: int = 30

    def validate(self) -> tuple[bool, str]:
        """Validate phase definition.

        Returns:
            (is_valid, error_message)
        """
        if not self.phase_id or not self.phase_id.strip():
            return False, "phase_id is required"
        if not self.phase_name or not self.phase_name.strip():
            return False, "phase_name is required"
        if self.timeout_seconds <= 0:
            return False, "timeout_seconds must be positive"
        if self.max_retries < 0:
            return False, "max_retries cannot be negative"

        return True, ""


@dataclass
class Gate:
    """Gate for evaluating phase completion."""

    gate_id: str
    gate_type: GateType
    params: dict[str, Any] = field(default_factory=dict)
    required: bool = True  # If true, gate must pass; if false, informational

    def validate(self) -> tuple[bool, str]:
        """Validate gate definition.

        Returns:
            (is_valid, error_message)
        """
        if not self.gate_id or not self.gate_id.strip():
            return False, "gate_id is required"
        if self.gate_type not in GateType:
            return False, f"Unknown gate_type: {self.gate_type}"

        return True, ""


@dataclass
class ExecutionPlan:
    """Execution plan for a task (output of TaskDefParser)."""

    task_id: str
    task_name: str
    autonomy_level: AutonomyLevel
    phases: list[Phase]
    phase_order: list[str]  # Topologically sorted phase IDs
    timeout_seconds: int  # Total task timeout
    success_criteria: dict[str, Any]  # Task success definition
    created_at: str  # ISO 8601 UTC
    version: str = "1.0"

    def validate(self) -> tuple[bool, str]:
        """Validate execution plan.

        Returns:
            (is_valid, error_message)
        """
        if not self.task_id or not self.task_id.strip():
            return False, "task_id is required"
        if not self.task_name or not self.task_name.strip():
            return False, "task_name is required"
        if self.autonomy_level not in AutonomyLevel:
            return False, f"Unknown autonomy_level: {self.autonomy_level}"
        if not self.phases:
            return False, "At least one phase is required"
        if not self.phase_order:
            return False, "phase_order is required"
        if len(self.phase_order) != len(self.phases):
            return False, "phase_order length must match number of phases"
        if self.timeout_seconds <= 0:
            return False, "timeout_seconds must be positive"

        # Validate all phases
        for phase in self.phases:
            is_valid, error = phase.validate()
            if not is_valid:
                return False, f"Phase {phase.phase_id}: {error}"

            # Validate gates
            for gate in phase.gates:
                is_valid, error = gate.validate()
                if not is_valid:
                    return False, f"Gate {gate.gate_id}: {error}"

            # Check that all dependencies are in phases list
            phase_ids = {p.phase_id for p in self.phases}
            for dep in phase.dependencies:
                if dep not in phase_ids:
                    return False, f"Phase {phase.phase_id} depends on unknown phase {dep}"

        # Validate phase_order matches phases
        phase_ids_in_plan = {p.phase_id for p in self.phases}
        phase_ids_in_order = set(self.phase_order)
        if phase_ids_in_plan != phase_ids_in_order:
            return False, "phase_order does not match phases"

        return True, ""

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict (for storage/JSON).

        Returns:
            Dictionary representation
        """
        return {
            "task_id": self.task_id,
            "task_name": self.task_name,
            "autonomy_level": self.autonomy_level.value,
            "phases": [
                {
                    "phase_id": p.phase_id,
                    "phase_name": p.phase_name,
                    "description": p.description,
                    "dependencies": p.dependencies,
                    "skills": p.skills,
                    "gates": [
                        {
                            "gate_id": g.gate_id,
                            "gate_type": g.gate_type.value,
                            "params": g.params,
                            "required": g.required,
                        }
                        for g in p.gates
                    ],
                    "timeout_seconds": p.timeout_seconds,
                    "parallelizable": p.parallelizable,
                    "max_retries": p.max_retries,
                    "retry_delay_seconds": p.retry_delay_seconds,
                }
                for p in self.phases
            ],
            "phase_order": self.phase_order,
            "timeout_seconds": self.timeout_seconds,
            "success_criteria": self.success_criteria,
            "created_at": self.created_at,
            "version": self.version,
        }


class TaskDefParser:
    """Parser for JSON-LD task definitions."""

    @staticmethod
    def parse(task_def: dict[str, Any]) -> tuple[Optional[ExecutionPlan], str]:
        """Parse a task definition and produce an execution plan.

        Args:
            task_def: JSON-LD task definition

        Returns:
            (execution_plan, error_message)
            If parsing succeeds, error_message is empty string.
            If parsing fails, execution_plan is None and error_message describes the problem.
        """
        # Extract required fields
        task_id = task_def.get("task_id")
        task_name = task_def.get("task_name")
        autonomy_level_str = task_def.get("autonomy_level", "1")
        phases_data = task_def.get("phases", [])
        timeout_seconds = task_def.get("timeout_seconds", 3600)
        success_criteria = task_def.get("success_criteria", {})

        # Validate required fields
        if not task_id:
            return None, "task_id is required"
        if not task_name:
            return None, "task_name is required"
        if not phases_data:
            return None, "At least one phase is required"
        if timeout_seconds <= 0:
            return None, "timeout_seconds must be positive"

        # Parse autonomy level
        try:
            autonomy_level = AutonomyLevel(autonomy_level_str)
        except ValueError:
            return None, f"Unknown autonomy_level: {autonomy_level_str}"

        # Parse phases
        phases = []
        phase_ids = set()
        for phase_data in phases_data:
            phase_id = phase_data.get("phase_id")
            if not phase_id:
                return None, "Each phase must have a phase_id"
            if phase_id in phase_ids:
                return None, f"Duplicate phase_id: {phase_id}"
            phase_ids.add(phase_id)

            phase_name = phase_data.get("phase_name", phase_id)
            description = phase_data.get("description", "")
            dependencies = phase_data.get("dependencies", [])
            skills = phase_data.get("skills", [])
            gates_data = phase_data.get("gates", [])
            timeout = phase_data.get("timeout_seconds", 3600)
            parallelizable = phase_data.get("parallelizable", False)
            max_retries = phase_data.get("max_retries", 3)
            retry_delay = phase_data.get("retry_delay_seconds", 30)

            # Validate dependencies reference existing phases
            for dep in dependencies:
                if dep not in phase_ids and dep not in {p.get("phase_id") for p in phases_data}:
                    return None, f"Phase {phase_id} depends on unknown phase {dep}"

            # Parse gates
            gates = []
            for gate_data in gates_data:
                gate_id = gate_data.get("gate_id")
                if not gate_id:
                    return None, f"Each gate in phase {phase_id} must have a gate_id"

                gate_type_str = gate_data.get("gate_type")
                if not gate_type_str:
                    return None, f"Gate {gate_id} in phase {phase_id} must have a gate_type"

                try:
                    gate_type = GateType(gate_type_str)
                except ValueError:
                    return None, f"Unknown gate_type in gate {gate_id}: {gate_type_str}"

                gate_params = gate_data.get("params", {})
                gate_required = gate_data.get("required", True)

                gate = Gate(
                    gate_id=gate_id,
                    gate_type=gate_type,
                    params=gate_params,
                    required=gate_required,
                )
                gates.append(gate)

            phase = Phase(
                phase_id=phase_id,
                phase_name=phase_name,
                description=description,
                dependencies=dependencies,
                skills=skills,
                gates=gates,
                timeout_seconds=timeout,
                parallelizable=parallelizable,
                max_retries=max_retries,
                retry_delay_seconds=retry_delay,
            )
            phases.append(phase)

        # Detect cycles
        has_cycle, cycle_message = TaskDefParser._detect_cycles(phases)
        if has_cycle:
            return None, cycle_message

        # Topologically sort phases
        phase_order, sort_error = TaskDefParser._topological_sort(phases)
        if sort_error:
            return None, sort_error

        # Create execution plan
        plan = ExecutionPlan(
            task_id=task_id,
            task_name=task_name,
            autonomy_level=autonomy_level,
            phases=phases,
            phase_order=phase_order,
            timeout_seconds=timeout_seconds,
            success_criteria=success_criteria,
            created_at=datetime.utcnow().isoformat() + "Z",
        )

        # Validate complete plan
        is_valid, error = plan.validate()
        if not is_valid:
            return None, error

        return plan, ""

    @staticmethod
    def _detect_cycles(phases: list[Phase]) -> tuple[bool, str]:
        """Detect cycles in phase dependencies using DFS.

        Args:
            phases: List of phases

        Returns:
            (has_cycle, error_message)
        """
        phase_map = {p.phase_id: p for p in phases}
        visited = set()
        rec_stack = set()

        def visit(phase_id: str, path: list[str]) -> tuple[bool, str]:
            if phase_id in rec_stack:
                cycle_str = " -> ".join(path + [phase_id])
                return True, f"Cycle detected: {cycle_str}"

            if phase_id in visited:
                return False, ""

            visited.add(phase_id)
            rec_stack.add(phase_id)

            if phase_id not in phase_map:
                return False, ""

            phase = phase_map[phase_id]
            for dep in phase.dependencies:
                has_cycle, error = visit(dep, path + [phase_id])
                if has_cycle:
                    return True, error

            rec_stack.remove(phase_id)
            return False, ""

        for phase in phases:
            if phase.phase_id not in visited:
                has_cycle, error = visit(phase.phase_id, [])
                if has_cycle:
                    return True, error

        return False, ""

    @staticmethod
    def _topological_sort(phases: list[Phase]) -> tuple[list[str], str]:
        """Topologically sort phases based on dependencies.

        Args:
            phases: List of phases

        Returns:
            (sorted_phase_ids, error_message)
        """
        phase_map = {p.phase_id: p for p in phases}
        in_degree = {p.phase_id: 0 for p in phases}
        graph = defaultdict(list)

        # Build graph
        for phase in phases:
            for dep in phase.dependencies:
                graph[dep].append(phase.phase_id)
                in_degree[phase.phase_id] += 1

        # Kahn's algorithm
        queue = deque([p.phase_id for p in phases if in_degree[p.phase_id] == 0])
        result = []

        while queue:
            current = queue.popleft()
            result.append(current)

            for neighbor in graph[current]:
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)

        if len(result) != len(phases):
            return [], "Topological sort failed (cycle or missing dependency)"

        return result, ""
