"""Skill Config Adapter & Optimizer (ADR-0549 Stages 3–4).

Phase 2 of CONCEPT-0029. Takes hypotheses from FeedbackInterpreter and tests them
on real tasks via an iterative optimizer (150-epoch loop). Successful changes are
applied; failures are rejected.

Stages:
  3. Optimizer tests hypotheses (1 per epoch, 50-run baseline, 50 inner-loop, 50 refinement)
  4. Announce changes + enable reversibility (config versioning)

Audit trail (emitted by the caller through ``on_config_change`` — the adapter
is pure state + persistence, the console route owns the audit write):
  - skill_config_updated (change applied / rolled back)
  - skill_version_created (version snapshot)

Persistence (2026-09-06 adversarial review, F7):
  * the config lives under the TENANT home (``core.paths.tenant.tenant_home``),
    so ``CORVIN_HOME`` and the repo-local ``.corvin`` are honoured — it used to
    be hard-wired to ``~/.corvin`` and silently wrote outside the live root;
  * every version is persisted WITH its config snapshot and reloaded on the
    next start, so ``rollback()`` works after a restart — it used to reload
    only the current config and every rollback failed with "Version not found";
  * the optimizer epoch counter is persisted, so a restart does not restart the
    50-epoch baseline phase.

The config produced here is READ by ``DelegationRouterSkill`` via
:func:`load_skill_config` — that is what closes the loop (feedback → hypothesis
→ accepted config → the next routing decision uses it).
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from core.tenants.validation import validate_tenant_id
from .feedback_loop import ConfigHypothesis

logger = logging.getLogger(__name__)

__all__ = [
    "SkillConfig",
    "SkillConfigVersion",
    "SkillAdapter",
    "OptimizerState",
    "load_skill_config",
    "skill_config_path",
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
        if param not in self.__dataclass_fields__:
            raise ValueError(f"unknown skill config param: {param!r}")
        current = getattr(self, param)
        new_value = max(0.0, min(1.0, current + delta))  # Clamp to [0.0, 1.0]
        return SkillConfig(**{**self.__dict__, param: new_value})

    def to_dict(self) -> dict[str, float]:
        return dict(self.__dict__)

    @classmethod
    def from_dict(cls, data: dict | None) -> SkillConfig:
        data = data or {}
        known = {k: float(v) for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**known)


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


def _default_work_dir(tenant_id: str) -> Path:
    """``<tenant_home>/skills`` — honours CORVIN_HOME (never a bare ~/.corvin)."""
    try:
        from core.paths.tenant import tenant_home  # noqa: PLC0415

        return tenant_home(tenant_id) / "skills"
    except Exception:  # noqa: BLE001 — stripped install without core.paths
        root = os.environ.get("CORVIN_HOME")
        base = Path(root) if root else Path.home() / ".corvin"
        return base / "tenants" / tenant_id / "skills"


def skill_config_path(skill_id: str, tenant_id: str, work_dir: Optional[Path] = None) -> Path:
    """Where a Skill's learned config lives for a tenant (read-only helper)."""
    validate_tenant_id(tenant_id)
    base = Path(work_dir) if work_dir is not None else _default_work_dir(tenant_id)
    return base / f"{skill_id.replace('.', '_')}_config.json"


# Read-side cache for the router: (path) -> (mtime_ns, config, version_id)
_CONFIG_CACHE: dict[str, tuple[int, SkillConfig, Optional[str]]] = {}


def load_skill_config(
    skill_id: str, tenant_id: str, work_dir: Optional[Path] = None
) -> tuple[SkillConfig, Optional[str]]:
    """Return ``(config, current_version_id)`` for a Skill — the consumer side.

    Never raises and never creates directories: a Skill executing on the hot
    path must not fail or write because no config was learned yet. Missing or
    unreadable file → the default ``SkillConfig()`` and ``None``.
    """
    try:
        path = skill_config_path(skill_id, tenant_id, work_dir)
        mtime = path.stat().st_mtime_ns
    except (OSError, ValueError):
        return SkillConfig(), None
    key = str(path)
    cached = _CONFIG_CACHE.get(key)
    if cached is not None and cached[0] == mtime:
        return cached[1], cached[2]
    try:
        data = json.loads(path.read_text())
        config = SkillConfig.from_dict(data.get("config"))
        versions = data.get("versions") or []
        version_id = versions[-1].get("version_id") if versions else None
    except (OSError, ValueError, AttributeError, TypeError):
        return SkillConfig(), None
    _CONFIG_CACHE[key] = (mtime, config, version_id)
    return config, version_id


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

    Reversibility: every version is stored WITH its config. Operator can
    rollback anytime, including after a restart.
    """

    def __init__(
        self,
        skill_id: str,
        tenant_id: str,
        work_dir: Optional[Path] = None,
        *,
        on_config_change: Optional[Callable[[dict[str, Any]], None]] = None,
    ):
        validate_tenant_id(tenant_id)
        self.skill_id = skill_id
        self.tenant_id = tenant_id
        self.work_dir = Path(work_dir) if work_dir is not None else _default_work_dir(tenant_id)
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self._on_config_change = on_config_change

        # Load or initialize
        self.state = OptimizerState()
        self._load_or_init()

    # ── persistence ─────────────────────────────────────────────────────────

    @property
    def config_file(self) -> Path:
        return self.work_dir / f"{self.skill_id.replace('.', '_')}_config.json"

    def _load_or_init(self) -> None:
        """Load prior config, versions and epoch from disk, or start fresh."""
        if not self.config_file.exists():
            self.state.best_config = SkillConfig()
            return
        try:
            data = json.loads(self.config_file.read_text())
        except (OSError, ValueError) as e:
            raise RuntimeError(f"Failed to read SkillAdapter config: {e}") from e
        self.state.best_config = SkillConfig.from_dict(data.get("config"))
        opt = data.get("optimizer") or {}
        self.state.epoch = int(opt.get("epoch", 1))
        self.state.baseline_success_rate = float(opt.get("baseline_success_rate", 0.0))
        self.state.current_success_rate = float(opt.get("current_success_rate", 0.0))
        self.state.hypotheses_tested = int(opt.get("hypotheses_tested", 0))
        self.state.hypotheses_accepted = int(opt.get("hypotheses_accepted", 0))
        versions: list[SkillConfigVersion] = []
        for v in data.get("versions") or []:
            try:
                versions.append(
                    SkillConfigVersion(
                        version_id=str(v["version_id"]),
                        skill_id=self.skill_id,
                        timestamp=datetime.fromisoformat(v["timestamp"]),
                        config=SkillConfig.from_dict(v.get("config")),
                        change_reason=str(v.get("change_reason", "")),
                        improvement_pct=float(v.get("improvement_pct", 0.0)),
                        user_can_undo=bool(v.get("user_can_undo", True)),
                    )
                )
            except (KeyError, ValueError, TypeError):
                logger.warning("skipping malformed skill config version: %r", v)
        self.state.config_versions = versions

    def _persist(self) -> None:
        """Save config + versions + optimizer counters (atomic tmp → rename)."""
        data = {
            "skill_id": self.skill_id,
            "tenant_id": self.tenant_id,
            "config": self.state.best_config.to_dict(),
            "optimizer": {
                "epoch": self.state.epoch,
                "baseline_success_rate": self.state.baseline_success_rate,
                "current_success_rate": self.state.current_success_rate,
                "hypotheses_tested": self.state.hypotheses_tested,
                "hypotheses_accepted": self.state.hypotheses_accepted,
            },
            "versions": [
                {
                    "version_id": v.version_id,
                    "timestamp": v.timestamp.isoformat(),
                    "change_reason": v.change_reason,
                    "improvement_pct": v.improvement_pct,
                    "user_can_undo": v.user_can_undo,
                    "config": v.config.to_dict(),
                }
                for v in self.state.config_versions
            ],
        }
        tmp = self.config_file.with_suffix(".tmp")
        try:
            tmp.write_text(json.dumps(data, indent=2))
            tmp.replace(self.config_file)
        except (IOError, OSError) as e:
            raise RuntimeError(f"Failed to persist SkillAdapter config: {e}") from e

    def _announce(self, change: str, **fields: Any) -> None:
        if self._on_config_change is None:
            return
        payload = {
            "skill_id": self.skill_id,
            "tenant_id": self.tenant_id,
            "change": change,
            "config": self.state.best_config.to_dict(),
            "version_id": self.state.config_versions[-1].version_id if self.state.config_versions else None,
            **fields,
        }
        try:
            self._on_config_change(payload)
        except Exception as e:  # noqa: BLE001 — the announcement must not undo the change
            logger.error("skill config change announcement failed: %s", e)

    # ── optimizer ───────────────────────────────────────────────────────────

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
            self._persist()
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
                self._announce(
                    "hypothesis_accepted",
                    hypothesis_id=hypothesis.hypothesis_id,
                    param=hypothesis.param,
                    delta=hypothesis.delta,
                    improvement_pct=improvement * 100,
                )
                return True, f"improvement_{improvement:.2%}_accepted"
            else:
                # Reject: keep current config
                self.state.hypotheses_tested += 1
                self._persist()
                return False, f"improvement_{improvement:.2%}_below_mde_{mde}"

        # Phase 3–150: Refinement (test combinations)
        if self.state.epoch > 100:
            # For now, just converge (don't test more)
            self._persist()
            if self.state.epoch >= 150:
                return False, "convergence_reached"

        self._persist()
        return False, "no_action"

    def rollback(self, to_version: str) -> SkillConfig:
        """Rollback to a prior version (user override, GDPR Art. 21)."""
        for v in self.state.config_versions:
            if v.version_id == to_version:
                self.state.best_config = v.config
                self._persist()
                self._announce("rollback", to_version=to_version)
                return v.config
        raise ValueError(f"Version {to_version} not found")

    def get_current_config(self) -> SkillConfig:
        """Get current configuration."""
        return self.state.best_config

    def get_version_history(self) -> list[SkillConfigVersion]:
        """Get all versions (for dashboard)."""
        return list(self.state.config_versions)
