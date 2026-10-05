"""LayerForgeOrchestrator — the pipeline (ADR-2222 D1):
VALIDATE -> TEST (quality gates) -> ENFORCE (host-awareness) -> AUDIT+CREATE -> AUDIT+PROMOTE.

Every check runs before anything is written, so a rejected definition leaves no
registry entry — only a ``layer_forge.definition_rejected`` audit record. Every
state change is audit-first under the entry's ``LayerPrimitive`` lock: the chain
record commits, then the registry file is replaced; a failed chain write
aborts the change (``LayerForgeAuditError``).

PLAN is deterministic (the caller supplies the manifest); LLM planning and the
adversarial REVIEW phase are deferred (ADR-2222 Consequences).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from . import audit
from .enforcement import EnforcementChecker, EnforcementVerdict
from .gate_runner import QualityGateRunner, QualityGateVerdict
from .primitive import LayerPrimitive
from .registry import LayerPromotionError, LayerRegistry
from .schema import LayerDependencyDAGError, LayerSchemaValidationError

REPO_ROOT = Path(__file__).resolve().parents[3]


def layer_forge_home(tenant_id: str) -> Path:
    from core.paths import tenant_home

    return tenant_home(tenant_id) / "global" / "layer_forge"


@dataclass
class LayerForgeResult:
    status: str  # SUCCESS | FAILED
    registry_key: str | None = None
    error: str | None = None
    phase: str | None = None  # where a FAILED run stopped: validate | test | enforce | audit
    gate_verdicts: list = field(default_factory=list)
    enforcement_verdicts: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "registry_key": self.registry_key,
            "error": self.error,
            "phase": self.phase,
            "gate_verdicts": [v.__dict__ for v in self.gate_verdicts],
            "enforcement_verdicts": [v.__dict__ for v in self.enforcement_verdicts],
        }


class LayerForgeOrchestrator:
    def __init__(self, tenant_id: str, *, repo_root: Path = REPO_ROOT, actor: str = "cli"):
        self.tenant_id = tenant_id
        self.actor = actor
        home = layer_forge_home(tenant_id)
        self.registry = LayerRegistry(home / "registry")
        self._lock_dir = home / "locks"
        self.gate_runner = QualityGateRunner(Path(repo_root))
        self.enforcement = EnforcementChecker(Path(repo_root))

    def _audit(self, event: str, **details) -> str:
        return audit.emit(event, tenant_id=self.tenant_id, **details)

    def _reject(self, phase: str, error: str, *, entry_id=None, version=None,
                error_class=None, failing_gates=None, gates=(), enforcement=()) -> LayerForgeResult:
        try:
            self._audit("layer_forge.definition_rejected", entry_id=entry_id, version=version,
                        phase=phase, error_class=error_class, failing_gates=failing_gates,
                        actor=self.actor)
        except audit.LayerForgeAuditError as exc:
            return LayerForgeResult("FAILED", error=f"{error}; {exc}", phase="audit",
                                    gate_verdicts=list(gates), enforcement_verdicts=list(enforcement))
        return LayerForgeResult("FAILED", error=error, phase=phase,
                                gate_verdicts=list(gates), enforcement_verdicts=list(enforcement))

    def create_layer_definition(self, manifest: dict, *, skip_gates: bool = False) -> LayerForgeResult:
        # VALIDATE (pure). An invalid id/version is never written to the chain.
        try:
            self.registry.validate(manifest)
        except (LayerSchemaValidationError, LayerDependencyDAGError, LayerPromotionError) as e:
            valid_key = not isinstance(e, LayerSchemaValidationError)
            return self._reject(
                "validate", f"validation failed: {e}",
                entry_id=manifest.get("id") if valid_key else None,
                version=manifest.get("version") if valid_key else None,
                error_class=type(e).__name__,
            )
        entry_id, version = manifest["id"], manifest["version"]

        # TEST: quality gates, fail-closed on FAIL/ERROR
        gates: list[QualityGateVerdict] = []
        if not skip_gates:
            gates = self.gate_runner.run_all_gates(manifest)
            try:
                for v in gates:
                    self._audit("layer_forge.quality_gate_evaluated", entry_id=entry_id,
                                version=version, gate_id=v.gate_id, status=v.status)
            except audit.LayerForgeAuditError as exc:
                return LayerForgeResult("FAILED", error=str(exc), phase="audit", gate_verdicts=gates)
            failing = [v.gate_id for v in gates if v.status != "PASS"]
            if failing:
                return self._reject("test", f"quality gates failed: {failing}", entry_id=entry_id,
                                    version=version, failing_gates=failing, gates=gates)

        # ENFORCE: host-awareness, fail-closed on FAIL (SKIPPED is recorded, not passed)
        enforcement: list[EnforcementVerdict] = [self.enforcement.check_host_awareness(manifest)]
        try:
            for v in enforcement:
                self._audit("layer_forge.enforcement_evaluated", entry_id=entry_id,
                            version=version, rule_id=v.rule_id, status=v.status)
        except audit.LayerForgeAuditError as exc:
            return LayerForgeResult("FAILED", error=str(exc), phase="audit",
                                    gate_verdicts=gates, enforcement_verdicts=enforcement)
        failing_enf = [v.detail for v in enforcement if v.status == "FAIL"]
        if failing_enf:
            return self._reject("enforce", f"enforcement check failed: {failing_enf}",
                                entry_id=entry_id, version=version, gates=gates,
                                enforcement=enforcement)

        # CREATE + PROMOTE, each audit-first under the entry lock
        try:
            with LayerPrimitive(entry_id, self._lock_dir).locked():
                self.registry.validate(manifest)  # re-check: another writer may have won
                self._audit("layer_forge.definition_proposed", entry_id=entry_id, version=version,
                            target_layers=[t["layer_id"] for t in manifest["targets"]],
                            gate_count=len(manifest.get("quality_gates", [])),
                            rule_count=len(manifest.get("enforcement_rules", [])),
                            gates_skipped=skip_gates, actor=self.actor)
                key = self.registry.create(manifest)
                self._transition_locked(entry_id, version, "accepted")
        except LayerPromotionError as e:
            return self._reject("validate", f"validation failed: {e}", entry_id=entry_id,
                                version=version, error_class=type(e).__name__,
                                gates=gates, enforcement=enforcement)
        except audit.LayerForgeAuditError as exc:
            return LayerForgeResult("FAILED", error=str(exc), phase="audit",
                                    gate_verdicts=gates, enforcement_verdicts=enforcement)

        return LayerForgeResult("SUCCESS", registry_key=key, gate_verdicts=gates,
                                enforcement_verdicts=enforcement)

    def _transition_locked(self, entry_id: str, version: str, to_status: str) -> dict:
        current = self.registry.check_transition(entry_id, version, to_status)
        self._audit("layer_forge.definition_transitioned", entry_id=entry_id, version=version,
                    from_status=current, to_status=to_status, actor=self.actor)
        return self.registry.promote(entry_id, version, to_status)

    def promote(self, entry_id: str, version: str, to_status: str) -> dict:
        """Audited status transition. Raises LayerNotFoundError / LayerPromotionError /
        LayerForgeAuditError; nothing changes unless the audit record committed."""
        self.registry._check_key(entry_id, version)
        with LayerPrimitive(entry_id, self._lock_dir).locked():
            return self._transition_locked(entry_id, version, to_status)

    def get(self, entry_id: str, version: str | None = None) -> dict:
        return self.registry.get(entry_id, version)

    def list_definitions(self) -> list[dict]:
        return self.registry.list_all()
