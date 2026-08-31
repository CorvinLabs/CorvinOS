"""Token-accounting canon — the fix for the ~99.99% input undercount.

A real measured turn reported input_tokens=2 while cache_read=24433 and cache_creation=38060.
Reading only input_tokens (as chat_runtime + the first benchmark draft did) captured 0.003%
of the true input. These tests pin the correct four-class summation.
"""
from core.learning.token_accounting import (
    input_tokens_total, token_components, total_tokens,
)

# The exact usage shape a real console turn returned.
REAL = {
    "input_tokens": 2,
    "cache_creation_input_tokens": 38060,
    "cache_read_input_tokens": 24433,
    "output_tokens": 3,
    "iterations": [{"input_tokens": 2}],  # extra keys must be ignored
}


def test_components_split():
    assert token_components(REAL) == {
        "fresh_input": 2, "cache_creation": 38060, "cache_read": 24433, "output": 3,
    }


def test_input_is_additive_not_double_counted():
    # fresh + creation + read (disjoint, additive) — matches the TDE inline canon.
    assert input_tokens_total(REAL) == 2 + 38060 + 24433 == 62495


def test_total_includes_output():
    assert total_tokens(REAL) == 62495 + 3 == 62498


def test_old_buggy_way_undercounts_by_99_99_percent():
    old = REAL["input_tokens"] + REAL["output_tokens"]  # what the code used to count
    assert old == 5
    assert total_tokens(REAL) - old == 62493  # the hidden 62k of cached input


def test_robust_to_malformed():
    assert total_tokens(None) == 0
    assert total_tokens({}) == 0
    assert total_tokens("garbage") == 0
    assert total_tokens({"input_tokens": None, "output_tokens": "x"}) == 0
    # partial usage still sums what's there
    assert total_tokens({"output_tokens": 10}) == 10
    assert input_tokens_total({"cache_read_input_tokens": 100}) == 100
