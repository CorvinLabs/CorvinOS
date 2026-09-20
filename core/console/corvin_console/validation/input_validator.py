"""Input validation for path traversal prevention (OWASP A01:2021).

Validates skill_id, version, and filename parameters to prevent ../../ escapes
and other path traversal attacks. Fail-closed: invalid input → False, never bypass.

All validators:
  - Reject .. / ~ * ? and other path-like characters
  - Enforce alphanumeric + allowed punctuation (-, _, .)
  - Enforce length bounds (1-128 chars)
  - Return bool (never raise; caller decides HTTP status)
"""
from __future__ import annotations

import re
from typing import Optional


# Compiled patterns for efficiency
_SKILL_ID_PATTERN = re.compile(r"^[a-zA-Z0-9._-]+$")
_VERSION_PATTERN = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")  # Semantic versioning X.Y.Z
_FILENAME_PATTERN = re.compile(r"^[a-zA-Z0-9._-]+$")

# Length bounds
_SKILL_ID_MAX_LEN = 128
_VERSION_MAX_LEN = 128
_FILENAME_MAX_LEN = 255


def validate_skill_id(skill_id: Optional[str]) -> bool:
    r"""Validate skill_id parameter (e.g., 'os.delegation_router', 'my_skill').

    Valid:
      - 'os.delegation_router'
      - 'my_skill'
      - 'skill-name'
      - 'SKILL_123'

    Invalid:
      - '../../etc/passwd' (contains /)
      - '..\windows\system' (contains \)
      - 'skill/../dangerous' (contains /)
      - '~/.corvin' (contains ~)
      - '/etc/passwd' (starts with /)
      - '' (empty)
      - None (None)
      - 'x' * 129 (too long)

    Returns:
      - True if valid
      - False if invalid (no exception raised)
    """
    if not skill_id or not isinstance(skill_id, str):
        return False

    if len(skill_id) < 1 or len(skill_id) > _SKILL_ID_MAX_LEN:
        return False

    # Reject: /, \, .., ~, *, ?
    if "/" in skill_id or "\\" in skill_id or ".." in skill_id or "~" in skill_id:
        return False

    # Only allow: a-z A-Z 0-9 . - _
    if not _SKILL_ID_PATTERN.match(skill_id):
        return False

    return True


def validate_version(version: Optional[str]) -> bool:
    r"""Validate version parameter as semantic versioning (X.Y.Z).

    Valid:
      - '1.2.3'
      - '0.0.1'
      - '99.99.99'

    Invalid:
      - '1.2' (missing patch)
      - '1.2.3.4' (too many parts)
      - '1.2.a' (non-numeric)
      - '../../1.2.3' (contains /)
      - '..\1.2.3' (contains \)
      - 'v1.2.3' (contains 'v' prefix)
      - '' (empty)
      - None (None)
      - 'x' * 129 (too long)

    Returns:
      - True if valid semver X.Y.Z format
      - False if invalid (no exception raised)
    """
    if not version or not isinstance(version, str):
        return False

    if len(version) < 1 or len(version) > _VERSION_MAX_LEN:
        return False

    # Reject: /, \, .., ~, *, ?
    if "/" in version or "\\" in version or ".." in version or "~" in version:
        return False

    # Match X.Y.Z exactly (semantic versioning)
    if not _VERSION_PATTERN.match(version):
        return False

    return True


def validate_filename(filename: Optional[str]) -> bool:
    r"""Validate filename parameter for safe file operations.

    Valid:
      - 'manifest.json'
      - 'skill_v1.2.3.zip'
      - 'README.md'
      - 'config-backup.tar.gz'

    Invalid:
      - '../../etc/passwd' (contains /)
      - 'manifest.json/../../escape' (contains /)
      - '..\windows\file' (contains \)
      - 'file~backup' (contains ~)
      - 'file*' (contains *)
      - 'file?' (contains ?)
      - '' (empty)
      - None (None)
      - 'x' * 256 (too long)

    Returns:
      - True if valid
      - False if invalid (no exception raised)
    """
    if not filename or not isinstance(filename, str):
        return False

    if len(filename) < 1 or len(filename) > _FILENAME_MAX_LEN:
        return False

    # Reject: /, \, .., ~, *, ?
    if "/" in filename or "\\" in filename or ".." in filename or "~" in filename:
        return False

    # Only allow: a-z A-Z 0-9 . - _
    if not _FILENAME_PATTERN.match(filename):
        return False

    return True


def validate_path_component(component: Optional[str], max_len: int = 128) -> bool:
    """Generic path component validator.

    Validates that a component is safe for use in a path (no traversal escapes).

    Returns:
      - True if valid
      - False if invalid (no exception raised)
    """
    if not component or not isinstance(component, str):
        return False

    if len(component) < 1 or len(component) > max_len:
        return False

    # Reject: /, \, .., ~, *, ?
    if "/" in component or "\\" in component or ".." in component or "~" in component:
        return False

    return True
