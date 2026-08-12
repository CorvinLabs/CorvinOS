"""Unit tests for Skill object (ADR-0306)."""

import pytest
from datetime import datetime

from core.skills import Skill, Grade


class TestGrade:
    """Grade value object tests."""

    def test_grade_valid(self):
        g = Grade(value=0.8, feedback="good")
        assert g.value == 0.8
        assert g.feedback == "good"
        assert isinstance(g.timestamp, datetime)

    def test_grade_invalid_value_below_zero(self):
        with pytest.raises(ValueError, match="0.0–1.0"):
            Grade(value=-0.1)

    def test_grade_invalid_value_above_one(self):
        with pytest.raises(ValueError, match="0.0–1.0"):
            Grade(value=1.5)

    def test_grade_boundary_zero(self):
        g = Grade(value=0.0)
        assert g.value == 0.0

    def test_grade_boundary_one(self):
        g = Grade(value=1.0)
        assert g.value == 1.0


class TestSkill:
    """Skill object tests."""

    def test_skill_create(self):
        s = Skill(
            name="code-review",
            version="1.0",
            body="# code review skill",
            tags=["code", "review"],
        )
        assert s.name == "code-review"
        assert s.version == "1.0"
        assert s.body == "# code review skill"
        assert s.tags == ["code", "review"]
        assert s.tier == "bundled"

    def test_skill_invalid_name_empty(self):
        with pytest.raises(ValueError, match="invalid"):
            Skill(name="", version="1.0", body="test")

    def test_skill_invalid_name_slash(self):
        with pytest.raises(ValueError, match="invalid"):
            Skill(name="foo/bar", version="1.0", body="test")

    def test_skill_invalid_version_empty(self):
        with pytest.raises(ValueError, match="version"):
            Skill(name="test", version="", body="code")

    def test_skill_invalid_body_empty(self):
        with pytest.raises(ValueError, match="body"):
            Skill(name="test", version="1.0", body="")

    def test_skill_mean_score_empty(self):
        s = Skill(name="test", version="1.0", body="code")
        assert s.mean_score == 0.0

    def test_skill_mean_score_one_grade(self):
        s = Skill(name="test", version="1.0", body="code")
        s.add_grade(Grade(value=0.8))
        assert s.mean_score == 0.8

    def test_skill_mean_score_multiple(self):
        s = Skill(name="test", version="1.0", body="code")
        s.add_grade(Grade(value=0.8))
        s.add_grade(Grade(value=0.6))
        s.add_grade(Grade(value=1.0))
        assert s.mean_score == pytest.approx((0.8 + 0.6 + 1.0) / 3)

    def test_skill_n_trials(self):
        s = Skill(name="test", version="1.0", body="code")
        assert s.n_trials == 0
        s.add_grade(Grade(value=0.5))
        assert s.n_trials == 1
        s.add_grade(Grade(value=0.7))
        assert s.n_trials == 2

    def test_skill_last_updated_empty(self):
        s = Skill(name="test", version="1.0", body="code")
        assert s.last_updated is None

    def test_skill_last_updated_populated(self):
        s = Skill(name="test", version="1.0", body="code")
        t1 = datetime(2026, 1, 1, 10, 0, 0)
        t2 = datetime(2026, 1, 1, 11, 0, 0)
        s.add_grade(Grade(value=0.5, timestamp=t1))
        s.add_grade(Grade(value=0.7, timestamp=t2))
        assert s.last_updated == t2

    def test_skill_to_dict(self):
        s = Skill(
            name="test",
            version="1.0",
            body="code",
            tags=["tag1"],
            tier="installed",
        )
        s.add_grade(Grade(value=0.9, feedback="great"))
        d = s.to_dict()

        assert d["name"] == "test"
        assert d["version"] == "1.0"
        assert d["body"] == "code"
        assert d["tags"] == ["tag1"]
        assert d["tier"] == "installed"
        assert d["mean_score"] == 0.9
        assert d["n_trials"] == 1
        assert len(d["grades"]) == 1
        assert d["grades"][0]["value"] == 0.9
        assert d["grades"][0]["feedback"] == "great"

    def test_skill_from_dict(self):
        original = Skill(name="test", version="1.0", body="code")
        original.add_grade(Grade(value=0.8, feedback="ok"))

        d = original.to_dict()
        restored = Skill.from_dict(d)

        assert restored.name == original.name
        assert restored.version == original.version
        assert restored.body == original.body
        assert restored.mean_score == original.mean_score
        assert len(restored.grades) == 1
        assert restored.grades[0].value == 0.8

    def test_skill_equality(self):
        s1 = Skill(name="test", version="1.0", body="code")
        s2 = Skill(name="test", version="1.0", body="code")
        s3 = Skill(name="test", version="1.0", body="different")

        assert s1 == s2  # Same name/version/body
        assert s1 != s3  # Different body
        assert s1 != "not a skill"

    def test_skill_hash(self):
        s1 = Skill(name="test", version="1.0", body="code")
        s2 = Skill(name="test", version="1.0", body="code")

        skill_set = {s1}
        assert s2 in skill_set  # Same hash

    def test_skill_roundtrip_json(self):
        import json

        original = Skill(
            name="complex",
            version="2.0",
            body="long code",
            tags=["a", "b", "c"],
            tier="community",
        )
        for val in [0.1, 0.5, 0.9]:
            original.add_grade(Grade(value=val, feedback=f"score {val}"))

        d = original.to_dict()
        json_str = json.dumps(d)
        restored_dict = json.loads(json_str)
        restored = Skill.from_dict(restored_dict)

        assert restored.name == original.name
        assert restored.version == original.version
        assert restored.mean_score == original.mean_score
        assert len(restored.grades) == len(original.grades)
