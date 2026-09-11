"""Phase 10: Delivery — Final manifest and versioning."""

from dataclasses import dataclass
from typing import Dict, List, Any
import time
from ..events import PhaseCompletedEvent, LossComponents, LossEmitter


@dataclass
class DeliveryRequest:
    """Request for delivery phase."""
    skill_id: str
    manifest: Dict[str, Any]
    version: str
    package_name: str


@dataclass
class DeliveryResult:
    """Output of delivery phase."""
    skill_id: str
    final_manifest: Dict[str, Any]
    delivery_status: str
    artifacts: List[str]


class DeliveryPhase:
    """Phase 10: Finalize and prepare for delivery."""

    def execute(self, request: DeliveryRequest, emitter: LossEmitter) -> DeliveryResult:
        """Execute delivery phase."""
        start = time.time()
        errors = []

        # Finalize manifest with additional metadata
        final_manifest = dict(request.manifest)
        final_manifest["generated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        final_manifest["package_name"] = request.package_name
        final_manifest["status"] = "ready"

        artifacts = [
            f"skill_{request.skill_id}.py",
            "tests/test_skill.py",
            "README.md",
            "MANIFEST.json",
        ]

        result = DeliveryResult(
            skill_id=request.skill_id,
            final_manifest=final_manifest,
            delivery_status="ready",
            artifacts=artifacts,
        )

        # Loss: complete delivery = high completeness
        relevance = 1.0
        completeness = 1.0
        performance = 1.0
        maintainability = 0.95

        event = PhaseCompletedEvent(
            phase_num=10,
            skill_id=request.skill_id,
            duration_ms=(time.time() - start) * 1000,
            errors=errors,
            loss_components=LossComponents(
                relevance=relevance,
                completeness=completeness,
                performance=performance,
                maintainability=maintainability,
            ),
        )
        emitter.emit(event)

        return result
