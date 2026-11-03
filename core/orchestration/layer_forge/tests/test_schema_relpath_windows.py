"""Manifest paths: drive-relative ``D:x`` and ADS ``a.py:s`` must be rejected."""
import pytest

from core.orchestration.layer_forge.schema import LayerSchemaValidationError, _check_repo_relpath


@pytest.mark.parametrize("bad", ["D:x", "C:evil.py", "tests/a.py:stream", "/abs", "a/../b"])
def test_rejects(bad):
    with pytest.raises(LayerSchemaValidationError):
        _check_repo_relpath(bad, field_name="f")


def test_accepts_plain_relative():
    _check_repo_relpath("tests/test_a.py", field_name="f", required_prefix="tests")
