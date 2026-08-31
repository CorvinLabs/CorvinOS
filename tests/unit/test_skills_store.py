"""Unit tests for Skill Store (ADR-0306)."""

import json
import tempfile
from pathlib import Path

import pytest

from core.skills import FileSkillStore, Grade, InMemorySkillStore, Skill


class TestInMemorySkillStore:
    """In-memory store tests."""

    def test_save_and_load(self):
        store = InMemorySkillStore()
        skill = Skill(name="test", version="1.0", body="code")
        skill.add_grade(Grade(value=0.8))

        store.save(skill)
        loaded = store.load("test", "1.0")

        assert loaded is not None
        assert loaded.name == "test"
        assert loaded.mean_score == 0.8

    def test_load_nonexistent(self):
        store = InMemorySkillStore()
        loaded = store.load("nonexistent", "1.0")
        assert loaded is None

    def test_save_overwrites(self):
        store = InMemorySkillStore()
        s1 = Skill(name="test", version="1.0", body="old")
        s1.add_grade(Grade(value=0.5))
        store.save(s1)

        s2 = Skill(name="test", version="1.0", body="new")
        s2.add_grade(Grade(value=0.9))
        store.save(s2)

        loaded = store.load("test", "1.0")
        assert loaded.body == "new"
        assert loaded.mean_score == 0.9

    def test_list_all(self):
        store = InMemorySkillStore()
        s1 = Skill(name="skill1", version="1.0", body="code1")
        s2 = Skill(name="skill2", version="1.0", body="code2")
        store.save(s1)
        store.save(s2)

        all_skills = store.list_all()
        assert len(all_skills) == 2
        names = {s.name for s in all_skills}
        assert names == {"skill1", "skill2"}

    def test_list_by_mean_score(self):
        store = InMemorySkillStore()
        s1 = Skill(name="low", version="1.0", body="code1")
        s1.add_grade(Grade(value=0.2))

        s2 = Skill(name="mid", version="1.0", body="code2")
        s2.add_grade(Grade(value=0.5))

        s3 = Skill(name="high", version="1.0", body="code3")
        s3.add_grade(Grade(value=0.9))

        store.save(s1)
        store.save(s2)
        store.save(s3)

        ranked = store.list_by_mean_score()
        assert len(ranked) == 3
        assert ranked[0].name == "high"
        assert ranked[1].name == "mid"
        assert ranked[2].name == "low"

    def test_list_by_mean_score_limit(self):
        store = InMemorySkillStore()
        for i in range(5):
            s = Skill(name=f"skill{i}", version="1.0", body="code")
            s.add_grade(Grade(value=0.1 * (i + 1)))
            store.save(s)

        top3 = store.list_by_mean_score(limit=3)
        assert len(top3) == 3

    def test_delete(self):
        store = InMemorySkillStore()
        s = Skill(name="test", version="1.0", body="code")
        store.save(s)

        assert store.exists("test", "1.0")
        deleted = store.delete("test", "1.0")
        assert deleted is True
        assert not store.exists("test", "1.0")

    def test_delete_nonexistent(self):
        store = InMemorySkillStore()
        deleted = store.delete("nonexistent", "1.0")
        assert deleted is False

    def test_exists(self):
        store = InMemorySkillStore()
        s = Skill(name="test", version="1.0", body="code")
        store.save(s)

        assert store.exists("test", "1.0")
        assert not store.exists("test", "2.0")
        assert not store.exists("other", "1.0")

    def test_clear(self):
        store = InMemorySkillStore()
        s1 = Skill(name="test1", version="1.0", body="code")
        s2 = Skill(name="test2", version="1.0", body="code")
        store.save(s1)
        store.save(s2)

        assert len(store.list_all()) == 2
        store.clear()
        assert len(store.list_all()) == 0


class TestFileSkillStore:
    """File-based store tests."""

    @pytest.fixture
    def temp_store(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            yield FileSkillStore(Path(tmpdir))

    def test_save_and_load(self, temp_store):
        skill = Skill(name="test", version="1.0", body="code")
        skill.add_grade(Grade(value=0.8, feedback="good"))

        temp_store.save(skill)
        loaded = temp_store.load("test", "1.0")

        assert loaded is not None
        assert loaded.name == "test"
        assert loaded.mean_score == 0.8
        assert len(loaded.grades) == 1

    def test_file_layout(self, temp_store):
        skill = Skill(name="myskill", version="2.0", body="code")
        temp_store.save(skill)

        expected_file = temp_store.root / "myskill" / "2.0.json"
        assert expected_file.exists()

    def test_file_content_json(self, temp_store):
        skill = Skill(name="test", version="1.0", body="code")
        skill.add_grade(Grade(value=0.7))
        temp_store.save(skill)

        file_path = temp_store.root / "test" / "1.0.json"
        content = json.loads(file_path.read_text())

        assert content["name"] == "test"
        assert content["version"] == "1.0"
        assert content["mean_score"] == 0.7

    def test_load_nonexistent(self, temp_store):
        loaded = temp_store.load("nonexistent", "1.0")
        assert loaded is None

    def test_list_all(self, temp_store):
        s1 = Skill(name="skill1", version="1.0", body="code1")
        s2 = Skill(name="skill2", version="1.0", body="code2")
        temp_store.save(s1)
        temp_store.save(s2)

        all_skills = temp_store.list_all()
        assert len(all_skills) == 2

    def test_list_by_mean_score(self, temp_store):
        for i, score in enumerate([0.9, 0.5, 0.2]):
            s = Skill(name=f"skill{i}", version="1.0", body="code")
            s.add_grade(Grade(value=score))
            temp_store.save(s)

        ranked = temp_store.list_by_mean_score()
        assert ranked[0].mean_score == 0.9
        assert ranked[1].mean_score == 0.5
        assert ranked[2].mean_score == 0.2

    def test_delete(self, temp_store):
        s = Skill(name="test", version="1.0", body="code")
        temp_store.save(s)

        assert temp_store.exists("test", "1.0")
        deleted = temp_store.delete("test", "1.0")
        assert deleted is True
        assert not temp_store.exists("test", "1.0")

    def test_delete_nonexistent(self, temp_store):
        deleted = temp_store.delete("nonexistent", "1.0")
        assert deleted is False

    def test_exists(self, temp_store):
        s = Skill(name="test", version="1.0", body="code")
        temp_store.save(s)

        assert temp_store.exists("test", "1.0")
        assert not temp_store.exists("test", "2.0")

    def test_multiple_versions(self, temp_store):
        s1 = Skill(name="test", version="1.0", body="code1")
        s2 = Skill(name="test", version="2.0", body="code2")
        temp_store.save(s1)
        temp_store.save(s2)

        loaded1 = temp_store.load("test", "1.0")
        loaded2 = temp_store.load("test", "2.0")

        assert loaded1.body == "code1"
        assert loaded2.body == "code2"

    def test_corrupted_json_skipped(self, temp_store):
        # Save a valid skill
        s = Skill(name="valid", version="1.0", body="code")
        temp_store.save(s)

        # Manually write corrupted JSON
        corrupt_dir = temp_store.root / "corrupt"
        corrupt_dir.mkdir()
        (corrupt_dir / "1.0.json").write_text("not valid json {")

        # list_all should skip the corrupted file
        all_skills = temp_store.list_all()
        names = {sk.name for sk in all_skills}
        assert names == {"valid"}
