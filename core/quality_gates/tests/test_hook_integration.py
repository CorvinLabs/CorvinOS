"""Tests for git hook integration (ADR-0688)."""

import pytest
import tempfile
import os
from pathlib import Path
from datetime import datetime

from core.quality_gates.hooks.pre_commit import (
    get_file_content,
    extract_frontmatter,
    validate_adr_frontmatter,
    validate_adr_file,
)


class TestPreCommitHook:
    """Test git pre-commit hook."""

    def test_extract_frontmatter_valid(self):
        """Test extracting valid frontmatter."""
        content = """---
id: ADR-0688
status: proposed
depends_on: [ADR-0687]
paths: [core/quality_gates/]
docs: [docs/quality-gates/]
commits: []
---

# ADR-0688 — Quality Gates

Some content here.
"""

        frontmatter = extract_frontmatter(content)

        assert frontmatter is not None
        assert frontmatter["id"] == "ADR-0688"
        assert frontmatter["status"] == "proposed"

    def test_extract_frontmatter_missing_separator(self):
        """Test that missing frontmatter separator returns None."""
        content = """id: ADR-0688
status: proposed
---

# ADR-0688
"""

        frontmatter = extract_frontmatter(content)

        assert frontmatter is None

    def test_validate_adr_frontmatter_valid(self):
        """Test validating valid frontmatter."""
        frontmatter = {
            "id": "ADR-0688",
            "status": "proposed",
            "depends_on": ["ADR-0687"],
            "paths": ["core/quality_gates/"],
            "docs": ["docs/quality-gates/"],
            "commits": ["abc123"],
        }

        violations = validate_adr_frontmatter("test.md", frontmatter)

        assert len(violations) == 0

    def test_validate_adr_frontmatter_missing_field(self):
        """Test validating with missing field."""
        frontmatter = {
            "id": "ADR-0688",
            "status": "proposed",
            "depends_on": [],
            "paths": ["core/quality_gates/"],
            "docs": ["docs/quality-gates/"],
            # Missing: commits
        }

        violations = validate_adr_frontmatter("test.md", frontmatter)

        assert len(violations) > 0
        assert any("commits" in v for v in violations)

    def test_validate_adr_frontmatter_empty_paths(self):
        """Test validating with empty paths list."""
        frontmatter = {
            "id": "ADR-0688",
            "status": "proposed",
            "depends_on": [],
            "paths": [],
            "docs": ["docs/quality-gates/"],
            "commits": ["abc123"],
        }

        violations = validate_adr_frontmatter("test.md", frontmatter)

        assert len(violations) > 0
        assert any("paths" in v for v in violations)

    def test_validate_adr_frontmatter_invalid_status(self):
        """Test validating with invalid status."""
        frontmatter = {
            "id": "ADR-0688",
            "status": "invalid_status",
            "depends_on": [],
            "paths": ["core/quality_gates/"],
            "docs": ["docs/quality-gates/"],
            "commits": ["abc123"],
        }

        violations = validate_adr_frontmatter("test.md", frontmatter)

        assert len(violations) > 0
        assert any("status" in v for v in violations)

    def test_validate_adr_frontmatter_invalid_id_format(self):
        """Test validating with invalid id format."""
        frontmatter = {
            "id": "INVALID-0688",
            "status": "proposed",
            "depends_on": [],
            "paths": ["core/quality_gates/"],
            "docs": ["docs/quality-gates/"],
            "commits": ["abc123"],
        }

        violations = validate_adr_frontmatter("test.md", frontmatter)

        assert len(violations) > 0
        assert any("id" in v for v in violations)

    def test_validate_adr_file_valid(self):
        """Test validating a complete ADR file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            filepath = os.path.join(tmpdir, "ADR-0688.md")
            content = """---
id: ADR-0688
status: proposed
depends_on: [ADR-0687]
paths: [core/quality_gates/]
docs: [docs/quality-gates/]
commits: [abc123]
---

# ADR-0688 — Quality Gates

Some content here.
"""

            with open(filepath, "w") as f:
                f.write(content)

            violations = validate_adr_file(filepath)

            assert len(violations) == 0

    def test_validate_adr_file_missing_frontmatter(self):
        """Test validating file with missing frontmatter."""
        with tempfile.TemporaryDirectory() as tmpdir:
            filepath = os.path.join(tmpdir, "ADR-0688.md")
            content = """# ADR-0688 — Quality Gates

Some content without frontmatter.
"""

            with open(filepath, "w") as f:
                f.write(content)

            violations = validate_adr_file(filepath)

            assert len(violations) > 0
            assert any("frontmatter" in v.lower() for v in violations)

    def test_validate_adr_file_missing_field(self):
        """Test validating file with missing field."""
        with tempfile.TemporaryDirectory() as tmpdir:
            filepath = os.path.join(tmpdir, "ADR-0688.md")
            content = """---
id: ADR-0688
status: proposed
depends_on: []
paths: [core/quality_gates/]
docs: [docs/quality-gates/]
---

# ADR-0688 — Quality Gates
"""

            with open(filepath, "w") as f:
                f.write(content)

            violations = validate_adr_file(filepath)

            assert len(violations) > 0
            assert any("commits" in v for v in violations)

    def test_get_file_content(self):
        """Test reading file content."""
        with tempfile.TemporaryDirectory() as tmpdir:
            filepath = os.path.join(tmpdir, "test.txt")
            test_content = "Test content here"

            with open(filepath, "w") as f:
                f.write(test_content)

            content = get_file_content(filepath)

            assert content == test_content

    def test_get_file_content_not_found(self):
        """Test reading non-existent file."""
        content = get_file_content("/non/existent/file.txt")

        assert content is None

    def test_multiple_adr_validation(self):
        """Test validating multiple ADR files."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create valid ADR
            valid_path = os.path.join(tmpdir, "ADR-0688.md")
            valid_content = """---
id: ADR-0688
status: proposed
depends_on: []
paths: [core/quality_gates/]
docs: [docs/quality-gates/]
commits: [abc123]
---

# ADR-0688
"""

            with open(valid_path, "w") as f:
                f.write(valid_content)

            # Create invalid ADR
            invalid_path = os.path.join(tmpdir, "ADR-0689.md")
            invalid_content = """---
id: ADR-0689
status: proposed
depends_on: []
paths: []
docs: [docs/quality-gates/]
commits: [abc123]
---

# ADR-0689
"""

            with open(invalid_path, "w") as f:
                f.write(invalid_content)

            # Validate both
            valid_violations = validate_adr_file(valid_path)
            invalid_violations = validate_adr_file(invalid_path)

            assert len(valid_violations) == 0
            assert len(invalid_violations) > 0
