"""Tests for ADR-0314: Skill Versioning."""

import pytest

from core.skills.versioning import SkillVersion


class TestSkillVersion:
    def test_create(self):
        v = SkillVersion("1.2.3")
        assert str(v) == "1.2.3"

    def test_is_newer_than(self):
        v1 = SkillVersion("2.0.0")
        v2 = SkillVersion("1.0.0")

        assert v1.is_newer_than(v2)
        assert not v2.is_newer_than(v1)

    def test_is_compatible(self):
        v1 = SkillVersion("1.2.0")
        v2 = SkillVersion("1.5.0")
        v3 = SkillVersion("2.0.0")

        assert v1.is_compatible_with(v2)  # Same major
        assert not v1.is_compatible_with(v3)  # Different major

    def test_compare_with_string(self):
        v = SkillVersion("2.0.0")

        assert v.is_newer_than("1.0.0")
        assert v.is_compatible_with("2.5.0")
