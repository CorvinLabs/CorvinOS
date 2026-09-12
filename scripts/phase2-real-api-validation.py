#!/usr/bin/env python3
"""
Phase 2: Real API Validation for ADR-0377 Model Routing
========================================================

Validates synthetic accuracy profiles (from EXP-001) against real API calls.

Executes N=5 real tasks across complexity/task-type diversity:
  1. Code Generation (Simple)
  2. Chat (Simple)
  3. Analysis (Medium)
  4. Code Review (Complex)
  5. Research (Complex)

For each task:
  - Call real Anthropic API with both Sonnet and Opus
  - Measure actual tokens (input_tokens, output_tokens)
  - Compare to predictions
  - Grade output quality (1–5 scale)
  - Record all events to audit trail

Output:
  - Real cost deltas (predicted vs. observed)
  - Real accuracy deltas (profile vs. observed)
  - MAE (Mean Absolute Error) per component
  - Phase 2 verdict (GATING-ready or needs refinement)

Requires:
  - ANTHROPIC_API_KEY in environment
  - ~$20–30 budget for 10 API calls (2 models × 5 tasks)

Usage:
  export ANTHROPIC_API_KEY="sk-ant-..."
  python3 scripts/phase2-real-api-validation.py --run-all
"""

import json
import os
import sys
import time
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, List, Tuple
import statistics

# Import Anthropic client
try:
    from anthropic import Anthropic
except ImportError:
    print("ERROR: anthropic package not found. Install with: pip install anthropic")
    sys.exit(1)


@dataclass(frozen=True)
class TaskDefinition:
    """A single test task."""
    task_id: str
    task_type: str  # "chat", "code_generation", "analysis", "code_review", "research"
    complexity: str  # "simple", "medium", "complex"
    prompt: str
    expected_tokens_approx: int  # For planning; actual varies


@dataclass
class APICall:
    """Record of a single API call."""
    timestamp: str
    task_id: str
    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    latency_sec: float
    output_preview: str  # First 200 chars


@dataclass
class QualityGrade:
    """Human grade of output quality (1–5 scale)."""
    grader_notes: str
    score: float  # 1.0–5.0
    confidence: float  # 0.0–1.0 (grader confidence)

    def to_accuracy_multiplier(self) -> float:
        """Convert 1–5 scale to 0–1 accuracy multiplier."""
        # 5.0 = 1.0 (perfect), 1.0 = 0.0 (useless)
        return (self.score - 1.0) / 4.0


# Test tasks: diverse coverage
PHASE2_TASKS = [
    TaskDefinition(
        task_id="phase2-001-codegen-simple",
        task_type="code_generation",
        complexity="simple",
        prompt="""Write a Python function that converts a list of numbers to a dictionary
        with the number as key and its square as value. Include docstring and type hints.""",
        expected_tokens_approx=200,
    ),
    TaskDefinition(
        task_id="phase2-002-chat-simple",
        task_type="chat",
        complexity="simple",
        prompt="""Explain in 2–3 sentences why model routing is useful for cost optimization
        in AI systems. Keep it accessible to a non-technical person.""",
        expected_tokens_approx=150,
    ),
    TaskDefinition(
        task_id="phase2-003-analysis-medium",
        task_type="analysis",
        complexity="medium",
        prompt="""Analyze the following code for potential bugs and performance issues:

def process_list(items):
    result = []
    for i in range(len(items)):
        if items[i] > 0:
            result.append(items[i] * 2)
    return sorted(result, reverse=True)

Identify 3–5 issues (correctness, style, performance). Suggest fixes.""",
        expected_tokens_approx=400,
    ),
    TaskDefinition(
        task_id="phase2-004-codereview-complex",
        task_type="code_review",
        complexity="complex",
        prompt="""Review this production code for security, performance, and maintainability:

import json
import requests

def fetch_user_data(user_id: str) -> dict:
    url = f"https://api.example.com/users/{user_id}"
    resp = requests.get(url)
    return json.loads(resp.text)

Issues to check:
1. Error handling (network failures, malformed JSON)
2. Security (input validation, injection risks)
3. Performance (timeouts, retries)
4. Maintainability (logging, type hints)

Provide detailed fixes with code examples.""",
        expected_tokens_approx=600,
    ),
    TaskDefinition(
        task_id="phase2-005-research-complex",
        task_type="research",
        complexity="complex",
        prompt="""Research and explain: What are the main trade-offs between fine-tuning
        and prompt engineering for domain-specific AI tasks?

Include:
- 3–4 key differences
- Pros/cons of each approach
- Real-world scenarios where each is preferred
- Cost and latency implications

Cite reasoning; be precise and technical.""",
        expected_tokens_approx=800,
    ),
]


class Phase2Validator:
    """Executes and documents Phase 2 validation."""

    PRICING = {
        "opus": {
            "input_per_1m": 3000,  # $30/1M input tokens
            "output_per_1m": 15000,  # $150/1M output tokens
        },
        "sonnet": {
            "input_per_1m": 300,  # $3/1M input
            "output_per_1m": 1500,  # $15/1M output
        },
    }

    ACCURACY_PROFILES = {
        # From EXP-001: empirical Sonnet accuracy multipliers (relative to Opus)
        "code_generation": {"sonnet": 0.96},
        "chat": {"sonnet": 0.99},
        "analysis": {"sonnet": 0.97},
        "code_review": {"sonnet": 0.97},
        "research": {"sonnet": 0.96},
    }

    def __init__(self, api_key: Optional[str] = None, output_dir: Optional[str] = None):
        """Initialize validator with Anthropic client."""
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not self.api_key:
            raise ValueError(
                "ANTHROPIC_API_KEY not found. Set environment variable or pass api_key."
            )

        self.client = Anthropic(api_key=self.api_key)

        self.output_dir = Path(output_dir or "/home/shumway/projects/CorvinOS/.corvin/tenants/_default/experiments/exp-001-model-routing-cost-accuracy")
        self.output_dir.mkdir(parents=True, exist_ok=True)

        self.phase2_dir = self.output_dir / "phase2-real-api"
        self.phase2_dir.mkdir(parents=True, exist_ok=True)

        self.api_calls: List[APICall] = []
        self.quality_grades: Dict[str, QualityGrade] = {}

    def calculate_cost(self, model: str, input_tokens: int, output_tokens: int) -> float:
        """Calculate cost in USD."""
        if model not in self.PRICING:
            raise ValueError(f"Unknown model: {model}")

        pricing = self.PRICING[model]
        input_cost = (input_tokens / 1_000_000) * pricing["input_per_1m"]
        output_cost = (output_tokens / 1_000_000) * pricing["output_per_1m"]
        return (input_cost + output_cost) / 100  # Convert cents to dollars

    def call_api(self, task: TaskDefinition, model: str) -> APICall:
        """Make real API call to Claude."""
        print(f"\n📡 Calling {model} for {task.task_id}...")

        start_time = time.time()
        try:
            response = self.client.messages.create(
                model=f"claude-{model}-5",
                max_tokens=2048,
                messages=[
                    {"role": "user", "content": task.prompt}
                ]
            )
            latency = time.time() - start_time

            input_tokens = response.usage.input_tokens
            output_tokens = response.usage.output_tokens
            cost = self.calculate_cost(model, input_tokens, output_tokens)

            output_text = response.content[0].text if response.content else ""
            output_preview = output_text[:200].replace("\n", " ") + "..." if len(output_text) > 200 else output_text

            api_call = APICall(
                timestamp=datetime.utcnow().isoformat(),
                task_id=task.task_id,
                model=model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cost_usd=cost,
                latency_sec=latency,
                output_preview=output_preview,
            )

            self.api_calls.append(api_call)

            print(f"  ✅ {model.upper()}: {input_tokens} in, {output_tokens} out, ${cost:.4f}, {latency:.2f}s")
            return api_call

        except Exception as e:
            print(f"  ❌ API Error: {e}")
            raise

    def grade_quality(self, task_id: str, model: str, output_text: str, rubric: str) -> QualityGrade:
        """Prompt user to grade output quality (1–5 scale)."""
        print(f"\n🎓 Quality Grading: {task_id} ({model})")
        print(f"  Output preview: {output_text[:150]}...")
        print(f"  Rubric: {rubric}")

        while True:
            try:
                score = float(input("  Enter score (1.0–5.0): "))
                if 1.0 <= score <= 5.0:
                    break
                print("  Invalid score. Enter 1.0–5.0.")
            except ValueError:
                print("  Invalid input. Enter a number.")

        confidence = float(input("  Enter grader confidence (0.0–1.0): "))
        notes = input("  Grading notes (optional): ") or "(none)"

        grade = QualityGrade(
            grader_notes=notes,
            score=score,
            confidence=confidence,
        )

        self.quality_grades[f"{task_id}_{model}"] = grade
        return grade

    def run_phase2_batch(self, interactive: bool = True):
        """Run Phase 2: 5 tasks × 2 models (10 API calls)."""
        print("=" * 80)
        print("PHASE 2: Real API Validation")
        print("=" * 80)
        print(f"Tasks: {len(PHASE2_TASKS)}")
        print(f"Models: sonnet, opus (2 models per task)")
        print(f"Total API calls: {len(PHASE2_TASKS) * 2}")
        print(f"Estimated cost: $20–30")
        print()

        if interactive:
            confirm = input("Proceed? (yes/no): ").strip().lower()
            if confirm != "yes":
                print("Cancelled.")
                return

        for task in PHASE2_TASKS:
            print(f"\n{'=' * 80}")
            print(f"Task: {task.task_id}")
            print(f"  Type: {task.task_type}, Complexity: {task.complexity}")
            print(f"  Prompt: {task.prompt[:100]}...")
            print()

            # Call both models
            calls = {}
            for model in ["sonnet", "opus"]:
                api_call = self.call_api(task, model)
                calls[model] = api_call

            # Calculate cost delta
            cost_delta = calls["opus"].cost_usd - calls["sonnet"].cost_usd
            cost_delta_pct = (cost_delta / calls["opus"].cost_usd) * 100
            print(f"\n💰 Cost delta: ${cost_delta:.4f} ({cost_delta_pct:.1f}% savings)")

            # Grade quality
            if interactive:
                for model in ["sonnet", "opus"]:
                    self.grade_quality(
                        task.task_id,
                        model,
                        calls[model].output_preview,
                        f"Evaluate {task.task_type} quality on 1–5 scale."
                    )

        # Summary
        self._print_phase2_summary()
        self._save_phase2_results()

    def _print_phase2_summary(self):
        """Print summary of Phase 2 results."""
        print("\n" + "=" * 80)
        print("PHASE 2 SUMMARY")
        print("=" * 80)

        total_cost_sonnet = sum(c.cost_usd for c in self.api_calls if c.model == "sonnet")
        total_cost_opus = sum(c.cost_usd for c in self.api_calls if c.model == "opus")

        total_tokens_sonnet = sum(
            c.input_tokens + c.output_tokens for c in self.api_calls if c.model == "sonnet"
        )
        total_tokens_opus = sum(
            c.input_tokens + c.output_tokens for c in self.api_calls if c.model == "opus"
        )

        savings = total_cost_opus - total_cost_sonnet
        savings_pct = (savings / total_cost_opus) * 100 if total_cost_opus > 0 else 0

        print(f"\nCost Summary:")
        print(f"  Sonnet total: ${total_sonnet:.4f}")
        print(f"  Opus total:   ${total_cost_opus:.4f}")
        print(f"  Savings:      ${savings:.4f} ({savings_pct:.1f}%)")
        print(f"  Expected (EXP-001): 45.53%")
        print(f"  Delta: {savings_pct - 45.53:+.1f}pp")

        print(f"\nToken Summary:")
        print(f"  Sonnet total tokens: {total_tokens_sonnet:,}")
        print(f"  Opus total tokens:   {total_tokens_opus:,}")

        print(f"\nAccuracy Summary:")
        if self.quality_grades:
            sonnet_scores = [
                g.to_accuracy_multiplier() for k, g in self.quality_grades.items() if "sonnet" in k
            ]
            opus_scores = [
                g.to_accuracy_multiplier() for k, g in self.quality_grades.items() if "opus" in k
            ]

            avg_sonnet = statistics.mean(sonnet_scores) if sonnet_scores else 0
            avg_opus = statistics.mean(opus_scores) if opus_scores else 0
            accuracy_loss = (avg_opus - avg_sonnet) * 100

            print(f"  Sonnet avg accuracy: {avg_sonnet*100:.1f}%")
            print(f"  Opus avg accuracy:   {avg_opus*100:.1f}%")
            print(f"  Accuracy loss:       {accuracy_loss:.1f}pp")
            print(f"  Expected (EXP-001):  1.87pp")
            print(f"  Delta: {accuracy_loss - 1.87:+.2f}pp")
        else:
            print("  (Grading not completed)")

        print(f"\nMAE (Mean Absolute Error) Calculation:")
        if self.quality_grades:
            cost_mae = abs(savings_pct - 45.53)
            accuracy_mae = abs(accuracy_loss - 1.87) if self.quality_grades else 0

            print(f"  Cost MAE:     {cost_mae:.1f}pp")
            print(f"  Accuracy MAE: {accuracy_mae:.2f}pp")
            print(f"  Threshold:    ≤ 15% for GATING-ready")

            if cost_mae <= 15 and accuracy_mae <= 2.0:
                print(f"  Status: ✅ GATING-READY (MAEs within tolerance)")
            else:
                print(f"  Status: ⚠️  ADVISORY (needs refinement)")

        print("\nLatency Summary:")
        sonnet_latencies = [c.latency_sec for c in self.api_calls if c.model == "sonnet"]
        opus_latencies = [c.latency_sec for c in self.api_calls if c.model == "opus"]

        if sonnet_latencies:
            print(f"  Sonnet avg latency: {statistics.mean(sonnet_latencies):.2f}s")
            print(f"  Opus avg latency:   {statistics.mean(opus_latencies):.2f}s")

    def _save_phase2_results(self):
        """Save Phase 2 results to JSON."""
        results = {
            "timestamp": datetime.utcnow().isoformat(),
            "phase": "phase2-real-api-validation",
            "n_tasks": len(PHASE2_TASKS),
            "n_models": 2,
            "total_api_calls": len(self.api_calls),

            "api_calls": [asdict(c) for c in self.api_calls],
            "quality_grades": {
                k: {
                    "score": g.score,
                    "confidence": g.confidence,
                    "notes": g.grader_notes,
                    "accuracy_multiplier": g.to_accuracy_multiplier(),
                }
                for k, g in self.quality_grades.items()
            },

            "aggregate": {
                "total_cost_sonnet_usd": sum(c.cost_usd for c in self.api_calls if c.model == "sonnet"),
                "total_cost_opus_usd": sum(c.cost_usd for c in self.api_calls if c.model == "opus"),
                "total_tokens_sonnet": sum(
                    c.input_tokens + c.output_tokens for c in self.api_calls if c.model == "sonnet"
                ),
                "total_tokens_opus": sum(
                    c.input_tokens + c.output_tokens for c in self.api_calls if c.model == "opus"
                ),
            },
        }

        output_file = self.phase2_dir / "phase2-results.json"
        with open(output_file, "w") as f:
            json.dump(results, f, indent=2)

        print(f"\n✅ Results saved to {output_file}")


def main():
    """Main entry point."""
    import argparse

    parser = argparse.ArgumentParser(description="Phase 2: Real API Validation")
    parser.add_argument(
        "--run-all",
        action="store_true",
        help="Run all 5 tasks (requires API key and ~$20–30 budget)",
    )
    parser.add_argument(
        "--api-key",
        help="Anthropic API key (or use ANTHROPIC_API_KEY env var)",
    )
    parser.add_argument(
        "--output-dir",
        help="Output directory for results",
    )

    args = parser.parse_args()

    if not args.run_all:
        print("Phase 2: Real API Validation")
        print("\nThis script runs 5 real API tasks × 2 models (10 calls).")
        print("Estimated cost: $20–30")
        print("\nUsage: python3 phase2-real-api-validation.py --run-all")
        print("\nOr set ANTHROPIC_API_KEY and run interactively:")
        print("  export ANTHROPIC_API_KEY='sk-ant-...'")
        print("  python3 phase2-real-api-validation.py")
        return

    validator = Phase2Validator(api_key=args.api_key, output_dir=args.output_dir)
    validator.run_phase2_batch(interactive=True)


if __name__ == "__main__":
    main()
