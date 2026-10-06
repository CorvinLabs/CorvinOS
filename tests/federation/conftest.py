"""Fixtures for Federation (CONCEPT-0097 Phase 1) E2E tests.

Mirrors ``tests/forge_bundle/conftest.py``'s ``tmp_corvin_home`` pattern —
tests never touch the live install's ``~/.corvin`` or its audit chain.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Generator

import pytest

TENANT_ID = "_default"


@pytest.fixture
def tmp_corvin_home(tmp_path: Path) -> Generator[Path, None, None]:
    home = tmp_path / "corvin_home"
    (home / "tenants" / TENANT_ID / "global" / "federation").mkdir(parents=True, exist_ok=True)

    orig_env = {k: os.environ.get(k) for k in (
        "CORVIN_HOME", "CORVIN_TENANT_ID", "VOICE_AUDIT_PATH", "CORVIN_FORCE_SCOPE",
    )}
    os.environ["CORVIN_HOME"] = str(home)
    os.environ["CORVIN_TENANT_ID"] = TENANT_ID
    os.environ["VOICE_AUDIT_PATH"] = str(home / "audit.jsonl")
    os.environ["CORVIN_FORCE_SCOPE"] = "user"
    try:
        yield home
    finally:
        for k, v in orig_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
