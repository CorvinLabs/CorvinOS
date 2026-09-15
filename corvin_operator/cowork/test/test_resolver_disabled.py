"""resolver.list_available() must EXCLUDE personas deactivated via the console
``.disabled.json`` registry — this is what makes "deactivate a persona" take
effect at runtime (auto-routing / discovery), not just hide it in the console UI.

An explicit pin via resolver.load(name) still resolves — disabling means
"don't offer it automatically", not "brick an active chat".
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent / "lib"))


@pytest.fixture()
def resolver_in_sandbox():
    sandbox = Path(tempfile.mkdtemp(prefix="cowork-disabled-"))
    user_personas = sandbox / "user" / "personas"
    user_personas.mkdir(parents=True)
    prev = {k: os.environ.get(k) for k in ("COWORK_USER_DIR", "COWORK_MCP_CACHE")}
    os.environ["COWORK_USER_DIR"] = str(sandbox / "user")
    os.environ["COWORK_MCP_CACHE"] = str(sandbox / "mcp")
    sys.modules.pop("resolver", None)
    import resolver  # type: ignore
    try:
        yield resolver, user_personas
    finally:
        sys.modules.pop("resolver", None)
        for k, v in prev.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _write_disabled(user_personas: Path, names: list[str]) -> None:
    (user_personas / ".disabled.json").write_text(
        json.dumps({"disabled": names}), encoding="utf-8")


def test_disabled_bundle_persona_excluded_from_list_available(resolver_in_sandbox):
    resolver, user_personas = resolver_in_sandbox
    before = {p["name"] for p in resolver.list_available()}
    assert "research" in before  # a known bundle persona

    _write_disabled(user_personas, ["research"])
    after = {p["name"] for p in resolver.list_available()}
    assert "research" not in after
    # everything else still present
    assert "assistant" in after
    # explicit pin still resolves (disable != brick)
    assert resolver.load("research") is not None


def test_disabled_user_persona_excluded(resolver_in_sandbox):
    resolver, user_personas = resolver_in_sandbox
    (user_personas / "mine.json").write_text(
        json.dumps({"name": "mine", "description": "x"}), encoding="utf-8")
    assert "mine" in {p["name"] for p in resolver.list_available()}

    _write_disabled(user_personas, ["mine"])
    assert "mine" not in {p["name"] for p in resolver.list_available()}


def test_corrupt_registry_fails_open(resolver_in_sandbox):
    resolver, user_personas = resolver_in_sandbox
    (user_personas / ".disabled.json").write_text("{ not json", encoding="utf-8")
    # A broken registry must never hide every persona (fail-open).
    names = {p["name"] for p in resolver.list_available()}
    assert "assistant" in names
