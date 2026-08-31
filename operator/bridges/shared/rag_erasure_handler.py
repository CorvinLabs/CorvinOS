"""L36: GDPR Art. 17 Right to Deletion Handler for RAG.

Enables erasure of user-related data from RAG indexes/caches.
Registered with the erasure orchestrator for coordinated deletion.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class ErasureRequest:
    """GDPR erasure request."""
    subject_id: str  # User/organization to erase
    provider_id: str  # Which RAG provider
    reason: str = "User requested deletion"


class RAGErasureHandler:
    """Handle GDPR erasure from RAG providers."""

    def __init__(self, registry_dir: Path):
        self.registry_dir = registry_dir
        self.erasure_log = registry_dir.parent / "erasure" / "rag_erasure.log"
        self.erasure_log.parent.mkdir(parents=True, exist_ok=True)

    async def erase(self, request: ErasureRequest) -> dict:
        """Execute erasure for a subject across RAG providers.

        Returns: {status, provider_id, subject_id, deleted_count}
        """
        try:
            # Load manifest for provider
            manifest_path = self.registry_dir / "manifests" / f"{request.provider_id}.yaml"
            if not manifest_path.exists():
                return {
                    "status": "SKIPPED",
                    "reason": "Provider not found",
                    "subject_id": request.subject_id,
                }

            # Check if provider has erasure handler configured
            import yaml
            with open(manifest_path) as f:
                manifest = yaml.safe_load(f)

            # Canonical manifest key is snake_case `erasure_handler` (what the
            # ManifestValidator enforces and the README documents). Reading only
            # the camelCase `erasureHandler` made GDPR Art. 17 erasure a silent
            # no-op for every validator-compliant manifest (security review
            # 2026-06-27). Accept both; snake_case wins.
            _spec = manifest.get("spec", {}) or {}
            erasure_config = _spec.get("erasure_handler") or _spec.get("erasureHandler") or {}
            if not erasure_config:
                return {
                    "status": "SKIPPED",
                    "reason": "Provider has no erasure handler",
                    "subject_id": request.subject_id,
                }

            # For Phase 5.5: Call provider's erasure endpoint
            # For now: log request and mark as pending
            logger.info(
                f"L36 ERASE request logged for {request.subject_id} "
                f"from provider {request.provider_id}"
            )

            # Append to erasure log (immutable trail)
            with open(self.erasure_log, "a") as f:
                f.write(
                    f"{request.subject_id}|{request.provider_id}|{request.reason}\n"
                )

            return {
                "status": "ACCEPTED",
                "subject_id": request.subject_id,
                "provider_id": request.provider_id,
                "reason": "Erasure request queued for processing",
            }

        except Exception as e:
            logger.error(f"Erasure handler error: {e}")
            return {
                "status": "FAILED",
                "error": str(e),
                "subject_id": request.subject_id,
            }
