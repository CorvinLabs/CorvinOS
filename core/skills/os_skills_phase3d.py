"""Phase 3d: Advanced OS-Skills (Cost Optimizer, Multimodal, Preference Learner, Error Recovery)."""

from typing import Dict, Any


class CostOptimizerSkill:
    """os.cost_optimizer — Learns cost-efficient routing (cheaper engines for simple tasks)."""

    def execute(self, input: Dict[str, Any]) -> Dict:
        complexity = input.get("complexity", 5)
        cost_budget = input.get("cost_budget", 1.0)

        # Simple heuristic: complexity ≤ 3 → haiku (cheapest)
        if complexity <= 3:
            engine = "haiku"
            confidence = 0.95
        elif complexity <= 7:
            engine = "sonnet"
            confidence = 0.90
        else:
            engine = "opus"
            confidence = 0.85

        # Learning: user provides actual_cost feedback → optimizer tunes thresholds
        return {
            "engine": engine,
            "estimated_cost": cost_budget * (1.0 if engine == "opus" else 0.5 if engine == "sonnet" else 0.1),
            "confidence": confidence,
            "reasoning": f"Cost-optimized: {engine} for complexity {complexity}"
        }


class MultimodalCoordinatorSkill:
    """os.multimodal_coordinator — Selects vision/audio/text based on input modality."""

    def execute(self, input: Dict[str, Any]) -> Dict:
        modalities = input.get("available_modalities", ["text"])
        task = input.get("task", "analyze")

        # Heuristic: image input → vision, audio → audio, text → text
        modality_scores = {
            "vision": 0.9 if "image" in str(modalities).lower() else 0.3,
            "audio": 0.9 if "audio" in str(modalities).lower() else 0.3,
            "text": 0.9 if "text" in str(modalities).lower() else 0.3,
        }

        selected = max(modality_scores, key=modality_scores.get)
        confidence = modality_scores[selected]

        return {
            "selected_modality": selected,
            "confidence": confidence,
            "available_modalities": modalities,
            "reasoning": f"Selected {selected} for {task}"
        }


class UserPreferencelearnerSkill:
    """os.user_preference_learner — Learns individual user routing preferences."""

    def __init__(self):
        self.user_preferences = {}  # user_id → preferred_engine

    def execute(self, input: Dict[str, Any]) -> Dict:
        user_id = input.get("user_id", "unknown")
        task_complexity = input.get("complexity", 5)

        # If we have learned preference for this user, use it
        if user_id in self.user_preferences:
            preferred_engine = self.user_preferences[user_id]
            confidence = 0.9
        else:
            # Default: complexity-based
            preferred_engine = "sonnet"
            confidence = 0.5

        # Learning: user feedback → optimizer updates user_preferences dict
        return {
            "preferred_engine": preferred_engine,
            "confidence": confidence,
            "user_id": user_id,
            "reasoning": f"Personalized routing for {user_id}: {preferred_engine}"
        }


class ErrorRecoverySkill:
    """os.error_recovery — Learns fallback strategies for failed Skills."""

    def execute(self, input: Dict[str, Any]) -> Dict:
        failed_skill = input.get("failed_skill", "unknown")
        error_type = input.get("error_type", "timeout")

        # Simple fallback strategies
        fallback_map = {
            "timeout": "retry_with_sonnet",
            "out_of_memory": "retry_with_haiku",
            "rate_limit": "retry_with_backoff",
            "permission_denied": "escalate_to_admin",
        }

        fallback = fallback_map.get(error_type, "escalate_to_admin")
        confidence = 0.85 if error_type in fallback_map else 0.5

        return {
            "fallback_strategy": fallback,
            "confidence": confidence,
            "failed_skill": failed_skill,
            "reasoning": f"Recovery for {error_type}: {fallback}"
        }


# Tests
def test_phase3d_skills():
    """Test Phase 3d OS-Skills."""
    print("Phase 3d OS-Skills Tests:\n")

    # Test 1: CostOptimizer
    print("1. CostOptimizer...")
    skill = CostOptimizerSkill()
    result = skill.execute({"complexity": 2, "cost_budget": 1.0})
    assert result["engine"] == "haiku", "Simple tasks → haiku"
    print(f"   ✅ {result['reasoning']}\n")

    # Test 2: MultimodalCoordinator
    print("2. MultimodalCoordinator...")
    skill = MultimodalCoordinatorSkill()
    result = skill.execute({"available_modalities": ["image", "text"], "task": "analyze"})
    assert result["selected_modality"] in ["vision", "text"]
    print(f"   ✅ {result['reasoning']}\n")

    # Test 3: UserPreferenceLearner
    print("3. UserPreferenceLearner...")
    skill = UserPreferencelearnerSkill()
    result = skill.execute({"user_id": "alice", "complexity": 5})
    assert result["preferred_engine"] == "sonnet"
    print(f"   ✅ {result['reasoning']}\n")

    # Test 4: ErrorRecovery
    print("4. ErrorRecovery...")
    skill = ErrorRecoverySkill()
    result = skill.execute({"failed_skill": "s1", "error_type": "timeout"})
    assert result["fallback_strategy"] == "retry_with_sonnet"
    print(f"   ✅ {result['reasoning']}\n")

    print("✅ All Phase 3d skills pass!")


if __name__ == "__main__":
    test_phase3d_skills()
