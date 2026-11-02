"""Windows has no ``os.O_NOFOLLOW``: a bare reference is an ``AttributeError`` at call time.

Where the call sat inside a broad ``except`` (forge run events) the loss was SILENT, so a
fresh Windows install simply had no run log and no skill-feedback history. The flag must
always be read through ``getattr(os, "O_NOFOLLOW", 0)``.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture
def no_nofollow(monkeypatch, tmp_path):
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path / "home"))
    monkeypatch.delattr(os, "O_NOFOLLOW", raising=False)  # what Windows looks like
    assert not hasattr(os, "O_NOFOLLOW")


def test_skill_feedback_history_append_without_o_nofollow(no_nofollow):
    from core.skills.feedback import history_store

    history_store.append(
        "_default", feedback_id="f1", timestamp="2026-11-02T00:00:00Z",
        skill_id="s", subject_id="x", rating=1, category="c",
    )
    p = history_store._path("_default")
    rows = [json.loads(line) for line in p.read_bytes().splitlines()]
    assert rows and rows[0]["feedback_id"] == "f1"
    assert b"\r\n" not in p.read_bytes(), "JSONL must stay byte-exact (O_BINARY on Windows)"


def test_prompt_skill_version_record_without_o_nofollow(no_nofollow, tmp_path):
    from core.skills.prompt_skill_versions import PromptVersionCatalog

    cat = PromptVersionCatalog(tmp_path / "versions")
    version = cat.record("demo", "body text")
    assert cat.record("demo", "body text") == version  # idempotent
    assert cat.versions("demo")


def test_forge_run_event_append_without_o_nofollow(no_nofollow):
    from corvin_console import forge_runs

    src = Path(forge_runs.__file__).read_text(encoding="utf-8")
    # The event/blob writers sit inside a broad ``except``; a bare attribute read
    # there is swallowed. Pin the guarded spelling at every open() of the module.
    assert not re.search(r"(?<!getattr\(os, \")os\.O_NOFOLLOW", src)


def test_no_bare_o_nofollow_anywhere_in_production_code():
    bare = re.compile(r"(?<![\"'])\bos\.O_NOFOLLOW\b")
    offenders = []
    for root in ("core", "corvin_operator"):
        for path in (REPO / root).rglob("*.py"):
            parts = set(path.parts)
            if parts & {"tests", "node_modules", ".venv", "__pycache__"} or path.name.startswith("test_"):
                continue
            for n, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                code = line.split("#", 1)[0]
                if bare.search(code) and "getattr" not in code and "hasattr" not in code:
                    offenders.append(f"{path.relative_to(REPO)}:{n}")
    assert not offenders, "bare os.O_NOFOLLOW breaks Windows: " + ", ".join(offenders)
