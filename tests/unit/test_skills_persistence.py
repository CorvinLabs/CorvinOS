"""Tests for ADR-0313: Skill Persistence."""

import tempfile
from pathlib import Path

import pytest

from core.skills.persistence import SkillPersistence


class TestSkillPersistence:
    def test_save_and_load(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            persistence = SkillPersistence(tmpdir)
            skill_data = {"name": "test", "value": 100}

            persistence.save_skill("tenant1", "skill1", "1.0", skill_data)
            loaded = persistence.load_skill("tenant1", "skill1", "1.0")

            assert loaded == skill_data

    def test_file_created(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            persistence = SkillPersistence(tmpdir)

            persistence.save_skill("tenant1", "skill1", "1.0", {"data": "test"})

            skill_file = Path(tmpdir) / "tenant1" / "skill1-1.0.json"
            assert skill_file.exists()

    def test_load_nonexistent(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            persistence = SkillPersistence(tmpdir)

            loaded = persistence.load_skill("tenant1", "nonexistent", "1.0")
            assert loaded is None

    def test_delete(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            persistence = SkillPersistence(tmpdir)

            persistence.save_skill("tenant1", "skill1", "1.0", {"data": "test"})
            assert persistence.load_skill("tenant1", "skill1", "1.0") is not None

            persistence.delete_skill("tenant1", "skill1", "1.0")
            assert persistence.load_skill("tenant1", "skill1", "1.0") is None
