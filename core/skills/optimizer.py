"""Skill optimization loop (Phase 7, LDD-style Inner + Refinement)."""

import json
from pathlib import Path
from typing import Dict, Any, List, Optional
from dataclasses import dataclass, asdict
import logging

logger = logging.getLogger(__name__)


@dataclass
class OptimizerReport:
    baseline_score: float
    final_score: float
    improvement_pct: float
    convergence_reason: str  # 'target_reached', 'plateau', 'max_iterations_hit'
    iterations_used: int
    max_iterations: int
    target_score: float
    shortfall: float
    recommendation: str
    hypotheses_tested: int


class SkillOptimizer:
    """Optimize skill (Inner + Refinement Loops)."""

    def __init__(self, skill_dir: Path):
        self.skill_dir = skill_dir
        self.epoch_state_file = skill_dir / 'current_epoch_state.json'

    def optimize_epoch(self, run_logs: List[Dict[str, Any]],
                       resume: bool = True, target_score: float = 0.70) -> OptimizerReport:
        """
        Optimize: Baseline (1-50) → Inner (51-100) → Refinement (101-150).
        """
        # Try resume
        if resume and self.epoch_state_file.exists():
            with open(self.epoch_state_file) as f:
                epoch_state = json.load(f)
            start_iteration = epoch_state['completed_iterations'] + 1
            best_score = epoch_state['best_score']
            baseline_score = epoch_state['baseline_score']
            logger.info(f"Resuming from iteration {start_iteration}, score {best_score}")
        else:
            start_iteration = 0
            baseline_score = self._score_runs(run_logs[:50])
            best_score = baseline_score
            epoch_state = {
                'epoch': 1,
                'baseline_score': baseline_score,
                'best_score': best_score,
                'completed_iterations': -1,
                'hypotheses': [],
            }

        K_MAX = 10
        plateau_counter = 0
        convergence_reason = 'max_iterations_hit'
        k = start_iteration - 1  # Initialize k to handle empty range

        # Inner Loop
        for k in range(start_iteration, K_MAX):
            # Generate hypothesis (MVP: simple rule changes)
            hypothesis = self._generate_hypothesis(k)

            # Score with hypothesis
            simulated_score = self._score_with_hypothesis(hypothesis, run_logs[50:100])

            if simulated_score > best_score:
                best_score = simulated_score
                plateau_counter = 0
                accepted = True
                logger.info(f"Iteration {k}: ✓ Score {best_score:.3f}")
            else:
                plateau_counter += 1
                accepted = False
                logger.info(f"Iteration {k}: ✗ No improvement ({plateau_counter}/3)")

            # Checkpoint (atomic write: tmp → rename)
            epoch_state['completed_iterations'] = k
            epoch_state['best_score'] = best_score
            epoch_state['hypotheses'].append({
                'iteration': k,
                'hypothesis': hypothesis,
                'score': simulated_score,
                'accepted': accepted
            })

            tmp_file = self.epoch_state_file.with_suffix('.tmp')
            with open(tmp_file, 'w') as f:
                json.dump(epoch_state, f)
            tmp_file.replace(self.epoch_state_file)

            # Plateau check
            if plateau_counter >= 3:
                convergence_reason = 'plateau'
                logger.info(f"Plateau detected after {k} iterations")
                break

            # Target check
            if best_score >= target_score:
                convergence_reason = 'target_reached'
                logger.info(f"Target score {target_score} reached")
                break

        # Refinement Loop (up to 3 iterations)
        for r in range(3):
            refined_hypothesis = self._generate_refined_hypothesis(best_score, r)
            refined_score = self._score_with_hypothesis(refined_hypothesis, run_logs[100:150])

            if refined_score > best_score:
                best_score = refined_score
                logger.info(f"Refinement {r}: ✓ Score {best_score:.3f}")

                epoch_state['best_score'] = best_score
                with open(self.epoch_state_file, 'w') as f:
                    json.dump(epoch_state, f)
            else:
                logger.info(f"Refinement {r}: ✗ No improvement")
                break

        # Cleanup
        self.epoch_state_file.unlink(missing_ok=True)

        # Report
        improvement = best_score - baseline_score
        improvement_pct = (improvement / baseline_score * 100) if baseline_score > 0 else 0
        shortfall = max(0, target_score - best_score)

        return OptimizerReport(
            baseline_score=baseline_score,
            final_score=best_score,
            improvement_pct=improvement_pct,
            convergence_reason=convergence_reason,
            iterations_used=k + 1,
            max_iterations=K_MAX,
            target_score=target_score,
            shortfall=shortfall,
            recommendation=self._generate_recommendation(convergence_reason, target_score, best_score),
            hypotheses_tested=len(epoch_state['hypotheses'])
        )

    def _score_runs(self, runs: List[Dict]) -> float:
        """Calculate MDE (Mean Directional Error)."""
        if not runs:
            return 0.0

        errors = []
        for run in runs:
            outcome = run.get('outcome', {})
            if 'latency_actual' not in outcome or 'latency_predicted' not in outcome:
                continue

            actual = outcome['latency_actual']
            predicted = outcome['latency_predicted']
            if predicted > 0:
                error = abs(actual - predicted) / predicted
                errors.append(error)

        if not errors:
            return 0.0

        mde = sum(errors) / len(errors)
        score = 1.0 - mde
        # ADR-0315: Score must be in [0, 1] (probability invariant)
        return max(0.0, min(1.0, score))

    def _score_with_hypothesis(self, hypothesis: str, runs: List[Dict]) -> float:
        """Score runs with hypothesis (simplified)."""
        # MVP: just return baseline + random improvement
        return self._score_runs(runs) + (0.02 if 'increase' in hypothesis else 0)

    def _generate_hypothesis(self, iteration: int) -> str:
        """Generate hypothesis (MVP)."""
        if iteration % 2 == 0:
            return f"increase_acs_threshold_to_{10 + iteration}s"
        else:
            return f"reduce_tde_overhead_factor_{1.0 - iteration * 0.05:.2f}"

    def _generate_refined_hypothesis(self, best_score: float, refinement_num: int) -> str:
        """Generate refined hypothesis."""
        return f"fine_tune_params_refinement_{refinement_num}"

    def _generate_recommendation(self, reason: str, target: float, score: float) -> str:
        """Generate recommendation for operator."""
        if reason == 'target_reached':
            return f"✓ Target score {target} reached. Epoch complete."
        elif reason == 'plateau':
            shortfall = target - score
            return f"Plateau after ~3-4 iterations. Shortfall: {shortfall:.3f}. Consider manual tuning."
        else:
            return f"Max iterations reached. Score: {score:.3f}. Consider longer optimization or different approach."
