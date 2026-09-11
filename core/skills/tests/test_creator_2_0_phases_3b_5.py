"""Tests for Creator 2.0 phases 3b–5 (Checkpoint, Structure, Content)."""

import pytest
from core.skills.os_skills.creator_2_0.phases.phase_3b import CheckpointPhase, CheckpointRequest
from core.skills.os_skills.creator_2_0.phases.phase_4 import StructurePhase, StructureRequest
from core.skills.os_skills.creator_2_0.phases.phase_5 import ContentPhase, ContentRequest
from core.skills.os_skills.creator_2_0.events import LossEmitter


class TestCheckpointPhase:
    """Test Phase 3b: Checkpoint."""

    def test_checkpoint_validates_plan(self):
        """Test that checkpoint validates the plan."""
        emitter = LossEmitter()
        phase = CheckpointPhase()

        result = phase.execute(
            CheckpointRequest(
                skill_id="test",
                architecture={},
                functions=[
                    {"name": "validate", "returns": "bool"},
                    {"name": "detail", "returns": "dict"},
                ],
                dependencies=["re"],
            ),
            emitter,
        )

        assert result.is_sensible
        assert len(result.issues) == 0

    def test_checkpoint_detects_too_many_functions(self):
        """Test that checkpoint detects too many functions."""
        emitter = LossEmitter()
        phase = CheckpointPhase()

        functions = [{"name": f"func_{i}", "returns": "Any"} for i in range(25)]

        result = phase.execute(
            CheckpointRequest(
                skill_id="test",
                architecture={},
                functions=functions,
                dependencies=["re"],
            ),
            emitter,
        )

        assert not result.is_sensible
        assert len(result.issues) > 0

    def test_checkpoint_detects_too_many_dependencies(self):
        """Test that checkpoint detects too many dependencies."""
        emitter = LossEmitter()
        phase = CheckpointPhase()

        dependencies = [f"lib_{i}" for i in range(15)]

        result = phase.execute(
            CheckpointRequest(
                skill_id="test",
                architecture={},
                functions=[{"name": "func", "returns": "Any"}],
                dependencies=dependencies,
            ),
            emitter,
        )

        assert not result.is_sensible

    def test_checkpoint_emits_event(self):
        """Test that checkpoint emits event."""
        emitter = LossEmitter()
        phase = CheckpointPhase()

        phase.execute(
            CheckpointRequest(
                skill_id="test",
                architecture={},
                functions=[{"name": "test", "returns": "Any"}],
                dependencies=[],
            ),
            emitter,
        )

        events = emitter.get_events()
        assert len(events) == 1
        assert events[0].phase_num == 3  # Checkpoint uses phase 3 numbering


class TestStructurePhase:
    """Test Phase 4: Structure."""

    def test_structure_designs_file_layout(self):
        """Test that structure designs file layout."""
        emitter = LossEmitter()
        phase = StructurePhase()

        result = phase.execute(
            StructureRequest(
                skill_id="test",
                intent="validation",
                functions=[
                    {"name": "validate", "returns": "bool"},
                    {"name": "detail", "returns": "dict"},
                ],
                dependencies=["re"],
            ),
            emitter,
        )

        assert len(result.file_layout) > 0
        assert "__init__.py" in result.file_layout
        assert "skill.py" in result.file_layout

    def test_structure_organizes_functions(self):
        """Test that structure organizes functions into modules."""
        emitter = LossEmitter()
        phase = StructurePhase()

        result = phase.execute(
            StructureRequest(
                skill_id="test",
                intent="validation",
                functions=[
                    {"name": "validate", "returns": "bool"},
                    {"name": "get_rules", "returns": "dict"},
                ],
                dependencies=["re"],
            ),
            emitter,
        )

        assert len(result.module_structure) > 0

    def test_structure_single_function_minimal_layout(self):
        """Test that single function gets minimal layout."""
        emitter = LossEmitter()
        phase = StructurePhase()

        result = phase.execute(
            StructureRequest(
                skill_id="test",
                intent="validation",
                functions=[{"name": "validate", "returns": "bool"}],
                dependencies=["re"],
            ),
            emitter,
        )

        # Minimal layout should have only 2 files
        assert len(result.file_layout) <= 2

    def test_structure_multiple_functions_fuller_layout(self):
        """Test that many functions get fuller layout."""
        emitter = LossEmitter()
        phase = StructurePhase()

        functions = [{"name": f"func_{i}", "returns": "Any"} for i in range(10)]

        result = phase.execute(
            StructureRequest(
                skill_id="test",
                intent="validation",
                functions=functions,
                dependencies=["re"],
            ),
            emitter,
        )

        # Fuller layout should have more files
        assert len(result.file_layout) > 2

    def test_structure_emits_event(self):
        """Test that structure emits event."""
        emitter = LossEmitter()
        phase = StructurePhase()

        phase.execute(
            StructureRequest(
                skill_id="test",
                intent="validation",
                functions=[{"name": "test", "returns": "Any"}],
                dependencies=[],
            ),
            emitter,
        )

        events = emitter.get_events()
        assert len(events) == 1
        assert events[0].phase_num == 4


class TestContentPhase:
    """Test Phase 5: Content."""

    def test_content_generates_code(self):
        """Test that content phase generates code."""
        emitter = LossEmitter()
        phase = ContentPhase()

        result = phase.execute(
            ContentRequest(
                skill_id="test",
                intent="validation",
                functions=[
                    {"name": "validate", "params": ["input"], "returns": "bool"},
                    {"name": "detail", "params": ["input"], "returns": "dict"},
                ],
                module_structure={"core": ["validate", "detail"]},
            ),
            emitter,
        )

        assert result.code_generated is not None
        assert "def validate" in result.code_generated
        assert "def detail" in result.code_generated

    def test_content_counts_functions(self):
        """Test that content counts functions correctly."""
        emitter = LossEmitter()
        phase = ContentPhase()

        functions = [{"name": f"func_{i}", "params": [], "returns": "Any"} for i in range(5)]

        result = phase.execute(
            ContentRequest(
                skill_id="test",
                intent="validation",
                functions=functions,
                module_structure={},
            ),
            emitter,
        )

        assert result.function_count == 5

    def test_content_calculates_lines_of_code(self):
        """Test that content calculates LOC."""
        emitter = LossEmitter()
        phase = ContentPhase()

        result = phase.execute(
            ContentRequest(
                skill_id="test",
                intent="validation",
                functions=[{"name": "test", "params": [], "returns": "Any"}],
                module_structure={},
            ),
            emitter,
        )

        assert result.lines_of_code > 0

    def test_content_includes_imports(self):
        """Test that generated code includes imports."""
        emitter = LossEmitter()
        phase = ContentPhase()

        result = phase.execute(
            ContentRequest(
                skill_id="test",
                intent="validation",
                functions=[{"name": "test", "params": [], "returns": "Any"}],
                module_structure={},
            ),
            emitter,
        )

        assert "from typing import" in result.code_generated

    def test_content_emits_event(self):
        """Test that content emits event."""
        emitter = LossEmitter()
        phase = ContentPhase()

        phase.execute(
            ContentRequest(
                skill_id="test",
                intent="validation",
                functions=[{"name": "test", "params": [], "returns": "Any"}],
                module_structure={},
            ),
            emitter,
        )

        events = emitter.get_events()
        assert len(events) == 1
        assert events[0].phase_num == 5
