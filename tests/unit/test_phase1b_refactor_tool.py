"""Unit tests for Phase 1b refactoring tool.

Tests ensure the regex-based transformations work correctly before
applying to Wave 1 production files.
"""

import pytest
import sys
from pathlib import Path

# Add scripts/ to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "scripts"))

from phase1b_refactor_tool import (
    refactor_is_enabled,
    refactor_set_enabled,
    refactor_worker_engine_mode,
    add_skill_import,
)


class TestRefactorIsEnabled:
    """Test is_enabled() → skill.execute() transformation."""

    def test_simple_is_enabled(self):
        """Basic is_enabled call without tenant_id."""
        content = 'if _ff.is_enabled("my_flag"):'
        result = refactor_is_enabled(content)
        assert 'feature_flags_skill.execute(' in result
        assert '"operation": "is_enabled"' in result
        assert '"flag_id": "my_flag"' in result
        assert '"tenant_id": "_default"' in result

    def test_is_enabled_with_tenant(self):
        """is_enabled call with explicit tenant_id."""
        content = '_ff.is_enabled("flag", tid)'
        result = refactor_is_enabled(content)
        assert 'feature_flags_skill.execute(' in result
        assert '"flag_id": "flag"' in result
        assert '"tenant_id": tid' in result

    def test_module_style_is_enabled(self):
        """is_enabled call using _feature_flags_module style."""
        content = '_feature_flags_module.is_enabled("flag")'
        result = refactor_is_enabled(content)
        assert 'feature_flags_skill.execute(' in result
        assert '"operation": "is_enabled"' in result


class TestRefactorSetEnabled:
    """Test set_enabled() → skill.execute() transformation."""

    def test_simple_set_enabled(self):
        """Basic set_enabled call."""
        content = '_ff.set_enabled("flag", True)'
        result = refactor_set_enabled(content)
        assert 'feature_flags_skill.execute(' in result
        assert '"operation": "set_enabled"' in result
        assert '"flag_id": "flag"' in result
        assert '"enabled": True' in result

    def test_set_enabled_with_tenant(self):
        """set_enabled with explicit tenant_id."""
        content = '_ff.set_enabled("flag", False, tenant_id="test")'
        result = refactor_set_enabled(content)
        assert 'feature_flags_skill.execute(' in result
        assert '"enabled": False' in result


class TestRefactorWorkerEngineMode:
    """Test worker_engine_mode() → skill.execute() transformation."""

    def test_worker_engine_mode_no_args(self):
        """worker_engine_mode with no args (default tenant)."""
        content = 'mode = _ff.worker_engine_mode()'
        result = refactor_worker_engine_mode(content)
        assert 'feature_flags_skill.execute(' in result
        assert '"operation": "worker_engine_mode"' in result
        assert '"tenant_id": "_default"' in result

    def test_worker_engine_mode_with_tenant(self):
        """worker_engine_mode with explicit tenant_id."""
        content = 'mode = _ff.worker_engine_mode(tid)'
        result = refactor_worker_engine_mode(content)
        assert 'feature_flags_skill.execute(' in result
        assert '"tenant_id": tid' in result


class TestAddSkillImport:
    """Test import insertion logic."""

    def test_add_import_after_docstring(self):
        """Import inserted after module docstring."""
        content = '"""Module docstring."""\n\nsome_code()'
        result = add_skill_import(content)
        assert 'from core.skills.feature_flags_skill' in result
        assert result.startswith('"""Module docstring."""')

    def test_add_import_after_shebang(self):
        """Import inserted after shebang, before docstring."""
        content = '#!/usr/bin/env python3\n"""Docstring."""\n'
        result = add_skill_import(content)
        assert 'from core.skills.feature_flags_skill' in result
        assert result.startswith('#!/usr/bin/env python3')

    def test_import_not_duplicated(self):
        """Import not added if already present."""
        content = 'from core.skills.feature_flags_skill import feature_flags_skill\n'
        result = add_skill_import(content)
        count = result.count('from core.skills.feature_flags_skill')
        assert count == 1, "Import should not be duplicated"


class TestIntegration:
    """End-to-end refactoring scenarios."""

    def test_multiple_patterns_in_file(self):
        """Refactor multiple patterns in same file."""
        content = '''
_ff.is_enabled("flag1")
_ff.set_enabled("flag2", True)
_ff.worker_engine_mode()
'''
        result = refactor_is_enabled(content)
        result = refactor_set_enabled(result)
        result = refactor_worker_engine_mode(result)

        # All three should be transformed
        assert result.count('feature_flags_skill.execute') == 3


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
