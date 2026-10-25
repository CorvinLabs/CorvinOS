"""Mod generator (CONCEPT-0118 phase 1, ADR-2285).

Two layers, deliberately separate:

* pure tests of the parser and ``generate_mod_files`` (determinism, refusal of bad input);
* an END-TO-END test through the real entry point: the CLI as a subprocess writes a mod from a
  SkillForge-shaped store, then the REAL ``claude plugin validate`` and ``claude plugin test`` run on
  the output. A generator whose output Claude Code refuses to load is not a generator, whatever its
  unit tests say. Skipped, with the reason named, only when the ``claude`` CLI is not installed.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from plugin_builder.generators import ModSpecError, generate_mod_files, parse_quiz_cards, write_mod
from plugin_builder.generators.mod import QuizCard, load_store

_CORE_PLUGINS = Path(__file__).resolve().parents[2]
_CLAUDE = shutil.which("claude")

BODY = """# Mod quiz

Intro text that is not part of the quiz.

## quiz
- mod-vs-skill | What distinguishes a mod from a skill? | process, interface, code | A mod is code in the Claude Code process.
- mod-sandbox | Is a mod sandboxed? | not, rights | No, it runs with the user's rights.

## other
- not-a-card | ignored | x | y
"""


def _store(tmp_path: Path, bodies: dict[str, str]) -> Path:
    root = tmp_path / "skill-forge"
    skills = []
    for name, body in bodies.items():
        d = root / "skills" / name
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text(body, encoding="utf-8")
        skills.append(
            {"name": name, "lifecycle": "active",
             "metadata": {"type": "domain", "description": f"{name} test skill", "scope": "user"}}
        )
    (root / "manifest.json").write_text(json.dumps({"schema": "ADR-0420", "skills": skills}), encoding="utf-8")
    return root


# ── parser ────────────────────────────────────────────────────────────────────

def test_parse_reads_only_the_quiz_section():
    cards = parse_quiz_cards("s", BODY)
    assert [c.id for c in cards] == ["mod-vs-skill", "mod-sandbox"]
    assert cards[0].keywords == ("process", "interface", "code")
    assert cards[1].hint == "No, it runs with the user's rights."


def test_a_skill_without_a_quiz_section_has_no_cards():
    assert parse_quiz_cards("s", "# Title\n\n- just | a | list | item\n") == []


@pytest.mark.parametrize(
    "line",
    [
        "- only | three | parts",
        "- BAD ID | q | kwd | hint",
        "- ok | q | ab | hint",  # keyword shorter than 3 characters matches almost any answer
        "- ok | q |  | hint",  # no keyword at all
        "- ok |  | kwd | hint",  # no question
    ],
)
def test_malformed_card_lines_are_refused_not_skipped(line):
    with pytest.raises(ModSpecError):
        parse_quiz_cards("s", "## quiz\n" + line + "\n")


def test_duplicate_card_ids_are_refused():
    with pytest.raises(ModSpecError):
        parse_quiz_cards("s", "## quiz\n- a | q1 | kwd | h\n- a | q2 | kwd | h\n")


# ── generation ────────────────────────────────────────────────────────────────

def _cards() -> list[QuizCard]:
    return parse_quiz_cards("assistant.demo", BODY)


def test_generation_is_deterministic():
    kw = dict(mod_name="demo-mod", version="0.1.0", description="d", cards=_cards(), info=True)
    assert generate_mod_files(**kw) == generate_mod_files(**kw)


def test_generated_mod_contains_the_gate_and_no_forbidden_hooks():
    js = generate_mod_files(mod_name="demo-mod", version="0.1.0", description="d", cards=_cards(), info=True)[
        "hooks/register.js"
    ]
    assert "requireAdapter" in js and "requireServers" in js
    assert "$.env.get('CORVIN_CHANNEL_ID')" in js
    for hook in ("tool.call", "tool.check", "prompt.submit", "plugin.register"):
        assert f"on('{hook}'" not in js, f"the mod must not hook {hook}"
    # the probes come before the first write in the answer handler
    answer = js.split("{ command: 'quiz-answer' }")[1]
    assert answer.index("requireServers") < answer.index("skill_grade") < answer.index("audit_write_event")


@pytest.mark.parametrize(
    "kwargs",
    [
        dict(mod_name="Bad Name"),
        dict(mod_name="claude-thing"),
        dict(mod_name="ok-name", version="1.0"),
    ],
)
def test_bad_name_or_version_is_refused(kwargs):
    base = dict(mod_name="ok-name", version="0.1.0", description="d", cards=_cards(), info=False)
    base.update(kwargs)
    with pytest.raises(ModSpecError):
        generate_mod_files(**base)


def test_nothing_to_generate_is_refused():
    with pytest.raises(ModSpecError):
        generate_mod_files(mod_name="ok", version="0.1.0", description="d", cards=[], info=False)


def test_write_refuses_a_non_empty_directory_and_path_traversal(tmp_path):
    files = generate_mod_files(mod_name="ok", version="0.1.0", description="d", cards=_cards(), info=False)
    out = tmp_path / "out"
    out.mkdir()
    (out / "keep.txt").write_text("x")
    with pytest.raises(ModSpecError):
        write_mod(files, out)
    assert (out / "keep.txt").read_text() == "x"
    with pytest.raises(ModSpecError):
        write_mod({"../escape.txt": "x"}, tmp_path / "fresh")
    assert not (tmp_path / "escape.txt").exists()


def test_a_manifest_entry_without_skill_md_is_an_error_not_a_skip(tmp_path):
    root = _store(tmp_path, {"a": BODY})
    (root / "skills" / "a" / "SKILL.md").unlink()
    with pytest.raises(ModSpecError):
        load_store(root)


# ── end to end through the real entry point and the real Claude Code CLI ──────

def _run_cli(*args: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "PYTHONPATH": str(_CORE_PLUGINS)}
    return subprocess.run(
        [sys.executable, "-W", "error::RuntimeWarning", "-m", "plugin_builder.generators.mod_cli", *args],
        capture_output=True, text=True, env=env, timeout=60,
    )


def test_cli_refuses_a_missing_store(tmp_path):
    proc = _run_cli("--store", str(tmp_path / "nope"), "--out", str(tmp_path / "o"))
    assert proc.returncode == 2
    assert "manifest.json" in proc.stderr


@pytest.mark.skipif(_CLAUDE is None, reason="the claude CLI is not installed, so Claude Code cannot be driven")
def test_cli_output_is_accepted_by_claude_code(tmp_path):
    # A card text that tries to break out of a JavaScript string and a script tag.
    hostile = (
        "## quiz\n"
        '- evil | Say "hi" and `ls` ${process.exit(1)} </script> \\\\ ? | quote, backtick | It\'s "fine".\n'
    )
    root = _store(tmp_path, {"assistant.demo": BODY, "assistant.hostile": hostile, "assistant.plain": "# none\n"})
    out = tmp_path / "mod"
    proc = _run_cli("--store", str(root), "--out", str(out), "--name", "demo-mod", "--info")
    assert proc.returncode == 0, proc.stderr
    summary = json.loads(proc.stdout)
    assert summary["skills"] == 3 and summary["cards"] == 3

    validate = subprocess.run([_CLAUDE, "plugin", "validate", str(out)], capture_output=True, text=True, timeout=120)
    assert "Validation passed" in validate.stdout, validate.stdout + validate.stderr
    assert "$.env.get (via requireAdapter)" in validate.stdout
    assert "env reads: CORVIN_CHANNEL_ID" in validate.stdout

    test = subprocess.run([_CLAUDE, "plugin", "test", str(out)], capture_output=True, text=True, timeout=180, cwd=out)
    assert test.returncode == 0, test.stdout + test.stderr
    assert "0 fail" in test.stdout and "15 pass" in test.stdout  # 7 quiz-gate + 8 skill-info tests


@pytest.mark.skipif(_CLAUDE is None, reason="the claude CLI is not installed, so Claude Code cannot be driven")
def test_info_only_mod_for_a_store_without_any_quiz_is_complete_and_tested(tmp_path):
    """The phase 2 shape: many skills, no quiz section anywhere -> one info mod WITH its own tests."""
    root = _store(tmp_path, {f"assistant.s{i}": "# no quiz here\n" for i in range(25)})
    out = tmp_path / "mod"
    proc = _run_cli("--store", str(root), "--out", str(out), "--name", "skills-info-mod", "--info")
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout) == {"mod": "skills-info-mod", "skills": 25, "cards": 0, "files": 5}
    assert (out / "tests" / "info.test.ts").is_file()
    assert not (out / "tests" / "gate.test.ts").exists()
    js = (out / "hooks" / "register.js").read_text(encoding="utf-8")
    assert "command: 'quiz'" not in js and "skill-info" in js

    validate = subprocess.run([_CLAUDE, "plugin", "validate", str(out)], capture_output=True, text=True, timeout=120)
    assert "Validation passed" in validate.stdout, validate.stdout + validate.stderr
    test = subprocess.run([_CLAUDE, "plugin", "test", str(out)], capture_output=True, text=True, timeout=180, cwd=out)
    assert test.returncode == 0, test.stdout + test.stderr
    assert "0 fail" in test.stdout and "8 pass" in test.stdout


@pytest.mark.skipif(_CLAUDE is None, reason="the claude CLI is not installed, so Claude Code cannot be driven")
def test_a_gate_removed_from_the_output_makes_the_generated_tests_fail(tmp_path):
    """The generated tests must be able to fail: with the adapter gate cut out they have to go red."""
    root = _store(tmp_path, {"assistant.demo": BODY})
    out = tmp_path / "mod"
    assert _run_cli("--store", str(root), "--out", str(out), "--name", "demo-mod").returncode == 0
    js = out / "hooks" / "register.js"
    text = js.read_text(encoding="utf-8")
    assert "await requireAdapter($)" in text
    js.write_text(text.replace("await requireAdapter($)", "// gate removed"), encoding="utf-8")
    test = subprocess.run([_CLAUDE, "plugin", "test", str(out)], capture_output=True, text=True, timeout=180, cwd=out)
    assert test.returncode != 0
    assert "(fail)" in test.stdout
