"""Integration bridge between Marketplace Installer and Skill Registry (ADR-0511 + ADR-0533).

Provides:
1. Registry integration (auto-register installed skills)
2. Learning loop connection (emit events to ADR-0314 infrastructure)
3. Audit trail binding (GDPR Art. 30/32 compliance)
4. Canary deployment coordination (phase-aware skill routing)

ADR-0511: Marketplace Plugin-First Architecture
ADR-0533: OS-Skill Manifest Schema & Versioning
ADR-0314: Learning Infrastructure (event emission)
ADR-0722: DoD Loss Signal Learning Integration

NOT WIRED: no production caller as of 2026-09-27 (adversarial review).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, List, Optional, Any, Callable
from datetime import datetime

from core.skills.marketplace_installer import (
    MarketplaceSkillInstaller,
    SkillPackage,
    DeploymentStage,
    InstallationRecord,
)
from core.skills.ab_testing import ABTestingFramework, CohortAssigner, ExperimentConfig

logger = logging.getLogger(__name__)


class MarketplaceSkillIntegration:
    """Integrate marketplace installer with skill registry and learning loop."""

    def __init__(
        self,
        installer: MarketplaceSkillInstaller,
        ab_framework: ABTestingFramework,
        registry_callback: Optional[Callable[[str, Dict[str, Any]], None]] = None,
        learning_emit: Optional[Callable[[str, Dict[str, Any]], None]] = None,
    ):
        """
        Initialize marketplace skill integration.

        Args:
            installer: MarketplaceSkillInstaller instance
            ab_framework: ABTestingFramework instance
            registry_callback: Callback to register skill with skill registry
            learning_emit: Callback to emit learning events (ADR-0314)
        """
        self.installer = installer
        self.ab_framework = ab_framework
        # No default registry: the old default only logged "[REGISTRY]
        # Registering skill" and registered nothing, so a skill was reported
        # installed-and-registered while no registry knew about it.
        self.registry_callback = registry_callback
        self.learning_emit = learning_emit or self._default_learning_emit

    def _default_learning_emit(self, event_type: str, payload: Dict[str, Any]) -> None:
        """No learning sink is wired by default: say so, do not pretend."""
        logger.debug("learning sink not wired; %s not emitted", event_type)

    def install_and_register(
        self,
        skill: SkillPackage,
        enable_ab_testing: bool = False,
        baseline_version: Optional[str] = None,
    ) -> InstallationRecord:
        """
        Install skill and register with skill registry.

        Workflow:
        1. Install skill (marketplace_installer)
        2. Register with skill registry (ADR-0533 compliance)
        3. Emit learning event (ADR-0314)
        4. Setup A/B test if enabled (canary deployment)

        Args:
            skill: SkillPackage to install
            enable_ab_testing: Enable canary deployment with A/B testing
            baseline_version: Baseline version for A/B test (required if enable_ab_testing=True)

        Returns:
            InstallationRecord
        """
        if self.registry_callback is None:
            # Refuse BEFORE installing: an installed-but-unregistered skill is
            # exactly the half state this method promises not to leave.
            raise NotImplementedError(
                "not_implemented: no skill registry callback configured"
            )

        # 1. Install skill
        record = self.installer.install_skill(
            skill,
            deployment_stage=DeploymentStage.CANARY_10 if enable_ab_testing else DeploymentStage.PROMOTED,
        )

        # 2. Register with skill registry (audit-first)
        try:
            self.registry_callback(skill.skill_id, {
                "version": skill.version,
                "source_url": skill.source_url,
                "boot_layer": skill.boot_layer,
                "origin": skill.origin,
                "dependencies": skill.dependencies,
                "manifest": skill.manifest,
                "local_path": str(record.local_path),
                "installed_at": record.installed_at.isoformat(),
            })
        except Exception as e:
            logger.error(f"Failed to register skill with registry: {e}")
            self.installer.audit_emit("skill_registration_failed", {
                "skill_id": skill.skill_id,
                "version": skill.version,
                "error_type": type(e).__name__,
            })
            raise

        # 3. Emit learning event (ADR-0314: event infrastructure)
        try:
            self.learning_emit("skill_installed_from_marketplace", {
                "skill_id": skill.skill_id,
                "version": skill.version,
                "source_url": skill.source_url,
                "boot_layer": skill.boot_layer,
                "origin": skill.origin,
                "install_id": record.install_id,
                "local_path": str(record.local_path),
                "installed_at": record.installed_at.isoformat(),
            })
        except Exception as e:
            logger.error(f"Failed to emit learning event: {e}")

        # 4. Setup A/B test for canary deployment (ADR-0533)
        if enable_ab_testing and baseline_version:
            try:
                exp_config = ExperimentConfig(
                    experiment_id=f"{skill.skill_id}_{skill.version}_canary",
                    skill_id=skill.skill_id,
                    baseline_version=baseline_version,
                    variant_version=skill.version,
                    variant_name=f"{skill.skill_id} v{skill.version}",
                    sample_size_per_variant=100,
                    min_runtime_days=1,
                    success_criteria={
                        "latency": -0.05,  # 5% latency reduction
                        "quality": 0.02,   # 2% quality improvement
                    },
                    rollout_percentage=10,  # Start with 10% canary
                )

                self.ab_framework.create_experiment(exp_config)

                logger.info(f"✅ A/B test created for {skill.skill_id}: {exp_config.experiment_id}")
            except Exception as e:
                logger.error(f"Failed to create A/B test: {e}")

        return record

    def promote_canary_to_rollout(
        self,
        install_id: str,
        next_traffic_percent: int = 50,
    ) -> InstallationRecord:
        """
        Promote canary deployment to next stage based on A/B test results.

        Workflow:
        1. Analyze A/B test (chi-square test, p<0.05)
        2. If variant wins: promote to next traffic stage (10% → 50% → 100%)
        3. If control wins: rollback variant
        4. Emit audit events

        Args:
            install_id: Installation ID to promote
            next_traffic_percent: Next traffic percentage (50 or 100)

        Returns:
            Updated InstallationRecord
        """
        record = self.installer.get_installation(install_id)
        if not record:
            raise ValueError(f"Installation not found: {install_id}")

        # Find matching A/B test
        exp_id = f"{record.skill_id}_{record.version}_canary"
        if exp_id not in self.ab_framework.experiments:
            # No evidence → no promotion (fail-closed). This used to promote
            # anyway, i.e. "based on A/B results" with no results at all.
            logger.warning(f"No A/B test found for {exp_id}; not promoting")
            return record

        # Analyze A/B test
        result = self.ab_framework.analyze_experiment(exp_id)

        if result.winner == "variant":
            # Promote variant
            logger.info(f"✅ Variant won A/B test: {exp_id} (confidence={result.confidence:.2%})")
            return self._promote_stage(install_id, next_traffic_percent)
        elif result.winner == "control":
            # Rollback variant
            logger.warning(f"❌ Control won A/B test: {exp_id}. Rolling back variant.")
            return self.installer.rollback_skill(
                install_id,
                reason=f"A/B test failed: {result.recommendation}",
            )
        else:
            logger.warning(f"⚠️ A/B test inconclusive: {exp_id}. Keeping current stage.")
            return record

    def _promote_stage(self, install_id: str, next_percent: int) -> InstallationRecord:
        """Promote to next deployment stage."""
        record = self.installer.get_installation(install_id)
        if not record:
            raise ValueError(f"Installation not found: {install_id}")

        if record.deployment_stage == DeploymentStage.CANARY_10:
            if next_percent >= 50:
                return self.installer.promote_canary(install_id, DeploymentStage.CANARY_50)
            else:
                return record
        elif record.deployment_stage == DeploymentStage.CANARY_50:
            if next_percent >= 100:
                return self.installer.promote_canary(install_id, DeploymentStage.PROMOTED)
            else:
                return record
        else:
            return record

    def get_installed_skill_version(
        self,
        skill_id: str,
        tenant_id: str = "_default",
    ) -> Optional[str]:
        """
        Get the version of an installed skill for a tenant.

        Considers:
        1. Deployment stage (canary 10/50 or promoted)
        2. A/B test cohort assignment
        3. Tenant-specific version pinning

        Args:
            skill_id: Skill identifier
            tenant_id: Tenant for version resolution

        Returns:
            Version string or None if not installed
        """
        record = self.installer.get_installed_skill(skill_id)
        if not record:
            return None

        # Check if skill is in A/B test
        exp_id = f"{skill_id}_{record.version}_canary"
        if exp_id in self.ab_framework.experiments:
            config = self.ab_framework.experiments[exp_id]

            stage_percent = {
                DeploymentStage.CANARY_10: 10,
                DeploymentStage.CANARY_50: 50,
            }.get(record.deployment_stage)
            if stage_percent is not None:
                # ONE stable hash decides, at the stage's traffic share. This
                # used to AND the experiment cohort (10%) with a second gate on
                # Python's per-process-randomised ``hash()`` — ~1% instead of
                # 10%, and a different answer after every restart.
                return (record.version if self._should_route_to_variant(exp_id, tenant_id, stage_percent)
                        else config.baseline_version)

        return record.version

    @staticmethod
    def _should_route_to_variant(exp_id: str, tenant_id: str, percent: int) -> bool:
        """Deterministic across processes (sha256, not ``hash()``)."""
        return CohortAssigner(exp_id).assign(tenant_id, percent).value == "variant"

    def list_installed_skills(self) -> List[Dict[str, Any]]:
        """List all installed skills with their current versions."""
        skills = {}
        for record in self.installer.list_installations():
            if record.skill_id not in skills:
                skills[record.skill_id] = record
            elif record.installed_at > skills[record.skill_id].installed_at:
                skills[record.skill_id] = record

        return [
            {
                "skill_id": record.skill_id,
                "version": record.version,
                "deployment_stage": record.deployment_stage.value,
                "installed_at": record.installed_at.isoformat(),
                "local_path": str(record.local_path),
            }
            for record in skills.values()
        ]

    def check_canary_readiness(self, install_id: str) -> Dict[str, Any]:
        """Check if a canary deployment is ready for promotion."""
        record = self.installer.get_installation(install_id)
        if not record:
            raise ValueError(f"Installation not found: {install_id}")

        exp_id = f"{record.skill_id}_{record.version}_canary"
        result = {
            "install_id": install_id,
            "skill_id": record.skill_id,
            "version": record.version,
            "deployment_stage": record.deployment_stage.value,
            "is_in_ab_test": exp_id in self.ab_framework.experiments,
            "ready_for_promotion": False,
            "recommendation": "",
        }

        if not result["is_in_ab_test"]:
            result["recommendation"] = "Not in A/B test. Promote manually or create experiment."
            return result

        # Check A/B test status
        metrics = self.ab_framework.metrics.get(exp_id)
        if not metrics:
            result["recommendation"] = "No metrics collected yet. Wait for data collection."
            return result

        if metrics.sample_size_variant < 50:
            result["recommendation"] = f"Insufficient samples: {metrics.sample_size_variant}/50"
            return result

        # Analyze A/B test
        ab_result = self.ab_framework.analyze_experiment(exp_id)

        if ab_result.winner == "variant":
            result["ready_for_promotion"] = True
            result["recommendation"] = f"Variant wins (p={ab_result.metrics.pvalue:.4f}). Ready to promote."
        elif ab_result.winner == "control":
            result["recommendation"] = f"Control wins (variant regressed). Recommend rollback."
        else:
            result["recommendation"] = "Results inconclusive. Collect more data."

        return result
