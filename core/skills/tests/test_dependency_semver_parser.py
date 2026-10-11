"""Version constraints on a Skill dependency must be enforced, with or without a space.

``DependencyValidator._semver_range_satisfies`` only recognised an operator as its own
whitespace-separated token (``">= 1.0.0"``). ``">=1.0.0"`` and ``"<2.0.0"`` matched no branch
and the function returned True for every version, so a constraint written the usual way was
silently ignored (fail-OPEN). It now parses terms with a regex and rejects anything it cannot read.
"""
from __future__ import annotations

import pytest

from core.skills.skill_dag_loader import DependencyValidator

sat = DependencyValidator._semver_range_satisfies


@pytest.mark.parametrize("installed,constraint,expected", [
    # the original bug: no space after the operator
    ("2.0.0", "<2.0.0", False),
    ("1.9.9", "<2.0.0", True),
    ("0.9.0", ">=1.0.0", False),
    ("1.0.0", ">=1.0.0", True),
    # both spellings agree
    ("2.0.0", "< 2.0.0", False),
    ("0.9.0", ">= 1.0.0", False),
    # ranges joined by space or comma
    ("1.5.0", ">=1.0.0 <2.0.0", True),
    ("2.0.0", ">=1.0.0 <2.0.0", False),
    ("0.5.0", ">=1.0.0,<2.0.0", False),
    ("1.5.0", ">=1.0.0, <2.0.0", True),
    # the other operators
    ("1.0.0", ">1.0.0", False), ("1.0.1", ">1.0.0", True),
    ("1.0.0", "<=1.0.0", True), ("1.0.1", "<=1.0.0", False),
    ("1.0.0", "==1.0.0", True), ("1.0.1", "==1.0.0", False),
    ("1.0.1", "!=1.0.0", True), ("1.0.0", "!=1.0.0", False),
    # a bare version means ==
    ("1.2.3", "1.2.3", True), ("1.2.4", "1.2.3", False),
    # 1.0 is 1.0.0; comparison is numeric, not lexical
    ("1.0.0", ">=1.0", True), ("1.10.0", ">1.9.0", True),
])
def test_constraints_are_enforced(installed, constraint, expected):
    assert sat(installed, constraint) is expected


@pytest.mark.parametrize("installed,constraint", [
    ("1.0.0", ""),                 # nothing to check is not "satisfied"
    ("1.0.0", ">="),               # operator without a version
    ("1.0.0", ">=1.0.0 junk"),     # text that is not a term
    ("1.0.0", "^1.0.0"),           # operator this parser does not implement
    ("1.0.0", ">=1.0.0-beta"),     # pre-release tags are not supported
    ("not-a-version", ">=1.0.0"),  # unparseable installed version
])
def test_anything_unreadable_is_not_satisfied(installed, constraint):
    assert sat(installed, constraint) is False
