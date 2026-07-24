"""Benchmark fixture definitions — deterministic, reproducible tasks.

Each fixture is a complete, self-contained task definition including:
  - Task prompt
  - Expected input/context
  - Expected output (for validation)
  - Estimated tokens
  - Category (trivial, simple, moderate, complex, parallel, big_data)
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class BenchmarkFixture:
    """One benchmark task."""

    fixture_id: str
    category: str  # trivial | simple | moderate | complex | parallel | big_data
    prompt: str
    context: dict[str, Any]
    expected_output: str | None = None
    estimated_tokens: int = 0
    parallelizable: float = 0.0  # 0.0-1.0
    context_depth: str = "low"  # low | medium | high | very_high
    description: str = ""


def load_fixtures() -> dict[str, BenchmarkFixture]:
    """Load all benchmark fixtures (in-memory definitions)."""

    fixtures = {
        # ===== TRIVIAL CATEGORY =====
        "trivial_001": BenchmarkFixture(
            fixture_id="trivial_001",
            category="trivial",
            prompt="Fix the typo in line 5 of this README: change 'CorvinOS Quickstart' to 'CorvinOS Quick Start'",
            context={
                "file_content": """# CorvinOS Documentation

## Installation

See INSTALLATION.md for setup.

## CorvinOS Quickstart

Run: `corvin --help`
"""
            },
            expected_output="CorvinOS Quick Start",
            estimated_tokens=500,
            parallelizable=0.0,
            context_depth="low",
            description="Fix single typo in text file",
        ),
        "trivial_002": BenchmarkFixture(
            fixture_id="trivial_002",
            category="trivial",
            prompt="Add the missing return statement to this function",
            context={
                "function": """def add(a, b):
    result = a + b
"""
            },
            expected_output="return result",
            estimated_tokens=600,
            parallelizable=0.0,
            context_depth="low",
            description="Add missing return statement",
        ),
        "trivial_003": BenchmarkFixture(
            fixture_id="trivial_003",
            category="trivial",
            prompt="Update this import statement from 'from x import y' to 'import x.y'",
            context={
                "code": "from collections import defaultdict\n\ndata = defaultdict(list)"
            },
            expected_output="import collections",
            estimated_tokens=550,
            parallelizable=0.0,
            context_depth="low",
            description="Update import statement",
        ),
        # ===== SIMPLE CATEGORY =====
        "simple_001": BenchmarkFixture(
            fixture_id="simple_001",
            category="simple",
            prompt="Rename the function 'compute_avg' to 'calculate_average' and update all 3 callers",
            context={
                "files": {
                    "math_utils.py": "def compute_avg(vals): return sum(vals) / len(vals)",
                    "stats.py": "x = compute_avg(data1)\ny = compute_avg(data2)",
                    "report.py": "avg = compute_avg(values)",
                }
            },
            expected_output="calculate_average",
            estimated_tokens=1800,
            parallelizable=0.0,
            context_depth="medium",
            description="Rename function + update all callers",
        ),
        "simple_002": BenchmarkFixture(
            fixture_id="simple_002",
            category="simple",
            prompt="Add type hints to this function: def greet(name, age): return f'Hello {name}, you are {age}'",
            context={"function": "def greet(name, age): return f'Hello {name}, you are {age}'"},
            expected_output="def greet(name: str, age: int) -> str:",
            estimated_tokens=1500,
            parallelizable=0.0,
            context_depth="low",
            description="Add type hints to function",
        ),
        # ===== MODERATE CATEGORY =====
        "moderate_001": BenchmarkFixture(
            fixture_id="moderate_001",
            category="moderate",
            prompt="""Refactor this calculator.py to extract magic numbers into named constants.
The file has 4 magic numbers that should become: TAX_RATE, DISCOUNT_THRESHOLD,
MIN_PURCHASE, MAX_PURCHASE. After refactoring, run tests to verify all still pass.
If any tests fail, fix the issues and iterate.""",
            context={
                "calculator.py": """def apply_tax(amount):
    return amount * 1.07

def apply_discount(amount):
    if amount > 100:
        return amount * 0.9
    return amount

def validate_purchase(amount):
    if amount < 10 or amount > 10000:
        return False
    return True
""",
                "test_calculator.py": """def test_tax(): assert apply_tax(100) == 107
def test_discount(): assert apply_discount(150) == 135
def test_validate(): assert validate_purchase(50)
""",
            },
            expected_output="TAX_RATE, DISCOUNT_THRESHOLD, MIN_PURCHASE, MAX_PURCHASE",
            estimated_tokens=8500,
            parallelizable=0.2,
            context_depth="high",
            description="Refactor with constants + iterate on test failures",
        ),
        "moderate_002": BenchmarkFixture(
            fixture_id="moderate_002",
            category="moderate",
            prompt="""Review this API handler and suggest improvements:
1. Add request validation
2. Add error handling
3. Add logging
4. Update docstring
Implement all suggestions and test with 3 different inputs (valid, missing field, invalid type).""",
            context={
                "handler.py": """def process_user(data):
    user = User(data['name'], data['age'])
    db.save(user)
    return {'status': 'ok'}
"""
            },
            expected_output="validation, error handling, logging",
            estimated_tokens=10000,
            parallelizable=0.25,
            context_depth="high",
            description="API handler refactor + iterative testing",
        ),
        # ===== COMPLEX CATEGORY =====
        "complex_001": BenchmarkFixture(
            fixture_id="complex_001",
            category="complex",
            prompt="""Design and implement a caching layer for this data pipeline.
The pipeline reads 3 CSV files, transforms each with 4 steps, and aggregates results.
Add caching at appropriate boundaries, invalidation strategy, and monitoring.
Test with repeated runs and verify cache hits increase over time.""",
            context={
                "pipeline_steps": [
                    "read_csv_1",
                    "transform_step_1",
                    "transform_step_2",
                    "read_csv_2",
                    "transform_step_3",
                    "read_csv_3",
                    "transform_step_4",
                    "aggregate",
                ]
            },
            expected_output="caching layer, invalidation, monitoring",
            estimated_tokens=18000,
            parallelizable=0.3,
            context_depth="very_high",
            description="Design + implement caching system with validation",
        ),
        # ===== PARALLEL CATEGORY =====
        "parallel_001": BenchmarkFixture(
            fixture_id="parallel_001",
            category="parallel",
            prompt="""Process 50 CSV files (each 1MB, ~500 rows).
For each file: read → extract column 'sales' → compute sum.
Then aggregate all sums into one final total.
This should parallelize well across the 50 files.""",
            context={
                "files": 50,
                "rows_per_file": 500,
                "total_mb": 50,
                "description": "50 x 1MB CSV files, embarrassingly parallel",
            },
            expected_output="total_sum",
            estimated_tokens=12000,
            parallelizable=0.95,
            context_depth="low",
            description="Parallel CSV processing (50 files)",
        ),
        "parallel_002": BenchmarkFixture(
            fixture_id="parallel_002",
            category="parallel",
            prompt="""Transform 100 JSON records through a 3-step pipeline:
1. Extract key fields
2. Validate schema
3. Normalize values
All records are independent; this should parallelize fully.""",
            context={
                "records": 100,
                "record_size_kb": 10,
                "total_mb": 1,
                "description": "100 independent JSON record transformations",
            },
            expected_output="transformed_records",
            estimated_tokens=8000,
            parallelizable=0.98,
            context_depth="low",
            description="Parallel JSON transformation (100 records)",
        ),
        # ===== BIG DATA CATEGORY =====
        "big_data_001": BenchmarkFixture(
            fixture_id="big_data_001",
            category="big_data",
            prompt="""Analyze a 500MB Parquet file with 1M rows.
Compute:
1. Row count per user_id (top 10)
2. Average transaction amount by category
3. Identify outliers (>3σ from mean)
Output summary statistics.""",
            context={
                "file_size_mb": 500,
                "row_count": 1_000_000,
                "columns": ["user_id", "amount", "category", "timestamp"],
                "note": "Too large for LLM context; ACS required",
            },
            expected_output="summary_statistics",
            estimated_tokens=8000,  # ACS will handle, LLM just coordinates
            parallelizable=0.99,
            context_depth="low",
            description="Big data analysis (500MB Parquet, 1M rows)",
        ),
    }

    return fixtures


def get_fixture(fixture_id: str) -> BenchmarkFixture | None:
    """Get a specific fixture by ID."""
    fixtures = load_fixtures()
    return fixtures.get(fixture_id)


def get_fixtures_by_category(category: str) -> dict[str, BenchmarkFixture]:
    """Get all fixtures in a category."""
    fixtures = load_fixtures()
    return {fid: f for fid, f in fixtures.items() if f.category == category}


def get_categories() -> list[str]:
    """List all available categories."""
    return ["trivial", "simple", "moderate", "complex", "parallel", "big_data"]
