#!/usr/bin/env python3
"""
NineD_LossOptimizer (ADR-0614/0615/0616) — Unified 9D Learning Vector

Orchestrates all learning loops:
  - 6 core loops (Tier 1): Routing, Confidence, Feedback, Attention, Latency, Diversity
  - 3 infrastructure loops (Tier 2): Memory, Skills, Plugins
  - 1 meta loop (Tier 3): self-tuning hyperparameters (α, damping) for Tier 1/2

Computes unified loss:
  L_total = 0.6 * L_core + 0.3 * L_infra + 0.1 * L_meta
  where:
    L_core = mean(L_routing, L_confidence, L_feedback, L_attention, L_latency, L_diversity)
    L_infra = 1/3*L_memory + 1/3*L_skills + 1/3*L_plugins
    L_meta = the meta optimizer's loss (0.0 until its first phase-locked update)

Tier-specific damping prevents coupling oscillation. The Tier 2 learning rate
and damping are NOT hardcoded here: they are the meta optimizer's ``α_infra`` /
``damping_infra`` (that is the whole point of Tier 3, F-L9).

Divergence safety (ADR-0625): every meta update is bracketed by the
``DivergenceWatchdog`` — checkpoint → apply → validate → restore on divergence.
Checkpoints are persisted under ``<CORVIN_HOME>/tenants/<tenant>/learning/
meta_checkpoints/`` (never ``~/.corvin``, F-L12).
"""

from typing import Dict, List, Any, Optional
from pathlib import Path

from core.learning.base import LearningLoop
from core.learning.memory_optimizer import MemoryOptimizer
from core.learning.composition_optimizer import CompositionOptimizer
from core.learning.plugin_optimizer import PluginOrchestrator
from core.learning.live_collector_integration import LiveCollectorIntegration
from core.learning.meta_optimizer import MetaOptimizer
from core.learning.watchdog import DivergenceWatchdog
from core.paths.tenant import tenant_home


class NineD_LossOptimizer:
    """
    Unified 9D Learning Vector Optimizer.

    Coordinates all 9 learning loops (6 core + 3 infra) and computes unified loss
    with feedback backpropagation.
    """

    def __init__(
        self,
        tenant_id: str = "_default",
        collector_integration: Optional[LiveCollectorIntegration] = None,
        meta_optimizer: Optional[MetaOptimizer] = None,
    ):
        """
        Initialize the 9D optimizer with all sub-loops.

        Args:
            tenant_id: for audit trail and data isolation
            collector_integration: if provided, emit events to Live-Collector
            meta_optimizer: injectable Tier 3 optimizer (tests hand in one with
                a recording audit sink; production uses the core chain)
        """
        self.tenant_id = tenant_id
        self.collector = collector_integration or LiveCollectorIntegration(tenant_id)

        # ===== CORE LOOPS (Tier 1) =====
        # Moving averages fed by ``update_core_loop_loss`` from the core loop processes.
        self.core_loop_losses = {
            "routing": 0.3,
            "confidence": 0.25,
            "feedback": 0.2,
            "attention": 0.25,
            "latency": 0.2,
            "diversity": 0.15,
        }

        # ===== INFRASTRUCTURE LOOPS (Tier 2) =====
        self.memory_loop = MemoryOptimizer(tenant_id=tenant_id)
        self.skills_loop = CompositionOptimizer()
        self.plugins_loop = PluginOrchestrator()

        # ===== META LOOP (Tier 3) =====
        self.meta_optimizer = meta_optimizer or MetaOptimizer(tenant_id=tenant_id)
        self.watchdog = DivergenceWatchdog(tenant_id)
        self.checkpoint_dir: Path = tenant_home(tenant_id) / "learning" / "meta_checkpoints"
        self._last_meta_losses: Optional[Dict[str, float]] = None
        self._last_infra_loss = 0.0
        self.meta_rollbacks = 0

        # ===== UNIFIED LOSS TRACKING =====
        self.loss_history: List[float] = []
        self.step_count = 0

        # Weights
        self.core_weight = 0.6
        self.infra_weight = 0.3
        self.meta_weight = 0.1

        # Infra sub-weights
        self.infra_sub_weights = {
            "memory": 1.0 / 3.0,
            "skills": 1.0 / 3.0,
            "plugins": 1.0 / 3.0,
        }

        # Thresholds
        self.convergence_gradient_threshold = 0.001
        self.convergence_variance_threshold = 0.05

    # ── hyperparameters (owned by the meta loop) ───────────────────────────

    @property
    def infra_learning_rate(self) -> float:
        return float(self.meta_optimizer.α_infra)

    @property
    def infra_damping(self) -> float:
        return float(self.meta_optimizer.damping_infra)

    @property
    def core_smoothing(self) -> float:
        return float(self.meta_optimizer.damping_core)

    # ── losses ─────────────────────────────────────────────────────────────

    def compute_L_core(self) -> float:
        """
        Compute core loop loss = mean of 6 core loops.

        Returns:
            scalar loss in [0, 1]
        """
        losses = list(self.core_loop_losses.values())
        if not losses:
            return 0.0

        L_core = sum(losses) / len(losses)
        return float(max(0.0, min(1.0, L_core)))

    def compute_L_infra(self, feedback: Dict[str, Dict[str, float]]) -> float:
        """
        Compute infrastructure loop loss from sub-loops.

        Args:
            feedback: {
                'memory': {missing_context_ratio, ...},
                'skills': {composition_error_rate, ...},
                'plugins': {quality_gain, ...}
            }

        Returns:
            scalar loss in [0, 1]
        """
        L_memory = self.memory_loop.compute_loss(feedback.get("memory", {}))
        L_skills = self.skills_loop.compute_loss(feedback.get("skills", {}))
        L_plugins = self.plugins_loop.compute_loss(feedback.get("plugins", {}))

        L_infra = (
            self.infra_sub_weights["memory"] * L_memory
            + self.infra_sub_weights["skills"] * L_skills
            + self.infra_sub_weights["plugins"] * L_plugins
        )

        return float(max(0.0, min(1.0, L_infra)))

    def compute_L_meta(self) -> float:
        """Meta loop loss: the meta optimizer's last recorded loss (0.0 before its first update)."""
        history = self.meta_optimizer.loss_history
        return float(history[-1]) if history else 0.0

    def compute_L_total(self, feedback: Dict[str, Dict[str, float]]) -> float:
        """
        Compute unified loss across all 9D dimensions.

        Formula:
          L_total = 0.6 * L_core + 0.3 * L_infra + 0.1 * L_meta

        Args:
            feedback: {
                'memory': {...},
                'skills': {...},
                'plugins': {...}
            }

        Returns:
            scalar loss in [0, 1]
        """
        L_core = self.compute_L_core()
        L_infra = self.compute_L_infra(feedback)
        L_meta = self.compute_L_meta()

        L_total = (
            self.core_weight * L_core
            + self.infra_weight * L_infra
            + self.meta_weight * L_meta
        )

        return float(max(0.0, min(1.0, L_total)))

    # ── one optimisation step ──────────────────────────────────────────────

    def _step_loop(self, loop: LearningLoop, loop_feedback: Dict[str, float]) -> Dict[str, float]:
        """Gradient step for one Tier 2 loop with the meta loop's α/damping."""
        current = loop.compute_loss(loop_feedback)
        previous = loop.loss_history[-2] if len(loop.loss_history) > 1 else current
        gradients = loop.compute_gradients(current, previous)
        loop.apply_gradients(
            gradients,
            learning_rate=self.infra_learning_rate,
            damping=self.infra_damping,
        )
        return gradients

    def step(self, feedback: Dict[str, Dict[str, float]]) -> float:
        """
        Execute one optimization step across all 9D loops.

        Process:
          1. Compute unified L_total
          2. Compute gradients per loop
          3. Apply gradient updates with the meta loop's α / damping
          4. Emit Live-Collector events
          5. Every ``phase_lock_interval`` steps: meta update under watchdog

        Args:
            feedback: {
                'memory': {missing_context_ratio, irrelevance_score, ...},
                'skills': {composition_error_rate, dag_execution_time_ms, ...},
                'plugins': {quality_gain, execution_time_ms, ...}
            }

        Returns:
            L_total (scalar)
        """
        self.step_count += 1

        # ===== UPDATE TIER 2 (INFRASTRUCTURE) LOOPS =====
        # Each loop's loss is computed (and recorded) EXACTLY once per step, so
        # ``loss_history[-2]`` is the previous step's value and the gradient is a
        # real delta. Calling ``compute_loss`` again for L_infra recorded the same
        # step twice and every Tier 2 gradient was identically 0.
        memory_gradients = self._step_loop(self.memory_loop, feedback.get("memory", {}))
        skills_gradients = self._step_loop(self.skills_loop, feedback.get("skills", {}))
        plugins_gradients = self._step_loop(self.plugins_loop, feedback.get("plugins", {}))
        L_memory = self.memory_loop.loss_history[-1]
        L_skills = self.skills_loop.loss_history[-1]
        L_plugins = self.plugins_loop.loss_history[-1]
        L_infra = float(max(0.0, min(1.0,
            self.infra_sub_weights["memory"] * L_memory
            + self.infra_sub_weights["skills"] * L_skills
            + self.infra_sub_weights["plugins"] * L_plugins
        )))
        self._last_infra_loss = L_infra

        L_total = float(max(0.0, min(1.0,
            self.core_weight * self.compute_L_core()
            + self.infra_weight * L_infra
            + self.meta_weight * self.compute_L_meta()
        )))
        self.loss_history.append(L_total)

        # ===== EMIT LIVE-COLLECTOR EVENTS =====
        components = {k: float(v) for k, v in self.core_loop_losses.items()}

        self.collector.on_loss_computed(
            loss_total=L_total,
            loss_components={
                **components,
                "memory": float(L_memory),
                "skills": float(L_skills),
                "plugins": float(L_plugins),
            },
            gradients={**memory_gradients, **skills_gradients, **plugins_gradients},
            weights={
                "core": self.core_weight,
                "infra": self.infra_weight,
                "meta": self.meta_weight,
            },
            learning_rate=self.infra_learning_rate,
        )

        self.memory_loop.emit_event(self.collector, feedback=feedback.get("memory", {}))
        self.skills_loop.emit_event(
            self.collector, feedback=feedback.get("skills", {}), execution_time_ms=0
        )
        plugins_feedback = feedback.get("plugins", {})
        self.plugins_loop.emit_event(
            self.collector,
            feedback={pid: plugins_feedback for pid in self.plugins_loop.plugin_priority_weights} or {"plugins": plugins_feedback},
            task_type="generic",
        )

        # ===== UPDATE TIER 3 (META) LOOP =====
        if self.step_count % self.meta_optimizer.phase_lock_interval == 0:
            self._meta_update(feedback, L_total)

        return L_total

    def _meta_update(self, feedback: Dict[str, Dict[str, float]], L_total: float) -> None:
        """One phase-locked meta update, bracketed by the divergence watchdog."""
        L_core = self.compute_L_core()
        L_infra = self._last_infra_loss  # recorded by this step; no second compute_loss
        previous = self._last_meta_losses or {"core": L_core, "infra": L_infra}

        meta_feedback = {
            'core_loss': L_core,
            'prev_core_loss': previous["core"],
            'infra_loss': L_infra,
            'prev_infra_loss': previous["infra"],
            'core_loss_variance': self.memory_loop.get_loss_variance(self.meta_optimizer.phase_lock_interval),
            'infra_loss_variance': self.skills_loop.get_loss_variance(self.meta_optimizer.phase_lock_interval),
            'avg_gradient_magnitude': self.meta_optimizer.get_avg_gradient_magnitude(self.meta_optimizer.phase_lock_interval),
            'meta_loss': L_total,
        }
        self._last_meta_losses = {"core": L_core, "infra": L_infra}

        meta_loss = self.meta_optimizer.compute_loss(meta_feedback)
        history = self.meta_optimizer.loss_history
        prev_meta_loss = history[-2] if len(history) > 1 else meta_loss
        meta_gradients = self.meta_optimizer.compute_gradients(meta_loss, prev_meta_loss)

        # checkpoint → apply → validate → restore on divergence
        before = self.meta_optimizer.get_state()
        checkpoint_id = self.watchdog.save_checkpoint(before, directory=self.checkpoint_dir)
        self.meta_optimizer.apply_gradients(meta_gradients)

        after = self.meta_optimizer.get_state()
        after['loss'] = meta_loss
        if not self.watchdog.validate_state(after):
            reason = self.watchdog.get_divergence_reason(after)
            self.watchdog.on_divergence(reason)
            restored = self.watchdog.restore_checkpoint(checkpoint_id)
            if restored is not None:
                self.meta_optimizer.rollback_to_state(restored)
                self.meta_rollbacks += 1
                for name in ('α_core', 'α_infra', 'damping_core', 'damping_infra'):
                    if after.get(name) != restored.get(name):
                        self.collector.on_meta_rollback(
                            parameter=name,
                            old_value=float(restored[name]),
                            new_value=float(after[name]),
                            reason=reason,
                        )

        self.meta_optimizer.emit_event(self.collector, step_count=self.step_count)

    # ── convergence / state ────────────────────────────────────────────────

    def get_convergence_metrics(self) -> Dict[str, float]:
        """
        Get convergence metrics across all loops.

        Returns:
            {
                'avg_gradient_magnitude': float,
                'loss_variance': float,
                'memory_param_stability': float,
                'skills_param_stability': float,
                'plugins_param_stability': float,
            }
        """
        all_gradients = []

        for loop in [self.memory_loop, self.skills_loop, self.plugins_loop]:
            for param_gradients in loop.gradient_history.values():
                all_gradients.extend([abs(g) for g in param_gradients[-100:]])

        avg_grad_mag = (
            sum(all_gradients) / len(all_gradients)
            if all_gradients
            else float("inf")
        )

        recent_losses = self.loss_history[-100:]
        if len(recent_losses) < 2:
            loss_var = float("inf")
        else:
            mean_loss = sum(recent_losses) / len(recent_losses)
            loss_var = sum((x - mean_loss) ** 2 for x in recent_losses) / len(recent_losses)

        return {
            "avg_gradient_magnitude": float(avg_grad_mag),
            "loss_variance": float(loss_var),
            "memory_param_stability": self.memory_loop.get_parameter_stability(100),
            "skills_param_stability": self.skills_loop.get_parameter_stability(100),
            "plugins_param_stability": self.plugins_loop.get_parameter_stability(100),
        }

    def check_convergence(self) -> bool:
        """
        Check if the system has converged.

        Criteria:
          1. All loops have converged individually
          2. Unified loss variance < threshold
          3. Average gradient magnitude < threshold

        Returns:
            True if converged, False otherwise
        """
        if len(self.loss_history) < 100:
            return False

        memory_converged = self.memory_loop.check_convergence()
        skills_converged = self.skills_loop.check_convergence()
        plugins_converged = self.plugins_loop.check_convergence()

        if not (memory_converged and skills_converged and plugins_converged):
            return False

        metrics = self.get_convergence_metrics()

        if metrics["avg_gradient_magnitude"] > self.convergence_gradient_threshold:
            return False

        if metrics["loss_variance"] > self.convergence_variance_threshold:
            return False

        return True

    def get_state_snapshot(self) -> Dict[str, Any]:
        """
        Get complete state snapshot for serialization/debugging.

        Returns:
            Dict with all loop states, losses, and parameters
        """
        return {
            "step_count": self.step_count,
            "loss_history": self.loss_history[-100:],  # Last 100 steps
            "core_loop_losses": self.core_loop_losses,
            "core_weight": self.core_weight,
            "infra_weight": self.infra_weight,
            "meta_weight": self.meta_weight,
            "memory_loop": {
                "context_window_size": self.memory_loop.context_window_size,
                "layer_importance": self.memory_loop.layer_importance,
                "recall_threshold": self.memory_loop.recall_threshold,
                "loss_history": self.memory_loop.loss_history[-50:],
            },
            "skills_loop": {
                "skill_priority_weights": self.skills_loop.skill_priority_weights,
                "current_order": self.skills_loop.current_order,
                "loss_history": self.skills_loop.loss_history[-50:],
            },
            "plugins_loop": {
                "plugin_priority_weights": self.plugins_loop.plugin_priority_weights,
                "loss_history": self.plugins_loop.loss_history[-50:],
            },
            "meta_loop": {
                **self.meta_optimizer.get_state(),
                "rollbacks": self.meta_rollbacks,
                "checkpoints": self.watchdog.checkpoint_count,
            },
            "convergence_metrics": self.get_convergence_metrics(),
            "is_converged": self.check_convergence(),
        }

    def update_core_loop_loss(self, loop_id: str, loss: float):
        """
        Update a core loop's loss (called externally from core loop processes).

        Args:
            loop_id: one of ['routing', 'confidence', 'feedback', 'attention', 'latency', 'diversity']
            loss: scalar loss value in [0, 1]
        """
        if loop_id in self.core_loop_losses:
            loss = float(max(0.0, min(1.0, loss)))
            # Exponential smoothing with the meta loop's Tier 1 damping
            alpha = self.core_smoothing
            self.core_loop_losses[loop_id] = (
                alpha * self.core_loop_losses[loop_id] + (1 - alpha) * loss
            )
