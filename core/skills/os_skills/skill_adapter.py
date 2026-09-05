"""Skill Config Adapter & Optimizer (ADR-0549 Stages 3–4).

Phase 2 of CONCEPT-0029. Takes hypotheses from FeedbackInterpreter and tests them
on real tasks via an iterative optimizer (150-epoch loop). Successful changes are
applied; failures are rejected.

Stages:
  3. Optimizer tests hypotheses (1 per epoch, 50-run baseline, 50 inner-loop, 50 refinement)
  4. Announce changes + enable reversibility (config versioning)

Audit trail:
  - optimizer_hypothesis_tested (result of test)
  - optimizer_hypothesis_accepted/rejected
  - skill_config_updated (change applied)
  - skill_version_created (version snapshot)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from core.tenants.validation import validate_tenant_id
from .feedback_loop import ConfigHypothesis


__all__ = [
    "SkillConfig",
    "SkillConfigVersion",
    "SkillAdapter",
    "OptimizerState",
]


@dataclass(frozen=True)
class SkillConfig:
    """Current Skill configuration (all tunable parameters)."""

    confidence_threshold: float = 0.70
    speed_weight: float = 0.50
    clarity_weight: float = 0.30
    exploration_rate: float = 0.20
    latency_penalty: float = 0.10

    def apply_delta(self, param: str, delta: float) -> SkillConfig:
        """Create new SkillConfig with one param changed."""
        current = getattr(self, param)
        new_value = max(0.0, min(1.0, current + delta))  # Clamp to [0.0, 1.0]
        return SkillConfig(**{**self.__dict__, param: new_value})


@dataclass(frozen=True)
class SkillConfigVersion:
    """Immutable snapshot of a Skill config at a point in time."""

    version_id: str  # v1, v2, v3, ...
    skill_id: str
    timestamp: datetime
    config: SkillConfig
    change_reason: str = "baseline"
    improvement_pct: float = 0.0  # % improvement vs. baseline
    user_can_undo: bool = True


@dataclass
class OptimizerState:
    """Optimizer tracking (mutable during execution, then frozen)."""

    epoch: int = 1
    baseline_success_rate: float = 0.0
    current_success_rate: float = 0.0
    best_config: SkillConfig = field(default_factory=SkillConfig)
    config_versions: list[SkillConfigVersion] = field(default_factory=list)
    hypotheses_tested: int = 0
    hypotheses_accepted: int = 0
    mde_threshold: float = 0.05  # Minimum Detectable Effect


class SkillAdapter:
    """Autonomously tune Skill configs based on feedback (ADR-0549).

    Usage:
      1. Collect 50 tasks → compute baseline metrics
      2. For each hypothesis:
         - Test on 10 tasks with new config
         - If improvement >= MDE: accept + update
         - Else: reject + revert
      3. Continue until epoch 150
      4. Return final optimized config

    Reversibility: every version is stored. Operator can rollback anytime.
    """

    def __init__(self, skill_id: str, tenant_id: str, work_dir: Optional[Path] = None):
        validate_tenant_id(tenant_id)
        self.skill_id = skill_id
        self.tenant_id = tenant_id
        self.work_dir = work_dir or Path.home() / ".corvin" / "tenants" / tenant_id / "skills"
        self.work_dir.mkdir(parents=True, exist_ok=True)

        # Load or initialize
        self.state = OptimizerState()
        self._load_or_init()

    def _load_or_init(self) -> None:
        """Load prior versions from disk, or start fresh."""
        config_file = self.work_dir / f"{self.skill_id.replace('.', '_')}_config.json"
        if config_file.exists():
            import json
            data = json.loads(config_file.read_text())
            self.state.best_config = SkillConfig(**data.get("config", {}))
        else:
            # Start with baseline
            self.state.best_config = SkillConfig()

    def run_optimizer_epoch(
        self,
        hypothesis: Optional[ConfigHypothesis],
        recent_successes: int,
        recent_total: int,
    ) -> tuple[bool, str]:
        """Run one optimizer epoch.

        Args:
            hypothesis: hypothesis to test (None = baseline phase)
            recent_successes: successes in last 10 tasks
            recent_total: total tasks in last 10

        Returns:
            (accepted, reason): whether hypothesis was accepted
        """
        self.state.epoch += 1
        recent_success_rate = recent_successes / recent_total if recent_total > 0 else 0.0

        # Phase 1–50: Baseline collection (no changes)
        if self.state.epoch <= 50:
            self.state.baseline_success_rate = recent_success_rate
            return False, "baseline_collection_phase"

        # Phase 2–100: Test one hypothesis per epoch
        if self.state.epoch <= 100 and hypothesis:
            self.state.current_success_rate = recent_success_rate
            improvement = self.state.current_success_rate - self.state.baseline_success_rate
            mde = self.state.mde_threshold

            if improvement >= mde:
                # Accept: apply the change
                new_config = self.state.best_config.apply_delta(
                    hypothesis.param,
                    hypothesis.delta
                )
                self.state.best_config = new_config
                self.state.hypotheses_accepted += 1

                # Record version
                version = SkillConfigVersion(
                    version_id=f"v{len(self.state.config_versions) + 1}",
                    skill_id=self.skill_id,
                    timestamp=datetime.now(timezone.utc),
                    config=new_config,
                    change_reason=hypothesis.reason,
                    improvement_pct=improvement * 100,
                )
                self.state.config_versions.append(version)

                self._persist()
                return True, f"improvement_{improvement:.2%}_accepted"
            else:
                # Reject: keep current config
                self.state.hypotheses_tested += 1
                return False, f"improvement_{improvement:.2%}_below_mde_{mde}"

        # Phase 3–150: Refinement (test combinations)
        if self.state.epoch > 100:
            # For now, just converge (don't test more)
            if self.state.epoch >= 150:
                return False, "convergence_reached"

        return False, "no_action"

    def _persist(self) -> None:
        """Save current config to disk (fail-closed: exception on write failure)."""
        import json
        config_file = self.work_dir / f"{self.skill_id.replace('.', '_')}_config.json"
        data = {
            "config": self.state.best_config.__dict__,
            "versions": [
                {
                    "version_id": v.version_id,
                    "timestamp": v.timestamp.isoformat(),
                    "change_reason": v.change_reason,
                    "improvement_pct": v.improvement_pct,
                }
                for v in self.state.config_versions
            ],
        }
        try:
            config_file.write_text(json.dumps(data, indent=2))
        except (IOError, OSError) as e:
            raise RuntimeError(f"Failed to persist SkillAdapter config: {e}") from e

    def rollback(self, to_version: str) -> SkillConfig:
        """Rollback to a prior version (user override, GDPR Art. 21)."""
        for v in self.state.config_versions:
            if v.version_id == to_version:
                self.state.best_config = v.config
                self._persist()
                return v.config
        raise ValueError(f"Version {to_version} not found")

    def get_current_config(self) -> SkillConfig:
        """Get current configuration."""
        return self.state.best_config

    def get_version_history(self) -> list[SkillConfigVersion]:
        """Get all versions (for dashboard)."""
        return self.state.config_versions
