#!/usr/bin/env python3
"""
NineD_LossOptimizer (ADR-0614/0615/0616) — Unified 9D Learning Vector

Orchestrates all learning loops:
  - 6 core loops (Tier 1): Routing, Confidence, Feedback, Attention, Latency, Diversity
  - 3 infrastructure loops (Tier 2): Memory, Skills, Plugins
  - 1 meta loop (Tier 3): Reserved for Phase 2B

Computes unified loss:
  L_total = 0.6 * L_core + 0.3 * L_infra + 0.1 * L_meta
  where:
    L_core = mean(L_routing, L_confidence, L_feedback, L_attention, L_latency, L_diversity)
    L_infra = 0.1*L_memory + 0.1*L_skills + 0.1*L_plugins
    L_meta = 0.0 (placeholder for Phase 2B)

Tier-specific damping prevents coupling oscillation:
  - Tier 1 (core): damping = 0.9 (responsive)
  - Tier 2 (infra): damping = 0.95 (stable)
  - Tier 3 (meta): damping = 0.99 (conservative)
"""

import json
from typing import Dict, List, Any, Optional
from datetime import datetime
from pathlib import Path

from core.learning.base import LearningLoop
from core.learning.memory_optimizer import MemoryOptimizer
from core.learning.composition_optimizer import CompositionOptimizer
from core.learning.plugin_optimizer import PluginOrchestrator
from core.learning.live_collector_integration import LiveCollectorIntegration


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
    ):
        """
        Initialize the 9D optimizer with all sub-loops.

        Args:
            tenant_id: for audit trail and data isolation
            collector_integration: if provided, emit events to Live-Collector
        """
        self.tenant_id = tenant_id
        self.collector = collector_integration or LiveCollectorIntegration(tenant_id)

        # ===== CORE LOOPS (Tier 1) =====
        # These are tracked as moving averages (simulated if not fed externally)
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

        # ===== UNIFIED LOSS TRACKING =====
        self.loss_history = []
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
        # Memory loop
        L_memory = self.memory_loop.compute_loss(feedback.get("memory", {}))

        # Skills loop
        L_skills = self.skills_loop.compute_loss(feedback.get("skills", {}))

        # Plugins loop
        L_plugins = self.plugins_loop.compute_loss(feedback.get("plugins", {}))

        # Weighted combination
        L_infra = (
            self.infra_sub_weights["memory"] * L_memory
            + self.infra_sub_weights["skills"] * L_skills
            + self.infra_sub_weights["plugins"] * L_plugins
        )

        return float(max(0.0, min(1.0, L_infra)))

    def compute_L_meta(self) -> float:
        """
        Compute meta loop loss (Phase 2B: reserved).

        Returns:
            scalar loss (currently 0.0)
        """
        return 0.0

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

        # Clip to valid range
        L_total = float(max(0.0, min(1.0, L_total)))

        return L_total

    def step(self, feedback: Dict[str, Dict[str, float]]) -> float:
        """
        Execute one optimization step across all 9D loops.

        Process:
          1. Compute unified L_total
          2. Compute gradients per loop
          3. Apply gradient updates with damping
          4. Emit Live-Collector events

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

        # Compute unified loss
        L_total = self.compute_L_total(feedback)
        self.loss_history.append(L_total)

        # Get previous loss for gradient computation
        prev_L_total = self.loss_history[-2] if len(self.loss_history) > 1 else L_total

        # ===== UPDATE TIER 2 (INFRASTRUCTURE) LOOPS =====

        # Memory loop: compute gradients and apply
        memory_gradients = self.memory_loop.compute_gradients(
            self.memory_loop.compute_loss(feedback.get("memory", {})),
            self.memory_loop.loss_history[-2]
            if len(self.memory_loop.loss_history) > 1
            else self.memory_loop.compute_loss(feedback.get("memory", {})),
        )
        self.memory_loop.apply_gradients(
            memory_gradients, learning_rate=0.01, damping=0.95
        )

        # Skills loop: compute gradients and apply
        skills_gradients = self.skills_loop.compute_gradients(
            self.skills_loop.compute_loss(feedback.get("skills", {})),
            self.skills_loop.loss_history[-2]
            if len(self.skills_loop.loss_history) > 1
            else self.skills_loop.compute_loss(feedback.get("skills", {})),
        )
        self.skills_loop.apply_gradients(
            skills_gradients, learning_rate=0.01, damping=0.95
        )

        # Plugins loop: compute gradients and apply
        plugins_gradients = self.plugins_loop.compute_gradients(
            self.plugins_loop.compute_loss(feedback.get("plugins", {})),
            self.plugins_loop.loss_history[-2]
            if len(self.plugins_loop.loss_history) > 1
            else self.plugins_loop.compute_loss(feedback.get("plugins", {})),
        )
        self.plugins_loop.apply_gradients(
            plugins_gradients, learning_rate=0.01, damping=0.95
        )

        # ===== EMIT LIVE-COLLECTOR EVENTS =====

        # Core loss components
        components = {
            k: float(v) for k, v in self.core_loop_losses.items()
        }

        # Infra loss components
        L_memory = self.memory_loop.compute_loss(feedback.get("memory", {}))
        L_skills = self.skills_loop.compute_loss(feedback.get("skills", {}))
        L_plugins = self.plugins_loop.compute_loss(feedback.get("plugins", {}))

        self.collector.on_loss_computed(
            loss_total=L_total,
            loss_components={
                **components,
                "memory": float(L_memory),
                "skills": float(L_skills),
                "plugins": float(L_plugins),
            },
            gradients={
                **memory_gradients,
                **skills_gradients,
                **plugins_gradients,
            },
            weights={
                "core": self.core_weight,
                "infra": self.infra_weight,
                "meta": self.meta_weight,
            },
            learning_rate=0.01,
        )

        # Emit per-loop events
        self.memory_loop.emit_event(self.collector, feedback=feedback.get("memory", {}))
        self.skills_loop.emit_event(
            self.collector, feedback=feedback.get("skills", {}), execution_time_ms=0
        )

        # For plugins, wrap feedback in per-plugin structure
        plugins_feedback = feedback.get("plugins", {})
        plugin_feedback_per_plugin = {
            "plugin_a": plugins_feedback,
            "plugin_b": plugins_feedback,
            "plugin_c": plugins_feedback,
        }
        self.plugins_loop.emit_event(
            self.collector, feedback=plugin_feedback_per_plugin, task_type="generic"
        )

        return L_total

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

        # Collect all gradients
        for loop in [self.memory_loop, self.skills_loop, self.plugins_loop]:
            for param_gradients in loop.gradient_history.values():
                all_gradients.extend([abs(g) for g in param_gradients[-100:]])

        avg_grad_mag = (
            sum(all_gradients) / len(all_gradients)
            if all_gradients
            else float("inf")
        )

        # Loss variance
        recent_losses = self.loss_history[-100:]
        if len(recent_losses) < 2:
            loss_var = float("inf")
        else:
            mean_loss = sum(recent_losses) / len(recent_losses)
            loss_var = sum((x - mean_loss) ** 2 for x in recent_losses) / len(
                recent_losses
            )

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

        # Check individual loops
        memory_converged = self.memory_loop.check_convergence()
        skills_converged = self.skills_loop.check_convergence()
        plugins_converged = self.plugins_loop.check_convergence()

        if not (memory_converged and skills_converged and plugins_converged):
            return False

        # Check unified metrics
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
            # Exponential smoothing to reduce noise
            alpha = 0.9
            self.core_loop_losses[loop_id] = (
                alpha * self.core_loop_losses[loop_id] + (1 - alpha) * loss
            )
