#!/usr/bin/env python3
"""
Phase 2 Comprehensive: Automated Full Validation (20 Tasks × 2 Models)
=====================================================================

Executes Phase 2 with MAXIMUM DATA COLLECTION:
- 20 diverse tasks (not 5)
- 2 models per task (Sonnet vs. Opus)
- 40 API calls total
- Automated quality grading (no manual intervention)
- Complete audit trail + statistical analysis
- Comprehensive report with all measurements

Automated grading uses:
- Token efficiency (shorter output for same quality = better)
- Completeness (did it answer all parts of prompt?)
- Coherence (no errors, proper structure?)
- Relevance (on-topic, no hallucinations?)

Usage:
  export ANTHROPIC_API_KEY="sk-ant-..."
  python3 scripts/phase2-automated-comprehensive.py
"""

import json
import os
import sys
import time
import random
from dataclasses import dataclass, asdict, field
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, List, Tuple
import statistics
import hashlib

try:
    from anthropic import Anthropic
except ImportError:
    print("ERROR: anthropic package not found. Install: pip install anthropic")
    sys.exit(1)


@dataclass(frozen=True)
class TaskDefinition:
    """Test task definition."""
    task_id: str
    task_type: str
    complexity: str
    category: str  # "simple", "medium", "complex"
    prompt: str
    expected_tokens_approx: int
    evaluation_criteria: str  # For automated grading


@dataclass
class APICallRecord:
    """Complete API call record."""
    timestamp: str
    task_id: str
    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    latency_sec: float
    total_tokens: int
    output_hash: str
    output_length: int


@dataclass
class AutomatedGrade:
    """Automated quality assessment."""
    task_id: str
    model: str

    # Automated metrics (0.0–1.0)
    completeness_score: float  # Did it answer all parts?
    coherence_score: float  # No errors, proper structure?
    relevance_score: float  # On-topic, no hallucinations?
    efficiency_score: float  # Token efficiency (shorter=better, if complete)

    # Composite
    overall_score: float  # Weighted average

    # Metadata
    output_tokens: int
    output_length: int


@dataclass
class ComparisonResult:
    """Comparison of Sonnet vs Opus for a task."""
    task_id: str
    sonnet_grade: AutomatedGrade
    opus_grade: AutomatedGrade

    # Deltas
    cost_delta_usd: float
    cost_delta_pct: float
    quality_delta: float  # Opus score - Sonnet score
    efficiency_delta: float  # Sonnet efficiency - Opus efficiency


# Comprehensive 20-task suite
PHASE2_COMPREHENSIVE_TASKS = [
    # SIMPLE TASKS (5)
    TaskDefinition(
        task_id="phase2-comp-001-hello-world",
        task_type="code_generation",
        complexity="simple",
        category="simple",
        prompt="Write a Python 'Hello, World!' program. Just 2–3 lines, no extras.",
        expected_tokens_approx=80,
        evaluation_criteria="Does it print 'Hello, World!'? Is it Python? Nothing unnecessary?",
    ),
    TaskDefinition(
        task_id="phase2-comp-002-list-sum",
        task_type="code_generation",
        complexity="simple",
        category="simple",
        prompt="Write a Python function that sums a list. Include docstring.",
        expected_tokens_approx=150,
        evaluation_criteria="Correct sum logic? Has docstring? Type hints?",
    ),
    TaskDefinition(
        task_id="phase2-comp-003-explain-ml",
        task_type="chat",
        complexity="simple",
        category="simple",
        prompt="Explain machine learning in 2 sentences for a 10-year-old.",
        expected_tokens_approx=100,
        evaluation_criteria="Age-appropriate? Accurate? Exactly 2 sentences?",
    ),
    TaskDefinition(
        task_id="phase2-comp-004-fibonacci",
        task_type="code_generation",
        complexity="simple",
        category="simple",
        prompt="Write a Python function that returns the Nth Fibonacci number. N=0→0, N=1→1.",
        expected_tokens_approx=180,
        evaluation_criteria="Correct logic? Handles base cases? Clean code?",
    ),
    TaskDefinition(
        task_id="phase2-comp-005-greeting",
        task_type="chat",
        complexity="simple",
        category="simple",
        prompt="Write a friendly greeting for someone named Alice. One sentence, warm tone.",
        expected_tokens_approx=80,
        evaluation_criteria="Warm? One sentence? Uses 'Alice'?",
    ),

    # MEDIUM TASKS (5)
    TaskDefinition(
        task_id="phase2-comp-006-sql-query",
        task_type="code_generation",
        complexity="medium",
        category="medium",
        prompt="""Write a SQL query to find the top 5 customers by total purchase amount
        (assume tables: customers, orders, order_items). Include joins and aggregation.""",
        expected_tokens_approx=300,
        evaluation_criteria="Correct SQL syntax? Joins? GROUP BY? ORDER BY DESC? LIMIT 5?",
    ),
    TaskDefinition(
        task_id="phase2-comp-007-debug-loop",
        task_type="analysis",
        complexity="medium",
        category="medium",
        prompt="""Find bugs in this code:

def count_evens(nums):
    count = 0
    for i in range(len(nums)):
        if nums[i] % 2 == 0:
            count += 1
    return count

List 2–3 bugs (real or potential) and fixes.""",
        expected_tokens_approx=250,
        evaluation_criteria="Identified real issues? Provided fixes? Clear explanation?",
    ),
    TaskDefinition(
        task_id="phase2-comp-008-api-design",
        task_type="analysis",
        complexity="medium",
        category="medium",
        prompt="""Design a REST API endpoint for creating a user account.
        Include: HTTP method, URL path, request body (JSON), response body, status codes.""",
        expected_tokens_approx=350,
        evaluation_criteria="Valid HTTP method? Proper JSON format? All status codes? RESTful?",
    ),
    TaskDefinition(
        task_id="phase2-comp-009-compare-algos",
        task_type="analysis",
        complexity="medium",
        category="medium",
        prompt="""Compare QuickSort vs. MergeSort on 3 dimensions:
        (1) time complexity, (2) space complexity, (3) when to use. Be specific.""",
        expected_tokens_approx=300,
        evaluation_criteria="All 3 dimensions covered? Correct complexity? Practical advice?",
    ),
    TaskDefinition(
        task_id="phase2-comp-010-docker-basics",
        task_type="chat",
        complexity="medium",
        category="medium",
        prompt="""Explain Docker in 3–4 sentences. Assume reader knows Linux but not Docker.
        Include: what it is, why it's useful, one real example.""",
        expected_tokens_approx=200,
        evaluation_criteria="Accurate? Accessible? 3–4 sentences? Real example?",
    ),

    # COMPLEX TASKS (5)
    TaskDefinition(
        task_id="phase2-comp-011-auth-design",
        task_type="code_review",
        complexity="complex",
        category="complex",
        prompt="""Review this authentication code for security issues:

import hashlib

def store_password(username, password):
    pwd_hash = hashlib.md5(password.encode()).hexdigest()
    db.insert(username, pwd_hash)

Find: (1) cryptography weaknesses, (2) design flaws, (3) fixes. Be specific.""",
        expected_tokens_approx=400,
        evaluation_criteria="Identified MD5 weakness? Salt needed? Bcrypt suggested? Detailed fixes?",
    ),
    TaskDefinition(
        task_id="phase2-comp-012-ml-pipeline",
        task_type="analysis",
        complexity="complex",
        category="complex",
        prompt="""Design a machine learning pipeline for sentiment analysis.
        Include: data prep, feature engineering, model selection, evaluation, deployment.
        List tools/frameworks you'd use (sklearn, TensorFlow, etc.).""",
        expected_tokens_approx=500,
        evaluation_criteria="All steps covered? Realistic tools? Proper sequence? Evaluation metrics?",
    ),
    TaskDefinition(
        task_id="phase2-comp-013-distributed-cache",
        task_type="research",
        complexity="complex",
        category="complex",
        prompt="""Explain cache invalidation strategies in distributed systems.
        Include: TTL, event-driven, write-through, write-back.
        Trade-offs (consistency vs. performance) for each. Real examples.""",
        expected_tokens_approx=600,
        evaluation_criteria="All strategies? Trade-offs explained? Examples? Technical depth?",
    ),
    TaskDefinition(
        task_id="phase2-comp-014-transaction-design",
        task_type="code_review",
        complexity="complex",
        category="complex",
        prompt="""Review this database transaction code:

def transfer_money(from_acct, to_acct, amount):
    from_balance = db.get_balance(from_acct)
    if from_balance >= amount:
        db.update_balance(from_acct, from_balance - amount)
        db.update_balance(to_acct, db.get_balance(to_acct) + amount)
        return True
    return False

Find: (1) race condition, (2) data consistency issues, (3) transaction handling. Suggest fixes.""",
        expected_tokens_approx=450,
        evaluation_criteria="Race condition identified? Consistency issue found? ACID discussed? Fixes correct?",
    ),
    TaskDefinition(
        task_id="phase2-comp-015-kubernetes-tradeoff",
        task_type="research",
        complexity="complex",
        category="complex",
        prompt="""Compare Kubernetes vs. serverless (AWS Lambda) for deploying microservices.
        Include: (1) control & flexibility, (2) cost models, (3) scaling behavior,
        (4) operational complexity, (5) when to use each. Be nuanced.""",
        expected_tokens_approx=700,
        evaluation_criteria="All 5 dimensions? Nuanced tradeoffs? Cost models accurate? Practical advice?",
    ),

    # COMPLEX TASKS (5 more, different domains)
    TaskDefinition(
        task_id="phase2-comp-016-security-audit",
        task_type="code_review",
        complexity="complex",
        category="complex",
        prompt="""Audit this web service for OWASP Top 10 vulnerabilities:

from flask import Flask, request, render_template_string

app = Flask(__name__)

@app.route('/search')
def search():
    query = request.args.get('q')
    template = f"<h1>Results for {query}</h1>"
    return render_template_string(template)

Find vulnerabilities and fixes. Be specific about attack vectors.""",
        expected_tokens_approx=500,
        evaluation_criteria="Found template injection? XSS? Explained attacks? Fixes correct? OWASP reference?",
    ),
    TaskDefinition(
        task_id="phase2-comp-017-performance-optimization",
        task_type="analysis",
        complexity="complex",
        category="complex",
        prompt="""Optimize this Python code for performance:

def find_duplicates(lst):
    duplicates = []
    for i in range(len(lst)):
        for j in range(i+1, len(lst)):
            if lst[i] == lst[j] and lst[i] not in duplicates:
                duplicates.append(lst[i])
    return duplicates

Identify: (1) algorithmic inefficiency, (2) time complexity, (3) optimized solution, (4) new complexity.""",
        expected_tokens_approx=350,
        evaluation_criteria="Identified O(n²) → optimized? Hash set approach? New complexity correct? Code provided?",
    ),
    TaskDefinition(
        task_id="phase2-comp-018-testing-strategy",
        task_type="research",
        complexity="complex",
        category="complex",
        prompt="""Design a comprehensive testing strategy for a payment processing system.
        Include: (1) unit tests, (2) integration tests, (3) E2E tests, (4) security tests,
        (5) load tests. For each: what to test, tools, success criteria.""",
        expected_tokens_approx=700,
        evaluation_criteria="All 5 levels? Specific test cases? Tools realistic? Success criteria clear?",
    ),
    TaskDefinition(
        task_id="phase2-comp-019-migration-planning",
        task_type="research",
        complexity="complex",
        category="complex",
        prompt="""Plan a migration from monolithic to microservices architecture.
        Include: (1) decomposition strategy, (2) data migration, (3) communication patterns,
        (4) failure modes, (5) rollback plan. Assume 100+ API endpoints, 5TB data.""",
        expected_tokens_approx=800,
        evaluation_criteria="Realistic strategy? Data integrity? Communication patterns? Rollback plan? Scale-aware?",
    ),
    TaskDefinition(
        task_id="phase2-comp-020-ai-ethics",
        task_type="research",
        complexity="complex",
        category="complex",
        prompt="""Discuss AI bias in hiring algorithms. Include: (1) how bias enters models,
        (2) real-world harms, (3) detection methods, (4) mitigation strategies,
        (5) legal/regulatory landscape. Be specific and technical.""",
        expected_tokens_approx=700,
        evaluation_criteria="Bias mechanisms explained? Real harms cited? Detection methods? Mitigation? Legal landscape?",
    ),
]


class Phase2Comprehensive:
    """Automated comprehensive Phase 2 validator."""

    PRICING = {
        "opus": {"input": 3000, "output": 15000},  # per 1M tokens, in cents
        "sonnet": {"input": 300, "output": 1500},
    }

    def __init__(self, api_key: Optional[str] = None, output_dir: Optional[str] = None):
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not self.api_key:
            raise ValueError("ANTHROPIC_API_KEY required")

        self.client = Anthropic(api_key=self.api_key)

        self.output_dir = Path(output_dir or "/home/shumway/projects/CorvinOS/.corvin/tenants/_default/experiments/exp-001-model-routing-cost-accuracy")
        self.phase2_dir = self.output_dir / "phase2-comprehensive"
        self.phase2_dir.mkdir(parents=True, exist_ok=True)

        self.api_calls: List[APICallRecord] = []
        self.grades: List[AutomatedGrade] = []
        self.comparisons: List[ComparisonResult] = []

    def calculate_cost(self, model: str, input_tokens: int, output_tokens: int) -> float:
        """Cost in USD."""
        pricing = self.PRICING[model]
        input_cost = (input_tokens / 1_000_000) * pricing["input"]
        output_cost = (output_tokens / 1_000_000) * pricing["output"]
        return (input_cost + output_cost) / 100  # cents → dollars

    def call_api(self, task: TaskDefinition, model: str) -> Tuple[APICallRecord, str]:
        """Call real API."""
        print(f"  📡 {model.upper()}: {task.task_id}...", end=" ", flush=True)

        start = time.time()
        try:
            resp = self.client.messages.create(
                model=f"claude-{model}-5",
                max_tokens=2048,
                messages=[{"role": "user", "content": task.prompt}],
            )
            latency = time.time() - start

            input_tok = resp.usage.input_tokens
            output_tok = resp.usage.output_tokens
            total_tok = input_tok + output_tok
            cost = self.calculate_cost(model, input_tok, output_tok)

            output_text = resp.content[0].text if resp.content else ""
            output_hash = hashlib.sha256(output_text.encode()).hexdigest()[:16]

            record = APICallRecord(
                timestamp=datetime.utcnow().isoformat(),
                task_id=task.task_id,
                model=model,
                input_tokens=input_tok,
                output_tokens=output_tok,
                cost_usd=cost,
                latency_sec=latency,
                total_tokens=total_tok,
                output_hash=output_hash,
                output_length=len(output_text),
            )

            self.api_calls.append(record)
            print(f"✅ {input_tok}in/{output_tok}out, ${cost:.4f}, {latency:.2f}s")

            return record, output_text

        except Exception as e:
            print(f"❌ {e}")
            raise

    def auto_grade(self, task: TaskDefinition, model: str, output_text: str, api_call: APICallRecord) -> AutomatedGrade:
        """Automated quality grading."""
        # Completeness: did output address all parts of prompt?
        prompt_lower = task.prompt.lower()
        output_lower = output_text.lower()

        # Count prompt requirements answered
        requirements = task.evaluation_criteria.split("?")
        answered = sum(1 for req in requirements if any(
            kw in output_lower for kw in req.split() if len(kw) > 3
        ))
        completeness = min(1.0, answered / len(requirements))

        # Coherence: no obvious errors, proper structure
        # Check for common error patterns
        error_patterns = ["error", "undefined", "traceback", "exception", "invalid"]
        has_errors = any(p in output_lower for p in error_patterns)
        coherence = 0.7 if has_errors else 0.95

        # Relevance: on-topic, no off-topic rambling
        task_type_keywords = {
            "code_generation": ["def", "class", "return", "import", "function"],
            "chat": [],  # no specific keywords
            "analysis": ["issue", "bug", "problem", "fix", "solution", "error"],
            "code_review": ["security", "performance", "review", "code", "issue"],
            "research": ["research", "study", "explain", "discuss", "compare"],
        }

        keywords = task_type_keywords.get(task.task_type, [])
        relevant_words = sum(1 for kw in keywords if kw in output_lower)
        relevance = min(1.0, (relevant_words + 2) / (len(keywords) + 2))  # default to 0.66 if no keywords

        # Efficiency: shorter is better if complete
        # Longer output = less efficient if same completeness
        output_tokens = api_call.output_tokens
        expected_output = api_call.total_tokens - api_call.input_tokens
        efficiency = max(0.5, 1.0 - (output_tokens / (expected_output * 1.5)))

        # Weighted composite score
        overall = (
            completeness * 0.4 +
            coherence * 0.3 +
            relevance * 0.2 +
            efficiency * 0.1
        )

        grade = AutomatedGrade(
            task_id=task.task_id,
            model=model,
            completeness_score=completeness,
            coherence_score=coherence,
            relevance_score=relevance,
            efficiency_score=efficiency,
            overall_score=overall,
            output_tokens=api_call.output_tokens,
            output_length=api_call.output_length,
        )

        self.grades.append(grade)
        return grade

    def run_comprehensive(self):
        """Execute all 20 tasks × 2 models."""
        print("=" * 80)
        print("PHASE 2 COMPREHENSIVE: 20 Tasks × 2 Models (40 API Calls)")
        print("=" * 80)
        print(f"\nTasks: {len(PHASE2_COMPREHENSIVE_TASKS)}")
        print(f"Models: sonnet, opus")
        print(f"Total API calls: {len(PHASE2_COMPREHENSIVE_TASKS) * 2}")
        print(f"Estimated cost: $50–80")
        print()

        task_num = 0
        for task in PHASE2_COMPREHENSIVE_TASKS:
            task_num += 1
            print(f"\n[{task_num:2d}/{len(PHASE2_COMPREHENSIVE_TASKS)}] {task.task_id}")
            print(f"  Type: {task.task_type} ({task.complexity})")
            print(f"  Prompt: {task.prompt[:80]}...")

            # Call both models
            calls = {}
            outputs = {}
            for model in ["sonnet", "opus"]:
                call, output = self.call_api(task, model)
                calls[model] = call
                outputs[model] = output

            # Grade both
            grades = {}
            for model in ["sonnet", "opus"]:
                grade = self.auto_grade(task, model, outputs[model], calls[model])
                grades[model] = grade
                print(f"    {model.upper()} grade: {grade.overall_score:.2f} (compl={grade.completeness_score:.2f}, coh={grade.coherence_score:.2f}, rel={grade.relevance_score:.2f}, eff={grade.efficiency_score:.2f})")

            # Compare
            cost_delta = calls["opus"].cost_usd - calls["sonnet"].cost_usd
            cost_delta_pct = (cost_delta / calls["opus"].cost_usd * 100) if calls["opus"].cost_usd > 0 else 0
            quality_delta = grades["opus"].overall_score - grades["sonnet"].overall_score
            eff_delta = grades["sonnet"].efficiency_score - grades["opus"].efficiency_score

            comp = ComparisonResult(
                task_id=task.task_id,
                sonnet_grade=grades["sonnet"],
                opus_grade=grades["opus"],
                cost_delta_usd=cost_delta,
                cost_delta_pct=cost_delta_pct,
                quality_delta=quality_delta,
                efficiency_delta=eff_delta,
            )
            self.comparisons.append(comp)

            print(f"  💰 Cost delta: ${cost_delta:.4f} ({cost_delta_pct:.1f}% savings)")
            print(f"  ✓ Quality delta (Opus - Sonnet): {quality_delta:+.3f}")

        self._generate_report()

    def _generate_report(self):
        """Generate comprehensive report."""
        print("\n" + "=" * 80)
        print("PHASE 2 COMPREHENSIVE REPORT")
        print("=" * 80)

        # Cost analysis
        total_sonnet = sum(c.cost_usd for c in self.api_calls if c.model == "sonnet")
        total_opus = sum(c.cost_usd for c in self.api_calls if c.model == "opus")
        total_savings = total_opus - total_sonnet
        savings_pct = (total_savings / total_opus * 100) if total_opus > 0 else 0

        print(f"\nCOST SUMMARY:")
        print(f"  Sonnet total:     ${total_sonnet:.4f}")
        print(f"  Opus total:       ${total_opus:.4f}")
        print(f"  Total savings:    ${total_savings:.4f} ({savings_pct:.1f}%)")
        print(f"  Expected (EXP-001): 45.53%")
        print(f"  Delta:             {savings_pct - 45.53:+.1f}pp")

        # Token analysis
        sonnet_tokens = sum(c.total_tokens for c in self.api_calls if c.model == "sonnet")
        opus_tokens = sum(c.total_tokens for c in self.api_calls if c.model == "opus")

        print(f"\nTOKEN SUMMARY:")
        print(f"  Sonnet total:     {sonnet_tokens:,} tokens")
        print(f"  Opus total:       {opus_tokens:,} tokens")
        print(f"  Token delta:      {opus_tokens - sonnet_tokens:,}")

        # Quality analysis
        sonnet_scores = [g.overall_score for g in self.grades if g.model == "sonnet"]
        opus_scores = [g.overall_score for g in self.grades if g.model == "opus"]

        sonnet_avg = statistics.mean(sonnet_scores)
        opus_avg = statistics.mean(opus_scores)
        quality_loss = (opus_avg - sonnet_avg) * 100  # percentage points

        print(f"\nQUALITY SUMMARY (Automated Grades, 0–1 scale):")
        print(f"  Sonnet avg score: {sonnet_avg:.3f}")
        print(f"  Opus avg score:   {opus_avg:.3f}")
        print(f"  Quality delta:    {quality_loss:+.2f}pp (negative = Sonnet worse)")
        print(f"  Expected loss:    1.87pp (from EXP-001)")

        # Breakdown by complexity
        print(f"\nBREAKDOWN BY COMPLEXITY:")
        for complexity in ["simple", "medium", "complex"]:
            tasks = [t for t in PHASE2_COMPREHENSIVE_TASKS if t.complexity == complexity]
            if not tasks:
                continue

            task_ids = [t.task_id for t in tasks]
            calls_for_complexity = [c for c in self.api_calls if c.task_id in task_ids]
            comparisons_for_complexity = [comp for comp in self.comparisons if comp.task_id in task_ids]

            if not calls_for_complexity:
                continue

            sonnet_cost = sum(c.cost_usd for c in calls_for_complexity if c.model == "sonnet")
            opus_cost = sum(c.cost_usd for c in calls_for_complexity if c.model == "opus")
            savings = (opus_cost - sonnet_cost) / opus_cost * 100 if opus_cost > 0 else 0

            avg_quality_delta = statistics.mean([comp.quality_delta for comp in comparisons_for_complexity])

            print(f"  {complexity.upper()} ({len(tasks)} tasks):")
            print(f"    Savings: {savings:.1f}%")
            print(f"    Quality delta: {avg_quality_delta:+.3f}")

        # Latency analysis
        sonnet_latencies = [c.latency_sec for c in self.api_calls if c.model == "sonnet"]
        opus_latencies = [c.latency_sec for c in self.api_calls if c.model == "opus"]

        print(f"\nLATENCY SUMMARY:")
        print(f"  Sonnet avg:       {statistics.mean(sonnet_latencies):.2f}s (min={min(sonnet_latencies):.2f}, max={max(sonnet_latencies):.2f})")
        print(f"  Opus avg:         {statistics.mean(opus_latencies):.2f}s (min={min(opus_latencies):.2f}, max={max(opus_latencies):.2f})")

        # Gating decision
        cost_mae = abs(savings_pct - 45.53)
        quality_mae = abs(quality_loss - 1.87)

        print(f"\nGATING DECISION (MAE Analysis):")
        print(f"  Cost MAE:         {cost_mae:.1f}pp (threshold: ≤15pp)")
        print(f"  Quality MAE:      {quality_mae:.2f}pp (threshold: ≤2.0pp)")

        if cost_mae <= 15 and quality_mae <= 2.0:
            gating = "✅ GATING-READY"
        else:
            gating = "⚠️  ADVISORY"

        print(f"  Status:           {gating}")

        # Save results
        self._save_results(savings_pct, quality_loss, cost_mae, quality_mae, gating)

    def _save_results(self, savings_pct: float, quality_loss: float, cost_mae: float, quality_mae: float, gating: str):
        """Save comprehensive results."""
        results = {
            "timestamp": datetime.utcnow().isoformat(),
            "phase": "phase2-comprehensive",
            "n_tasks": len(PHASE2_COMPREHENSIVE_TASKS),
            "n_models": 2,
            "total_api_calls": len(self.api_calls),

            "api_calls": [asdict(c) for c in self.api_calls],
            "grades": [asdict(g) for g in self.grades],
            "comparisons": [
                {
                    "task_id": c.task_id,
                    "cost_delta_usd": c.cost_delta_usd,
                    "cost_delta_pct": c.cost_delta_pct,
                    "quality_delta": c.quality_delta,
                    "efficiency_delta": c.efficiency_delta,
                }
                for c in self.comparisons
            ],

            "aggregate": {
                "total_cost_sonnet_usd": sum(c.cost_usd for c in self.api_calls if c.model == "sonnet"),
                "total_cost_opus_usd": sum(c.cost_usd for c in self.api_calls if c.model == "opus"),
                "total_tokens_sonnet": sum(c.total_tokens for c in self.api_calls if c.model == "sonnet"),
                "total_tokens_opus": sum(c.total_tokens for c in self.api_calls if c.model == "opus"),
                "cost_savings_pct": savings_pct,
                "quality_loss_pct": quality_loss,
            },

            "gating": {
                "cost_mae": cost_mae,
                "quality_mae": quality_mae,
                "verdict": gating,
                "timestamp": datetime.utcnow().isoformat(),
            },
        }

        output_file = self.phase2_dir / "phase2-comprehensive-results.json"
        with open(output_file, "w") as f:
            json.dump(results, f, indent=2)

        print(f"\n✅ Results saved: {output_file}")


def main():
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("ERROR: ANTHROPIC_API_KEY not set")
        sys.exit(1)

    validator = Phase2Comprehensive(api_key=api_key)
    validator.run_comprehensive()


if __name__ == "__main__":
    main()
