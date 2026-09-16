"""Tier 4: Adversarial Sweep — Robustness Testing

Systematically perturb inputs, test failure modes, validate recovery.
"""

from dataclasses import dataclass
from typing import Callable, Dict, List, Any
from enum import Enum
import random


class AdversarialType(Enum):
    INPUT_MUTATION = "input_mutation"
    LATENCY_INJECTION = "latency_injection"
    CONCURRENCY_STRESS = "concurrency_stress"


@dataclass
class AdversarialResult:
    """Result of a single adversarial test."""
    test_type: AdversarialType
    input_mutation: Dict[str, Any]
    expected_output: Any
    actual_output: Any
    passed: bool
    error: str = None


class AdversarialSweep:
    """Systematic robustness testing."""
    
    def __init__(self, handler: Callable, baseline_output: Any = None):
        self.handler = handler
        self.baseline_output = baseline_output
        self.results: List[AdversarialResult] = []
    
    async def mutate_input(self, input_data: Dict, mutation_type: str = "noise") -> Dict:
        """Mutate input."""
        mutated = dict(input_data)
        
        if mutation_type == "noise":
            for key, value in mutated.items():
                if isinstance(value, (int, float)):
                    mutated[key] = value * (1 + random.uniform(-0.5, 0.5))
        elif mutation_type == "missing":
            keys = list(mutated.keys())
            if keys:
                mutated.pop(random.choice(keys), None)
        elif mutation_type == "overflow":
            for key in mutated:
                mutated[key] = 10**10
        
        return mutated
    
    async def test_input_robustness(self, inputs: List[Dict], mutation_count: int = 10) -> List[AdversarialResult]:
        """Test with mutated inputs."""
        results = []
        
        for input_data in inputs:
            for mutation_type in ["noise", "missing", "overflow"]:
                for _ in range(mutation_count // 3):
                    try:
                        mutated = await self.mutate_input(input_data, mutation_type)
                        result = await self.handler(mutated)
                        
                        results.append(AdversarialResult(
                            test_type=AdversarialType.INPUT_MUTATION,
                            input_mutation=mutated,
                            expected_output=self.baseline_output,
                            actual_output=result,
                            passed=result is not None
                        ))
                    except Exception as e:
                        results.append(AdversarialResult(
                            test_type=AdversarialType.INPUT_MUTATION,
                            input_mutation=mutated,
                            expected_output=self.baseline_output,
                            actual_output=None,
                            passed=False,
                            error=str(e)
                        ))
        
        self.results.extend(results)
        return results
    
    def get_pass_rate(self) -> float:
        """Get percentage of passed tests."""
        if not self.results:
            return 1.0
        passed = sum(1 for r in self.results if r.passed)
        return passed / len(self.results)
