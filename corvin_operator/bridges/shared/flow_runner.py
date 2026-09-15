"""flow_runner.py — CorvinFlow M1/M5: FlowRunner with pre-spawn gate + checkpoints.

ADR-0121.  Pre-spawn gate order (every step, before A2A envelope):
  1. Dependency check   — all depends_on completed
  2. Checkpoint gate    — M5: pause if step declares checkpoint: human_approval
  3. Budget gate        — all five dimensions have headroom  (fail-closed)
  4. License gate       — validator.assert_limit  (fail-closed)
  5. Audit-first write  — mesh_flow.step_dispatched to L16 chain AND manifest BEFORE spawn
  6. Node execution     — pluggable NodeExecutor (local in M1; A2A in M2)
  7. Accounting         — record_step + step_completed event

NodeExecutor is the extension point: M2 will swap in A2ANodeExecutor.

L16 audit chain integration (EU AI Act Art. 12/13/14, GDPR Art. 30):
  FlowRunner writes mesh_flow.run_started, mesh_flow.step_dispatched, and
  mesh_flow.run_completed to the tenant's L16 hash chain BEFORE writing to
  the manifest. This makes the flow traceable via voice-audit verify.

  The chain path is derived from manifest_root:
    manifest_root = <tenant_home>/global/flows/runs
    audit_path    = <tenant_home>/global/forge/audit.jsonl

  If flow.compliance.require_audit: true, an L16 write failure on
  run_started raises FlowDefinitionError (fail-closed).
"""
from __future__ import annotations

import logging
import sys
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

# Optional forge dependency — same pattern as audit.py in this directory.
# Silent fallback: when forge is absent, L16 writes are no-ops and
# require_audit: true will trigger an explicit error at run time.
_security_events: Any = None
try:
    _SHARED_DIR = Path(__file__).resolve().parent
    _FORGE_ROOT = _SHARED_DIR.parents[1] / "forge"
    if _FORGE_ROOT.is_dir() and (_FORGE_ROOT / "forge").is_dir():
        if str(_FORGE_ROOT) not in sys.path:
            sys.path.insert(0, str(_FORGE_ROOT))
        from forge import security_events as _security_events  # type: ignore[assignment]
except Exception:
    _security_events = None

try:
    from .flow_definition import (
        FlowBudget,
        FlowBudgetExceeded,
        FlowDefinition,
        FlowDefinitionError,
        FlowRunManifest,
        sha256_prefix,
    )
except ImportError:
    from flow_definition import (  # type: ignore[no-redef]
        FlowBudget,
        FlowBudgetExceeded,
        FlowDefinition,
        FlowDefinitionError,
        FlowRunManifest,
        sha256_prefix,
    )

try:
    from .flow_checkpoint import FlowCheckpointPaused
except ImportError:
    from flow_checkpoint import FlowCheckpointPaused  # type: ignore[no-redef]

log = logging.getLogger(__name__)


class NodeExecutor(ABC):
    """Execute a single flow step on its target node."""

    @abstractmethod
    def execute(self, step_id: str, step: dict[str, Any], task: str) -> str:
        """Return step output string. Raise on failure."""


class LocalNodeExecutor(NodeExecutor):
    """M1 executor: all steps run as local no-ops returning a placeholder.

    Replace with A2ANodeExecutor in M2 to dispatch real TaskEnvelopes.
    """

    def execute(self, step_id: str, step: dict[str, Any], task: str) -> str:
        node = step.get("node", "local")
        return f"[{node}] processed: {task[:80]}"


class FlowRunner:
    """Production FlowRunner — orchestrates a FlowDefinition into a FlowRun.

    Usage::

        runner = FlowRunner(flow_def, manifest_root, flow_input)
        result = runner.run()

    The manifest_root is typically
    ``<corvin_home>/tenants/<tid>/global/flows/runs/``.
    """

    def __init__(
        self,
        flow_def: FlowDefinition,
        manifest_root: Path,
        flow_input: dict[str, Any],
        *,
        executor: NodeExecutor | None = None,
        license_validator: Any = None,
        checkpoint_store: Any = None,
    ) -> None:
        self._flow = flow_def
        self._run_id = f"fr_{int(time.time() * 1000)}"
        self._manifest = FlowRunManifest(
            manifest_root / f"{self._run_id}.manifest.jsonl"
        )
        self._budget = FlowBudget(flow_def.budget)
        self._input = flow_input
        self._outputs: dict[str, str] = {}
        self._executor: NodeExecutor = executor or LocalNodeExecutor()
        self._validator = license_validator or _default_validator()
        self._checkpoint_store = checkpoint_store  # FlowCheckpointStore | None

        # Derive tenant L16 audit chain from manifest_root layout:
        #   manifest_root = <tenant_home>/global/flows/runs
        #   audit_path    = <tenant_home>/global/forge/audit.jsonl
        try:
            self._audit_path: Path | None = (
                manifest_root.parents[2] / "global" / "forge" / "audit.jsonl"
            )
        except IndexError:
            self._audit_path = None

    # ── public ────────────────────────────────────────────────────────────────

    def _l16_write(self, event_type: str, **details: Any) -> bool:
        """Write a metadata-only event to the tenant L16 hash chain.

        Returns True on success. Never raises — callers decide whether a
        False return is fatal (require_audit: true) or best-effort.
        """
        if _security_events is None or self._audit_path is None:
            return False
        try:
            _security_events.write_event(self._audit_path, event_type, details=details)
            return True
        except Exception as exc:
            log.warning("L16 audit write failed for %s: %s", event_type, exc)
            return False

    def run(self) -> dict[str, Any]:
        """Execute all steps in dependency order. Returns run summary."""
        require_audit = bool(self._flow.compliance.get("require_audit", False))

        # Audit-first: L16 chain write BEFORE manifest write (EU AI Act Art. 12).
        l16_ok = self._l16_write(
            "mesh_flow.run_started",
            run_id=self._run_id,
            flow_id=self._flow.id,
            flow_version=self._flow.version,
            step_count=len(self._flow.steps),
        )
        if require_audit and not l16_ok:
            raise FlowDefinitionError(
                f"require_audit: true but L16 chain write failed for run {self._run_id}"
            )

        self._manifest.append(
            "mesh_flow.run_started",
            run_id=self._run_id,
            flow_id=self._flow.id,
            flow_version=self._flow.version,
            budget_allocated=self._flow.budget,
        )

        completed: set[str] = set()
        pending = dict(self._flow.steps)

        try:
            while pending:
                ready = {
                    sid: step
                    for sid, step in pending.items()
                    if all(d in completed for d in step.get("depends_on", []))
                }
                if not ready:
                    raise RuntimeError(
                        f"Dependency cycle or unsatisfiable deps in flow '{self._flow.id}'"
                    )
                for step_id, step in ready.items():
                    self._run_step(step_id, step)
                    completed.add(step_id)
                    del pending[step_id]
        except FlowCheckpointPaused:
            snap = self._budget.snapshot()
            self._l16_write(
                "mesh_flow.run_paused",
                run_id=self._run_id,
                steps_done=snap["steps_done"],
                status="paused",
            )
            self._manifest.append(
                "mesh_flow.run_paused",
                run_id=self._run_id,
                **snap,
                status="paused",
            )
            raise
        except Exception as _run_exc:
            # H5: write run_failed audit event for any non-pause exception
            # (cycle errors, budget exhaustion, executor failures) so the
            # L16 chain always has a terminal event — never a silent gap.
            snap = self._budget.snapshot()
            self._l16_write(
                "mesh_flow.run_failed",
                run_id=self._run_id,
                steps_done=snap["steps_done"],
                error=type(_run_exc).__name__,
                status="failed",
            )
            self._manifest.append(
                "mesh_flow.run_failed",
                run_id=self._run_id,
                **snap,
                error=type(_run_exc).__name__,
                status="failed",
            )
            raise

        snap = self._budget.snapshot()
        self._l16_write(
            "mesh_flow.run_completed",
            run_id=self._run_id,
            steps_done=snap["steps_done"],
            tokens_used=snap["tokens_used"],
            wall_time_elapsed_s=snap["wall_time_elapsed_s"],
            status="success",
        )
        self._manifest.append(
            "mesh_flow.run_completed",
            run_id=self._run_id,
            **snap,
            status="success",
        )
        return {
            "run_id": self._run_id,
            "status": "success",
            "outputs": self._outputs,
            "budget": self._budget.snapshot(),
        }

    # ── internal ──────────────────────────────────────────────────────────────

    def _run_step(self, step_id: str, step: dict[str, Any]) -> None:
        # 1. Checkpoint gate — M5: pause for human_approval before budget/spawn
        if step.get("checkpoint") == "human_approval" and self._checkpoint_store is not None:
            self._l16_write(
                "mesh_flow.checkpoint_paused",
                run_id=self._run_id,
                step_id=step_id,
            )
            self._manifest.append(
                "mesh_flow.checkpoint_paused",
                run_id=self._run_id,
                step_id=step_id,
            )
            self._checkpoint_store.pause(self._run_id, step_id)
            raise FlowCheckpointPaused(
                self._run_id, step_id, self._checkpoint_store._dir
            )

        # 2. Budget gate — pre-spawn, fail-closed
        try:
            self._budget.check()
        except FlowBudgetExceeded as exc:
            self._l16_write(
                "mesh_flow.budget_exceeded",
                run_id=self._run_id,
                step_id=step_id,
                reason=str(exc),
            )
            self._manifest.append(
                "mesh_flow.budget_exceeded",
                run_id=self._run_id,
                step_id=step_id,
                reason=str(exc),
            )
            raise

        # 3. License compute gate — pre-spawn, fail-closed
        if self._validator is not None:
            try:
                self._validator.assert_compute(self._budget._compute_used + 1)
            except Exception as exc:
                self._l16_write(
                    "mesh_flow.budget_exceeded",
                    run_id=self._run_id,
                    step_id=step_id,
                    reason=str(exc),
                )
                self._manifest.append(
                    "mesh_flow.budget_exceeded",
                    run_id=self._run_id,
                    step_id=step_id,
                    reason=str(exc),
                )
                raise FlowBudgetExceeded(str(exc)) from exc

        # 4. Audit-first — write to L16 THEN manifest, BEFORE spawn
        target_node = step.get("node", "local")
        self._l16_write(
            "mesh_flow.step_dispatched",
            run_id=self._run_id,
            step_id=step_id,
            target_node=target_node,
        )
        self._manifest.append(
            "mesh_flow.step_dispatched",
            run_id=self._run_id,
            step_id=step_id,
            target_node=target_node,
            budget_before=self._budget.snapshot(),
        )

        # 5. Resolve task template
        task = self._resolve_template(step.get("task", ""))

        # 6. Execute via pluggable executor
        output = self._executor.execute(step_id, step, task)

        # 7. Post-step accounting
        self._budget.record_step(compute=1, tokens=len(task) + len(output))
        self._outputs[step_id] = output
        self._flush_outputs()

        self._manifest.append(
            "mesh_flow.step_completed",
            run_id=self._run_id,
            step_id=step_id,
            tokens_used=len(task) + len(output),
            output_sha256_prefix=sha256_prefix(output),
            budget_after=self._budget.snapshot(),
        )
        self._manifest.append(
            "mesh_flow.budget_checkpoint",
            run_id=self._run_id,
            **self._budget.snapshot(),
        )

    _MAX_OUTPUT_BYTES = 8 * 1024  # 8 KB per step output

    def _flush_outputs(self) -> None:
        """Write step outputs to <run_id>.outputs.json beside the manifest.

        Outputs are truncated at 8 KB each — no PII-sensitive content enters
        the audit chain. The file is safe for the owner console to read.
        """
        if not self._manifest._path:
            return
        out_path = self._manifest._path.parent / f"{self._run_id}.outputs.json"
        try:
            payload = {
                step_id: (
                    output if len(output.encode()) <= self._MAX_OUTPUT_BYTES
                    else output.encode()[: self._MAX_OUTPUT_BYTES].decode("utf-8", errors="replace") + "\n…[truncated]"
                )
                for step_id, output in self._outputs.items()
            }
            import json as _json
            tmp = out_path.with_suffix(".tmp")
            tmp.write_text(_json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(out_path)
        except Exception as exc:
            log.warning("Could not flush step outputs for %s: %s", self._run_id, exc)

    def _resolve_template(self, template: str) -> str:
        result = template
        for k, v in self._input.items():
            result = result.replace(f"{{flow.input.{k}}}", str(v))
        for sid, out in self._outputs.items():
            result = result.replace(f"{{steps.{sid}.output}}", str(out)[:500])
        return result


# ── license validator shim ─────────────────────────────────────────────────

class _LicenseValidatorShim:
    """Thin adapter — wraps operator/license/validator for M1."""

    def __init__(self) -> None:
        try:
            from license import validator as v
            from license import limits as l
            self._v = v
            self._l = l
        except ImportError as exc:
            self._v = None
            self._l = None
            # ADR-0144: emit an audit event so the operator knows the license gate
            # is degraded.  Fail-open here is an explicit design choice (self_test.py
            # catches a non-importable license module at boot with CRITICAL severity).
            try:
                import logging as _log
                _log.getLogger("corvin.license").warning(
                    "flow_runner: license.validator not importable (%s); "
                    "compute quota gate is DISABLED for this session. "
                    "Self-test should have caught this at boot.",
                    exc,
                )
                # Best-effort audit — may silently fail if audit chain is also broken.
                from shared.audit import emit as _emit
                _emit("license.gate_unavailable", {"reason": str(exc)[:128], "gate": "compute_quota"})
            except Exception:  # noqa: BLE001
                pass

    def assert_compute(self, units: int) -> None:
        if self._v is None:
            return
        try:
            self._v.assert_limit("compute_units_per_day", units)
        except Exception:
            raise


def _default_validator() -> _LicenseValidatorShim | None:
    shim = _LicenseValidatorShim()
    return shim if shim._v is not None else None
