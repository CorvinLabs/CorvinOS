"""LayerForgeOrchestrator — the five-phase pipeline (ADR-2222 D1):
VALIDATE -> TEST (quality gates) -> ENFORCE (host-awareness) -> AUDIT -> PROMOTE.

The MVP's PLAN phase is deterministic (the manifest arrives fully formed from
the caller) — no LLM call. REVIEW (adversarial LLM review) is deferred to the
console-integration follow-up ADR, named explicitly in ADR-2222 Consequences.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from .enforcement import EnforcementChecker, EnforcementVerdict
from .gate_runner import QualityGateRunner, QualityGateVerdict
from .primitive import LayerPrimitive
from .registry import LayerPromotionError, LayerRegistry
from .schema import LayerDependencyDAGError, LayerSchemaValidationError


@dataclass
class LayerForgeResult:
    status: str  # SUCCESS | FAILED
    registry_key: str | None = None
    error: str | None = None
    gate_verdicts: list = field(default_factory=list)
    enforcement_verdicts: list = field(default_factory=list)


class LayerForgeOrchestrator:
    def __init__(self, tenant_root: Path, repo_root: Path):
        self.tenant_root = Path(tenant_root)
        self.repo_root = Path(repo_root)
        self.registry = LayerRegistry(self.tenant_root / "layer_forge" / "registry")
        self.gate_runner = QualityGateRunner(self.repo_root)
        self.enforcement = EnforcementChecker(self.repo_root)
        self._audit_path = self.tenant_root / "layer_forge" / "layer_forge_audit.jsonl"
        self._audit_path.parent.mkdir(parents=True, exist_ok=True)

    def _audit(self, event_type: str, **details) -> None:
        record = {"event_type": event_type, "ts": time.time(), **details}
        with self._audit_path.open("a") as f:
            f.write(json.dumps(record, sort_keys=True) + "\n")

    def create_layer_definition(self, manifest: dict, *, skip_gates: bool = False) -> LayerForgeResult:
        """PLAN (deterministic) -> VALIDATE -> TEST -> ENFORCE -> AUDIT -> PROMOTE."""
        entry_id = manifest.get("id", "<unknown>")
        version = manifest.get("version", "<unknown>")

        # VALIDATE + register as 'proposed' (registry.create does schema + DAG validation)
        try:
            registry_key = self.registry.create(manifest)
        except (LayerSchemaValidationError, LayerDependencyDAGError, LayerPromotionError) as e:
            self._audit("layer_forge.definition_validation_failed", id=entry_id, version=version, error=str(e))
            return LayerForgeResult(status="FAILED", error=f"validation failed: {e}")

        self._audit("layer_forge.definition_created", id=entry_id, version=version, registry_key=registry_key)

        # TEST: run quality gates (fail-closed — any FAIL/ERROR blocks promotion)
        gate_verdicts: list[QualityGateVerdict] = []
        if not skip_gates:
            gate_verdicts = self.gate_runner.run_all_gates(manifest)
            failing = [v for v in gate_verdicts if v.status != "PASS"]
            if failing:
                self._audit(
                    "layer_forge.quality_gate_failed",
                    id=entry_id, version=version,
                    failing_gates=[v.gate_id for v in failing],
                )
                return LayerForgeResult(
                    status="FAILED",
                    error=f"quality gates failed: {[v.gate_id for v in failing]}",
                    gate_verdicts=gate_verdicts,
                )
            for v in gate_verdicts:
                self._audit("layer_forge.quality_gate_passed", id=entry_id, gate_id=v.gate_id)

        # ENFORCE: host-awareness check (fail-closed on FAIL, SKIPPED is allowed)
        enforcement_verdicts: list[EnforcementVerdict] = [
            self.enforcement.check_host_awareness(manifest)
        ]
        failing_enf = [v for v in enforcement_verdicts if v.status == "FAIL"]
        if failing_enf:
            self._audit(
                "layer_forge.enforcement_check_failed",
                id=entry_id, version=version,
                details=[v.detail for v in failing_enf],
            )
            return LayerForgeResult(
                status="FAILED",
                error=f"enforcement check failed: {[v.detail for v in failing_enf]}",
                gate_verdicts=gate_verdicts,
                enforcement_verdicts=enforcement_verdicts,
            )
        for v in enforcement_verdicts:
            self._audit("layer_forge.enforcement_rule_applied", id=entry_id, rule_id=v.rule_id, status=v.status)

        # PROMOTE: proposed -> accepted, guarded by the race-free primitive
        prim = LayerPrimitive(entry_id, self.tenant_root / "layer_forge" / "primitive_state")
        prim.write_state({"last_promotion_attempt": time.time(), "registry_key": registry_key})
        self.registry.promote(entry_id, version, "accepted")
        self._audit("layer_forge.definition_promoted", id=entry_id, version=version, to_status="accepted")

        return LayerForgeResult(
            status="SUCCESS",
            registry_key=registry_key,
            gate_verdicts=gate_verdicts,
            enforcement_verdicts=enforcement_verdicts,
        )
